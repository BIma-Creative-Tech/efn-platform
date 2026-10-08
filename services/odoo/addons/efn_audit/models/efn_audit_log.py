"""Log audit immutable (docs/08 §5.1, F-17; ADR-031; R-S10, R-B06, SEC-07, UU PDP).

- Ditulis hanya oleh hook `base` (models/base.py) via `sudo()`. Tidak ada grup dengan hak
  create/write/unlink (ir.model.access.csv).
- `write`/`unlink` SELALU ditolak (`UserError`), termasuk untuk admin/sudo, kecuali di dalam
  metode privat `_efn_audit_purge_*` yang memasang penanda di `cr.cache` (bukan context —
  context bisa diisi klien lewat RPC; pola sama dengan efn_pos_otp_gate).
- Retensi: `@api.autovacuum` `_gc_efn_audit_logs`. Erasure UU PDP: `_efn_audit_purge_records`.
- Gelombang 11 (SEC-43): retensi efektif minimal `MIN_RETENTION_DAYS` (hard-coded); log operasi
  `purge` dan log model kontrol (`PROTECTED_LOG_MODELS`: aturan audit, parameter, server action,
  cron, modul, otomasi) TIDAK PERNAH dihapus GC; log purge juga dikirim ke sink (Loki).
- SEC-45: `is_personal` (log model orang / baris ter-mask) membatasi log tanpa company ke grup
  Auditor Data Pribadi Lintas PT. SEC-46: `prev_company_id` (company sebelum write).

Batas (diakui, README): metode privat dapat dipanggil kode server action oleh admin sistem;
mitigasinya = jejak server action yang tidak ikut GC + sink eksternal setelah commit.

Gelombang 13 (SEC-51 butir 4): `_efn_audit_purge_logs` menolak log yang belum melewati retensi
efektifnya (pemeriksaan umur di metode, bukan hanya di GC). Log purge yang tidak ditulis dari jalur
resmi (GC retensi / erasure, penanda `cr.cache`) diberi awalan `manual:` + `audit_op=purge_manual`
di sink, sehingga tidak lolos pengecualian alert `retensi:`/`erasure:`. Admin yang menulis kode
server action tetap dapat meniru penanda — menaikkan biaya, bukan mencegah (kontrol nyata SEC-43 b5).
"""

import logging
from contextlib import contextmanager
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .audit_policy import (
    DEFAULT_RETENTION_DAYS,
    PERSONAL_MODELS,
    PROTECTED_LOG_MODELS,
    RETENTION_PARAM,
    effective_retention,
    mask_free_text,
    parse_retention,
)
from .base import emit_sink, origin_uid, sink_event

_logger = logging.getLogger(__name__)

PURGE_KEY = "efn_audit_purge"
# SEC-51: jalur purge resmi ("retention" / "erasure") yang sedang berjalan di cursor ini.
PURGE_PATH_KEY = "efn_audit_purge_path"
GC_BATCH = 10000
# Domain yang dikecualikan dari GC retensi (SEC-43).
GC_PROTECTED_DOMAIN = [("operation", "!=", "purge"), ("model_name", "not in", sorted(PROTECTED_LOG_MODELS))]
OPERATIONS = [
    ("create", "Create"),
    ("write", "Write"),
    ("unlink", "Delete"),
    ("purge", "Purge"),
]


@contextmanager
def _purge_allowed(cr):
    """Izinkan write/unlink log selama blok ini saja (penanda per cursor/transaksi)."""
    previous = cr.cache.get(PURGE_KEY)
    cr.cache[PURGE_KEY] = True
    try:
        yield
    finally:
        if previous:
            cr.cache[PURGE_KEY] = previous
        else:
            cr.cache.pop(PURGE_KEY, None)


