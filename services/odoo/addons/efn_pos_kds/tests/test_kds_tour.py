"""efn_pos_kds — HttpCase: controller layar KDS (tag ``efn``) dan tour UI (tag ``efn_tour``).

Tour butuh Chrome headless (image target ``test``), sama seperti tour efn_pos_otp_gate.
"""

import json
import uuid
from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.tests import HttpCase, new_test_user, tagged
from odoo.tools import mute_logger

from odoo.addons.pos_restaurant.tests.test_frontend import TestFrontendCommon

SERVER_DATE = "2026-10-08 10:00:00"
DEVICE_GROUPS = "base.group_user,efn_pos_kds.group_kds_device"
POS_GROUPS = "base.group_user,point_of_sale.group_pos_user"


@tagged("post_install", "-at_install", "efn")
class TestEfnKdsController(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        company = env.company
        cls.station_a = env["efn.kds.station"].create({"name": "Stasiun A", "company_id": company.id})
        cls.station_b = env["efn.kds.station"].create({"name": "Stasiun B", "company_id": company.id})
        cls.device = new_test_user(env, login="efn_kds_ctl_dev", groups=DEVICE_GROUPS)
        cls.station_a.device_user_ids = [Command.link(cls.device.id)]
        cls.cashier = new_test_user(env, login="efn_kds_ctl_cashier", groups=POS_GROUPS)

    def test_device_opens_own_station_only(self):
        self.authenticate("efn_kds_ctl_dev", "efn_kds_ctl_dev")
        response = self.url_open(f"/efn_kds/{self.station_a.id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.station_a.sudo().access_token, response.text)
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        response = self.url_open(f"/efn_kds/{self.station_b.id}")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(self.station_b.sudo().access_token, response.text)
        self.assertEqual(self.url_open("/efn_kds/999999").status_code, 404)

    def test_cashier_and_anonymous_denied(self):
        self.authenticate("efn_kds_ctl_cashier", "efn_kds_ctl_cashier")
        response = self.url_open(f"/efn_kds/{self.station_a.id}")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(self.station_a.sudo().access_token, response.text)
        self.authenticate(None, None)
        response = self.url_open(f"/efn_kds/{self.station_a.id}", allow_redirects=False)
        self.assertIn(response.status_code, (302, 303))
        self.assertIn("/web/login", response.headers.get("Location", ""))


@tagged("post_install", "-at_install", "efn_tour")
class TestEfnKdsTour(TestFrontendCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        food = env["pos.category"].search([("name", "=", "Food")], limit=1)
        drinks = env["pos.category"].search([("name", "=", "Drinks")], limit=1)
        cls.printer = cls.pos_config.printer_ids[:1]
        cls.product_rice = env["product.product"].create(
            {
                "name": "KDS Nasi Tour",
                "available_in_pos": True,
                "list_price": 3.0,
                "pos_categ_ids": [Command.set(food.ids)],
                "taxes_id": [Command.clear()],
            }
        )
        cls.station_kitchen = env["efn.kds.station"].create(
            {
                "name": "Dapur Panas Tour",
                "company_id": cls.pos_config.company_id.id,
                "config_ids": [Command.link(cls.pos_config.id)],
                "category_ids": [Command.set(food.ids)],
            }
        )
        cls.station_bar = env["efn.kds.station"].create(
            {
                "name": "Bar Tour",
                "company_id": cls.pos_config.company_id.id,
                "config_ids": [Command.link(cls.pos_config.id)],
                "category_ids": [Command.set(drinks.ids)],
                "mode": "kds_printer",
                "fallback_printer_id": cls.printer.id,
            }
        )
        cls.device = new_test_user(
            env,
            login="efn_kds_tour_dev",
            groups="base.group_user,efn_pos_kds.group_kds_device",
            company_id=cls.pos_config.company_id.id,
            company_ids=[Command.set(cls.pos_config.company_id.ids)],
        )
        cls.station_kitchen.device_user_ids = [Command.link(cls.device.id)]

    def _tickets(self, station):
        return self.env["efn.kds.ticket"].sudo().search([("station_id", "=", station.id)], order="id")

    def _assert_routed(self):
        kitchen = self._tickets(self.station_kitchen)
        bar = self._tickets(self.station_bar)
        self.assertEqual(len(kitchen), 1, "1 tiket di dapur")
        self.assertEqual(len(bar), 1, "1 tiket di bar")
        self.assertEqual(kitchen.line_ids.product_id, self.product_rice)
        self.assertEqual(bar.line_ids.product_name, "Coca-Cola")
        self.assertEqual(kitchen.table_label, "5 / Main Floor")
        return kitchen, bar

    def test_pos_order_routed_to_stations(self):
        self.pos_config.with_user(self.pos_user).open_ui()
        self.start_pos_tour("EfnKdsPosOrderTour")
        _kitchen, bar = self._assert_routed()
        self.assertFalse(bar.printed_offline)

    def test_kds_station_down_prints_fallback(self):
        self.station_bar.write({"mode": "kds", "last_seen": fields.Datetime.now() - timedelta(minutes=5)})
        self.pos_config.with_user(self.pos_user).open_ui()
        self.start_pos_tour("EfnKdsPosFallbackTour")
        _kitchen, bar = self._assert_routed()
        self.assertTrue(bar.printed_offline, "dapur sudah memegang cetakan cadangan")
        self.assertEqual(bar.state, "in_progress")

    def test_kds_station_alive_suppresses_printer(self):
        self.station_bar.write({"mode": "kds", "last_seen": fields.Datetime.now() + timedelta(minutes=30)})
        self.pos_config.with_user(self.pos_user).open_ui()
        self.start_pos_tour("EfnKdsPosSuppressTour")
        _kitchen, bar = self._assert_routed()
        self.assertFalse(bar.printed_offline)
        self.assertEqual(bar.state, "new")

    def test_kds_ticket_failure_forces_fallback_print(self):
        """SEC-76: layar hidup (printer ditahan) tetapi tiket gagal di server -> cetak cadangan."""
        self.station_bar.write({"mode": "kds", "last_seen": fields.Datetime.now() + timedelta(minutes=30)})
        self.pos_config.with_user(self.pos_user).open_ui()
        ticket_cls = type(self.env["efn.kds.ticket"])
        with (
            patch.object(ticket_cls, "_efn_kds_create_for_order", side_effect=ValueError("boom")),
            mute_logger("odoo.addons.efn_pos_kds.alert"),
        ):
            self.start_pos_tour("EfnKdsPosTicketFailedTour")
        self.assertFalse(self._tickets(self.station_bar))
        order = self.env["pos.order"].search([("config_id", "=", self.pos_config.id)], order="id desc", limit=1)
        self.assertTrue(order.efn_kds_ticket_failed)

    def test_kds_screen_bump_to_served(self):
        self.pos_config.with_user(self.pos_user).open_ui()
        session = self.pos_config.current_session_id
        customer = self.env["res.partner"].create({"name": "Pelanggan Tour KDS", "phone": "+6281299990002"})
        coke = self.env["product.product"].search([("name", "=", "Coca-Cola")], limit=1)
        lines, prep = [], {}
        for product in (self.product_rice, coke):
            line_uuid = str(uuid.uuid4())
            lines.append(
                Command.create(
                    {
                        "uuid": line_uuid,
                        "product_id": product.id,
                        "qty": 1,
                        "price_unit": product.lst_price,
                        "price_subtotal": product.lst_price,
                        "price_subtotal_incl": product.lst_price,
                        "tax_ids": [],
                    }
                )
            )
            prep[line_uuid] = {"uuid": line_uuid, "product_id": product.id, "quantity": 1}
        self.env["pos.order"].sync_from_ui(
            [
                {
                    "uuid": str(uuid.uuid4()),
                    "session_id": session.id,
                    "state": "draft",
                    "lines": lines,
                    "payment_ids": [],
                    "amount_paid": 0,
                    "amount_return": 0,
                    "amount_tax": 0,
                    "amount_total": self.product_rice.lst_price + coke.lst_price,
                    "partner_id": customer.id,
                    "table_id": self.main_floor_table_5.id,
                    "pricelist_id": self.pos_config.pricelist_id.id,
                    "user_id": self.pos_user.id,
                    "last_order_preparation_change": json.dumps(
                        {"lines": prep, "metadata": {"serverDate": SERVER_DATE}}
                    ),
                }
            ]
        )
        kitchen = self._tickets(self.station_kitchen)
        self.assertEqual(len(kitchen), 1)
        self.start_tour(f"/efn_kds/{self.station_kitchen.id}", "EfnKdsScreenTour", login="efn_kds_tour_dev")
        self.assertEqual(kitchen.state, "served")
        self.assertTrue(kitchen.started_at and kitchen.ready_at and kitchen.served_at)
        self.assertEqual(self._tickets(self.station_bar).state, "new", "tiket bar tidak tersentuh")
