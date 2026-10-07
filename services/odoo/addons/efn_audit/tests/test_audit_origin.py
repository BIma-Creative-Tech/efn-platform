"""SEC-46 sisa (Gelombang 12): uid pemicu saat `with_user(SUPERUSER_ID)` + index purge erasure."""

from types import SimpleNamespace
from unittest.mock import patch

from odoo import SUPERUSER_ID, Command
from odoo.tests import tagged
from odoo.tools import SQL

from odoo.addons.efn_audit.models import base as audit_base
from odoo.addons.efn_audit.models.base import ORIGIN_UID_KEY, elevate_as_superuser, sink_event

from .common import EfnAuditCase


@tagged("post_install", "-at_install", "efn")
class TestEfnAuditOrigin(EfnAuditCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Category = cls.env["res.partner.category"]
        cls.user = cls.env["res.users"].create(
            {
                "name": "User Pemicu",
                "login": "user.pemicu.efn",
                "group_ids": [Command.set([cls.env.ref("base.group_user").id])],
            }
        )

    def setUp(self):
        super().setUp()
        self._set_rule("res.partner.category", tracked=("name",))

    def test_with_user_superuser_without_trusted_source(self):
        """Tanpa sesi HTTP/helper: tercatat OdooBot, uid pemicu tidak diketahui (batasan README)."""
        cat = self.Category.create({"name": "Origin 0"})
        cat.with_user(self.user).with_user(SUPERUSER_ID).write({"name": "Origin 0b"})
        log = self._logs(cat, "write")
        self.assertEqual(log.user_id.id, SUPERUSER_ID)
        self.assertFalse(log.origin_user_id)

    def test_elevate_helper_records_origin(self):
        cat = self.Category.create({"name": "Origin 1"})
        with elevate_as_superuser(self.env(user=self.user)) as su_env:
            self.assertEqual(su_env.uid, SUPERUSER_ID)
            su_env["res.partner.category"].browse(cat.id).write({"name": "Origin 1b"})
            # Bersarang: penanda luar dipertahankan.
            with elevate_as_superuser(su_env) as inner:
                inner["res.partner.category"].browse(cat.id).write({"name": "Origin 1c"})
        self.assertNotIn(ORIGIN_UID_KEY, self.env.cr.cache, "penanda dibersihkan setelah blok")
        logs = self._logs(cat, "write").sorted("id")
        self.assertEqual(len(logs), 2)
        for log in logs:
            self.assertEqual(log.user_id.id, SUPERUSER_ID)
            self.assertEqual(log.origin_user_id, self.user)
        # Setelah blok: tidak ada lagi origin.
        cat.with_user(SUPERUSER_ID).write({"name": "Origin 1d"})
        self.assertFalse(self._logs(cat, "write").sorted("id")[-1].origin_user_id)

    def test_context_cannot_spoof_origin(self):
        """Context bisa diisi klien RPC -> tidak pernah dipakai sebagai sumber uid pemicu."""
        cat = self.Category.create({"name": "Origin 2"})
        cat.with_context(**{ORIGIN_UID_KEY: self.user.id, "efn_audit_origin_uid": self.user.id}).with_user(
            SUPERUSER_ID
        ).write({"name": "Origin 2b"})
        self.assertFalse(self._logs(cat, "write").origin_user_id)

    def test_http_session_uid_used_as_origin(self):
        """Sesi HTTP terautentikasi (server-side) = sumber tepercaya uid pemicu."""
        fake_request = SimpleNamespace(session=SimpleNamespace(uid=self.user.id))
        cat = self.Category.create({"name": "Origin 3"})
        with patch.object(audit_base, "request", fake_request):
            cat.with_user(SUPERUSER_ID).write({"name": "Origin 3b"})
            # uid sama dengan sesi -> tidak diisi (tidak redundan).
            cat.with_user(self.user).sudo().write({"name": "Origin 3c"})
        logs = self._logs(cat, "write").sorted("id")
        self.assertEqual(logs[0].origin_user_id, self.user)
        self.assertEqual(logs[0].user_id.id, SUPERUSER_ID)
        self.assertEqual(logs[1].user_id, self.user)
        self.assertFalse(logs[1].origin_user_id)
        event = sink_event({"user_id": SUPERUSER_ID, "origin_user_id": self.user.id, "line_ids": []}, 1)
        self.assertEqual(event["origin_uid"], self.user.id)

    def test_purge_log_records_origin(self):
        Log = self.env["efn.audit.log"]
        with elevate_as_superuser(self.env(user=self.user)) as su_env:
            purge = su_env["efn.audit.log"]._efn_audit_write_purge_log("res.partner", "uji origin")
        self.assertEqual(Log.sudo().browse(purge.id).origin_user_id, self.user)


@tagged("post_install", "-at_install", "efn")
class TestEfnAuditPurgeIndex(EfnAuditCase):
    def test_ref_prefix_indexes_exist_and_usable(self):
        """Index parsial text_pattern_ops untuk `=like 'model,%'` pada purge erasure (SEC-46)."""
        cr = self.env.cr
        cr.execute(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'efn_audit_log_line' AND indexdef LIKE %s",
            ["%text_pattern_ops%"],
        )
        found = {name: definition for name, definition in cr.fetchall()}
        self.assertEqual(len(found), 2, found)
        self.assertTrue(any("old_ref" in d for d in found.values()))
        self.assertTrue(any("new_ref" in d for d in found.values()))

        # Query yang dibangkitkan ORM untuk domain purge memakai index (seqscan dimatikan agar
        # hasil EXPLAIN tidak bergantung pada ukuran tabel uji).
        Line = self.env["efn.audit.log.line"].sudo()
        prefix = "res.partner,%"
        query = Line._search(["|", ("old_ref", "=like", prefix), ("new_ref", "=like", prefix)])
        sql = query.select()
        cr.execute("SET LOCAL enable_seqscan = off")
        try:
            cr.execute(SQL("EXPLAIN %s", sql))
            plan = "\n".join(row[0] for row in cr.fetchall())
        finally:
            cr.execute("SET LOCAL enable_seqscan = on")
        self.assertTrue(any(name in plan for name in found), plan)
