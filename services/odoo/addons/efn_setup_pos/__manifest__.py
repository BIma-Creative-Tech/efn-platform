{
    "name": "EFN Setup - Point of Sale (Retail & Restoran)",
    "summary": "Bundel modul POS CE 19 untuk retail & F&B; demo stasiun dapur, printer dapur, denah meja.",
    "version": "19.0.1.2.0",
    "category": "EFN/Setup",
    "author": "Era Food and Nourishment",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # ADR-013 (scope POS F&B CE 19: printer dapur, kiosk pay-after-meal; KDS custom), ADR-017, ADR-020.
    # Semua depends terverifikasi ada di odoo/odoo 19.0 (docs/06 + verifikasi 2026-10-05).
    # pos_self_order depends pos_restaurant; pos_mrp auto-install (point_of_sale + mrp);
    # l10n_id_pos auto-install (l10n_id + point_of_sale).
    "depends": [
        "point_of_sale",
        "pos_restaurant",
        "pos_self_order",
        "pos_loyalty",
        "pos_mrp",
        "pos_hr",
        "pos_discount",
        "efn_setup_company",
    ],
    # Gelombang 13 (Fase 1 pilot retail): nomenklatur barcode IDR (Q-25), pecahan Rupiah,
    # default SOP kas/void (R-O05, R-O06, Q-27), batas muat produk offline (Q-7).
    "data": [
        "data/pos_config_defaults.xml",
        "data/pos_retail_defaults.xml",
        "data/pos_audit_filters.xml",
        "data/pos_payment_method_data.xml",
        "data/barcode_nomenclature_data.xml",
        "data/pos_bill_idr_data.xml",
    ],
    "demo": [
        "demo/pos_demo.xml",
        "demo/pos_retail_demo.xml",
    ],
    "installable": True,
    "application": False,
}
