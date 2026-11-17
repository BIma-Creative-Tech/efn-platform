"""Kontrak lintas-berkas (QA G41): env yang dibaca config.py harus diteruskan overlay compose dan terdokumentasi
(docs/04 §25.2); alias `NAME:` di-resolve view; nama secret konsisten antara kode, overlay, dan secrets.list."""

import re
from pathlib import Path

from efn_esb.config import KNOWN_BRANDS

ROOT = Path(__file__).resolve().parents[3]
CONFIG = (ROOT / "services" / "esb-extract" / "efn_esb" / "config.py").read_text(encoding="utf-8")
OVERLAY = (ROOT / "infra" / "compose" / "docker-compose.esb.yml").read_text(encoding="utf-8")
CONTRACT = (ROOT / "docs" / "04_SERVICE_CONTRACTS.md").read_text(encoding="utf-8")
SECRETS_LIST = (ROOT / "scripts" / "secrets.list").read_text(encoding="utf-8")
DDL = (ROOT / "infra" / "clickhouse" / "init" / "12-g41-esb.sql").read_text(encoding="utf-8")

# Hanya dipakai CLI/uji (bukan konfigurasi operator di compose).
NOT_FORWARDED = {"ESB_RUN_ONCE", "ESB_HEARTBEAT_FILE"}


def config_env_names() -> set[str]:
    """Nama env literal yang dibaca config.py (yang berpola f-string ditangani terpisah)."""
    return set(re.findall(r'"(ESB_[A-Z0-9_]+)"', CONFIG))


def test_every_config_env_is_forwarded_by_overlay() -> None:
    missing = sorted(n for n in config_env_names() - NOT_FORWARDED if not re.search(rf"^\s+{n}:", OVERLAY, re.M))
    assert not missing, f"env dibaca kode tetapi tidak diteruskan overlay (nilai .env tak berpengaruh): {missing}"


def test_every_config_env_is_documented_in_contract() -> None:
    missing = sorted(n for n in config_env_names() - {"ESB_RUN_ONCE"} if f"`{n}`" not in CONTRACT)
    assert not missing, f"env tidak terdokumentasi di docs/04 §25.2: {missing}"


def test_brand_user_password_and_secret_names_are_consistent() -> None:
    for brand in KNOWN_BRANDS:
        upper = brand.upper()
        assert re.search(rf"^\s+ESB_USER_{upper}:", OVERLAY, re.M), brand
        assert f"ESB_PASSWORD_{upper}_FILE: /run/secrets/esb_core_password_{brand}\n" in OVERLAY, brand
        assert re.search(rf"^\s+- esb_core_password_{brand}$", OVERLAY, re.M), brand
        assert f"esb_core_password_{brand} " in SECRETS_LIST, brand


def test_flow_view_resolves_name_alias_through_dim_alias() -> None:
    flow = DDL.split("CREATE OR REPLACE VIEW efn_dwh.v_esb_flow_daily AS", 1)[1].split(";", 1)[0]
    # 'NAME:' = 5 karakter -> alias_norm mulai dari posisi 6 (1-based).
    # transfer: asal + tujuan
    assert flow.count("startsWith(tr.product_code, 'NAME:'), substring(tr.product_code, 6)") == 2
    assert "startsWith(f.product_code, 'NAME:'), substring(f.product_code, 6)" in flow  # fin_sold
    assert "al.brand = r.brand AND al.alias_norm = r.alias_key" in flow
    # Alias tak ketemu -> kode mentah dipertahankan (NAME:...) agar muncul di v_esb_data_quality.
    assert "if(al.product_code = '', r.code_raw, al.product_code) AS p_code" in flow
