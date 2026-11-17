"""Klien HTTP ESB: guard GET-only, login/401, retry 429/5xx, budget, token bucket, tanpa bocor kredensial."""

import io
import json
import urllib.error
from collections.abc import Mapping
from email.message import Message
from typing import Any

import pytest

from efn_esb.http import (
    DEFAULT_MAX_RESPONSE_BYTES,
    ERROR_BODY_MAX_BYTES,
    MIB,
    CallBudgetExceeded,
    EsbAuthError,
    EsbClient,
    EsbError,
    EsbHttpError,
    EsbProtocolError,
    EsbResponseTooLarge,
    HttpResponse,
    MethodNotAllowed,
    NoRedirect,
    TokenBucket,
    UrllibTransport,
    backoff_delay,
    check_request,
    content_length,
    read_limited,
)

BASE = "https://services.esb.co.id/core"
PASSWORD = "sintetis-Rahasia-123"
TOKEN = "jwt-sintetis-abc"


def ok(result: Any) -> HttpResponse:
    return HttpResponse(200, json.dumps({"status": "ok", "result": result}).encode(), {})


def login_ok(token: str = TOKEN) -> HttpResponse:
    return ok({"accessToken": token})


class FakeTransport:
    def __init__(self, responses: list[HttpResponse | Exception]) -> None:
        self.responses = list(responses)
        self.sent: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def send(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float
    ) -> HttpResponse:
        self.sent.append((method, url, dict(headers), body))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class NoBucket(TokenBucket):
    def __init__(self) -> None:
        super().__init__(1000.0)

    def acquire(self) -> None:
        return None


def make(transport: FakeTransport, **kwargs: Any) -> tuple[EsbClient, list[float]]:
    sleeps: list[float] = []
    client = EsbClient(
        BASE, "user-sintetis", PASSWORD, transport, NoBucket(), sleep=sleeps.append, jitter=lambda: 0.0, **kwargs
    )
    return client, sleeps


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_request_rejects_non_get_before_network(method: str) -> None:
    transport = FakeTransport([])
    client, _ = make(transport)
    with pytest.raises(MethodNotAllowed):
        client.request(method, "/product")
    assert transport.sent == []


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("DELETE", "/product/1"),
        ("POST", "/product"),
        ("GET", "/auth/login"),
        ("GET", "/product/bom/push"),
        ("GET", "/product/import"),
        ("GET", "/product/authorize"),
        ("GET", "/../admin"),
        ("GET", "product"),
        ("GET", "/product?x=1"),
        ("GET", "//evil.example/x"),
    ],
)
def test_check_request_negative(method: str, path: str) -> None:
    with pytest.raises(MethodNotAllowed):
        check_request(method, path)


def test_check_request_allows_get_and_login_post() -> None:
    check_request("GET", "/report/sales-menu-cogs")
    check_request("GET", "/production/simple-manufacturing/SM%2F2026%2F001")
    check_request("POST", "/auth/login")


def test_private_send_guard_blocks_other_methods() -> None:
    transport = FakeTransport([])
    client, _ = make(transport)
    with pytest.raises(MethodNotAllowed):
        client._send("DELETE", "/product", "", None, None)
    assert transport.sent == []


def test_login_then_get_with_bearer_and_query() -> None:
    transport = FakeTransport([login_ok(), ok({"count": 1, "data": [{"a": 1}]})])
    client, _ = make(transport)
    result = client.get("/report/sales-menu-cogs", {"salesDate": "2026-11-16", "branchID": 4, "skip": None})
    assert result == {"count": 1, "data": [{"a": 1}]}
    method, url, headers, body = transport.sent[0]
    assert (method, url) == ("POST", f"{BASE}/auth/login")
    assert json.loads(body or b"{}") == {"username": "user-sintetis", "password": PASSWORD}
    method, url, headers, body = transport.sent[1]
    assert method == "GET" and body is None
    assert url == f"{BASE}/report/sales-menu-cogs?salesDate=2026-11-16&branchID=4"
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert client.calls == 2


def test_401_relogin_once_then_success() -> None:
    transport = FakeTransport([login_ok("t1"), HttpResponse(401, b"", {}), login_ok("t2"), ok([])])
    client, _ = make(transport)
    assert client.get("/product") == []
    assert transport.sent[3][2]["Authorization"] == "Bearer t2"


def test_401_twice_raises_auth_error() -> None:
    transport = FakeTransport([login_ok("t1"), HttpResponse(401, b"", {}), login_ok("t2"), HttpResponse(401, b"", {})])
    client, _ = make(transport)
    with pytest.raises(EsbAuthError):
        client.get("/product")


def test_login_rejected_message_has_no_secret() -> None:
    transport = FakeTransport([HttpResponse(401, json.dumps({"password": PASSWORD}).encode(), {})])
    client, _ = make(transport)
    with pytest.raises(EsbAuthError) as info:
        client.get("/product")
    assert PASSWORD not in str(info.value)
    assert "user-sintetis" not in str(info.value)


