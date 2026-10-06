"""Log audit immutable (docs/08 §5.1, F-17; ADR-031; R-S10, R-B06, SEC-07, UU PDP).

- Ditulis hanya oleh hook `base` (models/base.py) via `sudo()`. Tidak ada grup dengan hak
  create/write/unlink (ir.model.access.csv).
- `write`/`unlink` SELALU ditolak (`UserError`), termasuk untuk admin/sudo, kecuali di dalam
  metode privat `_efn_audit_purge_*` yang memasang penanda di `cr.cache` (bukan context —
  context bisa diisi klien lewat RPC; pola sama dengan efn_pos_otp_gate).
- Retensi: `@api.autovacuum` `_gc_efn_audit_logs`. Erasure UU PDP: `_efn_audit_purge_records`.
"""

import logging
from contextlib import contextmanager
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

PURGE_KEY = "efn_audit_purge"
DEFAULT_RETENTION_DAYS = 730
GC_BATCH = 10000
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
    is_sudo = fields.Boolean(string="Via sudo")
    operation = fields.Selection(OPERATIONS, required=True, index=True)
    model_id = fields.Many2one("ir.model", string="Model", ondelete="set null")
    model_name = fields.Char(string="Nama Model", required=True, index=True)
    res_id = fields.Many2oneReference(string="ID Record", model_field="model_name", index=True)
    res_name = fields.Char(string="Record")
    company_id = fields.Many2one("res.company", string="Company", index=True, ondelete="set null")
    rule_id = fields.Many2one("efn.audit.rule", string="Aturan", ondelete="set null")
    line_ids = fields.One2many("efn.audit.log.line", "log_id", string="Perubahan")
    note = fields.Char(help="Hanya untuk operasi purge: jumlah & alasan, tanpa PII.")

    _model_res_idx = models.Index("(model_name, res_id)")

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
        return self.sudo().create(
            {
                "user_id": self.env.uid,
                "is_sudo": self.env.su,
                "operation": "purge",
                "model_name": model_name,
                "model_id": self.env["ir.model"]._get_id(model_name) if model_name in self.env else False,
                "note": note,
            }
        )

    @api.model
    def _efn_audit_purge_records(self, model_name, res_ids):
        """Erasure UU PDP (dipanggil efn_privacy): hapus log `(model_name, res_id ∈ res_ids)` dan
        kosongkan nilai/ref pada baris log model lain yang menunjuk record tsb. Menulis satu log
        `purge` tanpa id/nama. Mengembalikan jumlah log yang dihapus."""
        ids = {int(i) for i in res_ids or () if i}
        if not ids or not model_name:
            return 0
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
            self._efn_audit_write_purge_log(
                model_name, f"erasure: {count} log dihapus, {len(lines)} baris ref dikosongkan"
            )
        return count

    def _efn_audit_purge_logs(self, note):
        """Hapus log `self` (retensi) + tulis satu log purge."""
        count = len(self)
        if not count:
            return 0
        with _purge_allowed(self.env.cr):
            self.sudo().unlink()
        self._efn_audit_write_purge_log("efn.audit.log", note.format(count=count))
        return count

    @api.model
    def _efn_audit_global_retention(self):
        raw = self.env["ir.config_parameter"].sudo().get_param("efn.audit.retention_days")
        try:
            days = int(raw)
        except (TypeError, ValueError):
            return DEFAULT_RETENTION_DAYS
        return days if days >= 0 else DEFAULT_RETENTION_DAYS

    @api.autovacuum
    def _gc_efn_audit_logs(self):
        """Retensi: `rule.retention_days` (>0) atau parameter global `efn.audit.retention_days`
        (tidak valid/hilang -> 730; 0 = simpan selamanya). Maks GC_BATCH log per jalan."""
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
            cutoff = now - timedelta(days=rule.retention_days)
            found = Log.search([("rule_id", "=", rule.id), ("create_date", "<", cutoff)], limit=remaining, order="id")
            to_delete |= found
            remaining -= len(found)
        global_days = self._efn_audit_global_retention()
        if global_days and remaining > 0:
            domain = [("create_date", "<", now - timedelta(days=global_days))]
            if own_rules:
                domain.append(("rule_id", "not in", own_rules.ids))
            to_delete |= Log.search(domain, limit=remaining, order="id")
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
    field_id = fields.Many2one("ir.model.fields", string="Field", ondelete="set null")
    field_name = fields.Char(string="Nama Field", index=True)
    field_label = fields.Char(string="Label")
    old_value = fields.Text(string="Nilai Lama")
    new_value = fields.Text(string="Nilai Baru")
    old_ref = fields.Char(string="Ref Lama", help="model,id[,id...] untuk many2one/many2many")
    new_ref = fields.Char(string="Ref Baru")
    is_masked = fields.Boolean(string="Disamarkan")

    def write(self, vals):
        if not self.env.cr.cache.get(PURGE_KEY):
            raise UserError(_("Log audit tidak dapat diubah."))
        return super().write(vals)

    def unlink(self):
        if not self.env.cr.cache.get(PURGE_KEY):
            raise UserError(_("Log audit tidak dapat dihapus (hanya lewat retensi/erasure)."))
        return super().unlink()
