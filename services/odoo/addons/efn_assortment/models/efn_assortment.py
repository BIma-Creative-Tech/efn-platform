"""Restriksi assortment outlet supermarket (G44, docs/04 §28.5, ADR-045 §3.2, fit-gap G45 §5, L-150).

``_efn_assortment_allowed(product, warehouse)`` -> ``(bool, alasan)`` adalah SATU-SATUNYA fungsi keputusan, dipakai:
(1) generator usulan ``efn_replenishment`` (baris gagal tidak diusulkan, kode dicatat), (2) konfirmasi transfer DC ->
outlet (``stock.move._action_confirm``), (3) laporan pelanggaran harian. **Tidak** ditegakkan di POS.

Urutan keputusan (kode alasan kontrak §28.5):
1. ada baris ``deny`` aktif (produk/kategori)                         -> ``denied``
2. policy gudang ``closed`` dan tidak ada baris ``allow``              -> ``not_listed``
3. ``efn_required_capability_ids`` produk ⊄ capability gudang          -> ``missing_capability:<code>``
4. supplier berkontrak region dan region outlet tidak tercakup         -> ``supplier_region``
5. baris allow paling spesifik ``supply_mode = dsd`` (pasok dari DC)   -> ``dsd_only``
Selain itu ``(True, "ok")``. Baris produk lebih spesifik daripada baris kategori; kategori lebih dalam lebih spesifik.

Override: grup *Assortment Manager*, alasan wajib, per transfer, teraudit ``efn_audit``; **tidak tersedia** bila
capability yang kurang ``alcohol_license`` / ``non_halal_allowed`` (Q-SM22). Parameter ``efn_assortment.enforce``
default ``true``; hanya nilai persis ``false``/``0`` yang mematikan (fail closed).
"""

import logging
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

ENFORCE_PARAM = "efn_assortment.enforce"
PROTECTED_CAPABILITIES = frozenset({"alcohol_license", "non_halal_allowed"})
CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
REASON_CODES = ("denied", "not_listed", "missing_capability", "supplier_region", "dsd_only")
GROUP_MANAGER = "efn_assortment.group_assortment_manager"


def reason_base(reason):
    """``missing_capability:freezer`` -> ``missing_capability``."""
    return (reason or "").split(":", 1)[0]


def is_overridable(reason):
    if reason_base(reason) == "missing_capability":
        return reason.split(":", 1)[1] not in PROTECTED_CAPABILITIES
    return reason_base(reason) in REASON_CODES


class EfnAssortmentCapability(models.Model):
    _name = "efn.assortment.capability"
    _description = "EFN kemampuan outlet (chiller, freezer, izin alkohol, boleh non-halal)"
    _order = "code"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, index=True, help="Huruf kecil/angka/garis bawah, mis. chiller, alcohol_license.")
    company_id = fields.Many2one("res.company", index=True, help="Kosong = berlaku untuk semua PT.")
    description = fields.Text()
    active = fields.Boolean(default=True)
    is_protected = fields.Boolean(
        compute="_compute_is_protected",
        help="Kekurangan capability ini tidak bisa di-override (alkohol / non-halal, L-150).",
    )

    @api.depends("code")
    def _compute_is_protected(self):
        for rec in self:
            rec.is_protected = rec.code in PROTECTED_CAPABILITIES

    @api.constrains("code", "company_id")
    def _check_efn_code(self):
        for rec in self:
            if not CODE_RE.match(rec.code or ""):
                raise ValidationError(_("efn_assortment:capability_code — huruf kecil, angka, garis bawah (2-32)."))
            dup = (
                self.sudo()
                .with_context(active_test=False)
                .search_count(
                    [("id", "!=", rec.id), ("code", "=", rec.code), ("company_id", "in", (False, rec.company_id.id))]
                )
            )
            if dup:
                raise ValidationError(_("efn_assortment:capability_dup — kode capability %s sudah ada.", rec.code))


