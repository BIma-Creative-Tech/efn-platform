"""Validasi skema lokal docs/04 §28.2 + chunking (≤ 500 baris, ≤ 512 KB, deterministik)."""

import copy
from datetime import date
from typing import Any

import pytest

from efn_forecast.contract import (
    FORECAST_VALUE_KEYS,
    MAX_BODY_BYTES,
    MAX_QTY_PER_LINE,
    MINMAX_VALUE_KEYS,
    ContractError,
    body_bytes,
    chunk,
    validate_calendar_upsert,
    validate_ingest,
    validate_minmax,
    validate_run_close,
    validate_run_open,
    within_cap,
)

RUN = "6f1c2b8e-0d3a-4c8e-9d61-1b5a2f4e7c90"
TODAY = date(2026, 11, 18)


def _line(**kw: Any) -> dict[str, Any]:
    line = {
        "warehouse_code": "SMD01",
        "product_id": 123,
        "date": "2026-11-19",
        "yhat": 12.4,
        "p10": 8.0,
        "p90": 17.1,
        "sigma": 2.9,
        "model": "AutoETS",
    }
    line.update(kw)
    return line


def _ingest(lines: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"schema_version": 1, "run_id": RUN, "chunk_no": 0, "lines": lines or [_line()]}


def test_valid_payloads_pass() -> None:
    validate_calendar_upsert(
        {
            "schema_version": 1,
            "company_code": "SMD",
            "entries": [{"date": "2027-03-10", "kind": "holiday", "name": "Hari Raya Idul Fitri", "source": "lib"}],
        }
    )
    validate_run_open(
        {
            "schema_version": 1,
            "run_id": RUN,
            "company_code": "SMD",
            "model_version": "g44-1.0.0",
            "kind": "daily",
            "generated_at": "2026-11-18T16:30:00Z",
            "horizon_days": 28,
            "chunk_total": 1,
        }
    )
    validate_ingest(_ingest(), TODAY, 28)
    validate_ingest(_ingest([_line(date="2026-11-18"), _line(date="2026-12-16", model="category_mean")]), TODAY, 28)
    validate_minmax(
        {
            "schema_version": 1,
            "run_id": RUN,
            "chunk_no": 0,
            "lines": [
                {
                    "warehouse_code": "SMDDC",
                    "product_id": 1,
                    "min_qty": 10,
                    "max_qty": 40,
                    "service_level": 0.95,
                    "basis_daily_demand": 3.2,
                }
            ],
        }
    )
    validate_run_close(
        {
            "schema_version": 1,
            "run_id": RUN,
            "status": "ok",
            "metrics": {"series": 2400, "duration_s": 312, "wape_backtest": 0.27, "failed_series": 3},
        }
    )
    # wape_backtest opsional (Odoo menolak null): dihilangkan bila tidak ada backtest
    validate_run_close(
        {
            "schema_version": 1,
            "run_id": RUN,
            "status": "failed",
            "metrics": {"series": 0, "duration_s": 1.5, "failed_series": 0},
        }
    )


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda p: p.update(extra=1), "ingest_keys"),
        (lambda p: p["lines"][0].update(extra=1), "line_keys"),
        (lambda p: p.update(schema_version=2), "schema_version"),
        (lambda p: p.update(schema_version=True), "schema_version"),
        (lambda p: p.update(run_id="6f1c2b8e-0d3a-1c8e-9d61-1b5a2f4e7c90"), "run_id"),  # bukan v4
        (lambda p: p.update(chunk_no=-1), "chunk_no"),
        (lambda p: p.update(chunk_no=True), "chunk_no"),
        (lambda p: p["lines"][0].update(p10=13.0), "line_interval"),
        (lambda p: p["lines"][0].update(p90=12.0), "line_interval"),
        (lambda p: p["lines"][0].update(yhat=-0.1), "line_yhat"),
        (lambda p: p["lines"][0].update(sigma=float("nan")), "line_sigma"),
        (lambda p: p["lines"][0].update(sigma="2.9"), "line_sigma"),
        (lambda p: p["lines"][0].update(date="2026-11-17"), "line_date_range"),
        (lambda p: p["lines"][0].update(date="2026-12-17"), "line_date_range"),
        (lambda p: p["lines"][0].update(model="Prophet"), "line_model"),
        (lambda p: p["lines"][0].update(product_id=0), "product_id"),
        (lambda p: p["lines"][0].update(warehouse_code="SMD 01"), "warehouse_code"),
        (lambda p: p.update(lines=[]), "lines"),
        (lambda p: p.update(lines=[_line()] * 501), "lines"),
    ],
)
def test_ingest_negative(mutate: Any, code: str) -> None:
    payload = copy.deepcopy(_ingest())
    mutate(payload)
    with pytest.raises(ContractError) as err:
        validate_ingest(payload, TODAY, 28)
    assert err.value.code == code


