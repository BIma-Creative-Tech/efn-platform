"""Orkestrasi batch: urutan 5 metode, chunk berurutan & idempoten, DC agregat, kegagalan -> run_close failed."""

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from conftest import FakeRunner

from efn_forecast.calendar import CalendarEntry
from efn_forecast.config import Params
from efn_forecast.contract import UUID4_RE
from efn_forecast.odoo import OdooError
from efn_forecast.pipeline import PipelineConfig
from efn_forecast.runner import run_batch
from efn_forecast.source import Warehouse

NOW = datetime(2026, 11, 18, 16, 30, tzinfo=UTC)  # 23:30 WIB, hari bisnis 18 Nov 2026


class FakeSource:
    def __init__(self, fail: str = "", sales_lag: int = 0) -> None:
        self.fail = fail
        self.sales_lag = sales_lag  # hari terakhir tanpa data (histori legacy/ESB yang tertinggal)
        self.calls: list[str] = []

    def _hit(self, name: str) -> None:
        self.calls.append(name)
        if name == self.fail:
            raise RuntimeError("clickhouse mati: SELECT ... rahasia")

    def warehouses(self, company: str) -> list[Warehouse]:
        self._hit("warehouses")
        return [Warehouse(1, "SMDDC", 9), Warehouse(2, "SMD01", 9)] if company == "SMD" else []

    def sales(self, company: str, start: date, end: date) -> list[tuple[int, int, date, float]]:
        self._hit("sales")
        rows = []
        for i in range((end - start).days + 1 - self.sales_lag):
            day = start + timedelta(days=i)
            rows.append((2, 101, day, 5.0 + (day.weekday() >= 5) * 3))
            if i % 4 == 0:
                rows.append((2, 102, day, 2.0))
        return rows

    def stock(self, company_id: int, start: date, end: date) -> list[tuple[int, int, date, float]]:
        self._hit("stock")
        return [(2, 103, end, 4.0), (2, 101, end, 30.0)]  # 103 = SKU baru (stok tanpa penjualan)

    def scrap(self, company_id: int, start: date, end: date) -> list[tuple[int, str, float]]:
        self._hit("scrap")
        return [(2, "Segar / Sayur", 200.0)]

    def calendar(self, company_id: int, start: date, end: date) -> list[CalendarEntry]:
        self._hit("calendar")
        return [CalendarEntry(date(2026, 12, 24), "custom", "Promo akhir tahun", "manual")]

    def products(self, company_id: int) -> dict[int, str]:
        self._hit("products")
        return {101: "Segar / Sayur", 102: "Kering / Snack", 103: "Kering / Snack"}


