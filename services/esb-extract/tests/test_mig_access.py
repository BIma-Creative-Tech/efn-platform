"""G43 (docs/04 §27.10): akses ClickHouse user `efn_mig` (role mig_rw) -- statis, tanpa ClickHouse.

Wajib: efn_mig tidak bisa INSERT tabel selain map_esb_odoo, tidak bisa membaca raw_doc / stock_snapshot / mart Odoo,
tidak punya DDL. Uji perilaku (INSERT ditolak, SELECT raw_doc ditolak, CREATE ditolak) dijalankan lead di ClickHouse
24.8 (lihat laporan efn-data-ai-engineer bagian G43).
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CH = ROOT / "infra" / "clickhouse"
ACCESS = (CH / "access" / "mig-access.sql").read_text(encoding="utf-8")
INIT_SH = (CH / "clickhouse-init.sh").read_text(encoding="utf-8")
DDL12 = (CH / "init" / "12-g41-esb.sql").read_text(encoding="utf-8")
DDL14 = (CH / "init" / "14-g43-migration.sql").read_text(encoding="utf-8")


def strip_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def view_body(name: str) -> str:
    header = f"CREATE OR REPLACE VIEW efn_dwh.{name} AS"
    return strip_comments(DDL12.split(header, 1)[1].split(";", 1)[0])


def referenced(name: str, seen: set[str] | None = None) -> set[str]:
    seen = set() if seen is None else seen
    for ref in re.findall(r"\b(efn_(?:dwh|esb)\.\w+)", view_body(name)):
        if ref not in seen:
            seen.add(ref)
            if ref.startswith("efn_dwh.v_"):
                referenced(ref.split(".")[1], seen)
    return seen


def grants(privileges: str) -> set[str]:
    return set(re.findall(rf"^GRANT {privileges} ON (efn_\w+\.\w+) TO mig_rw;$", ACCESS, re.MULTILINE))


def test_only_map_esb_odoo_is_writable() -> None:
    assert grants("SELECT, INSERT") == {"efn_dwh.map_esb_odoo"}
    lines = [ln for ln in strip_comments(ACCESS).splitlines() if "TO mig_rw" in ln]
    assert all(re.fullmatch(r"GRANT (SELECT|SELECT, INSERT) ON efn_\w+\.\w+ TO mig_rw;", ln) for ln in lines), lines
    assert not re.search(r"GRANT (ALL|ALTER|DROP|TRUNCATE|CREATE|OPTIMIZE|INSERT ON)", ACCESS)


def test_reads_exactly_needed_objects_without_raw() -> None:
    read = grants("SELECT")
    assert "efn_esb.raw_doc" not in read
    assert "efn_dwh.fact_esb_stock_snapshot" not in read
    assert all(r.startswith("efn_esb.") or "esb" in r for r in read), read  # tanpa mart Odoo/GL/audit
    g43 = set(re.findall(r"CREATE TABLE IF NOT EXISTS (efn_dwh\.\w+)", DDL14)) - {"efn_dwh.map_esb_odoo"}
    assert g43 <= read
    for view in ("v_esb_sales_daily", "v_esb_payment_daily", "v_esb_flow_daily", "v_esb_data_quality"):
        assert f"efn_dwh.{view}" in read, view
        missing = {r for r in referenced(view) if not r.startswith("efn_dwh.v_")} - read
        assert not missing, (view, missing)  # view INVOKER butuh hak tabel dasar


def test_profile_blocks_ddl() -> None:
    for stmt in ("CREATE SETTINGS PROFILE IF NOT EXISTS mig_profile", "ALTER SETTINGS PROFILE mig_profile"):
        body = ACCESS.split(stmt, 1)[1].split(";", 1)[0]
        assert "allow_ddl = 0 CONST" in body and "readonly = 0 CONST" in body
        assert "allow_introspection_functions = 0 CONST" in body
        assert "max_execution_time = 600" in body


def test_init_creates_user_only_with_real_secret_and_binds_role() -> None:
    assert "MIG_SECRET=/run/secrets/clickhouse_password_mig" in INIT_SH
    assert 'create_user efn_mig "$MIG_SECRET"' in INIT_SH
    assert "DROP USER IF EXISTS efn_mig" in INIT_SH
    assert "''|disabled|changeme" in INIT_SH  # placeholder secrets.list -> user dihapus
    bind = INIT_SH.split("GRANT mig_rw TO efn_mig;", 1)
    assert len(bind) == 2 and "ALTER USER efn_mig SETTINGS PROFILE 'mig_profile';" in bind[1]
    # Pengikatan hanya bila user dibuat (tanpa user, GRANT ke user tak ada = init gagal).
    assert 'if [ "$MIG_ENABLED" = "1" ]; then' in INIT_SH.split("GRANT mig_rw TO efn_mig;", 1)[0][-200:]
