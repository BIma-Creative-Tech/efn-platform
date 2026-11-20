"""G47 (docs/04 §28.13.8, fit-gap G47 C-1..C-7): logika murni mart supermarket + job khusus dengan fake I/O."""

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from efn_etl import sm, sm_cycle
from efn_etl.config import Settings
from efn_etl.heartbeat import JobRun
from efn_etl.jobs import enabled_jobs
from efn_etl.runner import run_job
from efn_etl.waste import scrap_sql
from efn_etl.watermark import Cursor

ROOT = Path(__file__).resolve().parents[3]
GRANTS = (ROOT / "services" / "etl" / "sql" / "odoo-readonly-grants.sql").read_text(encoding="utf-8")
WD = datetime(2026, 11, 20, 3, 0, 0, 123456)  # naive = UTC (Odoo)
D = date(2026, 11, 21)


def at(day: date, hour: int) -> datetime:
    """Jam WIB pada `day` sebagai datetime UTC naive (Odoo)."""
    return datetime(day.year, day.month, day.day, hour, 0) - timedelta(hours=7)


# ---------------------------------------------------------------- konfigurasi & kode PT
def test_parse_sm_companies_fail_closed() -> None:
    assert sm.parse_sm_companies(None) == ("SMD",)
    assert sm.parse_sm_companies("") == ("SMD",)
    assert sm.parse_sm_companies(" smd , SMD ") == ("SMD",)
    for bad in ("PB", "SMD,PB", "XYZ", "SMD'; DROP", ","):
        with pytest.raises(ValueError):
            sm.parse_sm_companies(bad)


def test_company_filter_literal_is_validated() -> None:
    assert sm.company_filter("c", ("SMD",)) == "c.efn_brand_code IN ('SMD')"
    with pytest.raises(ValueError):
        sm.company_filter("c; --", ("SMD",))
    with pytest.raises(ValueError):
        sm.company_filter("c", ("PB",))


@pytest.fixture
def tls_env(tmp_path: Path) -> dict[str, str]:
    ca = tmp_path / "ca.crt"
    ca.write_text("-----BEGIN CERTIFICATE-----\ndummy\n-----END CERTIFICATE-----\n", encoding="utf-8")
    return {"APP_ENV": "production", "PG_SSLROOTCERT": str(ca), "CH_CA_FILE": str(ca), "AMQP_TLS_CA_FILE": str(ca)}


def test_settings_sm_flags_default_off(tls_env: dict[str, str]) -> None:
    s = Settings.from_env(tls_env)
    assert (s.sm_enabled, s.sm_expiry_enabled, s.sm_campaign_enabled) == (False, False, False)
    assert s.sm_companies == ("SMD",)
    assert (s.sm_fill_recalc_days, s.sm_campaign_attr_days, s.sm_stock_backfill_days) == (14, 30, 90)


@pytest.mark.parametrize(
    "override",
    [
        {"ETL_ENABLE_SM": "true"},  # tanpa ETL_ENABLE_ODOO
        {"ETL_ENABLE_ODOO": "true", "ETL_ENABLE_SM_EXPIRY": "true"},
        {"ETL_ENABLE_ODOO": "true", "ETL_ENABLE_SM_CAMPAIGN": "true"},
        {"ETL_ENABLE_ODOO": "true", "ETL_ENABLE_SM": "true", "ETL_SM_COMPANIES": "PB"},
        {"ETL_ENABLE_ODOO": "true", "ETL_ENABLE_SM": "true", "ETL_SM_FILL_RECALC_DAYS": "0"},
        # L-180: production + kampanye promo mati -> ditolak
        {"ETL_ENABLE_ODOO": "true", "ETL_ENABLE_SM": "true", "ETL_ENABLE_SM_CAMPAIGN": "true"},
    ],
)
def test_settings_sm_fail_closed(tls_env: dict[str, str], override: dict[str, str]) -> None:
    with pytest.raises(RuntimeError):
        Settings.from_env({**tls_env, **override})


def test_settings_sm_campaign_allowed_when_promo_on(tls_env: dict[str, str]) -> None:
    env = {
        **tls_env,
        "ETL_ENABLE_ODOO": "true",
        "ETL_ENABLE_SM": "true",
        "ETL_ENABLE_SM_CAMPAIGN": "true",
        "PROMO_CAMPAIGN_ENABLED": "true",
        "ETL_ENABLE_SM_EXPIRY": "true",
    }
    s = Settings.from_env(env)
    assert s.sm_enabled and s.sm_campaign_enabled and s.sm_expiry_enabled


