"""`efn_core.scope`: penanda jalur internal ber-token (SEC-113/126) dan kunci baris `touch_row` (SEC-127).

Uji SEC-127 memakai DUA koneksi PostgreSQL nyata (``odoo.sql_db.db_connect``, isolasi REPEATABLE READ seperti
request Odoo) yang commit sungguhan, bukan spy. Baris yang disentuh: negara Indonesia (`base.id`, data install
ter-commit); UPDATE no-op tidak mengubah nilai apa pun.
"""

import threading
import time

import psycopg2.errors

from odoo.sql_db import db_connect
from odoo.tests import TransactionCase, tagged
from odoo.tools import SQL, mute_logger

from odoo.addons.efn_core.scope import in_scope, internal_scope, scope_tokens, touch_row

KEY = "efn_test_scope"


@tagged("post_install", "-at_install", "efn")
class TestEfnScope(TransactionCase):
    def test_tokens_nesting_and_cleanup(self):
        self.assertFalse(in_scope(self.env, KEY))
        with internal_scope(self.env, KEY, {("id", 1)}) as outer:
            with internal_scope(self.env, KEY, {("uuid", "u-2")}):
                self.assertEqual(scope_tokens(self.env, KEY), {("id", 1), ("uuid", "u-2")})
            self.assertEqual(scope_tokens(self.env, KEY), {("id", 1)})
            outer.add(("id", 3))
            self.assertEqual(scope_tokens(self.env, KEY), {("id", 1), ("id", 3)})
        self.assertFalse(in_scope(self.env, KEY))
        self.assertEqual(scope_tokens(self.env, KEY), set())
        with self.assertRaises(ValueError), internal_scope(self.env, KEY, {("id", 9)}):
            raise ValueError("gagal di tengah jalur")
        self.assertFalse(in_scope(self.env, KEY), "penanda dibersihkan walau ada exception")
        # Context RPC tidak berpengaruh.
        self.assertFalse(in_scope(self.env(context={KEY: True}), KEY))

    # ------------------------------------------------------------- SEC-127
    def _two_cursors(self):
        dbname = self.env.cr.dbname
        cursors = [db_connect(dbname).cursor(), db_connect(dbname).cursor()]
        for cr in cursors:
            self.addCleanup(cr.close)  # close tanpa commit = rollback
            cr.execute("SET lock_timeout = '15s'")
        return cursors

    def _row(self):
        return "res_country", self.env.ref("base.id").id

    def test_touch_row_second_transaction_fails_serialization_after_commit(self):
        """Refund B sudah membaca snapshot sebelum refund A commit -> B gagal serialisasi (Odoo me-retry)."""
        table, row_id = self._row()
        cr_a, cr_b = self._two_cursors()
        cr_b.execute("SELECT count(*) FROM res_company")  # snapshot B diambil lebih dulu
        touch_row(cr_a, table, row_id)
        cr_a.commit()
        with mute_logger("odoo.sql_db"), self.assertRaises(psycopg2.errors.SerializationFailure):
            touch_row(cr_b, table, row_id)
        cr_b.rollback()
        # Retry (snapshot baru) berhasil.
        touch_row(cr_b, table, row_id)
        cr_b.commit()

    def test_touch_row_waiting_transaction_fails_serialization(self):
        """B menunggu kunci A; setelah A commit, B gagal serialisasi (tidak lanjut dengan data lama)."""
        table, row_id = self._row()
        cr_a, cr_b = self._two_cursors()
        cr_b.execute("SELECT count(*) FROM res_company")
        touch_row(cr_a, table, row_id)
        result = {}

        def run_b():
            try:
                touch_row(cr_b, table, row_id)
                result["outcome"] = "ok"
            except psycopg2.errors.SerializationFailure:
                result["outcome"] = "serialization_failure"
            except Exception as exc:  # dilaporkan ke assert
                result["outcome"] = repr(exc)

        with mute_logger("odoo.sql_db"):
            worker = threading.Thread(target=run_b, daemon=True)
            worker.start()
            time.sleep(0.5)
            self.assertTrue(worker.is_alive(), "B harus menunggu kunci baris A")
            cr_a.commit()
            worker.join(20)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result.get("outcome"), "serialization_failure")
        cr_b.rollback()

    def test_select_for_update_alone_is_not_enough(self):
        """Kontrol (alasan SEC-127): SELECT ... FOR UPDATE + commit tanpa UPDATE -> B lanjut tanpa error."""
        table, row_id = self._row()
        cr_a, cr_b = self._two_cursors()
        cr_b.execute("SELECT count(*) FROM res_company")
        lock = SQL("SELECT id FROM %s WHERE id = %s FOR UPDATE", SQL.identifier(table), row_id)
        cr_a.execute(lock)
        cr_a.commit()
        cr_b.execute(lock)
        self.assertEqual(cr_b.fetchone()[0], row_id)
        cr_b.rollback()
