{
    "name": "EFN Setup - Audit Log",
    "summary": (
        "Konfigurasi data-only: aturan efn_audit untuk harga, pricelist, "
        "program promo, penyesuaian stok, kontak, dan parameter efn.*."
    ),
    "version": "19.0.2.0.0",
    "category": "EFN/Setup",
    "author": "Era Food and Nourishment",
    # LGPL-3: sejak Gelombang 10 tidak lagi bergantung pada OCA auditlog (AGPL-3).
    # Kebijakan: tanpa dependency OCA/Cybrosys/vendor (CLAUDE.md, ADR-031).
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # R-S10 (insider/fraud harga & stok), R-B06 (fraud promo). Fit-gap F-17, spesifikasi
    # model efn_audit: docs/08_ODOO_FIT_GAP.md §5.1.
    "depends": [
        "efn_audit",
        "product",
        "stock",
        "loyalty",
    ],
    "data": [
        "data/efn_audit_rule_data.xml",
        "data/ir_config_parameter.xml",
    ],
    "installable": True,
    "application": False,
}
