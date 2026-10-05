{
    "name": "EFN Setup - Audit Log",
    "summary": (
        "Konfigurasi data-only: aturan OCA auditlog untuk harga, "
        "pricelist, program loyalty/promo, dan penyesuaian stok."
    ),
    "version": "19.0.1.0.1",
    "category": "EFN/Setup",
    "author": "Era Food and Nourishment",
    # AGPL-3: bergantung pada OCA auditlog (AGPL-3) — konvensi CLAUDE.md / services/odoo/oca/README.md.
    "license": "AGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # R-S10 (insider/fraud harga & stok), R-B06 (fraud promo). ADR-015 (OCA dulu).
    # auditlog 19.0.2.0.0 (OCA/server-tools, AGPL-3, depends base) terverifikasi 2026-10-05;
    # diambil oleh scripts/fetch-oca.sh.
    "depends": [
        "auditlog",
        "product",
        "stock",
        "loyalty",
    ],
    "data": [
        "data/auditlog_rule_data.xml",
    ],
    "installable": True,
    "application": False,
}
