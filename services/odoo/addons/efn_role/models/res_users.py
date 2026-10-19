"""`res.users`: flag `efn_role_managed`, larangan menulis grup terkelola langsung, constraint SoD (E-47).

- User tanpa `efn_role_managed` TIDAK PERNAH disentuh penerapan peran (admin bawaan, user teknis, user test lain).
- Menulis grup yang dikelola peran ke user ber-flag tanpa penanda penerapan (`cr.cache`) -> ValidationError
  `efn_role:managed_group` (kelola lewat peran). Grup non-peran tetap bebas diubah.
- SoD: constraint `group_ids`/`company_ids`/`active` (19.0: `group_ids` eksplisit, `all_group_ids` compute tidak
  stored) -> evaluasi grup efektif lewat `efn.sod.rule._efn_enforce`.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from .role_policy import APPLY_KEY, INTERNAL_KEY, has_flag

_logger = logging.getLogger(__name__)


class ResUsers(models.Model):
    _inherit = "res.users"

    efn_role_line_ids = fields.One2many(
        "efn.role.line",
        "user_id",
        string="Peran EFN",
        groups="base.group_erp_manager,efn_audit.group_auditor",
    )
    efn_role_managed = fields.Boolean(
        string="Dikelola Peran EFN",
        default=False,
        copy=False,
        readonly=True,
        help="Diisi otomatis saat baris peran pertama dibuat. Grup yang dikelola peran hanya berubah lewat peran.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        if not has_flag(self.env.cr, INTERNAL_KEY):
            for vals in vals_list:
                vals.pop("efn_role_managed", None)
        return super().create(vals_list)

    def write(self, vals):
        if (
            "efn_role_managed" in vals
            and not has_flag(self.env.cr, INTERNAL_KEY)
            and not self.env.user.has_group("base.group_system")
        ):
            raise AccessError(_("efn_role:managed_flag — flag peran hanya diubah sistem / Superadmin."))
        guard = "group_ids" in vals and not has_flag(self.env.cr, APPLY_KEY) and self.pool.ready
        before = self._efn_managed_explicit() if guard else {}
        res = super().write(vals)
        if before:
            after = self.browse(list(before))._efn_managed_explicit(force=True)
            changed = [uid for uid, groups in before.items() if after.get(uid) != groups]
            if changed:
                for uid in changed:
                    _logger.warning("EFN_ROLE_MANAGED_GROUP_WRITE uid=%s", uid)
                raise ValidationError(
                    _(
                        "efn_role:managed_group — grup ini dikelola Peran EFN untuk user tersebut. Ubah lewat "
                        "Settings > Users & Companies > Peran EFN (penetapan peran), bukan langsung di user/grup."
                    )
                )
        return res

    def _efn_managed_explicit(self, force=False):
        """{uid: frozenset(grup eksplisit ∩ terkelola)} untuk user ber-flag (atau semua `self` bila `force`)."""
        users = self.sudo().with_context(active_test=False)
        if not force:
            users = users.filtered("efn_role_managed")
        if not users:
            return {}
        managed = self.env["efn.role"]._efn_managed_group_ids()
        if not managed:
            return {}
        return {user.id: frozenset(set(user.group_ids.ids) & managed) for user in users}

    @api.constrains("group_ids", "company_ids", "active")
    def _efn_check_sod(self):
        self.env["efn.sod.rule"]._efn_enforce(self.ids)