class EfnAssortmentLine(models.Model):
    _name = "efn.assortment.line"
    _description = "EFN baris assortment outlet (izin/larangan, mode pasok, kapasitas rak)"
    _order = "warehouse_id, mode desc, id"
    _check_company_auto = True

    company_id = fields.Many2one("res.company", required=True, index=True, default=lambda self: self.env.company)
    warehouse_id = fields.Many2one("stock.warehouse", required=True, index=True, check_company=True)
    product_tmpl_id = fields.Many2one("product.template", string="Produk", index=True, check_company=True)
    categ_id = fields.Many2one("product.category", string="Kategori", index=True)
    mode = fields.Selection([("allow", "Izinkan"), ("deny", "Larang")], required=True, default="allow")
    supply_mode = fields.Selection(
        [("dc", "Dari DC"), ("dsd", "Direct store delivery (supplier langsung)")], required=True, default="dc"
    )
    dsd_partner_id = fields.Many2one("res.partner", string="Supplier DSD", check_company=True)
    shelf_capacity_qty = fields.Float(
        string="Kapasitas rak (UoM produk)",
        digits="Product Unit",
        help="0 = tidak dibatasi (dibaca efn_replenishment).",
    )
    date_from = fields.Date()
    date_to = fields.Date()
    active = fields.Boolean(default=True)
    note = fields.Char()

    @api.constrains(
        "product_tmpl_id",
        "categ_id",
        "supply_mode",
        "dsd_partner_id",
        "date_from",
        "date_to",
        "shelf_capacity_qty",
        "mode",
    )
    def _check_efn_line(self):
        for rec in self:
            if bool(rec.product_tmpl_id) == bool(rec.categ_id):
                raise ValidationError(_("efn_assortment:target — isi produk ATAU kategori (tepat satu)."))
            if rec.mode == "allow" and rec.supply_mode == "dsd" and not rec.dsd_partner_id:
                raise ValidationError(_("efn_assortment:dsd_partner — mode DSD butuh supplier DSD."))
            if rec.date_from and rec.date_to and rec.date_to < rec.date_from:
                raise ValidationError(_("efn_assortment:dates — tanggal akhir sebelum tanggal mulai."))
            if rec.shelf_capacity_qty < 0:
                raise ValidationError(_("efn_assortment:shelf — kapasitas rak tidak boleh negatif."))

    @api.depends("warehouse_id", "product_tmpl_id", "categ_id", "mode")
    def _compute_display_name(self):
        for rec in self:
            target = rec.product_tmpl_id.display_name or rec.categ_id.display_name or "?"
            rec.display_name = f"{rec.warehouse_id.code or '?'} / {target} ({rec.mode})"

    # ------------------------------------------------------------ keputusan
    @api.model
    def _efn_lines_for(self, product, warehouse, day=None):
        """Baris aktif yang berlaku untuk produk di gudang, urut paling spesifik dulu."""
        day = day or fields.Date.context_today(self)
        categ_ids = []
        categ = product.categ_id
        while categ:
            categ_ids.append(categ.id)
            categ = categ.parent_id
        lines = self.sudo().search(
            [
                ("warehouse_id", "=", warehouse.id),
                "|",
                ("product_tmpl_id", "=", product.product_tmpl_id.id),
                ("categ_id", "in", categ_ids),
                "|",
                ("date_from", "=", False),
                ("date_from", "<=", day),
                "|",
                ("date_to", "=", False),
                ("date_to", ">=", day),
            ]
        )
        depth = {cid: i for i, cid in enumerate(categ_ids)}  # 0 = kategori produk sendiri (paling spesifik)

        def specificity(line):
            if line.product_tmpl_id:
                return (0, 0)
            return (1, depth.get(line.categ_id.id, 999))

        return lines.sorted(key=specificity)

    @api.model
    def _efn_assortment_allowed(self, product, warehouse, day=None):
        """Keputusan tunggal (docs/04 §28.5). ``product`` = product.product, ``warehouse`` = outlet tujuan.

        Kembalikan ``(True, "ok")`` atau ``(False, kode)`` dengan kode ∈ ``denied``, ``not_listed``,
        ``missing_capability:<code>``, ``supplier_region``, ``dsd_only``."""
        product.ensure_one()
        warehouse.ensure_one()
        product = product.sudo()
        warehouse = warehouse.sudo()
        lines = self._efn_lines_for(product, warehouse, day)
        if lines.filtered(lambda line: line.mode == "deny"):
            return False, "denied"
        allow = lines.filtered(lambda line: line.mode == "allow")
        if warehouse.efn_assortment_policy == "closed" and not allow:
            return False, "not_listed"
        required = product.product_tmpl_id.efn_required_capability_ids
        have = set(warehouse.efn_capability_ids.mapped("code"))
        missing = sorted(code for code in required.mapped("code") if code not in have)
        if missing:
            # Capability terlindungi dilaporkan lebih dulu agar override tidak tersedia (L-150).
            protected = [code for code in missing if code in PROTECTED_CAPABILITIES]
            return False, f"missing_capability:{(protected or missing)[0]}"
        if not self._efn_region_ok(product, warehouse):
            return False, "supplier_region"
        if allow[:1].supply_mode == "dsd":
            return False, "dsd_only"
        return True, "ok"

    @api.model
    def _efn_region_ok(self, product, warehouse):
        """Lolos bila tidak ada supplier berkontrak region, atau ada supplier tanpa kontrak region, atau region outlet
        (``warehouse.partner_id.efn_region_id``) tercakup salah satu kontrak."""
        sellers = product.seller_ids.filtered(
            lambda s: (
                (not s.company_id or s.company_id == warehouse.company_id)
                and (not s.product_id or s.product_id == product)
            )
        )
        if not sellers or not sellers.filtered("efn_region_ids"):
            return True
        if sellers.filtered(lambda s: not s.efn_region_ids):
            return True
        region = warehouse.partner_id.efn_region_id
        return bool(region) and any(region in s.efn_region_ids for s in sellers)

    @api.model
    def _efn_allow_line(self, product, warehouse, day=None):
        """Baris ``allow`` paling spesifik (sumber ``supply_mode`` & ``shelf_capacity_qty``) atau recordset kosong."""
        return self._efn_lines_for(product, warehouse, day).filtered(lambda line: line.mode == "allow")[:1]


