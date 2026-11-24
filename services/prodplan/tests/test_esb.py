import json
import logging

import pytest

from efn_prodplan.esb import (
    EsbClient,
    EsbForbiddenCall,
    EsbHttpError,
    EsbInvalidCredentials,
    EsbNotSent,
    EsbRejected,
    EsbSentUnknown,
    EsbTokenRejected,
    EsbUnavailable,
    HttpResponse,
    HttpsTransport,
    TokenBucket,
    classify_call,
    encode_doc_number,
    jwt_claims,
)
from efn_prodplan.logs import JsonFormatter

STG = "https://stg7.esb.co.id/core-stg"
USER = "user-for-test"
PASSWORD = "pw-for-test-only"
TOKEN = "tok-for-test-123"
REFRESH = "refresh-for-test-456"


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def send(self, method, url, headers, body, timeout):
        self.calls.append((method, url, dict(headers), body))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ok(result, status=200):
    return HttpResponse(status, json.dumps({"status": "ok", "code": 200, "result": result}).encode(), {})


def err(status, code="E1"):
    return HttpResponse(status, json.dumps({"status": "fail", "code": code, "message": "x"}).encode(), {})


def make(transport, *, allow_write=False, mode="actual", token=TOKEN, **kw):
    return EsbClient(
        STG,
        token,
        transport,
        TokenBucket(100.0),
        mfg_mode=mode,
        allow_write=allow_write,
        sleep=lambda _s: None,
        **kw,
    )


# ---------------------------------------------------------------- allowlist (murni)
@pytest.mark.parametrize(
    ("method", "path", "template"),
    [
        ("POST", "/auth/login", "/auth/login"),
        ("POST", "/auth/logout", "/auth/logout"),
        ("GET", "/user/access", "/user/access"),
        ("GET", "/branch/list/user", "/branch/list/user"),
        ("GET", "/product/bom", "/product/bom"),
        ("GET", "/product", "/product"),
        ("GET", "/product/123", "/product/{productID}"),
        ("GET", "/product/bom/detail/55", "/product/bom/detail/{bomID}"),
        ("GET", "/product/bom/12", "/product/bom/{bomID}"),
        ("GET", "/product/branch-stock", "/product/branch-stock"),
        ("GET", "/location/branch/4", "/location/branch/{branchID}"),
        ("GET", "/purchase/purchase-request", "/purchase/purchase-request"),
        ("GET", "/purchase/purchase-request/PR2026110001", "/purchase/purchase-request/{num}"),
        ("GET", "/simple-transfer/ST202611240001", "/simple-transfer/{num}"),
        ("GET", "/inventory/goods-transfer-request/TR1", "/inventory/goods-transfer-request/{num}"),
        ("GET", "/production/simple-manufacturing/SM202611240001%20-%201", "/production/simple-manufacturing/{num}"),
        ("GET", "/production/simple-manufacturing", "/production/simple-manufacturing"),
    ],
)
def test_allowed_reads(method, path, template):
    assert classify_call(method, path, mfg_mode="actual", allow_write=False) == template


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/simple-transfer"),
        ("POST", "/inventory/goods-transfer-request"),
        ("POST", "/production/simple-manufacturing/assembly-actual"),
        ("DELETE", "/simple-transfer/ST1"),
        ("DELETE", "/inventory/goods-transfer-request/TR1"),
        ("DELETE", "/production/simple-manufacturing/SM202611240001%20-%201"),
    ],
)
def test_writes_allowed_only_with_allow_write(method, path):
    assert classify_call(method, path, mfg_mode="actual", allow_write=True)
    with pytest.raises(EsbForbiddenCall):
        classify_call(method, path, mfg_mode="actual", allow_write=False)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/simple-transfer/ST1/authorize"),
        ("GET", "/simple-transfer/ST1/authorize"),
        ("POST", "/simple-transfer/authorize"),
        ("GET", "/simple-transfer/authorize"),
        ("POST", "/production/simple-manufacturing/assembly"),  # mode actual: hanya assembly-actual
        ("GET", "/production/simple-manufacturing/assembly-actual"),
        ("POST", "/inventory/goods-transfer-request/import"),
        ("POST", "/production/simple-manufacturing/import"),
        ("GET", "/sales/push"),
        ("PUT", "/simple-transfer"),
        ("PATCH", "/simple-transfer/ST1"),
        ("DELETE", "/product/bom/1"),
        ("DELETE", "/purchase/purchase-request/PR1"),
        ("POST", "/purchase/purchase-request"),
        ("GET", "/product/../auth/login"),
        ("GET", "/simple-transfer/..%2F1"),
        ("GET", "//product"),
        ("GET", "/product?x=1"),
        ("GET", "/auth/login"),
        ("GET", "/simple-transfer/abc"),
        ("GET", "/simple-transfer/ST%2"),
        ("GET", "/simple-transfer/ST 1"),
        ("GET", "/product/bom/detail/abc"),
        ("GET", "/product/bom/abc"),
        ("DELETE", "/simple-transfer/ST1/authorize"),
        ("PATCH", "/simple-transfer/ST1/authorize"),
        ("GET", "/auth/me"),
        ("POST", "/auth/refresh"),
        ("GET", "/auth/logout"),
        ("POST", "/user/access"),
    ],
)
def test_forbidden_calls(method, path):
    with pytest.raises(EsbForbiddenCall):
        classify_call(method, path, mfg_mode="actual", allow_write=True)


