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
TEST_KEY = "0123456789abcdef" * 4  # sintetis, 64 hex  # gitleaks:allow (kunci uji palsu)
URL = "https://insight.test"
TOKEN_RE = re.compile(r'value="(eit[123]\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43})"')


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
        # G52: kepala toko F&B PB dengan dua cabang ESB (id sintetis: "pb:91012" < "pb:9107" urutan ASCII).
        Branch = cls.env["efn.insight.esb.branch"]
        Branch.with_context(active_test=False).search([("esb_branch_id", ">=", 9000)]).unlink()
        cls.branches = Branch.create(
            [
                {"company_id": cls.pb.id, "esb_brand": "pb", "esb_branch_id": 9107, "name": "PB uji 9107"},
                {"company_id": cls.pb.id, "esb_brand": "pb", "esb_branch_id": 91012, "name": "PB uji 91012"},
            ]
        )
        store_groups = "base.group_user,efn_insight.group_insight_store"
        cls.store = new_test_user(
            cls.env,
            login="efn_ins_store",
            groups=store_groups,
            company_id=cls.pb.id,
            company_ids=[Command.set([cls.pb.id, cls.bcf.id])],
        )
        cls.store.sudo().efn_insight_esb_branch_ids = [Command.set(cls.branches.ids)]
        cls.store_none = new_test_user(
            cls.env,
            login="efn_ins_store_none",
            groups=store_groups,
            company_id=cls.pb.id,
            company_ids=[Command.set([cls.pb.id])],
        )

    # ------------------------------------------------------------------ helper
    def _env(self, **overrides):
        values = {
            "INSIGHT_ENABLED": "true",
            "EFN_INSIGHT_URL": URL,
            "EFN_INSIGHT_SSO_KEY_FILE": self.key_file,
            "APP_ENV": "production",
            "EFN_ALLOW_PLAINTEXT": "false",
            # G47: test lama memakai eit2 eksplisit, bagian SM mati (test SM ada di efn_insight_sm). G52: test eit3
            # menimpa EFN_INSIGHT_TOKEN_VERSION; persona F&B mati kecuali disebut.
            "EFN_INSIGHT_TOKEN_VERSION": "2",
            "INSIGHT_SM_ENABLED": "false",
            "INSIGHT_FNB_PERSONA_ENABLED": "false",
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
        self.assertTrue(match, "halaman form harus berisi token eit1/eit2")
        return match.group(1)

    def _verify(self, token):
        if token.startswith("eit3."):
            return it.verify_token_v3(token, TEST_KEY, now=int(time.time()))
        if token.startswith("eit2."):
            return it.verify_token_v2(token, TEST_KEY, now=int(time.time()))
        return it.verify_token(token, TEST_KEY, now=int(time.time()))

    def _assert_no_token(self, text):
        self.assertNotIn("eit1.", text)
        self.assertNotIn("eit2.", text)
        self.assertNotIn("eit3.", text)
        self.assertNotIn('name="t"', text)

    def _log_count(self):
        return self.env["ir.logging"].sudo().search_count([("name", "=", "efn_insight")])

    # ------------------------------------------------------------------ akses
    def test_anonymous_redirected_to_login(self):
        with self._env():
            response = self.url_open("/efn/insight/open", allow_redirects=False)
        self.assertIn(response.status_code, (302, 303))
        self.assertIn("/web/login", response.headers.get("Location", ""))
        self._assert_no_token(response.text)

    def test_user_without_group_forbidden(self):
        response = self._open("efn_ins_plain")
        self.assertEqual(response.status_code, 403)
        self._assert_no_token(response.text)

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
        self._assert_no_token(response.text)
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
        self.assertEqual((payload["v"], payload["aud"], payload["iss"]), (2, "insight-web", "odoo"))
        self.assertTrue(token.startswith("eit2."))
        self.assertEqual((payload["wids"], payload["cats"]), ([], []))
        self.assertRegex(payload["jti"], r"^[0-9a-f]{32}$")
        self.assertEqual(set(payload), it.PAYLOAD_KEYS_V2)
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
        self.assertIn(" v=2 ", entry.message)
        self.assertIn("wids_n=0 cats_n=0", entry.message)
        self.assertIn("codes=BCF,PB", entry.message)
        self.assertIn(f"jti={payload['jti']}", entry.message)
        output = "\n".join(logs.output) + entry.message
        for secret in (token, token.split(".")[2], token.split(".")[1], "Staf Uji Insight", "efn_ins_user", TEST_KEY):
            self.assertNotIn(secret, output)

    def test_token_version_rollback_eit1(self):
        """G47 docs/04 §28.13.3 butir 7: EFN_INSIGHT_TOKEN_VERSION=1 -> eit1 persis §26.3; nilai lain -> eit2."""
        token = self._token(self._open("efn_ins_user", EFN_INSIGHT_TOKEN_VERSION="1"))
        self.assertTrue(token.startswith("eit1."))
        payload = self._verify(token)
        self.assertEqual(set(payload), it.PAYLOAD_KEYS)
        self.assertEqual(payload["v"], 1)
        self.assertEqual(payload["codes"], ["BCF", "PB"])
        entry = self.env["ir.logging"].sudo().search([("name", "=", "efn_insight")], order="id desc", limit=1)
        self.assertIn(" v=1 ", entry.message)
        self.assertNotIn("wids_n", entry.message)
        # Bagian SM diabaikan walau INSIGHT_SM_ENABLED=true.
        token = self._token(self._open("efn_ins_user", EFN_INSIGHT_TOKEN_VERSION="1", INSIGHT_SM_ENABLED="true"))
        self.assertTrue(token.startswith("eit1."))
        # G52 (docs/04 §28.15.4 butir 8): `2` = eit2, nilai lain -> eit3.
        for value, prefix in (("2", "eit2."), (" 2 ", "eit2."), ("3", "eit3."), ("", "eit3."), ("v1", "eit3.")):
            with self.subTest(value=value):
                token = self._token(self._open("efn_ins_user", EFN_INSIGHT_TOKEN_VERSION=value))
                self.assertTrue(token.startswith(prefix))

    def test_token_version_unset_defaults_to_eit3(self):
        self.authenticate("efn_ins_user", "efn_ins_user")
        with self._env():
            os.environ.pop("EFN_INSIGHT_TOKEN_VERSION", None)
            response = self.url_open("/efn/insight/open", allow_redirects=False)
        token = self._token(response)
        self.assertTrue(token.startswith("eit3."))
        payload = self._verify(token)
        self.assertEqual(set(payload), it.PAYLOAD_KEYS_V3)
        self.assertEqual((payload["v"], payload["roles"], payload["brs"]), (3, ["user"], []))
        entry = self.env["ir.logging"].sudo().search([("name", "=", "efn_insight")], order="id desc", limit=1)
        self.assertIn(" v=3 ", entry.message)
        self.assertIn("wids_n=0 cats_n=0 brs_n=0", entry.message)

    # ------------------------------------------------------------------ G52: kepala toko F&B (docs/04 §28.15.4)
    def test_store_fnb_token_has_branches(self):
        response = self._open("efn_ins_store", EFN_INSIGHT_TOKEN_VERSION="3", INSIGHT_FNB_PERSONA_ENABLED="true")
        token = self._token(response)
        self.assertTrue(token.startswith("eit3."))
        payload = self._verify(token)
        self.assertEqual(payload["codes"], ["PB"], "BCF di company_ids tidak ikut: hanya PT dari cabang")
        self.assertEqual(payload["cids"], [self.pb.id])
        self.assertEqual(payload["roles"], ["store", "user"])
        self.assertEqual(payload["brs"], ["pb:91012", "pb:9107"])
        entry = self.env["ir.logging"].sudo().search([("name", "=", "efn_insight")], order="id desc", limit=1)
        self.assertIn("brs_n=2", entry.message)
        self.assertNotIn("pb:", entry.message, "daftar cabang tidak dicatat (L-249)")

    def test_store_fnb_revoked_without_flag_or_on_rollback(self):
        # L-241: kepala toko store-only tidak pernah lagi mendapat PT penuh (flag mati, eit2, maupun eit1).
        for env in (
            {"EFN_INSIGHT_TOKEN_VERSION": "3", "INSIGHT_FNB_PERSONA_ENABLED": "false"},
            {"EFN_INSIGHT_TOKEN_VERSION": "2", "INSIGHT_FNB_PERSONA_ENABLED": "true"},
            {"EFN_INSIGHT_TOKEN_VERSION": "1", "INSIGHT_FNB_PERSONA_ENABLED": "true"},
        ):
            with self.subTest(env=env):
                response = self._open("efn_ins_store", **env)
                self.assertEqual(response.status_code, 200)
                self.assertIn('data-efn-insight-state="no_company"', response.text)
                self._assert_no_token(response.text)

    def test_store_fnb_without_branch_info_page(self):
        before = self._log_count()
        response = self._open("efn_ins_store_none", EFN_INSIGHT_TOKEN_VERSION="3", INSIGHT_FNB_PERSONA_ENABLED="true")
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-efn-insight-state="no_branch"', response.text)
        self.assertIn("belum ditautkan ke cabang ESB", response.text)
        self._assert_no_token(response.text)
        self.assertEqual(self._log_count(), before)

    def test_persona_conflict_info_page_not_500(self):
        # SEC-G52-02: exec + store + marketing melanggar T5 -> halaman info (fail closed), bukan HTTP 500.
        new_test_user(
            self.env,
            login="efn_ins_combo",
            groups="base.group_user,efn_insight.group_insight_exec,efn_insight.group_insight_store,"
            "efn_insight.group_insight_marketing",
            company_id=self.pb.id,
            company_ids=[Command.set([self.pb.id])],
        )
        response = self._open("efn_ins_combo", EFN_INSIGHT_TOKEN_VERSION="3", INSIGHT_FNB_PERSONA_ENABLED="true")
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-efn-insight-state="persona_conflict"', response.text)
        self._assert_no_token(response.text)

    def test_sm_enabled_without_sm_module_scope_is_esb_only(self):
        # Hook dasar efn_insight = cakupan SM kosong; bila efn_insight_sm terpasang, user ini tetap tanpa persona.
        payload = self._verify(self._token(self._open("efn_ins_user", INSIGHT_SM_ENABLED="true")))
        self.assertEqual(payload["roles"], ["user"])
        self.assertEqual((payload["wids"], payload["cats"]), ([], []))

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
        self.assertNotIn("eit2.", response.text)

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