def test_login_without_token() -> None:
    transport = FakeTransport([ok({"refresh": "x"})])
    client, _ = make(transport)
    with pytest.raises(EsbAuthError):
        client.login()


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_retry_then_success_with_backoff(status: int) -> None:
    transport = FakeTransport([login_ok(), HttpResponse(status, b"", {}), HttpResponse(status, b"", {}), ok([1])])
    client, sleeps = make(transport, backoff_base=1.0)
    assert client.get("/product") == [1]
    assert sleeps == [1.0, 2.0]


def test_retry_exhausted_raises_http_error() -> None:
    transport = FakeTransport([login_ok()] + [HttpResponse(503, b"", {})] * 6)
    client, sleeps = make(transport, max_retries=5)
    with pytest.raises(EsbHttpError) as info:
        client.get("/report/sales-menu-cogs", {"salesDate": "2026-11-16"})
    assert info.value.status == 503
    assert len(sleeps) == 5
    assert "salesDate" not in str(info.value)  # tanpa query di pesan


def test_max_retries_capped_at_five() -> None:
    transport = FakeTransport([login_ok()] + [HttpResponse(500, b"", {})] * 6)
    client, sleeps = make(transport, max_retries=99)
    with pytest.raises(EsbHttpError):
        client.get("/product")
    assert len(sleeps) == 5


def test_retry_after_respected() -> None:
    transport = FakeTransport([login_ok(), HttpResponse(429, b"", {"Retry-After": "7"}), ok([])])
    client, sleeps = make(transport)
    client.get("/product")
    assert sleeps == [7.0]


def test_other_4xx_not_retried() -> None:
    transport = FakeTransport([login_ok(), HttpResponse(400, b'{"status":"fail"}', {})])
    client, sleeps = make(transport)
    with pytest.raises(EsbHttpError) as info:
        client.get("/report/sales-menu-cogs-detail", {"limit": 50})
    assert info.value.status == 400 and sleeps == []


def test_redirect_status_is_error_not_followed() -> None:
    transport = FakeTransport([login_ok(), HttpResponse(302, b"", {"Location": "https://evil.example/"})])
    client, _ = make(transport)
    with pytest.raises(EsbHttpError):
        client.get("/product")
    assert len(transport.sent) == 2
    assert NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://evil.example/") is None


def test_network_error_retried_and_message_sanitised() -> None:
    error = OSError(f"gagal {BASE}/product?salesDate=2026-11-16 token={TOKEN}")
    transport = FakeTransport([login_ok()] + [error] * 3)
    client, sleeps = make(transport, max_retries=2)
    with pytest.raises(EsbError) as info:
        client.get("/product")
    assert TOKEN not in str(info.value) and "salesDate" not in str(info.value)
    assert info.value.__cause__ is None and info.value.__suppress_context__
    assert len(sleeps) == 2


def test_envelope_fail_and_non_json() -> None:
    transport = FakeTransport(
        [login_ok(), HttpResponse(200, b'{"status":"fail","code":"EC03100032"}', {}), HttpResponse(200, b"<html>", {})]
    )
    client, _ = make(transport)
    with pytest.raises(EsbProtocolError):
        client.get("/product")
    with pytest.raises(EsbProtocolError):
        client.get("/product")


def test_call_budget_exceeded_stops_before_send() -> None:
    transport = FakeTransport([login_ok(), ok([]), ok([])])
    client, _ = make(transport, max_calls=2)
    client.get("/product")
    with pytest.raises(CallBudgetExceeded):
        client.get("/product")
    assert len(transport.sent) == 2
    client.reset_budget(5)
    assert client.calls == 0


def test_repr_hides_secrets() -> None:
    transport = FakeTransport([login_ok()])
    client, _ = make(transport)
    client.login()
    assert PASSWORD not in repr(client) and TOKEN not in repr(client) and "user-sintetis" not in repr(client)


def test_urllib_transport_rejects_plain_http() -> None:
    with pytest.raises(MethodNotAllowed):
        UrllibTransport().send("GET", "http://services.esb.co.id/core/product", {}, None, 5)


