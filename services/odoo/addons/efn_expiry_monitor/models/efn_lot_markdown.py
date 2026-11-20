"""Markdown per lot × outlet (docs/04 §28.5a, fit-gap G45 §3 opsi D, Q-SM4, Q-SM12).

- ``price`` (harga jual satuan baru, basis sama dengan Harga Jual produk — ritel SMD: termasuk PPN) **XOR**
  ``discount_pct`` (0 < x < 100).
- Siklus: ``draft`` -> ``active`` (aksi Aktifkan, cek batas diskon per peran) -> ``expired`` (cron setelah
  ``date_to``) / ``cancelled``. Setelah draft, field harga/periode/lot/outlet/kuota terkunci (batalkan lalu buat baru).
- Batas diskon efektif per peran (parameter, default Q-SM4): kepala toko (outlet di
  ``res.users.efn_outlet_warehouse_ids``) <= 50 %, *Markdown Manager* <= 90 %. Diskon efektif untuk ``price`` =
  ``1 - price / harga jual produk``.
- ``qty_cap`` (0 = tanpa batas) dan ``qty_sold`` (diisi server saat sync order POS, baris dikunci).
- ``notify_members``: G45 hanya menyimpan + ``notify_state = pending``. Outbox ``efn.campaign.outbox`` dan
  pengiriman ke member (``promo.campaign.requested`` kind ``markdown``) dibangun **G46** (docs/04 §28.7).
- Kanal online: tidak berlaku di fase 1 (lot tidak dipilih saat pesanan online, Q-SM12).
- Teraudit ``efn_audit`` (aturan ``audit_rule_efn_lot_markdown``); transisi state ditulis via sudo setelah cek
  (log mencatat ``is_sudo`` + user pemicu).
"""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .common import (
    DEFAULT_MANAGER_MAX,
    DEFAULT_STORE_MAX,
    GROUP_MARKDOWN_MANAGER,
    GROUP_STOCK_USER,
    MANAGER_MAX_PARAM,
    STORE_MAX_PARAM,
    param_float,
)

# Field yang hanya ditulis kode server (sudo) setelah cek.
SERVER_FIELDS = frozenset({"state", "qty_sold", "activated_by", "activated_at", "notify_state"})
# Field yang terkunci setelah draft.
LOCKED_AFTER_DRAFT = frozenset(
    {"company_id", "warehouse_id", "lot_id", "price", "discount_pct", "date_from", "date_to", "qty_cap"}
)


