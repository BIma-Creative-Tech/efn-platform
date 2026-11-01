"""Gateway LLM untuk Text-to-SQL (G38, K25-01, docs/04 §23.4). Pola sama dengan ai-ocr-worker `efn_ocr.llm` (G37).

* ``NineRouterLlm`` — 9router Anthropic Messages ``POST /v1/messages`` (``x-api-key``), teks saja.
* ``OllamaLlm`` — Ollama lokal ``/api/generate`` lewat sidecar mTLS (CN ``text2sql``).
* ``FallbackLlm`` — penyedia berurutan; gagal satu -> berikutnya; semua gagal -> ``LlmUnavailableError``.

Yang dikirim hanya prompt (skema semantic layer + contoh + pertanyaan). Hasil query TIDAK pernah dikirim ke LLM.
Kunci API tidak pernah di-log; body error penyedia tidak di-log (hanya kode status).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

import requests

log = logging.getLogger(__name__)

ALLOWED_HOSTS = frozenset({"9router.bimacreative.tech"})
ANTHROPIC_VERSION = "2023-06-01"
MAX_RESPONSE_BYTES = 64 * 1024


class LlmUnavailableError(RuntimeError):
    """Penyedia LLM tidak menjawab dengan benar (timeout, HTTP != 200, body rusak)."""


@dataclass(frozen=True)
class LlmAnswer:
    text: str
    engine: str


class LlmClient(Protocol):
    name: str

    def complete(self, prompt: str) -> str: ...


def validate_ninerouter_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise ValueError("NINEROUTER_URL wajib https://")
    if parts.username or parts.password:
        raise ValueError("NINEROUTER_URL tidak boleh memuat kredensial")
    if (parts.hostname or "") not in ALLOWED_HOSTS:
        raise ValueError(f"NINEROUTER_URL host tidak di allowlist {sorted(ALLOWED_HOSTS)}")
    if parts.port not in (None, 443):
        raise ValueError("NINEROUTER_URL hanya port 443")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError("NINEROUTER_URL hanya origin (tanpa path/query)")
    return f"https://{parts.hostname}"


def _json_body(resp: requests.Response, prefix: str) -> Any:
    if resp.status_code != 200:
        raise LlmUnavailableError(f"{prefix}:http_{resp.status_code}")
    if len(resp.content) > MAX_RESPONSE_BYTES:
        raise LlmUnavailableError(f"{prefix}:response_too_large")
    try:
        return resp.json()
    except ValueError as exc:
        raise LlmUnavailableError(f"{prefix}:invalid_body") from exc


class NineRouterLlm:
    def __init__(
        self,
        url: str,
        api_key: str,
        model: str,
        timeout_seconds: float,
        session: requests.Session | None = None,
        max_tokens: int = 800,
    ) -> None:
        if not api_key or api_key == "disabled":
            raise ValueError("NINEROUTER_API_KEY_FILE kosong/placeholder")
        self._url = validate_ninerouter_url(url)
        self._key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._max_tokens = int(max_tokens)
        self._session = session or requests.Session()
        self._session.trust_env = False
        self._session.verify = True
        self.name = f"ninerouter:{model}"

    def __repr__(self) -> str:
        return f"NineRouterLlm(model={self._model!r})"

    def payload(self, prompt: str) -> dict[str, Any]:
        return {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        }

    def complete(self, prompt: str) -> str:
        headers = {
            "x-api-key": self._key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
            "accept": "application/json",
        }
        try:
            resp = self._session.post(
                f"{self._url}/v1/messages",
                json=self.payload(prompt),
                headers=headers,
                timeout=self._timeout,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise LlmUnavailableError(f"ninerouter:{type(exc).__name__}") from exc
        body = _ninerouter_body(resp)
        if not isinstance(body, dict) or not isinstance(body.get("content"), list):
            raise LlmUnavailableError("ninerouter:invalid_shape")
        return "".join(
            block.get("text", "")
            for block in body["content"]
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
        )


def _ninerouter_body(resp: requests.Response) -> Any:
    """9router kadang menjawab `text/event-stream` berisi satu objek JSON + `data: [DONE]` (diuji 1 Nov 2026)."""
    if resp.status_code != 200:
        raise LlmUnavailableError(f"ninerouter:http_{resp.status_code}")
    if len(resp.content) > MAX_RESPONSE_BYTES:
        raise LlmUnavailableError("ninerouter:response_too_large")
    import json  # noqa: PLC0415

    text = resp.content.decode("utf-8", "replace").strip()
    if text.endswith("data: [DONE]"):
        text = text[: -len("data: [DONE]")].strip()
    try:
        obj, _ = json.JSONDecoder().raw_decode(text)
    except ValueError as exc:
        raise LlmUnavailableError("ninerouter:invalid_body") from exc
    return obj


class OllamaLlm:
    def __init__(
        self,
        url: str,
        model: str,
        timeout_seconds: float,
        ca_file: str | None,
        client_cert: tuple[str, str] | None = None,
        session: requests.Session | None = None,
    ) -> None:
        if url.startswith("https://") and not ca_file:
            raise ValueError("OLLAMA_URL https membutuhkan OLLAMA_CA_FILE")
        self._url = url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds
        self._session = session or requests.Session()
        self._session.verify = ca_file if ca_file else True
        self._session.trust_env = False
        if client_cert is not None:
            self._session.cert = client_cert
        self.name = f"ollama:{model}"

    def complete(self, prompt: str) -> str:
        payload = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0, "num_predict": 400},
            "keep_alive": "30m",
        }
        try:
            resp = self._session.post(f"{self._url}/api/generate", json=payload, timeout=self._timeout)
        except requests.RequestException as exc:
            raise LlmUnavailableError(f"ollama:{type(exc).__name__}") from exc
        body = _json_body(resp, "ollama")
        if not isinstance(body, dict):
            raise LlmUnavailableError("ollama:invalid_shape")
        return str(body.get("response", ""))


class FallbackLlm:
    def __init__(self, providers: Sequence[LlmClient]) -> None:
        if not providers:
            raise ValueError("FallbackLlm butuh minimal 1 penyedia")
        self._providers = tuple(providers)
        self.name = "+".join(p.name for p in self._providers)

    def answer(self, prompt: str) -> LlmAnswer:
        reasons: list[str] = []
        for provider in self._providers:
            try:
                return LlmAnswer(text=provider.complete(prompt), engine=provider.name)
            except LlmUnavailableError as exc:
                reasons.append(str(exc))
                log.warning("penyedia LLM %s gagal (%s); mencoba penyedia berikutnya", provider.name, exc)
        raise LlmUnavailableError("semua penyedia gagal: " + ", ".join(reasons))


def build_llm(settings: Any) -> FallbackLlm:
    def ollama() -> LlmClient:
        cert = (
            (settings.ollama_client_cert_file, settings.ollama_client_key_file)
            if settings.ollama_client_cert_file and settings.ollama_client_key_file
            else None
        )
        return OllamaLlm(
            settings.ollama_url,
            settings.ollama_model,
            settings.ollama_timeout_seconds,
            ca_file=settings.ollama_ca_file or None,
            client_cert=cert,
        )

    def ninerouter() -> LlmClient:
        return NineRouterLlm(
            settings.ninerouter_url,
            settings.ninerouter_api_key,
            settings.ninerouter_model,
            settings.ninerouter_timeout_seconds,
        )

    if settings.llm_provider == "ollama":
        return FallbackLlm([ollama()])
    if settings.llm_provider == "ninerouter":
        return FallbackLlm([ninerouter()])
    return FallbackLlm([ninerouter(), ollama()])