def test_token_bucket_rate() -> None:
    now = [0.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    bucket = TokenBucket(2.0, capacity=1.0, clock=lambda: now[0], sleep=sleep)
    for _ in range(5):
        bucket.acquire()
    assert sleeps == [0.5, 0.5, 0.5, 0.5]
    assert now[0] == pytest.approx(2.0)


def test_token_bucket_invalid_rate() -> None:
    with pytest.raises(ValueError):
        TokenBucket(0)


# ---------------------------------------------------------------- SEC-187: batas ukuran respons


class CountingStream:
    """Stream sintetis yang mencatat jumlah byte yang benar-benar dibaca."""

    def __init__(self, data: bytes) -> None:
        self._buf = io.BytesIO(data)
        self.read_bytes = 0
        self.read_calls = 0

    def read(self, amt: int = -1) -> bytes:
        self.read_calls += 1
        chunk = self._buf.read(amt)
        self.read_bytes += len(chunk)
        return chunk


def test_read_limited_within_and_at_limit() -> None:
    assert read_limited(CountingStream(b"abc"), {}, 10) == b"abc"
    assert read_limited(CountingStream(b"x" * 10), {"Content-Length": "10"}, 10) == b"x" * 10


def test_read_limited_rejects_body_over_limit_reading_at_most_limit_plus_one() -> None:
    stream = CountingStream(b"x" * 1000)
    with pytest.raises(EsbResponseTooLarge):
        read_limited(stream, {}, 100)
    assert stream.read_bytes == 101


def test_read_limited_rejects_content_length_before_reading() -> None:
    stream = CountingStream(b"x" * 10)
    with pytest.raises(EsbResponseTooLarge):
        read_limited(stream, {"Content-Length": str(64 * MIB)}, 10)
    assert stream.read_calls == 0


def test_read_limited_invalid_content_length_still_capped() -> None:
    assert content_length({"Content-Length": "abc"}) is None
    assert content_length({"Content-Length": "-5"}) is None
    assert content_length(None) is None and content_length({}) is None
    assert content_length({"Content-Length": " 42 "}) == 42
    with pytest.raises(EsbResponseTooLarge):
        read_limited(CountingStream(b"x" * 50), {"Content-Length": "abc"}, 10)
    # Content-Length berbohong (kecil) tidak meloloskan body besar.
    with pytest.raises(EsbResponseTooLarge):
        read_limited(CountingStream(b"x" * 50), {"Content-Length": "5"}, 10)


def test_read_limited_non_strict_truncates() -> None:
    assert read_limited(CountingStream(b"x" * 50), {"Content-Length": "999999"}, 10, strict=False) == b"x" * 10
    with pytest.raises(ValueError):
        read_limited(CountingStream(b""), {}, 0)


class FakeHttpResponse:
    def __init__(self, status: int, body: bytes, headers: dict[str, str]) -> None:
        self.status = status
        self.stream = CountingStream(body)
        self.headers = Message()
        for key, value in headers.items():
            self.headers[key] = value

    def read(self, amt: int = -1) -> bytes:
        return self.stream.read(amt)

    def __enter__(self) -> "FakeHttpResponse":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None


class FakeOpener:
    def __init__(self, outcome: FakeHttpResponse | Exception) -> None:
        self.outcome = outcome

    def open(self, request: Any, timeout: float) -> FakeHttpResponse:
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def transport_with(outcome: FakeHttpResponse | Exception, max_bytes: int = MIB) -> UrllibTransport:
    transport = UrllibTransport(max_response_bytes=max_bytes)
    transport._opener = FakeOpener(outcome)  # type: ignore[assignment]
    return transport


URL = f"{BASE}/report/sales-menu-cogs?salesDate=2026-11-16&branchID=4"


def test_transport_reads_body_under_limit() -> None:
    fake = FakeHttpResponse(200, b'{"status":"ok"}', {"Content-Type": "application/json"})
    response = transport_with(fake).send("GET", URL, {}, None, 5)
    assert response.status == 200 and response.body == b'{"status":"ok"}'
    assert response.headers["Content-Type"] == "application/json"


@pytest.mark.parametrize("headers", [{}, {"Content-Length": str(2 * MIB)}])
def test_transport_rejects_oversized_body_without_leaking_url(headers: dict[str, str]) -> None:
    fake = FakeHttpResponse(200, b"x" * (MIB + 10), headers)
    with pytest.raises(EsbResponseTooLarge) as info:
        transport_with(fake).send("GET", URL, {"Authorization": f"Bearer {TOKEN}"}, None, 5)
    message = str(info.value)
    assert "salesDate" not in message and "services.esb.co.id" not in message and TOKEN not in message
    assert info.value.__cause__ is None
    assert fake.stream.read_bytes <= MIB + 1


def test_transport_error_body_truncated_status_kept() -> None:
    hdrs = Message()
    hdrs["Retry-After"] = "3"
    body = io.BytesIO(b"e" * (ERROR_BODY_MAX_BYTES * 4))
    error = urllib.error.HTTPError(URL, 503, "Service Unavailable", hdrs, body)
    response = transport_with(error).send("GET", URL, {}, None, 5)
    assert response.status == 503 and len(response.body) == ERROR_BODY_MAX_BYTES
    assert response.headers["Retry-After"] == "3"


def test_transport_max_bytes_validated_and_default() -> None:
    with pytest.raises(ValueError):
        UrllibTransport(max_response_bytes=0)
    assert DEFAULT_MAX_RESPONSE_BYTES == 32 * MIB


def test_client_does_not_retry_oversized_response() -> None:
    transport = FakeTransport([login_ok(), EsbResponseTooLarge("respons ESB melebihi batas ukuran"), ok([])])
    client, sleeps = make(transport, max_retries=5)
    with pytest.raises(EsbResponseTooLarge):
        client.get("/report/sales-menu-cogs", {"salesDate": "2026-11-16"})
    assert sleeps == [] and len(transport.sent) == 2


def test_backoff_delay() -> None:
    assert backoff_delay(0, None, 1.0, 30.0, 0.0) == 1.0
    assert backoff_delay(10, None, 1.0, 30.0, 0.25) == 30.25
    assert backoff_delay(0, "120", 1.0, 30.0, 0.0) == 60.0
    assert backoff_delay(1, "abc", 1.0, 30.0, 0.0) == 2.0
