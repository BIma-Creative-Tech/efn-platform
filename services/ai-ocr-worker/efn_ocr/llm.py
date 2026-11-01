"""Gateway LLM untuk OCR tahap 2 (G37, K25-01, docs/04 §22, ADR-037).

Satu antarmuka ``VlmClient`` (``extract(image_bytes) -> VlmResult``, ``name``) dengan penyedia:

* ``NineRouterVlm`` — 9router (``https://9router.bimacreative.tech``), API **Anthropic Messages** ``POST /v1/messages``
  dengan header ``x-api-key``. Jalur OpenAI-compatible (``/v1/chat/completions`` + ``image_url`` data URI) ditolak 403
  oleh WAF 9router (diuji 1 Nov 2026), jadi tidak dipakai.
* ``OllamaVlm`` (``efn_ocr.vlm``) — lokal, jaringan privat.
* ``FallbackVlm`` — coba penyedia berurutan; gagal satu (``VlmUnavailableError``) -> penyedia berikutnya. Semua gagal
  -> ``VlmUnavailableError`` (laporan di-flag untuk review manual, bukan error transaksi; CLAUDE.md prinsip AI).

Keamanan & privasi:
* Hanya host di ``ALLOWED_HOSTS`` (https, port 443) yang boleh dipanggil; redirect tidak diikuti; proxy env diabaikan.
* Payload = prompt tetap + gambar yang sudah di-encode ulang ``encode_for_vlm`` (JPEG baru, EXIF/GPS hilang). Nama,
  harga laporan, user, lokasi, dan id laporan TIDAK dikirim.
* API key dari file secret, tidak pernah di-log.
"""

from __future__ import annotations

import base64
import logging
import re
from collections.abc import Sequence
from typing import Any, Protocol
from urllib.parse import urlsplit

import requests

from efn_ocr import metrics
from efn_ocr.errors import VlmUnavailableError
from efn_ocr.vlm import PROMPT, VlmResult, parse_vlm_response

log = logging.getLogger(__name__)

ALLOWED_HOSTS = frozenset({"9router.bimacreative.tech"})
ANTHROPIC_VERSION = "2023-06-01"
MAX_RESPONSE_BYTES = 64 * 1024
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


class VlmClient(Protocol):
    name: str

    def extract(self, image_bytes: bytes) -> VlmResult: ...


def validate_ninerouter_url(url: str) -> str:
    """https + host di allowlist + tanpa kredensial/port lain. Dipanggil saat start (gagal = tidak start)."""
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


def extract_json_text(text: str) -> str:
    """Ambil objek JSON pertama dari jawaban model (Claude sering membungkus dengan ```json ... ```)."""
    match = _JSON_OBJECT.search(text or "")
    return match.group(0) if match else ""


