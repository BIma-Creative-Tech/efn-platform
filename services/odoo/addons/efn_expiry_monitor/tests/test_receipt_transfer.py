"""Cek sisa umur saat outlet menerima dari DC (S-10) + usulan transfer antar outlet (S-14)."""

import uuid
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import EfnExpiryCommon


@tagged("post_install", "-at_install", "efn")
class TestEfnExpiryReceipt(EfnExpiryCommon):
    def dc_transfer(self, lot, qty=2):
        picking = self.env["stock.picking"].with_company(self.company).create(
            {
                "picking_type_id": self.dc.int_type_id.id,
                "location_id": self.dc.lot_stock_id.id,
                "location_dest_id": self.outlet_a.lot_stock_id.id,
                "company_id": self.company.id,
                "move_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product.id,
                            "company_id": self.company.id,
                            "product_uom_qty": qty,
                            "location_id": self.dc.lot_stock_id.id,
                            "location_dest_id": self.outlet_a.lot_stock_id.id,
                        },
                    )
                ],
            }
        )
        self.put(lot, self.dc, qty)
        picking.action_confirm()
        picking.action_assign()
        picking.move_ids.picked = True
        return picking

    def test_short_life_blocked_then_override(self):
        short = self.make_lot("RCV-SHORT", 3)  # min 5 hari (diwarisi dari kategori induk)
        picking = self.dc_transfer(short)
        self.assertEqual(picking.move_line_ids.lot_id, short)
        self.assertIn("RCV-SHORT", picking.efn_short_life_warning or "")
        with self.assertRaisesRegex(UserError, "efn_expiry:short_life"):
            picking.button_validate()
        with self.assertRaises(AccessError):
            picking.with_user(self.clerk).action_efn_expiry_receipt_override()
        with self.assertRaises(AccessError):
            picking.with_user(self.clerk).write({"efn_short_life_override_id": False})
        Wizard = self.env["efn.expiry.receipt.override.wizard"]
        clerk_wizard = Wizard.with_user(self.clerk).create(
            {"picking_id": picking.id, "reason": "alasan cukup panjang"}
        )
        with self.assertRaises(AccessError):
            clerk_wizard.action_confirm()
        with self.assertRaisesRegex(UserError, "reason"):
            Wizard.with_user(self.manager).create({"picking_id": picking.id, "reason": "pendek"}).action_confirm()
        Wizard.with_user(self.manager).create(
            {"picking_id": picking.id, "reason": "Stok outlet kosong, disetujui category manager"}
        ).action_confirm()
        event = picking.efn_short_life_override_id
        self.assertEqual((event.kind, event.user_id, event.lot_id), ("receipt_override", self.manager, short))
        picking.button_validate()
        self.assertEqual(picking.state, "done")
        with self.assertRaises(UserError):
            event.with_user(self.manager).write({"reason": "ubah"})

    def test_sec220_override_bound_to_evaluated_lots(self):
        """SEC-220: lot yang diganti/ditambah setelah override tetap diblokir."""
        lot_x = self.make_lot("RCV-X", 3)
        picking = self.dc_transfer(lot_x)
        self.env["efn.expiry.receipt.override.wizard"].with_user(self.manager).create(
            {"picking_id": picking.id, "reason": "Stok outlet kosong, disetujui category manager"}
        ).action_confirm()
        self.assertEqual(picking.efn_short_life_override_id.lot_ids, lot_x)
        self.assertFalse(picking.efn_short_life_warning)
        lot_y = self.make_lot("RCV-Y", 1)
        self.put(lot_y, self.dc, 2)
        picking.move_line_ids.write({"lot_id": lot_y.id})
        picking.invalidate_recordset(["efn_short_life_warning"])
        self.assertIn("RCV-Y", picking.efn_short_life_warning or "")
        with self.assertRaisesRegex(UserError, "efn_expiry:short_life"):
            picking.button_validate()

    def test_long_life_and_flag_off(self):
        picking = self.dc_transfer(self.make_lot("RCV-LONG", 30))
        self.assertFalse(picking.efn_short_life_warning)
        picking.button_validate()
        self.assertEqual(picking.state, "done")
        self.env["ir.config_parameter"].sudo().set_param("efn_expiry_monitor.enabled", "false")
        picking = self.dc_transfer(self.make_lot("RCV-SHORT-OFF", 1))
        picking.button_validate()
        self.assertEqual(picking.state, "done")


