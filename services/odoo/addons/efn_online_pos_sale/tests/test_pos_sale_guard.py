"""SEC-105: SO online tidak bisa dimuat/di-settle lewat pos_sale."""

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.efn_online_order.tests.common import EfnOnlineCommon


@tagged("post_install", "-at_install", "efn")
class TestEfnOnlinePosSaleGuard(EfnOnlineCommon):
    def test_online_so_excluded_from_pos(self):
        part, _payload, _result = self.place()
        self.pay(part)
        so = self.record(part).sale_order_id
        domain = self.env["sale.order"]._load_pos_data_domain({}, self.env["pos.config"])
        self.assertIn(("efn_online_order_id", "=", False), [tuple(d) for d in domain])
        with self.assertRaises(UserError):
            so.load_sale_order_from_pos(False)
        with self.assertRaises(UserError):
            self.env["pos.order.line"].create([{"sale_order_origin_id": so.id, "product_id": self.p_rice.id, "qty": 3}])
