"""G51 A6: run forecast as-of (histori akurasi demo) -- hitung saja, payload tervalidasi, tanpa RPC, deterministik."""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeRunner

from efn_forecast import asof
from efn_forecast.__main__ import main
from efn_forecast.calendar import CalendarEntry
from efn_forecast.config import Params
from efn_forecast.contract import UUID4_RE, ContractError
from efn_forecast.pipeline import PipelineConfig
from efn_forecast.runner import run_batch
from efn_forecast.source import Warehouse

AS_OF = date(2026, 9, 28)  # Senin


class WindowSource:
    """Sumber palsu yang mencatat batas akhir kueri (tidak boleh membaca data setelah Minggu as-of)."""

    def __init__(self) -> None:
        self.ends: list[date] = []

    def warehouses(self, company: str) -> list[Warehouse]:
        return [Warehouse(1, "SMDDC", 9), Warehouse(2, "SMD01", 9)]

    def sales(self, company: str, start: date, end: date) -> list[tuple[int, int, date, float]]:
        self.ends.append(end)
        return [(2, 101, start + timedelta(days=i), 5.0) for i in range((end - start).days + 1)]

    def stock(self, company_id: int, start: date, end: date) -> list[tuple[int, int, date, float]]:
        self.ends.append(end)
        return [(2, 101, end, 30.0)]

    def scrap(self, company_id: int, start: date, end: date) -> list[tuple[int, str, float]]:
        return []

    def calendar(self, company_id: int, start: date, end: date) -> list[CalendarEntry]:
        return []

    def products(self, company_id: int) -> dict[int, str]:
        return {101: "Segar / Sayur"}


def _lib(years: Any) -> list[CalendarEntry]:
    return []


def _params(**kw: Any) -> Params:
    base: dict[str, Any] = {
        "pipeline": PipelineConfig(candidates=("AutoETS",), cv_windows=0, horizon_days=28),
        "history_days": 120,
        "stock_days": 30,
        "chunk_size": 20,
        "backtest_enabled": False,
        "include_today": False,  # env produksi apa pun; as_of_params memaksa True
        "seed_calendar": True,
    }
    base.update(kw)
    return Params(**base)


def _run(source: WindowSource | None = None) -> tuple[asof.RecordingOdoo, Any]:
    rec = asof.RecordingOdoo()
    report = run_batch(
        "daily",
        source or WindowSource(),
        rec,
        "SMD",
        asof.as_of_params(_params()),
        FakeRunner(),
        now=asof.as_of_now(AS_OF),
        lib=_lib,
        run_id=asof.as_of_run_id("SMD", AS_OF),
    )
    return rec, report


def test_parse_as_of_rules() -> None:
    today = date(2026, 11, 21)
    assert asof.parse_as_of("2026-09-28", today) == AS_OF
    for bad in ("2026-09-29", "2026-13-01", "kemarin", "2026-11-23", "2025-09-29"):  # Selasa, format, masa depan, >400
        with pytest.raises(ValueError):
            asof.parse_as_of(bad, today)


def test_as_of_now_is_sunday_2300_wib() -> None:
    assert asof.as_of_now(AS_OF) == datetime(2026, 9, 27, 16, 0, tzinfo=UTC)


def test_run_id_deterministic_and_v4_shaped() -> None:
    a = asof.as_of_run_id("SMD", AS_OF)
    assert UUID4_RE.fullmatch(a) and a == asof.as_of_run_id("SMD", AS_OF)
    assert a != asof.as_of_run_id("SMD", AS_OF + timedelta(days=7)) and a != asof.as_of_run_id("SMD", AS_OF, "x")


def test_params_force_history_through_sunday_and_no_calendar_rpc() -> None:
    p = asof.as_of_params(_params())
    assert p.include_today is True and p.seed_calendar is False


def test_as_of_batch_reads_only_past_and_forecasts_from_monday() -> None:
    src = WindowSource()
    rec, report = _run(src)
    assert report.status == "ok"
    assert src.ends and max(src.ends) == AS_OF - timedelta(days=1)  # tidak membaca data Senin dst.
    assert rec.run_open_payload is not None
    assert rec.run_open_payload["generated_at"] == "2026-09-27T16:00:00Z"
    assert rec.run_open_payload["run_id"] == asof.as_of_run_id("SMD", AS_OF)
    dates = sorted({line["date"] for p in rec.ingest_payloads for line in p["lines"]})
    assert dates[0] == AS_OF.isoformat() and dates[-1] == (AS_OF + timedelta(days=27)).isoformat()


def test_export_document_roundtrip(tmp_path: Path) -> None:
    rec, _ = _run()
    doc = asof.export_document("SMD", AS_OF, rec)
    path = tmp_path / "asof.json"
    asof.write_document(path, doc)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert asof.validate_document(loaded)["run_open"]["chunk_total"] == len(loaded["chunks"])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(format="x"),
        lambda d: d["run_close"].update(status="failed"),
        lambda d: d["chunks"][0].update(run_id="00000000-0000-4000-8000-000000000000"),
        lambda d: d.update(as_of="2026-10-05"),  # generated_at tidak cocok dengan tanggal
        lambda d: d["run_open"].update(kind="weekly_minmax"),
        lambda d: d.update(chunks=[]),
    ],
)
def test_validate_document_rejects(mutate: Any) -> None:
    rec, _ = _run()
    doc = json.loads(json.dumps(asof.export_document("SMD", AS_OF, rec)))
    mutate(doc)
    with pytest.raises(ContractError):
        asof.validate_document(doc)


def test_export_refuses_failed_run() -> None:
    rec = asof.RecordingOdoo()
    with pytest.raises(ContractError):
        asof.export_document("SMD", AS_OF, rec)
    rec.run_open({"run_id": "r", "kind": "daily", "chunk_total": 1})
    rec.ingest({"run_id": "r", "chunk_no": 0, "lines": []})
    rec.run_close({"run_id": "r", "status": "failed"})
    with pytest.raises(ContractError):
        asof.export_document("SMD", AS_OF, rec)


def test_recording_odoo_rejects_calendar_and_minmax() -> None:
    rec = asof.RecordingOdoo()
    for call in (rec.calendar_upsert, rec.minmax_ingest):
        with pytest.raises(RuntimeError):
            call({})


@pytest.mark.parametrize(
    "argv",
    [
        ["run", "--as-of", "2026-09-28"],  # tanpa --out
        ["run", "--out", "out.json"],  # tanpa --as-of
        ["run", "--as-of", "2026-09-28", "--out", "out.json", "--once"],
        ["run", "--as-of", "2026-09-28", "--out", "out.json", "--kind", "weekly_minmax"],
        ["run", "--as-of", "2026-09-29", "--out", "out.json"],  # bukan Senin
    ],
)
def test_cli_as_of_guards(argv: list[str]) -> None:
    assert main(argv) == 2  # ditolak sebelum menyentuh ClickHouse/Odoo
