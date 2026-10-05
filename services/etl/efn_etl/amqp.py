"""Publisher RabbitMQ ETL (hanya `privacy.erasure.completed`, docs/04 §4 & §8.4). Disalin ringkas dari ai-ocr-worker.

- TLS: `AMQP_URL=amqps://efn-etl@rabbitmq:5671` + CA internal (`AMQP_TLS_CA_FILE`), hostname diverifikasi.
- SEC-08: properti AMQP `user_id` = user koneksi (`efn-etl`); RabbitMQ menolak pesan bila tidak cocok.
- ETL TIDAK mendeklarasikan queue (tanpa izin `configure`; queue + DLQ dideklarasikan BROKER lewat definitions
  infra/rabbitmq/rabbitmq-entrypoint.sh, docs/04 §8.10). Publish
  memakai default exchange + `mandatory` + publisher confirms: queue belum ada -> UnroutableError -> diulang siklus
  berikutnya (request tetap `processing`).
"""

import ssl
from collections.abc import Sequence
from urllib.parse import unquote, urlsplit

import pika

from efn_etl.erasure import OutgoingMessage


def build_connection_params(
    url: str,
    password: str,
    connection_name: str,
    ssl_context: ssl.SSLContext | None,
) -> pika.ConnectionParameters:
    """`AMQP_URL` tanpa password + password dari Docker secret. `amqps` wajib `ssl_context` (CA internal)."""
    parts = urlsplit(url)
    host = parts.hostname or "localhost"
    if parts.scheme == "amqps":
        if ssl_context is None:
            raise ValueError("amqps:// membutuhkan SSL context (AMQP_TLS_CA_FILE)")
        ssl_options: pika.SSLOptions | None = pika.SSLOptions(ssl_context, server_hostname=host)
        default_port = 5671
    elif parts.scheme == "amqp":
        if ssl_context is not None:
            raise ValueError("SSL context diberikan tetapi URL berskema amqp://")
        ssl_options = None
        default_port = 5672
    else:
        raise ValueError("AMQP_URL harus berskema amqps:// (atau amqp:// di dev)")
    if parts.password:
        raise ValueError("AMQP_URL tidak boleh memuat password")
    vhost = unquote(parts.path[1:]) if len(parts.path) > 1 else "/"
    return pika.ConnectionParameters(
        host=host,
        port=parts.port or default_port,
        virtual_host=vhost,
        credentials=pika.PlainCredentials(unquote(parts.username or ""), password),
        ssl_options=ssl_options,
        heartbeat=60,
        blocked_connection_timeout=120,
        connection_attempts=2,
        retry_delay=5,
        client_properties={"connection_name": connection_name},
    )


def message_properties(message: OutgoingMessage, user_id: str) -> pika.BasicProperties:
    return pika.BasicProperties(
        content_type="application/json",
        delivery_mode=2,
        message_id=message.message_id,
        user_id=user_id,
    )


class PikaPublisher:
    """Koneksi singkat per batch (ETL publish jarang: beberapa pesan per siklus 15 menit)."""

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
