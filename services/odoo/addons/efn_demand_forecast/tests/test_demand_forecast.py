"""efn_demand_forecast (G28, K25-12): faktor musim, rumus min/max, persetujuan planner -> orderpoint."""

import math
from datetime import date, timedelta

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "efn")
class TestEfnDemandForecast(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["efn.forecast.season"].search([]).active = False
        cls.wh = cls.env["stock.warehouse"].create({"name": "Outlet forecast", "code": "OFC"})
        cls.product = cls.env["product.product"].create({"name": "Roti (uji)", "type": "consu", "is_storable": True})
        cls.customer = cls.env.ref("stock.stock_location_customers")
        cls.today = date(2026, 11, 2)  # Senin

    def _sell(self, day, qty):
        self.env["stock.quant"]._update_available_quantity(self.product, self.wh.lot_stock_id, qty)
        move = self.env["stock.move"].create(
            {
                "product_id": self.product.id,
                "product_uom_qty": qty,
                "location_id": self.wh.lot_stock_id.id,
                "location_dest_id": self.customer.id,
            }
        )
        move._action_confirm()
        move._action_assign()
        move.quantity = qty
        move.picked = True
        move._action_done()
        move.date = f"{day} 05:00:00"

    def _forecast(self, **params):
        return (
            self.env["efn.demand.forecast"]
            .with_context(tz="Asia/Jakarta")
            ._efn_generate(self.wh, self.product, date=self.today, **params)
        )

    def test_flat_demand(self):
        for i in range(1, 15):
            self._sell(self.today - timedelta(days=i), 10)
        fc = self._forecast(history_days=14, lead_days=2, review_days=7)
        self.assertAlmostEqual(fc.history_qty, 140)
        self.assertAlmostEqual(fc.base_daily, 10)
        self.assertAlmostEqual(fc.sigma_daily, 0)
        self.assertAlmostEqual(fc.season_factor, 1.0)
        self.assertEqual(fc.proposed_min, 20)
        self.assertEqual(fc.proposed_max, 90)

    def test_weekend_season_normalizes_history_and_lifts_horizon(self):
        self.env["efn.forecast.season"].create(
            {"name": "Akhir pekan uji", "kind": "weekday", "weekdays": "5,6", "factor": 2.0}
        )
        for i in range(1, 15):
            day = self.today - timedelta(days=i)
            self._sell(day, 20 if day.weekday() >= 5 else 10)
        fc = self._forecast(history_days=14, lead_days=7, review_days=0)
        # Hari akhir pekan dinormalkan /2 -> permintaan dasar datar 10/hari, simpangan 0.
        self.assertAlmostEqual(fc.base_daily, 10)
        self.assertAlmostEqual(fc.sigma_daily, 0)
        self.assertAlmostEqual(fc.season_factor, 9 / 7, places=3)
        self.assertAlmostEqual(fc.proposed_min, 90)

    def test_safety_stock_from_variability(self):
        for i in range(1, 15):
            self._sell(self.today - timedelta(days=i), 5 if i % 2 else 15)
        fc = self._forecast(history_days=14, lead_days=4, review_days=0, service_level="95")
        self.assertAlmostEqual(fc.base_daily, 10)
        self.assertGreater(fc.sigma_daily, 4)
        expected = math.ceil(10 * 4 + 1.65 * fc.sigma_daily * 2)
        self.assertEqual(fc.proposed_min, expected)

    def test_apply_requires_planner_and_updates_orderpoint(self):
        for i in range(1, 8):
            self._sell(self.today - timedelta(days=i), 6)
        fc = self._forecast(history_days=7, lead_days=1, review_days=2)
        clerk = new_test_user(self.env, login="efn_fc_clerk", groups="stock.group_stock_user")
        with self.assertRaisesRegex(UserError, "planner_only"):
            fc.with_user(clerk).action_efn_apply()
        fc.approved_max = 40
        fc.action_efn_apply()
        op = fc.orderpoint_id
        self.assertEqual(fc.state, "applied")
        self.assertEqual(op.product_min_qty, fc.approved_min)
        self.assertEqual(op.product_max_qty, 40)
        self.assertEqual(op.efn_forecast_id, fc)
        with self.assertRaisesRegex(UserError, "state"):
            fc.action_efn_apply()

    def test_new_run_supersedes_draft(self):
        first = self._forecast(history_days=7)
        second = self._forecast(history_days=7)
        self.assertEqual(first.state, "superseded")
        self.assertEqual(second.state, "draft")

    def test_season_validation_and_month_wrap(self):
        Season = self.env["efn.forecast.season"]
        with self.assertRaises(ValidationError):
            Season.create({"name": "x", "kind": "period", "date_from": "2027-01-10", "date_to": "2027-01-01"})
        payday = Season.create({"name": "gajian", "kind": "monthly", "day_from": 25, "day_to": 5, "factor": 1.5})
        self.assertTrue(payday._efn_applies(date(2026, 11, 28), self.product))
        self.assertTrue(payday._efn_applies(date(2026, 12, 3), self.product))
        self.assertFalse(payday._efn_applies(date(2026, 12, 10), self.product))
