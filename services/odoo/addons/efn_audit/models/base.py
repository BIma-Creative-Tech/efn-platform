"""Hook audit di AbstractModel `base` (diwarisi semua model) — docs/08 §5.1.

Pola resmi Odoo (`_inherit = "base"`), tanpa monkeypatch kelas registry. Karena berada di dasar
MRO, hook melihat `vals` final setelah override modul lain (mis. kunci HP efn_pos_otp_gate) dan
tidak punya masalah urutan patch seperti OCA auditlog (`_patch_methods` di `_register_hook`).

Dilarang di sini (penyebab bug OCA auditlog × ORM 19): menukar cache transaksi
(`ThrowAwayCache`, `field_data`/`field_dirty`), `env.invalidate_all()` di tengah write, SQL
langsung ke tabel model yang diaudit. Nilai lama/baru diambil dengan `read()` biasa.

Alur hemat `write(vals)`:
1. aturan dari `ormcache` (data polos); keluar cepat bila tidak ada aturan / registry belum siap
   (install/upgrade modul) / model transient/abstrak / model log sendiri;
2. irisan field dilacak ∩ `vals` dihitung SEBELUM membaca apa pun — kosong -> langsung super
   (mis. `stock.quant.write({'quantity': ...})` pada setiap mutasi stok tidak membaca apa pun);
3. saring record dengan `filter_domain` (in-memory); 4. `read` nilai lama; 5. super;
6. `read` nilai baru; 7. hanya field yang berubah; 8. satu `create` batch log + baris.

Kegagalan menulis log tidak ditelan: exception naik dan transaksi gagal (log audit = kontrol).
"""

import json

from odoo import api, models
from odoo.tools import float_repr, html2plaintext
from odoo.tools.safe_eval import safe_eval

from odoo.addons.efn_core.models.efn_bff_client import mask_phone, normalize_member_ref

MAX_VALUE_LEN = 2000
MAX_X2M_NAMES = 20


def _truncate(text):
    if text and len(text) > MAX_VALUE_LEN:
        return text[: MAX_VALUE_LEN - 1] + "…"
    return text


def mask_email(value):
    """`budi@example.com` -> `b***@example.com` (UU PDP)."""
    if not value or "@" not in value:
        return "****"
    local, _sep, domain = value.partition("@")
    return f"{local[:1]}***@{domain}"


def mask_value(fname, text):
    """Samarkan nilai field PII SEBELUM masuk DB. Nilai lengkap tidak pernah disimpan."""
    if not text:
        return text
    lname = fname.lower()
    if "phone" in lname or "mobile" in lname:
        return mask_phone(normalize_member_ref(text) or text)
    if "email" in lname:
        return mask_email(text)
    return f"****({len(text)})"


def _normalize(field, value):
    """Nilai mentah `read(load=False)` untuk perbandingan lama vs baru."""
    if field.type in ("many2many", "one2many"):
        return tuple(sorted(value or ()))
    if value is None:
        return False
    return value


