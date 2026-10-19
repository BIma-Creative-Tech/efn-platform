"""K-a (ADR-036 §0.2, Q-PII7, 18-40): partner milik user internal (`share=False`) = identitas kerja, plaintext.

Partner user dibuat lewat `_inherits` di dalam `res.users.create` dengan context server
`efn_pii_skip_default` (nilai `pii_model.SKIP_TOKEN`, tidak bisa dibuat klien RPC). Setelah create,
partner milik user portal (`share=True`, kelas A) langsung dijadikan terlindungi (enkripsi nilai yang
baru ditulis, transaksi yang sama). Residu: tuple mati berisi plaintext partner portal di heap/WAL
sampai VACUUM (dicatat di README; perbaikan di E-38b bila diperlukan).
"""

from odoo import api, models

from . import pii_model as pm
from .fields_pii import PROTECTED_FIELD

GROUP_KEYS = ("group_ids", "groups_id", "share")


class ResUsers(models.Model):
    _inherit = "res.users"

    @api.model_create_multi
    def create(self, vals_list):
        users = super(ResUsers, self.with_context(**{pm.SKIP_CONTEXT_KEY: pm.SKIP_TOKEN})).create(vals_list)
        users = users.with_env(self.env)
        users._efn_pii_protect_share_partners()
        return users

    def write(self, vals):
        res = super().write(vals)
        if any(key in GROUP_KEYS or key.startswith(("sel_groups_", "in_group_")) for key in vals):
            self._efn_pii_protect_share_partners()
        return res

    def _efn_pii_protect_share_partners(self):
        """Partner user portal/share (kelas A) wajib terlindungi."""
        partners = self.sudo().filtered("share").partner_id.filtered(lambda p: not p[PROTECTED_FIELD])
        if partners:
            partners._efn_pii_protect()
