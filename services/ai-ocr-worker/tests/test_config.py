import ssl
from pathlib import Path

import pika
import pika.exceptions
import pytest

from efn_ocr.config import Settings, read_secret
from efn_ocr.messages import OutgoingMessage
from efn_ocr.publisher import (
    REQUIRED_CONSUME_QUEUES,
    PikaPublisher,
    TopologyMissingError,
    build_connection_params,
    message_properties,
    verify_topology,
)
from efn_ocr.tls import TlsConfigError


@pytest.fixture
def tls_env(tmp_path: Path) -> dict[str, str]:
    """Env produksi minimal: semua koneksi TLS, CA & sertifikat metrik berupa file dummy yang terbaca."""
    ca = tmp_path / "ca.crt"
    ca.write_text("-----BEGIN CERTIFICATE-----\ndummy\n-----END CERTIFICATE-----\n", encoding="utf-8")
    cert = tmp_path / "metrics.crt"
    cert.write_text("dummy-cert", encoding="utf-8")
    key = tmp_path / "metrics.key"
    key.write_text("dummy-key", encoding="utf-8")
    return {
        "APP_ENV": "production",
        "AMQP_URL": "amqps://efn-ocr@rabbitmq:5671",
        "AMQP_TLS_CA_FILE": str(ca),
        "PG_SSLMODE": "verify-full",
        "PG_SSLROOTCERT": str(ca),
        "MINIO_ENDPOINT": "minio",
        "MINIO_PORT": "9000",
        "MINIO_USE_SSL": "true",
        "MINIO_CA_FILE": str(ca),
        "OLLAMA_URL": "https://ollama:11434",
        "OLLAMA_CA_FILE": str(ca),
        "OLLAMA_CLIENT_CERT_FILE": str(cert),
        "OLLAMA_CLIENT_KEY_FILE": str(key),
        "METRICS_TLS_CERT_FILE": str(cert),
        "METRICS_TLS_KEY_FILE": str(key),
        "METRICS_TLS_CLIENT_CA_FILE": str(ca),
    }


PLAIN_DEV = {
    "APP_ENV": "development",
    "EFN_ALLOW_PLAINTEXT": "true",
    "AMQP_URL": "amqp://efn-ocr@rabbitmq:5672",
    "PG_SSLMODE": "disable",
    "MINIO_USE_SSL": "false",
    "OLLAMA_URL": "http://ollama:11434",
}


def test_read_secret_prefers_file(tmp_path: Path) -> None:
    secret_file = tmp_path / "pg"
    secret_file.write_text("from-file\n", encoding="utf-8")
    env = {"SAMPLE_CRED_FILE": str(secret_file), "SAMPLE_CRED": "from-env"}
    assert read_secret("SAMPLE_CRED", env) == "from-file"


def test_read_secret_env_fallback_and_missing_file(tmp_path: Path) -> None:
    assert read_secret("SAMPLE_CRED", {"SAMPLE_CRED": "from-env"}) == "from-env"
    assert read_secret("SAMPLE_CRED", {}) == ""
    with pytest.raises(RuntimeError):
        read_secret("SAMPLE_CRED", {"SAMPLE_CRED_FILE": str(tmp_path / "missing")})


def test_settings_tls_production(tls_env: dict[str, str]) -> None:
    settings = Settings.from_env(tls_env)
    assert settings.amqp_ca_file == tls_env["AMQP_TLS_CA_FILE"]
    assert settings.amqp_user == "efn-ocr"
    assert (settings.pg_sslmode, settings.pg_sslrootcert) == ("verify-full", tls_env["PG_SSLROOTCERT"])
    assert settings.pg_user == "efn_ocr"
    assert settings.minio_endpoint == "minio:9000"
    assert settings.minio_secure is True
    assert settings.minio_access_key == "efn-ocr"
    assert settings.ollama_ca_file == tls_env["OLLAMA_CA_FILE"]
    assert settings.metrics_cert_file == tls_env["METRICS_TLS_CERT_FILE"]
    assert settings.metrics_client_ca_file == tls_env["METRICS_TLS_CLIENT_CA_FILE"]
    assert settings.ollama_client_cert_file == tls_env["OLLAMA_CLIENT_CERT_FILE"]
    assert settings.ollama_client_key_file == tls_env["OLLAMA_CLIENT_KEY_FILE"]
    assert settings.prefetch == 2
    assert settings.name_similarity_threshold == 75.0


def test_settings_defaults_are_tls(tls_env: dict[str, str]) -> None:
    """Tanpa AMQP_URL/OLLAMA_URL/PG_SSLMODE eksplisit, default-nya TLS (bukan plaintext)."""
    env = {k: v for k, v in tls_env.items() if k not in ("AMQP_URL", "OLLAMA_URL", "PG_SSLMODE", "MINIO_USE_SSL")}
    settings = Settings.from_env(env)
    assert settings.amqp_url.startswith("amqps://")
    assert settings.ollama_url.startswith("https://")
    assert settings.pg_sslmode == "verify-full"
    assert settings.minio_secure is True


