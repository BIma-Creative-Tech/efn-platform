"""Mart konsolidasi GL (F-19, docs/08 §5.3; Gelombang 12): Odoo `account_move_line` -> ClickHouse
`fact_gl_line`, dimensi `dim_gl_account` (kode per company) dan `dim_company`; view `v_gl_consolidated`
(infra/clickhouse/init/03-gl-audit.sql).

Nama kolom diverifikasi di DB dev Odoo CE 19.0 (7 Okt 2026, information_schema):
- `account_move_line`: id, move_id, journal_id, company_id, account_id, partner_id, date, parent_state, debit, credit,
  balance, amount_currency, currency_id, write_date;
- `account_move`: id, name, move_type, write_date;
- `account_account`: id, code_store (jsonb per company: kunci = id company root), name (jsonb terjemahan),
  account_type, active, write_date; relasi company `account_account_res_company_rel` (account_account_id,
  res_company_id);
- `res_company`: id, name, partner_id, parent_path, currency_id, fiscalyear_last_month (varchar), fiscalyear_last_day,
  efn_brand_code, write_date; `res_currency`: id, name.

Privasi: `partner_id` TIDAK disalin (pelanggan perorangan); hanya `ic_company_id` = company lain bila partner baris
adalah partner company grup (dasar eliminasi inter-company). Label baris (`name`) dan `ref` tidak dibaca
(bisa memuat nama pelanggan). Semua state (draft/posted/cancel) dimuat agar transisi posted->draft/cancel menimpa
versi lama; view hanya menjumlah `posted`.
"""

from decimal import Decimal
from typing import Any

from efn_etl.jobs import Job
from efn_etl.watermark import to_utc

_GL_SELECT = """
SELECT l.id AS line_id, l.company_id, l.move_id, COALESCE(m.name, '') AS move_name, m.move_type, l.journal_id,
       l.account_id, l.date, l.parent_state, l.debit, l.credit, l.balance, l.amount_currency, l.currency_id,
       icc.id AS ic_company_id, GREATEST(l.write_date, m.write_date) AS version_ts,
       {cursor_ts} AS _cursor_ts, l.id::text AS _cursor_key
FROM account_move_line l
JOIN account_move m ON m.id = l.move_id
LEFT JOIN res_company icc ON icc.partner_id = l.partner_id AND icc.id <> l.company_id
WHERE l.account_id IS NOT NULL
  AND {cursor_ts} >= %(cursor_ts)s
  AND ({cursor_ts}, l.id::text) > (%(cursor_ts)s, %(cursor_key)s)
ORDER BY {cursor_ts}, l.id::text
LIMIT %(limit)s
"""
# Dua cursor ke tabel target yang sama (ReplacingMergeTree(version_ts)): perubahan baris (write_date baris) dan
# perubahan header (post/cancel/reset menulis account_move.write_date; recompute kolom related `parent_state` belum
# tentu menyentuh write_date baris). Index cursor: sql/odoo-readonly-grants.sql.
SQL_GL_LINES = _GL_SELECT.format(cursor_ts="l.write_date")
SQL_GL_LINES_BY_MOVE = _GL_SELECT.format(cursor_ts="m.write_date")

GL_LINE_COLUMNS = (
    "line_id",
    "company_id",
    "move_id",
    "move_name",
    "move_type",
    "journal_id",
    "account_id",
    "date",
    "parent_state",
    "debit",
    "credit",
    "balance",
    "amount_currency",
    "currency_id",
    "ic_company_id",
    "version_ts",
)

PARENT_STATES = frozenset({"draft", "posted", "cancel"})


def _dec(value: Any) -> Decimal:
    return Decimal(str(value if value is not None else 0))


def transform_gl_line(row: dict[str, Any]) -> list[Any]:
    state = str(row["parent_state"] or "")
    if state not in PARENT_STATES:
        raise ValueError("parent_state tidak dikenal")
    return [
        int(row["line_id"]),
        int(row["company_id"]),
        int(row["move_id"]),
        str(row["move_name"] or ""),
        str(row["move_type"] or ""),
        int(row["journal_id"]),
        int(row["account_id"]),
        row["date"],
        state,
        _dec(row["debit"]),
        _dec(row["credit"]),
        _dec(row["balance"]),
        _dec(row["amount_currency"]),
        int(row["currency_id"]) if row["currency_id"] is not None else None,
        int(row["ic_company_id"]) if row["ic_company_id"] is not None else None,
        to_utc(row["version_ts"]),
    ]


