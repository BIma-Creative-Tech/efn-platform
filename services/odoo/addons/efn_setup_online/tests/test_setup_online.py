"""efn_setup_online (docs/08 §8.4 Test)."""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "efn")
class TestEfnSetupOnline(TransactionCase):
    def test_api_key_duration_set(self):
        group = self.env.ref("efn_online_order.group_online_channel")
        self.assertGreater(group.api_key_duration, 1.0)

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
