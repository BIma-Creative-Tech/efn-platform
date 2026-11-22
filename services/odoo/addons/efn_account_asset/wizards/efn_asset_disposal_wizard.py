"""Wizard disposal aset (port `custom.fixed.asset.disposal.wizard`, ADR-049). Hanya Aset / Manager."""

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class EfnAccountAssetDisposalWizard(models.TransientModel):
    _name = "efn.account.asset.disposal.wizard"
    _description = "EFN Asset Disposal Wizard"

    def _default_asset(self):
        return self.env["efn.account.asset"].browse(self.env.context.get("default_asset_id"))

    def _default_surplus_account(self):
        asset = self._default_asset()
        if not asset:
            return False
        reval = asset.revaluation_ids.filtered("surplus_account_id")[:1]
        if reval:
            return reval.surplus_account_id.id
        return asset.group_id.default_revaluation_surplus_account_id.id

    def _default_retained_earnings(self):
        return self._default_asset().group_id.default_retained_earnings_account_id.id

    asset_id = fields.Many2one("efn.account.asset", required=True, readonly=True)
    company_id = fields.Many2one(related="asset_id.company_id")
    currency_id = fields.Many2one(related="asset_id.currency_id")
    net_book_value = fields.Monetary(related="asset_id.net_book_value", currency_field="currency_id")
    disposal_date = fields.Date(required=True, default=fields.Date.context_today)
    disposal_value = fields.Monetary(
        string="Hasil Penjualan / Pelepasan",
        required=True,
        default=0.0,
        currency_field="currency_id",
        help="Nol bila aset dihapusbukukan.",
    )
    gain_loss = fields.Monetary(string="Laba / (Rugi)", compute="_compute_gain_loss", currency_field="currency_id")
    gain_account_id = fields.Many2one("account.account", string="Akun Laba")
    loss_account_id = fields.Many2one("account.account", string="Akun Rugi")
    receivable_account_id = fields.Many2one("account.account", string="Akun Hasil (piutang/bank)")
    surplus_balance = fields.Monetary(related="asset_id.revaluation_surplus_balance", currency_field="currency_id")
    surplus_account_id = fields.Many2one(
        "account.account", string="Akun Surplus Revaluasi", default=_default_surplus_account
    )
    retained_earnings_account_id = fields.Many2one(
        "account.account", string="Akun Laba Ditahan", default=_default_retained_earnings
    )
    create_journal_entry = fields.Boolean(string="Buat Jurnal", default=True)
    note = fields.Text()

    @api.depends("disposal_value", "asset_id.net_book_value")
    def _compute_gain_loss(self):
        for wiz in self:
            wiz.gain_loss = (wiz.disposal_value or 0.0) - (wiz.asset_id.net_book_value or 0.0)

    def action_dispose(self):
        self.ensure_one()
        asset = self.asset_id
        asset._efn_check_manager()
        if asset.state != "running":
            raise UserError(_("Hanya aset berjalan yang dapat dilepas."))
        move = self._create_disposal_move() if self.create_journal_entry else False
        vals = {
            "state": "disposed",
            "disposal_date": self.disposal_date,
            "disposal_value": self.disposal_value,
            "disposal_gain_loss": self.gain_loss,
            "disposal_move_id": move.id if move else False,
        }
        if move and asset.revaluation_surplus_balance:
            vals["revaluation_surplus_balance"] = 0.0
        asset._efn_system().write(vals)
        asset.depreciation_line_ids.filtered(lambda line: not line.posted and not line.reversed).unlink()
        asset.message_post(
            body=_(
                "Aset dilepas pada %(date)s. Hasil: %(value)s, laba/(rugi): %(gain)s. %(note)s",
                date=self.disposal_date,
                value=self.disposal_value,
                gain=self.gain_loss,
                note=self.note or "",
            )
        )
        return {"type": "ir.actions.act_window_close"}

    def _create_disposal_move(self):
        """DR akumulasi + DR hasil + DR rugi / CR aset + CR laba (+ surplus -> laba ditahan, PSAK 16 / IAS 16.41)."""
        self.ensure_one()
        asset = self.asset_id
        if not asset.asset_account_id or not asset.depreciation_account_id:
            raise UserError(_("Akun aset dan akun akumulasi penyusutan wajib diisi untuk jurnal disposal."))
        if self.disposal_value and not self.receivable_account_id:
            raise UserError(_("Akun hasil wajib diisi bila nilai pelepasan > 0."))
        if self.gain_loss > 0 and not self.gain_account_id:
            raise UserError(_("Akun laba wajib diisi bila hasil melebihi nilai buku."))
        if self.gain_loss < 0 and not self.loss_account_id:
            raise UserError(_("Akun rugi wajib diisi bila hasil di bawah nilai buku."))
        surplus = asset.revaluation_surplus_balance or 0.0
        if surplus > 0 and (not self.surplus_account_id or not self.retained_earnings_account_id):
            raise UserError(
                _(
                    "Aset membawa surplus revaluasi %(amount)s: akun surplus revaluasi dan akun laba ditahan wajib diisi.",
                    amount=surplus,
                )
            )
        accum = asset.accumulated_depreciation
        cost = asset.acquisition_value + (asset.revaluation_value or 0.0)
        gain = self.gain_loss
        lines = []

        def add(account, debit, credit, name):
            lines.append(
                fields.Command.create({"name": name, "account_id": account.id, "debit": debit, "credit": credit})
            )

        if accum:
            add(asset.depreciation_account_id, accum, 0.0, _("Pelepasan akumulasi penyusutan"))
        if self.disposal_value:
            add(self.receivable_account_id, self.disposal_value, 0.0, _("Hasil pelepasan"))
        if gain < 0:
            add(self.loss_account_id, abs(gain), 0.0, _("Rugi pelepasan"))
        add(asset.asset_account_id, 0.0, cost, _("Pelepasan nilai perolehan"))
        if gain > 0:
            add(self.gain_account_id, 0.0, gain, _("Laba pelepasan"))
        if surplus > 0:
            add(self.surplus_account_id, surplus, 0.0, _("Pelepasan surplus revaluasi"))
            add(self.retained_earnings_account_id, 0.0, surplus, _("Surplus revaluasi ke laba ditahan"))
        move = (
            self.env["account.move"]
            .with_company(asset.company_id)
            .create(
                {
                    "move_type": "entry",
                    "date": self.disposal_date,
                    "journal_id": asset.journal_id.id,
                    "company_id": asset.company_id.id,
                    "ref": _("Disposal %(code)s", code=asset.code),
                    "line_ids": lines,
                }
            )
        )
        move.action_post()
        return move
