"""SEC-136: `mail.tracking.value` tidak pernah menyimpan data pribadi partner terlindungi dalam plaintext.

Odoo 19.0 `mail.tracking.value._create_tracking_values(initial_value, new_value, col_name, col_info, record)`
(`@api.model`, `col_info` = hasil `fields_get`, TANPA kunci `relation`) mengisi `old_value_char`/`new_value_char`
(dan `*_text`) dengan nilai mentah (char/text) atau `display_name` (many2one). Pemeriksaan di titik pembuatan ini
berlaku untuk field apa pun yang di-track oleh modul mana pun — bukan daftar statis:

1. record yang di-track terlindungi (`efn_pii_protected` / `_efn_pii_always`) -> SEMUA nilai char/text dikosongkan
   (termasuk field turunan non-`EfnPiiChar`, mis. related/compute dari nama induk);
2. nilai many2one berupa partner terlindungi -> label `Kontak terlindungi #<id>`;
3. record lain (tidak terlindungi): nilai char/text yang SAMA dengan atau MEMUAT nilai plaintext field PII dari partner
   terlindungi yang terkait (nilai many2one lama/baru, field many2one ke `res.partner` pada record itu, induk /
   commercial partner) -> dikosongkan (atau label bila sisi many2one).

`res.partner._mail_track` tetap menyaring field cakupan (lapis pertama); ini lapis kedua.
"""

from odoo import api, models

from .fields_pii import PROTECTED_FIELD
from .pii_model import REDACTED_LABEL

VALUE_KEYS = ("old_value_char", "new_value_char", "old_value_text", "new_value_text")
MIN_SECRET_LEN = 3


def _is_protected_partner(value):
    return (
        isinstance(value, models.BaseModel)
        and value._name == "res.partner"
        and len(value) == 1
        and bool(value.sudo()[PROTECTED_FIELD])
    )


def _record_protected(record):
    if not isinstance(record, models.BaseModel) or len(record) != 1:
        return False
    if getattr(record, "_efn_pii_always", False):
        return True
    return PROTECTED_FIELD in record._fields and bool(record.sudo()[PROTECTED_FIELD])


def _linked_protected_partners(record, initial_value, new_value):
    partners = []
    for value in (initial_value, new_value):
        if _is_protected_partner(value):
            partners.append(value)
    if isinstance(record, models.BaseModel) and len(record) == 1:
        sudo_record = record.sudo()
        for name, field in record._fields.items():
            if field.type == "many2one" and field.comodel_name == "res.partner" and field.store:
                value = sudo_record[name]
                if _is_protected_partner(value):
                    partners.append(value)
        if record._name == "res.partner":
            for name in ("parent_id", "commercial_partner_id"):
                value = sudo_record[name] if name in record._fields else None
                if _is_protected_partner(value):
                    partners.append(value)
    return partners


def _secret_strings(partners):
    """Nilai plaintext field PII (EfnPiiChar + display_name) partner terlindungi, untuk pencocokan isi tracking."""
    secrets = set()
    for partner in partners:
        partner = partner.sudo()
        names = [n for n, f in partner._fields.items() if getattr(f, "efn_pii_encrypted", False) and f.store]
        for name in [*names, "display_name"]:
            value = partner[name]
            if isinstance(value, str) and len(value.strip()) >= MIN_SECRET_LEN:
                secrets.add(value.strip())
    return secrets


def _scope_all(record, fname):
    """True bila semua nilai char/text record terlindungi dikosongkan. Model dengan
    `_efn_pii_tracking_scope = "fields"` (hr.employee/hr.version: nama & jabatan karyawan = identitas kerja, 18-40)
    hanya mengosongkan nilai field terenkripsi (EfnPiiChar/EfnPiiText)."""
    if getattr(record, "_efn_pii_tracking_scope", "all") != "fields" or not fname:
        return True
    return bool(getattr(record._fields.get(fname), "efn_pii_encrypted", False))


def _redact(values, record, initial_value, new_value, fname=None):
    """Redaksi in-place dict nilai tracking (lihat docstring modul)."""
    if _record_protected(record) and _scope_all(record, fname):
        for key in VALUE_KEYS:
            if values.get(key):
                values[key] = False
        # sisi many2one ke partner terlindungi tetap diberi label (bukan data pribadi)
        for key, partner in (("old_value_char", initial_value), ("new_value_char", new_value)):
            if _is_protected_partner(partner):
                values[key] = f"{REDACTED_LABEL}{partner.id}"
        return values
    for key, partner in (("old_value_char", initial_value), ("new_value_char", new_value)):
        if key in values and _is_protected_partner(partner):
            values[key] = f"{REDACTED_LABEL}{partner.id}"
    partners = _linked_protected_partners(record, initial_value, new_value)
    if partners:
        secrets = _secret_strings(partners)
        for key in VALUE_KEYS:
            value = values.get(key)
            if isinstance(value, str) and not value.startswith(REDACTED_LABEL):
                if any(secret in value for secret in secrets):
                    values[key] = False
    return values


class MailTrackingValue(models.Model):
    _inherit = "mail.tracking.value"

    @api.model
    def _create_tracking_values(self, initial_value, new_value, col_name, col_info, record):
        values = super()._create_tracking_values(initial_value, new_value, col_name, col_info, record)
        if isinstance(values, dict):
            _redact(values, record, initial_value, new_value, col_name)
        return values

    @api.model_create_multi
    def create(self, vals_list):
        """Lapis ketiga: jalur yang membuat tracking value TANPA `_create_tracking_values` (mis. log pesan
        dengan `tracking_value_ids` siap pakai) tetap diredaksi berdasarkan field + record pesan."""
        for vals in vals_list:
            self._efn_pii_redact_create_vals(vals)
        return super().create(vals_list)

    @api.model
    def _efn_pii_redact_create_vals(self, vals):
        if not any(vals.get(key) for key in VALUE_KEYS):
            return
        field = self.env["ir.model.fields"].sudo().browse(vals.get("field_id") or [])
        message = self.env["mail.message"].sudo().browse(vals.get("mail_message_id") or [])
        model_name = message.model or field.model
        res_id = message.res_id
        if not model_name or model_name not in self.env or not res_id:
            return
        record = self.env[model_name].browse(res_id).exists()
        if not record:
            return
        initial = new = None
        if field.ttype == "many2one" and field.relation == "res.partner":
            Partner = self.env["res.partner"]
            initial = Partner.browse(vals.get("old_value_integer") or []).exists()
            new = Partner.browse(vals.get("new_value_integer") or []).exists()
        _redact(vals, record, initial, new, field.name or None)