@pytest.mark.parametrize(
    "override",
    [
        {"AMQP_URL": "amqp://efn-ocr@rabbitmq:5672"},
        {"AMQP_URL": "amqps://efn-ocr:inline@rabbitmq:5671"},
        {"PG_SSLMODE": "require"},
        {"PG_SSLMODE": "disable"},
        {"PG_SSLMODE": "prefer"},
        {"MINIO_USE_SSL": "false"},
        {"OLLAMA_URL": "http://ollama:11434"},
        {"AMQP_TLS_CA_FILE": "/nonexistent/ca.crt"},
        {"PG_SSLROOTCERT": "/nonexistent/ca.crt"},
        {"MINIO_CA_FILE": "/nonexistent/ca.crt"},
        {"OLLAMA_CA_FILE": "/nonexistent/ca.crt"},
        {"METRICS_TLS_KEY_FILE": ""},
        {"METRICS_TLS_CERT_FILE": "", "METRICS_TLS_KEY_FILE": ""},
        # Flag plaintext di production ditolak walaupun semua koneksi TLS.
        {"EFN_ALLOW_PLAINTEXT": "true"},
        # SEC-27: APP_ENV kosong = production -> flag tetap ditolak.
        {"EFN_ALLOW_PLAINTEXT": "true", "APP_ENV": ""},
        {"EFN_ALLOW_PLAINTEXT": "true", "APP_ENV": "staging"},
        # mTLS wajib di production (docs/04 §8.5).
        {"OLLAMA_CLIENT_CERT_FILE": "", "OLLAMA_CLIENT_KEY_FILE": ""},
        {"OLLAMA_CLIENT_KEY_FILE": ""},
        {"OLLAMA_CLIENT_CERT_FILE": "/nonexistent/c.crt"},
        {"METRICS_TLS_CLIENT_CA_FILE": ""},
        {"METRICS_TLS_CLIENT_CA_FILE": "/nonexistent/ca.crt"},
    ],
)
def test_settings_fail_closed_without_tls(tls_env: dict[str, str], override: dict[str, str]) -> None:
    with pytest.raises(TlsConfigError):
        Settings.from_env({**tls_env, **override})


def test_empty_ca_file_rejected(tls_env: dict[str, str], tmp_path: Path) -> None:
    empty = tmp_path / "empty.crt"
    empty.write_text("  \n", encoding="utf-8")
    with pytest.raises(TlsConfigError):
        Settings.from_env({**tls_env, "MINIO_CA_FILE": str(empty)})


def test_mtls_optional_outside_production(tls_env: dict[str, str]) -> None:
    """Di dev (TLS tanpa mTLS) sertifikat klien opsional; di production wajib (lihat parametrize di atas)."""
    env = {
        k: v
        for k, v in tls_env.items()
        if k not in ("OLLAMA_CLIENT_CERT_FILE", "OLLAMA_CLIENT_KEY_FILE", "METRICS_TLS_CLIENT_CA_FILE")
    }
    settings = Settings.from_env({**env, "APP_ENV": "development"})
    assert settings.ollama_client_cert_file is None
    assert settings.metrics_client_ca_file is None


def test_plaintext_only_with_explicit_flag_outside_production() -> None:
    with pytest.raises(TlsConfigError):
        Settings.from_env({k: v for k, v in PLAIN_DEV.items() if k != "EFN_ALLOW_PLAINTEXT"})
    with pytest.raises(TlsConfigError):
        # SEC-27: tanpa APP_ENV = production.
        Settings.from_env({k: v for k, v in PLAIN_DEV.items() if k != "APP_ENV"})
    with pytest.raises(TlsConfigError):
        Settings.from_env({**PLAIN_DEV, "APP_ENV": "production"})
    with pytest.raises(TlsConfigError):
        Settings.from_env({**PLAIN_DEV, "NODE_ENV": "production"})
    settings = Settings.from_env(PLAIN_DEV)
    assert settings.amqp_ca_file is None
    assert settings.pg_sslrootcert is None
    assert settings.minio_secure is False
    assert settings.metrics_cert_file is None


def test_settings_other_values() -> None:
    env = {
        **PLAIN_DEV,
        "NAME_SIMILARITY_THRESHOLD": "0.80",
        "OCR_CONFIDENCE_THRESHOLD": "0.7",
        "GEOFENCE_CIRCLES": "-8.67,115.21,30000",
        "MINIO_ACCESS_KEY": "efn-ocr-dev",
        "MINIO_ENDPOINT": "minio:9100",
        "OLLAMA_URL": "http://ollama:11434/",
        "PG_DB": "efn_platform_x",
        "PLATFORM_DB": "ignored",
    }
    settings = Settings.from_env(env)
    assert settings.name_similarity_threshold == pytest.approx(80.0)
    assert settings.ocr_confidence_threshold == pytest.approx(0.7)
    assert len(settings.geofence) == 1
    assert settings.minio_access_key == "efn-ocr-dev"
    assert settings.minio_endpoint == "minio:9100"
    assert settings.ollama_url == "http://ollama:11434"
    assert settings.pg_database == "efn_platform_x"


