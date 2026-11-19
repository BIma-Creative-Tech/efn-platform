"""Format bundel migrasi `efn-mig-esb/1` (docs/04 §27.3, §27.3a, L-132). Murni: stdlib + `cryptography`.

Berkas `.efnmig` = baris header JSON + `\\n` -> nonce 12 byte -> AES-256-GCM(tar) dengan AAD = byte header (termasuk
`\\n`). Tar (tanpa kompresi, deterministik) berisi `manifest.json` + berkas entitas (`*.jsonl`, histori `*.jsonl.gz`)
+ tabel pemetaan (`maps/*.csv`). Kunci `migration_bundle_key` (hex >= 64 karakter, di-decode ke byte) ->
HKDF-SHA256 (salt kosong) info `efn:mig-esb:enc:v1` (kunci AES 32 byte) dan `efn:mig-esb:mac:v1` (kunci HMAC 32 byte).
Manifest ditandatangani HMAC-SHA256 atas canonical JSON tanpa `sig`. `sha256` per berkas = atas byte yang disimpan di
tar (terkompresi untuk `.gz`); `rows` = jumlah baris JSONL terdekompresi.

Pembaca memverifikasi header -> dekripsi (tag) -> struktur tar -> HMAC manifest -> run_id/brand/kind header =
manifest -> sha256 + rows setiap berkas (gzip dibatasi rasio <= 50x dan <= 512 MB terdekompresi) SEBELUM data
dipakai. Semua kegagalan
= `IntegrityError` (exit 3). Satu bundel dienkripsi sekaligus di memori: bundel histori dipecah per company x bulan
(§27.3a) agar muat di container 1 GB.

Hardening G43 (SECURITY_REVIEW_2026-11-18_g43):
- SEC-200: ukuran berkas dicek (`os.fstat`) SEBELUM dibaca; batas `EFN_MIG_MAX_BUNDLE_BYTES` (default 1 GiB).
- SEC-201: laporan validator ditandatangani kunci HKDF ketiga `efn:mig-esb:val:v1` (`sign_validation` /
  `verify_validation`), mengikat run_id + sha256 berkas bundel + sha256 manifest.
- SEC-202: semua tulisan lewat `write_secure` (O_CREAT|O_EXCL|O_NOFOLLOW, 0600, lalu rename atomik); pembacaan
  bundel O_NOFOLLOW.
"""

import gzip
import hashlib
import hmac
import io
import json
import os
import re
import stat
import tarfile
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

FORMAT = "efn-mig-esb/1"
KINDS = frozenset({"bundle", "result"})
NONCE_BYTES = 12
HEADER_MAX = 4096
ENC_INFO = b"efn:mig-esb:enc:v1"
MAC_INFO = b"efn:mig-esb:mac:v1"
VAL_INFO = b"efn:mig-esb:val:v1"
DEFAULT_MAX_BUNDLE_BYTES = 1024 * 1024 * 1024
MAX_BUNDLE_ENV = "EFN_MIG_MAX_BUNDLE_BYTES"
VALIDATION_FORMAT = "efn-mig-esb-validation/1"
GZIP_MAX_RATIO = 50
GZIP_MAX_BYTES = 512 * 1024 * 1024
# Rasio hanya diperiksa di atas ambang ini (berkas sangat kecil bisa berasio tinggi tanpa bahaya).
GZIP_RATIO_MIN_BYTES = 1024 * 1024
MANIFEST = "manifest.json"
_NAME = re.compile(r"^(maps/)?[a-z0-9_]+(\.jsonl|\.jsonl\.gz|\.json|\.csv)$")
_CHUNK = 1024 * 1024


class IntegrityError(Exception):
    """Bundel rusak, diubah, kunci salah, atau tidak konsisten (exit 3)."""


class BundleKeyError(ValueError):
    """Kunci bundel tidak valid (exit 2)."""


@dataclass(frozen=True)
class Keys:
    enc: bytes = field(repr=False)
    mac: bytes = field(repr=False)
    val: bytes = field(repr=False)


