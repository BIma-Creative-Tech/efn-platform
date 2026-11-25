"""DDL `infra/postgres/init/24-prodplan-esb-login.sql` sesuai §30.17.8 (baca source; di-skip bila repo root tak ada)."""

import re
from pathlib import Path

import pytest

SQL_PATH = Path(__file__).resolve().parents[3] / "infra" / "postgres" / "init" / "24-prodplan-esb-login.sql"
if not SQL_PATH.exists():
    pytest.skip("infra/postgres/init tidak terpasang di container test", allow_module_level=True)
SQL = SQL_PATH.read_text(encoding="utf-8")


def test_guard_aborts_first_application_with_open_plans():
    guard = SQL.split("RAISE EXCEPTION '", 1)[0]
    assert "to_regclass('prodplan.actor') IS NULL" in guard
    assert "status IN ('draft','submitted','approved')" in guard
    assert "NOT IN ('confirmed','failed','deleted','delete_failed')" in guard


def test_actor_table_contract():
    ddl = SQL.split("CREATE TABLE IF NOT EXISTS prodplan.actor (", 1)[1].split(");", 1)[0]
    assert "GENERATED ALWAYS AS IDENTITY (START WITH 1000000)" in ddl
    assert "UNIQUE (esb_env, esb_brand, username_norm)" in ddl
    assert "username_norm ~ '^[a-z0-9._@-]{1,64}$'" in ddl
    for forbidden in ("password", "token", "role_id", "branch"):
        assert forbidden not in ddl


def test_outbox_run_as_and_needs_login_status():
    assert "ADD COLUMN IF NOT EXISTS run_as_sub integer" in SQL
    check = re.search(r"outbox_status_check_v2 CHECK \(status IN \((.*?)\)\)", SQL, re.S)
    assert check is not None
    statuses = set(re.findall(r"'([a-z_]+)'", check.group(1)))
    assert "needs_login" in statuses and {"pending", "sent", "delete_failed", "deleted"} <= statuses
    assert "CREATE INDEX" not in SQL and "DROP INDEX" not in SQL  # ix_outbox_work tidak diubah (tanpa needs_login)


def test_grants_least_privilege():
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON prodplan.actor TO efn_prodplan;" in SQL
    assert "TRUNCATE ON" not in SQL and "SUPERUSER" not in SQL
    assert SQL.count("BEGIN;") == 1 and "COMMIT;" in SQL
