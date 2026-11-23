"""account.move: tagihan vendor -> draf aset (G57 §1.3, baru) + sinkron baris penyusutan (port ADR-049).

- `_post`: tagihan `in_invoice` yang terposting -> satu draf aset pool per baris produk berkategori kelompok aset
  (`categ_ids`, `auto_create='draft'`), idempoten per `source_move_line_id`. Nota kredit tidak menyentuh aset.
- `button_draft` (juga dipanggil `button_cancel` CE untuk move posted): tolak bila tagihan sudah menjadi aset
  non-draft; hapus draf aset dari tagihan; baris penyusutan yang jurnalnya di-draft -> tidak posted.
- `unlink`: baris penyusutan dilepas dari jurnal yang dihapus (bisa diposting ulang).
User Billing/Accounting tidak punya ACL aset: akses ke model aset di sini lewat sudo() **setelah** memastikan
company move ada di company aktif user. Aset draft tidak menjurnal apa pun.
"""

import logging

from odoo import _, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = "account.move"

    def _efn_asset_depreciation_lines(self):
        if not self:
            return self.env["efn.account.asset.depreciation.line"].sudo()
        return self.env["efn.account.asset.depreciation.line"].sudo().search([("move_id", "in", self.ids)])

    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        posted.filtered(lambda m: m.move_type == "in_invoice" and m.state == "posted")._efn_asset_create_from_bill()
        return posted

    def _efn_asset_group_for(self, category, groups_by_categ):
        """Kelompok paling spesifik untuk `category` (kategori sendiri lalu leluhur, via parent_path)."""
        ancestor_ids = [int(cid) for cid in (category.parent_path or "").split("/") if cid]
        for categ_id in reversed(ancestor_ids):
            if categ_id in groups_by_categ:
                return groups_by_categ[categ_id]
        return None

    def _efn_asset_create_from_bill(self):
        # source_move_line_id = field sistem (SEC-G57-02): ditulis lewat _efn_system sesudah cek company di bawah.
        Asset = self.env["efn.account.asset"]._efn_system()
        Group = self.env["efn.account.asset.group"].sudo()
        for move in self:
            if not self.env.su and move.company_id not in self.env.companies:
                continue
            groups = Group.search([("company_id", "=", move.company_id.id), ("auto_create", "=", "draft")])
            groups_by_categ = {categ.id: group for group in groups for categ in group.categ_ids}
            if not groups_by_categ:
                continue
            created = Asset
            for line in move.invoice_line_ids.filtered(lambda ln: ln.display_type == "product" and ln.product_id):
                group = self._efn_asset_group_for(line.product_id.categ_id, groups_by_categ)
                if not group or Asset.search_count([("source_move_line_id", "=", line.id)]):
                    continue
                created |= Asset.create(
                    {
                        "name": line.name or line.product_id.display_name,
                        "company_id": move.company_id.id,
                        "group_id": group.id,
                        "quantity": line.quantity or 1.0,
                        "acquisition_value": abs(line.balance),
                        "acquisition_date": move.invoice_date or move.date,
                        "source_move_line_id": line.id,
                    }
                )
            if created:
                move.message_post(
                    body=_(
                        "Draf aset tetap dibuat dari tagihan ini: %(codes)s. Finance memeriksa lalu mengonfirmasi.",
                        codes=", ".join(created.mapped("code")),
                    ),
                    subtype_xmlid="mail.mt_note",
                )
                _logger.info("efn_account_asset: move %s -> %s draf aset", move.id, len(created))

    def _efn_asset_release_bill_assets(self):
        bills = self.filtered(lambda m: m.move_type == "in_invoice" and m.state == "posted")
        if not bills:
            return
        assets = self.env["efn.account.asset"].sudo().search([("source_move_id", "in", bills.ids)])
        booked = assets.filtered(lambda a: a.state in ("running", "disposed"))
        if booked:
            raise UserError(
                _(
                    "Tagihan ini sudah menjadi aset tetap berjalan (%(codes)s). Batalkan/disposal aset dulu.",
                    codes=", ".join(booked.mapped("code")),
                )
            )
        drafts = assets.filtered(lambda a: a.state == "draft")
        if drafts:
            for bill in bills:
                codes = drafts.filtered(lambda a, b=bill: a.source_move_id == b).mapped("code")
                if codes:
                    bill.message_post(
                        body=_("Draf aset dihapus karena tagihan di-reset: %(codes)s.", codes=", ".join(codes)),
                        subtype_xmlid="mail.mt_note",
                    )
            drafts.unlink()

    def button_draft(self):
        self._efn_asset_release_bill_assets()
        res = super().button_draft()
        lines = self._efn_asset_depreciation_lines().filtered("posted")
        if lines:
            lines.write({"posted": False})
        return res

    def unlink(self):
        lines = self._efn_asset_depreciation_lines()
        if lines:
            lines.write({"posted": False, "move_id": False})
        return super().unlink()
