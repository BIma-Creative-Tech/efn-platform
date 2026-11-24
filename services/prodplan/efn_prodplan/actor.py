"""Verifikasi header asersi aktor `X-EFN-Actor: ppa1.<payload>.<sig>` (docs/04 §30.7). Logika murni + cache jti.

Urutan verifikasi persis kontrak: regex -> HMAC (constant-time) -> kunci tepat (11, berurutan) -> aud -> waktu ->
m/p -> bh -> jti -> tipe klaim. Semua kegagalan = `ActorInvalid` (API membalas 401 `actor_invalid` tanpa detail);
`reason` hanya untuk metrik `prodplan_actor_rejected_total{reason}`.
"""

import base64
import hashlib
import hmac
import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

HEADER_RE = re.compile(r"^ppa1\.[A-Za-z0-9_-]{16,1800}\.[A-Za-z0-9_-]{43}$")
MAX_HEADER_BYTES = 2048
CLAIM_KEYS = ("aud", "bh", "codes", "exp", "iat", "jti", "m", "name", "p", "roles", "sub")
AUDIENCE = "prodplan"
TTL_SECONDS = 60
CLOCK_SKEW_SECONDS = 30
JTI_TTL_SECONDS = 120
JTI_CACHE_MAX = 50_000
PP_ROLES = ("pp_planner", "pp_warehouse")
ESB_CODES = ("BCF", "BCR", "CUP", "IMD", "PB", "WTZ")
_B64_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class ActorInvalid(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__("actor_invalid")
        self.reason = reason


@dataclass(frozen=True)
class Actor:
    sub: int
    name: str
    roles: frozenset[str]
    codes: frozenset[str]

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


def sign(payload_b64: str, key: bytes) -> str:
    return b64url_encode(hmac.new(key, ("ppa1." + payload_b64).encode("ascii"), hashlib.sha256).digest())


def make_header(claims: dict[str, Any], key: bytes) -> str:
    """Pembuat header (dipakai test; insight-web punya implementasi TypeScript yang byte-exact sama)."""
    ordered = {k: claims[k] for k in CLAIM_KEYS}
    payload = b64url_encode(json.dumps(ordered, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    return f"ppa1.{payload}.{sign(payload, key)}"


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


def _sorted_unique_strs(value: Any, allowed: tuple[str, ...], low: int, high: int) -> bool:
    return (
        isinstance(value, list)
        and low <= len(value) <= high
        and all(isinstance(x, str) and x in allowed for x in value)
        and value == sorted(set(value))
    )


def verify_actor(
    header: str | None,
    *,
    method: str,
    path: str,
    body: bytes,
    key: bytes,
    jti_cache: JtiCache,
    now: float | None = None,
) -> Actor:
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
    name, sub, p = claims["name"], claims["sub"], claims["p"]
    if (
        not _is_int(sub)
        or sub <= 0
        or not isinstance(name, str)
        or not 1 <= len(name) <= 64
        or not isinstance(p, str)
        or not p.startswith("/v1/")
        or not isinstance(claims["m"], str)
        or claims["m"] != claims["m"].upper()
        or not _sorted_unique_strs(claims["codes"], ESB_CODES, 1, 6)
        or not _sorted_unique_strs(claims["roles"], PP_ROLES, 1, 2)
    ):
        raise ActorInvalid("claims")
    return Actor(sub=sub, name=name, roles=frozenset(claims["roles"]), codes=frozenset(claims["codes"]))
