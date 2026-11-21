"""Gelombang 47 (docs/04 §28.13.8 v20.3, ADR-041 §3.3-§3.4 Proposed, fit-gap docs/fit-gap/G47_INSIGHT_SM.md C-1..C-7):
mart EFN Insight supermarket. Logika murni (SQL, transform, alokasi fill rate, gabung kampanye, hari agregat stok);
I/O ada di efn_etl/sm_cycle.py dan efn_etl/adapters.py.

Job incremental lewat runner umum (cursor `(_cursor_ts, _cursor_key)`, overlap window, ReplacingMergeTree -> idempoten):
- `sm_dims_warehouse`      : stock_warehouse ⋈ res_company           -> dim_sm_warehouse
- `sm_dims_online_outlet`  : efn_online_outlet ⋈ efn_online_channel  -> dim_online_outlet (L-190)
- `sm_replenishment`       : proposal_line ⋈ proposal                 -> fact_replenishment
- `sm_dc_purchase`         : purchase_order_line ⋈ PO (DC)            -> fact_dc_purchase_line
- `sm_dc_supplier`         : view efn_etl.purchase_partner (DC)       -> dim_sm_supplier
- `sm_expiry` / `sm_expiry_markdown` (#114, ETL_ENABLE_SM_EXPIRY): efn_expiry_alert / efn_lot_markdown
Job khusus (sm_cycle.py): `sm_dims_kpi` (salin penuh + nonaktifkan baris yang dihapus), `sm_replenishment_bias`
(MergeTree, dedup log_id), `sm_dc_fill_rate` (hitung ulang jendela), `sm_stock_agg` / `sm_expiry_agg` (INSERT ... SELECT
di ClickHouse), `sm_campaign` (#117, ETL_ENABLE_SM_CAMPAIGN).

Hanya company dengan `efn_brand_code ∈ ETL_SM_COMPANIES` (default SMD, ⊆ SM_CODES) yang dimuat; kode divalidasi ketat
lalu disisipkan sebagai literal (runner hanya meneruskan parameter cursor). SMD tanpa data ESB/legacy (L-196).

PDP (§28.13.8): tanpa user pelaku (approved_by, changed_by, user_id, activated_by, maker_id, approved_by_id,
cancelled_by_id), tanpa partner pelanggan, member, isi pesan kampanye; supplier perorangan tanpa nama (view Odoo
efn_etl.purchase_partner sudah mengosongkan nama partner non-perusahaan).
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Final

from efn_etl.entities import COMPANY_CODES, normalize_code
from efn_etl.jobs import Job
from efn_etl.sm_kpi import KPI_BY_KEY, parse_kpi_value
from efn_etl.stock import JAKARTA
from efn_etl.watermark import to_utc

# Konstanta kontrak §28.13.3 (identik dengan Odoo insight_token.py dan insight-web lib/claims.ts). Menambah PT SM =
# revisi kontrak, bukan konfigurasi.
SM_CODES: Final = frozenset({"SMD"})
DEFAULT_SM_COMPANIES: Final = ("SMD",)

JOB_DIMS_WAREHOUSE: Final = "sm_dims_warehouse"
JOB_DIMS_OUTLET: Final = "sm_dims_online_outlet"
JOB_DIMS_KPI: Final = "sm_dims_kpi"
JOB_REPLENISHMENT: Final = "sm_replenishment"
JOB_BIAS: Final = "sm_replenishment_bias"
JOB_FILL: Final = "sm_dc_fill_rate"
JOB_PURCHASE: Final = "sm_dc_purchase"
JOB_SUPPLIER: Final = "sm_dc_supplier"
JOB_STOCK_AGG: Final = "sm_stock_agg"
JOB_EXPIRY: Final = "sm_expiry"
JOB_MARKDOWN: Final = "sm_expiry_markdown"
JOB_EXPIRY_AGG: Final = "sm_expiry_agg"
JOB_CAMPAIGN: Final = "sm_campaign"
JOB_SCRAPS: Final = "sm_scraps"
JOB_SCRAP_REASONS: Final = "sm_scrap_reasons"

Q2: Final = Decimal("0.01")
Q3: Final = Decimal("0.001")
ZERO3: Final = Decimal("0.000")
_MAX_LABEL = 128
PROPOSAL_STATES: Final = frozenset({"draft", "proposed", "approved", "rejected", "expired"})
BIAS_KINDS: Final = frozenset({"outlet_change", "outlet_approved", "dc_system", "dc_confirmed"})
EXPIRY_STAGES: Final = frozenset({"near", "markdown", "expired"})
EXPIRY_STATES: Final = frozenset({"open", "actioned", "closed"})
MARKDOWN_STATES: Final = frozenset({"draft", "active", "expired", "cancelled"})
CAMPAIGN_CHANNELS: Final = (
    ("channel_inbox", "inbox"),
    ("channel_push", "push"),
    ("channel_email", "email"),
    ("channel_wa_unofficial", "wa_unofficial"),
)


# ---------------------------------------------------------------- konfigurasi
def parse_sm_companies(raw: str | None) -> tuple[str, ...]:
    """`ETL_SM_COMPANIES` (dipisah koma). Wajib tidak kosong dan ⊆ kode PT §12.1 ∩ SM_CODES (fail closed)."""
    text = "" if raw is None else str(raw).strip()
    if not text:
        return DEFAULT_SM_COMPANIES
    codes = sorted({normalize_code(part) for part in text.split(",") if part.strip()})
    if not codes:
        raise ValueError("ETL_SM_COMPANIES kosong")
    for code in codes:
        if code not in COMPANY_CODES or code not in SM_CODES:
            raise ValueError("ETL_SM_COMPANIES hanya boleh kode PT supermarket (SM_CODES)")
    return tuple(codes)


def company_filter(alias: str, codes: Sequence[str]) -> str:
    """Predikat SQL `<alias>.efn_brand_code IN ('SMD')`. Kode divalidasi ulang (huruf besar, ⊆ SM_CODES)."""
    if not alias.isidentifier():
        raise ValueError("alias tidak valid")
    checked = parse_sm_companies(",".join(codes))
    literal = ", ".join(f"'{code}'" for code in checked)
    return f"{alias}.efn_brand_code IN ({literal})"


# ---------------------------------------------------------------- helper transform
def _label(value: Any, size: int = _MAX_LABEL) -> str:
    if isinstance(value, dict):  # field terjemahan jsonb -> id_ID lalu en_US
        value = value.get("id_ID") or value.get("en_US") or next(iter(value.values()), "")
    return ("" if value is None else str(value).strip())[:size]


def _dec(value: Any, quantum: Decimal = Q3) -> Decimal:
    number = Decimal(str(value if value is not None else 0))
    if not number.is_finite():
        raise ValueError("angka tidak valid")
    return number.quantize(quantum)


def _opt_dec(value: Any, quantum: Decimal = Q2) -> Decimal | None:
    if value is None or value == "":
        return None
    number = _dec(value, quantum)
    return None if number == 0 else number


def _opt_ts(value: Any) -> datetime | None:
    return to_utc(value).replace(microsecond=0) if isinstance(value, datetime) else None


def _ts0(value: datetime) -> datetime:
    return to_utc(value).replace(microsecond=0)


def _date(value: Any) -> date:
    if isinstance(value, datetime):
        return to_utc(value).astimezone(JAKARTA).date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _choice(value: Any, allowed: frozenset[str], what: str) -> str:
    text = str(value or "").strip()
    if text not in allowed:
        # Fail closed: nilai di luar kontrak = skema Odoo berubah -> halaman tidak dimuat (heartbeat error).
        raise ValueError(f"{what} di luar kontrak")
    return text


def wib_date(value: datetime) -> date:
    return to_utc(value).astimezone(JAKARTA).date()


# ---------------------------------------------------------------- stock_warehouse -> dim_sm_warehouse
def _sql_warehouses(codes: Sequence[str]) -> str:
    return f"""
SELECT w.id AS warehouse_id, COALESCE(w.code, '') AS code, COALESCE(w.name, '') AS name, w.company_id,
       COALESCE(c.efn_brand_code, '') AS company_code, COALESCE(w.efn_is_dc, FALSE) AS is_dc,
       COALESCE(w.active, FALSE) AS active, w.write_date,
       w.write_date AS _cursor_ts, w.id::text AS _cursor_key
FROM stock_warehouse w
JOIN res_company c ON c.id = w.company_id
WHERE {company_filter("c", codes)}
  AND w.write_date >= %(cursor_ts)s
  AND (w.write_date, w.id::text) > (%(cursor_ts)s, %(cursor_key)s)
