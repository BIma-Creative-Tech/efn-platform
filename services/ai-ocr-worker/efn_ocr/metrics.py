"""Metrik Prometheus (scrape `https://ocr-worker:9100/metrics`).

TLS: `prometheus_client.start_http_server(..., certfile=, keyfile=, client_cafile=, client_auth_required=)` tersedia
sejak prometheus-client 0.17.0 (dipin 0.21.1). Sertifikat dari `METRICS_TLS_CERT_FILE`/`METRICS_TLS_KEY_FILE`
(secret `tls_ocr_worker_crt/key`).

mTLS (docs/04 §8.5, SEC-20): bila `METRICS_TLS_CLIENT_CA_FILE` di-set (wajib di production), server meminta dan
memverifikasi sertifikat klien terhadap CA internal (`client_auth_required=True`). Batasan: prometheus-client tidak
bisa membatasi CN/SAN klien — setiap sertifikat dari CA internal diterima (lihat laporan agent).
Tanpa sertifikat hanya boleh di mode plaintext dev (divalidasi Settings.from_env).
"""

from typing import Any

from prometheus_client import Counter, Histogram, start_http_server

REPORTS = Counter(
    "efn_ocr_reports",
    "Laporan Price Hunter yang selesai diproses, per status akhir (valid|rejected|flagged).",
    ["status"],
)
PROCESSED = Counter("efn_ocr_processed", "Total laporan yang selesai diproses (semua status akhir).")
DELIVERIES = Counter(
    "efn_ocr_deliveries",
    "Hasil penanganan pesan ocr.requested (ack|skipped|retry|dead_letter|requeue).",
    ["action"],
)
VLM_CALLS = Counter("efn_ocr_vlm_calls", "Pemanggilan VLM Ollama per hasil (ok|unavailable|disabled).", ["outcome"])
LATENCY = Histogram(
    "efn_ocr_processing_seconds",
    "Durasi pemrosesan satu laporan dari klaim sampai commit.",
    buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 90, 120, 300),
)


def metrics_server_kwargs(certfile: str | None, keyfile: str | None, client_cafile: str | None) -> dict[str, Any]:
    """Argumen TLS untuk `start_http_server`. Logika murni (diuji tanpa membuka port)."""
    if not (certfile and keyfile):
        if client_cafile:
            raise ValueError("client_cafile membutuhkan certfile/keyfile")
        return {}
    kwargs: dict[str, Any] = {"certfile": certfile, "keyfile": keyfile}
    if client_cafile:
        kwargs.update(client_cafile=client_cafile, client_auth_required=True)
    return kwargs


def start_metrics_server(
    port: int,
    certfile: str | None,
    keyfile: str | None,
    client_cafile: str | None = None,
    addr: str = "127.0.0.1",
) -> None:
    start_http_server(port, addr=addr, **metrics_server_kwargs(certfile, keyfile, client_cafile))
