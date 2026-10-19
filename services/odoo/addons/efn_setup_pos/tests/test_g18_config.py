"""Gelombang 18 (docs/08 §9): W-22 default stasiun KDS, W-25 grup kasir pembuat pelanggan (sempit, SEC-121).

Nomor HP di bawah fiktif khusus test (bukan data pribadi nyata).
"""

from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "efn")
class TestEfnG18Config(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref("efn_setup_company.company_fnb")
        cls.group = cls.env.ref("efn_setup_pos.group_efn_cashier_contact_creation")
        cls.cashier = (
            cls.env["res.users"]
            .with_context(no_reset_password=True)
            .create(
                {
                    "name": "Kasir Uji W-25",
                    "login": "kasir.uji.w25",
                    "company_id": cls.company.id,
                    "company_ids": [Command.set(cls.company.ids)],
                    "group_ids": [Command.set([cls.env.ref("base.group_user").id, cls.group.id])],
                }
            )
        )

    # ------------------------------------------------------------------ W-22
    def test_kds_station_defaults_warn_5_late_10(self):
        station = (
            self.env["efn.kds.station"]
            .with_company(self.company)
            .create({"name": "Stasiun Uji W-22", "company_id": self.company.id})
        )
        self.assertEqual(station.warn_minutes, 5)
        self.assertEqual(station.late_minutes, 10)

    def test_kds_retention_90_days(self):
        value = self.env["ir.config_parameter"].sudo().get_param("efn.kds.retention_days")
        self.assertEqual(value, "90")

    # ------------------------------------------------------------------ W-25 / SEC-121 (L-25i)
    def _as_cashier(self, model):
        return self.env[model].with_user(self.cashier).with_company(self.company)

    def test_cashier_group_is_narrow(self):
        """SEC-121: grup kasir = POS User + buat pelanggan saja, TIDAK mewarisi Contact Creation standar."""
        self.assertTrue(self.cashier.has_group("point_of_sale.group_pos_user"))
        self.assertFalse(self.cashier.has_group("base.group_partner_manager"))
        self.assertFalse(self.cashier.has_group("point_of_sale.group_pos_manager"))
        self.assertNotIn(self.env.ref("base.group_partner_manager"), self.group.all_implied_ids)

    def test_cashier_creates_individual_customer_in_own_company(self):
        partner = self._as_cashier("res.partner").create({"name": "Pelanggan Baru W-25", "phone": "0812-0000-2525"})
        self.assertEqual(partner.company_id, self.company)
        self.assertFalse(partner.is_company)
        defaults = self._as_cashier("res.partner").default_get(["company_id"])  # form POS memakai default ini
        self.assertEqual(defaults.get("company_id"), self.company.id)

    def test_cashier_cannot_create_company_shared_or_other_pt_partner(self):
        Partner = self._as_cashier("res.partner")
        other_pt = self.env.ref("efn_setup_company.company_rtl")
        parent = self.env["res.partner"].create({"name": "Induk Uji SEC-121", "company_id": self.company.id})
        for vals in (
            {"name": "PT Pemasok Uji", "is_company": True},
            {"name": "Partner Bersama Uji", "company_id": False},
            {"name": "Partner PT Lain Uji", "company_id": other_pt.id},
            {"name": "Kontak Anak Uji", "parent_id": parent.id},
        ):
            with self.subTest(vals=vals), self.assertRaises(AccessError):
                Partner.create(vals)

    def test_cashier_cannot_write_or_unlink_existing_partners(self):
        own = self.env["res.partner"].create({"name": "Pelanggan Lama W-25", "company_id": self.company.id})
        shared = self.env["res.partner"].create({"name": "Partner Bersama Lama", "company_id": False})
        for partner in (own, shared):
            with self.subTest(partner=partner.name):
                with self.assertRaises(AccessError):
                    partner.with_user(self.cashier).write({"name": "Diubah kasir"})
                with self.assertRaises(AccessError):
                    partner.with_user(self.cashier).write({"phone": "0813-9999-2525"})
                with self.assertRaises(AccessError):
                    partner.with_user(self.cashier).unlink()

    def test_cashier_has_no_bank_account_write(self):
        partner = self.env["res.partner"].create({"name": "Pemasok Rekening Uji", "company_id": self.company.id})
        bank = self.env["res.partner.bank"].create({"partner_id": partner.id, "acc_number": "SEC121-0001"})
        with self.assertRaises(AccessError):
            self._as_cashier("res.partner.bank").create({"partner_id": partner.id, "acc_number": "SEC121-0002"})
        with self.assertRaises(AccessError):
            bank.with_user(self.cashier).write({"acc_number": "SEC121-0003"})
        with self.assertRaises(AccessError):
            bank.with_user(self.cashier).unlink()

    def test_partner_manager_in_cashier_group_not_restricted(self):
        """Rule grup di-OR: Contact Creation standar + grup kasir tetap boleh partner bersama & company."""
        self.cashier.group_ids = [Command.link(self.env.ref("base.group_partner_manager").id)]
        shared = self._as_cashier("res.partner").create(
            {"name": "Pemasok Bersama", "is_company": True, "company_id": False}
        )
        shared.with_user(self.cashier).write({"name": "Pemasok Bersama (ubah)"})
        self.assertFalse(shared.company_id)
        # Default CE (partner bersama) tetap untuk user Contact Creation standar.
        self.assertFalse(self._as_cashier("res.partner").default_get(["company_id"]).get("company_id"))


@tagged("post_install", "-at_install", "efn")
class TestEfnMemberPriceOtp(TransactionCase):
    """G18 L-23 (Q-F18-7): harga member = promo ber-OTP (template nonaktif), bukan pricelist pilihan kasir."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref("efn_setup_company.company_rtl")
        cls.program = cls.env.ref("efn_setup_pos.loyalty_program_member_price_rtl")

    def _cashier_config(self):
        return self.env["pos.config"].search([("company_id", "=", self.company.id)], limit=1) or self.env[
            "pos.config"
        ].with_company(self.company).create({"name": "POS Uji L-23", "company_id": self.company.id})

    def test_template_is_inactive_otp_promotion(self):
        program = self.program
        self.assertFalse(program.active)
        self.assertFalse(program.pos_ok)
        self.assertTrue(program.efn_requires_otp)
        self.assertEqual(program.program_type, "promotion")
        self.assertEqual(program.company_id, self.company)
        reward = program.with_context(active_test=False).reward_ids
        self.assertEqual(len(reward), 1)
        self.assertEqual((reward.reward_type, reward.discount, reward.discount_mode), ("discount", 5, "percent"))

    def test_activation_requires_cashier_pos(self):
        with self.assertRaises(ValidationError):
            self.program.write({"active": True, "pos_ok": True})
        config = self._cashier_config()
        self.program.write({"active": True, "pos_ok": True, "pos_config_ids": [Command.set(config.ids)]})
        self.assertTrue(self.program.active)
        self.assertEqual(self.program.pos_config_ids, config)

    def test_bcr_template_is_inactive_otp_promotion(self):
        # G20 W-32: template harga member BCR sama dengan GL (nonaktif, wajib OTP, company BCR).
        program = self.env.ref("efn_setup_pos.loyalty_program_member_price_bcr")
        self.assertFalse(program.active)
        self.assertFalse(program.pos_ok)
        self.assertTrue(program.efn_requires_otp)
        self.assertEqual(program.company_id, self.env.ref("efn_setup_company.company_bcr"))

    def test_no_member_pricelist_offered_to_cashiers(self):
        """Tidak ada pricelist 'Member' aktif di daftar pilihan kasir POS mana pun (R-B06)."""
        configs = self.env["pos.config"].search([])
        offered = configs.available_pricelist_ids.filtered(lambda p: "member" in (p.name or "").lower())
        self.assertFalse(offered, offered.mapped("name"))


@tagged("post_install", "-at_install", "efn")
class TestEfnPointsPaymentMethodData(TransactionCase):
    """G18 W-01/E-30: metode bayar "Poin EFN" per PT + penautan ke POS redeem-aktif (data efn_setup_pos)."""

    def _method(self, suffix):
        return self.env.ref(f"efn_setup_pos.pos_payment_method_points_{suffix}")

    def _new_config(self, company, **vals):
        return self.env["pos.config"].with_company(company).create({"name": "POS Uji Poin", **vals})

    def test_points_method_per_pt(self):
        # G20 W-32 (docs/04 §12.1): 4 PT ber-POS = GL (rtl), PB (fnb), BCR, BCF.
        for suffix, company_xmlid in (
            ("rtl", "company_rtl"),
            ("fnb", "company_fnb"),
            ("bcr", "company_bcr"),
            ("bcf", "company_bcf"),
        ):
            with self.subTest(pt=suffix):
                method = self._method(suffix)
                self.assertEqual(method.company_id, self.env.ref(f"efn_setup_company.{company_xmlid}"))
                self.assertTrue(method.efn_is_loyalty_points)
                self.assertTrue(method.active)
                self.assertEqual(method.payment_method_type, "none")
                self.assertFalse(method.split_transactions)
                self.assertFalse(method.is_cash_count)
                self.assertFalse(method.use_payment_terminal)
                self.assertEqual(method.journal_id.type, "bank")
                self.assertEqual(method.journal_id.company_id, method.company_id)
                self.assertFalse(method.journal_id.bank_account_id)
                self.assertEqual(method.outstanding_account_id.account_type, "liability_current")

    def test_points_journal_payment_lines_use_pt_liability_account(self):
        """G22 L-79: baris metode masuk/keluar jurnal Poin EFN tiap PT = akun liabilitas/kliring Poin EFN PT itu."""
        for suffix in ("rtl", "fnb", "bcr", "bcf"):
            with self.subTest(pt=suffix):
                journal = self.env.ref(f"efn_setup_pos.journal_points_{suffix}")
                account = self.env.ref(f"efn_setup_pos.account_points_liability_{suffix}")
                inbound = journal.inbound_payment_method_line_ids
                outbound = journal.outbound_payment_method_line_ids
                self.assertTrue(inbound and outbound)
                self.assertEqual((inbound | outbound).payment_account_id, account)

    def test_points_journal_accounts_function_fills_only_empty_lines(self):
        Method = self.env["pos.payment.method"]
        journal = self.env.ref("efn_setup_pos.journal_points_fnb")
        account = self.env.ref("efn_setup_pos.account_points_liability_fnb")
        inbound = journal.inbound_payment_method_line_ids[:1]
        outbound = journal.outbound_payment_method_line_ids[:1]
        other = account.copy({"code": "21990092", "name": "Akun pilihan Finance uji"})
        inbound.payment_account_id = False
        outbound.payment_account_id = other
        filled = Method._efn_setup_points_journal_accounts()
        self.assertIn(inbound, filled)
        self.assertEqual(inbound.payment_account_id, account)
        self.assertEqual(outbound.payment_account_id, other, "pilihan Finance tidak ditimpa")

    def test_redeem_enabled_config_gets_points_method(self):
        company = self.env.ref("efn_setup_company.company_rtl")
        config = self._new_config(company, efn_loyalty_redeem_enabled=True)
        self.assertIn(self._method("rtl"), config.payment_method_ids)
        self.assertEqual(config._efn_loyalty_points_method_record(), self._method("rtl"))

    def test_points_method_linked_when_redeem_enabled_later(self):
        company = self.env.ref("efn_setup_company.company_fnb")
        config = self._new_config(company)
        self.assertNotIn(self._method("fnb"), config.payment_method_ids)
        config.efn_loyalty_redeem_enabled = True
        self.assertIn(self._method("fnb"), config.payment_method_ids)
        self.assertNotIn(self._method("rtl"), config.payment_method_ids)

    def test_bacha_config_gets_own_company_points_method(self):
        company = self.env.ref("efn_setup_company.company_bcf")
        config = self._new_config(company, efn_loyalty_redeem_enabled=True)
        self.assertIn(self._method("bcf"), config.payment_method_ids)
        for other in ("rtl", "fnb", "bcr"):
            with self.subTest(other=other):
                self.assertNotIn(self._method(other), config.payment_method_ids)

    def test_upgrade_function_relinks_missing_method(self):
        company = self.env.ref("efn_setup_company.company_rtl")
        config = self._new_config(company, efn_loyalty_redeem_enabled=True)
        config.payment_method_ids = [Command.unlink(self._method("rtl").id)]
        self.assertFalse(config._efn_loyalty_points_method_record())
        self.env["pos.config"]._efn_setup_link_points_methods_all()
        self.assertIn(self._method("rtl"), config.payment_method_ids)