def derive_keys(secret: str) -> Keys:
    """`migration_bundle_key` (hex >= 64 karakter) -> kunci enkripsi, MAC manifest, dan MAC laporan validator
    terpisah (HKDF-SHA256, salt kosong)."""
    value = secret.strip()
    if len(value) < 64 or len(value) % 2 or not re.fullmatch(r"[0-9a-fA-F]+", value):
        raise BundleKeyError("migration_bundle_key wajib hex >= 64 karakter")
    ikm = bytes.fromhex(value)

    def _hkdf(info: bytes) -> bytes:
        return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(ikm)

    return Keys(enc=_hkdf(ENC_INFO), mac=_hkdf(MAC_INFO), val=_hkdf(VAL_INFO))


def max_bundle_bytes(env: Mapping[str, str] | None = None) -> int:
    """SEC-200: batas ukuran berkas bundel (byte) dari env; nilai tidak valid -> ValueError (konfigurasi)."""
    raw = ((os.environ if env is None else env).get(MAX_BUNDLE_ENV) or "").strip()
    if not raw:
        return DEFAULT_MAX_BUNDLE_BYTES
    if not raw.isdigit() or not 1024 <= int(raw) <= 8 * DEFAULT_MAX_BUNDLE_BYTES:
        raise ValueError(f"{MAX_BUNDLE_ENV} wajib bilangan bulat 1024..{8 * DEFAULT_MAX_BUNDLE_BYTES}")
    return int(raw)


_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_BINARY = getattr(os, "O_BINARY", 0)


def write_secure(path: Path, data: bytes) -> None:
    """SEC-202: tulis `<path>.part` dengan O_CREAT|O_EXCL|O_NOFOLLOW (0600) -> fsync -> rename atomik ke `path`.

    `.part` yang sudah ada (sisa run gagal / symlink tanaman) -> FileExistsError, tidak pernah diikuti/ditimpa.
    `os.replace` mengganti entri direktori `path` (bukan target symlink)."""
    tmp = path.with_name(path.name + ".part")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _BINARY, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)