ORDER BY w.write_date, w.id::text
LIMIT %(limit)s
"""  # noqa: S608 - kode PT tervalidasi (company_filter)


WAREHOUSE_COLUMNS: Final = (
    "warehouse_id",
    "code",
    "name",
    "company_id",
    "company_code",
    "is_dc",
    "active",
    "write_date",
)


def transform_warehouse(row: dict[str, Any]) -> list[Any]:
    return [
        int(row["warehouse_id"]),
        str(row.get("code") or ""),
        _label(row.get("name")),
        int(row["company_id"]),
        normalize_code(row.get("company_code")),
        1 if row.get("is_dc") else 0,
        1 if row.get("active") else 0,
        to_utc(row["write_date"]),
    ]


# ---------------------------------------------------------------- efn_online_outlet -> dim_online_outlet
_OUTLET_VERSION = "GREATEST(o.write_date, ch.write_date)"

# efn_online_outlet/efn_online_channel milik efn_online_order (F&B online). Supermarket tidak boleh bergantung padanya:
# job `sm_dims_online_outlet` hanya jalan bila kedua tabel terlihat oleh efn_etl (ada + di-grant; information_schema
# hanya menampilkan tabel yang punya hak akses). Dideteksi tiap siklus di sm_cycle.run_sm_online_outlets.
SQL_ONLINE_OUTLET_VISIBLE: Final = """
SELECT count(*) AS n FROM information_schema.tables
WHERE table_schema = 'public' AND table_name IN ('efn_online_outlet', 'efn_online_channel')
"""


def _sql_online_outlets(codes: Sequence[str]) -> str:
    v = _OUTLET_VERSION
    return f"""
SELECT ch.code AS channel_code, o.code AS outlet_code, o.id AS outlet_id, o.company_id,
       COALESCE(o.warehouse_id, 0) AS warehouse_id,
       (COALESCE(o.active, FALSE) AND COALESCE(ch.active, FALSE)) AS active, {v} AS write_date,
       {v} AS _cursor_ts, o.id::text AS _cursor_key
FROM efn_online_outlet o
JOIN efn_online_channel ch ON ch.id = o.channel_id
JOIN res_company c ON c.id = o.company_id
WHERE {company_filter("c", codes)}
  AND {v} >= %(cursor_ts)s
  AND ({v}, o.id::text) > (%(cursor_ts)s, %(cursor_key)s)
ORDER BY {v}, o.id::text
LIMIT %(limit)s
"""  # noqa: S608 - kode PT tervalidasi


ONLINE_OUTLET_COLUMNS: Final = (
    "channel_code",
    "outlet_code",
    "outlet_id",
    "company_id",
    "warehouse_id",
    "active",
    "write_date",
)


def transform_online_outlet(row: dict[str, Any]) -> list[Any]:
    # L-190: kunci (channel_code, outlet_code) PERSIS (peka huruf besar, tanpa upper/strip).
    channel_code = str(row.get("channel_code") or "")
    outlet_code = str(row.get("outlet_code") or "")
    if not channel_code or not outlet_code:
        raise ValueError("efn_online_outlet: kode kanal/outlet kosong")
    return [
        channel_code,
        outlet_code,
        int(row["outlet_id"]),
        int(row["company_id"]),
        int(row.get("warehouse_id") or 0),
        1 if row.get("active") else 0,
        to_utc(row["write_date"]),
    ]


# ---------------------------------------------------------------- efn_insight_sm_kpi -> dim_sm_kpi_config
def sql_kpi_all(codes: Sequence[str]) -> str:
    """Salin penuh (tabel kecil: ≤ 39 kunci x PT SM) agar baris yang DIHAPUS di Odoo terdeteksi (§28.13.8 -> active 0).
    Termasuk baris arsip (active = false)."""
    return f"""
SELECT k.id, k.company_id, k.key, k.value, COALESCE(k.active, FALSE) AS active, k.write_date
FROM efn_insight_sm_kpi k
JOIN res_company c ON c.id = k.company_id
WHERE {company_filter("c", codes)}
ORDER BY k.id
LIMIT %(limit)s
"""  # noqa: S608 - kode PT tervalidasi


KPI_COLUMNS: Final = ("company_id", "key", "value_num", "value_str", "active", "write_date")
SQL_KPI_EXISTING: Final = """
SELECT company_id, toString(key) AS key, argMax(active, write_date) AS active, max(write_date) AS last_write_date
FROM efn_dwh.dim_sm_kpi_config
GROUP BY company_id, key
"""


@dataclass(frozen=True)
class KpiSync:
    rows: list[list[Any]]
    unknown_keys: int
    invalid_values: int
    deactivated: int


def kpi_sync_rows(
    odoo_rows: Iterable[Mapping[str, Any]],
    existing: Mapping[tuple[int, str], tuple[int, datetime]],
    now: datetime,
) -> KpiSync:
    """Baris yang perlu ditulis ke dim_sm_kpi_config.

    - baris Odoo yang baru/berubah (write_date > versi tersimpan) -> nilai terparse; nilai tidak sah -> active 0;
    - kunci di luar registri -> tidak disalin (dihitung `unknown_keys`);
    - (company, key) aktif di ClickHouse tetapi tidak ada lagi di Odoo (dihapus) -> active 0, versi = `now`.
    Baris yang sama persis tidak ditulis ulang (idempoten, tanpa part baru tiap siklus).
    """
    out: list[list[Any]] = []
    seen: set[tuple[int, str]] = set()
    unknown = invalid = deactivated = 0
    for row in odoo_rows:
        key = str(row.get("key") or "").strip()
        company_id = int(row["company_id"])
        if key not in KPI_BY_KEY:
            unknown += 1
            continue
        ident = (company_id, key)
        seen.add(ident)
        version = to_utc(row["write_date"])
        stored = existing.get(ident)
        if stored is not None and to_utc(stored[1]) >= version:
            continue
        parsed = parse_kpi_value(key, row.get("value"))
        active = 1 if row.get("active") else 0
        if parsed is None:
            invalid += 1
            parsed, active = (None, ""), 0
        out.append([company_id, key, parsed[0], parsed[1], active, version])
    stamp = to_utc(now)
    for ident, (active, version) in sorted(existing.items()):
        if ident in seen or not active:
            continue
        deactivated += 1
        out.append([ident[0], ident[1], None, "", 0, max(stamp, to_utc(version) + timedelta(microseconds=1))])
    return KpiSync(out, unknown, invalid, deactivated)


# ---------------------------------------------------------------- proposal line -> fact_replenishment
_REPL_VERSION = "GREATEST(l.write_date, p.write_date)"


def _sql_replenishment(codes: Sequence[str]) -> str:
    v = _REPL_VERSION
    # Predikat OR redundan agar index (write_date, id) kedua tabel bisa dipakai (R-O03); keyset tetap pada GREATEST.
    return f"""
SELECT l.id AS line_id, p.id AS proposal_id, p.company_id, p.warehouse_id, p.supply_warehouse_id, p.schedule_id,
       p.delivery_date, p.cutoff_at, p.state, p.approved_at, COALESCE(p.picking_id, 0) AS picking_id,
       COALESCE(p.run_id, 0) AS run_id, l.product_id, l.forecast_qty, l.on_hand, l.in_transit, l.qty_need,
       l.qty_shelf_cap, l.qty_rack_cap, l.qty_proposed, l.qty_approved, COALESCE(l.excluded, FALSE) AS excluded,
       COALESCE(l.reason_codes, '') AS reason_codes, COALESCE(l.change_reason, '') AS change_reason,
       p.write_date AS header_ts, {v} AS version_ts,
       {v} AS _cursor_ts, l.id::text AS _cursor_key
FROM efn_replenishment_proposal_line l
JOIN efn_replenishment_proposal p ON p.id = l.proposal_id
JOIN res_company c ON c.id = p.company_id
WHERE {company_filter("c", codes)}
  AND (l.write_date >= %(cursor_ts)s OR p.write_date >= %(cursor_ts)s)
  AND {v} >= %(cursor_ts)s
  AND ({v}, l.id::text) > (%(cursor_ts)s, %(cursor_key)s)
