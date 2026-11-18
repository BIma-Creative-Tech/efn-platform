"""HttpCase `GET /efn/insight/open` (docs/04 §26.2; fit-gap efn-odoo-functional G42 §C.8).

Server HttpCase berjalan di proses yang sama, jadi `patch.dict(os.environ)` berlaku untuk request. Kunci uji sintetis
(bukan secret) ditulis ke file sementara.
"""

import os
import re
import tempfile
import time
from unittest.mock import patch

from odoo import Command
from odoo.tests import HttpCase, new_test_user, tagged

from odoo.addons.efn_insight.lib import insight_token as it

CTRL = "odoo.addons.efn_insight.controllers.main"
TEST_KEY = "0123456789abcdef" * 4  # sintetis, 64 hex
URL = "https://insight.test"
TOKEN_RE = re.compile(r'value="(eit1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43})"')


@tagged("efn", "post_install", "-at_install")
class TestInsightSso(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.key_file = os.path.join(tmp.name, "insight_sso_key")
        with open(cls.key_file, "w", encoding="ascii") as handle:
            handle.write(TEST_KEY + "\n")
        cls.short_key_file = os.path.join(tmp.name, "short_key")
        with open(cls.short_key_file, "w", encoding="ascii") as handle:
            handle.write("ab" * 20)

        Company = cls.env["res.company"]
        # Kode company unik per DB (efn_setup_company bisa sudah memakainya): kosongkan dulu (rollback akhir test).
        # active_test=False: company yang diarsipkan juga memegang kode unik.
        Company.with_context(active_test=False).search([("efn_brand_code", "!=", False)]).write(
            {"efn_brand_code": False}
        )
        Company.flush_model(["efn_brand_code"])
        cls.pb = Company.create({"name": "PT Uji Insight PB", "efn_brand_code": "PB"})
        cls.bcf = Company.create({"name": "PT Uji Insight BCF", "efn_brand_code": "BCF"})
        cls.wtz = Company.create({"name": "PT Uji Insight WTZ", "efn_brand_code": "WTZ"})
        cls.gl = Company.create({"name": "PT Uji Insight GL", "efn_brand_code": "GL"})
        cls.efn = Company.create({"name": "PT Uji Insight EFN", "efn_brand_code": "EFN"})

        user_groups = "base.group_user,efn_insight.group_insight_user"
        cls.user = new_test_user(
            cls.env,
            login="efn_ins_user",
            name="Staf Uji Insight",
            groups=user_groups,
            company_id=cls.pb.id,
            company_ids=[Command.set([cls.pb.id, cls.bcf.id, cls.gl.id])],
        )
        cls.user_arch = new_test_user(
            cls.env,
            login="efn_ins_arch",
            groups=user_groups,
            company_id=cls.pb.id,
            company_ids=[Command.set([cls.pb.id, cls.wtz.id])],
        )
        cls.user_gl = new_test_user(
            cls.env,
            login="efn_ins_gl",
            groups=user_groups,
            company_id=cls.gl.id,
            company_ids=[Command.set([cls.gl.id, cls.efn.id])],
        )
        cls.user_all = new_test_user(
            cls.env,
            login="efn_ins_all",
            groups="base.group_user,efn_insight.group_insight_all_brands",
            company_id=cls.efn.id,
            company_ids=[Command.set([cls.efn.id])],
        )
        cls.plain = new_test_user(cls.env, login="efn_ins_plain", groups="base.group_user", company_id=cls.pb.id)

    # ------------------------------------------------------------------ helper
    def _env(self, **overrides):
        values = {
            "INSIGHT_ENABLED": "true",
            "EFN_INSIGHT_URL": URL,
            "EFN_INSIGHT_SSO_KEY_FILE": self.key_file,
            "APP_ENV": "production",
            "EFN_ALLOW_PLAINTEXT": "false",
        }
        values.update(overrides)
        return patch.dict(os.environ, values)

    def _open(self, login, **env):
        self.authenticate(login, login)
        with self._env(**env):
            return self.url_open("/efn/insight/open", allow_redirects=False)

    def _token(self, response):
        self.assertEqual(response.status_code, 200)
        match = TOKEN_RE.search(response.text)
        self.assertTrue(match, "halaman form harus berisi token eit1")
        return match.group(1)

    def _verify(self, token):
        return it.verify_token(token, TEST_KEY, now=int(time.time()))

    def _log_count(self):
        return self.env["ir.logging"].sudo().search_count([("name", "=", "efn_insight")])

    # ------------------------------------------------------------------ akses
    def test_anonymous_redirected_to_login(self):
        with self._env():
            response = self.url_open("/efn/insight/open", allow_redirects=False)
        self.assertIn(response.status_code, (302, 303))
        self.assertIn("/web/login", response.headers.get("Location", ""))
        self.assertNotIn("eit1.", response.text)

    def test_user_without_group_forbidden(self):
        response = self._open("efn_ins_plain")
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("eit1.", response.text)

    def test_kill_switch(self):
        # Hanya "true" persis yang menyalakan (sama dengan insight-web, docs/04 §26.2): "1"/"yes"/"on"/"True" = mati.
        for value in (None, "false", "0", "1", "yes", "on", "True", "TRUE", ""):
            with self.subTest(value=value):
                env = {"INSIGHT_ENABLED": value} if value is not None else {}
                self.authenticate("efn_ins_user", "efn_ins_user")
                with self._env(**env):
                    if value is None:
                        os.environ.pop("INSIGHT_ENABLED", None)
                    response = self.url_open("/efn/insight/open", allow_redirects=False)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn('name="t"', response.text)
                self.assertIn('data-efn-insight-state="disabled"', response.text)
                self.assertIn("frame-ancestors 'none'", response.headers.get("Content-Security-Policy", ""))
                # Halaman info Odoo tetap no-referrer; hanya halaman form SSO yang strict-origin (docs/04 §26.2).
                self.assertEqual(response.headers.get("Referrer-Policy"), "no-referrer")

    def test_kill_switch_off_creates_no_token_and_no_audit(self):
        before = self._log_count()
        self.authenticate("efn_ins_user", "efn_ins_user")
        with self._env(INSIGHT_ENABLED="false"):
            response = self.url_open("/efn/insight/open", allow_redirects=False)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("eit1.", response.text)
        self.assertEqual(self._log_count(), before)

    # ------------------------------------------------------------------ token & cakupan
    def test_user_scope_token(self):
        before = self._log_count()
        with self.assertLogs(CTRL, "INFO") as logs:
            response = self._open("efn_ins_user")
        token = self._token(response)
        payload = self._verify(token)
        self.assertEqual(payload["codes"], ["BCF", "PB"])  # GL tidak ikut
        self.assertEqual(payload["cids"], sorted([self.pb.id, self.bcf.id]))
        self.assertEqual(payload["roles"], ["user"])
        self.assertEqual(payload["sub"], self.user.id)
        self.assertEqual(payload["name"], "Staf Uji Insight")
        self.assertEqual(payload["exp"] - payload["iat"], 60)
        self.assertEqual((payload["v"], payload["aud"], payload["iss"]), (1, "insight-web", "odoo"))
        self.assertRegex(payload["jti"], r"^[0-9a-f]{32}$")
        self.assertEqual(set(payload), it.PAYLOAD_KEYS)
        for forbidden in ("login", "email", "phone"):
            self.assertNotIn(forbidden, payload)
        # form POST ke /sso, token tidak di URL
        self.assertIn('action="https://insight.test/sso"', response.text)
        self.assertIn('method="post"', response.text)
        self.assertIn("/efn_insight/static/src/js/insight_autosubmit.js", response.text)
        self.assertNotIn("?t=", response.text)
        # header
        csp = response.headers.get("Content-Security-Policy", "")
        self.assertEqual(
            csp,
            "default-src 'none'; script-src 'self'; form-action https://insight.test; base-uri 'none'; "
            "frame-ancestors 'none'",
        )
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        # strict-origin (bukan no-referrer): Origin harus terkirim ke insight-web /sso (C1, docs/04 §26.2).
        self.assertEqual(response.headers.get("Referrer-Policy"), "strict-origin")
        self.assertIn('<meta name="referrer" content="strict-origin"/>', response.text)
        # audit: tepat satu baris, tanpa token/sig/nama
        self.assertEqual(self._log_count(), before + 1)
        entry = self.env["ir.logging"].sudo().search([("name", "=", "efn_insight")], order="id desc", limit=1)
        self.assertIn(f"uid={self.user.id}", entry.message)
        self.assertIn("codes=BCF,PB", entry.message)
        self.assertIn(f"jti={payload['jti']}", entry.message)
        output = "\n".join(logs.output) + entry.message
        for secret in (token, token.split(".")[2], token.split(".")[1], "Staf Uji Insight", "efn_ins_user", TEST_KEY):
            self.assertNotIn(secret, output)

    def test_jti_unique_per_request(self):
        first = self._verify(self._token(self._open("efn_ins_user")))
        second = self._verify(self._token(self._open("efn_ins_user")))
        self.assertNotEqual(first["jti"], second["jti"])

    def test_archived_company_excluded(self):
        self.wtz.active = False
        payload = self._verify(self._token(self._open("efn_ins_arch")))
        self.assertEqual(payload["codes"], ["PB"])
        self.assertEqual(payload["cids"], [self.pb.id])

    def test_no_esb_company_shows_info_without_token(self):
        before = self._log_count()
        response = self._open("efn_ins_gl")
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-efn-insight-state="no_company"', response.text)
        self.assertNotIn('name="t"', response.text)
        self.assertEqual(self._log_count(), before)

    def test_all_brands(self):
        self.assertTrue(self.user_all.has_group("efn_insight.group_insight_user"))  # implied
        payload = self._verify(self._token(self._open("efn_ins_all")))
        self.assertEqual(payload["codes"], ["BCF", "BCR", "CUP", "IMD", "PB", "WTZ"])
        self.assertEqual(payload["roles"], ["all_brands"])
        self.assertEqual(payload["cids"], sorted([self.pb.id, self.bcf.id, self.wtz.id]))

    def test_all_brands_archived_company_not_in_cids_but_codes_fixed(self):
        # docs/04 §26.2: `codes` grup semua brand tetap enam (tidak diturunkan dari cids); cids hanya company aktif.
        self.wtz.active = False
        payload = self._verify(self._token(self._open("efn_ins_all")))
        self.assertEqual(payload["codes"], ["BCF", "BCR", "CUP", "IMD", "PB", "WTZ"])
        self.assertEqual(payload["cids"], sorted([self.pb.id, self.bcf.id]))

    def test_user_scope_includes_inactive_switcher_company_but_not_foreign(self):
        # Q-INS2 / L-120: semua user.company_ids (bukan hanya company aktif); company di luar company_ids tidak ikut,
        # walau berkode ESB (WTZ ada di DB tetapi bukan milik efn_ins_user).
        payload = self._verify(self._token(self._open("efn_ins_user")))
        self.assertIn(self.bcf.id, payload["cids"])  # company_id aktif = PB, BCF hanya di company_ids
        self.assertNotIn(self.wtz.id, payload["cids"])
        self.assertNotIn("WTZ", payload["codes"])

    def test_form_page_is_get_only(self):
        # Rute hanya GET: POST (mis. dari halaman pihak ketiga) tidak boleh menerbitkan token.
        self.authenticate("efn_ins_user", "efn_ins_user")
        with self._env():
            response = self.url_open("/efn/insight/open", data={"x": "1"}, allow_redirects=False)
        self.assertNotIn("eit1.", response.text)

    # ------------------------------------------------------------------ konfigurasi salah
    def _assert_config_page(self, response):
        self.assertEqual(response.status_code, 503)
        self.assertIn('data-efn-insight-state="config"', response.text)
        self.assertNotIn('name="t"', response.text)
        self.assertNotIn("<script", response.text)

    def test_misconfiguration_fails_closed(self):
        cases = (
            {"EFN_INSIGHT_SSO_KEY_FILE": "/nonexistent/insight_sso_key"},
            {"EFN_INSIGHT_SSO_KEY_FILE": self.short_key_file},
            {"EFN_INSIGHT_URL": "http://insight.test"},
            {"EFN_INSIGHT_URL": "http://insight.test", "EFN_ALLOW_PLAINTEXT": "true"},  # APP_ENV=production
            {"EFN_INSIGHT_URL": 'https://insight.test/"><script>alert(1)</script>'},
            {"EFN_INSIGHT_URL": "https://insight.test/app"},
            {"EFN_INSIGHT_URL": ""},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                before = self._log_count()
                with self.assertLogs(CTRL, "ERROR") as logs:
                    response = self._open("efn_ins_user", **overrides)
                self._assert_config_page(response)
                self.assertIn("insight_misconfigured", "\n".join(logs.output))
                self.assertNotIn(TEST_KEY, "\n".join(logs.output))
                self.assertEqual(self._log_count(), before)

    def test_plaintext_allowed_in_dev(self):
        response = self._open(
            "efn_ins_user", EFN_INSIGHT_URL="http://insight:3002", APP_ENV="development", EFN_ALLOW_PLAINTEXT="true"
        )
        self._token(response)
        self.assertIn('action="http://insight:3002/sso"', response.text)

    # ------------------------------------------------------------------ data & aset
    def test_menu_and_action_data(self):
        group = self.env.ref("efn_insight.group_insight_user")
        menu = self.env.ref("efn_insight.menu_insight")
        action = self.env.ref("efn_insight.action_open")
        self.assertEqual(menu.group_ids, group)
        self.assertEqual(menu.action, action)
        self.assertEqual((action.url, action.target), ("/efn/insight/open", "self"))
        all_brands = self.env.ref("efn_insight.group_insight_all_brands")
        self.assertIn(group, all_brands.implied_ids)

    def test_autosubmit_script_served(self):
        response = self.url_open("/efn_insight/static/src/js/insight_autosubmit.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn("efn-insight-sso", response.text)
