"""SEC-63 (r6): barcode diskon `22` tidak aktif di nomenklatur mana pun; filter audit label harga."""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "efn")
class TestEfnBarcodeHardening(TransactionCase):
    def test_no_discount_barcode_rule_in_any_nomenclature(self):
        self.assertFalse(self.env["barcode.rule"].search_count([("type", "=", "discount")]))
        rule = self.env.ref("point_of_sale.barcode_rule_discount", raise_if_not_found=False)
        if rule:
            self.assertEqual(rule.type, "product")
            self.assertEqual(rule.pattern, "22")

    def test_discount_barcode_does_not_parse_as_discount(self):
        for company in self.env["res.company"].search([]):
            nomenclature = company.nomenclature_id
            if not nomenclature:
                continue
            parsed = nomenclature.parse_barcode("2250")
            parsed = parsed[0] if isinstance(parsed, list) else parsed
            self.assertNotEqual(parsed.get("type"), "discount", company.name)

    def test_weighed_price_label_filter(self):
        audit = self.env.ref("efn_setup_pos.filter_pos_line_weighed_price_label")
        self.assertIn("to_weight", audit.domain)
        self.assertIn("manual", audit.domain)
