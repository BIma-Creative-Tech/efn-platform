"""Kebijakan Poin EFN per PT (docs/04 §13.1 no. 1-3, docs/08 §11.4, W-29/E-44; ADR-020, ADR-001 revisi; L-49, L-62).

- Satu record per (company, version). ``version`` dialokasikan server saat create, naik monoton per company
  (advisory lock + UNIQUE), tidak bisa diisi/diubah pengguna. Draft yang ditolak tetap memakai nomornya (celah
  versi wajar; BFF hanya menerima versi yang dipublikasikan).
- Record **global** = company berkode ``EFN`` (Holding): satu-satunya tempat ``point_value_idr`` (L-62: nilai tukar
  seragam lintas PT karena dompet member lintas PT). PT lain hanya laju & batas.
- Alur: ``draft -> approved -> published`` | ``draft -> rejected``. Empat mata: pembuat (grup EFN / Loyalty Manager)
  dan **setiap** user yang pernah menyunting isi draft (``editor_ids``, himpunan yang ditulis server, SEC-157 r14)
  != penyetuju (grup Accounting / Manager, company yang sama). Setelah approved, field
  kebijakan read-only untuk SEMUA (termasuk sudo); perubahan = versi baru. Record non-draft tidak bisa dihapus.
- Field status/jejak (``state``, ``maker_id``, ``approved_by_id``, ...) hanya ditulis kode server ber-sudo setelah
  cek; penulisan langsung lewat ORM/RPC oleh pengguna -> ``AccessError``.
- Teraudit: aturan ``efn_audit`` (data modul ini) + chatter (tracking state) + log aplikasi (uid, tanpa PII).
- Publikasi ke BFF: ``efn.loyalty.policy.outbox`` (dibuat saat approve) -> ``PUT /v1/pos/loyalty/policies/...``.
- G22 W-17 / D-22-13, D-22-15 (docs/04 §14.D.2, §14.D.3, §14.D.8, L-72): record global juga memuat parameter hangus
  karena tidak aktif (``inactivity_expiry_months``, ``expiry_notice_days``, ``activity_epoch``) -- ikut empat mata,
  read-only setelah approved, dan dikirim di ``params`` body publikasi HANYA untuk ``company_code=EFN``. BFF tetap
  memegang plafon env (``LOYALTY_POLICY_CEILING_INACTIVITY_MONTHS_MIN/_MAX``) dan fallback env bila belum ada
  kebijakan. Job hangus di production OFF sampai Legal/Meta (L-73) -- field ini tidak menyalakan apa pun.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.fields import Command

_logger = logging.getLogger(__name__)

GLOBAL_COMPANY_CODE = "EFN"
SCHEMA_VERSION = 1
# Namespace advisory lock alokasi versi (angka tetap, unik untuk modul ini).
_VERSION_LOCK_NAMESPACE = 704401

STATES = [
    ("draft", "Draft"),
    ("approved", "Disetujui"),
    ("published", "Dipublikasikan"),
    ("rejected", "Ditolak"),
]
# Parameter per PT yang dikirim di body `params` (urutan = urutan kontrak §13.1 no. 1).
PARAM_FIELDS = (
    "earn_idr_per_point",
    "redeem_min_points",
    "redeem_max_pct",
    "max_points_per_order",
    "daily_max_points_member",
    "pos_config_hourly_max",
    "company_hourly_max",
    "earn_on_money_only",
)
# W-17 (D-22-13, D-22-15): parameter hangus karena tidak aktif, HANYA di record global (EFN); dikirim di `params`.
GLOBAL_PARAM_FIELDS = ("inactivity_expiry_months", "expiry_notice_days", "activity_epoch")
# Field yang hanya bermakna di record global (PT lain wajib 0/kosong).
GLOBAL_ONLY_FIELDS = ("point_value_idr", *GLOBAL_PARAM_FIELDS)
# Batas statis Odoo (sanity, lebih longgar dari plafon env BFF 6..36 bulan / 7..60 hari yang configurable; nilai di
# luar plafon BFF -> 422 policy_out_of_bounds, publikasi gagal final, perbaikan = versi baru).
INACTIVITY_MONTHS_MIN, INACTIVITY_MONTHS_MAX = 1, 60
NOTICE_DAYS_MIN, NOTICE_DAYS_MAX = 7, 90
POLICY_FIELDS = (*PARAM_FIELDS, *GLOBAL_ONLY_FIELDS, "effective_from", "company_id", "note")
# Field jejak/status: hanya kode server (sudo) yang menulis.
SYSTEM_FIELDS = (
    "version",
    "state",
    "maker_id",
    "last_editor_id",
    "editor_ids",
    "approved_by_id",
    "approved_at",
    "rejected_by_id",
    "rejected_at",
    "reject_reason",
    "published_at",
)
APPROVER_GROUP = "account.group_account_manager"
MAKER_GROUP = "efn_loyalty_accounting.group_loyalty_manager"

# Default = nilai env BFF saat ini (docs/04 §9.1, app-config.ts), PLACEHOLDER sampai Finance memutuskan.
DEFAULTS = {
    "earn_idr_per_point": 10_000,
    "redeem_min_points": 50,
    "redeem_max_pct": 50,
    "max_points_per_order": 5_000,
    "daily_max_points_member": 2_000,
    "pos_config_hourly_max": 10_000,
    "company_hourly_max": 50_000,
    "earn_on_money_only": True,
    "point_value_idr": 100,
    # W-17: default kontrak §14.D.3/§14.D.8 (= fallback env BFF); PLACEHOLDER sampai syarat program disetujui Legal.
    "inactivity_expiry_months": 12,
    "expiry_notice_days": 30,
    "activity_epoch": False,
}
_GLOBAL_EMPTY = {"point_value_idr": 0, "inactivity_expiry_months": 0, "expiry_notice_days": 0, "activity_epoch": False}


class EfnLoyaltyPolicy(models.Model):
    _name = "efn.loyalty.policy"
    _description = "EFN Loyalty Policy (per PT)"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "company_id, version desc"
    _check_company_auto = True
    _rec_name = "display_label"

    company_id = fields.Many2one(
        "res.company", required=True, index=True, default=lambda self: self.env.company, tracking=True
    )
    company_code = fields.Selection(related="company_id.efn_brand_code", string="Kode PT", store=True)
    is_global = fields.Boolean(
        string="Kebijakan global (Holding EFN)",
        compute="_compute_is_global",
        store=True,
        help="Company berkode EFN: satu-satunya tempat nilai tukar poin (L-62).",
    )
    display_label = fields.Char(compute="_compute_display_label")
    version = fields.Integer(readonly=True, copy=False, index=True, help="Dialokasikan server, naik monoton per PT.")
    state = fields.Selection(STATES, default="draft", required=True, readonly=True, copy=False, tracking=True)
    effective_from = fields.Datetime(
        string="Berlaku mulai",
        required=True,
        default=fields.Datetime.now,
        tracking=True,
        help="BFF memakai versi tertinggi yang effective_from <= sekarang. Bila sudah lewat saat disetujui, "
        "digeser ke waktu persetujuan (tidak pernah berlaku surut).",
    )
    earn_idr_per_point = fields.Integer(
        string="Rupiah per 1 poin (earn)", default=lambda self: self._efn_default("earn_idr_per_point"), tracking=True
    )
    redeem_min_points = fields.Integer(
        string="Minimum poin ditukar", default=lambda self: self._efn_default("redeem_min_points"), tracking=True
    )
    redeem_max_pct = fields.Integer(
        string="Maks. % order dibayar poin", default=lambda self: self._efn_default("redeem_max_pct"), tracking=True
    )
    max_points_per_order = fields.Integer(
        string="Maks. poin earn per order",
        default=lambda self: self._efn_default("max_points_per_order"),
        tracking=True,
    )
    daily_max_points_member = fields.Integer(
        string="Maks. poin earn per member per hari",
        default=lambda self: self._efn_default("daily_max_points_member"),
        tracking=True,
        help="Isi >= 1. Nilai 0 (tanpa batas) ditolak BFF (422 policy_out_of_bounds, publikasi gagal final) selama "
        "plafon env BFF LOYALTY_POLICY_CEILING_* > 0 (default).",
    )
    pos_config_hourly_max = fields.Integer(
        string="Maks. poin earn per kasir per jam",
        default=lambda self: self._efn_default("pos_config_hourly_max"),
        tracking=True,
    )
    company_hourly_max = fields.Integer(
        string="Maks. poin earn per PT per jam",
        default=lambda self: self._efn_default("company_hourly_max"),
        tracking=True,
    )
    earn_on_money_only = fields.Boolean(
        string="Earn hanya dari porsi uang",
        default=lambda self: self._efn_default("earn_on_money_only"),
        tracking=True,
        help="18-10: porsi order yang dibayar Poin EFN tidak menghasilkan poin.",
    )
    point_value_idr = fields.Integer(
        string="Nilai tukar 1 poin (Rp)",
        default=lambda self: self._efn_default("point_value_idr"),
        tracking=True,
        help="Hanya pada kebijakan global (company EFN, L-62).",
    )
    inactivity_expiry_months = fields.Integer(
        string="Poin hangus setelah tidak aktif (bulan)",
        default=lambda self: self._efn_default("inactivity_expiry_months"),
        tracking=True,
        help="W-17 / D-22-13: hanya pada kebijakan global (company EFN). Saldo member hangus bila tidak ada aktivitas "
        "selama N bulan kalender (definisi aktivitas docs/04 §14.D.2). Odoo menerima 1..60; BFF menolak di luar "
        "plafon env (default 6..36) -> publikasi gagal final.",
    )
    expiry_notice_days = fields.Integer(
        string="Pemberitahuan sebelum hangus (hari)",
        default=lambda self: self._efn_default("expiry_notice_days"),
        tracking=True,
        help="W-17: hanya pada kebijakan global. Member selalu mendapat minimal sekian hari pemberitahuan sebelum "
        "poin hangus (WA T-N). Odoo menerima 7..90; plafon env BFF default 7..60.",
    )
    activity_epoch = fields.Date(
        string="Epoch aktivitas (go-live)",
        default=lambda self: self._efn_default("activity_epoch"),
        tracking=True,
        help="W-17 / D-22-15: opsional, hanya pada kebijakan global. Aktivitas terakhir efektif = MAX(aktivitas "
        "terakhir, tanggal ini 00:00 WIB). Kosong -> BFF memakai env LOYALTY_ACTIVITY_EPOCH.",
    )
    note = fields.Text(string="Catatan / dasar keputusan")

    maker_id = fields.Many2one("res.users", string="Pembuat", readonly=True, copy=False, index=True)
    last_editor_id = fields.Many2one("res.users", string="Penyunting terakhir", readonly=True, copy=False)
    editor_ids = fields.Many2many(
        "res.users",
        "efn_loyalty_policy_editor_rel",
        "policy_id",
        "user_id",
        string="Semua penyunting draft",
        readonly=True,
        copy=False,
        help="SEC-157: setiap user yang membuat/menyunting isi draft (ditulis server, tidak bisa dihapus pengguna). "
        "Tidak satu pun boleh menyetujui/menolak kebijakan ini.",
    )
    approved_by_id = fields.Many2one("res.users", string="Disetujui oleh", readonly=True, copy=False, tracking=True)
    approved_at = fields.Datetime(readonly=True, copy=False)
    rejected_by_id = fields.Many2one("res.users", string="Ditolak oleh", readonly=True, copy=False)
    rejected_at = fields.Datetime(readonly=True, copy=False)
    reject_reason = fields.Char(readonly=True, copy=False)
    published_at = fields.Datetime(readonly=True, copy=False)
    outbox_ids = fields.One2many("efn.loyalty.policy.outbox", "policy_id", readonly=True)

    _company_version_unique = models.Constraint(
        "UNIQUE (company_id, version)", "Versi kebijakan poin harus unik per company."
    )
    _non_negative = models.Constraint(
        "CHECK (earn_idr_per_point >= 0 AND redeem_min_points >= 0 AND redeem_max_pct >= 0"
        " AND max_points_per_order >= 0 AND daily_max_points_member >= 0 AND pos_config_hourly_max >= 0"
        " AND company_hourly_max >= 0 AND COALESCE(point_value_idr, 0) >= 0)",
        "Nilai kebijakan poin harus bilangan bulat >= 0.",
    )
    _inactivity_non_negative = models.Constraint(
        "CHECK (COALESCE(inactivity_expiry_months, 0) >= 0 AND COALESCE(expiry_notice_days, 0) >= 0)",
        "Parameter hangus karena tidak aktif harus bilangan bulat >= 0.",
    )

    # ------------------------------------------------------------------ compute
    @api.depends("company_id.efn_brand_code")
    def _compute_is_global(self):
        for policy in self:
            policy.is_global = policy.company_id.efn_brand_code == GLOBAL_COMPANY_CODE

    @api.depends("company_code", "version", "state")
    def _compute_display_label(self):
        labels = dict(STATES)
        for policy in self:
            code = policy.company_code or "?"
            policy.display_label = f"{code} v{policy.version or '-'} ({labels.get(policy.state)})"

    @api.model
    def _efn_default(self, name):
        """Default field: nilai versi terakhir yang disetujui/dipublikasikan company aktif (memudahkan 'versi
        baru'); belum ada -> DEFAULTS (PLACEHOLDER env BFF). Privat (bukan override ``default_get`` publik, SEC-58)."""
        return self._efn_default_for(self.env.company, name)

    @api.model
    def _efn_default_for(self, company, name):
        """Default per company. Field global-only: PT selain EFN -> 0/kosong; EFN -> versi terakhir yang disetujui
        bila terisi (versi pra-G22 tidak punya parameter W-17), selain itu DEFAULTS."""
        latest = self.sudo().search(
            [("company_id", "=", company.id), ("state", "in", ("approved", "published"))],
            order="version desc",
            limit=1,
        )
        if name in GLOBAL_ONLY_FIELDS:
            if company.sudo().efn_brand_code != GLOBAL_COMPANY_CODE:
                return _GLOBAL_EMPTY[name]
            if latest and latest[name]:
                return latest[name]
            # activity_epoch: tidak ada nilai sebelumnya -> tetap kosong (fallback env BFF).
            return DEFAULTS[name]
        if latest:
            return latest[name]
        return DEFAULTS[name]

    # --------------------------------------------------------------- validation
    @api.constrains(*PARAM_FIELDS, *GLOBAL_ONLY_FIELDS, "company_id")
    def _check_efn_policy_values(self):
        int_fields = [name for name in PARAM_FIELDS if name != "earn_on_money_only"] + ["point_value_idr"]
        for policy in self:
            if not policy.company_id.efn_brand_code:
                raise ValidationError(_("Company belum punya Kode Company EFN; kebijakan poin tidak bisa dibuat."))
            if any(policy[name] < 0 for name in int_fields):
                raise ValidationError(_("Nilai kebijakan poin harus bilangan bulat >= 0."))
            if policy.earn_idr_per_point < 1:
                raise ValidationError(_("Rupiah per 1 poin minimal 1."))
            if policy.redeem_min_points < 1:
                raise ValidationError(_("Minimum poin ditukar minimal 1."))
            if not 1 <= policy.redeem_max_pct <= 100:
                raise ValidationError(_("Maks. % order dibayar poin harus 1..100."))
            if policy.max_points_per_order < 1:
                raise ValidationError(_("Maks. poin earn per order minimal 1."))
            if policy.is_global:
                if policy.point_value_idr < 1:
                    raise ValidationError(_("Kebijakan global (EFN) wajib mengisi nilai tukar 1 poin (>= Rp1)."))
                policy._efn_check_inactivity_params()
            elif policy.point_value_idr:
                raise ValidationError(
                    _("Nilai tukar poin hanya diatur di kebijakan global company EFN (L-62), bukan per PT.")
                )
            elif policy.inactivity_expiry_months or policy.expiry_notice_days or policy.activity_epoch:
                raise ValidationError(
                    _(
                        "Parameter poin hangus karena tidak aktif hanya diatur di kebijakan global company EFN "
                        "(D-22-13), bukan per PT."
                    )
                )

    def _efn_check_inactivity_params(self):
        """W-17 (record global): rentang statis Odoo + pemberitahuan harus lebih pendek dari masa tidak aktif
        (N bulan kalender >= 28 x N hari). Plafon keras tetap di env BFF (§14.D.8)."""
        self.ensure_one()
        months, notice = self.inactivity_expiry_months, self.expiry_notice_days
        if not INACTIVITY_MONTHS_MIN <= months <= INACTIVITY_MONTHS_MAX:
            raise ValidationError(
                _(
                    "Masa tidak aktif sebelum poin hangus harus %(lo)s..%(hi)s bulan.",
                    lo=INACTIVITY_MONTHS_MIN,
                    hi=INACTIVITY_MONTHS_MAX,
                )
            )
        if not NOTICE_DAYS_MIN <= notice <= NOTICE_DAYS_MAX:
            raise ValidationError(
                _(
                    "Pemberitahuan sebelum poin hangus harus %(lo)s..%(hi)s hari.",
                    lo=NOTICE_DAYS_MIN,
                    hi=NOTICE_DAYS_MAX,
                )
            )
        if notice >= months * 28:
            raise ValidationError(_("Pemberitahuan sebelum poin hangus harus lebih pendek dari masa tidak aktif."))

    # ------------------------------------------------------------------ ORM
    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and not self.env.user.has_group(MAKER_GROUP):
            raise AccessError(_("Hanya EFN / Loyalty Manager yang dapat membuat kebijakan poin."))
        for vals in vals_list:
            if not self.env.su and set(vals) & set(SYSTEM_FIELDS):
                raise AccessError(_("Status dan jejak persetujuan kebijakan poin diisi sistem."))
            company_id = vals.get("company_id") or self.env.company.id
            vals["company_id"] = company_id
            # Default field global-only mengikuti company record (bukan company aktif user), mis. Loyalty Manager
            # multi-company yang membuat kebijakan EFN dari company lain. Nilai yang dikirim pengguna tidak diubah.
            company = self.env["res.company"].sudo().browse(company_id)
            for name in GLOBAL_ONLY_FIELDS:
                if name not in vals:
                    vals[name] = self._efn_default_for(company, name)
            vals["version"] = self._efn_next_version(company_id)
            vals["state"] = "draft"
            vals["maker_id"] = self.env.uid
            vals["last_editor_id"] = self.env.uid
            vals["editor_ids"] = [Command.set([self.env.uid])]
        records = super().create(vals_list)
        for record in records:
            _logger.info(
                "EFN loyalty policy dibuat company=%s version=%s uid=%s",
                record.company_code,
                record.version,
                self.env.uid,
            )
        return records

    @api.model
    def _efn_next_version(self, company_id):
        """Versi berikutnya per company (advisory lock transaksi; UNIQUE sebagai jaring pengaman)."""
        self.env.cr.execute("SELECT pg_advisory_xact_lock(%s, %s)", (_VERSION_LOCK_NAMESPACE, int(company_id)))
        self.env.cr.execute(
            "SELECT COALESCE(MAX(version), 0) FROM efn_loyalty_policy WHERE company_id = %s", (int(company_id),)
        )
        return self.env.cr.fetchone()[0] + 1

    def write(self, vals):
        touched = set(vals)
        if touched & set(SYSTEM_FIELDS) and not self.env.su:
            raise AccessError(_("Status dan jejak persetujuan kebijakan poin hanya diubah lewat tombol persetujuan."))
        content = touched & set(POLICY_FIELDS)
        if content:
            # Read-only setelah keluar dari draft: berlaku untuk SEMUA, termasuk sudo (perubahan = versi baru).
            if any(policy.state != "draft" for policy in self):
                raise UserError(_("Kebijakan yang sudah disetujui/ditolak tidak dapat diubah; buat versi baru."))
            if not self.env.su and not self.env.user.has_group(MAKER_GROUP):
                raise AccessError(_("Hanya EFN / Loyalty Manager yang dapat mengubah draft kebijakan poin."))
            if "company_id" in vals and any(policy.company_id.id != vals["company_id"] for policy in self):
                raise UserError(_("Company kebijakan tidak dapat dipindah; buat kebijakan baru di company tujuan."))
            # SEC-157: SEMUA penyunting (himpunan, bukan hanya terakhir) ikut dicek empat mata; juga untuk kode ber-sudo
            # (uid asli). Command.link menambah tanpa menghapus penyunting sebelumnya.
            vals = {**vals, "last_editor_id": self.env.uid, "editor_ids": [Command.link(self.env.uid)]}
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_not_draft(self):
        if any(policy.state != "draft" for policy in self):
            raise UserError(_("Kebijakan poin yang sudah diputuskan tidak dapat dihapus (jejak audit)."))

    def copy(self, default=None):
        raise UserError(_("Gunakan 'Baru': draft diisi otomatis dari versi terakhir yang disetujui."))

    # -------------------------------------------------------------- approval
    def _efn_check_approver(self):
        """Grup Accounting / Manager + company record di company user + bukan pembuat / penyunting mana pun."""
        user = self.env.user
        if not user.has_group(APPROVER_GROUP):
            raise AccessError(_("Hanya Accounting / Manager yang dapat menyetujui atau menolak kebijakan poin."))
        self.check_access("read")  # record rule multi-company
        for policy in self:
            if policy.company_id not in user.company_ids:
                raise AccessError(_("Kebijakan poin milik company lain."))
            if policy.state != "draft":
                raise UserError(_("Hanya kebijakan berstatus draft yang dapat diputuskan."))
            if user in (policy.sudo().maker_id | policy.sudo().last_editor_id | policy.sudo().editor_ids):
                raise UserError(_("Empat mata: pembuat/penyunting draft tidak boleh memutuskan kebijakannya sendiri."))

    def action_efn_approve(self):
        """Setujui draft (empat mata) -> antre publikasi bertanda tangan ke BFF."""
        self._efn_check_approver()
        now = fields.Datetime.now()
        Outbox = self.env["efn.loyalty.policy.outbox"]
        for policy in self.sudo():
            values = {"state": "approved", "approved_by_id": self.env.uid, "approved_at": now}
            # Tidak pernah berlaku surut: effective_from yang sudah lewat digeser ke waktu persetujuan.
            shifted = policy.effective_from < now
            if shifted:
                values["effective_from"] = now
            policy._efn_system_write(values, allow_effective_from=True)
            policy.message_post(
                body=_(
                    "Kebijakan v%(version)s disetujui oleh %(user)s%(shift)s.",
                    version=policy.version,
                    user=self.env.user.name,
                    shift=_(" (berlaku mulai digeser ke waktu persetujuan)") if shifted else "",
                ),
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )
            Outbox._efn_enqueue_policy(policy)
            _logger.info(
                "EFN loyalty policy disetujui company=%s version=%s maker_uid=%s approver_uid=%s",
                policy.company_code,
                policy.version,
                policy.maker_id.id,
                self.env.uid,
            )
        Outbox._efn_trigger()
        return True

    def action_efn_reject(self):
        """Tolak draft (empat mata). Record tetap ada (jejak), tidak bisa diubah/dihapus."""
        self._efn_check_approver()
        reason = (self.env.context.get("efn_reject_reason") or "").strip()[:200] or False
        now = fields.Datetime.now()
        for policy in self.sudo():
            policy._efn_system_write(
                {"state": "rejected", "rejected_by_id": self.env.uid, "rejected_at": now, "reject_reason": reason}
            )
            _logger.info(
                "EFN loyalty policy ditolak company=%s version=%s uid=%s",
                policy.company_code,
                policy.version,
                self.env.uid,
            )
        return True

    def _efn_system_write(self, values, allow_effective_from=False):
        """Tulis field sistem (sudo), melewati penjaga ``write`` (``super().write`` = mail.thread -> base, sehingga
        tracking chatter & hook efn_audit tetap berjalan). Isi kebijakan tidak pernah boleh, kecuali menggeser
        ``effective_from`` ke waktu persetujuan di dalam ``action_efn_approve``."""
        self.ensure_one()
        if not self.env.su:
            raise AccessError(_("Penulisan sistem kebijakan poin wajib sudo."))
        content = set(values) & set(POLICY_FIELDS)
        if content and (not allow_effective_from or content != {"effective_from"}):
            raise UserError(_("Isi kebijakan tidak dapat diubah oleh sistem."))
        return super().write(values)

    def _efn_mark_published(self):
        for policy in self.sudo():
            if policy.state == "approved":
                policy._efn_system_write({"state": "published", "published_at": fields.Datetime.now()})
                _logger.info(
                    "EFN loyalty policy dipublikasikan company=%s version=%s", policy.company_code, policy.version
                )

    # ----------------------------------------------------------------- body
    def _efn_publish_body(self):
        """Body kontrak §13.1 no. 3 (``additionalProperties: false``). Angka integer, tanpa desimal.

        G22 (§14.D.8, D-22-13/D-22-15): HANYA untuk ``company_code=EFN``, ``params`` bertambah
        ``inactivity_expiry_months`` (int), ``expiry_notice_days`` (int) dan ``activity_epoch`` ("YYYY-MM-DD", hanya
        bila diisi). Kunci dihilangkan bila nilainya kosong (versi global pra-G22) -> BFF memakai fallback env.
        ``schema_version`` tetap 1 (penambahan opsional)."""
        self.ensure_one()
        policy = self.sudo()
        params = {
            name: (bool(policy[name]) if name == "earn_on_money_only" else int(policy[name])) for name in PARAM_FIELDS
        }
        body = {
            "schema_version": SCHEMA_VERSION,
            "company_code": policy.company_code,
            "version": policy.version,
            "effective_from": policy.effective_from.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "approved_by_uid": policy.approved_by_id.id,
            "params": params,
        }
        if policy.is_global:
            body["point_value_idr"] = int(policy.point_value_idr)
            if policy.inactivity_expiry_months:
                params["inactivity_expiry_months"] = int(policy.inactivity_expiry_months)
            if policy.expiry_notice_days:
                params["expiry_notice_days"] = int(policy.expiry_notice_days)
            if policy.activity_epoch:
                params["activity_epoch"] = policy.activity_epoch.isoformat()
        return body
