"""Tour UI POS efn_expiry_monitor (G45/G48, docs/04 §28.5a), tag `efn_tour` (Chrome headless, job CI odoo-tour).

Fixture mengikuti tests/test_pos_expiry.py (lot dengan expiration/removal date, markdown aktif) tetapi di atas
TestPointOfSaleHttpCommon (pola tour efn_pos_refund_origin). Nama produk/lot harus sama dengan
static/tests/tours/efn_expiry_tour.js. Pindaian GS1 (01)(10) belum dicakup tour (lihat README).
"""

from datetime import timedelta

from odoo import Command, fields
from odoo.tests import new_test_user, tagged

from odoo.addons.efn_pos_refund_origin.models.return_pin import hash_pin
from odoo.addons.point_of_sale.tests.test_frontend import TestPointOfSaleHttpCommon

TOUR_PIN = "135790"
MARKDOWN_PRICE_B = 12000.0


@tagged("post_install", "-at_install", "efn_tour")
class TestEfnExpiryTour(TestPointOfSaleHttpCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        if "loyalty.program" in env:
            env["loyalty.program"].search([]).write({"active": False})
        icp = env["ir.config_parameter"].sudo()
        icp.set_param("efn_expiry_monitor.enabled", "true")
        icp.set_param("efn.pos.return_pin_kdf_rounds", "1000")
        config = cls.main_pos_config
        company = config.company_id
        cls.warehouse = config.picking_type_id.warehouse_id
        location = config.picking_type_id.default_location_src_id
        Product = env["product.product"]
        common = {
            "available_in_pos": True,
            "is_storable": True,
            "tracking": "lot",
            "use_expiration_date": True,
            "taxes_id": [Command.clear()],
        }
        cls.product_a = Product.create({**common, "name": "Produk Tour FEFO", "list_price": 15000.0})
        cls.product_b = Product.create({**common, "name": "Produk Tour Markdown", "list_price": 20000.0})
        now = fields.Datetime.now()

        def lot(product, name, days):
            expiry = now + timedelta(days=days)
            record = env["stock.lot"].create(
                {
                    "name": name,
                    "product_id": product.id,
                    "company_id": company.id,
                    "expiration_date": expiry,
                    "removal_date": expiry,
                }
            )
            env["stock.quant"]._update_available_quantity(product, location, 10, lot_id=record)
            return record

        # Produk A: lot markdown kedaluwarsa paling awal (tidak boleh dipilih FEFO), lalu SOON (dipilih), LATE.
        cls.lot_a_md = lot(cls.product_a, "TOUR-A-MD", 1)
        cls.lot_a_soon = lot(cls.product_a, "TOUR-A-SOON", 3)
        cls.lot_a_late = lot(cls.product_a, "TOUR-A-LATE", 20)
        # Produk B: hanya lot markdown + lot kedaluwarsa -> tidak ada kandidat FEFO -> popup lot CE (2 lot ber-stok,
        # kategori tanpa removal strategy fifo/lifo, jadi core tidak memilih otomatis).
        cls.lot_b_md = lot(cls.product_b, "TOUR-B-MD", 2)
        cls.lot_b_old = lot(cls.product_b, "TOUR-B-OLD", -2)
        today = fields.Date.context_today(env["efn.lot.markdown"])
        Markdown = env["efn.lot.markdown"]
        # Periode lebar (kemarin s.d. +2 hari): POS memeriksa periode dengan tanggal perangkat.
        period = {"date_from": today - timedelta(days=1), "date_to": today + timedelta(days=2)}
        cls.markdown_a = Markdown.create(
            {
                "company_id": company.id,
                "warehouse_id": cls.warehouse.id,
                "lot_id": cls.lot_a_md.id,
                "discount_pct": 30,
                **period,
            }
        )
        cls.markdown_b = Markdown.create(
            {
                "company_id": company.id,
                "warehouse_id": cls.warehouse.id,
                "lot_id": cls.lot_b_md.id,
                "price": MARKDOWN_PRICE_B,
                "qty_cap": 5,
                **period,
            }
        )
        (cls.markdown_a | cls.markdown_b).sudo().write({"state": "active"})
        cls.supervisor = new_test_user(
            env,
            login="efn_exp_tour_supervisor",
            name="Supervisor Tour Expiry",
            groups="base.group_user,efn_expiry_monitor.group_expiry_override",
            company_id=company.id,
        )
        cls.supervisor.sudo().write({"efn_return_pin_hash": hash_pin(env, TOUR_PIN)})

    def _last_order(self):
        return self.env["pos.order"].search(
            [("config_id", "=", self.main_pos_config.id), ("state", "in", ("paid", "done", "invoiced"))],
            order="id desc",
            limit=1,
        )

    @staticmethod
    def _lines_with_lot(order, lot):
        return order.lines.filtered(lambda line: lot.name in line.pack_lot_ids.mapped("lot_name"))

    def _events(self, order, kind):
        return self.env["efn.expiry.event"].sudo().search([("pos_order_id", "=", order.id), ("kind", "=", kind)])

    def test_fefo_and_markdown_lot(self):
        self.assertFalse(
            self.pos_user.has_group("efn_expiry_monitor.group_expiry_override"), "kasir tour tanpa grup override"
        )
        self.main_pos_config.with_user(self.pos_user).open_ui()
        self.start_pos_tour("EfnExpiryFefoMarkdownTour", login="pos_user")
        order = self._last_order()
        self.assertTrue(order, "order tour tersinkron")
        fefo = self._lines_with_lot(order, self.lot_a_soon)
        self.assertEqual(len(fefo), 1)
        self.assertEqual(fefo.efn_lot_source, "fefo")
        self.assertFalse(fefo.efn_markdown_id)
        self.assertFalse(self._lines_with_lot(order, self.lot_a_md), "lot markdown tidak dipilih FEFO")
        md_line = self._lines_with_lot(order, self.lot_b_md)
        self.assertEqual(len(md_line), 1)
        self.assertEqual(md_line.efn_lot_source, "manual")
        self.assertEqual(md_line.efn_markdown_ref, self.markdown_b.id)
        self.assertEqual(md_line.efn_markdown_id, self.markdown_b)
        self.assertAlmostEqual(md_line.price_unit, MARKDOWN_PRICE_B)
        self.assertEqual(self.markdown_b.qty_sold, 1)
        self.assertEqual(self.markdown_a.qty_sold, 0)
        self.assertFalse(
            self.env["efn.expiry.event"].sudo().search([("pos_order_id", "=", order.id)]),
            "tanpa event markdown_invalid / price_mismatch / kedaluwarsa",
        )

    def test_expired_lot_cancel_then_supervisor_override(self):
        self.main_pos_config.with_user(self.pos_user).open_ui()
        self.start_pos_tour("EfnExpiryOverrideTour", login="pos_user")
        order = self._last_order()
        self.assertTrue(order, "order tour tersinkron")
        old_line = self._lines_with_lot(order, self.lot_b_old)
        self.assertEqual(len(old_line), 1, "jalur Batal tidak menambah baris")
        self.assertTrue(old_line.efn_expiry_override)
        event = self._events(order, "pos_expired_override")
        self.assertEqual(len(event), 1)
        self.assertEqual((event.lot_id, event.approver_id), (self.lot_b_old, self.supervisor))
        self.assertFalse(self._events(order, "pos_expired_no_override"))
        approvals = self.env["efn.expiry.pos.approval"].sudo().search([("order_ref", "=", order.uuid)])
        self.assertEqual(sorted(approvals.mapped("state")), ["consumed", "failed"], "satu PIN salah lalu benar")
        consumed = approvals.filtered(lambda a: a.state == "consumed")
        self.assertEqual((consumed.approver_user_id, consumed.cashier_user_id), (self.supervisor, self.pos_user))
        self.assertEqual(consumed.consumed_order_id, order)
