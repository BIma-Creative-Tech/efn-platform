"""App HTTP (tanpa socket): gerbang disabled, asersi `ppa2` + sesi memori, rate limit, galat tanpa detail internal."""

import itertools
import json

from efn_prodplan.actor import body_hash, make_header, sid_hash
from efn_prodplan.api import ROUTES, App, RateLimiter
from efn_prodplan.session import Session, SessionStore
from efn_prodplan.workflow import ApiError

KEY = ("ab" * 32).encode("ascii")
NOW = 1_795_000_000
PLAN_ID = "0b6f1c1e-1111-4222-8333-444455556666"
SID = "s" * 43
SID2 = "t" * 43
NONCE = "n" * 22
CKEY = "k" * 22  # kunci klien `c` rekaan
_counter = itertools.count()


class StubService:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def handler(*args):
            self.calls.append((name, args))
            if name == "submit":
                raise ApiError(409, "stale_calculation")
            if name == "approve":
                raise RuntimeError("SELECT * FROM secret_table; https://stg7.esb.co.id/core-stg?token=abc")
            if name == "create_plan":
                return {"id": PLAN_ID}
            return {"ok": name}

        return handler


class StubLogin:
    def __init__(self):
        self.created = []
        self.logged_out = []

    def create(self, username, password, client_key):
        self.created.append((username, password, client_key))
        return {"sid": SID2, "expires_at": "2026-11-25T08:00:00Z", "session": {"name": "x"}}

    def logout(self, session):
        self.logged_out.append(session.actor_id)


def session(sid=SID, actor_id=1000001, exp=NOW + 3000):
    return Session(
        sid_hash=sid_hash(sid),
        actor_id=actor_id,
        esb_env="staging",
        esb_brand="pb",
        company_code="PB",
        username_norm="planner.uji",
        name="Planner Uji",
        user_role_id=7,
        roles=frozenset({"pp_planner"}),
        branch_ids=frozenset({4}),
        access_token="tok-rekaan",
        exp=exp,
        created_at=NOW,
    )


def header(method, path, body=b"", sid=SID, **over):
    claims = {
        "aud": "prodplan",
        "bh": body_hash(body),
        "exp": NOW + 60,
        "iat": NOW,
        "jti": f"{next(_counter):022d}",
        "m": method,
        "p": path,
        "sid": sid,
    }
    claims.update(over)
    return {"x-efn-actor": make_header(claims, KEY)}


def app(enabled=True, service=None, limiter=None, ready=True, sessions=None, login=None):
    store = sessions
    if store is None and enabled:
        store = SessionStore(clock=lambda: NOW)
        store.add(session())
    return App(
        enabled=enabled,
        assert_key=KEY if enabled else None,
        service=service if service is not None else (StubService() if enabled else None),
        ready_check=lambda: ready,
        sessions=store,
        login=login or StubLogin(),
        rate_limiter=limiter,
        clock=lambda: NOW,
    )


def call(a, method, path, body=b"", headers=None):
    status, out_headers, out = a.handle(method, path, headers or {}, body)
    is_json = out_headers.get("Content-Type", "").startswith("application/json")
    return status, json.loads(out) if is_json else out


def test_health_without_actor():
    assert call(app(), "GET", "/healthz") == (200, {"status": "ok"})
    assert call(app(ready=False), "GET", "/readyz")[0] == 503
    assert call(app(enabled=False), "GET", "/readyz") == (200, {"status": "disabled"})
    status, text = call(app(), "GET", "/metrics")
    assert status == 200 and b"prodplan_esb_requests_total" in text and b"prodplan_login_total" in text


def test_disabled_returns_503_for_v1():
    status, body = call(app(enabled=False), "GET", "/v1/meta")
    assert status == 503 and body["error"]["code"] == "disabled"


def test_missing_or_bad_actor_is_401_without_detail():
    status, body = call(app(), "GET", "/v1/meta")
    assert status == 401 and body == {"error": {"code": "actor_invalid", "message": "Identitas pengguna tidak valid"}}
    h = header("GET", "/v1/meta")
    assert call(app(), "GET", "/v1/plans", headers=h)[0] == 401  # p tidak cocok