def test_enabled_jobs_sm_wiring() -> None:
    names = {j.name for j in enabled_jobs(True)}
    core = {sm.JOB_DIMS_WAREHOUSE, sm.JOB_DIMS_OUTLET, sm.JOB_REPLENISHMENT, sm.JOB_PURCHASE, sm.JOB_SUPPLIER}
    assert not names & (core | {sm.JOB_EXPIRY, sm.JOB_MARKDOWN})
    with_sm = {j.name: j for j in enabled_jobs(True, sm=True)}
    assert core <= set(with_sm) and sm.JOB_EXPIRY not in with_sm
    assert {sm.JOB_EXPIRY, sm.JOB_MARKDOWN} <= {j.name for j in enabled_jobs(True, sm=True, sm_expiry=True)}
    assert not {j.name for j in enabled_jobs(False, sm=True, sm_expiry=True)} & core
    assert all(with_sm[n].source == "odoo" for n in core)


# ---------------------------------------------------------------- higiene SQL (minimisasi, keyset, filter PT)
FORBIDDEN = (
    "approved_by",
    "changed_by",
    "user_id",
    "activated_by",
    "maker_id",
    "approved_by_id",
    "cancelled_by_id",
    "partner_ids",
    "editor_ids",
    "message_title",
    "message_body",
    "segment_metrics",
    "member_id",
    "payload",
)


def _all_sm_sql() -> dict[str, str]:
    out = {j.name: j.sql for j in sm.sm_jobs(("SMD",), expiry=True)}
    out.update(
        {
            "kpi": sm.sql_kpi_all(("SMD",)),
            "bias": sm.sql_bias(("SMD",)),
            "fill_lines": sm.sql_fill_lines(("SMD",)),
            "fill_moves": sm.SQL_FILL_MOVES,
            "campaigns": sm.sql_campaigns(("SMD",)),
            "cards": sm.SQL_CAMPAIGN_CARDS,
            "usage_online": sm.sql_campaign_usage(True),
            "usage_pos": sm.sql_campaign_usage(False),
            "stats": sm.SQL_CAMPAIGN_STATS,
            "scraps": sm.sm_scrap_job(("SMD",), False, False).sql,
        }
    )
    return out


@pytest.mark.parametrize("name", sorted(_all_sm_sql()))
def test_sql_minimised(name: str) -> None:
    sql = _all_sm_sql()[name]
    words = set(re.findall(r"[a-z_]+", sql))
    assert not words & set(FORBIDDEN), (name, words & set(FORBIDDEN))
    assert "select *" not in sql.lower()
    assert "{" not in sql.replace("{day:Date}", ""), name  # tidak ada placeholder format tertinggal


def test_incremental_jobs_keyset_and_company_filter() -> None:
    for job in sm.sm_jobs(("SMD",), expiry=True):
        for token in ("%(cursor_ts)s", "%(cursor_key)s", "LIMIT %(limit)s", "_cursor_ts", "_cursor_key", "ORDER BY"):
            assert token in job.sql, (job.name, token)
        assert "efn_brand_code IN ('SMD')" in job.sql, job.name
    codes = ("SMD",)
    for sql in (sm.sql_kpi_all(codes), sm.sql_bias(codes), sm.sql_fill_lines(codes), sm.sql_campaigns(codes)):
        assert "efn_brand_code IN ('SMD')" in sql


def test_supplier_never_reads_res_partner_directly() -> None:
    for sql in _all_sm_sql().values():
        assert not re.search(r"\bres_partner\b", sql)
    assert "efn_etl.purchase_partner" in sm.sm_jobs(("SMD",))[3].sql


def test_online_redemption_uses_reward_id_and_confirmed_so() -> None:
    online = sm.sql_campaign_usage(True)
    assert "sl.reward_id IS NOT NULL" in online and "so.state = 'sale'" in online
    assert "is_reward_line" not in online.split("UNION ALL", 1)[1].split("orders AS", 1)[0]  # C-4
    pos_only = sm.sql_campaign_usage(False)
    assert "sale_order" not in pos_only and "o.state IN ('paid', 'done')" in pos_only


def test_fill_moves_sql_follows_transit_and_crossing_rule() -> None:
    sql = sm.SQL_FILL_MOVES
    assert "ld.usage = 'transit'" not in sql  # dest_usage dipilih di CTE first_moves
    assert "f.dest_usage = 'transit'" in sql and "l2.usage = 'transit'" in sql
    assert "sp.backorder_id = ch.picking_id" in sql and "ch.depth < 3" in sql and "d.depth < 2" in sql
    assert "stock_move_move_rel" in sql and "ls.warehouse_id" in sql


def test_scrap_sql_detects_efn_columns() -> None:
    plain = scrap_sql(False, False)
    # c.efn_brand_code (efn_core, selalu ada) boleh; kolom opsional scrap s.efn_* tidak.
    assert not re.search(r"\bs\.efn_\w+", plain) and "sl.warehouse_id" in plain and "mv.value" in plain and "standard_price" in plain
    full = scrap_sql(True, True)
    assert "COALESCE(s.efn_warehouse_id, sl.warehouse_id, 0)" in full and "COALESCE(s.efn_waste_value," in full


