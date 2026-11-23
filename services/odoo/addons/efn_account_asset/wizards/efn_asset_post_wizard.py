"""Wizard posting penyusutan massal (port `custom.fixed.asset.post.wizard`, ADR-049). Hanya Aset / Manager."""

from odoo import _, fields, models

from ..models.efn_account_asset_group import efn_check_asset_manager


class EfnAccountAssetPostWizard(models.TransientModel):
    _name = "efn.account.asset.post.wizard"
    _description = "EFN Post Due Asset Depreciation"

    cutoff_date = fields.Date(
        string="Posting Sampai",
        required=True,
        default=fields.Date.context_today,
        help="Semua baris penyusutan aset berjalan bertanggal <= tanggal ini diposting.",
    )
    group_id = fields.Many2one("efn.account.asset.group", string="Kelompok")
    location_id = fields.Many2one("efn.account.asset.location", string="Lokasi")
    company_ids = fields.Many2many(
        "res.company",
        "efn_asset_post_wiz_company_rel",
        "wizard_id",
        "company_id",
        string="PT",
        default=lambda self: self.env.companies,
    )

    def action_post(self):
        self.ensure_one()
        efn_check_asset_manager(self.env)
        # Hanya PT aktif user, walau wizard diisi PT lain lewat RPC.
        companies = (self.company_ids or self.env.companies) & self.env.companies
        domain = [("state", "=", "running"), ("company_id", "in", companies.ids)]
        if self.group_id:
            domain.append(("group_id", "=", self.group_id.id))
        if self.location_id:
            domain.append(("location_id", "=", self.location_id.id))
        assets = self.env["efn.account.asset"].search(domain)
        assets._efn_check_manager()
        count = assets._post_due_depreciation(as_of=self.cutoff_date)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success" if count else "warning",
                "title": _("Posting Penyusutan"),
                "message": _("%(count)s baris penyusutan diposting sampai %(date)s.", count=count, date=self.cutoff_date),
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
