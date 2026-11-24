"""App HTTP (tanpa socket): gerbang disabled, asersi aktor, rate limit, galat tanpa detail internal."""

import itertools
import json

from efn_prodplan.actor import body_hash, make_header
from efn_prodplan.api import ROUTES, App, RateLimiter
from efn_prodplan.workflow import ApiError

KEY = ("ab" * 32).encode("ascii")
NOW = 1_795_000_000
PLAN_ID = "0b6f1c1e-1111-4222-8333-444455556666"
_counter = itertools.count()


class StubService:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def handler(*args):
            self.calls.append((name, args[1:]))
            if name == "submit":
                raise ApiError(409, "stale_calculation")
            if name == "approve":
                raise RuntimeError("SELECT * FROM secret_table; https://stg7.esb.co.id/core-stg?token=abc")
            if name == "create_plan":
                return {"id": PLAN_ID}
            return {"ok": name}

        return handler


def header(method, path, body=b"", **over):
    jti = f"{next(_counter):022d}"
    claims = {
        "aud": "prodplan",
        "bh": body_hash(body),
        "codes": ["PB"],
        "exp": NOW + 60,
        "iat": NOW,
        "jti": jti,
        "m": method,
        "name": "Planner",
        "p": path,
        "roles": ["pp_planner"],
        "sub": 42,
    }
    claims.update(over)
    return {"x-efn-actor": make_header(claims, KEY)}


def app(enabled=True, service=None, limiter=None, ready=True):
    return App(
        enabled=enabled,
        assert_key=KEY if enabled else None,
        service=service if service is not None else (StubService() if enabled else None),
        ready_check=lambda: ready,
        rate_limiter=limiter,
        clock=lambda: NOW,
    )


def call(a, method, path, body=b"", headers=None):
    status, out_headers, out = a.handle(method, path, headers or {}, body)
    return status, json.loads(out) if out_headers.get("Content-Type", "").startswith("application/json") else out


def test_health_without_actor():
    assert call(app(), "GET", "/healthz") == (200, {"status": "ok"})
    assert call(app(ready=False), "GET", "/readyz")[0] == 503
    assert call(app(enabled=False), "GET", "/readyz") == (200, {"status": "disabled"})
    status, text = call(app(), "GET", "/metrics")
    assert status == 200 and b"prodplan_esb_requests_total" in text


def test_disabled_returns_503_for_v1():
    status, body = call(app(enabled=False), "GET", "/v1/meta")
    assert status == 503 and body["error"]["code"] == "disabled"


def test_missing_or_bad_actor_is_401_without_detail():
    status, body = call(app(), "GET", "/v1/meta")
    assert status == 401 and body == {"error": {"code": "actor_invalid", "message": "Identitas pengguna tidak valid"}}
    h = header("GET", "/v1/meta")
    status, _ = call(app(), "GET", "/v1/plans", headers=h)  # p tidak cocok
    assert status == 401


def test_valid_get_and_query_binding():
    svc = StubService()
    a = app(service=svc)
    path = "/v1/plans?brand=pb&limit=50"
    status, body = call(a, "GET", path, headers=header("GET", path))
    assert status == 200 and body == {"ok": "list_plans"}
    assert svc.calls[0][1][0] == {"brand": "pb", "limit": "50"}
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
    status, _ = call(a, "POST", path, body, header("POST", path, body))
    assert status == 200
    name, args = svc.calls[-1]
    assert name == "decide_transfer" and args[:3] == (PLAN_ID, "12", "reject")
    path = f"/v1/plans/{PLAN_ID}/calculate"
    body = b'{"row_version":1}'
    assert call(a, "POST", path, body, header("POST", path, body))[0] == 202


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


def test_rate_limit_mutations_per_sub():
    a = app(limiter=RateLimiter(limit=2))
    path = f"/v1/plans/{PLAN_ID}/calculate"
    body = b'{"row_version":1}'
    results = [call(a, "POST", path, body, header("POST", path, body))[0] for _ in range(3)]
    assert results == [202, 202, 429]
    assert call(a, "GET", "/v1/meta", headers=header("GET", "/v1/meta"))[0] == 200  # GET tidak dibatasi
    other = header("POST", path, body, sub=43)
    assert call(a, "POST", path, body, other)[0] == 202


def test_route_table_covers_contract():
    names = {r.name for r in ROUTES}
    assert {
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
        "resend",
        "search_boms",
    } == names


def test_rate_limiter_window():
    rl = RateLimiter(limit=1, window=60)
    assert rl.allow(1, 0) and not rl.allow(1, 30) and rl.allow(1, 61)
