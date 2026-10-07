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

Gelombang 12 (SEC-46 sisa):
- `origin_user_id`: uid manusia pemicu bila `env.uid` berbeda (mis. `with_user(SUPERUSER_ID)` tercatat
  OdooBot). Sumber tepercaya saja: penanda `cr.cache` dari `elevate_as_superuser()` atau
  `request.session.uid` (sesi HTTP terautentikasi). Context RPC TIDAK dipakai (bisa diisi klien).

Gelombang 13:
- SEC-49: hak akses (`res.users` grup/aktif/login/company, `res.groups`, `ir.rule`, `ir.model.access`,
  `res.users.apikeys`) = model kontrol. Baris sink membawa `audit_op` (`access_grant`, `access_revoke`,
  `access_rule_change`, `apikey_create`, `apikey_revoke`, `user_change`, `group_change`), xmlid grup
  yang diberikan/dicabut, uid terdampak (tanpa nama/login), dan `critical` (grup admin/auditor).
  Saat create model kontrol, semua field dilacak yang bernilai dicatat (bukan hanya yang ada di vals).
- SEC-51: baris sink versi 2 — JSON satu baris (ensure_ascii: newline/teks pengguna selalu di-escape)
  + `mac` HMAC-SHA256 dengan kunci dari file secret (`/run/secrets/efn_audit_sink_key`) yang dibaca
  proses Odoo saja; tidak ada di DB/parameter sehingga tidak terbaca admin lewat UI/server action.
  Verifikasi di luar Odoo (ETL/rekonsiliasi). Promtail wajib memfilter format baris + nama logger.
- SEC-56: nilai relasi ke model orang (`PERSONAL_MODELS`) disimpan `model,id` tanpa nama; relasi ke
  `res.groups` sebagai xmlid; x2many pada write disimpan sebagai selisih (`dihapus:` / `ditambah:`),
  ref tetap daftar lengkap.