ORDER BY {v}, l.id::text
LIMIT %(limit)s
"""  # noqa: S608 - kode PT tervalidasi


REPLENISHMENT_COLUMNS: Final = (
    "line_id",
    "proposal_id",
    "company_id",
    "warehouse_id",
    "supply_warehouse_id",
    "schedule_id",
    "delivery_date",
    "cutoff_at",
    "state",
    "approved_at",
    "picking_id",
    "run_id",
    "product_id",
    "forecast_qty",
    "on_hand",
    "in_transit",
    "qty_need",
    "qty_shelf_cap",
    "qty_rack_cap",
    "qty_proposed",
    "qty_approved",
    "excluded",
    "reason_codes",
    "change_reason",
    "header_ts",
    "version_ts",
)


def transform_replenishment(row: dict[str, Any]) -> list[Any]:
    return [
        int(row["line_id"]),
        int(row["proposal_id"]),
        int(row["company_id"]),
        int(row["warehouse_id"]),
        int(row["supply_warehouse_id"]),
        int(row.get("schedule_id") or 0),
        _date(row["delivery_date"]),
        _ts0(row["cutoff_at"]),
        _choice(row.get("state"), PROPOSAL_STATES, "efn_replenishment_proposal.state"),
        _opt_ts(row.get("approved_at")),
        int(row.get("picking_id") or 0),
        int(row.get("run_id") or 0),
        int(row["product_id"]),
        _dec(row.get("forecast_qty")),
        _dec(row.get("on_hand")),
        _dec(row.get("in_transit")),
        _dec(row.get("qty_need")),
        _dec(row.get("qty_shelf_cap")),
        _dec(row.get("qty_rack_cap")),
        _dec(row.get("qty_proposed")),
        _dec(row.get("qty_approved")),
        1 if row.get("excluded") else 0,
        _label(row.get("reason_codes"), 256),
        _label(row.get("change_reason"), 32),
        to_utc(row["header_ts"]),
        to_utc(row["version_ts"]),
    ]


# ---------------------------------------------------------------- bias log -> fact_replenishment_bias (MergeTree)
def sql_bias(codes: Sequence[str]) -> str:
    # Tanpa user_id (grant per kolom tidak memuatnya).
    return f"""
SELECT b.id AS log_id, b.company_id, b.kind, COALESCE(b.proposal_id, 0) AS proposal_id,
       COALESCE(b.warehouse_id, 0) AS warehouse_id, COALESCE(b.product_id, 0) AS product_id,
       COALESCE(b.run_id, 0) AS run_id, COALESCE(b.purchase_line_id, 0) AS purchase_line_id,
       b.qty_system, b.qty_before, b.qty_after, COALESCE(b.change_reason, '') AS change_reason,
       b.create_date AS created_at,
       b.create_date AS _cursor_ts, b.id::text AS _cursor_key
FROM efn_replenishment_bias_log b
JOIN res_company c ON c.id = b.company_id
WHERE {company_filter("c", codes)}
  AND b.create_date >= %(cursor_ts)s
  AND (b.create_date, b.id::text) > (%(cursor_ts)s, %(cursor_key)s)
ORDER BY b.create_date, b.id::text
LIMIT %(limit)s
"""  # noqa: S608 - kode PT tervalidasi


BIAS_COLUMNS: Final = (
    "log_id",
    "company_id",
    "kind",
    "proposal_id",
    "warehouse_id",
    "product_id",
    "run_id",
    "purchase_line_id",
    "qty_system",
    "qty_before",
    "qty_after",
    "change_reason",
    "created_at",
)
SQL_BIAS_EXISTING: Final = "SELECT log_id FROM efn_dwh.fact_replenishment_bias WHERE log_id IN {ids:Array(UInt64)}"


def transform_bias(row: dict[str, Any]) -> list[Any]:
    return [
        int(row["log_id"]),
        int(row["company_id"]),
        _choice(row.get("kind"), BIAS_KINDS, "efn_replenishment_bias_log.kind"),
        int(row.get("proposal_id") or 0),
        int(row.get("warehouse_id") or 0),
        int(row.get("product_id") or 0),
        int(row.get("run_id") or 0),
        int(row.get("purchase_line_id") or 0),
        _dec(row.get("qty_system")),
        _dec(row.get("qty_before")),
        _dec(row.get("qty_after")),
        _label(row.get("change_reason"), 32),
        to_utc(row["created_at"]),
    ]


def new_bias_rows(rows: Sequence[dict[str, Any]], existing_ids: Iterable[int]) -> list[list[Any]]:
    """Dedup append-only: log_id yang sudah ada di ClickHouse (overlap window / crash sebelum watermark) dilewati;
    duplikat dalam halaman yang sama juga dibuang."""
    skip = {int(i) for i in existing_ids}
    out: list[list[Any]] = []
    for row in rows:
        log_id = int(row["log_id"])
        if log_id in skip:
            continue
        skip.add(log_id)
        out.append(transform_bias(row))
    return out


# ---------------------------------------------------------------- PO DC -> fact_dc_purchase_line + dim_sm_supplier
_PO_VERSION = "GREATEST(pol.write_date, po.write_date)"


def _sql_dc_purchase(codes: Sequence[str]) -> str:
    v = _PO_VERSION
    # C-7: from_orderpoint = orderpoint_id ⋈ orderpoint terkelola efn_replenishment. supplier = commercial partner (view
    # efn_etl.purchase_partner; efn_etl tidak punya grant res_partner).
    return f"""
SELECT pol.id AS line_id, po.id AS order_id, po.company_id, spt.warehouse_id,
       COALESCE(pp.commercial_partner_id, po.partner_id, 0) AS supplier_id, pol.product_id, po.state AS po_state,
       po.date_order, po.date_approve, pol.date_planned, pol.product_qty, pol.qty_received, pol.price_subtotal,
       (pol.orderpoint_id IS NOT NULL AND COALESCE(op.efn_replenishment_managed, FALSE)) AS from_orderpoint,
       {v} AS version_ts,
       {v} AS _cursor_ts, pol.id::text AS _cursor_key
FROM purchase_order_line pol
JOIN purchase_order po ON po.id = pol.order_id
JOIN res_company c ON c.id = po.company_id
JOIN stock_picking_type spt ON spt.id = po.picking_type_id
JOIN stock_warehouse w ON w.id = spt.warehouse_id
LEFT JOIN efn_etl.purchase_partner pp ON pp.id = po.partner_id
LEFT JOIN stock_warehouse_orderpoint op ON op.id = pol.orderpoint_id
WHERE {company_filter("c", codes)}
  AND COALESCE(w.efn_is_dc, FALSE)
  AND pol.product_id IS NOT NULL
  AND (pol.write_date >= %(cursor_ts)s OR po.write_date >= %(cursor_ts)s)
  AND {v} >= %(cursor_ts)s
  AND ({v}, pol.id::text) > (%(cursor_ts)s, %(cursor_key)s)
ORDER BY {v}, pol.id::text
LIMIT %(limit)s
"""  # noqa: S608 - kode PT tervalidasi


PURCHASE_COLUMNS: Final = (
    "line_id",
    "order_id",
    "company_id",
    "warehouse_id",
    "supplier_id",
    "product_id",
    "po_state",
    "date_order",
    "date_approve",
    "date_planned",
    "product_qty",
    "qty_received",
    "price_subtotal",
    "from_orderpoint",
    "version_ts",
)


def transform_purchase(row: dict[str, Any]) -> list[Any]:
    date_order = _opt_ts(row.get("date_order")) or _ts0(row["version_ts"])
    return [
        int(row["line_id"]),
        int(row["order_id"]),
        int(row["company_id"]),
        int(row["warehouse_id"]),
        int(row.get("supplier_id") or 0),
        int(row["product_id"]),
        _label(row.get("po_state"), 16),
        date_order,
        _opt_ts(row.get("date_approve")),
        _opt_ts(row.get("date_planned")),
        _dec(row.get("product_qty")),
        _dec(row.get("qty_received")),
        _dec(row.get("price_subtotal"), Q2),
        1 if row.get("from_orderpoint") else 0,
        to_utc(row["version_ts"]),
    ]


def _sql_dc_supplier(codes: Sequence[str]) -> str:
    # Cursor = GREATEST(partner, PO DC terakhir): supplier lama yang baru dirujuk PO DC tetap tertangkap.
    return f"""
SELECT pp.id AS supplier_id, pp.name, pp.is_company, pp.write_date,
       GREATEST(pp.write_date, s.last_po) AS _cursor_ts, pp.id::text AS _cursor_key
