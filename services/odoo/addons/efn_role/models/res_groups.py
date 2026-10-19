"""`res.groups`: SoD & grup terkelola dari sisi grup (E-47, docs/08 §12.3 titik pemeriksaan b).

Menutup jalur yang tidak melewati `res.users.write`:
- `user_ids` (anggota ditambah dari form grup / `all_user_ids` inverse);
- `implied_ids` / `implied_by_ids` (mis. *Settings* `implied_group` menambah implied_ids ke `base.group_user`) ->
  user anggota grup terdampak DAN grup yang menyiratkannya (leluhur di graf implied) diperiksa.

Hanya pelanggaran BARU yang ditolak (baseline sebelum write), agar pelanggaran lama user lain (mis. pengecualian
kedaluwarsa yang belum diselesaikan) tidak memblokir perubahan grup yang tidak terkait.
"""

import logging

from odoo import _, api, models
from odoo.exceptions import ValidationError

from .role_policy import APPLY_KEY, closure, has_flag, implied_map, reverse_map, users_of_groups

_logger = logging.getLogger(__name__)

SOD_KEYS = ("implied_ids", "implied_by_ids", "user_ids")


class ResGroups(models.Model):
    _inherit = "res.groups"

    def _efn_affected_user_ids(self):
        """uid anggota eksplisit grup ini atau grup mana pun yang (transitif) menyiratkannya."""
        ancestors = closure(self.ids, reverse_map(implied_map(self.env)))
        return users_of_groups(self.env, ancestors)

    def _efn_sod_ready(self):
        return self.pool.ready and "efn.sod.rule" in self.env and self.env["efn.sod.rule"]._efn_has_block_rules()

    def _efn_managed_members(self):
        """{gid: frozenset(uid ber-flag efn_role_managed)} untuk grup terkelola di `self`."""
        managed = self.env["efn.role"]._efn_managed_group_ids()
        groups = self.sudo().filtered(lambda g: g.id in managed)
        return {
            group.id: frozenset(group.user_ids.with_context(active_test=False).filtered("efn_role_managed").ids)
            for group in groups
        }

    @api.model_create_multi
    def create(self, vals_list):
        groups = super().create(vals_list)
        if groups._efn_sod_ready():
            self.env.registry.clear_cache("groups")
            users = groups._efn_affected_user_ids()
            if users:
                # Baseline = keadaan seolah grup baru tidak ada.
                self.env["efn.sod.rule"]._efn_enforce(users, exclude_group_ids=groups.ids)
        return groups

    def write(self, vals):
        if not any(key in vals for key in SOD_KEYS) or not self.pool.ready or "efn.role" not in self.env:
            return super().write(vals)
        members_before = None
        if "user_ids" in vals and not has_flag(self.env.cr, APPLY_KEY):
            members_before = self._efn_managed_members()
        sod = self._efn_sod_ready()
        baseline = set()
        if sod:
            Rule = self.env["efn.sod.rule"]
            baseline = Rule._efn_violation_keys(self._efn_affected_user_ids())
        res = super().write(vals)
        if members_before:
            members_after = self.browse(list(members_before))._efn_managed_members()
            if members_after != members_before:
                _logger.warning("EFN_ROLE_MANAGED_GROUP_WRITE groups=%s", sorted(members_before))
                raise ValidationError(
                    _(
                        "efn_role:managed_group — keanggotaan grup ini untuk user yang dikelola Peran EFN hanya "
                        "berubah lewat penetapan peran."
                    )
                )
        if sod:
            self.env.registry.clear_cache("groups")
            self.env["efn.sod.rule"]._efn_enforce(self._efn_affected_user_ids(), baseline=baseline)
        return res
