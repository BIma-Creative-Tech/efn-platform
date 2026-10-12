"""Tour UI POS E-31 (refund ke metode asal), tag `efn_tour` (Chrome headless, job CI odoo-tour)."""

from odoo import Command
from odoo.tests import tagged

from odoo.addons.point_of_sale.tests.test_frontend import TestPointOfSaleHttpCommon


@tagged("post_install", "-at_install", "efn_tour")
class TestEfnPosRefundOriginTour(TestPointOfSaleHttpCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["loyalty.program"].search([]).write({"active": False})
        cls.product = cls.env["product.template"].create(
            {
                "name": "Produk Tour Refund",
                "list_price": 25000.0,
                "available_in_pos": True,
                "taxes_id": [Command.clear()],
            }
        )

    def test_refund_only_origin_method(self):
        self.main_pos_config.with_user(self.pos_user).open_ui()
        self.start_pos_tour("EfnPosRefundOriginTour", login="pos_user")
        refund = self.env["pos.order"].search(
            [("config_id", "=", self.main_pos_config.id), ("amount_total", "<", 0)], order="id desc", limit=1
        )
        self.assertEqual(refund.state, "paid")
        self.assertEqual(set(refund.payment_ids.mapped("payment_method_id.name")), {"Bank"})
        self.assertEqual(sum(refund.payment_ids.mapped("amount")), -25000.0)
