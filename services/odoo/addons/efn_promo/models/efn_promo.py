"""Promo Super-App (docs/04 §18.1): banner slider + pop-up, dikelola di Odoo, dibaca BFF lewat RPC terfilter.

- ``efn.promo.banner`` / ``efn.promo.popup`` (``_inherit`` bersama ``efn.promo.mixin``): materi visual + tautan; tidak
  memuat harga/diskon (diskon nyata tetap ``sale_loyalty``). Satu record milik satu PT, tampil untuk semua brand.
- RPC #22 ``efn_promo_feed``, #23 ``efn_promo_image``, #24 ``efn_promo_push_claim`` hanya untuk user teknis
  ``group_promo_bff`` (Internal User + grup itu saja, tepat satu company), lalu ``sudo`` terbatas company itu.
- Tidak ada data member di Odoo: push dikirim BFF ke member ber-consent marketing.
"""

import base64
import hashlib
import logging
import re

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import frozendict
from odoo.tools.mimetypes import guess_mimetype

_logger = logging.getLogger(__name__)

GROUP_BFF = "efn_promo.group_promo_bff"
BRAND_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")
URL_RE = re.compile(r"^https://[A-Za-z0-9.-]+(?::\d{1,5})?(?:/[A-Za-z0-9._~/%-]*)?(?:\?[A-Za-z0-9._~=&%-]*)?$")
MAX_BANNERS = 20
MAX_POPUPS = 10
MAX_PUSH_CLAIM = 5
LINK_TYPES = [
    ("none", "Tanpa tautan"),
    ("brand", "Buka brand"),
    ("product", "Buka produk"),
    ("url", "Tautan web (https)"),
]
FREQUENCIES = [("once", "Sekali per kampanye"), ("daily", "Sekali per hari"), ("every_open", "Setiap aplikasi dibuka")]
ERROR_CODES = frozenset({"invalid_input", "not_found"})


def promo_error(code):
    assert code in ERROR_CODES
    return UserError(f"efn_promo:{code}")


def iso(dt):
    return fields.Datetime.to_datetime(dt).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


