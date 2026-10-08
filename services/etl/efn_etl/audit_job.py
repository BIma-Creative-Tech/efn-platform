"""Job salinan audit tamper-evident + rekonsiliasi (Gelombang 12). Orkestrasi; I/O lewat Protocol (diuji dengan fake).

Alur per siklus ETL (bila `ETL_AUDIT_COPY_ENABLED`):
1. *late pass*: id <= max id tersalin yang muncul belakangan (transaksi panjang commit setelah id lebih besar
   tersalin) dalam jendela `ETL_AUDIT_LATE_WINDOW_SECONDS` -> disalin dengan `ingest_kind='late'`;
2. *new pass*: id > max id tersalin, `create_date < now - ETL_AUDIT_SETTLE_SECONDS` (memberi waktu transaksi commit);
3. setiap `ETL_AUDIT_RECON_INTERVAL_SECONDS`: rekonsiliasi PG vs salinan + verifikasi rantai -> baris
   `audit_recon_run` + log `EFN_AUDIT_RECON {json}` (WARNING bila ada anomali; untuk alert Loki).
   Gelombang 14 (SEC-51/52): bila `sink_check` diberikan, rekonsiliasi juga memverifikasi baris sink Odoo di Loki
   (`sink_mac_invalid`, `sink_missing_log`, `log_missing_sink`) dan jangkar kepala rantai antar hari
   (`anchor_mismatch`, `anchor_missing`, `anchor_absent`) — efn_etl/sink.py. Gagal ambil Loki = `sink_error=1` (alert).

Kepala rantai dibaca dari ClickHouse setiap siklus (satu instance ETL saja; dua instance = rantai bercabang, terdeteksi
verifikasi sebagai `seq_gap`/`prev_mismatch`). Kepala dicatat ke log (`EFN_AUDIT_CHAIN_HEAD`) -> Loki = jangkar
eksternal: penulisan ulang salinan oleh admin ClickHouse terdeteksi dengan membandingkan kepala lama di Loki.
"""

import json
import logging
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from efn_etl.audit import (
    LINE_COPY_COLUMNS,
    LOG_COPY_COLUMNS,
    ChainHead,
    ChainRow,
    CopiedLog,
    PurgeBudget,
    PurgeLog,
    ReconResult,
    build_copy,
    classify_missing,
    compare_content,
    verify_chain,
)
from efn_etl.sink import SinkCheckResult
from efn_etl.watermark import EPOCH

log = logging.getLogger(__name__)

LOG_COPY_TABLE = "audit_log_copy"
LINE_COPY_TABLE = "audit_log_line_copy"
RECON_TABLE = "audit_recon_run"
RECON_COLUMNS = (
    "run_at",
    "max_log_id",
    "checked",
    "retention",
    "erasure",
    "unexplained",
    "unseen",
    "modified",
    "erasure_redacted",
    "chain_problems",
    "sample_ids",
    # Gelombang 14 (05-loyalty-recon.sql menambah kolom): verifikasi sink Loki + jangkar rantai.
    "sink_checked",
    "sink_error",
    "sink_lines",
    "sink_mac_invalid",
    "sink_missing_log",
    "log_missing_sink",
    "anchors_checked",
    "anchor_mismatch",
    "anchor_missing",
    "anchor_absent",
)

SinkCheck = Callable[[datetime], SinkCheckResult]

# Kolom sumber = grant di sql/odoo-readonly-grants.sql. origin_user_id: efn_audit >= 19.0.1.2.0 (SEC-46).
SQL_AUDIT_NEW = """
SELECT id, create_date, create_uid, user_id, origin_user_id, is_sudo, operation, model_name, res_id, res_name,
       company_id, prev_company_id, is_personal, note
FROM efn_audit_log
WHERE id > %(after_id)s AND create_date < %(settle_before)s
ORDER BY id
LIMIT %(limit)s
"""
SQL_AUDIT_BY_IDS = """
SELECT id, create_date, create_uid, user_id, origin_user_id, is_sudo, operation, model_name, res_id, res_name,
       company_id, prev_company_id, is_personal, note
FROM efn_audit_log
WHERE id = ANY(%(ids)s)
ORDER BY id
"""
SQL_AUDIT_RANGE = """
SELECT id, create_date, create_uid, user_id, origin_user_id, is_sudo, operation, model_name, res_id, res_name,
       company_id, prev_company_id, is_personal, note
FROM efn_audit_log
WHERE id > %(lo)s AND id <= %(hi)s
ORDER BY id
"""
SQL_AUDIT_IDS_RANGE = "SELECT id FROM efn_audit_log WHERE id > %(lo)s AND id <= %(hi)s ORDER BY id"
SQL_AUDIT_LINES = """
SELECT id, log_id, create_date, field_name, field_label, old_value, new_value, old_ref, new_ref, is_masked
FROM efn_audit_log_line
WHERE log_id = ANY(%(ids)s)
ORDER BY log_id, id
"""
SQL_AUDIT_PURGES = "SELECT model_name, note, create_date FROM efn_audit_log WHERE operation = 'purge' ORDER BY id"


