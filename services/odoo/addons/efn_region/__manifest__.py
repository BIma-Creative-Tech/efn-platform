{
    "name": "EFN Wilayah (kab/kota Kemendagri)",
    "summary": "ADR-036 §0.7: kab/kota berkode Kemendagri + res.partner.efn_region_id (plaintext) untuk laporan per "
    "wilayah lewat DWH; alamat/kota/kode pos tetap terenkripsi (efn_pii). Data contoh + importer CSV (Q-PII10).",
    "version": "19.0.1.0.0",
    "category": "EFN",
    "author": "Era Food and Nourishment",
    # LGPL-3. Tanpa dependency modul pihak ketiga (ADR-031, CLAUDE.md).
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    "depends": ["base", "contacts"],
    "data": [
        "security/ir.model.access.csv",
        "security/efn_region_security.xml",
        "data/efn.region.csv",
        "views/efn_region_views.xml",
        "views/res_partner_views.xml",
    ],
    "installable": True,
    "application": False,
}
