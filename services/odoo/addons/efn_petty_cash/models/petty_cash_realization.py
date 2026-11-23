"""Realisasi (pertanggungjawaban) uang muka atau belanja kas kecil outlet.

Port dari odoo-platform `custom_petty_cash/models/petty_cash_realization.py` (ADR-049). Perubahan:
- realisasi terhadap **pengajuan** (uang muka) **atau fund** (kas kecil outlet) — tepat satu (baru, §2.1);
- posting oleh Finance != pembuat/penyunting/pemegang (empat mata, K-PC2); posting hanya dari status Diajukan;
- tagihan pihak ketiga dilunasi dari uang muka dengan entri jurnal umum (Dr Utang Usaha / Cr Uang Muka) yang
  direkonsiliasi ke tagihan, pengganti `account.payment.register` sumber yang menulis ulang akun outstanding
  metode pembayaran jurnal (gotcha "jurnal dedicated" sumber hilang);
- PPh (`x_custom_withholding_category_id`) dibuang (G59/G60 nanti meng-extend tagihan vendor).
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.fields import Command

from .common import GROUP_FINANCE, SYSTEM_CTX, is_recipient, is_system_write

_logger = logging.getLogger(__name__)

NEW_NAME = "Baru"
SYSTEM_FIELDS = frozenset(
    {"state", "maker_id", "last_editor_id", "editor_ids", "posted_by_id", "posted_at", "replenish_request_id"}
)
CONTENT_FIELDS = frozenset({"request_id", "fund_id", "date", "line_ids"})


class EfnPettyCashRealization(models.Model):
    _name = "efn.petty.cash.realization"
    _description = "EFN Realisasi Kas Kecil / Uang Muka"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, id desc"
    _check_company_auto = True

    name = fields.Char(string="Nomor", required=True, default=NEW_NAME, copy=False, tracking=True)
    request_id = fields.Many2one(
        "efn.petty.cash.request",
        string="Pengajuan Uang Muka",
        ondelete="restrict",
        index=True,
        tracking=True,
        check_company=True,
        domain="[('state', 'in', ('disbursed', 'in_realization')), ('fund_id', '=', False)]",
    )
    fund_id = fields.Many2one(
        "efn.petty.cash.fund",
        string="Dana Kas Kecil",
        ondelete="restrict",
        index=True,
        tracking=True,
        check_company=True,
        domain="[('state', '=', 'active')]",
    )
    company_id = fields.Many2one(
        "res.company",
        compute="_compute_owner_fields",
        store=True,
        precompute=True,
        index=True,
        readonly=True,
    )
    currency_id = fields.Many2one("res.currency", compute="_compute_owner_fields", store=True, precompute=True)
    advance_type_id = fields.Many2one(
        "efn.petty.cash.type",
        string="Jenis",
        compute="_compute_owner_fields",
        store=True,
        precompute=True,
    )
    holder_partner_id = fields.Many2one(
        "res.partner",
        string="Pemegang",
        compute="_compute_owner_fields",
        store=True,
        precompute=True,
    )
    holder_user_id = fields.Many2one(
        "res.users",
        string="User Pemegang",
        compute="_compute_owner_fields",
        store=True,
        precompute=True,
        index=True,
    )
    date = fields.Date(string="Tanggal", default=fields.Date.context_today, required=True, tracking=True)
    line_ids = fields.One2many("efn.petty.cash.realization.line", "realization_id", string="Baris")
    amount_total = fields.Monetary(
        string="Total",
        currency_field="currency_id",
        compute="_compute_amount_total",
        store=True,
    )
    state = fields.Selection(
        [("draft", "Draft"), ("submitted", "Diajukan"), ("posted", "Diposting"), ("cancelled", "Dibatalkan")],
        default="draft",
        required=True,
        tracking=True,
        copy=False,
        readonly=True,
    )
    bill_ids = fields.One2many("account.move", "efn_petty_cash_realization_id", string="Entri Jurnal")
    bill_count = fields.Integer(compute="_compute_bill_count", compute_sudo=True)
    replenish_request_id = fields.Many2one(
        "efn.petty.cash.request",
        string="Diisi Ulang oleh",
        readonly=True,
        copy=False,
        index=True,
    )

    maker_id = fields.Many2one("res.users", string="Pembuat", readonly=True, copy=False)
    last_editor_id = fields.Many2one("res.users", string="Penyunting Terakhir", readonly=True, copy=False)
    editor_ids = fields.Many2many(
        "res.users",
        "efn_petty_cash_realization_editor_rel",
        "realization_id",
        "user_id",
        string="Penyunting",
        readonly=True,
        copy=False,
    )
    posted_by_id = fields.Many2one("res.users", string="Diposting oleh", readonly=True, copy=False)
    posted_at = fields.Datetime(string="Diposting pada", readonly=True, copy=False)

    _owner_check = models.Constraint(
        "CHECK((request_id IS NULL) != (fund_id IS NULL))",
        "Realisasi harus terikat ke tepat satu: pengajuan uang muka atau dana kas kecil.",
    )

    # ------------------------------------------------------------------ compute
    @api.depends("request_id", "fund_id")
    def _compute_owner_fields(self):
        for rec in self:
            owner = rec.request_id or rec.fund_id
            rec.company_id = owner.company_id if owner else self.env.company
            rec.currency_id = rec.request_id.currency_id if rec.request_id else rec.company_id.currency_id
            rec.advance_type_id = rec.request_id.advance_type_id if rec.request_id else rec.fund_id.type_id
            rec.holder_partner_id = owner.holder_partner_id if owner else False
            rec.holder_user_id = owner.holder_user_id if owner else False

    @api.depends("line_ids.price_total")
    def _compute_amount_total(self):
        for rec in self:
            rec.amount_total = sum(rec.line_ids.mapped("price_total"))

    def _compute_bill_count(self):
        for rec in self:
            rec.bill_count = len(rec.bill_ids)

    def _efn_owner(self):
        self.ensure_one()
        return self.request_id or self.fund_id

    def _efn_system_write(self, vals):
        return self.sudo().with_context(**{SYSTEM_CTX: True}).write(vals)

    # ------------------------------------------------------------------ CRUD
    @api.model_create_multi
    def create(self, vals_list):
        system = is_system_write(self.env)
        Sequence = self.env["ir.sequence"].sudo()
        for vals in vals_list:
            if not system:
                for fname in SYSTEM_FIELDS:
                    vals.pop(fname, None)
            vals["maker_id"] = self.env.uid
            vals["last_editor_id"] = self.env.uid
            vals["editor_ids"] = [Command.set([self.env.uid])]
            if not vals.get("name") or vals["name"] == NEW_NAME:
                vals["name"] = Sequence.next_by_code("efn.petty.cash.realization") or NEW_NAME
        return super().create(vals_list)

    def write(self, vals):
        if not is_system_write(self.env):
            touched = set(vals)
            if touched & SYSTEM_FIELDS:
                raise AccessError(_("Status dan jejak posting realisasi hanya diubah lewat tombol alur kerja."))
            if touched & CONTENT_FIELDS:
                if any(rec.state != "draft" for rec in self):
                    raise UserError(_("Realisasi hanya dapat diubah saat draft."))
                vals = {**vals, "last_editor_id": self.env.uid, "editor_ids": [Command.link(self.env.uid)]}
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_not_draft(self):
        if any(rec.state not in ("draft", "cancelled") for rec in self):
            raise UserError(_("Hanya realisasi draft/dibatalkan yang dapat dihapus."))

    def _efn_mark_editor(self):
        """Dipanggil baris realisasi: perubahan baris = penyuntingan realisasi (empat mata)."""
        if is_system_write(self.env):
            return
        if any(rec.state != "draft" for rec in self):
            raise UserError(_("Baris realisasi hanya dapat diubah saat draft."))
        self._efn_system_write({"last_editor_id": self.env.uid, "editor_ids": [Command.link(self.env.uid)]})

    # ------------------------------------------------------------------ validasi
    def _efn_check_ready(self):
        """Validasi isi sebelum diajukan/diposting (pemilik terbuka, baris, batas fund)."""
        self.ensure_one()
        if not self.line_ids:
            raise UserError(_("Tambahkan minimal satu baris."))
        if self.request_id and self.request_id.state not in ("disbursed", "in_realization"):
            raise UserError(_("Pengajuan %s belum dicairkan atau sudah selesai.", self.request_id.name))
        third_party = self.line_ids.filtered(lambda ln: ln.line_type == "third_party")
        if third_party and not self.advance_type_id.allow_third_party:
            raise UserError(_("Jenis %s tidak mengizinkan realisasi pihak ketiga.", self.advance_type_id.name))
        fund = self.fund_id
        if fund:
            if fund.state != "active":
                raise UserError(_("Dana kas kecil %s sudah ditutup.", fund.name))
            limit = fund.limit_per_transaction
            over = self.line_ids.filtered(
                lambda ln: limit and fund.currency_id.compare_amounts(ln.price_total, limit) > 0
            )
            if over:
                raise UserError(
                    _(
                        "Baris melebihi batas per transaksi fund (%(limit)s): %(lines)s.",
                        limit=limit,
                        lines=", ".join(over.mapped("name")),
                    )
                )

    # ------------------------------------------------------------------ alur
    def action_submit(self):
        self.check_access("write")
        for rec in self:
            if rec.state != "draft":
                raise UserError(_("Hanya realisasi draft yang dapat diajukan."))
            rec._efn_check_ready()
            rec._efn_system_write({"state": "submitted"})
            rec.message_post(body=_("Diajukan untuk diposting Finance."), subtype_xmlid="mail.mt_note")
        return True

    def _efn_check_poster(self):
        """Grup Finance + record rule + company user + pemosting != pembuat/penyunting/pemegang (empat mata)."""
        user = self.env.user
        if not user.has_group(GROUP_FINANCE):
            raise AccessError(_("Hanya Kas Kecil / Finance yang dapat memposting realisasi."))
        self.check_access("write")
        for rec in self:
            if rec.company_id not in user.company_ids:
                raise AccessError(_("Realisasi milik company lain."))
            trail = rec.sudo()
            if user in (trail.maker_id | trail.last_editor_id | trail.editor_ids | trail.holder_user_id) or (
                is_recipient(user, trail.holder_partner_id)
            ):
                raise UserError(_("Empat mata: pembuat, penyunting, atau pemegang tidak boleh memposting sendiri."))

    def action_post(self):
        self.ensure_one()
        self._efn_check_poster()
        if self.state != "submitted":
            raise UserError(_("Hanya realisasi berstatus Diajukan yang dapat diposting."))
        self._efn_check_ready()
        owner = self._efn_owner()
        fund = self.fund_id
        if fund and self.currency_id.compare_amounts(self.amount_total, fund.balance) > 0:
            raise UserError(
                _(
                    "Total realisasi (%(total)s) melebihi saldo kas kecil %(fund)s (%(balance)s).",
                    total=self.amount_total,
                    fund=fund.name,
                    balance=fund.balance,
                )
            )
        # Akun & jurnal diperiksa sebelum entri apa pun dibuat (fail-closed, tanpa entri setengah jadi).
        owner._efn_advance_account()
        third_party = self.line_ids.filtered(lambda ln: ln.line_type == "third_party")
        expense = self.line_ids.filtered(lambda ln: ln.line_type == "expense")
        if third_party:
            self.advance_type_id._efn_payment_journal()
        if expense:
            self.advance_type_id._efn_expense_journal()
        for partner in third_party.mapped("partner_id"):
            lines = third_party.filtered(lambda ln, p=partner: ln.partner_id == p)
            self._efn_post_vendor_bill(owner, partner, lines)
        if expense:
            self._efn_post_expense_entry(owner, expense)
        now = fields.Datetime.now()
        self._efn_system_write({"state": "posted", "posted_by_id": self.env.uid, "posted_at": now})
        if self.request_id:
            self.request_id._efn_on_realization_posted()
        self.message_post(body=_("Realisasi diposting."), subtype_xmlid="mail.mt_note")
        _logger.info("EFN petty cash realization %s diposting uid=%s", self.id, self.env.uid)
        return True

    def action_cancel(self):
        self.check_access("write")
        for rec in self:
            if rec.state == "posted":
                raise UserError(_("Realisasi yang sudah diposting tidak dapat dibatalkan; balik entri jurnalnya."))
            rec._efn_system_write({"state": "cancelled"})
        return True

    def action_reset_to_draft(self):
        self.check_access("write")
        for rec in self:
            if rec.state not in ("submitted", "cancelled"):
                raise UserError(_("Hanya realisasi diajukan/dibatalkan yang dapat dikembalikan ke draft."))
            rec._efn_system_write({"state": "draft"})
        return True

    # ------------------------------------------------------------------ akuntansi
    def _efn_tags(self, owner):
        return {**owner._efn_move_tags(), "efn_petty_cash_realization_id": self.id}

    def _efn_post_vendor_bill(self, owner, partner, lines):
        """Tagihan vendor per pemasok (PPN masukan lewat tax_ids), lalu dilunasi dari uang muka."""
        self.ensure_one()
        analytic = owner._efn_analytic()
        invoice_lines = []
        for line in lines:
            vals = {
                "name": line.name or self.name,
                "product_id": line.product_id.id or False,
                "account_id": line.account_id.id,
                "quantity": 1.0,
                "price_unit": line.price_subtotal,
                "tax_ids": [Command.set(line.tax_ids.ids)],
            }
            if analytic:
                vals["analytic_distribution"] = analytic
            invoice_lines.append(Command.create(vals))
        move_vals = {
            "move_type": "in_invoice",
            "partner_id": partner.id,
            "invoice_date": self.date,
            "date": self.date,
            "ref": self.name,
            **self._efn_tags(owner),
            "invoice_line_ids": invoice_lines,
        }
        if self.currency_id != self.company_id.currency_id:
            move_vals["currency_id"] = self.currency_id.id
        bill = self.env["account.move"].with_company(self.company_id).create(move_vals)
        bill.action_post()
        self._efn_pay_bill_from_advance(owner, bill)
        return bill

    def _efn_pay_bill_from_advance(self, owner, bill):
        """Dr Utang Usaha (vendor) / Cr Uang Muka (pemegang), lalu rekonsiliasi dengan baris utang tagihan."""
        self.ensure_one()
        payable = bill.line_ids.filtered(lambda ln: ln.display_type == "payment_term")
        payable_account = payable[:1].account_id
        amount_comp = -sum(payable.mapped("balance"))
        amount_cur = -sum(payable.mapped("amount_currency"))
        advance = owner._efn_advance_account()
        journal = self.advance_type_id._efn_payment_journal()
        label = _("Pelunasan %(bill)s dari uang muka — %(name)s", bill=bill.name, name=self.name)
        currency = bill.currency_id
        move = (
            self.env["account.move"]
            .with_company(self.company_id)
            .create(
                {
                    "move_type": "entry",
                    "journal_id": journal.id,
                    "date": self.date,
                    "ref": self.name,
                    **self._efn_tags(owner),
                    "line_ids": [
                        Command.create(
                            {
                                "name": label,
                                "account_id": payable_account.id,
                                "partner_id": bill.commercial_partner_id.id,
                                "currency_id": currency.id,
                                "amount_currency": amount_cur,
                                "balance": amount_comp,
                            }
                        ),
                        Command.create(
                            {
                                "name": label,
                                "account_id": advance.id,
                                "partner_id": owner._efn_holder_partner().id,
                                "currency_id": currency.id,
                                "amount_currency": -amount_cur,
                                "balance": -amount_comp,
                            }
                        ),
                    ],
                }
            )
        )
        move.action_post()
        counterpart = move.line_ids.filtered(lambda ln: ln.account_id == payable_account)
        (payable | counterpart).reconcile()
        return move

    def _efn_post_expense_entry(self, owner, lines):
        """Satu entri: Dr Beban (per baris, termasuk pajak non-kreditabel) / Cr Uang Muka."""
        self.ensure_one()
        advance = owner._efn_advance_account()
        partner = owner._efn_holder_partner()
        journal = self.advance_type_id._efn_expense_journal()
        analytic = owner._efn_analytic()
        move_lines = []
        total_cur = total_comp = 0.0
        for line in lines:
            amount = line.price_total
            amount_comp = owner._efn_conv(amount, self.date)
            total_cur += amount
            total_comp += amount_comp
            vals = {
                "name": line.name or self.name,
                "account_id": line.account_id.id,
                "partner_id": partner.id,
                **owner._efn_leg(amount, amount_comp),
            }
            if analytic:
                vals["analytic_distribution"] = analytic
            move_lines.append(Command.create(vals))
        # Kredit = jumlah konversi per baris (bukan konversi ulang total) agar entri tetap seimbang.
        move_lines.append(
            Command.create(
                {
                    "name": _("Realisasi uang muka — %s", self.name),
                    "account_id": advance.id,
                    "partner_id": partner.id,
                    **owner._efn_leg(-total_cur, -total_comp),
                }
            )
        )
        move = (
            self.env["account.move"]
            .with_company(self.company_id)
            .create(
                {
                    "move_type": "entry",
                    "journal_id": journal.id,
                    "date": self.date,
                    "ref": self.name,
                    **self._efn_tags(owner),
                    "line_ids": move_lines,
                }
            )
        )
        move.action_post()
        return move

    def action_view_bills(self):
        self.ensure_one()
        self.check_access("read")
        return {
            "type": "ir.actions.act_window",
            "name": _("Entri Jurnal"),
            "res_model": "account.move",
            "domain": [("efn_petty_cash_realization_id", "=", self.id)],
            "view_mode": "list,form",
        }
