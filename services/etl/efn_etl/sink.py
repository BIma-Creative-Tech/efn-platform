"""Verifikasi baris sink audit Odoo v2 + jangkar rantai salinan (SEC-51/52). Di-wire ke rekonsiliasi (Gelombang 14).

Sumber kebenaran format: `services/odoo/addons/efn_audit/models/base.py` (`sink_canonical`, `sink_mac`, `sink_line`)
dan README efn_audit "Format sink v2". Baris log Odoo: `... odoo.addons.efn_audit.sink: EFN_AUDIT_SINK {json}`;
`mac` = HMAC-SHA256 hex atas JSON kanonik tanpa `mac` (sort_keys, separator `,`/`:`, ensure_ascii), kunci dari
`/run/secrets/efn_audit_sink_key` (env `EFN_AUDIT_SINK_KEY_FILE`, >= 32 byte).

Anomali yang dihasilkan (untuk `EFN_AUDIT_RECON`):
- `sink_mac_invalid`  : baris sink ber-`mac` salah / null / JSON rusak (pemalsuan lewat injeksi log, SEC-51 butir 1,
                        atau kunci belum di-mount);
- `sink_missing_log`  : baris sink valid yang `log_id`-nya tidak ada di PostgreSQL maupun salinan ClickHouse, atau
                        model/operasinya berbeda (log dihapus/diubah setelah sink terkirim);
- `log_missing_sink`  : log model kontrol / purge di PostgreSQL (lebih tua dari jendela settle) tanpa baris sink valid
                        di jendela Loki (sink dimatikan, Promtail/Loki putus, atau log disisipkan lewat SQL).

Jangkar (SEC-52 butir 3): baris `EFN_AUDIT_CHAIN_HEAD` siklus-siklus sebelumnya (stream Loki `efn_audit_anchor`,
retensi 400 hari) dibandingkan dengan salinan ClickHouse SAAT INI:
- `anchor_mismatch` : (company, seq) ada di salinan tetapi `row_hash` berbeda dari yang dicatat dulu, atau Loki memuat
                      dua hash berbeda untuk (company, seq) yang sama -> salinan ditulis ulang (admin ClickHouse);
- `anchor_missing`  : (company, seq) yang pernah dicatat tidak ada lagi di salinan (baris dihapus/rantai dipotong);
- `anchor_absent`   : salinan punya rantai tetapi tidak ada satu pun jangkar di jendela (promtail/Loki putus).

Wiring (`run_sink_check`): dipanggil `audit_job.run_audit_recon` (harian). Baris Loki diambil lewat `efn_etl.loki`
(query_range, mTLS CN `etl` ke loki-tls). Kegagalan Loki = `sink_error=1` di `EFN_AUDIT_RECON` (alert), bukan diam.
"""

import hashlib
import hmac
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

from efn_etl.loki import LokiTransport, iter_lines, query_range
from efn_etl.watermark import to_utc

SINK_PREFIX = "EFN_AUDIT_SINK"
SINK_VERSION = 2
MIN_KEY_BYTES = 32
# Diambil dari baris terstruktur Odoo; JSON tanpa newline (ensure_ascii di sisi Odoo) -> satu baris.
_LINE_RE = re.compile(r" odoo\.addons\.efn_audit\.sink: EFN_AUDIT_SINK (\{[^\n]*\})\s*$")
# Model yang SETIAP log-nya dikirim ke sink (CONTROL_SPECS efn_audit tanpa res.users — log res.users bisa juga
# berasal dari aturan data pribadi biasa yang tidak di-sink) + efn.audit.rule. Operasi `purge` selalu di-sink.
EXPECTED_SINK_MODELS = frozenset(
    {
        "ir.config_parameter",
        "ir.actions.server",
        "ir.cron",
        "ir.module.module",
        "base.automation",
        "res.groups",
        "ir.rule",
        "ir.model.access",
        "res.users.apikeys",
        "efn.audit.rule",
    }
)


