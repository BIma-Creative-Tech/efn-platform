"""Usulan replenishment outlet -> DC dan qty orderpoint DC -> RFQ CE (G44, docs/04 §28.4, ADR-046 §3.4-§3.5,
ADR-045 §3.3, fit-gap G45 S-12/S-13/S-16, L-147/L-149/L-151).

Rumus keluaran 2 (usulan order harian outlet, per produk):
    need       = Σ yhat[awal, kirim berikutnya-setelah-ini + review) + z·σ̄·√L − on_hand − in_transit   (≥ 0)
    cap_shelf  = Σ yhat[tanggal kirim, ED lot DC terdekat − sisa umur minimum)      (hanya produk use_expiration_date)
    cap_rack   = shelf_capacity_qty (efn.assortment.line) − on_hand − in_transit     (bila diisi)
    qty        = round_pack(min(need, cap_shelf, cap_rack))
- ``round_pack``: naik ke kelipatan ``replenishment_uom_id`` orderpoint outlet; bila pembulatan naik melewati cap
  (shelf/rack) -> turun ke kelipatan (cap umur simpan/rak tidak pernah dilanggar). Kode alasan: ``shelf_cap``,
  ``rack_cap``, ``pack_round``; produk yang gagal ``_efn_assortment_allowed`` dicatat sebagai baris dikecualikan
  ``assortment:<kode>`` dengan qty 0.
- Stok LIVE Odoo: ``qty_available`` + ``incoming_qty`` gudang outlet (konteks ``warehouse_id``) saat job berjalan.
- Tanpa run ``daily`` berstatus ok yang ditutup HARI INI -> tidak ada usulan baru (reorder rule berlaku, L-147).

Approval kepala toko sebelum ``cutoff_at``; lewat cut-off -> ``expired`` (tidak dikirim otomatis, L-149/Q-SM23).
Approval -> ``_efn_create_supply_document()``: orderpoint outlet (``trigger=manual``, rute resupply CE dari DC)
``qty_to_order_manual`` + ``action_replenish`` CE. Satu titik ini yang diganti bila DC pindah ke PT terpisah (ADR-045
§3.3 opsi B).
"""

import logging
import math
from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import float_compare

_logger = logging.getLogger(__name__)

ENABLED_PARAM = "efn_replenishment.enabled"
Z_PARAM = "efn_replenishment.safety_z"
DEFAULT_Z = 1.65
# SEC-210: batas atas qty_approved = maks(qty_proposed × faktor, cap rak bila ada); melewati = grup administrator.
MAX_APPROVE_FACTOR_PARAM = "efn_replenishment.max_approve_factor"
DEFAULT_MAX_APPROVE_FACTOR = 3.0
# QA-3: hari order supplier = hari kalkulasi + offset (default 1: kalkulasi malam untuk order besok).
ORDER_DAY_OFFSET_PARAM = "efn_replenishment.order_day_offset"
DEFAULT_ORDER_DAY_OFFSET = 1
DEFAULT_TZ = "Asia/Jakarta"
GROUP_STORE = "efn_replenishment.group_replenishment_store"
GROUP_BUYER = "efn_replenishment.group_replenishment_buyer"
GROUP_ADMIN = "efn_replenishment.group_replenishment_admin"
WEEKDAY_LABELS = ("Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min")
CHANGE_REASONS = [
    ("local_promo", "Promo lokal"),
    ("event", "Event"),
    ("rack_full", "Rak penuh"),
    ("other", "Lainnya"),
]
LINE_USER_FIELDS = frozenset({"qty_approved", "change_reason"})
PROPOSAL_USER_FIELDS = frozenset({"line_ids", "note"})
EPS = 1e-6


def _tz_list(self):
    return [(tz, tz) for tz in pytz.common_timezones]


def _zone(name):
    try:
        return pytz.timezone(name or DEFAULT_TZ)
    except pytz.UnknownTimeZoneError:
        return pytz.timezone(DEFAULT_TZ)


def local_today(tz_name, now=None):
    now = now or fields.Datetime.now()
    return pytz.utc.localize(now).astimezone(_zone(tz_name)).date()


def sum_series(series, start, end):
    """Σ yhat untuk tanggal di [start, end); tanggal tanpa proyeksi diisi rata-rata seri (ekstrapolasi datar)."""
    if end <= start:
        return 0.0
    if not series:
        return 0.0
    mean = sum(v[0] for v in series.values()) / len(series)
    total, day = 0.0, start
    while day < end:
        total += series[day][0] if day in series else mean
        day += timedelta(days=1)
    return total