class AuditSource(Protocol):
    def query(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]: ...


class AuditStore(Protocol):
    def heads(self) -> dict[int, ChainHead]: ...

    def max_log_id(self) -> int: ...

    def min_log_id_since(self, since: datetime) -> int | None: ...

    def log_ids_between(self, lo: int, hi: int) -> set[int]: ...

    def logs_with_lines(self, log_ids: Sequence[int]) -> set[int]: ...

    def insert(self, table: str, columns: Sequence[str], rows: list[list[Any]]) -> None: ...

    def copied_range(self, lo: int, hi: int) -> list[CopiedLog]: ...

    def line_hashes(self, log_ids: Sequence[int]) -> dict[int, dict[int, str]]: ...

    def chain_rows(self, company: int, after_seq: int, limit: int) -> list[ChainRow]: ...

    def last_recon_at(self) -> datetime | None: ...


@dataclass(frozen=True)
class AuditSettings:
    settle: timedelta = timedelta(minutes=10)
    late_window: timedelta = timedelta(days=1)
    batch_size: int = 2000
    max_pages: int = 100
    recon_interval: timedelta = timedelta(hours=24)
    recon_content: bool = True


@dataclass(frozen=True)
class AuditCopyResult:
    new_rows: int
    late_rows: int
    heads: dict[int, ChainHead]


