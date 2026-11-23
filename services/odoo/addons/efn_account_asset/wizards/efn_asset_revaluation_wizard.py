"""Wizard revaluasi prospektif PSAK 16 / IAS 16 (port `custom.fixed.asset.revaluation.wizard`, ADR-049)."""

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class EfnAccountAssetRevaluationWizard(models.TransientModel):
    _name = "efn.account.asset.revaluation.wizard"
    _description = "EFN Asset Revaluation Wizard"

    def _default_asset(self):
        return self.env["efn.account.asset"].browse(self.env.context.get("default_asset_id"))

    def _default_new_value(self):
        asset = self._default_asset()
        return asset.net_book_value if asset else 0.0

    def _default_remaining_life(self):
        asset = self._default_asset()
        if not asset:
            return 0
        return max(0, asset.useful_life_months - len(asset.depreciation_line_ids.filtered("posted")))

    def _default_journal(self):
        return self._default_asset().journal_id.id

    def _default_surplus_account(self):
        return self._default_asset().group_id.default_revaluation_surplus_account_id.id

    def _default_loss_account(self):
        return self._default_asset().group_id.default_revaluation_loss_account_id.id

    def _default_income_account(self):
        return self._default_asset().group_id.default_revaluation_income_account_id.id

    asset_id = fields.Many2one("efn.account.asset", required=True, readonly=True)
    company_id = fields.Many2one(related="asset_id.company_id")
    currency_id = fields.Many2one(related="asset_id.currency_id")
    net_book_value = fields.Monetary(
        string="Nilai Buku Saat Ini", related="asset_id.net_book_value", currency_field="currency_id"
    )
    revaluation_date = fields.Date(required=True, default=fields.Date.context_today)
    new_value = fields.Monetary(string="Nilai Baru", required=True, default=_default_new_value, currency_field="currency_id")
    revaluation_amount = fields.Monetary(
        string="Penyesuaian", compute="_compute_revaluation_amount", currency_field="currency_id"
    )
    new_remaining_life = fields.Integer(string="Sisa Umur (bulan)", default=_default_remaining_life)
    surplus_account_id = fields.Many2one(
        "account.account", string="Akun Surplus Revaluasi", default=_default_surplus_account
    )
    loss_account_id = fields.Many2one("account.account", string="Akun Rugi Revaluasi", default=_default_loss_account)
    income_account_id = fields.Many2one(
        "account.account", string="Akun Pendapatan Revaluasi", default=_default_income_account
    )
    journal_id = fields.Many2one(
        "account.journal",
        string="Jurnal",
        default=_default_journal,
        domain="[('type', '=', 'general'), ('company_id', '=', company_id)]",
    )
    note = fields.Text()

    @api.depends("new_value", "asset_id.net_book_value")
    def _compute_revaluation_amount(self):
        for wiz in self:
            wiz.revaluation_amount = (wiz.new_value or 0.0) - (wiz.asset_id.net_book_value or 0.0)

    def action_revalue(self):
        self.ensure_one()
        asset = self.asset_id
        asset._efn_check_manager()
        if asset.state != "running":
            raise UserError(_("Hanya aset berjalan yang dapat direvaluasi."))
        if not asset.asset_account_id:
            raise UserError(_("Akun aset wajib diisi sebelum revaluasi."))
        if self.new_remaining_life < 0:
            raise UserError(_("Sisa umur tidak boleh negatif."))
        increment = (self.new_value or 0.0) - (asset.net_book_value or 0.0)
        if asset.currency_id.is_zero(increment):
            raise UserError(_("Nilai baru sama dengan nilai buku; tidak ada revaluasi."))

        surplus_balance = asset.revaluation_surplus_balance or 0.0
        loss_recognized = asset.revaluation_loss_recognized or 0.0
        if increment > 0:
            # Naik: balik rugi L/R sebelumnya dulu (pendapatan), sisanya ke surplus.
            income_amt = min(increment, loss_recognized)
            surplus_amt = increment - income_amt
            to_pl = from_surplus = 0.0
            new_surplus_balance = surplus_balance + surplus_amt
            new_loss_recognized = loss_recognized - income_amt
        else:
            # Turun: kurangi surplus dulu, sisanya rugi L/R.
            decrease = -increment
            from_surplus = min(decrease, surplus_balance)
            to_pl = decrease - from_surplus
            income_amt = surplus_amt = 0.0
            new_surplus_balance = surplus_balance - from_surplus
            new_loss_recognized = loss_recognized + to_pl

        if (surplus_amt > 0 or from_surplus > 0) and not self.surplus_account_id:
            raise UserError(_("Akun surplus revaluasi wajib diisi."))
        if income_amt > 0 and not self.income_account_id:
            raise UserError(_("Akun pendapatan revaluasi wajib diisi untuk membalik rugi revaluasi sebelumnya."))
        if to_pl > 0 and not self.loss_account_id:
            raise UserError(_("Akun rugi revaluasi wajib diisi untuk bagian penurunan ke L/R."))

        nbv_before = asset.net_book_value
        useful_life_before = asset.useful_life_months
        posted_count = len(asset.depreciation_line_ids.filtered("posted"))
        move = self._create_revaluation_move(increment, income_amt, surplus_amt, from_surplus, to_pl)

        vals = {
            "revaluation_value": asset.revaluation_value + increment,
            "revaluation_surplus_balance": new_surplus_balance,
            "revaluation_loss_recognized": new_loss_recognized,
        }
        if self.new_remaining_life:
            vals["useful_life_months"] = posted_count + self.new_remaining_life
        asset._efn_system().write(vals)
        asset._build_schedule()

        remaining_after = asset.useful_life_months - posted_count
        self.env["efn.account.asset.revaluation"].create(
            {
                "asset_id": asset.id,
                "name": _("Revaluasi %(code)s", code=asset.code),
                "revaluation_date": self.revaluation_date,
                "net_book_value_before": nbv_before,
                "new_value": self.new_value,
                "revaluation_amount": increment,
                "surplus_movement": surplus_amt - from_surplus,
                "pl_movement": income_amt - to_pl,
                "useful_life_before": useful_life_before,
                "remaining_life_after": remaining_after,
                "surplus_balance_after": new_surplus_balance,
                "loss_recognized_after": new_loss_recognized,
                "surplus_account_id": self.surplus_account_id.id,
                "loss_account_id": self.loss_account_id.id,
                "income_account_id": self.income_account_id.id,
                "journal_id": (self.journal_id or asset.journal_id).id,
                "move_id": move.id,
                "note": self.note or False,
            }
        )
        asset.message_post(
            body=_(
                "Revaluasi %(date)s: nilai buku %(before)s -> %(after)s (penyesuaian %(amount)s). Sisa umur %(life)s "
                "bulan. %(note)s",
                date=self.revaluation_date,
                before=nbv_before,
                after=self.new_value,
                amount=increment,
                life=remaining_after,
                note=self.note or "",
            )
        )
        return {"type": "ir.actions.act_window_close"}

    def _create_revaluation_move(self, increment, income_amt, surplus_amt, from_surplus, to_pl):
        """Naik: DR aset / CR pendapatan revaluasi + CR surplus. Turun: DR surplus + DR rugi / CR aset."""
        self.ensure_one()
        asset = self.asset_id
        journal = self.journal_id or asset.journal_id
        if not journal:
            raise UserError(_("Jurnal wajib diisi untuk revaluasi."))
        lines = []

        def add(account, debit, credit, name):
            lines.append(
                fields.Command.create({"name": name, "account_id": account.id, "debit": debit, "credit": credit})
            )

        if increment > 0:
            add(asset.asset_account_id, increment, 0.0, _("Kenaikan revaluasi %(name)s", name=asset.name))
            if income_amt > 0:
                add(self.income_account_id, 0.0, income_amt, _("Pendapatan revaluasi %(name)s", name=asset.name))
            if surplus_amt > 0:
                add(self.surplus_account_id, 0.0, surplus_amt, _("Surplus revaluasi %(name)s", name=asset.name))
        else:
            add(asset.asset_account_id, 0.0, -increment, _("Penurunan revaluasi %(name)s", name=asset.name))
            if from_surplus > 0:
                add(self.surplus_account_id, from_surplus, 0.0, _("Pengurangan surplus %(name)s", name=asset.name))
            if to_pl > 0:
                add(self.loss_account_id, to_pl, 0.0, _("Rugi revaluasi %(name)s", name=asset.name))
        move = (
            self.env["account.move"]
            .with_company(asset.company_id)
            .create(
                {
                    "move_type": "entry",
                    "date": self.revaluation_date,
                    "journal_id": journal.id,
                    "company_id": asset.company_id.id,
                    "ref": _("Revaluasi %(code)s", code=asset.code),
                    "line_ids": lines,
                }
            )
        )
        move.action_post()
        return move
