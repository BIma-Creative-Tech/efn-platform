"""E-38b kelas K (ADR-036 §0.2, Q-PII7, 18-40): data PRIBADI karyawan terenkripsi; identitas kerja tetap plaintext.

Odoo 19.0 (`hr/models/hr_employee.py`, WebFetch 19 Okt 2026): `_inherits = {'hr.version': 'version_id'}`; field pribadi
langsung di `hr.employee` (semua `groups="hr.group_hr_user"`): `private_phone`, `private_email`, `emergency_contact`,
`emergency_phone`, `place_of_birth`, `visa_no`, `permit_no`, `private_car_plate` (sebagian `tracking=True`).
`birthday`/`spouse_birthdate` = Date (tidak bisa EfnPiiChar -> residu, README). Alamat pribadi, NIK
(`identification_id`), `ssnid`, `passport_id`, `spouse_complete_name` ada di `hr.version` (lihat hr_version.py).

- K-a: `_create_work_contacts(self)` membuat `res.partner` work contact dengan context server `efn_pii_skip_default`
  (`SKIP_TOKEN`, tidak bisa dibuat klien RPC) -> nama/email/telepon KERJA plaintext.
- Nama karyawan, jabatan, email/telepon kerja tetap plaintext (18-40): `_efn_pii_tracking_scope = "fields"` -> redaksi
  tracking hanya untuk field terenkripsi.
- `_efn_pii_always = True`: semua baris dienkripsi (tanpa penanda). Mode terkunci: menulis data pribadi -> UserError.
"""

from odoo import api, models
from odoo.tools import SQL

from odoo.addons.efn_pii.models import pii_model as pm
from odoo.addons.efn_pii.models.fields_pii import EfnPiiChar

EMPLOYEE_PII_FIELDS = (
    "private_phone",
    "private_email",
    "emergency_contact",
    "emergency_phone",
    "place_of_birth",
    "visa_no",
    "permit_no",
    "private_car_plate",
)


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    _efn_pii_always = True
    _efn_pii_tracking_scope = "fields"
    _efn_pii_fields = EMPLOYEE_PII_FIELDS

    private_phone = EfnPiiChar()
    private_email = EfnPiiChar()
    emergency_contact = EfnPiiChar()
    emergency_phone = EfnPiiChar()
    place_of_birth = EfnPiiChar()
    visa_no = EfnPiiChar()
    permit_no = EfnPiiChar()
    private_car_plate = EfnPiiChar()

    # ------------------------------------------------------------------ registry / baca
    def _register_hook(self):
        res = super()._register_hook()
        pm.check_registry(self)
        return res

    def _fetch_query(self, query, fields):
        return pm.scan_fetch(self, lambda: super(HrEmployee, self)._fetch_query(query, fields))

    def _mail_track(self, tracked_fields, initial_values):
        tracked_fields = {k: v for k, v in tracked_fields.items() if k not in EMPLOYEE_PII_FIELDS}
        return super()._mail_track(tracked_fields, initial_values)

    # ------------------------------------------------------------------ K-a
    def _create_work_contacts(self):
        return super(HrEmployee, self.with_context(**{pm.SKIP_CONTEXT_KEY: pm.SKIP_TOKEN}))._create_work_contacts()

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
        if any(vals.get(fname) for fname in EMPLOYEE_PII_FIELDS):
            pm.require_keys(self.env)
        return super().write(vals)

    # ------------------------------------------------------------------ migrasi
    @api.model
    def _efn_pii_encrypt_legacy(self):
        """Baris lama (sebelum modul) plaintext -> efnpii2. :returns: jumlah nilai dienkripsi."""
        return legacy_encrypt(self)


def legacy_encrypt(model):
    pm.require_keys(model.env)
    model.env.flush_all()
    model.env.cr.execute(SQL("SELECT id FROM %s ORDER BY id", SQL.identifier(model._table)))
    ids = [row[0] for row in model.env.cr.fetchall()]
    allow_v1 = pm.migration_allows_v1(model.env)
    total = 0
    for start in range(0, len(ids), 1000):
        chunk = ids[start : start + 1000]
        records = model.sudo().browse(chunk)
        counts = pm.rewrite_rows(records, chunk, encrypt_plain=True, allow_v1=allow_v1)
        total += counts["encrypted"] + counts["bound"]
        records.invalidate_recordset(pm.pii_columns(records))
    pm.redact_tracking_fields(model.env, model._name, list(model._efn_pii_fields))
    return total
