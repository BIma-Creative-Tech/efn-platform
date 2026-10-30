"""Kebijakan MGM (member-get-member) per PT -- K25-10, ADR-009, docs/04 §19.

Struktur (K25-10): level 1 dan 2 = persentase dari nilai belanja bersih downline (basis poin), level 3 s.d. kedalaman
maksimum = reward FLAT (poin tetap per transaksi yang memenuhi syarat). Kedalaman maksimum WAJIB diisi (2..5): tidak ada
nilai "tak terbatas" (skema berjenjang tanpa batas berisiko dianggap skema piramida, R-L02 -> Legal menetapkan).

Pola tata kelola sama dengan ``efn.loyalty.policy`` (§13.1 no. 2): versi dialokasikan server per PT, empat mata
(pembuat/penyunting = EFN / Loyalty Manager, penyetuju = Accounting / Manager company yang sama dan bukan penyunting),
read-only setelah disetujui (perubahan = versi baru), tidak bisa dihapus setelah diputuskan. Status ``approved`` di
modul ini TIDAK menyalakan apa pun: engine MGM (loyalty-service, K25-05) belum ada, jadi belum ada publikasi ke BFF.
Kontrak publikasi menyusul bersama engine (docs/04 §19.3).

Semua angka = PLACEHOLDER (ADR-009 Proposed) sampai Finance (angka) dan Legal (struktur) memutuskan.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.fields import Command

_logger = logging.getLogger(__name__)

_VERSION_LOCK_NAMESPACE = 704402
STATES = [("draft", "Draft"), ("approved", "Disetujui"), ("published", "Dipublikasikan"), ("rejected", "Ditolak")]
SCHEMA_VERSION = 1
# Urutan = urutan kontrak docs/04 §20.1 (body `params`).
PARAM_FIELDS = (
    "level1_pct_bp",
    "level2_pct_bp",
    "flat_points_level3",
    "max_depth",
    "cap_points_per_referrer_month",
    "min_order_idr",
    "settle_hold_days",
    "relation_months",
)
MAKER_GROUP = "efn_loyalty_accounting.group_loyalty_manager"
APPROVER_GROUP = "account.group_account_manager"
# Batas statis (sanity). Angka final = Finance/Legal.
MAX_DEPTH_MIN, MAX_DEPTH_MAX = 2, 5
PCT_BP_MAX = 2_000  # basis poin: 2000 bp = 20 %
HOLD_DAYS_MIN, HOLD_DAYS_MAX = 1, 60
RELATION_MONTHS_MIN, RELATION_MONTHS_MAX = 1, 36
POLICY_FIELDS = (
    "company_id",
    "effective_from",
    "level1_pct_bp",
    "level2_pct_bp",
    "flat_points_level3",
    "max_depth",
    "cap_points_per_referrer_month",
    "min_order_idr",
    "settle_hold_days",
    "relation_months",
    "excluded_categ_ids",
    "note",
)
SYSTEM_FIELDS = (
    "version",
    "state",
    "maker_id",
    "editor_ids",
    "approved_by_id",
    "approved_at",
    "rejected_by_id",
    "rejected_at",
    "reject_reason",
    "published_at",
)
# ADR-009 usulan simulasi (bukan final): L1 2 %, L2 0,5 %, cap Rp100.000/bulan (= 1.000 poin @Rp100), settle H+8,
# relasi 12 bulan. L3+ flat & kedalaman 3 = PLACEHOLDER K25-10.
DEFAULTS = {
    "level1_pct_bp": 200,
    "level2_pct_bp": 50,
    "flat_points_level3": 5,
    "max_depth": 3,
    "cap_points_per_referrer_month": 1_000,
    "min_order_idr": 50_000,
    "settle_hold_days": 8,
    "relation_months": 12,
}


class EfnMgmPolicy(models.Model):
    _name = "efn.mgm.policy"
    _description = "EFN MGM Policy (per PT)"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "company_id, version desc"
    _check_company_auto = True
    _rec_name = "display_label"

    company_id = fields.Many2one(
        "res.company", required=True, index=True, default=lambda self: self.env.company, tracking=True
    )
    company_code = fields.Selection(related="company_id.efn_brand_code", string="Kode PT", store=True)
    display_label = fields.Char(compute="_compute_display_label")
    version = fields.Integer(readonly=True, copy=False, index=True)
    state = fields.Selection(STATES, default="draft", required=True, readonly=True, copy=False, tracking=True)
    effective_from = fields.Datetime(string="Berlaku mulai", required=True, default=fields.Datetime.now, tracking=True)
    level1_pct_bp = fields.Integer(
        string="Level 1 (basis poin)",
        default=lambda self: self._efn_default("level1_pct_bp"),
        tracking=True,
        help="Persentase dari nilai belanja bersih downline langsung, dalam basis poin (100 bp = 1 %).",
    )
    level2_pct_bp = fields.Integer(
        string="Level 2 (basis poin)", default=lambda self: self._efn_default("level2_pct_bp"), tracking=True
    )
    flat_points_level3 = fields.Integer(
        string="Level 3+ (poin flat)",
        default=lambda self: self._efn_default("flat_points_level3"),
        tracking=True,
        help="K25-10: reward tetap per transaksi yang memenuhi syarat untuk level 3 s.d. kedalaman maksimum.",
    )
    max_depth = fields.Integer(
        string="Kedalaman maksimum",
        default=lambda self: self._efn_default("max_depth"),
        tracking=True,
        help="Wajib 2..5. Tidak ada nilai tak terbatas (R-L02, Legal).",
    )
    cap_points_per_referrer_month = fields.Integer(
        string="Cap poin per referrer per bulan",
        default=lambda self: self._efn_default("cap_points_per_referrer_month"),
        tracking=True,
    )
    min_order_idr = fields.Integer(
        string="Minimal belanja bersih (Rp)", default=lambda self: self._efn_default("min_order_idr"), tracking=True
    )
    settle_hold_days = fields.Integer(
        string="Settle setelah (hari)",
        default=lambda self: self._efn_default("settle_hold_days"),
        tracking=True,
        help="ADR-009: masa retur + 1. Reward dibatalkan (clawback) bila transaksi diretur dalam masa ini.",
    )
    relation_months = fields.Integer(
        string="Masa relasi referral (bulan)", default=lambda self: self._efn_default("relation_months"), tracking=True
    )
    excluded_categ_ids = fields.Many2many(
        "product.category",
        "efn_mgm_policy_excluded_categ_rel",
        "policy_id",
        "categ_id",
        string="Kategori dikecualikan",
        help="ADR-009: mis. rokok, susu formula, pulsa, barang promo. Turunan kategori ikut dikecualikan.",
    )
    note = fields.Text(string="Catatan / dasar keputusan")
    maker_id = fields.Many2one("res.users", string="Pembuat", readonly=True, copy=False)
    editor_ids = fields.Many2many(
        "res.users", "efn_mgm_policy_editor_rel", "policy_id", "user_id", string="Penyunting", readonly=True, copy=False
    )
    approved_by_id = fields.Many2one("res.users", string="Disetujui oleh", readonly=True, copy=False, tracking=True)
    approved_at = fields.Datetime(readonly=True, copy=False)
    rejected_by_id = fields.Many2one("res.users", string="Ditolak oleh", readonly=True, copy=False)
    rejected_at = fields.Datetime(readonly=True, copy=False)
    reject_reason = fields.Char(readonly=True, copy=False)
    published_at = fields.Datetime(string="Dipublikasikan ke BFF", readonly=True, copy=False)
    outbox_ids = fields.One2many("efn.mgm.policy.outbox", "policy_id", readonly=True)

    _company_version_unique = models.Constraint(
        "UNIQUE (company_id, version)", "Versi kebijakan MGM harus unik per company."
    )
    _depth_bounded = models.Constraint(
        "CHECK (max_depth BETWEEN 2 AND 5)", "Kedalaman maksimum MGM wajib 2..5 (tidak ada tak terbatas)."
    )

    @api.depends("company_code", "version", "state")
    def _compute_display_label(self):
        labels = dict(STATES)
        for policy in self:
            policy.display_label = (
                f"MGM {policy.company_code or '?'} v{policy.version or '-'} ({labels.get(policy.state)})"
            )

    @api.model
    def _efn_default(self, name):
        latest = self.sudo().search(
            [("company_id", "=", self.env.company.id), ("state", "in", ("approved", "published"))],
            order="version desc",
            limit=1,
        )
        return latest[name] if latest else DEFAULTS[name]

    @api.constrains(
        "level1_pct_bp",
        "level2_pct_bp",
        "flat_points_level3",
        "max_depth",
        "cap_points_per_referrer_month",
        "min_order_idr",
        "settle_hold_days",
        "relation_months",
        "company_id",
    )
    def _check_efn_mgm_values(self):
        for policy in self:
            if not policy.company_id.efn_brand_code:
                raise ValidationError(_("Company belum punya Kode Company EFN; kebijakan MGM tidak bisa dibuat."))
            if not MAX_DEPTH_MIN <= policy.max_depth <= MAX_DEPTH_MAX:
                raise ValidationError(
                    _("Kedalaman maksimum MGM wajib %(lo)s..%(hi)s.", lo=MAX_DEPTH_MIN, hi=MAX_DEPTH_MAX)
                )
            for name in ("level1_pct_bp", "level2_pct_bp"):
                if not 0 <= policy[name] <= PCT_BP_MAX:
                    raise ValidationError(_("Persentase level 1/2 harus 0..%(hi)s basis poin.", hi=PCT_BP_MAX))
            if policy.level2_pct_bp > policy.level1_pct_bp:
                raise ValidationError(_("Persentase level 2 tidak boleh melebihi level 1."))
            if policy.flat_points_level3 < 0 or policy.min_order_idr < 0:
                raise ValidationError(_("Nilai MGM harus bilangan bulat >= 0."))
            if policy.cap_points_per_referrer_month < 1:
                raise ValidationError(_("Cap poin per referrer per bulan wajib diisi (minimal 1)."))
            if not HOLD_DAYS_MIN <= policy.settle_hold_days <= HOLD_DAYS_MAX:
                raise ValidationError(
                    _("Masa tunggu settle harus %(lo)s..%(hi)s hari.", lo=HOLD_DAYS_MIN, hi=HOLD_DAYS_MAX)
                )
            if not RELATION_MONTHS_MIN <= policy.relation_months <= RELATION_MONTHS_MAX:
                raise ValidationError(
                    _(
                        "Masa relasi referral harus %(lo)s..%(hi)s bulan.",
                        lo=RELATION_MONTHS_MIN,
                        hi=RELATION_MONTHS_MAX,
                    )
                )

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and not self.env.user.has_group(MAKER_GROUP):
            raise AccessError(_("Hanya EFN / Loyalty Manager yang dapat membuat kebijakan MGM."))
        for vals in vals_list:
            if not self.env.su and set(vals) & set(SYSTEM_FIELDS):
                raise AccessError(_("Status dan jejak persetujuan kebijakan MGM diisi sistem."))
            company_id = vals.get("company_id") or self.env.company.id
            vals.update(
                {
                    "company_id": company_id,
                    "version": self._efn_next_version(company_id),
                    "state": "draft",
                    "maker_id": self.env.uid,
                    "editor_ids": [Command.set([self.env.uid])],
                }
            )
        records = super().create(vals_list)
        for record in records:
            _logger.info(
                "EFN MGM policy dibuat company=%s version=%s uid=%s", record.company_code, record.version, self.env.uid
            )
        return records

    @api.model
    def _efn_next_version(self, company_id):
        self.env.cr.execute("SELECT pg_advisory_xact_lock(%s, %s)", (_VERSION_LOCK_NAMESPACE, int(company_id)))
        self.env.cr.execute(
            "SELECT COALESCE(MAX(version), 0) FROM efn_mgm_policy WHERE company_id = %s", (int(company_id),)
        )
        return self.env.cr.fetchone()[0] + 1

    def write(self, vals):
        if not self.env.su and set(vals) & set(SYSTEM_FIELDS):
            raise AccessError(_("Status dan jejak persetujuan kebijakan MGM hanya diubah lewat tombol persetujuan."))
        content = set(vals) & set(POLICY_FIELDS)
        if content:
            if any(policy.state != "draft" for policy in self):
                raise UserError(_("Kebijakan MGM yang sudah diputuskan tidak dapat diubah; buat versi baru."))
            if not self.env.su and not self.env.user.has_group(MAKER_GROUP):
                raise AccessError(_("Hanya EFN / Loyalty Manager yang dapat mengubah draft kebijakan MGM."))
            if "company_id" in vals and any(policy.company_id.id != vals["company_id"] for policy in self):
                raise UserError(_("Company kebijakan MGM tidak dapat dipindah."))
            # editor_ids = field sistem -> ditulis sudo SETELAH hak tulis (ACL + record rule company) dicek
            # sebagai user.
            self.check_access("write")
            vals = {**vals, "editor_ids": [Command.link(self.env.uid)]}
            return super(EfnMgmPolicy, self.sudo()).write(vals)
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_not_draft(self):
        if any(policy.state != "draft" for policy in self):
            raise UserError(_("Kebijakan MGM yang sudah diputuskan tidak dapat dihapus (jejak audit)."))

    def copy(self, default=None):
        raise UserError(_("Gunakan 'Baru': draft diisi otomatis dari versi terakhir yang disetujui."))

    def _efn_check_approver(self):
        user = self.env.user
        if not user.has_group(APPROVER_GROUP):
            raise AccessError(_("Hanya Accounting / Manager yang dapat menyetujui atau menolak kebijakan MGM."))
        self.check_access("read")  # record rule multi-company
        for policy in self:
            if policy.company_id not in user.company_ids:
                raise AccessError(_("Kebijakan MGM milik company lain."))
            if policy.state != "draft":
                raise UserError(_("Hanya kebijakan MGM berstatus draft yang dapat diputuskan."))
            if user in policy.sudo().editor_ids:
                raise AccessError(
                    _("Empat mata: pembuat/penyunting draft tidak boleh memutuskan kebijakannya sendiri.")
                )

    def action_efn_approve(self):
        self._efn_check_approver()
        now = fields.Datetime.now()
        for policy in self.sudo():
            values = {"state": "approved", "approved_by_id": self.env.uid, "approved_at": now}
            if policy.effective_from < now:
                values["effective_from"] = now
            super(EfnMgmPolicy, policy).write(values)
            # §20.1: antre publikasi bertanda tangan ke BFF (transaksi yang sama dengan persetujuan)
            self.env["efn.mgm.policy.outbox"]._efn_enqueue_policy(policy)
            _logger.info(
                "EFN MGM policy disetujui company=%s version=%s approver_uid=%s",
                policy.company_code,
                policy.version,
                self.env.uid,
            )
        self.env["efn.mgm.policy.outbox"]._efn_trigger()
        return True

    def action_efn_reject(self):
        self._efn_check_approver()
        reason = (self.env.context.get("efn_reject_reason") or "").strip()[:200] or False
        now = fields.Datetime.now()
        for policy in self.sudo():
            super(EfnMgmPolicy, policy).write(
                {"state": "rejected", "rejected_by_id": self.env.uid, "rejected_at": now, "reject_reason": reason}
            )
        return True

    def _efn_mark_published(self):
        for policy in self.sudo():
            if policy.state == "approved":
                super(EfnMgmPolicy, policy).write({"state": "published", "published_at": fields.Datetime.now()})
                _logger.info("EFN MGM policy dipublikasikan company=%s version=%s", policy.company_code, policy.version)

    def _efn_publish_body(self):
        """Body kontrak docs/04 §20.1 (``additionalProperties: false``); angka integer."""
        self.ensure_one()
        policy = self.sudo()
        return {
            "schema_version": SCHEMA_VERSION,
            "company_code": policy.company_code,
            "version": policy.version,
            "effective_from": policy.effective_from.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "approved_by_uid": policy.approved_by_id.id,
            "params": {name: int(policy[name]) for name in PARAM_FIELDS},
        }

    @api.model
    def efn_active_policy(self, company):
        """Versi tertinggi yang disetujui dan sudah berlaku untuk PT ini (dipakai engine MGM kelak).

        Kosong bila tidak ada."""
        return self.sudo().search(
            [
                ("company_id", "=", company.id),
                ("state", "in", ("approved", "published")),
                ("effective_from", "<=", fields.Datetime.now()),
            ],
            order="version desc",
            limit=1,
        )

    def _efn_reward_points(self, level, net_amount_idr, point_value_idr):
        """Rumus kontrak §19.2 (tanpa cap bulanan & pengecualian kategori: dihitung engine dengan konteks member).

        Level 1/2: floor(net x bp / 10.000 / nilai 1 poin). Level 3..max_depth: flat. Di luar kedalaman: 0.
        Belanja di bawah minimal: 0."""
        self.ensure_one()
        if level < 1 or level > self.max_depth or net_amount_idr < self.min_order_idr or point_value_idr < 1:
            return 0
        if level <= 2:
            bp = self.level1_pct_bp if level == 1 else self.level2_pct_bp
            return int(net_amount_idr) * bp // (10_000 * int(point_value_idr))
        return self.flat_points_level3
