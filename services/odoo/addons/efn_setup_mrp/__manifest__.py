{
    "name": "EFN Setup - MRP Central Kitchen, Lot & FEFO",
    "summary": (
        "Konfigurasi data-only: lot/serial, multi-step routes, kategori FEFO; demo reorder rule Manufacture paket CK."
    ),
    "version": "19.0.1.0.1",
    "category": "EFN/Setup",
    "author": "Era Food and Nourishment",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # ADR-018 (kitting = BoM Manufacture + MO, Proposed), R-L07 (traceability), Q-34, Q-35.
    # depends terverifikasi di odoo/odoo 19.0 (2026-10-05): mrp, product_expiry (depends stock,
    # tidak auto-install). efn_kitting dipakai untuk demo (produk paket & company demo CK).
    "depends": [
        "mrp",
        "product_expiry",
        "efn_kitting",
    ],
    "data": [
        "data/stock_config_data.xml",
    ],
    "demo": [
        "demo/mrp_demo.xml",
    ],
    "installable": True,
    "application": False,
}
