"""efn_agri (G28, K25-13): standar mutu per komoditas, grade otomatis, gerbang QC gudang 3 langkah, harga PO per grade,
reject di-scrap, traceability lot."""

from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "efn")
class TestEfnAgri(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.kg = cls.env.ref("uom.product_uom_kgm")
        cls.cabai = cls.env["product.product"].create(
            {"name": "Cabai merah (uji)", "type": "consu", "is_storable": True, "uom_id": cls.kg.id, "tracking": "lot"}
        )
        cls.commodity = cls.env["efn.agri.commodity"].create(
            {
                "name": "Cabai merah",
                "code": "CBM",
                "product_ids": [Command.set(cls.cabai.ids)],
                "uom_id": cls.kg.id,
                "floor_price": 20000,
                "param_ids": [
                    Command.create(
                        {"code": "MOIST", "name": "Kadar air", "unit": "%", "has_max": True, "max_value": 85}
                    ),
                    Command.create({"code": "REJECT", "name": "Reject", "unit": "%", "has_max": True, "max_value": 30}),
                    Command.create({"code": "PEST", "name": "Bebas hama", "kind": "pass"}),
                ],
            }
        )
        moist, reject, _pest = cls.commodity.param_ids
        cls.grade_a, cls.grade_b, cls.grade_r = cls.env["efn.agri.grade"].create(
            [
                {
                    "commodity_id": cls.commodity.id,
                    "sequence": 1,
                    "name": "A",
                    "price": 30000,
                    "rule_ids": [
                        Command.create({"param_id": moist.id, "max_value": 75}),
                        Command.create({"param_id": reject.id, "max_value": 5}),
                    ],
                },
                {
                    "commodity_id": cls.commodity.id,
                    "sequence": 2,
                    "name": "B",
                    "price": 25000,
                    "rule_ids": [Command.create({"param_id": reject.id, "max_value": 15})],
                },
                {"commodity_id": cls.commodity.id, "sequence": 9, "name": "Reject", "price": 0, "is_reject": True},
            ]
        )
        cls.partner = cls.env["res.partner"].create({"name": "Poktan Uji"})
        cls.farmer = cls.env["efn.agri.farmer"].create(
            {
                "partner_id": cls.partner.id,
                "region": "Garut",
                "land_area_ha": 2.5,
                "commodity_ids": [Command.set(cls.commodity.ids)],
            }
        )
        cls.wh = cls.env["stock.warehouse"].create({"name": "Collection center uji", "code": "CCU"})
        cls.wh.action_efn_agri_enable_qc_gate()
        cls.inspector = new_test_user(
            cls.env, login="efn_qc_insp", groups="efn_agri.group_agri_inspector,purchase.group_purchase_user"
        )

    def _receive(self, qty=100):
        harvest = self.env["efn.agri.harvest"].create(
            {
                "farmer_id": self.farmer.id,
                "commodity_id": self.commodity.id,
                "product_id": self.cabai.id,
                "expected_date": "2026-11-05",
                "expected_qty": qty,
            }
        )
        harvest.action_create_purchase()
        po = harvest.purchase_id
        po.picking_type_id = self.wh.in_type_id
        po.button_confirm()
        receipt = po.picking_ids
        for ml in receipt.move_ids.move_line_ids:
            ml.lot_name = "LOT-CBM-001"
        receipt.move_ids.picked = True
        receipt.button_validate()
        qc = receipt.move_ids.move_dest_ids.picking_id
        self.assertEqual(qc.picking_type_id, self.wh.qc_type_id)
        return po, qc

    def _measure(self, insp, moist, pest=True):
        for line in insp.line_ids:
            if line.param_id.code == "MOIST":
                line.value_num = moist
            elif line.param_id.code == "PEST":
                line.value_pass = pest
                line.value_set = True

    def test_qc_gate_blocks_until_inspection_done(self):
        po, qc = self._receive()
        self.assertTrue(qc.efn_agri_qc_gate)
        qc.action_assign()
        with self.assertRaisesRegex(UserError, "qc_required"):
            qc.button_validate()
        qc.action_efn_agri_create_inspections()
        insp = qc.efn_agri_inspection_ids
        self.assertEqual(insp.farmer_id, self.farmer)
        self.assertEqual(insp.purchase_line_id, po.order_line)
        self.assertEqual(len(insp.line_ids), 3)
        self.assertEqual(str(insp.harvest_date), "2026-11-05")

    def test_grade_a_sets_po_price_and_traceability(self):
        po, qc = self._receive()
        qc.action_efn_agri_create_inspections()
        insp = qc.efn_agri_inspection_ids.with_user(self.inspector)
        insp.qty_reject = 3
        self._measure(insp, 70)
        self.assertEqual(insp.suggested_grade_id, self.grade_a)
        insp.action_done()
        self.assertEqual(insp.state, "done")
        self.assertEqual(insp.result, "pass")
        self.assertEqual(po.order_line.price_unit, 30000)
        self.assertEqual(insp.scrap_id.scrap_qty, 3)
        self.assertEqual(qc.move_ids.product_uom_qty, 97)
        qc.move_ids.picked = True
        qc.button_validate()
        self.assertEqual(qc.state, "done")
        lot = insp.lot_id
        self.assertEqual(lot.name, "LOT-CBM-001")
        payload = self.env["stock.lot"]._efn_trace_payload(lot)
        self.assertEqual(payload["region"], "Garut")
        self.assertEqual(payload["grade"], "A")
        self.assertEqual(payload["farmer_code"], self.farmer.code)
        self.assertNotIn("Poktan", str(payload))
        self.assertTrue(lot._efn_trace_qr_data_uri().startswith("data:image/png;base64,"))
        # Mode test: Odoo merender HTML (bukan wkhtmltopdf); cukup pastikan QR inline ikut di label.
        html, _fmt = self.env["ir.actions.report"]._render_qweb_html("efn_agri.action_report_trace_label", lot.ids)
        self.assertIn(b"data:image/png;base64,", html)

    def test_grade_b_by_reject_rate_and_override_requires_note(self):
        _po, qc = self._receive()
        qc.action_efn_agri_create_inspections()
        insp = qc.efn_agri_inspection_ids.with_user(self.inspector)
        insp.qty_reject = 10
        self._measure(insp, 70)
        self.assertEqual(insp.suggested_grade_id, self.grade_b)
        insp.grade_id = self.grade_a
        with self.assertRaisesRegex(UserError, "override_note"):
            insp.action_done()
        insp.grade_override_note = "Sortir ulang di tempat"
        insp.action_done()
        self.assertEqual(insp.grade_id, self.grade_a)

    def test_blocking_param_fail_rejects_all(self):
        po, qc = self._receive()
        qc.action_efn_agri_create_inspections()
        insp = qc.efn_agri_inspection_ids.with_user(self.inspector)
        self._measure(insp, 70, pest=False)
        insp.grade_id = self.grade_r
        insp.grade_override_note = "hama"
        insp.action_done()
        self.assertEqual(insp.result, "fail")
        self.assertEqual(insp.scrap_id.scrap_qty, 100)
        self.assertEqual(qc.move_ids.state, "cancel")
        self.assertEqual(po.order_line.price_unit, 20000)

    def test_only_inspector_can_finish(self):
        _po, qc = self._receive()
        qc.action_efn_agri_create_inspections()
        staff = new_test_user(self.env, login="efn_qc_staff", groups="efn_agri.group_agri_user")
        insp = qc.efn_agri_inspection_ids.with_user(staff)
        self._measure(insp, 70)
        with self.assertRaisesRegex(UserError, "inspector_only"):
            insp.action_done()

    def test_measurement_required(self):
        _po, qc = self._receive()
        qc.action_efn_agri_create_inspections()
        insp = qc.efn_agri_inspection_ids.with_user(self.inspector)
        with self.assertRaisesRegex(UserError, "measure_missing"):
            insp.action_done()

    def test_grade_price_below_floor_rejected(self):
        with self.assertRaisesRegex(ValidationError, "below_floor"):
            self.grade_b.price = 15000

    def test_farmer_stats(self):
        _po, qc = self._receive()
        qc.action_efn_agri_create_inspections()
        insp = qc.efn_agri_inspection_ids.with_user(self.inspector)
        insp.qty_reject = 4
        self._measure(insp, 70)
        insp.action_done()
        self.farmer.invalidate_recordset()
        self.assertEqual(self.farmer.delivered_qty, 96)
        self.assertAlmostEqual(self.farmer.reject_rate, 4.0)
