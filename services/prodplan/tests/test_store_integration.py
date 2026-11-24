"""Integrasi PostgreSQL nyata (di-skip tanpa `PRODPLAN_TEST_DSN`).

Prasyarat: DB sudah dimigrasi `infra/postgres/init/23-prodplan-g66.sql` dan DSN memakai role `efn_prodplan` (DML saja).
Contoh: PRODPLAN_TEST_DSN="host=postgres dbname=efn_prodplan user=efn_prodplan password=... sslmode=verify-full
sslrootcert=/run/secrets/efn_ca_crt". Data uji dibuat dan dihapus sendiri (esb_env staging, plan_no PP-TEST-*).
"""

import os
import uuid
from contextlib import contextmanager
from datetime import date

import pytest

DSN = os.environ.get("PRODPLAN_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="PRODPLAN_TEST_DSN tidak di-set")


@pytest.fixture()
def db():
    psycopg = pytest.importorskip("psycopg")
    from psycopg.rows import dict_row

    from efn_prodplan.config import PgSettings
    from efn_prodplan.store import Database, Tx

    class DsnDatabase(Database):
        @contextmanager
        def tx(self):
            conn = psycopg.connect(DSN, autocommit=True, row_factory=dict_row)
            try:
                with conn.transaction(), conn.cursor() as cur:
                    yield Tx(cur)
            finally:
                conn.close()

    return DsnDatabase(PgSettings())


def _plan(db, created_by=1):
    with db.tx() as tx:
        row = tx.one(
            "INSERT INTO prodplan.plan (plan_no, company_code, esb_brand, esb_env, branch_id, production_location_id,"
            " production_date, created_by_sub, created_by_name) VALUES (%s,'PB','pb','staging',4,7,%s,%s,'Uji')"
            " RETURNING id",
            (f"PP-TEST-{uuid.uuid4().hex[:8]}", date(2026, 11, 25), created_by),
        )
    return row["id"]


def _cleanup(db, plan_id):
    with db.tx() as tx:
        tx.exec("DELETE FROM prodplan.outbox WHERE plan_id = %s", (plan_id,))
        tx.exec("DELETE FROM prodplan.plan WHERE id = %s", (plan_id,))


def test_sod_check_constraint(db):
    import psycopg

    plan_id = _plan(db, created_by=1)
    try:
        with pytest.raises(psycopg.errors.CheckViolation), db.tx() as tx:
            tx.exec("UPDATE prodplan.plan SET submitted_by_sub = 2, approved_by_sub = 1 WHERE id = %s", (plan_id,))
        with pytest.raises(psycopg.errors.CheckViolation), db.tx() as tx:
            tx.exec("UPDATE prodplan.plan SET submitted_by_sub = 2, approved_by_sub = 2 WHERE id = %s", (plan_id,))
        with db.tx() as tx:
            tx.exec("UPDATE prodplan.plan SET submitted_by_sub = 2, approved_by_sub = 3 WHERE id = %s", (plan_id,))
    finally:
        _cleanup(db, plan_id)


def test_outbox_recover_and_claim(db):
    from datetime import UTC, datetime, timedelta

    from efn_prodplan.store import PgOutboxStore, jsonb

    plan_id = _plan(db)
    store = PgOutboxStore(db)
    try:
        with db.tx() as tx:
            row = tx.one(
                "INSERT INTO prodplan.outbox (company_code, esb_brand, esb_env, plan_id, seq, phase, doc_kind, marker,"
                " request_body, status, created_by_sub) VALUES ('PB','pb','staging',%s,1,'transfers','simple_transfer',"
                " %s, %s, 'sending', 1) RETURNING id",
                (plan_id, "pp:" + uuid.uuid4().hex[:12], jsonb({"a": 1})),
            )
        assert store.recover_sending() >= 1
        rows = [r for r in store.candidates("staging", datetime.now(UTC)) if r["id"] == row["id"]]
        assert rows and rows[0]["status"] == "unknown"
        lease = datetime.now(UTC) + timedelta(seconds=60)
        assert store.claim(row["id"], "unknown", None, lease)
        assert not store.claim(row["id"], "pending", "sending", lease)
        store.update(row["id"], {"status": "failed", "last_error_code": "not_found_after_timeout"})
        counts, _ = store.status_counts("staging")
        assert counts.get("failed", 0) >= 1
    finally:
        _cleanup(db, plan_id)


def test_audit_event_is_append_only_for_app_role(db):
    import psycopg

    from efn_prodplan.store import audit

    with db.tx() as tx:
        audit(tx, "PB", None, 1, "Uji", "integration_test", {"x": 1})
    with pytest.raises(psycopg.errors.InsufficientPrivilege), db.tx() as tx:
        tx.exec("DELETE FROM prodplan.audit_event WHERE action = 'integration_test'")
    with db.tx() as tx:
        row = tx.one("SELECT prodplan.purge_expired_audit_events() AS n")
    assert row is not None
