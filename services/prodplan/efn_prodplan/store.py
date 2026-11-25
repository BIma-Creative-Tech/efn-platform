"""Akses PostgreSQL `efn_prodplan` (docs/04 §30.6). Driver resmi psycopg 3, TLS `verify-full` dengan CA internal.

- Role aplikasi `efn_prodplan` hanya DML (tanpa DDL): skema dibuat migrasi `infra/postgres/init/23-prodplan-g66.sql`
  + `24-prodplan-esb-login.sql` (§30.17.8: `prodplan.actor`, `outbox.run_as_sub`, status `needs_login`)
  (superuser + `SET ROLE efn_prodplan_owner`, dijalankan `deploy.sh`) = satu-satunya sumber DDL; aplikasi tidak migrasi.
- Satu koneksi per unit kerja (volume rendah, replika 1) -> tanpa pool/dependency tambahan.
- `OutboxStore` / `JobStore` = antarmuka yang dipakai worker; test memakai implementasi palsu di memori.
"""

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Protocol

from efn_prodplan.config import PgSettings

OUTBOX_WORK_STATUSES = ("pending", "sending", "sent", "unknown", "delete_pending", "deleting")


class Tx:
    """Pembungkus kursor psycopg (row dict) dalam satu transaksi."""

    def __init__(self, cursor: Any) -> None:
        self._cur = cursor

    def one(self, sql: str, params: Mapping[str, Any] | Sequence[Any] | None = None) -> dict[str, Any] | None:
        self._cur.execute(sql, params)
        row = self._cur.fetchone()
        return dict(row) if row is not None else None

    def all(self, sql: str, params: Mapping[str, Any] | Sequence[Any] | None = None) -> list[dict[str, Any]]:
        self._cur.execute(sql, params)
        return [dict(r) for r in self._cur.fetchall()]

    def exec(self, sql: str, params: Mapping[str, Any] | Sequence[Any] | None = None) -> int:
        self._cur.execute(sql, params)
        return int(self._cur.rowcount or 0)


def jsonb(value: Any) -> Any:
    from psycopg.types.json import Jsonb

    return Jsonb(value)


class Database:
    def __init__(self, pg: PgSettings) -> None:
        self._pg = pg

    def __repr__(self) -> str:
        return f"Database(host={self._pg.host!r}, db={self._pg.dbname!r}, user={self._pg.user!r})"

    def _connect(self) -> Any:
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(
            host=self._pg.host,
            port=self._pg.port,
            dbname=self._pg.dbname,
            user=self._pg.user,
            password=self._pg.password,
            sslmode=self._pg.sslmode,
            sslrootcert=self._pg.sslrootcert,
            connect_timeout=5,
            application_name="prodplan",
            options="-c statement_timeout=15000 -c lock_timeout=5000",
            autocommit=True,
            row_factory=dict_row,
        )

    @contextmanager
    def tx(self) -> Iterator[Tx]:
        conn = self._connect()
        try:
            with conn.transaction(), conn.cursor() as cur:
                yield Tx(cur)
        finally:
            conn.close()

    def ready(self) -> bool:
        try:
            with self.tx() as tx:
                tx.one("SELECT 1 AS ok FROM prodplan.plan LIMIT 0")
            return True
        except Exception:  # noqa: BLE001 - readiness: kegagalan apa pun = belum siap
            return False


def audit(
    tx: Tx,
    company_code: str,
    plan_id: Any,
    actor_sub: int | None,
    actor_name: str | None,
    action: str,
    detail: Mapping[str, Any] | None = None,
    request_id: str | None = None,
) -> None:
    tx.exec(
        "INSERT INTO prodplan.audit_event (company_code, plan_id, actor_sub, actor_name, action, detail, request_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            company_code,
            plan_id,
            actor_sub,
            actor_name[:64] if actor_name else None,
            action[:40],
            jsonb(dict(detail or {})),
            request_id,
        ),
    )