# ---------------------------------------------------------------- grant Odoo menutup kolom yang dibaca
def _granted(table: str) -> set[str]:
    cols: set[str] = set()
    for found in re.findall(rf"GRANT SELECT \(([^)]*)\)\s+ON public\.{table} TO efn_etl", GRANTS):
        cols |= {c.strip() for c in found.split(",")}
    for found in re.findall(rf"GRANT SELECT \((\w+)\) ON public\.{table} TO efn_etl", GRANTS):
        cols.add(found)
    return cols


ALIASES = {
    sm.JOB_DIMS_WAREHOUSE: {"w": "stock_warehouse", "c": "res_company"},
    sm.JOB_DIMS_OUTLET: {"o": "efn_online_outlet", "ch": "efn_online_channel"},
    sm.JOB_REPLENISHMENT: {"l": "efn_replenishment_proposal_line", "p": "efn_replenishment_proposal"},
    sm.JOB_PURCHASE: {
        "pol": "purchase_order_line",
        "po": "purchase_order",
        "spt": "stock_picking_type",
        "op": "stock_warehouse_orderpoint",
        "w": "stock_warehouse",
    },
    sm.JOB_EXPIRY: {"a": "efn_expiry_alert", "lot": "stock_lot"},
    sm.JOB_MARKDOWN: {"m": "efn_lot_markdown"},
    "kpi": {"k": "efn_insight_sm_kpi"},
    "bias": {"b": "efn_replenishment_bias_log"},
    "fill_lines": {"l": "efn_replenishment_proposal_line", "p": "efn_replenishment_proposal"},
    "fill_moves": {
        "sp": "stock_picking",
        "m": "stock_move",
        "m2": "stock_move",
        "ld": "stock_location",
        "ls": "stock_location",
        "l2": "stock_location",
        "r": "stock_move_move_rel",
        "mu": "uom_uom",
        "pu": "uom_uom",
        "pt": "product_template",
    },
    "campaigns": {"k": "efn_campaign"},
    "cards": {"lc": "loyalty_card"},
    "usage_online": {"l": "pos_order_line", "o": "pos_order", "sl": "sale_order_line", "so": "sale_order"},
    "scraps": {"s": "stock_scrap", "sl": "stock_location", "mv": "stock_move"},
}


@pytest.mark.parametrize("name", sorted(ALIASES))
def test_odoo_grants_cover_columns_read(name: str) -> None:
    sql = _all_sm_sql()[name]
    for alias, table in ALIASES[name].items():
        read = set(re.findall(rf"(?<![\w.]){re.escape(alias)}\.([a-z_]+)", sql))
        if not read:
            continue
        granted = _granted(table) | {"id"}
        assert read <= granted, (name, table, read - granted)


def test_g47_grants_exclude_actor_and_partner_columns() -> None:
    block = GRANTS.split("Gelombang 47 (efn-data-ai-engineer)", 1)[1]
    granted = set()
    for found in re.findall(r"GRANT SELECT \(([^)]*)\)", block):
        granted |= {c.strip() for c in found.split(",")}
    assert not granted & set(FORBIDDEN)
    assert "ON public.res_partner" not in block  # supplier lewat view efn_etl.purchase_partner
    assert "CASE WHEN COALESCE(p.is_company, FALSE) THEN COALESCE(p.name, '') ELSE '' END AS name" in block
    assert "\\if :efn_g47_sm" in block and "\\if :efn_g47_expiry" in block and "\\if :efn_g47_campaign" in block


# ---------------------------------------------------------------- transform
def test_transform_warehouse_and_outlet() -> None:
    row = {
        "warehouse_id": 21,
        "code": "SMD01",
        "name": " Outlet 1 ",
        "company_id": 11,
        "company_code": "smd",
        "is_dc": False,
        "active": True,
        "write_date": WD,
    }
    assert sm.transform_warehouse(row) == [21, "SMD01", "Outlet 1", 11, "SMD", 0, 1, WD.replace(tzinfo=UTC)]
    out = {
        "channel_code": "Web-SMD",
        "outlet_code": "smd-01",
        "outlet_id": 3,
        "company_id": 11,
        "warehouse_id": 21,
        "active": True,
        "write_date": WD,
    }
    assert sm.transform_online_outlet(out)[:2] == ["Web-SMD", "smd-01"]  # L-190: peka huruf besar
    with pytest.raises(ValueError):
        sm.transform_online_outlet({**out, "outlet_code": ""})


