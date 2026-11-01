"""Konfigurasi service Text-to-SQL dari env + Docker secret (docs/04 §23, G38). Gagal start bila tidak aman.

- Semua koneksi keluar TLS terverifikasi: ClickHouse HTTPS 8443 (CA internal), Ollama mTLS (CN text2sql), 9router
  (CA publik, allowlist host). Listener HTTPS 8443 dengan sertifikat `tls_text2sql`.
- `APP_ENV` kosong/tidak dikenal = production (fail closed, SEC-27). Plaintext listener hanya untuk test/dev eksplisit.
- 9router (`TEXT2SQL_LLM_PROVIDER` != ollama) = transfer lintas negara (PDP P7): wajib `TEXT2SQL_CROSS_BORDER_OK=true`.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

NON_PRODUCTION_ENVS = frozenset({"dev", "development", "local", "test", "ci"})
PROVIDERS = frozenset({"ollama", "ninerouter", "ninerouter_ollama"})
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}
_MODEL_RE = re.compile(r"^[a-z0-9][a-z0-9._:/-]{0,127}$")
_HEX_RE = re.compile(r"^[0-9a-f]{32,128}$")


class ConfigError(RuntimeError):
    """Konfigurasi tidak aman/tidak lengkap. Service wajib berhenti."""


def _get(env: Mapping[str, str], name: str, default: str = "") -> str:
    return (env.get(name) or default).strip()


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = _get(env, name).lower()
    if not raw:
        return default
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise ConfigError(f"{name} harus true/false")


def _int(env: Mapping[str, str], name: str, default: int, lo: int, hi: int) -> int:
    raw = _get(env, name)
    try:
        value = int(raw) if raw else default
    except ValueError as exc:
        raise ConfigError(f"{name} harus bilangan bulat") from exc
    if not lo <= value <= hi:
        raise ConfigError(f"{name} di luar rentang {lo}..{hi}")
    return value


def _file(env: Mapping[str, str], name: str, required: bool) -> str:
    """Isi file `<name>` (path). Kosong bila tidak di-set dan tidak wajib."""
    path = _get(env, name)
    if not path:
        if required:
            raise ConfigError(f"{name} wajib di-set")
        return ""
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ConfigError(f"{name}: file tidak terbaca") from exc


def _path(env: Mapping[str, str], name: str, default: str = "") -> str:
    path = _get(env, name, default)
    if path and not Path(path).is_file():
        raise ConfigError(f"{name}: file {path} tidak ada")
    return path


@dataclass(frozen=True)
class Settings:
    app_env: str
    enabled: bool
    semantic_layer_path: str
    # listener
    bind: str
    port: int
    tls_cert_file: str
    tls_key_file: str
    api_key: str
    # ClickHouse
    ch_url: str
    ch_user: str
    ch_password: str
    ch_ca_file: str
    scope_key: str
    # LLM
    llm_provider: str
    cross_border_ok: bool
    ollama_url: str
    ollama_model: str
    ollama_ca_file: str
    ollama_client_cert_file: str
    ollama_client_key_file: str
    ollama_timeout_seconds: int
    ninerouter_url: str
    ninerouter_api_key: str
    ninerouter_model: str
    ninerouter_timeout_seconds: int
    # G39 agent (docs/04 §24)
    agent_enabled: bool = False
    agent_summary_provider: str = "none"
    agent_results_cross_border_ok: bool = False

    def __repr__(self) -> str:  # jangan pernah menampilkan key/password
        return f"Settings(app_env={self.app_env!r}, enabled={self.enabled}, llm_provider={self.llm_provider!r})"

    @property
    def production_like(self) -> bool:
        return self.app_env not in NON_PRODUCTION_ENVS

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        e = os.environ if env is None else env
        app_env = _get(e, "APP_ENV").lower()
        allow_plain = _bool(e, "EFN_ALLOW_PLAINTEXT", False)
        if allow_plain and app_env not in NON_PRODUCTION_ENVS:
            raise ConfigError("EFN_ALLOW_PLAINTEXT=true hanya untuk APP_ENV non-produksi")

        tls_cert = _path(e, "TLS_CERT_FILE")
        tls_key = _path(e, "TLS_KEY_FILE")
        if not (tls_cert and tls_key) and not allow_plain:
            raise ConfigError("TLS_CERT_FILE dan TLS_KEY_FILE wajib (listener HTTPS, docs/04 §23.2)")

        api_key = _file(e, "TEXT2SQL_API_KEY_FILE", required=True)
        if len(api_key) < 24 or api_key == "disabled":
            raise ConfigError("TEXT2SQL_API_KEY_FILE kosong/terlalu pendek")
        scope_key = _file(e, "TEXT2SQL_SCOPE_KEY_FILE", required=True)
        if not _HEX_RE.match(scope_key):
            raise ConfigError("TEXT2SQL_SCOPE_KEY_FILE harus hex huruf kecil >= 32 karakter")

        ch_host = _get(e, "CH_HOST", "clickhouse")
        ch_port = _int(e, "CH_PORT", 8443, 1, 65535)
        ch_secure = _bool(e, "CH_SECURE", True)
        if not ch_secure and not allow_plain:
            raise ConfigError("CH_SECURE wajib true (ClickHouse HTTPS 8443)")
        ch_ca = _path(e, "CH_CA_FILE", "/run/secrets/efn_ca_crt" if ch_secure else "")
        if ch_secure and not ch_ca:
            raise ConfigError("CH_CA_FILE wajib untuk HTTPS")

        provider = _get(e, "TEXT2SQL_LLM_PROVIDER", "ollama").lower()
        if provider not in PROVIDERS:
            raise ConfigError(f"TEXT2SQL_LLM_PROVIDER tidak dikenal: {provider}")
        cross_border = _bool(e, "TEXT2SQL_CROSS_BORDER_OK", False)
        nr_key = ""
        if provider != "ollama":
            if not cross_border:
                raise ConfigError(
                    "TEXT2SQL_LLM_PROVIDER 9router membutuhkan TEXT2SQL_CROSS_BORDER_OK=true (PDP P7, Q-T7)",
                )
            nr_key = _file(e, "NINEROUTER_API_KEY_FILE", required=True)
            if not nr_key or nr_key == "disabled":
                raise ConfigError("NINEROUTER_API_KEY_FILE kosong/placeholder")

        models = {
            "ollama": _get(e, "TEXT2SQL_OLLAMA_MODEL", "qwen2.5-coder:7b"),
            "ninerouter": _get(e, "TEXT2SQL_NINEROUTER_MODEL", "cc/claude-haiku-4-5-20251001"),
        }
        for name, model in models.items():
            if not _MODEL_RE.match(model):
                raise ConfigError(f"model {name} tidak valid")

        # G39: ringkasan agent MENGIRIM angka hasil ke LLM -> persetujuan terpisah dari G38 (yang hanya mengirim
        # pertanyaan). none = tanpa ringkasan; ollama = lokal; ninerouter* = lintas negara, wajib
        # TEXT2SQL_AGENT_RESULTS_CROSS_BORDER_OK=true (Q-T7; production ditolak deploy-validate).
        agent_enabled = _bool(e, "TEXT2SQL_AGENT_ENABLED", False)
        summary_provider = _get(e, "TEXT2SQL_AGENT_SUMMARY_PROVIDER", "none").lower()
        if summary_provider not in PROVIDERS | {"none"}:
            raise ConfigError(f"TEXT2SQL_AGENT_SUMMARY_PROVIDER tidak dikenal: {summary_provider}")
        results_cross_border = _bool(e, "TEXT2SQL_AGENT_RESULTS_CROSS_BORDER_OK", False)
        if summary_provider.startswith("ninerouter"):
            if not results_cross_border:
                raise ConfigError(
                    "TEXT2SQL_AGENT_SUMMARY_PROVIDER 9router membutuhkan TEXT2SQL_AGENT_RESULTS_CROSS_BORDER_OK=true "
                    "(hasil agregat dikirim ke luar negeri, PDP P8, Q-T7)",
                )
            if not nr_key:
                raise ConfigError("ringkasan 9router membutuhkan TEXT2SQL_LLM_PROVIDER 9router (overlay egress + key)")

        ollama_url = _get(e, "OLLAMA_URL", "https://ollama:11434")
        if not ollama_url.startswith("https://") and not allow_plain:
            raise ConfigError("OLLAMA_URL wajib https:// (sidecar mTLS)")

        return cls(
            app_env=app_env or "production",
            enabled=_bool(e, "TEXT2SQL_ENABLED", False),
            semantic_layer_path=_path(e, "TEXT2SQL_SEMANTIC_LAYER", "/app/semantic_layer.yaml"),
            bind=_get(e, "TEXT2SQL_BIND", "0.0.0.0"),  # noqa: S104 - listener service di network internal compose
            port=_int(e, "TEXT2SQL_PORT", 8443, 1, 65535),
            tls_cert_file=tls_cert,
            tls_key_file=tls_key,
            api_key=api_key,
            ch_url=f"{'https' if ch_secure else 'http'}://{ch_host}:{ch_port}",
            ch_user=_get(e, "CH_USER", "efn_text2sql"),
            ch_password=_file(e, "CH_PASSWORD_FILE", required=True),
            ch_ca_file=ch_ca,
            scope_key=scope_key,
            llm_provider=provider,
            cross_border_ok=cross_border,
            ollama_url=ollama_url.rstrip("/"),
            ollama_model=models["ollama"],
            ollama_ca_file=_path(
                e, "OLLAMA_CA_FILE", "/run/secrets/efn_ca_crt" if ollama_url.startswith("https") else ""
            ),
            ollama_client_cert_file=_path(e, "OLLAMA_CLIENT_CERT_FILE"),
            ollama_client_key_file=_path(e, "OLLAMA_CLIENT_KEY_FILE"),
            ollama_timeout_seconds=_int(e, "TEXT2SQL_OLLAMA_TIMEOUT_SECONDS", 120, 5, 600),
            ninerouter_url=_get(e, "NINEROUTER_URL", "https://9router.bimacreative.tech"),
            ninerouter_api_key=nr_key,
            ninerouter_model=models["ninerouter"],
            ninerouter_timeout_seconds=_int(e, "NINEROUTER_TIMEOUT_SECONDS", 45, 5, 300),
            agent_enabled=agent_enabled,
            agent_summary_provider=summary_provider,
            agent_results_cross_border_ok=results_cross_border,
        )
