"""Kiriman QR ponsel + 3 RPC BFF (E-27, docs/04 §14.A.5 path 13-15, §14.7 L-70/L-71/L-72/L-76).

Satu record = satu kiriman (``submission``) dari Super-App = **satu ``pos.order`` draft baru di meja** (L-70: order
pelayan tidak pernah disentuh, sehingga ``recompute_prices`` tidak menimpa harga/diskon manager). Record juga menjadi
kunci idempotensi ``(config, idempotency_key)`` + ``payload_sha256`` (tanpa isi catatan/baris; hanya id produk & qty
ada di ``line_uuids``/``item_count``).

RPC (``/json/2/efn.pos.qr.order/<metode>``, user teknis per PT ``efn_qr_pb``/``efn_qr_bcf`` lewat odoo-rpc-gateway,
terdaftar di ``RPC_ALLOWLIST``):

- ``efn_qr_menu(config_id, since=None, cursor=None, limit=200)`` (#13, baca)
- ``efn_qr_submit(config_id, table_session_ref, submission, order_uuid=None)`` (#14)
- ``efn_qr_status(config_id, submission_refs)`` (#15, baca)

Setiap RPC diawali ``_efn_qr_check_caller(config_id)``: grup ``group_qr_bff``; user internal aktif dengan TEPAT satu
company = company config; tidak punya grup lain selain Internal User (+ implied) — fail closed bila user teknis diberi
grup tambahan (SEC-101 pola E-28); context klien dibuang kecuali ``lang`` whitelist; lalu ``sudo`` + ``with_company``.
Galat akses = ``AccessError`` seragam (tidak membedakan config tidak ada vs milik PT lain). Galat bisnis =
``UserError("efn_qr:<kode>")``. Harga SELALU dihitung Odoo (klien tidak mengirim harga). Log hanya id, tanpa catatan.
"""

import json
import logging
import uuid

from odoo import api, fields, models
from odoo.exceptions import AccessError
from odoo.fields import Domain
from odoo.tools import frozendict

from odoo.addons.efn_core.scope import in_scope, internal_scope
from odoo.addons.efn_pos_kds.models.pos_order import prep_line_uuids

from .qr_common import (
    ALLOWED_LANGS,
    IDEMPOTENCY_KEY_RE,
    MAX_ATTRIBUTE_VALUES,
    MAX_LINE_QTY,
    MAX_LINES,
    MAX_MENU_LIMIT,
    MAX_STATUS_REFS,
    ORDER_UUID_RE,
    STATE_RANK,
    SUBMISSION_STATES,
    TERMINAL_STATES,
    UUID_RE,
    canonical_sha256,
    check_int,
    check_str,
    clean_note,
    iso_utc,
    note_json,
    parse_since,
    qr_error,
    require,
)

_logger = logging.getLogger(__name__)
# Logger alert (rule Loki `|= "EFN_QR_ORDER_STALE"`); satu baris JSON tanpa PII.
_alert_logger = logging.getLogger("odoo.addons.efn_pos_bff_order.alert")

GROUP_XMLID = "efn_pos_bff_order.group_qr_bff"
# Penanda jalur internal (efn_core.scope, bukan context RPC): baris order QR boleh membawa efn_qr_submission_ref.
QR_INTERNAL_SCOPE = "efn_qr_internal"
NOTE_RETENTION_DAYS = 90  # L-76
MULTI_DISPLAY = "multi"
DISPLAY_TYPES = ("radio", "select", "multi", "pills")


