"""Baris tabel kendali `efn_esb.*` (raw, checkpoint, run, rasio BOM). Logika murni."""

import re
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from efn_esb.extract import RawDoc
from efn_esb.normalize import canonical_json, redact, sha256_hex
from efn_esb.planning import SliceTask
from efn_esb.transform import BomRatio

RAW_COLUMNS = ("brand", "endpoint", "doc_key", "doc_date", "payload", "payload_sha256", "fetched_at")
CHECKPOINT_COLUMNS = ("brand", "entity", "scope_key", "day", "status", "rows", "calls", "finished_at")
RUN_COLUMNS = ("run_id", "brand", "mode", "started_at", "finished_at", "calls", "rows", "errors", "status")
BOM_RATIO_COLUMNS = ("brand", "bom_id", "product_code", "product_name", "uom", "ratio", "sample_doc", "_version")
CONTROL_TABLES: dict[str, tuple[str, ...]] = {
    "raw_doc": RAW_COLUMNS,
    "extract_checkpoint": CHECKPOINT_COLUMNS,
    "extract_run": RUN_COLUMNS,
    "bom_ratio": BOM_RATIO_COLUMNS,
}
# extract_run.status (docs/04 §25.2/§25.3). `auth_suspended` = brand ditangguhkan setelah `auth_failed` berturut-turut
# (SEC-186; tanpa login/panggilan ESB sampai proses di-restart).
RUN_STATUSES = frozenset({"ok", "partial", "error", "limit", "auth_failed", "auth_suspended", "window_closed"})
_SAFE = re.compile(r"^[a-z0-9_*|:.\-]{1,64}$")


def version_now(now: datetime | None = None) -> datetime:
    """Versi slice: UTC, presisi milidetik (= DateTime64(3) agar cocok persis dengan checkpoint di view)."""
    value = (now or datetime.now(UTC)).astimezone(UTC)
    return value.replace(microsecond=(value.microsecond // 1000) * 1000)


def raw_rows(brand: str, docs: Iterable[RawDoc], fetched_at: datetime) -> list[list[Any]]:
    """Raw doc -> baris `efn_esb.raw_doc`. Redaksi diulang di sini (idempoten) sebagai lapisan kedua L-110."""
    out: list[list[Any]] = []
    for doc in docs:
        payload = canonical_json(redact(doc.payload))
        out.append([brand, doc.endpoint, doc.doc_key[:256], doc.doc_date, payload, sha256_hex(payload), fetched_at])
    return out


def checkpoint_row(brand: str, task: SliceTask, version: datetime, rows: int, calls: int) -> list[Any]:
    return [brand, task.entity, task.scope, task.day, "done", rows, calls, version]


def bom_ratio_rows(brand: str, ratios: Mapping[int, BomRatio], version: datetime) -> list[list[Any]]:
    return [
        [brand, bom_id, r.product_code, r.product_name, r.uom, float(r.ratio), r.sample_doc, version]
        for bom_id, r in sorted(ratios.items())
    ]


@dataclass
class RunRecord:
    brand: str
    mode: str
    started_at: datetime
    run_id: uuid.UUID
    calls: int = 0
    rows: int = 0
    errors: int = 0
    status: str = "ok"
    slices: int = 0

    def row(self, finished_at: datetime) -> list[Any]:
        if self.status not in RUN_STATUSES:
            raise ValueError("status run tidak valid")
        return [
            self.run_id,
            self.brand,
            self.mode,
            self.started_at,
            finished_at,
            self.calls,
            self.rows,
            self.errors,
            self.status,
        ]


def safe_token(value: str) -> str:
    """Nilai yang masuk baris log heartbeat: hanya karakter aman (anti log injection)."""
    return value if _SAFE.fullmatch(value) else "invalid"
