"""Tour UI POS E-45 (bayar pesanan online di kasir), tag `efn_tour` (Chrome headless, job odoo-tour)."""

from odoo.tests import tagged

from odoo.addons.point_of_sale.tests.test_frontend import TestPointOfSaleHttpCommon

from .common import make_counter_channel, online_record, place_counter, set_test_params

TOUR_CODE = "HJKM2345"  # sama dengan static/tests/tours/efn_counter_pay_tour.js


@tagged("post_install", "-at_install", "efn_tour")
class TestEfnOnlineCounterTour(TestPointOfSaleHttpCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["loyalty.program"].search([]).write({"active": False})
        set_test_params(cls.env)
        config = cls.main_pos_config
        cls.fx = make_counter_channel(
            cls.env, config.company_id, config.picking_type_id.warehouse_id, "TCU", "efn_online_tcu"
        )

    def test_pay_online_order_at_counter(self):
        part, _payload, _result = place_counter(self.env, self.fx, TOUR_CODE)
        online = online_record(self.env, part)
        self.assertTrue(self.main_pos_config.efn_online_counter_enabled)
        self.main_pos_config.with_user(self.pos_user).open_ui()
        self.start_pos_tour("EfnOnlineCounterPayTour", login="pos_user")
        self.assertEqual(online.state, "confirmed")
        settlement = self.env["efn.online.counter.settlement"].sudo().search([("online_order_id", "=", online.id)])
        self.assertEqual(settlement.pos_order_id.state, "paid")
        self.assertFalse(settlement.pos_order_id.partner_id)
        logs = self.env["efn.online.counter.lookup"].sudo().search([("config_id", "=", self.main_pos_config.id)])
        self.assertEqual(sorted(logs.mapped("result")), ["not_found", "ok"], "satu kode salah lalu benar")
