"""Kanal & outlet online + 4 metode baca BFF (katalog, stok, gambar, quote).

Kontrak: docs/04_SERVICE_CONTRACTS.md §10.4.3 no. 1-4 (+ delta §10.4.4), docs/08 §8.3 A & C.
Setiap metode BFF diawali ``_efn_online_check_caller(channel_code)``:

1. user punya grup ``group_online_channel`` (user teknis per PT, tanpa ACL dokumen);
2. kanal ada, user ∈ ``channel.user_ids``, company kanal ∈ ``user.company_ids`` (gagal = ``AccessError`` seragam,
   tanpa membedakan "kanal tidak ada" vs "bukan kanalmu");
3. kanal aktif (selain itu ``efn_online:channel_inactive``);
4. **context klien dibuang** kecuali ``lang`` (whitelist ``id_ID``/``en_US``) — klien /json/2 tidak bisa menyetel
   ``allowed_company_ids``, ``active_test``, dsb. (delta §10.4.4 no. 4) — lalu ``sudo`` + ``with_company`` kanal.

Tulis ke DB hanya lewat ``sudo`` SETELAH cek ini. Metode publik terdaftar di ``efn_core/tests/test_rpc_surface.py``
(``RPC_ALLOWLIST``, SEC-58). Tidak ada tarif pajak di kode (ADR-027): harga & pajak = pricelist + ``account.tax``.

G19 (docs/04 §11, docs/08 §10.1-§10.3):
- E-35: ``stock_hold_mode='confirm'`` diizinkan (reservasi saat place, logika di ``efn.online.order``); default batas
  bayar Odoo 70 menit (BFF 3600 s + 10).
- E-36: promo ``sale_loyalty`` standar per pricelist kanal. Quote berpromo memakai SO NYATA di savepoint yang
  di-rollback (bukan ``new()``: ``sale_loyalty`` membuat ``loyalty.card`` dengan ``order_id`` = id SO). Klaim reward
  deterministik ditulis ulang dari pola ``website_sale_loyalty._auto_apply_rewards`` (CE, tidak dipasang, tidak
  disalin). Semua galat kode -> ``promo_rejected`` tanpa pesan Odoo (anti-enumerasi kupon). Kode tidak di-log (asli
  maupun hash, L-42).
- E-37a: ``brand_code`` kanal (blok ``channel`` katalog).

Putaran perbaikan r12 (docs/04 §11.7 L-40..L-44): kunci kuota promo di place (SEC-130), batas mode confirm
``max_order_amount > 0`` + ``max_line_qty`` -> issue ``qty_limit`` (SEC-131), log promo tanpa tag (SEC-132), reward
produk hadiah tanpa stok dilepas (QA G19 #3).

G22 (docs/04 §14.B W-04 + §14.C W-12; keputusan L-72 D-22-8/D-22-9):
- B-2: blok ``channel`` katalog bertambah ``business_type`` (``retail``|``fnb``) dan ``loyalty: {earn, redeem}``
  (flag kanal ``loyalty_earn``/``loyalty_redeem``, default mati); item bertambah ``attributes`` (format A.6, ``[]``
  untuk produk tanpa atribut ``no_variant``). Produk ``combo`` tidak dikirim (D-22-9; berlaku semua kanal karena SO
  online tidak membangun baris anak combo).
- B-3: ``efn_online_sold_out`` (toggle Ops) -> ``efn_online_stock`` ``out`` + issue ``out_of_stock`` di quote; kanal
  F&B -> ``in_stock`` selain itu, tanpa ``qty``.
- B-4: baris cart ``{product_id, qty, attribute_value_ids?}``; PTAV harus ``no_variant`` milik template produk, aktif,
  bukan ``is_custom``, tepat satu nilai per atribut non-``multi`` (``attribute_invalid`` di quote). ``price_extra``
  dihitung standar ``sale.order.line`` (``product_no_variant_attribute_value_ids`` ->
  ``_get_product_price_context`` -> ``no_variant_attributes_price_extra``, diverifikasi source 19.0). ``quote_hash``
  memuat PTAV (hanya bila ada -> hash baris tanpa atribut sama dengan G21).
"""

import base64
import hashlib
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.fields import Command, Domain
from odoo.tools import float_compare, float_round, frozendict
from odoo.tools.mimetypes import guess_mimetype

from .efn_online_common import (
    ALLOWED_LANGS,
    BRAND_CODE_RE,
    CHANNEL_CODE_RE,
    DEFAULT_PAYMENT_MODE,
    FULFILMENTS,
    MAX_BATCH,
    ONLINE_PROGRAM_TYPES,
    OUTLET_CODE_RE,
    canonical_sha256,
    cart_lines_payload,
    check_int,
    check_str,
    like_escape,
    online_error,
    parse_payment_modes,
    promo_codes_payload,
    require,
)

_logger = logging.getLogger(__name__)


class _QuoteRollback(Exception):
    """Sentinel: membatalkan savepoint SO quote berpromo setelah ringkasan diambil (L-28)."""


class _PromoRejected(Exception):
    """Sentinel: satu kode/reward ditolak -> savepoint kode itu di-rollback."""


GROUP_XMLID = "efn_online_order.group_online_channel"
MAX_REWARD_LINES = 20  # batas parser BFF per grup (docs/04 §11.2, catatan lead G19)
IMAGE_SIZES = {512: "image_512", 1024: "image_1024"}
DEFAULT_CATALOG_LIMIT = 200


def iso_utc(value):
    """Datetime naive UTC Odoo -> ISO-8601 dengan ``Z`` (None bila kosong)."""
    if not value:
        return None
    return value.replace(microsecond=0).isoformat() + "Z"


def parse_iso_utc(value):
    """ISO-8601 dari BFF -> datetime naive UTC. Hanya ``Z``/``+00:00`` atau tanpa zona (dianggap UTC)."""
    check_str(value, max_len=40)
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1]
    elif text.endswith("+00:00"):
        text = text[:-6]
    try:
        parsed = fields.Datetime.to_datetime(text.replace("T", " ").split(".")[0])
    except ValueError:
        parsed = None
    require(parsed is not None)
    return parsed


