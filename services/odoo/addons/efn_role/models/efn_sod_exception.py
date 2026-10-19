"""`efn.sod.exception`: pengecualian SoD per (aturan, user) (E-47, docs/08 §12.3 butir 5; L-65).

L-65 (klien: 1 Superadmin, R-B11): dibuat HANYA oleh Superadmin (`base.group_system`) dengan alasan wajib
(>= 20 karakter), berlaku maks. 90 hari (default 30), notifikasi otomatis (aktivitas) ke grup Auditor, dan tampil
di review akses bulanan. Bukan persetujuan dua Superadmin. Tetap: pemberi != penerima (Superadmin tidak
mengecualikan dirinya sendiri).

Tidak bisa diubah setelah dibuat kecuali dicabut (`active=False`); tanpa unlink; tanpa pengaktifan ulang
(buat pengecualian baru). Kedaluwarsa: grup TIDAK dicabut otomatis; cron harian membuat aktivitas untuk Superadmin,
dan penulisan grup berikutnya untuk user itu ditolak sampai pelanggaran diselesaikan.
"""

import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.base.models.ir_model import MODULE_UNINSTALL_FLAG
from odoo.addons.efn_audit.models.audit_policy import mask_free_text

from .role_policy import (
    EXCEPTION_DEFAULT_DAYS,
    EXCEPTION_MAX_DAYS,
    EXCEPTION_MIN_REASON,
    INTERNAL_KEY,
    auditor_users,
    cache_flag,
    efn_today,
    has_flag,
    superadmin_users,
)

_logger = logging.getLogger(__name__)

MAIL_PREFIXES = ("message_", "activity_")
SERVER_FIELDS = frozenset({"granted_by_id", "company_id", "expiry_notified", "revoked_by_id", "revoked_on"})