def _repl_row(**kw: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "line_id": 7,
        "proposal_id": 3,
        "company_id": 11,
        "warehouse_id": 21,
        "supply_warehouse_id": 20,
        "schedule_id": 1,
        "delivery_date": D,
        "cutoff_at": WD,
        "state": "approved",
        "approved_at": WD,
        "picking_id": 55,
        "run_id": 9,
        "product_id": 100,
        "forecast_qty": 12.3456,
        "on_hand": 1,
        "in_transit": 0,
        "qty_need": 11,
        "qty_shelf_cap": 0,
        "qty_rack_cap": 0,
        "qty_proposed": 12,
        "qty_approved": 10,
        "excluded": False,
        "reason_codes": "pack_round",
        "change_reason": "rack_full",
        "header_ts": WD,
        "version_ts": WD,
    }
    row.update(kw)
    return row


def test_transform_replenishment() -> None:
    out = dict(zip(sm.REPLENISHMENT_COLUMNS, sm.transform_replenishment(_repl_row()), strict=True))
    assert out["cutoff_at"].microsecond == 0 and out["approved_at"].tzinfo is UTC
    assert out["forecast_qty"] == Decimal("12.346") and out["qty_approved"] == Decimal("10.000")
    assert out["state"] == "approved" and out["header_ts"] == WD.replace(tzinfo=UTC)
    with pytest.raises(ValueError):
        sm.transform_replenishment(_repl_row(state="weird"))


def test_transform_purchase_and_supplier_minimisation() -> None:
    row = {
        "line_id": 1,
        "order_id": 2,
        "company_id": 11,
        "warehouse_id": 20,
        "supplier_id": 77,
        "product_id": 100,
        "po_state": "purchase",
        "date_order": WD,
        "date_approve": None,
        "date_planned": WD,
        "product_qty": 5,
        "qty_received": 2,
        "price_subtotal": "10000.005",
        "from_orderpoint": True,
        "version_ts": WD,
    }
    out = dict(zip(sm.PURCHASE_COLUMNS, sm.transform_purchase(row), strict=True))
    assert out["from_orderpoint"] == 1 and out["date_approve"] is None and out["price_subtotal"] == Decimal("10000.00")
    assert (
        sm.transform_supplier(
            {"supplier_id": 77, "name": "Budi Santoso", "is_company": False, "write_date": WD, "_cursor_ts": WD}
        )[1]
        == ""
    )
    assert (
        sm.transform_supplier(
            {"supplier_id": 78, "name": "PT Pemasok", "is_company": True, "write_date": WD, "_cursor_ts": WD}
        )[1]
        == "PT Pemasok"
    )


def test_transform_expiry_and_markdown() -> None:
    row = {
        "alert_id": 1,
        "company_id": 11,
        "warehouse_id": 21,
        "lot_id": 5,
        "lot_name": "L-1",
        "product_id": 100,
        "categ_id": None,
        "stage": "markdown",
        "state": "open",
        "detected_date": D,
        "last_run_date": None,
        "days_left": 3,
        "expiration_date": WD,
        "qty": 4,
        "write_date": WD,
    }
    out = dict(zip(sm.EXPIRY_COLUMNS, sm.transform_expiry(row), strict=True))
    assert out["last_run_date"] == D and out["categ_id"] == 0 and out["expiration_date"].microsecond == 0
    with pytest.raises(ValueError):
        sm.transform_expiry({**row, "stage": "soon"})
    md = {
        "markdown_id": 9,
        "company_id": 11,
        "warehouse_id": 21,
        "lot_id": 5,
        "product_id": 100,
        "alert_id": None,
        "state": "active",
        "price": 0,
        "discount_pct": "20",
        "date_from": D,
        "date_to": D,
        "qty_cap": 0,
        "qty_sold": 1,
        "notify_members": False,
        "write_date": WD,
    }
    mout = dict(zip(sm.MARKDOWN_COLUMNS, sm.transform_markdown(md), strict=True))
    assert mout["price"] is None and mout["discount_pct"] == Decimal("20.00") and mout["alert_id"] == 0


# ---------------------------------------------------------------- KPI (L-200)
def test_kpi_sync_rows() -> None:
    old = datetime(2026, 11, 1, tzinfo=UTC)
    existing = {(11, "oos.basis"): (1, old), (11, "fill.basis"): (1, old), (11, "gmroi.period"): (1, WD)}
    rows = [
        {"company_id": 11, "key": "oos.basis", "value": "free", "active": True, "write_date": WD},  # berubah
        {"company_id": 11, "key": "gmroi.period", "value": "month", "active": True, "write_date": WD},  # sama
        {"company_id": 11, "key": "critical.cover_days", "value": "99", "active": True, "write_date": WD},  # rentang
        {"company_id": 11, "key": "no.such.key", "value": "1", "active": True, "write_date": WD},
    ]
    res = sm.kpi_sync_rows(rows, existing, WD)
    by_key = {r[1]: r for r in res.rows}
    assert by_key["oos.basis"][2:5] == [None, "free", 1]
    assert "gmroi.period" not in by_key  # tidak ditulis ulang (idempoten)
    assert by_key["critical.cover_days"][4] == 0 and res.invalid_values == 1  # tidak sah -> default
    assert res.unknown_keys == 1
    # fill.basis dihapus di Odoo -> active 0 dengan versi lebih baru
    assert by_key["fill.basis"][4] == 0 and by_key["fill.basis"][5] > old and res.deactivated == 1
    # Jalankan ulang dengan state baru -> tidak ada yang perlu ditulis
    state = {(r[0], r[1]): (r[4], r[5]) for r in res.rows} | {(11, "gmroi.period"): (1, WD)}
    again = sm.kpi_sync_rows(rows, state, WD)
    assert [r[1] for r in again.rows] == []


