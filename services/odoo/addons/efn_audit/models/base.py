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

Gelombang 11:
- SEC-45: masking wajib berbasis pola nama field (HP/email/NPWP/NIK/rekening — `is_pii_field`)
  berlaku walau tidak dipilih di aturan; nama orang di-mask inisial; `res_name` model orang =
  `model,id`.
- SEC-46: bila `company_id` berubah pada write, company lama disimpan (`prev_company_id`) agar
  auditor PT lama tetap melihat jejak pemindahan.
- SEC-43: log model kontrol + log purge juga dikirim ke logger sink `odoo.addons.efn_audit.sink`
  SETELAH commit (Loki = salinan di luar kendali admin Odoo; tanpa nilai, tanpa PII).
"""

import json
import logging

from odoo import api, models
from odoo.tools import float_repr, html2plaintext
from odoo.tools.safe_eval import safe_eval

from .audit_policy import (
    PERSON_NAME_FIELDS,
    PERSONAL_MODELS,
    PROTECTED_LOG_MODELS,
    is_pii_field,
    mask_email,  # noqa: F401 - API lama (dipakai test & modul lain)
    mask_value,
)

MAX_VALUE_LEN = 2000
MAX_X2M_NAMES = 20

_sink_logger = logging.getLogger("odoo.addons.efn_audit.sink")


def _truncate(text):
    if text and len(text) > MAX_VALUE_LEN:
        return text[: MAX_VALUE_LEN - 1] + "…"
    return text


def emit_sink(cr, events):
    """Kirim ringkasan log kontrol ke logger sink setelah commit (Promtail -> Loki).

    Isi: id log, operasi, model, res_id, uid, flag sudo, nama field berubah, catatan purge — tanpa
    nilai lama/baru dan tanpa PII. Transaksi yang di-rollback tidak menghasilkan baris sink.
    """
    if not events:
        return
    payload = [json.dumps(event, sort_keys=True, default=str) for event in events]

    def _emit():
        for line in payload:
            _sink_logger.info("EFN_AUDIT_SINK %s", line)

    cr.postcommit.add(_emit)


def sink_event(log_vals, log_id):
    return {
        "log_id": log_id,
        "operation": log_vals.get("operation"),
        "model": log_vals.get("model_name"),
        "res_id": log_vals.get("res_id") or None,
        "uid": log_vals.get("user_id"),
        "sudo": bool(log_vals.get("is_sudo")),
        "fields": sorted(line[2]["field_name"] for line in log_vals.get("line_ids", ()) if line[2].get("field_name")),
        "note": log_vals.get("note") or None,
    }


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
        if records and rule.filters:
            source = records.sudo().with_context(active_test=False)
            matched = source.browse()
            for text in rule.filters:
                matched |= source.filtered_domain(safe_eval(text))
            records = self.browse([rid for rid in records._ids if rid in set(matched._ids)])
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
        prev_company = records._efn_audit_company_before(vals)
        result = super().write(vals)
        new = records._efn_audit_read(fnames)
        records._efn_audit_log("write", rule, {rid: fnames for rid in records.ids}, old, new, prev_company=prev_company)
        return result

    def _efn_audit_company_before(self, vals):
        """SEC-46: {id: company_id lama} hanya bila `company_id` ada di vals (tanpa query lain)."""
        if "company_id" not in vals or not self._efn_audit_has_company():
            return {}
        return {record.id: record.company_id.id for record in self.sudo().with_context(active_test=False)}

    def _efn_audit_has_company(self):
        field = self._fields.get("company_id")
        return field is not None and field.type == "many2one" and field.comodel_name == "res.company"

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
    def _efn_audit_log(self, operation, rule, fnames_by_id, old, new, prev_company=None):
        """Satu create batch `efn.audit.log` (+ baris) untuk record `self`."""
        formatter = _Formatter(self.env, self, rule, old, new)
        has_company = self._efn_audit_has_company()
        records = self.sudo().with_context(active_test=False)
        model_id = self.env["ir.model"]._get_id(self._name)
        field_ids = self.env["ir.model.fields"]._get_ids(self._name)
        personal = self._name in PERSONAL_MODELS
        name_fields = PERSON_NAME_FIELDS.get(self._name, frozenset())
        prev_company = prev_company or {}
        vals_list = []
        for record in records:
            lines = []
            # Nama perusahaan (res.partner is_company) bukan data pribadi -> tidak di-mask.
            person = bool(name_fields) and not (self._name == "res.partner" and record.is_company)
            for fname in fnames_by_id.get(record.id, ()):
                field = self._fields[fname]
                o_raw = old.get(record.id, {}).get(fname) if operation != "create" else None
                n_raw = new.get(record.id, {}).get(fname) if operation != "unlink" else None
                if operation == "write" and _normalize(field, o_raw) == _normalize(field, n_raw):
                    continue
                o_text, o_ref = formatter.format(field, o_raw) if operation != "create" else (False, False)
                n_text, n_ref = formatter.format(field, n_raw) if operation != "unlink" else (False, False)
                kind = "name" if (person and fname in name_fields) else None
                # SEC-45: masking wajib (pola nama field / nama orang) + pilihan aturan.
                masked = fname in rule.masked or kind == "name" or is_pii_field(fname, field)
                if masked:
                    o_text, n_text = mask_value(fname, o_text, kind), mask_value(fname, n_text, kind)
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
            company_id = record.company_id.id if has_company else False
            previous = prev_company.get(record.id) or False
            vals_list.append(
                {
                    "user_id": self.env.uid,
                    "is_sudo": self.env.su,
                    "operation": operation,
                    "model_id": model_id,
                    "model_name": self._name,
                    "res_id": record.id,
                    # SEC-45: model orang tanpa nama (auditor membuka record bila berhak).
                    "res_name": f"{self._name},{record.id}" if personal else _truncate(record.display_name or ""),
                    "company_id": company_id,
                    "prev_company_id": previous if previous != company_id else False,
                    "rule_id": rule.rule_id or False,
                    "line_ids": [(0, 0, line) for line in lines],
                }
            )
        if vals_list:
            logs = self.env["efn.audit.log"].sudo().create(vals_list)
            if self._name in PROTECTED_LOG_MODELS:
                emit_sink(self.env.cr, [sink_event(v, log.id) for v, log in zip(vals_list, logs, strict=True)])


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
