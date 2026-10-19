"""Review akses bulanan per PT (E-47c, docs/08 §12.5b; docs/04 §13.3 no. 4; A-5, R-S14; L-65).

`efn.access.review.line` = SNAPSHOT tersimpan (bukan view SQL): bukti tidak berubah walau grup diubah kemudian.
Satu baris per (user internal company review, grup efektif). Grup efektif dihitung saat snapshot (penutup
implied_ids, sama dengan `all_group_ids`). Sumber: `role` (eksplisit & dari peran efektif), `direct` (eksplisit
di luar peran), `implied` (tersirat). Pelanggaran SoD (aturan aktif, kedua mode) & pengecualian (efektif /
kedaluwarsa) ditandai per baris.

Status `draft -> in_review -> done`; `done` = read-only untuk semua (termasuk sudo) kecuali chatter. Baca: Auditor
(`efn_audit.group_auditor`, TANPA hak Access Rights) + Access Rights; tulis status: Auditor lewat tombol.
Data pribadi: hanya nama & login user internal (identitas kerja, 18-40). Retensi = `efn.audit.retention_days`.

Ekspor (SEC-155, L-67): Auditor TIDAK diberi `base.group_allow_export` (grup itu membuka ekspor massal semua model
yang bisa dibaca, termasuk kontak terdekripsi). Ekspor review lewat `action_efn_export_csv`: Auditor/Superadmin +
record rule company; CSV dibangun HANYA dari baris snapshot review ini, kolom tanpa login/login terakhir/2FA (hanya
nama user internal, grup, peran, SoD); disimpan sebagai attachment review (ikut terhapus bersama review/GC).
"""

import csv
import io
import logging
import re
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.fields import Command

from odoo.addons.base.models.ir_model import MODULE_UNINSTALL_FLAG
from odoo.addons.efn_audit.models.audit_policy import (
    DEFAULT_RETENTION_DAYS,
    RETENTION_PARAM,
    effective_retention,
    mask_free_text,
    parse_retention,
)

from .role_policy import (
    AUTO_GROUP_XMLIDS,
    INTERNAL_KEY,
    REVIEW_CATCHUP_DAYS,
    STALE_LOGIN_DAYS,
    auditor_users,
    cache_flag,
    closure,
    efn_today,
    has_flag,
    implied_map,
    previous_period,
    xmlid_group_ids,
)

_logger = logging.getLogger(__name__)

PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
MAIL_PREFIXES = ("message_", "activity_")
COUNT_FIELDS = (
    "user_count",
    "sod_violation_count",
    "exception_count",
    "expired_exception_count",
    "direct_grant_count",
    "stale_login_count",
    "no_2fa_count",
    "users_without_role_count",
)
SERVER_FIELDS = frozenset({"state", "reviewer_id", "reviewed_on", "snapshot_date", "line_ids", *COUNT_FIELDS})
# Kolom CSV ekspor review (SEC-155): tanpa login, login terakhir, 2FA, atau data kontak.
EXPORT_HEADER = (
    "period",
    "company",
    "uid",
    "user_name",
    "user_active",
    "company_roles",
    "privilege",
    "group",
    "source",
    "via_role",
    "sod_codes",
    "sod_unexcepted",
    "exception_state",
)
# Awalan sel yang dieksekusi spreadsheet sebagai rumus (CSV/formula injection; nama user bisa diubah pemiliknya).
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value):
    if value is None or value is False:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    text = str(value)
    if text.startswith(FORMULA_PREFIXES):
        return "'" + text
    return text