def mean_sigma(series, start, end):
    values = [v[1] for d, v in series.items() if start <= d < end]
    return sum(values) / len(values) if values else 0.0


def weekday_mask_ok(mask):
    return isinstance(mask, int) and 1 <= mask <= 127


class EfnScheduleMixin(models.AbstractModel):
    _name = "efn.replenishment.schedule.mixin"
    _description = "EFN jadwal mingguan (bitmask Sen..Min, cut-off jam lokal)"

    weekdays = fields.Integer(
        string="Hari (bitmask)",
        default=31,
        help="Bitmask Sen=1, Sel=2, Rab=4, Kam=8, Jum=16, Sab=32, Min=64 (31 = Sen-Jum, 127 = setiap hari).",
    )
    weekday_label = fields.Char(compute="_compute_weekday_label", string="Hari")
    cutoff_time = fields.Float(string="Jam cut-off", default=10.0, help="Jam lokal (zona waktu di bawah), 0-23.99.")
    tz = fields.Selection(_tz_list, string="Zona waktu", default=DEFAULT_TZ, required=True)

    @api.depends("weekdays")
    def _compute_weekday_label(self):
        for rec in self:
            rec.weekday_label = ", ".join(
                label for i, label in enumerate(WEEKDAY_LABELS) if (rec.weekdays or 0) & (1 << i)
            )

    def _efn_runs_on(self, day):
        self.ensure_one()
        return bool((self.weekdays or 0) & (1 << day.weekday()))

    def _efn_next_day_after(self, day, max_days=14):
        self.ensure_one()
        for offset in range(1, max_days + 1):
            candidate = day + timedelta(days=offset)
            if self._efn_runs_on(candidate):
                return candidate
        return None

    def _efn_local_dt_to_utc(self, day, hour_float):
        hours = int(hour_float)
        minutes = min(int(round((hour_float - hours) * 60)), 59)
        local = _zone(self.tz).localize(datetime.combine(day, time(hours, minutes)))
        return local.astimezone(pytz.utc).replace(tzinfo=None)


class EfnDeliverySchedule(models.Model):
    _name = "efn.delivery.schedule"
    _inherit = ["efn.replenishment.schedule.mixin"]
    _description = "EFN jadwal kirim DC -> outlet per kelompok produk"
    _order = "warehouse_id, product_group, id"
    _check_company_auto = True

    name = fields.Char(compute="_compute_name", store=True)
    company_id = fields.Many2one("res.company", required=True, index=True, default=lambda self: self.env.company)
    warehouse_id = fields.Many2one("stock.warehouse", string="Outlet", required=True, check_company=True)
    supply_warehouse_id = fields.Many2one("stock.warehouse", string="DC", required=True, check_company=True)
    product_group = fields.Many2one("product.category", string="Kelompok produk", help="Kosong = semua kategori.")
    lead_days = fields.Integer(string="Lead time (hari)", default=1, help="Hari order (cut-off) = tanggal kirim − lead.")
    review_days = fields.Integer(default=0, help="Tambahan hari cakupan setelah kiriman berikutnya.")
    active = fields.Boolean(default=True)

    @api.depends("warehouse_id", "supply_warehouse_id", "product_group")
    def _compute_name(self):
        for rec in self:
            rec.name = (
                f"{rec.supply_warehouse_id.code or '?'} -> {rec.warehouse_id.code or '?'}"
                f" / {rec.product_group.display_name or _('Semua')}"
            )

    @api.constrains("weekdays", "lead_days", "cutoff_time", "review_days", "warehouse_id", "supply_warehouse_id")
    def _check_efn_schedule(self):
        for rec in self:
            if not weekday_mask_ok(rec.weekdays):
                raise ValidationError(_("efn_replenishment:weekdays — pilih minimal satu hari (bitmask 1-127)."))
            if rec.lead_days < 0 or rec.lead_days > 30 or rec.review_days < 0 or rec.review_days > 60:
                raise ValidationError(_("efn_replenishment:days — lead 0-30, review 0-60 hari."))
            if not 0 <= rec.cutoff_time < 24:
                raise ValidationError(_("efn_replenishment:cutoff — jam cut-off 0-23.99."))
            if rec.warehouse_id == rec.supply_warehouse_id:
                raise ValidationError(_("efn_replenishment:same_wh — outlet dan DC harus berbeda."))

    def _efn_cutoff_at(self, delivery_date):
        """Cut-off (UTC naive) = jam cut-off lokal pada (tanggal kirim − lead)."""
        self.ensure_one()
        return self._efn_local_dt_to_utc(delivery_date - timedelta(days=self.lead_days), self.cutoff_time)

    def _efn_next_delivery(self, now=None):
        """Tanggal kirim terdekat yang cut-off-nya masih di depan ``now`` -> ``(date, cutoff_utc)``."""
        self.ensure_one()
        now = now or fields.Datetime.now()
        today = local_today(self.tz, now)
        for offset in range(0, 15):
            day = today + timedelta(days=offset)
            if not self._efn_runs_on(day):
                continue
            cutoff = self._efn_cutoff_at(day)
            if cutoff > now:
                return day, cutoff
        return None, None


