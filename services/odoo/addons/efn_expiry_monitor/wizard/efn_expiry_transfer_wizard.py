"""Usulan transfer antar outlet dari alert near-expiry (fit-gap S-14, Q-SM7 -> fase 1 satu langkah A -> B).

Outlet tujuan diusulkan = outlet lain (non-DC, PT yang sama) dengan laju jual rata-rata harian tertinggi untuk produk
itu, dan harus lebih tinggi dari outlet asal:
- sumber 1: ``efn.forecast.daily`` run ``daily`` aktif (``efn_demand_forecast`` v2), rata-rata ``yhat`` 7 hari ke
  depan, bila ada barisnya;
- sumber 2 (fallback): move selesai dari lokasi internal outlet ke lokasi ``customer`` 28 hari terakhir / 28.
Outlet tujuan wajib lolos ``_efn_assortment_allowed`` (``dsd_only`` dianggap boleh: produk memang dijual di sana).

Hasil = picking internal **draft** outlet A/Stock -> outlet B/Stock (picking type internal outlet A) dengan
``restrict_lot_id`` = lot alert bila field ada di 19.0; staf meninjau lalu mengonfirmasi. Picking draft tidak
memicu penegakan efn_assortment (sumber bukan DC) — cek assortment dilakukan wizard ini.
"""

from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

FORECAST_DAYS = 7
HISTORY_DAYS = 28
ALLOWED_ASSORTMENT = ("ok", "dsd_only")


