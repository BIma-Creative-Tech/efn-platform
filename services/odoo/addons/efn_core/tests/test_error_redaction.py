"""SEC-22 (Gelombang 12b): traceback tidak keluar di respons error JSON-RPC / ``/json/2``
untuk user non-``base.group_system``; admin sistem tetap mendapat ``debug``.

Method pemicu error ditambahkan sementara ke kelas model registry (``patch.object(...,
create=True)``) — server HttpCase berjalan di proses yang sama, jadi method ikut terlihat
oleh ``/web/dataset/call_kw`` tanpa menambah route produksi.
"""

import json
from datetime import timedelta
from unittest.mock import patch

from odoo import api, fields
from odoo.exceptions import UserError
from odoo.tests import HttpCase, new_test_user, tagged
from odoo.tools import mute_logger

BUSINESS_MSG = "Stok EFN tidak cukup untuk order ini"
INTERNAL_DETAIL = "/opt/efn/internal_secret.py password_hash=abc123"


@api.model
def _efn_raise_user_error(self):
    raise UserError(BUSINESS_MSG)


@api.model
def _efn_raise_internal(self):
    raise KeyError(INTERNAL_DETAIL)


@tagged("post_install", "-at_install", "efn")
class TestEfnErrorRedaction(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cashier = new_test_user(
            cls.env, login="efn_redact_cashier", password="efn_redact_cashier_pw", groups="base.group_user"
        )
        cls.sysadmin = new_test_user(
            cls.env,
            login="efn_redact_admin",
            password="efn_redact_admin_pw",
            groups="base.group_user,base.group_system",
        )

    def setUp(self):
        super().setUp()
        partner_cls = type(self.env["res.partner"])
        for name, func in (("efn_test_user_error", _efn_raise_user_error), ("efn_test_internal", _efn_raise_internal)):
            patcher = patch.object(partner_cls, name, func, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _call_kw(self, method):
        response = self.url_open(
            "/web/dataset/call_kw",
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "call",
                    "id": 1,
                    "params": {"model": "res.partner", "method": method, "args": [], "kwargs": {}},
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("error", payload)
        return payload["error"]

    def assertNoInternals(self, text):
        for needle in ("Traceback", ".py", "internal_secret", "password_hash", "efn_test_"):
            self.assertNotIn(needle, text)

    # --- JSON-RPC ---------------------------------------------------------------------------

    def test_cashier_user_error_keeps_message_without_traceback(self):
        self.authenticate("efn_redact_cashier", "efn_redact_cashier_pw")
        with mute_logger("odoo.http"):
            error = self._call_kw("efn_test_user_error")
        data = error["data"]
        self.assertEqual(data["debug"], "")
        self.assertEqual(data["name"], "odoo.exceptions.UserError")
        self.assertEqual(data["message"], BUSINESS_MSG)
        self.assertEqual(data["arguments"], [BUSINESS_MSG])

    def test_cashier_unexpected_error_is_generic(self):
        self.authenticate("efn_redact_cashier", "efn_redact_cashier_pw")
        with mute_logger("odoo.http"):
            error = self._call_kw("efn_test_internal")
        self.assertEqual(error["data"]["debug"], "")
        self.assertEqual(error["data"]["name"], "Exception")
        self.assertNoInternals(json.dumps(error))

    def test_no_session_has_no_traceback(self):
        # Tanpa sesi: call_kw menolak (SessionExpired) -- tetap tanpa traceback.
        with mute_logger("odoo.http"):
            error = self._call_kw("efn_test_internal")
        self.assertEqual(error["data"]["debug"], "")
        self.assertNoInternals(json.dumps(error))

    def test_admin_keeps_debug(self):
        self.authenticate("efn_redact_admin", "efn_redact_admin_pw")
        with mute_logger("odoo.http"):
            error = self._call_kw("efn_test_internal")
        self.assertIn("Traceback", error["data"]["debug"])
        self.assertIn("internal_secret", error["data"]["message"])
        with mute_logger("odoo.http"):
            error = self._call_kw("efn_test_user_error")
        self.assertIn("Traceback", error["data"]["debug"])
        self.assertEqual(error["data"]["message"], BUSINESS_MSG)

    # --- /json/2 (modul rpc, auto_install) ----------------------------------------------------

    def _json2(self, user, method):
        if self.env["ir.module.module"]._get("rpc").state != "installed":
            self.skipTest("modul rpc (/json/2) tidak terpasang")
        key = (
            self.env["res.users.apikeys"]
            .with_user(user)
            ._generate(None, "efn redaction test", fields.Datetime.now() + timedelta(days=1))
        )
        response = self.url_open(
            f"/json/2/res.partner/{method}",
            data=json.dumps({}),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        )
        self.assertGreaterEqual(response.status_code, 400)
        return response.json()

    def test_json2_cashier_redacted_admin_debug(self):
        with mute_logger("odoo.http"):
            body = self._json2(self.cashier, "efn_test_internal")
        self.assertEqual(body["debug"], "")
        self.assertNoInternals(json.dumps(body))
        with mute_logger("odoo.http"):
            body = self._json2(self.cashier, "efn_test_user_error")
        self.assertEqual(body["debug"], "")
        self.assertEqual(body["message"], BUSINESS_MSG)
        with mute_logger("odoo.http"):
            body = self._json2(self.sysadmin, "efn_test_internal")
        self.assertIn("Traceback", body["debug"])
