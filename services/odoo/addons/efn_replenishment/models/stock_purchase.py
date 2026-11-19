"""Orderpoint terkelola (``trigger=manual``), qty orderpoint DC -> RFQ CE, SoD PO, hook run forecast (docs/04 §28.4)."""

import logging
import math
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

from .efn_replenishment import (
    DEFAULT_TZ,
    GROUP_ADMIN,
    GROUP_STORE,
    local_today,
    mean_sigma,
    round_pack,
    sum_series,
)

_logger = logging.getLogger(__name__)
DEFAULT_SUPPLIER_REVIEW_DAYS = 7


class StockWarehouseOrderpoint(models.Model):
    _inherit = "stock.warehouse.orderpoint"

    efn_replenishment_managed = fields.Boolean(
        string="Dikelola efn_replenishment",
        readonly=True,
        copy=False,
        help="Orderpoint ini diisi usulan efn_replenishment dan dipaksa trigger manual (cegah dobel order dengan "
        "scheduler CE, fit-gap G45 §6).",
    )
    efn_last_system_qty = fields.Float(string="Qty sistem terakhir (EFN)", readonly=True, copy=False)
    efn_last_run_id = fields.Many2one("efn.forecast.run", readonly=True, copy=False, ondelete="set null")

    @api.model
    def _efn_managed_orderpoint(self, warehouse, product, route=None):
        """Orderpoint (gudang, produk) di lokasi stok utama; dibuat bila belum ada; dipaksa ``trigger=manual``."""
        company = warehouse.company_id
        op = self.sudo().search(
            [
                ("product_id", "=", product.id),
                ("location_id", "=", warehouse.lot_stock_id.id),
                ("company_id", "=", company.id),
            ],
            limit=1,
        )
        vals = {"trigger": "manual", "efn_replenishment_managed": True}
        if route:
            vals["route_id"] = route.id
        if op:
            op.write(vals)
            return op
        return (
            self.sudo()
            .with_company(company)
            .create(
                dict(
                    vals,
                    product_id=product.id,
                    warehouse_id=warehouse.id,
                    location_id=warehouse.lot_stock_id.id,
                    company_id=company.id,
                    product_min_qty=0.0,
                    product_max_qty=0.0,
                )
            )
        )

    @api.model
    def _efn_compute_dc_orderpoints(self, run, now=None):
        """Keluaran 3 (ADR-046 §3.4): tulis ``qty_to_order_manual`` orderpoint DC; buyer menekan "Order" CE -> RFQ
        draft per vendor, konfirmasi RFQ = approval buyer. Tanpa model usulan PO sendiri (fit-gap S-13).

        need = Σ yhat_DC[awal, order supplier berikutnya-setelah-hari-order + lead) + z·σ̄·√L − virtual_available DC
        (on hand + PO terbuka − keluar terjadwal, termasuk usulan outlet yang sudah disetujui); awal = maks(tanggal
        proyeksi pertama run, hari ini) (QA-2); hari order = hari ini + ``efn_replenishment.order_day_offset``
        (default 1, QA-3) — bukan hari kunjungan supplier -> dilewati; tanpa jadwal: cakupan 7 hari + lead dari awal;
        cap umur simpan dari tiba (hari order + lead); dibulatkan ``replenishment_uom_id``."""
        now = now or fields.Datetime.now()
        Proposal = self.env["efn.replenishment.proposal"].sudo()
        Bias = self.env["efn.replenishment.bias.log"].sudo()
        today = local_today(DEFAULT_TZ, now)
        # QA-2: jendela mulai di tanggal proyeksi pertama run;
        # QA-3: hari order supplier = hari ini + offset (default 1).
        start = Proposal._efn_window_start(run, today)
        order_day = today + timedelta(days=Proposal._efn_order_day_offset())
        z = Proposal._efn_z()
        count = 0
        dcs = (
            self.env["stock.warehouse"]
            .sudo()
            .search([("company_id", "=", run.company_id.id), ("efn_is_dc", "=", True)])
        )
        for dc in dcs:
            for product, series in Proposal._efn_series(run, dc).items():
                seller = product.seller_ids.filtered(
                    lambda s, c=dc.company_id: not s.company_id or s.company_id == c
                ).sorted("sequence")[:1]
                schedule = self.env["efn.supplier.schedule"].sudo()
                if seller:
                    schedule = schedule.search(
                        [("partner_id", "=", seller.partner_id.id), ("warehouse_id", "=", dc.id)], limit=1
                    )
                if schedule and not schedule._efn_runs_on(order_day):
                    continue
                lead = (schedule.lead_days if schedule and schedule.lead_days else 0) or int(seller.delay or 0)
                next_order = schedule._efn_next_day_after(order_day) if schedule else None
                if next_order:
                    coverage_end = next_order + timedelta(days=lead)
                else:
                    coverage_end = start + timedelta(days=DEFAULT_SUPPLIER_REVIEW_DAYS + lead)
                demand = sum_series(series, start, coverage_end)
                protection = max((coverage_end - start).days, 1)
                sigma = mean_sigma(series, start, coverage_end)
                virtual = product.with_context(warehouse_id=dc.id).virtual_available
                need = max(demand + z * sigma * math.sqrt(protection) - virtual, 0.0)
                arrival = order_day + timedelta(days=lead)
                shelf = Proposal._efn_shelf_cap_new_lot(product, arrival, series)
                qty = min(need, shelf) if shelf is not None else need
                op = self._efn_managed_orderpoint(dc, product)
                qty = round_pack(qty, Proposal._efn_pack_qty(product, dc), shelf)
                op.write({"qty_to_order_manual": qty, "efn_last_system_qty": qty, "efn_last_run_id": run.id})
                Bias.create(
                    {
                        "company_id": dc.company_id.id,
                        "kind": "dc_system",
                        "orderpoint_id": op.id,
                        "run_id": run.id,
                        "warehouse_id": dc.id,
                        "product_id": product.id,
                        "qty_system": qty,
                        "qty_before": 0.0,
                        "qty_after": qty,
                    }
                )
                count += 1
        return count