def test_other_methods_negative() -> None:
    with pytest.raises(ContractError):
        validate_run_open(
            {
                "schema_version": 1,
                "run_id": RUN,
                "company_code": "smd",
                "model_version": "g44-1.0.0",
                "kind": "daily",
                "generated_at": "2026-11-18T16:30:00Z",
                "horizon_days": 28,
                "chunk_total": 1,
            }
        )
    with pytest.raises(ContractError):
        validate_run_open(
            {
                "schema_version": 1,
                "run_id": RUN,
                "company_code": "SMD",
                "model_version": "g44-1.0.0",
                "kind": "daily",
                "generated_at": "2026-11-18T23:30:00+07:00",
                "horizon_days": 28,
                "chunk_total": 1,
            }
        )
    with pytest.raises(ContractError):
        validate_run_close(
            {
                "schema_version": 1,
                "run_id": RUN,
                "status": "ok",
                "metrics": {"series": 1, "duration_s": 1, "wape_backtest": 0.2, "failed_series": 0, "wape_mean56": 0.4},
            }
        )
    for metrics in (
        {"series": 1, "duration_s": 1, "wape_backtest": None, "failed_series": 0},  # null ditolak Odoo
        {"series": 1, "wape_backtest": 0.2, "failed_series": 0},  # duration_s wajib
    ):
        with pytest.raises(ContractError):
            validate_run_close({"schema_version": 1, "run_id": RUN, "status": "ok", "metrics": metrics})
    with pytest.raises(ContractError):  # chunk_total 0 ditolak Odoo
        validate_run_open(
            {
                "schema_version": 1,
                "run_id": RUN,
                "company_code": "SMD",
                "model_version": "g44-1.0.0",
                "kind": "daily",
                "generated_at": "2026-11-18T16:30:00Z",
                "horizon_days": 28,
                "chunk_total": 0,
            }
        )
    with pytest.raises(ContractError):
        validate_run_close(
            {
                "schema_version": 1,
                "run_id": RUN,
                "status": "partial",
                "metrics": {"series": 1, "duration_s": 1, "wape_backtest": 0.2, "failed_series": 0},
            }
        )
    base = {
        "warehouse_code": "SMDDC",
        "product_id": 1,
        "min_qty": 10,
        "max_qty": 40,
        "service_level": 0.95,
        "basis_daily_demand": 3.2,
    }
    for bad in ({"min_qty": 50}, {"service_level": 1.0}, {"service_level": 0}):
        with pytest.raises(ContractError):
            validate_minmax({"schema_version": 1, "run_id": RUN, "chunk_no": 0, "lines": [{**base, **bad}]})
    for bad_entry in ({"kind": "weekend"}, {"source": "manual"}, {"name": ""}, {"name": "x" * 65}):
        entry = {"date": "2027-03-10", "kind": "holiday", "name": "Idul Fitri", "source": "lib", **bad_entry}
        with pytest.raises(ContractError):
            validate_calendar_upsert({"schema_version": 1, "company_code": "SMD", "entries": [entry]})


def test_chunk_by_count_deterministic() -> None:
    lines = [_line(product_id=i + 1) for i in range(1201)]
    parts = chunk(lines)
    assert [len(p) for p in parts] == [500, 500, 201]
    assert chunk(lines) == parts  # input sama -> chunk_no sama (idempoten saat batch diulang)
    assert [len(p) for p in chunk(lines, 100)][:2] == [100, 100]
    with pytest.raises(ValueError):
        chunk(lines, 501)
    assert chunk([]) == []


def test_chunk_by_bytes_and_body_limit() -> None:
    lines = [_line() for _ in range(400)]  # ukuran baris seragam
    size = body_bytes(lines[0]) + 1
    parts = chunk(lines, 500, max_bytes=size * 150)
    assert [len(p) for p in parts] == [150, 150, 100]
    for p in chunk(lines):
        assert body_bytes(_ingest(p)) <= MAX_BODY_BYTES
    with pytest.raises(ContractError):
        chunk([{"x": "y" * 100}], 10, max_bytes=50)


def _minmax_line(**kw: Any) -> dict[str, Any]:
    line = {
        "warehouse_code": "SMDDC",
        "product_id": 1,
        "min_qty": 10,
        "max_qty": 40,
        "service_level": 0.95,
        "basis_daily_demand": 3.2,
    }
    line.update(kw)
    return line


@pytest.mark.parametrize(("level", "ok"), [(0.5, True), (0.999, True), (0.49, False), (1.0, False), (0.0, False)])
def test_minmax_service_level_range_matches_odoo(level: float, ok: bool) -> None:
    payload = {"schema_version": 1, "run_id": RUN, "chunk_no": 0, "lines": [_minmax_line(service_level=level)]}
    if ok:
        validate_minmax(payload)
    else:
        with pytest.raises(ContractError) as err:
            validate_minmax(payload)
        assert err.value.code == "service_level"


def test_value_cap_per_line() -> None:
    assert MAX_QTY_PER_LINE == 100_000
    validate_ingest(_ingest([_line(yhat=100_000, p10=99_000, p90=100_000, sigma=1.0)]), TODAY, 28)
    for kw in ({"p90": 100_000.5}, {"sigma": 200_000.0}):
        with pytest.raises(ContractError) as err:
            validate_ingest(_ingest([_line(**kw)]), TODAY, 28)
        assert err.value.code == "value_too_large"
    with pytest.raises(ContractError) as err:  # plafon dari env (FORECAST_MAX_QTY_PER_LINE) lebih kecil
        validate_ingest(_ingest([_line()]), TODAY, 28, max_qty=10)
    assert err.value.code == "value_too_large"
    big = {"schema_version": 1, "run_id": RUN, "chunk_no": 0, "lines": [_minmax_line(max_qty=150_000)]}
    with pytest.raises(ContractError) as err:
        validate_minmax(big)
    assert err.value.code == "value_too_large"
    validate_minmax(big, max_qty=200_000)
    assert within_cap(_line(), FORECAST_VALUE_KEYS, 100) and not within_cap(_line(), FORECAST_VALUE_KEYS, 10)
    assert not within_cap(_minmax_line(max_qty=11), MINMAX_VALUE_KEYS, 10)


def test_warehouse_code_case_preserved() -> None:
    validate_ingest(_ingest([_line(warehouse_code="Smd01")]), TODAY, 28)  # tidak dinormalisasi / ditolak
