"""Mart konsolidasi GL (G12): SQL kontrak, transformasi, tahun fiskal."""

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from efn_etl.gl import (
    COMPANIES,
    GL_ACCOUNTS,
    GL_JOBS,
    GL_LINE_COLUMNS,
    GL_LINES,
    GL_LINES_BY_MOVE,
    fiscal_year,
    internal_group,
    transform_company,
    transform_gl_account,
    transform_gl_line,
)

ROOT = Path(__file__).resolve().parents[1]


def _line(**kw: object) -> dict[str, object]:
    row: dict[str, object] = {
        "line_id": 10,
        "company_id": 3,
        "move_id": 5,
        "move_name": "INV/2026/00001",
        "move_type": "out_invoice",
        "journal_id": 1,
        "account_id": 477,
        "date": date(2026, 10, 1),
        "parent_state": "posted",
        "debit": Decimal("111000.00"),
        "credit": 0,
        "balance": Decimal("111000.00"),
        "amount_currency": Decimal("111000.00"),
        "currency_id": 12,
        "ic_company_id": 4,
        "version_ts": datetime(2026, 10, 1, 3, 0),
    }
    row.update(kw)
    return row


def test_job_sql_contract_and_no_pii_columns() -> None:
    for job in GL_JOBS:
        sql = job.sql
        for token in ("%(cursor_ts)s", "%(cursor_key)s", "%(limit)s", "_cursor_ts", "_cursor_key", "ORDER BY"):
            assert token in sql, (job.name, token)
        assert "SELECT *" not in sql
        assert "res_partner" not in sql
    assert "l.write_date AS _cursor_ts" in GL_LINES.sql
    assert "m.write_date AS _cursor_ts" in GL_LINES_BY_MOVE.sql
    assert GL_LINES.target_table == GL_LINES_BY_MOVE.target_table == "fact_gl_line"
    # partner_id hanya dipakai untuk join inter-company, tidak dipilih ke output.
    assert "l.partner_id," not in GL_LINES.sql and "AS partner_id" not in GL_LINES.sql
    assert (COMPANIES.target_table, GL_ACCOUNTS.target_table) == ("dim_company", "dim_gl_account")


def test_columns_exist_in_clickhouse_schema_and_grants() -> None:
    ddl = (ROOT.parents[1] / "infra" / "clickhouse" / "init" / "03-gl-audit.sql").read_text(encoding="utf-8")
    for job in GL_JOBS:
        for col in job.columns:
            assert f"    {col} " in ddl, (job.target_table, col)
    grants = (ROOT / "sql" / "odoo-readonly-grants.sql").read_text(encoding="utf-8")
    for needle in (
        "ON public.account_move_line",
        "ON public.account_account_res_company_rel",
        "ON public.efn_audit_log ",
    ):
        assert needle in grants


def test_transform_gl_line() -> None:
    row = transform_gl_line(_line())
    out = dict(zip(GL_LINE_COLUMNS, row, strict=True))
    assert out["balance"] == Decimal("111000.00") and out["credit"] == Decimal("0")
    assert out["ic_company_id"] == 4
    assert out["version_ts"] == datetime(2026, 10, 1, 3, 0, tzinfo=UTC)
    assert (
        transform_gl_line(_line(ic_company_id=None, currency_id=None))[GL_LINE_COLUMNS.index("ic_company_id")] is None
    )


def test_transform_gl_line_rejects_unknown_state() -> None:
    with pytest.raises(ValueError):
        transform_gl_line(_line(parent_state="weird"))


@pytest.mark.parametrize(
    ("account_type", "group"),
    [
        ("asset_receivable", "asset"),
        ("asset_current", "asset"),
        ("liability_payable", "liability"),
        ("equity_unaffected", "equity"),
        ("income_other", "income"),
        ("expense_direct_cost", "expense"),
        ("off_balance", "off_balance"),
        ("", "other"),
    ],
)
def test_internal_group(account_type: str, group: str) -> None:
    assert internal_group(account_type) == group


def test_transform_account_and_company() -> None:
    acc = transform_gl_account(
        {
            "account_id": 477,
            "company_id": 3,
            "code": "121000",
            "name": "Piutang",
            "account_type": "asset_receivable",
            "active": True,
            "write_date": datetime(2026, 1, 1),
        }
    )
    assert acc[:7] == [477, 3, "121000", "Piutang", "asset_receivable", "asset", 1]
    comp = transform_company(
        {
            "company_id": 3,
            "name": "PT EFN Retail",
            "brand": "RTL",
            "parent_id": None,
            "currency": "IDR",
            "fiscalyear_last_month": "12",
            "fiscalyear_last_day": 31,
            "write_date": datetime(2026, 1, 1),
        }
    )
    assert comp[:7] == [3, "PT EFN Retail", "RTL", None, "IDR", 12, 31]
    with pytest.raises(ValueError):
        transform_company(
            {
                **dict(zip(("company_id", "name", "brand", "parent_id", "currency"), comp, strict=False)),
                "fiscalyear_last_month": "13",
                "fiscalyear_last_day": 31,
                "write_date": datetime(2026, 1, 1),
            }
        )


@pytest.mark.parametrize(
    ("day", "month", "last_day", "expected"),
    [
        (date(2026, 12, 31), 12, 31, 2026),
        (date(2026, 1, 1), 12, 31, 2026),
        (date(2026, 3, 31), 3, 31, 2026),
        (date(2026, 4, 1), 3, 31, 2027),
    ],
)
def test_fiscal_year(day: date, month: int, last_day: int, expected: int) -> None:
    assert fiscal_year(day, month, last_day) == expected
