"""G37 (K25-01): gateway LLM 9router (Anthropic Messages) + fallback Ollama. Tanpa jaringan (session palsu)."""

import base64
import json
from pathlib import Path

import pytest
import requests

from efn_ocr import metrics
from efn_ocr.config import Settings
from efn_ocr.errors import VlmUnavailableError
from efn_ocr.llm import FallbackVlm, NineRouterVlm, build_vlm, extract_json_text, validate_ninerouter_url
from efn_ocr.vlm import PROMPT, VlmResult

KEY_UJI = "uji-kunci-9router-bukan-asli"


class FakeResponse:
    def __init__(self, status: int, body: object = None, content: bytes | None = None) -> None:
        self.status_code = status
        self._body = body
        if content is None:
            content = b"bukan-json" if isinstance(body, Exception) else json.dumps(body).encode()
        self.content = content

    def json(self) -> object:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeSession:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []
        self.trust_env = True
        self.verify: object = None

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item  # type: ignore[return-value]


def anthropic_body(text: str) -> dict[str, object]:
    return {
        "type": "message",
        "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": text}],
        "stop_reason": "end_turn",
    }


def client(session: FakeSession, **kw: object) -> NineRouterVlm:
    return NineRouterVlm(
        "https://9router.bimacreative.tech", KEY_UJI, "cc/claude-haiku-4-5-20251001", 30, session, **kw
    )


def test_payload_anthropic_messages_without_pii_and_headers() -> None:
    session = FakeSession(
        [FakeResponse(200, anthropic_body('```json\n{"product_name": "Indomie", "price": "Rp 3.500"}\n```'))]
    )
    result = client(session).extract(b"\xff\xd8jpeg")
    assert (result.product_name, result.price) == ("Indomie", 3500)
    call = session.calls[0]
    assert call["url"] == "https://9router.bimacreative.tech/v1/messages"
    assert call["allow_redirects"] is False
    headers = call["headers"]
    assert headers["x-api-key"] == KEY_UJI and headers["anthropic-version"] == "2023-06-01"
    body = call["json"]
    assert body["model"] == "cc/claude-haiku-4-5-20251001" and body["temperature"] == 0
    content = body["messages"][0]["content"]
    assert [c["type"] for c in content] == ["image", "text"]
    assert content[0]["source"] == {
        "type": "base64",
        "media_type": "image/jpeg",
        "data": base64.b64encode(b"\xff\xd8jpeg").decode(),
    }
    assert content[1]["text"] == PROMPT
    # Hanya prompt tetap + gambar: tidak ada nama/harga laporan, user, atau id.
    assert set(body) == {"model", "max_tokens", "temperature", "messages"}
    assert session.trust_env is False and session.verify is True


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (FakeResponse(403, {"error": "x"}), "ninerouter:http_403"),
        (FakeResponse(503, {"error": {"message": "quota"}}), "ninerouter:http_503"),
        (FakeResponse(200, ValueError("bad")), "ninerouter:invalid_body"),
        (FakeResponse(200, {"content": "teks"}), "ninerouter:invalid_shape"),
        (FakeResponse(200, anthropic_body("maaf, tidak bisa")), "invalid_json"),
        (FakeResponse(200, None, content=b"x" * (70 * 1024)), "ninerouter:response_too_large"),
        (requests.Timeout("lambat"), "ninerouter:Timeout"),
    ],
)
def test_errors_become_vlm_unavailable_without_body(response: object, reason: str) -> None:
    with pytest.raises(VlmUnavailableError) as exc:
        client(FakeSession([response])).extract(b"img")
    assert str(exc.value) == reason
    assert KEY_UJI not in str(exc.value)


def test_key_never_in_repr() -> None:
    assert KEY_UJI not in repr(client(FakeSession([])))


@pytest.mark.parametrize(
    "url",
    [
        "http://9router.bimacreative.tech",
        "https://evil.example.com",
        "https://9router.bimacreative.tech.evil.com",
        "https://user:pw@9router.bimacreative.tech",
        "https://9router.bimacreative.tech:8443",
        "https://9router.bimacreative.tech/v1",
    ],
)
def test_url_allowlist(url: str) -> None:
    with pytest.raises(ValueError):
        validate_ninerouter_url(url)


def test_url_allowlist_accepts_origin() -> None:
    assert validate_ninerouter_url("https://9router.bimacreative.tech/") == "https://9router.bimacreative.tech"