def test_standard_mode_switches_production_endpoint():
    path_std = "/production/simple-manufacturing/assembly"
    assert classify_call("POST", path_std, mfg_mode="standard", allow_write=True) == path_std
    with pytest.raises(EsbForbiddenCall):
        classify_call("POST", "/production/simple-manufacturing/assembly-actual", mfg_mode="standard", allow_write=True)


def test_encode_doc_number():
    assert encode_doc_number("SM202611240001 - 1") == "SM202611240001%20-%201"
    assert encode_doc_number("ST/2026/1") == "ST%2F2026%2F1"
    for bad in ("x;rm", "", "a" * 41, "abc", "SM1?x=1", "SM1#", "ŠM1"):
        with pytest.raises(EsbForbiddenCall):
            encode_doc_number(bad)


def test_client_rejects_disallowed_host_and_mode():
    with pytest.raises(EsbForbiddenCall):
        evil = "https://evil.example/core"
        EsbClient(evil, TOKEN, FakeTransport([]), TokenBucket(1), mfg_mode="actual", allow_write=False)
    with pytest.raises(EsbForbiddenCall):
        EsbClient(STG, TOKEN, FakeTransport([]), TokenBucket(1), mfg_mode="x", allow_write=False)


def test_forbidden_call_never_opens_socket():
    transport = FakeTransport([])
    client = make(transport)
    with pytest.raises(EsbForbiddenCall):
        client.get("/sales/push")
    with pytest.raises(EsbForbiddenCall):
        client.post_doc("simple_transfer", {"a": 1})  # allow_write=False
    with pytest.raises(EsbForbiddenCall):
        client.delete_doc("simple_transfer", "ST1")
    with pytest.raises(EsbForbiddenCall):
        client.get_doc("simple_transfer", "../1")
    assert transport.calls == []


# ---------------------------------------------------------------- baca
def test_get_uses_user_token_bearer_and_query_without_login():
    transport = FakeTransport([ok({"page": 1, "limit": 2, "count": 1, "data": [{"bomID": 1}]})])
    client = make(transport)
    rows = client.get_all("/product/bom", {"bomTypeID": 1}, limit=2)
    assert rows == [{"bomID": 1}]
    (get,) = transport.calls  # tidak ada POST /auth/login (tanpa akun service)
    assert get[1].startswith(STG + "/product/bom?") and "bomTypeID=1" in get[1] and "page=1" in get[1]
    assert get[2]["Authorization"] == f"Bearer {TOKEN}"


def test_get_all_handles_null_data():
    transport = FakeTransport([ok({"page": 1, "limit": 50, "count": 0, "data": None})])
    assert make(transport).get_all("/simple-transfer", {}) == []


def test_get_401_evicts_session_and_never_relogs():
    evicted = []
    transport = FakeTransport([err(401)])
    client = make(transport, on_unauthorized=lambda: evicted.append(1))
    with pytest.raises(EsbTokenRejected):
        client.get("/location/branch/4")
    assert evicted == [1] and len(transport.calls) == 1


def test_client_without_token_never_calls_esb():
    transport = FakeTransport([])
    with pytest.raises(EsbTokenRejected):
        make(transport, token=None).get("/product/bom")
    assert transport.calls == []


