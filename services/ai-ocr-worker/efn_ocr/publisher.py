"""Koneksi & publish RabbitMQ dari thread worker.

- TLS: `AMQP_URL=amqps://efn-ocr@rabbitmq:5671` + CA internal (`AMQP_TLS_CA_FILE`), hostname diverifikasi
  (SNI/`server_hostname` = host di URL). `amqp://` hanya bila Settings mengizinkan plaintext (dev).
- SEC-08: setiap pesan membawa properti AMQP `user_id` = user koneksi (`efn-ocr`); RabbitMQ menolak pesan bila
  nilainya tidak sama dengan user yang login, sehingga BFF bisa memercayai asal pesan.
- Channel pika tidak thread-safe, jadi publish memakai koneksi singkat terpisah per batch (dibuka-tutup per laporan)
  dengan publisher confirms + `mandatory`. Biaya koneksi kecil dibanding waktu OCR/VLM.
"""

import ssl
from collections.abc import Sequence
from urllib.parse import unquote, urlsplit

import pika
import pika.exceptions
from pika.adapters.blocking_connection import BlockingChannel

from efn_ocr.messages import QUEUE_OCR_REQUESTED, OutgoingMessage, dlq_name


def build_connection_params(
    url: str,
    password: str,
    connection_name: str,
    ssl_context: ssl.SSLContext | None,
) -> pika.ConnectionParameters:
    """`AMQP_URL` tanpa password (amqps://user@host:port/vhost) + password dari Docker secret.

    `amqps` wajib disertai `ssl_context` (CA internal); `amqp` hanya bila `ssl_context` None (mode dev eksplisit).
    """
    parts = urlsplit(url)
    if parts.scheme == "amqps":
        if ssl_context is None:
            raise ValueError("amqps:// membutuhkan SSL context (AMQP_TLS_CA_FILE)")
        host = parts.hostname or "localhost"
        ssl_options: pika.SSLOptions | None = pika.SSLOptions(ssl_context, server_hostname=host)
        default_port = 5671
    elif parts.scheme == "amqp":
        if ssl_context is not None:
            raise ValueError("SSL context diberikan tetapi URL berskema amqp://")
        host = parts.hostname or "localhost"
        ssl_options = None
        default_port = 5672
    else:
        raise ValueError("AMQP_URL harus berskema amqps:// (atau amqp:// di dev)")
    if parts.password:
        raise ValueError("AMQP_URL tidak boleh memuat password")
    vhost = unquote(parts.path[1:]) if len(parts.path) > 1 else "/"
    credentials = pika.PlainCredentials(unquote(parts.username or ""), password)
    return pika.ConnectionParameters(
        host=host,
        port=parts.port or default_port,
        virtual_host=vhost,
        credentials=credentials,
        ssl_options=ssl_options,
        heartbeat=60,
        blocked_connection_timeout=300,
        client_properties={"connection_name": connection_name},
    )


class TopologyMissingError(RuntimeError):
    """Queue yang wajib dideklarasikan broker (definitions) belum ada. Worker berhenti, tidak membuat queue sendiri."""


# Gelombang 5b: topologi dideklarasikan broker (infra/rabbitmq definitions, devsecops). Worker hanya memeriksa
# keberadaan queue yang DIKONSUMSI (passive declare); queue publish (loyalty.earn.requested, notify.push) tidak
# disentuh — publish memakai `mandatory=True`, sehingga queue yang hilang terdeteksi sebagai UnroutableError.
REQUIRED_CONSUME_QUEUES = (dlq_name(QUEUE_OCR_REQUESTED), QUEUE_OCR_REQUESTED)


def verify_topology(channel: BlockingChannel, queues: Sequence[str] = REQUIRED_CONSUME_QUEUES) -> None:
    """`queue_declare(passive=True)`: hanya cek ada; tidak membuat/mengubah queue (tanpa izin `configure`).

    Queue tidak ada -> broker menutup channel dengan 404 -> `TopologyMissingError` (pesan jelas, gagal start).
    """
    for queue in queues:
        try:
            channel.queue_declare(queue=queue, passive=True)
        except pika.exceptions.ChannelClosedByBroker as exc:
            if exc.reply_code == 404:
                raise TopologyMissingError(
                    f"queue RabbitMQ '{queue}' belum ada; topologi wajib dideklarasikan broker "
                    "(infra/rabbitmq definitions). Worker tidak membuat queue sendiri."
                ) from exc
            raise


def message_properties(message: OutgoingMessage, user_id: str) -> pika.BasicProperties:
    """Properti AMQP sesuai kontrak §4 + `user_id` (SEC-08)."""
    return pika.BasicProperties(
        content_type="application/json",
        delivery_mode=2,
        message_id=message.message_id,
        user_id=user_id,
        headers=dict(message.headers) or None,
    )


class PikaPublisher:
    def __init__(self, params: pika.ConnectionParameters, user_id: str) -> None:
        if not user_id:
            raise ValueError("user_id publisher wajib (nama user RabbitMQ)")
        self._params = params
        self._user_id = user_id

    def publish(self, messages: Sequence[OutgoingMessage]) -> None:
        if not messages:
            return
        connection = pika.BlockingConnection(self._params)
        try:
            channel = connection.channel()
            channel.confirm_delivery()
            for message in messages:
                # Dengan confirm_delivery, kegagalan memunculkan UnroutableError/NackError.
                channel.basic_publish(
                    exchange="",
                    routing_key=message.queue,
                    body=message.body,
                    properties=message_properties(message, self._user_id),
                    mandatory=True,
                )
        finally:
            if connection.is_open:
                connection.close()
