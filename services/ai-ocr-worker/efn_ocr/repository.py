"""Akses PostgreSQL `platform.price_hunter_report` (psycopg 3). Satu koneksi per thread worker.

Klaim idempotent sesuai kontrak: `queued -> processing` via `UPDATE ... WHERE status='queued' RETURNING`.
Tambahan: laporan `processing` boleh diklaim ulang bila pesan adalah redelivery/retry atau klaim lama sudah
basi (worker crash), agar laporan tidak macet.

Least privilege (docs/04 §7.3, user `efn_ocr`): query di file ini HANYA menulis kolom
`status, reason, extracted, confidence, image_phash, updated_at` dan HANYA membaca kolom non-PII
(`id, user_hash, product_name, reported_price, competitor_name, geo_lat, geo_lng, image_object, image_phash,
status, created_at, updated_at`). Kolom `*_enc` (mis. `user_enc`) tidak pernah dibaca. Koneksi TLS verify-full.
Test `tests/test_repository_sql.py` mengunci aturan ini.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

log = logging.getLogger(__name__)

SQL_CLAIM = """
UPDATE platform.price_hunter_report
SET status = 'processing', updated_at = now()
WHERE id = %(id)s::uuid
  AND (
    status = 'queued'
    OR (
      status = 'processing'
      AND (%(reclaim)s OR updated_at < now() - make_interval(secs => %(stale)s))
    )
  )
RETURNING id::text AS id, user_hash, product_name, reported_price, competitor_name, geo_lat, geo_lng, image_object
"""

SQL_STATUS = "SELECT status FROM platform.price_hunter_report WHERE id = %(id)s::uuid"

# Jarak Hamming dihitung di PostgreSQL (>= 14: bit_count). CASE menjamin cast hanya untuk nilai hex valid.
SQL_FIND_DUPLICATE = """
SELECT id::text AS id
FROM platform.price_hunter_report
WHERE id <> %(id)s::uuid
  AND created_at >= now() - make_interval(days => %(days)s)
  AND image_phash IS NOT NULL
  AND CASE
        WHEN image_phash ~ '^[0-9a-f]{16}$'
        THEN bit_count(('x' || image_phash)::bit(64) # ('x' || %(phash)s)::bit(64)) <= %(max_distance)s
        ELSE false
      END
ORDER BY created_at DESC
LIMIT 1
"""

SQL_COUNT_VALID_TODAY = """
SELECT count(*) AS n
FROM platform.price_hunter_report
WHERE user_hash = %(user_hash)s
  AND id <> %(id)s::uuid
  AND status = 'valid'
  AND created_at >= (date_trunc('day', now() AT TIME ZONE 'Asia/Jakarta') AT TIME ZONE 'Asia/Jakarta')
"""

SQL_FINALIZE = """
UPDATE platform.price_hunter_report
SET status = %(status)s,
    reason = %(reason)s,
    extracted = %(extracted)s,
    confidence = %(confidence)s,
    image_phash = COALESCE(%(phash)s, image_phash),
    updated_at = now()
WHERE id = %(id)s::uuid AND status = 'processing'
"""

SQL_RELEASE = """
UPDATE platform.price_hunter_report
SET status = 'queued', updated_at = now()
WHERE id = %(id)s::uuid AND status = 'processing'
"""


@dataclass(frozen=True)
class ReportRow:
    report_id: str
    user_hash: str
    product_name: str
    reported_price: int
    competitor_name: str | None
    geo_lat: float | None
    geo_lng: float | None
    image_object: str


def _to_rupiah(value: Any) -> int:
    return int(Decimal(str(value)).to_integral_value(rounding=ROUND_HALF_UP))


class PgRepository:
    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        dbname: str,
        sslmode: str,
        sslrootcert: str | None,
    ) -> None:
        # sslmode/sslrootcert sudah divalidasi Settings.from_env (verify-full kecuali EFN_ALLOW_PLAINTEXT di dev).
        # make_conninfo mengabaikan argumen bernilai None.
        self._conninfo = make_conninfo(
            host=host,
            port=port,
            user=user,
            password=password,
            dbname=dbname,
            sslmode=sslmode,
            sslrootcert=sslrootcert,
            application_name="ai-ocr-worker",
            connect_timeout=10,
        )
        self._local = threading.local()

    def _conn(self) -> psycopg.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None or conn.closed or conn.broken:
            conn = psycopg.connect(self._conninfo, autocommit=True, row_factory=dict_row)
            self._local.conn = conn
        return conn

    def _reset(self) -> None:
        conn = getattr(self._local, "conn", None)
        self._local.conn = None
        if conn is not None and not conn.closed:
            conn.close()

    def _fetchone(self, sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
        try:
            return self._conn().execute(sql, params).fetchone()
        except psycopg.OperationalError:
            self._reset()
            raise

    def claim(self, report_id: str, allow_reclaim: bool, stale_seconds: int) -> ReportRow | None:
        row = self._fetchone(SQL_CLAIM, {"id": report_id, "reclaim": allow_reclaim, "stale": float(stale_seconds)})
        if row is None:
            return None
        return ReportRow(
            report_id=row["id"],
            user_hash=row["user_hash"],
            product_name=row["product_name"],
            reported_price=_to_rupiah(row["reported_price"]),
            competitor_name=row["competitor_name"],
            geo_lat=row["geo_lat"],
            geo_lng=row["geo_lng"],
            image_object=row["image_object"],
        )

    def current_status(self, report_id: str) -> str | None:
        row = self._fetchone(SQL_STATUS, {"id": report_id})
        return None if row is None else str(row["status"])

    def find_duplicate(self, report_id: str, phash: str, max_distance: int, lookback_days: int) -> str | None:
        params = {"id": report_id, "phash": phash, "max_distance": max_distance, "days": lookback_days}
        row = self._fetchone(SQL_FIND_DUPLICATE, params)
        return None if row is None else str(row["id"])

    def count_valid_today(self, user_hash: str, report_id: str) -> int:
        row = self._fetchone(SQL_COUNT_VALID_TODAY, {"user_hash": user_hash, "id": report_id})
        return 0 if row is None else int(row["n"])

    def finalize(
        self,
        report_id: str,
        status: str,
        reason: str,
        extracted: dict[str, Any],
        confidence: float | None,
        phash: str | None,
        before_commit: Callable[[], None],
    ) -> bool:
        """UPDATE hasil + publish event (`before_commit`) dalam satu transaksi.

        Publish terjadi sebelum COMMIT: bila publish gagal, UPDATE di-rollback dan pesan di-retry. Bila COMMIT
        gagal setelah publish, retry mem-publish ulang dengan idempotency key yang sama (BFF men-dedup).
        """
        params = {
            "id": report_id,
            "status": status,
            "reason": reason,
            "extracted": Jsonb(extracted),
            "confidence": confidence,
            "phash": phash,
        }
        conn = self._conn()
        try:
            with conn.transaction():
                cur = conn.execute(SQL_FINALIZE, params)
                if cur.rowcount != 1:
                    return False
                before_commit()
        except psycopg.OperationalError:
            self._reset()
            raise
        return True

    def release(self, report_id: str) -> None:
        """Kembalikan ke `queued` sebelum retry agar klaim berikutnya berhasil."""
        try:
            self._conn().execute(SQL_RELEASE, {"id": report_id})
        except psycopg.OperationalError:
            self._reset()
            raise
