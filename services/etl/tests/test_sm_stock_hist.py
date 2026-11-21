"""G51 B6 (docs/design/G51_SMD_E2E_LEAD_SPEC.md §B6): rekonstruksi stok harian historis -- logika murni + fake I/O."""

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from efn_etl import sm_cycle, sm_stock_hist
from efn_etl.config import Settings
from efn_etl.sm import SQL_STOCK_AGG

ROOT = Path(__file__).resolve().parents[3]
GRANTS = (ROOT / "services" / "etl" / "sql" / "odoo-readonly-grants.sql").read_text(encoding="utf-8")
INIT = "\n".join(p.read_text(encoding="utf-8") for p in sorted((ROOT / "infra" / "clickhouse" / "init").glob("*.sql")))
COMPOSE = (ROOT / "infra" / "compose" / "docker-compose.yml").read_text(encoding="utf-8")
TODAY = date(2026, 11, 21)
NOW = datetime(2026, 11, 21, 3, 0, tzinfo=UTC)  # 10:00 WIB
D = Decimal


# ---------------------------------------------------------------- konfigurasi
@pytest.fixture
def tls_env(tmp_path: Path) -> dict[str, str]:
    ca = tmp_path / "ca.crt"
    ca.write_text("-----BEGIN CERTIFICATE-----\ndummy\n-----END CERTIFICATE-----\n", encoding="utf-8")
    return {"APP_ENV": "production", "PG_SSLROOTCERT": str(ca), "CH_CA_FILE": str(ca), "AMQP_TLS_CA_FILE": str(ca)}


def test_reconstruct_flag_default_off(tls_env: dict[str, str]) -> None:
    assert Settings.from_env(tls_env).sm_stock_reconstruct_days == 0


@pytest.mark.parametrize("value", ["-1", "401", "abc"])
def test_reconstruct_flag_range_rejected(tls_env: dict[str, str], value: str) -> None:
    env = {**tls_env, "ETL_ENABLE_ODOO": "true", "ETL_ENABLE_SM": "true", "ETL_SM_STOCK_RECONSTRUCT_DAYS": value}
    with pytest.raises((RuntimeError, ValueError)):
        Settings.from_env(env)


def test_reconstruct_requires_sm(tls_env: dict[str, str]) -> None:
    with pytest.raises(RuntimeError):
        Settings.from_env({**tls_env, "ETL_SM_STOCK_RECONSTRUCT_DAYS": "90"})
    ok = {**tls_env, "ETL_ENABLE_ODOO": "true", "ETL_ENABLE_SM": "true", "ETL_SM_STOCK_RECONSTRUCT_DAYS": "400"}
    assert Settings.from_env(ok).sm_stock_reconstruct_days == 400


def test_validate_days_bounds() -> None:
    assert sm_stock_hist.validate_days(0) == 0 and sm_stock_hist.validate_days(400) == 400
    for bad in (-1, 401):
        with pytest.raises(ValueError):
            sm_stock_hist.validate_days(bad)


def test_compose_passes_flags_with_safe_defaults() -> None:
    assert "ETL_SM_STOCK_RECONSTRUCT_DAYS: ${ETL_SM_STOCK_RECONSTRUCT_DAYS:-0}" in COMPOSE
    assert "ETL_FORECAST_ACCURACY_WEEKS: ${ETL_FORECAST_ACCURACY_WEEKS:-2}" in COMPOSE


# ---------------------------------------------------------------- logika murni
def test_target_days_skip_real_snapshots_and_today() -> None:
    real = [TODAY - timedelta(days=2), TODAY]
    days = sm_stock_hist.target_days(TODAY, 3, real)
    assert days == [TODAY - timedelta(days=3), TODAY - timedelta(days=1)]
    assert sm_stock_hist.target_days(TODAY, 0, []) == []


def test_recon_due_once_per_wib_day() -> None:
    assert sm_stock_hist.recon_due(None, NOW)
    assert not sm_stock_hist.recon_due(NOW - timedelta(hours=2), NOW)
    # 23:30 WIB kemarin = 16:30 UTC -> hari WIB berbeda
    assert sm_stock_hist.recon_due(datetime(2026, 11, 20, 16, 30, tzinfo=UTC), NOW)


def test_end_of_day_stock_walks_back_from_current() -> None:
    d1, d2, d3 = TODAY - timedelta(days=3), TODAY - timedelta(days=2), TODAY - timedelta(days=1)
    net = {d2: D(5), d3: D(-2), TODAY: D(-1)}  # masuk 5 (d2), keluar 2 (d3), keluar 1 (hari ini)
    out = sm_stock_hist.end_of_day_stock(D(10), net, [d1, d2, d3])
    assert out == {d3: D(11), d2: D(13), d1: D(8)}


