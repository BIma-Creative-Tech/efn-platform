"""Entry point: `python -m efn_ocr`."""

import logging
import os

from efn_ocr.config import Settings
from efn_ocr.consumer import Consumer
from efn_ocr.metrics import start_metrics_server


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # Library HTTP dapat mencatat URL/header di level DEBUG; tahan di WARNING.
    for noisy in ("urllib3", "pika"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # Gagal start (TlsConfigError) bila TLS tidak terkonfigurasi; tidak ada fallback ke plaintext.
    settings = Settings.from_env()
    start_metrics_server(
        settings.metrics_port,
        settings.metrics_cert_file,
        settings.metrics_key_file,
        settings.metrics_client_ca_file,
        addr=settings.metrics_bind_address,
    )
    log = logging.getLogger("efn_ocr")
    log.info(
        "ocr-worker mulai: engine=%s vlm=%s llm=%s model=%s/%s threshold=%.2f",
        settings.ocr_engine,
        settings.vlm_enabled,
        settings.llm_provider,
        settings.ninerouter_model if settings.llm_provider != "ollama" else "-",
        settings.ollama_model,
        settings.ocr_confidence_threshold,
    )
    if settings.amqp_ca_file is None or settings.pg_sslmode != "verify-full" or not settings.minio_secure:
        log.warning("MODE PLAINTEXT (EFN_ALLOW_PLAINTEXT=true): hanya untuk dev, jangan dipakai dengan data nyata")
    Consumer(settings).run()


if __name__ == "__main__":
    main()
