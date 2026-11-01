import json

import pytest
import requests

from efn_text2sql.llm import (
    FallbackLlm,
    LlmUnavailableError,
    NineRouterLlm,
    OllamaLlm,
    validate_ninerouter_url,
)


class FakeResponse:
    def __init__(self, status: int, body: object, raw: bytes | None = None) -> None:
        self.status_code = status
        self.content = raw if raw is not None else json.dumps(body).encode()

    def json(self) -> object:
        return json.loads(self.content)


class FakeSession:
    def __init__(self, response: FakeResponse | Exception) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []
        self.trust_env = True
        self.verify: object = None
        self.cert: object = None

    def post(self, url: str, **kw: object) -> FakeResponse:
        self.calls.append({"url": url, **kw})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


ANTHROPIC_OK = {"content": [{"type": "text", "text": "```sql\nSELECT 1\n```"}]}


def nr(session: FakeSession) -> NineRouterLlm:
    return NineRouterLlm("https://9router.bimacreative.tech", "k" * 30, "cc/m", 10, session=session)  # type: ignore[arg-type]


def test_ninerouter_payload_headers_and_text() -> None:
    s = FakeSession(FakeResponse(200, ANTHROPIC_OK))
    assert nr(s).complete("halo") == "```sql\nSELECT 1\n```"
    call = s.calls[0]
    assert call["url"] == "https://9router.bimacreative.tech/v1/messages"
    assert call["headers"]["x-api-key"] == "k" * 30  # type: ignore[index]
    assert call["json"]["messages"] == [{"role": "user", "content": "halo"}]  # type: ignore[index]
    assert call["allow_redirects"] is False
    assert s.trust_env is False and s.verify is True


def test_ninerouter_event_stream_body() -> None:
    raw = json.dumps(ANTHROPIC_OK).encode() + b"data: [DONE]"
    s = FakeSession(FakeResponse(200, None, raw=raw))
    assert "SELECT 1" in nr(s).complete("x")


@pytest.mark.parametrize(
    "resp",
    [
        FakeResponse(403, {}),
        FakeResponse(500, {"error": "kuota akun x"}),
        FakeResponse(200, None, raw=b"bukan-json"),
        FakeResponse(200, {"content": "x"}),
        FakeResponse(200, None, raw=b"{" + b"a" * 70000),
        requests.ConnectTimeout("t"),
    ],
)
def test_ninerouter_errors_are_unavailable_without_body_or_key(resp: object) -> None:
    s = FakeSession(resp)  # type: ignore[arg-type]
    with pytest.raises(LlmUnavailableError) as info:
        nr(s).complete("x")
    assert "kuota" not in str(info.value)
    assert "k" * 30 not in str(info.value)


def test_repr_hides_key() -> None:
    assert "k" * 30 not in repr(nr(FakeSession(FakeResponse(200, {}))))


@pytest.mark.parametrize(
    "url",
    [
        "http://9router.bimacreative.tech",
        "https://evil.example",
        "https://u:p@9router.bimacreative.tech",
        "https://9router.bimacreative.tech:8443",
        "https://9router.bimacreative.tech/v1",
    ],
)
def test_url_allowlist(url: str) -> None:
    with pytest.raises(ValueError):
        validate_ninerouter_url(url)


def test_placeholder_key_rejected() -> None:
    with pytest.raises(ValueError):
        NineRouterLlm("https://9router.bimacreative.tech", "disabled", "m", 5)


def test_ollama_payload_and_mtls() -> None:
    s = FakeSession(FakeResponse(200, {"response": "SELECT 1"}))
    llm = OllamaLlm("https://ollama:11434", "qwen", 5, ca_file="/ca", client_cert=("c", "k"), session=s)  # type: ignore[arg-type]
    assert llm.complete("p") == "SELECT 1"
    assert s.calls[0]["url"] == "https://ollama:11434/api/generate"
    assert s.verify == "/ca" and s.cert == ("c", "k") and s.trust_env is False


class Stub:
    def __init__(self, name: str, fail: bool) -> None:
        self.name = name
        self.fail = fail

    def complete(self, prompt: str) -> str:
        if self.fail:
            raise LlmUnavailableError(f"{self.name}:http_429")
        return "ok"


def test_fallback_order_and_engine() -> None:
    a = FallbackLlm([Stub("ninerouter:m", True), Stub("ollama:q", False)]).answer("p")
    assert (a.text, a.engine) == ("ok", "ollama:q")
    a = FallbackLlm([Stub("ninerouter:m", False), Stub("ollama:q", False)]).answer("p")
    assert a.engine == "ninerouter:m"
    with pytest.raises(LlmUnavailableError):
        FallbackLlm([Stub("a", True), Stub("b", True)]).answer("p")
