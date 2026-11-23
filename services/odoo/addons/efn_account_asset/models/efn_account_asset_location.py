"""Lokasi fisik aset (pohon bebas per PT). Port `custom.fixed.asset.location` (ADR-049); baru: company_id wajib."""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class EfnAccountAssetLocation(models.Model):
    _name = "efn.account.asset.location"
    _description = "EFN Asset Location"
    _order = "complete_name"
    _parent_store = True
    _parent_name = "parent_id"
    _check_company_auto = True

    name = fields.Char(required=True)
    code = fields.Char()
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    complete_name = fields.Char(compute="_compute_complete_name", recursive=True, store=True)
    parent_id = fields.Many2one(
        "efn.account.asset.location",
        string="Lokasi Induk",
        ondelete="restrict",
        index=True,
        check_company=True,
    )
    parent_path = fields.Char(index=True)
    child_ids = fields.One2many("efn.account.asset.location", "parent_id", string="Sub-lokasi")
    address = fields.Text()
    note = fields.Char()
    active = fields.Boolean(default=True)

    @api.depends("name", "parent_id.complete_name")
    def _compute_complete_name(self):
        for loc in self:
            loc.complete_name = f"{loc.parent_id.complete_name} / {loc.name}" if loc.parent_id else loc.name

    @api.constrains("parent_id")
    def _check_location_recursion(self):
        if self._has_cycle():
            raise ValidationError(_("Lokasi aset tidak boleh rekursif."))
