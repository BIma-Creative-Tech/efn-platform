"""G20 W-33 / E-35b: batas berat pesanan per kanal (docs/04 §12.3, docs/08 §11.7).

``max_order_weight_kg`` (0 = mati) + ``weight_missing_policy`` (``block`` | ``ignore``); total = Σ
``product.weight × product_qty`` (rumus ``delivery.carrier._match_weight`` 19.0). Issue ``weight_limit``
(``product_id`` null) dan ``weight_missing`` masuk ``quote_hash``; place menolak (``invalid_input``). Katalog
``weight_kg`` nullable. Angka = data uji.
"""

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import EfnOnlineCommon
from .test_online_g19 import EfnOnlineG19Common


def _code(exc):
    return str(exc.exception.args[0])


class _WeightMixin:
    def counts(self):
        return (
            self.env["sale.order"].sudo().search_count([]),
            self.env["efn.online.order"].sudo().search_count([]),
        )


@tagged("post_install", "-at_install", "efn")
class TestEfnOnlineWeightLimit(_WeightMixin, EfnOnlineCommon):
    """Kanal TST mode ``none`` (tanpa mesin promo -> quote ``sale.order.new()``)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.p_rice.weight = 5.0
        cls.p_bag.weight = 0.1

    def test_defaults_and_constraints(self):
        Channel = self.env["efn.online.channel"]
        channel = Channel.create(
            {
                "name": "Kanal berat",
                "code": "TSW",
                "company_id": self.company.id,
                "pricelist_id": self.pricelist.id,
                "partner_id": self.channel.partner_id.id,
                "journal_titipan_id": self.channel.journal_titipan_id.id,
            }
        )
        self.assertEqual(channel.max_order_weight_kg, 0.0)
        self.assertEqual(channel.weight_missing_policy, "block")
        for bad in (-0.5, 100000.5):
            with self.subTest(bad=bad), self.assertRaises(ValidationError), self.cr.savepoint():
                channel.max_order_weight_kg = bad
        channel.max_order_weight_kg = 12.5
        self.assertEqual(channel.max_order_weight_kg, 12.5)

    def test_limit_off_keeps_g19_hash_and_no_issue(self):
        """Batas 0: berat tidak masuk hash (quote yang sedang berjalan saat deploy tetap sah) dan tanpa issue."""
        before = self.quote()
        self.p_rice.weight = 7.0
        self.p_bag.weight = 0.0
        after = self.quote([{"product_id": self.p_rice.id, "qty": 3}, {"product_id": self.p_bag.id, "qty": 1}])
        self.assertEqual(after["issues"], [])
        self.assertEqual(self.quote()["quote_hash"], before["quote_hash"])

    def test_weight_limit_issue_quote_and_place(self):
        self.channel.max_order_weight_kg = 10.0
        over = self.quote([{"product_id": self.p_rice.id, "qty": 3}])  # 15 kg
        self.assertEqual(over["issues"], [{"product_id": None, "code": "weight_limit"}])
        self.assertTrue(all(line["available"] for line in over["lines"]))  # bukan masalah stok
        before = self.counts()
        with self.assertRaises(UserError) as exc:
            self.place(lines=[{"product_id": self.p_rice.id, "qty": 3}], quote_hash=over["quote_hash"])
        self.assertEqual(_code(exc), "efn_online:invalid_input")
        self.assertEqual(self.counts(), before)
        # Tepat di batas (10 kg) = boleh; place memakai hash quote yang sama.
        lines = [{"product_id": self.p_rice.id, "qty": 2}]
        exact = self.quote(lines)
        self.assertEqual(exact["issues"], [])
        part, _payload, result = self.place(lines=lines, quote_hash=exact["quote_hash"])
        self.assertEqual(result["amount_payable"], exact["amount_payable"])
        self.assertTrue(self.record(part))

    def test_weight_missing_block_and_ignore(self):
        self.channel.max_order_weight_kg = 50.0
        self.p_bag.weight = 0.0
        lines = [{"product_id": self.p_rice.id, "qty": 1}, {"product_id": self.p_bag.id, "qty": 2}]
        blocked = self.quote(lines)
        self.assertEqual(blocked["issues"], [{"product_id": self.p_bag.id, "code": "weight_missing"}])
        with self.assertRaises(UserError) as exc:
            self.place(lines=lines, quote_hash=blocked["quote_hash"])
        self.assertEqual(_code(exc), "efn_online:invalid_input")
        # Jasa tidak pernah dianggap kurang data berat.
        service = self.env["product.product"].create(
            {
                "name": "Jasa bungkus kado",
                "type": "service",
                "list_price": 1000.0,
                "company_id": self.company.id,
                "taxes_id": [(6, 0, self.tax.ids)],
                "efn_online_channel_ids": [(6, 0, self.channel.ids)],
            }
        )
        self.assertEqual(self.quote([{"product_id": service.id, "qty": 1}])["issues"], [])
        # Kebijakan ignore: produk tanpa berat dihitung 0 kg; kebijakan masuk hash.
        self.channel.weight_missing_policy = "ignore"
        ignored = self.quote(lines)
        self.assertEqual(ignored["issues"], [])
        self.assertNotEqual(ignored["quote_hash"], blocked["quote_hash"])
        part, _payload, _result = self.place(lines=lines, quote_hash=ignored["quote_hash"])
        self.assertTrue(self.record(part))

    def test_weight_and_limit_change_invalidate_quote_hash(self):
        self.channel.max_order_weight_kg = 50.0
        lines = [{"product_id": self.p_rice.id, "qty": 2}]
        first = self.quote(lines)
        self.p_rice.weight = 6.0
        self.assertNotEqual(self.quote(lines)["quote_hash"], first["quote_hash"])
        with self.assertRaises(UserError) as exc:
            self.place(lines=lines, quote_hash=first["quote_hash"])
        self.assertEqual(_code(exc), "efn_online:price_changed")
        self.p_rice.weight = 5.0
        self.assertEqual(self.quote(lines)["quote_hash"], first["quote_hash"])
        self.channel.max_order_weight_kg = 40.0
        self.assertNotEqual(self.quote(lines)["quote_hash"], first["quote_hash"])

    def test_catalog_weight_kg(self):
        self.p_bag.weight = 0.0
        catalog = self.channel_api().efn_online_catalog("TST")
        weights = {item["product_id"]: item["weight_kg"] for item in catalog["items"]}
        self.assertEqual(weights[self.p_rice.id], 5.0)
        self.assertIsNone(weights[self.p_bag.id])

    def test_weight_in_lbs_database_converted_to_kg(self):
        """``product.weight_in_lbs`` = '1': berat tersimpan dalam lb -> dikonversi ke kg (katalog & batas)."""
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("product.weight_in_lbs", "1")
        # Cache get_param (ormcache) dibersihkan lagi sebelum rollback test agar test lain tidak melihat '1'.
        self.addCleanup(params.set_param, "product.weight_in_lbs", False)
        self.p_rice.weight = 10.0  # lb
        item = next(
            i for i in self.channel_api().efn_online_catalog("TST")["items"] if i["product_id"] == self.p_rice.id
        )
        self.assertAlmostEqual(item["weight_kg"], 4.536, delta=0.001)
        self.channel.max_order_weight_kg = 9.0  # kg: 2 x 4,536 = 9,072 kg > 9
        quote = self.quote([{"product_id": self.p_rice.id, "qty": 2}])
        self.assertEqual(quote["issues"], [{"product_id": None, "code": "weight_limit"}])


@tagged("post_install", "-at_install", "efn")
class TestEfnOnlineWeightLimitPromoConfirm(_WeightMixin, EfnOnlineG19Common):
    """Mode confirm + promo otomatis (mesin promo -> place menghitung dari SO tersimpan)."""

    def test_weight_limit_with_auto_promo_rejected_at_place(self):
        self.p_rice.weight = 5.0
        self.program("Diskon otomatis 10%")
        self.channel.max_order_weight_kg = 10.0
        lines = [{"product_id": self.p_rice.id, "qty": 3}]
        quote = self.quote(lines)
        self.assertTrue(self.reward_lines(quote))  # mesin promo dipakai
        self.assertEqual(quote["issues"], [{"product_id": None, "code": "weight_limit"}])
        before = self.counts()
        with self.assertRaises(UserError) as exc:
            self.place(lines=lines, quote_hash=quote["quote_hash"])
        self.assertEqual(_code(exc), "efn_online:invalid_input")
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.free(), 10.0)  # tidak ada reservasi tersisa
        # Reward diskon (produk diskon tanpa berat) tidak memicu weight_missing.
        ok_lines = [{"product_id": self.p_rice.id, "qty": 2}]
        ok = self.quote(ok_lines)
        self.assertEqual(ok["issues"], [])
        part, _payload, _result = self.place(lines=ok_lines, quote_hash=ok["quote_hash"])
        self.assertEqual(self.record(part).state, "placed")
