"""E-45: RPC lookup kode bayar di kasir + data frontend POS.

``efn_online_counter_lookup(code)`` (dipanggil ``[[config_id], code]``; terdaftar di ``RPC_ALLOWLIST``):
1. pemanggil = grup POS user + ``check_access('read')`` config + company config ∈ company user + sesi POS terbuka;
2. rate limit per sesi & per kasir (gagal beruntun, lihat ``efn.online.counter.lookup``);
3. kode dinormalisasi (huruf besar, tanpa spasi/strip) -> digest KDF per company -> pesanan ``counter`` milik company
   config DAN outlet pesanan = gudang config; masih ``placed`` dan belum kedaluwarsa;
4. sukses -> HANYA ``{"order_label", "amount_payable"}`` (L-50: tanpa data pelanggan); gagal ->
   ``{"error_code", "message"}`` (tanpa membedakan PT/outlet lain dari kode salah). Kode tidak di-log/disimpan.
Setiap panggilan dicatat (jejak + klaim untuk sync). Offline: tombol dinonaktifkan di frontend.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.efn_online_order.models.efn_online_common import PICKUP_CODE_RE, normalize_pickup_code

_logger = logging.getLogger(__name__)

MAX_CODE_INPUT = 32


class PosConfig(models.Model):
    _inherit = "pos.config"

    efn_online_counter_enabled = fields.Boolean(
        compute="_compute_efn_online_counter",
        help="E-45: tombol 'Bayar pesanan online' tampil (PT siap + ada kanal 'counter' dengan outlet di gudang ini).",
    )
    efn_online_counter_product_id = fields.Many2one(
        "product.product", compute="_compute_efn_online_counter", help="Produk pelunasan pesanan online PT ini."
    )

    def _efn_counter_warehouse(self):
        self.ensure_one()
        return self.sudo().picking_type_id.warehouse_id

    def _efn_counter_outlets(self):
        """Outlet online aktif milik kanal ``counter`` aktif di company config dengan gudang = gudang config."""
        self.ensure_one()
        warehouse = self._efn_counter_warehouse()
        if not warehouse:
            return self.env["efn.online.outlet"]
        outlets = (
            self.env["efn.online.outlet"]
            .sudo()
            .search(
                [
                    ("company_id", "=", self.company_id.id),
                    ("warehouse_id", "=", warehouse.id),
                    ("channel_id.active", "=", True),
                ]
            )
        )
        return outlets.filtered(lambda o: "counter" in o.channel_id._efn_online_payment_modes())

    def _compute_efn_online_counter(self):
        for config in self:
            company = config.company_id.sudo()
            ready = bool(company) and company._efn_online_counter_ready()
            config.efn_online_counter_product_id = company.efn_online_counter_product_id if ready else False
            config.efn_online_counter_enabled = ready and bool(config._efn_counter_outlets())

    @api.model
    def _load_pos_data_fields(self, config):
        fields_list = super()._load_pos_data_fields(config)
        if fields_list:
            extra = ["efn_online_counter_enabled", "efn_online_counter_product_id"]
            fields_list = [*fields_list, *[f for f in extra if f not in fields_list]]
        return fields_list

    def _get_special_products(self):
        """Produk pelunasan semua PT dimuat POS walau ``available_in_pos`` mati (POS menyaring per company) dan
        tidak tampil sebagai produk biasa."""
        products = self.env["res.company"].sudo().search([]).mapped("efn_online_counter_product_id")
        return super()._get_special_products() | products

    # ----------------------------------------------------------------- lookup
    def _efn_counter_check_caller(self):
        self.ensure_one()
        if not self.env.user.has_group("point_of_sale.group_pos_user"):
            raise AccessError(_("Anda tidak berhak memakai fitur ini."))
        try:
            self.check_access("read")
        except AccessError:
            raise AccessError(_("Anda tidak berhak memakai fitur ini.")) from None
        if self.company_id not in self.env.user.company_ids:
            raise AccessError(_("Anda tidak berhak memakai fitur ini."))
        session = self.current_session_id
        if not session or session.state != "opened":
            raise UserError(_("Sesi POS belum dibuka."))
        return session

    @api.model
    def _efn_counter_error(self, code):
        messages = {
            "invalid_format": _("Kode bayar terdiri dari 8 huruf/angka. Periksa kembali."),
            "not_found": _("Kode bayar tidak ditemukan untuk toko ini."),
            "not_payable": _("Pesanan ini tidak dapat dibayar (kedaluwarsa, dibatalkan, atau sudah dibayar)."),
            "rate_limited": _("Terlalu banyak kode salah. Tunggu beberapa menit lalu coba lagi."),
            "disabled": _("Bayar pesanan online tidak aktif di POS ini."),
        }
        return {"error_code": code, "message": messages.get(code, messages["not_found"])}

    def efn_online_counter_lookup(self, code):
        """Cari pesanan online bayar di kasir dari kode bayar (E-45, docs/04 §12.2 no. 5).

        :param str code: kode bayar dari aplikasi pelanggan (8 karakter alfabet §10.2; strip/spasi diabaikan).
        :returns: ``{"order_label", "amount_payable"}`` atau ``{"error_code", "message"}``.
        """
        session = self._efn_counter_check_caller()
        if not self.efn_online_counter_enabled:
            return self._efn_counter_error("disabled")
        Lookup = self.env["efn.online.counter.lookup"].sudo()
        base = {
            "company_id": self.company_id.id,
            "config_id": self.id,
            "session_id": session.id,
            "user_id": self.env.uid,
        }
        if Lookup._efn_rate_limited(session, self.env.user):
            Lookup.create({**base, "result": "rate_limited"})
            _logger.warning("EFN bayar di kasir: lookup dibatasi config %s user %s", self.id, self.env.uid)
            return self._efn_counter_error("rate_limited")
        normalized = normalize_pickup_code(code) if isinstance(code, str) and len(code) <= MAX_CODE_INPUT else ""
        if not PICKUP_CODE_RE.match(normalized):
            Lookup.create({**base, "result": "invalid_format"})
            return self._efn_counter_error("invalid_format")
        order = self.env["efn.online.order"]._efn_counter_find(self.company_id, normalized)
        outlet_ok = bool(order) and order.outlet_id.warehouse_id == self._efn_counter_warehouse()
        if not order or not outlet_ok or order.company_id != self.company_id:
            Lookup.create({**base, "result": "not_found"})
            _logger.info("EFN bayar di kasir: lookup tidak cocok config %s user %s", self.id, self.env.uid)
            return self._efn_counter_error("not_found")
        if not order._efn_counter_payable():
            Lookup.create({**base, "result": "not_payable", "online_order_id": order.id})
            return self._efn_counter_error("not_payable")
        Lookup.create({**base, "result": "ok", "online_order_id": order.id})
        _logger.info("EFN bayar di kasir: lookup order %s oleh user %s (config %s)", order.id, self.env.uid, self.id)
        return {"order_label": order.name, "amount_payable": order._efn_amount_out(order.amount_payable)}