def test_get_retries_on_503_and_network():
    transport = FakeTransport([err(503), EsbNotSent("x"), ok({"x": 1})])
    assert make(transport).get("/product/bom") == {"x": 1}


def test_get_doc_encodes_number_and_404_is_none():
    transport = FakeTransport([ok({"statusID": 3}), err(404)])
    client = make(transport)
    assert client.get_doc("simple_manufacturing", "SM202611240001 - 1") == {"statusID": 3}
    assert transport.calls[0][1] == STG + "/production/simple-manufacturing/SM202611240001%20-%201"
    assert client.get_doc("simple_transfer", "ST1") is None


def test_call_budget():
    from efn_prodplan.esb import CallBudgetExceeded

    client = make(FakeTransport([ok({})]), max_calls=1)
    client.get("/product/bom")
    with pytest.raises(CallBudgetExceeded):
        client.get("/product/bom")


# ---------------------------------------------------------------- login / logout (§30.17.3)
def _jwt(claims):
    import base64

    def seg(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{seg({'alg': 'HS256'})}.{seg(claims)}.c2lnLXJla2Fhbg"


LOGIN_RESULT = {
    "username": "planner.uji",
    "fullName": "Planner Uji",
    "companyID": 1,
    "companyCode": "CORP-UJI",
    "companyName": "PT Rekaan",
    "accessToken": TOKEN,
    "refreshToken": REFRESH,
    "flagActive": 1,
    "logInfo": {"logID": 1, "loginTime": "x", "logoutTime": None, "username": "planner.uji"},
}


def test_login_once_returns_only_needed_fields_and_drops_refresh_token():
    transport = FakeTransport([ok(LOGIN_RESULT)])
    result = make(transport, token=None).login(USER, PASSWORD)
    assert (result.access_token, result.company_code, result.flag_active) == (TOKEN, "CORP-UJI", True)
    assert not hasattr(result, "refresh_token") and REFRESH not in repr(result) and TOKEN not in repr(result)
    (call,) = transport.calls
    assert call[0] == "POST" and call[1] == STG + "/auth/login" and "Authorization" not in call[2]
    assert json.loads(call[3]) == {"username": USER, "password": PASSWORD}


@pytest.mark.parametrize(
    ("flag", "active"), [(1, True), (True, True), (0, False), (False, False), (None, False), ("1", False)]
)
def test_login_flag_active_accepts_int_one_as_sent_by_esb(flag, active):
    result = make(FakeTransport([ok({**LOGIN_RESULT, "flagActive": flag})]), token=None).login(USER, PASSWORD)
    assert result.flag_active is active


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (err(400, "EC03100032"), EsbInvalidCredentials),  # password salah (uji staging)
        (err(401), EsbInvalidCredentials),
        (err(403), EsbInvalidCredentials),
        (err(200, "EC1"), EsbInvalidCredentials),  # envelope gagal
        (err(500), EsbUnavailable),
        (err(503), EsbUnavailable),
        (EsbNotSent("dns"), EsbUnavailable),
        (EsbSentUnknown("timeout"), EsbUnavailable),
        (HttpResponse(200, b"<html>", {}), EsbUnavailable),
    ],
)
def test_login_failures_are_classified_and_never_retried(response, expected):
    transport = FakeTransport([response, ok(LOGIN_RESULT)])
    with pytest.raises(expected):
        make(transport, token=None).login(USER, PASSWORD)
    assert len(transport.calls) == 1


def test_logout_best_effort_with_short_timeout():
    seen = []

    class T(FakeTransport):
        def send(self, method, url, headers, body, timeout):
            seen.append(timeout)
            return super().send(method, url, headers, body, timeout)

    transport = T([EsbSentUnknown("timeout")])
    make(transport).logout()  # galat diabaikan
    assert transport.calls[0][0] == "POST" and transport.calls[0][1] == STG + "/auth/logout"
    assert transport.calls[0][3] == b"{}" and seen == [5.0]
    make(FakeTransport([]), token=None).logout()  # tanpa token: tidak ada panggilan


