"""G54 (docs/fit-gap/G54_G55_G61_ODOO_SETUP.md §7): wizard "Lengkapi konfigurasi POS".

POS uji dibuat seperti importer ESB (``efn_legacy_history`` ``pos_config_plan``: company, operation type gudang,
``payment_method_ids`` kosong). Company uji = IMD (CoA ``id`` dari efn_setup_company, kode brand 3 huruf) dan CUP
(company pembanding untuk isolasi). Wizard selalu dibatasi ke POS buatan test, jadi POS lain di DB tidak memengaruhi
hasil; langkah tingkat company (akun POS company, pajak menu) hanya menyentuh IMD.
"""

import re

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.efn_setup_pos.wizard.efn_pos_setup_wizard import cash_journal_code

ACCOUNT_FIELDS = (
    "account_default_pos_receivable_account_id",
    "default_cash_difference_income_account_id",
    "default_cash_difference_expense_account_id",
)


@tagged("post_install", "-at_install", "efn")
class TestG54PosSetupWizard(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ref = cls.env.ref
        cls.imd = ref("efn_setup_company.company_imd")
        cls.cup = ref("efn_setup_company.company_cup")
        cls.edc = ref("efn_setup_pos.pos_payment_method_edc_imd")
        cls.qris = ref("efn_setup_pos.pos_payment_method_qris_manual_imd")
        cls.points = ref("efn_setup_pos.pos_payment_method_points_imd")
        both = [Command.set((cls.imd | cls.cup).ids)]
        cls.manager = new_test_user(
            cls.env,
            login="g54_pos_account_manager",
            groups="base.group_user,point_of_sale.group_pos_manager,account.group_account_manager",
            company_id=cls.imd.id,
            company_ids=both,
        )
        cls.pos_manager_only = new_test_user(
            cls.env,
            login="g54_pos_manager_only",
            groups="base.group_user,point_of_sale.group_pos_manager",
            company_id=cls.imd.id,
            company_ids=both,
        )
        cls.cashier = new_test_user(
            cls.env,
            login="g54_cashier_imd",
            groups="base.group_user,point_of_sale.group_pos_user",
            company_id=cls.imd.id,
            company_ids=[Command.set(cls.imd.ids)],
        )
        cls.warehouses = {}
        for company, code in ((cls.imd, "G54I"), (cls.cup, "G54C")):
            cls.warehouses[company.id] = (
                cls.env["stock.warehouse"]
                .with_company(company)
                .create({"name": f"Gudang uji G54 {code}", "code": code, "company_id": company.id})
            )

    # ------------------------------------------------------------------------------------------------ helper
    def _config(self, company, name, **vals):
        """Seperti efn_legacy_history pos_config_plan: tanpa metode bayar."""
        warehouse = self.warehouses[company.id]
        self.assertTrue(warehouse.pos_type_id, "point_of_sale membuat operation type POS per gudang")
        return (
            self.env["pos.config"]
            .with_company(company)
            .create(
                {
                    "name": name,
                    "company_id": company.id,
                    "picking_type_id": warehouse.pos_type_id.id,
                    "payment_method_ids": [Command.set([])],
                    **vals,
                }
            )
        )

    def _wizard(self, company, configs, mode="check", user=None, **vals):
        Wizard = self.env["efn.pos.setup.wizard"].with_user(user or self.manager).with_company(company)
        wizard = Wizard.create(
            {"company_id": company.id, "config_ids": [Command.set(configs.ids)], "mode": mode, **vals}
        )
        wizard.action_run()
        return wizard

    def _snapshot(self, company, configs):
        Journal = self.env["account.journal"].with_context(active_test=False)
        Method = self.env["pos.payment.method"].with_context(active_test=False)
        methods = Method.search([("company_id", "=", company.id)], order="id")
        return (
            Journal.search_count([("company_id", "=", company.id)]),
            len(methods),
            tuple(m.outstanding_account_id.id for m in methods),
            tuple((c.id, tuple(sorted(c.payment_method_ids.ids)), c.journal_id.id) for c in configs),
            tuple(company[fname].id for fname in ACCOUNT_FIELDS),
        )

    def _lines(self, wizard, config, code=None, status=None):
        return wizard.line_ids.filtered(
            lambda line: line.config_id == config
            and (code is None or line.code == code)
            and (status is None or line.status == status)
        )

    # ------------------------------------------------------------------------------------------------ test
    def test_check_lists_blockers_and_changes_nothing(self):
        configs = self._config(self.imd, "G54 Outlet A") | self._config(self.imd, "G54 Outlet B")
        before = self._snapshot(self.imd, configs)
        wizard = self._wizard(self.imd, configs, "check")
        self.assertEqual(self._snapshot(self.imd, configs), before, "mode check tidak menulis apa pun")
        for config in configs:
            with self.subTest(config=config.name):
                self.assertTrue(self._lines(wizard, config, "P07", "blocked"), "tanpa metode bayar = blocked P07")
                self.assertFalse(config.payment_method_ids)
                codes = set(self._lines(wizard, config).mapped("code"))
                self.assertTrue({f"P{n:02d}" for n in range(1, 20)} <= codes, "laporan P01..P19 lengkap")
        self.assertIn("0 dari 2", wizard.summary)

    def test_apply_then_session_opens_and_closes(self):
        configs = self._config(self.imd, "G54 Outlet A") | self._config(self.imd, "G54 Outlet B")
        wizard = self._wizard(self.imd, configs, "apply")
        for config in configs:
            with self.subTest(config=config.name):
                cash = config.payment_method_ids.filtered("is_cash_count")
                self.assertEqual(len(cash), 1, "tepat satu metode tunai")
                self.assertEqual(cash.config_ids, config, "metode tunai eksklusif")
                self.assertEqual(cash.journal_id.type, "cash")
                self.assertTrue(cash.journal_id.loss_account_id and cash.journal_id.profit_account_id)
                self.assertIn(self.edc, config.payment_method_ids)
                self.assertIn(self.qris, config.payment_method_ids)
                self.assertNotIn(self.points, config.payment_method_ids, "Poin EFN hanya untuk POS redeem-aktif")
                blocked = self._lines(wizard, config, status="blocked")
                self.assertFalse(blocked, blocked.mapped(lambda line: f"{line.code}: {line.message}"))
        self.assertTrue(self.edc.outstanding_account_id and self.qris.outstanding_account_id)
        self.assertTrue(self.imd.account_default_pos_receivable_account_id)
        self.assertEqual(self.qris.payment_method_type, "none")

        config = configs[0]
        config.with_user(self.cashier).with_company(self.imd).open_ui()
        session = config.current_session_id
        self.assertTrue(session, "kasir non-superuser membuka sesi")
        self.assertEqual(session.user_id, self.cashier)
        session.set_opening_control(0, "")
        self.assertEqual(session.state, "opened")
        session.action_pos_session_closing_control()
        self.assertEqual(session.state, "closed")

    def test_rerun_apply_creates_nothing(self):
        configs = self._config(self.imd, "G54 Outlet A") | self._config(self.imd, "G54 Outlet B")
        self._wizard(self.imd, configs, "apply")
        after_first = self._snapshot(self.imd, configs)
        wizard = self._wizard(self.imd, configs, "apply")
        self.assertEqual(self._snapshot(self.imd, configs), after_first, "apply kedua tidak membuat/mengubah apa pun")
        self.assertFalse(wizard.line_ids.filtered(lambda line: line.status == "fixed"))

    def test_cash_journal_code_scheme(self):
        self.assertEqual(cash_journal_code("PB", set()), "PBC01")
        self.assertEqual(cash_journal_code("PB", {"PBC01", "PBC02"}), "PBC03")
        self.assertEqual(cash_journal_code("IMD", {"IMDC1"}), "IMDC2")
        self.assertEqual(cash_journal_code("IMD", {f"IMDC{c}" for c in "123456789"}), "IMDCA")
        self.assertFalse(cash_journal_code("FARM", set()), ">= 4 huruf = isi manual")
        self.assertFalse(cash_journal_code(False, set()))

        configs = self._config(self.imd, "G54 Outlet A") | self._config(self.imd, "G54 Outlet B")
        self._wizard(self.imd, configs, "apply")
        journals = configs.payment_method_ids.filtered("is_cash_count").journal_id
        self.assertEqual(len(journals), 2)
        for config in configs:
            cash = config.payment_method_ids.filtered("is_cash_count")
            self.assertRegex(cash.journal_id.code, re.compile(r"^IMDC[1-9A-Z]$"))
            self.assertEqual(cash.journal_id.name, f"Kas {config.name}")
            self.assertEqual(cash.name, f"Tunai {config.name}")

    def test_company_isolation(self):
        config_imd = self._config(self.imd, "G54 Outlet IMD")
        config_cup = self._config(self.cup, "G54 Outlet CUP")
        before_cup = self._snapshot(self.cup, config_cup)
        Wizard = self.env["efn.pos.setup.wizard"].with_user(self.manager).with_company(self.imd)
        wizard = Wizard.create({"company_id": self.imd.id, "mode": "apply"})  # semua POS IMD
        wizard.action_run()
        self.assertTrue(config_imd.payment_method_ids)
        self.assertEqual(self._snapshot(self.cup, config_cup), before_cup, "wizard IMD tidak menyentuh CUP")
        self.assertFalse(wizard.line_ids.filtered(lambda line: line.config_id == config_cup))
        with self.assertRaises(UserError):
            Wizard.create({"company_id": self.imd.id, "config_ids": [Command.set(config_cup.ids)], "mode": "apply"})

    def test_redeem_flag_attaches_points_method(self):
        redeem = self._config(self.imd, "G54 Outlet Redeem", efn_loyalty_redeem_enabled=True)
        redeem.payment_method_ids = [Command.unlink(self.points.id)]
        plain = self._config(self.imd, "G54 Outlet Tanpa Redeem")
        self.assertNotIn(self.points, redeem.payment_method_ids)
        self._wizard(self.imd, redeem | plain, "apply")
        self.assertIn(self.points, redeem.payment_method_ids)
        self.assertNotIn(self.points, plain.payment_method_ids)

    def test_apply_requires_account_manager(self):
        config = self._config(self.imd, "G54 Outlet Hak")
        wizard = self._wizard(self.imd, config, "check", user=self.pos_manager_only)
        self.assertTrue(self._lines(wizard, config, "P07", "blocked"))
        with self.assertRaises(AccessError):
            self._wizard(self.imd, config, "apply", user=self.pos_manager_only)
        self.assertFalse(config.payment_method_ids)

    def test_config_with_open_session_is_skipped(self):
        config = self._config(self.imd, "G54 Outlet Sesi")
        self.env["pos.session"].create({"config_id": config.id, "user_id": self.manager.id})
        wizard = self._wizard(self.imd, config, "apply")
        self.assertFalse(config.payment_method_ids, "metode bayar tidak boleh diubah selama sesi terbuka")
        self.assertTrue(self._lines(wizard, config, "P15", "warning"))

    def test_restaurant_option_uses_standard_floor(self):
        config = self._config(self.imd, "G54 Outlet Meja")
        untouched = self._config(self.imd, "G54 Outlet Takeaway")
        Floor = self.env["restaurant.floor"]
        floors_before = Floor.search_count([])
        wizard = self._wizard(self.imd, config | untouched, "check")
        self.assertTrue(self._lines(wizard, config, "W-05", "warning"), "POS F&B tanpa mode restoran = W-05")
        self.assertEqual(Floor.search_count([]), floors_before, "mode check tidak membuat lantai")

        self._wizard(self.imd, untouched, "apply")
        self.assertFalse(untouched.module_pos_restaurant, "opsi mode restoran default mati")
        self.assertFalse(untouched.floor_ids)

        wizard = self._wizard(self.imd, config, "apply", opt_tables=True)
        self.assertTrue(self._lines(wizard, config, "C-11", "fixed"))
        self.assertFalse(self._lines(wizard, config, "W-05"))
        self.assertTrue(config.module_pos_restaurant)
        self.assertTrue(config.iface_printbill)
        self.assertEqual(config.default_screen, "tables")
        self.assertEqual(len(config.floor_ids), 1, "lantai default CE (_setup_default_floor)")
        self.assertEqual(config.floor_ids.name, self.imd.name)
        self.assertEqual(len(config.floor_ids.table_ids), 1)

        wizard = self._wizard(self.imd, config, "apply", opt_tables=True)
        self.assertTrue(self._lines(wizard, config, "C-11", "ok"))
        self.assertEqual(len(config.floor_ids), 1, "apply ulang tidak menambah lantai")
