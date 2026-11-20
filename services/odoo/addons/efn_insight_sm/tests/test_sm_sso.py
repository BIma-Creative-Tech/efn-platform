"""HttpCase `GET /efn/insight/open` dengan persona supermarket (docs/04 §28.13.3): token eit2, kill switch
INSIGHT_SM_ENABLED, halaman no_outlet / scope_too_large, audit tanpa daftar wids/cats."""

import os
import re
import tempfile
import time
from unittest.mock import patch

from odoo import Command
from odoo.tests import HttpCase, tagged

from odoo.addons.efn_insight.lib import insight_token as it

from .common import setup_sm_fixture

TEST_KEY = "fedcba9876543210" * 4  # sintetis, 64 hex  # gitleaks:allow (kunci uji palsu)
TOKEN_RE = re.compile(r'value="(eit2\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43})"')
CTRL = "odoo.addons.efn_insight.controllers.main"


@tagged("efn", "post_install", "-at_install")
class TestInsightSmSso(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.key_file = os.path.join(tmp.name, "insight_sso_key")
        with open(cls.key_file, "w", encoding="ascii") as handle:
            handle.write(TEST_KEY)
        setup_sm_fixture(cls)
        cls.exec_user = cls.make_user("efn_sm_sso_exec", ["exec"])
        cls.store_user = cls.make_user("efn_sm_sso_store", ["store"])
        cls.store_user.sudo().efn_outlet_warehouse_ids = [Command.set([cls.outlet1.id])]
        cls.store_none = cls.make_user("efn_sm_sso_store_none", ["store"])
        cls.cat_user = cls.make_user("efn_sm_sso_cat", ["category"])

    def _open(self, login, **overrides):
        values = {
            "INSIGHT_ENABLED": "true",
            "INSIGHT_SM_ENABLED": "true",
            "EFN_INSIGHT_TOKEN_VERSION": "2",
            "EFN_INSIGHT_URL": "https://insight.test",
            "EFN_INSIGHT_SSO_KEY_FILE": self.key_file,
            "APP_ENV": "production",
            "EFN_ALLOW_PLAINTEXT": "false",
        }
        values.update(overrides)
        self.authenticate(login, login)
        with patch.dict(os.environ, values):
            return self.url_open("/efn/insight/open", allow_redirects=False)

    def _payload(self, response):
        self.assertEqual(response.status_code, 200)
        match = TOKEN_RE.search(response.text)
        self.assertTrue(match, "halaman form harus berisi token eit2")
        return it.verify_token_v2(match.group(1), TEST_KEY, now=int(time.time()))

    def _last_audit(self):
        return self.env["ir.logging"].sudo().search([("name", "=", "efn_insight")], order="id desc", limit=1)

    def test_exec_token(self):
        payload = self._payload(self._open("efn_sm_sso_exec"))
        self.assertEqual(payload["roles"], ["exec", "user"])
        self.assertEqual(payload["codes"], ["SMD"])
        self.assertEqual(payload["cids"], [self.smd.id])
        self.assertEqual(payload["wids"], [0, *self.smd_wh_ids])
        message = self._last_audit().message
        self.assertIn(f"wids_n={len(payload['wids'])} cats_n=0", message)
        self.assertIn("roles=exec,user", message)
        self.assertNotIn("wids=", message, "daftar wids tidak dicatat (hanya jumlah)")
        self.assertNotIn("cats=", message)

    def test_store_token_only_own_outlet(self):
        payload = self._payload(self._open("efn_sm_sso_store"))
        self.assertEqual(payload["roles"], ["store", "user"])
        self.assertEqual(payload["wids"], [self.outlet1.id])

    def test_store_without_outlet_info_page(self):
        before = self.env["ir.logging"].sudo().search_count([("name", "=", "efn_insight")])
        response = self._open("efn_sm_sso_store_none")
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-efn-insight-state="no_outlet"', response.text)
        self.assertNotIn('name="t"', response.text)
        self.assertEqual(self.env["ir.logging"].sudo().search_count([("name", "=", "efn_insight")]), before)

    def test_sm_kill_switch(self):
        # Hanya "true" persis; selain itu bagian SM diabaikan -> user SM-saja = no_company.
        for value in ("false", "1", "True", ""):
            with self.subTest(value=value):
                response = self._open("efn_sm_sso_exec", INSIGHT_SM_ENABLED=value)
                self.assertIn('data-efn-insight-state="no_company"', response.text)
                self.assertNotIn('name="t"', response.text)

    def test_rollback_eit1_ignores_sm(self):
        response = self._open("efn_sm_sso_exec", EFN_INSIGHT_TOKEN_VERSION="1")
        self.assertIn('data-efn-insight-state="no_company"', response.text)

    def test_category_cats_and_scope_too_large(self):
        categs = self.env["product.category"].create([{"name": f"Uji SM SSO {i}"} for i in range(51)])
        self.cat_user.sudo().efn_insight_categ_ids = [Command.set(categs[:2].ids)]
        payload = self._payload(self._open("efn_sm_sso_cat"))
        self.assertEqual(payload["cats"], sorted(categs[:2].ids))
        self.cat_user.sudo().efn_insight_categ_ids = [Command.set(categs.ids)]
        with self.assertLogs(CTRL, "INFO") as logs:
            response = self._open("efn_sm_sso_cat")
        self.assertIn('data-efn-insight-state="scope_too_large"', response.text)
        self.assertNotIn('name="t"', response.text)
        self.assertIn(f"insight_scope_too_large uid={self.cat_user.id}", "\n".join(logs.output))
        self.assertIn("cats_n=51", "\n".join(logs.output))
