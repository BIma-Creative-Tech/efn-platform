"""`res.partner.efn_member_hash` — kunci join erasure UU PDP (docs/04 §8.4, docs/08 §4.2).

- Dihitung dari nomor HP partner (`phone`, + `mobile` bila ada modul yang menambahkannya)
  yang dinormalisasi ke `^62...` oleh `efn_core.partner_member_ref`; nomor non-Indonesia /
  tidak valid -> kosong.
- `store=True, index=True` agar cron erasure bisa mencari tanpa nomor HP mentah dari BFF.
- `groups="base.group_system"`: tidak terbaca kasir; TIDAK ditambahkan ke
  `_load_pos_data_fields` (daftar eksplisit point_of_sale tidak diubah).
- Pepper tak terbaca -> nilai kosong + WARNING sekali per proses (simpan partner / POS tidak
  pernah diblokir). Cron erasure yang gagal keras (ERROR). Setelah pepper dipasang/dirotasi,
  jalankan server action "EFN: hitung ulang member hash".
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.efn_core.models.efn_bff_client import partner_member_ref

from .member_hash import PEPPER_FILE_ENV, PepperUnavailable, member_hash, read_pepper

_logger = logging.getLogger(__name__)

RECOMPUTE_BATCH = 2000
_warned = set()


def _phone_depends(model):
    return ["phone", "mobile"] if "mobile" in model._fields else ["phone"]


class ResPartner(models.Model):
    _inherit = "res.partner"

    efn_member_hash = fields.Char(
        string="EFN Member Hash",
        compute="_compute_efn_member_hash",
        store=True,
        index=True,
        readonly=True,
        copy=False,
        groups="base.group_system",
        help="hex(sha256(pepper_hex + member_ref)) dari nomor HP (docs/04 §1). Dipakai hanya "
        "untuk mencocokkan permintaan hapus data (UU PDP) dari BFF.",
    )
    efn_erasure_request_id = fields.Char(
        string="EFN Erasure Request",
        copy=False,
        readonly=True,
        index="btree_not_null",
        groups="base.group_system",
        help="id permintaan penghapusan (platform.dsar_request) yang menganonimkan partner ini.",
    )

    @api.depends(_phone_depends)
    def _compute_efn_member_hash(self):
        try:
            pepper = read_pepper()
        except PepperUnavailable as exc:
            if exc.code not in _warned:
                _warned.add(exc.code)
                # WARNING (bukan ERROR): instalasi/CI tanpa secret tidak boleh gagal; cron
                # erasure yang menandai kondisi ini sebagai ERROR.
                _logger.warning(
                    "EFN privacy: pepper member_hash tidak tersedia (%s, cek env %s); efn_member_hash dikosongkan",
                    exc.code,
                    PEPPER_FILE_ENV,
                )
            for partner in self:
                partner.efn_member_hash = False
            return
        _warned.clear()
        for partner in self:
            member, _error = partner_member_ref(partner)
            partner.efn_member_hash = member_hash(pepper, member) if member else False

    @api.model
    def _efn_recompute_member_hash(self):
        """Backfill / rotasi pepper: hitung ulang hash semua partner (termasuk arsip).

        Hanya administrator. Pepper wajib terbaca (kalau tidak, semua hash akan terhapus).
        """
        if not self.env.su and not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Hanya administrator yang boleh menghitung ulang member hash."))
        try:
            read_pepper()
        except PepperUnavailable as exc:
            raise UserError(
                _("Pepper member_hash tidak tersedia (%(code)s). Pasang secret dulu.", code=exc.code)
            ) from None
        Partner = self.sudo().with_context(active_test=False)
        ids = Partner.search([], order="id").ids
        field = self._fields["efn_member_hash"]
        for start in range(0, len(ids), RECOMPUTE_BATCH):
            batch = Partner.browse(ids[start : start + RECOMPUTE_BATCH])
            self.env.add_to_compute(field, batch)
            batch.flush_recordset(["efn_member_hash"])
            batch.invalidate_recordset(["efn_member_hash"])
        _logger.info("EFN privacy: member_hash dihitung ulang untuk %d partner", len(ids))
        return len(ids)
