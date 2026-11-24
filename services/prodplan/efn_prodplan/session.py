"""Sesi Produksi per pengguna ESB di MEMORI proses prodplan (docs/04 §30.17.3–§30.17.5, ADR-051 §8 amandemen A1).

- `POST /v1/session`: username + password ESB diteruskan SEKALI ke `POST /auth/login`; password hanya hidup di memori
  request ini (tidak pernah ke log, PG, metrik, pesan galat). `refreshToken` dibuang saat diterima (Q-PP15).
- Token akses ESB disimpan hanya di `Session` (dict `sha256(sid) -> Session`), tidak pernah keluar proses; restart =
  semua sesi hilang (L-260). TTL sesi = klaim `exp` token (absolut, tanpa perpanjangan).
- Batas (konstanta kode, bukan env): 3 sesi per aktor, 200 sesi total; lockout 5 gagal/15 menit per username
  dinormalisasi; antrean login global 1/dtk, maks 5 bersamaan.
- Peran, akses dokumen, dan cabang dihitung SEKALI saat login (L-259).
"""

import logging
import re
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from efn_prodplan import metrics
from efn_prodplan.access import MAX_BRANCHES, access_items, compute_roles, doc_access, parse_branch_page
from efn_prodplan.actor import Actor, sid_hash
from efn_prodplan.config import BrandConfig
from efn_prodplan.esb import (
    EsbClient,
    EsbError,
    EsbInvalidCredentials,
    TokenBucket,
    Transport,
    jwt_claims,
)
from efn_prodplan.logs import log_event
from efn_prodplan.provision import provision_payload
from efn_prodplan.workflow import ApiError

MAX_SESSIONS_PER_ACTOR = 3
MAX_SESSIONS_TOTAL = 200
LOCKOUT_FAILURES = 5
LOCKOUT_WINDOW_SECONDS = 900.0
LOCKOUT_SECONDS = 900.0
GLOBAL_FAILURES = 30  # SEC-234: rem brute force per username dari semua sumber
GLOBAL_WINDOW_SECONDS = 3600.0
LIMITER_MAX_KEYS = 10_000  # SEC-241: batas keras memori limiter
LOGIN_INTERVAL_SECONDS = 1.0
LOGIN_CONCURRENCY = 5
LOGIN_QUEUE_WAIT_SECONDS = 5.0  # SEC-236: antrean + login ESB (8 dtk) < timeout insight-web 15 dtk
TOKEN_TTL_MAX_SECONDS = 3600
TOKEN_TTL_FALLBACK_SECONDS = 3540
WRITE_MIN_REMAINING = 120  # §30.17.9: POST/DELETE butuh sisa token >= 120 dtk
READ_MIN_REMAINING = 30
SWEEP_SECONDS = 60.0
BRANCH_PAGE_LIMIT = 100
BRANCH_MAX_PAGES = 20
USERNAME_RE = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")
NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22}$")
CLIENT_KEY_RE = NONCE_RE  # `c`: 22 karakter base64url (HMAC IP klien dipotong, dihitung insight-web)
LOGIN_RESULTS = (
    "ok",
    "invalid_credentials",
    "login_locked",
    "no_pp_access",
    "esb_company_not_allowed",
    "esb_unavailable",
    "session_capacity",
)
_ERRORS: Mapping[str, int] = {
    "invalid_credentials": 401,
    "login_locked": 429,
    "no_pp_access": 403,
    "esb_company_not_allowed": 403,
    "esb_unavailable": 502,
    "session_capacity": 503,
    "internal": 500,
}


# ====================================================================== murni
def valid_login_shape(username: Any, password: Any) -> bool:
    """§30.17.2: username regex setelah trim, password string 1..128 karakter (tidak di-trim)."""
    return (
        isinstance(username, str)
        and USERNAME_RE.fullmatch(username.strip()) is not None
        and isinstance(password, str)
        and 1 <= len(password) <= 128
    )


def normalize_username(username: str) -> str:
    return username.strip().lower()