def open_secure_write(path: Path) -> Any:
    """Berkas biner baru untuk ditulis streaming: O_CREAT|O_EXCL|O_NOFOLLOW, 0600 (SEC-202)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _BINARY, 0o600)
    return os.fdopen(fd, "wb")


def read_limited(path: Path, limit: int | None = None) -> bytes:
    """SEC-200/202: buka O_NOFOLLOW, cek ukuran via fstat SEBELUM membaca; > batas atau bukan berkas reguler ->
    IntegrityError."""
    cap = max_bundle_bytes() if limit is None else limit
    try:
        fd = os.open(path, os.O_RDONLY | _NOFOLLOW | _BINARY)
    except OSError as exc:
        raise IntegrityError(f"bundel tidak bisa dibuka: {path.name}") from exc
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise IntegrityError("bundel bukan berkas reguler")
        if info.st_size > cap:
            raise IntegrityError(f"bundel {info.st_size} byte melebihi batas {cap} ({MAX_BUNDLE_ENV})")
        data = handle.read(cap + 1)
    if len(data) > cap:
        raise IntegrityError("bundel melebihi batas ukuran")
    return data


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sign_manifest(manifest: Mapping[str, Any], keys: Keys) -> dict[str, Any]:
    body = {k: v for k, v in manifest.items() if k != "sig"}
    return {**body, "sig": hmac.new(keys.mac, canonical_json(body), hashlib.sha256).hexdigest()}


def verify_manifest(manifest: Mapping[str, Any], keys: Keys) -> None:
    sig = manifest.get("sig")
    if not isinstance(sig, str):
        raise IntegrityError("manifest tanpa sig")
    body = {k: v for k, v in manifest.items() if k != "sig"}
    expected = hmac.new(keys.mac, canonical_json(body), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise IntegrityError("HMAC manifest tidak cocok")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def gzip_bytes(data: bytes) -> bytes:
    """gzip deterministik (mtime 0, tanpa nama berkas)."""
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0, compresslevel=6) as gz:
        gz.write(data)
    return buf.getvalue()


def iter_gzip_chunks(data: bytes) -> Iterator[bytes]:
    """Dekompresi streaming dengan batas bom dekompresi (§27.3a) -> IntegrityError."""
    total = 0
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data), mode="rb") as gz:
            while True:
                chunk = gz.read(_CHUNK)
                if not chunk:
                    return
                total += len(chunk)
                if total > GZIP_MAX_BYTES:
                    raise IntegrityError("berkas gzip melebihi 512 MB terdekompresi")
                if total > GZIP_RATIO_MIN_BYTES and total > GZIP_MAX_RATIO * max(1, len(data)):
                    raise IntegrityError("rasio dekompresi gzip > 50x")
                yield chunk
    except (OSError, EOFError) as exc:
        raise IntegrityError("gzip rusak") from exc


def count_lines(name: str, data: bytes) -> int:
    chunks: Iterator[bytes] = iter_gzip_chunks(data) if name.endswith(".gz") else iter([data])
    count, last = 0, b"\n"
    for chunk in chunks:
        count += chunk.count(b"\n")
        last = chunk[-1:] or last
    if last != b"\n":
        count += 1  # baris terakhir tanpa newline
    return count


def iter_jsonl(name: str, data: bytes) -> Iterator[dict[str, Any]]:
    """Baris JSONL (gzip bila `.gz`) -> dict. Baris kosong diabaikan; NaN/Infinity ditolak."""
    chunks: Iterator[bytes] = iter_gzip_chunks(data) if name.endswith(".gz") else iter([data])
    pending = b""
    for chunk in chunks:
        pending += chunk
        *lines, pending = pending.split(b"\n")
        for line in lines:
            if line.strip():
                yield _parse_line(line)
    if pending.strip():
        yield _parse_line(pending)


def _reject_constant(value: str) -> Any:
    raise IntegrityError(f"nilai JSON tidak valid: {value}")


def _parse_line(line: bytes) -> dict[str, Any]:
    try:
        obj = json.loads(line.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntegrityError("baris JSONL rusak") from exc
    if not isinstance(obj, dict):
        raise IntegrityError("baris JSONL bukan objek")
    return obj


def jsonl_bytes(rows: Iterator[Mapping[str, Any]] | list[Mapping[str, Any]]) -> tuple[bytes, int]:
    """Objek -> JSONL kanonik (sort_keys, tanpa NaN). Kembalikan (byte, jumlah baris)."""
    buf = io.BytesIO()
    count = 0
    for row in rows:
        buf.write(canonical_json(row))
        buf.write(b"\n")
        count += 1
    return buf.getvalue(), count


def file_entry(name: str, entity: str, data: bytes, rows: int) -> dict[str, Any]:
    return {"name": name, "entity": entity, "rows": rows, "sha256": sha256_hex(data)}


def header_bytes(run_id: str, brand: str, kind: str) -> bytes:
    if kind not in KINDS:
        raise ValueError("kind tidak dikenal")
    return canonical_json({"format": FORMAT, "run_id": run_id, "brand": brand, "kind": kind}) + b"\n"


def _tar(members: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name, data in sorted(members):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 0
            info.mode = 0o600
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def build_bundle(manifest: Mapping[str, Any], members: Mapping[str, bytes], keys: Keys, kind: str = "bundle") -> bytes:
    """Manifest (belum ditandatangani) + berkas -> byte `.efnmig`. Konsistensi manifest vs berkas dicek di sini juga."""
    run_id, brand = str(manifest["run_id"]), str(manifest["brand"])
    signed = sign_manifest({**manifest, "format": FORMAT, "kind": kind}, keys)
    listed = {f["name"] for f in signed.get("files", [])} | {f["name"] for f in signed.get("maps", [])}
    if listed != set(members):
        raise ValueError("daftar berkas manifest tidak sama dengan isi bundel")
    for name in members:
        if not _NAME.fullmatch(name) or name == MANIFEST:
            raise ValueError(f"nama berkas bundel tidak valid: {name}")
    tar = _tar([(MANIFEST, canonical_json(signed)), *members.items()])
    header = header_bytes(run_id, brand, kind)
    nonce = os.urandom(NONCE_BYTES)
    return header + nonce + AESGCM(keys.enc).encrypt(nonce, tar, header)


def write_bundle(path: Path, manifest: Mapping[str, Any], members: Mapping[str, bytes], keys: Keys, kind: str) -> None:
    write_secure(path, build_bundle(manifest, members, keys, kind))


@dataclass
class Bundle:
    header: dict[str, Any]
    manifest: dict[str, Any]
    files: dict[str, bytes]
    manifest_bytes: bytes
    blob_sha256: str = ""  # sha256 berkas .efnmig utuh (terenkripsi) -- diikat laporan validator (SEC-201)

    @property
    def manifest_sha256(self) -> str:
        return sha256_hex(self.manifest_bytes)

    def rows(self, name: str) -> Iterator[dict[str, Any]]:
        return iter_jsonl(name, self.files[name]) if name in self.files else iter(())

    def entity_file(self, entity: str) -> str | None:
        for entry in self.manifest.get("files", []):
            if entry.get("entity") == entity:
                return str(entry["name"])
        return None


def parse_header(blob: bytes) -> tuple[dict[str, Any], bytes]:
    end = blob.find(b"\n", 0, HEADER_MAX)
    if end < 0:
        raise IntegrityError("header bundel tidak ditemukan")
    raw = blob[: end + 1]
    try:
        header = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntegrityError("header bundel rusak") from exc
    if not isinstance(header, dict) or header.get("format") != FORMAT or header.get("kind") not in KINDS:
        raise IntegrityError("format/kind header tidak dikenal")
    try:
        uuid.UUID(str(header.get("run_id")))
    except ValueError as exc:
        raise IntegrityError("run_id header tidak valid") from exc
    if canonical_json(header) + b"\n" != raw:
        raise IntegrityError("header bundel tidak kanonik")
    return header, raw


def open_bundle(blob: bytes, keys: Keys, expect_kind: str | None = None, limit: int | None = None) -> Bundle:
    cap = max_bundle_bytes() if limit is None else limit
    if len(blob) > cap:
        raise IntegrityError(f"bundel melebihi batas {cap} byte ({MAX_BUNDLE_ENV})")
    header, raw = parse_header(blob)
    if expect_kind and header["kind"] != expect_kind:
        raise IntegrityError(f"bundel kind={header['kind']}, diharapkan {expect_kind}")
    body = blob[len(raw) :]
    if len(body) < NONCE_BYTES + 16:
        raise IntegrityError("bundel terpotong")
    try:
        tar_bytes = AESGCM(keys.enc).decrypt(body[:NONCE_BYTES], body[NONCE_BYTES:], raw)
    except InvalidTag as exc:
        raise IntegrityError("dekripsi gagal (kunci salah atau bundel diubah)") from exc
    members = _untar(tar_bytes)
    manifest_bytes = members.pop(MANIFEST, None)
    if manifest_bytes is None:
        raise IntegrityError("manifest.json tidak ada")
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntegrityError("manifest rusak") from exc
    if not isinstance(manifest, dict):
        raise IntegrityError("manifest bukan objek")
    verify_manifest(manifest, keys)
    for key in ("format", "run_id", "brand", "kind"):
        if manifest.get(key) != header.get(key):
            raise IntegrityError(f"{key} manifest tidak sama dengan header")
    _verify_files(manifest, members)
    return Bundle(header, manifest, members, manifest_bytes, sha256_hex(blob))


def _untar(tar_bytes: bytes) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as tar:
            for member in tar.getmembers():
                if not member.isreg() or not (_NAME.fullmatch(member.name) or member.name == MANIFEST):
                    raise IntegrityError(f"anggota tar tidak diizinkan: {member.name!r}")
                if member.name in out:
                    raise IntegrityError(f"anggota tar ganda: {member.name}")
                handle = tar.extractfile(member)
                if handle is None:
                    raise IntegrityError("anggota tar tidak terbaca")
                out[member.name] = handle.read()
    except tarfile.TarError as exc:
        raise IntegrityError("tar rusak") from exc
    return out


def _verify_files(manifest: Mapping[str, Any], members: Mapping[str, bytes]) -> None:
    entries = list(manifest.get("files") or []) + list(manifest.get("maps") or [])
    names = [str(e.get("name")) for e in entries]
    if len(names) != len(set(names)) or set(names) != set(members):
        raise IntegrityError("daftar berkas manifest tidak sama dengan isi bundel")
    for entry in entries:
        name = str(entry["name"])
        data = members[name]
        if not hmac.compare_digest(str(entry.get("sha256")), sha256_hex(data)):
            raise IntegrityError(f"sha256 tidak cocok: {name}")
        if name.endswith((".jsonl", ".jsonl.gz", ".csv")) and "rows" in entry:
            rows = count_lines(name, data) - (1 if name.endswith(".csv") and data else 0)
            if rows != int(entry["rows"]):
                raise IntegrityError(f"jumlah baris tidak cocok: {name}")


def read_bundle(path: Path, keys: Keys, expect_kind: str | None = None, limit: int | None = None) -> Bundle:
    """SEC-200: ukuran dicek via fstat SEBELUM berkas dibaca ke memori."""
    cap = max_bundle_bytes() if limit is None else limit
    return open_bundle(read_limited(path, cap), keys, expect_kind, cap)


# ================================================================== laporan validator (SEC-201)
VALIDATION_BINDING = ("format", "run_id", "bundle_sha256", "manifest_sha256", "errors")


def sign_validation(report: Mapping[str, Any], keys: Keys) -> dict[str, Any]:
    """Laporan validator -> + `format` + `sig` = HMAC-SHA256(kunci val, canonical JSON tanpa `sig`).

    Laporan wajib memuat `run_id`, `bundle_sha256` (sha256 berkas .efnmig), `manifest_sha256`, `errors` (int)."""
    body = {k: v for k, v in report.items() if k != "sig"}
    body["format"] = VALIDATION_FORMAT
    for key in VALIDATION_BINDING:
        if key not in body:
            raise ValueError(f"laporan validator tanpa {key}")
    return {**body, "sig": hmac.new(keys.val, canonical_json(body), hashlib.sha256).hexdigest()}


def verify_validation(report: Mapping[str, Any], keys: Keys, bundle: Bundle) -> None:
    """Dipakai importer (gerbang §27.5-1): tanda tangan sah, mengikat bundel ini, dan `errors == 0`.

    Tanda tangan / pengikatan gagal -> IntegrityError (exit 3); errors > 0 -> ValueError (gerbang, exit 4)."""
    sig = report.get("sig")
    body = {k: v for k, v in report.items() if k != "sig"}
    if not isinstance(sig, str) or body.get("format") != VALIDATION_FORMAT:
        raise IntegrityError("laporan validator tanpa tanda tangan / format salah")
    expected = hmac.new(keys.val, canonical_json(body), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise IntegrityError("tanda tangan laporan validator tidak sah")
    if str(body.get("run_id")) != str(bundle.manifest.get("run_id")):
        raise IntegrityError("laporan validator untuk run_id lain")
    if not bundle.blob_sha256 or not hmac.compare_digest(str(body.get("bundle_sha256")), bundle.blob_sha256):
        raise IntegrityError("laporan validator untuk berkas bundel lain (bundle_sha256)")
    if not hmac.compare_digest(str(body.get("manifest_sha256")), bundle.manifest_sha256):
        raise IntegrityError("laporan validator untuk manifest lain")
    errors = body.get("errors")
    if not isinstance(errors, int) or isinstance(errors, bool) or errors != 0:
        raise ValueError("laporan validator memuat error (gerbang §27.5-1)")
