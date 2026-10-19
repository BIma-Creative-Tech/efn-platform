"""`efn.role`: peran per PT = jabatan (opsional) -> himpunan `res.groups` (E-47, docs/08 §12.3 butir 1).

Penerapan peran (`_efn_apply_roles`) — ide "nothing more, nothing less" dari OCA `base_user_role` (LGPL-3,
referensi desain saja, ADR-031), ditulis ulang:
- `managed` = gabungan grup SEMUA peran (aktif & arsip, semua company) + grup penanda efn_role, minus
  `base.group_user` (selalu dipertahankan untuk user internal);
- `desired` = gabungan grup peran dari baris efektif user;
- grup eksplisit baru = (grup user - managed) | desired | {base.group_user} -> grup non-peran tidak disentuh;
- `company_ids` user += company baris efektif (tidak pernah dikurangi otomatis).
Hanya user ber-flag `efn_role_managed` yang disentuh (admin bawaan, user teknis, user test modul lain aman).

Grup Odoo bersifat GLOBAL (bukan per company): peran per company hanya menentukan company yang ditambahkan.
SoD dievaluasi global per user (docs/08 §12.2 catatan multi-company).
"""

import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.fields import Command

from odoo.addons.efn_audit.models.audit_policy import mask_free_text

from .role_policy import (
    ADMIN_GROUP_XMLIDS,
    APPLY_KEY,
    MARKER_GROUP_XMLIDS,
    TECHNICAL_GROUP_XMLIDS,
    cache_flag,
    closure,
    efn_today,
    implied_map,
    xmlid_group_ids,
)

_logger = logging.getLogger(__name__)


