{
    "name": "EFN POS Loyalty Ledger",
    "summary": "Earn, tukar (OTP), dan clawback Poin EFN di POS lewat ledger BFF (outbox transaksional).",
    "version": "19.0.1.1.0",
    "category": "EFN/Point of Sale",
    "author": "Era Food and Nourishment",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # Fit-gap F-39 / G13-1 / E-22 (docs/08 §6.3), kontrak docs/04 §9 v4.1 (K-1..K-8).
    # ADR-017 (OTP offline, Proposed), ADR-020 (ledger tunggal BFF, Proposed), ADR-029, ADR-031
    # (tanpa dependency vendor). Saldo poin TIDAK disimpan di Odoo (D-05).
    # `mail` untuk aktivitas review; pos_loyalty hanya untuk menetralkan baris poin dari
    # perhitungan program promo (bukan untuk menyimpan poin).
    "depends": [
        "point_of_sale",
        "pos_loyalty",
        "mail",
        "efn_core",
        "efn_pos_otp_gate",
    ],
    "data": [
        "security/ir.model.access.csv",
        "security/efn_pos_loyalty_security.xml",
        "data/product_data.xml",
        "data/ir_config_parameter.xml",
        "data/ir_cron.xml",
        "data/pos_audit_filters.xml",
        "views/efn_pos_loyalty_views.xml",
        "views/pos_order_views.xml",
        "views/product_category_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "efn_pos_loyalty_ledger/static/src/**/*",
        ],
        "web.assets_tests": [
            "efn_pos_loyalty_ledger/static/tests/tours/**/*",
        ],
    },
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
    "installable": True,
    "application": False,
}
