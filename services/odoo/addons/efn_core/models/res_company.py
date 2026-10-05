from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Kode company sesuai docs/04_SERVICE_CONTRACTS.md §1. Perubahan = ubah kontrak dulu.
EFN_BRAND_CODES = [
    ("EFN", "EFN - Holding / Agen Penagih"),
    ("RTL", "RTL - PT Retail / Supermarket (PPN)"),
    ("FNB", "FNB - PT F&B (PB1)"),
    ("FARM", "FARM - Collection Center / Farming"),
]

# Kode brand -> jenis usaha (relasi 1:1 tetap).
EFN_BRAND_BUSINESS_TYPE = {
    "EFN": "holding",
    "RTL": "retail",
    "FNB": "fnb",
    "FARM": "farm",
}


class ResCompany(models.Model):
    """Atribut per-PT yang dibutuhkan platform (kode brand, jenis usaha, agen penagih)."""

    _inherit = "res.company"

    efn_brand_code = fields.Selection(
        EFN_BRAND_CODES,
        string="Kode Company EFN",
        copy=False,
        help="Kode company yang dipakai BFF/Super-App (field `company_code` di kontrak "
        "docs/04_SERVICE_CONTRACTS.md §1). Unik per database.",
    )
    # Gelombang 3b (fit-gap E-8): compute stored + readonly dari efn_brand_code.
    # Dipilih (bukan dihapus) karena paling sedikit risiko: nama field & nilai tetap sama
    # untuk pembaca yang sudah ada (view, data efn_setup_company, BFF/ETL bila membaca),
    # tetapi tidak bisa lagi tidak konsisten. Stored agar bisa dipakai di domain/filter.
    # Rezim pajak TIDAK diturunkan dari field ini (pakai account_sale_tax_id/fiscal position).
    efn_business_type = fields.Selection(
        [
            ("retail", "Supermarket Retail (PPN)"),
            ("fnb", "F&B Restoran (PB1)"),
            ("holding", "Holding / Agen Penagih"),
            ("farm", "Farming / Collection Center"),
        ],
        string="Jenis Usaha",
        compute="_compute_efn_business_type",
        store=True,
        readonly=True,
        help="Diturunkan otomatis dari Kode Company EFN (tidak diisi manual).",
    )
    efn_is_merchant_of_record = fields.Boolean(
        string="Agen Penagih / Pemegang Akun Gateway",
        copy=False,
        help="PT pemegang akun payment gateway untuk unified cart yang menagih inter-company "
        "(ADR-019). Hanya SATU company boleh aktif.",
    )

    # Odoo 19: SQL constraint dideklarasikan dengan models.Constraint (pengganti _sql_constraints).
    # UNIQUE di PostgreSQL mengizinkan banyak NULL, jadi company tanpa kode tetap boleh.
    _efn_brand_code_unique = models.Constraint(
        "UNIQUE(efn_brand_code)",
        "Kode company EFN harus unik: satu kode hanya untuk satu company.",
    )

    @api.depends("efn_brand_code")
    def _compute_efn_business_type(self):
        for company in self:
            company.efn_business_type = EFN_BRAND_BUSINESS_TYPE.get(company.efn_brand_code) or False

    @api.constrains("efn_is_merchant_of_record")
    def _check_efn_single_merchant_of_record(self):
        if not self.filtered("efn_is_merchant_of_record"):
            return
        # Lintas company: sudo + active_test=False agar company arsip ikut dihitung.
        count = self.sudo().with_context(active_test=False).search_count([("efn_is_merchant_of_record", "=", True)])
        if count > 1:
            raise ValidationError(
                _("Hanya satu company yang boleh menjadi agen penagih / pemegang akun gateway (ADR-019).")
            )
