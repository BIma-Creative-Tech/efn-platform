"""Field assortment di CE + penegakan saat konfirmasi transfer DC -> outlet (docs/04 §28.5, ADR-045 §3.2)."""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .efn_assortment import ENFORCE_PARAM, GROUP_MANAGER, is_overridable

_logger = logging.getLogger(__name__)

# SEC-208: field master assortment hanya boleh ditulis Assortment Manager
# (guard server-side; view groups= bukan kontrol).
WAREHOUSE_GUARDED_FIELDS = frozenset({"efn_capability_ids", "efn_is_dc", "efn_assortment_policy"})
NEUTRAL_CREATE_VALUES = (False, None, "open", [], [(6, 0, [])])


def check_assortment_writer(env, vals, guarded, creating=False):
    """AccessError bila ``vals`` menyentuh field terjaga dan user bukan Assortment Manager (sudo/superuser lolos).

    Saat create, nilai netral (DC=False, policy open, tanpa capability) tidak dianggap perubahan."""
    touched = set(vals) & guarded
    if creating:
        touched = {name for name in touched if vals[name] not in NEUTRAL_CREATE_VALUES}
    if not touched or env.su or env.user.has_group(GROUP_MANAGER):
        return
    raise AccessError(
        _(
            "efn_assortment:manager_only — field %(fields)s hanya boleh diubah Assortment Manager.",
            fields=", ".join(sorted(touched)),
        )
    )


