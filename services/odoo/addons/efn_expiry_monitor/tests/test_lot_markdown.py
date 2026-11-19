"""efn.lot.markdown: XOR harga/diskon, batas diskon per peran (Q-SM4), kunci setelah draft, cakupan outlet, audit."""

from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import new_test_user, tagged

from .common import EfnExpiryCommon


@tagged("post_install", "-at_install", "efn")
class TestEfnLotMarkdown(EfnExpiryCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.lot = cls.make_lot("MD-LOT-1", 2)
        cls.lot_b = cls.make_lot("MD-LOT-B", 2)
        cls.put(cls.lot, cls.outlet_a, 10)
        cls.put(cls.lot_b, cls.outlet_b, 10)

    def vals(self, **extra):
        today = fields.Date.context_today(self.env["efn.lot.markdown"])
        vals = {
            "company_id": self.company.id,
            "warehouse_id": self.outlet_a.id,
            "lot_id": self.lot.id,
            "date_from": today,
            "date_to": today + timedelta(days=2),
            "qty_cap": 5,
        }
        vals.update(extra)
        return vals

    def test_value_constraints(self):
        Markdown = self.env["efn.lot.markdown"]
        for bad in (
            {},  # tanpa harga & diskon
            {"price": 15000, "discount_pct": 10},  # keduanya
            {"price": 25000},  # di atas harga normal
            {"discount_pct": 100},
            {"discount_pct": 10, "qty_cap": -1},
            {"discount_pct": 10, "warehouse_id": self.dc.id},  # DC bukan outlet
        ):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                Markdown.create(self.vals(**bad))
        md = Markdown.create(self.vals(price=15000))
        self.assertAlmostEqual(md.effective_discount_pct, 25.0)
        with self.assertRaisesRegex(ValidationError, "overlap"):
            Markdown.create(self.vals(discount_pct=10))

    def test_role_limits_store_head_and_manager(self):
        Markdown = self.env["efn.lot.markdown"]
        md40 = Markdown.with_user(self.store_head).create(self.vals(discount_pct=40))
        md40.action_efn_activate()
        self.assertEqual((md40.state, md40.activated_by), ("active", self.store_head))
        md40.action_efn_cancel()
        md60 = Markdown.with_user(self.store_head).create(self.vals(discount_pct=60))
        with self.assertRaisesRegex(UserError, "discount_limit"):
            md60.action_efn_activate()
        # Markdown Manager (tanpa outlet terdaftar) melihat & mengaktifkan di semua outlet PT sampai 90 %.
        md60.with_user(self.manager).action_efn_activate()
        self.assertEqual(md60.state, "active")
        md60.with_user(self.manager).action_efn_cancel()
        md95 = Markdown.with_user(self.manager).create(self.vals(discount_pct=95))
        with self.assertRaisesRegex(UserError, "discount_limit"):
            md95.action_efn_activate()
        # Batas configurable.
        self.env["ir.config_parameter"].sudo().set_param("efn_expiry_monitor.max_discount_markdown_manager", "96")
        md95.with_user(self.manager).action_efn_activate()
        self.assertEqual(md95.state, "active")

    def test_outlet_scope_and_server_fields(self):
        Markdown = self.env["efn.lot.markdown"]
        other = Markdown.create(self.vals(warehouse_id=self.outlet_b.id, lot_id=self.lot_b.id, discount_pct=20))
        self.assertFalse(Markdown.with_user(self.store_head).search([("id", "=", other.id)]), "outlet lain tersembunyi")
        with self.assertRaises(AccessError):
            other.with_user(self.store_head).action_efn_activate()
        with self.assertRaises(AccessError):
            other.with_user(self.clerk).action_efn_activate()
        with self.assertRaises(AccessError):
            Markdown.with_user(self.store_head).create(self.vals(discount_pct=20, state="active"))
        md = Markdown.with_user(self.store_head).create(self.vals(discount_pct=20))
        with self.assertRaises(AccessError):
            md.with_user(self.store_head).write({"qty_sold": 3})
        md.action_efn_activate()
        with self.assertRaisesRegex(UserError, "markdown_locked"):
            md.with_user(self.store_head).write({"discount_pct": 45})
        with self.assertRaisesRegex(UserError, "markdown_unlink"):
            md.with_user(self.store_head).unlink()

    def test_sec218_override_supervisor_reads_all_writes_own_outlet_only(self):
        """SEC-218: supervisor override yang juga stock user membaca semua outlet tetapi tidak menulis outlet lain."""
        supervisor = new_test_user(
            self.env,
            login="efn_exp_sup_ops_uji",
            groups="stock.group_stock_user,efn_expiry_monitor.group_expiry_override",
            company_id=self.company.id,
        )
        supervisor.sudo().efn_outlet_warehouse_ids = [(6, 0, self.outlet_a.ids)]
        other = self.env["efn.lot.markdown"].create(
            self.vals(warehouse_id=self.outlet_b.id, lot_id=self.lot_b.id, discount_pct=20)
        )
        self.assertTrue(other.with_user(supervisor).read(["discount_pct"]), "baca semua outlet")
        with self.assertRaises(AccessError):
            other.with_user(supervisor).write({"discount_pct": 25})
        with self.assertRaises(AccessError):
            other.with_user(supervisor).unlink()
        # Outlet sendiri tetap bisa ditulis (cakupan stock user).
        own = self.env["efn.lot.markdown"].with_user(supervisor).create(self.vals(discount_pct=10))
        own.write({"note": "ok"})

    def test_notify_members_pending_and_audit(self):
        self.env.registry.clear_cache()
        md = self.env["efn.lot.markdown"].with_user(self.store_head).create(
            self.vals(discount_pct=20, notify_members=True)
        )
        self.assertEqual(md.notify_state, "pending", "G45 hanya menyimpan; outbox G46")
        md.action_efn_activate()
        logs = self.env["efn.audit.log"].sudo().search(
            [("model_name", "=", "efn.lot.markdown"), ("res_id", "=", md.id)]
        )
        self.assertIn("create", logs.mapped("operation"))
        self.assertIn("write", logs.mapped("operation"), "aktivasi (state via sudo) teraudit")

    def test_cron_expires_and_alert_actioned(self):
        self.env["efn.expiry.alert"]._cron_efn_expiry_scan()
        alert = self.env["efn.expiry.alert"].search([("lot_id", "=", self.lot.id)])
        self.assertEqual(alert.stage, "markdown")
        action = alert.with_user(self.store_head).action_efn_create_markdown()
        self.assertEqual(action["context"]["default_lot_id"], self.lot.id)
        md = self.env["efn.lot.markdown"].with_user(self.store_head).with_context(action["context"]).create(
            {"discount_pct": 30}
        )
        self.assertEqual((md.alert_id, md.warehouse_id, md.qty_cap), (alert, self.outlet_a, 10))
        md.action_efn_activate()
        self.assertEqual(alert.state, "actioned")
        md.sudo().write({"date_from": md.date_from - timedelta(days=5), "date_to": md.date_from - timedelta(days=1)})
        self.assertGreaterEqual(self.env["efn.lot.markdown"]._cron_efn_expire(), 1)
        self.assertEqual(md.state, "expired")