def test_jwt_claims_without_signature_check():
    assert jwt_claims(_jwt({"exp": 123, "userRoleID": 7}))["userRoleID"] == 7
    for bad in ("", "abc", "a.b", "a..c", "a.WzFd.c"):  # WzFd = "[1]" (bukan objek)
        assert jwt_claims(bad) == {}


# ---------------------------------------------------------------- tulis: tanpa retry otomatis
def test_post_doc_success_returns_number():
    transport = FakeTransport([ok({"simpleManufacturingNum": "SM202611240001 - 1"})])
    client = make(transport, allow_write=True)
    assert client.post_doc("simple_manufacturing", {"branchID": 4}) == "SM202611240001 - 1"
    method, url, headers, body = transport.calls[0]
    assert method == "POST" and url == STG + "/production/simple-manufacturing/assembly-actual"
    assert json.loads(body) == {"branchID": 4} and headers["Content-Type"] == "application/json"


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (EsbSentUnknown("timeout"), EsbSentUnknown),
        (EsbNotSent("refused"), EsbNotSent),
        (err(500), EsbSentUnknown),
        (err(502), EsbSentUnknown),
        (err(400, "VAL"), EsbRejected),
        (err(422), EsbRejected),
        (ok({"other": 1}), EsbSentUnknown),  # 200 tanpa nomor -> rekonsiliasi
        (HttpResponse(200, b"<html>", {}), EsbSentUnknown),
        (err(200, "FAIL"), EsbRejected),
    ],
)
def test_post_doc_failures_never_retry(response, expected):
    transport = FakeTransport([response])
    client = make(transport, allow_write=True)
    with pytest.raises(expected):
        client.post_doc("simple_transfer", {"a": 1})
    assert len(transport.calls) == 1


def test_write_401_is_token_rejected_without_relogin_or_second_post():
    for call_write in (
        lambda c: c.post_doc("simple_transfer", {"a": 1}),
        lambda c: c.delete_doc("simple_transfer", "ST1"),
    ):
        evicted = []
        transport = FakeTransport([err(401), ok({"simpleTransferNum": "ST1"})])
        client = make(transport, allow_write=True, on_unauthorized=lambda e=evicted: e.append(1))
        with pytest.raises(EsbTokenRejected):
            call_write(client)
        assert len(transport.calls) == 1 and evicted == [1]


def test_delete_doc_outcomes():
    transport = FakeTransport([ok(None), err(404), err(400)])
    client = make(transport, allow_write=True)
    assert client.delete_doc("simple_transfer", "ST1") == "deleted"
    assert client.delete_doc("simple_transfer", "ST1") == "not_found"
    with pytest.raises(EsbRejected):
        client.delete_doc("simple_transfer", "ST1")
    assert transport.calls[0][0] == "DELETE" and transport.calls[0][1] == STG + "/simple-transfer/ST1"


def test_transport_connect_failure_is_not_sent():
    transport = HttpsTransport(1024)
    with pytest.raises(EsbNotSent):
        transport.send("POST", "https://127.0.0.1:1/core-stg/simple-transfer", {}, b"{}", 2.0)


# ---------------------------------------------------------------- tanpa rahasia di log/exception
def test_logs_and_errors_have_no_secrets(caplog):
    caplog.set_level(logging.INFO, logger="efn_prodplan")
    transport = FakeTransport([err(418), ok(LOGIN_RESULT), err(400)])
    client = make(transport)
    with pytest.raises(EsbHttpError) as exc:
        client.get("/product/bom", {"productName": "querysecret"})
    anon = make(transport, token=None)
    result = anon.login(USER, PASSWORD)
    with pytest.raises(EsbInvalidCredentials) as exc2:
        anon.login(USER, PASSWORD)
    text = str(exc.value) + repr(client) + repr(result) + str(exc2.value) + repr(anon)
    formatted = "\n".join(JsonFormatter().format(r) for r in caplog.records)
    for secret in (PASSWORD, TOKEN, REFRESH, USER, "querysecret", "Bearer"):
        assert secret not in text
        assert secret not in formatted
    assert "esb_call" in formatted and "/product/bom" in formatted


def test_token_bucket_waits():
    now = [0.0]
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        now[0] += seconds

    bucket = TokenBucket(2.0, clock=lambda: now[0], sleep=sleep)
    bucket.acquire()
    bucket.acquire()
    bucket.acquire()
    assert slept and abs(slept[0] - 0.5) < 1e-9
