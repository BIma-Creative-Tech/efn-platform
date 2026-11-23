"""Pengajuan uang muka / pengisian kas kecil — pengajuan, persetujuan empat mata, pencairan, settle.

Port dari odoo-platform `custom_petty_cash/models/petty_cash_request.py` (ADR-049). Perubahan utama:
- `hr.employee` -> `holder_partner_id` + `holder_user_id` (K-PC1); analitik OU/karyawan dibuang (K-PC6);
- `custom_approval_engine` -> empat mata (pola efn_loyalty_accounting): pembuat/penyunting/pemegang != penyetuju,
  pencair != pembuat/pemegang (K-PC2). Status & jejak persetujuan hanya ditulis tombol (sudo + konteks sistem);
- `fund_id`: pengajuan dana awal / pengisian ulang fund imprest (baru, §2.1).

Jurnal (akun uang muka dari jenis; partner = pemegang)::

    Cair        Dr Uang Muka / Cr Bank
    Realisasi   Dr Beban / Cr Uang Muka; pihak ketiga: tagihan vendor lalu Dr Utang Usaha / Cr Uang Muka
    Kembali     Dr Bank / Cr Uang Muka
    Kurang      Dr Uang Muka / Cr Bank
    Settle      baris uang muka bersaldo nol direkonsiliasi
"""

import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.fields import Command

from .common import (
    GROUP_APPROVER,
    GROUP_FINANCE,
    GROUP_LIMIT_OVERRIDE,
    SYSTEM_CTX,
    convert,
    is_recipient,
    is_system_write,
    move_leg,
)

_logger = logging.getLogger(__name__)

NEW_NAME = "Baru"
OPEN_STATES = ("approved", "disbursed", "in_realization")
# Hanya ditulis tombol (sudo + SYSTEM_CTX).
SYSTEM_FIELDS = frozenset(
    {
        "state",
        "maker_id",
        "last_editor_id",
        "editor_ids",
        "approved_by_id",
        "approved_at",
        "disbursed_by_id",
        "disburse_move_id",
    }
)
# Isi pengajuan: hanya boleh diubah saat draft; setiap perubahan mencatat penyunting (empat mata).
CONTENT_FIELDS = frozenset(
    {
        "holder_partner_id",
        "holder_user_id",
        "company_id",
        "currency_id",
        "advance_type_id",
        "fund_id",
        "is_replenishment",
        "request_date",
        "purpose",
        "amount_requested",
        "line_ids",
        "advance_account_id",
        "bank_journal_id",
    }
)


