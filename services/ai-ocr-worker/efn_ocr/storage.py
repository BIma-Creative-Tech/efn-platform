"""Unduh foto laporan dari MinIO. Hanya bucket & prefix yang dikonfigurasi (key berasal dari DB, bukan pesan).

TLS (docs/04 §7.1): `secure=True` + `http_client` urllib3 yang memverifikasi sertifikat server terhadap CA internal
(`MINIO_CA_FILE`) dan hostname `minio`. Kredensial service account `efn-ocr` (GetObject `market-scout/reports/*`).
Objek di bucket terenkripsi SSE-S3 di sisi MinIO; klien tidak perlu header SSE untuk GET.
"""

import urllib3
from minio import Minio
from minio.error import S3Error

from efn_ocr.errors import ImageNotFoundError, UnreadableImageError

_MISSING_CODES = {"NoSuchKey", "NoSuchBucket", "NoSuchObject"}


def build_http_client(ca_file: str | None) -> urllib3.PoolManager:
    """PoolManager urllib3. `ca_file` None hanya untuk mode plaintext dev (secure=False)."""
    timeout = urllib3.Timeout(connect=10.0, read=60.0)
    retries = urllib3.Retry(total=3, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504])
    if ca_file is None:
        return urllib3.PoolManager(timeout=timeout, retries=retries, maxsize=4)
    return urllib3.PoolManager(
        timeout=timeout,
        retries=retries,
        maxsize=4,
        cert_reqs="CERT_REQUIRED",
        ca_certs=ca_file,
    )


class MinioImageStore:
    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        secure: bool,
        ca_file: str | None,
        key_prefix: str,
        max_bytes: int,
    ) -> None:
        if secure and not ca_file:
            raise ValueError("MinIO TLS membutuhkan MINIO_CA_FILE")
        self._client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
            http_client=build_http_client(ca_file if secure else None),
        )
        self._prefix = key_prefix
        self._max_bytes = max_bytes

    def fetch(self, bucket: str, key: str) -> bytes:
        if not key or not key.startswith(self._prefix) or ".." in key:
            raise ImageNotFoundError("key di luar prefix yang diizinkan")
        try:
            response = self._client.get_object(bucket, key)
        except S3Error as exc:
            if exc.code in _MISSING_CODES:
                raise ImageNotFoundError(exc.code) from exc
            raise
        try:
            data = response.read(self._max_bytes + 1)
        finally:
            response.close()
            response.release_conn()
        if len(data) > self._max_bytes:
            raise UnreadableImageError("too_large")
        return data
