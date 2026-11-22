"""efn_setup_supermarket_online: kanal online SMD (pickup + pre-order), keputusan user 23 Nov 2026.

Data (selalu): pos.category katalog SMD (nama = kontrak situs), partner generik + tim Super-App SMD.
Demo (hanya DB --with-demo): kanal SMD, user teknis efn_online_smd, 5 outlet pickup di gudang toko SMD01..SMD05
dengan slot pre-order. Test demo di-skip bila data demo tidak ada.
"""

import re

from odoo.fields import Command
from odoo.tests import TransactionCase, tagged

MODULE = "efn_setup_supermarket_online"
SM = "efn_setup_supermarket"
OUTLET_CODES = ("SMD01", "SMD02", "SMD03", "SMD04", "SMD05")
# docs/04 §28.6: outlet_code event maks 16 karakter, huruf besar (Q-SM43).
EVENT_OUTLET_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{1,15}$")
POS_CATEGORIES = {
    "pos_categ_smd_vegetable": "Sayur",
    "pos_categ_smd_fruit": "Buah",
    "pos_categ_smd_meat": "Daging",
    "pos_categ_smd_fish": "Ikan & Seafood",
    "pos_categ_smd_bakery": "Bakery",
    "pos_categ_smd_dairy_chilled": "Dairy & Chilled",
    "pos_categ_smd_frozen": "Frozen",
    "pos_categ_smd_dry_grocery": "Dry Grocery",
    "pos_categ_smd_beverage": "Minuman",
    "pos_categ_smd_snack": "Snack",
    "pos_categ_smd_personal_care": "Personal Care",
    "pos_categ_smd_household": "Household",
    "pos_categ_smd_imported_premium": "Impor Premium",
}
FORBIDDEN_MODULES = ("efn_setup_online",)


