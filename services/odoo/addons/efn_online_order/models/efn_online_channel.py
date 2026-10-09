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
"""

import base64
import hashlib
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.fields import Command, Domain
from odoo.tools import float_round, frozendict
from odoo.tools.mimetypes import guess_mimetype

from .efn_online_common import (
    ALLOWED_LANGS,
    CHANNEL_CODE_RE,
    FULFILMENTS,
    MAX_BATCH,
    OUTLET_CODE_RE,
    canonical_sha256,
    check_int,
    check_str,
    lines_payload,
    online_error,
    require,
)

_logger = logging.getLogger(__name__)

GROUP_XMLID = "efn_online_order.group_online_channel"
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
        help="Kode stabil yang dipakai BFF (channel_code), pola ^[A-Z][A-Z0-9-]{1,15}$, mis. RTL, FNB-<BRAND>.",
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
        [("none", "Tanpa reservasi"), ("confirm", "Reservasi saat place (belum didukung)")],
        required=True,
        default="none",
    )
    payment_timeout_minutes = fields.Integer(
        default=25, help="Batas bayar di Odoo (>= batas BFF + 10 menit; default 25 = 15 + 10, docs/04 §10.4.4 no. 6)."
    )
    quote_validity_minutes = fields.Integer(default=10)
    max_lines = fields.Integer(default=50)
    max_qty_per_line = fields.Integer(default=99)
    max_order_amount = fields.Monetary(currency_field="currency_id", help="0 = tanpa batas (PLACEHOLDER).")
    invoice_on_paid = fields.Boolean(string="Faktur saat dibayar", default=True)
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
                raise ValidationError(_("Kode kanal harus berpola ^[A-Z][A-Z0-9-]{1,15}$ (mis. RTL, FNB-KOPI)."))

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

    @api.constrains("fulfilment_delivery", "stock_hold_mode")
    def _check_efn_unsupported(self):
        for channel in self:
            if channel.fulfilment_delivery:
                raise ValidationError(_("Pesan-antar belum didukung (Q-ON3/Q-39/Q-40); aktifkan setelah ADR delivery."))
            if channel.stock_hold_mode != "none":
                raise ValidationError(_("Reservasi stok saat place belum didukung pada pilot (Q-ON4)."))

    @api.constrains("payment_timeout_minutes", "quote_validity_minutes", "max_lines", "max_qty_per_line")
    def _check_efn_limits(self):
        for channel in self:
            if channel.payment_timeout_minutes < 5 or channel.payment_timeout_minutes > 24 * 60:
                raise ValidationError(_("Batas bayar Odoo harus 5..1440 menit."))
            if channel.quote_validity_minutes < 1 or channel.quote_validity_minutes > 60:
                raise ValidationError(_("Masa berlaku quote harus 1..60 menit."))
            if not 1 <= channel.max_lines <= 200 or not 1 <= channel.max_qty_per_line <= 9999:
                raise ValidationError(_("Batas baris (1..200) atau qty per baris (1..9999) tidak valid."))

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
            ]
        )

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

    def _efn_online_changed_since(self, since):
        """Perubahan pricelist/pajak/kategori sejak ``since`` -> kirim ulang katalog penuh."""
        self.ensure_one()
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
        return bool(taxes or outlets or categories)

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
                    "max_qty": channel.max_qty_per_line,
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
            "company_code": self.company_id.efn_brand_code or None,
            "currency": self.currency_id.name,
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
        free = (
            channel._efn_online_free_qty(outlet, products)
            if channel.stock_check == "free_qty"
            else dict.fromkeys(products.ids)
        )
        result = []
        for product in products.sorted("id"):
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
    def _efn_online_so_vals(self, outlet, product_qty):
        """Nilai sale.order (dipakai quote via ``new()`` dan place via ``create()`` -> hitungan identik)."""
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
                Command.create({"product_id": product.id, "product_uom_qty": qty}) for product, qty in product_qty
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

    def _efn_online_summarize(self, order, outlet, fulfilment):
        """Ringkasan harga/pajak dari sale.order (baru atau tersimpan) + ``quote_hash``."""
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
            lines.append(
                {
                    "product_id": line.product_id.id,
                    "name": line.product_id.with_context(display_default_code=False).display_name,
                    "qty": int(qty) if float(qty).is_integer() else qty,
                    "price_unit": money(line.price_unit),
                    "discount": line.discount,
                    "price_unit_incl": money(line.price_total / qty) if qty else 0.0,
                    "subtotal_excl": money(line.price_subtotal),
                    "subtotal_incl": money(line.price_total),
                    "tax_labels": labels,
                }
            )
            hash_lines.append(
                [
                    line.product_id.id,
                    f"{qty:.6f}",
                    f"{line.price_unit:.6f}",
                    f"{line.discount:.4f}",
                    sorted(line.tax_ids.ids),
                    f"{money(line.price_subtotal):.{digits}f}",
                    f"{money(line.price_total):.{digits}f}",
                ]
            )
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
        summary["quote_hash"] = canonical_sha256(
            {
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
        )
        return summary

    def _efn_online_evaluate(self, outlet_code, fulfilment, lines):
        """Validasi + hitung cart satu kanal. Kembalikan (outlet, products_qty, issues, summary)."""
        self.ensure_one()
        outlet = self._efn_online_outlet(outlet_code, fulfilment)
        requested = lines_payload(lines, self.max_lines)
        for _pid, qty in requested:
            require(qty <= self.max_qty_per_line)
        products = self._efn_online_published_products([pid for pid, _qty in requested])
        by_id = {p.id: p for p in products}
        issues = []
        product_qty = []
        for pid, qty in requested:
            product = by_id.get(pid)
            if not product:
                issues.append({"product_id": pid, "code": "not_found"})
                continue
            product_qty.append((product, qty))
        free = (
            self._efn_online_free_qty(outlet, products)
            if self.stock_check == "free_qty"
            else dict.fromkeys(products.ids)
        )
        unavailable = set()
        for product, qty in product_qty:
            available = free.get(product.id)
            if available is not None and available < qty:
                unavailable.add(product.id)
                issues.append({"product_id": product.id, "code": "out_of_stock"})
        order = self.env["sale.order"].new(self._efn_online_so_vals(outlet, product_qty))
        summary = self._efn_online_summarize(order, outlet, fulfilment)
        for line in summary["lines"]:
            line["available"] = line["product_id"] not in unavailable
        if self.max_order_amount and summary["amount_total"] > self.max_order_amount:
            issues.append({"product_id": None, "code": "order_limit"})
        return outlet, product_qty, issues, summary

    @api.model
    def efn_online_quote(self, channel_code, outlet_code, fulfilment, lines):
        """Harga, pajak, total, ``amount_payable``, ``quote_hash`` (docs/04 §10.4.3 no. 4). TANPA tulis DB."""
        channel = self._efn_online_check_caller(channel_code)
        outlet, _product_qty, issues, summary = channel._efn_online_evaluate(outlet_code, fulfilment, lines)
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