def user_mask(username_norm: str) -> str:
    """§30.17.5 + SEC-239: <= 6 karakter -> karakter pertama + `***`; selain itu 2 pertama + `***` + 1 terakhir."""
    if len(username_norm) <= 6:
        return f"{username_norm[:1]}***"
    return f"{username_norm[:2]}***{username_norm[-1]}"


def token_expiry(token: str, now: float) -> float:
    """§30.17.3 langkah 5: klaim `exp` JWT (tanpa verifikasi tanda tangan). Tak terbaca atau > now+3600 -> now+3540."""
    exp = jwt_claims(token).get("exp")
    if isinstance(exp, int | float) and not isinstance(exp, bool) and exp <= now + TOKEN_TTL_MAX_SECONDS:
        return float(exp)
    return now + TOKEN_TTL_FALLBACK_SECONDS


def token_role_id(token: str) -> int | None:
    value = jwt_claims(token).get("userRoleID")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def iso_utc(epoch: float) -> str:
    return datetime.fromtimestamp(int(epoch), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_branches(client: Any) -> dict[int, str]:
    """Semua halaman `GET /branch/list/user?limit&page` (`pageSize`/`dataCount`, beda dari list lain)."""
    out: dict[int, str] = {}
    for page in range(1, BRANCH_MAX_PAGES + 1):
        result = client.get("/branch/list/user", {"limit": BRANCH_PAGE_LIMIT, "page": page})
        rows, total = parse_branch_page(result)
        out.update(rows)
        if isinstance(result, list) or not rows or len(rows) < BRANCH_PAGE_LIMIT:
            return out
        if total is not None and len(out) >= total:
            return out
    return out


# ====================================================================== sesi
@dataclass(eq=False)
class Session:
    sid_hash: str = field(repr=False)
    actor_id: int
    esb_env: str
    esb_brand: str
    company_code: str
    username_norm: str = field(repr=False)
    name: str = field(repr=False)
    user_role_id: int | None = field(repr=False)
    roles: frozenset[str] = frozenset()
    doc_insert: frozenset[str] = frozenset()
    doc_delete: frozenset[str] = frozenset()
    branch_ids: frozenset[int] = frozenset()
    branch_names: Mapping[int, str] = field(default_factory=dict, repr=False)
    access_token: str = field(default="", repr=False)
    exp: float = 0.0
    created_at: float = 0.0

    def __repr__(self) -> str:  # tanpa token, username, nama, sid
        return f"Session(actor_id={self.actor_id}, esb_brand={self.esb_brand!r}, exp={int(self.exp)})"

    def actor(self) -> Actor:
        return Actor(
            sub=self.actor_id,
            name=self.name,
            roles=self.roles,
            brand=self.esb_brand,
            branch_ids=self.branch_ids,
            doc_insert=self.doc_insert,
            doc_delete=self.doc_delete,
            branch_names=dict(self.branch_names),
            sid_hash=self.sid_hash,
        )

    def view(self) -> dict[str, Any]:
        """`SessionView` §30.17.3: tanpa username, token, `userRoleID`, maupun sid. `actor_id` (= `*_sub` rencana)
        ditambahkan atas permintaan insight-web untuk petunjuk SoD di UI (bukan rahasia; id internal)."""
        return {
            "actor_id": self.actor_id,
            "name": self.name[:64],
            "esb_env": self.esb_env,
            "esb_brand": self.esb_brand,
            "company_code": self.company_code,
            "roles": sorted(self.roles),
            "doc_access": {"insert": sorted(self.doc_insert), "delete": sorted(self.doc_delete)},
            "branches": [{"branch_id": b, "name": self.branch_names.get(b) or None} for b in sorted(self.branch_ids)],
            "expires_at": iso_utc(self.exp),
        }


class SessionStore:
    """`sha256(sid) -> Session`, thread-safe. Penghapusan selalu lewat `_drop` (metrik + log alasan)."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._data: dict[str, Session] = {}
        self._lock = threading.Lock()

    def _drop(self, session: Session, reason: str) -> None:
        self._data.pop(session.sid_hash, None)
        metrics.SESSION_EVICTIONS.inc(reason)
        if reason == "logout":
            log_event("pp_logout", actor_id=session.actor_id, reason=reason)
        else:
            log_event("pp_session_evicted", actor_id=session.actor_id, reason=reason)

    def _gauge(self) -> None:
        metrics.SESSIONS_ACTIVE.set(len(self._data))

    def sweep(self) -> int:
        now = self._clock()
        with self._lock:
            expired = [s for s in self._data.values() if s.exp <= now]
            for s in expired:
                self._drop(s, "expired")
            self._gauge()
        return len(expired)

    def has_capacity(self) -> bool:
        self.sweep()
        with self._lock:
            return len(self._data) < MAX_SESSIONS_TOTAL

    def add(self, session: Session) -> list[Session] | None:
        """Simpan sesi. Lewat batas 3 per aktor -> sesi tertua dibuang (dikembalikan untuk logout ESB). Total penuh ->
        None (sesi tidak disimpan)."""
        with self._lock:
            if len(self._data) >= MAX_SESSIONS_TOTAL:
                return None
            own = sorted(
                (s for s in self._data.values() if s.actor_id == session.actor_id and s.esb_env == session.esb_env),
                key=lambda s: s.created_at,
            )
            evicted = own[: max(0, len(own) - (MAX_SESSIONS_PER_ACTOR - 1))]
            for s in evicted:
                self._drop(s, "limit")
            self._data[session.sid_hash] = session
            self._gauge()
            return evicted

    def get(self, sid_hash_value: str) -> Session | None:
        now = self._clock()
        with self._lock:
            session = self._data.get(sid_hash_value)
            if session is None:
                return None
            if session.exp <= now:
                self._drop(session, "expired")
                self._gauge()
                return None
            return session

    def remove(self, sid_hash_value: str, reason: str) -> Session | None:
        with self._lock:
            session = self._data.get(sid_hash_value)
            if session is not None:
                self._drop(session, reason)
                self._gauge()
            return session

    def token_for(self, actor_id: int, esb_env: str, esb_brand: str, min_remaining: float) -> Session | None:
        """§30.17.9: sesi hidup aktor itu dengan `exp` terbesar dan sisa >= `min_remaining` detik."""
        now = self._clock()
        with self._lock:
            live = [
                s
                for s in self._data.values()
                if s.actor_id == actor_id
                and s.esb_env == esb_env
                and s.esb_brand == esb_brand
                and s.exp - now >= min_remaining
            ]
        return max(live, key=lambda s: s.exp) if live else None

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


class _FailureCounter:
    """Hitungan gagal per kunci: `limit` gagal dalam `window` dtk -> terkunci `lock_seconds`. Memori dibatasi
    `max_keys` (penuh -> kunci yang paling lama tidak aktif dibuang); `sweep` membuang entri kedaluwarsa (SEC-241).
    Tidak thread-safe sendiri; dipakai di bawah kunci `LoginLimiter`."""

    def __init__(self, limit: int, window: float, lock_seconds: float, max_keys: int) -> None:
        self._limit = limit
        self._window = window
        self._lock_seconds = lock_seconds
        self._max = max_keys
        self._data: dict[Any, tuple[deque[float], float]] = {}  # kunci -> (hits, terkunci_sampai)

    def locked(self, key: Any, now: float) -> bool:
        entry = self._data.get(key)
        return entry is not None and entry[1] > now

    def failure(self, key: Any, now: float) -> None:
        hits, until = self._data.pop(key, (deque(), 0.0))  # pop + set = pindah ke akhir (paling baru aktif)
        while hits and hits[0] <= now - self._window:
            hits.popleft()
        hits.append(now)
        if len(hits) >= self._limit:
            until = now + self._lock_seconds
            hits.clear()
        self._data[key] = (hits, until)
        while len(self._data) > self._max:
            del self._data[next(iter(self._data))]

    def clear(self, key: Any) -> None:
        self._data.pop(key, None)

    def sweep(self, now: float) -> None:
        self._data = {
            k: (hits, until)
            for k, (hits, until) in self._data.items()
            if until > now or (hits and hits[-1] > now - self._window)
        }

    def __len__(self) -> int:
        return len(self._data)


class LoginLimiter:
    """SEC-234: lockout per pasangan (username_norm, kunci klien `c`) 5 gagal/15 menit -> pasangan terkunci 15 menit;
    rem global per username 30 gagal/60 menit -> semua sumber terkunci 15 menit. `c` = kunci buram dari insight-web
    (HMAC IP klien), tidak dihitung ulang/diverifikasi, tidak dilog. Login sukses hanya menghapus hitungan pasangan."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._pairs = _FailureCounter(LOCKOUT_FAILURES, LOCKOUT_WINDOW_SECONDS, LOCKOUT_SECONDS, LIMITER_MAX_KEYS)
        self._users = _FailureCounter(GLOBAL_FAILURES, GLOBAL_WINDOW_SECONDS, LOCKOUT_SECONDS, LIMITER_MAX_KEYS)
        self._lock = threading.Lock()

    def locked(self, username_norm: str, client_key: str) -> bool:
        now = self._clock()
        with self._lock:
            return self._pairs.locked((username_norm, client_key), now) or self._users.locked(username_norm, now)

    def failure(self, username_norm: str, client_key: str) -> None:
        now = self._clock()
        with self._lock:
            self._pairs.failure((username_norm, client_key), now)
            self._users.failure(username_norm, now)

    def success(self, username_norm: str, client_key: str) -> None:
        with self._lock:
            self._pairs.clear((username_norm, client_key))

    def sweep(self) -> None:
        now = self._clock()
        with self._lock:
            self._pairs.sweep(now)
            self._users.sweep(now)

    def sizes(self) -> tuple[int, int]:
        with self._lock:
            return len(self._pairs), len(self._users)


class LoginGate:
    """Antrean login global: maks 5 bersamaan, jarak minimal 1 dtk antar `POST /auth/login`."""

    def __init__(
        self,
        interval: float = LOGIN_INTERVAL_SECONDS,
        concurrency: int = LOGIN_CONCURRENCY,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._interval = interval
        self._sem = threading.BoundedSemaphore(concurrency)
        self._clock = clock
        self._sleep = sleep
        self._next = 0.0
        self._lock = threading.Lock()

    @contextmanager
    def slot(self) -> Iterator[None]:
        if not self._sem.acquire(timeout=LOGIN_QUEUE_WAIT_SECONDS):
            raise TimeoutError("antrean login penuh")
        try:
            with self._lock:
                now = self._clock()
                start = max(now, self._next)
                self._next = start + self._interval
            if start > now:
                self._sleep(start - now)
            yield
        finally:
            self._sem.release()


# ====================================================================== klien per sesi
class EsbClients:
    """Pabrik klien ESB bertoken pengguna. Satu token bucket per brand (beban ESB R-O10), satu untuk login."""

    def __init__(
        self,
        *,
        base_url: str,
        mfg_mode: str,
        transport: Transport,
        sessions: SessionStore,
        timeout: float = 30.0,
        rate_per_sec: float = 2.0,
    ) -> None:
        self._base = base_url
        self._mfg = mfg_mode
        self._transport = transport
        self._sessions = sessions
        self._timeout = timeout
        self._rate = rate_per_sec
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def _bucket(self, key: str) -> TokenBucket:
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = self._buckets[key] = TokenBucket(self._rate)
            return bucket

    def make(
        self,
        token: str | None,
        bucket_key: str,
        *,
        allow_write: bool = False,
        max_calls: int | None = None,
        session_key: str = "",
    ) -> EsbClient:
        def evict() -> None:
            if session_key:
                self._sessions.remove(session_key, "esb_401")

        return EsbClient(
            self._base,
            token,
            self._transport,
            self._bucket(bucket_key),
            mfg_mode=self._mfg,
            allow_write=allow_write,
            timeout=self._timeout,
            max_calls=max_calls,
            on_unauthorized=evict,
        )

    def for_session(self, session: Session, *, allow_write: bool = False, max_calls: int | None = None) -> EsbClient:
        return self.make(
            session.access_token,
            session.esb_brand,
            allow_write=allow_write,
            max_calls=max_calls,
            session_key=session.sid_hash,
        )

    def for_actor(self, actor: Actor) -> EsbClient | None:
        """Klien GET sinkron API = token sesi pemanggil (§30.17.9)."""
        session = self._sessions.get(actor.sid_hash)
        return None if session is None else self.for_session(session)

    def for_owner(
        self,
        actor_id: int,
        esb_env: str,
        esb_brand: str,
        min_remaining: float,
        *,
        allow_write: bool = False,
        max_calls: int | None = None,
    ) -> tuple[Session, EsbClient] | None:
        """Job/outbox: sesi hidup milik aktor pemicu (`token_for`); None -> `needs_login`."""
        session = self._sessions.token_for(actor_id, esb_env, esb_brand, min_remaining)
        if session is None:
            return None
        return session, self.for_session(session, allow_write=allow_write, max_calls=max_calls)


# ====================================================================== login
class ActorStore(Protocol):
    def upsert(self, esb_env: str, esb_brand: str, username_norm: str, display_name: str) -> int: ...


class Provisioner(Protocol):
    def enqueue(self, payload: Mapping[str, Any], actor_id: int) -> None: ...


class _Fail(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class LoginService:
    """Langkah `POST /v1/session` §30.17.3 (berurutan) + `DELETE /v1/session`."""

    def __init__(
        self,
        *,
        esb_env: str,
        brands: Sequence[BrandConfig],
        planner_rules: Sequence[tuple[str, frozenset[str]]],
        warehouse_rules: Sequence[tuple[str, frozenset[str]]],
        doc_access_map: Mapping[str, str],
        sessions: SessionStore,
        clients: EsbClients,
        actors: ActorStore,
        provisioner: Provisioner | None = None,
        limiter: LoginLimiter | None = None,
        gate: LoginGate | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._env = esb_env
        self._brands = tuple(brands)
        self._planner = tuple(planner_rules)
        self._warehouse = tuple(warehouse_rules)
        self._doc_map = dict(doc_access_map)
        self._sessions = sessions
        self._clients = clients
        self._actors = actors
        self._provisioner = provisioner
        self._limiter = limiter or LoginLimiter(clock)
        self._gate = gate or LoginGate()
        self._clock = clock

    def _brand_for(self, esb_company_code: str) -> BrandConfig | None:
        for brand in self._brands:
            if brand.esb_company_code and brand.esb_company_code == esb_company_code:
                return brand
        return None

    def _record(
        self, result: str, mask: str, started: float, brand: str | None = None, actor_id: int | None = None
    ) -> None:
        if result in LOGIN_RESULTS:
            metrics.LOGIN_TOTAL.inc(result)
        log_event(
            "pp_login",
            logging.INFO if result == "ok" else logging.WARNING,
            result=result,
            user_mask=mask,
            esb_brand=brand,
            actor_id=actor_id,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    def _error(self, code: str, mask: str, started: float) -> ApiError:
        self._record(code, mask, started)
        return ApiError(_ERRORS[code], code)

    def sweep(self) -> None:
        """Dipanggil penyapu 60 dtk: buang entri lockout kedaluwarsa (SEC-241)."""
        self._limiter.sweep()

    def create(self, username: Any, password: Any, client_key: str) -> dict[str, Any]:
        """`client_key` = `c` body (sudah lolos `CLIENT_KEY_RE` di API); hanya kunci pembatas, tidak dilog."""
        started = time.monotonic()
        if not isinstance(client_key, str) or not CLIENT_KEY_RE.fullmatch(client_key):
            raise ApiError(400, "invalid_request")
        if not valid_login_shape(username, password):
            raise self._error("invalid_credentials", "***", started)  # langkah 1: tanpa ESB
        user = username.strip()
        norm = normalize_username(user)
        mask = user_mask(norm)
        if self._limiter.locked(norm, client_key):
            raise self._error("login_locked", mask, started)  # langkah 2: tanpa ESB
        if not self._sessions.has_capacity():
            raise self._error("session_capacity", mask, started)
        try:
            with self._gate.slot():
                login = self._clients.make(None, "_login").login(user, password)
        except EsbInvalidCredentials:
            self._limiter.failure(norm, client_key)
            raise self._error("invalid_credentials", mask, started) from None
        except (EsbError, TimeoutError):
            raise self._error("esb_unavailable", mask, started) from None  # tidak dihitung lockout
        token = login.access_token
        temp = self._clients.make(token, "_login")
        try:
            session, sid = self._build(login, token, temp, norm)
        except _Fail as fail:
            temp.logout()  # best-effort setelah token diperoleh
            if fail.code == "invalid_credentials":
                self._limiter.failure(norm, client_key)
            raise self._error(fail.code, mask, started) from None
        except Exception:
            temp.logout()
            raise self._error("internal", mask, started) from None
        evicted = self._sessions.add(session)
        if evicted is None:
            temp.logout()
            raise self._error("session_capacity", mask, started)
        for old in evicted:
            self._clients.for_session(old).logout()
        self._limiter.success(norm, client_key)  # hanya pasangan ini; hitungan global tetap
        if self._provisioner is not None:
            self._provisioner.enqueue(
                provision_payload(
                    session.esb_brand,
                    norm,
                    login.full_name,
                    session.company_code,
                    session.user_role_id,
                    session.roles,
                    session.branch_ids,
                ),
                session.actor_id,
            )
        self._record("ok", mask, started, session.esb_brand, session.actor_id)
        return {"sid": sid, "expires_at": iso_utc(session.exp), "session": session.view()}

    def _build(self, login: Any, token: str, client: EsbClient, norm: str) -> tuple[Session, str]:
        """Langkah 3 (sisa) .. 8 sebelum sesi disimpan. `_Fail(code)` -> galat login + logout ESB."""
        if not login.flag_active or not token:
            raise _Fail("invalid_credentials")
        brand = self._brand_for(login.company_code)
        if brand is None:
            raise _Fail("esb_company_not_allowed")
        now = self._clock()
        exp = token_expiry(token, now)
        try:
            items = access_items(client.get("/user/access"))
            branches = fetch_branches(client)
        except EsbError:
            raise _Fail("esb_unavailable") from None
        roles = compute_roles(items, self._planner, self._warehouse)
        if not roles or not branches:
            raise _Fail("no_pp_access")
        doc_insert, doc_delete = doc_access(items, self._doc_map)
        display = (login.full_name or norm)[:64]
        actor_id = self._actors.upsert(self._env, brand.key, norm, display)
        sid = secrets.token_urlsafe(32)  # 43 karakter base64url; hanya hash-nya yang disimpan
        branch_ids = sorted(branches)[:MAX_BRANCHES]
        session = Session(
            sid_hash=sid_hash(sid),
            actor_id=int(actor_id),
            esb_env=self._env,
            esb_brand=brand.key,
            company_code=brand.company_code,
            username_norm=norm,
            name=display,
            user_role_id=token_role_id(token),
            roles=roles,
            doc_insert=doc_insert,
            doc_delete=doc_delete,
            branch_ids=frozenset(branch_ids),
            branch_names={b: branches[b] for b in branch_ids},
            access_token=token,
            exp=exp,
            created_at=now,
        )
        return session, sid

    def logout(self, session: Session) -> None:
        """`DELETE /v1/session`: buang sesi + `POST /auth/logout` ESB (timeout 5 dtk, galat diabaikan)."""
        self._sessions.remove(session.sid_hash, "logout")
        self._clients.for_session(session).logout()