class EfnPromoMixin(models.AbstractModel):
    _name = "efn.promo.mixin"
    _description = "Materi promo Super-App (bersama banner & pop-up)"
    _order = "sequence, date_start desc, id desc"

    name = fields.Char(string="Nama internal", required=True)
    title = fields.Char(string="Judul", size=80, required=True, translate=True)
    subtitle = fields.Char(string="Teks", size=160, translate=True)
    image = fields.Image(string="Gambar", max_width=1920, max_height=1920, required=True)
    cta_label = fields.Char(string="Label tombol", size=24, translate=True)
    link_type = fields.Selection(LINK_TYPES, string="Tautan", default="none", required=True)
    link_brand = fields.Char(string="Kode brand", help="Contoh: PB, GL, BCR, WTZ.")
    link_product_id = fields.Many2one("product.template", string="Produk", check_company=True)
    link_channel = fields.Char(string="Kode kanal produk", help="Kanal online produk, contoh: PB, GL.")
    link_url = fields.Char(string="URL (https)")
    channel_web = fields.Boolean(string="Tampil di web", default=True)
    channel_app = fields.Boolean(string="Tampil di aplikasi", default=True)
    date_start = fields.Datetime(string="Mulai tayang", required=True, default=fields.Datetime.now)
    date_end = fields.Datetime(string="Selesai tayang", required=True)
    sequence = fields.Integer(string="Urutan", default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", required=True, index=True, default=lambda self: self.env.company)
    image_sha = fields.Char(string="SHA gambar", compute="_compute_image_sha", store=True, readonly=True)
    state_label = fields.Char(string="Status tayang", compute="_compute_state_label")

    @api.depends("image")
    def _compute_image_sha(self):
        for rec in self:
            raw = base64.b64decode(rec.image) if rec.image else b""
            rec.image_sha = hashlib.sha256(raw).hexdigest() if raw else False

    @api.depends("date_start", "date_end", "active")
    def _compute_state_label(self):
        now = fields.Datetime.now()
        for rec in self:
            if not rec.active:
                rec.state_label = _("Nonaktif")
            elif rec.date_start and rec.date_start > now:
                rec.state_label = _("Terjadwal")
            elif rec.date_end and rec.date_end <= now:
                rec.state_label = _("Berakhir")
            else:
                rec.state_label = _("Tayang")

    @api.constrains(
        "date_start",
        "date_end",
        "link_type",
        "link_brand",
        "link_product_id",
        "link_channel",
        "link_url",
        "channel_web",
        "channel_app",
    )
    def _check_efn_promo(self):
        for rec in self:
            if rec.date_end and rec.date_start and rec.date_end <= rec.date_start:
                raise ValidationError(_("Selesai tayang harus setelah mulai tayang."))
            if not (rec.channel_web or rec.channel_app):
                raise ValidationError(_("Pilih minimal satu: tampil di web atau di aplikasi."))
            if rec.link_type == "brand" and not (rec.link_brand and BRAND_RE.match(rec.link_brand)):
                raise ValidationError(_("Kode brand harus huruf besar, contoh PB atau GL."))
            if rec.link_type == "product" and not (
                rec.link_product_id and rec.link_channel and BRAND_RE.match(rec.link_channel)
            ):
                raise ValidationError(_("Tautan produk butuh produk dan kode kanal (contoh PB)."))
            if rec.link_type == "url" and not (
                rec.link_url and URL_RE.match(rec.link_url) and len(rec.link_url) <= 300
            ):
                raise ValidationError(_("URL harus diawali https:// dan maksimal 300 karakter."))

    # ------------------------------------------------------------------ feed
    def _efn_promo_link(self):
        self.ensure_one()
        if self.link_type == "brand":
            return {"type": "brand", "brand": self.link_brand}
        if self.link_type == "product" and self.link_product_id:
            product = self.link_product_id.product_variant_id
            return {"type": "product", "channel": self.link_channel, "product_id": product.id} if product else None
        if self.link_type == "url":
            return {"type": "url", "url": self.link_url}
        return None

    def _efn_promo_payload(self):
        self.ensure_one()
        return {
            "id": self.id,
            "title": self.title or "",
            "subtitle": self.subtitle or "",
            "cta_label": self.cta_label or "",
            "link": self._efn_promo_link(),
            "web": bool(self.channel_web),
            "app": bool(self.channel_app),
            "date_start": iso(self.date_start),
            "date_end": iso(self.date_end),
            "sequence": self.sequence,
            "image_sha": self.image_sha or None,
            "write_date": iso(self.write_date),
        }


class EfnPromoBanner(models.Model):
    _name = "efn.promo.banner"
    _inherit = ["efn.promo.mixin", "mail.thread"]
    _description = "Banner slider promo Super-App"
    _order = "sequence, date_start desc, id desc"

    title = fields.Char(tracking=True)
    date_start = fields.Datetime(tracking=True)
    date_end = fields.Datetime(tracking=True)
    active = fields.Boolean(tracking=True)

    # ================================================================ caller
    @api.model
    def _efn_promo_allowed_groups(self):
        base = self.env.ref("base.group_user") | self.env.ref(GROUP_BFF)
        return base | base.all_implied_ids

    @api.model
    def _efn_promo_check_caller(self):
        """User teknis BFF (Internal User + grup BFF saja, tepat satu company). Kembalikan env sudo company itu."""
        bare = self.env(context=frozendict())
        denied = AccessError(bare._("Akses promo ditolak."))
        user = bare.user
        if not user.has_group(GROUP_BFF) or user.share or not user.active:
            raise denied
        user_sudo = user.sudo()
        companies = user_sudo.company_ids
        if len(companies) != 1:
            raise denied
        if user_sudo.all_group_ids - bare["efn.promo.banner"].sudo()._efn_promo_allowed_groups():
            _logger.error("EFN promo: user teknis #%s punya grup tambahan; RPC ditolak", user.id)
            raise denied
        return self.env(context=frozendict(active_test=True), su=True), companies

    # =================================================================== RPC
    @api.model
    def efn_promo_feed(self, since=None):
        """#22 Banner & pop-up yang belum berakhir (aktif), field whitelist (docs/04 §18.1)."""
        env, company = self._efn_promo_check_caller()
        if since is not None and not (isinstance(since, str) and len(since) <= 40):
            raise promo_error("invalid_input")
        now = fields.Datetime.now()
        domain = [("company_id", "=", company.id), ("date_end", ">", now)]
        banners = env["efn.promo.banner"].search(domain, limit=MAX_BANNERS)
        popups = env["efn.promo.popup"].search(domain, limit=MAX_POPUPS)
        return {
            "company_code": company.efn_brand_code or None,
            "banners": [b._efn_promo_payload() for b in banners],
            "popups": [dict(p._efn_promo_payload(), frequency=p.frequency) for p in popups],
            "server_time": iso(now),
        }

    @api.model
    def efn_promo_image(self, kind, record_id):
        """#23 Gambar promo (pola efn_online_image). BFF me-re-encode sebelum disimpan."""
        env, company = self._efn_promo_check_caller()
        if (
            kind not in ("banner", "popup")
            or not isinstance(record_id, int)
            or isinstance(record_id, bool)
            or record_id < 1
        ):
            raise promo_error("invalid_input")
        rec = env[f"efn.promo.{kind}"].search([("id", "=", record_id), ("company_id", "=", company.id)], limit=1)
        if not rec or not rec.image:
            raise promo_error("not_found")
        raw = base64.b64decode(rec.image)
        data = rec.image
        return {
            "sha": hashlib.sha256(raw).hexdigest(),
            "mimetype": guess_mimetype(raw, default="application/octet-stream"),
            "data_b64": data.decode("ascii") if isinstance(data, bytes) else data,
        }


class EfnPromoPopup(models.Model):
    _name = "efn.promo.popup"
    _inherit = ["efn.promo.mixin", "mail.thread"]
    _description = "Pop-up promo Super-App"
    _order = "sequence, date_start desc, id desc"

    title = fields.Char(tracking=True)
    date_start = fields.Datetime(tracking=True)
    date_end = fields.Datetime(tracking=True)
    active = fields.Boolean(tracking=True)
    frequency = fields.Selection(FREQUENCIES, string="Frekuensi tampil", default="once", required=True)
    push_enabled = fields.Boolean(string="Juga kirim push notification", default=False)
    push_title = fields.Char(string="Judul push", size=48, translate=True)
    push_body = fields.Char(string="Isi push", size=120, translate=True)
    push_state = fields.Selection(
        [("draft", "Belum dikirim"), ("queued", "Antre kirim"), ("sent", "Terkirim ke BFF")],
        string="Status push",
        default="draft",
        readonly=True,
        copy=False,
        tracking=True,
    )
    push_sent_at = fields.Datetime(string="Push diambil BFF", readonly=True, copy=False)

    @api.constrains("push_enabled", "push_title", "push_body")
    def _check_efn_push(self):
        for rec in self:
            if rec.push_enabled and not (rec.push_title and rec.push_body):
                raise ValidationError(_("Push butuh judul dan isi."))

    def action_efn_queue_push(self):
        """Pengelola Promo: antrekan push (dikirim BFF hanya ke member ber-consent marketing)."""
        now = fields.Datetime.now()
        for rec in self:
            if not rec.push_enabled:
                raise UserError(_("Aktifkan 'Juga kirim push notification' dulu."))
            if rec.push_state != "draft":
                raise UserError(_("Push promo ini sudah diantrekan atau terkirim."))
            if not rec.active or rec.date_end <= now or rec.date_start > now:
                raise UserError(_("Push hanya untuk promo yang sedang tayang."))
            rec.push_state = "queued"
        return True

    @api.model
    def efn_promo_push_claim(self, limit=MAX_PUSH_CLAIM):
        """#24 Ambil push antre (atomik queued -> sent) untuk dikirim BFF. Tanpa data member."""
        env, company = self.env["efn.promo.banner"]._efn_promo_check_caller()
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_PUSH_CLAIM:
            raise promo_error("invalid_input")
        now = fields.Datetime.now()
        recs = env["efn.promo.popup"].search(
            [("company_id", "=", company.id), ("push_state", "=", "queued"), ("date_end", ">", now)], limit=limit
        )
        if not recs:
            return {"items": []}
        # Kunci baris agar dua replika BFF tidak mengirim dua kali.
        env.cr.execute(
            "SELECT id FROM efn_promo_popup WHERE id = ANY(%s) AND push_state = 'queued' FOR UPDATE SKIP LOCKED",
            [recs.ids],
        )
        locked = env["efn.promo.popup"].browse([r[0] for r in env.cr.fetchall()])
        items = [
            {"id": r.id, "push_title": r.push_title, "push_body": r.push_body, "link": r._efn_promo_link()}
            for r in locked
        ]
        locked.write({"push_state": "sent", "push_sent_at": now})
        return {"items": items}