class EfnPettyCashRequest(models.Model):
    _name = "efn.petty.cash.request"
    _description = "EFN Pengajuan Uang Muka / Kas Kecil"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "request_date desc, id desc"
    _check_company_auto = True

    name = fields.Char(string="Nomor", required=True, default=NEW_NAME, copy=False, tracking=True)
    holder_partner_id = fields.Many2one(
        "res.partner",
        string="Pemegang",
        required=True,
        tracking=True,
        index=True,
        default=lambda self: self.env.user.partner_id,
        help="Kontak penerima uang muka (partner di baris jurnal akun uang muka).",
    )
    holder_user_id = fields.Many2one(
        "res.users",
        string="User Pemegang",
        tracking=True,
        index=True,
        default=lambda self: self.env.user,
        help="Opsional (staf tanpa user Odoo, Q-PC5). Menentukan siapa yang melihat pengajuan ini.",
    )
    company_id = fields.Many2one("res.company", required=True, index=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(
        "res.currency", string="Mata Uang", required=True, default=lambda self: self.env.company.currency_id
    )
    company_currency_id = fields.Many2one(related="company_id.currency_id", string="Mata Uang Company")
    advance_type_id = fields.Many2one(
        "efn.petty.cash.type",
        string="Jenis",
        required=True,
        index=True,
        tracking=True,
        check_company=True,
        default=lambda self: self._efn_default_type(),
    )
    advance_type_kind = fields.Selection(related="advance_type_id.kind", store=True, string="Jenis (kelompok)")
    fund_id = fields.Many2one(
        "efn.petty.cash.fund",
        string="Dana Kas Kecil",
        index=True,
        tracking=True,
        check_company=True,
        ondelete="restrict",
        help="Diisi untuk dana awal / pengisian ulang fund imprest outlet.",
    )
    is_replenishment = fields.Boolean(string="Pengisian Ulang", readonly=True, copy=False)
    replenish_realization_ids = fields.One2many(
        "efn.petty.cash.realization", "replenish_request_id", string="Realisasi yang Diisi Ulang"
    )
    request_date = fields.Date(string="Tanggal", default=fields.Date.context_today, required=True, tracking=True)
    purpose = fields.Char(string="Keperluan", tracking=True)
    line_ids = fields.One2many("efn.petty.cash.request.line", "request_id", string="Rincian Estimasi")
    amount_requested = fields.Monetary(string="Jumlah Diajukan", currency_field="currency_id", tracking=True)
    realization_deadline = fields.Date(string="Tenggat Realisasi", tracking=True)
    bank_journal_id = fields.Many2one(
        "account.journal",
        string="Jurnal Bank Keluar (override)",
        check_company=True,
        domain="[('type', 'in', ('bank', 'cash'))]",
    )
    advance_account_id = fields.Many2one(
        "account.account",
        string="Akun Uang Muka (override)",
        check_company=True,
        domain="[('reconcile', '=', True)]",
    )

    disburse_move_id = fields.Many2one("account.move", string="Entri Pencairan", copy=False, readonly=True)
    move_ids = fields.One2many("account.move", "efn_petty_cash_request_id", string="Entri Jurnal")
    move_count = fields.Integer(compute="_compute_move_count", compute_sudo=True)
    realization_ids = fields.One2many("efn.petty.cash.realization", "request_id", string="Realisasi")
    realization_count = fields.Integer(compute="_compute_realization_count", compute_sudo=True)

    amount_disbursed = fields.Monetary(currency_field="currency_id", compute="_compute_amounts", store=True)
    amount_realized = fields.Monetary(currency_field="currency_id", compute="_compute_amounts", store=True)
    amount_outstanding = fields.Monetary(
        string="Outstanding",
        currency_field="currency_id",
        compute="_compute_amounts",
        store=True,
        help="Saldo akun uang muka untuk pengajuan ini (positif = pemegang masih memegang uang).",
    )
    amount_outstanding_company = fields.Monetary(
        string="Outstanding (mata uang company)",
        currency_field="company_currency_id",
        compute="_compute_amounts",
        store=True,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("to_approve", "Menunggu Persetujuan"),
            ("approved", "Disetujui"),
            ("disbursed", "Dicairkan"),
            ("in_realization", "Dalam Realisasi"),
            ("settled", "Selesai"),
            ("cancelled", "Dibatalkan"),
        ],
        default="draft",
        required=True,
        tracking=True,
        copy=False,
        readonly=True,
    )
    is_overdue = fields.Boolean(string="Lewat Tenggat", compute="_compute_is_overdue", search="_search_is_overdue")

    # Jejak empat mata (system fields).
    maker_id = fields.Many2one("res.users", string="Pembuat", readonly=True, copy=False)
    last_editor_id = fields.Many2one("res.users", string="Penyunting Terakhir", readonly=True, copy=False)
    editor_ids = fields.Many2many(
        "res.users",
        "efn_petty_cash_request_editor_rel",
        "request_id",
        "user_id",
        string="Penyunting",
        readonly=True,
        copy=False,
    )
    approved_by_id = fields.Many2one("res.users", string="Disetujui oleh", readonly=True, copy=False)
    approved_at = fields.Datetime(string="Disetujui pada", readonly=True, copy=False)
    disbursed_by_id = fields.Many2one("res.users", string="Dicairkan oleh", readonly=True, copy=False)

    # ------------------------------------------------------------------ compute
    def _compute_move_count(self):
        for rec in self:
            rec.move_count = len(rec.move_ids)

    def _compute_realization_count(self):
        for rec in self:
            rec.realization_count = len(rec.realization_ids)

    @api.depends(
        "move_ids.state",
        "move_ids.line_ids.balance",
        "move_ids.line_ids.amount_currency",
        "advance_account_id",
        "advance_type_id.advance_account_id",
        "currency_id",
        "realization_ids.state",
        "realization_ids.amount_total",
    )
    def _compute_amounts(self):
        for rec in self:
            lines = rec._efn_advance_lines()
            balance = sum(lines.mapped("balance"))
            rec.amount_outstanding_company = balance
            if rec.currency_id == rec.company_id.currency_id:
                rec.amount_disbursed = sum(lines.filtered(lambda line: line.balance > 0).mapped("balance"))
                rec.amount_outstanding = balance
            else:
                same = lines.filtered(lambda line, cur=rec.currency_id: line.currency_id == cur)
                rec.amount_disbursed = sum(line.amount_currency for line in same if line.amount_currency > 0)
                rec.amount_outstanding = sum(same.mapped("amount_currency"))
            posted = rec.realization_ids.filtered(lambda r: r.state == "posted")
            rec.amount_realized = sum(posted.mapped("amount_total"))

    def _compute_is_overdue(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.is_overdue = bool(
                not rec.fund_id
                and rec.state in ("disbursed", "in_realization")
                and rec.realization_deadline
                and rec.realization_deadline < today
                and not rec.currency_id.is_zero(rec.amount_outstanding)
            )

    def _search_is_overdue(self, operator, value):
        if operator not in ("=", "!="):
            return NotImplemented
        domain = [
            ("fund_id", "=", False),
            ("state", "in", ("disbursed", "in_realization")),
            ("realization_deadline", "<", fields.Date.context_today(self)),
        ]
        if (operator == "=") == bool(value):
            return domain
        return ["!", "&", "&", *domain]

    # ------------------------------------------------------------------ helper
    @api.model
    def _efn_default_type(self):
        Type = self.env["efn.petty.cash.type"]
        company = self.env.company
        default = Type.search([("company_id", "=", company.id), ("is_default", "=", True)], limit=1)
        return default or Type.search([("company_id", "=", company.id), ("kind", "!=", "petty_cash")], limit=1)

    def _efn_advance_account(self, soft=False):
        self.ensure_one()
        if soft:
            return self.advance_account_id or self.advance_type_id.advance_account_id
        return self.advance_type_id._efn_advance_account(self.advance_account_id)

    def _efn_advance_lines(self):
        """Baris jurnal posted pada akun uang muka yang terikat ke pengajuan ini."""
        self.ensure_one()
        advance = self._efn_advance_account(soft=True)
        if not advance:
            return self.env["account.move.line"]
        return self.move_ids.line_ids.filtered(lambda ln: ln.account_id == advance and ln.parent_state == "posted")

    def _efn_conv(self, amount, date=None):
        self.ensure_one()
        return convert(self.currency_id, self.company_id, amount, date or fields.Date.context_today(self))

    def _efn_leg(self, amount_cur, amount_comp):
        self.ensure_one()
        return move_leg(self.currency_id, amount_cur, amount_comp)

    def _efn_holder_partner(self):
        self.ensure_one()
        return self.holder_partner_id

    def _efn_analytic(self):
        self.ensure_one()
        return dict(self.fund_id.analytic_distribution or {})

    def _efn_move_tags(self):
        self.ensure_one()
        return {"efn_petty_cash_request_id": self.id, "efn_petty_cash_fund_id": self.fund_id.id or False}

    def _efn_system_write(self, vals):
        return self.sudo().with_context(**{SYSTEM_CTX: True}).write(vals)

    def _efn_money(self, amount):
        currency = self.company_id.currency_id
        return f"{currency.symbol or currency.name} {currency.round(amount):,.2f}"

    # ------------------------------------------------------------------ CRUD
    @api.model_create_multi
    def create(self, vals_list):
        system = is_system_write(self.env)
        Fund = self.env["efn.petty.cash.fund"]
        Type = self.env["efn.petty.cash.type"].sudo()
        for vals in vals_list:
            if not system:
                for fname in SYSTEM_FIELDS:
                    vals.pop(fname, None)
                vals.pop("is_replenishment", None)
            if vals.get("fund_id"):
                fund = Fund.browse(vals["fund_id"])
                vals.update(
                    {
                        "company_id": fund.company_id.id,
                        "advance_type_id": fund.type_id.id,
                        "holder_partner_id": fund.holder_partner_id.id,
                        "holder_user_id": fund.holder_user_id.id or False,
                        "currency_id": fund.company_id.currency_id.id,
                    }
                )
            vals["maker_id"] = self.env.uid
            vals["last_editor_id"] = self.env.uid
            vals["editor_ids"] = [Command.set([self.env.uid])]
            if not vals.get("name") or vals["name"] == NEW_NAME:
                type_id = vals.get("advance_type_id")
                sequence = Type.browse(type_id).sequence_id if type_id else None
                if sequence:
                    vals["name"] = sequence.next_by_id()
                else:
                    vals["name"] = self.env["ir.sequence"].sudo().next_by_code("efn.petty.cash.request") or NEW_NAME
        return super().create(vals_list)

    def write(self, vals):
        if not is_system_write(self.env):
            touched = set(vals)
            if touched & SYSTEM_FIELDS:
                raise AccessError(_("Status dan jejak persetujuan hanya diubah lewat tombol alur kerja."))
            if touched & CONTENT_FIELDS:
                if any(rec.state != "draft" for rec in self):
                    raise UserError(_("Pengajuan hanya dapat diubah saat draft."))
                if "fund_id" in vals and any(rec.fund_id.id != vals["fund_id"] for rec in self):
                    raise UserError(_("Dana kas kecil pengajuan tidak dapat dipindah; buat pengajuan baru."))
                vals = {**vals, "last_editor_id": self.env.uid, "editor_ids": [Command.link(self.env.uid)]}
        return super().write(vals)

    @api.onchange("fund_id")
    def _onchange_fund_id(self):
        for rec in self.filtered("fund_id"):
            rec.advance_type_id = rec.fund_id.type_id
            rec.holder_partner_id = rec.fund_id.holder_partner_id
            rec.holder_user_id = rec.fund_id.holder_user_id
            rec.currency_id = rec.fund_id.company_id.currency_id

    @api.ondelete(at_uninstall=False)
    def _unlink_except_not_draft(self):
        if any(rec.state not in ("draft", "cancelled") or rec.sudo().move_ids for rec in self):
            raise UserError(_("Hanya pengajuan draft/dibatalkan tanpa entri jurnal yang dapat dihapus."))

    def _efn_mark_editor(self):
        """Dipanggil baris estimasi: perubahan rincian = penyuntingan pengajuan (empat mata)."""
        if is_system_write(self.env):
            return
        if any(rec.state != "draft" for rec in self):
            raise UserError(_("Rincian estimasi hanya dapat diubah saat pengajuan draft."))
        self._efn_system_write({"last_editor_id": self.env.uid, "editor_ids": [Command.link(self.env.uid)]})

    @api.constrains("line_ids", "amount_requested")
    def _check_breakdown_total(self):
        for rec in self.filtered("line_ids"):
            total = sum(rec.line_ids.mapped("amount"))
            if not rec.currency_id.is_zero(total - rec.amount_requested):
                raise UserError(
                    _(
                        "Rincian estimasi (%(bd)s) tidak sama dengan jumlah diajukan (%(req)s).",
                        bd=total,
                        req=rec.amount_requested,
                    )
                )

    @api.constrains("fund_id", "advance_type_id", "currency_id")
    def _check_fund_consistency(self):
        for rec in self:
            if rec.fund_id:
                if rec.advance_type_id != rec.fund_id.type_id or rec.currency_id != rec.company_id.currency_id:
                    raise UserError(_("Pengajuan dana kas kecil harus memakai jenis dan mata uang fund-nya."))
            elif rec.advance_type_id.kind == "petty_cash":
                raise UserError(_("Jenis kas kecil outlet hanya untuk pengajuan yang terikat ke Dana Kas Kecil."))

    # ------------------------------------------------------------------ plafon
    def _efn_open_peers(self):
        """Uang muka lain milik pemegang yang uangnya sudah terikat (approved juga dihitung)."""
        self.ensure_one()
        return self.sudo().search(
            [
                ("id", "!=", self.id or 0),
                ("fund_id", "=", False),
                ("holder_partner_id", "=", self.holder_partner_id.id),
                ("company_id", "=", self.company_id.id),
                ("state", "in", OPEN_STATES),
            ]
        )

    def _efn_committed_company(self):
        self.ensure_one()
        if self.state == "approved":
            return self._efn_conv(self.amount_requested, self.request_date)
        return self.amount_outstanding_company

    def _efn_check_fund_float(self):
        """Imprest: saldo fund + pengajuan fund lain yang tertunda + pengajuan ini <= plafon (selalu blokir)."""
        self.ensure_one()
        fund = self.fund_id
        if fund.state != "active":
            raise UserError(_("Dana kas kecil %s sudah ditutup.", fund.name))
        others = fund.sudo().request_ids.filtered(lambda r: r.id != self.id and r.state in ("to_approve", "approved"))
        total = fund.balance + sum(others.mapped("amount_requested")) + self.amount_requested
        if self.company_currency_id.compare_amounts(total, fund.float_amount) > 0:
            raise UserError(
                _(
                    "Saldo dana %(fund)s setelah pengajuan ini (%(total)s) melebihi plafon imprest %(float)s.",
                    fund=fund.name,
                    total=self._efn_money(total),
                    float=self._efn_money(fund.float_amount),
                )
            )

    def _efn_check_limits(self):
        """Plafon jenis (uang muka karyawan). Plafon per karyawan/jabatan sumber dibuang (K-PC1)."""
        self.ensure_one()
        if self.fund_id:
            return self._efn_check_fund_float()
        pc_type = self.advance_type_id
        mode = pc_type.limit_enforcement or "off"
        if mode == "off" or self.env.user.has_group(GROUP_LIMIT_OVERRIDE):
            return True
        currency = self.company_id.currency_id
        amount = self._efn_conv(self.amount_requested, self.request_date)
        peers = self._efn_open_peers()
        problems = []
        if pc_type.limit_per_request and currency.compare_amounts(amount, pc_type.limit_per_request) > 0:
            problems.append(
                _(
                    "Jumlah %(amount)s melebihi plafon per pengajuan %(limit)s.",
                    amount=self._efn_money(amount),
                    limit=self._efn_money(pc_type.limit_per_request),
                )
            )
        if pc_type.limit_outstanding:
            open_total = sum(peer._efn_committed_company() for peer in peers)
            if currency.compare_amounts(open_total + amount, pc_type.limit_outstanding) > 0:
                problems.append(
                    _(
                        "Total uang muka terbuka pemegang menjadi %(total)s, melebihi plafon %(limit)s.",
                        total=self._efn_money(open_total + amount),
                        limit=self._efn_money(pc_type.limit_outstanding),
                    )
                )
        if pc_type.max_open_requests and len(peers) >= pc_type.max_open_requests:
            problems.append(
                _(
                    "Pemegang sudah punya %(count)s uang muka terbuka (maks. %(max)s).",
                    count=len(peers),
                    max=pc_type.max_open_requests,
                )
            )
        if pc_type.block_when_overdue:
            today = fields.Date.context_today(self)
            overdue = peers.filtered(
                lambda r: r.realization_deadline
                and r.realization_deadline < today
                and not r.currency_id.is_zero(r.amount_outstanding)
            )
            if overdue:
                problems.append(_("Masih ada uang muka lewat tenggat: %s.", ", ".join(overdue.mapped("name"))))
        if not problems:
            return True
        message = "\n".join([_("Pemeriksaan plafon uang muka gagal:")] + [f"- {p}" for p in problems])
        if mode == "block":
            raise UserError(message)
        self.message_post(body=message, subtype_xmlid="mail.mt_note")
        return True

    # ------------------------------------------------------------------ cek akses tombol
    def _efn_check_approver(self):
        """Grup penyetuju + record rule + company user + bukan pembuat/penyunting/pemegang (empat mata)."""
        user = self.env.user
        if not user.has_group(GROUP_APPROVER):
            raise AccessError(_("Hanya Kas Kecil / Penyetuju yang dapat menyetujui atau menolak pengajuan."))
        self.check_access("write")
        for rec in self:
            if rec.company_id not in user.company_ids:
                raise AccessError(_("Pengajuan milik company lain."))
            if rec.state != "to_approve":
                raise UserError(_("Hanya pengajuan berstatus Menunggu Persetujuan yang dapat diputuskan."))
            if rec._efn_is_party(user):
                raise UserError(_("Empat mata: pembuat, penyunting, atau penerima tidak boleh menyetujui sendiri."))

    def _efn_is_party(self, user):
        """SEC-G57-01: user = pembuat, penyunting, user pemegang, atau penerima dana (partner komersial sama)."""
        self.ensure_one()
        trail = self.sudo()
        return user in (trail.maker_id | trail.last_editor_id | trail.editor_ids | trail.holder_user_id) or (
            is_recipient(user, trail.holder_partner_id)
        )

    def _efn_check_finance(self):
        """Grup Finance kas kecil + record rule + company user + pencair bukan pihak pengajuan (empat mata)."""
        user = self.env.user
        if not user.has_group(GROUP_FINANCE):
            raise AccessError(_("Hanya Kas Kecil / Finance yang dapat menjalankan langkah ini."))
        self.check_access("write")
        for rec in self:
            if rec.company_id not in user.company_ids:
                raise AccessError(_("Pengajuan milik company lain."))
            if rec._efn_is_party(user):
                raise UserError(_("Empat mata: pembuat, penyunting, atau penerima tidak boleh memproses sendiri."))

    # ------------------------------------------------------------------ alur: persetujuan
    def action_submit(self):
        self.check_access("write")
        for rec in self:
            if rec.state != "draft":
                raise UserError(_("Hanya pengajuan draft yang dapat diajukan."))
            if rec.amount_requested <= 0:
                raise UserError(_("Isi jumlah yang diajukan terlebih dahulu."))
            # Diperiksa dini agar pemohon bisa memperbaiki; diperiksa ulang saat pencairan (gerbang utama).
            rec._efn_check_limits()
            rec._efn_system_write({"state": "to_approve"})
            rec.message_post(body=_("Diajukan untuk persetujuan."), subtype_xmlid="mail.mt_note")
        return True

    def action_approve(self):
        self._efn_check_approver()
        now = fields.Datetime.now()
        for rec in self:
            rec._efn_system_write({"state": "approved", "approved_by_id": self.env.uid, "approved_at": now})
            rec.message_post(body=_("Disetujui."), subtype_xmlid="mail.mt_note")
            _logger.info("EFN petty cash request %s disetujui uid=%s", rec.id, self.env.uid)
        return True

    def action_reject(self):
        self._efn_check_approver()
        for rec in self:
            rec._efn_system_write({"state": "draft"})
            rec.message_post(body=_("Dikembalikan ke draft oleh penyetuju."), subtype_xmlid="mail.mt_note")
        return True

    # ------------------------------------------------------------------ alur: pencairan
    def action_disburse(self):
        self.ensure_one()
        self._efn_check_finance()
        if self.state != "approved":
            raise UserError(_("Setujui pengajuan sebelum dicairkan."))
        self._efn_check_limits()
        amount = self.amount_requested
        if amount <= 0:
            raise UserError(_("Tidak ada yang dicairkan."))
        advance = self._efn_advance_account()
        journal = self.advance_type_id._efn_bank_journal(self.currency_id, self.bank_journal_id)
        partner = self._efn_holder_partner()
        params = self.env["ir.config_parameter"].sudo()
        if params.get_param("efn_petty_cash.disburse_via_payment", "0") in ("1", "true", "True"):
            move = self._efn_disburse_via_payment(amount, advance, journal, partner)
        else:
            move = self._efn_book_bank_transfer(amount, "disburse", advance, journal, partner)
        vals = {"state": "disbursed", "disburse_move_id": move.id, "disbursed_by_id": self.env.uid}
        if not self.fund_id and not self.realization_deadline:
            days = self.advance_type_id.realization_days or int(
                params.get_param("efn_petty_cash.realization_days", "14")
            )
            vals["realization_deadline"] = fields.Date.context_today(self) + timedelta(days=days)
        self._efn_system_write(vals)
        self.message_post(
            body=_(
                "Dicairkan %(amt)s lewat %(journal)s (entri %(move)s).",
                amt=amount,
                journal=journal.name,
                move=move.name,
            ),
            subtype_xmlid="mail.mt_note",
        )
        return self._efn_action_open_move(move)

    def _efn_book_bank_transfer(self, amount, direction, advance=None, journal=None, partner=None):
        """Pindahkan ``amount`` (positif, mata uang dokumen) antara bank dan akun uang muka.

        ``disburse``/``reimburse`` mendebit uang muka; ``return`` mengkredit uang muka.
        """
        self.ensure_one()
        advance = advance or self._efn_advance_account()
        journal = journal or self.advance_type_id._efn_bank_journal(self.currency_id, self.bank_journal_id)
        partner = partner or self._efn_holder_partner()
        date = fields.Date.context_today(self)
        amount_company = self._efn_conv(amount, date)
        labels = {
            "disburse": _("Pencairan uang muka — %s", self.name),
            "return": _("Pengembalian uang muka — %s", self.name),
            "reimburse": _("Penggantian kekurangan uang muka — %s", self.name),
        }
        sign = -1 if direction == "return" else 1
        analytic = self._efn_analytic()
        advance_line = {
            "name": labels[direction],
            "account_id": advance.id,
            "partner_id": partner.id,
            **self._efn_leg(sign * amount, sign * amount_company),
        }
        if analytic:
            advance_line["analytic_distribution"] = analytic
        bank_line = {
            "name": labels[direction],
            "account_id": journal.default_account_id.id,
            "partner_id": partner.id,
            **self._efn_leg(-sign * amount, -sign * amount_company),
        }
        move = (
            self.env["account.move"]
            .with_company(self.company_id)
            .create(
                {
                    "move_type": "entry",
                    "journal_id": journal.id,
                    "date": date,
                    "ref": self.name,
                    **self._efn_move_tags(),
                    "line_ids": [Command.create(advance_line), Command.create(bank_line)],
                }
            )
        )
        move.action_post()
        return move

    def _efn_disburse_via_payment(self, amount, advance, journal, partner):
        self.ensure_one()
        payment = (
            self.env["account.payment"]
            .with_company(self.company_id)
            .create(
                {
                    "payment_type": "outbound",
                    "partner_type": "supplier",
                    "partner_id": partner.id,
                    "amount": amount,
                    "currency_id": self.currency_id.id,
                    "journal_id": journal.id,
                    "destination_account_id": advance.id,
                    "date": fields.Date.context_today(self),
                    "memo": self.name,
                }
            )
        )
        payment.action_post()
        move = payment.move_id
        if not move:
            # Odoo 18+: pembayaran tanpa akun outstanding tidak membuat entri -> saldo uang muka tidak terlacak.
            raise UserError(
                _(
                    "Metode pembayaran jurnal %s tidak punya akun outstanding; pencairan tidak bisa dilacak.",
                    journal.name,
                )
            )
        move.write(self._efn_move_tags())
        return move

    # ------------------------------------------------------------------ realisasi
    def action_open_realization(self):
        self.ensure_one()
        self.check_access("read")
        if self.fund_id:
            raise UserError(_("Realisasi kas kecil outlet dicatat pada Dana Kas Kecil, bukan pada pengajuan ini."))
        if self.state not in ("disbursed", "in_realization"):
            raise UserError(_("Cairkan pengajuan sebelum mencatat realisasi."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Realisasi Baru"),
            "res_model": "efn.petty.cash.realization",
            "view_mode": "form",
            "target": "current",
            "context": {"default_request_id": self.id},
        }

    def _efn_on_realization_posted(self):
        for rec in self.filtered(lambda r: r.state == "disbursed"):
            rec._efn_system_write({"state": "in_realization"})

    # ------------------------------------------------------------------ kembali / kurang / settle
    def _efn_check_settleable(self):
        """Hanya uang muka (bukan pengajuan dana fund) yang sudah dicairkan."""
        if self.fund_id:
            raise UserError(_("Pengajuan dana kas kecil tidak di-settle per pengajuan; gunakan Tutup Dana."))
        if self.state not in ("disbursed", "in_realization"):
            raise UserError(_("Hanya pengajuan yang sudah dicairkan yang dapat diproses."))

    def action_return_balance(self):
        """Pemegang mengembalikan sisa uang: Dr Bank / Cr Uang Muka."""
        self.ensure_one()
        self._efn_check_finance()
        self._efn_check_settleable()
        outstanding = self.amount_outstanding
        if outstanding <= 0:
            raise UserError(_("Tidak ada sisa uang yang perlu dikembalikan."))
        move = self._efn_book_bank_transfer(outstanding, "return")
        self.message_post(body=_("Sisa %(amt)s dikembalikan (entri %(move)s).", amt=outstanding, move=move.name))
        return self._efn_action_open_move(move)

    def action_reimburse_shortfall(self):
        """Perusahaan mengganti kelebihan belanja: Dr Uang Muka / Cr Bank."""
        self.ensure_one()
        self._efn_check_finance()
        self._efn_check_settleable()
        outstanding = self.amount_outstanding
        if outstanding >= 0:
            raise UserError(_("Tidak ada kekurangan yang perlu diganti."))
        move = self._efn_book_bank_transfer(-outstanding, "reimburse")
        self.message_post(body=_("Kekurangan %(amt)s diganti (entri %(move)s).", amt=-outstanding, move=move.name))
        return self._efn_action_open_move(move)

    def action_settle(self):
        self.ensure_one()
        self._efn_check_finance()
        self._efn_check_settleable()
        if not self.currency_id.is_zero(self.amount_outstanding):
            raise UserError(
                _(
                    "Uang muka belum nol (outstanding %(amt)s). Catat realisasi, pengembalian, atau penggantian.",
                    amt=self.amount_outstanding,
                )
            )
        if self.currency_id != self.company_id.currency_id and not self.company_id.currency_exchange_journal_id:
            raise UserError(_("Company %s belum punya jurnal selisih kurs.", self.company_id.name))
        lines = self._efn_advance_lines().filtered(lambda ln: not ln.reconciled)
        if lines:
            self._efn_tag_exchange_moves(lines.reconcile())
        self._efn_system_write({"state": "settled"})
        self.message_post(body=_("Selesai — uang muka nol dan direkonsiliasi."), subtype_xmlid="mail.mt_note")
        return True

    def _efn_tag_exchange_moves(self, result):
        """Tandai entri selisih kurs hasil reconcile() agar ikut di kartu uang muka."""
        self.ensure_one()
        if not isinstance(result, dict):
            return
        moves = self.env["account.move"]
        partials = result.get("exchange_partials")
        if partials is not None:
            moves |= partials.mapped("exchange_move_id")
        full = result.get("full_reconcile")
        if full is not None:
            moves |= full.mapped("exchange_move_id")
        moves = moves.filtered(lambda m: not m.efn_petty_cash_request_id)
        if moves:
            moves.write({"efn_petty_cash_request_id": self.id})

    # ------------------------------------------------------------------ batal / reset
    def action_cancel(self):
        self.check_access("write")
        for rec in self:
            if rec.state == "settled":
                raise UserError(_("Pengajuan yang sudah selesai tidak dapat dibatalkan."))
            if rec.sudo().move_ids.filtered(lambda m: m.state == "posted"):
                raise UserError(_("Pengajuan sudah punya entri jurnal posted. Balik entri itu dulu."))
            linked = rec.sudo().replenish_realization_ids
            if linked:
                linked.with_context(**{SYSTEM_CTX: True}).write({"replenish_request_id": False})
            rec._efn_system_write({"state": "cancelled"})
        return True

    def action_reset_to_draft(self):
        self.check_access("write")
        for rec in self:
            if rec.state not in ("cancelled", "to_approve"):
                raise UserError(_("Hanya pengajuan dibatalkan atau menunggu persetujuan yang dapat di-draft-kan."))
            if rec.is_replenishment and rec.state == "cancelled":
                raise UserError(_("Pengisian ulang yang dibatalkan tidak dapat dibuka lagi; buat yang baru."))
            rec._efn_system_write({"state": "draft"})
        return True

    # ------------------------------------------------------------------ smart button
    def _efn_action_open_move(self, move):
        return {"type": "ir.actions.act_window", "res_model": "account.move", "res_id": move.id, "view_mode": "form"}

    def action_view_moves(self):
        self.ensure_one()
        self.check_access("read")
        return {
            "type": "ir.actions.act_window",
            "name": _("Entri Jurnal"),
            "res_model": "account.move",
            "domain": [("efn_petty_cash_request_id", "=", self.id)],
            "view_mode": "list,form",
        }

    def action_view_realizations(self):
        self.ensure_one()
        self.check_access("read")
        return {
            "type": "ir.actions.act_window",
            "name": _("Realisasi"),
            "res_model": "efn.petty.cash.realization",
            "domain": [("request_id", "=", self.id)],
            "view_mode": "list,form",
            "context": {"default_request_id": self.id},
        }

    # ------------------------------------------------------------------ cron
    @api.model
    def _cron_efn_petty_cash_reminder(self):
        """Harian: aktivitas + email ke pemegang uang muka lewat tenggat; pengingat isi ulang fund."""
        today = fields.Date.context_today(self)
        template = self.env.ref("efn_petty_cash.mail_template_realization_reminder", raise_if_not_found=False)
        overdue = self.search(
            [
                ("fund_id", "=", False),
                ("state", "in", ("disbursed", "in_realization")),
                ("realization_deadline", "<", today),
            ]
        )
        for rec in overdue:
            if rec.currency_id.is_zero(rec.amount_outstanding):
                continue
            user = rec.holder_user_id
            if user and not rec.activity_ids.filtered(lambda a, u=user: a.user_id == u):
                rec.activity_schedule(
                    "mail.mail_activity_data_todo",
                    user_id=user.id,
                    summary=_("Realisasi uang muka lewat tenggat"),
                    note=_("Segera catat realisasi atau kembalikan sisa uang untuk %s.", rec.name),
                )
            if template and rec.holder_partner_id.email:
                template.send_mail(rec.id, force_send=False)
        self.env["efn.petty.cash.fund"]._efn_remind_replenish()
        return True