# ====================================================================== antarmuka worker
class OutboxStore(Protocol):
    def recover_sending(self) -> int: ...

    def candidates(self, esb_env: str, now: datetime) -> list[dict[str, Any]]: ...

    def claim(self, outbox_id: Any, expected_status: str, new_status: str | None, lease_until: datetime) -> bool: ...

    def update(self, outbox_id: Any, fields: Mapping[str, Any]) -> None: ...

    def known_numbers(self, esb_env: str, doc_kind: str) -> set[str]: ...

    def audit(self, row: Mapping[str, Any], action: str, detail: Mapping[str, Any]) -> None: ...

    def status_counts(self, esb_env: str) -> tuple[dict[str, int], float]: ...


_OUTBOX_FIELDS = frozenset(
    {
        "status",
        "attempts",
        "esb_doc_number",
        "esb_status_id",
        "last_http_status",
        "last_error_code",
        "next_attempt_at",
        "sent_at",
        "confirmed_at",
        "deleted_at",
    }
)


def outbox_update_sql(outbox_id: Any, fields: Mapping[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """UPDATE outbox dari kolom allowlist (SEC-226, ditegakkan di SQL, bukan hanya di worker):
    - `esb_doc_number` non-NULL tidak pernah ditimpa (`COALESCE(kolom lama, nilai baru)`);
    - baris yang sudah ber-nomor tidak pernah berpindah ke `failed`/`pending`/`unknown` (status lama dipertahankan)."""
    cols = [k for k in fields if k in _OUTBOX_FIELDS]
    if not cols:
        return None
    parts: list[str] = []
    for c in cols:  # nama kolom dari allowlist, bukan input
        if c == "esb_doc_number":
            parts.append("esb_doc_number = COALESCE(esb_doc_number, %(esb_doc_number)s)")
        elif c == "status":
            parts.append(
                "status = CASE WHEN esb_doc_number IS NOT NULL AND %(status)s IN ('failed','pending','unknown')"
                " THEN status ELSE %(status)s END"
            )
        else:
            parts.append(f"{c} = %({c})s")
    params = {c: fields[c] for c in cols}
    params["id"] = outbox_id
    sql = f"UPDATE prodplan.outbox SET {', '.join(parts)}, updated_at = now() WHERE id = %(id)s"  # noqa: S608
    return sql, params


class PgOutboxStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def recover_sending(self) -> int:
        """§30.9: `sending` yang ditemukan saat start (crash) diperlakukan `unknown`; `deleting` dicek ulang."""
        with self._db.tx() as tx:
            return tx.exec(
                "UPDATE prodplan.outbox SET status = 'unknown', sent_at = COALESCE(sent_at, now()),"
                " next_attempt_at = now(), updated_at = now() WHERE status = 'sending'"
            )

    def candidates(self, esb_env: str, now: datetime) -> list[dict[str, Any]]:
        """Semua baris outbox dari rencana yang punya kerja jatuh tempo (urutan seq dipilih `outbox.pick_next`)."""
        with self._db.tx() as tx:
            return tx.all(
                "SELECT o.id, o.company_code, o.esb_brand, o.esb_env, o.plan_id, o.seq, o.phase, o.doc_kind,"
                " o.marker, o.request_body, o.status, o.attempts, o.esb_doc_number, o.next_attempt_at, o.sent_at,"
                " o.last_error_code, o.created_by_sub, o.run_as_sub,"
                " p.production_date, p.branch_id, p.production_location_id"
                " FROM prodplan.outbox o JOIN prodplan.plan p ON p.id = o.plan_id"
                " WHERE o.esb_env = %(env)s AND o.plan_id IN ("
                "   SELECT plan_id FROM prodplan.outbox WHERE esb_env = %(env)s AND next_attempt_at <= %(now)s"
                "   AND status IN ('pending','sending','sent','unknown','delete_pending','deleting'))"
                " ORDER BY o.plan_id, o.seq",
                {"env": esb_env, "now": now},
            )

    def claim(self, outbox_id: Any, expected_status: str, new_status: str | None, lease_until: datetime) -> bool:
        """Kunci baris (SKIP LOCKED) bila status masih `expected_status`; commit status baru SEBELUM memanggil ESB."""
        with self._db.tx() as tx:
            # SEC-230: kirim (`sending`) hanya bila rencana belum dibatalkan; `cancel` mengunci baris outbox rencana
            # (FOR UPDATE) sehingga claim yang bersamaan dilewati (SKIP LOCKED) atau melihat status `failed`.
            guard = (
                " AND NOT EXISTS (SELECT 1 FROM prodplan.plan p WHERE p.id = prodplan.outbox.plan_id"
                " AND p.status = 'cancelled')"
                if new_status == "sending"
                else ""
            )
            row = tx.one(
                "SELECT id FROM prodplan.outbox WHERE id = %s AND status = %s"  # noqa: S608 - potongan SQL tetap
                f"{guard} FOR UPDATE SKIP LOCKED",
                (outbox_id, expected_status),
            )
            if row is None:
                return False
            if new_status == "sending":
                tx.exec(
                    "UPDATE prodplan.outbox SET status = 'sending', attempts = attempts + 1, sent_at = now(),"
                    " next_attempt_at = %s, updated_at = now() WHERE id = %s",
                    (lease_until, outbox_id),
                )
            elif new_status is not None:
                tx.exec(
                    "UPDATE prodplan.outbox SET status = %s, next_attempt_at = %s, updated_at = now() WHERE id = %s",
                    (new_status, lease_until, outbox_id),
                )
            else:
                tx.exec(
                    "UPDATE prodplan.outbox SET next_attempt_at = %s, updated_at = now() WHERE id = %s",
                    (lease_until, outbox_id),
                )
            return True

    def update(self, outbox_id: Any, fields: Mapping[str, Any]) -> None:
        built = outbox_update_sql(outbox_id, fields)
        if built is None:
            return
        with self._db.tx() as tx:
            tx.exec(*built)

    def known_numbers(self, esb_env: str, doc_kind: str) -> set[str]:
        with self._db.tx() as tx:
            rows = tx.all(
                "SELECT esb_doc_number FROM prodplan.outbox WHERE esb_env = %s AND doc_kind = %s"
                " AND esb_doc_number IS NOT NULL AND status <> 'deleted'",  # ESB memakai ulang nomor yang dihapus
                (esb_env, doc_kind),
            )
        return {r["esb_doc_number"] for r in rows}

    def audit(self, row: Mapping[str, Any], action: str, detail: Mapping[str, Any]) -> None:
        with self._db.tx() as tx:
            audit(tx, row["company_code"], row["plan_id"], None, "worker", action, detail)

    def status_counts(self, esb_env: str) -> tuple[dict[str, int], float]:
        with self._db.tx() as tx:
            rows = tx.all(
                "SELECT status, count(*) AS n FROM prodplan.outbox WHERE esb_env = %s GROUP BY status", (esb_env,)
            )
            oldest = tx.one(
                "SELECT COALESCE(EXTRACT(EPOCH FROM now() - min(created_at)), 0) AS age FROM prodplan.outbox"
                " WHERE esb_env = %s AND status IN ('pending','unknown')",
                (esb_env,),
            )
        return {r["status"]: int(r["n"]) for r in rows}, float((oldest or {}).get("age") or 0)


class PgActorStore:
    """`prodplan.actor` (§30.17.8): satu baris per (esb_env, esb_brand, username_norm); id = `*_sub` di semua tabel."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(self, esb_env: str, esb_brand: str, username_norm: str, display_name: str) -> int:
        with self._db.tx() as tx:
            row = tx.one(
                "INSERT INTO prodplan.actor (esb_env, esb_brand, username_norm, display_name) VALUES (%s,%s,%s,%s)"
                " ON CONFLICT (esb_env, esb_brand, username_norm)"
                " DO UPDATE SET display_name = EXCLUDED.display_name, last_login_at = now() RETURNING id",
                (esb_env, esb_brand, username_norm, display_name[:64]),
            )
        assert row is not None
        return int(row["id"])


# §30.17.13: aktor yang tidak dirujuk tabel mana pun dan `last_login_at` > 400 hari dihapus purge harian.
PURGE_ACTORS_SQL = (
    "DELETE FROM prodplan.actor a WHERE a.last_login_at < now() - interval '400 days'"
    " AND NOT EXISTS (SELECT 1 FROM prodplan.plan p WHERE a.id IN"
    " (p.created_by_sub, p.submitted_by_sub, p.approved_by_sub, p.posted_by_sub, p.cancelled_by_sub))"
    " AND NOT EXISTS (SELECT 1 FROM prodplan.plan_demand d WHERE d.created_by_sub = a.id)"
    " AND NOT EXISTS (SELECT 1 FROM prodplan.plan_suggestion s WHERE s.decided_by_sub = a.id)"
    " AND NOT EXISTS (SELECT 1 FROM prodplan.plan_transfer t WHERE t.decided_by_sub = a.id)"
    " AND NOT EXISTS (SELECT 1 FROM prodplan.job j WHERE j.requested_by_sub = a.id)"
    " AND NOT EXISTS (SELECT 1 FROM prodplan.outbox o WHERE a.id IN (o.created_by_sub, o.run_as_sub))"
    " AND NOT EXISTS (SELECT 1 FROM prodplan.audit_event e WHERE e.actor_sub = a.id)"
    " AND NOT EXISTS (SELECT 1 FROM prodplan.pack_rule r WHERE a.id IN (r.created_by_sub, r.updated_by_sub))"
)


class JobStore(Protocol):
    def recover_running(self) -> int: ...

    def claim_job(self) -> dict[str, Any] | None: ...

    def finish_job(
        self, job_id: Any, status: str, result: Mapping[str, Any] | None, error_code: str | None, esb_calls: int
    ) -> None: ...


class PgJobStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def recover_running(self) -> int:
        with self._db.tx() as tx:
            return tx.exec(
                "UPDATE prodplan.job SET status = 'failed', error_code = 'interrupted', finished_at = now()"
                " WHERE status = 'running'"
            )

    def claim_job(self) -> dict[str, Any] | None:
        with self._db.tx() as tx:
            row = tx.one(
                "SELECT id, company_code, plan_id, kind, params, requested_by_sub FROM prodplan.job"
                " WHERE status = 'queued' ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
            )
            if row is None:
                return None
            tx.exec("UPDATE prodplan.job SET status = 'running', started_at = now() WHERE id = %s", (row["id"],))
            return row

    def finish_job(
        self, job_id: Any, status: str, result: Mapping[str, Any] | None, error_code: str | None, esb_calls: int
    ) -> None:
        with self._db.tx() as tx:
            tx.exec(
                "UPDATE prodplan.job SET status = %s, result = %s, error_code = %s, esb_calls = %s,"
                " finished_at = now() WHERE id = %s",
                (status, jsonb(dict(result)) if result is not None else None, error_code, esb_calls, job_id),
            )


def purge(db: Database, retention_days: int) -> dict[str, int]:
    """§30.10. `audit_event` hanya lewat fungsi SECURITY DEFINER `prodplan.purge_expired_audit_events()` (batas 1825
    hari di SQL migrasi 23-prodplan-g66.sql); role aplikasi tidak punya DELETE pada tabel itu."""
    out: dict[str, int] = {}
    with db.tx() as tx:
        row = tx.one("SELECT prodplan.purge_expired_audit_events() AS n")
        out["audit_event"] = int((row or {}).get("n") or 0)
        out["stock_snapshot"] = tx.exec(
            "DELETE FROM prodplan.stock_snapshot WHERE fetched_at < now() - interval '30 days'"
        )
        out["outbox"] = tx.exec(
            "DELETE FROM prodplan.outbox WHERE created_at < now() - interval '1825 days'"
            " AND status IN ('confirmed','failed','deleted','delete_failed')"
        )
        out["plan"] = tx.exec(
            "DELETE FROM prodplan.plan p WHERE p.production_date < (now() AT TIME ZONE 'Asia/Jakarta')::date - %s"
            " AND NOT EXISTS (SELECT 1 FROM prodplan.outbox o WHERE o.plan_id = p.id)",
            (retention_days,),
        )
        out["actor"] = tx.exec(PURGE_ACTORS_SQL)
    return out