# Akun per company: Odoo 19 menyimpan kode per company root (code_store jsonb). Company cabang memakai kode root.
SQL_GL_ACCOUNTS = """
SELECT a.id AS account_id, r.res_company_id AS company_id,
       COALESCE(a.code_store ->> r.res_company_id::text,
                a.code_store ->> split_part(c.parent_path, '/', 1), '') AS code,
       COALESCE(a.name ->> 'id_ID', a.name ->> 'en_US', '') AS name, a.account_type, a.active, a.write_date,
       a.write_date AS _cursor_ts,
       lpad(a.id::text, 12, '0') || '-' || lpad(r.res_company_id::text, 8, '0') AS _cursor_key
FROM account_account a
JOIN account_account_res_company_rel r ON r.account_account_id = a.id
JOIN res_company c ON c.id = r.res_company_id
WHERE a.write_date >= %(cursor_ts)s
  AND (a.write_date, lpad(a.id::text, 12, '0') || '-' || lpad(r.res_company_id::text, 8, '0'))
      > (%(cursor_ts)s, %(cursor_key)s)
ORDER BY a.write_date, lpad(a.id::text, 12, '0') || '-' || lpad(r.res_company_id::text, 8, '0')
LIMIT %(limit)s
"""
GL_ACCOUNT_COLUMNS = (
    "account_id",
    "company_id",
    "code",
    "name",
    "account_type",
    "internal_group",
    "active",
    "write_date",
)
# account_type Odoo 19: asset_*, liability_*, equity, equity_unaffected, income, income_other, expense, expense_*,
# off_balance.
INTERNAL_GROUPS = {
    "asset": "asset",
    "liability": "liability",
    "equity": "equity",
    "income": "income",
    "expense": "expense",
}


def internal_group(account_type: str) -> str:
    if account_type == "off_balance":
        return "off_balance"
    return INTERNAL_GROUPS.get(account_type.split("_", 1)[0], "other")


def transform_gl_account(row: dict[str, Any]) -> list[Any]:
    account_type = str(row["account_type"] or "")
    return [
        int(row["account_id"]),
        int(row["company_id"]),
        str(row["code"] or ""),
        str(row["name"] or ""),
        account_type,
        internal_group(account_type),
        1 if row["active"] else 0,
        to_utc(row["write_date"]),
    ]


SQL_COMPANIES = """
SELECT c.id AS company_id, c.name, COALESCE(c.efn_brand_code, '') AS brand, c.parent_id, cur.name AS currency,
       c.fiscalyear_last_month, c.fiscalyear_last_day, c.write_date,
       c.write_date AS _cursor_ts, c.id::text AS _cursor_key
FROM res_company c
LEFT JOIN res_currency cur ON cur.id = c.currency_id
WHERE c.write_date >= %(cursor_ts)s
  AND (c.write_date, c.id::text) > (%(cursor_ts)s, %(cursor_key)s)
ORDER BY c.write_date, c.id::text
LIMIT %(limit)s
"""
COMPANY_COLUMNS = (
    "company_id",
    "name",
    "brand",
    "parent_id",
    "currency",
    "fy_last_month",
    "fy_last_day",
    "write_date",
)


def _fy_month(value: Any) -> int:
    month = int(str(value or "12"))
    if not 1 <= month <= 12:
        raise ValueError("fiscalyear_last_month di luar 1..12")
    return month


def _fy_day(value: Any) -> int:
    day = int(value or 31)
    if not 1 <= day <= 31:
        raise ValueError("fiscalyear_last_day di luar 1..31")
    return day


def transform_company(row: dict[str, Any]) -> list[Any]:
    return [
        int(row["company_id"]),
        str(row["name"] or ""),
        str(row["brand"] or ""),
        int(row["parent_id"]) if row["parent_id"] is not None else None,
        str(row["currency"] or ""),
        _fy_month(row["fiscalyear_last_month"]),
        _fy_day(row["fiscalyear_last_day"]),
        to_utc(row["write_date"]),
    ]


def fiscal_year(day: Any, last_month: int, last_day: int) -> int:
    """Tahun fiskal = tahun kalender tanggal AKHIR periode fiskal (sama dengan rumus view v_gl_consolidated)."""
    after_close = (day.month * 100 + day.day) > (last_month * 100 + last_day)
    return day.year + 1 if after_close else day.year


GL_LINES = Job("gl_lines", "odoo", SQL_GL_LINES, "fact_gl_line", GL_LINE_COLUMNS, transform_gl_line)
GL_LINES_BY_MOVE = Job(
    "gl_lines_by_move", "odoo", SQL_GL_LINES_BY_MOVE, "fact_gl_line", GL_LINE_COLUMNS, transform_gl_line
)
GL_ACCOUNTS = Job("gl_accounts", "odoo", SQL_GL_ACCOUNTS, "dim_gl_account", GL_ACCOUNT_COLUMNS, transform_gl_account)
COMPANIES = Job("companies", "odoo", SQL_COMPANIES, "dim_company", COMPANY_COLUMNS, transform_company)
GL_JOBS = (COMPANIES, GL_ACCOUNTS, GL_LINES, GL_LINES_BY_MOVE)