class EfnLotMarkdown(models.Model):
    _name = "efn.lot.markdown"
    _description = "EFN markdown harga per lot x outlet"
    _inherit = ["mail.thread"]
    _order = "date_from desc, id desc"
    _check_company_auto = True

    company_id = fields.Many2one(
        "res.company", required=True, index=True, default=lambda self: self.env.company, tracking=True
    )
    warehouse_id = fields.Many2one(
        "stock.warehouse", string="Outlet", required=True, index=True, check_company=True, tracking=True
    )
    lot_id = fields.Many2one("stock.lot", string="Lot", required=True, index=True, check_company=True, tracking=True)
    product_id = fields.Many2one(related="lot_id.product_id", store=True, index=True, string="Produk")
    lot_expiration_date = fields.Datetime(related="lot_id.expiration_date", string="Kedaluwarsa lot")
    alert_id = fields.Many2one("efn.expiry.alert", string="Alert asal", index="btree_not_null", readonly=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    list_price = fields.Float(related="product_id.lst_price", string="Harga jual normal")
    price = fields.Monetary(string="Harga markdown (satuan)", tracking=True)
    discount_pct = fields.Float(string="Diskon (%)", digits=(5, 2), tracking=True)
    effective_discount_pct = fields.Float(
        string="Diskon efektif (%)", digits=(5, 2), compute="_compute_effective_discount_pct"
    )
    date_from = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    date_to = fields.Date(required=True, tracking=True)
    qty_cap = fields.Float(string="Kuota qty (0 = tanpa batas)", digits="Product Unit", tracking=True)
    qty_sold = fields.Float(string="Qty terjual (markdown)", digits="Product Unit", readonly=True)
    state = fields.Selection(
        [("draft", "Draft"), ("active", "Aktif"), ("expired", "Berakhir"), ("cancelled", "Dibatalkan")],
        required=True,
        default="draft",
        index=True,
        readonly=True,
        tracking=True,
    )
    notify_members = fields.Boolean(
        string="Kirim ke member",
        help="G45: hanya disimpan (status 'menunggu'). Pengiriman lewat outbox -> BFF dibangun G46 (docs/04 §28.7).",
    )
    notify_state = fields.Selection(
        [("none", "Tidak dikirim"), ("pending", "Menunggu outbox (G46)")],
        default="none",
        required=True,
        readonly=True,
    )
    activated_by = fields.Many2one("res.users", readonly=True, ondelete="restrict")
    activated_at = fields.Datetime(readonly=True)
    note = fields.Char(string="Catatan")

    # ------------------------------------------------------------ compute/constraint
    @api.depends("price", "discount_pct", "list_price")
    def _compute_effective_discount_pct(self):
        for rec in self:
            rec.effective_discount_pct = rec._efn_effective_discount()

    def _efn_effective_discount(self):
        self.ensure_one()
        if self.discount_pct:
            return self.discount_pct
        base = self.product_id.lst_price
        if self.price and base > 0:
            return max(0.0, (1.0 - self.price / base) * 100.0)
        return 0.0

    @api.constrains("price", "discount_pct", "date_from", "date_to", "qty_cap", "lot_id", "warehouse_id", "state")
    def _check_efn_markdown(self):
        for rec in self:
            has_price = bool(rec.price)
            has_pct = bool(rec.discount_pct)
            if has_price == has_pct:
                raise ValidationError(
                    _("efn_expiry:markdown_value — isi harga markdown ATAU diskon persen (tepat satu).")
                )
            if has_price and (rec.price < 0 or rec.price >= rec.product_id.lst_price):
                raise ValidationError(
                    _("efn_expiry:markdown_price — harga markdown harus > 0 dan di bawah harga jual normal.")
                )
            if has_pct and not (0 < rec.discount_pct < 100):
                raise ValidationError(_("efn_expiry:markdown_pct — diskon harus di antara 0 dan 100 persen."))
            if rec.date_to < rec.date_from:
                raise ValidationError(_("efn_expiry:markdown_dates — tanggal akhir sebelum tanggal mulai."))
            if rec.qty_cap < 0:
                raise ValidationError(_("efn_expiry:markdown_cap — kuota tidak boleh negatif."))
            if rec.lot_id.product_id.tracking != "lot":
                raise ValidationError(_("efn_expiry:markdown_lot — produk harus dilacak per lot."))
            if rec.warehouse_id.efn_is_dc:
                raise ValidationError(_("efn_expiry:markdown_outlet — markdown hanya untuk outlet, bukan DC."))
            if rec.state in ("draft", "active"):
                overlap = self.sudo().search_count(
                    [
                        ("id", "!=", rec.id),
                        ("lot_id", "=", rec.lot_id.id),
                        ("warehouse_id", "=", rec.warehouse_id.id),
                        ("state", "in", ("draft", "active")),
                        ("date_from", "<=", rec.date_to),
                        ("date_to", ">=", rec.date_from),
                    ],
                    limit=1,
                )
                if overlap:
                    raise ValidationError(
                        _(
                            "efn_expiry:markdown_overlap — sudah ada markdown draft/aktif untuk lot & outlet ini pada "
                            "periode yang sama."
                        )
                    )

    # ------------------------------------------------------------ guard
    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            for vals in vals_list:
                touched = set(vals) & SERVER_FIELDS
                if touched - {"state"} or vals.get("state", "draft") != "draft":
                    raise AccessError(_("efn_expiry:server_field — status/qty terjual hanya diisi sistem."))
        for vals in vals_list:
            if vals.get("notify_members"):
                vals["notify_state"] = "pending"
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            if set(vals) & SERVER_FIELDS:
                raise AccessError(_("efn_expiry:server_field — status/qty terjual hanya diisi sistem."))
            if set(vals) & LOCKED_AFTER_DRAFT and self.filtered(lambda r: r.state != "draft"):
                raise UserError(
                    _(
                        "efn_expiry:markdown_locked — markdown yang sudah aktif/berakhir tidak bisa diubah; batalkan "
                        "lalu buat markdown baru."
                    )
                )
        if vals.get("notify_members"):
            vals = dict(vals, notify_state="pending")
        return super().write(vals)

    def unlink(self):
        if not self.env.su and self.filtered(lambda r: r.state != "draft"):
            raise UserError(_("efn_expiry:markdown_unlink — hanya markdown draft yang bisa dihapus; batalkan saja."))
        return super().unlink()

    # ------------------------------------------------------------ peran
    def _efn_role_limit(self, user=None):
        """Batas diskon (%) user untuk outlet markdown ini, atau None bila tidak berhak."""
        self.ensure_one()
        user = user or self.env.user
        if user.has_group(GROUP_MARKDOWN_MANAGER):
            return param_float(self.env, MANAGER_MAX_PARAM, DEFAULT_MANAGER_MAX, 0.0, 99.0)
        if user.has_group(GROUP_STOCK_USER) and self.warehouse_id in user.sudo().efn_outlet_warehouse_ids:
            return param_float(self.env, STORE_MAX_PARAM, DEFAULT_STORE_MAX, 0.0, 99.0)
        return None

    def _efn_check_actor(self):
        self.ensure_one()
        if self.env.su:
            return
        self.check_access("write")
        if self.company_id not in self.env.user.company_ids:
            raise AccessError(_("efn_expiry:company — markdown PT lain."))

    # ------------------------------------------------------------ aksi
    def action_efn_activate(self):
        """Draft -> aktif. Cek: akses tulis + company + peran (kepala toko outlet ini / Markdown Manager) + batas
        diskon peran + periode belum lewat."""
        today = fields.Date.context_today(self)
        for rec in self:
            rec._efn_check_actor()
            if rec.state != "draft":
                raise UserError(_("efn_expiry:markdown_state — hanya markdown draft yang bisa diaktifkan."))
            limit = rec._efn_role_limit()
            if limit is None:
                raise AccessError(
                    _(
                        "efn_expiry:markdown_role — hanya kepala toko outlet ini atau Markdown Manager yang bisa "
                        "mengaktifkan markdown."
                    )
                )
            pct = rec._efn_effective_discount()
            if pct > limit + 1e-6:
                raise UserError(
                    _(
                        "efn_expiry:discount_limit — diskon efektif %(pct).1f %% melebihi batas peran Anda "
                        "(%(limit).1f %%). Minta Markdown Manager mengaktifkan.",
                        pct=pct,
                        limit=limit,
                    )
                )
            if rec.date_to < today:
                raise UserError(_("efn_expiry:markdown_dates — periode markdown sudah lewat."))
            rec.sudo().write({"state": "active", "activated_by": self.env.uid, "activated_at": fields.Datetime.now()})
            if rec.alert_id and rec.alert_id.state == "open":
                rec.alert_id.sudo().write({"state": "actioned"})
        return True

    def action_efn_cancel(self):
        """Draft/aktif -> dibatalkan (akses tulis + company + peran yang sama dengan aktivasi)."""
        for rec in self:
            rec._efn_check_actor()
            if rec.state not in ("draft", "active"):
                raise UserError(_("efn_expiry:markdown_state — markdown ini sudah tidak aktif."))
            if rec._efn_role_limit() is None:
                raise AccessError(_("efn_expiry:markdown_role — hanya kepala toko outlet ini atau Markdown Manager."))
            rec.sudo().write({"state": "cancelled"})
        return True

    # ------------------------------------------------------------ server
    @api.model
    def _cron_efn_expire(self):
        today = fields.Date.context_today(self)
        stale = self.sudo().search([("state", "=", "active"), ("date_to", "<", today)])
        stale.write({"state": "expired"})
        return len(stale)

    @api.model
    def _efn_active_for(self, warehouse, day, product=None):
        domain = [
            ("warehouse_id", "=", warehouse.id),
            ("state", "=", "active"),
            ("date_from", "<=", day),
            ("date_to", ">=", day),
        ]
        if product:
            domain.append(("product_id", "=", product.id))
        return self.sudo().search(domain)

    def _efn_pos_dict(self):
        self.ensure_one()
        return {
            "id": self.id,
            "lot": self.lot_id.name,
            "product_id": self.product_id.id,
            "price": self.price or None,
            "discount_pct": self.discount_pct or None,
            "date_from": fields.Date.to_string(self.date_from),
            "date_to": fields.Date.to_string(self.date_to),
            "qty_cap": self.qty_cap,
            "qty_sold": self.qty_sold,
        }