@contextmanager
def _purge_path(cr, path):
    """Tandai jalur purge resmi selama blok ini (per cursor, bukan context RPC)."""
    previous = cr.cache.get(PURGE_PATH_KEY)
    cr.cache[PURGE_PATH_KEY] = path
    try:
        yield
    finally:
        if previous:
            cr.cache[PURGE_PATH_KEY] = previous
        else:
            cr.cache.pop(PURGE_PATH_KEY, None)


def _ref_matches(ref, model_name, ids):
    """`ref` berformat `model,id[,id...]` (M2o / M2m). True bila menunjuk salah satu `ids`."""
    if not ref:
        return False
    parts = ref.split(",")
    if parts[0] != model_name:
        return False
    return any(p.isdigit() and int(p) in ids for p in parts[1:])


class EfnAuditLog(models.Model):
    _name = "efn.audit.log"
    _description = "EFN Audit Log"
    _order = "id desc"

    user_id = fields.Many2one("res.users", string="User", required=True, index=True, ondelete="restrict")
    origin_user_id = fields.Many2one(
        "res.users",
        string="User Pemicu",
        index="btree_not_null",
        ondelete="restrict",
        help="SEC-46: user manusia yang memicu perubahan bila `User` berbeda (mis. kode memakai "
        "with_user(SUPERUSER_ID) -> OdooBot). Dari sesi HTTP atau helper elevate_as_superuser; "
        "kosong bila sama dengan User atau tidak diketahui (cron tanpa helper).",
    )
    is_sudo = fields.Boolean(string="Via sudo")
    operation = fields.Selection(OPERATIONS, required=True, index=True)
    model_id = fields.Many2one("ir.model", string="Model", ondelete="set null")
    model_name = fields.Char(string="Nama Model", required=True, index=True)
    res_id = fields.Many2oneReference(string="ID Record", model_field="model_name", index=True)
    res_name = fields.Char(string="Record")
    company_id = fields.Many2one("res.company", string="Company", index=True, ondelete="set null")
    prev_company_id = fields.Many2one(
        "res.company",
        string="Company Sebelumnya",
        index="btree_not_null",
        ondelete="set null",
        help="Diisi bila write memindahkan record ke company lain (SEC-46).",
    )
    is_personal = fields.Boolean(
        string="Data Pribadi",
        compute="_compute_is_personal",
        store=True,
        index=True,
        help="Log model orang (kontak/user) atau memuat nilai ter-mask. Tanpa company hanya "
        "terbaca oleh grup Auditor Data Pribadi Lintas PT (SEC-45).",
    )
    rule_id = fields.Many2one("efn.audit.rule", string="Aturan", ondelete="set null")
    line_ids = fields.One2many("efn.audit.log.line", "log_id", string="Perubahan")
    note = fields.Char(help="Hanya untuk operasi purge: jumlah & alasan, tanpa PII.")

    _model_res_idx = models.Index("(model_name, res_id)")

    @api.depends("model_name", "line_ids.is_masked")
    def _compute_is_personal(self):
        for log in self:
            log.is_personal = log.model_name in PERSONAL_MODELS or any(log.line_ids.mapped("is_masked"))

    @api.depends("model_name", "res_id", "operation")
    def _compute_display_name(self):
        labels = dict(OPERATIONS)
        for log in self:
            ref = f"{log.model_name},{log.res_id}" if log.res_id else log.model_name
            log.display_name = f"{ref} ({labels.get(log.operation, log.operation)})"

    # ------------------------------------------------------- immutability
    def write(self, vals):
        if not self.env.cr.cache.get(PURGE_KEY):
            raise UserError(_("Log audit tidak dapat diubah."))
        return super().write(vals)

    def unlink(self):
        if not self.env.cr.cache.get(PURGE_KEY):
            raise UserError(_("Log audit tidak dapat dihapus (hanya lewat retensi/erasure)."))
        return super().unlink()

    # ------------------------------------------------------------- purge
    @api.model
    def _efn_audit_write_purge_log(self, model_name, note):
        official = self.env.cr.cache.get(PURGE_PATH_KEY)
        # SEC-78: catatan = teks bebas -> HP/email/NIK di-mask di sumber (DB + sink, sebelum MAC).
        note = mask_free_text(note)
        if not official:
            note = f"manual: {note}"
        vals = {
            "user_id": self.env.uid,
            "origin_user_id": origin_uid(self.env),
            "is_sudo": self.env.su,
            "operation": "purge",
            "model_name": model_name,
            "model_id": self.env["ir.model"]._get_id(model_name) if model_name in self.env else False,
            "note": note,
        }
        log = self.sudo().create(vals)
        extra = {"audit_op": "purge"} if official else {"audit_op": "purge_manual", "critical": True}
        emit_sink(self.env.cr, [sink_event(vals, log.id, extra)])
        return log

    @api.model
    def _efn_audit_purge_records(self, model_name, res_ids):
        """Erasure UU PDP (dipanggil efn_privacy): hapus log `(model_name, res_id ∈ res_ids)` dan
        kosongkan nilai/ref pada baris log model lain yang menunjuk record tsb. Menulis satu log
        `purge` tanpa id/nama. Mengembalikan jumlah log yang dihapus."""
        ids = {int(i) for i in res_ids or () if i}
        if not ids or not model_name:
            return 0
        if model_name in PROTECTED_LOG_MODELS:
            # Erasure hanya untuk data pribadi; log kontrol (SEC-43) tidak boleh dihapus lewat jalur ini.
            raise UserError(_("Log audit model kontrol %s tidak dapat di-purge.", model_name))
        Log = self.sudo().with_context(active_test=False)
        Line = self.env["efn.audit.log.line"].sudo()
        logs = Log.search([("model_name", "=", model_name), ("res_id", "in", list(ids))])
        prefix = f"{model_name},%"
        candidates = Line.search(
            [("log_id", "not in", logs.ids), "|", ("old_ref", "=like", prefix), ("new_ref", "=like", prefix)]
        )
        lines = candidates.filtered(
            lambda line: _ref_matches(line.old_ref, model_name, ids) or _ref_matches(line.new_ref, model_name, ids)
        )
        count = len(logs)
        with _purge_allowed(self.env.cr):
            if lines:
                lines.write({"old_value": False, "new_value": False, "old_ref": False, "new_ref": False})
            if logs:
                logs.unlink()
        if count or lines:
            with _purge_path(self.env.cr, "erasure"):
                self._efn_audit_write_purge_log(
                    model_name, f"erasure: {count} log dihapus, {len(lines)} baris ref dikosongkan"
                )
        return count

    def _efn_audit_purge_logs(self, note):
        """Hapus log `self` (retensi) + tulis satu log purge. Log terlindungi (purge/model
        kontrol, SEC-43) dan log yang belum melewati retensi efektifnya (SEC-51) tidak pernah ikut
        terhapus walau ada di `self`."""
        global_days = self._efn_audit_global_retention()
        now = fields.Datetime.now()

        def expired(log):
            own = log.rule_id.retention_days if log.rule_id else 0
            days = effective_retention(own) if own and own > 0 else global_days
            return bool(days) and bool(log.create_date) and log.create_date < now - timedelta(days=days)

        logs = self.sudo().filtered(
            lambda log: log.operation != "purge" and log.model_name not in PROTECTED_LOG_MODELS and expired(log)
        )
        count = len(logs)
        if not count:
            return 0
        with _purge_allowed(self.env.cr):
            logs.sudo().unlink()
        self._efn_audit_write_purge_log("efn.audit.log", note.format(count=count))
        return count

    @api.model
    def _efn_audit_global_retention(self):
        """Hari retensi global efektif. Hilang/tidak valid -> 730; 0 = selamanya; nilai > 0 di
        bawah minimum (mis. ditulis lewat SQL melewati constraint) -> minimum (SEC-43)."""
        days = parse_retention(self.env["ir.config_parameter"].sudo().get_param(RETENTION_PARAM))
        if days is None:
            return DEFAULT_RETENTION_DAYS
        return effective_retention(days)

    @api.autovacuum
    def _gc_efn_audit_logs(self):
        """Retensi: `rule.retention_days` (>0) atau parameter global `efn.audit.retention_days`
        (tidak valid/hilang -> 730; 0 = simpan selamanya), keduanya minimal `MIN_RETENTION_DAYS`.
        Log purge & log model kontrol tidak pernah dihapus. Maks GC_BATCH log per jalan."""
        Log = self.sudo().with_context(active_test=False)
        now = fields.Datetime.now()
        remaining = GC_BATCH
        to_delete = Log.browse()
        own_rules = (
            self.env["efn.audit.rule"].sudo().with_context(active_test=False).search([("retention_days", ">", 0)])
        )
        for rule in own_rules:
            if remaining <= 0:
                break
            cutoff = now - timedelta(days=effective_retention(rule.retention_days))
            found = Log.search(
                [("rule_id", "=", rule.id), ("create_date", "<", cutoff), *GC_PROTECTED_DOMAIN],
                limit=remaining,
                order="id",
            )
            to_delete |= found
            remaining -= len(found)
        global_days = self._efn_audit_global_retention()
        if global_days and remaining > 0:
            domain = [("create_date", "<", now - timedelta(days=global_days)), *GC_PROTECTED_DOMAIN]
            if own_rules:
                domain.append(("rule_id", "not in", own_rules.ids))
            to_delete |= Log.search(domain, limit=remaining, order="id")
        with _purge_path(self.env.cr, "retention"):
            count = to_delete._efn_audit_purge_logs("retensi: {count} log")
        if count:
            _logger.info("EFN audit: retensi menghapus %d log", count)
        return count


