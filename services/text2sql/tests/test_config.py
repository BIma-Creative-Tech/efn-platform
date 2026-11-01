from pathlib import Path

import pytest

from efn_text2sql.config import ConfigError, Settings


@pytest.fixture
def files(tmp_path: Path) -> dict[str, str]:
    out = {}
    for name, value in {
        "api": "a" * 40,
        "scope": "0123456789abcdef" * 2,
        "chpw": "rahasia",
        "nr": "n" * 30,
        "crt": "x",
        "key": "y",
        "ca": "z",
    }.items():
        p = tmp_path / name
        p.write_text(value, encoding="utf-8")
        out[name] = str(p)
    layer = tmp_path / "layer.yaml"
    layer.write_text("tables: []\n", encoding="utf-8")
    out["layer"] = str(layer)
    return out


def env(files: dict[str, str], **kw: str) -> dict[str, str]:
    e = {
        "APP_ENV": "staging",
        "TLS_CERT_FILE": files["crt"],
        "TLS_KEY_FILE": files["key"],
        "TEXT2SQL_API_KEY_FILE": files["api"],
        "TEXT2SQL_SCOPE_KEY_FILE": files["scope"],
        "CH_PASSWORD_FILE": files["chpw"],
        "CH_CA_FILE": files["ca"],
        "OLLAMA_CA_FILE": files["ca"],
        "TEXT2SQL_SEMANTIC_LAYER": files["layer"],
    }
    e.update(kw)
    return e


def test_defaults_safe(files: dict[str, str]) -> None:
    s = Settings.from_env(env(files))
    assert s.enabled is False
    assert s.llm_provider == "ollama"
    assert s.ch_url == "https://clickhouse:8443"
    assert "rahasia" not in repr(s) and "a" * 40 not in repr(s)


def test_ninerouter_requires_cross_border(files: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        Settings.from_env(env(files, TEXT2SQL_LLM_PROVIDER="ninerouter_ollama", NINEROUTER_API_KEY_FILE=files["nr"]))
    s = Settings.from_env(
        env(
            files,
            TEXT2SQL_LLM_PROVIDER="ninerouter_ollama",
            TEXT2SQL_CROSS_BORDER_OK="true",
            NINEROUTER_API_KEY_FILE=files["nr"],
        ),
    )
    assert s.ninerouter_api_key == "n" * 30


@pytest.mark.parametrize(
    "override",
    [
        {"TLS_CERT_FILE": ""},
        {"TEXT2SQL_API_KEY_FILE": ""},
        {"CH_SECURE": "false"},
        {"TEXT2SQL_LLM_PROVIDER": "openai"},
        {"TEXT2SQL_ENABLED": "ya"},
        {"OLLAMA_URL": "http://ollama:11435"},
        {"EFN_ALLOW_PLAINTEXT": "true"},
        {"TEXT2SQL_OLLAMA_MODEL": "a b"},
    ],
)
def test_unsafe_config_rejected(files: dict[str, str], override: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        Settings.from_env(env(files, **override))


def test_bad_scope_key_rejected(files: dict[str, str], tmp_path: Path) -> None:
    p = tmp_path / "bad"
    p.write_text("XYZ", encoding="utf-8")
    with pytest.raises(ConfigError):
        Settings.from_env(env(files, TEXT2SQL_SCOPE_KEY_FILE=str(p)))


def test_plaintext_only_in_dev(files: dict[str, str]) -> None:
    s = Settings.from_env(
        env(files, APP_ENV="test", EFN_ALLOW_PLAINTEXT="true", TLS_CERT_FILE="", TLS_KEY_FILE="", CH_SECURE="false"),
    )
    assert s.ch_url.startswith("http://")


def test_agent_defaults_off_and_no_summary(files: dict[str, str]) -> None:
    s = Settings.from_env(env(files))
    assert (s.agent_enabled, s.agent_summary_provider, s.agent_results_cross_border_ok) == (False, "none", False)
    s = Settings.from_env(env(files, TEXT2SQL_AGENT_ENABLED="true", TEXT2SQL_AGENT_SUMMARY_PROVIDER="ollama"))
    assert s.agent_enabled and s.agent_summary_provider == "ollama"


def test_agent_summary_9router_needs_separate_results_consent(files: dict[str, str]) -> None:
    nr = {
        "TEXT2SQL_LLM_PROVIDER": "ninerouter_ollama",
        "TEXT2SQL_CROSS_BORDER_OK": "true",
        "NINEROUTER_API_KEY_FILE": files["nr"],
        "TEXT2SQL_AGENT_ENABLED": "true",
        "TEXT2SQL_AGENT_SUMMARY_PROVIDER": "ninerouter_ollama",
    }
    with pytest.raises(ConfigError, match="RESULTS_CROSS_BORDER_OK"):
        Settings.from_env(env(files, **nr))
    s = Settings.from_env(env(files, **nr, TEXT2SQL_AGENT_RESULTS_CROSS_BORDER_OK="true"))
    assert s.agent_summary_provider == "ninerouter_ollama"
    # izin hasil saja tanpa jalur 9router SQL (tanpa egress/key) ditolak
    with pytest.raises(ConfigError):
        Settings.from_env(
            env(
                files,
                TEXT2SQL_AGENT_SUMMARY_PROVIDER="ninerouter",
                TEXT2SQL_AGENT_RESULTS_CROSS_BORDER_OK="true",
            ),
        )
    with pytest.raises(ConfigError):
        Settings.from_env(env(files, TEXT2SQL_AGENT_SUMMARY_PROVIDER="openai"))
