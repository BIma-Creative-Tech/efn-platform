"""Cek sisa umur minimum saat outlet menerima barang dari DC (docs/04 §28.5a, fit-gap S-10, Q-SM9).

Berlaku (flag ``efn_expiry_monitor.enabled``) saat transfer di-``_action_done`` untuk move line ber-lot yang:
- tujuan = lokasi gudang **non-DC**;
- sumber = lokasi ``transit`` (langkah kedua resupply DC -> transit -> outlet) atau gudang ``efn_is_dc`` di PT yang
  sama (transfer satu langkah DC -> outlet);
- kategori produk punya ``efn_min_receipt_days`` > 0 dan lot punya ``expiration_date``;
- sisa umur (hari, TZ konteks) < ``efn_min_receipt_days``.

Pelanggaran -> ``UserError`` berisi daftar (produk, lot, sisa hari, minimum). Override: wizard (grup *Markdown
Manager* atau *Inventory Administrator*), alasan >= 10 karakter -> ``efn.expiry.event`` ``receipt_override``
(teraudit) yang ditautkan ke picking. ``efn_short_life_override_id`` hanya ditulis kode server (sudo).

Belum: filter reservasi di sisi DC (melewati lot yang pasti ditolak outlet, fit-gap S-10 "juga di sisi DC").
"""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .common import GROUP_MARKDOWN_MANAGER, GROUP_STOCK_MANAGER, is_enabled

MAX_LINES_IN_MESSAGE = 20


class StockPicking(models.Model):
    _inherit = "stock.picking"

    efn_short_life_override_id = fields.Many2one(
        "efn.expiry.event",
        string="Override sisa umur",
        readonly=True,
        copy=False,
        help="Diisi wizard override (Markdown Manager / Inventory Administrator); jejak di efn.expiry.event.",
    )
    efn_short_life_warning = fields.Text(string="Peringatan sisa umur", compute="_compute_efn_short_life_warning")

    def _compute_efn_short_life_warning(self):
        enabled = is_enabled(self.env)
        for picking in self:
            rows = picking._efn_short_life_uncovered() if enabled and picking.state not in ("done", "cancel") else []
            picking.efn_short_life_warning = picking._efn_short_life_text(rows) if rows else False

    @api.model
    def _efn_short_life_text(self, rows):
        lines = [
            _(
                "- %(product)s lot %(lot)s: sisa %(left)s hari (minimum %(min)s)",
                product=ml.product_id.display_name,
                lot=ml.lot_id.name,
                left=left,
                min=minimum,
            )
            for ml, left, minimum in rows[:MAX_LINES_IN_MESSAGE]
        ]
        if len(rows) > MAX_LINES_IN_MESSAGE:
            lines.append(_("... dan %s baris lain", len(rows) - MAX_LINES_IN_MESSAGE))
        return "\n".join(lines)

    @api.model
    def _efn_is_dc_receipt(self, move_line):
        dest_wh = move_line.location_dest_id.warehouse_id.sudo()
        if not dest_wh or dest_wh.efn_is_dc:
            return False
        source = (move_line.move_id.location_id or move_line.location_id).sudo()
        if source.usage == "transit":
            return not source.company_id or source.company_id == dest_wh.company_id
        src_wh = source.warehouse_id
        return bool(src_wh and src_wh.efn_is_dc and src_wh.company_id == dest_wh.company_id)

    def _efn_short_life_violations(self):
        """[(move_line, sisa_hari, minimum)] untuk penerimaan outlet dari DC dengan lot berumur terlalu pendek."""
        today = fields.Date.context_today(self)
        rows = []
        for picking in self:
            for ml in picking.move_line_ids.sudo():
                lot = ml.lot_id
                if not lot or not lot.expiration_date or ml.quantity <= 0:
                    continue
                categ = ml.product_id.categ_id
                minimum = categ._efn_effective_days("efn_min_receipt_days") if categ else 0
                if minimum <= 0 or not self._efn_is_dc_receipt(ml):
                    continue
                local = fields.Datetime.context_timestamp(picking, lot.expiration_date).date()
                left = (local - today).days
                if left < minimum:
                    rows.append((ml, left, minimum))
        return rows

    def _efn_short_life_uncovered(self):
        """SEC-220: pelanggaran yang lot-nya TIDAK tercakup ``efn_short_life_override_id.lot_ids`` (lot yang
        dievaluasi saat override). Lot yang ditambah/diganti setelah override tetap diblokir."""
        self.ensure_one()
        covered = self.sudo().efn_short_life_override_id.lot_ids
        return [row for row in self._efn_short_life_violations() if row[0].lot_id not in covered]

    def _action_done(self):
        if is_enabled(self.env):
            for picking in self:
                rows = picking._efn_short_life_uncovered()
                if rows:
                    raise UserError(
                        _(
                            "efn_expiry:short_life — penerimaan %(name)s berisi lot dengan sisa umur di bawah minimum "
                            "kategori (Q-SM9):\n%(lines)s\nTolak/ganti lot, atau minta Markdown Manager / Inventory "
                            "Administrator melakukan override (alasan wajib, teraudit).",
                            name=picking.name,
                            lines=self._efn_short_life_text(rows),
                        )
                    )
        return super()._action_done()

    def write(self, vals):
        if "efn_short_life_override_id" in vals and not self.env.su:
            raise AccessError(_("efn_expiry:server_field — override sisa umur hanya lewat wizard."))
        return super().write(vals)

    @api.model
    def _efn_can_override_short_life(self, user=None):
        user = user or self.env.user
        return user.has_group(GROUP_MARKDOWN_MANAGER) or user.has_group(GROUP_STOCK_MANAGER)

    def action_efn_expiry_receipt_override(self):
        """Buka wizard override sisa umur (Markdown Manager / Inventory Administrator + akses baca + company)."""
        self.ensure_one()
        if not self._efn_can_override_short_life():
            raise AccessError(_("efn_expiry:override_role — hanya Markdown Manager / Inventory Administrator."))
        self.check_access("read")
        if self.company_id not in self.env.user.company_ids:
            raise AccessError(_("efn_expiry:company — transfer PT lain."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Override sisa umur penerimaan"),
            "res_model": "efn.expiry.receipt.override.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_picking_id": self.id},
        }