class EfnAccessReview(models.Model):
    _name = "efn.access.review"
    _description = "EFN Monthly Access Review (per company)"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "period desc, company_id"
    _check_company_auto = True

    name = fields.Char(compute="_compute_name", store=True)
    company_id = fields.Many2one(
        "res.company", string="Company", required=True, index=True, default=lambda self: self.env.company
    )
    period = fields.Char(
        string="Periode",
        required=True,
        index=True,
        default=lambda self: previous_period(efn_today(self.env)),
        help="YYYY-MM bulan yang direview (snapshot diambil saat review dibuat / diambil ulang).",
    )
    state = fields.Selection(
        [("draft", "Draft"), ("in_review", "Sedang Direview"), ("done", "Selesai")],
        string="Status",
        required=True,
        default="draft",
        readonly=True,
        tracking=True,
    )
    reviewer_id = fields.Many2one("res.users", string="Reviewer", readonly=True, ondelete="restrict", tracking=True)
    reviewed_on = fields.Datetime(string="Direview pada", readonly=True)
    snapshot_date = fields.Datetime(string="Snapshot", readonly=True)
    notes = fields.Text(string="Catatan / Temuan")
    line_ids = fields.One2many("efn.access.review.line", "review_id", string="Baris", readonly=True)
    user_count = fields.Integer(string="User", readonly=True)
    sod_violation_count = fields.Integer(string="Pelanggaran SoD", readonly=True)
    exception_count = fields.Integer(string="Pengecualian Aktif", readonly=True)
    expired_exception_count = fields.Integer(string="Pengecualian Kedaluwarsa", readonly=True)
    direct_grant_count = fields.Integer(string="Pemberian Grup Langsung", readonly=True)
    stale_login_count = fields.Integer(string=f"Tanpa Login > {STALE_LOGIN_DAYS} hari", readonly=True)
    no_2fa_count = fields.Integer(string="Tanpa 2FA", readonly=True)
    users_without_role_count = fields.Integer(string="User tanpa Peran di PT", readonly=True)

    _company_period_uniq = models.Constraint("unique(company_id, period)", "Satu review akses per company per periode.")

    @api.depends("company_id.name", "period")
    def _compute_name(self):
        for review in self:
            review.name = _(
                "Review Akses %(company)s %(period)s",
                company=review.company_id.name or "",
                period=review.period or "",
            )

    @api.constrains("period")
    def _check_period(self):
        for review in self:
            if not PERIOD_RE.match(review.period or ""):
                raise ValidationError(_("efn_role:review_period — periode harus berformat YYYY-MM."))

    # ------------------------------------------------------------------- CRUD
    @api.model_create_multi
    def create(self, vals_list):
        if not has_flag(self.env.cr, INTERNAL_KEY):
            for vals in vals_list:
                for key in SERVER_FIELDS:
                    vals.pop(key, None)
                if vals.get("notes"):
                    vals["notes"] = mask_free_text(vals["notes"])
        reviews = super().create(vals_list)
        reviews._efn_take_snapshot()
        return reviews

    def write(self, vals):
        if has_flag(self.env.cr, INTERNAL_KEY):
            return super().write(vals)
        keys = {k for k in vals if not k.startswith(MAIL_PREFIXES)}
        if not keys:
            return super().write(vals)
        if any(review.state == "done" for review in self):
            raise UserError(_("efn_role:review_done_readonly — review yang selesai tidak dapat diubah."))
        if keys & SERVER_FIELDS:
            raise UserError(_("efn_role:review_server_fields — status & snapshot hanya lewat tombol review."))
        if keys & {"company_id", "period"} and any(review.state != "draft" for review in self):
            raise UserError(_("efn_role:review_locked — company/periode hanya bisa diubah saat draft."))
        if vals.get("notes"):
            vals["notes"] = mask_free_text(vals["notes"])
        res = super().write(vals)
        if keys & {"company_id", "period"}:
            self._efn_take_snapshot()
        return res

    def unlink(self):
        if not (has_flag(self.env.cr, INTERNAL_KEY) or self.env.context.get(MODULE_UNINSTALL_FLAG)):
            raise UserError(_("efn_role:review_no_delete — review akses adalah bukti audit dan tidak dapat dihapus."))
        return super().unlink()

    # ---------------------------------------------------------------- tombol
    def _efn_check_caller(self, auditor_only):
        user = self.env.user
        if auditor_only:
            allowed = user.has_group("efn_audit.group_auditor")
        else:
            allowed = user.has_group("efn_audit.group_auditor") or user.has_group("base.group_erp_manager")
        if not allowed:
            raise AccessError(_("efn_role:review_access — hanya Auditor (status) / Access Rights (snapshot)."))

    def action_efn_snapshot(self):
        """RPC publik (RPC_ALLOWLIST): Auditor/Access Rights + check_access(read) + record rule; hanya draft."""
        self._efn_check_caller(auditor_only=False)
        self.check_access("read")
        self._efn_take_snapshot()
        return True

    def action_efn_start_review(self):
        """RPC publik (RPC_ALLOWLIST): Auditor + check_access(write); draft -> in_review."""
        self._efn_check_caller(auditor_only=True)
        self.check_access("write")
        if any(review.state != "draft" for review in self):
            raise UserError(_("efn_role:review_state — hanya review draft yang bisa mulai direview."))
        with cache_flag(self.env.cr, INTERNAL_KEY):
            self.sudo().write({"state": "in_review", "reviewer_id": self.env.uid})
        return True

    def action_efn_done(self):
        """RPC publik (RPC_ALLOWLIST): Auditor + check_access(write); in_review -> done (read-only)."""
        self._efn_check_caller(auditor_only=True)
        self.check_access("write")
        if any(review.state != "in_review" for review in self):
            raise UserError(_("efn_role:review_state — hanya review yang sedang direview yang bisa diselesaikan."))
        with cache_flag(self.env.cr, INTERNAL_KEY):
            self.sudo().write({"state": "done", "reviewer_id": self.env.uid, "reviewed_on": fields.Datetime.now()})
        for review in self.sudo():
            review.message_post(body=_("Review akses ditandatangani reviewer (uid %s).", self.env.uid))
        return True

    def action_efn_export_csv(self):
        """RPC publik (RPC_ALLOWLIST, SEC-155): Auditor/Superadmin + check_access(read) + record rule company.

        Membangun CSV dari baris snapshot review ini saja (sudo SETELAH cek), menyimpannya sebagai attachment review,
        dan mengembalikan aksi unduh. Semua status (draft/in_review/done); tidak mengubah review.
        """
        self.ensure_one()
        user = self.env.user
        if not (user.has_group("efn_audit.group_auditor") or user.has_group("base.group_system")):
            raise AccessError(_("efn_role:review_export_access — ekspor review akses hanya Auditor / Superadmin."))
        self.check_access("read")
        review = self.sudo()
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\r\n")
        writer.writerow(EXPORT_HEADER)
        source_labels = dict(self.env["efn.access.review.line"]._fields["source"].selection)
        for line in review.line_ids:
            writer.writerow(
                [
                    _csv_cell(value)
                    for value in (
                        review.period,
                        review.company_id.name,
                        line.user_id.id or "",
                        line.user_id.name,
                        line.user_active,
                        line.role_names,
                        line.privilege_name,
                        line.group_full_name,
                        source_labels.get(line.source, line.source),
                        line.via_role_name,
                        line.sod_codes,
                        line.sod_unexcepted,
                        line.exception_state,
                    )
                ]
            )
        # BOM: Excel membaca UTF-8 dengan benar.
        data = ("﻿" + buffer.getvalue()).encode("utf-8")
        attachment = (
            self.env["ir.attachment"]
            .sudo()
            .create(
                {
                    "name": f"access_review_{review.company_id.id}_{review.period}.csv",
                    "type": "binary",
                    "raw": data,
                    "mimetype": "text/csv",
                    "res_model": self._name,
                    "res_id": review.id,
                    "company_id": review.company_id.id,
                }
            )
        )
        review.message_post(
            body=_(
                "Ekspor CSV review akses oleh uid %(uid)s (%(rows)s baris).",
                uid=self.env.uid,
                rows=len(review.line_ids),
            ),
            subtype_xmlid="mail.mt_note",
        )
        _logger.info(
            "EFN_ACCESS_REVIEW_EXPORT review=%s uid=%s rows=%s attachment=%s",
            review.id,
            self.env.uid,
            len(review.line_ids),
            attachment.id,
        )
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{attachment.id}?download=true",
            "target": "self",
        }

    # -------------------------------------------------------------- snapshot
    def _efn_take_snapshot(self):
        for review in self.sudo():
            if review.state != "draft":
                raise UserError(_("efn_role:review_state — snapshot hanya untuk review draft."))
            line_vals, counts = review._efn_build_snapshot()
            with cache_flag(self.env.cr, INTERNAL_KEY):
                review.line_ids.unlink()
                self.env["efn.access.review.line"].sudo().create(line_vals)
                review.write(dict(counts, snapshot_date=fields.Datetime.now()))

    def _efn_build_snapshot(self):
        """(vals baris, hitungan) untuk review `self` (sudo)."""
        self.ensure_one()
        env = self.env
        company = self.company_id
        today = efn_today(env)
        now = fields.Datetime.now()
        Users = env["res.users"].sudo().with_context(active_test=False)
        root = env.ref("base.user_root")  # OdooBot (__system__), bukan user manusia
        users = Users.search([("share", "=", False), ("company_ids", "in", company.id), ("id", "!=", root.id)])
        graph = implied_map(env)
        auto_ids = xmlid_group_ids(env, AUTO_GROUP_XMLIDS)
        has_totp = "totp_enabled" in Users._fields
        Rule = env["efn.sod.rule"].sudo()
        # Aturan aktif (kedua mode) yang berlaku untuk PT ini; context active_test pemanggil diabaikan.
        rules = Rule.with_context(active_test=True).search(
            [("active", "=", True), ("company_id", "in", [False, company.id])]
        )
        Exc = env["efn.sod.exception"].sudo().with_context(active_test=True)
        exceptions = Exc.search(
            [("active", "=", True), ("user_id", "in", users.ids), ("rule_id", "in", rules.ids)],
            order="date_to desc, id desc",
        )
        lines = (
            env["efn.role.line"]
            .sudo()
            .search([("user_id", "in", users.ids), ("is_effective", "=", True), ("role_id.active", "=", True)])
        )
        stale_before = now - timedelta(days=STALE_LOGIN_DAYS)
        line_vals = []
        counts = dict.fromkeys(COUNT_FIELDS, 0)
        counts["user_count"] = len(users)
        violations = set()
        effective_exc = set()
        expired_exc = set()
        for user in users:
            explicit = set(user.group_ids.ids)
            groups = env["res.groups"].sudo().browse(sorted(closure(explicit, graph))).exists()
            own = lines.filtered(lambda ln, u=user: ln.user_id == u)
            own_company = own.filtered(lambda ln: ln.company_id == company)
            # Grup -> peran asal (utamakan peran company review).
            via = {}
            for line in own_company + (own - own_company):
                for gid in line.role_id.group_ids.ids:
                    via.setdefault(gid, line.role_id)
            role_names = ", ".join(own_company.role_id.mapped("name"))
            gids = set(groups.ids)
            hit_rules = rules.filtered(lambda r, g=gids: r.group_a_id.id in g and r.group_b_id.id in g)
            rule_exc = {}
            for rule in hit_rules:
                mine = exceptions.filtered(lambda e, r=rule, u=user: e.rule_id == r and e.user_id == u)
                current = mine.filtered(lambda e: e.active and e.date_from <= today <= e.date_to)[:1]
                lapsed = mine.filtered(lambda e: e.active and e.date_to < today)[:1]
                rule_exc[rule.id] = (current, lapsed)
                if user.active:
                    if current:
                        effective_exc.add(current.id)
                    else:
                        violations.add((rule.id, user.id))
                        if lapsed:
                            expired_exc.add(lapsed.id)
            if user.active:
                if not user.login_date or user.login_date < stale_before:
                    counts["stale_login_count"] += 1
                if has_totp and not user.totp_enabled:
                    counts["no_2fa_count"] += 1
                if not own_company:
                    counts["users_without_role_count"] += 1
            totp = bool(user.totp_enabled) if has_totp else False
            for group in groups:
                if group.id in explicit:
                    source = "role" if group.id in via else "direct"
                else:
                    source = "implied"
                role = via.get(group.id) if source == "role" else env["efn.role"]
                rules_here = hit_rules.filtered(lambda r, g=group: g.id in (r.group_a_id.id, r.group_b_id.id))
                exception = env["efn.sod.exception"]
                exception_state = "none"
                for rule in rules_here:
                    current, lapsed = rule_exc[rule.id]
                    if current:
                        exception, exception_state = current, "effective"
                        break
                    if lapsed and not exception:
                        exception, exception_state = lapsed, "expired"
                if source == "direct" and group.id not in auto_ids and user.active:
                    counts["direct_grant_count"] += 1
                line_vals.append(
                    {
                        "review_id": self.id,
                        "user_id": user.id,
                        "user_login": user.login,
                        "user_active": user.active,
                        "login_date": user.login_date,
                        "totp_enabled": totp,
                        "role_names": role_names,
                        "has_company_role": bool(own_company),
                        "group_id": group.id,
                        "group_full_name": group.full_name or group.name,
                        "privilege_name": group.privilege_id.name or False,
                        "source": source,
                        "via_role_id": role.id if role and role.company_id == company else False,
                        "via_role_name": role.name if role else False,
                        "sod_rule_ids": [Command.set(rules_here.ids)],
                        "sod_codes": ", ".join(rules_here.mapped("code")) or False,
                        "sod_unexcepted": bool(rules_here) and exception_state != "effective",
                        "exception_id": exception.id or False,
                        "exception_state": exception_state,
                    }
                )
        counts["sod_violation_count"] = len(violations)
        counts["exception_count"] = len(effective_exc)
        counts["expired_exception_count"] = len(expired_exc)
        return line_vals, counts

    # ------------------------------------------------------------------ cron
    @api.model
    def _cron_efn_access_review_monthly(self, today=None):
        """Harian 02:30 WIB; membuat review periode lalu per company bila tanggal (WIB) <= 3 dan belum ada
        (tanggal 1 + toleransi bila cron terlewat). Aktivitas ke auditor company."""
        today = today or efn_today(self.env)
        if today.day > REVIEW_CATCHUP_DAYS:
            return self.browse()
        period = previous_period(today)
        created = self.browse()
        Review = self.sudo()
        for company in self.env["res.company"].sudo().search([]):
            if Review.search_count([("company_id", "=", company.id), ("period", "=", period)]):
                continue
            try:
                with self.env.cr.savepoint():
                    review = Review.create({"company_id": company.id, "period": period})
                    review._efn_notify_auditors()
                    created |= review
            except (UserError, ValidationError, AccessError) as exc:
                _logger.warning("EFN_ACCESS_REVIEW_FAIL company=%s error=%s", company.id, type(exc).__name__)
        return created

    def _efn_notify_auditors(self):
        for review in self.sudo():
            auditors = auditor_users(self.env, review.company_id)
            if not auditors:
                _logger.warning("EFN_ACCESS_REVIEW_NO_AUDITOR review=%s company=%s", review.id, review.company_id.id)
            for auditor in auditors:
                review.activity_schedule(
                    "mail.mail_activity_data_todo",
                    user_id=auditor.id,
                    summary=_("Review akses bulanan"),
                    note=_(
                        "Review akses %(period)s siap: %(viol)s pelanggaran SoD, %(exc)s pengecualian aktif, "
                        "%(expired)s pengecualian kedaluwarsa.",
                        period=review.period,
                        viol=review.sod_violation_count,
                        exc=review.exception_count,
                        expired=review.expired_exception_count,
                    ),
                )

    @api.autovacuum
    def _gc_efn_access_reviews(self):
        """Retensi = parameter efn.audit.retention_days (0 = selamanya; minimum efn_audit berlaku)."""
        raw = self.env["ir.config_parameter"].sudo().get_param(RETENTION_PARAM, DEFAULT_RETENTION_DAYS)
        days = parse_retention(raw)
        days = effective_retention(DEFAULT_RETENTION_DAYS if days is None else days)
        if not days:
            return
        cutoff = fields.Datetime.now() - timedelta(days=days)
        old = self.sudo().search([("state", "=", "done"), ("reviewed_on", "<", cutoff)])
        if old:
            _logger.info("EFN_ACCESS_REVIEW_GC count=%s", len(old))
            with cache_flag(self.env.cr, INTERNAL_KEY):
                old.unlink()


