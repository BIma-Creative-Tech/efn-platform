"""Dana kas kecil outlet (imprest) — BARU di EFN (G57 §2.1), tidak ada di sumber odoo-platform.

Fund menyimpan plafon (float), pemegang (kustodian), dan analitik outlet. Uang fund = uang muka kustodian pada akun
uang muka jenisnya (Q-PC2 default; mode "jurnal kas outlet" tidak dibangun). Siklus:

    dana awal       = pengajuan ber-fund (dicairkan: Dr Uang Muka / Cr Bank)
    belanja         = realisasi terhadap fund (Dr Beban / Cr Uang Muka; pihak ketiga lewat tagihan vendor)
    pengisian ulang = pengajuan ber-fund senilai realisasi posted yang belum diisi ulang
    tutup           = sisa dikembalikan ke bank lalu baris uang muka fund direkonsiliasi

Invarian: saldo + pengajuan fund tertunda <= plafon (ditegakkan di pengajuan).
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.fields import Command

from .common import GROUP_FINANCE, SYSTEM_CTX, convert, is_recipient, is_system_write, move_leg

_logger = logging.getLogger(__name__)

DEFAULT_REPLENISH_THRESHOLD_PCT = 70.0
LOCKED_AFTER_USE = frozenset({"type_id", "company_id", "holder_partner_id"})


class EfnPettyCashFund(models.Model):
    _name = "efn.petty.cash.fund"
    _description = "EFN Dana Kas Kecil Outlet"
    _inherit = ["mail.thread", "mail.activity.mixin", "analytic.mixin"]
    _order = "company_id, name"
    _check_company_auto = True

    name = fields.Char(string="Nama", required=True, tracking=True, help="Mis. 'Kas Kecil PB Kemang'.")
    company_id = fields.Many2one("res.company", required=True, index=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    type_id = fields.Many2one(
        "efn.petty.cash.type",
        string="Jenis",
        required=True,
        check_company=True,
        domain="[('kind', '=', 'petty_cash')]",
        tracking=True,
    )
    holder_partner_id = fields.Many2one("res.partner", string="Kustodian", required=True, tracking=True)
    holder_user_id = fields.Many2one(
        "res.users",
        string="User Kustodian",
        tracking=True,
        index=True,
        help="User yang melihat & mengelola fund ini.",
    )
    float_amount = fields.Monetary(
        string="Plafon Imprest",
        currency_field="currency_id",
        required=True,
        tracking=True,
    )
    limit_per_transaction = fields.Monetary(
        string="Batas per Transaksi",
        currency_field="currency_id",
        tracking=True,
        help="0 = tanpa batas.",
    )
    state = fields.Selection(
        [("active", "Aktif"), ("closed", "Ditutup")],
        default="active",
        required=True,
        tracking=True,
        readonly=True,
    )
    request_ids = fields.One2many("efn.petty.cash.request", "fund_id", string="Pengajuan Dana")
    realization_ids = fields.One2many("efn.petty.cash.realization", "fund_id", string="Realisasi")
    move_ids = fields.One2many("account.move", "efn_petty_cash_fund_id", string="Entri Jurnal")
    balance = fields.Monetary(
        string="Saldo Kas Kecil",
        currency_field="currency_id",
        compute="_compute_balance",
        compute_sudo=True,
        help="Saldo akun uang muka kustodian untuk fund ini = kas fisik yang seharusnya ada.",
    )
    unreplenished_amount = fields.Monetary(
        string="Belum Diisi Ulang",
        currency_field="currency_id",
        compute="_compute_unreplenished",
        compute_sudo=True,
    )
    last_replenish_date = fields.Date(
        string="Pengisian Terakhir",
        compute="_compute_unreplenished",
        compute_sudo=True,
    )

    @api.depends("move_ids.state", "move_ids.line_ids.balance", "type_id.advance_account_id")
    def _compute_balance(self):
        for fund in self:
            account = fund.type_id.advance_account_id
            lines = fund.move_ids.line_ids.filtered(
                lambda ln, acc=account: acc and ln.account_id == acc and ln.parent_state == "posted"
            )
            fund.balance = sum(lines.mapped("balance"))

    @api.depends(
        "realization_ids.state",
        "realization_ids.amount_total",
        "realization_ids.replenish_request_id.state",
        "request_ids.disburse_move_id",
    )
    def _compute_unreplenished(self):
        for fund in self:
            fund.unreplenished_amount = sum(fund._efn_unreplenished_realizations().mapped("amount_total"))
            done = fund.request_ids.filtered(lambda r: r.is_replenishment and r.disburse_move_id)
            fund.last_replenish_date = max(done.mapped("disburse_move_id.date"), default=False)

    def _efn_unreplenished_realizations(self):
        self.ensure_one()
        posted = self.realization_ids.filtered(lambda r: r.state == "posted")
        return posted.filtered(lambda r: not r.replenish_request_id or r.replenish_request_id.state == "cancelled")

    @api.constrains("type_id", "float_amount", "limit_per_transaction")
    def _check_values(self):
        for fund in self:
            if fund.type_id.kind != "petty_cash":
                raise UserError(_("Dana kas kecil harus memakai jenis 'Kas Kecil Outlet (imprest)'."))
            if fund.float_amount <= 0 or fund.limit_per_transaction < 0:
                raise UserError(_("Plafon imprest harus positif dan batas per transaksi tidak negatif."))

    def write(self, vals):
        if "state" in vals and not is_system_write(self.env):
            raise AccessError(_("Status dana kas kecil hanya diubah lewat tombol Tutup Dana."))
        if LOCKED_AFTER_USE & set(vals) and any(fund.move_ids for fund in self.sudo()):
            raise UserError(_("Jenis, company, dan kustodian fund yang sudah bertransaksi tidak dapat diubah."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_used(self):
        if any(fund.sudo().request_ids or fund.sudo().realization_ids for fund in self):
            raise UserError(_("Fund yang sudah punya pengajuan/realisasi tidak dapat dihapus; tutup saja."))

    # ------------------------------------------------------------------ helper akuntansi (dipakai realisasi)
    def _efn_advance_account(self):
        self.ensure_one()
        return self.type_id._efn_advance_account()

    def _efn_holder_partner(self):
        self.ensure_one()
        return self.holder_partner_id

    def _efn_conv(self, amount, date=None):
        self.ensure_one()
        return convert(self.currency_id, self.company_id, amount, date or fields.Date.context_today(self))

    def _efn_leg(self, amount_cur, amount_comp):
        self.ensure_one()
        return move_leg(self.currency_id, amount_cur, amount_comp)

    def _efn_analytic(self):
        self.ensure_one()
        return dict(self.analytic_distribution or {})

    def _efn_move_tags(self):
        self.ensure_one()
        return {"efn_petty_cash_fund_id": self.id}

    def _efn_check_finance(self):
        user = self.env.user
        if not user.has_group(GROUP_FINANCE):
            raise AccessError(_("Hanya Kas Kecil / Finance yang dapat menjalankan langkah ini."))
        self.check_access("write")
        for fund in self:
            if fund.company_id not in user.company_ids:
                raise AccessError(_("Fund milik company lain."))
            if user == fund.holder_user_id or is_recipient(user, fund.holder_partner_id):
                raise UserError(_("Empat mata: kustodian tidak boleh menutup fund-nya sendiri."))

    # ------------------------------------------------------------------ tombol
    def action_request_replenishment(self):
        """Buat pengajuan pengisian ulang (draft) senilai realisasi posted yang belum diisi ulang."""
        self.ensure_one()
        self.check_access("read")
        user = self.env.user
        if user != self.holder_user_id and not user.has_group(GROUP_FINANCE):
            raise AccessError(_("Hanya kustodian fund atau Finance yang dapat mengajukan pengisian ulang."))
        if self.state != "active":
            raise UserError(_("Fund sudah ditutup."))
        if self.sudo().request_ids.filtered(lambda r: r.state in ("draft", "to_approve", "approved")):
            raise UserError(_("Masih ada pengajuan dana untuk fund ini yang belum dicairkan."))
        realizations = self.sudo()._efn_unreplenished_realizations()
        amount = sum(realizations.mapped("amount_total"))
        if self.currency_id.compare_amounts(amount, 0) <= 0:
            raise UserError(_("Belum ada realisasi posted yang perlu diisi ulang."))
        # Dibuat dengan hak user (rule "milik sendiri"); penanda pengisian ulang & tautan realisasi = tulis sistem.
        request = self.env["efn.petty.cash.request"].create(
            {
                "fund_id": self.id,
                "amount_requested": amount,
                "purpose": _("Pengisian ulang %s", self.name),
            }
        )
        request._efn_system_write({"is_replenishment": True})
        realizations.with_context(**{SYSTEM_CTX: True}).write({"replenish_request_id": request.id})
        return {
            "type": "ir.actions.act_window",
            "res_model": "efn.petty.cash.request",
            "res_id": request.id,
            "view_mode": "form",
        }

    def action_close_fund(self):
        """Tutup fund: sisa saldo dikembalikan ke bank (Dr Bank / Cr Uang Muka), baris uang muka direkonsiliasi."""
        self.ensure_one()
        self._efn_check_finance()
        if self.state != "active":
            raise UserError(_("Fund sudah ditutup."))
        trail = self.sudo()
        if trail.request_ids.filtered(lambda r: r.state in ("to_approve", "approved")):
            raise UserError(_("Selesaikan atau batalkan pengajuan dana yang tertunda dulu."))
        if trail.realization_ids.filtered(lambda r: r.state in ("draft", "submitted")):
            raise UserError(_("Posting atau batalkan realisasi yang belum selesai dulu."))
        advance = self._efn_advance_account()
        balance = self.balance
        if self.currency_id.compare_amounts(balance, 0) < 0:
            raise UserError(_("Saldo fund negatif; periksa realisasi sebelum menutup."))
        if not self.currency_id.is_zero(balance):
            self._efn_return_to_bank(advance, balance)
        lines = self.move_ids.line_ids.filtered(
            lambda ln: ln.account_id == advance and ln.parent_state == "posted" and not ln.reconciled
        )
        if lines:
            lines.reconcile()
        self.sudo().with_context(**{SYSTEM_CTX: True}).write({"state": "closed"})
        self.message_post(body=_("Fund ditutup."), subtype_xmlid="mail.mt_note")
        _logger.info("EFN petty cash fund %s ditutup uid=%s", self.id, self.env.uid)
        return True

    def _efn_return_to_bank(self, advance, balance):
        self.ensure_one()
        journal = self.type_id._efn_bank_journal(self.currency_id)
        label = _("Penutupan kas kecil — %s", self.name)
        partner = self.holder_partner_id
        move = (
            self.env["account.move"]
            .with_company(self.company_id)
            .create(
                {
                    "move_type": "entry",
                    "journal_id": journal.id,
                    "date": fields.Date.context_today(self),
                    "ref": self.name,
                    **self._efn_move_tags(),
                    "line_ids": [
                        Command.create(
                            {
                                "name": label,
                                "account_id": advance.id,
                                "partner_id": partner.id,
                                **self._efn_leg(-balance, -balance),
                            }
                        ),
                        Command.create(
                            {
                                "name": label,
                                "account_id": journal.default_account_id.id,
                                "partner_id": partner.id,
                                **self._efn_leg(balance, balance),
                            }
                        ),
                    ],
                }
            )
        )
        move.action_post()
        return move

    def action_view_requests(self):
        self.ensure_one()
        self.check_access("read")
        return {
            "type": "ir.actions.act_window",
            "name": _("Pengajuan Dana"),
            "res_model": "efn.petty.cash.request",
            "domain": [("fund_id", "=", self.id)],
            "view_mode": "list,form",
        }

    def action_view_realizations(self):
        self.ensure_one()
        self.check_access("read")
        return {
            "type": "ir.actions.act_window",
            "name": _("Realisasi"),
            "res_model": "efn.petty.cash.realization",
            "domain": [("fund_id", "=", self.id)],
            "view_mode": "list,form",
            "context": {"default_fund_id": self.id},
        }

    def action_view_moves(self):
        self.ensure_one()
        self.check_access("read")
        return {
            "type": "ir.actions.act_window",
            "name": _("Entri Jurnal"),
            "res_model": "account.move",
            "domain": [("efn_petty_cash_fund_id", "=", self.id)],
            "view_mode": "list,form",
        }

    # ------------------------------------------------------------------ cron
    @api.model
    def _efn_remind_replenish(self):
        param = self.env["ir.config_parameter"].sudo().get_param("efn_petty_cash.replenish_threshold_pct")
        try:
            pct = float(param) if param else DEFAULT_REPLENISH_THRESHOLD_PCT
        except ValueError:
            pct = DEFAULT_REPLENISH_THRESHOLD_PCT
        for fund in self.search([("state", "=", "active"), ("holder_user_id", "!=", False)]):
            threshold = fund.float_amount * pct / 100.0
            if fund.currency_id.compare_amounts(fund.unreplenished_amount, threshold) < 0:
                continue
            if fund.activity_ids.filtered(lambda a, u=fund.holder_user_id: a.user_id == u):
                continue
            fund.activity_schedule(
                "mail.mail_activity_data_todo",
                user_id=fund.holder_user_id.id,
                summary=_("Ajukan pengisian ulang kas kecil"),
                note=_("Realisasi yang belum diisi ulang sudah mencapai ambang %s%% plafon.", int(pct)),
            )
