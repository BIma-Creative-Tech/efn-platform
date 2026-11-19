"""POS tanpa browser (JS-less): data preload, RPC kandidat lot, override PIN, dan validasi server saat sync order.

Tour UI (FEFO/blokir/markdown di layar kasir) belum ditulis: lihat README "Belum terverifikasi".
"""

import json
import uuid
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged

from odoo.addons.efn_expiry_monitor.models.pos_config import _pos_loading
from odoo.addons.efn_pos_refund_origin.models.return_pin import PIN_FAIL_EVENT, hash_pin
from odoo.addons.point_of_sale.tests.common import TestPoSCommon

PIN = "135790"


@tagged("post_install", "-at_install", "efn")
class TestEfnPosExpiry(TestPoSCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("efn_expiry_monitor.enabled", "true")
        icp.set_param("efn.pos.return_pin_kdf_rounds", "1000")
        icp.set_param("efn.pos.refund.unlinked_return_mode", "cash")
        cls.config = cls.basic_config
        cls.product = cls.create_product("Susu Lot Uji", cls.categ_basic, 10000.0)
        cls.product.write({"tracking": "lot", "use_expiration_date": True})
        cls.warehouse = cls.config.picking_type_id.warehouse_id
        cls.location = cls.config.picking_type_id.default_location_src_id
        company = cls.config.company_id
        Lot = cls.env["stock.lot"]
        now = fields.Datetime.now()

        def lot(name, days):
            expiry = now + timedelta(days=days)
            return Lot.create(
                {
                    "name": name,
                    "product_id": cls.product.id,
                    "company_id": company.id,
                    "expiration_date": expiry,
                    "removal_date": expiry,
                }
            )

        cls.lot_old = lot("POS-OLD", -2)
        cls.lot_soon = lot("POS-SOON", 3)
        cls.lot_md = lot("POS-MD", 2)
        cls.lot_late = lot("POS-LATE", 20)
        for record in (cls.lot_old, cls.lot_soon, cls.lot_md, cls.lot_late):
            cls.env["stock.quant"]._update_available_quantity(cls.product, cls.location, 10, lot_id=record)
        today = fields.Date.context_today(cls.env["efn.lot.markdown"])
        cls.markdown = cls.env["efn.lot.markdown"].create(
            {
                "company_id": company.id,
                "warehouse_id": cls.warehouse.id,
                "lot_id": cls.lot_md.id,
                "discount_pct": 30,
                "date_from": today,
                "date_to": today + timedelta(days=2),
                "qty_cap": 3,
            }
        )
        cls.markdown.sudo().write({"state": "active"})
        cls.supervisor = new_test_user(
            cls.env,
            login="efn_exp_supervisor_uji",
            name="Supervisor Uji",
            groups="base.group_user,efn_expiry_monitor.group_expiry_override",
            company_id=company.id,
        )
        cls.supervisor.sudo().write({"efn_return_pin_hash": hash_pin(cls.env, PIN)})

    def setUp(self):
        super().setUp()
        self.open_new_session()

    def sync(self, lines, order_uuid=None):
        data = self.create_ui_order_data(lines, uuid=order_uuid or str(uuid.uuid4()))
        self.env["pos.order"].sync_from_ui([data])
        return self.env["pos.order"].search([("uuid", "=", data["uuid"])])

    def line(self, lot, qty=1, discount=0.0, **extra):
        return dict(
            {
                "product": self.product,
                "quantity": qty,
                "discount": discount,
                "pack_lot_ids": [[0, 0, {"lot_name": lot.name}]],
            },
            **extra,
        )

    def events(self, order, kind=None):
        domain = [("pos_order_id", "=", order.id)]
        if kind:
            domain.append(("kind", "=", kind))
        return self.env["efn.expiry.event"].search(domain)

    # ------------------------------------------------------------ data & RPC
    def payload(self):
        """Preload seperti saat pos.session.load_data (penanda cr.cache, SEC-224)."""
        self.config.invalidate_recordset(["efn_expiry_pos_data"])
        with _pos_loading(self.env.cr):
            data = json.loads(self.config.efn_expiry_pos_data)
        self.config.invalidate_recordset(["efn_expiry_pos_data"])
        return data

    def test_preload_payload_and_flag(self):
        # SEC-224: di luar load sesi, payload berat tidak dihitung.
        self.config.invalidate_recordset(["efn_expiry_pos_data"])
        lazy = json.loads(self.config.efn_expiry_pos_data)
        self.assertEqual((lazy["enabled"], lazy.get("deferred"), "lots" in lazy), (True, True, False))
        data = self.payload()
        self.assertTrue(data["enabled"])
        names = [row[0] for row in data["lots"][str(self.product.id)]]
        self.assertEqual(names, ["POS-OLD", "POS-MD", "POS-SOON", "POS-LATE"], "urut kedaluwarsa terdekat")
        self.assertEqual([md["id"] for md in data["markdowns"]], [self.markdown.id])
        self.assertEqual(data["markdowns"][0]["lot"], "POS-MD")
        self.env["ir.config_parameter"].sudo().set_param("efn_expiry_monitor.enabled", "false")
        self.assertFalse(self.payload()["enabled"])
        self.assertIn("efn_lot_source", self.env["pos.order.line"]._load_pos_data_fields(self.config))

    def test_lot_candidates_rpc(self):
        res = self.config.efn_expiry_lot_candidates(self.product.id)
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["lots"]), 4)
        self.assertEqual(res["markdowns"][0]["discount_pct"], 30)
        self.assertEqual(self.config.efn_expiry_lot_candidates(True)["error_code"], "invalid_product")
        self.assertEqual(self.config.efn_expiry_lot_candidates(self.product.id, "x" * 80)["error_code"], "invalid_lot")
        plain = new_test_user(self.env, login="efn_exp_plain_uji", groups="base.group_user",
                              company_id=self.env.company.id)
        with self.assertRaises(AccessError):
            self.config.with_user(plain).efn_expiry_lot_candidates(self.product.id)

    # ------------------------------------------------------------ markdown
    def test_markdown_cap_settled_on_sync(self):
        first = self.sync([self.line(self.lot_md, qty=2, discount=30, efn_lot_source="gs1",
                                     efn_markdown_ref=self.markdown.id)])
        self.assertEqual(first.state, "paid")
        self.assertEqual(self.markdown.qty_sold, 2)
        self.assertEqual(first.lines.efn_markdown_id, self.markdown)
        self.assertFalse(self.events(first))
        second = self.sync([self.line(self.lot_md, qty=2, discount=30, efn_lot_source="manual",
                                      efn_markdown_ref=self.markdown.id)])
        self.assertEqual(self.markdown.qty_sold, 3, "kuota 3: hanya 1 dihitung")
        cap = self.events(second, "markdown_cap_exceeded")
        self.assertEqual(cap.qty, 1)

    def test_markdown_from_fefo_or_wrong_lot_invalid(self):
        order = self.sync([self.line(self.lot_md, qty=1, discount=30, efn_lot_source="fefo",
                                     efn_markdown_ref=self.markdown.id)])
        self.assertEqual(order.state, "paid", "order lunas tidak ditolak")
        self.assertTrue(self.events(order, "markdown_invalid"))
        self.assertEqual(self.markdown.qty_sold, 0)
        other = self.sync([self.line(self.lot_late, qty=1, discount=30, efn_lot_source="gs1",
                                     efn_markdown_ref=self.markdown.id)])
        self.assertTrue(self.events(other, "markdown_invalid"), "lot tidak cocok dengan markdown")
        greedy = self.sync([self.line(self.lot_md, qty=1, discount=50, efn_lot_source="gs1",
                                      efn_markdown_ref=self.markdown.id)])
        self.assertTrue(self.events(greedy, "markdown_price_mismatch"))

    def test_markdown_refund_restores_quota(self):
        """QA G45: retur baris markdown (refunded_orderline_id, tanpa klaim markdown) memulihkan kuota; tidak < 0."""
        sold = self.sync([self.line(self.lot_md, qty=2, discount=30, efn_lot_source="gs1",
                                    efn_markdown_ref=self.markdown.id)])
        origin = sold.lines
        self.assertEqual((self.markdown.qty_sold, origin.efn_markdown_id), (2, self.markdown))
        refund_line = self.env["pos.order.line"].sudo().create(
            {
                "order_id": sold.id,
                "product_id": self.product.id,
                "qty": -1,
                "price_unit": origin.price_unit,
                "price_subtotal": -origin.price_unit,
                "price_subtotal_incl": -origin.price_unit,
                "refunded_orderline_id": origin.id,
            }
        )
        sold.sudo()._efn_expiry_after_sync()
        self.assertEqual(self.markdown.qty_sold, 1, "retur 1 dari 2 -> kuota terpakai tinggal 1")
        self.assertEqual(refund_line.efn_markdown_id, self.markdown)
        self.assertTrue(refund_line.efn_expiry_checked)
        sold.sudo()._efn_expiry_after_sync()
        self.assertEqual(self.markdown.qty_sold, 1, "idempoten: baris yang sudah dicek tidak diproses ulang")
        # Retur melebihi qty_sold (mis. kuota sebagian terhitung) tidak membuat angka negatif.
        refund_big = self.env["pos.order.line"].sudo().create(
            {
                "order_id": sold.id,
                "product_id": self.product.id,
                "qty": -5,
                "price_unit": origin.price_unit,
                "price_subtotal": -5 * origin.price_unit,
                "price_subtotal_incl": -5 * origin.price_unit,
                "refunded_orderline_id": origin.id,
            }
        )
        sold.sudo()._efn_expiry_after_sync()
        self.assertEqual((self.markdown.qty_sold, refund_big.efn_expiry_checked), (0, True))

    def test_expired_markdown_cron_then_late_sync_invalid(self):
        """QA G45: markdown lewat date_to ditandai expired oleh cron dan tidak masuk preload; order sesudahnya
        yang masih mengklaim markdown itu tercatat markdown_invalid (order tetap lunas)."""
        today = fields.Date.context_today(self.env["efn.lot.markdown"])
        stale = self.env["efn.lot.markdown"].create(
            {
                "company_id": self.config.company_id.id,
                "warehouse_id": self.warehouse.id,
                "lot_id": self.lot_late.id,
                "discount_pct": 20,
                "date_from": today - timedelta(days=3),
                "date_to": today - timedelta(days=1),
            }
        )
        stale.sudo().write({"state": "active"})
        self.assertGreaterEqual(self.env["efn.lot.markdown"]._cron_efn_expire(), 1)
        self.assertEqual(stale.state, "expired")
        self.assertNotIn(stale.id, [md["id"] for md in self.payload()["markdowns"]])
        order = self.sync([self.line(self.lot_late, qty=1, discount=20, efn_lot_source="gs1",
                                     efn_markdown_ref=stale.id)])
        self.assertEqual(order.state, "paid")
        self.assertTrue(self.events(order, "markdown_invalid"))
        self.assertEqual(stale.qty_sold, 0)

    # ------------------------------------------------------------ kedaluwarsa
    def test_expired_without_override_logged(self):
        order = self.sync([self.line(self.lot_old)])
        self.assertEqual(order.state, "paid")
        event = self.events(order)
        self.assertEqual((event.kind, event.lot_id), ("pos_expired_no_override", self.lot_old))
        self.assertFalse(self.events(self.sync([self.line(self.lot_soon)])), "lot belum kedaluwarsa: tanpa event")

    def test_supervisor_pin_override(self):
        order_ref = str(uuid.uuid4())
        approvers = self.config.efn_expiry_override_approvers()
        self.assertIn(self.supervisor.id, [row["id"] for row in approvers["approvers"]])
        bad = self.config.efn_expiry_override_request(order_ref, self.product.id, "POS-OLD", self.supervisor.id,
                                                      "000000", "lot rusak")
        self.assertEqual(bad["error_code"], "invalid_pin")
        self.assertEqual(
            self.config.efn_expiry_override_request(order_ref, self.product.id, "POS-OLD", self.supervisor.id, PIN,
                                                    "x")["error_code"],
            "invalid_input",
        )
        ok = self.config.efn_expiry_override_request(order_ref, self.product.id, "POS-OLD", self.supervisor.id, PIN,
                                                     "dijual sesuai SOP")
        self.assertTrue(ok["ok"])
        order = self.sync([self.line(self.lot_old, efn_expiry_override=True)], order_uuid=order_ref)
        event = self.events(order)
        self.assertEqual((event.kind, event.approver_id), ("pos_expired_override", self.supervisor))
        approval = self.env["efn.expiry.pos.approval"].search(
            [("order_ref", "=", order_ref), ("state", "=", "consumed")]
        )
        self.assertEqual(approval.consumed_order_id, order)
        # Klaim override tanpa persetujuan (order lain) -> tetap tercatat tanpa override sah.
        other = self.sync([self.line(self.lot_old, efn_expiry_override=True)])
        cashier_may_override = self.env.user.has_group("efn_expiry_monitor.group_expiry_override")
        self.assertEqual(
            self.events(other).kind, "pos_expired_override" if cashier_may_override else "pos_expired_no_override"
        )

    # ------------------------------------------------------------ G45 security r1
    def test_sec217_payload_user_id_not_trusted(self):
        """SEC-217: `user_id` payload = supervisor ber-grup override tidak membuat override sah; identitas = env.uid."""
        if self.env.user.has_group("efn_expiry_monitor.group_expiry_override"):
            self.skipTest("user test sudah punya grup override")
        data = self.create_ui_order_data([self.line(self.lot_old, efn_expiry_override=True)], uuid=str(uuid.uuid4()))
        data["user_id"] = self.supervisor.id
        self.env["pos.order"].sync_from_ui([data])
        order = self.env["pos.order"].search([("uuid", "=", data["uuid"])])
        event = self.events(order)
        self.assertEqual(event.kind, "pos_expired_no_override")
        self.assertEqual(event.user_id, self.env.user)

    def test_sec217_clock_skew_uses_server_time(self):
        """SEC-217: jam perangkat dimundurkan sebelum sesi -> clock_skew + lot dinilai kedaluwarsa dengan waktu server."""
        data = self.create_ui_order_data([self.line(self.lot_old)], uuid=str(uuid.uuid4()))
        data["date_order"] = fields.Datetime.to_string(fields.Datetime.now() - timedelta(days=10))
        self.env["pos.order"].sync_from_ui([data])
        order = self.env["pos.order"].search([("uuid", "=", data["uuid"])])
        self.assertEqual(order.state, "paid")
        self.assertEqual(sorted(self.events(order).mapped("kind")), ["clock_skew", "pos_expired_no_override"])

    def test_sec222_percent_markdown_lowered_price_unit(self):
        """SEC-222: diskon persen sah tetapi price_unit diturunkan -> markdown_price_mismatch."""
        order = self.sync([self.line(self.lot_md, qty=1)])
        self.assertFalse(self.events(order))
        # Baris seolah dikirim POS dengan price_unit diturunkan + diskon 30 % sah, lalu dinilai ulang server.
        order.lines.sudo().write(
            {
                "price_unit": 5000.0,
                "discount": 30.0,
                "efn_markdown_ref": self.markdown.id,
                "efn_lot_source": "gs1",
                "efn_expiry_checked": False,
            }
        )
        order.sudo()._efn_expiry_after_sync()
        self.assertTrue(self.events(order, "markdown_price_mismatch"))

    def test_sec222_refund_ignores_claim_without_origin(self):
        """SEC-222: baris retur dengan klaim markdown tetapi tanpa baris asal markdown tidak membuka kuota."""
        self.markdown.sudo().write({"qty_sold": 2})
        sold = self.sync([self.line(self.lot_late, qty=1)])
        refund_line = self.env["pos.order.line"].sudo().create(
            {
                "order_id": sold.id,
                "product_id": self.product.id,
                "qty": -1,
                "price_unit": sold.lines.price_unit,
                "price_subtotal": -sold.lines.price_unit,
                "price_subtotal_incl": -sold.lines.price_unit,
                "refunded_orderline_id": sold.lines.id,
                "efn_markdown_ref": self.markdown.id,
                "efn_lot_source": "gs1",
            }
        )
        sold.sudo()._efn_expiry_after_sync()
        self.assertEqual(self.markdown.qty_sold, 2)
        self.assertFalse(refund_line.efn_markdown_id)

    def test_sec216_shared_pin_lockout_and_structured_log(self):
        """SEC-216: kegagalan PIN override kedaluwarsa dihitung bersama retur tanpa struk E-41."""
        Return = self.env["efn.pos.return.approval"].sudo()
        base = {
            "company_id": self.config.company_id.id,
            "config_id": self.config.id,
            "session_id": self.config.current_session_id.id,
            "order_ref": "ret-00000001",
            "cashier_user_id": self.env.uid,
            "approver_user_id": self.supervisor.id,
            "state": "failed",
            "failure_reason": "wrong_pin",
        }
        Return.create([base] * 3)
        order_ref = str(uuid.uuid4())
        with self.assertLogs("odoo.addons.efn_expiry_monitor.models.pos_config", "WARNING") as captured:
            first = self.config.efn_expiry_override_request(
                order_ref, self.product.id, "POS-OLD", self.supervisor.id, "000000", "lot rusak"
            )
            second = self.config.efn_expiry_override_request(
                order_ref, self.product.id, "POS-OLD", self.supervisor.id, "000000", "lot rusak"
            )
        self.assertEqual(first["error_code"], "invalid_pin")
        self.assertEqual(second["error_code"], "locked", "3 gagal retur + 2 gagal override = 5 -> terkunci")
        self.assertTrue(all(PIN_FAIL_EVENT in line for line in captured.output))
        self.assertFalse(any("000000" in line for line in captured.output), "PIN tidak pernah di-log")
        # PIN benar pun ditolak saat terkunci (kunci bersama).
        locked = self.config.efn_expiry_override_request(
            order_ref, self.product.id, "POS-OLD", self.supervisor.id, PIN, "lot rusak"
        )
        self.assertEqual(locked["error_code"], "locked")

    def test_sec216_approvers_scoped_to_outlet(self):
        other_wh = self.env["stock.warehouse"].create(
            {"name": "Outlet lain uji", "code": "EXPO2", "company_id": self.config.company_id.id}
        )
        self.supervisor.sudo().efn_outlet_warehouse_ids = [(6, 0, other_wh.ids)]
        ids = [row["id"] for row in self.config.efn_expiry_override_approvers()["approvers"]]
        self.assertNotIn(self.supervisor.id, ids, "supervisor outlet lain tidak ditampilkan")
        self.supervisor.sudo().efn_outlet_warehouse_ids = [(4, self.warehouse.id)]
        ids = [row["id"] for row in self.config.efn_expiry_override_approvers()["approvers"]]
        self.assertIn(self.supervisor.id, ids)

    def test_sec224_pos_approval_autovacuum(self):
        Approval = self.env["efn.expiry.pos.approval"].sudo()
        old = Approval.create(
            {
                "company_id": self.config.company_id.id,
                "config_id": self.config.id,
                "session_id": self.config.current_session_id.id,
                "order_ref": "old-0000001",
                "product_id": self.product.id,
                "lot_name": "POS-OLD",
                "cashier_user_id": self.env.uid,
                "approver_user_id": self.supervisor.id,
                "state": "failed",
                "failure_reason": "wrong_pin",
            }
        )
        self.env.cr.execute(
            "UPDATE efn_expiry_pos_approval SET create_date = now() - interval '800 days' WHERE id = %s", [old.id]
        )
        old.invalidate_recordset()
        Approval._gc_efn_expiry_pos_approvals()
        self.assertFalse(old.exists())
