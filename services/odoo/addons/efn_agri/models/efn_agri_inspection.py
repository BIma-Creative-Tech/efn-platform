"""Inspeksi QC penerimaan hasil tani (input manual staf collection center, K25-13).

Alur gudang 3 langkah CE: Penerimaan (Vendor -> Input) -> **QC (Input -> Quality Control)** -> Penyimpanan.
Inspeksi menempel pada transfer QC (atau penerimaan bila gudang 2 langkah). Transfer itu TIDAK bisa divalidasi
sebelum inspeksi ``done`` (lihat ``stock.picking.button_validate``):
* qty lolos = qty diterima - qty reject (diukur). Qty reject di-scrap otomatis dengan alasan "Reject QC".
* grade otomatis dari nilai ukur (aturan grade komoditas), boleh diubah inspektur dengan catatan.
* harga baris PO asal disetel ke harga grade (vendor bill mengikuti harga grade).
* lot penerimaan diberi data traceability: petani, asal daerah, tanggal panen, grade, nomor sertifikat QC.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class EfnAgriInspection(models.Model):
    _name = "efn.agri.inspection"
    _description = "EFN inspeksi QC hasil tani"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"
    _check_company_auto = True

    name = fields.Char(default="/", copy=False, readonly=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    picking_id = fields.Many2one(
        "stock.picking", string="Transfer", required=True, check_company=True, ondelete="cascade"
    )
    move_id = fields.Many2one("stock.move", string="Baris", required=True, check_company=True, ondelete="cascade")
    product_id = fields.Many2one(related="move_id.product_id", store=True)
    commodity_id = fields.Many2one("efn.agri.commodity", required=True)
    farmer_id = fields.Many2one("efn.agri.farmer", string="Mitra tani", check_company=True)
    purchase_line_id = fields.Many2one("purchase.order.line", string="Baris PO", readonly=True)
    harvest_date = fields.Date(string="Tanggal panen")
    qty_received = fields.Float(string="Qty diterima", required=True)
    qty_reject = fields.Float(string="Qty reject")
    qty_accepted = fields.Float(string="Qty lolos", compute="_compute_qty_accepted", store=True)
    reject_pct = fields.Float(string="% reject", compute="_compute_qty_accepted", store=True, digits=(5, 1))
    line_ids = fields.One2many("efn.agri.inspection.line", "inspection_id", string="Pengukuran")
    grade_id = fields.Many2one("efn.agri.grade", string="Grade", domain="[('commodity_id', '=', commodity_id)]")
    suggested_grade_id = fields.Many2one("efn.agri.grade", string="Grade otomatis", compute="_compute_suggested_grade")
    grade_override_note = fields.Char(string="Alasan ubah grade")
    price_unit = fields.Monetary(
        string="Harga grade", currency_field="currency_id", compute="_compute_price", store=True
    )
    currency_id = fields.Many2one(related="commodity_id.currency_id")
    result = fields.Selection([("pass", "Lolos"), ("fail", "Ditolak")], compute="_compute_result", store=True)
    inspector_id = fields.Many2one("res.users", default=lambda self: self.env.user, readonly=True)
    inspected_at = fields.Datetime(readonly=True)
    state = fields.Selection(
        [("draft", "Draft"), ("done", "Selesai"), ("cancel", "Batal")], default="draft", tracking=True
    )
    note = fields.Text()
    lot_id = fields.Many2one("stock.lot", string="Lot", readonly=True)
    scrap_id = fields.Many2one("stock.scrap", string="Scrap reject", readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "/") == "/":
                vals["name"] = self.env["ir.sequence"].next_by_code("efn.agri.inspection") or "/"
        records = super().create(vals_list)
        records._efn_init_lines()
        return records

    def _efn_init_lines(self):
        for rec in self.filtered(lambda r: not r.line_ids):
            rec.line_ids = [(0, 0, {"param_id": p.id}) for p in rec.commodity_id.param_ids]

    @api.depends("qty_received", "qty_reject")
    def _compute_qty_accepted(self):
        for rec in self:
            rec.qty_accepted = max(rec.qty_received - rec.qty_reject, 0.0)
            rec.reject_pct = 100.0 * rec.qty_reject / rec.qty_received if rec.qty_received else 0.0

    def _efn_param_value(self, param):
        line = self.line_ids.filtered(lambda line_: line_.param_id == param)[:1]
        if param.code == "REJECT" and line and not line.value_set:
            return self.reject_pct
        return line.value_num if line else 0.0

    @api.depends("line_ids.value_num", "line_ids.value_pass", "qty_reject", "qty_received", "commodity_id")
    def _compute_suggested_grade(self):
        for rec in self:
            grade = rec.env["efn.agri.grade"]
            for candidate in rec.commodity_id.grade_ids.sorted("sequence"):
                if all(rule._efn_ok(rec._efn_param_value(rule.param_id)) for rule in candidate.rule_ids):
                    grade = candidate
                    break
            rec.suggested_grade_id = grade

    @api.depends("grade_id", "grade_id.price")
    def _compute_price(self):
        for rec in self:
            rec.price_unit = rec.grade_id.price

    @api.depends("line_ids.passed", "grade_id", "qty_accepted")
    def _compute_result(self):
        for rec in self:
            blocking_fail = any(not line.passed and line.param_id.blocking for line in rec.line_ids)
            rec.result = "fail" if (blocking_fail or rec.grade_id.is_reject or not rec.qty_accepted) else "pass"

    def action_apply_suggested_grade(self):
        for rec in self:
            rec.grade_id = rec.suggested_grade_id

    def action_done(self):
        self.check_access("write")
        if not self.env.user.has_group("efn_agri.group_agri_inspector"):
            raise UserError(_("efn_agri:inspector_only — hanya Inspektur QC yang bisa menyelesaikan inspeksi."))
        for rec in self:
            if rec.state != "draft":
                continue
            missing = rec.line_ids.filtered(lambda line: not line.value_set and line.param_id.code != "REJECT")
            if missing:
                raise UserError(
                    _(
                        "efn_agri:measure_missing — isi pengukuran: %(names)s.",
                        names=", ".join(missing.param_id.mapped("name")),
                    )
                )
            if not rec.grade_id:
                rec.grade_id = rec.suggested_grade_id
            if not rec.grade_id:
                raise UserError(_("efn_agri:no_grade — tidak ada grade yang cocok; pilih grade (mis. Reject)."))
            if rec.suggested_grade_id and rec.grade_id != rec.suggested_grade_id and not rec.grade_override_note:
                raise UserError(_("efn_agri:override_note — isi alasan karena grade berbeda dari grade otomatis."))
            if rec.qty_reject > rec.qty_received:
                raise UserError(_("efn_agri:reject_qty — qty reject melebihi qty diterima."))
            rec.write({"state": "done", "inspected_at": fields.Datetime.now()})
            rec._efn_apply_to_stock()
            rec.message_post(
                body=_(
                    "QC %(res)s: grade %(grade)s, lolos %(ok)s, reject %(rej)s.",
                    res=dict(rec._fields["result"].selection)[rec.result],
                    grade=rec.grade_id.name,
                    ok=rec.qty_accepted,
                    rej=rec.qty_reject,
                )
            )

    def action_cancel(self):
        self.check_access("write")
        self.filtered(lambda r: r.state == "draft").write({"state": "cancel"})

    def _efn_apply_to_stock(self):
        """Harga PO = harga grade; reject -> di-scrap dari lokasi QC + qty transfer (dan langkah berikutnya) dikurangi;
        lot diberi data traceability. Ditulis server (sudo) setelah cek grup inspektur di ``action_done``."""
        self.ensure_one()
        move = self.move_id.sudo()
        pol = self.purchase_line_id.sudo()
        if pol and not self.grade_id.is_reject and self.result == "pass":
            pol.price_unit = self.commodity_id.uom_id._compute_price(self.price_unit, pol.product_uom_id)
        reject_qty = self.qty_received if self.result == "fail" else self.qty_reject
        lot = move.move_line_ids.lot_id[:1]
        if reject_qty:
            Scrap = self.env["stock.scrap"].sudo().with_context(efn_scrap_reason_optional=True)
            scrap = Scrap.create(
                {
                    "product_id": move.product_id.id,
                    "product_uom_id": move.product_uom.id,
                    "scrap_qty": reject_qty,
                    "location_id": move.location_id.id,
                    "company_id": self.company_id.id,
                    "origin": self.name,
                    "lot_id": lot.id,
                    "scrap_reason_tag_ids": [(6, 0, self.env.ref("efn_agri.scrap_reason_qc_reject").ids)],
                }
            )
            move.move_line_ids.unlink()
            scrap.do_scrap()
            self.sudo().scrap_id = scrap
            remaining = max(move.product_uom_qty - reject_qty, 0.0)
            chain = move | move.move_dest_ids.filtered(lambda m: m.state not in ("done", "cancel"))
            if remaining:
                chain.write({"product_uom_qty": remaining})
                move._action_assign()
                move.quantity = remaining
            else:
                chain._action_cancel()
        if lot:
            lot.sudo().write(
                {
                    "efn_farmer_id": self.farmer_id.id,
                    "efn_harvest_date": self.harvest_date,
                    "efn_grade_id": self.grade_id.id,
                    "efn_inspection_id": self.id,
                }
            )
            self.sudo().lot_id = lot


class EfnAgriInspectionLine(models.Model):
    _name = "efn.agri.inspection.line"
    _description = "EFN pengukuran QC"
    _order = "inspection_id, sequence, id"

    inspection_id = fields.Many2one("efn.agri.inspection", required=True, ondelete="cascade")
    param_id = fields.Many2one("efn.agri.qc.param", required=True)
    sequence = fields.Integer(related="param_id.sequence", store=True)
    kind = fields.Selection(related="param_id.kind")
    unit = fields.Char(related="param_id.unit")
    value_num = fields.Float(string="Nilai")
    value_pass = fields.Boolean(string="Lulus")
    value_set = fields.Boolean(string="Diisi", compute="_compute_value_set", store=True, readonly=False)
    passed = fields.Boolean(string="Memenuhi", compute="_compute_passed", store=True)
    limit_text = fields.Char(string="Batas", compute="_compute_limit_text")

    @api.depends("value_num", "value_pass")
    def _compute_value_set(self):
        for line in self:
            line.value_set = line.value_set or bool(line.value_num) or bool(line.value_pass)

    @api.depends("value_num", "value_pass", "value_set", "param_id")
    def _compute_passed(self):
        for line in self:
            value = line.value_num
            if line.param_id.code == "REJECT" and not line.value_set:
                value = line.inspection_id.reject_pct
            line.passed = line.param_id._efn_check(value, line.value_pass)

    def _compute_limit_text(self):
        for line in self:
            p = line.param_id
            if p.kind == "pass":
                line.limit_text = _("harus lulus")
                continue
            parts = []
            if p.has_min:
                parts.append(f">= {p.min_value:g}")
            if p.has_max:
                parts.append(f"<= {p.max_value:g}")
            line.limit_text = " dan ".join(parts) + (f" {p.unit}" if p.unit else "")
