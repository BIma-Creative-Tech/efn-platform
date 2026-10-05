"""Job `privacy_erasure`: penghapusan data subjek di ClickHouse (UU PDP; docs/04 §8.4). Logika murni + protokol I/O.

Alur per siklus ETL (dijalankan SEBELUM job incremental):
1. Baca `platform.dsar_request` (kolom `id, member_hash, status, stores_pending` — grant kolom untuk `efn_etl`)
   dengan `status = 'processing'` dan `'clickhouse' = ANY(stores_pending)`.
2. Untuk setiap tabel mart yang memuat hash member (`ERASURE_TARGETS`): bila ada baris cocok, jalankan
   `ALTER TABLE ... DELETE WHERE <kolom> = '<hash>'` (mutation; menulis ulang part sehingga data hilang secara fisik —
   lightweight `DELETE FROM` hanya menandai baris dan baru dibuang saat merge, kurang kuat untuk kewajiban hapus).
3. Verifikasi `count() = 0` di semua tabel. Baru setelah itu publish `privacy.erasure.completed`
   `{schema_version: 2, idempotency_key: "erasure:<request_id>:clickhouse", request_id, store: "clickhouse"}`
   (properti AMQP `message_id` = idempotency key, `user_id` = `efn-etl`).
4. Hash yang sedang diproses dikembalikan sebagai `excluded_hashes` agar job incremental pada siklus yang sama
   tidak memuat ulang baris subjek dari overlap window (lihat runner.run_job).

Idempoten: kegagalan di tengah (mutation timeout, publish gagal) = request tetap `processing` dan diulang siklus
berikutnya; DELETE ulang tanpa baris tidak dijalankan (cek count dulu), publish ulang memakai idempotency key sama
(BFF dedup via `platform.processed_message`).

`fact_sales_line.customer_id` (id `res.partner` Odoo) tidak dihapus di sini: setelah Odoo menganonimkan partner
(store `odoo`), id itu tidak lagi bisa dihubungkan ke orang. Lihat laporan agent (keputusan DPO).
"""

import json
import logging
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2
STORE = "clickhouse"
QUEUE_ERASURE_COMPLETED = "privacy.erasure.completed"
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

# (tabel mart, kolom hash member). Tambah baris di sini bila tabel mart baru memuat hash member.
ERASURE_TARGETS: tuple[tuple[str, str], ...] = (
    ("competitor_prices", "user_hash"),
    ("loyalty_events", "member_hash"),
)

# Sumber: platform.dsar_request (milik BFF). Hanya kolom yang di-grant ke efn_etl.
SQL_PENDING_ERASURES = """
SELECT id::text AS request_id, member_hash
FROM platform.dsar_request
WHERE status = 'processing'
  AND 'clickhouse' = ANY(stores_pending)
ORDER BY id
LIMIT %(limit)s
"""


@dataclass(frozen=True)
class ErasureRequest:
    request_id: str
    member_hash: str


@dataclass(frozen=True)
class ErasureResult:
    request_id: str
    status: str  # completed | pending | error
    deleted: dict[str, int]
    detail: str = ""


@dataclass(frozen=True)
class OutgoingMessage:
    queue: str
    body: bytes
    message_id: str


class ErasureStore(Protocol):
    def count(self, table: str, column: str, member_hash: str) -> int: ...

    def delete(self, table: str, column: str, member_hash: str) -> None: ...


class Publisher(Protocol):
    def publish(self, messages: Sequence[OutgoingMessage]) -> None: ...


