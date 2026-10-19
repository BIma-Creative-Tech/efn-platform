"""`efn.sod.rule`: pasangan grup yang tidak boleh dimiliki satu user (E-47, docs/08 §12.3 butir 4, §12.4; L-64).

Penegakan (`_efn_enforce`) memakai grup EFEKTIF (penutup implied_ids) yang dihitung dari tabel relasi lewat SQL
setelah flush — menutup pemberian langsung maupun lewat `implied_ids` grup lain, dan tidak bergantung pada cache
`groups` CE. Titik pemeriksaan: `res.users` (constraint group_ids/company_ids/active), `res.groups` (create/write
implied_ids, implied_by_ids, user_ids), penerapan peran, dan transisi aturan ke `block`.

`res_groups` CE tidak punya konsep SoD (`disjoint_ids` hanya tipe user internal/portal/publik, 19.0 [V]).
Selama registry belum siap (install/upgrade modul), penegakan dilewati; pelanggaran tetap muncul di review akses.
"""

import logging
from collections import namedtuple

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.base.models.ir_model import MODULE_UNINSTALL_FLAG
from odoo.addons.efn_audit.models.audit_policy import mask_free_text

from .role_policy import closure, explicit_memberships, implied_map

_logger = logging.getLogger(__name__)

Violation = namedtuple("Violation", "rule uid exception")

# (code, nama, xmlid grup A, xmlid grup B, mode bawaan, alasan) — docs/08 §12.4. SOD-04 tidak dibuat (CE `loyalty`
# tanpa alur persetujuan program). SOD-05/06 report_only menunggu Q-ORG-4.
DEFAULT_SOD_RULES = (
    (
        "SOD-01",
        "Sales Manager ⟂ Approver Refund CS",
        "sales_team.group_sale_manager",
        "efn_role.group_refund_approver",
        "block",
        "Sales Manager tidak menyetujui refund penjualannya sendiri (A-5, 18-24). Approver = grup host BFF "
        "efn-cs-approver, dicerminkan grup penanda.",
    ),
    (
        "SOD-02",
        "Kasir ⟂ POS Administrator (Store Manager)",
        "efn_role.group_role_cashier",
        "point_of_sale.group_pos_manager",
        "block",
        "Kasir tidak menyetujui retur tanpa struk / selisih kasnya sendiri (E-41).",
    ),
    (
        "SOD-03",
        "Loyalty Manager ⟂ Accounting Manager",
        "efn_loyalty_accounting.group_loyalty_manager",
        "account.group_account_manager",
        "block",
        "Pembuat kebijakan poin bukan penyetujunya (E-44, docs/04 §13.1 no. 2).",
    ),
    (
        "SOD-05",
        "Pengadaan ⟂ Accounting Manager (usulan)",
        "purchase.group_purchase_user",
        "account.group_account_manager",
        "report_only",
        "Pembuat PO bukan penyetuju/pembayar tagihan vendor. Menunggu Q-ORG-4.",
    ),
    (
        "SOD-06",
        "Auditor ⟂ Access Rights (usulan)",
        "efn_audit.group_auditor",
        "base.group_erp_manager",
        "report_only",
        "Auditor tidak mengatur hak akses yang ia review. Catatan: Superadmin selalu memegang keduanya (CE: "
        "group_system -> efn_audit.group_manager -> group_auditor). Menunggu Q-ORG-4.",
    ),
)

KEY_FIELDS = frozenset({"mode", "active", "group_a_id", "group_b_id", "company_id"})