FROM
(
    SELECT COALESCE(rp.commercial_partner_id, po.partner_id) AS supplier_id, MAX(po.write_date) AS last_po
    FROM purchase_order po
    JOIN res_company c ON c.id = po.company_id
    JOIN stock_picking_type spt ON spt.id = po.picking_type_id
    JOIN stock_warehouse w ON w.id = spt.warehouse_id
    JOIN efn_etl.purchase_partner rp ON rp.id = po.partner_id
    WHERE {company_filter("c", codes)} AND COALESCE(w.efn_is_dc, FALSE)
    GROUP BY 1
) AS s
JOIN efn_etl.purchase_partner pp ON pp.id = s.supplier_id
WHERE GREATEST(pp.write_date, s.last_po) >= %(cursor_ts)s
  AND (GREATEST(pp.write_date, s.last_po), pp.id::text) > (%(cursor_ts)s, %(cursor_key)s)
ORDER BY GREATEST(pp.write_date, s.last_po), pp.id::text
LIMIT %(limit)s
"""  # noqa: S608 - kode PT tervalidasi


SUPPLIER_COLUMNS: Final = ("supplier_id", "name", "write_date")


def transform_supplier(row: dict[str, Any]) -> list[Any]:
    # Minimisasi PDP (§28.13.8): partner perorangan -> nama kosong (UI: "Supplier #<id>"). Dicek ulang di ETL walau view
    # Odoo sudah mengosongkannya.
    name = _label(row.get("name")) if row.get("is_company") else ""
    version = row.get("write_date") or row["_cursor_ts"]
    return [int(row["supplier_id"]), name, to_utc(version)]


# ---------------------------------------------------------------- #114: efn_expiry_alert / efn_lot_markdown
def _sql_expiry(codes: Sequence[str]) -> str:
    return f"""
SELECT a.id AS alert_id, a.company_id, a.warehouse_id, a.lot_id, COALESCE(lot.name, '') AS lot_name, a.product_id,
       COALESCE(a.categ_id, 0) AS categ_id, a.stage, a.state, a.date AS detected_date, a.last_run_date, a.days_left,
       lot.expiration_date, a.qty, a.write_date,
       a.write_date AS _cursor_ts, a.id::text AS _cursor_key
FROM efn_expiry_alert a
JOIN res_company c ON c.id = a.company_id
LEFT JOIN stock_lot lot ON lot.id = a.lot_id
WHERE {company_filter("c", codes)}
  AND a.write_date >= %(cursor_ts)s
  AND (a.write_date, a.id::text) > (%(cursor_ts)s, %(cursor_key)s)
ORDER BY a.write_date, a.id::text
LIMIT %(limit)s
"""  # noqa: S608 - kode PT tervalidasi


EXPIRY_COLUMNS: Final = (
    "alert_id",
    "company_id",
    "warehouse_id",
    "lot_id",
    "lot_name",
    "product_id",
    "categ_id",
    "stage",
    "state",
    "detected_date",
    "last_run_date",
    "days_left",
    "expiration_date",
    "qty",
    "write_date",
)


def transform_expiry(row: dict[str, Any]) -> list[Any]:
    detected = _date(row["detected_date"])
    last_run = row.get("last_run_date")
    return [
        int(row["alert_id"]),
        int(row["company_id"]),
        int(row["warehouse_id"]),
        int(row["lot_id"]),
        _label(row.get("lot_name"), 64),
        int(row["product_id"]),
        int(row.get("categ_id") or 0),
        _choice(row.get("stage"), EXPIRY_STAGES, "efn_expiry_alert.stage"),
        _choice(row.get("state"), EXPIRY_STATES, "efn_expiry_alert.state"),
        detected,
        _date(last_run) if last_run else detected,
        max(min(int(row.get("days_left") or 0), 2**31 - 1), -(2**31)),
        _opt_ts(row.get("expiration_date")),
        _dec(row.get("qty")),
        to_utc(row["write_date"]),
    ]


def _sql_markdown(codes: Sequence[str]) -> str:
    # Tanpa activated_by / note (teks bebas).
    return f"""
SELECT m.id AS markdown_id, m.company_id, m.warehouse_id, m.lot_id, m.product_id, COALESCE(m.alert_id, 0) AS alert_id,
       m.state, m.price, m.discount_pct, m.date_from, m.date_to, m.qty_cap, m.qty_sold,
       COALESCE(m.notify_members, FALSE) AS notify_members, m.write_date,
       m.write_date AS _cursor_ts, m.id::text AS _cursor_key
FROM efn_lot_markdown m
JOIN res_company c ON c.id = m.company_id
WHERE {company_filter("c", codes)}
  AND m.write_date >= %(cursor_ts)s
  AND (m.write_date, m.id::text) > (%(cursor_ts)s, %(cursor_key)s)
ORDER BY m.write_date, m.id::text
LIMIT %(limit)s
"""  # noqa: S608 - kode PT tervalidasi


MARKDOWN_COLUMNS: Final = (
    "markdown_id",
    "company_id",
    "warehouse_id",
    "lot_id",
    "product_id",
    "alert_id",
    "state",
    "price",
    "discount_pct",
    "date_from",
    "date_to",
    "qty_cap",
    "qty_sold",
    "notify_members",
    "write_date",
)


def transform_markdown(row: dict[str, Any]) -> list[Any]:
    return [
        int(row["markdown_id"]),
        int(row["company_id"]),
        int(row["warehouse_id"]),
        int(row["lot_id"]),
        int(row.get("product_id") or 0),
        int(row.get("alert_id") or 0),
        _choice(row.get("state"), MARKDOWN_STATES, "efn_lot_markdown.state"),
        _opt_dec(row.get("price")),
        _opt_dec(row.get("discount_pct")),
        _date(row["date_from"]),
        _date(row["date_to"]),
        _dec(row.get("qty_cap")),
        _dec(row.get("qty_sold")),
        1 if row.get("notify_members") else 0,
        to_utc(row["write_date"]),
    ]


# ---------------------------------------------------------------- scrap SMD -> fact_scrap (fit-gap C-6)
# Kolom efn_* stock_scrap hanya ada (dan di-grant) bila efn_ck_production terpasang. information_schema.columns hanya
# menampilkan kolom yang bisa diakses efn_etl, jadi "terlihat" = ada + di-grant.
SQL_SCRAP_EFN_COLUMNS: Final = """
SELECT column_name FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'stock_scrap' AND column_name IN ('efn_warehouse_id', 'efn_waste_value')
"""


def sm_scrap_job(codes: Sequence[str], has_efn_warehouse: bool, has_efn_value: bool) -> Job:
    """Job `sm_scraps`: scrap PT SM ke fact_scrap tanpa bergantung efn_ck_production (ekspresi = job G32)."""
    from efn_etl.waste import SCRAP_COLUMNS, scrap_sql, transform_scrap

    sql = scrap_sql(has_efn_warehouse, has_efn_value, company_filter("c", codes))
    return Job(JOB_SCRAPS, "odoo", sql, "fact_scrap", SCRAP_COLUMNS, transform_scrap)


def sm_scrap_reason_job() -> Job:
    """Job `sm_scrap_reasons` (QA G51): nama alasan scrap -> dim_scrap_reason. Tanpa ini v_sm_waste_daily menampilkan
    "Tanpa alasan" untuk SMD bila ETL_ENABLE_WASTE (job G32 `waste_reasons`) mati. Tag tidak punya company_id."""
    from efn_etl.waste import REASON_COLUMNS, SQL_REASONS, transform_reason

    return Job(JOB_SCRAP_REASONS, "odoo", SQL_REASONS, "dim_scrap_reason", REASON_COLUMNS, transform_reason)


# ---------------------------------------------------------------- daftar job runner
def sm_jobs(codes: Sequence[str] = DEFAULT_SM_COMPANIES, expiry: bool = False) -> tuple[Job, ...]:
    """Job incremental G47 (`ETL_ENABLE_SM`; `expiry` = `ETL_ENABLE_SM_EXPIRY`). Job khusus ada di sm_cycle.py."""
    jobs = [
        Job(
            JOB_DIMS_WAREHOUSE,
            "odoo",
            _sql_warehouses(codes),
            "dim_sm_warehouse",
            WAREHOUSE_COLUMNS,
            transform_warehouse,
        ),
        Job(
            JOB_REPLENISHMENT,
            "odoo",
            _sql_replenishment(codes),
            "fact_replenishment",
            REPLENISHMENT_COLUMNS,
            transform_replenishment,
        ),
        Job(
            JOB_PURCHASE, "odoo", _sql_dc_purchase(codes), "fact_dc_purchase_line", PURCHASE_COLUMNS, transform_purchase
        ),
        Job(JOB_SUPPLIER, "odoo", _sql_dc_supplier(codes), "dim_sm_supplier", SUPPLIER_COLUMNS, transform_supplier),
    ]
    if expiry:
        jobs.append(Job(JOB_EXPIRY, "odoo", _sql_expiry(codes), "fact_lot_expiry", EXPIRY_COLUMNS, transform_expiry))
        jobs.append(
            Job(JOB_MARKDOWN, "odoo", _sql_markdown(codes), "fact_lot_markdown", MARKDOWN_COLUMNS, transform_markdown)
        )
    return tuple(jobs)


def sm_online_outlet_job(codes: Sequence[str]) -> Job:
    """Job opsional `sm_dims_online_outlet` (bukan bagian sm_jobs; lihat SQL_ONLINE_OUTLET_VISIBLE)."""
    return Job(
        JOB_DIMS_OUTLET,
        "odoo",
        _sql_online_outlets(codes),
        "dim_online_outlet",
        ONLINE_OUTLET_COLUMNS,
        transform_online_outlet,
    )


# ---------------------------------------------------------------- fill rate DC (fit-gap C-3)
def sql_fill_lines(codes: Sequence[str]) -> str:
    return f"""