class EfnSupplierSchedule(models.Model):
    _name = "efn.supplier.schedule"
    _inherit = ["efn.replenishment.schedule.mixin"]
    _description = "EFN jadwal kunjungan/order supplier"
    _order = "partner_id, warehouse_id, id"
    _check_company_auto = True

    company_id = fields.Many2one("res.company", required=True, index=True, default=lambda self: self.env.company)
    partner_id = fields.Many2one("res.partner", string="Supplier", required=True, check_company=True)
    warehouse_id = fields.Many2one(
        "stock.warehouse", string="Gudang (DC / outlet DSD)", required=True, check_company=True
    )
    lead_days = fields.Integer(
        string="Lead time (hari)", default=0, help="0 = pakai 'Delivery Lead Time' supplier (product.supplierinfo)."
    )
    active = fields.Boolean(default=True)

    @api.constrains("weekdays", "cutoff_time", "lead_days")
    def _check_efn_schedule(self):
        for rec in self:
            if not weekday_mask_ok(rec.weekdays):
                raise ValidationError(_("efn_replenishment:weekdays — pilih minimal satu hari (bitmask 1-127)."))
            if not 0 <= rec.cutoff_time < 24 or not 0 <= rec.lead_days <= 60:
                raise ValidationError(_("efn_replenishment:cutoff — jam cut-off 0-23.99, lead 0-60 hari."))


