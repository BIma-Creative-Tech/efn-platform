"""SEC-74: kotak pasir (sandbox) akun perangkat dapur — allowlist MODEL, bukan denylist.

Akun perangkat adalah user internal (``base.group_user``) sehingga mewarisi seluruh ACL baca user
internal lewat RPC (``/web/dataset/call_kw``): produk + ``standard_price`` (HPP), ``hr.employee.public``
(direktori karyawan), kanal Discuss, kartu loyalty ber-partner, dll. Record rule biasa per grup
tidak cukup: rule grup di-OR dengan rule grup lain milik ``base.group_user`` pada model yang sama.

Karena itu domain keamanan dipaksa di ``ir.rule._compute_domain`` (dipakai ``_search`` dan
``check_access`` untuk semua operasi) — hanya untuk user ber-grup perangkat dan hanya bila tidak
``sudo``:

- model di :data:`DEVICE_MODELS` / awalan :data:`DEVICE_MODEL_PREFIXES` -> domain normal (+ domain
  tambahan di :func:`_device_extra_domain`: ``res.users`` = diri sendiri, ``res.partner`` = diri +
  partner company);
- model lain -> **FALSE** (tidak ada record yang terbaca/tertulis), termasuk model dari modul yang
  dipasang belakangan (tanpa perlu upgrade/migrasi, tidak terpengaruh ``noupdate``).

Semua alur layar KDS membaca/menulis lewat ``sudo`` setelah cek akses eksplisit
(``_efn_kds_check_device``), jadi tidak terpengaruh. Tampilan many2one (``display_name``) yang
dirender ORM lewat ``sudo`` hanya muncul pada model yang boleh dibaca perangkat (daftar di bawah,
tanpa relasi ke pelanggan) — diuji runtime oleh ``test_kds_device_surface.py``.
"""

from odoo import api, models
from odoo.fields import Domain

DEVICE_GROUP = "efn_pos_kds.group_kds_device"

# Model yang boleh dibaca perangkat (ACL & record rule standar tetap berlaku).
DEVICE_MODELS = frozenset(
    {
        "efn.kds.station",
        "efn.kds.ticket",
        "efn.kds.ticket.line",
        "res.users",  # hanya diri sendiri (domain tambahan)
        "res.partner",  # hanya diri + partner company (domain tambahan + rule XML)
        "res.company",
        "res.lang",
        "res.currency",
        "res.country",
        "res.country.state",
        "res.groups",
        "res.groups.privilege",
        "res.users.settings",
        "decimal.precision",
    }
)
# Model teknis (registry/UI/bus): tanpa data pelanggan atau harga.
DEVICE_MODEL_PREFIXES = ("ir.", "bus.", "base.", "web_tour.", "efn.kds.")


def is_device_model(model_name):
    return model_name in DEVICE_MODELS or model_name.startswith(DEVICE_MODEL_PREFIXES)


class IrRule(models.Model):
    _inherit = "ir.rule"

    def _device_extra_domain(self, model_name, user):
        if model_name == "res.users":
            return Domain("id", "=", user.id)
        if model_name == "res.partner":
            partner_ids = [user.partner_id.id, *user.company_ids.partner_id.ids]
            return Domain("id", "in", partner_ids)
        return Domain.TRUE

    @api.model
    def _compute_domain(self, model_name, mode="read"):
        domain = super()._compute_domain(model_name, mode)
        if self.env.su:
            return domain
        user = self.env.user.sudo()
        if not user or not user._has_group(DEVICE_GROUP):
            return domain
        if not is_device_model(model_name):
            return Domain.FALSE
        return domain & self._device_extra_domain(model_name, user)