SELECT l.id AS line_id, p.id AS proposal_id, p.company_id, p.warehouse_id, p.supply_warehouse_id AS dc_warehouse_id,
       l.product_id, p.delivery_date, l.qty_approved, COALESCE(p.picking_id, 0) AS picking_id
FROM efn_replenishment_proposal p
JOIN efn_replenishment_proposal_line l ON l.proposal_id = p.id
JOIN res_company c ON c.id = p.company_id
WHERE {company_filter("c", codes)}
  AND p.state = 'approved' AND p.delivery_date >= %(since)s
  AND l.qty_approved > 0 AND NOT COALESCE(l.excluded, FALSE)
  AND l.id > %(after_id)s
ORDER BY l.id
LIMIT %(limit)s
"""  # noqa: S608 - kode PT tervalidasi


# Move per picking akar (usulan + backorder ≤ 3). ship = move done ber-tujuan lokasi transit; bila picking usulan
# ternyata picking pick (tujuan bukan transit), diikuti satu langkah move_dest ke move ship (fit-gap §6.1). recv = move
# lanjutan (≤ 2 langkah dari ship) beserta warehouse asal & tujuan; ETL hanya menghitung yang MELINTASI batas outlet.
# qty dikonversi ke UoM produk (stock_move.quantity dalam UoM move).
SQL_FILL_MOVES: Final = """
WITH RECURSIVE chain(root_id, picking_id, depth) AS (
    SELECT sp.id, sp.id, 0 FROM stock_picking sp WHERE sp.id = ANY(%(pickings)s)
    UNION ALL
    SELECT ch.root_id, sp.id, ch.depth + 1
    FROM stock_picking sp JOIN chain ch ON sp.backorder_id = ch.picking_id
    WHERE ch.depth < 3
),
first_moves AS (
    SELECT ch.root_id, m.id AS move_id, ld.usage AS dest_usage
    FROM chain ch
    JOIN stock_move m ON m.picking_id = ch.picking_id AND m.state = 'done'
    JOIN stock_location ld ON ld.id = m.location_dest_id
),
ship(root_id, move_id) AS (
    SELECT f.root_id, f.move_id FROM first_moves f WHERE f.dest_usage = 'transit'
    UNION
    SELECT f.root_id, r.move_dest_id
    FROM first_moves f
    JOIN stock_move_move_rel r ON r.move_orig_id = f.move_id
    JOIN stock_move m2 ON m2.id = r.move_dest_id AND m2.state = 'done'
    JOIN stock_location l2 ON l2.id = m2.location_dest_id AND l2.usage = 'transit'
    WHERE f.dest_usage <> 'transit'
),
dest(root_id, move_id, depth) AS (
    SELECT s.root_id, s.move_id, 0 FROM ship s
    UNION ALL
    SELECT d.root_id, r.move_dest_id, d.depth + 1
    FROM dest d JOIN stock_move_move_rel r ON r.move_orig_id = d.move_id
    WHERE d.depth < 2
)
SELECT 'ship' AS kind, s.root_id, m.id AS move_id, m.product_id, m.quantity * mu.factor / pu.factor AS qty,
       m.date AS done_at, 0 AS dest_warehouse_id, 0 AS src_warehouse_id
FROM ship s
JOIN stock_move m ON m.id = s.move_id
JOIN uom_uom mu ON mu.id = m.product_uom
JOIN product_product pp ON pp.id = m.product_id
JOIN product_template pt ON pt.id = pp.product_tmpl_id
JOIN uom_uom pu ON pu.id = pt.uom_id
UNION ALL
SELECT DISTINCT 'recv' AS kind, d.root_id, m.id AS move_id, m.product_id, m.quantity * mu.factor / pu.factor AS qty,
       m.date AS done_at, COALESCE(ld.warehouse_id, 0) AS dest_warehouse_id,
       COALESCE(ls.warehouse_id, 0) AS src_warehouse_id
