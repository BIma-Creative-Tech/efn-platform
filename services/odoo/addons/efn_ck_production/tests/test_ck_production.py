"""efn_ck_production (G28, K25-08/K25-12): mode produksi, resupply CK -> outlet, waste beralasan, yield & varians."""

from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import Form, TransactionCase, tagged


@tagged("post_install", "-at_install", "efn")
class TestEfnCkProduction(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.Warehouse = cls.env["stock.warehouse"]
        cls.ck = cls.Warehouse.create({"name": "CK uji", "code": "CKU", "efn_is_central_kitchen": True})
        cls.outlet = cls.Warehouse.create({"name": "Outlet uji", "code": "OLU", "efn_ck_supplier_wh_id": cls.ck.id})
        cls.uom_kg = cls.env.ref("uom.product_uom_kgm")
        cls.uom_unit = cls.env.ref("uom.product_uom_unit")
        Product = cls.env["product.product"]
        storable = {"type": "consu", "is_storable": True}
        cls.ayam = Product.create(
            {"name": "Ayam utuh (uji)", "uom_id": cls.uom_kg.id, "standard_price": 40000, **storable}
        )
        cls.tepung = Product.create(
            {"name": "Tepung (uji)", "uom_id": cls.uom_kg.id, "standard_price": 12000, **storable}
        )
        cls.paket = Product.create({"name": "Paket ayam (uji)", "uom_id": cls.uom_unit.id, **storable})
        cls.dada = Product.create({"name": "Dada ayam (uji)", "uom_id": cls.uom_kg.id, **storable})
        cls.paha = Product.create({"name": "Paha ayam (uji)", "uom_id": cls.uom_kg.id, **storable})
        cls.bom = cls.env["mrp.bom"].create(
            {
                "product_tmpl_id": cls.paket.product_tmpl_id.id,
                "product_qty": 1,
                "type": "normal",
                "bom_line_ids": [
                    Command.create({"product_id": cls.ayam.id, "product_qty": 0.2, "product_uom_id": cls.uom_kg.id}),
                    Command.create({"product_id": cls.tepung.id, "product_qty": 0.05, "product_uom_id": cls.uom_kg.id}),
                ],
            }
        )
        Quant = cls.env["stock.quant"]
        for product, qty in ((cls.ayam, 50), (cls.tepung, 20)):
            Quant._update_available_quantity(product, cls.ck.lot_stock_id, qty)

    def _mo(self, qty):
        form = Form(self.env["mrp.production"])
        form.product_id = self.paket
        form.bom_id = self.bom
        form.product_qty = qty
        form.picking_type_id = self.ck.manu_type_id
        mo = form.save()
        mo.action_confirm()
        return mo

    def _finish(self, mo, produced, consumed):
        mo.qty_producing = produced
        for move in mo.move_raw_ids:
            move.quantity = consumed[move.product_id]
            move.picked = True
        self._mark_done(mo)
        return mo

    def _mark_done(self, mo):
        """button_mark_done + wizard CE (peringatan konsumsi, backorder) bila muncul."""
        action = mo.button_mark_done()
        for _i in range(3):
            if not isinstance(action, dict) or not action.get("res_model"):
                break
            wizard = Form.from_action(self.env, action).save()
            if action["res_model"] == "mrp.consumption.warning":
                action = wizard.action_confirm()
            elif action["res_model"] == "mrp.production.backorder":
                action = wizard.action_close_mo()
            else:
                break

    # ---------------------------------------------------------------- resupply & mode produksi
    def test_outlet_resupply_route_from_ck(self):
        self.assertIn(self.ck, self.outlet.resupply_wh_ids)
        route = self.outlet.efn_ck_supply_route_id
        self.assertTrue(route)
        self.assertEqual(route.supplier_wh_id, self.ck)

    def test_supplier_must_be_ck_same_company(self):
        plain = self.Warehouse.create({"name": "Gudang biasa", "code": "GBU"})
        with self.assertRaisesRegex(ValidationError, "not_ck"):
            self.outlet.efn_ck_supplier_wh_id = plain

    def test_production_mode_sets_routes(self):
        mto = self.env.ref("stock.route_warehouse0_mto")
        manufacture = self.env.ref("mrp.route_warehouse0_manufacture")
        tmpl = self.paket.product_tmpl_id
        tmpl.efn_ck_production_mode = "mto"
        self.assertTrue(mto.active)
        self.assertIn(mto, tmpl.route_ids)
        self.assertIn(manufacture, tmpl.route_ids)
        tmpl.efn_ck_production_mode = "mts"
        self.assertNotIn(mto, tmpl.route_ids)
        self.assertIn(manufacture, tmpl.route_ids)
        tmpl.efn_ck_production_mode = False
        self.assertNotIn(manufacture, tmpl.route_ids)

    def test_mts_reorder_rule_creates_mo(self):
        self.paket.product_tmpl_id.efn_ck_production_mode = "mts"
        op = self.env["stock.warehouse.orderpoint"].create(
            {
                "product_id": self.paket.id,
                "warehouse_id": self.ck.id,
                "location_id": self.ck.lot_stock_id.id,
                "product_min_qty": 10,
                "product_max_qty": 30,
                "trigger": "manual",
            }
        )
        op.action_replenish()
        mo = self.env["mrp.production"].search([("product_id", "=", self.paket.id)])
        self.assertEqual(sum(mo.mapped("product_qty")), 30)

    # ---------------------------------------------------------------- waste
    def test_scrap_requires_reason_and_values_waste(self):
        scrap = self.env["stock.scrap"].create(
            {"product_id": self.ayam.id, "scrap_qty": 2, "location_id": self.ck.lot_stock_id.id}
        )
        with self.assertRaisesRegex(UserError, "reason_required"):
            scrap.do_scrap()
        scrap.scrap_reason_tag_ids = self.env.ref("efn_ck_production.scrap_reason_rusak")
        scrap.do_scrap()
        self.assertEqual(scrap.state, "done")
        self.assertEqual(scrap.efn_warehouse_id, self.ck)
        self.assertAlmostEqual(scrap.efn_waste_value, 80000, delta=1)
        self.env["efn.ck.waste.report"].flush_model()
        row = self.env["efn.ck.waste.report"].search([("id", "=", scrap.id)])
        self.assertEqual(row.reason_id, self.env.ref("efn_ck_production.scrap_reason_rusak"))
        self.assertAlmostEqual(row.value, 80000, delta=1)

    # ---------------------------------------------------------------- yield & varians
    def test_yield_and_variance_within_threshold(self):
        mo = self._finish(self._mo(10), 10, {self.ayam: 2.0, self.tepung: 0.5})
        self.assertEqual(mo.state, "done")
        self.assertAlmostEqual(mo.efn_yield_pct, 100.0)
        self.assertAlmostEqual(mo.efn_variance_value, 0.0)
        self.assertFalse(mo.efn_yield_alert)

    def test_overconsumption_flags_alert(self):
        mo = self._finish(self._mo(10), 10, {self.ayam: 2.4, self.tepung: 0.5})
        rows = {r["product"]: r for r in mo._efn_variance_rows()}
        self.assertAlmostEqual(rows[self.ayam]["variance_qty"], 0.4)
        self.assertAlmostEqual(rows[self.ayam]["variance_pct"], 20.0)
        self.assertAlmostEqual(mo.efn_variance_value, 0.4 * 40000, delta=1)
        self.assertTrue(mo.efn_yield_alert)
        self.assertTrue(mo.activity_ids)
        self.assertEqual(len(mo.efn_variance_line_ids), 2)

    def test_low_yield_flags_alert(self):
        mo = self._mo(10)
        mo.qty_producing = 9
        for move in mo.move_raw_ids:
            move.quantity = {self.ayam: 1.8, self.tepung: 0.45}[move.product_id]
            move.picked = True
        self._mark_done(mo)
        self.assertEqual(mo.state, "done")
        self.assertAlmostEqual(mo.efn_yield_pct, 90.0)
        self.assertTrue(mo.efn_yield_alert)
        self.env["efn.ck.yield.report"].flush_model()
        row = self.env["efn.ck.yield.report"].search([("production_id", "=", mo.id)])
        self.assertTrue(row.alert)

    def test_byproduct_disassembly(self):
        self.env.user.group_ids |= self.env.ref("mrp.group_mrp_byproducts")
        bom = self.env["mrp.bom"].create(
            {
                "product_tmpl_id": self.dada.product_tmpl_id.id,
                "product_qty": 0.4,
                "product_uom_id": self.uom_kg.id,
                "type": "normal",
                "bom_line_ids": [Command.create({"product_id": self.ayam.id, "product_qty": 1.0})],
                "byproduct_ids": [
                    Command.create(
                        {
                            "product_id": self.paha.id,
                            "product_qty": 0.35,
                            "product_uom_id": self.uom_kg.id,
                            "cost_share": 40,
                        }
                    )
                ],
            }
        )
        form = Form(self.env["mrp.production"])
        form.product_id = self.dada
        form.bom_id = bom
        form.product_qty = 4
        form.picking_type_id = self.ck.manu_type_id
        mo = form.save()
        mo.action_confirm()
        mo.qty_producing = 4
        for move in mo.move_raw_ids:
            move.quantity = 10
            move.picked = True
        self._mark_done(mo)
        self.assertEqual(mo.state, "done")
        self.assertAlmostEqual(mo.efn_byproduct_qty, 3.5)
        self.assertAlmostEqual(self.paha.with_context(warehouse_id=self.ck.id).qty_available, 3.5)