def test_age_bucket_matches_sql_boundaries() -> None:
    assert [sm_stock_hist.age_bucket(a) for a in (0, 30, 31, 60, 61, 90, 91)] == [0, 0, 1, 1, 2, 2, 3]


def test_fifo_age_split() -> None:
    day = TODAY
    inflows = [(day - timedelta(days=5), D(4)), (day - timedelta(days=40), D(3)), (day + timedelta(days=1), D(9))]
    inflows.sort(reverse=True)
    assert sm_stock_hist.fifo_age_split(D(6), inflows, day) == [D(4), D(2), D(0), D(0)]
    # sisa tak tertutup -> > 90 hari; stok <= 0 -> nol
    assert sm_stock_hist.fifo_age_split(D(10), inflows, day) == [D(4), D(3), D(0), D(3)]
    assert sm_stock_hist.fifo_age_split(D(0), inflows, day) == [D(0)] * 4


def test_sold_window_matches_sql_half_open() -> None:
    daily = {
        TODAY - timedelta(days=28): 2.0,
        TODAY - timedelta(days=1): 3.0,
        TODAY: 100.0,
        TODAY - timedelta(days=29): 7.0,
    }
    assert sm_stock_hist.sold_window(daily, TODAY) == 5.0


def test_unit_cost_parsing() -> None:
    assert sm_stock_hist.unit_cost("1250.5") == D("1250.5")
    for bad in (None, "", "abc", "NaN"):
        assert sm_stock_hist.unit_cost(bad) is None


def test_moves_since_and_eod_ts_use_wib() -> None:
    assert sm_stock_hist.moves_since(date(2026, 9, 1)) == datetime(2026, 6, 1, 17, 0)  # 00:00 WIB 2 Jun
    assert sm_stock_hist.eod_snapshot_ts(TODAY) == datetime(2026, 11, 21, 16, 59, 59, tzinfo=UTC)


def _stock_rows() -> list[dict[str, Any]]:
    d3 = TODAY - timedelta(days=1)
    return [
        {"kind": "quant", "product_id": 7, "day": None, "qty_in": D(10), "qty_out": 0},
        {"kind": "move", "product_id": 7, "day": d3, "qty_in": D(0), "qty_out": D(4)},
        {"kind": "move", "product_id": 7, "day": TODAY - timedelta(days=2), "qty_in": D(6), "qty_out": D(0)},
        # masuk hari ini tanpa quant (data move tidak lengkap) -> stok rekonstruksi negatif di semua hari target
        {"kind": "move", "product_id": 8, "day": TODAY, "qty_in": D(50), "qty_out": D(0)},
    ]


def test_build_rows_values_flags_and_version() -> None:
    data = sm_stock_hist.parse_stock_rows(_stock_rows())
    days = [TODAY - timedelta(days=2), TODAY - timedelta(days=1)]
    res = sm_stock_hist.build_rows(
        11, 21, data, days, {7: D("1000"), 8: None}, {7: 4}, {}, {7: {TODAY - timedelta(days=3): 2.0}}
    )
    rows = [dict(zip(sm_stock_hist.AGG_COLUMNS, r, strict=True)) for r in res.rows]
    by_day = {r["day"]: r for r in rows if r["product_id"] == 7}
    # stok akhir kemarin = 10 (tanpa move hari ini); 2 hari lalu = 10 + 4 = 14
    assert by_day[days[1]]["on_hand_qty"] == D("10.000") and by_day[days[0]]["on_hand_qty"] == D("14.000")
    r = by_day[days[0]]
    assert r["stock_value_cost"] == D("14000.00") and r["value_no_lot"] == r["stock_value_cost"]
    assert r["value_age_0_30"] == D("6000.00") and r["value_age_91p"] == D("8000.00")  # 6 masuk hari itu, 8 lama
    assert r["value_exp_0_7"] == 0 and r["free_qty"] == r["on_hand_qty"] and r["categ_id"] == 4
    assert r["sold_28d"] == 1 and r["qty_sold_28d"] == D("2.000") and r["tracked"] == 0
    assert all(x["is_reconstructed"] == 1 and x["computed_at"] == sm_stock_hist.RECON_VERSION for x in rows)
    # produk 8: stok rekonstruksi negatif dihitung, ditulis 0 -> tanpa orderpoint tidak ditulis sama sekali
    assert res.negative == 2 and not [x for x in rows if x["product_id"] == 8]


def test_build_rows_keeps_zero_stock_for_orderpoint_products() -> None:
    data = sm_stock_hist.parse_stock_rows([])
    res = sm_stock_hist.build_rows(11, 21, data, [TODAY - timedelta(days=1)], {}, {}, {9: (D(2), D(8))}, {})
    (row,) = [dict(zip(sm_stock_hist.AGG_COLUMNS, r, strict=True)) for r in res.rows]
    assert row["tracked"] == 1 and row["on_hand_qty"] == 0 and (row["min_qty"], row["max_qty"]) == (D(2), D(8))