class EfnAccessReviewLine(models.Model):
    _name = "efn.access.review.line"
    _description = "EFN Access Review Snapshot Line"
    _order = "review_id, user_id, privilege_name, group_full_name, id"
    _check_company_auto = True
    _rec_name = "group_full_name"

    review_id = fields.Many2one("efn.access.review", required=True, index=True, ondelete="cascade")
    company_id = fields.Many2one(related="review_id.company_id", store=True, index=True, string="Company")
    review_state = fields.Selection(related="review_id.state", string="Status Review")
    user_id = fields.Many2one("res.users", string="User", index=True, ondelete="set null")
    user_login = fields.Char(string="Login")
    user_active = fields.Boolean(string="User Aktif")
    login_date = fields.Datetime(string="Login Terakhir")
    totp_enabled = fields.Boolean(string="2FA")
    role_names = fields.Char(string="Peran (PT ini)")
    has_company_role = fields.Boolean(string="Punya Peran di PT")
    group_id = fields.Many2one("res.groups", string="Grup", index=True, ondelete="set null")
    group_full_name = fields.Char(string="Nama Grup")
    privilege_name = fields.Char(string="Privilege")
    source = fields.Selection(
        [("role", "Peran"), ("direct", "Langsung"), ("implied", "Tersirat")], string="Sumber", index=True
    )
    via_role_id = fields.Many2one("efn.role", string="Lewat Peran", ondelete="set null")
    via_role_name = fields.Char(string="Nama Peran Asal")
    sod_rule_ids = fields.Many2many(
        "efn.sod.rule", "efn_access_review_line_sod_rule_rel", "line_id", "rule_id", string="Aturan SoD Dilanggar"
    )
    sod_codes = fields.Char(string="Kode SoD")
    sod_unexcepted = fields.Boolean(string="SoD tanpa Pengecualian Efektif", index=True)
    exception_id = fields.Many2one("efn.sod.exception", string="Pengecualian", ondelete="set null")
    exception_state = fields.Selection(
        [("none", "-"), ("effective", "Efektif"), ("expired", "Kedaluwarsa")], string="Status Pengecualian"
    )

    @api.model_create_multi
    def create(self, vals_list):
        if not has_flag(self.env.cr, INTERNAL_KEY):
            raise UserError(_("efn_role:review_line_snapshot_only — baris review hanya dibuat oleh snapshot."))
        return super().create(vals_list)

    def write(self, vals):
        if not has_flag(self.env.cr, INTERNAL_KEY):
            raise UserError(_("efn_role:review_line_immutable — baris snapshot tidak dapat diubah."))
        return super().write(vals)

    def unlink(self):
        if not (has_flag(self.env.cr, INTERNAL_KEY) or self.env.context.get(MODULE_UNINSTALL_FLAG)):
            raise UserError(_("efn_role:review_line_immutable — baris snapshot tidak dapat dihapus."))
        return super().unlink()
