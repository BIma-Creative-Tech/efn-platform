"""Ambang kedaluwarsa per kategori (docs/04 §28.5a; fit-gap S-08/S-10; nama field = kontrak efn-odoo-functional).

Nilai 0 = kosong. **Pewarisan per field**: kategori yang nilainya 0 memakai induk terdekat yang terisi
(``_efn_effective_days``); bila tidak ada sampai root -> tidak dikelola. ``efn_replenishment._efn_min_remaining_days``
memakai helper yang sama bila modul ini terpasang. Nilai sama (mis. near = markdown) valid; constraint hanya menolak
markdown > near pada kategori yang mengisi keduanya.
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

MAX_DAYS = 3650


class ProductCategory(models.Model):
    _inherit = "product.category"

    efn_near_expiry_days = fields.Integer(
        string="Ambang near-expiry (hari)",
        default=0,
        help="Lot dengan sisa umur <= nilai ini masuk alert 'mendekati kedaluwarsa' di outlet. 0 = tidak dikelola.",
    )
    efn_markdown_days = fields.Integer(
        string="Ambang markdown (hari)",
        default=0,
        help="Lot dengan sisa umur <= nilai ini masuk alert 'perlu markdown'. Harus <= ambang near-expiry bila "
        "keduanya diisi. 0 = tanpa tahap markdown.",
    )
    efn_min_receipt_days = fields.Integer(
        string="Sisa umur minimum saat terima (hari)",
        default=0,
        help="Penerimaan outlet dari DC menolak lot dengan sisa umur kurang dari nilai ini (override teraudit). "
        "Juga dibaca efn_replenishment (cap umur simpan). 0 = tidak dicek.",
    )

    @api.constrains("efn_near_expiry_days", "efn_markdown_days", "efn_min_receipt_days")
    def _check_efn_expiry_days(self):
        for categ in self:
            values = (categ.efn_near_expiry_days, categ.efn_markdown_days, categ.efn_min_receipt_days)
            if any(v < 0 or v > MAX_DAYS for v in values):
                raise ValidationError(_("efn_expiry:category_days — ambang hari harus 0..%s.", MAX_DAYS))
            if categ.efn_near_expiry_days and categ.efn_markdown_days > categ.efn_near_expiry_days:
                raise ValidationError(
                    _("efn_expiry:category_days — ambang markdown tidak boleh lebih besar dari ambang near-expiry.")
                )

    def _efn_effective_days(self, fname):
        """Nilai ``fname`` efektif: milik kategori ini bila > 0, selain itu induk terdekat yang terisi (0 bila tidak
        ada). Kategori daun kosong jatuh ke induk (efn_setup_supermarket mengisi induk/root sebagai cadangan)."""
        self.ensure_one()
        categ = self
        while categ:
            value = categ[fname] or 0
            if value > 0:
                return value
            categ = categ.parent_id
        return 0

    def _efn_expiry_thresholds(self):
        """{near, markdown, min_receipt} efektif (pewarisan per field)."""
        self.ensure_one()
        return {
            "near": self._efn_effective_days("efn_near_expiry_days"),
            "markdown": self._efn_effective_days("efn_markdown_days"),
            "min_receipt": self._efn_effective_days("efn_min_receipt_days"),
        }

    @api.model
    def _efn_managed_categ_ids(self, fnames=("efn_near_expiry_days", "efn_markdown_days")):
        """Id kategori yang (sendiri atau lewat induk) punya salah satu ambang ``fnames`` > 0."""
        domain = []
        for fname in fnames:
            domain = ["|", *domain, (fname, ">", 0)] if domain else [(fname, ">", 0)]
        roots = self.sudo().search(domain)
        if not roots:
            return []
        return self.sudo().search([("id", "child_of", roots.ids)]).ids