# ---------------------------------------------------------------- bias (MergeTree, dedup)
def _bias(log_id: int) -> dict[str, Any]:
    return {
        "log_id": log_id,
        "company_id": 11,
        "kind": "outlet_change",
        "proposal_id": 3,
        "warehouse_id": 21,
        "product_id": 100,
        "run_id": 9,
        "purchase_line_id": None,
        "qty_system": 10,
        "qty_before": 10,
        "qty_after": 12,
        "change_reason": "event",
        "created_at": WD,
    }


def test_new_bias_rows_dedup() -> None:
    rows = sm.new_bias_rows([_bias(1), _bias(2), _bias(2), _bias(3)], existing_ids=[1])
    assert [r[0] for r in rows] == [2, 3]
    with pytest.raises(ValueError):
        sm.new_bias_rows([{**_bias(4), "kind": "manual"}], [])


# ---------------------------------------------------------------- fill rate (C-3)
def _line(line_id: int, proposal: int, qty: str, day: date = D, picking: int = 100, product: int = 7) -> sm.FillLine:
    return sm.FillLine(line_id, proposal, 11, 21, 20, product, day, Decimal(qty), picking)


def _mv(
    kind: str, move_id: int, qty: str, when: datetime, root: int = 100, product: int = 7, dst: int = 0, src: int = 0
) -> dict[str, Any]:
    return {
        "kind": kind,
        "root_id": root,
        "move_id": move_id,
        "product_id": product,
        "qty": qty,
        "done_at": when,
        "dest_warehouse_id": dst,
        "src_warehouse_id": src,
    }


def test_fill_rate_two_step_receipt_counted_once() -> None:
    moves = [
        _mv("ship", 1, "10", at(D, 6)),
        _mv("recv", 2, "10", at(D, 9), dst=21, src=0),  # transit -> Input outlet (melintasi batas)
        _mv("recv", 3, "10", at(D, 10), dst=21, src=21),  # Input -> Stock outlet (di dalam outlet): TIDAK dihitung
    ]
    (row,) = sm.fill_rate_rows([_line(1, 3, "10")], moves, WD)
    out = dict(zip(sm.FILL_COLUMNS, row, strict=True))
    assert out["qty_shipped"] == Decimal("10.000") and out["qty_received"] == Decimal("10.000")
    assert out["on_time"] == 1 and out["picking_shared"] == 0
    assert out["last_received_at"] == at(D, 9).replace(tzinfo=UTC)


def test_fill_rate_backorder_late_and_partial() -> None:
    moves = [_mv("ship", 1, "6", at(D, 6)), _mv("ship", 2, "4", at(D + timedelta(days=1), 6))]
    (row,) = sm.fill_rate_rows([_line(1, 3, "10")], moves, WD)
    out = dict(zip(sm.FILL_COLUMNS, row, strict=True))
    assert out["qty_shipped"] == Decimal("10.000") and out["on_time"] == 0
    assert out["last_done_at"] == at(D + timedelta(days=1), 6).replace(tzinfo=UTC)
    # Penerimaan di gudang lain (salah kirim) tidak dihitung outlet ini.
    (row2,) = sm.fill_rate_rows([_line(1, 3, "10")], [_mv("recv", 9, "10", at(D, 9), dst=22, src=0)], WD)
    assert row2[9] == Decimal("0.000")


def test_fill_rate_shared_picking_fifo_and_excess_to_last() -> None:
    lines = [_line(1, 3, "5", D), _line(2, 4, "5", D + timedelta(days=1))]
    rows = sm.fill_rate_rows(lines, [_mv("ship", 1, "8", at(D, 6))], WD)
    assert [r[8] for r in rows] == [Decimal("5.000"), Decimal("3.000")]
    assert [r[13] for r in rows] == [1, 1]  # picking dipakai 2 usulan
    excess = sm.fill_rate_rows(lines, [_mv("ship", 1, "12", at(D, 6))], WD)
    assert [r[8] for r in excess] == [Decimal("5.000"), Decimal("7.000")]


