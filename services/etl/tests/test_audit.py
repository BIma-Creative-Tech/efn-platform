"""Salinan audit tamper-evident (G12): hash berantai, redaksi PII, late commit, rekonsiliasi."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from efn_etl import audit
from efn_etl.audit import (
    GENESIS,
    LINE_COPY_COLUMNS,
    LOG_COPY_COLUMNS,
    ChainHead,
    ChainRow,
    CopiedLog,
    PurgeBudget,
    PurgeLog,
    ReconResult,
    build_copy,
    chain_hash,
    classify_missing,
    payload_hash,
    verify_chain,
)
from efn_etl.audit_job import (
    SQL_AUDIT_BY_IDS,
    SQL_AUDIT_IDS_RANGE,
    SQL_AUDIT_LINES,
    SQL_AUDIT_NEW,
    SQL_AUDIT_PURGES,
    SQL_AUDIT_RANGE,
    AuditSettings,
    recon_due,
    run_audit_copy,
    run_audit_recon,
)

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def _log(log_id: int, company: int | None = 2, model: str = "product.template", **kw: Any) -> dict[str, Any]:
    row = {
        "id": log_id,
        "create_date": (NOW - timedelta(hours=2)).replace(tzinfo=None) + timedelta(seconds=log_id),
        "create_uid": 1,
        "user_id": 2,
        "origin_user_id": None,
        "is_sudo": False,
        "operation": "write",
        "model_name": model,
        "res_id": 10 + log_id,
        "res_name": f"Produk {log_id}",
        "company_id": company,
        "prev_company_id": None,
        "is_personal": False,
        "note": None,
    }
    row.update(kw)
    return row


def _line(line_id: int, log_id: int, **kw: Any) -> dict[str, Any]:
    row = {
        "id": line_id,
        "log_id": log_id,
        "create_date": (NOW - timedelta(hours=2)).replace(tzinfo=None),
        "field_name": "list_price",
        "field_label": "Harga",
        "old_value": "1000",
        "new_value": "1200",
        "old_ref": None,
        "new_ref": None,
        "is_masked": False,
    }
    row.update(kw)
    return row


def _col(row: list[Any], name: str, columns: tuple[str, ...] = LOG_COPY_COLUMNS) -> Any:
    return row[columns.index(name)]


# ------------------------------------------------------------------ hash & rantai


def test_policy_constants_match_odoo_source() -> None:
    src = Path(__file__).resolve().parents[2] / "odoo" / "addons" / "efn_audit" / "models" / "audit_policy.py"
    if not src.is_file():
        pytest.skip("sumber Odoo tidak tersedia di container test")
    text = src.read_text(encoding="utf-8")
    assert f"MIN_RETENTION_DAYS = {audit.MIN_RETENTION_DAYS}" in text
    for model in audit.PERSONAL_MODELS | audit.PROTECTED_LOG_MODELS:
        assert f'"{model}"' in text


def test_payload_hash_is_deterministic_and_order_independent_for_lines() -> None:
    log = _log(1)
    lines = [_line(2, 1), _line(1, 1, field_name="name", old_value="A", new_value="B")]
    assert payload_hash(log, lines) == payload_hash(log, list(reversed(lines)))
    # Naive vs aware UTC = nilai sama.
    aware = {**log, "create_date": log["create_date"].replace(tzinfo=UTC)}
    assert payload_hash(aware, lines) == payload_hash(log, lines)


@pytest.mark.parametrize(
    "change",
    [{"user_id": 3}, {"operation": "unlink"}, {"res_id": 99}, {"company_id": 3}, {"note": "x"}, {"is_sudo": True}],
)
def test_payload_hash_detects_log_changes(change: dict[str, Any]) -> None:
    assert payload_hash(_log(1), [])[1] != payload_hash(_log(1, **change), [])[1]


def test_payload_hash_ignores_set_null_fk_and_write_columns() -> None:
    base = payload_hash(_log(1), [_line(1, 1)])
    changed = payload_hash(
        _log(1, model_id=None, rule_id=None, write_date=NOW, write_uid=5), [_line(1, 1, field_id=None, write_date=NOW)]
    )
    assert base == changed


def test_build_copy_chains_per_company_from_genesis() -> None:
    heads: dict[int, ChainHead] = {}
    logs = [_log(1, company=2), _log(2, company=3), _log(3, company=2), _log(4, company=None)]
    batch = build_copy(logs, {1: [_line(1, 1)]}, heads, "new")
    by_company = {}
    for row in batch.log_rows:
        by_company.setdefault(_col(row, "chain_company"), []).append(row)
    assert sorted(by_company) == [0, 2, 3]
    first, second = by_company[2]
    assert (_col(first, "chain_seq"), _col(second, "chain_seq")) == (1, 2)
    assert _col(first, "prev_hash") == GENESIS
    assert _col(second, "prev_hash") == _col(first, "row_hash")
    assert _col(first, "row_hash") == chain_hash(GENESIS, _col(first, "payload_hash"))
    assert heads[2] == ChainHead(seq=2, row_hash=_col(second, "row_hash"))
    assert _col(first, "line_count") == 1
    assert batch.line_log_ids == {1}
    # Rantai berlanjut dari kepala yang ada.
    nxt = build_copy([_log(5, company=2)], {}, heads, "late")
    assert _col(nxt.log_rows[0], "chain_seq") == 3
    assert _col(nxt.log_rows[0], "prev_hash") == _col(second, "row_hash")
    assert _col(nxt.log_rows[0], "ingest_kind") == "late"


def _chain_rows(rows: list[list[Any]]) -> list[ChainRow]:
    return [
        ChainRow(
            _col(r, "chain_company"),
            _col(r, "chain_seq"),
            _col(r, "log_id"),
            _col(r, "payload_hash"),
            _col(r, "prev_hash"),
            _col(r, "row_hash"),
        )
        for r in rows
    ]


def test_verify_chain_ok_and_detects_tamper_gap_and_paging() -> None:
    rows = _chain_rows(build_copy([_log(i) for i in range(1, 6)], {}, {}, "new").log_rows)
    problems, head = verify_chain(rows)
    assert problems == [] and head.seq == 5
    # Paging: dua halaman dengan kepala dibawa.
    p1, h1 = verify_chain(rows[:2])
    p2, _ = verify_chain(rows[2:], h1)
    assert p1 == p2 == []
    # Payload diganti (isi salinan diubah tanpa menghitung ulang rantai).
    forged = rows[2].__class__(**{**rows[2].__dict__, "payload_hash": "f" * 64})
    assert any("row_hash_mismatch" in p for p in verify_chain([*rows[:2], forged, *rows[3:]])[0])
    # Baris dihapus dari salinan.
    gap = verify_chain([*rows[:2], *rows[3:]])[0]
    assert any("seq_gap" in p for p in gap) and any("prev_mismatch" in p for p in gap)
    # Jangkar setelah TTL: mulai dari seq 3 tanpa kepala -> dipercaya.
    assert verify_chain(rows[2:])[0] == []
    bad_genesis = rows[0].__class__(**{**rows[0].__dict__, "prev_hash": "a" * 64})
    assert any("bad_genesis" in p for p in verify_chain([bad_genesis])[0])


# ------------------------------------------------------------------ redaksi PII


def test_personal_log_copy_has_no_identifier_or_values() -> None:
    log = _log(1, model="res.partner", res_name="res.partner,77", res_id=77, is_personal=True)
    line = _line(1, 1, field_name="phone", old_value="62812****789", new_value="62812****111", is_masked=True)
    batch = build_copy([log], {1: [line]}, {}, "new")
    row = batch.log_rows[0]
    assert _col(row, "res_id") is None and _col(row, "res_name") is None
    copied = batch.line_rows[0]
    for name in ("old_value", "new_value", "old_ref", "new_ref"):
        assert copied[LINE_COPY_COLUMNS.index(name)] is None
    assert copied[LINE_COPY_COLUMNS.index("redacted")] == 1
    assert copied[LINE_COPY_COLUMNS.index("line_hash")] == audit.line_hash(line)
    assert "62812" not in repr(batch.log_rows) + repr(batch.line_rows)


def test_reference_to_person_redacted_in_non_personal_log() -> None:
    lines = [
        _line(1, 1, field_name="partner_id", old_value="Budi Santoso", old_ref="res.partner,45", new_value=None),
        _line(2, 1, field_name="list_price"),
    ]
    batch = build_copy([_log(1, model="sale.order")], {1: lines}, {}, "new")
    redacted, kept = batch.line_rows
    assert redacted[LINE_COPY_COLUMNS.index("old_value")] is None
    assert redacted[LINE_COPY_COLUMNS.index("old_ref")] is None
    assert redacted[LINE_COPY_COLUMNS.index("redacted")] == 1
    assert kept[LINE_COPY_COLUMNS.index("new_value")] == "1200"
    assert kept[LINE_COPY_COLUMNS.index("redacted")] == 0
    assert "Budi" not in repr(batch.line_rows)
    # Log non-pribadi tetap menyimpan res_id/res_name.
    assert _col(batch.log_rows[0], "res_id") == 11


# ------------------------------------------------------------------ klasifikasi log yang hilang di sumber


def _copied(log_id: int, age_days: float, model: str = "product.template", op: str = "write") -> CopiedLog:
    return CopiedLog(log_id, NOW - timedelta(days=age_days), model, op, "h" * 64)


def test_classify_missing_retention_erasure_and_unexplained() -> None:
    purges = [
        PurgeLog("efn.audit.log", "retensi: 2 log", NOW - timedelta(days=1)),
        PurgeLog("res.partner", "erasure: 1 log dihapus, 0 baris ref dikosongkan", NOW - timedelta(days=3)),
    ]
    budget = PurgeBudget.from_purges(purges)
    missing = [
        _copied(1, 400),  # retensi
        _copied(2, 500),  # retensi
        _copied(3, 450),  # retensi melebihi anggaran -> unexplained
        _copied(4, 10, "res.partner"),  # erasure (log dibuat sebelum erasure)
        _copied(5, 1, "res.partner"),  # dibuat SETELAH erasure -> unexplained
        _copied(6, 400, "ir.actions.server"),  # model kontrol tidak pernah di-GC
        _copied(7, 400, "efn.audit.log", "purge"),  # log purge tidak pernah dihapus
        _copied(8, 30),  # terlalu muda untuk retensi -> unexplained
    ]
    result = classify_missing(missing, budget, NOW, ReconResult())
    assert (result.retention, result.erasure, result.unexplained) == (2, 1, 5)
    assert result.sample_ids == [3, 5, 6, 7, 8]
    assert result.alert


def test_purge_budget_ignores_foreign_notes() -> None:
    budget = PurgeBudget.from_purges([PurgeLog("x", "manual: 99 log", NOW)])
    assert budget.retention == 0 and budget.erasure_by_model == {}


# ------------------------------------------------------------------ job dengan fake I/O


class FakeSource:
    def __init__(self, logs: list[dict[str, Any]], lines: list[dict[str, Any]]) -> None:
        self.logs = {int(r["id"]): r for r in logs}
        self.lines = lines
        self.calls: list[str] = []

    def query(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        self.calls.append(sql)
        ordered = [self.logs[k] for k in sorted(self.logs)]
        if sql == SQL_AUDIT_NEW:
            settle = params["settle_before"].replace(tzinfo=None)
            rows = [r for r in ordered if r["id"] > params["after_id"] and r["create_date"] < settle]
            return rows[: params["limit"]]
        if sql == SQL_AUDIT_BY_IDS:
            return [r for r in ordered if r["id"] in params["ids"]]
        if sql in (SQL_AUDIT_RANGE, SQL_AUDIT_IDS_RANGE):
            return [r for r in ordered if params["lo"] < r["id"] <= params["hi"]]
        if sql == SQL_AUDIT_LINES:
            return [ln for ln in self.lines if ln["log_id"] in params["ids"]]
        if sql == SQL_AUDIT_PURGES:
            return [r for r in ordered if r["operation"] == "purge"]
        raise AssertionError(sql)


class FakeStore:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = {
            "audit_log_copy": [],
            "audit_log_line_copy": [],
            "audit_recon_run": [],
        }
        self.fail_on: str | None = None

    def insert(self, table: str, columns: Any, rows: list[list[Any]]) -> None:
        if self.fail_on == table:
            self.fail_on = None
            raise ConnectionError("clickhouse down")
        self.tables[table].extend(dict(zip(columns, r, strict=True)) for r in rows)

    @property
    def logs(self) -> list[dict[str, Any]]:
        return self.tables["audit_log_copy"]

    def heads(self) -> dict[int, ChainHead]:
        out: dict[int, ChainHead] = {}
        for r in self.logs:
            cur = out.get(r["chain_company"])
            if cur is None or r["chain_seq"] > cur.seq:
                out[r["chain_company"]] = ChainHead(r["chain_seq"], r["row_hash"])
        return out

    def max_log_id(self) -> int:
        return max((r["log_id"] for r in self.logs), default=0)

    def min_log_id_since(self, since: datetime) -> int | None:
        ids = [r["log_id"] for r in self.logs if r["create_date"] >= since]
        return min(ids) if ids else None

    def log_ids_between(self, lo: int, hi: int) -> set[int]:
        return {r["log_id"] for r in self.logs if lo < r["log_id"] <= hi}

    def logs_with_lines(self, log_ids: Any) -> set[int]:
        return {r["log_id"] for r in self.tables["audit_log_line_copy"] if r["log_id"] in set(log_ids)}

    def copied_range(self, lo: int, hi: int) -> list[CopiedLog]:
        return [
            CopiedLog(r["log_id"], r["create_date"], r["model_name"], r["operation"], r["payload_hash"])
            for r in self.logs
            if lo < r["log_id"] <= hi
        ]

    def line_hashes(self, log_ids: Any) -> dict[int, dict[int, str]]:
        out: dict[int, dict[int, str]] = {}
        for r in self.tables["audit_log_line_copy"]:
            if r["log_id"] in set(log_ids):
                out.setdefault(r["log_id"], {})[r["line_id"]] = r["line_hash"]
        return out

    def chain_rows(self, company: int, after_seq: int, limit: int) -> list[ChainRow]:
        rows = sorted(
            (r for r in self.logs if r["chain_company"] == company and r["chain_seq"] > after_seq),
            key=lambda r: r["chain_seq"],
        )
        return [
            ChainRow(r["chain_company"], r["chain_seq"], r["log_id"], r["payload_hash"], r["prev_hash"], r["row_hash"])
            for r in rows[:limit]
        ]

    def last_recon_at(self) -> datetime | None:
        runs = self.tables["audit_recon_run"]
        return max(r["run_at"] for r in runs) if runs else None


CFG = AuditSettings(batch_size=2, max_pages=50)


def _fixture() -> tuple[FakeSource, FakeStore]:
    logs = [_log(i, company=2 if i % 2 else 3) for i in range(1, 7)]
    lines = [_line(100 + i, i) for i in range(1, 7)]
    return FakeSource(logs, lines), FakeStore()


def test_copy_is_incremental_idempotent_and_respects_settle_window() -> None:
    source, store = _fixture()
    source.logs[7] = _log(7, create_date=(NOW - timedelta(minutes=2)).replace(tzinfo=None))  # belum settle
    result = run_audit_copy(source, store, CFG, NOW)
    assert result.new_rows == 6
    assert sorted(r["log_id"] for r in store.logs) == [1, 2, 3, 4, 5, 6]
    assert len(store.tables["audit_log_line_copy"]) == 6
    again = run_audit_copy(source, store, CFG, NOW)
    assert (again.new_rows, again.late_rows) == (0, 0)
    assert len(store.logs) == 6
    later = run_audit_copy(source, store, CFG, NOW + timedelta(minutes=30))
    assert later.new_rows == 1
    assert verify_chain(store.chain_rows(2, 0, 100))[0] == []


def test_late_commit_is_appended_to_chain() -> None:
    source, store = _fixture()
    late = source.logs.pop(3)
    run_audit_copy(source, store, CFG, NOW)
    source.logs[3] = late  # transaksi panjang commit setelah id 4..6 tersalin
    result = run_audit_copy(source, store, CFG, NOW)
    assert (result.late_rows, result.new_rows) == (1, 0)
    row = next(r for r in store.logs if r["log_id"] == 3)
    assert row["ingest_kind"] == "late"
    assert verify_chain(store.chain_rows(2, 0, 100))[0] == []


def test_crash_between_lines_and_logs_does_not_duplicate_lines() -> None:
    source, store = _fixture()
    store.fail_on = "audit_log_copy"
    with pytest.raises(ConnectionError):
        run_audit_copy(source, store, CFG, NOW)
    assert store.logs == [] and len(store.tables["audit_log_line_copy"]) == 2
    run_audit_copy(source, store, CFG, NOW)
    line_ids = [r["line_id"] for r in store.tables["audit_log_line_copy"]]
    assert sorted(line_ids) == sorted(set(line_ids)) and len(line_ids) == 6


def test_recon_clean_then_detects_deletion_modification_and_forgery() -> None:
    source, store = _fixture()
    run_audit_copy(source, store, CFG, NOW)
    assert recon_due(store, CFG, NOW)
    clean = run_audit_recon(source, store, CFG, NOW)
    assert not clean.alert and clean.checked == 6
    assert not recon_due(store, CFG, NOW + timedelta(hours=1))
    # Tamper di PostgreSQL: hapus log 2 (SQL langsung), ubah nilai baris log 3, sisipkan log palsu id 0<5.
    del source.logs[2]
    next(ln for ln in source.lines if ln["log_id"] == 3)["new_value"] = "999999"
    source.logs[5]["user_id"] = 1
    result = run_audit_recon(source, store, CFG, NOW)
    assert (result.unexplained, result.modified, result.unseen) == (1, 2, 0)
    assert result.alert and set(result.sample_ids) == {2, 3, 5}
    assert store.tables["audit_recon_run"][-1]["unexplained"] == 1


def test_recon_erasure_redaction_and_purge_are_legitimate() -> None:
    source, store = _fixture()
    source.logs[2] = _log(2, model="res.partner", is_personal=True)
    source.logs[4]["model_name"] = "sale.order"
    for ln in source.lines:
        if ln["log_id"] == 4:
            ln.update(field_name="partner_id", old_ref="res.partner,9", old_value="Budi", new_value=None)
    run_audit_copy(source, store, CFG, NOW)
    # Erasure subjek res.partner,9: log 2 dihapus, ref di log 4 dikosongkan, log purge ditulis.
    del source.logs[2]
    for ln in source.lines:
        if ln["log_id"] == 4:
            ln.update(old_value=None, new_value=None, old_ref=None, new_ref=None)
    source.logs[50] = _log(
        50,
        model="res.partner",
        operation="purge",
        note="erasure: 1 log dihapus, 1 baris ref dikosongkan",
        create_date=NOW.replace(tzinfo=None),
    )
    result = run_audit_recon(source, store, CFG, NOW)
    assert (result.erasure, result.erasure_redacted, result.unexplained, result.modified) == (1, 1, 0, 0)
    assert result.unseen == 0  # log purge id 50 > max id tersalin: belum disalin, bukan unseen
    assert not result.alert


def test_recon_detects_rewritten_copy_chain() -> None:
    source, store = _fixture()
    run_audit_copy(source, store, CFG, NOW)
    store.logs[1]["payload_hash"] = "0" * 64
    result = run_audit_recon(source, store, CFG, NOW)
    assert result.chain_problems >= 1 and result.alert