FROM dest d
JOIN stock_move m ON m.id = d.move_id AND m.state = 'done'
JOIN stock_location ld ON ld.id = m.location_dest_id
JOIN stock_location ls ON ls.id = m.location_id
JOIN uom_uom mu ON mu.id = m.product_uom
JOIN product_product pp ON pp.id = m.product_id
JOIN product_template pt ON pt.id = pp.product_tmpl_id
JOIN uom_uom pu ON pu.id = pt.uom_id
WHERE d.depth >= 1
"""

FILL_COLUMNS: Final = (
    "line_id",
    "proposal_id",
    "company_id",
    "warehouse_id",
    "dc_warehouse_id",
    "product_id",
    "delivery_date",
    "qty_approved",
    "qty_shipped",
    "qty_received",
    "last_done_at",
    "last_received_at",
    "on_time",
    "picking_shared",
    "computed_at",
)
SQL_FILL_EXISTING: Final = """
SELECT line_id, qty_shipped, qty_received, last_done_at, last_received_at, on_time, picking_shared, qty_approved
FROM efn_dwh.fact_dc_fill_rate FINAL
WHERE delivery_date >= {since:Date}
"""


@dataclass
class _Alloc:
    qty: Decimal = ZERO3
    last_at: datetime | None = None
    by_due: Decimal = ZERO3


@dataclass(frozen=True)
class FillLine:
    line_id: int
    proposal_id: int
    company_id: int
    warehouse_id: int
    dc_warehouse_id: int
    product_id: int
    delivery_date: date
    qty_approved: Decimal
    picking_id: int


def fill_line(row: Mapping[str, Any]) -> FillLine:
    return FillLine(
        int(row["line_id"]),
        int(row["proposal_id"]),
        int(row["company_id"]),
        int(row["warehouse_id"]),
        int(row["dc_warehouse_id"]),
        int(row["product_id"]),
        _date(row["delivery_date"]),
        _dec(row["qty_approved"]),
        int(row.get("picking_id") or 0),
    )


def _allocate(lines: Sequence[FillLine], events: Sequence[tuple[datetime, Decimal]]) -> dict[int, _Alloc]:
    """FIFO: event (urut waktu) mengisi baris (urut delivery_date, line_id) sampai qty_approved; sisa ke baris terakhir.
    `by_due` = qty yang teralokasi dengan tanggal WIB <= delivery_date baris."""
    ordered = sorted(lines, key=lambda ln: (ln.delivery_date, ln.line_id))
    out = {ln.line_id: _Alloc() for ln in ordered}
    if not ordered:
        return out
    idx = 0
    for when, qty in sorted(events, key=lambda e: e[0]):
        remaining = qty
        while remaining > 0:
            line = ordered[idx]
            acc = out[line.line_id]
            room = line.qty_approved - acc.qty
            last = idx == len(ordered) - 1
            take = remaining if last or room >= remaining else max(room, ZERO3)
            if take > 0:
                acc.qty += take
                acc.last_at = when if acc.last_at is None or when > acc.last_at else acc.last_at
                if wib_date(when) <= line.delivery_date:
                    acc.by_due += take
                remaining -= take
            if remaining > 0:
                idx += 1
    return out


def fill_rate_rows(
    lines: Sequence[FillLine], moves: Sequence[Mapping[str, Any]], computed_at: datetime
) -> list[list[Any]]:
    """Baris fact_dc_fill_rate. Kunci alokasi = (picking akar, produk); picking_shared bila picking akar dipakai > 1
    usulan. qty_received hanya move yang tiba di warehouse outlet baris DAN berasal dari luar outlet (C-3)."""
    groups: dict[tuple[int, int], list[FillLine]] = defaultdict(list)
    proposals_per_picking: dict[int, set[int]] = defaultdict(set)
    for ln in lines:
        if ln.picking_id:
            groups[(ln.picking_id, ln.product_id)].append(ln)
            proposals_per_picking[ln.picking_id].add(ln.proposal_id)
    ship: dict[tuple[int, int], list[tuple[datetime, Decimal]]] = defaultdict(list)
    recv: dict[tuple[int, int], list[tuple[datetime, Decimal, int, int, int]]] = defaultdict(list)
    seen: set[tuple[str, int, int]] = set()
    for mv in moves:
        kind = str(mv.get("kind"))
        root = int(mv["root_id"])
        move_id = int(mv["move_id"])
        if (kind, root, move_id) in seen or not isinstance(mv.get("done_at"), datetime):
            continue
        seen.add((kind, root, move_id))
        key = (root, int(mv["product_id"]))
        qty = _dec(mv.get("qty"))
        when = _ts0(mv["done_at"])
        if kind == "ship":
            ship[key].append((when, qty))
        elif kind == "recv":
            dst = int(mv.get("dest_warehouse_id") or 0)
            src = int(mv.get("src_warehouse_id") or 0)
            recv[key].append((when, qty, dst, src, move_id))
    allocs_ship: dict[int, _Alloc] = {}
    allocs_recv: dict[int, _Alloc] = {}
    for key, group in groups.items():
        allocs_ship.update(_allocate(group, ship.get(key, [])))
        outlet = group[0].warehouse_id
        crossing = [(w, q) for w, q, dst, src, _m in recv.get(key, []) if dst == outlet and src != outlet]
        allocs_recv.update(_allocate(group, crossing))
    ts = to_utc(computed_at)
    out: list[list[Any]] = []
    for ln in sorted(lines, key=lambda x: x.line_id):
        s = allocs_ship.get(ln.line_id, _Alloc())
        r = allocs_recv.get(ln.line_id, _Alloc())
        shared = 1 if ln.picking_id and len(proposals_per_picking[ln.picking_id]) > 1 else 0
        out.append(
            [
                ln.line_id,
                ln.proposal_id,
                ln.company_id,
                ln.warehouse_id,
                ln.dc_warehouse_id,
                ln.product_id,
                ln.delivery_date,
                ln.qty_approved,
                s.qty.quantize(Q3),
                r.qty.quantize(Q3),
                s.last_at,
                r.last_at,
                1 if ln.qty_approved > 0 and s.by_due >= ln.qty_approved else 0,
                shared,
                ts,
            ]
        )
    return out


def changed_fill_rows(rows: Sequence[list[Any]], existing: Mapping[int, tuple[Any, ...]]) -> list[list[Any]]:
    """Hanya baris baru/berubah yang ditulis (hitung ulang tiap siklus tanpa jutaan versi identik). Perbandingan =
    (qty_shipped, qty_received, last_done_at, last_received_at, on_time, picking_shared, qty_approved)."""
    out: list[list[Any]] = []
    for row in rows:
        signature = (row[8], row[9], _norm_ts(row[10]), _norm_ts(row[11]), row[12], row[13], row[7])
        old = existing.get(int(row[0]))
        if old is not None:
            old_sig = (
                _dec(old[0]),
                _dec(old[1]),
                _norm_ts(old[2]),
                _norm_ts(old[3]),
                int(old[4]),
                int(old[5]),
                _dec(old[6]),
            )
            if old_sig == signature:
                continue
        out.append(row)
    return out


def _norm_ts(value: Any) -> datetime | None:
    return _ts0(value) if isinstance(value, datetime) else None


# ---------------------------------------------------------------- stok harian & expiry harian (ClickHouse)
SQL_STOCK_RUNS: Final = """
SELECT snapshot_date, max(snapshot_ts) AS ts
FROM efn_dwh.stock_snapshot_run
WHERE snapshot_date >= {since:Date}
GROUP BY snapshot_date
ORDER BY snapshot_date
"""


def stock_days_pending(
    runs: Iterable[tuple[date, datetime]], watermark: datetime | None, max_days: int
) -> list[tuple[date, datetime]]:
    """Hari (urut) yang snapshot selesai terakhirnya lebih baru dari watermark `sm_stock_agg`; hari berjalan dihitung
    ulang setiap snapshot baru. Dibatasi `max_days` per siklus (backfill bertahap)."""
    if max_days < 1:
        raise ValueError("max_days >= 1")
    pending = [(d, to_utc(ts)) for d, ts in runs if watermark is None or to_utc(ts) > to_utc(watermark)]
    return sorted(pending)[:max_days]


def stock_since(watermark: datetime | None, now: datetime, backfill_days: int) -> date:
    if watermark is None:
        return wib_date(now) - timedelta(days=backfill_days)
    return wib_date(watermark)


_STOCK_TS = "(SELECT max(snapshot_ts) FROM efn_dwh.stock_snapshot_run WHERE snapshot_date = {day:Date})"
_SM_WH = "(SELECT warehouse_id FROM efn_dwh.dim_sm_warehouse FINAL)"
_Z3 = "toDecimal128(0, 3)"
_Z2 = "toDecimal128(0, 2)"
_NULL_DAYS = "CAST(NULL AS Nullable(Int64))"

# Snapshot selesai terakhir hari {day}: quant (nilai HPP, umur, sisa umur lot) + orderpoint (tracked/min/max) + kunci
# yang sudah ada untuk hari itu dengan nilai 0 (produk yang hilang di snapshot lebih baru tidak tertinggal nilai lama).
# Hanya warehouse dim_sm_warehouse (PT SM). Penjualan 28 hari sebelum {day} = POS produk non-refund per warehouse.
SQL_STOCK_AGG: Final = f"""
INSERT INTO efn_dwh.agg_sm_stock_daily
    (day, snapshot_ts, company_id, warehouse_id, product_id, categ_id, tracked, sold_28d, qty_sold_28d, on_hand_qty,
     free_qty, min_qty, max_qty, stock_value_cost, value_age_0_30, value_age_31_60, value_age_61_90, value_age_91p,
     value_exp_expired, value_exp_0_7, value_exp_8_30, value_exp_31_90, value_exp_91p, value_no_lot, computed_at)
SELECT
    {{day:Date}}, {_STOCK_TS}, g.company_id, g.warehouse_id, g.product_id, p.categ_id, g.tracked,
    toUInt8(sl.qty > 0), toDecimal128(sl.qty, 3), g.on_hand_qty, g.free_qty, g.min_qty, g.max_qty, g.stock_value,
    g.a0, g.a31, g.a61, g.a91, g.ex, g.e0, g.e8, g.e31, g.e91, g.nolot, now64(6)