def _lines_by_log(source: AuditSource, ids: Sequence[int]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    if ids:
        for line in source.query(SQL_AUDIT_LINES, {"ids": list(ids)}):
            grouped[int(line["log_id"])].append(line)
    return grouped


def _copy(
    source: AuditSource, store: AuditStore, logs: list[dict[str, Any]], heads: dict[int, ChainHead], kind: str
) -> int:
    if not logs:
        return 0
    ids = [int(r["id"]) for r in logs]
    batch = build_copy(logs, _lines_by_log(source, ids), heads, kind)
    # Baris dulu, lalu log: log di salinan => barisnya ada. Crash di antara keduanya -> baris log yang sama sudah ada,
    # dilewati saat diulang (append-only, tanpa duplikat).
    present = store.logs_with_lines(sorted(batch.line_log_ids)) if batch.line_log_ids else set()
    line_rows = [row for row in batch.line_rows if row[1] not in present]
    if line_rows:
        store.insert(LINE_COPY_TABLE, LINE_COPY_COLUMNS, line_rows)
    store.insert(LOG_COPY_TABLE, LOG_COPY_COLUMNS, batch.log_rows)
    return len(batch.log_rows)


def run_audit_copy(source: AuditSource, store: AuditStore, cfg: AuditSettings, now: datetime) -> AuditCopyResult:
    heads = store.heads()
    max_id = store.max_log_id()
    late_rows = 0
    if max_id:
        low = store.min_log_id_since(now - cfg.late_window)
        low = (low - 1) if low is not None else max_id
        if low < max_id:
            pg_ids = {int(r["id"]) for r in source.query(SQL_AUDIT_IDS_RANGE, {"lo": low, "hi": max_id})}
            late = sorted(pg_ids - store.log_ids_between(low, max_id))
            for i in range(0, len(late), cfg.batch_size):
                chunk = late[i : i + cfg.batch_size]
                late_rows += _copy(source, store, source.query(SQL_AUDIT_BY_IDS, {"ids": chunk}), heads, "late")
            if late:
                log.warning("job=audit_copy %d log terlambat (late commit) disalin; id=%s", len(late), late[:20])
    new_rows = 0
    after = max_id
    for _ in range(cfg.max_pages):
        params = {"after_id": after, "settle_before": now - cfg.settle, "limit": cfg.batch_size}
        rows = source.query(SQL_AUDIT_NEW, params)
        if not rows:
            break
        new_rows += _copy(source, store, rows, heads, "new")
        after = int(rows[-1]["id"])
        if len(rows) < cfg.batch_size:
            break
    else:
        log.warning("job=audit_copy mencapai batas %d halaman; dilanjutkan siklus berikutnya", cfg.max_pages)
    for company, head in sorted(heads.items()):
        log.info(
            "EFN_AUDIT_CHAIN_HEAD %s",
            json.dumps({"company": company, "seq": head.seq, "row_hash": head.row_hash}, separators=(",", ":")),
        )
    return AuditCopyResult(new_rows=new_rows, late_rows=late_rows, heads=heads)


def recon_due(store: AuditStore, cfg: AuditSettings, now: datetime) -> bool:
    last = store.last_recon_at()
    return last is None or now - last >= cfg.recon_interval


def _verify_chains(store: AuditStore, companies: Sequence[int], page: int, result: ReconResult) -> None:
    for company in companies:
        head: ChainHead | None = None
        after = 0
        while True:
            rows = store.chain_rows(company, after, page)
            if not rows:
                break
            problems, head = verify_chain(rows, head)
            result.chain_problems += len(problems)
            for p in problems[:10]:
                log.warning("job=audit_recon rantai: %s", p)
            after = rows[-1].chain_seq
            if len(rows) < page:
                break


def run_audit_recon(
    source: AuditSource,
    store: AuditStore,
    cfg: AuditSettings,
    now: datetime,
    sink_check: SinkCheck | None = None,
) -> ReconResult:
    """Bandingkan seluruh id 1..max_id tersalin dengan PostgreSQL per jendela id; klasifikasi selisih.

    `sink_check` (opsional): verifikasi sink Loki + jangkar; hasilnya digabung ke log & baris `audit_recon_run`.
    """
    result = ReconResult()
    purges = [
        PurgeLog(str(r["model_name"]), str(r["note"] or ""), r["create_date"])
        for r in source.query(SQL_AUDIT_PURGES, {})
    ]
    budget = PurgeBudget.from_purges(purges)
    max_id = store.max_log_id()
    missing: list[CopiedLog] = []
    # Mulai dari log tertua yang masih ada di salinan (TTL salinan membuang yang tertua; tidak dihitung `unseen`).
    oldest = store.min_log_id_since(EPOCH)
    lo = (oldest - 1) if oldest is not None else max_id
    while lo < max_id:
        hi = min(lo + cfg.batch_size, max_id)
        copied = {c.log_id: c for c in store.copied_range(lo, hi)}
        if cfg.recon_content:
            pg_logs = {int(r["id"]): r for r in source.query(SQL_AUDIT_RANGE, {"lo": lo, "hi": hi})}
        else:
            pg_logs = {int(r["id"]): r for r in source.query(SQL_AUDIT_IDS_RANGE, {"lo": lo, "hi": hi})}
        result.checked += len(copied)
        missing.extend(c for log_id, c in copied.items() if log_id not in pg_logs)
        for log_id in sorted(set(pg_logs) - set(copied)):
            result.unseen += 1
            result.note(log_id)
        common = sorted(set(pg_logs) & set(copied))
        if cfg.recon_content and common:
            lines = _lines_by_log(source, common)
            hashes = store.line_hashes(common)
            for log_id in common:
                compare_content(
                    pg_logs[log_id], lines.get(log_id, []), copied[log_id], hashes.get(log_id, {}), budget, result
                )
        lo = hi
    classify_missing(missing, budget, now, result)
    _verify_chains(store, sorted(store.heads()), max(cfg.batch_size, 1000), result)
    sink: SinkCheckResult | None = None
    sink_error = False
    if sink_check is not None:
        try:
            sink = sink_check(now)
        except Exception as exc:
            # Pesan tanpa isi baris log (LokiError/psycopg); verifikasi tidak berjalan = alert, bukan diam.
            sink_error = True
            log.warning("job=audit_recon verifikasi sink gagal: %s", type(exc).__name__)
    # Kunci pertama WAJIB "max_log_id" (regex promtail stream efn_audit_anchor, audit_event=recon).
    payload: dict[str, Any] = {"max_log_id": max_id, **result.as_dict()}
    if sink_check is not None:
        payload["sink_error"] = int(sink_error)
        if sink is not None:
            payload.update(sink.as_dict())
    alert = result.alert or sink_error or (sink is not None and sink.alert)
    if alert:
        log.warning("EFN_AUDIT_RECON %s", json.dumps(payload, separators=(",", ":")))
    else:
        log.info("EFN_AUDIT_RECON %s", json.dumps(payload, separators=(",", ":")))
    store.insert(
        RECON_TABLE,
        RECON_COLUMNS,
        [
            [
                now,
                max_id,
                result.checked,
                result.retention,
                result.erasure,
                result.unexplained,
                result.unseen,
                result.modified,
                result.erasure_redacted,
                result.chain_problems,
                list(result.sample_ids),
                int(sink_check is not None),
                int(sink_error),
                sink.sink.sink_lines if sink else 0,
                sink.sink.sink_mac_invalid if sink else 0,
                sink.sink.sink_missing_log if sink else 0,
                sink.sink.log_missing_sink if sink else 0,
                sink.anchors.anchors_checked if sink else 0,
                sink.anchors.anchor_mismatch if sink else 0,
                sink.anchors.anchor_missing if sink else 0,
                sink.anchors.anchor_absent if sink else 0,
            ]
        ],
    )
    return result