class EfnAuditLogLine(models.Model):
    _name = "efn.audit.log.line"
    _description = "EFN Audit Log Line"
    _order = "log_id desc, id"

    log_id = fields.Many2one("efn.audit.log", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="log_id.company_id", store=True, index=True)
    prev_company_id = fields.Many2one(related="log_id.prev_company_id", store=True, index="btree_not_null")
    is_personal = fields.Boolean(related="log_id.is_personal", store=True, index=True)
    field_id = fields.Many2one("ir.model.fields", string="Field", ondelete="set null")
    field_name = fields.Char(string="Nama Field", index=True)
    field_label = fields.Char(string="Label")
    old_value = fields.Text(string="Nilai Lama")
    new_value = fields.Text(string="Nilai Baru")
    old_ref = fields.Char(string="Ref Lama", help="model,id[,id...] untuk many2one/many2many")
    new_ref = fields.Char(string="Ref Baru")
    is_masked = fields.Boolean(string="Disamarkan")

    # SEC-46 (Gelombang 12): `_efn_audit_purge_records` mencari `old_ref/new_ref =like "model,%"`.
    # btree text_pattern_ops mendukung LIKE prefix di collation non-C; parsial (hanya baris ber-ref,
    # yaitu field relasi) agar kecil. Diverifikasi EXPLAIN di test_audit_privacy.
    _old_ref_prefix_idx = models.Index("(old_ref text_pattern_ops) WHERE old_ref IS NOT NULL")
    _new_ref_prefix_idx = models.Index("(new_ref text_pattern_ops) WHERE new_ref IS NOT NULL")

    def write(self, vals):
        if not self.env.cr.cache.get(PURGE_KEY):
            raise UserError(_("Log audit tidak dapat diubah."))
        return super().write(vals)

    def unlink(self):
        if not self.env.cr.cache.get(PURGE_KEY):
            raise UserError(_("Log audit tidak dapat dihapus (hanya lewat retensi/erasure)."))
        return super().unlink()