class Base(models.AbstractModel):
    _inherit = "base"

    # ---------------------------------------------------------------- rule
    def _efn_audit_rule(self):
        """Data aturan (namedtuple) untuk model ini, atau None. Sangat murah: tanpa query."""
        if self._transient or self._abstract or not self.pool.ready:
            return None
        if "efn.audit.rule" not in self.pool:
            return None
        return self.env["efn.audit.rule"]._efn_get_rule(self._name)

    def _efn_audit_filter(self, rule):
        records = self.browse([rid for rid in self._ids if isinstance(rid, int)])
        if records and rule.filter_domain:
            domain = safe_eval(rule.filter_domain)
            records = self.browse(records.sudo().with_context(active_test=False).filtered_domain(domain)._ids)
        return records

    def _efn_audit_read(self, fnames):
        """{id: {field: nilai mentah}} lewat read() biasa (cache normal, tanpa trik)."""
        if not self or not fnames:
            return {}
        rows = self.sudo().with_context(active_test=False).read(list(fnames), load=False)
        return {row["id"]: row for row in rows}

    # --------------------------------------------------------------- hooks
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        rule = self._efn_audit_rule()
        if rule and rule.log_create and records:
            # Hanya field dilacak yang ADA di vals masing-masing record (nilai lama kosong).
            per_record = {
                record.id: [f for f in vals if f in rule.tracked]
                for record, vals in zip(records, vals_list, strict=True)
            }
            audited = records._efn_audit_filter(rule)
            if audited:
                union = {f for rid in audited.ids for f in per_record.get(rid, ())}
                new = audited._efn_audit_read(union)
                audited._efn_audit_log("create", rule, per_record, {}, new)
        return records

    def write(self, vals):
        rule = self._efn_audit_rule()
        if not rule or not rule.log_write:
            return super().write(vals)
        fnames = [f for f in vals if f in rule.tracked]
        if not fnames:
            return super().write(vals)
        records = self._efn_audit_filter(rule)
        if not records:
            return super().write(vals)
        old = records._efn_audit_read(fnames)
        result = super().write(vals)
        new = records._efn_audit_read(fnames)
        records._efn_audit_log("write", rule, {rid: fnames for rid in records.ids}, old, new)
        return result

    def unlink(self):
        rule = self._efn_audit_rule()
        if rule and rule.log_unlink:
            records = self._efn_audit_filter(rule)
            if records:
                fnames = sorted(rule.tracked) if rule.capture_unlink else []
                old = records._efn_audit_read(fnames)
                records._efn_audit_log("unlink", rule, {rid: fnames for rid in records.ids}, old, {})
        return super().unlink()

    # ----------------------------------------------------------------- log
    def _efn_audit_log(self, operation, rule, fnames_by_id, old, new):
        """Satu create batch `efn.audit.log` (+ baris) untuk record `self`."""
        formatter = _Formatter(self.env, self, rule, old, new)
        company_field = self._fields.get("company_id")
        has_company = (
            company_field is not None
            and company_field.type == "many2one"
            and company_field.comodel_name == "res.company"
        )
        records = self.sudo().with_context(active_test=False)
        model_id = self.env["ir.model"]._get_id(self._name)
        field_ids = self.env["ir.model.fields"]._get_ids(self._name)
        vals_list = []
        for record in records:
            lines = []
            for fname in fnames_by_id.get(record.id, ()):
                field = self._fields[fname]
                o_raw = old.get(record.id, {}).get(fname) if operation != "create" else None
                n_raw = new.get(record.id, {}).get(fname) if operation != "unlink" else None
                if operation == "write" and _normalize(field, o_raw) == _normalize(field, n_raw):
                    continue
                o_text, o_ref = formatter.format(field, o_raw) if operation != "create" else (False, False)
                n_text, n_ref = formatter.format(field, n_raw) if operation != "unlink" else (False, False)
                masked = fname in rule.masked
                if masked:
                    o_text, n_text = mask_value(fname, o_text), mask_value(fname, n_text)
                    o_ref = n_ref = False
                lines.append(
                    {
                        "field_id": field_ids.get(fname),
                        "field_name": fname,
                        "field_label": field.string,
                        "old_value": o_text or False,
                        "new_value": n_text or False,
                        "old_ref": o_ref or False,
                        "new_ref": n_ref or False,
                        "is_masked": masked,
                    }
                )
            if operation == "write" and not lines:
                continue
            vals_list.append(
                {
                    "user_id": self.env.uid,
                    "is_sudo": self.env.su,
                    "operation": operation,
                    "model_id": model_id,
                    "model_name": self._name,
                    "res_id": record.id,
                    "res_name": _truncate(record.display_name or ""),
                    "company_id": record.company_id.id if has_company else False,
                    "rule_id": rule.rule_id or False,
                    "line_ids": [(0, 0, line) for line in lines],
                }
            )
        if vals_list:
            self.env["efn.audit.log"].sudo().create(vals_list)


class _Formatter:
    """Format nilai mentah ke teks + ref; nama record relasi diambil batch (prefetch)."""

    def __init__(self, env, records, rule, old, new):
        self.env = env
        self._names = {}
        wanted = {}
        for values in (old, new):
            for row in values.values():
                for fname, raw in row.items():
                    field = records._fields.get(fname)
                    if field is None or field.type not in ("many2one", "many2many", "one2many") or not raw:
                        continue
                    ids = raw if isinstance(raw, (list, tuple)) else [raw]
                    wanted.setdefault(field.comodel_name, set()).update(i for i in ids if isinstance(i, int))
        for comodel, ids in wanted.items():
            recs = env[comodel].sudo().with_context(active_test=False).browse(sorted(ids))
            self._names[comodel] = {r.id: r.display_name or "" for r in recs.exists()}

    def _name(self, comodel, rid):
        return self._names.get(comodel, {}).get(rid) or f"{comodel},{rid}"

    def format(self, field, raw):
        ftype = field.type
        if ftype == "boolean":
            return str(bool(raw)), False
        if raw is None or raw is False or raw == "" or (ftype in ("many2many", "one2many") and not raw):
            return False, False
        if ftype == "many2one":
            return _truncate(self._name(field.comodel_name, raw)), f"{field.comodel_name},{raw}"
        if ftype in ("many2many", "one2many"):
            ids = list(raw)
            names = [self._name(field.comodel_name, i) for i in ids[:MAX_X2M_NAMES]]
            text = ", ".join(names)
            if len(ids) > MAX_X2M_NAMES:
                text += f" … (+{len(ids) - MAX_X2M_NAMES})"
            return _truncate(text), f"{field.comodel_name}," + ",".join(str(i) for i in ids)
        if ftype == "selection":
            try:
                labels = dict(field._description_selection(self.env))
            except Exception:  # noqa: BLE001 - selection dinamis yang gagal -> nilai mentah
                labels = {}
            return _truncate(str(labels.get(raw, raw))), False
        if ftype == "float":
            digits = field.get_digits(self.env)
            return (float_repr(raw, digits[1]) if digits else repr(raw)), False
        if ftype == "monetary":
            return repr(raw), False
        if ftype in ("date", "datetime"):
            return (raw.isoformat() if hasattr(raw, "isoformat") else str(raw)), False
        if ftype == "html":
            return _truncate(html2plaintext(raw or "")), False
        if ftype in ("json", "properties", "properties_definition"):
            return _truncate(json.dumps(raw, default=str, sort_keys=True)), False
        return _truncate(str(raw)), False
