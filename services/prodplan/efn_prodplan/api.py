"""API HTTP prodplan (docs/04 §30.8). Stdlib `ThreadingHTTPServer` plaintext di `127.0.0.1:8080`.

Pilihan stdlib (bukan framework): tanpa dependency tambahan; TLS + mTLS (CN `insight-web-pp` / `prometheus`) diterminasi
sidecar ghostunnel `prodplan-tls` di namespace yang sama, jadi server ini hanya terjangkau dari loopback.

`App.handle()` murni terhadap socket (dipakai test): verifikasi `X-EFN-Actor` (`ppa1`, §30.7), rate limit mutasi
60/menit per `sub`, batas body 256 KiB, galat `{"error": {...}}` tanpa SQL/stack/URL/body ESB.
"""

import json
import logging
import re
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from efn_prodplan import metrics
from efn_prodplan.actor import ActorInvalid, JtiCache, verify_actor
from efn_prodplan.logs import log_event
from efn_prodplan.workflow import ApiError

MAX_BODY_BYTES = 256 * 1024
MUTATIONS_PER_MINUTE = 60
_ID = r"([0-9a-fA-F-]{36})"
_NUM = r"([0-9]{1,18})"


class RateLimiter:
    def __init__(self, limit: int = MUTATIONS_PER_MINUTE, window: float = 60.0) -> None:
        self._limit = limit
        self._window = window
        self._hits: dict[int, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, sub: int, now: float) -> bool:
        with self._lock:
            hits = self._hits.setdefault(sub, deque())
            while hits and hits[0] <= now - self._window:
                hits.popleft()
            if len(hits) >= self._limit:
                return False
            hits.append(now)
            if len(self._hits) > 10_000:  # batas memori: buang pengguna tanpa hit aktif
                self._hits = {k: v for k, v in self._hits.items() if v}
            return True


@dataclass(frozen=True)
class Route:
    method: str
    pattern: re.Pattern[str]
    name: str


ROUTES: tuple[Route, ...] = tuple(
    Route(m, re.compile(p), n)
    for m, p, n in (
        ("GET", r"^/v1/meta$", "meta"),
        ("GET", r"^/v1/boms$", "search_boms"),
        ("GET", r"^/v1/pack-rules$", "list_pack_rules"),
        ("PUT", r"^/v1/pack-rules/([a-z]{2,8})/([A-Za-z0-9._-]{1,40})$", "put_pack_rule"),
        ("GET", r"^/v1/plans$", "list_plans"),
        ("POST", r"^/v1/plans$", "create_plan"),
        ("GET", rf"^/v1/plans/{_ID}$", "get_plan"),
        ("PUT", rf"^/v1/plans/{_ID}/demand$", "put_demand"),
        ("POST", rf"^/v1/plans/{_ID}/import-pr$", "import_pr"),
        ("POST", rf"^/v1/plans/{_ID}/calculate$", "calculate"),
        ("GET", rf"^/v1/jobs/{_ID}$", "get_job"),
        ("POST", rf"^/v1/plans/{_ID}/suggestions/{_NUM}/(accept|reject)$", "decide_suggestion"),
        ("POST", rf"^/v1/plans/{_ID}/submit$", "submit"),
        ("POST", rf"^/v1/plans/{_ID}/transfers/{_NUM}/(approve|reject)$", "decide_transfer"),
        ("POST", rf"^/v1/plans/{_ID}/approve$", "approve"),
        ("POST", rf"^/v1/plans/{_ID}/return-to-draft$", "return_to_draft"),
        ("GET", rf"^/v1/plans/{_ID}/post-preview$", "post_preview"),
        ("POST", rf"^/v1/plans/{_ID}/post$", "post"),
        ("POST", rf"^/v1/plans/{_ID}/cancel$", "cancel"),
        ("POST", rf"^/v1/outbox/{_ID}/resend$", "resend"),
    )
)
_CREATED = frozenset({"create_plan"})
_ACCEPTED = frozenset({"import_pr", "calculate", "post", "resend"})


def dispatch(
    service: Any,
    name: str,
    actor: Any,
    groups: tuple[str, ...],
    query: Mapping[str, str],
    body: Mapping[str, Any],
    rid: str,
) -> Any:
    """Panggil method service sesuai nama route (argumen posisional = grup regex)."""
    g = [x.lower() if len(x) == 36 else x for x in groups]
    if name == "meta":
        return service.meta(actor)
    if name in ("list_pack_rules", "list_plans", "search_boms"):
        return getattr(service, name)(actor, query)
    if name == "put_pack_rule":
        return service.put_pack_rule(actor, g[0], g[1], body, rid)
    if name == "create_plan":
        return service.create_plan(actor, body, rid)
    if name in ("get_plan", "get_job"):
        return getattr(service, name)(actor, g[0])
    if name == "post_preview":
        return service.post_preview(actor, g[0], query)
    if name in ("decide_suggestion", "decide_transfer"):
        return getattr(service, name)(actor, g[0], g[1], g[2], body, rid)
    if name == "resend":
        return service.resend(actor, g[0], rid)
    return getattr(service, name)(actor, g[0], body, rid)