class EfnPosQrOrder(models.Model):
    _name = "efn.pos.qr.order"
    _description = "EFN POS - Kiriman pesanan QR ponsel"
    _order = "id desc"
    _check_company_auto = True
    _rec_name = "submission_ref"

    company_id = fields.Many2one("res.company", required=True, index=True, readonly=True)
    config_id = fields.Many2one("pos.config", required=True, index=True, readonly=True, check_company=True)
    table_session_id = fields.Many2one(
        "efn.pos.table.session", required=True, index=True, readonly=True, ondelete="cascade", check_company=True
    )
    submission_ref = fields.Char(required=True, readonly=True, copy=False, help="UUID kiriman dari BFF.")
    idempotency_key = fields.Char(required=True, readonly=True, copy=False)
    payload_sha256 = fields.Char(required=True, readonly=True, copy=False)
    order_id = fields.Many2one("pos.order", readonly=True, index=True, ondelete="set null", check_company=True)
    order_uuid = fields.Char(readonly=True, help="uuid order POS yang dibuat (tetap ada bila order dihapus/digabung).")
    line_uuids = fields.Json(readonly=True, help="uuid baris order yang dibuat server (untuk status tiket dapur).")
    item_count = fields.Integer(readonly=True, help="Σ qty kiriman (batas per sesi meja).")
    initial_state = fields.Selection([(s, s) for s in SUBMISSION_STATES], readonly=True)
    last_status = fields.Selection([(s, s) for s in SUBMISSION_STATES], readonly=True, index=True)
    last_seq = fields.Integer(readonly=True)
    sent_to_kitchen = fields.Boolean(readonly=True)
    stale_alerted = fields.Boolean(readonly=True)
    result = fields.Json(readonly=True, help="Hasil RPC submit (id produk, nama produk, qty, nominal); tanpa catatan.")

    _config_key_unique = models.Constraint(
        "unique (config_id, idempotency_key)", "Kunci idempotensi kiriman QR harus unik per POS."
    )
    _submission_ref_unique = models.Constraint("unique (submission_ref)", "submission_ref harus unik.")

    # ================================================================== pemanggil
    @api.model
    def _efn_qr_allowed_groups(self):
        base = self.env.ref("base.group_user") | self.env.ref(GROUP_XMLID)
        return base | base.all_implied_ids

    @api.model
    def _efn_qr_check_caller(self, config_id, allow_probe=False):
        """Cek user teknis BFF; kembalikan config (sudo, company config, context bersih).

        ``allow_probe`` (hanya ``efn_qr_status``): ``config_id=0`` setelah cek user lolos -> ``None`` (verifikasi
        rotasi API key oleh devsecops, L-76: jawaban 200 kosong, tidak pernah 500)."""
        lang = self.env.context.get("lang")
        bare = self.env(context=frozendict())
        # Pesan dari env bersih: _() membaca lang context klien (mis. kode tidak valid -> UserError, bukan 403).
        denied = AccessError(bare._("Akses QR ditolak."))
        clean = {}
        if lang in ALLOWED_LANGS and bare["res.lang"].sudo().search_count([("code", "=", lang)], limit=1):
            clean["lang"] = lang
        user = bare.user
        if not user.has_group(GROUP_XMLID) or user.share or not user.active:
            raise denied
        user_sudo = user.sudo()
        companies = user_sudo.company_ids
        if len(companies) != 1:
            raise denied
        if user_sudo.all_group_ids - bare["efn.pos.qr.order"].sudo()._efn_qr_allowed_groups():
            # Fail closed: user teknis tidak boleh memegang grup lain (POS/Sales/Accounting/Settings).
            _logger.error("EFN QR: user teknis #%s punya grup tambahan; RPC ditolak", user.id)
            raise denied
        if allow_probe and config_id == 0 and not isinstance(config_id, bool):
            return None
        if not isinstance(config_id, int) or isinstance(config_id, bool) or config_id <= 0:
            raise denied
        config = bare["pos.config"].sudo().browse(config_id).exists()
        if not config or config.company_id != companies:
            raise denied
        env = self.env(context=frozendict(clean), su=True)
        return config.with_env(env).with_company(config.company_id)

    # ================================================================== menu
    @api.model
    def _efn_qr_product_domain(self, config):
        domain = Domain(
            [
                ("available_in_pos", "=", True),
                ("sale_ok", "=", True),
                ("type", "!=", "combo"),  # D-22-9: combo ditunda untuk QR/online (tetap di POS kasir)
                ("company_id", "in", [False, config.company_id.id]),
            ]
        )
        if config.limit_categories and config.iface_available_categ_ids:
            domain &= Domain("pos_categ_ids", "in", config.iface_available_categ_ids.ids)
        return domain

    @api.model
    def _efn_qr_taxes(self, config, product, fiscal_position):
        taxes = product.taxes_id._filter_taxes_by_company(config.company_id)
        return fiscal_position.map_tax(taxes) if fiscal_position else taxes

    @api.model
    def _efn_qr_excl(self, config, product, taxes, amount):
        if not taxes:
            return config.currency_id.round(amount)
        res = taxes.compute_all(amount, config.currency_id, 1.0, product=product)
        return config.currency_id.round(res["total_excluded"])

    @api.model
    def _efn_qr_tax_labels(self, taxes):
        labels = []
        flat = taxes.flatten_taxes_hierarchy() if hasattr(taxes, "flatten_taxes_hierarchy") else taxes
        for tax in flat.sorted(lambda t: (t.sequence, t.id)):
            label = tax.invoice_label or tax.name or ""
            if tax.amount_type == "percent" and "%" not in label:
                label = f"{label} {tax.amount:g}%"
            labels.append(label[:40])
        return labels

    @api.model
    def _efn_qr_attributes(self, config, product, taxes):
        """Atribut ``no_variant`` template (§14.A.6), tanpa nilai ``is_custom`` (teks bebas = risiko PII)."""
        result = []
        for line in product.product_tmpl_id.attribute_line_ids.sorted(lambda ln: (ln.sequence, ln.id)):
            attribute = line.attribute_id
            if attribute.create_variant != "no_variant":
                continue
            values = line.product_template_value_ids.filtered(lambda v: v.ptav_active and not v.is_custom)
            if not values:
                continue
            display = attribute.display_type if attribute.display_type in DISPLAY_TYPES else "radio"
            result.append(
                {
                    "attribute_id": attribute.id,
                    "name": attribute.name,
                    "display_type": display,
                    "required": display != MULTI_DISPLAY,
                    "values": [
                        {
                            "id": value.id,
                            "name": value.name,
                            "price_extra_excl": self._efn_qr_excl(config, product, taxes, value.price_extra),
                        }
                        for value in values.sorted(lambda v: (v.product_attribute_value_id.sequence, v.id))
                    ],
                }
            )
        return result

    @api.model
    def _efn_qr_menu_item(self, config, product, fiscal_position, pricelist):
        taxes = self._efn_qr_taxes(config, product, fiscal_position)
        price = pricelist._get_product_price(product, 1.0, currency=config.currency_id)
        return {
            "product_id": product.id,
            "name": product.with_context(display_default_code=False).display_name,
            "price_excl": self._efn_qr_excl(config, product, taxes, price),
            "tax_labels": self._efn_qr_tax_labels(taxes),
            "available": bool(product.self_order_available),
            "attributes": self._efn_qr_attributes(config, product, taxes),
        }

    @api.model
    def _efn_qr_settings_changed(self, config, since):
        """Perubahan config/preset/FP/pricelist/pajak sejak ``since`` -> kirim ulang menu penuh."""
        preset, fiscal_position, pricelist = config._efn_qr_pricing()
        for record in (config, preset, fiscal_position, pricelist):
            if record and record.write_date and record.write_date > since:
                return True
        if pricelist and self.env["product.pricelist.item"].search_count(
            [("pricelist_id", "=", pricelist.id), ("write_date", ">", since)], limit=1
        ):
            return True
        return bool(
            self.env["account.tax"]
            .with_context(active_test=False)
            .search_count([("company_id", "=", config.company_id.id), ("write_date", ">", since)], limit=1)
        )

    @api.model
    def efn_qr_menu(self, config_id, since=None, cursor=None, limit=MAX_MENU_LIMIT):
        """#13 overlay menu per config (baca). ``since`` kosong = penuh (BFF menandai produk yang tidak muncul sebagai
        terhapus); ``since`` terisi = delta + ``deleted`` (produk berubah yang keluar dari menu)."""
        config = self._efn_qr_check_caller(config_id)
        self = self.with_env(config.env)  # noqa: PLW0642 - sudo + context bersih setelah cek pemanggil
        server_time = fields.Datetime.now()
        since_dt = parse_since(since)
        limit = check_int(limit, 1, MAX_MENU_LIMIT) if limit is not None else MAX_MENU_LIMIT
        after = 0
        if cursor not in (None, "", False):
            require(isinstance(cursor, str) and cursor.isdigit() and len(cursor) <= 12)
            after = int(cursor)
        preset, fiscal_position, pricelist = config._efn_qr_pricing()
        Product = self.env["product.product"]
        base = self._efn_qr_product_domain(config)
        full = since_dt is None or self._efn_qr_settings_changed(config, since_dt)
        if full:
            candidates = Product.search(base & Domain("id", ">", after), order="id", limit=limit + 1)
        else:
            changed_tmpl = (
                self.env["product.template.attribute.value"]
                .with_context(active_test=False)
                .search([("write_date", ">", since_dt)])
                .product_tmpl_id
            )
            changed = Domain.OR(
                [
                    Domain("write_date", ">", since_dt),
                    Domain("product_tmpl_id.write_date", ">", since_dt),
                    Domain("product_tmpl_id", "in", changed_tmpl.ids),
                ]
            )
            candidates = Product.with_context(active_test=False).search(
                changed & Domain("id", ">", after) & Domain("company_id", "in", [False, config.company_id.id]),
                order="id",
                limit=limit + 1,
            )
        more = len(candidates) > limit
        candidates = candidates[:limit]
        in_menu = candidates.with_context(active_test=True).filtered_domain(base & Domain("active", "=", True))
        deleted = [] if full else sorted((candidates - in_menu).ids)
        items = [self._efn_qr_menu_item(config, product, fiscal_position, pricelist) for product in in_menu]
        return {
            "config": {
                "config_id": config.id,
                "company_code": config._efn_qr_company_code(),
                "outlet_name": (config.name or "")[:64],
                "qr_enabled": bool(config.efn_qr_enabled and not config._efn_qr_config_problem()),
                "confirm_mode": config.efn_qr_confirm_mode,
            },
            "items": items,
            "deleted": deleted,
            "next_cursor": str(candidates[-1].id) if more and candidates else None,
            "server_time": iso_utc(server_time),
        }

    # ================================================================== submit
    @api.model
    def _efn_qr_parse_lines(self, lines):
        """Validasi bentuk ``lines`` (input BFF tidak tepercaya). Hasil: list dict ternormalisasi."""
        require(isinstance(lines, list) and lines)
        require(len(lines) <= MAX_LINES, "limit_exceeded")
        result = []
        for line in lines:
            require(isinstance(line, dict) and set(line) <= {"product_id", "qty", "attribute_value_ids", "note"})
            product_id = check_int(line.get("product_id"), minimum=1)
            qty = check_int(line.get("qty"), minimum=1)
            require(qty <= MAX_LINE_QTY, "limit_exceeded")
            values = line.get("attribute_value_ids") or []
            require(isinstance(values, list) and len(values) <= MAX_ATTRIBUTE_VALUES)
            values = sorted({check_int(value, minimum=1) for value in values})
            result.append(
                {
                    "product_id": product_id,
                    "qty": qty,
                    "attribute_value_ids": values,
                    "note": clean_note(line.get("note")),
                }
            )
        return result

    @api.model
    def _efn_qr_check_products(self, config, lines):
        """Produk ∈ menu QR + ``self_order_available``; atribut ⊆ PTAV ``no_variant`` template, tanpa ``is_custom``,
        satu nilai per atribut non-multi (wajib)."""
        Product = self.env["product.product"]
        ids = sorted({line["product_id"] for line in lines})
        products = Product.browse(ids).exists().filtered_domain(self._efn_qr_product_domain(config))
        products = products.filtered(lambda p: p.active and p.self_order_available)
        unavailable = sorted(set(ids) - set(products.ids))
        if unavailable:
            _logger.info("EFN QR: produk tidak tersedia %s (config %s)", unavailable, config.id)
            raise qr_error("item_unavailable", unavailable)
        PTAV = self.env["product.template.attribute.value"]
        for line in lines:
            product = Product.browse(line["product_id"])
            values = PTAV.browse(line["attribute_value_ids"]).exists()
            require(len(values) == len(line["attribute_value_ids"]))
            per_attribute = {}
            for value in values:
                require(
                    value.product_tmpl_id == product.product_tmpl_id
                    and value.attribute_id.create_variant == "no_variant"
                    and value.ptav_active
                    and not value.is_custom
                )
                per_attribute.setdefault(value.attribute_id, []).append(value)
            for attr_line in product.product_tmpl_id.attribute_line_ids:
                attribute = attr_line.attribute_id
                if attribute.create_variant != "no_variant":
                    continue
                chosen = per_attribute.get(attribute, [])
                if attribute.display_type == MULTI_DISPLAY:
                    continue
                selectable = attr_line.product_template_value_ids.filtered(lambda v: v.ptav_active and not v.is_custom)
                if selectable:
                    require(len(chosen) == 1)
                else:
                    require(not chosen)
        return products

    @api.model
    def _efn_qr_attributes_text(self, ptavs):
        """Teks modifier baris (pola ``efn_online_order``): ``Atribut: nilai[, nilai]`` per atribut, dipisah ``; ``.
        Kosong -> ``None`` (BFF: ``attributes_text`` nullable)."""
        groups = {}
        for value in ptavs.sorted(lambda v: (v.attribute_line_id.sequence, v.attribute_line_id.id, v.id)):
            groups.setdefault(value.attribute_id, []).append(value.name or "")
        text = "; ".join(f"{attribute.name}: {', '.join(names)}" for attribute, names in groups.items())[:500]
        return text or None

    @api.model
    def _efn_qr_replay(self, existing, sha):
        if existing.payload_sha256 != sha:
            raise qr_error("idempotency_conflict")
        result = dict(existing.result or {})
        # K1 r15: hasil yang disimpan sebelum r15 belum memuat order_uuid.
        result.setdefault("order_uuid", existing.order_uuid or None)
        return result

    def _efn_order_uuid(self):
        """K1 r15: uuid ``pos.order`` yang dibuat untuk kiriman ini (stabil: tetap sama walau order kemudian digabung
        ke order lain/dihapus); ``None`` bila kiriman belum punya order."""
        self.ensure_one()
        return self.order_uuid or None

    @api.model
    def efn_qr_submit(self, config_id, table_session_ref, submission, order_uuid=None):
        """#14: satu kiriman = satu ``pos.order`` draft baru di meja (L-70). Lihat docstring modul."""
        config = self._efn_qr_check_caller(config_id)
        self = self.with_env(config.env)  # noqa: PLW0642 - sudo + context bersih setelah cek pemanggil
        check_str(table_session_ref, UUID_RE, 36)
        require(isinstance(submission, dict) and set(submission) <= {"idempotency_key", "submission_ref", "lines"})
        key = check_str(submission.get("idempotency_key"), IDEMPOTENCY_KEY_RE, 128)
        submission_ref = check_str(submission.get("submission_ref"), UUID_RE, 36).lower()
        if order_uuid is not None:
            check_str(order_uuid, ORDER_UUID_RE, 64)
        lines = self._efn_qr_parse_lines(submission.get("lines"))
        sha = canonical_sha256({"table_session_ref": table_session_ref.lower(), "lines": lines})
        existing = self.search([("config_id", "=", config.id), ("idempotency_key", "=", key)], limit=1)
        if existing:
            return self._efn_qr_replay(existing, sha)
        if self.search_count([("submission_ref", "=", submission_ref)], limit=1):
            raise qr_error("idempotency_conflict")
        if not config.efn_qr_enabled or config._efn_qr_config_problem() or not config._efn_qr_company_code():
            raise qr_error("qr_disabled")
        pos_session = config._efn_qr_open_pos_session()
        if not pos_session:
            raise qr_error("pos_session_closed")
        table_session = self.env["efn.pos.table.session"].search(
            [("bff_session_id", "=", table_session_ref.lower()), ("config_id", "=", config.id)], limit=1
        )
        if not table_session or not table_session._efn_is_live():
            raise qr_error("session_closed")
        if order_uuid is not None and order_uuid != table_session.order_uuid:
            raise qr_error("session_closed")
        table_session._efn_lock()
        # Batas per sesi meja (BFF menegakkan yang sama lebih dulu; ini batas keras server).
        if len(table_session.submission_ids) >= config.efn_qr_max_submissions:
            raise qr_error("limit_exceeded")
        item_count = sum(line["qty"] for line in lines)
        if sum(table_session.submission_ids.mapped("item_count")) + item_count > config.efn_qr_max_items:
            raise qr_error("limit_exceeded")
        products = self._efn_qr_check_products(config, lines)
        initial = "accepted" if config.efn_qr_confirm_mode == "auto" else "pending_confirmation"
        record = self.create(
            {
                "company_id": config.company_id.id,
                "config_id": config.id,
                "table_session_id": table_session.id,
                "submission_ref": submission_ref,
                "idempotency_key": key,
                "payload_sha256": sha,
                "item_count": item_count,
                "initial_state": initial,
                "last_status": initial,
            }
        )
        order = record._efn_qr_create_order(config, pos_session, table_session, lines, products)
        session_total = sum(
            table_session.order_ids.filtered(lambda o: o.state != "cancel" and o.efn_qr_order_ref).mapped(
                "amount_total"
            )
        )
        if config.currency_id.compare_amounts(session_total, config.efn_qr_max_amount) > 0:
            raise qr_error("limit_exceeded")
        result = {
            "submission_ref": submission_ref,
            "state": initial,
            "order_uuid": order.uuid,  # K1 r15 (SEC-164c): BFF mengenali order QR untuk earn_pending reverse
            # L-78: PTAV urut naik + teks atribut, agar BFF tidak kehilangan modifier saat mengganti tampilan kiriman.
            "lines": [
                {
                    "product_id": line.product_id.id,
                    "name": line.product_id.with_context(display_default_code=False).display_name,
                    "qty": int(line.qty),
                    "attribute_value_ids": sorted(line.attribute_value_ids.ids),
                    "attributes_text": self._efn_qr_attributes_text(line.attribute_value_ids),
                }
                for line in order.lines
            ],
            "amount_indicative_excl": config.currency_id.round(order.amount_total - order.amount_tax),
        }
        record.write(
            {
                "order_id": order.id,
                "order_uuid": order.uuid,
                "line_uuids": order.lines.mapped("uuid"),
                "result": result,
            }
        )
        config._efn_kds_cashier_send(
            "EFN_QR_ORDER",
            {"order_id": order.id, "table_id": order.table_id.id, "auto_send": config.efn_qr_confirm_mode == "auto"},
        )
        _logger.info(
            "EFN_QR_SUBMIT session=%s order=%s lines=%s mode=%s",
            table_session.id,
            order.id,
            len(order.lines),
            config.efn_qr_confirm_mode,
        )
        return dict(result)

    def _efn_qr_create_order(self, config, pos_session, table_session, lines, products):
        """Bangun payload order DI SERVER (bukan payload klien) -> ``sync_from_ui`` -> ``recompute_prices``.

        Tanpa partner, tanpa ``last_order_preparation_change`` (tidak ada tiket dapur dari server, L-71), harga 0
        lalu dihitung ulang ``pos_self_order.recompute_prices`` (pricelist + atribut + pajak FP preset)."""
        self.ensure_one()
        preset, fiscal_position, pricelist = config._efn_qr_pricing()
        PTAV = self.env["product.template.attribute.value"]
        line_commands = []
        for line in lines:
            product = products.browse(line["product_id"])
            values = PTAV.browse(line["attribute_value_ids"])
            line_commands.append(
                [
                    0,
                    0,
                    {
                        "uuid": str(uuid.uuid4()),
                        "product_id": product.id,
                        "qty": line["qty"],
                        "price_unit": 0.0,
                        "price_subtotal": 0.0,
                        "price_subtotal_incl": 0.0,
                        "discount": 0.0,
                        "tax_ids": [[6, 0, []]],
                        "attribute_value_ids": [[6, 0, values.ids]],
                        "full_product_name": product.with_context(display_default_code=False).display_name,
                        "note": note_json(line["note"]),
                        "efn_qr_submission_ref": self.submission_ref,
                    },
                ]
            )
        order_vals = {
            "uuid": str(uuid.uuid4()),
            "session_id": pos_session.id,
            "company_id": config.company_id.id,
            "user_id": pos_session.user_id.id,
            "table_id": table_session.table_id.id,
            "preset_id": preset.id,
            "fiscal_position_id": fiscal_position.id or False,
            "pricelist_id": pricelist.id,
            "partner_id": False,
            "state": "draft",
            "amount_tax": 0.0,
            "amount_total": 0.0,
            "amount_paid": 0.0,
            "amount_return": 0.0,
            "lines": line_commands,
            "payment_ids": [],
        }
        PosOrder = self.env["pos.order"].with_company(config.company_id)
        with internal_scope(self.env, QR_INTERNAL_SCOPE, {("submission", self.submission_ref)}):
            synced = PosOrder.sync_from_ui([order_vals])
            order = PosOrder.browse(synced["pos.order"][0]["id"])
            order.recompute_prices()
            order.write(
                {
                    "efn_qr_order_ref": self.submission_ref,
                    "efn_qr_table_session_id": table_session.id,
                }
            )
        return order

    # ================================================================== status
    @api.model
    def efn_qr_status(self, config_id, submission_refs):
        """#15 (baca): ``[{submission_ref, state, seq, order_uuid}]``; ref asing/tidak ada -> ``absent`` (order_uuid
        ``None``). K1 r15: ``order_uuid`` = uuid order yang dibuat kiriman (``_efn_order_uuid``).

        ``config_id=0, submission_refs=[]`` = probe API key (L-76) -> ``[]``; probe dengan ref -> ``invalid_input``."""
        config = self._efn_qr_check_caller(config_id, allow_probe=True)
        require(isinstance(submission_refs, list) and len(submission_refs) <= MAX_STATUS_REFS)
        if config is None:
            require(not submission_refs)
            return []
        self = self.with_env(config.env)  # noqa: PLW0642 - sudo + context bersih setelah cek pemanggil
        refs = []
        for ref in submission_refs:
            refs.append(check_str(ref, UUID_RE, 36).lower())
        found = {
            record.submission_ref: record
            for record in self.search([("config_id", "=", config.id), ("submission_ref", "in", refs)])
        }
        result = []
        for ref in refs:
            record = found.get(ref)
            if not record:
                result.append({"submission_ref": ref, "state": "absent", "seq": 0, "order_uuid": None})
            else:
                result.append(
                    {
                        "submission_ref": ref,
                        "state": record.last_status,
                        "seq": record.last_seq,
                        "order_uuid": record._efn_order_uuid(),
                    }
                )
        return result

    def _efn_qr_lines(self):
        self.ensure_one()
        return self.env["pos.order.line"].sudo().search([("efn_qr_submission_ref", "=", self.submission_ref)])

    def _efn_derive_status(self):
        """Status kiriman dari baris order (termasuk setelah digabung) + tiket KDS (§14.A.3).

        Semua tiket ``new`` = accepted; ada ``in_progress``/campuran = preparing; semua ``ready|served`` = ready; semua
        ``served`` = served. Belum dikirim ke dapur = pending_confirmation (mode pelayan) / accepted (mode auto).
        Order dihapus/batal sebelum dikirim ke dapur = rejected (pelayan menolak); sesudahnya = cancelled."""
        self.ensure_one()
        lines = self._efn_qr_lines()
        live = lines.filtered(lambda line: line.order_id.state != "cancel")
        sent = self.sent_to_kitchen
        uuids = set(self.line_uuids or []) | set(live.mapped("uuid"))
        if live:
            for order in live.order_id:
                if set(live.filtered(lambda line, o=order: line.order_id == o).mapped("uuid")) & prep_line_uuids(
                    order.last_order_preparation_change
                ):
                    sent = True
        tickets = self.env["efn.kds.ticket"]
        if uuids:
            ticket_lines = (
                self.env["efn.kds.ticket.line"]
                .sudo()
                .search([("order_line_uuid", "in", sorted(uuids)), ("qty_delta", ">", 0)])
            )
            tickets = ticket_lines.ticket_id.filtered(lambda t: t.state != "cancelled")
            if ticket_lines:
                sent = True
        if sent and not self.sent_to_kitchen:
            self.sudo().write({"sent_to_kitchen": True})
        if not live:
            return "cancelled" if sent else "rejected"
        if tickets:
            states = set(tickets.mapped("state"))
            if states == {"served"}:
                return "served"
            if states <= {"ready", "served"}:
                return "ready"
            if states == {"new"}:
                return "accepted"
            return "preparing"
        if sent:
            return "accepted"
        return "accepted" if self.config_id.efn_qr_confirm_mode == "auto" else "pending_confirmation"

    def _efn_refresh_status(self):
        """Hitung ulang status; maju monoton (kecuali final rejected/cancelled) -> event ``submission_status``."""
        if in_scope(self.env, QR_INTERNAL_SCOPE):
            return
        Outbox = self.env["efn.pos.qr.event.outbox"].sudo()
        for record in self.sudo():
            current = record.last_status
            if current in TERMINAL_STATES:
                continue
            new = record._efn_derive_status()
            if new == current:
                continue
            if new not in TERMINAL_STATES and STATE_RANK.get(new, 0) <= STATE_RANK.get(current, 0):
                continue
            outbox = Outbox._efn_enqueue_status(record, new)
            record.write({"last_status": new, "last_seq": outbox.seq})

    @api.model
    def _efn_refresh_for_orders(self, orders):
        """Kiriman yang menyentuh ``orders`` (order QR atau baris hasil gabung) -> refresh status."""
        orders = orders.sudo().exists()
        if not orders:
            return
        refs = set(orders.mapped("efn_qr_order_ref")) | set(orders.lines.mapped("efn_qr_submission_ref"))
        refs.discard(False)
        if refs:
            self.sudo().search([("submission_ref", "in", sorted(refs))])._efn_refresh_status()

    # ================================================================== cron
    @api.model
    def _cron_efn_qr_stale(self):
        """Order QR belum dikirim ke dapur > ``efn_qr_stale_minutes`` -> alert (sekali) + notifikasi kasir."""
        now = fields.Datetime.now()
        candidates = self.sudo().search(
            [
                ("stale_alerted", "=", False),
                ("sent_to_kitchen", "=", False),
                ("last_status", "in", ("pending_confirmation", "accepted")),
                ("order_id", "!=", False),
            ],
            limit=500,
        )
        for record in candidates:
            config = record.config_id
            if record.create_date > fields.Datetime.subtract(now, minutes=config.efn_qr_stale_minutes):
                continue
            record._efn_derive_status()
            if record.sent_to_kitchen or record.order_id.state != "draft":
                continue
            event = {
                "event": "EFN_QR_ORDER_STALE",
                "config_id": config.id,
                "company_id": record.company_id.id,
                "order_id": record.order_id.id,
                "table_session_id": record.table_session_id.id,
                "minutes": config.efn_qr_stale_minutes,
            }
            _alert_logger.warning("EFN_QR_ORDER_STALE %s", json.dumps(event, sort_keys=True))
            config._efn_kds_cashier_send(
                "EFN_QR_STALE", {"order_id": record.order_id.id, "table_id": record.order_id.table_id.id}
            )
            record.write({"stale_alerted": True})

    @api.model
    def _cron_efn_qr_note_retention(self):
        """L-76: catatan baris QR (teks tamu) dikosongkan setelah 90 hari, tanpa mengubah total/pajak.

        SQL langsung (bukan ORM write) agar tidak memicu recompute/guard order POS yang sudah dibayar; hanya kolom
        ``note`` baris yang membawa ``efn_qr_submission_ref``."""
        limit = fields.Datetime.subtract(fields.Datetime.now(), days=NOTE_RETENTION_DAYS)
        self.env["pos.order.line"].flush_model(["note", "efn_qr_submission_ref"])
        self.env.cr.execute(
            """
            UPDATE pos_order_line SET note = NULL
             WHERE efn_qr_submission_ref IS NOT NULL AND note IS NOT NULL AND create_date < %s
            """,
            [limit],
        )
        cleared = self.env.cr.rowcount
        if cleared:
            self.env["pos.order.line"].invalidate_model(["note"])
            _logger.info("EFN QR: retensi catatan mengosongkan %s baris > %s hari", cleared, NOTE_RETENTION_DAYS)
        return cleared