class FakeOdoo:
    def __init__(self, fail_on: str = "", fail_chunk: int = -1) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_on = fail_on
        self.fail_chunk = fail_chunk

    def _call(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, payload))
        if name == self.fail_on and payload.get("chunk_no", -1) in (self.fail_chunk, -1):
            raise OdooError("transport", "http_503", 503)
        if name in ("ingest", "minmax_ingest"):
            return {
                "ok": True,
                "run_id": payload["run_id"],
                "chunk_no": payload["chunk_no"],
                "accepted": len(payload["lines"]) - 1,
                "rejected": [{"index": 0, "code": "not_listed"}],
                "duplicate_chunk": False,
            }
        return {"ok": True}

    def calendar_upsert(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._call("calendar_upsert", payload)

    def run_open(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._call("run_open", payload)

    def ingest(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._call("ingest", payload)

    def minmax_ingest(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._call("minmax_ingest", payload)

    def run_close(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._call("run_close", payload)


def _lib(years: Any) -> list[CalendarEntry]:
    return [CalendarEntry(date(y, 12, 25), "holiday", "Hari Raya Natal") for y in years]


def _params(**kw: Any) -> Params:
    base = {
        "pipeline": PipelineConfig(candidates=("AutoETS",), cv_windows=0, horizon_days=28),
        "history_days": 120,
        "stock_days": 30,
        "chunk_size": 50,
        "backtest_enabled": False,
    }
    base.update(kw)
    return Params(**base)


def _names(odoo: FakeOdoo) -> list[str]:
    return [n for n, _ in odoo.calls]


def test_daily_batch_sequence_chunks_and_dc_aggregate(caplog: pytest.LogCaptureFixture) -> None:
    odoo = FakeOdoo()
    with caplog.at_level(logging.INFO):
        report = run_batch("daily", FakeSource(), odoo, "SMD", _params(), FakeRunner(), NOW, _lib)
    assert report.status == "ok" and report.error == ""
    names = _names(odoo)
    assert names[0] == "calendar_upsert" and names[1] == "run_open" and names[-1] == "run_close"
    ingests = [p for n, p in odoo.calls if n == "ingest"]
    open_payload = odoo.calls[1][1]
    assert UUID4_RE.fullmatch(open_payload["run_id"])
    assert open_payload["chunk_total"] == len(ingests) == report.chunks
    assert open_payload["kind"] == "daily" and open_payload["generated_at"] == "2026-11-18T16:30:00Z"
    assert [p["chunk_no"] for p in ingests] == list(range(len(ingests)))  # berurutan
    assert all(p["run_id"] == open_payload["run_id"] and len(p["lines"]) <= 50 for p in ingests)
    lines = [line for p in ingests for line in p["lines"]]
    # 3 seri outlet (101 reguler, 102 intermiten, 103 SKU baru) + 3 seri DC agregat, masing-masing 28 hari
    assert report.series == 6 and len(lines) == report.lines == 6 * 28
    assert {line["warehouse_code"] for line in lines} == {"SMD01", "SMDDC"}
    dates = sorted(line["date"] for line in lines)
    assert (dates[0], dates[-1]) == ("2026-11-19", "2026-12-16")
    models = {(line["warehouse_code"], line["product_id"]): line["model"] for line in lines}
    assert models[("SMD01", 101)] == "AutoETS"
    assert models[("SMD01", 102)] == "CrostonOptimized"
    assert models[("SMD01", 103)] == "category_mean"

    def _yhat(code: str) -> dict[str, float]:
        return {x["date"]: x["yhat"] for x in lines if x["warehouse_code"] == code and x["product_id"] == 101}

    assert _yhat("SMDDC") == pytest.approx(_yhat("SMD01"), abs=0.002)  # DC tanpa penjualan langsung = Σ outlet
    close = odoo.calls[-1][1]
    assert close["status"] == "ok"
    # backtest mati -> wape_backtest dihilangkan (Odoo menolak null)
    assert set(close["metrics"]) == {"series", "duration_s", "failed_series"}
    assert report.rejected == len(ingests)
    assert "EFN_FORECAST_RUN" in caplog.text and '"company":"SMD"}' in caplog.text  # PT di alert Loki (SEC-G46d-02)


def test_seed_calendar_payload_current_and_next_year() -> None:
    odoo = FakeOdoo()
    run_batch("daily", FakeSource(), odoo, "SMD", _params(), FakeRunner(), NOW, _lib)
    seed = odoo.calls[0][1]
    assert seed["company_code"] == "SMD" and seed["schema_version"] == 1
    assert [e["date"] for e in seed["entries"]] == ["2026-12-25", "2027-12-25"]


def test_seed_failure_does_not_stop_forecast() -> None:
    odoo = FakeOdoo(fail_on="calendar_upsert")
    report = run_batch("daily", FakeSource(), odoo, "SMD", _params(), FakeRunner(), NOW, _lib)
    assert report.status == "ok" and _names(odoo)[-1] == "run_close"


def test_chunk_failure_closes_run_failed_without_raising(caplog: pytest.LogCaptureFixture) -> None:
    odoo = FakeOdoo(fail_on="ingest", fail_chunk=1)
    with caplog.at_level(logging.INFO):
        report = run_batch("daily", FakeSource(), odoo, "SMD", _params(), FakeRunner(), NOW, _lib)
    assert report.status == "failed" and report.error == "odoo_transport:http_503"
    names = _names(odoo)
    assert names.count("run_open") == 1 and names[-1] == "run_close"
    assert odoo.calls[-1][1]["status"] == "failed" and "wape_backtest" not in odoo.calls[-1][1]["metrics"]
    assert "rahasia" not in caplog.text


def test_source_failure_opens_empty_run_then_closes_failed(caplog: pytest.LogCaptureFixture) -> None:
    odoo = FakeOdoo()
    with caplog.at_level(logging.INFO):
        report = run_batch("daily", FakeSource(fail="sales"), odoo, "SMD", _params(), FakeRunner(), NOW, _lib)
    assert report.status == "failed" and report.error == "RuntimeError"
    assert "SELECT" not in caplog.text and "rahasia" not in caplog.text  # pesan exception mentah tidak di-log
    opens = [p for n, p in odoo.calls if n == "run_open"]
    assert len(opens) == 1 and opens[0]["chunk_total"] == 1  # Odoo menolak 0; run failed tidak butuh chunk
    assert odoo.calls[-1][0] == "run_close" and odoo.calls[-1][1]["status"] == "failed"
    # Odoo juga mati -> tetap tidak raise
    report2 = run_batch(
        "daily", FakeSource(fail="sales"), FakeOdoo(fail_on="run_open"), "SMD", _params(), FakeRunner(), NOW, _lib
    )
    assert report2.status == "failed"


def test_no_warehouses_fails_cleanly() -> None:
    odoo = FakeOdoo()
    report = run_batch("daily", FakeSource(), odoo, "XYZ", _params(), FakeRunner(), NOW, _lib)
    assert report.status == "failed" and report.error == "no_warehouses"


def test_dry_run_makes_no_odoo_calls() -> None:
    report = run_batch(
        "daily", FakeSource(), None, "SMD", _params(backtest_enabled=True, backtest_days=14), FakeRunner(), NOW, _lib
    )
    assert report.status == "dry_run" and report.lines == 6 * 28
    assert report.wape_backtest is not None and report.wape_baseline_mean56 is not None


def test_backtest_wape_sent_in_close_metrics() -> None:
    odoo = FakeOdoo()
    run_batch(
        "daily", FakeSource(), odoo, "SMD", _params(backtest_enabled=True, backtest_days=14), FakeRunner(), NOW, _lib
    )
    metrics = odoo.calls[-1][1]["metrics"]
    assert set(metrics) == {"series", "duration_s", "wape_backtest", "failed_series"}
    assert metrics["wape_backtest"] >= 0


def test_weekly_minmax_batch_and_waste_lowers_service_level() -> None:
    odoo = FakeOdoo()
    report = run_batch("weekly_minmax", FakeSource(), odoo, "SMD", _params(), FakeRunner(), NOW, _lib)
    assert report.status == "ok"
    names = _names(odoo)
    assert "calendar_upsert" not in names and "ingest" not in names and "minmax_ingest" in names
    assert odoo.calls[0][1]["kind"] == "weekly_minmax"
    sent = [x for n, p in odoo.calls if n == "minmax_ingest" for x in p["lines"]]
    lines = {(x["warehouse_code"], x["product_id"]): x for x in sent}
    fresh, dry = lines[("SMD01", 101)], lines[("SMD01", 102)]
    assert fresh["service_level"] < 0.95 == dry["service_level"]  # waste kategori segar tinggi
    assert all(x["min_qty"] <= x["max_qty"] for x in lines.values())
    assert lines[("SMDDC", 101)]["min_qty"] > fresh["min_qty"]  # lead time DC lebih panjang


class _CloseOkRejected(FakeOdoo):
    """Odoo menolak run_close(ok) dengan 422 incomplete_run (chunk hilang); close failed diterima."""

    def run_close(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload["status"] == "ok":
            self.calls.append(("run_close", payload))
            raise OdooError("rejected", "incomplete_run", 422)
        return self._call("run_close", payload)


def test_incomplete_run_on_close_then_run_closed_failed(caplog: pytest.LogCaptureFixture) -> None:
    odoo = _CloseOkRejected()
    with caplog.at_level(logging.INFO):
        report = run_batch("daily", FakeSource(), odoo, "SMD", _params(), FakeRunner(), NOW, _lib)
    assert report.status == "failed" and report.error == "odoo_rejected:incomplete_run"
    closes = [p["status"] for n, p in odoo.calls if n == "run_close"]
    assert closes == ["ok", "failed"]  # run tidak dibiarkan menggantung terbuka 6 jam
    assert _names(odoo).count("run_open") == 1  # run yang sama dipakai (tidak dibuka ulang)


def test_every_chunk_sent_once_in_order_and_within_contract_limits() -> None:
    odoo = FakeOdoo()
    run_batch("daily", FakeSource(), odoo, "SMD", _params(chunk_size=7), FakeRunner(), NOW, _lib)
    ingests = [p for n, p in odoo.calls if n == "ingest"]
    assert len(ingests) > 1 and [p["chunk_no"] for p in ingests] == list(range(len(ingests)))
    assert all(1 <= len(p["lines"]) <= 7 for p in ingests)
    keys = [(x["warehouse_code"], x["product_id"], x["date"]) for p in ingests for x in p["lines"]]
    assert len(keys) == len(set(keys)), "tidak ada baris ganda lintas chunk (Odoo menolak duplicate_line)"


def test_lines_above_value_cap_dropped_locally() -> None:
    odoo = FakeOdoo()
    report = run_batch("daily", FakeSource(), odoo, "SMD", _params(max_qty_per_line=6.0), FakeRunner(), NOW, _lib)
    assert report.status == "ok" and report.rejected_local > 0
    sent = [x for n, p in odoo.calls if n == "ingest" for x in p["lines"]]
    assert len(sent) == report.lines == 6 * 28 - report.rejected_local
    assert all(max(x["yhat"], x["p10"], x["p90"], x["sigma"]) <= 6.0 for x in sent)
    assert odoo.calls[1][1]["chunk_total"] == len([n for n, _ in odoo.calls if n == "ingest"])


class _MixedCaseSource(FakeSource):
    def warehouses(self, company: str) -> list[Warehouse]:
        self._hit("warehouses")
        return [Warehouse(1, "SMDDC", 9), Warehouse(2, "Smd01", 9)]


def test_warehouse_code_sent_exactly_as_in_source() -> None:
    odoo = FakeOdoo()
    run_batch("daily", _MixedCaseSource(), odoo, "SMD", _params(), FakeRunner(), NOW, _lib)
    codes = {x["warehouse_code"] for n, p in odoo.calls if n == "ingest" for x in p["lines"]}
    assert codes == {"Smd01", "SMDDC"}  # tidak di-uppercase


def test_invalid_kind_rejected() -> None:
    with pytest.raises(ValueError):
        run_batch("hourly", FakeSource(), None, "SMD", _params(), FakeRunner(), NOW, _lib)


def _lines(odoo: FakeOdoo) -> list[dict[str, Any]]:
    return [line for n, p in odoo.calls if n == "ingest" for line in p["lines"]]


def test_sales_lag_ends_history_at_last_data_day_and_keeps_line_dates() -> None:
    fresh, lagged, runner = FakeOdoo(), FakeOdoo(), FakeRunner()
    run_batch("daily", FakeSource(), fresh, "SMD", _params(), FakeRunner(), NOW, _lib)
    report = run_batch("daily", FakeSource(sales_lag=3), lagged, "SMD", _params(), runner, NOW, _lib)
    assert report.status == "ok" and report.lines == 6 * 28
    lines = _lines(lagged)
    dates = sorted(line["date"] for line in lines)
    assert (dates[0], dates[-1]) == ("2026-11-19", "2026-12-16")  # tetap as_of+1 .. as_of+horizon (lolos kontrak)
    assert all(h == 28 + 3 for _name, _n, h in runner.calls)  # horizon diperpanjang sebanyak lag lalu dipotong

    def _yhat(odoo: FakeOdoo) -> dict[str, float]:
        return {x["date"]: x["yhat"] for x in _lines(odoo) if x["warehouse_code"] == "SMD01" and x["product_id"] == 101}

    # 3 hari tanpa data tidak dibaca sebagai penjualan nol: level sama dengan data segar (pola mingguan, 4 minggu penuh)
    assert _yhat(lagged) == pytest.approx(_yhat(fresh), rel=0.02)


def test_sales_older_than_max_lag_fails_run() -> None:
    odoo = FakeOdoo()
    report = run_batch("daily", FakeSource(sales_lag=15), odoo, "SMD", _params(), FakeRunner(), NOW, _lib)
    assert report.status == "failed" and report.error == "stale_sales"
    assert odoo.calls[-1][0] == "run_close" and odoo.calls[-1][1]["status"] == "failed"
