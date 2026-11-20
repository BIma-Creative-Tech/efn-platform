"""Alert near-expiry per lot × outlet (docs/04 §28.5a, fit-gap S-08).

Cron harian ``_cron_efn_expiry_scan`` (hanya bila ``efn_expiry_monitor.enabled``):

1. Stok internal ber-lot dengan ``expiration_date`` di gudang **non-DC**, untuk produk yang kategorinya punya
   ``efn_near_expiry_days`` / ``efn_markdown_days`` > 0. Tahap: ``expired`` (kedaluwarsa/removal date lewat),
   ``markdown`` (sisa hari <= ambang markdown), ``near`` (sisa hari <= ambang near-expiry).
2. Satu alert terbuka per (outlet, lot): run berikutnya memperbarui qty/sisa hari/tahap; tahap naik membuka ulang.
   Kunci unik (company, outlet, lot, ``date`` deteksi pertama) -> idempoten per lot × outlet × hari; menjalankan cron
   dua kali di hari yang sama tidak membuat alert/aktivitas baru.
3. Alert yang stoknya habis / keluar ambang -> ``closed``.
4. Notifikasi hanya untuk alert baru atau tahap naik: satu To-Do per outlet per kepala toko
   (``res.users.efn_outlet_warehouse_ids``) + satu ringkasan per PT ke buyer (grup Markdown Manager dan, bila
   terpasang, ``efn_replenishment.group_replenishment_buyer``). Aktivitas CE ``product_expiry`` untuk lot yang sama
   dicegah di ``stock.lot._alert_date_exceeded`` (lihat stock_lot.py).

Markdown aktif yang lewat ``date_to`` -> ``expired`` (cron yang sama).
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .common import STAGE_RANK, STAGES, days_left, is_enabled, lot_blocked

_logger = logging.getLogger(__name__)

BUYER_GROUP = "efn_replenishment.group_replenishment_buyer"


class EfnExpiryAlert(models.Model):
    _name = "efn.expiry.alert"
    _description = "EFN alert near-expiry per lot x outlet"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "days_left, warehouse_id, id"
    _check_company_auto = True

    company_id = fields.Many2one("res.company", required=True, index=True, readonly=True)
    warehouse_id = fields.Many2one(
        "stock.warehouse", string="Outlet", required=True, index=True, readonly=True, check_company=True
    )
    lot_id = fields.Many2one("stock.lot", required=True, index=True, readonly=True)
    product_id = fields.Many2one("product.product", required=True, index=True, readonly=True)
    categ_id = fields.Many2one(related="product_id.categ_id", store=True, string="Kategori")
    expiration_date = fields.Datetime(related="lot_id.expiration_date", string="Kedaluwarsa")
    qty = fields.Float(digits="Product Unit", readonly=True)
    days_left = fields.Integer(string="Sisa hari", readonly=True)
    stage = fields.Selection(STAGES, required=True, index=True, readonly=True, tracking=True)
    state = fields.Selection(
        [("open", "Terbuka"), ("actioned", "Ditindaklanjuti"), ("closed", "Ditutup")],
        required=True,
        default="open",
        index=True,
        readonly=True,
        tracking=True,
    )
    date = fields.Date(string="Terdeteksi", required=True, readonly=True, index=True)
    last_run_date = fields.Date(string="Diperbarui", readonly=True)
    markdown_ids = fields.One2many("efn.lot.markdown", "alert_id", string="Markdown")
    transfer_picking_id = fields.Many2one(
        "stock.picking", string="Transfer antar outlet", readonly=True, check_company=True
    )

    _efn_alert_uniq = models.Constraint(
        "UNIQUE(company_id, warehouse_id, lot_id, date)",
        "Alert lot ini untuk outlet & tanggal yang sama sudah ada.",
    )

    def write(self, vals):
        """SEC-219: field bisnis alert hanya ditulis cron/aksi resmi (via sudo); user hanya boleh menyentuh field
        chatter/aktivitas (mail.thread / mail.activity.mixin)."""
        if not self.env.su:
            business = sorted(name for name in vals if not name.startswith(("message_", "activity_")))
            if business:
                raise AccessError(
                    _(
                        "efn_expiry:server_field — field alert hanya diubah sistem atau tombol resmi (%s).",
                        ", ".join(business),
                    )
                )
        return super().write(vals)

    @api.depends("lot_id", "warehouse_id", "stage")
    def _compute_display_name(self):
        labels = dict(STAGES)
        for rec in self:
            rec.display_name = f"{rec.warehouse_id.code or '?'} / {rec.lot_id.name or '?'} ({labels.get(rec.stage)})"

    # ------------------------------------------------------------ cron
    @api.model
    def _efn_stage(self, categ, left, blocked):
        if blocked or (left is not None and left < 0):
            return "expired"
        if left is None:
            return False
        limits = categ._efn_expiry_thresholds()
        if limits["markdown"] and left <= limits["markdown"]:
            return "markdown"
        if limits["near"] and left <= limits["near"]:
            return "near"
        return False

    @api.model
    def _efn_scan_rows(self):
        """{(warehouse, lot): qty} stok internal gudang non-DC, kategori terkelola, lot bertanggal kedaluwarsa."""
        Quant = self.env["stock.quant"].sudo()
        groups = Quant._read_group(
            [
                ("lot_id", "!=", False),
                ("lot_id.expiration_date", "!=", False),
                ("quantity", ">", 0),
                ("location_id.usage", "=", "internal"),
                ("location_id.warehouse_id", "!=", False),
                ("location_id.warehouse_id.efn_is_dc", "=", False),
                ("product_id.categ_id", "in", self.env["product.category"]._efn_managed_categ_ids()),
            ],
            groupby=["location_id", "lot_id"],
            aggregates=["quantity:sum"],
        )
        rows = {}
        for location, lot, qty in groups:
            key = (location.warehouse_id, lot)
            rows[key] = rows.get(key, 0.0) + qty
        return rows

    @api.model
    def _cron_efn_expiry_scan(self):
        if not is_enabled(self.env):
            return 0
        self.env["efn.lot.markdown"]._cron_efn_expire()
        Alert = self.sudo()
        today = fields.Date.context_today(self)
        now = fields.Datetime.now()
        rows = self._efn_scan_rows()
        open_alerts = Alert.search([("state", "in", ("open", "actioned"))])
        by_key = {(a.warehouse_id, a.lot_id): a for a in open_alerts}
        seen = Alert.browse()
        notify = Alert.browse()
        for (warehouse, lot), qty in rows.items():
            categ = lot.product_id.categ_id
            left = days_left(self, lot, today)
            stage = self._efn_stage(categ, left, lot_blocked(lot, now))
            if not stage:
                continue
            alert = by_key.get((warehouse, lot))
            if alert:
                vals = {"qty": qty, "days_left": left or 0, "last_run_date": today}
                if STAGE_RANK[stage] > STAGE_RANK[alert.stage]:
                    vals.update(stage=stage, state="open")
                    notify |= alert
                alert.write(vals)
            else:
                existing_today = Alert.search(
                    [("warehouse_id", "=", warehouse.id), ("lot_id", "=", lot.id), ("date", "=", today)], limit=1
                )
                if existing_today:
                    # Ditutup manual hari ini -> tidak dibuat ulang (idempoten per hari).
                    seen |= existing_today
                    continue
                alert = Alert.create(
                    {
                        "company_id": warehouse.company_id.id,
                        "warehouse_id": warehouse.id,
                        "lot_id": lot.id,
                        "product_id": lot.product_id.id,
                        "qty": qty,
                        "days_left": left or 0,
                        "stage": stage,
                        "date": today,
                        "last_run_date": today,
                    }
                )
                notify |= alert
            seen |= alert
        (open_alerts - seen).write({"state": "closed", "last_run_date": today})
        self._efn_notify(notify, today)
        _logger.info("EFN expiry: %d alert aktif, %d baru/naik tahap", len(seen), len(notify))
        return len(notify)

    @api.model
    def _efn_count_label(self, alerts):
        counts = {code: len(alerts.filtered(lambda a, c=code: a.stage == c)) for code, _label in STAGES}
        return _(
            "near %(near)s, markdown %(markdown)s, kedaluwarsa %(expired)s",
            near=counts["near"],
            markdown=counts["markdown"],
            expired=counts["expired"],
        )

    @api.model
    def _efn_notify(self, alerts, today):
        if not alerts:
            return
        Users = self.env["res.users"].sudo()
        for warehouse in alerts.warehouse_id:
            rows = alerts.filtered(lambda a, w=warehouse: a.warehouse_id == w)
            heads = Users.search(
                [
                    ("efn_outlet_warehouse_ids", "in", warehouse.ids),
                    ("share", "=", False),
                    ("company_ids", "in", warehouse.company_id.ids),
                ]
            )
            for user in heads:
                rows[:1].activity_schedule(
                    "mail.mail_activity_data_todo",
                    summary=_("%(n)s lot perlu tindakan di %(wh)s", n=len(rows), wh=warehouse.code),
                    note=self._efn_count_label(rows),
                    user_id=user.id,
                )
        groups = self.env.ref("efn_expiry_monitor.group_markdown_manager")
        buyer = self.env.ref(BUYER_GROUP, raise_if_not_found=False)
        if buyer:
            groups |= buyer
        for company in alerts.company_id:
            rows = alerts.filtered(lambda a, c=company: a.company_id == c)
            buyers = Users.search(
                [("group_ids", "in", groups.ids), ("share", "=", False), ("company_ids", "in", company.ids)]
            )
            for user in buyers:
                rows[:1].activity_schedule(
                    "mail.mail_activity_data_todo",
                    summary=_("Ringkasan near-expiry %(day)s: %(n)s lot", day=today, n=len(rows)),
                    note=self._efn_count_label(rows),
                    user_id=user.id,
                )

    # ------------------------------------------------------------ aksi
    def _efn_check_actor(self):
        self.ensure_one()
        self.check_access("write")
        if self.company_id not in self.env.user.company_ids:
            raise AccessError(_("efn_expiry:company — alert PT lain."))

    def action_efn_create_markdown(self):
        """Buka form markdown baru untuk lot × outlet alert ini (create biasa: ACL + record rule markdown)."""
        self._efn_check_actor()
        if self.state == "closed":
            raise UserError(_("efn_expiry:alert_closed — alert sudah ditutup."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Markdown lot"),
            "res_model": "efn.lot.markdown",
            "view_mode": "form",
            "target": "current",
            "context": {
                "default_company_id": self.company_id.id,
                "default_warehouse_id": self.warehouse_id.id,
                "default_lot_id": self.lot_id.id,
                "default_alert_id": self.id,
                "default_qty_cap": self.qty,
                "default_date_to": fields.Date.to_string(
                    self.expiration_date.date() if self.expiration_date else fields.Date.context_today(self)
                ),
            },
        }

    def action_efn_transfer(self):
        """Buka wizard usulan transfer antar outlet (hanya tahap near/markdown, belum kedaluwarsa)."""
        self._efn_check_actor()
        if self.state == "closed" or self.stage == "expired":
            raise UserError(_("efn_expiry:transfer_stage — lot kedaluwarsa/alert tertutup tidak dipindahkan."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Usulan transfer antar outlet"),
            "res_model": "efn.expiry.transfer.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_alert_id": self.id},
        }

    def action_efn_close(self):
        for rec in self:
            rec._efn_check_actor()
            rec.sudo().write({"state": "closed"})
        return True
