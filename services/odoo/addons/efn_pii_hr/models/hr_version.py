"""E-38b kelas K: data pribadi karyawan di `hr.version` (Odoo 19.0 `hr/models/hr_version.py`, WebFetch 19 Okt 2026).

`hr.version` (`_inherit = ['mail.thread', 'mail.activity.mixin']`): `private_street`, `private_street2`,
`private_city`, `private_zip`, `identification_id` (NIK/KTP), `ssnid`, `passport_id`, `spouse_complete_name` — semua
`fields.Char(groups="hr.group_hr_user", tracking=True)`, tanpa constraint/indeks. `hr.employee` membaca/menulis lewat
`_inherits` (salinan field non-stored), dan setiap versi menyimpan salinannya sendiri (copy saat versi baru).

- E untuk semua field di atas; **B** untuk `identification_id` (`efn_identification_id_bidx`, subkunci
  `identification_id`, normalisasi huruf-angka huruf besar) -> pencarian NIK lengkap tetap jalan.
- Tracking: hanya nilai field terenkripsi yang dikosongkan (`_efn_pii_tracking_scope = "fields"`).
"""

from odoo import api, fields, models

from odoo.addons.efn_pii.models import keyring as kr
from odoo.addons.efn_pii.models import pii_model as pm
from odoo.addons.efn_pii.models.fields_pii import EfnPiiChar, blind_index_value, keyring_for, raise_locked

from .hr_employee import legacy_encrypt

VERSION_PII_FIELDS = (
    "private_street",
    "private_street2",
    "private_city",
    "private_zip",
    "identification_id",
    "ssnid",
    "passport_id",
    "spouse_complete_name",
)


class HrVersion(models.Model):
    _inherit = "hr.version"

    _efn_pii_always = True
    _efn_pii_tracking_scope = "fields"
    _efn_pii_fields = VERSION_PII_FIELDS
    _efn_pii_bidx = {"identification_id": ("efn_identification_id_bidx", "identification_id")}

    private_street = EfnPiiChar()
    private_street2 = EfnPiiChar()
    private_city = EfnPiiChar()
    private_zip = EfnPiiChar()
    identification_id = EfnPiiChar()
    ssnid = EfnPiiChar()
    passport_id = EfnPiiChar()
    spouse_complete_name = EfnPiiChar()

    efn_identification_id_bidx = fields.Char(
        compute="_compute_efn_identification_id_bidx",
        store=True,
        index=True,
        readonly=True,
        copy=False,
        groups="base.group_system",
        help="Blind index NIK/nomor identitas (ADR-036 §0.4) untuk pencarian nilai lengkap.",
    )

    # ------------------------------------------------------------------ registry / baca
    def _register_hook(self):
        res = super()._register_hook()
        pm.check_registry(self)
        return res

    def _fetch_query(self, query, fields):
        return pm.scan_fetch(self, lambda: super(HrVersion, self)._fetch_query(query, fields))

    def _mail_track(self, tracked_fields, initial_values):
        tracked_fields = {k: v for k, v in tracked_fields.items() if k not in VERSION_PII_FIELDS}
        return super()._mail_track(tracked_fields, initial_values)

    # ------------------------------------------------------------------ compute
    @api.depends("identification_id")
    def _compute_efn_identification_id_bidx(self):
        for version in self:
            value = version.sudo().identification_id
            if value == kr.PLACEHOLDER:
                raise_locked(self.env, keyring_for(self.env))
            version.efn_identification_id_bidx = blind_index_value(self.env, "identification_id", value or "")

    # ------------------------------------------------------------------ create / write
    @api.model
    def _create(self, data_list):
        return pm.created(super()._create(data_list))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            pm.check_markers(self, vals)
        broken = pm.broken_fields(self)
        if broken:
            pm.refuse_broken(self, broken)
        records = super().create(vals_list)
        pm.bind_new_records(records)
        return records

    def write(self, vals):
        pm.check_markers(self, vals)
        broken = pm.broken_fields(self)
        if broken and pm.vals_touch_broken(self, broken, vals):
            pm.refuse_broken(self, broken)
        if any(vals.get(fname) for fname in VERSION_PII_FIELDS):
            pm.require_keys(self.env)
        return super().write(vals)

    # ------------------------------------------------------------------ migrasi
    @api.model
    def _efn_pii_encrypt_legacy(self):
        total = legacy_encrypt(self)
        versions = self.sudo().with_context(active_test=False).search([])
        self.env.add_to_compute(self._fields["efn_identification_id_bidx"], versions)
        versions.flush_recordset(["efn_identification_id_bidx"])
        return total