@tagged("efn", "post_install", "-at_install")
class TestSetupSupermarketOnline(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref(f"{SM}.company_smd")
        cls.channel = cls.env.ref(f"{MODULE}.channel_smd", raise_if_not_found=False)

    def _require_demo(self):
        if not self.channel:
            self.skipTest("data demo efn_setup_supermarket_online tidak dimuat (DB tanpa --with-demo)")

    # ------------------------------------------------------------------ data
    def test_pos_categories_exact_names(self):
        for xmlid, name in POS_CATEGORIES.items():
            categ = self.env.ref(f"{MODULE}.{xmlid}")
            self.assertEqual(categ.name, name, xmlid)
            self.assertFalse(categ.parent_id, f"{xmlid} harus kategori tingkat-1")
        names = self.env["ir.model.data"].search([("module", "=", MODULE), ("model", "=", "pos.category")])
        self.assertEqual(len(names), len(POS_CATEGORIES), "Alkohol tidak boleh punya kategori online")

    def test_generic_partner_and_team(self):
        partner = self.env.ref(f"{MODULE}.partner_superapp_smd")
        team = self.env.ref(f"{MODULE}.team_superapp_smd")
        self.assertEqual(partner.company_id, self.company)
        self.assertEqual(team.company_id, self.company)
        self.assertFalse(partner.phone or partner.email or partner.vat, "partner generik tanpa PII (UU PDP)")

    def test_module_does_not_depend_on_efn_setup_online(self):
        Module = self.env["ir.module.module"].sudo()
        seen, todo = set(), [MODULE]
        while todo:
            name = todo.pop()
            if name in seen:
                continue
            seen.add(name)
            module = Module.search([("name", "=", name)], limit=1)
            todo.extend(module.dependencies_id.mapped("name"))
        for forbidden in FORBIDDEN_MODULES:
            self.assertNotIn(forbidden, seen, f"{MODULE} tidak boleh (transitif) bergantung pada {forbidden}")
        self.assertFalse([n for n in seen if "esb" in n], "SMD online tidak boleh bergantung pada modul ESB")

    # ------------------------------------------------------------------ demo
    def test_channel_identity(self):
        self._require_demo()
        channel = self.channel
        self.assertTrue(channel.active)
        self.assertEqual(channel.code, "SMD")
        self.assertEqual(channel.brand_code, "SMD")
        self.assertEqual(channel.company_id, self.company)
        self.assertEqual(self.company.efn_brand_code, "SMD")
        self.assertEqual(channel.company_id.efn_business_type, "retail")
        self.assertEqual(channel.pricelist_id.company_id, self.company)
        self.assertEqual(channel.journal_titipan_id.company_id, self.company)
        self.assertEqual(channel.journal_titipan_id.type, "bank")
        self.assertEqual(channel.partner_id, self.env.ref(f"{MODULE}.partner_superapp_smd"))
        self.assertEqual(channel.payment_timeout_minutes, 70)
        self.assertEqual(channel.stock_hold_mode, "confirm")
        self.assertGreater(channel.max_order_amount, 0)
        self.assertEqual(channel.max_line_qty, 20)
        self.assertTrue(channel.fulfilment_pickup)
        self.assertFalse(channel.fulfilment_delivery)
        self.assertFalse(channel.require_outlet_fiscal_position)
        self.assertNotIn("counter", channel._efn_online_payment_modes(), "K25-07: online wajib lunas dulu")

    def test_channel_records_not_from_other_brand_modules(self):
        self._require_demo()
        channel = self.channel
        records = [
            channel,
            channel.pricelist_id,
            channel.partner_id,
            channel.team_id,
            channel.journal_titipan_id,
            channel.cash_rounding_id,
            *channel.sudo().user_ids,
            *channel.outlet_ids,
            *channel.outlet_ids.warehouse_id,
            *channel.outlet_ids.resource_calendar_id,
        ]
        Data = self.env["ir.model.data"].sudo()
        for record in records:
            modules = set(Data.search([("model", "=", record._name), ("res_id", "=", record.id)]).mapped("module"))
            self.assertTrue(modules, f"{record} harus ber-xmlid setup SMD")
            self.assertLessEqual(modules, {MODULE, SM}, f"{record} memakai xmlid modul lain: {modules}")

    def test_outlets_are_five_store_warehouses(self):
        self._require_demo()
        outlets = self.channel.with_context(active_test=False).outlet_ids
        self.assertEqual(sorted(outlets.mapped("code")), list(OUTLET_CODES))
        expected = {self.env.ref(f"{SM}.warehouse_smd0{i}") for i in range(1, 6)}
        self.assertEqual(set(outlets.mapped("warehouse_id")), expected)
        dc = self.env.ref(f"{SM}.warehouse_smd_dc")
        self.assertNotIn(dc, outlets.mapped("warehouse_id"), "outlet online bukan DC")
        for outlet in outlets:
            self.assertTrue(EVENT_OUTLET_RE.match(outlet.code), outlet.code)
            self.assertLessEqual(len(outlet.code), 16)
            self.assertTrue(outlet.active)
            self.assertTrue(outlet.pickup_enabled)
            self.assertFalse(outlet.delivery_enabled)
            self.assertFalse(outlet.fiscal_position_id)
            self.assertEqual(outlet.company_id, self.company)
            self.assertEqual(outlet.prep_minutes, 30)
            self.assertTrue(outlet.slot_enabled)
            self.assertTrue(outlet._efn_slot_calendar())
        smd05 = outlets.filtered(lambda o: o.code == "SMD05")
        self.assertEqual(smd05._efn_slot_calendar().tz, "Asia/Makassar")
        for outlet in outlets - smd05:
            self.assertEqual(outlet._efn_slot_calendar().tz, "Asia/Jakarta", outlet.code)

    def test_technical_user_single_company_channel_group(self):
        self._require_demo()
        user = self.env.ref(f"{MODULE}.user_online_smd")
        self.assertEqual(user.login, "efn_online_smd")
        self.assertEqual(self.channel.sudo().user_ids, user)
        self.assertEqual(user.company_ids, self.company)
        self.assertEqual(user.company_id, self.company)
        group = self.env.ref("efn_online_order.group_online_channel")
        self.assertIn(group, user.all_group_ids)
        allowed = self.env["efn.online.channel"]._efn_online_allowed_groups()
        self.assertFalse(user.all_group_ids - allowed, "SEC-101: hanya Internal User + grup kanal")
        self.assertFalse(user.sudo().api_key_ids, "API key tidak pernah ada di data/repo")

    def test_catalog_via_technical_user_uses_pos_category_and_ppn(self):
        self._require_demo()
        categ = self.env.ref(f"{MODULE}.pos_categ_smd_vegetable")
        taxes = self.company.account_sale_tax_id
        self.assertTrue(taxes, "company SMD harus punya pajak penjualan default (PPN l10n_id)")
        tmpl = (
            self.env["product.template"]
            .with_company(self.company)
            .create(
                {
                    "name": "Bayam Uji Online",
                    "type": "consu",
                    "list_price": 10000.0,
                    "sale_ok": True,
                    "company_id": self.company.id,
                    "taxes_id": [Command.set(taxes.ids)],
                    "pos_categ_ids": [Command.set(categ.ids)],
                    "efn_online_channel_ids": [Command.link(self.channel.id)],
                    "efn_online_description": "Data uji.",
                }
            )
        )
        user = self.env.ref(f"{MODULE}.user_online_smd")
        result = self.env["efn.online.channel"].with_user(user).efn_online_catalog("SMD")
        block = result["channel"]
        self.assertEqual(block["code"], "SMD")
        self.assertEqual(block["brand_code"], "SMD")
        self.assertEqual(block["company_code"], "SMD")
        self.assertEqual(block["business_type"], "retail")
        self.assertEqual(sorted(o["code"] for o in block["outlets"]), list(OUTLET_CODES))
        self.assertTrue(block["fulfilment"]["scheduled"], "slot pre-order aktif")
        self.assertFalse(block["fulfilment"]["delivery"])
        items = [i for i in result["items"] if i["tmpl_id"] == tmpl.id]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["categ"]["name"], "Sayur")
        self.assertIsNone(items[0]["categ"]["parent_id"])
        self.assertTrue(items[0]["prices"][0]["tax_labels"], "harga online memuat label PPN company SMD")
