"""Baris jadwal penyusutan. Port `custom.fixed.asset.depreciation.line` (ADR-049); tombol dicek manager."""

from odoo import _, fields, models
from odoo.exceptions import UserError


class EfnAccountAssetDepreciationLine(models.Model):
    _name = "efn.account.asset.depreciation.line"
    _description = "EFN Asset Depreciation Line"
    _order = "asset_id, sequence, date"

    asset_id = fields.Many2one("efn.account.asset", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="asset_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="asset_id.currency_id", store=True)
    sequence = fields.Integer(required=True)
    date = fields.Date(required=True)
    amount = fields.Monetary(required=True, currency_field="currency_id")
    posted = fields.Boolean(default=False, copy=False, help="Jurnal sudah dibukukan ke buku besar.")
    reversed = fields.Boolean(
        default=False,
        copy=False,
        help="Penyusutan terposting yang sudah dibalik: tidak dihitung di akumulasi dan tidak diposting ulang.",
    )
    move_id = fields.Many2one("account.move", string="Jurnal", readonly=True, copy=False, index=True)

    def action_post_now(self):
        """Posting manual satu baris lebih awal dari cron (memposting juga baris lebih tua aset itu)."""
        self.asset_id._efn_check_manager()
        for line in self:
            if line.posted or line.reversed:
                continue
            line.asset_id._post_due_depreciation(as_of=line.date)

    def action_reverse(self):
        """Balik penyusutan terposting. Jurnal bersama (posting dikelompokkan per tanggal) -> jurnal balik untuk
        nominal baris ini saja; jurnal milik sendiri -> `_reverse_moves(cancel=True)`."""
        self.asset_id._efn_check_manager()
        for line in self:
            if not line.posted:
                raise UserError(_("Baris penyusutan #%(seq)s belum diposting; tidak ada yang dibalik.", seq=line.sequence))
            move = line.move_id
            if move and move.state == "posted":
                siblings = self.search_count(
                    [("move_id", "=", move.id), ("id", "!=", line.id), ("reversed", "=", False)]
                )
                if siblings:
                    line._reverse_partial(move)
                else:
                    move._reverse_moves(
                        [
                            {
                                "date": fields.Date.context_today(line),
                                "ref": _(
                                    "Pembalikan penyusutan %(code)s #%(seq)s",
                                    code=line.asset_id.code,
                                    seq=line.sequence,
                                ),
                            }
                        ],
                        cancel=True,
                    )
            line.write({"posted": False, "reversed": True})

    def _reverse_partial(self, move):
        """Jurnal balik baris ini saja (DR akumulasi / CR beban), tidak direkonsiliasi dengan jurnal bersama."""
        self.ensure_one()
        asset = self.asset_id
        reversal = (
            self.env["account.move"]
            .with_company(move.company_id)
            .create(
                {
                    "move_type": "entry",
                    "date": fields.Date.context_today(self),
                    "journal_id": move.journal_id.id,
                    "company_id": move.company_id.id,
                    "ref": _(
                        "Pembalikan penyusutan %(code)s #%(seq)s (dari %(origin)s)",
                        code=asset.code,
                        seq=self.sequence,
                        origin=move.name,
                    ),
                    "line_ids": [
                        fields.Command.create(
                            {
                                "name": _("Pembalikan akumulasi penyusutan %(name)s", name=asset.name),
                                "account_id": asset.depreciation_account_id.id,
                                "debit": self.amount,
                                "credit": 0.0,
                            }
                        ),
                        fields.Command.create(
                            {
                                "name": _("Pembalikan penyusutan %(name)s", name=asset.name),
                                "account_id": asset.expense_account_id.id,
                                "debit": 0.0,
                                "credit": self.amount,
                            }
                        ),
                    ],
                }
            )
        )
        reversal.action_post()
        return reversal