def require_hash(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    if not HASH_RE.fullmatch(text):
        raise ValueError("member_hash bukan hex sha256 64 karakter")
    return text


def parse_request(row: dict[str, Any]) -> ErasureRequest:
    """Validasi baris dsar_request. Nilai tidak valid -> ValueError (baris dilewati & dicatat, bukan crash)."""
    try:
        request_id = str(uuid.UUID(str(row.get("request_id"))))
    except ValueError as exc:
        raise ValueError("request_id bukan UUID") from exc
    return ErasureRequest(request_id=request_id, member_hash=require_hash(row.get("member_hash")))


def parse_requests(rows: Sequence[dict[str, Any]]) -> list[ErasureRequest]:
    requests: list[ErasureRequest] = []
    for row in rows:
        try:
            requests.append(parse_request(row))
        except ValueError as exc:
            log.error("dsar_request tidak valid dilewati: %s", exc)
    return requests


def _ident(name: str) -> str:
    if not _IDENT_RE.fullmatch(name):
        raise ValueError(f"identifier tidak valid: {name!r}")
    return name


def _target(database: str, table: str, column: str) -> tuple[str, str]:
    if (table, column) not in ERASURE_TARGETS:
        raise ValueError(f"{table}.{column} bukan target erasure")
    return f"{_ident(database)}.{_ident(table)}", _ident(column)


def count_sql(database: str, table: str, column: str, member_hash: str) -> str:
    """Literal hash aman: divalidasi ketat `^[0-9a-f]{64}$` (tanpa kutip/escape). Parameter server-side tidak
    dipakai karena dukungan query parameter pada mutation ALTER belum terverifikasi di 24.8."""
    fq_table, col = _target(database, table, column)
    return f"SELECT count() FROM {fq_table} WHERE {col} = '{require_hash(member_hash)}'"  # noqa: S608 - tervalidasi


def delete_sql(database: str, table: str, column: str, member_hash: str) -> str:
    fq_table, col = _target(database, table, column)
    return f"ALTER TABLE {fq_table} DELETE WHERE {col} = '{require_hash(member_hash)}'"  # noqa: S608 - tervalidasi


def idempotency_key(request_id: str) -> str:
    return f"erasure:{request_id}:{STORE}"


def completed_message(request_id: str) -> OutgoingMessage:
    rid = str(uuid.UUID(request_id))
    key = idempotency_key(rid)
    payload = {"schema_version": SCHEMA_VERSION, "idempotency_key": key, "request_id": rid, "store": STORE}
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return OutgoingMessage(queue=QUEUE_ERASURE_COMPLETED, body=body, message_id=key)


def erase_one(request: ErasureRequest, store: ErasureStore, publisher: Publisher) -> ErasureResult:
    deleted: dict[str, int] = {}
    for table, column in ERASURE_TARGETS:
        found = store.count(table, column, request.member_hash)
        if found > 0:
            store.delete(table, column, request.member_hash)
        deleted[table] = found
    remaining = {table: store.count(table, column, request.member_hash) for table, column in ERASURE_TARGETS}
    leftover = {table: n for table, n in remaining.items() if n > 0}
    if leftover:
        # Mutation belum selesai/ gagal: JANGAN lapor selesai. Diulang siklus berikutnya.
        return ErasureResult(request.request_id, "pending", deleted, f"sisa baris: {leftover}")
    publisher.publish([completed_message(request.request_id)])
    return ErasureResult(request.request_id, "completed", deleted)


def run_erasure(
    requests: Sequence[ErasureRequest],
    store: ErasureStore,
    publisher: Publisher,
) -> list[ErasureResult]:
    """Proses semua request; kegagalan satu request tidak menghentikan yang lain."""
    results: list[ErasureResult] = []
    for request in requests:
        try:
            result = erase_one(request, store, publisher)
        except Exception as exc:  # I/O (ClickHouse/RabbitMQ): dicatat, diulang siklus berikutnya
            log.exception("erasure request=%s gagal", request.request_id)
            result = ErasureResult(request.request_id, "error", {}, type(exc).__name__)
        log.info(
            "erasure request=%s status=%s deleted=%s %s",
            result.request_id,
            result.status,
            result.deleted,
            result.detail,
        )
        results.append(result)
    return results


def excluded_hashes(requests: Sequence[ErasureRequest]) -> frozenset[str]:
    return frozenset(r.member_hash for r in requests)
