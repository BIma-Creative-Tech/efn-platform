"""efn_setup_online (docs/08 §8.4 Test)."""

from datetime import datetime, timedelta

from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "efn")
class TestEfnSetupOnline(TransactionCase):
    def test_api_key_duration_set(self):
        """W-11: key kanal 30 hari + rotasi otomatis -> batas grup 35 hari (30 + margin 5), bukan default 1 hari."""
        group = self.env.ref("efn_online_order.group_online_channel")
        self.assertEqual(group.api_key_duration, 35.0)

    def test_generic_partners_without_pii(self):
        # G20 W-32 (docs/04 §12.1): satu partner generik + tim per PT berkanal online (GL, PB, BCR, BCF).
        for suffix, company_xmlid in (
            ("rtl", "company_rtl"),
            ("fnb", "company_fnb"),
            ("bcr", "company_bcr"),
            ("bcf", "company_bcf"),
        ):
            with self.subTest(pt=suffix):
                partner = self.env.ref(f"efn_setup_online.partner_superapp_{suffix}")
                company = self.env.ref(f"efn_setup_company.{company_xmlid}")
                self.assertFalse(partner.phone or partner.email or partner.vat, suffix)
                self.assertFalse(partner.is_company)
                self.assertEqual(partner.company_id, company)
                self.assertEqual(self.env.ref(f"efn_setup_online.team_superapp_{suffix}").company_id, company)

    def test_loyalty_programs_not_on_sale_orders(self):
        programs = self.env["loyalty.program"].with_context(active_test=False).search([])
        self.assertFalse(programs.filtered("sale_ok"))
        new = self.env["loyalty.program"].create({"name": "Promo POS baru", "program_type": "promotion"})
        self.assertFalse(new.sale_ok)

    def test_lock_confirmed_sales_not_enabled(self):
        self.assertFalse(self.env["res.groups"]._is_feature_enabled("sale.group_auto_done_setting"))

    def test_demo_channels(self):
        gl = self.env.ref("efn_setup_online.channel_gl", raise_if_not_found=False)
        if not gl:
            self.skipTest("demo tidak dimuat")
        self.assertTrue(gl.active)
        # G19 W-02 (#18): BFF menunggu bayar 1 jam -> Odoo 70 menit (BFF + 10), semula 25.
        self.assertEqual(gl.payment_timeout_minutes, 70)
        self.assertEqual(gl.company_id, self.env.ref("efn_setup_company.company_rtl"))
        user = self.env.ref("efn_setup_online.user_online_gl")
        self.assertFalse(self.env["res.users.apikeys"].sudo().search_count([("user_id", "=", user.id)]))
        pb = self.env.ref("efn_setup_online.channel_pb")
        self.assertFalse(pb.active)
        self.assertTrue(pb.outlet_ids.fiscal_position_id)
        # Katalog demo bisa dihitung oleh user teknis (harga/pajak dari Odoo, Rupiah bulat).
        api = self.env["efn.online.channel"].with_user(user)
        catalog = api.efn_online_catalog("GL")
        self.assertEqual(len(catalog["items"]), 3)
        # L-41: kanal mode confirm -> max_qty katalog = min(max_qty_per_line 99, max_line_qty 20).
        self.assertEqual({item["max_qty"] for item in catalog["items"]}, {20})
        # G20 §12.1: kode kanal = kode PT; W-33: weight_kg katalog (contoh demo; tas belanja tanpa berat -> None).
        self.assertEqual(catalog["channel"]["code"], "GL")
        self.assertEqual(catalog["channel"]["company_code"], "GL")
        self.assertEqual(catalog["channel"]["brand_code"], "GL")
        self.assertEqual(catalog["channel"]["currency"], "IDR")
        self.assertEqual([o["code"] for o in catalog["channel"]["outlets"]], ["TOKO-01"])
        bag = self.env.ref("efn_setup_online.product_online_bag")
        rice = self.env.ref("efn_setup_online.product_online_rice")
        weights = {item["product_id"]: item["weight_kg"] for item in catalog["items"]}
        self.assertEqual(weights[rice.id], 5.0)
        self.assertIsNone(weights[bag.id])
        quote = api.efn_online_quote("GL", "TOKO-01", "pickup", [{"product_id": bag.id, "qty": 3}])
        self.assertIsInstance(quote["amount_payable"], int)
        self.assertEqual(quote["issues"], [])
        self.assertEqual(quote["company_code"], "GL")

    def test_demo_fnb_outlet_is_non_dine_in(self):
        """G19 W-14: outlet online F&B = pickup -> fiscal position outlet tidak boleh menambah service charge."""
        pb = self.env.ref("efn_setup_online.channel_pb", raise_if_not_found=False)
        if not pb:
            self.skipTest("demo tidak dimuat")
        pb1 = self.env.ref("efn_setup_company.tax_pb1_10_fnb")
        sc = self.env.ref("efn_setup_company.tax_service_charge_5_fnb")
        for outlet in pb.with_context(active_test=False).outlet_ids:
            with self.subTest(outlet=outlet.code):
                self.assertNotIn(sc, outlet.fiscal_position_id.map_tax(pb1))