FROM
(
    SELECT
        u.company_id AS company_id, u.warehouse_id AS warehouse_id, u.product_id AS product_id,
        max(u.tracked) AS tracked, sum(u.on_hand) AS on_hand_qty, sum(u.free) AS free_qty, sum(u.min_q) AS min_qty,
        sum(u.max_q) AS max_qty, sum(u.v) AS stock_value,
        sumIf(u.v, u.kind = 1 AND u.age <= 30) AS a0,
        sumIf(u.v, u.kind = 1 AND u.age > 30 AND u.age <= 60) AS a31,
        sumIf(u.v, u.kind = 1 AND u.age > 60 AND u.age <= 90) AS a61,
        sumIf(u.v, u.kind = 1 AND u.age > 90) AS a91,
        sumIf(u.v, u.kind = 1 AND isNotNull(u.exp_days) AND assumeNotNull(u.exp_days) < 0) AS ex,
        sumIf(u.v, u.kind = 1 AND isNotNull(u.exp_days) AND assumeNotNull(u.exp_days) BETWEEN 0 AND 7) AS e0,
        sumIf(u.v, u.kind = 1 AND isNotNull(u.exp_days) AND assumeNotNull(u.exp_days) BETWEEN 8 AND 30) AS e8,
        sumIf(u.v, u.kind = 1 AND isNotNull(u.exp_days) AND assumeNotNull(u.exp_days) BETWEEN 31 AND 90) AS e31,
        sumIf(u.v, u.kind = 1 AND isNotNull(u.exp_days) AND assumeNotNull(u.exp_days) > 90) AS e91,
        sumIf(u.v, u.kind = 1 AND isNull(u.exp_days)) AS nolot
    FROM
    (
        SELECT
            q.company_id AS company_id, q.warehouse_id AS warehouse_id, q.product_id AS product_id,
            toUInt8(1) AS kind, toUInt8(0) AS tracked,
            toDecimal128(q.quantity, 3) AS on_hand, toDecimal128(q.quantity - q.reserved_quantity, 3) AS free,
            {_Z3} AS min_q, {_Z3} AS max_q,
            toDecimal128(toDecimal128(q.quantity, 4)
                         * toDecimal128(ifNull(q.unit_cost, toDecimal64(0, 4)), 4), 2) AS v,
            toInt64(ifNull(dateDiff('day', toDate(q.in_date), {{day:Date}}), 0)) AS age,
            if(isNull(q.expiration_date), {_NULL_DAYS},
               toNullable(toInt64(dateDiff('day', {{day:Date}}, toDate(assumeNotNull(q.expiration_date))))))
                AS exp_days
        FROM efn_dwh.fact_stock_snapshot AS q
        WHERE q.snapshot_date = {{day:Date}} AND q.snapshot_ts = {_STOCK_TS}
          AND q.warehouse_id != 0 AND q.warehouse_id IN {_SM_WH}

        UNION ALL

        SELECT
            op.company_id AS company_id, op.warehouse_id AS warehouse_id, op.product_id AS product_id,
            toUInt8(2) AS kind, toUInt8(1) AS tracked, {_Z3} AS on_hand, {_Z3} AS free,
            toDecimal128(op.min_qty, 3) AS min_q, toDecimal128(op.max_qty, 3) AS max_q, {_Z2} AS v,
            toInt64(0) AS age, {_NULL_DAYS} AS exp_days
        FROM efn_dwh.fact_orderpoint_snapshot AS op
        WHERE op.snapshot_date = {{day:Date}} AND op.snapshot_ts = {_STOCK_TS}
          AND op.warehouse_id != 0 AND op.warehouse_id IN {_SM_WH}

        UNION ALL

        SELECT
            a.company_id AS company_id, a.warehouse_id AS warehouse_id, a.product_id AS product_id,
            toUInt8(3) AS kind, toUInt8(0) AS tracked, {_Z3} AS on_hand, {_Z3} AS free, {_Z3} AS min_q, {_Z3} AS max_q,
            {_Z2} AS v, toInt64(0) AS age, {_NULL_DAYS} AS exp_days
        FROM efn_dwh.agg_sm_stock_daily AS a
        WHERE a.day = {{day:Date}}
    ) AS u
    GROUP BY u.company_id, u.warehouse_id, u.product_id
) AS g
LEFT JOIN (SELECT product_id, categ_id FROM efn_dwh.dim_product FINAL) AS p ON p.product_id = g.product_id
LEFT JOIN
(
    SELECT o.warehouse_id AS warehouse_id, f.product_id AS product_id, toFloat64(sum(f.qty)) AS qty
    FROM
    (
        SELECT store_id, product_id, qty
        FROM efn_dwh.fact_sales_line FINAL
        WHERE source = 'pos' AND line_kind = 'product' AND is_refund = 0
          AND toDate(order_date) >= {{day:Date}} - 28 AND toDate(order_date) < {{day:Date}}
    ) AS f
    INNER JOIN
    (
        SELECT store_id, assumeNotNull(warehouse_id) AS warehouse_id
        FROM efn_dwh.dim_outlet FINAL WHERE isNotNull(warehouse_id)
    ) AS o ON o.store_id = f.store_id
    GROUP BY o.warehouse_id, f.product_id
) AS sl ON sl.warehouse_id = g.warehouse_id AND sl.product_id = g.product_id
"""  # noqa: S608 - hanya potongan SQL konstanta; nilai lewat parameter server {day:Date}

# Alert terbuka hari ini (WIB) per PT x warehouse x kategori x tahap. Nilai = qty x HPP satuan snapshot terakhir.
# Kunci yang sudah ada hari ini ikut dengan nilai 0 (alert yang ditutup setelah hitungan sebelumnya tidak tertinggal).
SQL_EXPIRY_AGG: Final = """
INSERT INTO efn_dwh.agg_sm_expiry_daily
    (day, company_id, warehouse_id, categ_id, stage, lots, qty, value_cost, computed_at)
SELECT {day:Date}, u.company_id, u.warehouse_id, u.categ_id, u.stage, toUInt32(sum(u.lots)), sum(u.qty),
       sum(u.value_cost), now64(6)
FROM
(
    SELECT
        e.company_id AS company_id, e.warehouse_id AS warehouse_id, e.categ_id AS categ_id, toString(e.stage) AS stage,
        toUInt64(uniqExact(e.lot_id)) AS lots, toDecimal128(sum(e.qty), 3) AS qty,
        toDecimal128(sum(toDecimal128(toDecimal128(e.qty, 3)
                                      * toDecimal128(ifNull(c.unit_cost, toDecimal64(0, 4)), 4), 2)), 2) AS value_cost
    FROM
    (
        SELECT company_id, warehouse_id, categ_id, stage, lot_id, qty, product_id
        FROM efn_dwh.fact_lot_expiry FINAL
        WHERE state IN ('open', 'actioned')
    ) AS e
    INNER JOIN (SELECT warehouse_id, company_id FROM efn_dwh.dim_sm_warehouse FINAL) AS w
        ON w.warehouse_id = e.warehouse_id AND w.company_id = e.company_id
    LEFT JOIN efn_dwh.v_sm_unit_cost AS c ON c.company_id = e.company_id AND c.product_id = e.product_id
    GROUP BY e.company_id, e.warehouse_id, e.categ_id, stage

    UNION ALL

    SELECT company_id, warehouse_id, categ_id, toString(stage) AS stage, toUInt64(0) AS lots, toDecimal128(0, 3) AS qty,
           toDecimal128(0, 2) AS value_cost
    FROM efn_dwh.agg_sm_expiry_daily
    WHERE day = {day:Date}
) AS u
GROUP BY u.company_id, u.warehouse_id, u.categ_id, u.stage
"""


# ---------------------------------------------------------------- kampanye & segmen (#117, fit-gap C-4)
def sql_campaigns(codes: Sequence[str]) -> str:
    # Tanpa maker_id, approved_by_id, cancelled_by_id, partner_ids, editor_ids, isi pesan, segment_metrics/summary.
    return f"""
SELECT k.id AS campaign_id, k.company_id, k.name, k.kind, k.origin, k.state,
       COALESCE(k.voucher_mode, '') AS voucher_mode,
       COALESCE(k.channel_inbox, FALSE) AS channel_inbox, COALESCE(k.channel_push, FALSE) AS channel_push,
       COALESCE(k.channel_email, FALSE) AS channel_email,
       COALESCE(k.channel_wa_unofficial, FALSE) AS channel_wa_unofficial,
       k.date_from, k.date_to, k.approved_at, k.sent_at, COALESCE(k.approval_rev, 0) AS approval_rev, k.budget_idr,
       COALESCE(k.segment_run_id, '') AS segment_run_id, COALESCE(k.segment_code, '') AS segment_code,
       COALESCE(k.segment_member_count, 0) AS segment_member_count, k.segment_generated_at, k.write_date
FROM efn_campaign k
JOIN res_company c ON c.id = k.company_id
WHERE {company_filter("c", codes)}
  AND (k.write_date >= %(since_ts)s OR k.date_to >= %(attr_since)s)
  AND k.id > %(after_id)s
