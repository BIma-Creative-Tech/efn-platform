"""Kampanye tertarget supermarket (G50, K18-01, ADR-047 §3.4, docs/04 §28.7, L-155/L-156/L-157/L-164).

Alur: segment-worker -> ``efn_campaign_proposal_upsert`` (ringkasan segmen tanpa data member) -> ``efn.campaign``
``proposed`` -> Marketing melengkapi (kanal, pesan, program voucher CE, mode voucher) -> *Marketing approver* (bukan
pembuat/penyunting, SoD) menyetujui -> voucher dibuat lewat wizard CE ``loyalty.generate.wizard`` sesuai mode -> baris
``efn.campaign.outbox`` ``queued`` -> BFF ``efn_campaign_claim`` -> publish ``promo.campaign.requested`` ->
``efn_campaign_ack`` -> kampanye ``sent``. Odoo tidak memegang kredensial RabbitMQ (L-156).

Mode voucher (dikunci setelah approval pertama, ``approval_rev`` > 0):
- ``anonymous_code`` (default): N ``loyalty.card`` tanpa partner; daftar kode ikut payload; BFF menugaskan ke member.
- ``public_code``: satu kode bersama = kode aturan ``with_code`` program CE ``promo_code``; kuota = ``max_usage``.
- ``member_bound``: kartu per ``partner_id`` dari ``partner_ids`` yang diisi Marketing (satu-satunya mode yang
  memasukkan identitas member ke Odoo, L-164); payload memuat ``partner_id`` (integer) + kode, tanpa nama/HP/email.

Markdown (G45) memakai jalur yang sama lewat ``_efn_propose_markdown`` (``ref_model``/``ref_id`` generik, tanpa
depends ke ``efn_expiry_monitor``).
"""

import logging
import re
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.fields import Command

from . import campaign_api as ca

_logger = logging.getLogger(__name__)

STATES = [
    ("draft", "Draft"),
    ("proposed", "Diusulkan"),
    ("approved", "Disetujui"),
    ("sent", "Terkirim ke BFF"),
    ("cancelled", "Dibatalkan"),
]
KINDS = [("segment", "Segmen perilaku"), ("markdown", "Markdown near-expiry")]
ORIGINS = [("manual", "Manual (Marketing)"), ("worker", "Usulan segment-worker"), ("markdown", "Markdown lot")]
VOUCHER_MODES = [
    ("anonymous_code", "Kode anonim (default)"),
    ("public_code", "Kode publik bersama"),
    ("member_bound", "Terikat member (partner)"),
]
CHANNELS = (
    ("channel_inbox", "inbox"),
    ("channel_push", "push"),
    ("channel_email", "email"),
    ("channel_wa_unofficial", "wa_unofficial"),
)
MAX_CODES = 5000
MAX_PUBLIC_QUOTA = 1_000_000
MAX_CLAIM = 20
MAX_ACK = 20
MAX_METRICS = 20
MAX_MEMBER_COUNT = 10_000_000
MAX_SUMMARY = 500
DEFAULT_DAYS = 14
REF_MODEL_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
RFC3339_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_UPSERT_LOCK_NAMESPACE = 705001

# Field isi kampanye (diubah Marketing selama draft/proposed; mengubahnya = menjadi penyunting -> tidak boleh approve).
CONTENT_FIELDS = (
    "name",
    "company_id",
    "kind",
    "channel_inbox",
    "channel_push",
    "channel_email",
    "channel_wa_unofficial",
    "date_from",
    "date_to",
    "message_title",
    "message_body",
    "voucher_mode",
    "program_id",
    "code_qty",
    "partner_ids",
    "budget_idr",
    "cap_enabled",
    "cap_per_member_week",
    "markdown_product_ids",
    "markdown_warehouse_ids",
)
# Field yang hanya diisi sistem (tombol/RPC bot), tidak lewat form.
SYSTEM_FIELDS = (
    "state",
    "origin",
    "approval_rev",
    "maker_id",
    "editor_ids",
    "approved_by_id",
    "approved_at",
    "sent_at",
    "cancelled_by_id",
    "cancelled_at",
    "cancel_reason",
    "segment_run_id",
    "segment_code",
    "segment_member_count",
    "segment_metrics",
    "segment_summary",
    "segment_generated_at",
    "source_ref_model",
    "source_ref_id",
)


def _iso_date(value):
    return fields.Date.to_string(value) if value else None


