import json
from pathlib import Path
from typing import Any

import pytest
import requests
import requests.adapters

from efn_ocr.metrics import metrics_server_kwargs
from efn_ocr.storage import build_http_client
from efn_ocr.tls import (
    DEFAULT_CA_FILE,
    TlsConfigError,
    ca_file,
    client_ssl_context,
    is_production,
    pg_ssl_settings,
    plaintext_allowed,
    server_cert_pair,
)
from efn_ocr.vlm import OllamaVlm


def _file(tmp_path: Path, name: str, content: str = "x") -> str:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_production_detection_fail_closed() -> None:
    """SEC-27: env kosong / tidak dikenal = production (selaras BFF & Odoo)."""
    assert is_production({"APP_ENV": "production"})
    assert is_production({"NODE_ENV": "Production"})
    assert is_production({"APP_ENV": "prod"})
    assert is_production({})
    assert is_production({"APP_ENV": "  "})
    assert is_production({"APP_ENV": "staging"})
    assert is_production({"APP_ENV": "devel"})
    assert is_production({"APP_ENV": "development", "NODE_ENV": "production"})
    for value in ("dev", "development", "local", "test", "ci", "Development"):
        assert not is_production({"APP_ENV": value}), value
    assert not is_production({"NODE_ENV": "test"})


def test_plaintext_flag() -> None:
    assert plaintext_allowed({}) is False
    assert plaintext_allowed({"EFN_ALLOW_PLAINTEXT": "false"}) is False
    assert plaintext_allowed({"EFN_ALLOW_PLAINTEXT": "true", "APP_ENV": "development"}) is True
    assert plaintext_allowed({"EFN_ALLOW_PLAINTEXT": "true", "APP_ENV": "ci"}) is True
    for env in (
        {"EFN_ALLOW_PLAINTEXT": "true", "APP_ENV": "production"},
        {"EFN_ALLOW_PLAINTEXT": "true"},  # APP_ENV kosong = production (SEC-27)
        {"EFN_ALLOW_PLAINTEXT": "true", "APP_ENV": "staging"},
    ):
        with pytest.raises(TlsConfigError):
            plaintext_allowed(env)


def test_ca_file_default_and_missing(tmp_path: Path) -> None:
    ca = _file(tmp_path, "ca.crt")
    assert ca_file({"X_CA_FILE": ca}, "X_CA_FILE") == ca
    if not Path(DEFAULT_CA_FILE).exists():
        with pytest.raises(TlsConfigError):
            ca_file({}, "X_CA_FILE")


def test_pg_ssl_settings(tmp_path: Path) -> None:
    ca = _file(tmp_path, "ca.crt")
    assert pg_ssl_settings({"PG_SSLROOTCERT": ca}, allow_plaintext=False) == ("verify-full", ca)
    with pytest.raises(TlsConfigError):
        pg_ssl_settings({"PG_SSLMODE": "require", "PG_SSLROOTCERT": ca}, allow_plaintext=False)
    with pytest.raises(TlsConfigError):
        pg_ssl_settings({"PG_SSLMODE": "verify-ca", "PG_SSLROOTCERT": ca}, allow_plaintext=False)
    assert pg_ssl_settings({"PG_SSLMODE": "disable"}, allow_plaintext=True) == ("disable", None)


def test_server_cert_pair(tmp_path: Path) -> None:
    cert, key = _file(tmp_path, "c.crt"), _file(tmp_path, "c.key")
    assert server_cert_pair({}, "C", "K") is None
    assert server_cert_pair({"C": cert, "K": key}, "C", "K") == (cert, key)
    with pytest.raises(TlsConfigError):
        server_cert_pair({"C": cert}, "C", "K")
    with pytest.raises(TlsConfigError):
        server_cert_pair({"C": cert, "K": str(tmp_path / "missing.key")}, "C", "K")


def test_client_ssl_context_rejects_invalid_ca(tmp_path: Path) -> None:
    with pytest.raises(TlsConfigError):
        client_ssl_context(_file(tmp_path, "bad.crt", "not a certificate"))
    with pytest.raises(TlsConfigError):
        client_ssl_context(str(tmp_path / "missing.crt"))


def test_minio_http_client_verifies_with_internal_ca(tmp_path: Path) -> None:
    ca = _file(tmp_path, "ca.crt")
    pool = build_http_client(ca)
    assert pool.connection_pool_kw["cert_reqs"] == "CERT_REQUIRED"
    assert pool.connection_pool_kw["ca_certs"] == ca
    assert "ca_certs" not in build_http_client(None).connection_pool_kw


def test_ollama_session_uses_internal_ca(tmp_path: Path) -> None:
    ca = _file(tmp_path, "ca.crt")
    vlm = OllamaVlm("https://ollama:11434", "m", 5.0, ca_file=ca)
    assert vlm._session.verify == ca
    assert vlm._session.trust_env is False
    with pytest.raises(ValueError):
        OllamaVlm("https://ollama:11434", "m", 5.0, ca_file=None)
    assert vlm._session.cert is None


def test_ollama_session_presents_client_cert(tmp_path: Path) -> None:
    """mTLS ke sidecar Ollama (docs/04 §8.5)."""
    ca, cert, key = _file(tmp_path, "ca.crt"), _file(tmp_path, "c.crt"), _file(tmp_path, "c.key")
    vlm = OllamaVlm("https://ollama:11434", "m", 5.0, ca_file=ca, client_cert=(cert, key))
    assert vlm._session.cert == (cert, key)
    with pytest.raises(ValueError):
        OllamaVlm("http://ollama:11434", "m", 5.0, ca_file=None, client_cert=(cert, key))


class _CaptureAdapter(requests.adapters.HTTPAdapter):
    """Adapter palsu: mencatat argumen TLS yang benar-benar diteruskan requests ke lapisan transport."""

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[dict[str, Any]] = []

    def send(self, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:  # type: ignore[override]
        self.sent.append({"url": request.url, "cert": kwargs.get("cert"), "verify": kwargs.get("verify")})
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({"response": '{"product_name": "Indomie", "price": "3500"}'}).encode()
        response.request = request
        return response


def test_ollama_request_actually_carries_client_cert(tmp_path: Path) -> None:
    """Gelombang 5b: sertifikat klien sampai ke transport pada setiap POST /api/generate (bukan hanya atribut)."""
    ca, cert, key = _file(tmp_path, "ca.crt"), _file(tmp_path, "c.crt"), _file(tmp_path, "c.key")
    adapter = _CaptureAdapter()
    session = requests.Session()
    session.mount("https://", adapter)
    vlm = OllamaVlm("https://ollama:11434", "m", 5.0, ca_file=ca, session=session, client_cert=(cert, key))
    result = vlm.extract(b"\xff\xd8fake-jpeg")
    assert result.price == 3500
    assert adapter.sent == [{"url": "https://ollama:11434/api/generate", "cert": (cert, key), "verify": ca}]


def test_metrics_server_kwargs_mtls() -> None:
    assert metrics_server_kwargs(None, None, None) == {}
    assert metrics_server_kwargs("c", "k", None) == {"certfile": "c", "keyfile": "k"}
    assert metrics_server_kwargs("c", "k", "ca") == {
        "certfile": "c",
        "keyfile": "k",
        "client_cafile": "ca",
        "client_auth_required": True,
    }
    with pytest.raises(ValueError):
        metrics_server_kwargs(None, None, "ca")
