"""QC gate di gudang (3 langkah CE) + traceability lot."""

import base64

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.image import image_data_uri


class StockPickingType(models.Model):
    _inherit = "stock.picking.type"

    efn_agri_qc_gate = fields.Boolean(
        string="Gerbang QC hasil tani",
        help="Transfer jenis ini (mis. Quality Control gudang 3 langkah) wajib punya inspeksi QC selesai untuk setiap "
        "produk komoditas sebelum divalidasi.",
    )


class StockWarehouse(models.Model):
    _inherit = "stock.warehouse"

    efn_agri_collection_center = fields.Boolean(string="Collection center hasil tani")

    def action_efn_agri_enable_qc_gate(self):
        """Ubah penerimaan menjadi 3 langkah (Input -> QC -> Stock) dan tandai tipe QC sebagai gerbang."""
        self.check_access("write")
        for warehouse in self:
            warehouse.write({"reception_steps": "three_steps", "efn_agri_collection_center": True})
            warehouse.qc_type_id.sudo().efn_agri_qc_gate = True
        return True


class StockPicking(models.Model):
    _inherit = "stock.picking"

    efn_agri_qc_gate = fields.Boolean(related="picking_type_id.efn_agri_qc_gate")
    efn_agri_inspection_ids = fields.One2many("efn.agri.inspection", "picking_id", string="Inspeksi QC")
    efn_agri_inspection_count = fields.Integer(compute="_compute_efn_agri_inspection_count")
    efn_agri_qc_pending = fields.Boolean(compute="_compute_efn_agri_inspection_count")

    def _compute_efn_agri_inspection_count(self):
        for picking in self:
            picking.efn_agri_inspection_count = len(picking.efn_agri_inspection_ids)
            picking.efn_agri_qc_pending = bool(picking._efn_agri_missing_moves())

    def _efn_agri_commodity_moves(self):
        Commodity = self.env["efn.agri.commodity"]
        return self.move_ids.filtered(lambda m: m.state != "cancel" and Commodity._efn_for_product(m.product_id))

    def _efn_agri_missing_moves(self):
        self.ensure_one()
        if not self.efn_agri_qc_gate:
            return self.env["stock.move"]
        done = self.efn_agri_inspection_ids.filtered(lambda i: i.state == "done").move_id
        return self._efn_agri_commodity_moves() - done

    def _efn_agri_origin_purchase_line(self, move):
        """Baris PO asal: move QC berasal dari move penerimaan (move_orig_ids) yang punya purchase_line_id."""
        seen, todo = set(), move
        while todo:
            current = todo[0]
            todo = todo[1:]
            if current.id in seen:
                continue
            seen.add(current.id)
            if current.purchase_line_id:
                return current.purchase_line_id
            todo |= current.move_orig_ids
        return self.env["purchase.order.line"]

    def action_efn_agri_create_inspections(self):
        self.check_access("write")
        Commodity = self.env["efn.agri.commodity"]
        Inspection = self.env["efn.agri.inspection"]
        Farmer = self.env["efn.agri.farmer"]
        for picking in self:
            existing = picking.efn_agri_inspection_ids.filtered(lambda i: i.state != "cancel").move_id
            for move in picking._efn_agri_commodity_moves() - existing:
                pol = picking._efn_agri_origin_purchase_line(move)
                partner = pol.order_id.partner_id or picking.partner_id
                harvest = (
                    self.env["efn.agri.harvest"].search([("purchase_id", "=", pol.order_id.id)], limit=1)
                    if pol
                    else False
                )
                Inspection.create(
                    {
                        "company_id": picking.company_id.id,
                        "picking_id": picking.id,
                        "move_id": move.id,
                        "commodity_id": Commodity._efn_for_product(move.product_id).id,
                        "farmer_id": Farmer.search([("partner_id", "=", partner.commercial_partner_id.id)], limit=1).id
                        if partner
                        else False,
                        "purchase_line_id": pol.id,
                        "qty_received": move.product_uom_qty,
                        "harvest_date": harvest.expected_date if harvest else False,
                    }
                )
        return self.action_efn_agri_view_inspections()

    def action_efn_agri_view_inspections(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("efn_agri.efn_agri_inspection_action")
        action["domain"] = [("picking_id", "=", self.id)]
        action["context"] = {"default_picking_id": self.id}
        if len(self.efn_agri_inspection_ids) == 1:
            action.update({"view_mode": "form", "res_id": self.efn_agri_inspection_ids.id, "views": [(False, "form")]})
        return action

    def button_validate(self):
        for picking in self.filtered("efn_agri_qc_gate"):
            missing = picking._efn_agri_missing_moves()
            if missing:
                raise UserError(
                    _(
                        "efn_agri:qc_required — %(pick)s: selesaikan inspeksi QC dulu untuk %(products)s.",
                        pick=picking.name,
                        products=", ".join(missing.product_id.mapped("display_name")),
                    )
                )
        return super().button_validate()


class StockLot(models.Model):
    _inherit = "stock.lot"

    efn_farmer_id = fields.Many2one("efn.agri.farmer", string="Mitra tani", readonly=True)
    efn_farmer_region = fields.Char(related="efn_farmer_id.region", string="Asal daerah")
    efn_harvest_date = fields.Date(string="Tanggal panen", readonly=True)
    efn_grade_id = fields.Many2one("efn.agri.grade", string="Grade", readonly=True)
    efn_inspection_id = fields.Many2one("efn.agri.inspection", string="Sertifikat QC", readonly=True)

    @api.model
    def _efn_trace_payload(self, lot):
        """Data traceability publik (tanpa PII petani: hanya kode mitra & daerah)."""
        return {
            "lot": lot.name,
            "product": lot.product_id.display_name,
            "farmer_code": lot.efn_farmer_id.code or None,
            "region": lot.efn_farmer_region or None,
            "harvest_date": lot.efn_harvest_date and lot.efn_harvest_date.isoformat(),
            "grade": lot.efn_grade_id.name or None,
            "qc_certificate": lot.efn_inspection_id.name or None,
            "qc_date": lot.efn_inspection_id.inspected_at and lot.efn_inspection_id.inspected_at.date().isoformat(),
        }

    def _efn_trace_qr_data_uri(self):
        """QR label traceability sebagai data URI PNG (dirender di server; tanpa request HTTP dari wkhtmltopdf)."""
        self.ensure_one()
        png = self.env["ir.actions.report"].barcode("QR", f"EFN-TRACE:{self.name}", width=240, height=240)
        return image_data_uri(base64.b64encode(png))
