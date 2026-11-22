"""`efn.insight.esb.branch`: cabang ESB untuk cakupan kepala toko F&B di Insight (G52, docs/04 §28.15.3, L-240;
ADR-041 §3.6 Proposed; fit-gap efn-odoo-functional 22 Nov 2026).

CE 19 tidak punya padanan: `company_ids` hanya granular PT, `property_warehouse_id` bisa ditulis user sendiri, dan
cabang ESB bukan record Odoo (brand yang belum cutover G43). Diisi admin lewat impor CSV standar dari ekspor
`efn_dwh.v_esb_branch_list`; Odoo tidak membaca ClickHouse. Kunci mart = `(esb_brand, esb_branch_id)`: `branch_id`
hanya unik per akun ESB (`12-g41-esb.sql:157`), Bacha melayani BCF + BCR.
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from ..lib.insight_token import BRAND_COMPANY_CODES, ESB_CODES, MAX_ID

ESB_BRAND_SELECTION = [
    ("bacha", "Bacha (BCF/BCR)"),
    ("cup", "CUP"),
    ("imd", "IMD"),
    ("pb", "Paris Baguette (PB)"),
    ("wtz", "WTZ"),
]


class EfnInsightEsbBranch(models.Model):
    _name = "efn.insight.esb.branch"
    _description = "Cabang ESB (Insight)"
    _order = "company_id, esb_brand, esb_branch_id"
    _check_company_auto = True

    company_id = fields.Many2one(
        "res.company",
        string="PT",
        required=True,
        index=True,
        ondelete="restrict",
        domain=[("efn_brand_code", "in", sorted(ESB_CODES))],
    )
    esb_brand = fields.Selection(ESB_BRAND_SELECTION, string="Akun ESB", required=True)
    esb_branch_id = fields.Integer(string="ID cabang ESB", required=True)
    name = fields.Char(string="Nama cabang", required=True)
    branch_code = fields.Char(string="Kode cabang ESB")
    is_store = fields.Boolean(
        string="Cabang toko",
        default=True,
        help="Hanya cabang toko yang dipakai cakupan Insight (bukan gudang/CK).",
    )
    active = fields.Boolean(default=True, help="Arsip = hak kepala toko atas cabang ini dicabut.")

    _efn_branch_unique = models.Constraint(
        "UNIQUE(esb_brand, esb_branch_id)",
        "efn_insight:esb_branch_duplicate — satu record per (akun ESB, ID cabang). Aktifkan kembali record arsip.",
    )

    @api.depends("esb_brand", "esb_branch_id", "name")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = f"[{rec.esb_brand or '?'}:{rec.esb_branch_id or 0}] {rec.name or ''}".strip()

    @api.constrains("esb_branch_id")
    def _check_esb_branch_id(self):
        for rec in self:
            if not 1 <= rec.esb_branch_id <= MAX_ID:
                raise ValidationError(_("efn_insight:esb_branch_id — ID cabang ESB harus 1..%(max)s.", max=MAX_ID))

    @api.constrains("company_id", "esb_brand")
    def _check_brand_company(self):
        # Mencegah cabang satu brand ditautkan ke PT brand lain (mis. cabang PB ke PT WTZ).
        for rec in self:
            allowed = BRAND_COMPANY_CODES.get(rec.esb_brand, frozenset())
            if rec.company_id.efn_brand_code not in allowed:
                raise ValidationError(
                    _(
                        "efn_insight:esb_branch_company — akun ESB %(brand)s hanya untuk PT berkode %(codes)s.",
                        brand=rec.esb_brand,
                        codes=", ".join(sorted(allowed)) or "-",
                    )
                )