class EfnAssortmentOverride(models.Model):
    """Override restriksi per transfer (Assortment Manager, alasan wajib). Append-only, teraudit efn_audit."""

    _name = "efn.assortment.override"
    _description = "EFN override restriksi assortment"
    _order = "id desc"
    _check_company_auto = True

    company_id = fields.Many2one("res.company", required=True, index=True, readonly=True)
    picking_id = fields.Many2one("stock.picking", required=True, index=True, readonly=True, check_company=True)
    warehouse_id = fields.Many2one("stock.warehouse", string="Outlet tujuan", required=True, readonly=True)
    product_id = fields.Many2one("product.product", required=True, readonly=True)
    reason_code = fields.Char(string="Kode restriksi", required=True, readonly=True)
    reason = fields.Text(string="Alasan override", required=True, readonly=True)
    user_id = fields.Many2one("res.users", required=True, readonly=True, default=lambda self: self.env.user)

    @api.constrains("reason_code", "reason")
    def _check_efn_override(self):
        for rec in self:
            if not is_overridable(rec.reason_code):
                raise ValidationError(
                    _("efn_assortment:not_overridable — restriksi %s tidak bisa di-override.", rec.reason_code)
                )
            if len((rec.reason or "").strip()) < 10:
                raise ValidationError(_("efn_assortment:reason — alasan override minimal 10 karakter."))

    def write(self, vals):
        raise UserError(_("efn_assortment:append_only — override tidak bisa diubah."))

    def unlink(self):
        raise UserError(_("efn_assortment:append_only — override tidak bisa dihapus."))