class EfnReplenishmentProposal(models.Model):
    _name = "efn.replenishment.proposal"
    _description = "EFN usulan order outlet -> DC"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "delivery_date desc, warehouse_id, id desc"
    _check_company_auto = True

    name = fields.Char(compute="_compute_name", store=True)
    company_id = fields.Many2one("res.company", required=True, index=True, readonly=True)
    warehouse_id = fields.Many2one("stock.warehouse", string="Outlet", required=True, readonly=True,
                                   check_company=True, index=True)
    supply_warehouse_id = fields.Many2one("stock.warehouse", string="DC", required=True, readonly=True,
                                          check_company=True)
    schedule_id = fields.Many2one("efn.delivery.schedule", required=True, readonly=True, ondelete="restrict")
    delivery_date = fields.Date(required=True, readonly=True, index=True)
    cutoff_at = fields.Datetime(string="Cut-off (UTC)", required=True, readonly=True, index=True)
    run_id = fields.Many2one("efn.forecast.run", string="Run forecast", readonly=True, ondelete="set null")
    state = fields.Selection(
        [
            ("draft", "Dihitung"),
            ("proposed", "Menunggu approval"),
            ("approved", "Disetujui"),
            ("rejected", "Ditolak"),
            ("expired", "Kedaluwarsa (lewat cut-off)"),
        ],
        default="draft",
        required=True,
        readonly=True,
        tracking=True,
        index=True,
    )
    approved_by = fields.Many2one("res.users", readonly=True)
    approved_at = fields.Datetime(readonly=True)
    picking_id = fields.Many2one("stock.picking", readonly=True)
    line_ids = fields.One2many("efn.replenishment.proposal.line", "proposal_id")
    note = fields.Text()

    _efn_proposal_uniq = models.Constraint(
        "UNIQUE(schedule_id, delivery_date)", "Satu usulan per jadwal kirim per tanggal kirim."
    )

    @api.depends("warehouse_id", "delivery_date", "schedule_id")
    def _compute_name(self):
        for rec in self:
            rec.name = f"{rec.warehouse_id.code or '?'} / {rec.delivery_date or ''} / {rec.schedule_id.name or ''}"

    # ------------------------------------------------------------ proteksi
    def write(self, vals):
        if not self.env.su and set(vals) - PROPOSAL_USER_FIELDS:
            raise AccessError(_("efn_replenishment:readonly — status usulan hanya berubah lewat tombol."))
        return super().write(vals)

    # --------------------------------------------------------------- aksi
    def _efn_check_store_head(self):
        self.ensure_one()
        user = self.env.user
        if not user.has_group(GROUP_STORE):
            raise AccessError(_("efn_replenishment:store_only — hanya kepala toko yang menyetujui usulan outlet."))
        self.check_access("read")
        if self.company_id not in user.company_ids or self.warehouse_id not in user.sudo().efn_outlet_warehouse_ids:
            raise AccessError(_("efn_replenishment:scope — outlet bukan cakupan Anda."))

    def _efn_past_cutoff(self):
        self.ensure_one()
        return fields.Datetime.now() >= self.cutoff_at

    def action_efn_approve(self):
        """Kepala toko (outlet cakupannya) menyetujui sebelum cut-off -> procurement CE lewat orderpoint."""
        expired = self.browse()
        for rec in self:
            rec._efn_check_store_head()
            if rec.state != "proposed":
                raise UserError(_("efn_replenishment:state — hanya usulan 'Menunggu approval' yang bisa disetujui."))
            if rec._efn_past_cutoff():
                rec.sudo().write({"state": "expired"})
                expired |= rec
                continue
            sudo_rec = rec.sudo()
            Bias = self.env["efn.replenishment.bias.log"].sudo()
            for line in sudo_rec.line_ids.filtered(lambda line: not line.excluded):
                Bias.create(line._efn_bias_vals("outlet_approved", line.qty_proposed, line.qty_approved))
            picking = sudo_rec.with_company(rec.company_id)._efn_create_supply_document()
            sudo_rec.write(
                {
                    "state": "approved",
                    "approved_by": self.env.uid,
                    "approved_at": fields.Datetime.now(),
                    "picking_id": picking.id if picking else False,
                }
            )
            sudo_rec.message_post(body=_("Usulan disetujui oleh %s.", self.env.user.name))
        if expired:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "type": "warning",
                    "sticky": True,
                    "message": _("Usulan sudah lewat cut-off dan ditandai kedaluwarsa (tidak dikirim)."),
                },
            }
        return True

    def action_efn_reject(self):
        for rec in self:
            rec._efn_check_store_head()
            if rec.state != "proposed":
                raise UserError(_("efn_replenishment:state — hanya usulan 'Menunggu approval' yang bisa ditolak."))
            rec.sudo().write({"state": "rejected"})
        return True

    def _efn_create_supply_document(self):
        """SATU titik pembuatan dokumen pasok setelah approval (ADR-045 §3.3).

        Jalur satu PT: orderpoint outlet ``trigger=manual`` + rute resupply CE dari DC
        (``stock.warehouse.resupply_route_ids``), ``qty_to_order_manual`` = qty disetujui, lalu ``action_replenish``
        CE (picking DC -> transit -> outlet). Bila DC kelak PT terpisah (opsi B), cukup ganti metode ini ke SO/PO.
        Mengembalikan picking keluar DC pertama (bisa kosong)."""
        self.ensure_one()
        outlet, dc, company = self.warehouse_id, self.supply_warehouse_id, self.company_id
        Orderpoint = self.env["stock.warehouse.orderpoint"].sudo().with_company(company)
        route = outlet.resupply_route_ids.filtered(lambda r: r.supplier_wh_id == dc)[:1]
        last_move = self.env["stock.move"].sudo().search([], order="id desc", limit=1).id or 0
        orderpoints = Orderpoint.browse()
        lines = self.line_ids.filtered(lambda line: not line.excluded and line.qty_approved > EPS)
        for line in lines:
            op = Orderpoint._efn_managed_orderpoint(outlet, line.product_id, route)
            op.write({"qty_to_order_manual": line.qty_approved})
            orderpoints |= op
        if orderpoints:
            orderpoints.action_replenish()
        moves = self.env["stock.move"].sudo().search(
            [
                ("id", ">", last_move),
                ("product_id", "in", lines.product_id.ids),
                ("location_id.warehouse_id", "=", dc.id),
            ],
            order="id",
        )
        return moves.picking_id[:1]

    # ---------------------------------------------------------------- cron
    @api.model
    def _cron_efn_expire(self):
        """Usulan yang belum disetujui saat cut-off -> expired (tidak dikirim otomatis, L-149)."""
        due = self.sudo().search([("state", "in", ("draft", "proposed")), ("cutoff_at", "<=", fields.Datetime.now())])
        if due:
            due.write({"state": "expired"})
        return len(due)

    @api.model
    def _cron_efn_compute_pending(self):
        """Antrean: run daily yang baru ditutup ok -> usulan outlet + qty orderpoint DC (bukan di request RPC)."""
        Run = self.env["efn.forecast.run"].sudo()
        enabled = self._efn_enabled()
        done = 0
        for run in Run.search([("efn_proposal_pending", "=", True)], order="id"):
            run.efn_proposal_pending = False
            if not enabled:
                _logger.info("EFN replenishment: dinonaktifkan (efn_replenishment.enabled) -> run %s dilewati", run.id)
                continue
            if run.state != "ok" or not run.is_active or run.kind != "daily":
                continue
            if not run.closed_at or local_today(DEFAULT_TZ, run.closed_at) != local_today(DEFAULT_TZ):
                continue  # L-147: hanya run yang ditutup hari ini
            self.sudo().with_company(run.company_id)._efn_compute_for_run(run)
            done += 1
        return done

    @api.model
    def _efn_enabled(self):
        value = (self.env["ir.config_parameter"].sudo().get_param(ENABLED_PARAM) or "false").strip().lower()
        return value in ("true", "1")

    @api.model
    def _efn_z(self):
        try:
            z = float(self.env["ir.config_parameter"].sudo().get_param(Z_PARAM) or DEFAULT_Z)
        except (TypeError, ValueError):
            return DEFAULT_Z
        return z if 0 <= z <= 4 else DEFAULT_Z

    # -------------------------------------------------------------- hitung
    @api.model
    def _efn_compute_for_run(self, run, now=None):
        """Usulan semua jadwal kirim aktif company run + qty orderpoint DC. Kembalikan usulan yang dibuat/diganti."""
        now = now or fields.Datetime.now()
        proposals = self.browse()
        schedules = self.env["efn.delivery.schedule"].sudo().search([("company_id", "=", run.company_id.id)])
        for schedule in schedules:
            proposal = self._efn_compute_schedule(schedule, run, now)
            if proposal:
                proposals |= proposal
        self.env["stock.warehouse.orderpoint"].sudo()._efn_compute_dc_orderpoints(run, now)
        return proposals

    @api.model
    def _efn_window_start(self, run, today):
        """QA-2: maks(tanggal proyeksi pertama run, hari ini)."""
        first = self.env["efn.forecast.daily"].sudo().search([("run_id", "=", run.id)], order="date asc", limit=1).date
        return max(first, today) if first else today

    @api.model
    def _efn_order_day_offset(self):
        try:
            offset = int(
                self.env["ir.config_parameter"].sudo().get_param(ORDER_DAY_OFFSET_PARAM) or DEFAULT_ORDER_DAY_OFFSET
            )
        except (TypeError, ValueError):
            return DEFAULT_ORDER_DAY_OFFSET
        return offset if 0 <= offset <= 7 else DEFAULT_ORDER_DAY_OFFSET

    @api.model
    def _efn_series(self, run, warehouse, product_group=None):
        domain = [("run_id", "=", run.id), ("warehouse_id", "=", warehouse.id)]
        if product_group:
            domain.append(("product_id.categ_id", "child_of", product_group.id))
        series = {}
        for row in self.env["efn.forecast.daily"].sudo().search(domain):
            series.setdefault(row.product_id, {})[row.date] = (row.yhat, row.sigma)
        return series

    @api.model
    def _efn_compute_schedule(self, schedule, run, now):
        delivery_date, cutoff = schedule._efn_next_delivery(now)
        if not delivery_date:
            return self.browse()
        Proposal = self.sudo()
        proposal = Proposal.search(
            [("schedule_id", "=", schedule.id), ("delivery_date", "=", delivery_date)], limit=1
        )
        if proposal and proposal.state not in ("draft", "proposed"):
            return self.browse()
        vals = {
            "company_id": schedule.company_id.id,
            "warehouse_id": schedule.warehouse_id.id,
            "supply_warehouse_id": schedule.supply_warehouse_id.id,
            "schedule_id": schedule.id,
            "delivery_date": delivery_date,
            "cutoff_at": cutoff,
            "run_id": run.id,
            "state": "draft",
        }
        if proposal:
            proposal.line_ids.sudo().with_context(efn_replenishment_recompute=True).unlink()
            proposal.write(vals)
        else:
            proposal = Proposal.create(vals)
        # QA-2: jendela kebutuhan mulai di tanggal proyeksi pertama run (run malam berisi besok dst.; hari ini sudah
        # tercermin di stok live), tidak lebih awal dari hari ini.
        start = self._efn_window_start(run, local_today(schedule.tz, now))
        following = schedule._efn_next_day_after(delivery_date) or delivery_date + timedelta(days=7)
        horizon_end = following + timedelta(days=schedule.review_days)
        line_vals = []
        for product, series in self._efn_series(run, schedule.warehouse_id, schedule.product_group).items():
            vals_line = proposal._efn_line_values(product, series, start, horizon_end)
            if vals_line:
                line_vals.append(vals_line)
        if not line_vals:
            proposal.unlink()
            return self.browse()
        self.env["efn.replenishment.proposal.line"].sudo().create(line_vals)
        proposal.write({"state": "proposed"})
        return proposal

    def _efn_line_values(self, product, series, today, horizon_end):
        """Nilai baris usulan (lihat rumus di docstring modul) atau ``None`` bila tidak perlu order."""
        self.ensure_one()
        outlet, dc = self.warehouse_id, self.supply_warehouse_id
        Line = self.env["efn.assortment.line"]
        stock = product.with_context(warehouse_id=outlet.id)
        on_hand, in_transit = stock.qty_available, stock.incoming_qty
        base = {
            "proposal_id": self.id,
            "product_id": product.id,
            "on_hand": on_hand,
            "in_transit": in_transit,
        }
        ok, reason = Line._efn_assortment_allowed(product, outlet)
        if not ok:
            return dict(base, excluded=True, reason_codes=f"assortment:{reason}", qty_proposed=0.0, qty_approved=0.0)
        demand = sum_series(series, today, horizon_end)
        protection = max((horizon_end - today).days, 1)
        sigma = mean_sigma(series, today, horizon_end)
        need = max(demand + self._efn_z() * sigma * math.sqrt(protection) - on_hand - in_transit, 0.0)
        if need <= EPS:
            return None
        reasons, qty = [], need
        shelf = self._efn_shelf_cap(product, dc, self.delivery_date, series)
        if shelf is not None and shelf < qty - EPS:
            qty = shelf
            reasons.append("shelf_cap")
        rack = None
        capacity = Line._efn_allow_line(product, outlet).shelf_capacity_qty
        if capacity and capacity > 0:
            rack = max(capacity - on_hand - in_transit, 0.0)
            if rack < qty - EPS:
                qty = rack
                reasons.append("rack_cap")
        hard_caps = [cap for cap in (shelf, rack) if cap is not None]
        rounded = round_pack(qty, self._efn_pack_qty(product, outlet), min(hard_caps) if hard_caps else None)
        if abs(rounded - qty) > EPS:
            reasons.append("pack_round")
        return dict(
            base,
            forecast_qty=demand,
            qty_need=need,
            qty_shelf_cap=shelf if shelf is not None else 0.0,
            qty_rack_cap=rack if rack is not None else 0.0,
            qty_proposed=rounded,
            qty_approved=rounded,
            reason_codes=",".join(reasons),
        )

    @api.model
    def _efn_min_remaining_days(self, product):
        """Sisa umur minimum saat terima (hari). Field kategori ``efn_min_receipt_days`` (efn_expiry_monitor, G45)
        dipakai bila ada; selain itu 0 (fit-gap Q-SM9 belum dijawab)."""
        categ = product.categ_id
        if "efn_min_receipt_days" in categ._fields:
            return max(int(categ.efn_min_receipt_days or 0), 0)
        return 0

    @api.model
    def _efn_shelf_cap(self, product, supply_warehouse, start_date, series):
        """Perkiraan terjual sebelum (ED lot terdekat di gudang pemasok − sisa umur minimum) (L-151).

        ``None`` = produk tanpa tanggal kedaluwarsa (tidak dibatasi). Tanpa lot ber-ED di gudang pemasok -> pakai
        ``expiration_time`` produk dari ``start_date`` (lot baru)."""
        tmpl = product.product_tmpl_id
        if "use_expiration_date" not in tmpl._fields or not tmpl.use_expiration_date:
            return None
        quants = self.env["stock.quant"].sudo().search(
            [
                ("product_id", "=", product.id),
                ("location_id", "child_of", supply_warehouse.view_location_id.id),
                ("location_id.usage", "=", "internal"),
                ("quantity", ">", 0),
                ("lot_id.expiration_date", "!=", False),
            ]
        )
        if quants:
            nearest = min(quants.mapped("lot_id.expiration_date"))
            expiry = local_today(DEFAULT_TZ, nearest)
        elif tmpl.expiration_time and tmpl.expiration_time > 0:
            expiry = start_date + timedelta(days=tmpl.expiration_time)
        else:
            return None
        sell_until = expiry - timedelta(days=self._efn_min_remaining_days(product))
        if sell_until <= start_date:
            return 0.0
        return sum_series(series, start_date, sell_until)

    @api.model
    def _efn_pack_qty(self, product, warehouse):
        """Kelipatan pack (UoM produk) dari ``replenishment_uom_id`` orderpoint gudang (fit-gap: ``qty_multiple``
        tidak ada di 19.0). Tanpa itu: 1 untuk satuan Unit, rounding UoM untuk lainnya."""
        op = self.env["stock.warehouse.orderpoint"].sudo().search(
            [
                ("product_id", "=", product.id),
                ("location_id", "=", warehouse.lot_stock_id.id),
                ("company_id", "=", warehouse.company_id.id),
            ],
            limit=1,
        )
        uom = op.replenishment_uom_id if op else self.env["uom.uom"]
        if uom and uom != product.uom_id:
            pack = uom._compute_quantity(1.0, product.uom_id, round=False)
            if pack > EPS:
                return pack
        unit = self.env.ref("uom.product_uom_unit", raise_if_not_found=False)
        return 1.0 if product.uom_id == unit else (product.uom_id.rounding or 1.0)