def test_fill_rate_without_picking_and_duplicate_moves() -> None:
    rows = sm.fill_rate_rows([_line(1, 3, "4", picking=0)], [_mv("ship", 1, "4", at(D, 6), root=0)], WD)
    assert rows[0][8] == Decimal("0.000") and rows[0][12] == 0
    dup = [_mv("ship", 1, "4", at(D, 6)), _mv("ship", 1, "4", at(D, 6))]
    assert sm.fill_rate_rows([_line(1, 3, "4")], dup, WD)[0][8] == Decimal("4.000")


def test_changed_fill_rows_skips_identical() -> None:
    rows = sm.fill_rate_rows([_line(1, 3, "10")], [_mv("ship", 1, "10", at(D, 6))], WD)
    r = rows[0]
    same = {1: (r[8], r[9], r[10], r[11], r[12], r[13], r[7])}
    assert sm.changed_fill_rows(rows, same) == []
    assert sm.changed_fill_rows(rows, {1: (Decimal("9"), r[9], r[10], r[11], r[12], r[13], r[7])}) == rows
    assert sm.changed_fill_rows(rows, {}) == rows


# ---------------------------------------------------------------- stok harian
def test_stock_days_pending_and_since() -> None:
    runs = [
        (D - timedelta(days=1), datetime(2026, 11, 20, 16, tzinfo=UTC)),
        (D, datetime(2026, 11, 21, 2, tzinfo=UTC)),
    ]
    assert sm.stock_days_pending(runs, None, 14) == runs
    assert sm.stock_days_pending(runs, datetime(2026, 11, 20, 16, tzinfo=UTC), 14) == runs[1:]
    assert sm.stock_days_pending(runs, None, 1) == runs[:1]
    assert sm.stock_days_pending(runs, datetime(2026, 11, 21, 2, tzinfo=UTC), 14) == []
    with pytest.raises(ValueError):
        sm.stock_days_pending(runs, None, 0)
    now = datetime(2026, 11, 21, 3, tzinfo=UTC)
    assert sm.stock_since(None, now, 90) == D - timedelta(days=90)
    assert sm.stock_since(datetime(2026, 11, 20, 18, tzinfo=UTC), now, 90) == D  # 01:00 WIB 21 Nov


# ---------------------------------------------------------------- kampanye
def _campaign(**kw: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "campaign_id": 5,
        "company_id": 11,
        "name": "x" * 80,
        "kind": "segment",
        "origin": "manual",
        "state": "sent",
        "voucher_mode": "member_bound",
        "channel_inbox": True,
        "channel_push": False,
        "channel_email": True,
        "channel_wa_unofficial": False,
        "date_from": D,
        "date_to": D + timedelta(days=7),
        "approved_at": WD,
        "sent_at": None,
        "approval_rev": 2,
        "budget_idr": 0,
        "segment_run_id": "r-1",
        "segment_code": "viewed_not_bought",
        "segment_member_count": 120,
        "segment_generated_at": WD,
        "write_date": WD,
    }
    row.update(kw)
    return row


def test_campaign_rows_merge_sources() -> None:
    stats = sm.merge_campaign_stats(
        [
            {
                "odoo_campaign_id": 5,
                "approval_rev": 1,
                "delivered": 10,
                "skipped": 1,
                "push_sent": 0,
                "email_queued": 3,
                "wa_queued": 0,
                "stop_reason": "old",
            },
            {
                "odoo_campaign_id": 5,
                "approval_rev": 2,
                "delivered": 5,
                "skipped": None,
                "push_sent": 2,
                "email_queued": 1,
                "wa_queued": 0,
                "stop_reason": "budget_cap",
            },
        ]
    )
    usage = {
        5: {"vouchers_used": 3, "orders_with_voucher": 4, "discount_idr": "15000", "revenue_with_voucher_idr": 90000}
    }
    (row,) = sm.campaign_rows([_campaign()], {5: 50}, usage, stats, WD)
    out = dict(zip(sm.CAMPAIGN_COLUMNS, row, strict=True))
    assert len(out["name"]) == 64 and out["channels"] == ["inbox", "email"] and out["budget_idr"] is None
    assert (out["delivered"], out["skipped"], out["push_sent"], out["email_queued"]) == (15, 1, 2, 4)
    assert out["stop_reason"] == "budget_cap"
    assert (out["vouchers_issued"], out["vouchers_used"], out["orders_with_voucher"]) == (50, 3, 4)
    assert out["discount_idr"] == Decimal("15000.00")
    (empty,) = sm.campaign_rows([_campaign(budget_idr=500000)], {}, {}, {}, WD)
    e = dict(zip(sm.CAMPAIGN_COLUMNS, empty, strict=True))
    assert e["budget_idr"] == 500000 and e["vouchers_used"] == 0 and e["revenue_with_voucher_idr"] == Decimal("0.00")


