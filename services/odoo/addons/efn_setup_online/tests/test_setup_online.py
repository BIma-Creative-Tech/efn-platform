"""efn_setup_online (docs/08 §8.4 Test)."""

from datetime import datetime, timedelta

from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "efn")
class TestEfnSetupOnline(TransactionCase):
    def test_api_key_duration_set(self):
        """W-11: key kanal 30 hari + rotasi otomatis -> batas grup 35 hari (30 + margin 5), bukan default 1 hari."""
        group = self.env.ref("efn_online_order.group_online_channel")
        self.assertEqual(group.api_key_duration, 35.0)

    def test_generic_partners_without_pii(self):
        for xmlid in ("efn_setup_online.partner_superapp_rtl", "efn_setup_online.partner_superapp_fnb"):
            partner = self.env.ref(xmlid)
            self.assertFalse(partner.phone or partner.email or partner.vat, xmlid)
            self.assertTrue(partner.company_id)

    def test_loyalty_programs_not_on_sale_orders(self):
        programs = self.env["loyalty.program"].with_context(active_test=False).search([])
        self.assertFalse(programs.filtered("sale_ok"))
        new = self.env["loyalty.program"].create({"name": "Promo POS baru", "program_type": "promotion"})
        self.assertFalse(new.sale_ok)

    def test_lock_confirmed_sales_not_enabled(self):
        self.assertFalse(self.env["res.groups"]._is_feature_enabled("sale.group_auto_done_setting"))

    def test_demo_channels(self):
        rtl = self.env.ref("efn_setup_online.channel_rtl", raise_if_not_found=False)
        if not rtl:
            self.skipTest("demo tidak dimuat")
        self.assertTrue(rtl.active)
        self.assertEqual(rtl.payment_timeout_minutes, 25)
        self.assertEqual(rtl.company_id, self.env.ref("efn_setup_company.company_rtl"))
        user = self.env.ref("efn_setup_online.user_online_rtl")
        self.assertFalse(self.env["res.users.apikeys"].sudo().search_count([("user_id", "=", user.id)]))
        fnb = self.env.ref("efn_setup_online.channel_fnb")
        self.assertFalse(fnb.active)
        self.assertTrue(fnb.outlet_ids.fiscal_position_id)
        # Katalog demo bisa dihitung oleh user teknis (harga/pajak dari Odoo, Rupiah bulat).
        api = self.env["efn.online.channel"].with_user(user)
        catalog = api.efn_online_catalog("RTL")
        self.assertEqual(len(catalog["items"]), 3)
        self.assertEqual(catalog["channel"]["company_code"], "RTL")
        self.assertEqual(catalog["channel"]["currency"], "IDR")
        self.assertEqual([o["code"] for o in catalog["channel"]["outlets"]], ["TOKO-01"])
        bag = self.env.ref("efn_setup_online.product_online_bag")
        quote = api.efn_online_quote("RTL", "TOKO-01", "pickup", [{"product_id": bag.id, "qty": 3}])
        self.assertIsInstance(quote["amount_payable"], int)
        self.assertEqual(quote["issues"], [])


@tagged("post_install", "-at_install", "efn")
class TestEfnOnlineApiKeyDuration(TransactionCase):
    """W-11 (temuan devsecops G18): durasi EFEKTIF = maks api_key_duration semua grup user (bukan grup kanal saja)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.env.ref("efn_setup_company.company_rtl")
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        base_vals = {"company_id": company.id, "company_ids": [Command.set(company.ids)]}
        cls.tech_user = Users.create(
            {
                **base_vals,
                "name": "Teknis Kanal Uji W-11",
                "login": "efn_online_uji_w11",
                "group_ids": [
                    Command.set(
                        [cls.env.ref("base.group_user").id, cls.env.ref("efn_online_order.group_online_channel").id]
                    )
                ],
            }
        )
        cls.staff_user = Users.create(
            {
                **base_vals,
                "name": "Staf Uji W-11",
                "login": "staf_uji_w11",
                "group_ids": [Command.set([cls.env.ref("base.group_user").id])],
            }
        )

    @staticmethod
    def _effective_days(user):
        return max(user.all_group_ids.mapped("api_key_duration")) or 1.0

    def test_general_group_has_no_longer_duration(self):
        self.assertFalse(self.env.ref("base.group_user").api_key_duration)

    def test_technical_user_effective_duration_is_35_days(self):
        self.assertEqual(self._effective_days(self.tech_user), 35.0)
        longer = self.tech_user.all_group_ids.filtered(lambda g: g.api_key_duration > 35)
        self.assertFalse(longer, longer.mapped("name"))

    def test_odoo_rejects_key_longer_than_35_days_for_technical_user(self):
        keys = self.env["res.users.apikeys"].with_user(self.tech_user)
        now = datetime.now()
        keys._check_expiration_date(now + timedelta(days=30))
        with self.assertRaises(ValidationError):
            keys._check_expiration_date(now + timedelta(days=36))

    def test_staff_user_without_duration_group_gets_odoo_fallback(self):
        """Dampak ke staf: tanpa grup berdurasi, API key staf maksimal 1 hari (fallback CE)."""
        self.assertEqual(self._effective_days(self.staff_user), 1.0)
        with self.assertRaises(ValidationError):
            self.env["res.users.apikeys"].with_user(self.staff_user)._check_expiration_date(
                datetime.now() + timedelta(days=2)
            )