class App:
    def __init__(
        self,
        *,
        enabled: bool,
        assert_key: bytes | None,
        service: Any,
        ready_check: Callable[[], bool],
        jti_cache: JtiCache | None = None,
        rate_limiter: RateLimiter | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._enabled = enabled
        self._key = assert_key
        self._service = service
        self._ready = ready_check
        self._jti = jti_cache or JtiCache()
        self._rate = rate_limiter or RateLimiter()
        self._clock = clock

    @staticmethod
    def _json(status: int, payload: Any, rid: str) -> tuple[int, dict[str, str], bytes]:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Request-ID": rid,
        }
        return status, headers, body

    def handle(
        self, method: str, raw_path: str, headers: Mapping[str, str], body: bytes
    ) -> tuple[int, dict[str, str], bytes]:
        rid = str(uuid.uuid4())
        parts = urlsplit(raw_path)
        path = parts.path
        if method == "GET" and path == "/healthz":
            return self._json(200, {"status": "ok"}, rid)
        if method == "GET" and path == "/readyz":
            if not self._enabled:
                return self._json(200, {"status": "disabled"}, rid)  # tanpa panggilan PG saat nonaktif
            ok = self._ready()
            return self._json(200 if ok else 503, {"status": "ok" if ok else "unavailable"}, rid)
        if method == "GET" and path == "/metrics":
            return (
                200,
                {"Content-Type": "text/plain; version=0.0.4", "Cache-Control": "no-store"},
                (metrics.render().encode("utf-8")),
            )
        if not path.startswith("/v1/"):
            return self._json(404, ApiError(404, "not_found").body(), rid)
        if not self._enabled or self._key is None or self._service is None:
            return self._json(503, ApiError(503, "disabled").body(), rid)
        route, groups, path_known = None, (), False
        for candidate in ROUTES:
            match = candidate.pattern.fullmatch(path)
            if match:
                path_known = True
                if candidate.method == method:
                    route, groups = candidate, match.groups()
                    break
        if route is None:
            err = ApiError(405, "method_not_allowed") if path_known else ApiError(404, "not_found")
            return self._json(err.status, err.body(), rid)
        try:
            actor = verify_actor(
                headers.get("x-efn-actor"),
                method=method,
                path=raw_path,
                body=body,
                key=self._key,
                jti_cache=self._jti,
                now=self._clock(),
            )
        except ActorInvalid as exc:
            metrics.ACTOR_REJECTED.inc(exc.reason)
            log_event("actor_rejected", logging.WARNING, request_id=rid, reason=exc.reason, route=route.name)
            return self._json(401, ApiError(401, "actor_invalid").body(), rid)
        if method != "GET" and not self._rate.allow(actor.sub, self._clock()):
            return self._json(429, ApiError(429, "rate_limited").body(), rid)
        payload: Any = {}
        if body:
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return self._json(400, ApiError(400, "invalid_request").body(), rid)
            if not isinstance(payload, dict):
                return self._json(400, ApiError(400, "invalid_request").body(), rid)
        query = {k: v[0] for k, v in parse_qs(parts.query, keep_blank_values=True).items() if v}
        started = time.monotonic()
        try:
            result = dispatch(self._service, route.name, actor, groups, query, payload, rid)
            status = 201 if route.name in _CREATED else 202 if route.name in _ACCEPTED else 200
            response = self._json(status, result, rid)
        except ApiError as exc:
            response = self._json(exc.status, exc.body(), rid)
        except Exception as exc:  # noqa: BLE001 - galat internal tidak membocorkan detail
            log_event("api_error", logging.ERROR, request_id=rid, route=route.name, error_class=type(exc).__name__)
            response = self._json(500, ApiError(500, "internal").body(), rid)
        log_event(
            "api_request",
            request_id=rid,
            sub=actor.sub,
            route=route.name,
            method=method,
            http_status=response[0],
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return response


class _Handler(BaseHTTPRequestHandler):
    app: App
    timeout = 30
    server_version = "prodplan"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - signature stdlib
        return  # log akses = log_event JSON di App (tanpa query string)

    def _error(self, status: int, code: str) -> None:
        self.close_connection = True
        body = json.dumps(ApiError(status, code).body()).encode("utf-8")
        self._reply(status, {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"}, body)

    def _serve(self) -> None:
        if self.headers.get("Transfer-Encoding"):
            self._error(400, "invalid_request")
            return
        raw_length = (self.headers.get("Content-Length") or "0").strip()
        if not raw_length.isdigit():
            self._error(400, "invalid_request")
            return
        length = int(raw_length)
        if length > MAX_BODY_BYTES:
            self._error(413, "payload_too_large")
            return
        body = self.rfile.read(length) if length else b""
        headers = {k.lower(): v for k, v in self.headers.items()}
        status, out_headers, out_body = self.app.handle(self.command, self.path, headers, body)
        self._reply(status, out_headers, out_body)

    def _reply(self, status: int, headers: Mapping[str, str], body: bytes) -> None:
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _serve  # noqa: N815
    do_POST = _serve  # noqa: N815
    do_PUT = _serve  # noqa: N815


def make_server(app: App, host: str = "127.0.0.1", port: int = 8080) -> ThreadingHTTPServer:
    handler = type("ProdplanHandler", (_Handler,), {"app": app})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server
