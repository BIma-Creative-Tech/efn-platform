"""efn_replenishment (G44, docs/04 §28.4, ADR-046 §3.4-§3.5): rumus usulan, cap, pack, assortment, cut-off, SoD."""

import uuid
from datetime import datetime, time, timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.efn_replenishment.models.efn_replenishment import (
    EfnReplenishmentProposal,
    local_today,
    round_pack,
)


@tagged("post_install", "-at_install", "efn")
class TestEfnReplenishment(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        icp = env["ir.config_parameter"].sudo()
        icp.set_param("efn_replenishment.safety_z", "1.65")
        icp.set_param("efn_assortment.enforce", "true")
        cls.company = env["res.company"].create({"name": "PT Uji Replenishment"})
        Warehouse = env["stock.warehouse"]
        cls.dc = Warehouse.create({"name": "DC repl uji", "code": "RDC1", "company_id": cls.company.id,
                                   "efn_is_dc": True})
        cls.outlet = Warehouse.create(
            {
                "name": "Outlet repl uji",
                "code": "ROT1",
                "company_id": cls.company.id,
                "resupply_wh_ids": [(6, 0, cls.dc.ids)],
            }
        )
        cls.categ = env["product.category"].create({"name": "Uji Replenishment"})
        Product = env["product.product"]
        cls.product = Product.create({"name": "Gula (uji)", "is_storable": True, "categ_id": cls.categ.id})
        cls.fresh = Product.create(
            {
                "name": "Yoghurt (uji)",
                "is_storable": True,
                "categ_id": cls.categ.id,
                "tracking": "lot",
                "use_expiration_date": True,
                "expiration_time": 1,
            }
        )
        cls.schedule = env["efn.delivery.schedule"].create(
            {
                "company_id": cls.company.id,
                "warehouse_id": cls.outlet.id,
                "supply_warehouse_id": cls.dc.id,
                "weekdays": 127,
                "lead_days": 1,
                "cutoff_time": 12.0,
                "tz": "Asia/Jakarta",
            }
        )
        # 01:00 UTC = 08:00 WIB hari yang sama -> kirim besok (cut-off hari ini 12:00 WIB).
        cls.now = datetime.combine(fields.Date.today(), time(1, 0))
        cls.today = local_today("Asia/Jakarta", cls.now)
        cls.store = new_test_user(
            env,
            login="efn_repl_store_uji",
            groups="efn_replenishment.group_replenishment_store,purchase.group_purchase_user",
            company_id=cls.company.id,
        )
        cls.store.sudo().efn_outlet_warehouse_ids = [(6, 0, cls.outlet.ids)]
        cls.store_other = new_test_user(
            env, login="efn_repl_store2_uji", groups="efn_replenishment.group_replenishment_store",
            company_id=cls.company.id,
        )
        cls.buyer = new_test_user(
            env, login="efn_repl_buyer_uji", groups="efn_replenishment.group_replenishment_buyer",
            company_id=cls.company.id,
        )

    # ------------------------------------------------------------ helper
    def make_run(self, rows, start_offset=0):
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
        vals = []
        for warehouse, product, yhat, sigma in rows:
            for offset in range(28):
                vals.append(
                    {
                        "run_id": run.id,
                        "company_id": self.company.id,
                        "warehouse_id": warehouse.id,
                        "product_id": product.id,
                        "date": self.today + timedelta(days=start_offset + offset),
                        "yhat": yhat,
                        "p10": max(yhat - 1, 0),
                        "p90": yhat + 1,
                        "sigma": sigma,
                        "model": "AutoETS",
                    }
                )
        self.env["efn.forecast.daily"].create(vals)
        return run

    def compute(self, run):
        Proposal = self.env["efn.replenishment.proposal"]
        Proposal._efn_compute_for_run(run, now=self.now)
        return Proposal.search([("schedule_id", "=", self.schedule.id)], limit=1)

    def line_of(self, proposal, product):
        return proposal.line_ids.filtered(lambda line: line.product_id == product)

    # ------------------------------------------------------------ rumus
    def test_round_pack_never_exceeds_cap(self):
        self.assertEqual(round_pack(15, 6), 18)
        self.assertEqual(round_pack(7, 6, cap=7), 6)
        self.assertEqual(round_pack(12, 6, cap=12), 12)
        self.assertEqual(round_pack(0, 6), 0)

    def test_need_from_forecast_and_live_stock(self):
        self.env["stock.quant"]._update_available_quantity(self.product, self.outlet.lot_stock_id, 5)
        proposal = self.compute(self.make_run([(self.outlet, self.product, 10.0, 0.0)]))
        self.assertEqual(proposal.delivery_date, self.today + timedelta(days=1))
        self.assertEqual(proposal.state, "proposed")
        line = self.line_of(proposal, self.product)
        # Cakupan = hari ini s/d kiriman berikutnya-setelah-ini (besok + 1) = 2 hari x 10 - on hand 5.
        self.assertAlmostEqual(line.forecast_qty, 20)
        self.assertAlmostEqual(line.on_hand, 5)
        self.assertAlmostEqual(line.qty_need, 15)
        self.assertAlmostEqual(line.qty_proposed, 15)
        self.assertEqual(line.qty_approved, line.qty_proposed)
        self.assertFalse(line.reason_codes)

    def test_pack_round_and_rack_cap(self):
        self.env["stock.quant"]._update_available_quantity(self.product, self.outlet.lot_stock_id, 5)
        with patch.object(EfnReplenishmentProposal, "_efn_pack_qty", lambda *a, **k: 6.0):
            proposal = self.compute(self.make_run([(self.outlet, self.product, 10.0, 0.0)]))
            line = self.line_of(proposal, self.product)
            self.assertEqual((line.qty_proposed, line.reason_codes), (18, "pack_round"))
            # Kapasitas rak 12 - on hand 5 = 7 -> dibulatkan TURUN ke 6 (cap tidak dilanggar).
            self.env["efn.assortment.line"].create(
                {
                    "company_id": self.company.id,
                    "warehouse_id": self.outlet.id,
                    "product_tmpl_id": self.product.product_tmpl_id.id,
                    "mode": "allow",
                    "shelf_capacity_qty": 12,
                }
            )
            proposal = self.compute(self.make_run([(self.outlet, self.product, 10.0, 0.0)]))
            line = self.line_of(proposal, self.product)
        self.assertAlmostEqual(line.qty_rack_cap, 7)
        self.assertEqual(line.qty_proposed, 6)
        self.assertEqual(line.reason_codes, "rack_cap,pack_round")

    def test_shelf_life_cap(self):
        # expiration_time 1 hari, tanpa lot ber-ED di DC -> terjual sebelum ED = yhat(tanggal kirim) = 10.
        proposal = self.compute(self.make_run([(self.outlet, self.fresh, 10.0, 0.0)]))
        line = self.line_of(proposal, self.fresh)
        self.assertAlmostEqual(line.qty_need, 20)
        self.assertAlmostEqual(line.qty_shelf_cap, 10)
        self.assertAlmostEqual(line.qty_proposed, 10)
        self.assertEqual(line.reason_codes, "shelf_cap")

    def test_shelf_life_cap_from_dc_lot(self):
        lot = self.env["stock.lot"].create(
            {
                "name": "LOT-UJI-1",
                "product_id": self.fresh.id,
                "company_id": self.company.id,
                "expiration_date": fields.Datetime.now() + timedelta(days=60),
            }
        )
        self.fresh.expiration_time = 0
        self.env["stock.quant"]._update_available_quantity(self.fresh, self.dc.lot_stock_id, 100, lot_id=lot)
        proposal = self.compute(self.make_run([(self.outlet, self.fresh, 10.0, 0.0)]))
        line = self.line_of(proposal, self.fresh)
        self.assertGreater(line.qty_shelf_cap, line.qty_need)
        self.assertAlmostEqual(line.qty_proposed, 20)

    def test_assortment_filter_excludes_with_code(self):
        self.outlet.efn_assortment_policy = "closed"
        proposal = self.compute(self.make_run([(self.outlet, self.product, 10.0, 0.0)]))
        line = self.line_of(proposal, self.product)
        self.assertTrue(line.excluded)
        self.assertEqual((line.reason_codes, line.qty_proposed), ("assortment:not_listed", 0))

    def test_no_order_when_stock_covers(self):
        self.env["stock.quant"]._update_available_quantity(self.product, self.outlet.lot_stock_id, 100)
        proposal = self.compute(self.make_run([(self.outlet, self.product, 10.0, 0.0)]))
        self.assertFalse(proposal, "tanpa kebutuhan -> tidak ada usulan")

    # ------------------------------------------------------------ approval
    def _proposal(self):
        proposal = self.compute(self.make_run([(self.outlet, self.product, 10.0, 0.0)]))
        proposal.sudo().write({"cutoff_at": fields.Datetime.now() + timedelta(hours=6)})
        return proposal

    def test_expired_after_cutoff_not_sent(self):
        proposal = self._proposal()
        proposal.sudo().write({"cutoff_at": fields.Datetime.now() - timedelta(minutes=1)})
        with patch.object(EfnReplenishmentProposal, "_efn_create_supply_document") as supply:
            result = proposal.with_user(self.store).action_efn_approve()
            supply.assert_not_called()
        self.assertEqual(proposal.state, "expired")
        self.assertEqual(result["tag"], "display_notification")

    def test_cron_expires_unapproved(self):
        proposal = self._proposal()
        proposal.sudo().write({"cutoff_at": fields.Datetime.now() - timedelta(minutes=1)})
        self.env["efn.replenishment.proposal"]._cron_efn_expire()
        self.assertEqual(proposal.state, "expired")
        self.assertFalse(proposal.picking_id)

    def test_store_head_edit_requires_reason_and_logs_bias(self):
        proposal = self._proposal()
        line = self.line_of(proposal, self.product).with_user(self.store)
        with self.assertRaisesRegex(UserError, "reason"):
            line.write({"qty_approved": 12})
        line.write({"qty_approved": 12, "change_reason": "rack_full"})
        self.assertEqual((line.qty_approved, line.changed_by), (12, self.store))
        log = self.env["efn.replenishment.bias.log"].search([("proposal_line_id", "=", line.id)])
        # Tanpa stok: usulan = 2 hari x 10 = 20.
        self.assertEqual((log.kind, log.qty_system, log.qty_before, log.qty_after), ("outlet_change", 20, 20, 12))
        with self.assertRaises(UserError):
            log.write({"qty_after": 1})
        with self.assertRaises(AccessError):
            line.write({"qty_proposed": 99})
        with self.assertRaises(AccessError):
            proposal.with_user(self.store).write({"state": "approved"})

    def test_approve_scope_and_sod(self):
        proposal = self._proposal()
        with self.assertRaises(AccessError):
            proposal.with_user(self.store_other).action_efn_approve()
        with self.assertRaises(AccessError):
            proposal.with_user(self.buyer).action_efn_approve()
        picking = self.env["stock.picking"]
        with patch.object(EfnReplenishmentProposal, "_efn_create_supply_document", lambda self: picking):
            proposal.with_user(self.store).action_efn_approve()
        self.assertEqual((proposal.state, proposal.approved_by), ("approved", self.store))
        logs = self.env["efn.replenishment.bias.log"].search([("proposal_id", "=", proposal.id)])
        self.assertEqual(logs.mapped("kind"), ["outlet_approved"])
        with self.assertRaisesRegex(UserError, "state"):
            proposal.with_user(self.store).action_efn_approve()
        # SoD: kepala toko tidak boleh mengonfirmasi PO dari orderpoint terkelola.
        op = self.env["stock.warehouse.orderpoint"]._efn_managed_orderpoint(self.dc, self.product)
        vendor = self.env["res.partner"].create({"name": "Vendor uji", "company_id": self.company.id})
        po = self.env["purchase.order"].create(
            {
                "partner_id": vendor.id,
                "company_id": self.company.id,
                # Penerimaan DC eksplisit: default CE = picking type gudang pertama company (mode test membuat
                # gudang tambahan) sehingga tidak konsisten dengan lokasi orderpoint DC.
                "picking_type_id": self.dc.in_type_id.id,
                "order_line": [
                    (0, 0, {"product_id": self.product.id, "product_qty": 10, "price_unit": 1.0,
                            "orderpoint_id": op.id})
                ],
            }
        )
        with self.assertRaisesRegex(AccessError, "sod"):
            po.with_user(self.store).button_confirm()
        po.button_confirm()
        confirmed = self.env["efn.replenishment.bias.log"].search([("kind", "=", "dc_confirmed"),
                                                                    ("orderpoint_id", "=", op.id)])
        self.assertAlmostEqual(confirmed.qty_after, 10)

    def test_supply_document_through_ce_orderpoint(self):
        proposal = self._proposal()
        proposal.with_user(self.store).action_efn_approve()
        op = self.env["stock.warehouse.orderpoint"].search(
            [("product_id", "=", self.product.id), ("location_id", "=", self.outlet.lot_stock_id.id)]
        )
        self.assertEqual((op.trigger, op.efn_replenishment_managed), ("manual", True))
        self.assertEqual(op.route_id.supplier_wh_id, self.dc)
        moves = self.env["stock.move"].search(
            [("product_id", "=", self.product.id), ("location_id.warehouse_id", "=", self.dc.id)]
        )
        self.assertTrue(moves, "procurement CE membuat move keluar DC")
        self.assertEqual(proposal.picking_id, moves.picking_id[:1])

    def test_next_delivery_skips_past_cutoff_and_respects_weekday_mask(self):
        # Jadwal setiap hari, lead 1, cut-off 12:00 WIB (= 05:00 UTC).
        day, cutoff = self.schedule._efn_next_delivery(self.now)  # 08:00 WIB: cut-off hari ini belum lewat
        self.assertEqual(day, self.today + timedelta(days=1))
        self.assertEqual(cutoff, datetime.combine(self.today, time(5, 0)))
        late = datetime.combine(fields.Date.today(), time(6, 0))  # 13:00 WIB: sudah lewat -> kirim berikutnya
        day, cutoff = self.schedule._efn_next_delivery(late)
        self.assertEqual(day, self.today + timedelta(days=2))
        self.assertEqual(cutoff, datetime.combine(self.today + timedelta(days=1), time(5, 0)))
        # Hanya Senin (bitmask 1), lead 0: tanggal kirim = Senin terdekat yang cut-off-nya belum lewat.
        monday = self.schedule.copy({"weekdays": 1, "lead_days": 0})
        expected = self.today
        while expected.weekday() != 0:
            expected += timedelta(days=1)
        self.assertEqual(monday._efn_next_delivery(self.now)[0], expected)

    def test_line_edit_locked_after_cutoff(self):
        proposal = self._proposal()
        line = self.line_of(proposal, self.product)
        proposed = line.qty_proposed
        proposal.sudo().write({"cutoff_at": fields.Datetime.now() - timedelta(minutes=1)})
        with self.assertRaisesRegex(UserError, "locked"):
            line.with_user(self.store).write({"qty_approved": 5, "change_reason": "other"})
        self.assertEqual(line.qty_approved, proposed)

    def test_reject_scope_and_no_supply_document(self):
        proposal = self._proposal()
        for user in (self.store_other, self.buyer):
            with self.assertRaises(AccessError):
                proposal.with_user(user).action_efn_reject()
        with patch.object(EfnReplenishmentProposal, "_efn_create_supply_document") as supply:
            proposal.with_user(self.store).action_efn_reject()
            supply.assert_not_called()
        self.assertEqual((proposal.state, proposal.picking_id), ("rejected", self.env["stock.picking"]))
        with self.assertRaisesRegex(UserError, "state"):
            proposal.with_user(self.store).action_efn_approve()

    # ------------------------------------------------------------ antrean & DC
    def test_run_close_queues_and_respects_enabled_flag(self):
        icp = self.env["ir.config_parameter"].sudo()
        run = self.make_run([(self.outlet, self.product, 10.0, 0.0)])
        run._efn_on_run_closed_ok()
        self.assertTrue(run.efn_proposal_pending)
        Proposal = self.env["efn.replenishment.proposal"]
        icp.set_param("efn_replenishment.enabled", "false")
        Proposal._cron_efn_compute_pending()
        self.assertFalse(run.efn_proposal_pending)
        self.assertFalse(Proposal.search([("run_id", "=", run.id)]))
        icp.set_param("efn_replenishment.enabled", "true")
        run.sudo().efn_proposal_pending = True
        Proposal._cron_efn_compute_pending()
        self.assertTrue(Proposal.search([("run_id", "=", run.id)]))

    def test_dc_orderpoint_manual_qty(self):
        run = self.make_run([(self.dc, self.product, 20.0, 0.0)])
        self.env["stock.warehouse.orderpoint"]._efn_compute_dc_orderpoints(run, now=self.now)
        op = self.env["stock.warehouse.orderpoint"].search(
            [("product_id", "=", self.product.id), ("location_id", "=", self.dc.lot_stock_id.id)]
        )
        self.assertEqual((op.trigger, op.efn_replenishment_managed), ("manual", True))
        # Tanpa jadwal supplier: cakupan 7 hari x 20, lead 0, stok 0.
        self.assertAlmostEqual(op.qty_to_order_manual, 140)
        log = self.env["efn.replenishment.bias.log"].search([("orderpoint_id", "=", op.id), ("kind", "=", "dc_system")])
        self.assertAlmostEqual(log.qty_system, 140)

    # ------------------------------------------------------------ perbaikan G44 (SEC-210/213, QA-2/3, run kosong)
    def test_sec210_qty_approved_bounds(self):
        proposal = self._proposal()  # qty_proposed 20 -> batas 3 x 20 = 60
        line = self.line_of(proposal, self.product)
        for bad in (61, float("inf"), float("nan"), -1):
            with self.subTest(qty=bad), self.assertRaisesRegex(UserError, "qty"):
                line.with_user(self.store).write({"qty_approved": bad, "change_reason": "event"})
        line.with_user(self.store).write({"qty_approved": 60, "change_reason": "event"})
        self.assertEqual(line.qty_approved, 60)
        boss = new_test_user(
            self.env,
            login="efn_repl_store_admin_uji",
            groups="efn_replenishment.group_replenishment_store,efn_replenishment.group_replenishment_admin",
            company_id=self.company.id,
        )
        boss.sudo().efn_outlet_warehouse_ids = [(6, 0, self.outlet.ids)]
        line.with_user(boss).write({"qty_approved": 100, "change_reason": "event"})
        self.assertEqual(line.qty_approved, 100)
        self.env["ir.config_parameter"].sudo().set_param("efn_replenishment.max_approve_factor", "1")
        with self.assertRaisesRegex(UserError, "qty_limit"):
            line.with_user(self.store).write({"qty_approved": 21, "change_reason": "event"})

    def test_sec213_store_head_cannot_approve_po(self):
        op = self.env["stock.warehouse.orderpoint"]._efn_managed_orderpoint(self.dc, self.product)
        vendor = self.env["res.partner"].create({"name": "Vendor uji 2", "company_id": self.company.id})
        po = self.env["purchase.order"].create(
            {
                "partner_id": vendor.id,
                "company_id": self.company.id,
                "picking_type_id": self.dc.in_type_id.id,
                "order_line": [
                    (0, 0, {"product_id": self.product.id, "product_qty": 5, "price_unit": 1.0,
                            "orderpoint_id": op.id})
                ],
            }
        )
        with self.assertRaisesRegex(AccessError, "sod"):
            po.with_user(self.store).button_approve()

    def test_qa2_window_starts_at_first_forecast_date(self):
        # 16:40 UTC = 23:40 WIB: run malam berisi besok dst.; kiriman = lusa (cut-off besok 12:00).
        late = datetime.combine(fields.Date.today(), time(16, 40))
        run = self.make_run([(self.outlet, self.product, 10.0, 0.0)], start_offset=1)
        self.env["efn.replenishment.proposal"]._efn_compute_for_run(run, now=late)
        proposal = self.env["efn.replenishment.proposal"].search([("schedule_id", "=", self.schedule.id)], limit=1)
        self.assertEqual(proposal.delivery_date, self.today + timedelta(days=2))
        line = self.line_of(proposal, self.product)
        # Jendela [besok, lusa+1) = 2 hari x 10 (hari ini tidak diisi rata-rata).
        self.assertAlmostEqual(line.forecast_qty, 20)

    def test_qa3_supplier_order_day_offset(self):
        order_day = self.today + timedelta(days=1)
        vendor = self.env["res.partner"].create({"name": "Supplier jadwal uji", "company_id": self.company.id})
        self.env["product.supplierinfo"].create(
            {
                "partner_id": vendor.id,
                "product_tmpl_id": self.product.product_tmpl_id.id,
                "company_id": self.company.id,
                "delay": 0,
            }
        )
        self.env["efn.supplier.schedule"].create(
            {
                "company_id": self.company.id,
                "partner_id": vendor.id,
                "warehouse_id": self.dc.id,
                "weekdays": 1 << order_day.weekday(),
            }
        )
        icp = self.env["ir.config_parameter"].sudo()
        Orderpoint = self.env["stock.warehouse.orderpoint"]
        domain = [("product_id", "=", self.product.id), ("location_id", "=", self.dc.lot_stock_id.id)]
        run = self.make_run([(self.dc, self.product, 20.0, 0.0)])
        icp.set_param("efn_replenishment.order_day_offset", "0")
        Orderpoint._efn_compute_dc_orderpoints(run, now=self.now)
        self.assertFalse(Orderpoint.search(domain), "hari ini bukan hari order supplier -> dilewati")
        icp.set_param("efn_replenishment.order_day_offset", "1")
        Orderpoint._efn_compute_dc_orderpoints(run, now=self.now)
        op = Orderpoint.search(domain)
        # Cakupan [hari ini, order berikutnya (order_day + 7) + lead 0) = 8 hari x 20.
        self.assertAlmostEqual(op.qty_to_order_manual, 160)

    def test_run_ok_without_accepted_lines_notifies_admin(self):
        admin = new_test_user(
            self.env,
            login="efn_repl_admin_uji",
            groups="efn_replenishment.group_replenishment_admin",
            company_id=self.company.id,
        )
        run = self.make_run([])
        run._efn_on_run_closed_ok()
        activities = run.activity_ids.filtered(lambda a: a.user_id == admin)
        self.assertEqual(len(activities), 1)
        full = self.make_run([(self.outlet, self.product, 10.0, 0.0)])
        self.env["efn.forecast.chunk"].create({"run_id": full.id, "chunk_no": 0, "accepted": 28})
        full._efn_on_run_closed_ok()
        self.assertFalse(full.activity_ids)