def test_empty_sid_only_for_login_and_meta():
    svc = StubService()
    a = app(service=svc)
    status, body = call(a, "GET", "/v1/meta", headers=header("GET", "/v1/meta", sid=""))
    assert status == 200 and svc.calls[-1] == ("meta", (None,))
    for method, path in (("GET", "/v1/plans"), ("GET", "/v1/session"), ("DELETE", "/v1/session")):
        status, body = call(a, method, path, headers=header(method, path, sid=""))
        assert status == 401 and body["error"]["code"] == "actor_invalid", path


def test_unknown_or_expired_sid_is_session_expired():
    a = app()
    status, body = call(a, "GET", "/v1/plans", headers=header("GET", "/v1/plans", sid=SID2))
    assert status == 401 and body["error"]["code"] == "session_expired"
    store = SessionStore(clock=lambda: NOW)
    store.add(session(exp=NOW - 1))
    status, body = call(app(sessions=store), "GET", "/v1/plans", headers=header("GET", "/v1/plans"))
    assert status == 401 and body["error"]["code"] == "session_expired"
    status, body = call(a, "GET", "/v1/meta", headers=header("GET", "/v1/meta", sid=SID2))
    assert body["error"]["code"] == "session_expired"


def test_session_routes():
    login = StubLogin()
    a = app(login=login)
    payload = {"username": "planner.uji", "password": "rahasia-rekaan", "n": NONCE, "c": CKEY}
    body = json.dumps(payload).encode()
    status, out = call(a, "POST", "/v1/session", body, header("POST", "/v1/session", body, sid=""))
    assert status == 201 and out["sid"] == SID2
    assert login.created == [("planner.uji", "rahasia-rekaan", CKEY)]
    status, out = call(a, "GET", "/v1/session", headers=header("GET", "/v1/session"))
    assert status == 200 and out["esb_brand"] == "pb" and "access_token" not in json.dumps(out)
    status, out = call(a, "DELETE", "/v1/session", headers=header("DELETE", "/v1/session"))
    assert status == 204 and out == b"" and login.logged_out == [1000001]
    # sesi tak dikenal -> tetap 204 (idempoten)
    status, _ = call(a, "DELETE", "/v1/session", headers=header("DELETE", "/v1/session", sid=SID2))
    assert status == 204


def test_session_create_body_rules():
    login = StubLogin()
    a = app(login=login)
    bad_bodies = [
        {"username": "u", "password": "p"},
        {"username": "u", "password": "p", "n": NONCE},  # tanpa `c` (SEC-234)
        {"username": "u", "password": "p", "n": NONCE, "c": CKEY, "x": 1},
        {"username": "u", "password": "p", "n": "short", "c": CKEY},
        {"username": "u", "password": "p", "n": NONCE, "c": "short"},
        {"username": "u", "password": "p", "n": NONCE, "c": "k" * 21 + "="},
        {"username": "u", "password": "p", "n": NONCE, "c": 123},
    ]
    for payload in bad_bodies:
        body = json.dumps(payload).encode()
        status, out = call(a, "POST", "/v1/session", body, header("POST", "/v1/session", body, sid=""))
        assert status == 400 and out["error"]["code"] == "invalid_request"
    body = json.dumps({"username": "u", "password": "p", "n": NONCE, "c": CKEY}).encode()
    status, _ = call(a, "POST", "/v1/session", body, header("POST", "/v1/session", body))  # sid terisi
    assert status == 401
    assert login.created == []


def test_valid_get_and_query_binding():
    svc = StubService()
    a = app(service=svc)
    path = "/v1/plans?brand=pb&limit=50"
    status, body = call(a, "GET", path, headers=header("GET", path))
    assert status == 200 and body == {"ok": "list_plans"}
    actor, query = svc.calls[0][1]
    assert query == {"brand": "pb", "limit": "50"}
    assert actor.sub == 1000001 and actor.brand == "pb" and actor.branch_ids == frozenset({4})
    path = "/v1/boms?brand=pb&q=roti"
    assert call(a, "GET", path, headers=header("GET", path))[1] == {"ok": "search_boms"}


