"""Metrik Prometheus format teks, tanpa dependency (docs/04 §30.11). Thread-safe, label kardinalitas rendah."""

import threading
from collections.abc import Sequence

_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _labels(names: Sequence[str], values: Sequence[str], extra: str = "") -> str:
    parts = [f'{n}="{_escape(str(v))}"' for n, v in zip(names, values, strict=True)]
    if extra:
        parts.append(extra)
    return "{" + ",".join(parts) + "}" if parts else ""


class _Metric:
    def __init__(self, name: str, help_text: str, kind: str, labels: Sequence[str] = ()) -> None:
        self.name = name
        self.help = help_text
        self.kind = kind
        self.label_names = tuple(labels)
        self._lock = threading.Lock()

    def render(self) -> list[str]:
        return []


class Counter(_Metric):
    def __init__(self, name: str, help_text: str, labels: Sequence[str] = ()) -> None:
        super().__init__(name, help_text, "counter", labels)
        self._values: dict[tuple[str, ...], float] = {}

    def inc(self, *labels: str, amount: float = 1.0) -> None:
        with self._lock:
            key = tuple(str(x) for x in labels)
            self._values[key] = self._values.get(key, 0.0) + amount

    def value(self, *labels: str) -> float:
        return self._values.get(tuple(labels), 0.0)

    def render(self) -> list[str]:
        with self._lock:
            return [f"{self.name}{_labels(self.label_names, k)} {v}" for k, v in sorted(self._values.items())]


class Gauge(_Metric):
    def __init__(self, name: str, help_text: str, labels: Sequence[str] = ()) -> None:
        super().__init__(name, help_text, "gauge", labels)
        self._values: dict[tuple[str, ...], float] = {}

    def set(self, value: float, *labels: str) -> None:
        with self._lock:
            self._values[tuple(str(x) for x in labels)] = float(value)

    def replace(self, values: dict[tuple[str, ...], float]) -> None:
        with self._lock:
            self._values = dict(values)

    def render(self) -> list[str]:
        with self._lock:
            return [f"{self.name}{_labels(self.label_names, k)} {v}" for k, v in sorted(self._values.items())]


class Histogram(_Metric):
    def __init__(self, name: str, help_text: str, labels: Sequence[str] = ()) -> None:
        super().__init__(name, help_text, "histogram", labels)
        self._data: dict[tuple[str, ...], list[float]] = {}

    def observe(self, seconds: float, *labels: str) -> None:
        with self._lock:
            key = tuple(str(x) for x in labels)
            row = self._data.setdefault(key, [0.0] * (len(_BUCKETS) + 2))
            for i, bound in enumerate(_BUCKETS):
                if seconds <= bound:
                    row[i] += 1
            row[-2] += 1  # count
            row[-1] += seconds  # sum

    def render(self) -> list[str]:
        out: list[str] = []
        with self._lock:
            for key, row in sorted(self._data.items()):
                for i, bound in enumerate(_BUCKETS):
                    le = 'le="' + str(bound) + '"'
                    out.append(f"{self.name}_bucket{_labels(self.label_names, key, le)} {row[i]}")
                inf = 'le="+Inf"'
                out.append(f"{self.name}_bucket{_labels(self.label_names, key, inf)} {row[-2]}")
                out.append(f"{self.name}_count{_labels(self.label_names, key)} {row[-2]}")
                out.append(f"{self.name}_sum{_labels(self.label_names, key)} {row[-1]}")
        return out


ESB_REQUESTS = Counter("prodplan_esb_requests_total", "Panggilan HTTP ke ESB", ("method", "path_template", "status"))
ESB_SECONDS = Histogram("prodplan_esb_request_seconds", "Durasi panggilan ESB")
JOBS = Counter("prodplan_jobs_total", "Job selesai", ("kind", "status"))
JOB_SECONDS = Histogram("prodplan_job_seconds", "Durasi job", ("kind",))
OUTBOX_ROWS = Gauge("prodplan_outbox_rows", "Baris outbox per status", ("status",))
OUTBOX_OLDEST = Gauge("prodplan_outbox_oldest_pending_seconds", "Umur baris outbox pending/unknown tertua")
ACTOR_REJECTED = Counter("prodplan_actor_rejected_total", "Asersi ppa2 ditolak", ("reason",))
WRITE_ENABLED = Gauge("prodplan_write_enabled", "1 bila PRODPLAN_WRITE_ENABLED=true")
INFO = Gauge("prodplan_info", "Info konfigurasi", ("esb_env", "mfg_mode"))
# §30.17.13 (login per pengguna ESB)
LOGIN_TOTAL = Counter("prodplan_login_total", "Percobaan login ESB per hasil", ("result",))
SESSIONS_ACTIVE = Gauge("prodplan_sessions_active", "Sesi ESB hidup di memori")
SESSION_EVICTIONS = Counter("prodplan_session_evictions_total", "Sesi ESB dibuang per alasan", ("reason",))
ODOO_PROVISION = Counter("prodplan_odoo_provision_total", "Provisioning identitas ESB ke Odoo per hasil", ("result",))

ALL: tuple[_Metric, ...] = (
    ESB_REQUESTS,
    ESB_SECONDS,
    JOBS,
    JOB_SECONDS,
    OUTBOX_ROWS,
    OUTBOX_OLDEST,
    ACTOR_REJECTED,
    WRITE_ENABLED,
    INFO,
    LOGIN_TOTAL,
    SESSIONS_ACTIVE,
    SESSION_EVICTIONS,
    ODOO_PROVISION,
)


def render() -> str:
    lines: list[str] = []
    for metric in ALL:
        lines.append(f"# HELP {metric.name} {metric.help}")
        lines.append(f"# TYPE {metric.name} {metric.kind}")
        lines.extend(metric.render())
    return "\n".join(lines) + "\n"