class EfnReplenishmentProposalDc(models.Model):
    _inherit = "efn.replenishment.proposal"

    @api.model
    def _efn_shelf_cap_new_lot(self, product, arrival, series):
        """Cap umur simpan untuk lot baru dari supplier: Σ yhat[tiba, tiba + expiration_time − sisa umur minimum)."""
        tmpl = product.product_tmpl_id
        if "use_expiration_date" not in tmpl._fields or not tmpl.use_expiration_date or not tmpl.expiration_time:
            return None
        sell_until = arrival + timedelta(days=tmpl.expiration_time - self._efn_min_remaining_days(product))
        if sell_until <= arrival:
            return 0.0
        return sum_series(series, arrival, sell_until)


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    def _efn_managed_lines(self):
        return self.order_line.filtered(lambda line: line.orderpoint_id.efn_replenishment_managed)

    def _efn_check_sod(self):
        """SoD (docs/04 §28.4): kepala toko tidak boleh mengonfirmasi/menyetujui PO dari orderpoint terkelola."""
        managed = self._efn_managed_lines()
        if managed and self.env.user.has_group(GROUP_STORE) and not self.env.su:
            raise AccessError(_("efn_replenishment:sod — kepala toko tidak boleh mengonfirmasi PO supplier."))
        return managed

    def button_approve(self, *args, **kwargs):
        """SEC-213: jalur double validation PO CE juga tunduk SoD."""
        self._efn_check_sod()
        return super().button_approve(*args, **kwargs)

    def button_confirm(self):
        """SoD + log bias qty sistem vs qty RFQ dikonfirmasi (append-only)."""
        managed = self._efn_check_sod()
        result = super().button_confirm()
        Bias = self.env["efn.replenishment.bias.log"].sudo()
        for line in managed:
            op = line.orderpoint_id
            Bias.create(
                {
                    "company_id": line.order_id.company_id.id,
                    "kind": "dc_confirmed",
                    "orderpoint_id": op.id,
                    "purchase_line_id": line.id,
                    "run_id": op.efn_last_run_id.id,
                    "warehouse_id": op.warehouse_id.id,
                    "product_id": line.product_id.id,
                    "qty_system": op.efn_last_system_qty,
                    "qty_before": op.efn_last_system_qty,
                    # product_uom_qty = qty dalam UoM produk (compute CE purchase).
                    "qty_after": line.product_uom_qty,
                    "user_id": self.env.uid,
                }
            )
        return result


class EfnForecastRun(models.Model):
    _inherit = "efn.forecast.run"

    efn_proposal_pending = fields.Boolean(string="Usulan replenishment menunggu", readonly=True, copy=False)

    def _efn_on_run_closed_ok(self):
        """Tandai antrean + picu cron (perhitungan usulan TIDAK di request RPC, docs/04 §28.4)."""
        result = super()._efn_on_run_closed_ok()
        self._efn_notify_empty_runs()
        daily = self.filtered(lambda run: run.kind == "daily")
        if daily:
            daily.sudo().write({"efn_proposal_pending": True})
            cron = self.env.ref("efn_replenishment.ir_cron_efn_replenishment_compute", raise_if_not_found=False)
            if cron:
                cron.sudo()._trigger()
        return result

    def _efn_notify_empty_runs(self):
        """Run ``ok`` tanpa satu pun baris diterima (mis. semua kode gudang/produk ditolak) -> aktivitas To-Do untuk
        Replenishment administrator PT itu. Tanpa log ERROR (bukan galat sistem)."""
        group = self.env.ref(GROUP_ADMIN, raise_if_not_found=False)
        for run in self.sudo():
            if sum(run.chunk_ids.mapped("accepted")):
                continue
            users = (
                self.env["res.users"]
                .sudo()
                .search(
                    [
                        ("group_ids", "in", group.ids if group else []),
                        ("share", "=", False),
                        ("company_ids", "in", run.company_id.ids),
                    ]
                )
            )
            if not users:
                _logger.warning("EFN replenishment: run %s ok tanpa baris diterima; tidak ada administrator", run.id)
                continue
            for user in users:
                run.activity_schedule(
                    "mail.mail_activity_data_todo",
                    summary=_("Run forecast %s selesai tanpa baris diterima", run.run_id),
                    note=_("Periksa kode gudang/produk worker dan daftar baris ditolak (efn_forecast)."),
                    user_id=user.id,
                )