class StockWarehouse(models.Model):
    _inherit = "stock.warehouse"

    efn_is_dc = fields.Boolean(
        string="Distribution Center (assortment ditegakkan)",
        help="Transfer dari gudang ini ke gudang lain di PT yang sama dicek terhadap assortment outlet tujuan saat "
        "dikonfirmasi (ADR-045). Gudang CK/F&B tidak dicentang.",
    )
    efn_assortment_policy = fields.Selection(
        [("open", "Terbuka (semua boleh kecuali dilarang)"), ("closed", "Tertutup (hanya yang diizinkan)")],
        string="Kebijakan assortment",
        default="open",
        required=True,
    )
    efn_capability_ids = fields.Many2many(
        "efn.assortment.capability",
        "efn_assortment_warehouse_capability_rel",
        "warehouse_id",
        "capability_id",
        string="Kemampuan outlet",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            check_assortment_writer(self.env, vals, WAREHOUSE_GUARDED_FIELDS, creating=True)
        return super().create(vals_list)

    def write(self, vals):
        check_assortment_writer(self.env, vals, WAREHOUSE_GUARDED_FIELDS)
        return super().write(vals)

    @api.model
    def _efn_assortment_enforced(self):
        value = (self.env["ir.config_parameter"].sudo().get_param(ENFORCE_PARAM) or "true").strip().lower()
        return value not in ("false", "0")

    def _efn_assortment_allowed(self, product):
        """Kenyamanan: ``warehouse._efn_assortment_allowed(product)`` = keputusan tunggal di efn.assortment.line."""
        self.ensure_one()
        return self.env["efn.assortment.line"]._efn_assortment_allowed(product, self)


class StockMove(models.Model):
    _inherit = "stock.move"

    def _efn_target_warehouse(self):
        """Gudang tujuan akhir move (tujuan langsung, ``location_final_id``, atau move tujuan berantai)."""
        self.ensure_one()
        source = self.location_id.warehouse_id
        candidates = [self.location_dest_id.warehouse_id]
        if "location_final_id" in self._fields:
            candidates.append(self.location_final_id.warehouse_id)
        candidates.extend(self.move_dest_ids.location_dest_id.warehouse_id)
        for wh in candidates:
            if wh and wh != source:
                return wh
        return self.env["stock.warehouse"]

    def _efn_transit_leg_target(self):
        """SEC-209: gudang outlet tujuan bila move ini adalah langkah transit -> outlet (picking kedua transfer dua
        langkah DC -> transit -> outlet), selain itu recordset kosong.

        Syarat: lokasi sumber ber-usage ``transit`` milik PT yang sama dengan gudang tujuan, gudang tujuan bukan DC,
        dan PT itu punya minimal satu gudang ``efn_is_dc`` (PT F&B tanpa DC tidak terpengaruh). Transit antar-PT
        (lokasi tanpa company) dilewati. Langkah pertama DC -> transit tanpa tujuan akhir tidak bisa dinilai; keputusan
        jatuh di langkah ini, sehingga transfer manual dua langkah tetap terblokir (preventif, bukan hanya laporan).

        SEC-221 (G45, 19.0.1.3.0): lokasi antara juga mencakup lokasi ``internal`` TANPA gudang dan transit antar-PT
        (company kosong); lokasi ber-company harus sama dengan PT outlet tujuan."""
        self.ensure_one()
        Warehouse = self.env["stock.warehouse"]
        source = self.location_id.sudo()
        intermediate = source.usage == "transit" or (source.usage == "internal" and not source.warehouse_id)
        if not intermediate:
            return Warehouse
        target = self.location_dest_id.warehouse_id.sudo()
        if not target or target.efn_is_dc or (source.company_id and target.company_id != source.company_id):
            return Warehouse
        has_dc = Warehouse.sudo().search_count(
            [("company_id", "=", target.company_id.id), ("efn_is_dc", "=", True)], limit=1
        )
        return target if has_dc else Warehouse

    def _efn_assortment_violations(self):
        """[(move, outlet, kode)] untuk move dari gudang DC ke gudang lain PT yang sama, atau langkah transit -> outlet
        di PT ber-DC (SEC-209), yang tidak lolos."""
        result = []
        for move in self:
            source = move.location_id.warehouse_id
            if source and source.sudo().efn_is_dc:
                target = move._efn_target_warehouse()
                if not target or target.company_id != source.company_id:
                    continue
            else:
                target = move._efn_transit_leg_target()
                if not target:
                    continue
            ok, reason = target._efn_assortment_allowed(move.product_id)
            if ok:
                continue
            if move.picking_id and is_overridable(reason):
                override = (
                    self.env["efn.assortment.override"]
                    .sudo()
                    .search_count(
                        [
                            ("picking_id", "=", move.picking_id.id),
                            ("product_id", "=", move.product_id.id),
                            ("reason_code", "=", reason),
                        ],
                        limit=1,
                    )
                )
                if override:
                    continue
            result.append((move, target, reason))
        return result

    def _action_confirm(self, *args, **kwargs):
        if self.env["stock.warehouse"]._efn_assortment_enforced():
            violations = self._efn_assortment_violations()
            if violations:
                lines = "\n".join(
                    f"- {move.product_id.display_name} -> {outlet.code}: {reason}"
                    for move, outlet, reason in violations[:20]
                )
                raise UserError(
                    _(
                        "efn_assortment:blocked — transfer DC ke outlet melanggar assortment (ADR-045):\n%(lines)s\n"
                        "Hapus baris tersebut, perbaiki data assortment, atau minta Assortment Manager melakukan "
                        "override (tidak tersedia untuk izin alkohol / non-halal).",
                        lines=lines,
                    )
                )
        else:
            _logger.warning("EFN assortment: penegakan dimatikan (efn_assortment.enforce=false)")
        return super()._action_confirm(*args, **kwargs)


class StockPicking(models.Model):
    _inherit = "stock.picking"

    def action_efn_assortment_override(self):
        """Buka wizard override (grup Assortment Manager + akses baca transfer + company)."""
        self.ensure_one()
        if not self.env.user.has_group(GROUP_MANAGER):
            raise AccessError(_("efn_assortment:manager_only — hanya Assortment Manager."))
        self.check_access("read")
        if self.company_id not in self.env.user.company_ids:
            raise AccessError(_("efn_assortment:company — transfer PT lain."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Override assortment"),
            "res_model": "efn.assortment.override.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_picking_id": self.id},
        }