class EfnExpiryTransferWizard(models.TransientModel):
    _name = "efn.expiry.transfer.wizard"
    _description = "EFN wizard usulan transfer antar outlet (near-expiry)"

    alert_id = fields.Many2one("efn.expiry.alert", required=True, readonly=True)
    company_id = fields.Many2one(related="alert_id.company_id")
    source_warehouse_id = fields.Many2one(related="alert_id.warehouse_id", string="Outlet asal")
    product_id = fields.Many2one(related="alert_id.product_id")
    lot_id = fields.Many2one(related="alert_id.lot_id")
    qty = fields.Float(
        string="Qty dipindah", digits="Product Unit", compute="_compute_efn_suggestion", store=True, readonly=False
    )
    dest_warehouse_id = fields.Many2one(
        "stock.warehouse",
        string="Outlet tujuan",
        compute="_compute_efn_suggestion",
        store=True,
        readonly=False,
        domain="[('company_id', '=', company_id), ('id', '!=', source_warehouse_id), ('efn_is_dc', '=', False)]",
    )
    candidate_info = fields.Text(
        string="Perbandingan laju jual", compute="_compute_efn_suggestion", store=True, readonly=True
    )

    @api.depends("alert_id")
    def _compute_efn_suggestion(self):
        for wiz in self:
            alert = wiz.alert_id
            if not alert:
                wiz.qty, wiz.dest_warehouse_id, wiz.candidate_info = 0.0, False, False
                continue
            best, info = wiz._efn_suggest(alert)
            wiz.qty = alert.qty
            wiz.dest_warehouse_id = best
            wiz.candidate_info = info

    # ------------------------------------------------------------ laju jual
    @api.model
    def _efn_sales_rate(self, warehouse, product):
        """(laju harian, sumber) — forecast v2 aktif bila ada, selain itu histori 28 hari."""
        today = fields.Date.context_today(self)
        if "efn.forecast.daily" in self.env:
            rows = (
                self.env["efn.forecast.daily"]
                .sudo()
                .search(
                    [
                        ("warehouse_id", "=", warehouse.id),
                        ("product_id", "=", product.id),
                        ("run_id.is_active", "=", True),
                        ("run_id.kind", "=", "daily"),
                        ("date", ">=", today),
                        ("date", "<", today + timedelta(days=FORECAST_DAYS)),
                    ]
                )
            )
            if rows:
                return sum(rows.mapped("yhat")) / len(rows), "forecast"
        since = fields.Datetime.now() - timedelta(days=HISTORY_DAYS)
        moves = (
            self.env["stock.move"]
            .sudo()
            .search(
                [
                    ("product_id", "=", product.id),
                    ("state", "=", "done"),
                    ("date", ">=", since),
                    ("location_id", "child_of", warehouse.view_location_id.id),
                    ("location_id.usage", "=", "internal"),
                    ("location_dest_id.usage", "=", "customer"),
                ]
            )
        )
        return sum(moves.mapped("product_qty")) / HISTORY_DAYS, "history"

    @api.model
    def _efn_suggest(self, alert):
        """(outlet terbaik atau kosong, teks perbandingan)."""
        product = alert.product_id
        source = alert.warehouse_id
        src_rate, src_kind = self._efn_sales_rate(source, product)
        lines = [_("%(wh)s (asal): %(rate).2f/hari [%(kind)s]", wh=source.code, rate=src_rate, kind=src_kind)]
        best, best_rate = self.env["stock.warehouse"], src_rate
        outlets = (
            self.env["stock.warehouse"]
            .sudo()
            .search([("company_id", "=", alert.company_id.id), ("id", "!=", source.id), ("efn_is_dc", "=", False)])
        )
        for outlet in outlets:
            _ok, reason = outlet._efn_assortment_allowed(product)
            rate, kind = self._efn_sales_rate(outlet, product)
            if reason not in ALLOWED_ASSORTMENT:
                lines.append(_("%(wh)s: ditolak assortment (%(code)s)", wh=outlet.code, code=reason))
                continue
            lines.append(_("%(wh)s: %(rate).2f/hari [%(kind)s]", wh=outlet.code, rate=rate, kind=kind))
            if rate > best_rate:
                best, best_rate = outlet, rate
        return best, "\n".join(lines)

    # ------------------------------------------------------------ aksi
    def action_confirm(self):
        """Buat picking internal draft A -> B. Cek: akses tulis alert + company + tahap + qty + PT sama + non-DC +
        assortment outlet tujuan. Picking dibuat dengan hak user (ACL stock.picking)."""
        self.ensure_one()
        alert = self.alert_id
        alert.check_access("write")
        if alert.company_id not in self.env.user.company_ids:
            raise AccessError(_("efn_expiry:company — alert PT lain."))
        if alert.state == "closed" or alert.stage == "expired":
            raise UserError(_("efn_expiry:transfer_stage — lot kedaluwarsa/alert tertutup tidak dipindahkan."))
        dest = self.dest_warehouse_id
        if not dest:
            raise UserError(
                _(
                    "efn_expiry:transfer_dest — tidak ada outlet dengan laju jual lebih tinggi; pilih "
                    "outlet tujuan manual atau lakukan markdown."
                )
            )
        if dest == alert.warehouse_id or dest.company_id != alert.company_id or dest.efn_is_dc:
            raise UserError(_("efn_expiry:transfer_dest — tujuan harus outlet lain di PT yang sama."))
        if not (0 < self.qty <= alert.qty + 1e-6):
            raise UserError(_("efn_expiry:transfer_qty — qty harus > 0 dan <= qty alert (%s).", alert.qty))
        _ok, reason = dest._efn_assortment_allowed(alert.product_id)
        if reason not in ALLOWED_ASSORTMENT:
            raise UserError(
                _(
                    "efn_expiry:transfer_assortment — %(product)s tidak diizinkan di %(wh)s (%(code)s).",
                    product=alert.product_id.display_name,
                    wh=dest.code,
                    code=reason,
                )
            )
        source = alert.warehouse_id
        move_vals = {
            "product_id": alert.product_id.id,
            "company_id": alert.company_id.id,
            "product_uom_qty": self.qty,
            "location_id": source.lot_stock_id.id,
            "location_dest_id": dest.lot_stock_id.id,
        }
        if "restrict_lot_id" in self.env["stock.move"]._fields:
            move_vals["restrict_lot_id"] = alert.lot_id.id
        picking = (
            self.env["stock.picking"]
            .with_company(alert.company_id)
            .create(
                {
                    "picking_type_id": source.int_type_id.id,
                    "location_id": source.lot_stock_id.id,
                    "location_dest_id": dest.lot_stock_id.id,
                    "company_id": alert.company_id.id,
                    "origin": _("EFN near-expiry %(lot)s", lot=alert.lot_id.name),
                    "move_ids": [(0, 0, move_vals)],
                }
            )
        )
        alert.sudo().write({"state": "actioned", "transfer_picking_id": picking.id})
        return {
            "type": "ir.actions.act_window",
            "res_model": "stock.picking",
            "res_id": picking.id,
            "view_mode": "form",
            "target": "current",
        }