def test_segment_rows_require_run_and_time() -> None:
    rows = sm.segment_rows(
        [_campaign(), _campaign(campaign_id=6, segment_run_id=""), _campaign(campaign_id=7, segment_generated_at=None)]
    )
    assert len(rows) == 1 and dict(zip(sm.SEGMENT_COLUMNS, rows[0], strict=True))["run_id"] == "r-1"


# ---------------------------------------------------------------- fake I/O untuk job khusus
class FakeSink:
    def __init__(self) -> None:
        self.inserts: list[tuple[str, list[list[Any]]]] = []

    def insert(self, table: str, columns: Any, rows: list[list[Any]]) -> None:
        if rows:
            self.inserts.append((table, rows))


class FakeStore:
    def __init__(self) -> None:
        self.marks: dict[str, datetime] = {}

    def get(self, job: str) -> datetime | None:
        return self.marks.get(job)

    def set(self, job: str, watermark: datetime, rows_loaded: int) -> None:
        self.marks[job] = watermark


class FakeCh:
    def __init__(self, rows: dict[str, list[tuple[Any, ...]]] | None = None) -> None:
        self.data = rows or {}
        self.executed: list[tuple[str, dict[str, Any]]] = []

    def rows(self, sql: str, parameters: dict[str, Any] | None = None) -> list[tuple[Any, ...]]:
        for needle, value in self.data.items():
            if needle in sql:
                return value
        return []

    def execute(self, sql: str, parameters: dict[str, Any]) -> None:
        self.executed.append((sql, parameters))