def sink_canonical(event: Mapping[str, Any]) -> str:
    body = {k: v for k, v in event.items() if k != "mac"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def sink_mac(key: bytes, event: Mapping[str, Any]) -> str:
    return hmac.new(key, sink_canonical(event).encode("ascii"), hashlib.sha256).hexdigest()


def load_key(raw: bytes) -> bytes:
    key = raw.strip()
    if len(key) < MIN_KEY_BYTES:
        raise ValueError("kunci sink audit < 32 byte")
    return key


@dataclass(frozen=True)
class SinkEvent:
    log_id: int
    model: str
    operation: str
    valid: bool


def parse_sink_line(line: str, key: bytes) -> SinkEvent | None:
    """None = bukan baris sink (diabaikan). valid=False = baris sink yang tidak bisa diverifikasi."""
    match = _LINE_RE.search(line)
    if not match:
        return None
    try:
        event = json.loads(match.group(1))
    except json.JSONDecodeError:
        return SinkEvent(0, "", "", False)
    if not isinstance(event, dict):
        return SinkEvent(0, "", "", False)
    mac = event.get("mac")
    log_id = event.get("log_id")
    ok = (
        event.get("v") == SINK_VERSION
        and isinstance(mac, str)
        and isinstance(log_id, int)
        and hmac.compare_digest(mac, sink_mac(key, event))
    )
    return SinkEvent(
        int(log_id) if isinstance(log_id, int) else 0,
        str(event.get("model") or ""),
        str(event.get("operation") or ""),
        ok,
    )


@dataclass(frozen=True)
class PgControlLog:
    log_id: int
    model: str
    operation: str
    create_date: datetime


@dataclass
class SinkReconResult:
    sink_lines: int = 0
    sink_mac_invalid: int = 0
    sink_missing_log: int = 0
    log_missing_sink: int = 0
    sample_ids: list[int] = field(default_factory=list)
    # Loki tidak mengembalikan semua baris jendela (batas halaman) -> hasil tidak lengkap = alert.
    truncated: bool = False

    @property
    def alert(self) -> bool:
        return bool(self.sink_mac_invalid or self.sink_missing_log or self.log_missing_sink or self.truncated)

    def note(self, log_id: int) -> None:
        if log_id and len(self.sample_ids) < 50:
            self.sample_ids.append(log_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sink_lines": self.sink_lines,
            "sink_mac_invalid": self.sink_mac_invalid,
            "sink_missing_log": self.sink_missing_log,
            "log_missing_sink": self.log_missing_sink,
            "sink_truncated": int(self.truncated),
            "sink_sample_ids": list(self.sample_ids),
        }


def reconcile_sink(
    lines: Iterable[str],
    key: bytes,
    pg_logs: Mapping[int, PgControlLog],
    copied_ids: set[int],
    window_start: datetime,
    window_end: datetime,
    settle: timedelta,
) -> SinkReconResult:
    """Bandingkan baris sink (Loki, jendela [start, end]) dengan log kontrol PostgreSQL di jendela yang sama.

    `pg_logs`: log PG di jendela (model kontrol/purge, id -> log). `copied_ids`: id yang pernah tersalin ke ClickHouse
    (log yang sah dihapus retensi tetap punya jejak salinan). Log yang lebih muda dari `window_end - settle` belum
    diwajibkan punya sink (Promtail/Loki punya latensi).
    """
    result = SinkReconResult()
    seen: set[int] = set()
    for line in lines:
        event = parse_sink_line(line, key)
        if event is None:
            continue
        result.sink_lines += 1
        if not event.valid:
            result.sink_mac_invalid += 1
            result.note(event.log_id)
            continue
        seen.add(event.log_id)
        log = pg_logs.get(event.log_id)
        if log is None:
            if event.log_id not in copied_ids:
                result.sink_missing_log += 1
                result.note(event.log_id)
            continue
        if log.model != event.model or log.operation != event.operation:
            result.sink_missing_log += 1
            result.note(event.log_id)
    start, cutoff = to_utc(window_start), to_utc(window_end) - settle
    for log in pg_logs.values():
        expected = log.operation == "purge" or log.model in EXPECTED_SINK_MODELS
        created = to_utc(log.create_date)
        if expected and start <= created <= cutoff and log.log_id not in seen:
            result.log_missing_sink += 1
            result.note(log.log_id)
    return result


# ============================================================== Jangkar rantai salinan (SEC-52 butir 3)

ANCHOR_PREFIX = "EFN_AUDIT_CHAIN_HEAD"
# Format baris = audit_job.run_audit_copy (logger efn_etl.audit_job, format logging __main__) = regex promtail stream
# efn_audit_anchor. Dicocokkan di akhir baris (timestamp di depan tidak dipakai).
_ANCHOR_RE = re.compile(
    r" INFO efn_etl\.audit_job EFN_AUDIT_CHAIN_HEAD "
    r'(\{"company":\d{1,10},"seq":\d{1,19},"row_hash":"[0-9a-f]{64}"(?:,"epoch":\d{1,10})?\})\s*$'
)
SINK_QUERY = '{stream="efn_audit_sink"}'
ANCHOR_QUERY = '{stream="efn_audit_anchor", audit_event="chain_head"}'


@dataclass(frozen=True)
class Anchor:
    company: int
    seq: int
    row_hash: str
    epoch: int = 0  # chain epoch (audit.decide_epoch); baris lama tanpa kunci epoch = 0


def parse_anchor_line(line: str) -> Anchor | None:
    match = _ANCHOR_RE.search(line)
    if not match:
        return None
    body = json.loads(match.group(1))
    return Anchor(int(body["company"]), int(body["seq"]), str(body["row_hash"]), int(body.get("epoch", 0)))


@dataclass
class AnchorResult:
    anchors_checked: int = 0
    anchor_mismatch: int = 0
    anchor_missing: int = 0
    anchor_absent: int = 0
    samples: list[str] = field(default_factory=list)

    @property
    def alert(self) -> bool:
        return bool(self.anchor_mismatch or self.anchor_missing or self.anchor_absent)

    def note(self, company: int, seq: int) -> None:
        if len(self.samples) < 20:
            self.samples.append(f"{company}:{seq}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "anchors_checked": self.anchors_checked,
            "anchor_mismatch": self.anchor_mismatch,
            "anchor_missing": self.anchor_missing,
            "anchor_absent": self.anchor_absent,
            "anchor_samples": list(self.samples),
        }


def verify_anchors(
    anchors: Iterable[Anchor],
    current: Mapping[tuple[int, int, int], str],
    companies_with_chain: Iterable[int],
) -> AnchorResult:
    """Bandingkan jangkar lama dengan salinan saat ini. `current`: (epoch, company, seq) -> row_hash salinan ClickHouse
    untuk jangkar yang dicatat (yang tidak ada = baris hilang). Epoch membedakan rantai setelah restore DB Odoo
    (seq dimulai ulang), sehingga jangkar epoch lama tetap dicocokkan ke salinan bukti epoch lama."""
    result = AnchorResult()
    unique: dict[tuple[int, int, int], set[str]] = {}
    for anchor in anchors:
        unique.setdefault((anchor.epoch, anchor.company, anchor.seq), set()).add(anchor.row_hash)
    for (epoch, company, seq), hashes in sorted(unique.items()):
        result.anchors_checked += 1
        stored = current.get((epoch, company, seq))
        if stored is None:
            result.anchor_missing += 1
            result.note(company, seq)
        elif len(hashes) > 1 or stored not in hashes:
            result.anchor_mismatch += 1
            result.note(company, seq)
    if not unique and any(True for _ in companies_with_chain):
        result.anchor_absent = 1
    return result


# ============================================================== Orkestrasi (dipanggil audit_job.run_audit_recon)

SQL_SINK_PG_WINDOW = """
SELECT id, model_name, operation, create_date
FROM efn_audit_log
WHERE create_date >= %(start)s AND create_date < %(end)s
  AND (operation = 'purge' OR model_name = ANY(%(models)s))
ORDER BY id
"""
SQL_SINK_PG_BY_IDS = "SELECT id, model_name, operation, create_date FROM efn_audit_log WHERE id = ANY(%(ids)s)"
_ID_CHUNK = 5000


class SinkSource(Protocol):
    def query(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]: ...


class SinkStore(Protocol):
    def copied_log_ids(self, log_ids: Sequence[int]) -> set[int]: ...

    def chain_hashes(self, company: int, seqs: Sequence[int], epoch: int = 0) -> dict[int, str]: ...

    def heads(self) -> Mapping[int, Any]: ...


@dataclass(frozen=True)
class SinkCheckSettings:
    window: timedelta = timedelta(hours=25)
    settle: timedelta = timedelta(minutes=10)
    anchor_lookback: timedelta = timedelta(days=8)
    page_limit: int = 5000
    max_pages: int = 200


@dataclass
class SinkCheckResult:
    sink: SinkReconResult
    anchors: AnchorResult

    @property
    def alert(self) -> bool:
        return self.sink.alert or self.anchors.alert

    def as_dict(self) -> dict[str, Any]:
        return {**self.sink.as_dict(), **self.anchors.as_dict()}


def _pg_logs(rows: Iterable[Mapping[str, Any]]) -> dict[int, PgControlLog]:
    return {
        int(r["id"]): PgControlLog(
            int(r["id"]), str(r["model_name"] or ""), str(r["operation"] or ""), r["create_date"]
        )
        for r in rows
    }


def run_sink_check(
    loki: LokiTransport,
    source: SinkSource,
    store: SinkStore,
    key: bytes,
    now: datetime,
    cfg: SinkCheckSettings,
) -> SinkCheckResult:
    """Verifikasi sink (jendela `cfg.window`) + jangkar (`cfg.anchor_lookback`). Melempar bila Loki/PG gagal."""
    start = now - cfg.window
    # Baris sink dikirim Odoo setelah commit (postcommit) -> sedikit lebih lambat dari create_date log; jendela Loki
    # dimulai `settle` lebih awal dan log PG termuda (< settle) belum diwajibkan punya sink (reconcile_sink).
    fetched = query_range(loki, SINK_QUERY, start - cfg.settle, now, cfg.page_limit, cfg.max_pages)
    lines = list(iter_lines(fetched))
    parsed = (parse_sink_line(line, key) for line in lines)
    sink_ids = sorted({e.log_id for e in parsed if e is not None and e.valid and e.log_id})
    pg_logs = _pg_logs(
        source.query(SQL_SINK_PG_WINDOW, {"start": start, "end": now, "models": sorted(EXPECTED_SINK_MODELS)})
    )
    # Sink untuk log di luar jendela PG (mis. log res.users, atau batas jendela) dicari per id.
    outside = [i for i in sink_ids if i not in pg_logs]
    for i in range(0, len(outside), _ID_CHUNK):
        pg_logs.update(_pg_logs(source.query(SQL_SINK_PG_BY_IDS, {"ids": outside[i : i + _ID_CHUNK]})))
    absent_in_pg = [i for i in sink_ids if i not in pg_logs]
    copied = store.copied_log_ids(absent_in_pg) if absent_in_pg else set()
    sink = reconcile_sink(lines, key, pg_logs, copied, start, now, cfg.settle)
    sink.truncated = fetched.truncated

    anchor_fetch = query_range(loki, ANCHOR_QUERY, now - cfg.anchor_lookback, now, cfg.page_limit, cfg.max_pages)
    anchors = [a for a in (parse_anchor_line(line) for line in iter_lines(anchor_fetch)) if a is not None]
    by_chain: dict[tuple[int, int], set[int]] = {}
    for anchor in anchors:
        by_chain.setdefault((anchor.epoch, anchor.company), set()).add(anchor.seq)
    current: dict[tuple[int, int, int], str] = {}
    for (epoch, company), seqs in sorted(by_chain.items()):
        for seq, digest in store.chain_hashes(company, sorted(seqs), epoch).items():
            current[(epoch, company, seq)] = digest
    anchor_result = verify_anchors(anchors, current, store.heads().keys())
    sink.truncated = sink.truncated or anchor_fetch.truncated
    return SinkCheckResult(sink=sink, anchors=anchor_result)
