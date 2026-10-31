"""Penyesuaian konsolidasi manual (G36, W-16).

Jurnal tingkat grup untuk laporan konsolidasi (mis. eliminasi investasi pada anak usaha vs modal, laba belum
terealisasi di persediaan antar-PT, reklasifikasi) yang TIDAK diposting ke buku PT mana pun. Aturan:

* Seimbang (Σ debit = Σ kredit, > 0), minimal 2 baris, setiap baris milik salah satu PT cakupan (``company_ids`` >= 2)
  dan akunnya milik PT baris itu.
* Tidak boleh menyentuh akun kas & setara kas (kas grup tidak berubah karena penyesuaian; arus kas tetap seimbang).
* Empat mata: pembuat (draft) tidak boleh memposting penyesuaiannya sendiri; keduanya Accounting Manager dengan akses
  ke SEMUA PT cakupan. Setelah posted hanya bisa dibatalkan (tidak diedit/dihapus); jejak di chatter.
* Laporan konsolidasi memakai penyesuaian posted bila SEMUA PT-nya ada dalam cakupan laporan dan tanggalnya
  <= tanggal laporan (opsi "Penyesuaian konsolidasi" di wizard, default aktif).
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

LOCKED_FIELDS = {"name", "date", "company_ids", "line_ids"}


class EfnConsolidationAdjustment(models.Model):
    _name = "efn.consolidation.adjustment"
    _description = "EFN penyesuaian konsolidasi"
    _inherit = ["mail.thread"]
    _order = "date desc, id desc"

    name = fields.Char(string="Keterangan", required=True, tracking=True)
    date = fields.Date(string="Tanggal", required=True, default=fields.Date.context_today, tracking=True)
    company_ids = fields.Many2many(
        "res.company",
        string="PT dalam konsolidasi",
        required=True,
        domain=lambda self: [("id", "in", self.env.user.company_ids.ids)],
        help="Penyesuaian dipakai bila SEMUA PT ini ikut dalam laporan konsolidasi.",
    )
    line_ids = fields.One2many("efn.consolidation.adjustment.line", "adjustment_id", string="Baris", copy=True)
    state = fields.Selection(
        [("draft", "Draft"), ("posted", "Posted"), ("cancelled", "Dibatalkan")],
        default="draft",
        required=True,
        readonly=True,
        copy=False,
        tracking=True,
    )
    maker_id = fields.Many2one(
        "res.users", string="Dibuat/diubah oleh", readonly=True, copy=False, default=lambda self: self.env.user
    )
    approved_by_id = fields.Many2one("res.users", string="Diposting oleh", readonly=True, copy=False, tracking=True)
    approved_at = fields.Datetime(string="Diposting pada", readonly=True, copy=False)
    total_debit = fields.Monetary(compute="_compute_totals", currency_field="currency_id")
    total_credit = fields.Monetary(compute="_compute_totals", currency_field="currency_id")
    currency_id = fields.Many2one("res.currency", compute="_compute_currency")

    @api.depends("company_ids")
    def _compute_currency(self):
        idr = self.env.ref("base.IDR", raise_if_not_found=False)
        for rec in self:
            rec.currency_id = rec.company_ids[:1].currency_id or idr

    @api.depends("line_ids.debit", "line_ids.credit")
    def _compute_totals(self):
        for rec in self:
            rec.total_debit = sum(rec.line_ids.mapped("debit"))
            rec.total_credit = sum(rec.line_ids.mapped("credit"))

    # ------------------------------------------------------------------ guard
    def _efn_scope(self):
        """Semua PT cakupan, TANPA saringan record rule user.

        Dibaca sebagai user, many2many ``company_ids`` disaring rule res.company (PT tanpa akses tidak terlihat, juga
        di cache sudo), sehingga cek "akses ke SEMUA PT" selalu lolos. Ambil dari tabel relasi.
        """
        self.ensure_one()
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT res_company_id FROM efn_consolidation_adjustment_res_company_rel"
            " WHERE efn_consolidation_adjustment_id = %s",
            [self.id],
        )
        return self.env["res.company"].sudo().browse([row[0] for row in self.env.cr.fetchall()])

    def _efn_check_manager(self):
        if not self.env.user.has_group("account.group_account_manager"):
            raise UserError(_("efn_account_reports:access — butuh Accounting Manager."))
        for rec in self:
            if rec._efn_scope() - self.env.user.company_ids:
                raise UserError(_("efn_account_reports:company — PT di luar akses Anda."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals.pop("state", None)
            vals.pop("approved_by_id", None)
            vals.pop("approved_at", None)
            vals["maker_id"] = self.env.uid
        records = super().create(vals_list)
        records._efn_check_manager()
        return records

    def write(self, vals):
        protected = {"state", "approved_by_id", "approved_at", "maker_id"} & set(vals)
        if protected and not self.env.context.get("efn_adj_internal"):
            raise UserError(_("efn_account_reports:adjustment — status hanya lewat tombol Posting/Batalkan."))
        if LOCKED_FIELDS & set(vals) and not self.env.context.get("efn_adj_internal"):
            if any(rec.state != "draft" for rec in self):
                raise UserError(_("efn_account_reports:adjustment — hanya draft yang bisa diubah."))
            self._efn_check_manager()
            vals = {**vals, "maker_id": self.env.uid}
        return super().write(vals)

    def unlink(self):
        if any(rec.state != "draft" for rec in self):
            raise UserError(_("efn_account_reports:adjustment — penyesuaian posted/batal tidak bisa dihapus."))
        self._efn_check_manager()
        return super().unlink()

    @api.constrains("company_ids")
    def _check_companies(self):
        for rec in self:
            if len(rec.company_ids) < 2:
                raise ValidationError(_("efn_account_reports:adjustment — minimal 2 PT (penyesuaian konsolidasi)."))

    def _efn_validate_lines(self):
        engine = self.env["efn.financial.report.engine"].sudo()
        for rec in self:
            lines = rec.sudo().line_ids
            if len(lines) < 2:
                raise UserError(_("efn_account_reports:adjustment — minimal 2 baris."))
            debit = round(sum(lines.mapped("debit")), 2)
            credit = round(sum(lines.mapped("credit")), 2)
            if not debit or debit != credit:
                raise UserError(_("efn_account_reports:adjustment — debit dan kredit harus sama dan lebih dari 0."))
            scope = rec._efn_scope()
            cash = engine._efn_cash_account_ids(scope)
            for line in lines.sudo():
                if line.company_id not in scope:
                    raise UserError(_("efn_account_reports:adjustment — PT baris di luar cakupan penyesuaian."))
                if line.company_id not in line.account_id.company_ids:
                    raise UserError(_("efn_account_reports:adjustment — akun bukan milik PT baris."))
                if line.account_id.id in cash or line.account_id.account_type == "off_balance":
                    raise UserError(
                        _("efn_account_reports:adjustment — akun kas/setara kas atau off-balance tidak diizinkan.")
                    )

    # ------------------------------------------------------------------ aksi
    def action_post(self):
        """Posting (empat mata): Accounting Manager lain dari pembuat/penyunting terakhir draft."""
        self._efn_check_manager()
        for rec in self:
            if rec.state != "draft":
                raise UserError(_("efn_account_reports:adjustment — hanya draft yang bisa diposting."))
            if rec.maker_id == self.env.user:
                raise UserError(_("efn_account_reports:four_eyes — pembuat/penyunting tidak boleh memposting sendiri."))
            rec._efn_validate_lines()
            rec.with_context(efn_adj_internal=True).write(
                {"state": "posted", "approved_by_id": self.env.uid, "approved_at": fields.Datetime.now()}
            )
        return True

    def action_cancel(self):
        self._efn_check_manager()
        for rec in self:
            if rec.state != "posted":
                raise UserError(_("efn_account_reports:adjustment — hanya penyesuaian posted yang bisa dibatalkan."))
            rec.with_context(efn_adj_internal=True).write({"state": "cancelled"})
            rec.message_post(body=_("Penyesuaian dibatalkan oleh %s.", self.env.user.name))
        return True


class EfnConsolidationAdjustmentLine(models.Model):
    _name = "efn.consolidation.adjustment.line"
    _description = "EFN baris penyesuaian konsolidasi"
    _order = "adjustment_id, sequence, id"

    adjustment_id = fields.Many2one("efn.consolidation.adjustment", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one("res.company", string="PT", required=True)
    account_id = fields.Many2one(
        "account.account",
        string="Akun",
        required=True,
        domain="[('company_ids', 'in', company_id), ('account_type', 'not in', ('asset_cash', 'off_balance'))]",
    )
    name = fields.Char(string="Label")
    debit = fields.Float(string="Debit", digits=(16, 2), default=0.0)
    credit = fields.Float(string="Kredit", digits=(16, 2), default=0.0)
    state = fields.Selection(related="adjustment_id.state")

    _efn_adj_line_amount = models.Constraint(
        "CHECK (debit >= 0 AND credit >= 0 AND (debit = 0 OR credit = 0))",
        "efn_account_reports:adjustment — debit/kredit tidak boleh negatif atau terisi keduanya.",
    )

    def _efn_guard_draft(self, adjustments):
        if any(adj.state != "draft" for adj in adjustments):
            raise UserError(_("efn_account_reports:adjustment — hanya draft yang bisa diubah."))
        adjustments._efn_check_manager()

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        self._efn_guard_draft(lines.adjustment_id)
        lines.adjustment_id.with_context(efn_adj_internal=True).write({"maker_id": self.env.uid})
        return lines

    def write(self, vals):
        self._efn_guard_draft(self.adjustment_id)
        res = super().write(vals)
        self.adjustment_id.with_context(efn_adj_internal=True).write({"maker_id": self.env.uid})
        return res

    def unlink(self):
        adjustments = self.adjustment_id
        self._efn_guard_draft(adjustments)
        res = super().unlink()
        adjustments.with_context(efn_adj_internal=True).write({"maker_id": self.env.uid})
        return res

    @api.model
    def _efn_report_rows(self, companies, date_from, date_to):
        """[(account, company_id, opening, debit, credit)] baris penyesuaian POSTED yang berlaku untuk cakupan.

        Berlaku bila semua PT penyesuaian ada di ``companies`` dan tanggal <= ``date_to``; sebelum ``date_from`` =
        saldo awal (seperti _efn_lines).
        """
        adjustments = (
            self.env["efn.consolidation.adjustment"]
            .sudo()
            .search([("state", "=", "posted"), ("date", "<=", date_to), ("company_ids", "in", companies.ids)])
            .filtered(lambda a: not (a.company_ids - companies))
        )
        rows = []
        for line in adjustments.line_ids:
            amount = line.debit - line.credit
            if line.adjustment_id.date < date_from:
                rows.append((line.account_id, line.company_id.id, amount, 0.0, 0.0))
            else:
                rows.append((line.account_id, line.company_id.id, 0.0, line.debit, line.credit))
        return rows

    @api.model
    def _efn_report_adjustments(self, companies, date_to):
        return (
            self.env["efn.consolidation.adjustment"]
            .sudo()
            .search([("state", "=", "posted"), ("date", "<=", date_to), ("company_ids", "in", companies.ids)])
            .filtered(lambda a: not (a.company_ids - companies))
        )
