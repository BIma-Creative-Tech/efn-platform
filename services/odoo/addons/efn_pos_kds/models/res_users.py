"""Least privilege user perangkat dapur (docs/08 §7.3 "Akses perangkat").

User grup ``efn_pos_kds.group_kds_device`` adalah akun layar khusus: tidak boleh sekaligus user
POS, manager, atau admin (mencegah akun layar di dapur menjadi pintu ke data kasir/pelanggan).
"""

from odoo import _, api, models
from odoo.exceptions import ValidationError

FORBIDDEN_WITH_DEVICE = (
    "point_of_sale.group_pos_user",
    "base.group_erp_manager",
    "base.group_system",
)


class ResUsers(models.Model):
    _inherit = "res.users"

    @api.constrains("group_ids")
    def _check_efn_kds_device_groups(self):
        device = self.env.ref("efn_pos_kds.group_kds_device", raise_if_not_found=False)
        if not device:
            return
        for user in self:
            if device not in user.all_group_ids:
                continue
            for xmlid in FORBIDDEN_WITH_DEVICE:
                group = self.env.ref(xmlid, raise_if_not_found=False)
                if group and group in user.all_group_ids:
                    raise ValidationError(
                        _(
                            "User perangkat KDS %(user)s tidak boleh memiliki grup %(group)s.",
                            user=user.login,
                            group=group.display_name,
                        )
                    )