"""

import hashlib
import hmac
import json
import logging
import os
from contextlib import contextmanager

from odoo import SUPERUSER_ID, api, models
from odoo.http import request
from odoo.tools import float_repr, html2plaintext
from odoo.tools.safe_eval import safe_eval

from .audit_policy import (
    ACCESS_MODELS,
    CRITICAL_GROUP_XMLIDS,
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
_logger = logging.getLogger(__name__)

# SEC-51 (Gelombang 13): format & integritas baris sink.
SINK_VERSION = 2
SINK_PREFIX = "EFN_AUDIT_SINK"
SINK_KEY_FILE_ENV = "EFN_AUDIT_SINK_KEY_FILE"  # path (bukan rahasia); isi file = rahasia
DEFAULT_SINK_KEY_FILE = "/run/secrets/efn_audit_sink_key"
SINK_KEY_MIN_BYTES = 32
_SINK_KEY = []  # cache per proses: [bytes | None]

# SEC-46 (Gelombang 12): penanda uid pemicu di `cr.cache` (per cursor/transaksi, tidak bisa diisi
# klien lewat RPC — berbeda dengan context). Diisi oleh `elevate_as_superuser`.
ORIGIN_UID_KEY = "efn_audit_origin_uid"


@contextmanager
def elevate_as_superuser(env):
    """Elevasi `with_user(SUPERUSER_ID)` yang TETAP mencatat uid pemicu di log audit (SEC-46).

    Pakai ini (bukan `env(user=SUPERUSER_ID)` langsung) di kode kustom yang perlu bertindak
    sebagai OdooBot, mis. dari cron/consumer tanpa sesi HTTP::

        with elevate_as_superuser(self.env) as su_env:
            su_env["product.product"].browse(ids).write({...})

    Log audit di dalam blok: `user_id` = OdooBot, `origin_user_id` = uid pemanggil.
    """
    cache = env.cr.cache
    previous = cache.get(ORIGIN_UID_KEY)
    if not previous and env.uid != SUPERUSER_ID:
        cache[ORIGIN_UID_KEY] = env.uid
    try:
        yield env(user=SUPERUSER_ID)
    finally:
        if previous:
            cache[ORIGIN_UID_KEY] = previous
        else:
            cache.pop(ORIGIN_UID_KEY, None)


def origin_uid(env):
    """uid manusia pemicu bila berbeda dari `env.uid` (mis. `with_user(SUPERUSER_ID)`), atau False.

    Sumber tepercaya saja (bukan context RPC): (1) penanda `cr.cache` dari `elevate_as_superuser`;
    (2) `request.session.uid` — user yang terautentikasi di sesi HTTP, ditetapkan server.
    Cron/queue tanpa sesi HTTP dan tanpa helper -> False (batasan, README).
    """
    uid = env.cr.cache.get(ORIGIN_UID_KEY)
    if not uid and request:
        try:
            uid = request.session.uid
        except (AttributeError, RuntimeError):
            uid = None
    if isinstance(uid, int) and not isinstance(uid, bool) and uid != env.uid:
        return uid
    return False


def _truncate(text):
    if text and len(text) > MAX_VALUE_LEN:
        return text[: MAX_VALUE_LEN - 1] + "…"
    return text


def _sink_key():
    """Kunci HMAC sink (bytes) dari file secret, atau None. Dibaca sekali per proses.

    Tidak pernah disimpan di DB / `ir.config_parameter` (terbaca admin Odoo). Kunci tidak ada / terlalu
    pendek -> baris sink tetap dikirim dengan `"mac": null` + WARNING sekali (bukan diam-diam);
    verifikator (ETL) memperlakukan baris tanpa mac sebagai tidak terverifikasi.
    """
    if not _SINK_KEY:
        path = os.environ.get(SINK_KEY_FILE_ENV) or DEFAULT_SINK_KEY_FILE
        key = None
        try:
            with open(path, "rb") as handle:
                key = handle.read().strip() or None
        except OSError:
            key = None
        if key is not None and len(key) < SINK_KEY_MIN_BYTES:
            _logger.warning("EFN audit sink: kunci HMAC < %d byte, diabaikan", SINK_KEY_MIN_BYTES)
            key = None
        if key is None:
            _logger.warning("EFN audit sink: kunci HMAC tidak tersedia; baris sink dikirim dengan mac=null")
        _SINK_KEY.append(key)
    return _SINK_KEY[0]


def sink_canonical(event):
    """Bentuk kanonik untuk HMAC: JSON tanpa `mac`, kunci terurut, tanpa spasi, ASCII."""
    body = {k: v for k, v in event.items() if k != "mac"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def sink_mac(key, event):
    return hmac.new(key, sink_canonical(event).encode("ascii"), hashlib.sha256).hexdigest()


def sink_line(event, key=None):
    """JSON satu baris (+ `mac`). `ensure_ascii` meng-escape newline/karakter kontrol/Unicode, jadi isi
    event (mis. `note`) tidak pernah bisa memecah baris atau membentuk baris sink kedua.

    Verifikasi (ETL): `hmac.compare_digest(mac, sink_mac(key, json.loads(line)))`.
    """
    body = json.loads(sink_canonical(event))
    body["mac"] = sink_mac(key, body) if key else None
    return json.dumps(body, sort_keys=True, ensure_ascii=True)


def emit_sink(cr, events):
    """Kirim ringkasan log kontrol ke logger sink setelah commit (Promtail -> Loki).

    Isi: id log, operasi, `audit_op`, model, res_id, uid, flag sudo, nama field berubah, catatan purge,
    xmlid grup diberikan/dicabut + uid terdampak (SEC-49) — tanpa nilai lama/baru dan tanpa PII.
    Transaksi yang di-rollback tidak menghasilkan baris sink.
    """
    if not events:
        return
    key = _sink_key()
    payload = [sink_line(event, key) for event in events]

    def _emit():
        for line in payload:
            _sink_logger.info("%s %s", SINK_PREFIX, line)

    cr.postcommit.add(_emit)


def sink_event(log_vals, log_id, extra=None):
    operation = log_vals.get("operation")
    event = {
        "v": SINK_VERSION,
        "audit_op": "purge" if operation == "purge" else "control_change",
        "critical": False,
        "log_id": log_id,
        "operation": log_vals.get("operation"),
        "model": log_vals.get("model_name"),
        "res_id": log_vals.get("res_id") or None,
        "uid": log_vals.get("user_id"),
        "origin_uid": log_vals.get("origin_user_id") or None,
        "sudo": bool(log_vals.get("is_sudo")),
        "fields": sorted(line[2]["field_name"] for line in log_vals.get("line_ids", ()) if line[2].get("field_name")),
        "note": log_vals.get("note") or None,
    }
    if extra:
        event.update(extra)
    return event


def _has_value(raw):
    return raw not in (None, False, "", (), [])


def _diff_ids(old_ids, new_ids):
    """(ditambah, dihapus) dengan urutan asli."""
    old_ids = [i for i in (old_ids or ()) if isinstance(i, int)]
    new_ids = [i for i in (new_ids or ()) if isinstance(i, int)]
    return [i for i in new_ids if i not in old_ids], [i for i in old_ids if i not in new_ids]


def group_refs(env, ids):
    """{id: xmlid} grup; tanpa xmlid (grup buatan UI / sudah terhapus) -> `res.groups,id`."""
    if not ids:
        return {}
    groups = env["res.groups"].sudo().with_context(active_test=False).browse(ids).exists()
    xml = groups.get_external_id()
    return {gid: xml.get(gid) or f"res.groups,{gid}" for gid in ids}


def is_critical_grant(env, group_ids):
    """True bila grup (atau grup yang di-imply-nya) termasuk CRITICAL_GROUP_XMLIDS."""
    groups = env["res.groups"].sudo().with_context(active_test=False).browse(group_ids).exists()
    if not groups:
        return False
    implied = groups | groups.all_implied_ids
    return any(xmlid in CRITICAL_GROUP_XMLIDS for xmlid in implied.get_external_id().values())


def access_extra(env, model, operation, rid, old_row, new_row):
    """Field sink tambahan untuk model hak akses (SEC-49). Hanya id/xmlid, tanpa nama/login."""
    if model in ("ir.rule", "ir.model.access"):
        return {"audit_op": "access_rule_change", "critical": True}
    if model == "res.users.apikeys":
        owner = (new_row or old_row).get("user_id") or None
        return {"audit_op": "apikey_create" if operation == "create" else "apikey_revoke", "key_owner_uid": owner}
    granted, revoked, users_added, users_removed = [], [], [], []
    if model == "res.users":
        granted, revoked = _diff_ids(old_row.get("group_ids"), new_row.get("group_ids"))
        users_added = [rid] if granted else []
        users_removed = [rid] if revoked else []
        default_op = "user_change"
    elif model == "res.groups":
        users_added, users_removed = _diff_ids(old_row.get("user_ids"), new_row.get("user_ids"))
        if users_added:
            granted.append(rid)
        if users_removed:
            revoked.append(rid)
        # implied_ids: anggota grup ini mendapat/kehilangan grup lain.
        implied_added, implied_removed = _diff_ids(old_row.get("implied_ids"), new_row.get("implied_ids"))
        granted += implied_added
        revoked += implied_removed
        # implied_by_ids: anggota grup lain kini mendapat/kehilangan grup ini.
        by_added, by_removed = _diff_ids(old_row.get("implied_by_ids"), new_row.get("implied_by_ids"))
        if by_added:
            granted.append(rid)
        if by_removed:
            revoked.append(rid)
        default_op = "group_change"
    else:
        return {}
    if not (granted or revoked):
        return {"audit_op": default_op}
    refs = group_refs(env, sorted(set(granted) | set(revoked)))
    extra = {
        "audit_op": "access_grant" if granted else "access_revoke",
        "critical": bool(granted) and is_critical_grant(env, sorted(set(granted))),
        "groups_added": sorted({refs[g] for g in granted}),
        "groups_removed": sorted({refs[g] for g in revoked}),
    }
    if users_added:
        extra["users_added"] = sorted(users_added)
    if users_removed:
        extra["users_removed"] = sorted(users_removed)
    return extra


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
                if rule.control:
                    # Model kontrol (SEC-49): field bernilai dari default/compute juga dicatat
                    # (mis. grup default user baru), bukan hanya yang ada di vals.
                    new = audited._efn_audit_read(rule.tracked)
                    for rid in audited.ids:
                        given = list(per_record.get(rid, ()))
                        row = new.get(rid, {})
                        given += [f for f in sorted(rule.tracked) if f not in given and _has_value(row.get(f))]
                        per_record[rid] = given
                else:
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
        trigger_uid = origin_uid(self.env)
        access = self._name in ACCESS_MODELS and self._name in PROTECTED_LOG_MODELS
        vals_list = []
        extras = []
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
                if operation == "write" and field.type in ("many2many", "one2many"):
                    # Selisih (dicabut / ditambah) agar tidak terpotong MAX_X2M_NAMES; ref = daftar lengkap.
                    added, removed = _diff_ids(o_raw, n_raw)
                    o_text = formatter.format_ids(field, removed, "dihapus: ")
                    n_text = formatter.format_ids(field, added, "ditambah: ")
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
                    "origin_user_id": trigger_uid,
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
            extras.append(
                access_extra(self.env, self._name, operation, record.id, old.get(record.id, {}), new.get(record.id, {}))
                if access
                else None
            )
        if vals_list:
            logs = self.env["efn.audit.log"].sudo().create(vals_list)
            if self._name in PROTECTED_LOG_MODELS:
                emit_sink(
                    self.env.cr,
                    [sink_event(v, log.id, extra) for v, log, extra in zip(vals_list, logs, extras, strict=True)],
                )


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
            if comodel in PERSONAL_MODELS:
                continue  # SEC-56: tanpa nama orang -> `model,id`
            recs = env[comodel].sudo().with_context(active_test=False).browse(sorted(ids)).exists()
            if comodel == "res.groups":
                self._names[comodel] = group_refs(env, recs.ids)  # SEC-49: xmlid, bukan label
            else:
                self._names[comodel] = {r.id: r.display_name or "" for r in recs}

    def _name(self, comodel, rid):
        if comodel in PERSONAL_MODELS:
            return f"{comodel},{rid}"
        return self._names.get(comodel, {}).get(rid) or f"{comodel},{rid}"

    def format_ids(self, field, ids, prefix):
        """Teks daftar id relasi (selisih x2many) dengan awalan, atau False bila kosong."""
        ids = [i for i in ids if isinstance(i, int)]
        if not ids:
            return False
        text = ", ".join(self._name(field.comodel_name, i) for i in ids[:MAX_X2M_NAMES])
        if len(ids) > MAX_X2M_NAMES:
            text += f" … (+{len(ids) - MAX_X2M_NAMES})"
        return _truncate(prefix + text)

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
