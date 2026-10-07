import json
from datetime import UTC, datetime, timedelta

import pytest

from efn_etl.sink import (
    PgControlLog,
    load_key,
    parse_sink_line,
    reconcile_sink,
    sink_canonical,
    sink_mac,
)

KEY = b"k" * 32
T0 = datetime(2026, 10, 7, 3, 0, tzinfo=UTC)
PREFIX = "2026-10-07 03:00:01,123 42 INFO efn_odoo odoo.addons.efn_audit.sink: EFN_AUDIT_SINK "


def _event(log_id: int = 7, model: str = "ir.rule", operation: str = "write", **extra: object) -> dict:
    event = {
        "v": 2,
        "audit_op": "access_rule_change",
        "critical": True,
        "log_id": log_id,
        "operation": operation,
        "model": model,
        "res_id": 3,
        "uid": 2,
        "origin_uid": None,
        "sudo": False,
        "fields": ["domain_force"],
        "note": None,
    }
    event.update(extra)
    return event


def _line(event: dict, key: bytes | None = KEY) -> str:
    # Sama dengan efn_audit.models.base.sink_line.
    body = json.loads(sink_canonical(event))
    body["mac"] = sink_mac(key, body) if key else None
    return PREFIX + json.dumps(body, sort_keys=True, ensure_ascii=True)


def test_canonical_matches_odoo_contract() -> None:
    assert sink_canonical({"b": 1, "a": "é\n", "mac": "x"}) == '{"a":"\\u00e9\\n","b":1}'


def test_valid_line_verifies() -> None:
    event = parse_sink_line(_line(_event()), KEY)
    assert event is not None and event.valid and event.log_id == 7 and event.model == "ir.rule"


@pytest.mark.parametrize(
    "line",
    [
        _line(_event(), key=None),  # kunci belum di-mount di Odoo -> mac null
        _line(_event(), key=b"x" * 32),  # kunci lain
        _line(_event()).replace('"res_id": 3', '"res_id": 4'),  # isi dimanipulasi
        PREFIX + "{bukan json}",
        PREFIX + json.dumps(dict(_event(), v=1, mac="00")),
    ],
)
def test_invalid_lines_flagged(line: str) -> None:
    event = parse_sink_line(line, KEY)
    assert event is not None and not event.valid


def test_non_sink_lines_ignored() -> None:
    assert parse_sink_line("2026 INFO odoo.http: GET /web 200", KEY) is None


def test_load_key_min_length() -> None:
    with pytest.raises(ValueError):
        load_key(b"short\n")
    assert load_key(KEY + b"\n") == KEY


def _pg(log_id: int, model: str = "ir.rule", operation: str = "write", age: timedelta = timedelta(hours=2)):
    return PgControlLog(log_id, model, operation, T0 - age)


def _reconcile(lines, pg, copied=frozenset()):
    return reconcile_sink(
        lines, KEY, {p.log_id: p for p in pg}, set(copied), T0 - timedelta(days=1), T0, timedelta(minutes=15)
    )


def test_clean_reconciliation() -> None:
    result = _reconcile([_line(_event(7))], [_pg(7)])
    assert not result.alert and result.sink_lines == 1


def test_forged_line_counts_mac_invalid() -> None:
    forged = PREFIX + json.dumps(dict(_event(99, operation="purge"), mac="0" * 64))
    result = _reconcile([_line(_event(7)), forged], [_pg(7)])
    assert result.sink_mac_invalid == 1 and result.alert


def test_sink_without_log_is_missing_log_unless_copied() -> None:
    assert _reconcile([_line(_event(8))], []).sink_missing_log == 1
    assert _reconcile([_line(_event(8))], [], copied={8}).sink_missing_log == 0


def test_sink_with_changed_model_is_missing_log() -> None:
    assert _reconcile([_line(_event(7))], [_pg(7, model="ir.cron")]).sink_missing_log == 1


def test_control_log_without_sink_flagged_after_settle() -> None:
    result = _reconcile([], [_pg(5), _pg(6, age=timedelta(minutes=5)), _pg(9, model="res.partner")])
    # 5 = model kontrol tanpa sink; 6 = masih dalam settle; 9 = model biasa (tidak wajib sink)
    assert result.log_missing_sink == 1 and result.sample_ids == [5]


def test_purge_always_expected() -> None:
    assert _reconcile([], [_pg(5, model="res.partner", operation="purge")]).log_missing_sink == 1
