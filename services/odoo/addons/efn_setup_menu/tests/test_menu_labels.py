"""G55 efn_setup_menu: induk, grup, label (en_US + id_ID), visibilitas akar Accounting."""

from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.efn_setup_menu.hooks import _efn_uninstall_restore_menus
from odoo.addons.efn_setup_menu.models.ir_ui_menu import EFN_MENU_LABELS

ROOT = "efn_setup_menu.menu_accounting_root"
MOVED = (
    "account.menu_finance_entries",
    "account.account_audit_menu",
    "account.menu_finance_reports",
    "account.menu_finance_configuration",
)
EXPECTED = {
    "account.menu_finance": "Finance",
    ROOT: "Accounting",
    "mrp.menu_mrp_root": "Produksi",
    "account.menu_finance_entries": "Jurnal & Penutupan",
}


@tagged("post_install", "-at_install", "efn")
class TestEfnSetupMenu(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.root = cls.env.ref(ROOT)
        cls.Menu = cls.env["ir.ui.menu"]

    def _name(self, xmlid, lang):
        return self.env.ref(xmlid).with_context(lang=lang).name

    def test_root_structure_and_groups(self):
        self.assertFalse(self.root.parent_id)
        self.assertEqual(self.root.sequence, 56)
        self.assertEqual(self.root.web_icon, "account,static/description/icon.png")
        self.assertEqual(
            self.root.group_ids,
            self.env.ref("account.group_account_readonly") | self.env.ref("account.group_account_manager"),
        )
        for xmlid in MOVED:
            self.assertEqual(self.env.ref(xmlid).parent_id, self.root, xmlid)
        finance = self.env.ref("account.menu_finance")
        self.assertFalse(finance.parent_id)
        self.assertEqual(self.env.ref("efn_setup_menu.menu_efn_cash_bank").parent_id, finance)
        self.assertEqual(self.env.ref("account.menu_board_journal_1").parent_id, finance)

    def test_labels_en_us(self):
        self.assertEqual(EFN_MENU_LABELS, EXPECTED)
        for xmlid, label in EXPECTED.items():
            self.assertEqual(self._name(xmlid, "en_US"), label, xmlid)

    def test_labels_id_id_after_activation(self):
        self.env["res.lang"]._activate_lang("id_ID")
        self.env.registry.clear_cache()  # get_installed() di-ormcache
        # Simulasi terjemahan CE yang dimuat saat bahasa dipasang, lalu -u efn_setup_menu (README).
        self.env.ref("mrp.menu_mrp_root").update_field_translations("name", {"id_ID": "Manufaktur"})
        self.Menu._efn_apply_menu_labels()
        for xmlid, label in EXPECTED.items():
            self.assertEqual(self._name(xmlid, "id_ID"), label, xmlid)
            self.assertEqual(self._name(xmlid, "en_US"), label, xmlid)

    def test_efn_account_reports_menus_follow_parent(self):
        menus = [
            self.env.ref(x, raise_if_not_found=False)
            for x in (
                "efn_account_reports.menu_efn_financial_report",
                "efn_account_reports.menu_efn_consolidation_adjustment",
            )
        ]
        menus = [m for m in menus if m]
        if not menus:
            self.skipTest("efn_account_reports tidak terpasang")
        prefix = f"{self.root.id}/"
        for menu in menus:
            self.assertTrue(menu.parent_path.startswith(prefix), menu.complete_name)

    def test_uninstall_hook_restores_ce_parents(self):
        _efn_uninstall_restore_menus(self.env)
        finance = self.env.ref("account.menu_finance")
        for xmlid in MOVED:
            self.assertEqual(self.env.ref(xmlid).parent_id, finance, xmlid)
        self.assertFalse(self.Menu.search([("parent_id", "=", self.root.id)]))

    def test_billing_user_cannot_see_accounting_root(self):
        billing = new_test_user(
            self.env, login="efn_menu_billing", groups="base.group_user,account.group_account_invoice"
        )
        accountant = new_test_user(
            self.env, login="efn_menu_accountant", groups="base.group_user,account.group_account_user"
        )
        finance = self.env.ref("account.menu_finance")
        billing_visible = self.Menu.with_user(billing)._visible_menu_ids()
        self.assertNotIn(self.root.id, billing_visible)
        self.assertIn(finance.id, billing_visible)
        self.assertIn(self.root.id, self.Menu.with_user(accountant)._visible_menu_ids())
