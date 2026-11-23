"""G56 efn_web_home: bootstrap beranda (aplikasi terlihat, branding PT aktif) + hook landing default."""

import json

from odoo import Command
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.efn_web_home.hooks import (
    ACTION_XMLID,
    _efn_post_init_set_default_home,
    _efn_uninstall_clear_default_home,
)
from odoo.addons.efn_web_home.models.res_company import DEFAULT_ACCENT
from odoo.addons.efn_web_home.models.res_users import HOME_GROUPS, HOME_MENU_XMLID

GROUP_KEYS = {key for key, _label in HOME_GROUPS}


@tagged("post_install", "-at_install", "efn")
class TestEfnHomeBootstrap(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Company = cls.env["res.company"]
        cls.c1 = Company.create(
            {
                "name": "PT Uji Beranda Satu",
                "efn_brand_accent_color": "#123456",
                "efn_home_announcement_active": True,
                "efn_home_announcement_html": "<p>Halo PT Satu</p>",
            }
        )
        cls.c2 = Company.create(
            {
                "name": "PT Uji Beranda Dua",
                "efn_brand_accent_color": "#654321",
                "efn_home_announcement_active": True,
                "efn_home_announcement_html": "<p>Rahasia PT Dua</p>",
            }
        )
        groups = ["base.group_user"]
        if cls.env.ref("point_of_sale.group_pos_user", raise_if_not_found=False):
            groups.append("point_of_sale.group_pos_user")
        cls.pos_user = new_test_user(
            cls.env,
            login="efn_home_pos_user",
            groups=",".join(groups),
            company_id=cls.c1.id,
            company_ids=[Command.set([cls.c1.id, cls.c2.id])],
        )

    def _bootstrap(self, user, company):
        return self.env["res.users"].with_user(user).with_company(company).efn_home_bootstrap()

    def _menu_id(self, xmlid):
        rec = self.env.ref(xmlid, raise_if_not_found=False)
        return rec.id if rec else None

    def test_pos_user_sees_only_accessible_apps(self):
        data = self._bootstrap(self.pos_user, self.c1)
        app_ids = {app["id"] for app in data["apps"]}
        self.assertTrue(app_ids, "user internal minimal melihat satu aplikasi (mis. Discuss)")
        for xmlid in (
            "account.menu_finance",
            "base.menu_administration",
            # base.menu_management (Apps) sengaja tidak dicek: di CE 19 menu ini tanpa grup (terverifikasi
            # di DB efn-prod), beranda hanya mengikuti visibilitas menu bawaan.
            "efn_setup_menu.menu_accounting_root",
            HOME_MENU_XMLID,
        ):
            menu_id = self._menu_id(xmlid)
            if menu_id:
                self.assertNotIn(menu_id, app_ids, xmlid)
        pos_root = self._menu_id("point_of_sale.menu_point_root")
        if pos_root and self.pos_user.has_group("point_of_sale.group_pos_user"):
            self.assertIn(pos_root, app_ids)
            self.assertEqual(next(a["group"] for a in data["apps"] if a["id"] == pos_root), "ops")
        for app in data["apps"]:
            self.assertIn(app["group"], GROUP_KEYS)
        self.assertEqual([g["key"] for g in data["groups"]], [key for key, _label in HOME_GROUPS])

    def test_admin_sees_settings_in_admin_group(self):
        admin = self.env.ref("base.user_admin")
        data = self._bootstrap(admin, admin.company_id)
        settings = self._menu_id("base.menu_administration")
        groups = {app["id"]: app["group"] for app in data["apps"]}
        self.assertEqual(groups.get(settings), "admin")

    def test_branding_belongs_to_active_company_only(self):
        data = self._bootstrap(self.pos_user, self.c1)
        company = data["company"]
        self.assertEqual(company["id"], self.c1.id)
        self.assertEqual(company["accent"], "#123456")
        self.assertIn("Halo PT Satu", company["announcement_html"])
        dumped = json.dumps(data, default=str)
        self.assertNotIn("Rahasia PT Dua", dumped)
        self.assertNotIn("#654321", dumped)
        self.assertNotIn(self.c2.name, dumped)

        # Ganti PT aktif -> branding ikut PT aktif itu saja.
        data2 = self._bootstrap(self.pos_user, self.c2)
        self.assertEqual(data2["company"]["id"], self.c2.id)
        self.assertNotIn("Halo PT Satu", json.dumps(data2, default=str))

    def test_announcement_hidden_when_inactive_and_accent_validated(self):
        self.c1.write({"efn_home_announcement_active": False, "efn_brand_accent_color": "red;background:url(x)"})
        company = self._bootstrap(self.pos_user, self.c1)["company"]
        self.assertEqual(company["announcement_html"], "")
        self.assertEqual(company["accent"], DEFAULT_ACCENT)


@tagged("post_install", "-at_install", "efn")
class TestEfnHomeHooks(TransactionCase):
    def test_post_init_sets_action_only_when_empty_and_uninstall_clears_ours(self):
        action = self.env.ref(ACTION_XMLID)
        other = self.env["ir.actions.client"].create({"name": "Landing lain", "tag": "efn_web_home_test_other"})
        empty = new_test_user(self.env, login="efn_home_hook_empty", groups="base.group_user")
        custom = new_test_user(self.env, login="efn_home_hook_custom", groups="base.group_user")
        portal = new_test_user(self.env, login="efn_home_hook_portal", groups="base.group_portal")
        (empty | portal).write({"action_id": False})
        custom.write({"action_id": other.id})

        _efn_post_init_set_default_home(self.env)
        self.assertEqual(empty.action_id.id, action.id)
        self.assertEqual(custom.action_id.id, other.id)
        self.assertFalse(portal.action_id, "user portal tidak diberi landing backend")

        _efn_uninstall_clear_default_home(self.env)
        self.assertFalse(empty.action_id)
        self.assertEqual(custom.action_id.id, other.id)
