"""Least privilege user perangkat dapur (docs/08 §7.3 "Akses perangkat", SEC-74, SEC-77).

User grup ``efn_pos_kds.group_kds_device`` adalah akun layar khusus. **Allowlist** (SEC-74, bukan
denylist lagi): grup efektif user perangkat harus ⊆ grup implisit dari ``base.group_user`` +
``group_kds_device``. Grup lain apa pun (POS, akuntansi, stok, kontak, admin, ...) ditolak.

Ditegakkan dari **kedua sisi** relasi grup:

- ``res.users`` (``@api.constrains("group_ids")``: create/write user, termasuk lewat ``role``);
- ``res.groups`` (write/create yang mengubah ``user_ids``/``implied_ids``/``implied_by_ids`` — form
  Grup tab *Users* atau menambah implikasi), dicek ulang untuk SEMUA user perangkat.

Opsi lebih kuat (akun perangkat = user *share*) butuh ADR kecil (2FA portal) — belum diambil.

SEC-77: token kanal stasiun dirotasi otomatis bila user perangkatnya dinonaktifkan, dihapus, atau
grup/login/password/company-nya berubah, juga bila ia dikeluarkan dari grup perangkat lewat form Grup.
"""

from odoo import _, api, models
from odoo.exceptions import ValidationError

DEVICE_GROUP = "efn_pos_kds.group_kds_device"
# Field res.users yang perubahannya merotasi token kanal stasiun milik user itu (SEC-77).
ROTATE_ON_USER_FIELDS = frozenset({"active", "group_ids", "login", "password", "company_ids", "company_id"})
# Field res.groups yang mengubah keanggotaan efektif.
GROUP_MEMBERSHIP_FIELDS = frozenset({"user_ids", "implied_ids", "implied_by_ids"})


class ResUsers(models.Model):
    _inherit = "res.users"

    @api.model
    def _efn_kds_device_group(self):
        return self.env.ref(DEVICE_GROUP, raise_if_not_found=False)

    @api.model
    def _efn_kds_allowed_groups(self):
        """Grup yang boleh dimiliki user perangkat: implikasi transitif group_user + grup perangkat
        itu sendiri (BUKAN implikasi grup perangkat: grup lain yang di-imply-kan ke grup perangkat
        juga ditolak)."""
        device = self._efn_kds_device_group()
        base_user = self.env.ref("base.group_user")
        return base_user.sudo().all_implied_ids | device

    def _efn_kds_check_device_allowlist(self):
        device = self._efn_kds_device_group()
        if not device:
            return
        allowed = self._efn_kds_allowed_groups()
        for user in self.sudo().with_context(active_test=False):
            groups = user.all_group_ids
            if device not in groups:
                continue
            extra = groups - allowed
            if extra:
                raise ValidationError(
                    _(
                        "User perangkat KDS %(user)s hanya boleh memiliki grup 'Internal User' dan "
                        "'Perangkat dapur (layar KDS)'. Cabut grup: %(groups)s.",
                        user=user.login,
                        groups=", ".join(sorted(extra.mapped("display_name"))),
                    )
                )

    @api.constrains("group_ids")
    def _check_efn_kds_device_groups(self):
        self._efn_kds_check_device_allowlist()

    # ------------------------------------------------------------ rotasi token (SEC-77)
    def _efn_kds_rotate_stations(self, reason):
        if not self.ids:
            return
        # Lewat tabel relasi langsung: domain field `device_user_ids` (grup perangkat) ikut diterapkan
        # ORM pada pencarian x2many, sehingga user yang BARU dicabut dari grup tidak akan ditemukan.
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT DISTINCT station_id FROM efn_kds_station_device_user_rel WHERE user_id = ANY(%s)",
            [list(self.ids)],
        )
        station_ids = [row[0] for row in self.env.cr.fetchall()]
        stations = self.env["efn.kds.station"].sudo().with_context(active_test=False).browse(station_ids).exists()
        if stations:
            stations._efn_kds_rotate_token(reason)

    def write(self, vals):
        res = super().write(vals)
        if ROTATE_ON_USER_FIELDS & set(vals):
            self._efn_kds_rotate_stations("device_user_changed")
        return res

    def unlink(self):
        self._efn_kds_rotate_stations("device_user_deleted")
        return super().unlink()


class ResGroups(models.Model):
    _inherit = "res.groups"

    def _efn_kds_device_users(self):
        """User yang (langsung/lewat implikasi) memiliki grup perangkat — dari relasi tersimpan (SQL)."""
        device = self.env["res.users"]._efn_kds_device_group()
        if not device:
            return self.env["res.users"]
        self.env.flush_all()
        implying = device.sudo().all_implied_by_ids | device
        return self.env["res.users"].sudo().with_context(active_test=False).search([("group_ids", "in", implying.ids)])

    @api.model_create_multi
    def create(self, vals_list):
        groups = super().create(vals_list)
        if any(GROUP_MEMBERSHIP_FIELDS & set(vals) for vals in vals_list):
            groups._efn_kds_device_users()._efn_kds_check_device_allowlist()
        return groups

    def write(self, vals):
        watch = bool(GROUP_MEMBERSHIP_FIELDS & set(vals))
        before = self._efn_kds_device_users() if watch else None
        res = super().write(vals)
        if watch:
            after = self._efn_kds_device_users()
            after._efn_kds_check_device_allowlist()
            (before - after)._efn_kds_rotate_stations("device_group_revoked")
        return res