class EfnSodException(models.Model):
    _name = "efn.sod.exception"
    _description = "EFN Segregation of Duties Exception"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_to desc, id desc"
    _check_company_auto = True

    rule_id = fields.Many2one(
        "efn.sod.rule", string="Aturan", required=True, index=True, ondelete="cascade", check_company=True
    )
    user_id = fields.Many2one(
        "res.users", string="User", required=True, index=True, ondelete="cascade", domain=[("share", "=", False)]
    )
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        readonly=True,
        index=True,
        help="Company aturan, atau company utama user untuk aturan global (diisi server).",
    )
    reason = fields.Text(string="Alasan", required=True)
    granted_by_id = fields.Many2one(
        "res.users",
        string="Diberikan oleh",
        required=True,
        readonly=True,
        ondelete="restrict",
        default=lambda self: self.env.user,
    )
    date_from = fields.Date(string="Mulai", required=True, default=lambda self: efn_today(self.env))
    date_to = fields.Date(
        string="Sampai",
        required=True,
        default=lambda self: efn_today(self.env) + timedelta(days=EXCEPTION_DEFAULT_DAYS),
    )
    active = fields.Boolean(default=True)
    state = fields.Selection(
        [("scheduled", "Terjadwal"), ("effective", "Efektif"), ("expired", "Kedaluwarsa"), ("revoked", "Dicabut")],
        compute="_compute_state",
        string="Status",
    )
    expiry_notified = fields.Boolean(readonly=True, copy=False)
    revoked_by_id = fields.Many2one("res.users", string="Dicabut oleh", readonly=True, ondelete="restrict")
    revoked_on = fields.Datetime(string="Dicabut pada", readonly=True)

    @api.depends("rule_id.code")
    def _compute_display_name(self):
        for exc in self:
            exc.display_name = f"{exc.rule_id.code or ''} #{exc.id or '-'}"

    @api.depends("active", "date_from", "date_to")
    def _compute_state(self):
        today = efn_today(self.env)
        for exc in self:
            if not exc.active:
                exc.state = "revoked"
            elif exc.date_to and exc.date_to < today:
                exc.state = "expired"
            elif exc.date_from and exc.date_from > today:
                exc.state = "scheduled"
            else:
                exc.state = "effective"

    # ------------------------------------------------------------------- CRUD
    @api.model_create_multi
    def create(self, vals_list):
        user = self.env.user
        if not user.has_group("base.group_system"):
            raise AccessError(_("efn_role:sod_exception_superadmin_only — pengecualian SoD hanya oleh Superadmin."))
        Rule = self.env["efn.sod.rule"].sudo()
        Users = self.env["res.users"].sudo().with_context(active_test=False)
        for vals in vals_list:
            if vals.get("granted_by_id") not in (None, False, user.id):
                raise ValidationError(_("efn_role:sod_exception_granter — pemberi pengecualian = user yang login."))
            for key in SERVER_FIELDS:
                vals.pop(key, None)
            vals["granted_by_id"] = user.id
            vals["active"] = True
            vals["reason"] = mask_free_text((vals.get("reason") or "").strip())
            if len(vals["reason"]) < EXCEPTION_MIN_REASON:
                raise ValidationError(
                    _("efn_role:sod_exception_reason — alasan wajib, minimal %s karakter.", EXCEPTION_MIN_REASON)
                )
            rule = Rule.browse(vals.get("rule_id")).exists()
            target = Users.browse(vals.get("user_id")).exists()
            vals["company_id"] = rule.company_id.id or target.company_id.id or self.env.company.id
        records = super().create(vals_list)
        records._efn_validate()
        records._efn_notify_auditors()
        return records

    def _efn_validate(self):
        today = efn_today(self.env)
        for exc in self.sudo():
            if not exc.granted_by_id.has_group("base.group_system"):
                raise ValidationError(_("efn_role:sod_exception_granter — pemberi harus Superadmin."))
            if exc.granted_by_id == exc.user_id:
                raise ValidationError(
                    _(
                        "efn_role:sod_exception_self — Superadmin tidak dapat memberi pengecualian untuk "
                        "dirinya sendiri."
                    )
                )
            if exc.user_id.share:
                raise ValidationError(_("efn_role:sod_exception_share_user — hanya untuk user internal."))
            if len((exc.reason or "").strip()) < EXCEPTION_MIN_REASON:
                raise ValidationError(
                    _("efn_role:sod_exception_reason — alasan wajib, minimal %s karakter.", EXCEPTION_MIN_REASON)
                )
            if exc.date_from < today:
                raise ValidationError(_("efn_role:sod_exception_backdated — tanggal mulai tidak boleh di masa lalu."))
            if exc.date_to < exc.date_from:
                raise ValidationError(_("efn_role:sod_exception_dates — tanggal akhir sebelum tanggal mulai."))
            if (exc.date_to - exc.date_from).days > EXCEPTION_MAX_DAYS:
                raise ValidationError(
                    _("efn_role:sod_exception_too_long — masa berlaku maksimal %s hari.", EXCEPTION_MAX_DAYS)
                )

    def write(self, vals):
        # Recordset kosong (mis. `toggle_active` CE menulis active=True ke sisa kosong) tidak mengubah apa pun.
        if not self or has_flag(self.env.cr, INTERNAL_KEY):
            return super().write(vals)
        keys = {k for k in vals if not k.startswith(MAIL_PREFIXES)}
        if not keys:
            return super().write(vals)
        if keys != {"active"} or vals.get("active"):
            raise UserError(
                _(
                    "efn_role:sod_exception_immutable — pengecualian tidak dapat diubah; cabut (arsipkan) lalu buat "
                    "yang baru."
                )
            )
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("efn_role:sod_exception_superadmin_only — pencabutan hanya oleh Superadmin."))
        to_revoke = self.filtered("active")
        res = super().write(vals)
        if to_revoke:
            with cache_flag(self.env.cr, INTERNAL_KEY):
                to_revoke.sudo().write({"revoked_by_id": self.env.uid, "revoked_on": fields.Datetime.now()})
            for exc in to_revoke.sudo():
                exc.message_post(body=_("Pengecualian SoD dicabut."), subtype_xmlid="mail.mt_note")
        return res

    def unlink(self):
        if not self.env.context.get(MODULE_UNINSTALL_FLAG):
            raise UserError(_("efn_role:sod_exception_no_delete — pengecualian tidak dapat dihapus; cabut (arsipkan)."))
        return super().unlink()

    # ---------------------------------------------------------------- query
    @api.model
    def _efn_effective_map(self, rules, user_ids, today=None):
        """{(rule_id, uid): pengecualian efektif hari ini}."""
        if not rules or not user_ids:
            return {}
        today = today or efn_today(self.env)
        found = (
            self.sudo()
            .with_context(active_test=True)
            .search(
                [
                    ("active", "=", True),
                    ("rule_id", "in", rules.ids),
                    ("user_id", "in", list(user_ids)),
                    ("date_from", "<=", today),
                    ("date_to", ">=", today),
                ],
                order="date_to desc, id desc",
            )
        )
        result = {}
        for exc in found:
            result.setdefault((exc.rule_id.id, exc.user_id.id), exc)
        return result

    # ----------------------------------------------------------- notifikasi
    def _efn_notify_auditors(self):
        """L-65: aktivitas To-Do untuk auditor (bukan admin hak akses) company pengecualian + catatan chatter."""
        for exc in self.sudo():
            note = _(
                "Pengecualian SoD %(code)s dibuat Superadmin (uid %(granter)s) untuk uid %(uid)s, %(start)s s/d "
                "%(end)s. Tinjau di review akses bulanan.",
                code=exc.rule_id.code,
                granter=exc.granted_by_id.id,
                uid=exc.user_id.id,
                start=exc.date_from,
                end=exc.date_to,
            )
            exc.message_post(body=note, subtype_xmlid="mail.mt_note")
            auditors = auditor_users(self.env, exc.company_id)
            if not auditors:
                _logger.warning("EFN_SOD_EXCEPTION_NO_AUDITOR exception=%s company=%s", exc.id, exc.company_id.id)
            for auditor in auditors:
                exc.activity_schedule(
                    "mail.mail_activity_data_todo",
                    user_id=auditor.id,
                    summary=_("Pengecualian SoD baru"),
                    note=note,
                )
            _logger.info(
                "EFN_SOD_EXCEPTION_CREATED exception=%s rule=%s uid=%s granter=%s auditors=%s",
                exc.id,
                exc.rule_id.code,
                exc.user_id.id,
                exc.granted_by_id.id,
                len(auditors),
            )

    @api.model
    def _efn_notify_expired(self, today=None):
        """Cron harian: pengecualian kedaluwarsa -> aktivitas Superadmin (sekali), tanpa mencabut grup."""
        today = today or efn_today(self.env)
        expired = (
            self.sudo()
            .with_context(active_test=True)
            .search([("active", "=", True), ("date_to", "<", today), ("expiry_notified", "=", False)])
        )
        for exc in expired:
            note = _(
                "Pengecualian SoD %(code)s #%(id)s kedaluwarsa %(end)s. Grup tidak dicabut otomatis: selesaikan "
                "pelanggaran (cabut salah satu grup) atau buat pengecualian baru. Penulisan grup user ini ditolak "
                "sampai diselesaikan.",
                code=exc.rule_id.code,
                id=exc.id,
                end=exc.date_to,
            )
            for admin in superadmin_users(self.env, exc.company_id):
                exc.activity_schedule(
                    "mail.mail_activity_data_todo",
                    user_id=admin.id,
                    summary=_("Pengecualian SoD kedaluwarsa"),
                    note=note,
                )
            with cache_flag(self.env.cr, INTERNAL_KEY):
                exc.write({"expiry_notified": True})
            _logger.warning(
                "EFN_SOD_EXCEPTION_EXPIRED exception=%s rule=%s uid=%s", exc.id, exc.rule_id.code, exc.user_id.id
            )
        return expired
