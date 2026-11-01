"""Loop consumer RabbitMQ (pika BlockingConnection).

- Ack manual, prefetch = `OCR_PREFETCH` (default 2), satu thread worker per slot prefetch.
- OCR/VLM bisa > 60 detik; pemrosesan berjalan di thread sehingga thread utama tetap memompa heartbeat
  (`process_data_events`). Ack/nack dikirim balik ke thread koneksi via `add_callback_threadsafe`.
- Reconnect otomatis; SIGTERM/SIGINT -> berhenti menerima pesan, tunggu pekerjaan berjalan, lalu tutup.
"""

import functools
import logging
import signal
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

import pika
import pika.exceptions
from pika.adapters.blocking_connection import BlockingChannel, BlockingConnection

from efn_ocr.config import Settings
from efn_ocr.delivery import Settle, handle_delivery
from efn_ocr.llm import build_vlm
from efn_ocr.messages import QUEUE_OCR_REQUESTED
from efn_ocr.ocr_engines import build_engine
from efn_ocr.processor import ReportProcessor
from efn_ocr.publisher import PikaPublisher, TopologyMissingError, build_connection_params, verify_topology
from efn_ocr.repository import PgRepository
from efn_ocr.storage import MinioImageStore
from efn_ocr.tls import client_ssl_context

log = logging.getLogger(__name__)
RECONNECT_DELAY_SECONDS = 5.0


def build_amqp_params(settings: Settings, connection_name: str) -> pika.ConnectionParameters:
    """Parameter koneksi RabbitMQ; konteks TLS dibangun di sini (CA rusak -> gagal start)."""
    context = client_ssl_context(settings.amqp_ca_file) if settings.amqp_ca_file else None
    return build_connection_params(settings.amqp_url, settings.amqp_password, connection_name, context)


def build_processor(settings: Settings, publisher: PikaPublisher) -> ReportProcessor:
    repo = PgRepository(
        host=settings.pg_host,
        port=settings.pg_port,
        user=settings.pg_user,
        password=settings.pg_password,
        dbname=settings.pg_database,
        sslmode=settings.pg_sslmode,
        sslrootcert=settings.pg_sslrootcert,
    )
    images = MinioImageStore(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
        ca_file=settings.minio_ca_file,
        key_prefix=settings.minio_key_prefix,
        max_bytes=settings.max_image_bytes,
    )
    vlm = build_vlm(settings)  # G37: gateway LLM (9router / Ollama / keduanya dengan fallback)
    ocr = build_engine(settings.ocr_engine, settings.tesseract_lang)
    return ReportProcessor(settings, repo, images, ocr, vlm, publisher)


def _settle(channel: BlockingChannel, delivery_tag: int, outcome: Settle) -> None:
    if not channel.is_open:
        log.warning("channel tertutup sebelum ack; pesan akan di-redeliver oleh broker")
        return
    if outcome is Settle.ACK:
        channel.basic_ack(delivery_tag=delivery_tag)
    elif outcome is Settle.REQUEUE:
        channel.basic_nack(delivery_tag=delivery_tag, requeue=True)
    else:
        channel.basic_nack(delivery_tag=delivery_tag, requeue=False)


class Consumer:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._params = build_amqp_params(settings, "ai-ocr-worker-consumer")
        self._publisher = PikaPublisher(build_amqp_params(settings, "ai-ocr-worker-publisher"), settings.amqp_user)
        self._processor = build_processor(settings, self._publisher)
        self._executor = ThreadPoolExecutor(max_workers=settings.prefetch, thread_name_prefix="ocr")
        self._stop = threading.Event()
        self._inflight: set[Future[None]] = set()
        self._lock = threading.Lock()

    def stop(self, *_args: Any) -> None:
        self._stop.set()

    def run(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        while not self._stop.is_set():
            try:
                connection = pika.BlockingConnection(self._params)
            except pika.exceptions.AMQPConnectionError as exc:
                log.warning("RabbitMQ belum siap (%s); coba lagi %.0f s", type(exc).__name__, RECONNECT_DELAY_SECONDS)
                self._stop.wait(RECONNECT_DELAY_SECONDS)
                continue
            try:
                self._consume(connection)
            except TopologyMissingError as exc:
                # Konfigurasi broker salah/belum lengkap: berhenti dengan pesan jelas (bukan reconnect tanpa akhir).
                log.error("%s", exc)
                self._executor.shutdown(wait=True)
                raise SystemExit(2) from exc
            except pika.exceptions.AMQPError as exc:
                # Koneksi putus / channel ditutup broker (selain queue hilang): reconnect.
                log.warning("koneksi/channel RabbitMQ ditutup (%s: %s); reconnect", type(exc).__name__, exc)
                self._stop.wait(RECONNECT_DELAY_SECONDS)
            finally:
                if connection.is_open:
                    connection.close()
        self._executor.shutdown(wait=True)
        log.info("consumer berhenti")

    def _consume(self, connection: BlockingConnection) -> None:
        channel = connection.channel()
        verify_topology(channel)  # passive: queue dideklarasikan broker (Gelombang 5b)
        channel.basic_qos(prefetch_count=self._settings.prefetch)
        on_message = functools.partial(self._on_message, connection)
        tag = channel.basic_consume(queue=QUEUE_OCR_REQUESTED, on_message_callback=on_message, auto_ack=False)
        log.info("consuming %s (prefetch=%d)", QUEUE_OCR_REQUESTED, self._settings.prefetch)
        while not self._stop.is_set():
            connection.process_data_events(time_limit=1)
        # Shutdown: berhenti menerima pesan baru, selesaikan yang sedang berjalan agar ack sempat terkirim.
        channel.basic_cancel(tag)
        while self._has_inflight():
            connection.process_data_events(time_limit=1)
        connection.process_data_events(time_limit=0)

    def _has_inflight(self) -> bool:
        with self._lock:
            return bool(self._inflight)

    def _on_message(
        self,
        connection: BlockingConnection,
        channel: BlockingChannel,
        method: pika.spec.Basic.Deliver,
        properties: pika.spec.BasicProperties,
        body: bytes,
    ) -> None:
        future = self._executor.submit(
            self._work,
            connection,
            channel,
            method.delivery_tag,
            bool(method.redelivered),
            properties.headers,
            properties.message_id,
            body,
        )
        with self._lock:
            self._inflight.add(future)
        future.add_done_callback(self._done)

    def _done(self, future: Future[None]) -> None:
        with self._lock:
            self._inflight.discard(future)

    def _work(
        self,
        connection: BlockingConnection,
        channel: BlockingChannel,
        delivery_tag: int,
        redelivered: bool,
        headers: dict[str, Any] | None,
        message_id: str | None,
        body: bytes,
    ) -> None:
        try:
            outcome = handle_delivery(
                self._processor,
                self._publisher,
                body,
                headers,
                message_id,
                redelivered,
                self._settings.max_retries,
            )
        except Exception:
            log.exception("error tak terduga di handler; pesan di-requeue")
            outcome = Settle.REQUEUE
        try:
            connection.add_callback_threadsafe(functools.partial(_settle, channel, delivery_tag, outcome))
        except pika.exceptions.AMQPError:
            log.warning("koneksi sudah tertutup; ack hilang, broker akan me-redeliver pesan")
