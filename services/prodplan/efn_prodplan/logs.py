"""Log JSON satu baris (docs/04 §30.11).

Hanya kunci di `ALLOWED_FIELDS` yang ditulis; kunci lain dibuang diam-diam. Dengan begitu password, token ESB, header
`X-EFN-Actor`, query string ESB, body ESB, dan username ESB tidak bisa masuk log lewat `log_event(...)` walau pemanggil
keliru meneruskannya.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

ALLOWED_FIELDS = frozenset(
    {
        "request_id",
        "sub",
        "plan_id",
        "job_id",
        "outbox_id",
        "esb_method",
        "esb_path_template",
        "http_status",
        "duration_ms",
        "error_code",
        # Tambahan aman (bukan rahasia, bukan data pribadi pelanggan):
        "reason",
        "brand",
        "esb_env",
        "status",
        "kind",
        "route",
        "method",
        "count",
        "error_class",
        "mfg_mode",
        "write_enabled",
    }
)
_LOGGER = logging.getLogger("efn_prodplan")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "event": getattr(record, "event", None) or "log",
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), default=str)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper() if level else "INFO")
    logging.getLogger("psycopg").setLevel(logging.WARNING)


def clean_fields(fields: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in fields.items():
        if key not in ALLOWED_FIELDS or value is None:
            continue
        if isinstance(value, str):
            value = value[:200]
        out[key] = value
    return out


def log_event(event: str, level: int = logging.INFO, **fields: Any) -> None:
    _LOGGER.log(level, event, extra={"event": event, "fields": clean_fields(fields)})