class KeysetSource:
    """Sumber Odoo palsu: menerapkan keyset (_cursor_ts, _cursor_key) seperti SQL job."""

    def __init__(self, rows: list[dict[str, Any]], queries: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.rows_ = sorted(rows, key=lambda r: (r["_cursor_ts"], r["_cursor_key"]))
        self.queries = queries or {}

    def fetch(self, sql: str, cursor: Cursor, limit: int) -> list[dict[str, Any]]:
        out = []
        for r in self.rows_:
            ts = r["_cursor_ts"].replace(tzinfo=UTC)
            if ts >= cursor.ts and (ts, r["_cursor_key"]) > (cursor.ts, cursor.key):
                out.append(r)
        return out[:limit]

    def query(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        for needle, value in self.queries.items():
            if needle in sql:
                return value(params) if callable(value) else value
        return []


def test_runner_idempotent_rerun_replacing_merge_tree() -> None:
    """Re-run dengan overlap membaca ulang baris; kunci ORDER BY RMT + versi -> state akhir identik (ADR-003)."""
    rows = [
        {
            **_repl_row(line_id=i, version_ts=WD + timedelta(seconds=i), header_ts=WD),
            "_cursor_ts": WD + timedelta(seconds=i),
            "_cursor_key": str(i),
        }
        for i in range(1, 8)
    ]
    job = next(j for j in sm.sm_jobs(("SMD",)) if j.name == sm.JOB_REPLENISHMENT)
    sink, store = FakeSink(), FakeStore()
    src = KeysetSource(rows)

    order_by = ("company_id", "warehouse_id", "delivery_date", "proposal_id", "line_id")

    def state() -> dict[tuple[Any, ...], list[Any]]:
        idx = {c: i for i, c in enumerate(sm.REPLENISHMENT_COLUMNS)}
        rmt: dict[tuple[Any, ...], list[Any]] = {}
        for _table, batch in sink.inserts:
            for r in batch:
                key = tuple(r[idx[c]] for c in order_by)
                if key not in rmt or r[idx["version_ts"]] >= rmt[key][idx["version_ts"]]:
                    rmt[key] = r
        return rmt

    run_job(job, src, sink, store, timedelta(minutes=5), 3, 50)
    first = state()
    run_job(job, src, sink, store, timedelta(minutes=5), 3, 50)  # overlap: baca ulang
    assert state() == first and len(first) == 7
    assert store.marks[sm.JOB_REPLENISHMENT] == (WD + timedelta(seconds=7)).replace(tzinfo=UTC)


def test_run_sm_bias_dedups_against_clickhouse() -> None:
    rows = [{**_bias(i), "_cursor_ts": WD + timedelta(seconds=i), "_cursor_key": str(i)} for i in (1, 2, 3)]
    sink, store = FakeSink(), FakeStore()
    ch = FakeCh({"fact_replenishment_bias WHERE log_id IN": [(1,), (2,)]})
    res = sm_cycle.run_sm_bias(KeysetSource(rows), ch, sink, store, ("SMD",), timedelta(minutes=5), 10, 5)
    assert res.rows == 1 and [r[0] for r in sink.inserts[0][1]] == [3]
    assert store.marks[sm.JOB_BIAS] == (WD + timedelta(seconds=3)).replace(tzinfo=UTC)


def test_run_sm_stock_agg_processes_pending_days_and_moves_watermark() -> None:
    ts1, ts2 = datetime(2026, 11, 20, 16, tzinfo=UTC), datetime(2026, 11, 21, 2, tzinfo=UTC)
    ch = FakeCh({"FROM efn_dwh.stock_snapshot_run": [(D - timedelta(days=1), ts1), (D, ts2)]})
    store = FakeStore()
    store.marks[sm.JOB_STOCK_AGG] = ts1
    done = sm_cycle.run_sm_stock_agg(ch, store, datetime(2026, 11, 21, 3, tzinfo=UTC), 90)
    assert done == [D] and store.marks[sm.JOB_STOCK_AGG] == ts2
    assert ch.executed[0][1] == {"day": D} and "INSERT INTO efn_dwh.agg_sm_stock_daily" in ch.executed[0][0]


def test_run_sm_fill_rate_end_to_end_writes_only_changes() -> None:
    lines = [
        {
            "line_id": 1,
            "proposal_id": 3,
            "company_id": 11,
            "warehouse_id": 21,
            "dc_warehouse_id": 20,
            "product_id": 7,
            "delivery_date": D,
            "qty_approved": 10,
            "picking_id": 100,
        }
    ]
    moves = [_mv("ship", 1, "10", at(D, 6))]
    odoo = KeysetSource(
        [],
        {
            "FROM efn_replenishment_proposal p": lambda p: lines if p["after_id"] == 0 else [],
            "WITH RECURSIVE chain": moves,
        },
    )
    sink = FakeSink()
    res = sm_cycle.run_sm_fill_rate(odoo, FakeCh(), sink, ("SMD",), WD, 14, 100, 5)
    assert res.rows == 1 and sink.inserts[0][0] == "fact_dc_fill_rate"
    written = sink.inserts[0][1][0]
    existing = FakeCh({"FROM efn_dwh.fact_dc_fill_rate FINAL": [(1, *written[8:14], written[7])]})
    sink2 = FakeSink()
    assert sm_cycle.run_sm_fill_rate(odoo, existing, sink2, ("SMD",), WD, 14, 100, 5).rows == 0
    assert sink2.inserts == []


def test_run_sm_campaign_with_online_detection() -> None:
    odoo = KeysetSource(
        [],
        {
            "FROM efn_campaign k": lambda p: [_campaign()] if p["after_id"] == 0 else [],
            "information_schema.column_privileges": [{"n": 2}],
            "COUNT(*) AS vouchers_issued": [{"campaign_id": 5, "vouchers_issued": 9}],
            "WITH cards AS": lambda p: [
                {
                    "campaign_id": 5,
                    "vouchers_used": 2,
                    "orders_with_voucher": 2,
                    "discount_idr": 1000,
                    "revenue_with_voucher_idr": 5000,
                }
            ],
        },
    )
    platform = KeysetSource([], {"platform.v_campaign_stats": []})
    sink, store = FakeSink(), FakeStore()
    res = sm_cycle.run_sm_campaign(odoo, platform, sink, store, ("SMD",), WD, timedelta(minutes=5), 30, 100)
    assert res.online and res.campaigns == 1 and res.segments == 1
    assert {t for t, _ in sink.inserts} == {"fact_campaign_result", "fact_sm_segment_run"}
    assert store.marks[sm.JOB_CAMPAIGN] == WD.replace(tzinfo=UTC)


def test_run_sm_cycle_isolates_failures() -> None:
    class Boom(KeysetSource):
        def query(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
            if "efn_insight_sm_kpi" in sql:
                raise RuntimeError("db down")
            return super().query(sql, params)

    beats: list[JobRun] = []
    cfg = sm_cycle.SmCycleConfig(
        ("SMD",),
        stock=False,
        expiry=True,
        campaign=False,
        fill_recalc_days=14,
        campaign_attr_days=30,
        stock_backfill_days=90,
        overlap=timedelta(minutes=5),
        batch_size=100,
        max_pages=5,
    )
    status = sm_cycle.run_sm_cycle(
        cfg, Boom([]), KeysetSource([]), FakeCh(), FakeSink(), FakeStore(), beats.append, now=WD.replace(tzinfo=UTC)
    )
    assert status[sm.JOB_DIMS_KPI] is False
    assert status[sm.JOB_FILL] and status[sm.JOB_BIAS] and status[sm.JOB_EXPIRY_AGG] and status[sm.JOB_SCRAPS]
    assert sm.JOB_STOCK_AGG not in status and sm.JOB_CAMPAIGN not in status
    assert {b.job: b.status for b in beats}[sm.JOB_DIMS_KPI] == "error"