def _rmt_final(rows: list[dict[str, Any]]) -> dict[tuple[Any, ...], dict[str, Any]]:
    """Semantik ReplacingMergeTree(computed_at): versi terbesar menang; versi sama -> insert terakhir."""
    out: dict[tuple[Any, ...], dict[str, Any]] = {}
    for r in rows:
        key = (r["company_id"], r["day"], r["warehouse_id"], r["product_id"])
        if key not in out or r["computed_at"] >= out[key]["computed_at"]:
            out[key] = r
    return out


def test_real_snapshot_rows_always_win_over_reconstructed() -> None:
    """Keputusan lead G51: baris rekonstruksi tidak boleh menang atas baris snapshot nyata, apa pun urutannya."""
    key = {"company_id": 11, "day": TODAY - timedelta(days=1), "warehouse_id": 21, "product_id": 7}
    real = {**key, "computed_at": datetime(2026, 1, 1, tzinfo=UTC), "is_reconstructed": 0, "on_hand_qty": D(3)}
    recon = {**key, "computed_at": sm_stock_hist.RECON_VERSION, "is_reconstructed": 1, "on_hand_qty": D(9)}
    assert _rmt_final([real, recon])[tuple(key.values())]["is_reconstructed"] == 0
    assert _rmt_final([recon, real])[tuple(key.values())]["is_reconstructed"] == 0
    recon2 = {**recon, "on_hand_qty": D(5)}
    assert _rmt_final([recon, recon2])[tuple(key.values())]["on_hand_qty"] == D(5)  # rekonstruksi ulang menggantikan
    # baris nyata ditulis SQL_STOCK_AGG dengan now64(6) (>> 2000-01-01) dan tanpa is_reconstructed (default 0)
    assert "now64(6)" in SQL_STOCK_AGG and "is_reconstructed" not in SQL_STOCK_AGG
    assert sm_stock_hist.RECON_VERSION < datetime(2020, 1, 1, tzinfo=UTC)


def test_ddl_and_columns_match() -> None:
    assert "ALTER TABLE efn_dwh.agg_sm_stock_daily ADD COLUMN IF NOT EXISTS is_reconstructed UInt8 DEFAULT 0;" in INIT
    block = INIT.split("CREATE TABLE IF NOT EXISTS efn_dwh.agg_sm_stock_daily", 1)[1].split("ENGINE", 1)[0]
    ddl_cols = [c for c in re.findall(r"^\s{4}(\w+)\s", block, re.MULTILINE) if c != "_loaded_at"]
    assert list(sm_stock_hist.AGG_COLUMNS) == [*ddl_cols, "is_reconstructed"]


# ---------------------------------------------------------------- higiene SQL & grant
def _granted(table: str) -> set[str]:
    cols: set[str] = set()
    for found in re.findall(rf"GRANT SELECT \(([^)]*)\)\s+ON public\.{table} TO efn_etl", GRANTS):
        cols |= {c.strip() for c in found.split(",")}
    return cols


def test_pg_sql_reads_only_granted_columns_and_no_actor() -> None:
    warehouses_sql = sm_stock_hist.sql_recon_warehouses(("SMD",))
    sqls = sm_stock_hist.SQL_RECON_STOCK + sm_stock_hist.SQL_RECON_COST + warehouses_sql
    aliases = {
        "m": "stock_move",
        "l": "stock_location",
        "q": "stock_quant",
        "pp": "product_product",
        "pt": "product_template",
        "mu": "uom_uom",
        "pu": "uom_uom",
        "w": "stock_warehouse",
        "c": "res_company",
    }
    for alias, table in aliases.items():
        read = set(re.findall(rf"(?<![\w.]){alias}\.([a-z_]+)", sqls))
        assert read <= _granted(table) | {"id"}, (table, read - _granted(table))
    words = set(re.findall(r"[a-z_]+", sqls.lower()))
    assert not words & {"owner_id", "user_id", "partner_id", "create_uid", "write_uid"}
    assert "select *" not in sqls.lower() and "efn_brand_code IN ('SMD')" in sqls
    # filter rentang (bukan full scan)
    assert "m.state = 'done' AND m.date >= %(since)s" in sm_stock_hist.SQL_RECON_STOCK


def test_ch_sql_has_no_aggregate_alias_shadowing() -> None:
    for sql in (sm_stock_hist.SQL_LATEST_ORDERPOINTS, sm_stock_hist.SQL_POS_SALES_DAILY):
        aliases = set(re.findall(r"\) AS (\w+)", sql))
        args = re.findall(r"sum\(([^()]*)\)", sql)
        inside = {t.split(".")[-1] for a in args for t in re.findall(r"[a-z_][\w.]*", a)}
        assert not aliases & inside, sql