@tagged("post_install", "-at_install", "efn")
class TestEfnOnlineEntityChannels(TransactionCase):
    """G20 W-32/E-43 (docs/04 §12.1, L-48): kode kanal = kode PT (GL, PB, BCR, BCF); brand_code GL/PB/BACHA;
    satu user teknis per kanal (L-37); mode confirm + batas L-41. Menggantikan kanal per brand G19 (W-21)."""

    CHANNELS = {
        # xmlid: (kode kanal, xmlid company, brand_code, stock_hold_mode, login user teknis)
        "channel_gl": ("GL", "company_rtl", "GL", "confirm", "efn_online_gl"),
        "channel_pb": ("PB", "company_fnb", "PB", "none", "efn_online_pb"),
        "channel_bcr": ("BCR", "company_bcr", "BACHA", "confirm", "efn_online_bcr"),
        "channel_bcf": ("BCF", "company_bcf", "BACHA", "none", "efn_online_bcf"),
    }

    def setUp(self):
        super().setUp()
        if not self.env.ref("efn_setup_online.channel_gl", raise_if_not_found=False):
            self.skipTest("demo tidak dimuat")

    def _channel(self, xmlid):
        return self.env.ref(f"efn_setup_online.{xmlid}")

    def test_channel_code_equals_company_code(self):
        pricelists = self.env["product.pricelist"]
        teams = self.env["crm.team"]
        for xmlid, (code, company_xmlid, brand, hold, login) in self.CHANNELS.items():
            with self.subTest(channel=code):
                channel = self._channel(xmlid)
                company = self.env.ref(f"efn_setup_company.{company_xmlid}")
                self.assertEqual(channel.code, code)
                self.assertEqual(channel.company_id, company)
                self.assertEqual(company.efn_brand_code, code)  # L-48: kode kanal = kode PT
                self.assertEqual(channel.brand_code, brand)
                self.assertEqual(channel.stock_hold_mode, hold)
                self.assertEqual(channel.payment_timeout_minutes, 70)
                self.assertEqual(channel.pricelist_id.company_id, company)
                self.assertEqual(channel.partner_id.company_id, company)
                self.assertEqual(channel.journal_titipan_id.company_id, company)
                self.assertEqual(channel.user_ids.mapped("login"), [login])
                self.assertEqual(channel.user_ids.company_ids, company)
                if company.efn_business_type == "fnb":
                    self.assertTrue(channel.require_outlet_fiscal_position)
                pricelists |= channel.pricelist_id
                teams |= channel.team_id
        self.assertEqual(len(pricelists), 4, "pricelist per kanal")
        self.assertEqual(len(teams), 4, "tim penjualan per PT")
        self.assertTrue(self._channel("channel_gl").active)
        for xmlid in ("channel_pb", "channel_bcr", "channel_bcf"):
            with self.subTest(inactive=xmlid):
                self.assertFalse(self._channel(xmlid).active)  # outlet/SKU dari klien

    def test_old_channel_codes_removed(self):
        Channel = self.env["efn.online.channel"].with_context(active_test=False)
        old = ("RTL", "FNB", "RTL-GL", "RTL-BACHA", "FNB-PB", "FNB-BACHA")
        self.assertFalse(Channel.search([("code", "in", old)]))
        for xmlid in ("channel_rtl", "channel_fnb", "channel_rtl_gl", "channel_rtl_bacha", "channel_fnb_pb"):
            with self.subTest(xmlid=xmlid):
                self.assertFalse(self.env.ref(f"efn_setup_online.{xmlid}", raise_if_not_found=False))

    def test_confirm_limits_and_one_user_per_channel(self):
        """L-41 (SEC-131): setiap kanal mode confirm punya batas nominal (> 0) + qty per baris; L-37 user unik."""
        Channel = self.env["efn.online.channel"].with_context(active_test=False)
        confirm = Channel.search([("stock_hold_mode", "=", "confirm")])
        self.assertIn(self._channel("channel_gl"), confirm)
        self.assertIn(self._channel("channel_bcr"), confirm)
        for channel in confirm:
            with self.subTest(confirm=channel.code):
                self.assertGreater(channel.max_order_amount, 0)
                self.assertEqual(channel.max_line_qty, 20)
        channels = Channel.search([])
        users = channels.mapped("user_ids")
        self.assertEqual(len(users), sum(len(c.user_ids) for c in channels), "user teknis dipakai > 1 kanal")

    def test_bacha_two_channels_two_companies_separate_catalogs(self):
        """Bacha = dua kanal di dua PT (BCR ritel PPN, BCF kafe PB1) berbagi brand_code BACHA."""
        bcr = self._channel("channel_bcr")
        bcf = self._channel("channel_bcf")
        (bcr | bcf).write({"active": True})
        company_bcr = self.env.ref("efn_setup_company.company_bcr")
        company_bcf = self.env.ref("efn_setup_company.company_bcf")
        Product = self.env["product.product"]
        p_bcr = Product.create(
            {
                "name": "Teh Bacha kemasan uji",
                "type": "consu",
                "list_price": 150000,
                "company_id": company_bcr.id,
                "taxes_id": [Command.set(self.env.ref(f"account.{company_bcr.id}_tax_ST4").ids)],
                "efn_online_channel_ids": [Command.set(bcr.ids)],
            }
        )
        p_bcf = Product.create(
            {
                "name": "Kopi Bacha kafe uji",
                "type": "consu",
                "list_price": 65000,
                "company_id": company_bcf.id,
                "taxes_id": [Command.set(self.env.ref("efn_setup_company.tax_pb1_10_bcf").ids)],
                "efn_online_channel_ids": [Command.set(bcf.ids)],
            }
        )
        Channel = self.env["efn.online.channel"]
        bcr_cat = Channel.with_user(self.env.ref("efn_setup_online.user_online_bcr")).efn_online_catalog("BCR")
        bcf_cat = Channel.with_user(self.env.ref("efn_setup_online.user_online_bcf")).efn_online_catalog("BCF")
        bcr_ids = {item["product_id"] for item in bcr_cat["items"]}
        bcf_ids = {item["product_id"] for item in bcf_cat["items"]}
        self.assertIn(p_bcr.id, bcr_ids)
        self.assertNotIn(p_bcf.id, bcr_ids)
        self.assertIn(p_bcf.id, bcf_ids)
        self.assertNotIn(p_bcr.id, bcf_ids)
        self.assertEqual((bcr_cat["channel"]["company_code"], bcr_cat["channel"]["brand_code"]), ("BCR", "BACHA"))
        self.assertEqual((bcf_cat["channel"]["company_code"], bcf_cat["channel"]["brand_code"]), ("BCF", "BACHA"))
        # User teknis satu kanal tidak bisa memakai kanal lain (company/kanal berbeda).
        with self.assertRaises(AccessError):
            Channel.with_user(self.env.ref("efn_setup_online.user_online_bcr")).efn_online_catalog("BCF")


@tagged("post_install", "-at_install", "efn")
class TestEfnOnlineApiKeyDuration(TransactionCase):
    """W-11 (temuan devsecops G18): durasi EFEKTIF = maks api_key_duration semua grup user (bukan grup kanal saja)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.env.ref("efn_setup_company.company_rtl")  # PT Grand Lucky (GL)
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
