"""`res.partner.efn_region_id` (ADR-036 §0.7): kode wilayah kab/kota PLAINTEXT (bukan alamat).

Kebocoran yang diterima ADR: kab/kota domisili (±500 nilai). Alamat, kota teks, dan kode pos tetap terenkripsi
(`efn_pii`). Diisi lewat dropdown (kasir/back office) atau dari BFF (`region_code`, K-36-11). Tidak diturunkan otomatis
dari kode pos (tahap 1). ETL membaca kolom `res_partner.efn_region_id` hanya lewat view `efn_etl.partner_region`
(skrip grant data-ai). Erasure UU PDP wajib mengosongkan field ini (K-36-4, `efn_privacy`).
"""

from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    efn_region_id = fields.Many2one(
        "efn.region",
        string="Wilayah (Kab/Kota)",
        index=True,
        ondelete="restrict",
        help="Kode wilayah Kemendagri untuk laporan per wilayah (plaintext; alamat tetap terenkripsi).",
    )