def round_pack(qty, pack, cap=None):
    """Naik ke kelipatan ``pack``; bila melewati ``cap`` -> turun ke kelipatan (cap tidak pernah dilanggar)."""
    if qty <= EPS:
        return 0.0
    pack = pack if pack and pack > EPS else 1.0
    up = math.ceil(qty / pack - EPS) * pack
    if cap is not None and up > cap + EPS:
        return max(math.floor(qty / pack + EPS) * pack, 0.0)
    return up


class EfnReplenishmentProposalLine(models.Model):
    _name = "efn.replenishment.proposal.line"
    _description = "EFN baris usulan order outlet"
    _order = "excluded, product_id"
    _check_company_auto = True

    proposal_id = fields.Many2one(
        "efn.replenishment.proposal", required=True, ondelete="cascade", index=True, readonly=True
    )
    company_id = fields.Many2one(related="proposal_id.company_id", store=True, index=True)
    warehouse_id = fields.Many2one(related="proposal_id.warehouse_id", store=True, index=True)
    state = fields.Selection(related="proposal_id.state")
    product_id = fields.Many2one("product.product", required=True, readonly=True)
    product_uom_id = fields.Many2one(related="product_id.uom_id")
    forecast_qty = fields.Float(string="Forecast cakupan", readonly=True, digits="Product Unit")
    on_hand = fields.Float(readonly=True, digits="Product Unit")
    in_transit = fields.Float(readonly=True, digits="Product Unit")
    qty_need = fields.Float(string="Kebutuhan bersih", readonly=True, digits="Product Unit")
    qty_shelf_cap = fields.Float(string="Cap umur simpan", readonly=True, digits="Product Unit")
    qty_rack_cap = fields.Float(string="Cap rak", readonly=True, digits="Product Unit")
    qty_proposed = fields.Float(string="Qty usulan", readonly=True, digits="Product Unit")
    qty_approved = fields.Float(string="Qty disetujui", digits="Product Unit")
    reason_codes = fields.Char(string="Kode alasan", readonly=True)
    excluded = fields.Boolean(string="Dikecualikan (assortment)", readonly=True)
    change_reason = fields.Selection(CHANGE_REASONS, string="Alasan perubahan")
    changed_by = fields.Many2one("res.users", readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(_("efn_replenishment:readonly — baris usulan dibuat sistem."))
        return super().create(vals_list)

    def unlink(self):
        if not self.env.su:
            raise AccessError(_("efn_replenishment:readonly — baris usulan tidak bisa dihapus."))
        return super().unlink()

    def write(self, vals):
        if self.env.su:
            return super().write(vals)
        if set(vals) - LINE_USER_FIELDS:
            raise AccessError(_("efn_replenishment:readonly — hanya qty disetujui & alasan yang bisa diubah."))
        Bias = self.env["efn.replenishment.bias.log"].sudo()
        for line in self:
            proposal = line.proposal_id
            proposal._efn_check_store_head()
            if proposal.state != "proposed" or proposal._efn_past_cutoff():
                raise UserError(_("efn_replenishment:locked — usulan tidak lagi bisa diubah (status / cut-off)."))
            if line.excluded:
                raise UserError(_("efn_replenishment:excluded — produk ini tidak lolos assortment."))
            new_qty = vals.get("qty_approved", line.qty_approved)
            if (
                not isinstance(new_qty, (int, float))
                or isinstance(new_qty, bool)
                or not math.isfinite(new_qty)
                or new_qty < 0
            ):
                raise UserError(_("efn_replenishment:qty — qty harus angka >= 0."))
            if new_qty > line._efn_max_approvable() + EPS and not self.env.user.has_group(GROUP_ADMIN):
                raise UserError(
                    _(
                        "efn_replenishment:qty_limit — qty melebihi batas %(limit)s (usulan × faktor / kapasitas rak); "
                        "butuh Replenishment administrator.",
                        limit=line._efn_max_approvable(),
                    )
                )
            reason = vals.get("change_reason", line.change_reason)
            changed = float_compare(new_qty, line.qty_proposed, precision_digits=6) != 0
            if changed and not reason:
                raise UserError(_("efn_replenishment:reason — pilih alasan perubahan qty."))
            before = line.qty_approved
            super(EfnReplenishmentProposalLine, line.sudo()).write(dict(vals, changed_by=self.env.uid))
            if float_compare(before, new_qty, precision_digits=6) != 0:
                Bias.create(line._efn_bias_vals("outlet_change", before, new_qty, reason))
        return True

    def _efn_max_approvable(self):
        """SEC-210: maks(qty_proposed × ``efn_replenishment.max_approve_factor`` (default 3), cap rak bila ada)."""
        self.ensure_one()
        try:
            factor = float(
                self.env["ir.config_parameter"].sudo().get_param(MAX_APPROVE_FACTOR_PARAM)
                or DEFAULT_MAX_APPROVE_FACTOR
            )
        except (TypeError, ValueError):
            factor = DEFAULT_MAX_APPROVE_FACTOR
        if not math.isfinite(factor) or factor < 1:
            factor = DEFAULT_MAX_APPROVE_FACTOR
        return max(self.qty_proposed * factor, self.qty_rack_cap if self.qty_rack_cap > 0 else 0.0)

    def _efn_bias_vals(self, kind, before, after, reason=None):
        self.ensure_one()
        return {
            "company_id": self.company_id.id,
            "kind": kind,
            "proposal_id": self.proposal_id.id,
            "proposal_line_id": self.id,
            "warehouse_id": self.warehouse_id.id,
            "product_id": self.product_id.id,
            "qty_system": self.qty_proposed,
            "qty_before": before,
            "qty_after": after,
            "change_reason": reason or self.change_reason or False,
            "user_id": self.env.uid,
            "run_id": self.proposal_id.run_id.id,
        }


class EfnReplenishmentBiasLog(models.Model):
    """Log bias append-only (ADR-046 §3.5): qty sistem vs qty disetujui/dikonfirmasi; ETL -> ``fact_replenishment``."""

    _name = "efn.replenishment.bias.log"
    _description = "EFN log bias replenishment (append-only)"
    _order = "id desc"
    _check_company_auto = True

    company_id = fields.Many2one("res.company", required=True, index=True, readonly=True)
    kind = fields.Selection(
        [
            ("outlet_change", "Perubahan qty kepala toko"),
            ("outlet_approved", "Usulan outlet disetujui"),
            ("dc_system", "Qty sistem orderpoint DC"),
            ("dc_confirmed", "RFQ DC dikonfirmasi"),
        ],
        required=True,
        readonly=True,
        index=True,
    )
    proposal_id = fields.Many2one("efn.replenishment.proposal", readonly=True, ondelete="set null", index=True)
    proposal_line_id = fields.Many2one("efn.replenishment.proposal.line", readonly=True, ondelete="set null")
    orderpoint_id = fields.Many2one("stock.warehouse.orderpoint", readonly=True, ondelete="set null")
    purchase_line_id = fields.Many2one("purchase.order.line", readonly=True, ondelete="set null")
    run_id = fields.Many2one("efn.forecast.run", readonly=True, ondelete="set null")
    warehouse_id = fields.Many2one("stock.warehouse", readonly=True, index=True)
    product_id = fields.Many2one("product.product", readonly=True, index=True)
    qty_system = fields.Float(readonly=True, digits="Product Unit")
    qty_before = fields.Float(readonly=True, digits="Product Unit")
    qty_after = fields.Float(readonly=True, digits="Product Unit")
    change_reason = fields.Selection(CHANGE_REASONS, readonly=True)
    user_id = fields.Many2one("res.users", readonly=True)

    def write(self, vals):
        raise UserError(_("efn_replenishment:append_only — log bias tidak bisa diubah."))

    def unlink(self):
        raise UserError(_("efn_replenishment:append_only — log bias tidak bisa dihapus."))