ORDER BY k.id
LIMIT %(limit)s
"""  # noqa: S608 - kode PT tervalidasi


SQL_CAMPAIGN_CARDS: Final = """
SELECT lc.efn_campaign_id AS campaign_id, COUNT(*) AS vouchers_issued
FROM loyalty_card lc
WHERE lc.efn_campaign_id = ANY(%(ids)s)
GROUP BY lc.efn_campaign_id
"""

_USED_POS = """
    SELECT cd.efn_campaign_id AS campaign_id, 'pos' AS channel, l.order_id, l.coupon_id AS card_id,
           COALESCE(l.is_reward_line, FALSE) AS is_reward, l.price_subtotal
    FROM pos_order_line l
    JOIN cards cd ON cd.id = l.coupon_id
    JOIN pos_order o ON o.id = l.order_id
    WHERE o.state IN ('paid', 'done')"""
# C-4: sale.order.line.is_reward_line tidak tersimpan -> baris reward = reward_id IS NOT NULL; hanya SO 'sale'.
_USED_SALE = """
    UNION ALL
    SELECT cd.efn_campaign_id, 'sale', sl.order_id, sl.coupon_id, sl.reward_id IS NOT NULL, sl.price_subtotal
    FROM sale_order_line sl
    JOIN cards cd ON cd.id = sl.coupon_id
    JOIN sale_order so ON so.id = sl.order_id
    WHERE so.state = 'sale'"""
_REV_SALE = """
    UNION ALL
    SELECT od.campaign_id, SUM(sl.price_subtotal)
    FROM orders od JOIN sale_order_line sl ON sl.order_id = od.order_id
    WHERE od.channel = 'sale'
    GROUP BY od.campaign_id"""


def sql_campaign_usage(online: bool) -> str:
    """Redemption per kampanye. Satu sumber per pesanan (C-4): kupon tercatat di dokumen tempat ia diterapkan --
    pos_order_line.coupon_id (POS/kiosk) atau sale_order_line.coupon_id (online). Pesanan online yang dilunasi di kasir
    (E-45) menjadi order POS berisi produk pelunasan TANPA kupon, jadi tidak dobel. Pesanan dihitung per
    (kanal, order_id); voucher dipakai = kartu berbeda lintas kanal. `online` = kolom sale_order_line.coupon_id terlihat
    oleh efn_etl (grant G47 #117 bagian online terpasang)."""
    used = _USED_POS + (_USED_SALE if online else "")
    rev_sale = _REV_SALE if online else ""
    return f"""
WITH cards AS (SELECT id, efn_campaign_id FROM loyalty_card WHERE efn_campaign_id = ANY(%(ids)s)),
used AS ({used}
),
orders AS (SELECT DISTINCT campaign_id, channel, order_id FROM used),
rev AS (
    SELECT od.campaign_id, SUM(l.price_subtotal) AS revenue
    FROM orders od JOIN pos_order_line l ON l.order_id = od.order_id
    WHERE od.channel = 'pos'
    GROUP BY od.campaign_id{rev_sale}
)
SELECT u.campaign_id, COUNT(DISTINCT u.card_id) AS vouchers_used,
       (SELECT COUNT(*) FROM orders od WHERE od.campaign_id = u.campaign_id) AS orders_with_voucher,
       COALESCE(-SUM(u.price_subtotal) FILTER (WHERE u.is_reward), 0) AS discount_idr,
       COALESCE((SELECT SUM(r.revenue) FROM rev r WHERE r.campaign_id = u.campaign_id), 0) AS revenue_with_voucher_idr
FROM used u
GROUP BY u.campaign_id
"""  # noqa: S608 - tanpa nilai masukan (potongan SQL konstanta)


SQL_ONLINE_COUPON_VISIBLE: Final = """
SELECT COUNT(*) AS n FROM information_schema.column_privileges
WHERE table_schema = 'public' AND table_name = 'sale_order_line' AND column_name IN ('coupon_id', 'reward_id')
  AND privilege_type = 'SELECT' AND grantee = current_user
"""

SQL_CAMPAIGN_STATS: Final = """
SELECT odoo_campaign_id, approval_rev, COALESCE(delivered, 0) AS delivered, COALESCE(skipped, 0) AS skipped,
       COALESCE(push_sent, 0) AS push_sent, COALESCE(email_queued, 0) AS email_queued,
       COALESCE(wa_queued, 0) AS wa_queued, COALESCE(stop_reason, '') AS stop_reason
FROM platform.v_campaign_stats
WHERE company_code = ANY(%(codes)s) AND odoo_campaign_id = ANY(%(ids)s)
"""

CAMPAIGN_COLUMNS: Final = (
    "campaign_id",
    "company_id",
    "name",
    "kind",
    "origin",
    "state",
    "voucher_mode",
    "channels",
    "date_from",
    "date_to",
    "approved_at",
    "sent_at",
    "approval_rev",
    "budget_idr",
    "segment_code",
    "segment_member_count",
    "delivered",
    "skipped",
    "push_sent",
    "email_queued",
    "wa_queued",
    "stop_reason",
    "vouchers_issued",
    "vouchers_used",
    "orders_with_voucher",
    "discount_idr",
    "revenue_with_voucher_idr",
    "computed_at",
)
SEGMENT_COLUMNS: Final = (
    "company_id",
    "run_id",
    "segment_code",
    "member_count",
    "generated_at",
    "campaign_id",
    "campaign_state",
    "write_date",
)
_STAT_FIELDS: Final = ("delivered", "skipped", "push_sent", "email_queued", "wa_queued")


@dataclass
class _Stats:
    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(_STAT_FIELDS, 0))
    stop_rev: int = -1
    stop_reason: str = ""


def merge_campaign_stats(rows: Iterable[Mapping[str, Any]]) -> dict[int, _Stats]:
    """Jumlah semua approval_rev per odoo_campaign_id (§28.13.8b); stop_reason dari rev terbesar."""
    out: dict[int, _Stats] = defaultdict(_Stats)
    for row in rows:
        acc = out[int(row["odoo_campaign_id"])]
        for name in _STAT_FIELDS:
            acc.counts[name] += max(int(row.get(name) or 0), 0)
        rev = int(row.get("approval_rev") or 0)
        if rev >= acc.stop_rev:
            acc.stop_rev = rev
            acc.stop_reason = _label(row.get("stop_reason"), 40)
    return dict(out)


def campaign_rows(
    campaigns: Sequence[Mapping[str, Any]],
    issued: Mapping[int, int],
    usage: Mapping[int, Mapping[str, Any]],
    stats: Mapping[int, _Stats],
    computed_at: datetime,
) -> list[list[Any]]:
    ts = to_utc(computed_at)
    out: list[list[Any]] = []
    for c in campaigns:
        cid = int(c["campaign_id"])
        used = usage.get(cid, {})
        st = stats.get(cid, _Stats())
        budget = int(c.get("budget_idr") or 0)
        out.append(
            [
                cid,
                int(c["company_id"]),
                _label(c.get("name"), 64),
                _label(c.get("kind"), 16),
                _label(c.get("origin"), 16),
                _label(c.get("state"), 16),
                _label(c.get("voucher_mode"), 16),
                [name for col, name in CAMPAIGN_CHANNELS if c.get(col)],
                _date(c["date_from"]),
                _date(c["date_to"]) if c.get("date_to") else _date(c["date_from"]),
                _opt_ts(c.get("approved_at")),
                _opt_ts(c.get("sent_at")),
                max(int(c.get("approval_rev") or 0), 0),
                budget if budget > 0 else None,
                _label(c.get("segment_code"), 48),
                max(int(c.get("segment_member_count") or 0), 0),
                *(st.counts[name] for name in _STAT_FIELDS),
                st.stop_reason,
                max(int(issued.get(cid, 0)), 0),
                max(int(used.get("vouchers_used") or 0), 0),
                max(int(used.get("orders_with_voucher") or 0), 0),
                _dec(used.get("discount_idr"), Q2),
                _dec(used.get("revenue_with_voucher_idr"), Q2),
                ts,
            ]
        )
    return out


def segment_rows(campaigns: Sequence[Mapping[str, Any]]) -> list[list[Any]]:
    """fact_sm_segment_run dari kampanye ber-segment_run_id (tanpa daftar anggota)."""
    out: list[list[Any]] = []
    for c in campaigns:
        run_id = _label(c.get("segment_run_id"), 36)
        generated = c.get("segment_generated_at")
        if not run_id or not isinstance(generated, datetime):
            continue
        out.append(
            [
                int(c["company_id"]),
                run_id,
                _label(c.get("segment_code"), 48),
                max(int(c.get("segment_member_count") or 0), 0),
                to_utc(generated),
                int(c["campaign_id"]),
                _label(c.get("state"), 16),
                to_utc(c["write_date"]),
            ]
        )
    return out


def campaign_window(
    watermark: datetime | None, now: datetime, overlap: timedelta, attr_days: int
) -> tuple[datetime, date]:
    """(write_date sejak, date_to sejak): perubahan sejak watermark - overlap + kampanye yang masih dalam jendela
    atribusi (redemption terus bertambah setelah kampanye terkirim)."""
    since_ts = datetime(1970, 1, 1, tzinfo=UTC) if watermark is None else to_utc(watermark) - overlap
    return since_ts, wib_date(now) - timedelta(days=attr_days)