def test_vlm_disabled_does_not_require_ollama_tls(tls_env: dict[str, str]) -> None:
    env = {**tls_env, "OCR_VLM_ENABLED": "false", "OLLAMA_URL": "http://ollama:11434"}
    assert Settings.from_env(env).vlm_enabled is False


def test_secrets_not_in_repr(tls_env: dict[str, str], tmp_path: Path) -> None:
    secret_file = tmp_path / "mq"
    expected = "very-secret-value"
    secret_file.write_text(expected, encoding="utf-8")
    settings = Settings.from_env({**tls_env, "RABBITMQ_PASSWORD_FILE": str(secret_file)})
    assert settings.amqp_password == expected
    assert expected not in repr(settings)


def test_amqps_connection_params_use_tls_and_hostname() -> None:
    context = ssl.create_default_context()
    expected = "dummy-value"
    params = build_connection_params("amqps://efn-ocr@rabbitmq:5671/%2F", expected, "test", context)
    assert params.host == "rabbitmq"
    assert params.port == 5671
    assert params.virtual_host == "/"
    assert params.credentials.username == "efn-ocr"
    assert params.credentials.password == expected
    assert params.ssl_options is not None
    assert params.ssl_options.context is context
    assert params.ssl_options.server_hostname == "rabbitmq"
    # Port default amqps bila tidak ditulis.
    assert build_connection_params("amqps://efn-ocr@rabbitmq", "", "t", context).port == 5671


def test_connection_params_reject_insecure_combinations() -> None:
    context = ssl.create_default_context()
    with pytest.raises(ValueError):
        build_connection_params("amqps://efn-ocr@rabbitmq:5671", "", "t", None)
    with pytest.raises(ValueError):
        build_connection_params("amqp://efn-ocr@rabbitmq:5672", "", "t", context)
    with pytest.raises(ValueError):
        build_connection_params("amqps://efn-ocr:pw@rabbitmq:5671", "", "t", context)
    with pytest.raises(ValueError):
        build_connection_params("http://rabbitmq", "", "t", None)
    plain = build_connection_params("amqp://efn-ocr@rabbitmq", "", "t", None)
    assert plain.port == 5672 and plain.ssl_options is None


class _FakeChannel:
    """Channel palsu: queue yang ada di `existing` lolos passive declare; lainnya -> 404 seperti broker."""

    def __init__(self, existing: set[str]) -> None:
        self.existing = existing
        self.calls: list[tuple[str, dict[str, object]]] = []

    def queue_declare(self, queue: str, **kwargs: object) -> None:
        self.calls.append((queue, kwargs))
        if queue not in self.existing:
            raise pika.exceptions.ChannelClosedByBroker(404, f"NOT_FOUND - no queue '{queue}'")


def test_verify_topology_is_passive_and_only_consume_queues() -> None:
    """Gelombang 5b: topologi milik broker; worker tidak membuat queue dan tidak menyentuh queue publish."""
    channel = _FakeChannel({"ocr.requested", "ocr.requested.dlq"})
    verify_topology(channel)  # type: ignore[arg-type]
    assert [q for q, _ in channel.calls] == ["ocr.requested.dlq", "ocr.requested"]
    assert all(kwargs == {"passive": True} for _, kwargs in channel.calls)
    assert set(REQUIRED_CONSUME_QUEUES) == {"ocr.requested", "ocr.requested.dlq"}


@pytest.mark.parametrize("missing", ["ocr.requested", "ocr.requested.dlq"])
def test_verify_topology_fails_with_clear_error(missing: str) -> None:
    channel = _FakeChannel({"ocr.requested", "ocr.requested.dlq"} - {missing})
    with pytest.raises(TopologyMissingError, match=missing):
        verify_topology(channel)  # type: ignore[arg-type]


def test_verify_topology_other_broker_errors_propagate() -> None:
    class _Denied(_FakeChannel):
        def queue_declare(self, queue: str, **kwargs: object) -> None:
            raise pika.exceptions.ChannelClosedByBroker(403, "ACCESS_REFUSED")

    with pytest.raises(pika.exceptions.ChannelClosedByBroker):
        verify_topology(_Denied(set()))  # type: ignore[arg-type]


def test_published_messages_carry_amqp_user_id() -> None:
    """SEC-08: properti user_id = user RabbitMQ (efn-ocr); RabbitMQ memvalidasinya terhadap user koneksi."""
    message = OutgoingMessage(queue="loyalty.earn.requested", body=b"{}", message_id="price_hunter:x")
    props = message_properties(message, "efn-ocr")
    assert props.user_id == "efn-ocr"
    assert props.message_id == "price_hunter:x"
    assert props.content_type == "application/json"
    assert props.delivery_mode == 2
    with pytest.raises(ValueError):
        PikaPublisher(pika.ConnectionParameters(), "")
