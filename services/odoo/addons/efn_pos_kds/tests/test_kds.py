"""efn_pos_kds — test unit (docs/08 §7.3 "Test"). Tag ``efn``.

Order disimulasikan seperti frontend POS 19: ``sync_from_ui`` dengan
``last_order_preparation_change`` (pemicu "dikirim ke dapur" standar) — tiket dihitung server.
"""

import json
import uuid
from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import new_test_user, tagged

from odoo.addons.point_of_sale.tests.common import TestPoSCommon

CUSTOMER_NAME = "Pelanggan Rahasia KDS"
CUSTOMER_PHONE = "+6281299990001"  # fiktif
PAYLOAD_KEYS = {
    "id",
    "state",
    "table_label",
    "tracking_number",
    "preset_name",
    "course_index",
    "is_rush",
    "printed_offline",
    "internal_note",
    "sequence_no",
    "sent_at",
    "ready_at",
    "served_at",
    "lines",
}
LINE_KEYS = {"id", "product_name", "qty_delta", "attribute_text", "note", "combo_parent_name", "state"}


@tagged("post_install", "-at_install", "efn")
class TestEfnKdsCommon(TestPoSCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.config = cls.basic_config
        cls.company = cls.config.company_id
        cls.categ_kitchen = env["pos.category"].create({"name": "KDS Dapur"})
        cls.categ_kitchen_sub = env["pos.category"].create({"name": "KDS Nasi", "parent_id": cls.categ_kitchen.id})
        cls.categ_bar = env["pos.category"].create({"name": "KDS Bar"})
        cls.categ_misc = env["pos.category"].create({"name": "KDS Lain"})

        def product(name, categ):
            return env["product.product"].create(
                {
                    "name": name,
                    "available_in_pos": True,
                    "list_price": 10.0,
                    "taxes_id": [Command.clear()],
                    "pos_categ_ids": [Command.set(categ.ids)] if categ else [],
                }
            )

        cls.p_rice = product("KDS Nasi Goreng", cls.categ_kitchen_sub)
        cls.p_coffee = product("KDS Es Kopi", cls.categ_bar)
        cls.p_misc = product("KDS Kerupuk", cls.categ_misc)
        cls.p_combo = product("KDS Paket", cls.categ_misc)

        cls.station_kitchen = env["efn.kds.station"].create(
            {
                "name": "Dapur Panas",
                "company_id": cls.company.id,
                "config_ids": [Command.link(cls.config.id)],
                "category_ids": [Command.link(cls.categ_kitchen.id)],
            }
        )
        cls.station_bar = env["efn.kds.station"].create(
            {
                "name": "Bar",
                "company_id": cls.company.id,
                "config_ids": [Command.link(cls.config.id)],
                "category_ids": [Command.link(cls.categ_bar.id)],
            }
        )
        cls.device_kitchen = new_test_user(
            env,
            login="efn_kds_dev_kitchen",
            groups="base.group_user,efn_pos_kds.group_kds_device",
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )
        cls.device_bar = new_test_user(
            env,
            login="efn_kds_dev_bar",
            groups="base.group_user,efn_pos_kds.group_kds_device",
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )
        cls.station_kitchen.device_user_ids = [Command.link(cls.device_kitchen.id)]
        cls.station_bar.device_user_ids = [Command.link(cls.device_bar.id)]
        cls.plain_user = new_test_user(env, login="efn_kds_plain", groups="base.group_user")
        cls.cashier = new_test_user(
            env,
            login="efn_kds_cashier",
            groups="base.group_user,point_of_sale.group_pos_user",
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )
        cls.customer = env["res.partner"].create({"name": CUSTOMER_NAME, "phone": CUSTOMER_PHONE})

        floor = env["restaurant.floor"].create({"name": "KDS Lantai", "pos_config_ids": [Command.link(cls.config.id)]})
        cls.table = env["restaurant.table"].create({"table_number": 12, "floor_id": floor.id, "seats": 2})

    def setUp(self):
        super().setUp()
        self.open_new_session()
        self.sent = []
        bus_cls = type(self.env["bus.bus"])
        sent = self.sent

        def fake_sendone(_bus, target, notification_type, message):
            sent.append((target, notification_type, message))

        patcher = patch.object(bus_cls, "_sendone", autospec=True, side_effect=fake_sendone)
        patcher.start()
        self.addCleanup(patcher.stop)

    # ------------------------------------------------------------------ helper
    def _line(self, product, qty, line_uuid=None, **kw):
        return {"product": product, "quantity": qty, "uuid": line_uuid or str(uuid.uuid4()), **kw}

    def _sync(self, lines, order_uuid=None, sent=None, extra=None, prep=True):
        """Sinkron order draft. ``sent`` = uuid baris yang sudah "dikirim ke dapur" (default semua)."""
        customer = (extra or {}).pop("_customer", False)
        data = self.create_ui_order_data(lines, payments=[], uuid=order_uuid, customer=customer)
        # Frontend mengirim list (sinkron ulang mengubah CREATE -> UPDATE di tempat).
        data["lines"] = [list(command) for command in data["lines"]]
        sent_uuids = [line["uuid"] for line in lines] if sent is None else sent
        prep_lines = {}
        for line in lines:
            if line["uuid"] in sent_uuids:
                prep_lines[line["uuid"]] = {
                    "uuid": line["uuid"],
                    "product_id": line["product"].id,
                    "quantity": line["quantity"],
                }
        change = {"lines": prep_lines, "metadata": {"serverDate": fields.Datetime.to_string(fields.Datetime.now())}}
        data.update(
            {
                "state": "draft",
                "last_order_preparation_change": json.dumps(change) if prep else "{}",
                "table_id": self.table.id,
                **(extra or {}),
            }
        )
        result = self.env["pos.order"].sync_from_ui([data])
        return self.env["pos.order"].browse(result["pos.order"][0]["id"])

    def _tickets(self, order, station=None):
        domain = [("order_id", "=", order.id)]
        if station:
            domain.append(("station_id", "=", station.id))
        return self.env["efn.kds.ticket"].sudo().search(domain, order="id")


@tagged("post_install", "-at_install", "efn")
class TestEfnKdsTickets(TestEfnKdsCommon):
    def test_routing_category_to_station(self):
        order = self._sync([self._line(self.p_rice, 2), self._line(self.p_coffee, 1), self._line(self.p_misc, 1)])
        kitchen = self._tickets(order, self.station_kitchen)
        bar = self._tickets(order, self.station_bar)
        self.assertEqual(len(kitchen), 1)
        self.assertEqual(len(bar), 1)
        self.assertEqual(kitchen.line_ids.product_id, self.p_rice, "sub-kategori ikut stasiun induk")
        self.assertEqual(kitchen.line_ids.qty_delta, 2)
        self.assertEqual(bar.line_ids.product_id, self.p_coffee)
        self.assertEqual(len(self._tickets(order)), 2, "baris tanpa stasiun dilewati")
        self.assertEqual(kitchen.table_label, "12 / KDS Lantai")
        self.assertEqual(kitchen.state, "new")
        self.assertEqual(kitchen.company_id, self.company)

    def test_catch_all_station_receives_everything(self):
        expo = self.env["efn.kds.station"].create(
            {"name": "Expo", "company_id": self.company.id, "config_ids": [Command.link(self.config.id)]}
        )
        order = self._sync([self._line(self.p_rice, 1), self._line(self.p_misc, 1)])
        self.assertEqual(self._tickets(order, expo).line_ids.product_id, self.p_rice | self.p_misc)
        with self.assertRaises(ValidationError):
            self.env["efn.kds.station"].create(
                {"name": "Expo 2", "company_id": self.company.id, "config_ids": [Command.link(self.config.id)]}
            )

    def test_qty_delta_add_change_remove(self):
        line_uuid = str(uuid.uuid4())
        order_uuid = self.create_random_uid()
        order = self._sync([self._line(self.p_rice, 2, line_uuid)], order_uuid=order_uuid)
        self.assertEqual(self._tickets(order).line_ids.qty_delta, 2)
        order = self._sync([self._line(self.p_rice, 3, line_uuid)], order_uuid=order_uuid)
        tickets = self._tickets(order)
        self.assertEqual(len(tickets), 2)
        self.assertEqual(tickets[1].line_ids.qty_delta, 1)
        self.assertEqual(tickets[1].sequence_no, 2)
        self.assertEqual(order.lines.efn_kds_sent_qty, 3)
        # Hapus baris (frontend: Command.delete) + preparation change tanpa baris itu.
        data = self.create_ui_order_data([], payments=[], uuid=order_uuid)
        data.update(
            {
                "state": "draft",
                "lines": [(2, order.lines.id, 0)],
                "last_order_preparation_change": json.dumps(
                    {"lines": {}, "metadata": {"serverDate": fields.Datetime.to_string(fields.Datetime.now())}}
                ),
                "table_id": self.table.id,
            }
        )
        self.env["pos.order"].sync_from_ui([data])
        tickets = self._tickets(order)
        self.assertEqual(len(tickets), 3)
        self.assertEqual(tickets[2].line_ids.qty_delta, -3)

    def test_resync_without_change_creates_nothing(self):
        lines = [self._line(self.p_rice, 1)]
        order_uuid = self.create_random_uid()
        order = self._sync(lines, order_uuid=order_uuid)
        self.assertEqual(len(self._tickets(order)), 1)
        # Sinkron ulang (metadata/serverDate berubah, isi sama) -> tanpa tiket baru.
        self._sync(lines, order_uuid=order_uuid)
        self.assertEqual(len(self._tickets(order)), 1)

    def test_line_not_sent_to_kitchen_not_ticketed(self):
        sent_line = self._line(self.p_rice, 1)
        pending = self._line(self.p_coffee, 1)
        order = self._sync([sent_line, pending], sent=[sent_line["uuid"]])
        self.assertFalse(self._tickets(order, self.station_bar))
        self.assertTrue(self._tickets(order, self.station_kitchen))

    def test_no_preparation_change_no_ticket(self):
        order = self._sync([self._line(self.p_rice, 1)], prep=False)
        self.assertFalse(self._tickets(order))

    def test_course_not_fired_then_fired(self):
        order_uuid = self.create_random_uid()
        first = self._line(self.p_rice, 1)
        second = self._line(self.p_coffee, 1)
        order = self._sync([first, second], order_uuid=order_uuid, prep=False)
        course1, course2 = self.env["restaurant.order.course"].create(
            [{"order_id": order.id, "index": 1, "fired": True}, {"order_id": order.id, "index": 2}]
        )
        order.lines.filtered(lambda line: line.uuid == first["uuid"]).course_id = course1
        order.lines.filtered(lambda line: line.uuid == second["uuid"]).course_id = course2
        self._sync([first, second], order_uuid=order_uuid)
        self.assertEqual(self._tickets(order).line_ids.product_id, self.p_rice, "course 2 belum fired")
        self.assertEqual(self._tickets(order).course_index, 1)
        # Frontend fireCourse: course.fired ikut sinkron order (tanpa perubahan preparation change).
        self._sync([first, second], order_uuid=order_uuid, extra={"course_ids": [[1, course2.id, {"fired": True}]]})
        bar = self._tickets(order, self.station_bar)
        self.assertEqual(len(bar), 1)
        self.assertEqual(bar.course_index, 2)

    def test_combo_children_split_across_stations(self):
        order_uuid = self.create_random_uid()
        parent = self._line(self.p_combo, 1)
        child_rice = self._line(self.p_rice, 1)
        child_coffee = self._line(self.p_coffee, 1)
        lines = [parent, child_rice, child_coffee]
        order = self._sync(lines, order_uuid=order_uuid, prep=False)
        parent_line = order.lines.filtered(lambda line: line.uuid == parent["uuid"])
        (order.lines - parent_line).combo_parent_id = parent_line
        self._sync(lines, order_uuid=order_uuid)
        kitchen = self._tickets(order, self.station_kitchen)
        bar = self._tickets(order, self.station_bar)
        self.assertEqual(kitchen.line_ids.product_id, self.p_rice)
        self.assertEqual(bar.line_ids.product_id, self.p_coffee)
        self.assertEqual(kitchen.line_ids.combo_parent_name, self.p_combo.display_name)
        self.assertNotIn(self.p_combo, self._tickets(order).line_ids.product_id, "induk combo tidak ditiket")

    def test_order_cancel_cancels_tickets(self):
        order = self._sync([self._line(self.p_rice, 1)])
        order.action_pos_order_cancel()
        self.assertEqual(self._tickets(order).state, "cancelled")
        self.assertTrue(self._tickets(order).cancelled_at)

    def test_printed_offline_ticket_in_progress(self):
        printer = self.env["pos.printer"].create(
            {
                "name": "KDS Printer",
                "printer_type": "epson_epos",
                "epson_printer_ip": "192.0.2.50",
                "company_id": self.company.id,
                "product_categories_ids": [Command.set(self.categ_kitchen.ids)],
            }
        )
        self.config.write({"is_order_printer": True, "printer_ids": [Command.link(printer.id)]})
        self.station_kitchen.write({"mode": "kds", "fallback_printer_id": printer.id})
        order = self._sync(
            [self._line(self.p_rice, 1), self._line(self.p_coffee, 1)], extra={"efn_kds_printed_offline": True}
        )
        kitchen = self._tickets(order, self.station_kitchen)
        bar = self._tickets(order, self.station_bar)
        self.assertTrue(kitchen.printed_offline)
        self.assertEqual(kitchen.state, "in_progress")
        self.assertFalse(bar.printed_offline, "stasiun kds_printer tidak terdampak")
        self.assertEqual(bar.state, "new")
        self.assertFalse(order.efn_kds_printed_offline)

    def test_printer_mode_station_creates_no_ticket(self):
        printer = self.env["pos.printer"].create(
            {
                "name": "KDS Printer Bar",
                "printer_type": "epson_epos",
                "epson_printer_ip": "192.0.2.51",
                "company_id": self.company.id,
            }
        )
        self.config.write({"printer_ids": [Command.link(printer.id)]})
        self.station_bar.write({"mode": "printer", "fallback_printer_id": printer.id})
        order = self._sync([self._line(self.p_coffee, 1)])
        self.assertFalse(self._tickets(order))

    def test_station_constraints(self):
        with self.assertRaises(ValidationError):
            self.station_bar.write({"mode": "kds"})  # tanpa printer cadangan
        printer = self.env["pos.printer"].create(
            {
                "name": "Tidak terpasang",
                "printer_type": "epson_epos",
                "epson_printer_ip": "192.0.2.52",
                "company_id": self.company.id,
            }
        )
        with self.assertRaises(ValidationError):
            self.station_bar.write({"mode": "kds", "fallback_printer_id": printer.id})

    def test_autovacuum_retention(self):
        order = self._sync([self._line(self.p_rice, 1)])
        ticket = self._tickets(order)
        ticket.sudo().write({"sent_at": fields.Datetime.now() - timedelta(days=91)})
        self.env["efn.kds.ticket"]._gc_efn_kds_tickets()
        self.assertFalse(ticket.exists())


@tagged("post_install", "-at_install", "efn")
class TestEfnKdsBumpAccess(TestEfnKdsCommon):
    def _order_tickets(self):
        order = self._sync([self._line(self.p_rice, 1), self._line(self.p_coffee, 1)], extra={"partner_id": False})
        return order, self._tickets(order, self.station_kitchen), self._tickets(order, self.station_bar)

    def test_bump_forward_and_recall(self):
        _order, kitchen, _bar = self._order_tickets()
        ticket = kitchen.with_user(self.device_kitchen)
        ticket.efn_kds_bump("in_progress")
        self.assertTrue(kitchen.started_at)
        ticket.efn_kds_bump("ready")
        self.assertEqual(kitchen.state, "ready")
        self.assertGreaterEqual(kitchen.wait_seconds, 0)
        self.assertTrue(all(line.state == "done" for line in kitchen.line_ids))
        with self.assertRaises(UserError):
            ticket.efn_kds_bump("new")  # mundur selain recall
        ticket.efn_kds_bump("in_progress")  # recall ready -> in_progress
        self.assertEqual(kitchen.state, "in_progress")
        ticket.efn_kds_bump("served")
        ticket.efn_kds_bump("ready")  # recall served -> ready dalam batas waktu
        ticket.efn_kds_bump("served")
        kitchen.sudo().write({"served_at": fields.Datetime.now() - timedelta(minutes=11)})
        with self.assertRaises(UserError):
            ticket.efn_kds_bump("ready")

    def test_line_done_marks_ticket_ready(self):
        _order, kitchen, _bar = self._order_tickets()
        kitchen.line_ids.with_user(self.device_kitchen).efn_kds_line_done()
        self.assertEqual(kitchen.state, "ready")

    def test_device_cannot_touch_other_station(self):
        _order, kitchen, bar = self._order_tickets()
        with self.assertRaises(AccessError):
            bar.with_user(self.device_kitchen).efn_kds_bump("ready")
        with self.assertRaises(AccessError):
            bar.line_ids.with_user(self.device_kitchen).efn_kds_line_done()
        with self.assertRaises(AccessError):
            self.station_bar.with_user(self.device_kitchen).efn_kds_load()
        visible = self.env["efn.kds.ticket"].with_user(self.device_kitchen).search([])
        self.assertEqual(visible, kitchen)
        with self.assertRaises(AccessError):
            bar.with_user(self.device_kitchen).read(["state"])
        self.assertEqual(bar.state, "new")

    def test_device_cannot_read_order_or_customer(self):
        order, kitchen, _bar = self._order_tickets()
        order.partner_id = self.customer
        with self.assertRaises(AccessError):
            kitchen.with_user(self.device_kitchen).read(["order_id"])
        with self.assertRaises(AccessError):
            order.with_user(self.device_kitchen).read(["partner_id"])
        partners = self.env["res.partner"].with_user(self.device_kitchen).search([])
        self.assertNotIn(self.customer, partners)
        self.assertFalse(self.env["res.partner"].with_user(self.device_kitchen).search([("name", "=", CUSTOMER_NAME)]))

    def test_other_company_denied(self):
        other = self.env["res.company"].create({"name": "PT Lain KDS"})
        station = self.env["efn.kds.station"].create({"name": "Dapur PT Lain", "company_id": other.id})
        station.device_user_ids = [Command.link(self.device_kitchen.id)]
        with self.assertRaises(AccessError):
            station.with_user(self.device_kitchen).efn_kds_load()

    def test_users_without_group_denied(self):
        _order, kitchen, _bar = self._order_tickets()
        for user in (self.plain_user, self.cashier):
            with self.subTest(user=user.login):
                with self.assertRaises(AccessError):
                    kitchen.with_user(user).efn_kds_bump("ready")
                with self.assertRaises(AccessError):
                    self.station_kitchen.with_user(user).efn_kds_load()
                with self.assertRaises(AccessError):
                    self.station_kitchen.with_user(user).efn_kds_heartbeat()
        with self.assertRaises(AccessError):
            self.station_kitchen.with_user(self.cashier).action_rotate_token()
        with self.assertRaises(AccessError):
            self.config.with_user(self.device_kitchen).efn_kds_station_status()

    def test_manager_can_bump_any_station(self):
        _order, _kitchen, bar = self._order_tickets()
        bar.with_user(self.env.user).efn_kds_bump("ready")  # user test = manager POS (TestPoSCommon)
        self.assertEqual(bar.state, "ready")

    def test_payload_and_bus_without_customer_data(self):
        order = self._sync([self._line(self.p_rice, 1, note="tanpa sambal")], extra={"_customer": self.customer})
        self.assertEqual(order.partner_id, self.customer)
        kitchen = self._tickets(order, self.station_kitchen)
        result = self.station_kitchen.with_user(self.device_kitchen).efn_kds_load()
        payload = result["tickets"][0]
        self.assertEqual(set(payload), PAYLOAD_KEYS)
        self.assertEqual(set(payload["lines"][0]), LINE_KEYS)
        self.assertEqual(payload["lines"][0]["note"], "tanpa sambal")
        kitchen.with_user(self.device_kitchen).efn_kds_bump("ready")
        dump = json.dumps([result, self.sent], default=str)
        for secret in (CUSTOMER_NAME, CUSTOMER_PHONE, "partner"):
            self.assertNotIn(secret, dump)
        kds_msgs = [m for _t, kind, m in self.sent if kind.endswith("-EFN_KDS")]
        self.assertTrue(kds_msgs)
        for msg in kds_msgs:
            self.assertEqual(set(msg), {"type", "ticket_ids", "ts"})
        ready = [m for _t, kind, m in self.sent if kind.endswith("-EFN_KDS_READY")]
        expected = {"order_id": order.id, "table_id": self.table.id, "ticket_id": kitchen.id, "state": "ready"}
        self.assertEqual(ready[-1], expected)

    def test_channel_uses_random_token_and_rotation(self):
        station = self.station_kitchen.sudo()
        token = station.access_token
        self.assertRegex(token, r"^[0-9a-f-]{36}$")
        self._sync([self._line(self.p_rice, 1)])
        targets = {(t, k) for t, k, _m in self.sent if k.endswith("-EFN_KDS")}
        self.assertEqual(targets, {(token, f"{token}-EFN_KDS")})
        self.station_kitchen.action_rotate_token()
        self.assertNotEqual(station.access_token, token)
        self.sent.clear()
        self._sync([self._line(self.p_rice, 1)])
        self.assertIn(f"{station.access_token}-EFN_KDS", {k for _t, k, _m in self.sent})
        self.assertNotIn(f"{token}-EFN_KDS", {k for _t, k, _m in self.sent})

    def test_token_not_readable_by_device_or_cashier(self):
        for user in (self.device_kitchen, self.cashier):
            with self.subTest(user=user.login), self.assertRaises(AccessError):
                self.station_kitchen.with_user(user).read(["access_token"])
        fields_loaded = self.env["efn.kds.station"]._load_pos_data_fields(self.config)
        self.assertNotIn("access_token", fields_loaded)
        self.assertNotIn("device_user_ids", fields_loaded)

    def test_heartbeat_and_status(self):
        status = self.config.with_user(self.cashier).efn_kds_station_status()
        self.assertFalse(any(row["is_online"] for row in status))
        res = self.station_kitchen.with_user(self.device_kitchen).efn_kds_heartbeat()
        self.assertTrue(res["token_fp"])
        self.assertNotEqual(res["token_fp"], self.station_kitchen.sudo().access_token)
        status = {row["id"]: row["is_online"] for row in self.config.with_user(self.cashier).efn_kds_station_status()}
        self.assertTrue(status[self.station_kitchen.id])
        self.assertFalse(status[self.station_bar.id])
        self.assertTrue(any(k.endswith("-EFN_KDS_STATUS") for _t, k, _m in self.sent))

    def test_device_user_cannot_be_pos_user(self):
        with self.assertRaises(ValidationError):
            self.device_kitchen.write({"group_ids": [Command.link(self.env.ref("point_of_sale.group_pos_user").id)]})