class EfnRole(models.Model):
    _name = "efn.role"
    _description = "EFN Role (job -> groups, per company)"
    _order = "company_id, sequence, name, id"
    _check_company_auto = True

    name = fields.Char(required=True)
    company_id = fields.Many2one(
        "res.company", string="Company", required=True, index=True, default=lambda self: self.env.company
    )
    job_id = fields.Many2one(
        "hr.job",
        string="Jabatan",
        check_company=True,
        index="btree_not_null",
        ondelete="restrict",
        help="Opsional. Kosong = peran tanpa jabatan (mis. Terminal POS). Karyawan ber-user dengan jabatan ini "
        "di company yang sama mendapat baris peran sumber 'Jabatan' (tombol / cron harian).",
    )
    group_ids = fields.Many2many(
        "res.groups",
        "efn_role_res_groups_rel",
        "role_id",
        "group_id",
        string="Grup",
        domain=[("share", "=", False)],
        help="Grup eksplisit peran. Grup yang tersirat (implied_ids) tidak perlu ditulis ulang.",
    )
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    note = fields.Text(string="Catatan")
    line_ids = fields.One2many("efn.role.line", "role_id", string="Penetapan")
    user_count = fields.Integer(string="User Efektif", compute="_compute_user_count")
    is_admin_role = fields.Boolean(
        string="Peran Admin",
        compute="_compute_is_admin_role",
        help="Memuat (langsung/tersirat) base.group_system atau base.group_erp_manager: hanya Superadmin.",
    )

    _company_job_uniq = models.UniqueIndex(
        "(company_id, job_id) WHERE job_id IS NOT NULL",
        "Satu peran per jabatan per company.",
    )

    # ---------------------------------------------------------------- compute
    @api.depends("line_ids.is_effective", "line_ids.user_id")
    def _compute_user_count(self):
        for role in self:
            role.user_count = len(role.line_ids.filtered("is_effective").user_id)

    @api.depends("group_ids")
    def _compute_is_admin_role(self):
        # Tampilan saja (ORM all_implied_ids); penegakan memakai _efn_admin_roles (graf SQL terkini).
        admin_ids = xmlid_group_ids(self.env, ADMIN_GROUP_XMLIDS)
        for role in self:
            groups = role.group_ids.sudo()
            role.is_admin_role = bool(set((groups | groups.all_implied_ids).ids) & admin_ids)

    def _efn_admin_roles(self):
        """Subset peran yang memuat grup admin (dihitung dari data terkini, tanpa cache compute)."""
        admin_ids = xmlid_group_ids(self.env, ADMIN_GROUP_XMLIDS)
        graph = implied_map(self.env)
        return self.sudo().filtered(lambda r: closure(r.group_ids.ids, graph) & admin_ids)

    # ------------------------------------------------------------ constraints
    @api.constrains("group_ids")
    def _check_group_ids(self):
        technical = xmlid_group_ids(self.env, TECHNICAL_GROUP_XMLIDS)
        for role in self:
            if not role.group_ids:
                raise ValidationError(_("efn_role:role_no_group — peran %s harus memuat minimal satu grup.", role.name))
            if role.group_ids.filtered("share"):
                raise ValidationError(_("efn_role:role_share_group — peran tidak boleh memuat grup portal/publik."))
            if set(role.group_ids.ids) & technical:
                raise ValidationError(
                    _("efn_role:role_technical_group — grup akun teknis (kanal online / perangkat KDS) bukan peran.")
                )

    # ----------------------------------------------------------- hak admin
    def _efn_check_admin_rights(self):
        """Peran berisi grup admin hanya boleh dibuat/diubah/ditetapkan oleh base.group_system (docs/08 §12.3)."""
        if self.env.user.has_group("base.group_system"):
            return
        if self._efn_admin_roles():
            raise AccessError(
                _(
                    "efn_role:admin_role_superadmin_only — peran yang memuat grup Administrator / Access Rights "
                    "hanya dapat dikelola Superadmin."
                )
            )

    # ------------------------------------------------------------------- CRUD
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("note"):
                vals["note"] = mask_free_text(vals["note"])
        roles = super().create(vals_list)
        roles._efn_check_admin_rights()
        if roles.line_ids:
            # Baris dibuat bersama peran (form one2many): baris sudah menerapkan dirinya; pastikan sekali lagi.
            self._efn_apply_roles(roles.line_ids.user_id)
        return roles

    def write(self, vals):
        if vals.get("note"):
            vals["note"] = mask_free_text(vals["note"])
        if "company_id" in vals and any(
            role.line_ids and role.company_id.id != vals["company_id"] for role in self.sudo()
        ):
            raise UserError(_("efn_role:role_company_locked — company peran yang sudah ditetapkan tidak bisa diubah."))
        self._efn_check_admin_rights()
        relevant = {"group_ids", "active"} & set(vals)
        old_groups = set(self.sudo().with_context(active_test=False).group_ids.ids) if relevant else set()
        res = super().write(vals)
        self._efn_check_admin_rights()
        if relevant:
            lines = self.sudo().with_context(active_test=False).line_ids
            lines._efn_refresh_effective(apply=False)
            # Grup yang dikeluarkan dari peran tetap dianggap terkelola untuk penerapan ini -> dicabut dari user.
            self._efn_apply_roles(lines.user_id, extra_managed=old_groups)
        return res

    # ------------------------------------------------------------ penerapan
    @api.model
    def _efn_managed_group_ids(self):
        roles = self.env["efn.role"].sudo().with_context(active_test=False).search([])
        ids = set(roles.group_ids.ids) | xmlid_group_ids(self.env, MARKER_GROUP_XMLIDS)
        group_user = self.env.ref("base.group_user", raise_if_not_found=False)
        if group_user is not None:
            ids.discard(group_user.id)
        return ids

    @api.model
    def _efn_apply_roles(self, users, extra_managed=()):
        """Terapkan peran ke `users` ber-flag `efn_role_managed` (user lain diabaikan)."""
        users = users.sudo().with_context(active_test=False).filtered("efn_role_managed")
        if not users:
            return
        group_user = self.env.ref("base.group_user")
        managed = self._efn_managed_group_ids() | set(extra_managed)
        managed.discard(group_user.id)
        Line = self.env["efn.role.line"].sudo()
        lines = Line.search([("user_id", "in", users.ids), ("is_effective", "=", True), ("role_id.active", "=", True)])
        for user in users:
            own = lines.filtered(lambda line, u=user: line.user_id == u)
            desired = set(own.role_id.group_ids.ids)
            current = set(user.group_ids.ids)
            target = (current - managed) | desired | {group_user.id}
            vals = {}
            if target != current:
                vals["group_ids"] = [Command.set(sorted(target))]
            missing = own.company_id - user.company_ids
            if missing:
                vals["company_ids"] = [Command.link(company.id) for company in missing]
            if vals:
                with cache_flag(self.env.cr, APPLY_KEY):
                    user.write(vals)
                _logger.info(
                    "EFN_ROLE_APPLY uid=%s added=%s removed=%s companies_added=%s",
                    user.id,
                    sorted(target - current),
                    sorted(current - target),
                    missing.ids,
                )

    # ------------------------------------------------------------ jabatan
    def action_efn_apply_job_roles(self):
        """Tombol 'Terapkan peran dari jabatan' (RPC publik, RPC_ALLOWLIST): grup Access Rights + hak tulis."""
        if not self.env.user.has_group("base.group_erp_manager"):
            raise AccessError(_("efn_role:job_sync_access — hanya admin hak akses."))
        self.check_access("write")
        self._efn_check_admin_rights()
        self._efn_sync_job_lines(roles=self, raise_errors=True)
        return True

    @api.model
    def _efn_sync_job_lines(self, roles=None, raise_errors=False, today=None):
        """Baris sumber 'job' mengikuti jabatan karyawan (hr.employee.job_id = jabatan versi aktif, 19.0).

        Peran admin TIDAK pernah disinkron dari jabatan (cegah eskalasi lewat penetapan jabatan oleh HR):
        penetapan peran admin = baris manual oleh Superadmin. Baris 'manual' tidak disentuh.
        """
        today = today or efn_today(self.env)
        yesterday = today - timedelta(days=1)
        Role = self.env["efn.role"].sudo().with_context(active_test=False)
        roles = (roles.sudo().with_context(active_test=False) if roles is not None else Role.search([])).exists()
        admin_roles = roles._efn_admin_roles()
        Employee = self.env["hr.employee"].sudo()
        Line = self.env["efn.role.line"].sudo()
        for role in roles:
            targets = self.env["res.users"]
            if role.active and role.job_id and role not in admin_roles:
                employees = Employee.search([("company_id", "=", role.company_id.id), ("user_id", "!=", False)])
                targets = employees.filtered(lambda e, r=role: e.job_id == r.job_id).user_id
            elif role in admin_roles and role.job_id:
                _logger.info("EFN_ROLE_JOB_SKIP_ADMIN role=%s", role.id)
            current = Line.search(
                [
                    ("role_id", "=", role.id),
                    ("source", "=", "job"),
                    "|",
                    ("date_to", "=", False),
                    ("date_to", ">=", today),
                ]
            )
            for user in targets - current.user_id:
                self._efn_job_step(
                    lambda r=role, u=user: Line.create(
                        {"role_id": r.id, "user_id": u.id, "source": "job", "date_from": today}
                    ),
                    raise_errors,
                    role,
                    user,
                )
            for line in current.filtered(lambda ln, t=targets: ln.user_id not in t):
                if line.date_from and line.date_from > yesterday:
                    # Dibuat hari ini lalu jabatan berubah: tidak ada riwayat untuk ditutup (unlink tercatat audit).
                    self._efn_job_step(line.unlink, raise_errors, role, line.user_id)
                else:
                    self._efn_job_step(
                        lambda ln=line: ln.write({"date_to": yesterday}), raise_errors, role, line.user_id
                    )

    @api.model
    def _efn_job_step(self, func, raise_errors, role, user):
        if raise_errors:
            func()
            return
        try:
            with self.env.cr.savepoint():
                func()
        except (UserError, ValidationError, AccessError) as exc:
            _logger.warning("EFN_ROLE_JOB_FAIL role=%s uid=%s error=%s", role.id, user.id, type(exc).__name__)

    # ----------------------------------------------------------------- cron
    @api.model
    def _cron_efn_role_daily(self, today=None):
        """00:30 Asia/Jakarta: masa berlaku baris, penerapan ulang, sinkron jabatan, pengecualian kedaluwarsa,
        aturan SoD bawaan. Setiap langkah per user/peran di savepoint sendiri (satu kegagalan tidak menghentikan)."""
        today = today or efn_today(self.env)
        Line = self.env["efn.role.line"].sudo()
        Line.search([])._efn_refresh_effective(today=today, apply=False)
        users = self.env["res.users"].sudo().with_context(active_test=False).search([("efn_role_managed", "=", True)])
        for user in users:
            try:
                with self.env.cr.savepoint():
                    self._efn_apply_roles(user)
            except (UserError, ValidationError, AccessError) as exc:
                _logger.warning("EFN_ROLE_APPLY_FAIL uid=%s error=%s", user.id, type(exc).__name__)
        self._efn_sync_job_lines(raise_errors=False, today=today)
        self.env["efn.sod.exception"]._efn_notify_expired(today=today)
        self.env["efn.sod.rule"]._efn_ensure_default_rules()
