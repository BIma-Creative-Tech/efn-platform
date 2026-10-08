import json
from datetime import UTC, datetime, timedelta

import pytest

from efn_etl.loki import LokiError, to_ns
from efn_etl.sink import (
    ANCHOR_QUERY,
    SINK_QUERY,
    SQL_SINK_PG_BY_IDS,
    SQL_SINK_PG_WINDOW,
    Anchor,
    PgControlLog,
    SinkCheckSettings,
    load_key,
    parse_anchor_line,
    parse_sink_line,
    reconcile_sink,
    run_sink_check,
    sink_canonical,
    sink_mac,
    verify_anchors,
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


# ---------------------------------------------------------------- G14: jangkar rantai + wiring Loki


ANCHOR_PREFIX_LINE = "2026-10-07 03:00:01,123 INFO efn_etl.audit_job EFN_AUDIT_CHAIN_HEAD "


def _anchor_line(company: int, seq: int, digest: str) -> str:
    return ANCHOR_PREFIX_LINE + json.dumps({"company": company, "seq": seq, "row_hash": digest}, separators=(",", ":"))


def test_parse_anchor_line_strict() -> None:
    anchor = parse_anchor_line(_anchor_line(1, 10, "a" * 64))
    assert anchor == Anchor(1, 10, "a" * 64)
    assert parse_anchor_line(_anchor_line(1, 10, "A" * 64)) is None  # bukan hex kecil
    assert parse_anchor_line(_anchor_line(1, 10, "a" * 64).replace(" INFO ", " WARNING ")) is None
    assert parse_anchor_line(_anchor_line(1, 10, "a" * 64) + ' {"x":1}') is None


def test_verify_anchors_detects_rewrite_deletion_fork_and_absence() -> None:
    h1, h2 = "1" * 64, "2" * 64
    anchors = [
        Anchor(1, 10, h1),
        Anchor(1, 10, h1),
        Anchor(1, 20, h1),
        Anchor(2, 5, h1),
        Anchor(2, 6, h1),
        Anchor(2, 6, h2),
    ]
    current = {(1, 10): h1, (1, 20): h2, (2, 6): h1}
    result = verify_anchors(anchors, current, [1, 2])
    # (1,10) ok; (1,20) ditulis ulang; (2,5) hilang; (2,6) dua hash berbeda di Loki
    assert result.anchors_checked == 4 and result.anchor_mismatch == 2 and result.anchor_missing == 1
    assert result.alert and "2:5" in result.samples
    assert verify_anchors([], {}, [1]).anchor_absent == 1
    assert not verify_anchors([], {}, []).alert


class FakeLoki:
    def __init__(self, sink: list[str], anchors: list[str]) -> None:
        self.streams = {SINK_QUERY: sink, ANCHOR_QUERY: anchors}
        self.fail = False

    def get_json(self, path: str, params: dict[str, str]) -> object:
        if self.fail:
            raise LokiError("Loki tidak terjangkau")
        lines = self.streams[params["query"]]
        start = int(params["start"])
        values = [[str(start + i), line] for i, line in enumerate(lines)]
        if start != to_ns(T0 - timedelta(hours=25, minutes=15)) and start != to_ns(T0 - timedelta(days=8)):
            values = []  # halaman kedua: kosong
        return {"status": "success", "data": {"resultType": "streams", "result": [{"stream": {}, "values": values}]}}


class FakeAuditSource:
    def __init__(self, logs: list[PgControlLog]) -> None:
        self.logs = {p.log_id: p for p in logs}

    def query(self, sql: str, params: dict) -> list[dict]:
        def row(p: PgControlLog) -> dict:
            return {"id": p.log_id, "model_name": p.model, "operation": p.operation, "create_date": p.create_date}

        if sql == SQL_SINK_PG_WINDOW:
            return [row(p) for p in self.logs.values() if params["start"] <= p.create_date < params["end"]]
        if sql == SQL_SINK_PG_BY_IDS:
            return [row(self.logs[i]) for i in params["ids"] if i in self.logs]
        raise AssertionError(sql)


class FakeSinkStore:
    def __init__(self, chain: dict[tuple[int, int], str], copied: set[int] = frozenset()) -> None:
        self.chain = chain
        self.copied = set(copied)

    def copied_log_ids(self, log_ids: list[int]) -> set[int]:
        return self.copied & set(log_ids)

    def chain_hashes(self, company: int, seqs: list[int]) -> dict[int, str]:
        return {s: h for (c, s), h in self.chain.items() if c == company and s in seqs}

    def heads(self) -> dict[int, object]:
        return {c: object() for c, _s in self.chain}


def test_run_sink_check_clean_and_anomalies() -> None:
    h = "a" * 64
    cfg = SinkCheckSettings(settle=timedelta(minutes=15))
    old_log = PgControlLog(3, "res.users", "write", T0 - timedelta(days=3))  # di luar jendela, dicari per id
    loki = FakeLoki([_line(_event(7)), _line(_event(3, model="res.users"))], [_anchor_line(1, 4, h)])
    source = FakeAuditSource([_pg(7), old_log])
    result = run_sink_check(loki, source, FakeSinkStore({(1, 4): h}), KEY, T0, cfg)
    assert not result.alert, result.as_dict()
    assert result.sink.sink_lines == 2 and result.anchors.anchors_checked == 1

    loki = FakeLoki([_line(_event(7), key=b"z" * 32)], [_anchor_line(1, 4, h)])
    source = FakeAuditSource([_pg(7), _pg(8, model="ir.cron")])
    result = run_sink_check(loki, source, FakeSinkStore({(1, 4): "b" * 64}), KEY, T0, cfg)
    data = result.as_dict()
    assert data["sink_mac_invalid"] == 1 and data["log_missing_sink"] == 2 and data["anchor_mismatch"] == 1
