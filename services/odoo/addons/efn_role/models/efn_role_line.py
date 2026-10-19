"""`efn.role.line`: penetapan peran ke user, bertanggal (E-47, docs/08 §12.3 butir 2).

`is_effective` = Boolean tersimpan yang HANYA ditulis kode server (penanda `cr.cache`): tanggal hari ini
(Asia/Jakarta) dalam [date_from, date_to] dan peran aktif. Diperbarui saat baris/peran berubah dan oleh cron
harian 00:30 WIB. Penulisan `is_effective` lewat RPC ditolak (mencegah mengaktifkan baris masa depan).
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.efn_audit.models.audit_policy import mask_free_text

from .role_policy import INTERNAL_KEY, TECHNICAL_GROUP_XMLIDS, cache_flag, efn_today, has_flag, xmlid_group_ids


class EfnRoleLine(models.Model):
    _name = "efn.role.line"
    _description = "EFN Role Assignment"
    _order = "role_id, date_from desc, id desc"
    _check_company_auto = True

    role_id = fields.Many2one("efn.role", string="Peran", required=True, index=True, ondelete="restrict")
    user_id = fields.Many2one(
        "res.users", string="User", required=True, index=True, ondelete="cascade", domain=[("share", "=", False)]
    )
    company_id = fields.Many2one(related="role_id.company_id", store=True, index=True, string="Company")
    date_from = fields.Date(string="Mulai", default=lambda self: efn_today(self.env))
    date_to = fields.Date(string="Sampai")
    source = fields.Selection(
        [("manual", "Manual"), ("job", "Jabatan")], string="Sumber", required=True, default="manual"
    )
    is_effective = fields.Boolean(string="Efektif", readonly=True, copy=False, index=True)
    reason = fields.Text(string="Alasan", help="Wajib untuk peran admin tanpa tanggal akhir.")

    @api.depends("role_id.name")
    def _compute_display_name(self):
        # Tanpa nama user (log audit `res_name` = display_name; SEC-45 minimisasi).
        for line in self:
            line.display_name = f"{line.role_id.name or ''} #{line.id or '-'}"

    # ------------------------------------------------------------ constraints
    @api.constrains("date_from", "date_to")
    def _check_dates(self):
        for line in self:
            if line.date_from and line.date_to and line.date_to < line.date_from:
                raise ValidationError(_("efn_role:line_dates — tanggal akhir peran sebelum tanggal mulai."))

    @api.constrains("user_id")
    def _check_user(self):
        technical = xmlid_group_ids(self.env, TECHNICAL_GROUP_XMLIDS)
        for line in self.sudo():
            user = line.user_id.with_context(active_test=False)
            if user.share:
                raise ValidationError(_("efn_role:line_share_user — peran hanya untuk user internal."))
            if technical and set(user.all_group_ids.ids) & technical:
                raise ValidationError(
                    _(
                        "efn_role:line_technical_user — user teknis (kanal online / perangkat KDS) tidak boleh "
                        "diberi peran."
                    )
                )

    @api.constrains("reason", "date_to", "role_id")
    def _check_reason(self):
        admin_roles = self.sudo().role_id._efn_admin_roles()
        for line in self.sudo():
            if line.role_id in admin_roles and not line.date_to and not (line.reason or "").strip():
                raise ValidationError(
                    _("efn_role:line_admin_reason — peran admin tanpa tanggal akhir wajib diberi alasan.")
                )

    # ------------------------------------------------------------------- CRUD
    def _efn_check_admin_rights(self):
        # sudo() tetap membawa env.user asli -> has_group memeriksa pemanggil sebenarnya.
        self.sudo().role_id._efn_check_admin_rights()

    @api.model_create_multi
    def create(self, vals_list):
        internal = has_flag(self.env.cr, INTERNAL_KEY)
        for vals in vals_list:
            if not internal:
                vals.pop("is_effective", None)
            if vals.get("reason"):
                vals["reason"] = mask_free_text(vals["reason"])
        lines = super().create(vals_list)
        lines._efn_check_admin_rights()
        users = lines.sudo().user_id.with_context(active_test=False)
        fresh = users.filtered(lambda u: not u.efn_role_managed)
        if fresh:
            with cache_flag(self.env.cr, INTERNAL_KEY):
                fresh.write({"efn_role_managed": True})
        lines._efn_refresh_effective(apply=False)
        self.env["efn.role"]._efn_apply_roles(users)
        return lines

    def write(self, vals):
        internal = has_flag(self.env.cr, INTERNAL_KEY)
        if "is_effective" in vals and not internal:
            raise UserError(_("efn_role:line_effective_readonly — status efektif dihitung server."))
        if internal and set(vals) <= {"is_effective"}:
            return super().write(vals)
        if {"role_id", "user_id"} & set(vals):
            for line in self:
                if ("role_id" in vals and vals["role_id"] != line.role_id.id) or (
                    "user_id" in vals and vals["user_id"] != line.user_id.id
                ):
                    raise UserError(_("efn_role:line_locked — ganti peran/user dengan baris baru."))
        if vals.get("reason"):
            vals["reason"] = mask_free_text(vals["reason"])
        self._efn_check_admin_rights()
        res = super().write(vals)
        self._efn_check_admin_rights()
        self._efn_refresh_effective(apply=False)
        self.env["efn.role"]._efn_apply_roles(self.sudo().user_id)
        return res

    def unlink(self):
        self._efn_check_admin_rights()
        users = self.sudo().user_id.with_context(active_test=False)
        res = super().unlink()
        self.env["efn.role"]._efn_apply_roles(users)
        return res

    # ------------------------------------------------------------ efektif
    def _efn_refresh_effective(self, today=None, apply=True):
        """Hitung ulang `is_effective`; kembalikan baris yang berubah (dan terapkan peran user terdampak)."""
        today = today or efn_today(self.env)
        to_true = self.browse()
        to_false = self.browse()
        for line in self.sudo().exists():
            value = bool(
                line.role_id.active
                and (not line.date_from or line.date_from <= today)
                and (not line.date_to or line.date_to >= today)
            )
            if value != line.is_effective:
                if value:
                    to_true |= line
                else:
                    to_false |= line
        with cache_flag(self.env.cr, INTERNAL_KEY):
            if to_true:
                to_true.sudo().write({"is_effective": True})
            if to_false:
                to_false.sudo().write({"is_effective": False})
        changed = to_true | to_false
        if apply and changed:
            self.env["efn.role"]._efn_apply_roles(changed.sudo().user_id)
        return changed
