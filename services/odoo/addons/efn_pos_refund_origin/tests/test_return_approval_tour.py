"""Tour UI POS E-41 (retur tanpa struk + persetujuan Store Manager), tag `efn_tour` (Chrome headless, job odoo-tour)."""

from odoo import Command
from odoo.tests import new_test_user, tagged

from odoo.addons.efn_pos_refund_origin.models.return_pin import hash_pin
from odoo.addons.point_of_sale.tests.test_frontend import TestPointOfSaleHttpCommon

TOUR_PIN = "135790"


@tagged("post_install", "-at_install", "efn_tour")
class TestEfnPosReturnApprovalTour(TestPointOfSaleHttpCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["loyalty.program"].search([]).write({"active": False})
        cls.env["ir.config_parameter"].sudo().set_param("efn.pos.refund.unlinked_return_mode", "manager_approval")
        cls.env["ir.config_parameter"].sudo().set_param("efn.pos.return_pin_kdf_rounds", "1000")
        cls.product = cls.env["product.template"].create(
            {
                "name": "Produk Tour Retur",
                "list_price": 25000.0,
                "available_in_pos": True,
                "taxes_id": [Command.clear()],
            }
        )
        company = cls.main_pos_config.company_id
        cls.store_manager = new_test_user(
            cls.env,
            login="efn_tour_sm",
            name="Store Manager Tour",
            groups="base.group_user,point_of_sale.group_pos_manager",
            company_id=company.id,
        )
        cls.store_manager.sudo().write({"efn_return_pin_hash": hash_pin(cls.env, TOUR_PIN)})

    def test_unlinked_return_needs_manager_pin(self):
        self.main_pos_config.with_user(self.pos_user).open_ui()
        self.start_pos_tour("EfnPosReturnApprovalTour", login="pos_user")
        order = self.env["pos.order"].search(
            [("config_id", "=", self.main_pos_config.id), ("amount_total", "<", 0)], order="id desc", limit=1
        )
        self.assertEqual(order.state, "paid")
        self.assertEqual(set(order.payment_ids.mapped("payment_method_id.is_cash_count")), {True})
        approvals = self.env["efn.pos.return.approval"].sudo().search([("order_ref", "=", order.uuid)])
        self.assertEqual(sorted(approvals.mapped("state")), ["consumed", "failed"], "satu PIN salah lalu benar")
        consumed = approvals.filtered(lambda a: a.state == "consumed")
        self.assertEqual((consumed.approver_user_id, consumed.cashier_user_id), (self.store_manager, self.pos_user))
        self.assertEqual(consumed.consumed_order_id, order)
