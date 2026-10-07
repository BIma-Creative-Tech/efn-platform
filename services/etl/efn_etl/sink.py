"""Verifikasi baris sink audit Odoo v2 (Gelombang 13, SEC-51/52) — logika murni, belum di-wire ke rekonsiliasi.

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

Yang BELUM: pengambilan baris dari Loki (query_range lewat loki-tls mTLS, butuh sertifikat klien CN `etl` + network
observability) dan mount secret ke container ETL — spesifikasi di laporan Gelombang 13 / README.
"""

import hashlib
import hmac
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

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

    @property
    def alert(self) -> bool:
        return bool(self.sink_mac_invalid or self.sink_missing_log or self.log_missing_sink)

    def note(self, log_id: int) -> None:
        if log_id and len(self.sample_ids) < 50:
            self.sample_ids.append(log_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sink_lines": self.sink_lines,
            "sink_mac_invalid": self.sink_mac_invalid,
            "sink_missing_log": self.sink_missing_log,
            "log_missing_sink": self.log_missing_sink,
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