class EfnCampaign(models.Model):
    _name = "efn.campaign"
    _description = "EFN kampanye tertarget (segmen/markdown)"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"
    _check_company_auto = True

    name = fields.Char(string="Nama kampanye", required=True, tracking=True)
    company_id = fields.Many2one(
        "res.company", required=True, index=True, default=lambda self: self.env.company, tracking=True
    )
    company_code = fields.Selection(related="company_id.efn_brand_code", string="Kode PT")
    kind = fields.Selection(KINDS, string="Jenis", required=True, default="segment", tracking=True)
    origin = fields.Selection(ORIGINS, string="Asal", required=True, default="manual", readonly=True, copy=False)
    state = fields.Selection(STATES, default="draft", required=True, readonly=True, copy=False, tracking=True)
    approval_rev = fields.Integer(
        string="Revisi approval",
        readonly=True,
        copy=False,
        help="Bertambah setiap kali kampanye disetujui; ikut idempotency_key promo.campaign.requested.",
    )

    # ----------------------------------------------------------------- kanal & pesan
    channel_inbox = fields.Boolean(string="Inbox/voucher in-app", default=True, tracking=True)
    channel_push = fields.Boolean(string="Push", default=True, tracking=True)
    channel_email = fields.Boolean(string="Email", tracking=True)
    channel_wa_unofficial = fields.Boolean(
        string="WhatsApp unofficial",
        tracking=True,
        help="ADR-044: hanya dikirim bila WA_UNOFFICIAL_ENABLED di BFF (OFF di production).",
    )
    date_from = fields.Date(string="Berlaku mulai", required=True, default=fields.Date.context_today, tracking=True)
    date_to = fields.Date(
        string="Berlaku sampai",
        required=True,
        default=lambda self: fields.Date.context_today(self) + timedelta(days=DEFAULT_DAYS),
        tracking=True,
    )
    message_title = fields.Char(string="Judul pesan", size=48, tracking=True)
    message_body = fields.Char(string="Isi pesan", size=160, tracking=True)

    # ----------------------------------------------------------------- voucher
    voucher_mode = fields.Selection(
        VOUCHER_MODES,
        string="Mode voucher",
        required=True,
        default="anonymous_code",
        tracking=True,
        help="Dikunci setelah approval pertama (ADR-047 §3.4, L-164). Ganti mode = kampanye baru.",
    )
    program_id = fields.Many2one(
        "loyalty.program",
        string="Program voucher (CE)",
        check_company=True,
        tracking=True,
        domain="[('program_type', '=', 'promo_code' if voucher_mode == 'public_code' else 'coupons')]",
        help="Kosong = kampanye tanpa voucher (notifikasi saja). Kode anonim / terikat member: program tipe Kupon. "
        "Kode publik: program tipe Kode Promo dengan satu aturan berkode.",
    )
    program_type = fields.Selection(related="program_id.program_type", string="Tipe program")
    code_qty = fields.Integer(
        string="Jumlah kode / kuota",
        tracking=True,
        help=f"Kode anonim: jumlah kartu (maks {MAX_CODES}). Kode publik: kuota pemakaian (max_usage program). "
        "Terikat member: diabaikan (= jumlah partner).",
    )
    partner_ids = fields.Many2many(
        "res.partner",
        "efn_campaign_partner_rel",
        "campaign_id",
        "partner_id",
        string="Member (mode terikat member)",
        check_company=True,
        help="Hanya mode terikat member. Diisi Marketing; tidak ada partner yang dibuat otomatis.",
    )
    card_ids = fields.One2many("loyalty.card", "efn_campaign_id", string="Voucher dibuat", readonly=True)
    card_count = fields.Integer(compute="_compute_card_count", string="Jumlah voucher")
    budget_idr = fields.Integer(string="Anggaran (Rp)", tracking=True)
    cap_enabled = fields.Boolean(
        string="Terapkan cap anggaran/frekuensi",
        tracking=True,
        help="Default nonaktif (L-157, Q-SM24). Bila aktif, anggaran & cap per member per minggu ikut payload; "
        "penegakan di BFF (CAMPAIGN_*_CAP_ENABLED).",
    )
    cap_per_member_week = fields.Integer(string="Cap per member per minggu", tracking=True)

    # ----------------------------------------------------------------- markdown (G45, generik)
    markdown_product_ids = fields.Many2many(
        "product.product",
        "efn_campaign_markdown_product_rel",
        "campaign_id",
        "product_id",
        string="Produk markdown",
        check_company=True,
    )
    markdown_warehouse_ids = fields.Many2many(
        "stock.warehouse",
        "efn_campaign_markdown_warehouse_rel",
        "campaign_id",
        "warehouse_id",
        string="Outlet markdown",
        check_company=True,
    )
    source_ref_model = fields.Char(string="Model sumber", readonly=True, copy=False)
    source_ref_id = fields.Integer(string="ID sumber", readonly=True, copy=False)

    # ----------------------------------------------------------------- ringkasan segmen (worker, tanpa data member)
    segment_run_id = fields.Char(string="Run segmen", size=36, readonly=True, copy=False, index=True)
    segment_code = fields.Char(string="Kode segmen", size=48, readonly=True, copy=False)
    segment_member_count = fields.Integer(string="Jumlah anggota segmen", readonly=True, copy=False)
    segment_metrics = fields.Json(string="Metrik agregat", readonly=True, copy=False)
    segment_metrics_display = fields.Text(string="Metrik", compute="_compute_segment_metrics_display")
    segment_summary = fields.Text(string="Ringkasan segmen", readonly=True, copy=False)
    segment_generated_at = fields.Datetime(string="Segmen dihitung", readonly=True, copy=False)

    # ----------------------------------------------------------------- jejak approval
    maker_id = fields.Many2one("res.users", string="Pembuat", readonly=True, copy=False)
    editor_ids = fields.Many2many(
        "res.users",
        "efn_campaign_editor_rel",
        "campaign_id",
        "user_id",
        string="Penyunting",
        readonly=True,
        copy=False,
    )
    approved_by_id = fields.Many2one("res.users", string="Disetujui oleh", readonly=True, copy=False, tracking=True)
    approved_at = fields.Datetime(string="Disetujui pada", readonly=True, copy=False)
    sent_at = fields.Datetime(string="Diambil BFF pada", readonly=True, copy=False)
    cancelled_by_id = fields.Many2one("res.users", string="Dibatalkan oleh", readonly=True, copy=False)
    cancelled_at = fields.Datetime(readonly=True, copy=False)
    cancel_reason = fields.Char(string="Alasan batal", readonly=True, copy=False)
    outbox_ids = fields.One2many("efn.campaign.outbox", "campaign_id", string="Outbox", readonly=True)

    _efn_segment_uniq = models.Constraint(
        "UNIQUE (company_id, segment_run_id, segment_code)", "Usulan segmen sudah ada untuk run dan kode ini."
    )
    _efn_source_uniq = models.Constraint(
        "UNIQUE (company_id, source_ref_model, source_ref_id)", "Kampanye untuk sumber ini sudah ada."
    )
    _efn_amounts_positive = models.Constraint(
        "CHECK (code_qty >= 0 AND budget_idr >= 0 AND cap_per_member_week >= 0 AND approval_rev >= 0)",
        "Angka kampanye tidak boleh negatif.",
    )

    # ================================================================ compute / constraint
    @api.depends("card_ids")
    def _compute_card_count(self):
        for camp in self:
            camp.card_count = len(camp.sudo().with_context(active_test=False).card_ids)

    @api.depends("segment_metrics")
    def _compute_segment_metrics_display(self):
        for camp in self:
            metrics = camp.segment_metrics if isinstance(camp.segment_metrics, dict) else {}
            camp.segment_metrics_display = "\n".join(f"{k}: {v}" for k, v in sorted(metrics.items())) or False

    def _efn_channels(self):
        self.ensure_one()
        return [code for fname, code in CHANNELS if self[fname]]

    @api.constrains(
        *[fname for fname, _code in CHANNELS],
        "date_from",
        "date_to",
        "partner_ids",
        "voucher_mode",
        "program_id",
        "markdown_warehouse_ids",
        "company_id",
    )
    def _check_efn_campaign(self):
        for camp in self:
            if not camp._efn_channels():
                raise ValidationError(_("Pilih minimal satu kanal kampanye."))
            if camp.date_from and camp.date_to and camp.date_to < camp.date_from:
                raise ValidationError(_("Tanggal berlaku sampai harus sama atau setelah tanggal mulai."))
            if camp.partner_ids and camp.voucher_mode != "member_bound":
                raise ValidationError(_("Daftar member hanya untuk mode voucher terikat member."))
            if len(camp.partner_ids) > MAX_CODES:
                raise ValidationError(_("Maksimal %s member per kampanye.", MAX_CODES))
            # Eksplisit (selain check_company) karena penulisan form dilakukan sudo setelah cek hak tulis.
            company = camp.company_id
            if camp.partner_ids.filtered(lambda p, c=company: p.company_id and p.company_id != c):
                raise ValidationError(_("Member harus milik company kampanye (atau bersama)."))
            if camp.program_id.company_id and camp.program_id.company_id != company:
                raise ValidationError(_("Program voucher harus milik company kampanye."))
            if camp.markdown_warehouse_ids.filtered(lambda w, c=company: w.company_id != c):
                raise ValidationError(_("Outlet markdown harus milik company kampanye."))

    # ================================================================ CRUD + guard
    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and not self.env.user.has_group(ca.GROUP_USER):
            raise AccessError(_("Hanya Marketing yang dapat membuat kampanye."))
        for vals in vals_list:
            if not self.env.su and set(vals) & set(SYSTEM_FIELDS):
                raise AccessError(_("Status, jejak approval, dan ringkasan segmen kampanye diisi sistem."))
            vals.setdefault("maker_id", self.env.uid)
            if not self.env.su:
                vals.update({"state": "draft", "editor_ids": [Command.set([self.env.uid])]})
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and set(vals) & set(SYSTEM_FIELDS):
            raise AccessError(_("Status dan jejak approval kampanye hanya diubah lewat tombol."))
        content = set(vals) & set(CONTENT_FIELDS)
        if content:
            if any(camp.state not in ("draft", "proposed") for camp in self):
                raise UserError(_("Kampanye disetujui/dibatalkan tidak dapat diubah; reset ke draft dulu."))
            if "voucher_mode" in vals and any(
                camp.approval_rev > 0 and camp.voucher_mode != vals["voucher_mode"] for camp in self
            ):
                raise UserError(_("Mode voucher dikunci setelah kampanye pernah disetujui; buat kampanye baru."))
            if "company_id" in vals and any(camp.company_id.id != vals["company_id"] for camp in self):
                raise UserError(_("Company kampanye tidak dapat dipindah."))
            if not self.env.su:
                if not self.env.user.has_group(ca.GROUP_USER):
                    raise AccessError(_("Hanya Marketing yang dapat mengubah kampanye."))
                # editor_ids = field sistem -> ditulis sudo SETELAH hak tulis (ACL + record rule company) dicek.
                self.check_access("write")
                vals = {**vals, "editor_ids": [Command.link(self.env.uid)]}
                return super(EfnCampaign, self.sudo()).write(vals)
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_used(self):
        for camp in self:
            if camp.approval_rev > 0 or camp.state in ("approved", "sent"):
                raise UserError(_("Kampanye yang pernah disetujui tidak dapat dihapus (jejak audit); batalkan saja."))

    def _efn_sys_write(self, vals):
        """Tulis field sistem (sudo, melewati guard form) setelah pemeriksaan di pemanggil."""
        return super(EfnCampaign, self.sudo()).write(vals)

    def _efn_lock_rows(self):
        self.flush_recordset()
        self.env.cr.execute("SELECT id FROM efn_campaign WHERE id = ANY(%s) FOR UPDATE", [self.ids])
        self.invalidate_recordset()

    # ================================================================ tombol Marketing
    def _efn_check_user(self, group=ca.GROUP_USER):
        user = self.env.user
        if not user.has_group(group):
            raise AccessError(_("Anda tidak punya hak untuk tindakan kampanye ini."))
        self.check_access("write")
        for camp in self:
            if camp.company_id not in user.company_ids:
                raise AccessError(_("Kampanye milik company lain."))

    def action_efn_propose(self):
        self._efn_check_user()
        for camp in self:
            if camp.state != "draft":
                raise UserError(_("Hanya kampanye draft yang dapat diajukan."))
            camp._efn_sys_write({"state": "proposed"})
        return True

    def action_efn_reset_draft(self):
        self._efn_check_user()
        for camp in self:
            if camp.state not in ("proposed", "cancelled"):
                raise UserError(_("Hanya kampanye diusulkan/dibatalkan yang dapat dikembalikan ke draft."))
            camp._efn_sys_write({"state": "draft", "cancelled_by_id": False, "cancelled_at": False})
        return True

    def action_efn_cancel(self):
        self._efn_check_user()
        if any(camp.state == "approved" for camp in self) and not self.env.user.has_group(ca.GROUP_APPROVER):
            raise AccessError(_("Hanya Marketing approver yang dapat membatalkan kampanye yang sudah disetujui."))
        reason = (self.env.context.get("efn_cancel_reason") or "").strip()[:200] or False
        self._efn_lock_rows()
        now = fields.Datetime.now()
        for camp in self:
            if camp.state not in ("draft", "proposed", "approved"):
                raise UserError(_("Kampanye ini tidak dapat dibatalkan (sudah terkirim atau dibatalkan)."))
            if camp.state == "approved":
                rows = camp.sudo().outbox_ids.filtered(lambda r, c=camp: r.approval_rev == c.approval_rev)
                if rows.filtered(lambda r: r.state in ("claimed", "done")):
                    raise UserError(_("Kampanye sedang/sudah diambil BFF; tidak dapat dibatalkan dari Odoo."))
                rows.filtered(lambda r: r.state == "queued").write({"state": "failed", "last_error": "cancelled"})
                camp._efn_deactivate_cards()
            camp._efn_sys_write(
                {"state": "cancelled", "cancelled_by_id": self.env.uid, "cancelled_at": now, "cancel_reason": reason}
            )
        return True

    def _efn_deactivate_cards(self):
        cards = self.sudo().with_context(active_test=False).card_ids.filtered("active")
        if cards:
            cards.with_context(loyalty_no_mail=True).write({"active": False})

    # ================================================================ approval (SoD)
    def _efn_check_approver(self):
        user = self.env.user
        if not user.has_group(ca.GROUP_APPROVER):
            raise AccessError(_("Hanya Marketing approver yang dapat menyetujui kampanye."))
        self.check_access("read")  # record rule multi-company
        for camp in self:
            if camp.company_id not in user.company_ids:
                raise AccessError(_("Kampanye milik company lain."))
            sudo = camp.sudo()
            if sudo.maker_id == user or user in sudo.editor_ids:
                raise AccessError(_("Pemisahan tugas: pembuat/penyunting tidak boleh menyetujui kampanyenya sendiri."))

    def action_efn_approve(self):
        self._efn_check_approver()
        self._efn_lock_rows()
        now = fields.Datetime.now()
        Outbox = self.env["efn.campaign.outbox"]
        for camp in self:
            if camp.state != "proposed":
                raise UserError(_("Hanya kampanye berstatus diusulkan yang dapat disetujui."))
            if not ca.is_enabled(self.env, camp.company_id):
                raise UserError(
                    _("Kampanye promo dimatikan untuk PT ini (parameter efn_campaign.enabled); approval ditolak.")
                )
            camp._efn_check_ready()
            camp._efn_sys_write(
                {
                    "approval_rev": camp.approval_rev + 1,
                    "state": "approved",
                    "approved_by_id": self.env.uid,
                    "approved_at": now,
                }
            )
            voucher = camp._efn_generate_vouchers()
            Outbox._efn_enqueue(camp, voucher)
            _logger.info(
                "EFN campaign disetujui id=%s company=%s rev=%s mode=%s approver_uid=%s",
                camp.id,
                camp.company_code,
                camp.approval_rev,
                camp.voucher_mode,
                self.env.uid,
            )
        return True

    def _efn_check_ready(self):
        """Syarat kampanye siap disetujui (dicek ulang di server, bukan hanya di form)."""
        self.ensure_one()
        camp = self.sudo()
        today = fields.Date.context_today(self)
        if not camp.company_id.efn_brand_code:
            raise UserError(_("Company belum punya Kode Company EFN."))
        if not (camp.message_title and camp.message_body):
            raise UserError(_("Judul dan isi pesan kampanye wajib diisi."))
        if camp.date_to < today:
            raise UserError(_("Periode kampanye sudah lewat."))
        if camp.kind == "markdown" and not (camp.markdown_product_ids and camp.markdown_warehouse_ids):
            raise UserError(_("Kampanye markdown butuh produk dan outlet."))
        if camp.kind == "segment" and camp.voucher_mode != "public_code" and not camp.segment_run_id:
            raise UserError(_("Kampanye segmen butuh usulan segmen dari segment-worker (kecuali kode publik)."))
        if camp.voucher_mode == "member_bound" and not camp.program_id:
            raise UserError(_("Mode terikat member butuh program voucher."))
        if not camp.program_id:
            if camp.partner_ids:
                raise UserError(_("Daftar member hanya dipakai bila ada program voucher."))
            return
        program = camp.program_id
        if not program.active or program.company_id != camp.company_id:
            raise UserError(_("Program voucher harus aktif dan milik company kampanye."))
        if program.date_to and program.date_to < camp.date_to:
            raise UserError(_("Program voucher berakhir sebelum kampanye berakhir."))
        if camp.voucher_mode == "public_code":
            if program.program_type != "promo_code":
                raise UserError(_("Mode kode publik butuh program tipe Kode Promo."))
            camp._efn_public_rule()
            if not 1 <= camp.code_qty <= MAX_PUBLIC_QUOTA:
                raise UserError(_("Kuota kode publik harus 1..%s.", MAX_PUBLIC_QUOTA))
            other = self.sudo().search_count(
                [("program_id", "=", program.id), ("state", "in", ("approved", "sent")), ("id", "!=", camp.id)]
            )
            if other:
                raise UserError(_("Program kode publik ini sudah dipakai kampanye lain yang disetujui."))
            return
        if program.program_type != "coupons":
            raise UserError(_("Mode kode anonim / terikat member butuh program tipe Kupon."))
        if camp.voucher_mode == "anonymous_code" and not 1 <= camp.code_qty <= MAX_CODES:
            raise UserError(_("Jumlah kode anonim harus 1..%s.", MAX_CODES))
        if camp.voucher_mode == "member_bound":
            partners = camp.partner_ids
            if not partners:
                raise UserError(_("Mode terikat member butuh minimal satu member."))
            if partners.filtered(lambda p: not p.active or (p.company_id and p.company_id != camp.company_id)):
                raise UserError(_("Semua member harus aktif dan milik company kampanye (atau bersama)."))

    def _efn_public_rule(self):
        self.ensure_one()
        rules = self.sudo().program_id.rule_ids.filtered(lambda r: r.active and r.mode == "with_code" and r.code)
        if len(rules) != 1:
            raise UserError(_("Program kode publik harus punya tepat satu aturan aktif berkode."))
        return rules

    def _efn_generate_vouchers(self):
        """Buat voucher sesuai mode lewat wizard CE ``loyalty.generate.wizard`` (tanpa email CE ke member).

        Kembalikan dict ``voucher`` untuk payload outbox, atau ``None`` bila tanpa program."""
        self.ensure_one()
        camp = self.sudo()
        program = camp.program_id
        if not program:
            return None
        if camp.voucher_mode == "public_code":
            rule = camp._efn_public_rule()
            program.write({"limit_usage": True, "max_usage": camp.code_qty})
            return {"mode": "public_code", "code": rule.code, "quota": camp.code_qty}
        ctx = {"loyalty_no_mail": True, "action_no_send_mail": True, "allowed_company_ids": [camp.company_id.id]}
        Card = self.env["loyalty.card"].sudo().with_context(active_test=False)
        last_id = Card.search([], order="id desc", limit=1).id or 0
        points = max(program.reward_ids.mapped("required_points") or [1.0])
        vals = {
            "program_id": program.id,
            "points_granted": points,
            "valid_until": camp.date_to,
            "description": f"Kampanye #{camp.id} rev {camp.approval_rev}",
        }
        if camp.voucher_mode == "anonymous_code":
            vals.update({"mode": "anonymous", "coupon_qty": camp.code_qty})
            expected = camp.code_qty
        else:
            vals.update({"mode": "selected", "customer_ids": [Command.set(camp.partner_ids.ids)]})
            expected = len(camp.partner_ids)
        wizard = self.env["loyalty.generate.wizard"].sudo().with_context(**ctx).create(vals)
        result = wizard.generate_coupons()
        if isinstance(result, models.BaseModel) and result._name == "loyalty.card":
            cards = result
        else:  # versi CE yang mengembalikan True/action: ambil kartu baru program ini
            cards = Card.search([("program_id", "=", program.id), ("id", ">", last_id)], order="id")
        if len(cards) != expected:
            raise UserError(_("Jumlah voucher yang dibuat tidak sesuai; approval dibatalkan."))
        cards.with_context(loyalty_no_mail=True).write({"efn_campaign_id": camp.id})
        expiration = _iso_date(camp.date_to)
        if camp.voucher_mode == "anonymous_code":
            if cards.filtered("partner_id"):
                raise UserError(_("Voucher anonim tidak boleh terikat partner."))
            return {"mode": "anonymous_code", "codes": cards.mapped("code"), "expiration_date": expiration}
        return {
            "mode": "member_bound",
            "cards": [{"partner_id": card.partner_id.id, "code": card.code} for card in cards],
            "expiration_date": expiration,
        }

    # ================================================================ payload & status
    def _efn_idempotency_key(self, rev=None):
        self.ensure_one()
        return f"campaign:{self.company_code}:{self.id}:{rev if rev is not None else self.approval_rev}"

    def _efn_outbox_payload(self, voucher):
        """Payload ``promo.campaign.requested`` (docs/04 §28.7) + ``voucher_mode``/``voucher``. Tanpa data member
        selain ``partner_id`` integer pada mode ``member_bound`` (pengecualian L-164)."""
        self.ensure_one()
        camp = self.sudo()
        markdown = None
        capped = camp.cap_enabled
        if camp.kind == "markdown":
            markdown = {
                "product_ids": sorted(camp.markdown_product_ids.ids),
                "outlet_codes": sorted(camp.markdown_warehouse_ids.mapped("code")),
                "valid_to": _iso_date(camp.date_to),
            }
        return {
            "schema_version": ca.SCHEMA_VERSION,
            "campaign_id": camp.id,
            "company_code": camp.company_code,
            "kind": camp.kind,
            "approval_rev": camp.approval_rev,
            "segment_run_id": camp.segment_run_id or None,
            "segment_code": camp.segment_code or None,
            "markdown": markdown,
            "channels": camp._efn_channels(),
            "title": camp.message_title,
            "body": camp.message_body,
            "voucher_mode": camp.voucher_mode,
            "voucher_program_ref": camp.program_id.id or None,
            "voucher": voucher,
            "budget_idr": (camp.budget_idr or None) if capped else None,
            "cap_per_member_week": (camp.cap_per_member_week or None) if capped else None,
            "valid_from": _iso_date(camp.date_from),
            "valid_to": _iso_date(camp.date_to),
        }

    def _efn_mark_sent(self, outbox_row):
        self.ensure_one()
        if self.state == "approved" and self.approval_rev == outbox_row.approval_rev:
            self._efn_sys_write({"state": "sent", "sent_at": fields.Datetime.now()})

    def _efn_notify_delivery_failed(self, outbox_row, reason):
        """Aktivitas ke Marketing (penyetuju; fallback pembuat) bila outbox gagal final."""
        self.ensure_one()
        camp = self.sudo()
        user = camp.approved_by_id or camp.maker_id
        if not user or user.share or not user.active:
            return
        camp.activity_schedule(
            "mail.mail_activity_data_todo",
            summary=_("Kampanye gagal dikirim ke BFF"),
            note=_(
                "Outbox #%(row)s rev %(rev)s: %(reason)s.",
                row=outbox_row.id,
                rev=outbox_row.approval_rev,
                reason=reason,
            ),
            user_id=user.id,
        )

    # ================================================================ markdown (G45, tanpa depends)
    @api.model
    def _efn_propose_markdown(self, company, ref_model, ref_id, product_ids, warehouse_ids, date_to, name=None):
        """Dipanggil modul markdown (G45) saat "kirim ke member": buat kampanye ``markdown`` ``proposed``.

        Idempoten per ``(company, ref_model, ref_id)``; kampanye yang sudah ada dikembalikan apa adanya. Tetap
        butuh approval Marketing (L-156) -- pertanyaan terbuka apakah markdown boleh tanpa approval (README)."""
        if not isinstance(ref_model, str) or not REF_MODEL_RE.match(ref_model) or len(ref_model) > 64:
            raise ValidationError(_("Model sumber markdown tidak valid."))
        if not ca.is_int(ref_id) or ref_id < 1:
            raise ValidationError(_("ID sumber markdown tidak valid."))
        Campaign = self.sudo().with_context(active_test=False)
        existing = Campaign.search(
            [("company_id", "=", company.id), ("source_ref_model", "=", ref_model), ("source_ref_id", "=", ref_id)],
            limit=1,
        )
        if existing:
            return existing
        warehouses = self.env["stock.warehouse"].sudo().browse(warehouse_ids).exists()
        if warehouses.filtered(lambda w: w.company_id != company):
            raise ValidationError(_("Outlet markdown harus milik company kampanye."))
        return Campaign.create(
            {
                "name": name or _("Markdown %(model)s #%(id)s", model=ref_model, id=ref_id),
                "company_id": company.id,
                "kind": "markdown",
                "origin": "markdown",
                "state": "proposed",
                "date_to": date_to,
                "source_ref_model": ref_model,
                "source_ref_id": ref_id,
                "markdown_product_ids": [Command.set(list(product_ids))],
                "markdown_warehouse_ids": [Command.set(warehouses.ids)],
            }
        )

    # ================================================================ RPC bot (/json/2)
    @api.model
    def efn_campaign_proposal_upsert(self, **payload):
        """docs/04 §28.7: ringkasan segmen dari segment-worker -> kampanye ``proposed``. Tanpa data member.

        Idempoten per ``(company, run_id, segment_code)``. Kampanye draft/proposed diperbarui; yang sudah
        disetujui/terkirim/dibatalkan tidak diubah (``locked: true``)."""
        env, company = ca.check_caller(self.env)
        ca.check_enabled(env, company)
        ca.check_payload(
            payload,
            (
                "schema_version",
                "company_code",
                "run_id",
                "segment_code",
                "member_count",
                "metrics",
                "summary",
                "generated_at",
            ),
            ("schema_version", "company_code", "run_id", "segment_code", "member_count"),
        )
        ca.check_company_code(company, payload["company_code"])
        run_id = payload["run_id"]
        code = payload["segment_code"]
        count = payload["member_count"]
        metrics = payload.get("metrics") or {}
        summary = payload.get("summary") or ""
        generated = payload.get("generated_at")
        if (
            not isinstance(run_id, str)
            or not ca.UUID4_RE.match(run_id)
            or not isinstance(code, str)
            or not ca.SEGMENT_CODE_RE.match(code)
            or not ca.is_int(count)
            or not 0 <= count <= MAX_MEMBER_COUNT
            or not isinstance(metrics, dict)
            or len(metrics) > MAX_METRICS
            or any(not isinstance(k, str) or not ca.METRIC_KEY_RE.match(k) for k in metrics)
            or any(not ca.is_number(v) or abs(v) >= 1e12 for v in metrics.values())
            or not isinstance(summary, str)
            or len(summary) > MAX_SUMMARY
            or (generated is not None and not (isinstance(generated, str) and RFC3339_UTC_RE.match(generated)))
        ):
            raise ca.campaign_error("invalid_input")
        if ca.PII_RE.search(summary) or any(ca.PII_RE.search(k) for k in metrics):
            raise ca.campaign_error("pii_suspected")
        generated_dt = fields.Datetime.to_datetime(generated[:19].replace("T", " ")) if generated else False
        vals = {
            "segment_member_count": count,
            "segment_metrics": dict(sorted(metrics.items())),
            "segment_summary": summary or False,
            "segment_generated_at": generated_dt,
        }
        # Serialkan upsert paralel untuk kunci yang sama (UNIQUE juga menjaga, tetapi galatnya bukan galat berkode).
        lock_key = f"{company.id}:{run_id}:{code}"
        env.cr.execute("SELECT pg_advisory_xact_lock(%s, hashtext(%s))", (_UPSERT_LOCK_NAMESPACE, lock_key))
        Campaign = env["efn.campaign"].with_context(active_test=False)
        camp = Campaign.search(
            [("company_id", "=", company.id), ("segment_run_id", "=", run_id), ("segment_code", "=", code)], limit=1
        )
        if not camp:
            camp = Campaign.with_context(mail_create_nolog=True, tracking_disable=True).create(
                dict(
                    vals,
                    name=f"{code} ({run_id[:8]})",
                    company_id=company.id,
                    kind="segment",
                    origin="worker",
                    state="proposed",
                    segment_run_id=run_id,
                    segment_code=code,
                )
            )
            return {"ok": True, "campaign_id": camp.id, "state": camp.state, "created": True, "updated": False}
        if camp.state not in ("draft", "proposed"):
            return {
                "ok": True,
                "campaign_id": camp.id,
                "state": camp.state,
                "created": False,
                "updated": False,
                "locked": True,
            }
        changed = {k: v for k, v in vals.items() if camp[k] != v and not (not camp[k] and not v)}
        if changed:
            camp._efn_sys_write(changed)
        return {"ok": True, "campaign_id": camp.id, "state": camp.state, "created": False, "updated": bool(changed)}

    @api.model
    def efn_campaign_claim(self, limit=MAX_CLAIM):
        """docs/04 §28.7: ambil baris outbox ``queued`` milik PT pemanggil secara atomik (``FOR UPDATE SKIP
        LOCKED``) -> ``claimed``. Pola ``efn_promo_push_claim``. Kampanye yang periodenya lewat -> ``failed``."""
        env, company = ca.check_caller(self.env)
        ca.check_enabled(env, company)
        if not ca.is_int(limit) or not 1 <= limit <= MAX_CLAIM:
            raise ca.campaign_error("invalid_input")
        Outbox = env["efn.campaign.outbox"]
        Outbox.flush_model()
        env.cr.execute(
            "SELECT id FROM efn_campaign_outbox WHERE company_id = %s AND state = 'queued' "
            "ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED",
            (company.id, limit),
        )
        rows = Outbox.browse([r[0] for r in env.cr.fetchall()])
        today = fields.Date.context_today(Outbox)
        now = fields.Datetime.now()
        items = []
        for row in rows:
            if row.campaign_id.date_to < today:
                row.write({"state": "failed", "last_error": "expired"})
                row.campaign_id._efn_notify_delivery_failed(row, _("periode kampanye lewat sebelum diambil BFF"))
                continue
            row.write({"state": "claimed", "claimed_at": now, "attempts": row.attempts + 1})
            items.append(row._efn_claim_item())
        return {"items": items}

    @api.model
    def efn_campaign_ack(self, ids):
        """docs/04 §28.7: BFF sudah menerbitkan ``promo.campaign.requested`` -> baris ``done``.

        Per baris: milik PT lain/tidak ada -> ``not_found``; belum pernah diklaim -> ``not_claimed``; sudah done ->
        ``already_done`` (idempoten). Baris yang sempat kembali ``queued``/``failed`` tetap diterima (sudah terbit;
        hilir idempoten lewat ``idempotency_key``)."""
        env, company = ca.check_caller(self.env)
        ca.check_enabled(env, company)
        wanted = ca.check_int_list(ids, MAX_ACK)
        Outbox = env["efn.campaign.outbox"]
        Outbox.flush_model()
        env.cr.execute(
            "SELECT id FROM efn_campaign_outbox WHERE id = ANY(%s) AND company_id = %s ORDER BY id FOR UPDATE",
            (wanted, company.id),
        )
        found = {r[0] for r in env.cr.fetchall()}
        Outbox.invalidate_model()
        acked, already, rejected = [], [], []
        now = fields.Datetime.now()
        for row_id in wanted:
            if row_id not in found:
                rejected.append({"id": row_id, "code": "not_found"})
                continue
            row = Outbox.browse(row_id)
            if row.state == "done":
                already.append(row_id)
            elif row.attempts < 1:
                rejected.append({"id": row_id, "code": "not_claimed"})
            else:
                row.write({"state": "done", "done_at": now})
                row.campaign_id._efn_mark_sent(row)
                acked.append(row_id)
        return {"ok": True, "acked": acked, "already_done": already, "rejected": rejected}