class EfnOnlineChannel(models.Model):
    _name = "efn.online.channel"
    _description = "EFN Online - Kanal penjualan (per PT/brand)"
    _order = "sequence, code"
    _check_company_auto = True

    name = fields.Char(required=True, translate=True)
    code = fields.Char(
        required=True,
        help="Kode stabil yang dipakai BFF (channel_code), pola ^[A-Z][A-Z0-9-]{1,15}$, tidak berakhiran -FILE (L-46). "
        "Sejak G20 kode kanal = kode PT: GL, PB, BCR, BCF, WTZ (docs/04 §12.1, §16.1).",
    )
    brand_code = fields.Char(
        string="Kode brand",
        help="Opsional, pola ^[A-Z][A-Z0-9]{1,9}$ (mis. GL, BACHA, PB). Tidak unik: satu brand bisa punya kanal di "
        "dua PT (Bacha butik & kafe). Dikirim di blok 'channel' katalog; BFF memfilter ?brand= (docs/04 §11.3).",
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", required=True, index=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    pricelist_id = fields.Many2one("product.pricelist", required=True, check_company=True)
    partner_id = fields.Many2one(
        "res.partner",
        string="Partner generik",
        required=True,
        check_company=True,
        help="Pelanggan generik per PT (tanpa HP/email/NPWP). Pemetaan order <-> member hanya di BFF (UU PDP).",
    )
    team_id = fields.Many2one("crm.team", string="Tim penjualan", check_company=True)
    cash_rounding_id = fields.Many2one(
        "account.cash.rounding",
        string="Pembulatan tagihan",
        help="amount_payable = total SO dibulatkan dengan aturan ini (QRIS/VA = Rupiah bulat, Q-ON6). "
        "Juga dipasang di faktur online.",
    )
    journal_titipan_id = fields.Many2one(
        "account.journal",
        string="Jurnal titipan Holding",
        check_company=True,
        domain="[('type', '=', 'bank')]",
        help="ADR-019 A1: pembayaran online dicatat di jurnal bank 'Titipan Online - Holding' milik PT ini.",
    )
    stock_check = fields.Selection(
        [("none", "Tanpa cek"), ("free_qty", "Stok bebas gudang outlet")], required=True, default="free_qty"
    )
    stock_buffer = fields.Float(default=0.0, help="Dikurangkan dari stok bebas sebelum dinyatakan tersedia.")
    stock_low_threshold = fields.Float(string="Ambang stok terbatas", default=5.0)
    stock_expose_qty = fields.Boolean(string="Kirim qty stok ke BFF", default=False)
    stock_hold_mode = fields.Selection(
        [("none", "Tanpa reservasi"), ("confirm", "Reservasi saat place")],
        required=True,
        default="none",
        help="'Reservasi saat place' (E-35, docs/04 §11.1): SO dikonfirmasi saat checkout sehingga stok tercadang "
        "sampai dibayar/kedaluwarsa; kode promo hanya berlaku di mode ini (kupon terkonsumsi saat place). "
        "Kasir POS tetap bisa menjual stok tercadang (SOP Q-F19-2) sampai pesanan ditandai siap (picked).",
    )
    payment_timeout_minutes = fields.Integer(
        default=70,
        help="Batas bayar di Odoo, harus >= batas BFF + 10 menit (default 70 = 60 + 10, docs/04 §11.1). Naikkan "
        "nilai Odoo DULU sebelum batas BFF dinaikkan.",
    )
    quote_validity_minutes = fields.Integer(default=10)
    max_lines = fields.Integer(default=50)
    max_qty_per_line = fields.Integer(default=99)
    max_line_qty = fields.Integer(
        string="Batas qty per baris (reservasi)",
        required=True,
        default=20,
        help="L-41 / SEC-131: hanya berlaku di mode 'Reservasi saat place'. Qty satu produk di atas batas ini -> issue "
        "'qty_limit' di quote (place menolak). Membatasi penimbunan stok tercadang. Pilot = 20 (PLACEHOLDER).",
    )
    max_order_amount = fields.Monetary(
        currency_field="currency_id",
        help="0 = tanpa batas (hanya mode tanpa reservasi). Mode 'Reservasi saat place' WAJIB > 0 (L-41 / SEC-131; "
        "pilot Rp5.000.000 = PLACEHOLDER).",
    )
    max_order_weight_kg = fields.Float(
        string="Batas berat pesanan (kg)",
        digits="Stock Weight",
        default=0.0,
        help="W-33 / E-35b (docs/04 §12.3): 0 = mati. Total berat = Σ berat produk × qty (rumus sama dengan "
        "delivery.carrier._match_weight, dikonversi ke kg). Di atas batas -> issue 'weight_limit' di quote; place "
        "menolak.",
    )
    weight_missing_policy = fields.Selection(
        [("block", "Tolak produk tanpa berat"), ("ignore", "Hitung sebagai 0 kg")],
        string="Produk tanpa berat",
        required=True,
        default="block",
        help="Hanya berlaku bila batas berat aktif. 'Tolak': produk (bukan jasa) tanpa berat -> issue 'weight_missing' "
        "(isi berat produk dulu). 'Hitung sebagai 0 kg': produk tanpa berat tidak menambah total.",
    )
    payment_modes = fields.Char(
        string="Metode bayar",
        required=True,
        default=DEFAULT_PAYMENT_MODE,
        help="G20 E-45 (docs/04 §12.2): daftar dipisah koma dari 'qris' (gateway lewat BFF) dan 'counter' (bayar "
        "di kasir dengan kode bayar). Pilot tanpa vendor payment = 'counter' saja (L-55). 'counter' butuh faktur saat "
        "dibayar + jurnal kliring PT (modul efn_online_pos_counter) dan hanya untuk pesanan satu PT (BFF).",
    )
    invoice_on_paid = fields.Boolean(string="Faktur saat dibayar", default=True)
    # G22 W-12 (docs/04 §14.C.1 no. 5, B-2): kill switch per kanal (selain env BFF). Default mati.
    loyalty_earn = fields.Boolean(
        string="Poin EFN: earn",
        help="Pesanan online kanal ini menghasilkan Poin EFN saat diambil (event picked_up membawa eligible_amount). "
        "Juga butuh ONLINE_LOYALTY_EARN_ENABLED di BFF.",
    )
    loyalty_redeem = fields.Boolean(
        string="Poin EFN: tukar",
        help="Member boleh membayar sebagian pesanan dengan Poin EFN (porsi pembayaran, bukan diskon). Odoo "
        "memvalidasi batas kebijakan PT yang dipublikasikan; pembayaran dicatat di jurnal metode 'Poin EFN' PT. Juga "
        "butuh ONLINE_LOYALTY_REDEEM_ENABLED di BFF.",
    )
    fulfilment_pickup = fields.Boolean(default=True)
    fulfilment_delivery = fields.Boolean(default=False)
    require_outlet_fiscal_position = fields.Boolean(
        string="Outlet wajib fiscal position",
        help="Aktifkan untuk kanal F&B: pajak (PB1 kota) ditentukan fiscal position outlet pemenuhan (ADR-027 §2).",
    )
    pickup_reminder_hours = fields.Integer(
        default=48, help="Pesanan siap/terkonfirmasi tidak diambil lewat batas ini -> aktivitas (Q-ON13)."
    )
    pickup_max_attempts = fields.Integer(default=5, help="Kode ambil salah sebanyak ini -> order 'review'.")
    user_ids = fields.Many2many(
        "res.users",
        "efn_online_channel_user_rel",
        "channel_id",
        "user_id",
        string="User teknis",
        groups="base.group_system",
        help="User teknis BFF yang boleh memakai kanal ini (grup 'Kanal online (user teknis)', satu PT).",
    )
    outlet_ids = fields.One2many("efn.online.outlet", "channel_id", string="Outlet")

    _code_unique = models.Constraint("unique (code)", "Kode kanal online harus unik.")

    # ------------------------------------------------------------------ constraints
    @api.constrains("code")
    def _check_efn_code(self):
        for channel in self:
            if not CHANNEL_CODE_RE.match(channel.code or ""):
                raise ValidationError(_("Kode kanal harus berpola ^[A-Z][A-Z0-9-]{1,15}$ (mis. GL, PB, BCR, BCF)."))
            if channel.code.endswith("-FILE"):
                # L-46 / SEC-144: bentrok dengan env ODOO_API_KEY_ONLINE_<KANAL>_FILE kanal lain di BFF.
                raise ValidationError(_("Kode kanal tidak boleh berakhiran -FILE."))

    @api.constrains("brand_code")
    def _check_efn_brand_code(self):
        for channel in self:
            if channel.brand_code and not BRAND_CODE_RE.match(channel.brand_code):
                raise ValidationError(_("Kode brand harus berpola ^[A-Z][A-Z0-9]{1,9}$ (mis. GL, BACHA, PB)."))

    @api.constrains("partner_id")
    def _check_efn_partner_generic(self):
        for channel in self:
            partner = channel.partner_id.sudo()
            if partner.phone or partner.email or partner.vat or ("mobile" in partner._fields and partner.mobile):
                raise ValidationError(
                    _(
                        "Partner generik kanal online tidak boleh memiliki HP, email, atau NPWP "
                        "(minimisasi data, UU PDP)."
                    )
                )

    @api.constrains("journal_titipan_id", "invoice_on_paid")
    def _check_efn_journal(self):
        for channel in self:
            if channel.journal_titipan_id and channel.journal_titipan_id.type != "bank":
                raise ValidationError(_("Jurnal titipan harus jurnal bertipe Bank."))
            if channel.invoice_on_paid and not channel.journal_titipan_id:
                raise ValidationError(_("Faktur saat dibayar membutuhkan jurnal titipan untuk mencatat pembayaran."))

    @api.constrains("payment_modes", "invoice_on_paid")
    def _check_efn_payment_modes(self):
        for channel in self:
            modes = parse_payment_modes(channel.payment_modes)
            if modes is None:
                raise ValidationError(
                    _("Metode bayar kanal harus daftar dipisah koma dari: qris, counter (tanpa duplikat).")
                )
            if "counter" in modes and not channel.invoice_on_paid:
                # Kliring bayar di kasir hanya bersih 0 bila faktur SO dibayar di jurnal kliring (E-45 no. 6).
                raise ValidationError(_("Metode 'counter' (bayar di kasir) membutuhkan 'Faktur saat dibayar'."))

    def _efn_online_payment_modes(self):
        """Metode bayar kanal (list kanonik). Nilai tersimpan tidak valid (tidak mungkin lewat constraint) -> []."""
        self.ensure_one()
        return parse_payment_modes(self.payment_modes) or []

    @api.constrains("fulfilment_delivery", "stock_hold_mode")
    def _check_efn_unsupported(self):
        for channel in self:
            if channel.fulfilment_delivery:
                raise ValidationError(_("Pesan-antar belum didukung (Q-ON3/Q-39/Q-40); aktifkan setelah ADR delivery."))
            if channel.stock_hold_mode not in ("none", "confirm"):
                raise ValidationError(_("Mode reservasi stok tidak dikenal."))

    @api.constrains("payment_timeout_minutes", "quote_validity_minutes", "max_lines", "max_qty_per_line")
    def _check_efn_limits(self):
        for channel in self:
            if channel.payment_timeout_minutes < 5 or channel.payment_timeout_minutes > 24 * 60:
                raise ValidationError(_("Batas bayar Odoo harus 5..1440 menit."))
            if channel.quote_validity_minutes < 1 or channel.quote_validity_minutes > 60:
                raise ValidationError(_("Masa berlaku quote harus 1..60 menit."))
            if not 1 <= channel.max_lines <= 200 or not 1 <= channel.max_qty_per_line <= 9999:
                raise ValidationError(_("Batas baris (1..200) atau qty per baris (1..9999) tidak valid."))

    @api.constrains("stock_hold_mode", "max_order_amount", "max_line_qty")
    def _check_efn_hold_limits(self):
        """L-41 / SEC-131: reservasi stok (mode confirm) tanpa batas nominal = penimbunan stok murah."""
        for channel in self:
            if not 1 <= channel.max_line_qty <= 9999:
                raise ValidationError(_("Batas qty per baris (reservasi) harus 1..9999."))
            if (channel.max_order_amount or 0.0) < 0:
                raise ValidationError(_("Batas nominal pesanan tidak boleh negatif."))
            if channel.stock_hold_mode == "confirm" and not (channel.max_order_amount or 0.0) > 0:
                raise ValidationError(
                    _(
                        "Mode 'Reservasi saat place' wajib punya batas nominal pesanan (> 0) untuk mencegah "
                        "penimbunan stok."
                    )
                )

    @api.constrains("max_order_weight_kg")
    def _check_efn_weight_limit(self):
        for channel in self:
            if not 0.0 <= (channel.max_order_weight_kg or 0.0) <= 100000.0:
                raise ValidationError(_("Batas berat pesanan harus 0..100000 kg (0 = tanpa batas)."))

    @api.constrains("user_ids", "company_id")
    def _check_efn_users(self):
        group = self.env.ref(GROUP_XMLID, raise_if_not_found=False)
        for channel in self:
            for user in channel.user_ids.sudo():
                if not group or group not in user.all_group_ids:
                    raise ValidationError(
                        _("User %(login)s tidak punya grup user teknis kanal online.", login=user.login)
                    )
                if user.company_ids != channel.company_id:
                    raise ValidationError(
                        _("User teknis %(login)s harus terikat ke tepat satu company (PT kanal ini).", login=user.login)
                    )
                other = (
                    self.sudo()
                    .with_context(active_test=False)
                    .search([("id", "!=", channel.id), ("user_ids", "in", user.ids)], limit=1)
                )
                if other:
                    # L-37: satu user teknis = satu kanal (API key per kanal; rotasi/pencabutan per user).
                    raise ValidationError(
                        _(
                            "User teknis %(login)s sudah dipakai kanal %(code)s; satu user teknis hanya untuk satu "
                            "kanal online.",
                            login=user.login,
                            code=other.code,
                        )
                    )
                extra = user.all_group_ids - channel._efn_online_allowed_groups()
                if extra:
                    # SEC-101: user teknis hanya Internal User + grup kanal (+ implied keduanya); grup lain
                    # (Sales/Inventory/Accounting/Settings, dsb.) memperluas ACL bila gateway terlewati.
                    raise ValidationError(
                        _(
                            "User teknis %(login)s hanya boleh memiliki grup 'Internal User' dan grup kanal online "
                            "(grup lain: %(groups)s).",
                            login=user.login,
                            groups=", ".join(sorted(extra.mapped("name"))),
                        )
                    )

    @api.model
    def _efn_online_allowed_groups(self):
        base = self.env.ref("base.group_user") | self.env.ref(GROUP_XMLID)
        return base | base.all_implied_ids

    @api.model
    def _efn_online_recheck_users(self, users):
        """Dipanggil saat grup/company user berubah (res.users / res.groups) -> constraint kanal dijalankan ulang."""
        channels = self.sudo().with_context(active_test=False).search([("user_ids", "in", users.ids)])
        channels._check_efn_users()

    # -------------------------------------------------------------- caller / env
    @api.model
    def _efn_online_check_caller(self, channel_code):
        """Cek pemanggil metode BFF; kembalikan kanal (sudo, company kanal, context bersih)."""
        if not isinstance(channel_code, str) or not CHANNEL_CODE_RE.match(channel_code):
            raise AccessError(_("Akses kanal online ditolak."))
        # Context klien dibuang SEBELUM operasi ORM apa pun (lang tak valid/allowed_company_ids/active_test klien
        # tidak ikut dievaluasi). Hanya `lang` terpasang dari whitelist yang dipertahankan.
        lang = self.env.context.get("lang")
        bare = self.env(context=frozendict())
        clean = {}
        if lang in ALLOWED_LANGS and bare["res.lang"].sudo().search_count([("code", "=", lang)], limit=1):
            clean["lang"] = lang
        user = bare.user
        if not user.has_group(GROUP_XMLID):
            raise AccessError(_("Akses kanal online ditolak."))
        channel = bare[self._name].sudo().with_context(active_test=False).search([("code", "=", channel_code)], limit=1)
        if not channel or user not in channel.user_ids or channel.company_id not in user.sudo().company_ids:
            raise AccessError(_("Akses kanal online ditolak."))
        if not channel.active:
            raise online_error("channel_inactive")
        env = self.env(context=frozendict(clean), su=True)
        return channel.with_env(env).with_company(channel.company_id)

    def _efn_online_outlet(self, outlet_code, fulfilment=None):
        self.ensure_one()
        check_str(outlet_code, OUTLET_CODE_RE, 24)
        outlet = self.outlet_ids.filtered(lambda o: o.code == outlet_code and o.active)
        if not outlet:
            raise online_error("not_found")
        if fulfilment is not None:
            require(fulfilment in FULFILMENTS)
            if fulfilment == "pickup" and not (self.fulfilment_pickup and outlet.pickup_enabled):
                raise online_error("invalid_input")
            if fulfilment == "delivery":
                raise online_error("invalid_input")
        return outlet

    # ----------------------------------------------------------------- catalog
    def _efn_online_product_domain(self):
        self.ensure_one()
        return Domain(
            [
                ("product_tmpl_id.efn_online_channel_ids", "in", self.ids),
                ("sale_ok", "=", True),
                ("company_id", "in", [False, self.company_id.id]),
                # G22 D-22-9: combo ditunda untuk online (SO online tidak membangun baris anak combo).
                ("type", "!=", "combo"),
            ]
        )

    def _efn_online_is_fnb(self):
        """Kanal F&B = company kanal ber-jenis usaha ``fnb`` (efn_core, diturunkan dari kode PT)."""
        self.ensure_one()
        return self.company_id.efn_business_type == "fnb"

    # ------------------------------------------------------- atribut (G22 B-4)
    @api.model
    def _efn_online_attribute_lines(self, template):
        """Baris atribut ``no_variant`` template yang punya nilai terpilih (urutan baris atribut)."""
        return template.valid_product_template_attribute_line_ids.filtered(
            lambda line: line.attribute_id.create_variant == "no_variant"
        )

    @api.model
    def _efn_online_selectable_values(self, attribute_line):
        """PTAV yang boleh dipilih online: aktif dan bukan nilai kustom (teks bebas = risiko PII, docs/08 §13.1 #3)."""
        return attribute_line.product_template_value_ids.filtered(lambda v: v.ptav_active and not v.is_custom)

    def _efn_online_line_ptavs(self, product, ptav_ids):
        """PTAV baris cart (B-4) atau ``None`` bila tidak sah: nilai harus milik baris atribut ``no_variant`` template
        produk, aktif, bukan kustom; atribut non-``multi`` wajib tepat satu nilai (``required`` di katalog); ``multi``
        boleh nol atau lebih."""
        self.ensure_one()
        PTAV = self.env["product.template.attribute.value"]
        attribute_lines = self._efn_online_attribute_lines(product.product_tmpl_id)
        allowed = PTAV.browse()
        for attribute_line in attribute_lines:
            allowed |= self._efn_online_selectable_values(attribute_line)
        chosen = PTAV.browse(list(ptav_ids))
        if set(ptav_ids) - set(allowed.ids):
            return None
        for attribute_line in attribute_lines:
            values = self._efn_online_selectable_values(attribute_line)
            if not values:
                continue
            picked = chosen & values
            if attribute_line.attribute_id.display_type != "multi" and len(picked) != 1:
                return None
        return chosen

    def _efn_online_attributes(self, product):
        """Blok ``attributes`` katalog (docs/04 §14.A.6): ``[{attribute_id, name, display_type, required, values:
        [{id (PTAV), name, price_extra_excl}]}]``. ``price_extra_excl`` = ``price_extra`` PTAV setelah pajak produk
        (tanpa fiscal position; harga final tetap dari quote)."""
        self.ensure_one()
        currency = self.currency_id
        taxes = product.taxes_id._filter_taxes_by_company(self.company_id)
        result = []
        for attribute_line in self._efn_online_attribute_lines(product.product_tmpl_id):
            values = self._efn_online_selectable_values(attribute_line)
            if not values:
                continue
            attribute = attribute_line.attribute_id
            entries = []
            for value in values:
                extra = value.price_extra or 0.0
                if extra and taxes:
                    extra = taxes.compute_all(extra, currency=currency, quantity=1.0, product=product)["total_excluded"]
                entries.append({"id": value.id, "name": value.name, "price_extra_excl": currency.round(extra)})
            result.append(
                {
                    "attribute_id": attribute.id,
                    "name": attribute.name,
                    "display_type": attribute.display_type,
                    "required": attribute.display_type != "multi",
                    "values": entries,
                }
            )
        return result

    @api.model
    def _efn_online_attributes_text(self, ptavs):
        """Teks modifier untuk baris quote: ``Atribut: nilai[, nilai]`` per atribut, dipisah ``; ``."""
        groups = {}
        for value in ptavs.sorted(lambda v: (v.attribute_line_id.sequence, v.attribute_line_id.id, v.id)):
            groups.setdefault(value.attribute_id, []).append(value.name or "")
        return "; ".join(f"{attribute.name}: {', '.join(names)}" for attribute, names in groups.items())[:500]

    def _efn_online_published_products(self, product_ids):
        self.ensure_one()
        return self.env["product.product"].search(self._efn_online_product_domain() & Domain("id", "in", product_ids))

    def _efn_online_tax_label(self, tax):
        return tax.invoice_label or tax.name

    def _efn_online_price(self, product, fiscal_position=None):
        """Harga indikatif 1 unit (pricelist + mesin pajak standar)."""
        self.ensure_one()
        currency = self.currency_id
        price = self.pricelist_id._get_product_price(product, 1.0, currency=currency)
        taxes = product.taxes_id._filter_taxes_by_company(self.company_id)
        if fiscal_position:
            taxes = fiscal_position.map_tax(taxes)
        res = taxes.compute_all(price, currency=currency, quantity=1.0, product=product)
        return {
            "price_excl": currency.round(res["total_excluded"]),
            "price_incl": currency.round(res["total_included"]),
            "tax_labels": sorted({self._efn_online_tax_label(t) for t in taxes}),
        }

    def _efn_online_image_shas(self, templates):
        attachments = (
            self.env["ir.attachment"]
            .sudo()
            .search(
                [
                    ("res_model", "=", "product.template"),
                    ("res_field", "=", "image_1920"),
                    ("res_id", "in", templates.ids),
                ]
            )
        )
        return {att.res_id: att.checksum for att in attachments}

    def _efn_online_max_qty(self):
        """``max_qty`` katalog: batas input per baris; mode confirm -> juga ``max_line_qty`` (L-41)."""
        self.ensure_one()
        if self.stock_hold_mode == "confirm":
            return min(self.max_qty_per_line, self.max_line_qty)
        return self.max_qty_per_line

    # ------------------------------------------------------------- berat (W-33)
    def _efn_online_weight_to_kg(self, value):
        """Berat dalam satuan berat database (``product.weight_in_lbs``: kg default / lb) -> kg.

        Terverifikasi 19.0 ``product/models/product_template.py``: ``_get_weight_uom_id_from_ir_config_parameter``
        mengembalikan ``uom.product_uom_lb`` bila parameter = '1', selain itu ``uom.product_uom_kgm``."""
        if not value:
            return 0.0
        uom = self.env["product.template"]._get_weight_uom_id_from_ir_config_parameter()
        kg = self.env.ref("uom.product_uom_kgm")
        if uom == kg:
            return value
        return uom._compute_quantity(value, kg, round=False)

    def _efn_online_weight_kg(self, product):
        """``weight_kg`` katalog (docs/04 §12.3): berat 1 unit dalam kg (3 desimal) atau ``None`` bila kosong."""
        weight = self._efn_online_weight_to_kg(product.weight or 0.0)
        return float_round(weight, precision_digits=3) if weight > 0 else None

    def _efn_online_order_weight(self, order):
        """(total kg, id produk tanpa berat) baris SO. Rumus = ``delivery.carrier._match_weight`` 19.0:
        Σ ``line.product_id.weight × qty baris dalam UoM produk`` (qty dalam UoM produk; produk tanpa berat = 0).

        Produk "tanpa berat" (``weight_missing``) hanya dari baris biasa yang bukan jasa: baris reward ``sale_loyalty``
        (produk diskon buatan Odoo) dan produk ``service`` tidak dianggap kurang data."""
        self.ensure_one()
        total = 0.0
        missing = set()
        for line in order.order_line:
            product = line.product_id
            if line.display_type or not product:
                continue
            weight = product.weight or 0.0
            # `product_qty` hanya ada bila `delivery` menambahkannya -> qty baris dikonversi ke UoM produk.
            qty = line.product_uom_id._compute_quantity(line.product_uom_qty, line.product_id.uom_id, round=False)
            total += weight * qty
            if weight <= 0 and not line.reward_id and product.type != "service":
                missing.add(product.id)
        return self._efn_online_weight_to_kg(total), sorted(missing)

    def _efn_online_weight_check(self, order):
        """W-33 / E-35b: (blok hash, issues) bila batas berat aktif; ``(None, [])`` bila mati.

        Issue ``{product_id, code: "weight_missing"}`` per produk tanpa berat (kebijakan ``block``) lalu
        ``{product_id: None, code: "weight_limit"}`` bila total > batas. Blok hash memuat batas, total, kebijakan, dan
        produk tanpa berat -> berat/batas yang berubah antara quote dan place = ``price_changed``."""
        self.ensure_one()
        limit = self.max_order_weight_kg or 0.0
        if limit <= 0:
            return None, []
        total, missing = self._efn_online_order_weight(order)
        if self.weight_missing_policy != "block":
            missing = []
        issues = [{"product_id": pid, "code": "weight_missing"} for pid in missing]
        if float_compare(total, limit, precision_digits=3) > 0:
            issues.append({"product_id": None, "code": "weight_limit"})
        block = {
            "limit": f"{limit:.3f}",
            "total": f"{float_round(total, precision_digits=3):.3f}",
            "policy": self.weight_missing_policy,
            "missing": missing,
        }
        return block, issues

    def _efn_online_changed_since(self, since):
        """Perubahan kanal (batas qty, mode)/pricelist/pajak/kategori sejak ``since`` -> kirim ulang katalog penuh."""
        self.ensure_one()
        if self.write_date and self.write_date > since:
            return True
        if self.pricelist_id.write_date and self.pricelist_id.write_date > since:
            return True
        items = self.env["product.pricelist.item"].search_count(
            [("pricelist_id", "=", self.pricelist_id.id), ("write_date", ">", since)], limit=1
        )
        if items:
            return True
        taxes = (
            self.env["account.tax"]
            .with_context(active_test=False)
            .search_count([("company_id", "=", self.company_id.id), ("write_date", ">", since)], limit=1)
        )
        outlets = self.with_context(active_test=False).outlet_ids.filtered(lambda o: o.write_date > since)
        categories = self.env["pos.category"].search_count([("write_date", ">", since)], limit=1)
        # G22 B-2: perubahan nilai/harga ekstra atribut tidak mengubah write_date template -> katalog penuh.
        changed = [("write_date", ">", since)]
        ptav = self.env["product.template.attribute.value"].search_count(changed, limit=1)
        Lines = self.env["product.template.attribute.line"].with_context(active_test=False)
        ptal = Lines.search_count(changed, limit=1)
        return bool(taxes or outlets or categories or ptav or ptal)

    @api.model
    def efn_online_catalog(self, channel_code, since=None, cursor=None, limit=DEFAULT_CATALOG_LIMIT):
        """Delta katalog kanal (docs/04 §10.4.3 no. 1). Field whitelist: tanpa HPP, vendor, akun."""
        channel = self._efn_online_check_caller(channel_code)
        limit = check_int(limit, minimum=1, maximum=MAX_BATCH)
        since_dt = parse_iso_utc(since) if since is not None else None
        after_id = 0
        if cursor is not None:
            check_str(cursor, max_len=20)
            require(cursor.isdigit())
            after_id = int(cursor)
        server_time = fields.Datetime.now()
        Product = channel.env["product.product"]
        domain = channel._efn_online_product_domain()
        full = since_dt is None or channel._efn_online_changed_since(since_dt)
        if not full:
            domain &= Domain("write_date", ">", since_dt) | Domain("product_tmpl_id.write_date", ">", since_dt)
        products = Product.search(domain & Domain("id", ">", after_id), order="id", limit=limit + 1)
        next_cursor = None
        if len(products) > limit:
            products = products[:limit]
            next_cursor = str(products[-1].id)
        outlets_fp = channel.outlet_ids.filtered("fiscal_position_id")
        shas = channel._efn_online_image_shas(products.product_tmpl_id)
        items = []
        for product in products:
            tmpl = product.product_tmpl_id
            categ = tmpl.pos_categ_ids[:1]
            prices = [{"outlet_code": None, **channel._efn_online_price(product)}]
            for outlet in outlets_fp:
                prices.append(
                    {"outlet_code": outlet.code, **channel._efn_online_price(product, outlet.fiscal_position_id)}
                )
            items.append(
                {
                    "product_id": product.id,
                    "tmpl_id": tmpl.id,
                    "name": product.with_context(display_default_code=False).display_name,
                    "description": (tmpl.efn_online_description or "")[:2000],
                    "categ": {
                        "id": categ.id,
                        "name": categ.name,
                        "parent_id": categ.parent_id.id or None,
                        "sequence": categ.sequence,
                    }
                    if categ
                    else None,
                    "uom": product.uom_id.name,
                    "image_sha": shas.get(tmpl.id),
                    "sequence": tmpl.efn_online_sequence,
                    "prices": prices,
                    "max_qty": channel._efn_online_max_qty(),
                    # G20 W-33 (docs/04 §12.3): berat 1 unit dalam kg, None bila produk tanpa berat.
                    "weight_kg": channel._efn_online_weight_kg(product),
                    # G22 B-2 (docs/04 §14.B, format §14.A.6): modifier `no_variant`; [] bila tidak ada.
                    "attributes": channel._efn_online_attributes(product),
                }
            )
        deleted = []
        if since_dt is not None and after_id == 0:
            # Produk yang berubah sejak `since` tetapi tidak lagi terpublikasi (arsip, sale_ok mati, dicabut dari
            # kanal, pindah company). Bisa memuat id yang belum pernah dikirim: BFF mengabaikannya.
            changed = Product.with_context(active_test=False).search(
                [
                    ("company_id", "in", [False, channel.company_id.id]),
                    "|",
                    ("write_date", ">", since_dt),
                    ("product_tmpl_id.write_date", ">", since_dt),
                ],
                order="id",
                limit=2000,
            )
            published = Product.search(channel._efn_online_product_domain() & Domain("id", "in", changed.ids))
            deleted = (changed - published).ids
        return {
            "channel": channel._efn_online_channel_block(),
            "items": items,
            "deleted": deleted,
            "next_cursor": next_cursor,
            "server_time": iso_utc(server_time),
        }

    def _efn_online_channel_block(self):
        """Blok ``channel`` katalog (docs/04 §10.17 L-11): outlet dari ``efn.online.outlet`` milik kanal (termasuk
        yang nonaktif, ditandai ``active``), bukan dari ``prices[]``, agar outlet tanpa harga khusus tetap muncul."""
        self.ensure_one()
        outlets = self.with_context(active_test=False).outlet_ids.sorted(lambda o: (o.sequence, o.code))
        return {
            "code": self.code,
            "name": self.name,
            "brand_code": self.brand_code or None,  # G19 E-37a (docs/04 §11.3 no. 2)
            "company_code": self.company_id.efn_brand_code or None,
            "currency": self.currency_id.name,
            # G20 E-45 (docs/04 §12.2 no. 1, L-52): array string kanonik, mis. ["qris", "counter"].
            "payment_modes": self._efn_online_payment_modes(),
            # G22 B-2 (docs/04 §14.B, §14.C.1 no. 5): jenis kanal + flag Poin EFN kanal (kill switch Odoo).
            "business_type": "fnb" if self._efn_online_is_fnb() else "retail",
            "loyalty": {"earn": bool(self.loyalty_earn), "redeem": bool(self.loyalty_redeem)},
            "outlets": [
                {
                    "code": outlet.code,
                    "public_name": outlet.public_name,
                    "pickup_enabled": outlet.pickup_enabled,
                    "prep_minutes": outlet.prep_minutes,
                    "active": outlet.active,
                }
                for outlet in outlets
            ],
        }

    # -------------------------------------------------------------------- stock
    def _efn_online_free_qty(self, outlet, products):
        """{product_id: qty tersedia setelah buffer} untuk produk ber-stok; non-storable = None (selalu ada)."""
        self.ensure_one()
        result = {}
        storable = products.filtered("is_storable")
        if storable:
            ctx_products = storable.with_context(warehouse_id=outlet.warehouse_id.id)
            ctx_products.invalidate_recordset(["free_qty"])  # selalu baca stok terkini (tanpa cache transaksi)
            for product in ctx_products:
                result[product.id] = product.free_qty - (self.stock_buffer or 0.0)
        for product in products - storable:
            result[product.id] = None
        return result

    def _efn_online_stock_status(self, available):
        if available is None:
            return "in_stock"
        if available <= 0:
            return "out"
        if available <= (self.stock_low_threshold or 0.0):
            return "low"
        return "in_stock"

    @api.model
    def efn_online_stock(self, channel_code, outlet_code, product_ids):
        """Status stok per outlet (docs/04 §10.4.3 no. 2). Produk di luar katalog kanal tidak dikembalikan."""
        channel = self._efn_online_check_caller(channel_code)
        outlet = channel._efn_online_outlet(outlet_code)
        require(isinstance(product_ids, list) and 0 < len(product_ids) <= MAX_BATCH)
        ids = sorted({check_int(pid, minimum=1) for pid in product_ids})
        products = channel._efn_online_published_products(ids)
        fnb = channel._efn_online_is_fnb()
        free = (
            channel._efn_online_free_qty(outlet, products)
            if channel.stock_check == "free_qty" and not fnb
            else dict.fromkeys(products.ids)
        )
        result = []
        for product in products.sorted("id"):
            if product.product_tmpl_id.efn_online_sold_out:
                # G22 B-3: ditandai habis oleh Ops (semua kanal).
                result.append({"product_id": product.id, "status": "out"})
                continue
            if fnb:
                # G22 B-3 (docs/04 §14.B): kanal F&B -> in_stock, tanpa qty.
                result.append({"product_id": product.id, "status": "in_stock"})
                continue
            entry = {"product_id": product.id, "status": channel._efn_online_stock_status(free.get(product.id))}
            if channel.stock_expose_qty and free.get(product.id) is not None:
                entry["qty"] = max(free[product.id], 0.0)
            result.append(entry)
        return result

    # -------------------------------------------------------------------- image
    @api.model
    def efn_online_image(self, channel_code, tmpl_id, size):
        """Gambar produk terpublikasi (docs/04 §10.4.3 no. 3). BFF me-re-encode sebelum disimpan."""
        channel = self._efn_online_check_caller(channel_code)
        tmpl_id = check_int(tmpl_id, minimum=1)
        size = check_int(size)
        require(size in IMAGE_SIZES)
        products = channel.env["product.product"].search(
            channel._efn_online_product_domain() & Domain("product_tmpl_id", "=", tmpl_id), limit=1
        )
        tmpl = products.product_tmpl_id
        data = tmpl[IMAGE_SIZES[size]] if tmpl else False
        if not data:
            raise online_error("not_found")
        raw = base64.b64decode(data)
        return {
            "sha": hashlib.sha256(raw).hexdigest(),
            "mimetype": guess_mimetype(raw, default="application/octet-stream"),
            "data_b64": data.decode("ascii") if isinstance(data, bytes) else data,
        }

    # -------------------------------------------------------------------- quote
    @api.model
    def _efn_online_line_vals(self, product, qty, ptavs):
        """Baris SO: atribut ``no_variant`` lewat field standar ``product_no_variant_attribute_value_ids`` -> harga
        ekstra dihitung mesin harga ``sale.order.line`` (G22 B-4, [V] source 19.0)."""
        vals = {"product_id": product.id, "product_uom_qty": qty}
        if ptavs:
            vals["product_no_variant_attribute_value_ids"] = [Command.set(ptavs.ids)]
        return vals

    def _efn_online_so_vals(self, outlet, product_qty):
        """Nilai sale.order (dipakai quote via ``new()`` dan place via ``create()`` -> hitungan identik).

        ``product_qty`` = list ``(product, qty, ptavs)`` (G22 B-4)."""
        self.ensure_one()
        return {
            "partner_id": self.partner_id.id,
            "company_id": self.company_id.id,
            "pricelist_id": self.pricelist_id.id,
            "fiscal_position_id": outlet.fiscal_position_id.id or False,
            "warehouse_id": outlet.warehouse_id.id,
            "team_id": self.team_id.id or False,
            "user_id": False,
            "order_line": [
                Command.create(self._efn_online_line_vals(product, qty, ptavs)) for product, qty, ptavs in product_qty
            ],
        }

    def _efn_online_cash_round(self, amount):
        self.ensure_one()
        currency = self.currency_id
        amount = currency.round(amount)
        if self.cash_rounding_id:
            rounding = self.cash_rounding_id
            amount = float_round(amount, precision_rounding=rounding.rounding, rounding_method=rounding.rounding_method)
        return amount

    def _efn_online_summarize(self, order, outlet, fulfilment, promo_applied=None):
        """Ringkasan harga/pajak dari sale.order (baru atau tersimpan) + ``quote_hash``.

        G19 E-36 (docs/04 §11.2 no. 3): baris reward ``sale_loyalty`` diberi ``reward: true`` dan nama program; hash
        memuat ``reward_id`` per baris reward + kode terpakai (urut). Tanpa promo, hash identik dengan G17/G18
        (kunci tambahan hanya bila ada) sehingga quote yang sedang berjalan saat deploy tidak menjadi ``price_changed``.
        """
        self.ensure_one()
        currency = self.currency_id
        digits = currency.decimal_places

        def money(value):
            return float(f"{currency.round(value):.{digits}f}")

        lines = []
        hash_lines = []
        for line in order.order_line.filtered(lambda line: not line.display_type):
            qty = line.product_uom_qty
            labels = sorted({self._efn_online_tax_label(t) for t in line.tax_ids})
            reward = line.reward_id
            if reward:
                name = reward.program_id.name or reward.description or ""
            else:
                name = line.product_id.with_context(display_default_code=False).display_name
            entry = {
                "product_id": line.product_id.id,
                "name": name,
                "qty": int(qty) if float(qty).is_integer() else qty,
                "price_unit": money(line.price_unit),
                "discount": line.discount,
                "price_unit_incl": money(line.price_total / qty) if qty else 0.0,
                "subtotal_excl": money(line.price_subtotal),
                "subtotal_incl": money(line.price_total),
                "tax_labels": labels,
            }
            hash_line = [
                line.product_id.id,
                f"{qty:.6f}",
                f"{line.price_unit:.6f}",
                f"{line.discount:.4f}",
                sorted(line.tax_ids.ids),
                f"{money(line.price_subtotal):.{digits}f}",
                f"{money(line.price_total):.{digits}f}",
            ]
            # G22 B-4: modifier baris (kunci tambahan hanya bila ada -> hash baris tanpa atribut sama dengan G21).
            # `_origin`: pada `sale.order.new()` nilai x2many bisa berupa pembungkus NewId record yang ada.
            ptavs = line.product_no_variant_attribute_value_ids._origin
            entry["attribute_value_ids"] = sorted(ptavs.ids)
            entry["attributes_text"] = self._efn_online_attributes_text(ptavs) if ptavs else ""
            if ptavs:
                hash_line.append(["ptav", sorted(ptavs.ids)])
            if reward:
                entry["reward"] = True
                hash_line.append(["reward", reward.id])
            lines.append(entry)
            hash_lines.append(hash_line)
        tax_groups = []
        totals = order.tax_totals or {}
        for subtotal in totals.get("subtotals", []):
            for group in subtotal.get("tax_groups", []):
                tax_groups.append(
                    {
                        "label": group.get("group_name"),
                        "base": money(group.get("base_amount_currency", 0.0)),
                        "amount": money(group.get("tax_amount_currency", 0.0)),
                    }
                )
        amount_total = money(order.amount_total)
        amount_payable = self._efn_online_cash_round(order.amount_total)
        summary = {
            "lines": lines,
            "tax_groups": tax_groups,
            "amount_untaxed": money(order.amount_untaxed),
            "amount_tax": money(order.amount_tax),
            "amount_total": amount_total,
            "amount_payable": int(round(amount_payable)) if float(amount_payable).is_integer() else amount_payable,
            "currency": currency.name,
        }
        hashed = {
            "v": 1,
            "channel": self.id,
            "outlet": outlet.id,
            "fulfilment": fulfilment,
            "pricelist": self.pricelist_id.id,
            "fiscal_position": outlet.fiscal_position_id.id or None,
            "currency": currency.name,
            "lines": hash_lines,
            "amount_total": f"{amount_total:.{digits}f}",
            "amount_payable": f"{amount_payable:.{digits}f}",
        }
        if promo_applied:
            hashed["promo_codes"] = sorted(promo_applied)
        # G20 W-33 / E-35b (docs/04 §12.3): berat masuk hash HANYA bila batas aktif (kanal tanpa batas: hash identik
        # dengan G19). Issue berat dibawa lewat kunci privat `_weight_issues` (dibuang pemanggil, tidak ke BFF).
        weight_block, weight_issues = self._efn_online_weight_check(order)
        if weight_block is not None:
            hashed["weight"] = weight_block
        summary["_weight_issues"] = weight_issues
        summary["quote_hash"] = canonical_sha256(hashed)
        return summary

    # -------------------------------------------------------------- promo (E-36)
    def _efn_online_promo_programs(self):
        """Program yang BOLEH berlaku di kanal ini (pertahanan berlapis di atas constraint ``loyalty.program``):
        ``sale_ok``, tipe online, company kanal, ``pricelist_ids`` memuat pricelist kanal, bukan wajib OTP POS.
        Program tanpa pricelist TIDAK ikut (standar ``sale_loyalty`` menganggapnya berlaku untuk semua pricelist)."""
        self.ensure_one()
        Program = self.env["loyalty.program"]
        domain = [
            ("sale_ok", "=", True),
            ("program_type", "in", ONLINE_PROGRAM_TYPES),
            ("company_id", "=", self.company_id.id),
            ("pricelist_ids", "in", self.pricelist_id.ids),
            ("portal_visible", "=", False),
        ]
        if "efn_requires_otp" in Program._fields:
            domain.append(("efn_requires_otp", "=", False))
        return Program.search(domain, order="sequence, id")

    def _efn_online_promo_plan(self, codes, has_lines):
        """(kode yang dicoba, program kanal, perlu mesin promo?). Kanal mode ``none`` -> semua kode ditolak (§11.2
        no. 8: kupon baru terkonsumsi saat bayar -> dobel pakai antar-checkout). Promo otomatis berlaku di kedua
        mode (L-38)."""
        self.ensure_one()
        usable = list(codes) if self.stock_hold_mode == "confirm" else []
        programs = self._efn_online_promo_programs() if has_lines else self.env["loyalty.program"]
        engine = bool(programs) and (bool(usable) or bool(programs.filtered(lambda p: p.trigger == "auto")))
        return usable, programs, engine

    def _efn_online_resolve_code(self, code, programs):
        """Kode ternormalisasi (huruf besar) -> kode tersimpan Odoo (aturan ``with_code`` atau kartu kupon) milik
        ``programs``, tanpa beda huruf besar/kecil. Kartu kupon buatan Odoo berhuruf kecil (``_generate_code``).
        Ambigu / tidak ada / milik program lain -> ``None`` (ditolak tanpa menyentuh ``_try_apply_code``)."""
        if not programs:
            return None
        pattern = like_escape(code)
        rules = (
            self.env["loyalty.rule"]
            .with_context(active_test=False)
            .search([("program_id", "in", programs.ids), ("code", "=ilike", pattern)])
        )
        cards = self.env["loyalty.card"].search(
            [("program_id", "in", programs.ids), ("code", "=ilike", pattern)], limit=2
        )
        matches = {r.code for r in rules if r.mode == "with_code" and (r.code or "").upper() == code}
        matches |= {c.code for c in cards if (c.code or "").upper() == code}
        return matches.pop() if len(matches) == 1 else None

    @staticmethod
    def _efn_online_coupon_key(coupon):
        return (coupon.program_id.sequence or 0, coupon.program_id.id, coupon.id)

    @staticmethod
    def _efn_online_single_reward(rewards):
        """Satu reward yang bisa diklaim tanpa pilihan pelanggan (pola website: bukan produk multi)."""
        if len(rewards) != 1:
            return None
        if rewards.reward_type == "product" and rewards.multi_product:
            return None
        return rewards

    def _efn_online_lock_quota_programs(self, codes, programs):
        """L-40 / SEC-130 (jalur PLACE saja): ``UPDATE loyalty_program SET write_date = write_date`` atas program
        ber-``limit_usage`` yang bisa berlaku (promo otomatis kanal + pemilik kode yang dikirim), SEBELUM kuota
        dihitung / kode diterapkan.

        Odoo memakai REPEATABLE READ: snapshot terbentuk di query pertama request, jadi ``FOR UPDATE NOWAIT``
        ``sale_loyalty`` + ``invalidate_model`` tidak cukup (hitungan dibaca dari snapshot lama). Dengan UPDATE baris,
        place kedua yang paralel menunggu kunci lalu mendapat ``SerializationFailure`` (atau langsung bila yang
        pertama sudah commit setelah snapshot-nya) -> Odoo mengulang request dengan snapshot baru -> kuota habis ->
        ``price_changed``. Error konkurensi TIDAK ditangkap. Kembalikan program yang dikunci (untuk test/diagnosis).
        Batasan: pemakaian kuota di luar jalur ini (SO manual staf) tidak meng-UPDATE baris program."""
        self.ensure_one()
        Program = self.env["loyalty.program"]
        quota = programs.filtered("limit_usage")
        if not quota:
            return Program
        targets = quota.filtered(lambda p: p.trigger == "auto")
        for code in codes:
            real = self._efn_online_resolve_code(code, quota)
            if not real:
                continue
            rules = (
                self.env["loyalty.rule"]
                .with_context(active_test=False)
                .search([("program_id", "in", quota.ids), ("code", "=", real)])
            )
            cards = self.env["loyalty.card"].search([("program_id", "in", quota.ids), ("code", "=", real)])
            targets |= rules.filtered(lambda r: r.mode == "with_code").program_id | cards.program_id
        if not targets:
            return Program
        Program.flush_model()
        self.env.cr.execute(
            "UPDATE loyalty_program SET write_date = write_date WHERE id IN %s", (tuple(sorted(targets.ids)),)
        )
        Program.invalidate_model(["order_count", "total_order_count"])
        return targets

    def _efn_online_try_code(self, order, code, programs, outlet):
        """Terapkan satu kode di savepoint; True bila minimal satu reward terklaim. Semua galat -> False (pesan Odoo
        dibuang). Error konkurensi PostgreSQL TIDAK ditangkap (Odoo mengulang request). Kuota ``limit_usage`` di
        jalur place dijaga ``_efn_online_lock_quota_programs`` (SEC-130), bukan oleh ``FOR UPDATE NOWAIT`` di sini.
        L-44: reward produk storable yang stoknya tidak cukup -> kode ditolak (bukan ``issues``)."""
        real = self._efn_online_resolve_code(code, programs)
        if not real:
            return False
        try:
            with self.env.cr.savepoint():
                # `order_count`/`total_order_count` sale_loyalty = compute non-stored tanpa depends -> nilai lama bisa
                # tertahan di cache env (mis. setelah place lain di transaksi yang sama). Ini hanya cache ORM.
                self.env["loyalty.program"].invalidate_model(["order_count", "total_order_count"])
                status = order._try_apply_code(real)
                if not isinstance(status, dict) or "error" in status or not status:
                    raise _PromoRejected
                claimed = False
                for coupon in sorted(status, key=self._efn_online_coupon_key):
                    if coupon.program_id not in programs:
                        raise _PromoRejected
                    reward = self._efn_online_single_reward(status[coupon])
                    if not reward:
                        raise _PromoRejected
                    if reward in order.order_line.reward_id:
                        continue
                    result = order._apply_program_reward(reward, coupon)
                    if isinstance(result, dict) and result.get("error"):
                        raise _PromoRejected
                    claimed = True
                if not claimed or self._efn_online_reward_short(order, outlet):
                    raise _PromoRejected
        except (_PromoRejected, UserError, ValidationError):
            return False
        return True

    def _efn_online_claim_auto_rewards(self, order, programs, outlet):
        """Klaim reward promo otomatis secara deterministik (urutan program ``sequence, id``). Ditulis ulang dari
        pola ``website_sale_loyalty._auto_apply_rewards`` (CE): lewati program > 1 reward, nominatif, reward produk
        multi, reward yang sudah ada; galat satu reward tidak menggagalkan yang lain. L-44: reward produk storable
        yang membuat kebutuhan melebihi stok bebas dilewati (bukan ``issues``)."""
        claimable = order._get_claimable_rewards()
        for coupon in sorted(claimable, key=self._efn_online_coupon_key):
            program = coupon.program_id
            if program not in programs or program.trigger != "auto":
                continue
            if len(program.reward_ids) != 1 or program.is_nominative:
                continue
            reward = self._efn_online_single_reward(claimable[coupon])
            if not reward or reward in order.order_line.reward_id:
                continue
            try:
                with self.env.cr.savepoint():
                    result = order._apply_program_reward(reward, coupon)
                    if isinstance(result, dict) and result.get("error"):
                        raise _PromoRejected
                    if self._efn_online_reward_short(order, outlet):
                        raise _PromoRejected
            except (_PromoRejected, UserError, ValidationError):
                continue

    def _efn_online_promo_unacceptable(self, order):
        """L-35 + batas BFF: tagihan grup < Rp1 (QRIS tidak bisa Rp0), > 20 baris reward, atau baris reward positif."""
        self.ensure_one()
        reward_lines = order.order_line.filtered(lambda line: not line.display_type and line.reward_id)
        rounding = self.currency_id.rounding
        return (
            self._efn_online_cash_round(order.amount_total) < 1
            or len(reward_lines) > MAX_REWARD_LINES
            or any(line.price_total > rounding for line in reward_lines)
        )

    def _efn_online_apply_codes_and_auto(self, order, codes, programs, outlet):
        applied = [code for code in codes if self._efn_online_try_code(order, code, programs, outlet)]
        self._efn_online_claim_auto_rewards(order, programs, outlet)
        order._update_programs_and_rewards()
        return applied

    def _efn_online_apply_promotions(self, order, codes, programs, outlet):
        """docs/08 §10.2 E-36 no. 2: hitung program -> kode satu per satu -> klaim otomatis -> hitung ulang.
        Kembalikan kode (ternormalisasi, bentuk dari BFF) yang terpakai, urutan input; subset ``codes``.

        L-35: bila reward membuat tagihan < Rp1 (atau melanggar batas baris reward), SEMUA reward dari kode dibatalkan
        untuk grup ini (savepoint) dan kodenya masuk ``promo_rejected``; promo otomatis dihitung ulang tanpa kode.
        L-44: reward produk hadiah yang stoknya kurang dilepas per kode/program (tidak pernah menjadi ``issues``)."""
        self.ensure_one()
        # Kuota promo otomatis (`_update_programs_and_rewards` memfilter `total_order_count < max_usage`): hitungan
        # sale_loyalty = compute non-stored tanpa depends -> buang cache agar order lain di transaksi ini terhitung.
        self.env["loyalty.program"].invalidate_model(["order_count", "total_order_count"])
        order._update_programs_and_rewards()
        applied = None
        if codes:
            try:
                with self.env.cr.savepoint():
                    applied = self._efn_online_apply_codes_and_auto(order, codes, programs, outlet)
                    if applied and self._efn_online_promo_unacceptable(order):
                        raise _PromoRejected
            except _PromoRejected:
                applied = None
        if applied is None:
            applied = self._efn_online_apply_codes_and_auto(order, [], programs, outlet)
        if self._efn_online_promo_unacceptable(order):
            # Hanya promo otomatis yang tersisa: konfigurasi Marketing salah (mis. diskon 100 %) -> dicatat, total
            # tetap dihitung Odoo; BFF menolak tagihan < Rp1 / baris tidak valid (online_config_error).
            _logger.warning(
                "EFN online promo channel=%s: promo otomatis menghasilkan tagihan/baris tidak valid", self.code
            )
        if codes:
            # L-42 / SEC-132: hanya jumlah. Tanpa kode, tanpa tag hash (hash tanpa kunci atas ruang kode kecil bisa
            # dibalik pembaca log).
            _logger.info("EFN online promo channel=%s codes=%d applied=%d", self.code, len(codes), len(applied))
        return applied

    def _efn_online_create_sale_order(self, outlet, product_qty, extra_vals=None, quote=False):
        self.ensure_one()
        vals = self._efn_online_so_vals(outlet, product_qty)
        vals.update(extra_vals or {})
        ctx = {"mail_create_nosubscribe": True}
        if quote:
            ctx.update(mail_create_nolog=True, tracking_disable=True)
        return self.env["sale.order"].with_context(**ctx).create(vals)

    def _efn_online_reward_short(self, order, outlet):
        """Produk storable dari reward (beli X gratis Y) yang total kebutuhannya (baris biasa + reward) melebihi stok
        bebas outlet. Dipakai saat klaim reward (L-44): reward seperti ini dilepas, bukan menjadi ``issues``."""
        self.ensure_one()
        if self.stock_check != "free_qty" or not outlet:
            return []
        lines = order.order_line.filtered(lambda line: not line.display_type and line.product_id)
        products = lines.filtered(lambda line: line.reward_id and line.product_id.is_storable).product_id
        if not products:
            return []
        free = self._efn_online_free_qty(outlet, products)
        short = []
        for product in products:
            need = sum(lines.filtered(lambda line, p=product: line.product_id == p).mapped("product_uom_qty"))
            available = free.get(product.id)
            if available is not None and available < need:
                short.append(product.id)
        return short

    def _efn_online_quote_with_promotions(self, outlet, fulfilment, product_qty, codes, programs):
        """L-28: SO nyata di savepoint yang SELALU di-rollback (nomor SO bolong diterima; SO bukan dokumen pajak).
        Kartu/poin/kunci program yang dibuat ``sale_loyalty`` ikut di-rollback."""
        self.ensure_one()
        result = {}
        try:
            with self.env.cr.savepoint():
                order = self._efn_online_create_sale_order(outlet, product_qty, quote=True)
                applied = self._efn_online_apply_promotions(order, codes, programs, outlet)
                summary = self._efn_online_summarize(order, outlet, fulfilment, promo_applied=applied)
                summary["promo_applied"] = applied
                result["summary"] = summary
                raise _QuoteRollback
        except _QuoteRollback:
            pass
        self.env.invalidate_all()
        return result["summary"]

    def _efn_online_evaluate(self, outlet_code, fulfilment, lines, promo_codes=None, for_place=False):
        """Validasi + hitung cart satu kanal. Kembalikan (outlet, products_qty, issues, summary).

        ``for_place`` + mesin promo diperlukan -> ``summary=None`` (place menghitung dari SO tersimpan; menghindari
        dua SO per place).

        G22 B-4: ``products_qty`` = list ``(product, qty, ptavs)``; satu produk boleh beberapa baris (modifier beda).
        Batas qty reservasi, stok, dan ``efn_online_sold_out`` dihitung per PRODUK (Σ qty semua barisnya). Atribut
        tidak sah -> issue ``{product_id, code: "attribute_invalid"}`` (baris tidak dihitung; place ->
        ``invalid_input``).
        """
        self.ensure_one()
        outlet = self._efn_online_outlet(outlet_code, fulfilment)
        requested = cart_lines_payload(lines, self.max_lines)
        for _pid, _ptav_ids, qty in requested:
            require(qty <= self.max_qty_per_line)
        products = self._efn_online_published_products(sorted({pid for pid, _ptav_ids, _qty in requested}))
        by_id = {p.id: p for p in products}
        issues = []

        def add_issue(product_id, code):
            issue = {"product_id": product_id, "code": code}
            if issue not in issues:
                issues.append(issue)

        product_qty = []
        totals = {}
        for pid, ptav_ids, qty in requested:
            product = by_id.get(pid)
            if not product:
                add_issue(pid, "not_found")
                continue
            ptavs = self._efn_online_line_ptavs(product, ptav_ids)
            if ptavs is None:
                add_issue(pid, "attribute_invalid")
                continue
            product_qty.append((product, qty, ptavs))
            totals[product.id] = totals.get(product.id, 0) + qty
        if self.stock_hold_mode == "confirm":
            # L-41 / SEC-131: batas qty per produk saat stok dicadangkan sejak place (anti penimbunan).
            for pid in sorted(totals):
                if totals[pid] > self.max_line_qty:
                    add_issue(pid, "qty_limit")
        free = (
            self._efn_online_free_qty(outlet, products)
            if self.stock_check == "free_qty"
            else dict.fromkeys(products.ids)
        )
        unavailable = set()
        for pid in sorted(totals):
            available = free.get(pid)
            if by_id[pid].product_tmpl_id.efn_online_sold_out or (available is not None and available < totals[pid]):
                unavailable.add(pid)
                add_issue(pid, "out_of_stock")
        codes = list(promo_codes or [])
        usable, programs, engine = self._efn_online_promo_plan(codes, bool(product_qty))
        if engine and for_place:
            return outlet, product_qty, issues, None
        if engine:
            # L-44: reward produk hadiah yang stoknya kurang sudah dilepas di mesin promo (tidak menjadi issues).
            summary = self._efn_online_quote_with_promotions(outlet, fulfilment, product_qty, usable, programs)
        else:
            # Tanpa promo: sale.order.new() (tanpa tulis DB, perilaku G17).
            order = self.env["sale.order"].new(self._efn_online_so_vals(outlet, product_qty))
            summary = self._efn_online_summarize(order, outlet, fulfilment)
            summary["promo_applied"] = []
        summary["promo_rejected"] = [code for code in codes if code not in summary["promo_applied"]]
        for line in summary["lines"]:
            line["available"] = line["product_id"] not in unavailable
        if self.max_order_amount and summary["amount_total"] > self.max_order_amount:
            issues.append({"product_id": None, "code": "order_limit"})
        # W-33 / E-35b: weight_missing (per produk) lalu weight_limit (product_id null); keduanya membuat place ditolak.
        issues.extend(summary.pop("_weight_issues", []))
        return outlet, product_qty, issues, summary

    @api.model
    def efn_online_quote(self, channel_code, outlet_code, fulfilment, lines, promo_codes=None):
        """Harga, pajak, total, ``amount_payable``, ``quote_hash`` (docs/04 §10.4.3 no. 4 + §11.2). Tanpa promo ->
        ``new()`` (tanpa tulis DB); dengan promo -> SO di savepoint yang di-rollback (L-28; efek samping: nomor urut SO
        terpakai + kunci ``FOR UPDATE NOWAIT`` program selama request, SEC-141). Mode confirm: qty > ``max_line_qty``
        -> issue ``qty_limit`` (L-41)."""
        channel = self._efn_online_check_caller(channel_code)
        codes = promo_codes_payload(promo_codes)
        outlet, _product_qty, issues, summary = channel._efn_online_evaluate(
            outlet_code, fulfilment, lines, promo_codes=codes
        )
        valid_until = fields.Datetime.now() + timedelta(minutes=channel.quote_validity_minutes)
        summary.pop("currency")
        return {
            "channel_code": channel.code,
            "company_code": channel.company_id.efn_brand_code or None,
            "outlet_code": outlet.code,
            "fulfilment": fulfilment,
            **summary,
            "issues": issues,
            "valid_until": iso_utc(valid_until),
        }


class EfnOnlineOutlet(models.Model):
    _name = "efn.online.outlet"
    _description = "EFN Online - Outlet pemenuhan"
    _order = "sequence, code"
    _check_company_auto = True

    channel_id = fields.Many2one("efn.online.channel", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="channel_id.company_id", store=True, index=True)
    code = fields.Char(required=True, help="Kode stabil untuk BFF, pola ^[A-Z0-9][A-Z0-9-]{1,23}$.")
    public_name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    warehouse_id = fields.Many2one("stock.warehouse", required=True, check_company=True)
    fiscal_position_id = fields.Many2one(
        "account.fiscal.position",
        check_company=True,
        help="Pajak dari outlet pemenuhan (ADR-027 §2): wajib untuk kanal F&B (PB1 kota), auto_apply mati.",
    )
    pickup_enabled = fields.Boolean(default=True)
    delivery_enabled = fields.Boolean(default=False)
    resource_calendar_id = fields.Many2one("resource.calendar", string="Jam ambil", check_company=True)
    prep_minutes = fields.Integer(default=15)
    active = fields.Boolean(default=True)

    _code_unique = models.Constraint("unique (channel_id, code)", "Kode outlet harus unik per kanal.")

    @api.constrains("code")
    def _check_efn_code(self):
        for outlet in self:
            if not OUTLET_CODE_RE.match(outlet.code or ""):
                raise ValidationError(_("Kode outlet harus berpola ^[A-Z0-9][A-Z0-9-]{1,23}$."))

    @api.constrains("fiscal_position_id", "channel_id")
    def _check_efn_fiscal_position(self):
        for outlet in self:
            fp = outlet.fiscal_position_id
            if fp and fp.auto_apply:
                raise ValidationError(_("Fiscal position outlet online tidak boleh auto-apply (ADR-027 §2)."))
            if outlet.channel_id.require_outlet_fiscal_position and not fp:
                raise ValidationError(_("Kanal ini mewajibkan fiscal position di setiap outlet (pajak kota)."))

    @api.constrains("delivery_enabled")
    def _check_efn_delivery(self):
        if any(outlet.delivery_enabled for outlet in self):
            raise ValidationError(_("Pesan-antar belum didukung pada pilot (Q-ON3)."))
