"""E-38a POS (ADR-036 §0.2, §0.6; 18-30, 18-31): pos.order.email/mobile terenkripsi, partner terlindungi
tidak di-preload, pencarian kasir via HP lengkap / nama (token). Data fiktif khusus test."""

from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.service.model import get_public_method
from odoo.tests import tagged
from odoo.tools import SQL

from odoo.addons.efn_pii.tests.common import KEYRING_LOGGER, V2, PiiKeyMixin
from odoo.addons.efn_pii_pos import post_init_hook
from odoo.addons.point_of_sale.tests.common import TestPoSCommon

# format internasional: `pos.order._compute_contact_details` memformat HP dengan `_phone_format` memakai negara
# partner/company; nomor lokal `0812…` dengan negara company test (bukan ID) menghasilkan mobile kosong.
PHONE = "+62 812-7000-0001"
EMAIL = "member.pos@example.com"
# urutan field klien POS 19 untuk kueri non-angka (partner_list.js `_getSearchFields`)
POS_TEXT_FIELDS = (
    "complete_name",
    "ref",
    "company_registry",
    "vat",
    "street",
    "zip",
    "email",
    "phone_mobile_search",
    "barcode",
)


@tagged("post_install", "-at_install", "efn", "efn_pii")
class TestEfnPiiPos(PiiKeyMixin, TestPoSCommon):
    @classmethod
    def setUpClass(cls):
        # kunci dipasang SEBELUM TestPoSCommon membuat partner perorangan (protect-by-default)
        cls._pii_class_env()
        super().setUpClass()
        cls.config = cls.basic_config

    def setUp(self):
        super().setUp()
        Partner = self.env["res.partner"]
        self.member = Partner.create(
            {"name": "Dewi Lestari", "phone": PHONE, "email": EMAIL, "country_id": self.env.ref("base.id").id}
        )
        self.company = Partner.create({"name": "PT Pelanggan Grosir", "is_company": True, "phone": "021-7000-0002"})
        self.product = self.create_product("Produk PII", self.categ_basic, 10.0)

    def _sync(self, partner=False):
        if not self.config.current_session_id:
            self.open_new_session()
        data = self.create_ui_order_data([(self.product, 1)], customer=partner)
        self.env["pos.order"].sync_from_ui([data])
        return self.env["pos.order"].search([("uuid", "=", data["uuid"])])

    def _new_partner_ids(self, domain):
        result = self.env["res.partner"].get_new_partner(self.config.id, domain, 0)
        return {item["id"]: item for item in result["res.partner"]}

    # ----------------------------------------------------------------- pos.order.email / mobile
    def test_order_contact_copy_encrypted_and_bound(self):
        order = self._sync(self.member)
        self.assertEqual(order.email, EMAIL)
        if not order.mobile:
            # format HP bergantung phone_validation/negara; jalur tulis eksplisit tetap diuji
            order.write({"mobile": PHONE})
        self.assertTrue(order.mobile)
        raw = self._raw_table("pos_order", order.id, "email", "mobile")
        self.assertBound(raw["email"], "pos_order", "email", order.id, EMAIL)
        self.assertBound(raw["mobile"], "pos_order", "mobile", order.id, order.mobile)
        # email yang diketik kasir untuk struk (action_send_receipt menulis self.email) juga terenkripsi
        order.write({"email": "struk.baru@example.com"})
        raw_email = self._raw_table("pos_order", order.id, "email")["email"]
        self.assertBound(raw_email, "pos_order", "email", order.id, "struk.baru@example.com")
        # partner perusahaan (tidak terlindungi) pun: SEMUA order terenkripsi
        company_order = self._sync(self.company)
        raw = self._raw_table("pos_order", company_order.id, "mobile")
        if raw["mobile"]:
            self.assertTrue(raw["mobile"].startswith(V2))
        # kondisi plaintext atas kolom terenkripsi tidak pernah cocok; semantik NULL tetap
        self.assertFalse(self.env["pos.order"].search([("email", "ilike", "struk.baru"), ("id", "=", order.id)]))
        self.assertEqual(self.env["pos.order"].search([("email", "!=", False), ("id", "=", order.id)]), order)

    def test_order_plaintext_raises_alarm(self):
        order = self._sync(self.member)
        self.env.flush_all()
        self.env.cr.execute(SQL("UPDATE pos_order SET email = %s WHERE id = %s", "bocor@example.com", order.id))
        self.env.invalidate_all()
        with self.assertLogs(KEYRING_LOGGER, "CRITICAL") as logs:
            self.assertEqual(order.email, "bocor@example.com")
        output = "\n".join(logs.output)
        self.assertIn("plaintext_in_protected", output)
        self.assertNotIn("bocor", output)

    def test_legacy_orders_encrypted_by_hook(self):
        order = self._sync(self.member)
        self.env.flush_all()
        self.env.cr.execute(
            SQL(
                "UPDATE pos_order SET email = %s, mobile = %s WHERE id = %s",
                "lama@example.com",
                "+6281270000001",
                order.id,
            )
        )
        self.env.invalidate_all()
        post_init_hook(self.env)
        raw = self._raw_table("pos_order", order.id, "email", "mobile")
        self.assertBound(raw["email"], "pos_order", "email", order.id, "lama@example.com")
        self.assertBound(raw["mobile"], "pos_order", "mobile", order.id, "+6281270000001")

    def test_locked_sale_continues_without_copy(self):
        # compute tertunda dari setUp (token nama member) di-flush selagi kunci masih ada
        self.env.flush_all()
        self._pii_locked()
        self.env.invalidate_all()
        order = self._sync(self.company)
        self.assertTrue(order)
        self.assertFalse(order.email)
        self.assertFalse(order.mobile)
        with self.assertLogs(KEYRING_LOGGER, "CRITICAL"), self.assertRaises(UserError):
            order.write({"email": "struk@example.com"})

    def test_redefined_order_field_refuses_writes(self):
        order = self._sync(self.company)
        field = self.env["pos.order"]._fields["email"]
        with patch.object(field, "efn_pii_encrypted", False):
            with self.assertLogs(KEYRING_LOGGER, "CRITICAL"), self.assertRaises(UserError):
                order.write({"email": "x@example.com"})

    # ----------------------------------------------------------------- preload & pencarian kasir
    def test_protected_partners_not_preloaded(self):
        self.env["ir.config_parameter"].sudo().set_param("point_of_sale.limited_customer_count", 100000)
        ids = {row[0] for row in self.config.get_limited_partners_loading()}
        self.assertNotIn(self.member.id, ids)
        self.assertIn(self.company.id, ids)
        # get_new_partner tanpa kueri (gulir daftar) juga tanpa partner terlindungi
        loaded = self._new_partner_ids([])
        self.assertNotIn(self.member.id, loaded)
        # pengecualian 18-30: partner order draft yang dimuat sesi tetap ikut
        data = {"pos.order": [{"partner_id": self.member.id}]}
        domain = self.env["res.partner"]._load_pos_data_domain(data, self.config)
        self.assertIn(self.member, self.env["res.partner"].search(domain))
        # override tidak dapat dipanggil lewat RPC (api.private)
        # (assertRaises Odoo tidak menerima tuple exception)
        denied = False
        try:
            get_public_method(self.config, "get_limited_partners_loading")
        except (AccessError, AttributeError):
            denied = True
        self.assertTrue(denied, "get_limited_partners_loading masih dapat dipanggil RPC")

    def test_cashier_search_full_phone_and_name(self):
        found = self._new_partner_ids([("phone_mobile_search", "ilike", "081270000001")])
        self.assertIn(self.member.id, found)
        self.assertEqual(found[self.member.id]["name"], "Dewi Lestari")
        self.assertNotIn(self.member.id, self._new_partner_ids([("phone_mobile_search", "ilike", "7000")]))
        self.assertIn(self.member.id, self._new_partner_ids([("complete_name", "ilike", "dewi")]))
        self.assertIn(self.member.id, self._new_partner_ids([("complete_name", "ilike", "dewi lest")]))
        self.assertNotIn(self.member.id, self._new_partner_ids([("complete_name", "ilike", "estari")]))
        self.assertIn(self.member.id, self._new_partner_ids([("email", "ilike", EMAIL)]))
        self.assertNotIn(self.member.id, self._new_partner_ids([("email", "ilike", "member.pos")]))
        # domain OR seperti klien POS 19: tetap jalan tanpa error dan menemukan nama
        domain = ["|"] * (len(POS_TEXT_FIELDS) - 1) + [(f, "ilike", "Dewi") for f in POS_TEXT_FIELDS]
        self.assertIn(self.member.id, self._new_partner_ids(domain))