@tagged("post_install", "-at_install", "efn")
class TestEfnExpiryTransfer(EfnExpiryCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.lot = cls.make_lot("TRF-NEAR", 8)
        cls.put(cls.lot, cls.outlet_a, 6)
        cls.env["efn.expiry.alert"]._cron_efn_expiry_scan()
        cls.alert = cls.env["efn.expiry.alert"].search([("lot_id", "=", cls.lot.id)])

    def rates(self, mapping):
        Wizard = type(self.env["efn.expiry.transfer.wizard"])

        def fake(_self, warehouse, product):
            return mapping.get(warehouse.id, 0.0), "history"

        return patch.object(Wizard, "_efn_sales_rate", autospec=True, side_effect=fake)

    def test_suggests_highest_rate_allowed_outlet(self):
        self.assertEqual(self.alert.stage, "near")
        # Outlet B laku paling tinggi tetapi assortment menolak -> C.
        self.outlet_b.efn_assortment_policy = "closed"
        with self.rates({self.outlet_a.id: 1.0, self.outlet_b.id: 9.0, self.outlet_c.id: 4.0}):
            wizard = self.env["efn.expiry.transfer.wizard"].with_user(self.store_head).create(
                {"alert_id": self.alert.id}
            )
            self.assertEqual(wizard.dest_warehouse_id, self.outlet_c)
            self.assertEqual(wizard.qty, 6)
            self.assertIn("not_listed", wizard.candidate_info)
            # Paksa B -> ditolak assortment.
            wizard.dest_warehouse_id = self.outlet_b
            with self.assertRaisesRegex(UserError, "transfer_assortment"):
                wizard.action_confirm()
            wizard.write({"dest_warehouse_id": self.outlet_c.id, "qty": 4})
            action = wizard.action_confirm()
        picking = self.env["stock.picking"].browse(action["res_id"])
        self.assertEqual(picking.state, "draft")
        self.assertEqual((picking.location_id, picking.location_dest_id),
                         (self.outlet_a.lot_stock_id, self.outlet_c.lot_stock_id))
        self.assertEqual(picking.move_ids.product_uom_qty, 4)
        if "restrict_lot_id" in picking.move_ids._fields:
            self.assertEqual(picking.move_ids.restrict_lot_id, self.lot)
        self.assertEqual((self.alert.state, self.alert.transfer_picking_id), ("actioned", picking))

    def test_no_better_outlet_and_qty_bounds(self):
        with self.rates({self.outlet_a.id: 5.0, self.outlet_b.id: 1.0, self.outlet_c.id: 2.0}):
            wizard = self.env["efn.expiry.transfer.wizard"].create({"alert_id": self.alert.id})
            self.assertFalse(wizard.dest_warehouse_id, "tidak ada outlet dengan laju lebih tinggi")
            with self.assertRaisesRegex(UserError, "transfer_dest"):
                wizard.action_confirm()
            wizard.write({"dest_warehouse_id": self.outlet_b.id, "qty": 7})
            with self.assertRaisesRegex(UserError, "transfer_qty"):
                wizard.action_confirm()
            wizard.dest_warehouse_id = self.dc
            wizard.qty = 1
            with self.assertRaisesRegex(UserError, "transfer_dest"):
                wizard.action_confirm()

    def test_sales_rate_from_forecast_then_history(self):
        if "efn.forecast.daily" not in self.env:
            self.skipTest("efn_demand_forecast tidak terpasang")
        Wizard = self.env["efn.expiry.transfer.wizard"]
        self.assertEqual(Wizard._efn_sales_rate(self.outlet_b, self.product), (0.0, "history"))
        run = self.env["efn.forecast.run"].create(
            {
                "run_id": str(uuid.uuid4()),
                "company_id": self.company.id,
                "kind": "daily",
                "chunk_total": 1,
                "horizon_days": 28,
                "state": "ok",
                "is_active": True,
                "closed_at": fields.Datetime.now(),
            }
        )
        today = fields.Date.context_today(Wizard)
        self.env["efn.forecast.daily"].create(
            [
                {
                    "run_id": run.id,
                    "company_id": self.company.id,
                    "warehouse_id": self.outlet_b.id,
                    "product_id": self.product.id,
                    "date": today + timedelta(days=offset),
                    "yhat": 3.0,
                    "p10": 2.0,
                    "p90": 4.0,
                    "sigma": 1.0,
                    "model": "AutoETS",
                }
                for offset in range(7)
            ]
        )
        rate, kind = Wizard._efn_sales_rate(self.outlet_b, self.product)
        self.assertEqual(kind, "forecast")
        self.assertAlmostEqual(rate, 3.0)
