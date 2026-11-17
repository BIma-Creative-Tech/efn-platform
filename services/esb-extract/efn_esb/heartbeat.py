"""Heartbeat run (pola `services/etl/efn_etl/heartbeat.py`): satu baris logfmt per run brand + file penanda di /tmp.

`EFN_ESB_HEARTBEAT brand=<b> mode=<run|backfill> status=<...> calls=<n> rows=<n> errors=<n> slices=<n>
duration_ms=<n> ts=<iso>` (satu baris). Tanpa kredensial/payload.
Alert umur data = `v_esb_data_quality` (docs/04 §25.8); file penanda dipakai
`python -m efn_esb health` (opsional; healthcheck compose cukup memastikan PID 1 hidup).
"""

import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from efn_esb.records import RunRecord, safe_token

MARKER = "EFN_ESB_HEARTBEAT"
log = logging.getLogger("efn_esb.heartbeat")


def heartbeat_line(run: RunRecord, duration_ms: int, ts: datetime) -> str:
    stamp = ts.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return (
        f"{MARKER} brand={safe_token(run.brand)} mode={safe_token(run.mode)} status={safe_token(run.status)} "
        f"calls={int(run.calls)} rows={int(run.rows)} errors={int(run.errors)} slices={int(run.slices)} "
        f"duration_ms={int(duration_ms)} ts={stamp}"
    )


def touch(path: str) -> None:
    try:
        file = Path(path)
        file.touch(exist_ok=True)
        os.utime(file, None)
    except OSError:
        log.warning("file heartbeat tidak bisa ditulis")


def is_fresh(path: str, max_age_seconds: float, now: float | None = None) -> bool:
    try:
        mtime = Path(path).stat().st_mtime
    except OSError:
        return False
    return ((time.time() if now is None else now) - mtime) <= max_age_seconds