class EfnAssortmentViolation(models.Model):
    """Laporan pelanggaran harian (cron): stok outlet yang tidak lagi diizinkan + override hari itu."""

    _name = "efn.assortment.violation"
    _description = "EFN pelanggaran assortment (laporan harian)"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, warehouse_id, id"
    _check_company_auto = True

    company_id = fields.Many2one("res.company", required=True, index=True, readonly=True)
    date = fields.Date(required=True, index=True, readonly=True)
    warehouse_id = fields.Many2one("stock.warehouse", required=True, readonly=True, check_company=True)
    product_id = fields.Many2one("product.product", required=True, readonly=True)
    reason_code = fields.Char(required=True, readonly=True)
    source = fields.Selection(
        [("stock", "Stok di outlet"), ("override", "Override transfer")], required=True, readonly=True
    )
    qty = fields.Float(digits="Product Unit", readonly=True)
    override_id = fields.Many2one("efn.assortment.override", readonly=True)

    _efn_violation_uniq = models.Constraint(
        "UNIQUE(company_id, date, warehouse_id, product_id, source, reason_code)",
        "Pelanggaran yang sama sudah tercatat hari ini.",
    )

    @api.model
    def _cron_efn_daily_report(self):
        """Pindai outlet yang dipasok DC (punya resupply dari gudang DC) + override 24 jam terakhir."""
        if not self.env["stock.warehouse"]._efn_assortment_enforced():
            _logger.warning("EFN assortment: efn_assortment.enforce=false -> laporan harian tetap dibuat")
        Warehouse = self.env["stock.warehouse"].sudo()
        today = fields.Date.context_today(self)
        created = self.sudo().browse()
        for dc in Warehouse.search([("efn_is_dc", "=", True)]):
            outlets = Warehouse.search(
                [("company_id", "=", dc.company_id.id), ("id", "!=", dc.id), ("resupply_wh_ids", "in", dc.ids)]
            )
            for outlet in outlets:
                created |= self.sudo()._efn_scan_outlet(outlet, today)
        since = fields.Datetime.subtract(fields.Datetime.now(), days=1)
        for override in self.env["efn.assortment.override"].sudo().search([("create_date", ">=", since)]):
            created |= self.sudo()._efn_record(
                override.company_id,
                today,
                override.warehouse_id,
                override.product_id,
                override.reason_code,
                "override",
                0.0,
                override,
            )
        self.sudo()._efn_notify(created, today)
        return len(created)

    def _efn_scan_outlet(self, outlet, today):
        quants = (
            self.env["stock.quant"]
            .sudo()
            .search(
                [
                    ("location_id", "child_of", outlet.view_location_id.id),
                    ("location_id.usage", "=", "internal"),
                    ("quantity", ">", 0),
                ]
            )
        )
        qty_by_product = {}
        for quant in quants:
            qty_by_product[quant.product_id] = qty_by_product.get(quant.product_id, 0.0) + quant.quantity
        created = self.browse()
        for product, qty in qty_by_product.items():
            ok, reason = outlet._efn_assortment_allowed(product)
            # Barang DSD memang tidak datang dari DC -> bukan pelanggaran stok.
            if ok or reason == "dsd_only":
                continue
            created |= self._efn_record(outlet.company_id, today, outlet, product, reason, "stock", qty)
        return created

    def _efn_record(self, company, day, warehouse, product, reason, source, qty, override=None):
        existing = self.search(
            [
                ("company_id", "=", company.id),
                ("date", "=", day),
                ("warehouse_id", "=", warehouse.id),
                ("product_id", "=", product.id),
                ("source", "=", source),
                ("reason_code", "=", reason),
            ],
            limit=1,
        )
        if existing:
            return self.browse()
        return self.create(
            {
                "company_id": company.id,
                "date": day,
                "warehouse_id": warehouse.id,
                "product_id": product.id,
                "reason_code": reason,
                "source": source,
                "qty": qty,
                "override_id": override.id if override else False,
            }
        )

    def _efn_notify(self, violations, day):
        """Satu aktivitas To-Do per company ke setiap Assortment Manager (bukan satu per baris)."""
        group = self.env.ref(GROUP_MANAGER, raise_if_not_found=False)
        if not group or not violations:
            return
        for company in violations.company_id:
            rows = violations.filtered(lambda v, c=company: v.company_id == c)
            users = (
                self.env["res.users"]
                .sudo()
                .search([("group_ids", "in", group.ids), ("share", "=", False), ("company_ids", "in", company.ids)])
            )
            for user in users:
                rows[:1].activity_schedule(
                    "mail.mail_activity_data_todo",
                    summary=_("%(n)s pelanggaran assortment %(day)s", n=len(rows), day=day),
                    note=_("Lihat Inventory > Assortment > Pelanggaran (filter tanggal %s).", day),
                    user_id=user.id,
                )