# ---------------------------------------------------------------- orkestrasi dengan fake I/O
class FakeOdoo:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def query(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        self.calls.append((sql, params))
        if "FROM stock_warehouse w" in sql:
            return [{"warehouse_id": 21, "company_id": 11}]
        if "WITH loc AS" in sql:
            return _stock_rows()
        if "standard_price" in sql:
            return [{"product_id": 7, "unit_cost": "1000"}, {"product_id": 8, "unit_cost": None}]
        return []


class FakeCh:
    def __init__(self, real_days: list[date]) -> None:
        self.real_days = real_days

    def rows(self, sql: str, parameters: dict[str, Any] | None = None) -> list[tuple[Any, ...]]:
        if "SELECT DISTINCT snapshot_date" in sql:
            return [(d,) for d in self.real_days]
        if "fact_orderpoint_snapshot" in sql:
            return [(21, 9, D(1), D(5))]
        if "dim_product FINAL WHERE product_id IN" in sql:
            return [(7, 4)]
        return []

    def execute(self, sql: str, parameters: dict[str, Any]) -> None:
        raise AssertionError("rekonstruksi tidak memakai INSERT ... SELECT")


class FakeSink:
    def __init__(self) -> None:
        self.inserts: list[tuple[str, tuple[str, ...], list[list[Any]]]] = []

    def insert(self, table: str, columns: Any, rows: list[list[Any]]) -> None:
        if rows:
            self.inserts.append((table, tuple(columns), rows))


class FakeStore:
    def __init__(self) -> None:
        self.marks: dict[str, datetime] = {}

    def get(self, job: str) -> datetime | None:
        return self.marks.get(job)

    def set(self, job: str, watermark: datetime, rows_loaded: int) -> None:
        self.marks[job] = watermark


def test_run_reconstruct_skips_real_days_and_runs_once_per_day() -> None:
    odoo, sink, store = FakeOdoo(), FakeSink(), FakeStore()
    ch = FakeCh([TODAY - timedelta(days=1)])  # kemarin sudah punya snapshot nyata
    res = sm_stock_hist.run_sm_stock_reconstruct(odoo, ch, sink, store, ("SMD",), NOW, 3)
    assert res.days == 2 and res.rows > 0 and not res.skipped
    (table, cols, rows) = sink.inserts[0]
    assert table == "agg_sm_stock_daily" and cols == sm_stock_hist.AGG_COLUMNS
    written_days = {r[0] for r in rows}
    assert written_days == {TODAY - timedelta(days=3), TODAY - timedelta(days=2)}
    assert {r[4] for r in rows} >= {7, 9}  # produk ber-stok + produk ber-orderpoint (stok 0)
    assert store.marks[sm_stock_hist.JOB_STOCK_RECONSTRUCT] == NOW
    stock_call = next(p for s, p in odoo.calls if "WITH loc AS" in s)
    assert stock_call == {"warehouse_id": 21, "since": sm_stock_hist.moves_since(TODAY - timedelta(days=3))}
    # siklus berikutnya di hari WIB yang sama -> dilewati
    again = sm_stock_hist.run_sm_stock_reconstruct(odoo, ch, FakeSink(), store, ("SMD",), NOW + timedelta(hours=1), 3)
    assert again.skipped


def test_run_reconstruct_off_does_nothing() -> None:
    odoo, sink, store = FakeOdoo(), FakeSink(), FakeStore()
    res = sm_stock_hist.run_sm_stock_reconstruct(odoo, FakeCh([]), sink, store, ("SMD",), NOW, 0)
    assert res.skipped and odoo.calls == [] and sink.inserts == [] and store.marks == {}


def test_sm_cycle_wires_reconstruct_only_when_enabled() -> None:
    base = dict(
        codes=("SMD",),
        stock=False,
        expiry=False,
        campaign=False,
        fill_recalc_days=14,
        campaign_attr_days=30,
        stock_backfill_days=90,
        overlap=timedelta(minutes=5),
        batch_size=100,
        max_pages=5,
    )
    beats: list[Any] = []
    off = sm_cycle.run_sm_cycle(
        sm_cycle.SmCycleConfig(**base), FakeOdoo(), FakeOdoo(), FakeCh([]), FakeSink(), FakeStore(), beats.append, NOW
    )
    assert sm_stock_hist.JOB_STOCK_RECONSTRUCT not in off
    sink = FakeSink()
    on = sm_cycle.run_sm_cycle(
        sm_cycle.SmCycleConfig(**base, stock_reconstruct_days=3),
        FakeOdoo(),
        FakeOdoo(),
        FakeCh([]),
        sink,
        FakeStore(),
        beats.append,
        NOW,
    )
    assert on[sm_stock_hist.JOB_STOCK_RECONSTRUCT] is True
    assert any(t == "agg_sm_stock_daily" for t, _c, _r in sink.inserts)
