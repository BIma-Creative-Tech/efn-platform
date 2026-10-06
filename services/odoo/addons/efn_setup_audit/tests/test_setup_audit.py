"""Aturan efn_setup_audit termuat dan bekerja dengan efn_audit (docs/08 §5.1, F-17).

Termasuk regresi bug OCA auditlog × ORM 19 (create produk saat record lain belum di-flush) dan
jaminan `stock.quant` mutasi stok tidak memicu read audit.
"""

import logging
import time
from unittest.mock import patch

from odoo import Command
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.efn_audit.models.base import Base as EfnAuditBase

_logger = logging.getLogger(__name__)

RULE_XMLIDS = (
    "audit_rule_product_pricelist",
    "audit_rule_product_pricelist_item",
    "audit_rule_product_template",
    "audit_rule_loyalty_program",
    "audit_rule_loyalty_reward",
    "audit_rule_loyalty_rule",
    "audit_rule_stock_quant",
    "audit_rule_res_partner",
    "audit_rule_ir_config_parameter",
)


@tagged("post_install", "-at_install", "efn")
class TestEfnSetupAudit(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Log = cls.env["efn.audit.log"].sudo()

    def setUp(self):
        super().setUp()
        self.addCleanup(self.env.registry.clear_cache)

    def _logs(self, records, operation=None):
        domain = [("model_name", "=", records._name), ("res_id", "in", records.ids)]
        if operation:
            domain.append(("operation", "=", operation))
        return self.Log.search(domain)

    def test_rules_loaded(self):
        rules = self.env["efn.audit.rule"].browse([self.env.ref(f"efn_setup_audit.{x}").id for x in RULE_XMLIDS])
        self.assertEqual(len(rules), 9)
        self.assertTrue(all(rules.mapped("active")))
        partner_rule = self.env.ref("efn_setup_audit.audit_rule_res_partner")
        self.assertEqual(set(partner_rule.masked_field_ids.mapped("name")), {"phone", "email"})
        self.assertFalse(partner_rule.log_create)
        quant = self.env["efn.audit.rule"]._efn_get_rule("stock.quant")
        self.assertEqual(quant.tracked, frozenset({"inventory_quantity", "inventory_date", "user_id"}))
        self.assertEqual(self.env["ir.config_parameter"].sudo().get_param("efn.audit.retention_days"), "730")

    def test_product_create_regression_unflushed(self):
        """Regresi bug OCA auditlog (ThrowAwayCache × ORM 19): AssertionError 'Could not find all
        values ... to flush them' saat membuat produk ketika record lain belum di-flush."""
        partner = self.env["res.partner"].create({"name": "Pemasok Regresi"})
        partner.name = "Pemasok Regresi (belum flush)"
        category = self.env["product.category"].create({"name": "Kategori Regresi"})
        attribute = self.env["product.attribute"].create(
            {"name": "Ukuran Regresi", "value_ids": [Command.create({"name": "S"}), Command.create({"name": "L"})]}
        )
        template = self.env["product.template"].create(
            {
                "name": "Produk Regresi",
                "categ_id": category.id,
                "list_price": 15000.0,
                "standard_price": 9000.0,
                "attribute_line_ids": [
                    Command.create({"attribute_id": attribute.id, "value_ids": [Command.set(attribute.value_ids.ids)]})
                ],
            }
        )
        template.write({"list_price": 17500.0, "name": "Produk Regresi 2"})
        self.env.flush_all()
        self.assertEqual(len(template.product_variant_ids), 2)
        self.assertEqual(len(self._logs(template, "create")), 1)
        write = self._logs(template, "write")
        price = write.line_ids.filtered(lambda line: line.field_name == "list_price")
        self.assertEqual((float(price.old_value), float(price.new_value)), (15000.0, 17500.0))

    def test_stock_quant_mutation_no_audit_read(self):
        product = self.env["product.product"].create({"name": "Stok Audit", "is_storable": True})
        location = self.env["stock.warehouse"].search([("company_id", "=", self.env.company.id)], limit=1).lot_stock_id
        Quant = self.env["stock.quant"].sudo()
        Quant._update_available_quantity(product, location, 10)
        quant = Quant._gather(product, location)
        with patch.object(EfnAuditBase, "_efn_audit_read", autospec=True, side_effect=AssertionError("read")) as mocked:
            Quant._update_available_quantity(product, location, 5)
            quant.write({"quantity": 20})
        mocked.assert_not_called()
        self.assertFalse(self._logs(quant))
        quant.inventory_quantity = 18
        log = self._logs(quant, "write")
        self.assertEqual(log.line_ids.mapped("field_name"), ["inventory_quantity"])

    def test_config_parameter_filter(self):
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("efn.pos.otp_required_for_promo", "False")
        ICP.set_param("web.base.url.efn_test", "x")
        efn = ICP.search([("key", "=", "efn.pos.otp_required_for_promo")])
        other = ICP.search([("key", "=", "web.base.url.efn_test")])
        self.assertTrue(self._logs(efn))
        self.assertFalse(self._logs(other))

    def test_partner_phone_masked(self):
        partner = self.env["res.partner"].create({"name": "Member Setup Audit", "phone": "0812-3456-7890"})
        self.assertFalse(self._logs(partner, "create"), "create partner tidak dicatat (volume)")
        partner.phone = "0813-1111-2222"
        line = self._logs(partner, "write").line_ids
        self.assertEqual((line.old_value, line.new_value), ("62812****890", "62813****222"))

    def test_loyalty_program_logged(self):
        program = self.env["loyalty.program"].create({"name": "Promo Audit", "program_type": "promotion"})
        program.write({"name": "Promo Audit 2"})
        self.assertTrue(self._logs(program, "create"))
        self.assertTrue(self._logs(program, "write"))

    def test_product_import_overhead(self):
        """Performa dasar: create 200 produk dengan aturan product.template semua field."""
        rule = self.env.ref("efn_setup_audit.audit_rule_product_template")

        def run(prefix):
            self.env.flush_all()
            queries, start = self.env.cr.sql_log_count, time.monotonic()
            self.env["product.template"].create([{"name": f"{prefix} {i}", "list_price": i} for i in range(200)])
            self.env.flush_all()
            return self.env.cr.sql_log_count - queries, time.monotonic() - start

        q_on, t_on = run("Audit On")
        rule.active = False
        q_off, t_off = run("Audit Off")
        _logger.info(
            "EFN audit perf: 200 product.template on=%d query/%.2fs off=%d query/%.2fs", q_on, t_on, q_off, t_off
        )
        self.assertLess(q_on - q_off, 50, "overhead audit create batch tidak sebanding jumlah produk")