def test_extract_json_text() -> None:
    assert extract_json_text('Berikut:\n```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extract_json_text("tanpa json") == ""


class StubVlm:
    def __init__(self, name: str, result: VlmResult | None) -> None:
        self.name = name
        self.result = result
        self.calls = 0

    def extract(self, image_bytes: bytes) -> VlmResult:
        self.calls += 1
        if self.result is None:
            raise VlmUnavailableError(f"{self.name} mati")
        return self.result


def _count(provider: str, outcome: str) -> float:
    return metrics.LLM_PROVIDER_CALLS.labels(provider=provider, outcome=outcome)._value.get()


def test_fallback_to_ollama_when_ninerouter_fails() -> None:
    primary = StubVlm("ninerouter:cc/x", None)
    secondary = StubVlm("ollama:qwen", VlmResult("Teh", 5000, {"product_name": "Teh", "price": "5000"}))
    before = (_count("ninerouter", "unavailable"), _count("ollama", "ok"))
    result = FallbackVlm([primary, secondary]).extract(b"img")
    assert result.price == 5000 and result.raw["engine"] == "ollama:qwen"
    assert (primary.calls, secondary.calls) == (1, 1)
    assert _count("ninerouter", "unavailable") == before[0] + 1
    assert _count("ollama", "ok") == before[1] + 1


def test_primary_ok_skips_fallback() -> None:
    primary = StubVlm("ninerouter:cc/x", VlmResult("Kopi", 9000, {}))
    secondary = StubVlm("ollama:qwen", VlmResult("x", 1, {}))
    assert FallbackVlm([primary, secondary]).extract(b"img").raw["engine"] == "ninerouter:cc/x"
    assert secondary.calls == 0


def test_all_providers_fail() -> None:
    vlm = FallbackVlm([StubVlm("ninerouter:a", None), StubVlm("ollama:b", None)])
    with pytest.raises(VlmUnavailableError, match="semua penyedia gagal"):
        vlm.extract(b"img")
    assert vlm.name == "ninerouter:a+ollama:b"


# ---------------------------------------------------------------- config (fail-closed)
def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {
        "APP_ENV": "dev",
        "EFN_ALLOW_PLAINTEXT": "true",
        "AMQP_URL": "amqp://efn-ocr@rabbitmq:5672/efn",
        "MINIO_USE_SSL": "false",
        "PG_SSLMODE": "disable",
        "OLLAMA_URL": "http://ollama:11434",
    }
    env.update(extra)
    return env


def _key(tmp_path: Path, value: str = KEY_UJI) -> str:
    path = tmp_path / "ninerouter_api_key"
    path.write_text(value + "\n", encoding="utf-8")
    return str(path)


def test_default_provider_is_ollama_without_key(tmp_path: Path) -> None:
    s = Settings.from_env(_env(tmp_path))
    assert s.llm_provider == "ollama" and s.ninerouter_api_key == ""
    assert build_vlm(s).name.startswith("ollama:")


def test_ninerouter_requires_cross_border_ok(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="OCR_LLM_CROSS_BORDER_OK"):
        Settings.from_env(_env(tmp_path, OCR_LLM_PROVIDER="ninerouter_ollama", NINEROUTER_API_KEY_FILE=_key(tmp_path)))


@pytest.mark.parametrize("value", ["", "disabled"])
def test_ninerouter_requires_real_key(tmp_path: Path, value: str) -> None:
    env = _env(
        tmp_path,
        OCR_LLM_PROVIDER="ninerouter",
        OCR_LLM_CROSS_BORDER_OK="true",
        NINEROUTER_API_KEY_FILE=_key(tmp_path, value),
    )
    with pytest.raises(ValueError, match="NINEROUTER_API_KEY_FILE"):
        Settings.from_env(env)


def test_unknown_provider_and_bad_url(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="OCR_LLM_PROVIDER"):
        Settings.from_env(_env(tmp_path, OCR_LLM_PROVIDER="openai"))
    env = _env(
        tmp_path,
        OCR_LLM_PROVIDER="ninerouter",
        OCR_LLM_CROSS_BORDER_OK="true",
        NINEROUTER_API_KEY_FILE=_key(tmp_path),
        NINEROUTER_URL="https://api.openai.com",
    )
    with pytest.raises(ValueError, match="allowlist"):
        Settings.from_env(env)


def test_ninerouter_ollama_chain(tmp_path: Path) -> None:
    env = _env(
        tmp_path,
        OCR_LLM_PROVIDER="ninerouter_ollama",
        OCR_LLM_CROSS_BORDER_OK="true",
        NINEROUTER_API_KEY_FILE=_key(tmp_path),
        NINEROUTER_VISION_MODEL="cc/claude-sonnet-5",
    )
    s = Settings.from_env(env)
    assert s.ninerouter_api_key == KEY_UJI and KEY_UJI not in repr(s)
    assert build_vlm(s).name == "ninerouter:cc/claude-sonnet-5+ollama:qwen2.5vl:7b"


def test_vlm_disabled_ignores_provider(tmp_path: Path) -> None:
    s = Settings.from_env(_env(tmp_path, OCR_VLM_ENABLED="false", OCR_LLM_PROVIDER="ninerouter"))
    assert build_vlm(s) is None
