"""PoC Kitting (ADR-018): MO Manufacture menghasilkan stok paket; penjualan menu Kit
memotong stok PAKET, bukan bahan mentah. Data dibuat sendiri (tidak bergantung demo)."""

from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import Form, TransactionCase, tagged


@tagged("post_install", "-at_install", "efn")
class TestEfnKitting(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse = cls.env["stock.warehouse"].search(
            [("company_id", "=", cls.env.company.id)], limit=1
        )
        cls.stock_location = cls.warehouse.lot_stock_id
        cls.customer_location = cls.env.ref("stock.stock_location_customers")
        cls.uom_kg = cls.env.ref("uom.product_uom_kgm")
        cls.uom_unit = cls.env.ref("uom.product_uom_unit")
        cls.uom_porsi = cls.env.ref("efn_kitting.uom_porsi")

        Product = cls.env["product.product"]
        storable = {"type": "consu", "is_storable": True}
        cls.ayam = Product.create({"name": "Ayam Utuh (uji)", "uom_id": cls.uom_kg.id, **storable})
        cls.tepung = Product.create({"name": "Tepung (uji)", "uom_id": cls.uom_kg.id, **storable})
        cls.paket = Product.create(
            {
                "name": "Paket Ayam Geprek (uji)",
                "uom_id": cls.uom_porsi.id,
                "efn_is_kitting_package": True,
                **storable,
            }
        )
        cls.nasi = Product.create(
            {
                "name": "Paket Nasi (uji)",
                "uom_id": cls.uom_porsi.id,
                "efn_is_kitting_package": True,
                **storable,
            }
        )
        cls.es_teh = Product.create({"name": "Es Teh (uji)", "type": "consu", "is_storable": False})
        cls.menu = Product.create({"name": "Menu Komplit (uji)", "type": "consu", "is_storable": False})

        Bom = cls.env["mrp.bom"]
        cls.bom_paket = Bom.create(
            {
                "product_tmpl_id": cls.paket.product_tmpl_id.id,
                "product_qty": 1.0,
                "product_uom_id": cls.uom_porsi.id,
                "type": "normal",
                "bom_line_ids": [
                    Command.create({"product_id": cls.ayam.id, "product_qty": 0.2, "product_uom_id": cls.uom_kg.id}),
                    Command.create({"product_id": cls.tepung.id, "product_qty": 0.05, "product_uom_id": cls.uom_kg.id}),
                ],
            }
        )
        cls.bom_menu = Bom.create(
            {
                "product_tmpl_id": cls.menu.product_tmpl_id.id,
                "product_qty": 1.0,
                "product_uom_id": cls.uom_unit.id,
                "type": "phantom",
                "bom_line_ids": [
                    Command.create({"product_id": cls.paket.id, "product_qty": 1.0, "product_uom_id": cls.uom_porsi.id}),
                    Command.create({"product_id": cls.nasi.id, "product_qty": 1.0, "product_uom_id": cls.uom_porsi.id}),
                    Command.create({"product_id": cls.es_teh.id, "product_qty": 1.0, "product_uom_id": cls.uom_unit.id}),
                ],
            }
        )

    def _qty(self, product):
        return self.env["stock.quant"]._get_available_quantity(product, self.stock_location)

    def _add_stock(self, product, qty):
        self.env["stock.quant"]._update_available_quantity(product, self.stock_location, qty)

    def test_kitting_role_field_removed(self):
        """Gelombang 3b: peran BoM diturunkan dari type + flag paket, tidak disimpan."""
        self.assertNotIn("efn_kitting_role", self.env["mrp.bom"]._fields)
        self.assertEqual(self.bom_paket.type, "normal")
        self.assertTrue(self.bom_paket.product_tmpl_id.efn_is_kitting_package)
        self.assertEqual(self.bom_menu.type, "phantom")

    def test_switching_package_bom_to_phantom_is_blocked(self):
        with self.assertRaises(ValidationError):
            self.bom_paket.type = "phantom"

    def test_mo_consumes_raw_and_produces_package(self):
        self._add_stock(self.ayam, 2.0)
        self._add_stock(self.tepung, 0.5)

        mo_form = Form(self.env["mrp.production"])
        mo_form.product_id = self.paket
        mo_form.bom_id = self.bom_paket
        mo_form.product_qty = 10.0
        mo = mo_form.save()
        mo.action_confirm()
        mo.action_assign()
        mo.button_mark_done()

        self.assertEqual(mo.state, "done")
        self.assertAlmostEqual(self._qty(self.ayam), 0.0, places=3)
        self.assertAlmostEqual(self._qty(self.tepung), 0.0, places=3)
        self.assertAlmostEqual(self._qty(self.paket), 10.0, places=3)

    def test_selling_combo_consumes_packages_not_raw(self):
        self._add_stock(self.paket, 5.0)
        self._add_stock(self.nasi, 5.0)
        self._add_stock(self.ayam, 1.0)
        self._add_stock(self.tepung, 1.0)

        picking = self.env["stock.picking"].create(
            {
                "picking_type_id": self.warehouse.out_type_id.id,
                "location_id": self.stock_location.id,
                "location_dest_id": self.customer_location.id,
                "move_ids": [
                    Command.create(
                        {
                            "product_id": self.menu.id,
                            "product_uom_qty": 2.0,
                            "product_uom": self.uom_unit.id,
                            "location_id": self.stock_location.id,
                            "location_dest_id": self.customer_location.id,
                        }
                    )
                ],
            }
        )
        picking.action_confirm()
        # Kit di-explode oleh mrp: move menu diganti move komponen (paket, nasi, es teh).
        self.assertEqual(
            set(picking.move_ids.product_id.ids),
            {self.paket.id, self.nasi.id, self.es_teh.id},
        )
        picking.action_assign()
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
        picking.move_ids.picked = True
        picking.button_validate()

        self.assertEqual(picking.state, "done")
        self.assertAlmostEqual(self._qty(self.paket), 3.0, places=3)
        self.assertAlmostEqual(self._qty(self.nasi), 3.0, places=3)
        # Bahan mentah TIDAK tersentuh oleh penjualan menu (inti koreksi R-T01).
        self.assertAlmostEqual(self._qty(self.ayam), 1.0, places=3)
        self.assertAlmostEqual(self._qty(self.tepung), 1.0, places=3)

    def test_package_cannot_use_phantom_bom(self):
        with self.assertRaises(ValidationError):
            self.env["mrp.bom"].create(
                {
                    "product_tmpl_id": self.paket.product_tmpl_id.id,
                    "product_uom_id": self.uom_porsi.id,
                    "type": "phantom",
                    "bom_line_ids": [
                        Command.create({"product_id": self.ayam.id, "product_qty": 0.2, "product_uom_id": self.uom_kg.id}),
                    ],
                }
            )

    def test_flagging_product_with_phantom_bom_is_blocked(self):
        product = self.env["product.product"].create(
            {"name": "Storable dengan Kit (uji)", "type": "consu", "is_storable": True}
        )
        self.env["mrp.bom"].create(
            {
                "product_tmpl_id": product.product_tmpl_id.id,
                "product_uom_id": self.uom_unit.id,
                "type": "phantom",
                "bom_line_ids": [
                    Command.create({"product_id": self.nasi.id, "product_qty": 1.0, "product_uom_id": self.uom_porsi.id}),
                ],
            }
        )
        with self.assertRaises(ValidationError):
            product.product_tmpl_id.efn_is_kitting_package = True

    def test_package_must_be_storable(self):
        with self.assertRaises(ValidationError):
            self.env["product.product"].create(
                {
                    "name": "Paket non-storable (uji)",
                    "type": "consu",
                    "is_storable": False,
                    "efn_is_kitting_package": True,
                }
            )
