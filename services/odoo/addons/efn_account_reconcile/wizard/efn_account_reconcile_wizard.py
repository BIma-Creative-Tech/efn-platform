"""Wizard rekonsiliasi item jurnal terpilih (+ write-off mata uang company). Port ADR-049 dari
`custom_account_reconcile/wizard/reconcile_wizard.py`.

Perubahan EFN: grup Accounting/Bookkeeper + `check_access('write')` item + satu company aktif — divalidasi di
`default_get` DAN ulang di `action_reconcile` (line_ids wizard bisa diisi langsung lewat RPC create).
Mesin rekonsiliasi = CE `account.move.line.reconcile()`.
"""

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError

RECONCILE_GROUP = "account.group_account_user"


class EfnAccountReconcileWizard(models.TransientModel):
    _name = "efn.account.reconcile.wizard"
    _description = "EFN Rekonsiliasi Item Jurnal"
    _check_company_auto = True

    line_ids = fields.Many2many("account.move.line", string="Item Jurnal", readonly=True)
    account_id = fields.Many2one("account.account", readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    currency_id = fields.Many2one("res.currency", related="company_id.currency_id")
    debit = fields.Monetary(readonly=True, currency_field="currency_id")
    credit = fields.Monetary(readonly=True, currency_field="currency_id")
    residual = fields.Monetary(
        string="Sisa",
        readonly=True,
        currency_field="currency_id",
        help="Saldo terbuka bersih pilihan (mata uang company). Nol = lunas penuh; selain itu pilih "
        "sebagian/write-off.",
    )
    is_balanced = fields.Boolean(readonly=True)
    mode = fields.Selection(
        [
            ("partial", "Sebagian — sisa tetap terbuka"),
            ("writeoff", "Write-off — bukukan sisa dan tutup penuh"),
        ],
        default="partial",
        required=True,
        string="Penanganan Selisih",
    )
    writeoff_account_id = fields.Many2one(
        "account.account",
        string="Akun Write-off",
        check_company=True,
        domain="[('account_type', 'not in', ('asset_receivable', 'liability_payable'))]",
    )
    writeoff_journal_id = fields.Many2one(
        "account.journal",
        string="Jurnal Write-off",
        check_company=True,
        domain="[('type', '=', 'general')]",
    )
    writeoff_date = fields.Date(string="Tanggal Write-off", default=fields.Date.context_today)
    writeoff_label = fields.Char(string="Label", default="Write-Off")

    @api.model
    def _efn_validate_lines(self, lines):
        """Cek akses + aturan bisnis; kembalikan (lines terbuka, akun, company)."""
        if not self.env.user.has_group(RECONCILE_GROUP):
            raise AccessError(_("Hanya Accounting / Bookkeeper yang dapat merekonsiliasi item jurnal."))
        lines.check_access("write")
        lines = lines.filtered(lambda line: not line.reconciled)
        if len(lines) < 2:
            raise UserError(_("Pilih minimal dua item jurnal yang belum direkonsiliasi."))
        if any(line.parent_state != "posted" for line in lines):
            raise UserError(_("Hanya item jurnal dari entri posted yang dapat direkonsiliasi."))
        companies = lines.company_id
        if len(companies) > 1:
            raise UserError(_("Semua item harus milik satu company."))
        if companies - self.env.companies:
            raise AccessError(_("Item jurnal milik company yang tidak aktif di sesi Anda."))
        accounts = lines.account_id
        if len(accounts) > 1:
            raise UserError(
                _(
                    "Semua item harus pada satu akun (ditemukan: %s). Rekonsiliasi mencocokkan debit dan kredit di "
                    "dalam satu akun.",
                    ", ".join(accounts.mapped("display_name")),
                )
            )
        if not accounts.reconcile:
            raise UserError(
                _("Akun %s tidak mengizinkan rekonsiliasi (aktifkan 'Allow Reconciliation').", accounts.display_name)
            )
        return lines, accounts, companies

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if self.env.context.get("active_model") != "account.move.line":
            return res
        lines = self.env["account.move.line"].browse(self.env.context.get("active_ids", []))
        lines, account, company = self._efn_validate_lines(lines)
        residual = sum(lines.mapped("amount_residual"))
        res.update(
            {
                "line_ids": [Command.set(lines.ids)],
                "account_id": account.id,
                "company_id": company.id,
                "debit": sum(lines.mapped("debit")),
                "credit": sum(lines.mapped("credit")),
                "residual": residual,
                "is_balanced": company.currency_id.is_zero(residual),
            }
        )
        return res

    def action_reconcile(self):
        self.ensure_one()
        lines, account, company = self._efn_validate_lines(self.line_ids)
        residual = sum(lines.mapped("amount_residual"))
        if not company.currency_id.is_zero(residual) and self.mode == "writeoff":
            lines |= self._efn_create_writeoff_line(lines, account, company, residual)
        lines.reconcile()
        return {"type": "ir.actions.act_window_close"}

    def _efn_create_writeoff_line(self, lines, account, company, residual):
        """Post entri write-off; kembalikan barisnya pada akun yang direkonsiliasi."""
        if not self.writeoff_account_id or not self.writeoff_journal_id:
            raise UserError(_("Mode write-off memerlukan akun dan jurnal write-off."))
        if self.writeoff_journal_id.company_id != company or company not in self.writeoff_account_id.company_ids:
            raise UserError(_("Akun/jurnal write-off harus milik company item jurnal."))
        if any(line.currency_id != company.currency_id for line in lines):
            raise UserError(
                _(
                    "Write-off hanya untuk item bermata uang company. Item valas: gunakan mode sebagian dan "
                    "selesaikan sisa dengan entri manual."
                )
            )
        partners = lines.partner_id
        partner_id = partners.id if len(partners) == 1 else False
        label = self.writeoff_label or _("Write-Off")
        move = self.env["account.move"].create(
            {
                "journal_id": self.writeoff_journal_id.id,
                "date": self.writeoff_date or fields.Date.context_today(self),
                "ref": label,
                "line_ids": [
                    Command.create(
                        {
                            "name": label,
                            "account_id": account.id,
                            "partner_id": partner_id,
                            "debit": -residual if residual < 0 else 0.0,
                            "credit": residual if residual > 0 else 0.0,
                        }
                    ),
                    Command.create(
                        {
                            "name": label,
                            "account_id": self.writeoff_account_id.id,
                            "partner_id": partner_id,
                            "debit": residual if residual > 0 else 0.0,
                            "credit": -residual if residual < 0 else 0.0,
                        }
                    ),
                ],
            }
        )
        move.action_post()
        return move.line_ids.filtered(lambda line: line.account_id == account)
