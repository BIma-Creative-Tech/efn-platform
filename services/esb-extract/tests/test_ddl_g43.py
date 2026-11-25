"""DDL G43 (14-g43-migration.sql) cocok dengan kolom transform_mig; map_esb_odoo kunci baru; migrasi kunci lama aman."""

import re
from pathlib import Path

from efn_esb.runner import ALL_TABLES
from efn_esb.transform import MART_TABLES
from efn_esb.transform_g65 import G65_TABLES
from efn_esb.transform_g68 import G68_TABLES
from efn_esb.transform_mig import MIG_TABLES

ROOT = Path(__file__).resolve().parents[3]
CH = ROOT / "infra" / "clickhouse"
DDL = (CH / "init" / "14-g43-migration.sql").read_text(encoding="utf-8")
DDL12 = (CH / "init" / "12-g41-esb.sql").read_text(encoding="utf-8")
INIT_SH = (CH / "clickhouse-init.sh").read_text(encoding="utf-8")
ESB_ACCESS = (CH / "access" / "esb-access.sql").read_text(encoding="utf-8")
METABASE = (CH / "access" / "metabase-access.sql").read_text(encoding="utf-8")
INSIGHT = (CH / "access" / "insight-access.sql").read_text(encoding="utf-8")


def block(qualified: str) -> str:
    return DDL.split(f"CREATE TABLE IF NOT EXISTS {qualified}\n", 1)[1].split(";", 1)[0]


def test_mig_columns_match_transform() -> None:
    for table, columns in MIG_TABLES.items():
        body = block(f"efn_dwh.{table}").split("ENGINE", 1)[0]
        for column in columns:
            assert re.search(rf"^\s+{column}\s", body, re.MULTILINE), (table, column)
        assert re.search(r"^\s+_loaded_at\s", body, re.MULTILINE), table
        assert "ReplacingMergeTree(_version)" in block(f"efn_dwh.{table}"), table


def test_tables_exact_idempotent_and_no_destructive_ddl() -> None:
    tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS (efn_\w+\.\w+)", DDL))
    assert tables == {f"efn_dwh.{t}" for t in MIG_TABLES} | {"efn_dwh.map_esb_odoo"}
    assert all("esb" in t.split(".")[1] for t in tables)
    assert not re.search(r"\bDROP\b|\bTRUNCATE\b|\bALTER\b", DDL)
    assert not set(MIG_TABLES) & set(MART_TABLES)
    assert set(ALL_TABLES) == set(MIG_TABLES) | set(MART_TABLES) | set(G65_TABLES) | set(G68_TABLES)


def test_map_esb_odoo_new_key_only_in_g43() -> None:
    body = block("efn_dwh.map_esb_odoo")
    assert "ORDER BY (brand, company_code, entity, esb_key)" in body
    for column in ("odoo_model", "odoo_xmlid", "odoo_id", "run_id", "_version"):
        assert re.search(rf"^\s+{column}\s", body, re.MULTILINE), column
    assert "run_id        UUID" in body
    assert "CREATE TABLE IF NOT EXISTS efn_dwh.map_esb_odoo" not in DDL12


def test_init_drops_old_map_only_when_empty_and_before_schema() -> None:
    after = INIT_SH.split('MAP_NEW_KEY="brand, company_code, entity, esb_key"', 1)[1]
    migration = after.split("for f in /init/schema", 1)[0]
    assert 'if [ "$MAP_ROWS" != "0" ]; then' in migration
    assert "exit 1" in migration.split("DROP TABLE", 1)[0]  # berisi -> gagal SEBELUM drop
    assert "DROP TABLE efn_dwh.map_esb_odoo SYNC" in migration
    assert INIT_SH.index("MAP_NEW_KEY=") < INIT_SH.index("for f in /init/schema/*.sql")


def test_new_tables_not_exposed_to_analysts() -> None:
    for table in [*MIG_TABLES, "map_esb_odoo"]:
        assert f"efn_dwh.{table} " not in METABASE and f"efn_dwh.{table} TO metabase_reader" not in ESB_ACCESS, table
        assert f"efn_dwh.{table}" not in INSIGHT, table


def test_customer_table_has_no_contact_columns() -> None:
    body = block("efn_dwh.dim_esb_customer").split("ENGINE", 1)[0].lower()
    for forbidden in ("address", "phone", "email", "pic", "npwp", "bank"):
        assert not re.search(rf"^\s+\w*{forbidden}\w*\s", body, re.MULTILINE), forbidden
