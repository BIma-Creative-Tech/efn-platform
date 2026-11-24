"""Verifikasi header asersi `X-EFN-Actor: ppa2.<payload>.<sig>` (docs/04 §30.17.7, menggantikan `ppa1` §30.7).

`ppa2` hanya mengikat request ke sesi Produksi (`sid` = cookie `__Host-epp`); identitas, peran, brand, dan cabang
diambil dari sesi memori prodplan, BUKAN dari klaim insight-web. `ppa1` ditolak (regex gagal -> `actor_invalid`).

Urutan verifikasi persis kontrak: regex -> HMAC (constant-time) -> kunci tepat (8, berurutan) -> aud -> waktu ->
m/p -> bh -> jti -> tipe klaim. Semua kegagalan = `ActorInvalid` (API membalas 401 `actor_invalid` tanpa detail);
`reason` hanya untuk metrik `prodplan_actor_rejected_total{reason}`. Aturan `sid` kosong + sesi tak dikenal ditegakkan
di `api.App` (butuh tabel route + penyimpanan sesi).
"""

import base64
import hashlib
import hmac
import json
import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

PREFIX = "ppa2"
HEADER_RE = re.compile(r"^ppa2\.[A-Za-z0-9_-]{16,1800}\.[A-Za-z0-9_-]{43}$")
SID_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
MAX_HEADER_BYTES = 2048
CLAIM_KEYS = ("aud", "bh", "exp", "iat", "jti", "m", "p", "sid")
AUDIENCE = "prodplan"
TTL_SECONDS = 60
CLOCK_SKEW_SECONDS = 30
JTI_TTL_SECONDS = 120
JTI_CACHE_MAX = 50_000
PP_ROLES = ("pp_planner", "pp_warehouse")
_B64_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class ActorInvalid(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__("actor_invalid")
        self.reason = reason


@dataclass(frozen=True)
class Actor:
    """Identitas pemanggil dari SESI prodplan (§30.17.4). `sub` = `prodplan.actor.id`. Tanpa token/username."""

    sub: int
    name: str = field(repr=False)
    roles: frozenset[str] = frozenset()
    brand: str = ""
    branch_ids: frozenset[int] = frozenset()
    doc_insert: frozenset[str] = frozenset()
    doc_delete: frozenset[str] = frozenset()
    branch_names: Mapping[int, str] = field(default_factory=dict, repr=False, compare=False, hash=False)
    sid_hash: str = field(default="", repr=False)

    def has(self, role: str) -> bool:
        return role in self.roles


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    if not _B64_RE.fullmatch(text):
        raise ValueError("bukan base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def body_hash(body: bytes) -> str:
    return b64url_encode(hashlib.sha256(body).digest())


def sid_hash(sid: str) -> str:
    """Kunci penyimpanan sesi = hex SHA-256 dari sid (sid mentah tidak pernah disimpan)."""
    return hashlib.sha256(sid.encode("ascii")).hexdigest()


def sign(payload_b64: str, key: bytes) -> str:
    return b64url_encode(hmac.new(key, (PREFIX + "." + payload_b64).encode("ascii"), hashlib.sha256).digest())


def canonical_payload(claims: Mapping[str, Any]) -> str:
    """JSON tanpa spasi, tepat 8 kunci berurutan `CLAIM_KEYS`, UTF-8 apa adanya (insight-web: `JSON.stringify`)."""
    ordered = {k: claims[k] for k in CLAIM_KEYS}
    return json.dumps(ordered, separators=(",", ":"), ensure_ascii=False)


def make_header(claims: Mapping[str, Any], key: bytes) -> str:
    """Pembuat header (dipakai test; insight-web punya implementasi TypeScript yang byte-exact sama)."""
    payload = b64url_encode(canonical_payload(claims).encode("utf-8"))
    return f"{PREFIX}.{payload}.{sign(payload, key)}"


class JtiCache:
    """Cache memori jti terpakai (TTL 120 dtk). Replika tunggal (ADR-051 §4.2)."""

    def __init__(self, ttl: int = JTI_TTL_SECONDS, max_size: int = JTI_CACHE_MAX) -> None:
        self._ttl = ttl
        self._max = max_size
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def use(self, jti: str, now: float) -> bool:
        """True bila jti baru (lalu dicatat); False bila replay atau cache penuh (fail closed)."""
        with self._lock:
            if len(self._seen) >= self._max:
                self._seen = {k: v for k, v in self._seen.items() if v > now}
                if len(self._seen) >= self._max:
                    return False
            expiry = self._seen.get(jti)
            if expiry is not None and expiry > now:
                return False
            self._seen[jti] = now + self._ttl
            return True


def _pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = tuple(k for k, _ in pairs)
    if len(set(keys)) != len(keys):
        raise ValueError("kunci ganda")
    out = dict(pairs)
    out["__order__"] = keys
    return out


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def verify_assertion(
    header: str | None,
    *,
    method: str,
    path: str,
    body: bytes,
    key: bytes,
    jti_cache: JtiCache,
    now: float | None = None,
) -> str:
    """Kembalikan `sid` (string 43 karakter, atau `""`). Semua kegagalan -> `ActorInvalid(reason)`."""
    current = time.time() if now is None else now
    if not header or len(header.encode("utf-8", "replace")) > MAX_HEADER_BYTES or not HEADER_RE.fullmatch(header):
        raise ActorInvalid("format")
    _, payload_b64, sig_b64 = header.split(".")
    expected = sign(payload_b64, key)
    if not hmac.compare_digest(expected.encode("ascii"), sig_b64.encode("ascii")):
        raise ActorInvalid("signature")
    try:
        claims = json.loads(b64url_decode(payload_b64).decode("utf-8"), object_pairs_hook=_pairs_hook)
    except (ValueError, UnicodeDecodeError):
        raise ActorInvalid("payload") from None
    if not isinstance(claims, dict) or claims.pop("__order__", None) != CLAIM_KEYS:
        raise ActorInvalid("keys")
    if claims["aud"] != AUDIENCE:
        raise ActorInvalid("aud")
    iat, exp = claims["iat"], claims["exp"]
    if not _is_int(iat) or not _is_int(exp):
        raise ActorInvalid("time")
    if not (iat <= current + CLOCK_SKEW_SECONDS and current < exp and exp - iat == TTL_SECONDS):
        raise ActorInvalid("expired")
    if claims["m"] != method or claims["p"] != path:
        raise ActorInvalid("binding")
    if not isinstance(claims["bh"], str) or not hmac.compare_digest(claims["bh"], body_hash(body)):
        raise ActorInvalid("body_hash")
    jti = claims["jti"]
    if not isinstance(jti, str) or len(jti) != 22 or not _B64_RE.fullmatch(jti):
        raise ActorInvalid("jti")
    if not jti_cache.use(jti, current):
        raise ActorInvalid("replay")
    sid, p, m = claims["sid"], claims["p"], claims["m"]
    if (
        not isinstance(sid, str)
        or (sid != "" and not SID_RE.fullmatch(sid))
        or not isinstance(p, str)
        or not p.startswith("/v1/")
        or not isinstance(m, str)
        or m != m.upper()
    ):
        raise ActorInvalid("claims")
    return sid
