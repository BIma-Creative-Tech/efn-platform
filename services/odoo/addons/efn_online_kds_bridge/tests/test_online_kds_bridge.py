"""E-29a (docs/04 §14.B B-6/B-7, docs/08 §13.2): tiket KDS dari pesanan online F&B.

BFF tidak dipanggil (event hanya diantrikan di outbox). Angka = data uji (ADR-027).
"""

import json
import uuid
from unittest.mock import patch

import psycopg2

from odoo import Command, fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import new_test_user, tagged
from odoo.tools import mute_logger

from odoo.addons.efn_online_kds_bridge.hooks import uninstall_hook
from odoo.addons.point_of_sale.tests.common import TestPoSCommon

PICKUP_CODE = "ABCD2345"
OUTLET = "KAFE-K1"
KDS_PAYLOAD_KEYS = {
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


@tagged("post_install", "-at_install", "efn")
class TestEfnOnlineKdsBridge(TestPoSCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env(su=True)
        cls.config = cls.basic_config
        company = cls.company = cls.config.company_id
        cls.warehouse = cls.config.picking_type_id.warehouse_id
        params = env["ir.config_parameter"]
        params.set_param("efn_online.pickup_code_kdf_rounds", "1000")
        env["loyalty.program"].with_context(active_test=False).search([]).write({"sale_ok": False})
        Account = env["account.account"].with_company(company)
        income = Account.search([("company_ids", "in", company.ids), ("account_type", "=", "income")], limit=1)
        titipan = env["account.journal"].create(
            {"name": "Titipan Online KDS", "type": "bank", "code": "TKDS", "company_id": company.id}
        )
        pricelist = env["product.pricelist"].create(
            {"name": "Online KDS", "currency_id": company.currency_id.id, "company_id": company.id}
        )
        partner = env["res.partner"].create({"name": "Pelanggan Super-App KDS", "company_id": company.id})
        cls.tech = new_test_user(
            env,
            login="efn_online_kds_tech",
            groups="base.group_user,efn_online_order.group_online_channel",
            company_id=company.id,
            company_ids=[Command.set(company.ids)],
        )
        cls.channel = env["efn.online.channel"].create(
            {
                "name": "Kanal KDS",
                "code": "TKD",
                "company_id": company.id,
                "pricelist_id": pricelist.id,
                "partner_id": partner.id,
                "journal_titipan_id": titipan.id,
                "stock_check": "none",
                "user_ids": [Command.set(cls.tech.ids)],
                "outlet_ids": [
                    Command.create({"code": OUTLET, "public_name": "Kafe Uji", "warehouse_id": cls.warehouse.id})
                ],
            }
        )
        cls.outlet = cls.channel.outlet_ids
        cls.categ_kitchen = env["pos.category"].create({"name": "KDS Online Dapur"})
        cls.categ_bar = env["pos.category"].create({"name": "KDS Online Bar"})
        cls.sugar = env["product.attribute"].create(
            {
                "name": "Gula KDS uji",
                "create_variant": "no_variant",
                "display_type": "radio",
                "value_ids": [Command.create({"name": "Normal"}), Command.create({"name": "Tanpa gula"})],
            }
        )
        Template = env["product.template"].with_company(company)

        def menu(name, categ, attributes=None):
            return Template.create(
                {
                    "name": name,
                    "type": "consu",
                    "list_price": 25000.0,
                    "taxes_id": [Command.clear()],
                    "company_id": company.id,
                    "available_in_pos": True,
                    "pos_categ_ids": [Command.set(categ.ids)],
                    "property_account_income_id": income.id,
                    "efn_online_channel_ids": [Command.set(cls.channel.ids)],
                    "attribute_line_ids": [
                        Command.create({"attribute_id": attr.id, "value_ids": [Command.set(attr.value_ids.ids)]})
                        for attr in (attributes or [])
                    ],
                }
            ).product_variant_id

        cls.p_rice = menu("Nasi Goreng Online", cls.categ_kitchen)
        cls.p_coffee = menu("Es Kopi Online", cls.categ_bar, [cls.sugar])
        cls.v_less = cls.p_coffee.product_tmpl_id.attribute_line_ids.product_template_value_ids.filtered(
            lambda v: v.name == "Tanpa gula"
        )
        Station = env["efn.kds.station"]
        cls.station_kitchen = Station.create(
            {
                "name": "Dapur Online",
                "company_id": company.id,
                "config_ids": [Command.link(cls.config.id)],
                "category_ids": [Command.link(cls.categ_kitchen.id)],
            }
        )
        cls.station_bar = Station.create(
            {
                "name": "Bar Online",
                "company_id": company.id,
                "config_ids": [Command.link(cls.config.id)],
                "category_ids": [Command.link(cls.categ_bar.id)],
            }
        )
        cls.device = new_test_user(
            env,
            login="efn_kds_online_device",
            groups="base.group_user,efn_pos_kds.group_kds_device",
            company_id=company.id,
            company_ids=[Command.set(company.ids)],
        )
        (cls.station_kitchen | cls.station_bar).write({"device_user_ids": [Command.link(cls.device.id)]})
        cls.stock_user = new_test_user(
            env,
            login="efn_kds_online_stock",
            groups="base.group_user,stock.group_stock_user",
            company_id=company.id,
            company_ids=[Command.set(company.ids)],
        )

    # ------------------------------------------------------------------ helpers
    def _place(self, lines=None):
        lines = lines or [
            {"product_id": self.p_rice.id, "qty": 2},
            {"product_id": self.p_coffee.id, "qty": 1, "attribute_value_ids": [self.v_less.id]},
        ]
        quote = self.env["efn.online.channel"].with_user(self.tech).efn_online_quote("TKD", OUTLET, "pickup", lines)
        part = str(uuid.uuid4())
        self.env["efn.online.order"].with_user(self.tech).efn_online_place(
            "TKD",
            {
                "idempotency_key": f"online:place:{part}",
                "platform_order_ref": part,
                "checkout_ref": str(uuid.uuid4()),
                "outlet_code": OUTLET,
                "fulfilment": "pickup",
                "lines": lines,
                "quote_hash": quote["quote_hash"],
                "pickup_code": PICKUP_CODE,
            },
        )
        return self.env["efn.online.order"].sudo().search([("platform_order_ref", "=", part)])

    def _pay(self, order):
        ref = str(uuid.uuid4())
        return (
            self.env["efn.online.order"]
            .with_user(self.tech)
            .efn_online_mark_paid(
                "TKD",
                order.platform_order_ref,
                {
                    "payment_ref": ref,
                    "amount_paid": int(order.amount_due),
                    "paid_at": fields.Datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "idempotency_key": f"online:paid:{order.platform_order_ref}:{ref}",
                },
            )
        )

    def _tickets(self, order):
        return self.env["efn.kds.ticket"].sudo().search([("efn_online_order_id", "=", order.id)], order="id")

    def _paid_order(self):
        order = self._place()
        self.assertEqual(self._pay(order), {"order_state": "confirmed"})
        return order

    # -------------------------------------------------------------------- test
    def test_tickets_created_after_payment_per_station_without_pii(self):
        order = self._place()
        self.assertFalse(self._tickets(order), "belum dibayar -> tanpa tiket")
        self._pay(order)
        tickets = self._tickets(order)
        self.assertEqual(tickets.station_id, self.station_kitchen | self.station_bar)
        for ticket in tickets:
            self.assertFalse(ticket.order_id)
            self.assertEqual(ticket.state, "new")
            self.assertEqual(ticket.tracking_number, order.sale_order_id.name[-12:])
            self.assertEqual(ticket.sequence_no, 1)
        kitchen = tickets.filtered(lambda t: t.station_id == self.station_kitchen)
        bar = tickets.filtered(lambda t: t.station_id == self.station_bar)
        self.assertEqual((kitchen.line_ids.product_id, kitchen.line_ids.qty_delta), (self.p_rice, 2.0))
        self.assertEqual(bar.line_ids.attribute_text, "Tanpa gula")
        payload = bar._efn_kds_payload()
        self.assertEqual(set(payload), KDS_PAYLOAD_KEYS, "payload layar KDS tidak berubah")
        raw = json.dumps([t._efn_kds_payload() for t in tickets])
        self.assertNotIn(PICKUP_CODE, raw)
        self.assertNotIn("Pelanggan Super-App KDS", raw)
        self.assertNotIn(order.platform_order_ref, raw)
        # Idempoten: pemanggilan ulang tidak membuat tiket kedua.
        self.assertFalse(order._efn_kds_create_tickets())
        self.assertEqual(self._tickets(order), tickets)

    def test_kitchen_flow_preparing_then_ready_kds(self):
        order = self._paid_order()
        tickets = self._tickets(order)
        bar = tickets.filtered(lambda t: t.station_id == self.station_bar)
        kitchen = tickets - bar
        bar.with_user(self.device).efn_kds_bump("in_progress")
        self.assertTrue(order.kitchen_started_at)
        self.assertEqual(order.state, "confirmed")
        preparing = order.event_ids.filtered(lambda e: e.event == "preparing")
        self.assertEqual(len(preparing), 1)
        self.assertTrue(preparing._efn_payload()["kitchen_started_at"].endswith("Z"))
        bar.with_user(self.device).efn_kds_bump("ready")
        self.assertEqual(order.state, "confirmed", "stasiun lain belum siap")
        kitchen.line_ids.with_user(self.device).efn_kds_line_done()
        self.assertEqual((order.state, order.ready_source), ("ready", "kds"))
        ready = order.event_ids.filtered(lambda e: e.event == "ready")
        self.assertEqual(ready._efn_payload()["ready_source"], "kds")
        self.assertEqual(len(order.event_ids.filtered(lambda e: e.event == "preparing")), 1, "preparing sekali")
        self.assertEqual(order.event_ids.sorted("seq").mapped("event")[-2:], ["preparing", "ready"])
        # Recall setelah siap tidak mengubah pesanan.
        kitchen.with_user(self.device).efn_kds_bump("in_progress")
        self.assertEqual(order.state, "ready")

    def test_manual_ready_still_possible_and_kds_noop_after(self):
        order = self._paid_order()
        order.with_user(self.stock_user).action_efn_ready()
        self.assertEqual(order.ready_source, "manual")
        for ticket in self._tickets(order):
            ticket.with_user(self.device).efn_kds_bump("ready")
        self.assertEqual(len(order.event_ids.filtered(lambda e: e.event == "ready")), 1)

    def test_cancel_and_full_refund_cancel_tickets(self):
        order = self._paid_order()
        self.env["efn.online.order"].with_user(self.tech).efn_online_cancel(
            "TKD", order.platform_order_ref, "customer", f"online:cancel:{order.platform_order_ref}"
        )
        self.assertEqual(set(self._tickets(order).mapped("state")), {"cancelled"})

    def test_partial_refund_creates_reduction_ticket(self):
        order = self._paid_order()
        self.env["efn.online.order"].with_user(self.tech).efn_online_refund(
            "TKD",
            order.platform_order_ref,
            {
                "idempotency_key": f"online:refund:{uuid.uuid4()}",
                "lines": [{"product_id": self.p_rice.id, "qty": 1}],
                "reason": "customer_cs",
            },
        )
        kitchen = self._tickets(order).filtered(lambda t: t.station_id == self.station_kitchen)
        self.assertEqual(kitchen.mapped("sequence_no"), [1, 2])
        self.assertEqual(kitchen[1].line_ids.qty_delta, -1.0)
        bar = self._tickets(order).filtered(lambda t: t.station_id == self.station_bar)
        self.assertEqual(len(bar), 1, "produk lain tidak tersentuh")

    def test_no_station_for_other_warehouse_and_override(self):
        warehouse = (
            self.env["stock.warehouse"]
            .sudo()
            .create({"name": "Gudang KDS Lain", "code": "WKDS", "company_id": self.company.id})
        )
        self.outlet.sudo().write({"warehouse_id": warehouse.id})
        order = self._paid_order()
        self.assertFalse(self._tickets(order), "gudang outlet tanpa POS ber-KDS")
        self.outlet.sudo().write({"kds_config_id": self.config.id})
        order2 = self._paid_order()
        self.assertEqual(len(self._tickets(order2)), 2, "override POS dapur")

    def test_ticket_failure_does_not_block_payment(self):
        Order = type(self.env["efn.online.order"])
        with (
            patch.object(Order, "_efn_kds_create_tickets", side_effect=ValueError("boom")),
            self.assertLogs("odoo.addons.efn_pos_kds.alert", level="ERROR") as logs,
        ):
            order = self._place()
            self.assertEqual(self._pay(order), {"order_state": "confirmed"})
        self.assertIn("EFN_KDS_TICKET_FAILED", "\n".join(logs.output))
        self.assertNotIn(order.platform_order_ref, "\n".join(logs.output))
        self.assertFalse(self._tickets(order))

    def test_single_source_and_unique_constraints(self):
        order = self._paid_order()
        Ticket = self.env["efn.kds.ticket"].sudo()
        base = {"company_id": self.company.id, "station_id": self.station_kitchen.id, "sequence_no": 99}
        with mute_logger("odoo.sql_db"), self.assertRaises(psycopg2.IntegrityError), self.cr.savepoint():
            Ticket.create(dict(base))
            Ticket.flush_model()
        with mute_logger("odoo.sql_db"), self.assertRaises(psycopg2.IntegrityError), self.cr.savepoint():
            Ticket.create(dict(base, efn_online_order_id=order.id, sequence_no=1))
            Ticket.flush_model()

    def test_device_cannot_read_online_order_through_ticket(self):
        order = self._paid_order()
        ticket = self._tickets(order)[:1]
        with self.assertRaises(AccessError):
            ticket.with_user(self.device).read(["efn_online_order_id"])
        with self.assertRaises(AccessError):
            order.with_user(self.device).read(["name"])
        self.assertTrue(ticket.with_user(self.device).read(["tracking_number"]))

    def test_outlet_fiscal_position_not_dine_in(self):
        Preset = self.env["pos.preset"] if "pos.preset" in self.env else None
        if Preset is None or "service_at" not in Preset._fields:
            self.skipTest("pos.preset.service_at (pos_self_order) tidak tersedia")
        fp_dine_in = self.env["account.fiscal.position"].create({"name": "Dine-in uji", "company_id": self.company.id})
        fp_takeaway = self.env["account.fiscal.position"].create({"name": "Ambil uji", "company_id": self.company.id})
        Preset.create({"name": "Makan di tempat uji", "fiscal_position_id": fp_dine_in.id, "service_at": "table"})
        self.outlet.write({"fiscal_position_id": fp_takeaway.id})
        self.channel.write({"require_outlet_fiscal_position": True})
        with self.assertRaises(ValidationError):
            self.outlet.write({"fiscal_position_id": fp_dine_in.id})

    def test_uninstall_hook_removes_online_tickets_only(self):
        order = self._paid_order()
        before = self.env["efn.kds.ticket"].sudo().search_count([("order_id", "!=", False)])
        self.env.flush_all()
        uninstall_hook(self.env)
        self.env.invalidate_all()
        self.assertFalse(self._tickets(order))
        self.assertEqual(self.env["efn.kds.ticket"].sudo().search_count([("order_id", "!=", False)]), before)