def test_post_create_201_and_body_bound():
    body = json.dumps({"brand": "pb"}).encode()
    a = app()
    status, out = call(a, "POST", "/v1/plans", body, header("POST", "/v1/plans", body))
    assert status == 201 and out == {"id": PLAN_ID}
    tampered = json.dumps({"brand": "wtz"}).encode()
    assert call(a, "POST", "/v1/plans", tampered, header("POST", "/v1/plans", body))[0] == 401


def test_replay_rejected():
    a = app()
    h = header("GET", "/v1/meta")
    assert call(a, "GET", "/v1/meta", headers=h)[0] == 200
    assert call(a, "GET", "/v1/meta", headers=h)[0] == 401


def test_routes_dispatch_with_ids():
    svc = StubService()
    a = app(service=svc)
    path = f"/v1/plans/{PLAN_ID}/transfers/12/reject"
    body = b'{"row_version":1,"reason":"x"}'
    assert call(a, "POST", path, body, header("POST", path, body))[0] == 200
    name, args = svc.calls[-1]
    assert name == "decide_transfer" and args[1:4] == (PLAN_ID, "12", "reject")
    for suffix in ("calculate", "resume-posting"):
        path = f"/v1/plans/{PLAN_ID}/{suffix}"
        body = b'{"row_version":1}'
        assert call(a, "POST", path, body, header("POST", path, body))[0] == 202
    assert svc.calls[-1][0] == "resume_posting"


def test_api_error_and_internal_error_mapping():
    a = app()
    path = f"/v1/plans/{PLAN_ID}/submit"
    status, body = call(a, "POST", path, b"{}", header("POST", path, b"{}"))
    assert status == 409 and body["error"]["code"] == "stale_calculation"
    path = f"/v1/plans/{PLAN_ID}/approve"
    status, body = call(a, "POST", path, b"{}", header("POST", path, b"{}"))
    text = json.dumps(body)
    assert status == 500 and body["error"]["code"] == "internal"
    assert "SELECT" not in text and "esb.co.id" not in text and "token" not in text


def test_not_found_and_method_not_allowed():
    a = app()
    assert call(a, "GET", "/v1/unknown")[0] == 404
    assert call(a, "GET", "/v2/x")[0] == 404
    assert call(a, "DELETE", "/v1/plans")[0] == 405
    assert call(a, "GET", "/v1/plans/not-a-uuid")[0] == 404


def test_invalid_json_body():
    a = app()
    for body in (b"{bad", b"[1,2]", b"\xff"):
        assert call(a, "POST", "/v1/plans", body, header("POST", "/v1/plans", body))[0] == 400


def test_rate_limit_mutations_per_actor():
    store = SessionStore(clock=lambda: NOW)
    store.add(session())
    store.add(session(sid=SID2, actor_id=1000002))
    a = app(limiter=RateLimiter(limit=2), sessions=store)
    path = f"/v1/plans/{PLAN_ID}/calculate"
    body = b'{"row_version":1}'
    results = [call(a, "POST", path, body, header("POST", path, body))[0] for _ in range(3)]
    assert results == [202, 202, 429]
    assert call(a, "GET", "/v1/meta", headers=header("GET", "/v1/meta"))[0] == 200  # GET tidak dibatasi
    assert call(a, "POST", path, body, header("POST", path, body, sid=SID2))[0] == 202


def test_route_table_covers_contract():
    names = {r.name for r in ROUTES}
    assert {
        "session_create",
        "session_get",
        "session_delete",
        "meta",
        "list_pack_rules",
        "put_pack_rule",
        "list_plans",
        "create_plan",
        "get_plan",
        "put_demand",
        "import_pr",
        "calculate",
        "get_job",
        "decide_suggestion",
        "submit",
        "decide_transfer",
        "approve",
        "return_to_draft",
        "post_preview",
        "post",
        "cancel",
        "resume_posting",
        "resend",
        "search_boms",
    } == names


def test_rate_limiter_window():
    rl = RateLimiter(limit=1, window=60)
    assert rl.allow(1, 0) and not rl.allow(1, 30) and rl.allow(1, 61)