class EfnSodRule(models.Model):
    _name = "efn.sod.rule"
    _description = "EFN Segregation of Duties Rule"
    _order = "code, id"
    _check_company_auto = True
    _rec_name = "code"

    code = fields.Char(required=True, index=True, help="Kode unik, mis. SOD-01.")
    name = fields.Char(required=True)
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        index=True,
        help="Kosong = global (default & rekomendasi: grup Odoo berlaku lintas company). Diisi = hanya berlaku "
        "bagi user yang memiliki company ini di company_ids.",
    )
    group_a_id = fields.Many2one("res.groups", string="Grup A", required=True, ondelete="cascade")
    group_b_id = fields.Many2one("res.groups", string="Grup B", required=True, ondelete="cascade")
    mode = fields.Selection(
        [("block", "Blokir"), ("report_only", "Laporan saja")], required=True, default="block", string="Mode"
    )
    active = fields.Boolean(default=True)
    rationale = fields.Text(string="Alasan")
    exception_ids = fields.One2many("efn.sod.exception", "rule_id", string="Pengecualian")
    violation_count = fields.Integer(string="Pelanggaran", compute="_compute_violation_count")

    _code_uniq = models.Constraint("unique(code)", "Kode aturan SoD harus unik.")

    @api.depends("group_a_id", "group_b_id", "company_id", "mode")
    def _compute_violation_count(self):
        saved = self.filtered(lambda r: isinstance(r.id, int) and r.group_a_id and r.group_b_id)
        per_rule = {}
        if saved:
            uids = self._efn_internal_user_ids()
            for v in self._efn_violations(uids, rules=saved.sudo()):
                if not v.exception:
                    per_rule.setdefault(v.rule.id, set()).add(v.uid)
        for rule in self:
            rule.violation_count = len(per_rule.get(rule.id, ()))

    # ------------------------------------------------------------ constraints
    @api.constrains("group_a_id", "group_b_id", "company_id")
    def _check_pair(self):
        Rule = self.sudo().with_context(active_test=False)
        for rule in self:
            if rule.group_a_id == rule.group_b_id:
                raise ValidationError(_("efn_role:sod_same_group — grup A dan B harus berbeda."))
            a, b = rule.group_a_id.id, rule.group_b_id.id
            duplicate = Rule.search_count(
                [
                    ("id", "!=", rule.id),
                    ("company_id", "=", rule.company_id.id or False),
                    "|",
                    "&",
                    ("group_a_id", "=", a),
                    ("group_b_id", "=", b),
                    "&",
                    ("group_a_id", "=", b),
                    ("group_b_id", "=", a),
                ]
            )
            if duplicate:
                raise ValidationError(_("efn_role:sod_duplicate_pair — pasangan grup ini sudah punya aturan."))

    # ------------------------------------------------------------------- CRUD
    def _efn_check_admin(self):
        if not (self.env.su or self.env.user.has_group("base.group_system")):
            raise AccessError(_("efn_role:sod_rule_superadmin_only — aturan SoD hanya dikelola Superadmin."))

    @api.model_create_multi
    def create(self, vals_list):
        self._efn_check_admin()
        for vals in vals_list:
            if vals.get("rationale"):
                vals["rationale"] = mask_free_text(vals["rationale"])
        rules = super().create(vals_list)
        rules._efn_check_block_transition()
        return rules

    def write(self, vals):
        self._efn_check_admin()
        if vals.get("rationale"):
            vals["rationale"] = mask_free_text(vals["rationale"])
        res = super().write(vals)
        if KEY_FIELDS & set(vals):
            self._efn_check_block_transition()
        return res

    def unlink(self):
        if not self.env.context.get(MODULE_UNINSTALL_FLAG):
            raise UserError(
                _("efn_role:sod_rule_no_delete — aturan SoD tidak dapat dihapus; arsipkan (tercatat audit).")
            )
        return super().unlink()

    def _efn_check_block_transition(self):
        """Aturan yang (menjadi) aktif + block ditolak bila masih ada pelanggaran tanpa pengecualian (jumlah saja)."""
        rules = self.sudo().filtered(lambda r: r.active and r.mode == "block")
        if not rules:
            return
        uids = self._efn_internal_user_ids()
        for rule in rules:
            count = len({v.uid for v in self._efn_violations(uids, rules=rule) if not v.exception})
            if not count:
                continue
            if not self.pool.ready:
                _logger.warning(
                    "EFN_SOD_RULE_BLOCK_WITH_VIOLATIONS rule=%s count=%s (registry loading)", rule.code, count
                )
                continue
            raise ValidationError(
                _(
                    "efn_role:sod_rule_violations — %(code)s tidak bisa diaktifkan sebagai Blokir: %(count)s "
                    "pelanggaran tanpa pengecualian. Selesaikan dulu (lihat Review Akses) atau buat pengecualian.",
                    code=rule.code,
                    count=count,
                )
            )

    # ----------------------------------------------------------------- engine
    @api.model
    def _efn_internal_user_ids(self):
        Users = self.env["res.users"].sudo()
        return Users.search([("share", "=", False)]).ids

    @api.model
    def _efn_active_rules(self, domain=()):
        """Aturan AKTIF saja, apa pun `active_test` di context pemanggil (mis. penerapan peran memakai
        active_test=False pada user -> constraint mewarisi context itu)."""
        return self.sudo().with_context(active_test=True).search([("active", "=", True), *domain])

    @api.model
    def _efn_has_block_rules(self):
        return bool(self._efn_active_rules([("mode", "=", "block")]))

    @api.model
    def _efn_violations(self, user_ids, rules=None, modes=("block",), exclude_group_ids=(), today=None):
        """[Violation(rule, uid, exception)] untuk user aktif internal; `exception` = pengecualian efektif atau
        recordset kosong. `exclude_group_ids`: hitung seolah grup itu tidak ada (baseline saat create grup)."""
        if rules is None:
            rules = self._efn_active_rules([("mode", "in", list(modes))])
        rules = rules.sudo()
        user_ids = [uid for uid in set(user_ids or ()) if isinstance(uid, int)]
        if not rules or not user_ids:
            return []
        exclude = frozenset(exclude_group_ids or ())
        graph = implied_map(self.env)
        explicit = explicit_memberships(self.env, user_ids)
        group_user = self.env.ref("base.group_user").id
        users = self.env["res.users"].sudo().with_context(active_test=False).browse(user_ids).exists()
        effective = self.env["efn.sod.exception"]._efn_effective_map(rules, users.ids, today=today)
        result = []
        for user in users:
            if not user.active:
                continue
            groups = closure(explicit.get(user.id, ()), graph, exclude)
            if group_user not in groups:
                continue  # portal/publik: SoD internal tidak relevan
            companies = set(user.company_ids.ids)
            for rule in rules:
                if rule.company_id and rule.company_id.id not in companies:
                    continue
                if rule.group_a_id.id in groups and rule.group_b_id.id in groups:
                    exc = effective.get((rule.id, user.id)) or self.env["efn.sod.exception"]
                    result.append(Violation(rule, user.id, exc))
        return result

    @api.model
    def _efn_violation_keys(self, user_ids, **kwargs):
        return {(v.rule.id, v.uid) for v in self._efn_violations(user_ids, **kwargs) if not v.exception}

    @api.model
    def _efn_enforce(self, user_ids, baseline=None, exclude_group_ids=()):
        """Tolak (ValidationError) pelanggaran `block` tanpa pengecualian pada `user_ids`.

        `baseline`: kunci (rule_id, uid) yang sudah melanggar sebelum operasi (tidak dianggap baru; dipakai jalur
        res.groups agar pelanggaran lama user lain tidak memblokir perubahan grup yang tidak terkait).
        Superadmin/sudo TIDAK kebal (kontrak: pengecualian hanya diberikan Superadmin, dengan alasan).
        """
        if not self.pool.ready or not user_ids:
            return
        rules = self._efn_active_rules([("mode", "=", "block")])
        if not rules:
            return
        found = [v for v in self._efn_violations(user_ids, rules=rules) if not v.exception]
        if exclude_group_ids:
            base = self._efn_violation_keys(user_ids, rules=rules, exclude_group_ids=exclude_group_ids)
            baseline = (baseline or set()) | base
        if baseline:
            found = [v for v in found if (v.rule.id, v.uid) not in baseline]
        if found:
            self._efn_raise(found)

    @api.model
    def _efn_raise(self, violations):
        by_rule = {}
        for v in violations:
            by_rule.setdefault(v.rule, set()).add(v.uid)
            # Log tanpa PII: kode aturan + uid saja.
            _logger.warning("EFN_SOD_BLOCK rule=%s uid=%s", v.rule.code, v.uid)
        details = "; ".join(
            _(
                '%(code)s: grup "%(a)s" dan "%(b)s" tidak boleh dimiliki satu user (%(count)s user)',
                code=rule.code,
                a=rule.group_a_id.sudo().full_name or rule.group_a_id.sudo().name,
                b=rule.group_b_id.sudo().full_name or rule.group_b_id.sudo().name,
                count=len(uids),
            )
            for rule, uids in sorted(by_rule.items(), key=lambda item: item[0].code)
        )
        raise ValidationError(
            _(
                "efn_role:sod_block — pelanggaran pemisahan tugas. %(details)s. Pengecualian hanya oleh Superadmin "
                "(Pengecualian SoD: alasan wajib, maks. 90 hari).",
                details=details,
            )
        )

    # ------------------------------------------------------------ bawaan
    @api.model
    def _efn_ensure_default_rules(self):
        """Buat aturan bawaan yang belum ada (kunci `code`, termasuk arsip) dan kedua grupnya ada.

        Aturan `block` yang saat dibuat sudah punya pelanggaran dibuat `report_only` (log
        EFN_SOD_DEFAULT_REPORT_ONLY) agar install/upgrade dan penulisan grup lain tidak macet; Superadmin
        menaikkan ke `block` setelah pelanggaran diselesaikan.
        """
        Rule = self.sudo().with_context(active_test=False)
        created = Rule.browse()
        uids = None
        for code, name, xmlid_a, xmlid_b, mode, rationale in DEFAULT_SOD_RULES:
            if Rule.search_count([("code", "=", code)]):
                continue
            group_a = self.env.ref(xmlid_a, raise_if_not_found=False)
            group_b = self.env.ref(xmlid_b, raise_if_not_found=False)
            if group_a is None or group_b is None or group_a._name != "res.groups" or group_b._name != "res.groups":
                continue
            a, b = group_a.id, group_b.id
            if Rule.search_count(
                [
                    ("company_id", "=", False),
                    "|",
                    "&",
                    ("group_a_id", "=", a),
                    ("group_b_id", "=", b),
                    "&",
                    ("group_a_id", "=", b),
                    ("group_b_id", "=", a),
                ]
            ):
                continue
            final_mode = mode
            if mode == "block":
                if uids is None:
                    uids = self._efn_internal_user_ids()
                count = self._efn_count_pair(uids, a, b)
                if count:
                    final_mode = "report_only"
                    _logger.warning("EFN_SOD_DEFAULT_REPORT_ONLY rule=%s violations=%s", code, count)
            created |= Rule.create(
                {
                    "code": code,
                    "name": name,
                    "group_a_id": a,
                    "group_b_id": b,
                    "mode": final_mode,
                    "rationale": rationale,
                }
            )
        return created

    @api.model
    def _efn_count_pair(self, user_ids, group_a_id, group_b_id):
        """Jumlah user aktif internal yang memegang kedua grup (efektif) — untuk aturan yang belum dibuat."""
        graph = implied_map(self.env)
        explicit = explicit_memberships(self.env, user_ids)
        group_user = self.env.ref("base.group_user").id
        users = self.env["res.users"].sudo().browse(user_ids).exists()
        count = 0
        for user in users:
            groups = closure(explicit.get(user.id, ()), graph)
            if user.active and group_user in groups and group_a_id in groups and group_b_id in groups:
                count += 1
        return count
