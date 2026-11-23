"""Wizard pelepasan sebagian aset pool (port `custom.fixed.asset.partial.disposal.wizard`, ADR-049).

Contoh: 5 unit tempat sampah dalam satu nomor aset, satu rusak -> seperlima nilai & akumulasi keluar ke buku besar,
aset menjadi 4 unit, sisa jadwal dibangun ulang.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.efn_account_asset_partial_disposal import RETIREMENT_REASONS


class EfnAccountAssetPartialDisposalWizard(models.TransientModel):
    _name = "efn.account.asset.partial.disposal.wizard"
    _description = "EFN Asset Partial Retirement Wizard"

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
    asset_quantity = fields.Float(string="Unit Dimiliki", related="asset_id.quantity")
    unit_acquisition_value = fields.Monetary(
        string="Nilai per Unit", related="asset_id.unit_acquisition_value", currency_field="currency_id"
    )
    quantity = fields.Float(string="Unit Dilepas", required=True, default=1.0, digits="Product Unit of Measure")
    disposal_date = fields.Date(required=True, default=fields.Date.context_today)
    reason = fields.Selection(RETIREMENT_REASONS, default="scrap", required=True)
    proceeds = fields.Monetary(string="Hasil", default=0.0, currency_field="currency_id")
    cost_removed = fields.Monetary(string="Nilai Dikeluarkan", compute="_compute_amounts", currency_field="currency_id")
    accumulated_removed = fields.Monetary(
        string="Akumulasi Dikeluarkan", compute="_compute_amounts", currency_field="currency_id"
    )
    net_book_value_removed = fields.Monetary(
        string="Nilai Buku Dikeluarkan", compute="_compute_amounts", currency_field="currency_id"
    )
    gain_loss = fields.Monetary(string="Laba / (Rugi)", compute="_compute_amounts", currency_field="currency_id")
    quantity_after = fields.Float(string="Unit Tersisa", compute="_compute_amounts", digits="Product Unit of Measure")
    receivable_account_id = fields.Many2one("account.account", string="Akun Hasil (piutang/bank)")
    gain_account_id = fields.Many2one("account.account", string="Akun Laba")
    loss_account_id = fields.Many2one("account.account", string="Akun Rugi")
    surplus_account_id = fields.Many2one(
        "account.account", string="Akun Surplus Revaluasi", default=_default_surplus_account
    )
    retained_earnings_account_id = fields.Many2one(
        "account.account", string="Akun Laba Ditahan", default=_default_retained_earnings
    )
    create_journal_entry = fields.Boolean(string="Buat Jurnal", default=True)
    note = fields.Text()

    @api.depends("quantity", "proceeds", "asset_id")
    def _compute_amounts(self):
        for wiz in self:
            asset = wiz.asset_id
            qty = wiz.quantity or 0.0
            if not asset or qty <= 0 or qty > (asset.quantity or 0.0):
                wiz.cost_removed = wiz.accumulated_removed = wiz.net_book_value_removed = wiz.gain_loss = 0.0
                wiz.quantity_after = (asset.quantity or 0.0) - qty if asset else 0.0
                continue
            split = asset._partial_retirement_split(qty)
            wiz.cost_removed = split["cost"]
            wiz.accumulated_removed = split["accumulated_depreciation"]
            wiz.net_book_value_removed = split["net_book_value"]
            wiz.gain_loss = (wiz.proceeds or 0.0) - split["net_book_value"]
            wiz.quantity_after = asset.quantity - qty

    def action_retire(self):
        self.ensure_one()
        asset = self.asset_id
        asset._efn_check_manager()
        if asset.state != "running":
            raise UserError(_("Hanya aset berjalan yang dapat dilepas sebagian."))
        split = asset._partial_retirement_split(self.quantity)
        quantity_before = asset.quantity
        gain_loss = (self.proceeds or 0.0) - split["net_book_value"]
        move = self._create_retirement_move(split, gain_loss) if self.create_journal_entry else False
        asset._apply_partial_retirement(split, self.disposal_date, self.proceeds, gain_loss, move)
        record = self.env["efn.account.asset.partial.disposal"].create(
            {
                "name": f"{asset.code}/RET/{len(asset.partial_disposal_ids) + 1}",
                "asset_id": asset.id,
                "disposal_date": self.disposal_date,
                "reason": self.reason,
                "quantity": self.quantity,
                "quantity_before": quantity_before,
                "quantity_after": 0.0 if split["full"] else asset.quantity,
                "cost_removed": split["cost"],
                "accumulated_removed": split["accumulated_depreciation"],
                "net_book_value_removed": split["net_book_value"],
                "proceeds": self.proceeds,
                "gain_loss": gain_loss,
                "move_id": move.id if move else False,
                "note": self.note,
            }
        )
        asset.message_post(
            body=_(
                "%(qty)s unit dilepas pada %(date)s (%(reason)s). Nilai keluar %(cost)s, akumulasi dilepas %(accum)s, "
                "laba/(rugi) %(gain)s. Sisa unit %(left)s.",
                qty=self.quantity,
                date=self.disposal_date,
                reason=dict(RETIREMENT_REASONS).get(self.reason, self.reason),
                cost=split["cost"],
                accum=split["accumulated_depreciation"],
                gain=gain_loss,
                left=0 if split["full"] else asset.quantity,
            )
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Pelepasan Sebagian"),
            "res_model": "efn.account.asset.partial.disposal",
            "res_id": record.id,
            "view_mode": "form",
            "target": "current",
        }

    def _create_retirement_move(self, split, gain_loss):
        """DR akumulasi + DR hasil + DR rugi / CR aset + CR laba (+ surplus unit keluar -> laba ditahan)."""
        self.ensure_one()
        asset = self.asset_id
        if not asset.asset_account_id or not asset.depreciation_account_id:
            raise UserError(_("Akun aset dan akun akumulasi penyusutan wajib diisi untuk jurnal pelepasan."))
        if not asset.journal_id:
            raise UserError(_("Jurnal penyusutan wajib diisi pada aset untuk jurnal pelepasan."))
        if self.proceeds and not self.receivable_account_id:
            raise UserError(_("Akun hasil wajib diisi bila hasil > 0."))
        if gain_loss > 0 and not self.gain_account_id:
            raise UserError(_("Akun laba wajib diisi bila hasil melebihi nilai buku yang dilepas."))
        if gain_loss < 0 and not self.loss_account_id:
            raise UserError(_("Akun rugi wajib diisi bila hasil di bawah nilai buku yang dilepas."))
        surplus = split["surplus"]
        if surplus > 0 and (not self.surplus_account_id or not self.retained_earnings_account_id):
            raise UserError(
                _(
                    "Unit yang dilepas membawa surplus revaluasi %(amount)s: akun surplus dan laba ditahan wajib diisi.",  # noqa: E501 - string terjemahan utuh
                    amount=surplus,
                )
            )
        lines = []

        def add(account, debit, credit, name):
            lines.append(
                fields.Command.create({"name": name, "account_id": account.id, "debit": debit, "credit": credit})
            )

        if split["accumulated_depreciation"]:
            add(
                asset.depreciation_account_id,
                split["accumulated_depreciation"],
                0.0,
                _("Pelepasan akumulasi penyusutan (%(qty)s unit)", qty=self.quantity),
            )
        if self.proceeds:
            add(self.receivable_account_id, self.proceeds, 0.0, _("Hasil pelepasan sebagian"))
        if gain_loss < 0:
            add(self.loss_account_id, abs(gain_loss), 0.0, _("Rugi pelepasan sebagian"))
        add(
            asset.asset_account_id, 0.0, split["cost"], _("Pelepasan nilai perolehan (%(qty)s unit)", qty=self.quantity)
        )
        if gain_loss > 0:
            add(self.gain_account_id, 0.0, gain_loss, _("Laba pelepasan sebagian"))
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
                    "ref": _("Pelepasan sebagian %(code)s (%(qty)s unit)", code=asset.code, qty=self.quantity),
                    "line_ids": lines,
                }
            )
        )
        move.action_post()
        return move