class NineRouterVlm:
    """Klien 9router (Anthropic Messages). TLS diverifikasi dengan bundle CA publik (sertifikat publik 9router)."""

    def __init__(
        self,
        url: str,
        api_key: str,
        model: str,
        timeout_seconds: float,
        session: requests.Session | None = None,
        max_tokens: int = 256,
    ) -> None:
        if not api_key or api_key == "disabled":
            raise ValueError("NINEROUTER_API_KEY_FILE kosong/placeholder")
        self._url = validate_ninerouter_url(url)
        self._key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._max_tokens = int(max_tokens)
        self._session = session or requests.Session()
        self._session.trust_env = False  # abaikan HTTP(S)_PROXY / .netrc
        self._session.verify = True
        self.name = f"ninerouter:{model}"

    def __repr__(self) -> str:  # jangan pernah menampilkan key
        return f"NineRouterVlm(model={self._model!r})"

    def payload(self, image_bytes: bytes) -> dict[str, Any]:
        return {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "temperature": 0,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": base64.b64encode(image_bytes).decode("ascii"),
                            },
                        },
                        {"type": "text", "text": PROMPT},
                    ],
                }
            ],
        }

    def extract(self, image_bytes: bytes) -> VlmResult:
        headers = {
            "x-api-key": self._key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
            "accept": "application/json",
        }
        try:
            resp = self._session.post(
                f"{self._url}/v1/messages",
                json=self.payload(image_bytes),
                headers=headers,
                timeout=self._timeout,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise VlmUnavailableError(f"ninerouter:{type(exc).__name__}") from exc
        if resp.status_code != 200:
            # Kode status saja; body bisa memuat detail akun/kuota penyedia.
            raise VlmUnavailableError(f"ninerouter:http_{resp.status_code}")
        if len(resp.content) > MAX_RESPONSE_BYTES:
            raise VlmUnavailableError("ninerouter:response_too_large")
        try:
            body = resp.json()
        except ValueError as exc:
            raise VlmUnavailableError("ninerouter:invalid_body") from exc
        if not isinstance(body, dict) or not isinstance(body.get("content"), list):
            raise VlmUnavailableError("ninerouter:invalid_shape")
        text = "".join(
            block.get("text", "")
            for block in body["content"]
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
        )
        return parse_vlm_response(extract_json_text(text))


class FallbackVlm:
    """Penyedia berurutan (K25-01: primer 9router, sekunder Ollama). Mencatat metrik per penyedia."""

    def __init__(self, providers: Sequence[VlmClient]) -> None:
        if not providers:
            raise ValueError("FallbackVlm butuh minimal 1 penyedia")
        self._providers = tuple(providers)
        self.name = "+".join(p.name for p in self._providers)

    def extract(self, image_bytes: bytes) -> VlmResult:
        reasons: list[str] = []
        for provider in self._providers:
            kind = provider.name.split(":", 1)[0]
            try:
                result = provider.extract(image_bytes)
            except VlmUnavailableError as exc:
                metrics.LLM_PROVIDER_CALLS.labels(provider=kind, outcome="unavailable").inc()
                reasons.append(str(exc))
                log.warning("penyedia VLM %s gagal (%s); mencoba penyedia berikutnya", kind, exc)
                continue
            metrics.LLM_PROVIDER_CALLS.labels(provider=kind, outcome="ok").inc()
            return _tag(result, provider.name)
        raise VlmUnavailableError("semua penyedia gagal: " + ", ".join(reasons))


def _tag(result: VlmResult, engine: str) -> VlmResult:
    raw = dict(result.raw)
    raw["engine"] = engine
    return VlmResult(product_name=result.product_name, price=result.price, raw=raw)


def build_vlm(settings: Any) -> VlmClient | None:
    """Rakit penyedia dari Settings (``llm_provider``): ollama | ninerouter | ninerouter_ollama."""
    from efn_ocr.vlm import OllamaVlm  # noqa: PLC0415 - hindari siklus impor saat test

    if not settings.vlm_enabled:
        return None

    def ollama() -> VlmClient:
        return OllamaVlm(
            settings.ollama_url,
            settings.ollama_model,
            settings.ollama_timeout_seconds,
            ca_file=settings.ollama_ca_file,
            client_cert=(
                (settings.ollama_client_cert_file, settings.ollama_client_key_file)
                if settings.ollama_client_cert_file and settings.ollama_client_key_file
                else None
            ),
            num_predict=settings.ollama_num_predict,
            num_ctx=settings.ollama_num_ctx,
            keep_alive=settings.ollama_keep_alive,
        )

    def ninerouter() -> VlmClient:
        return NineRouterVlm(
            settings.ninerouter_url,
            settings.ninerouter_api_key,
            settings.ninerouter_model,
            settings.ninerouter_timeout_seconds,
        )

    if settings.llm_provider == "ollama":
        return FallbackVlm([ollama()])
    if settings.llm_provider == "ninerouter":
        return FallbackVlm([ninerouter()])
    return FallbackVlm([ninerouter(), ollama()])
