{
    "name": "EFN POS Kitchen Display (KDS)",
    "summary": "Layar dapur per stasiun untuk POS restoran CE 19: tiket per stasiun, bump, SLA, cadangan printer.",
    "version": "19.0.1.1.0",
    "category": "EFN/Point of Sale",
    "author": "Era Food and Nourishment",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # Fit-gap F-52..F-54 / E-26 (docs/08 §7.3). ADR-013 opsi A (KDS custom, Proposed), ADR-023
    # (perangkat = peer WireGuard), ADR-031 (tanpa dependency vendor; Cybrosys
    # pos_kitchen_screen_odoo hanya referensi, tidak disalin). Tanpa pos_self_order / pos_mrp.
    "depends": [
        "pos_restaurant",
        "bus",
        "efn_core",
    ],
    "data": [
        "security/efn_pos_kds_security.xml",
        "security/ir.model.access.csv",
        "data/ir_config_parameter.xml",
        # SEC-77: rotasi berkala token kanal stasiun.
        "data/ir_cron.xml",
        "views/efn_kds_station_views.xml",
        "views/efn_kds_ticket_views.xml",
        "views/efn_kds_templates.xml",
    ],
    "demo": [
        "demo/efn_kds_demo.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "efn_pos_kds/static/src/pos/**/*",
        ],
        # Layar KDS: bundle sendiri (tanpa memuat seluruh POS), pola customer display CE.
        "efn_pos_kds.assets_kds": [
            ("include", "point_of_sale.base_app"),
            "efn_pos_kds/static/src/kds/**/*",
        ],
        "efn_pos_kds.assets_kds_tests": [
            ("include", "point_of_sale.base_tests"),
            "efn_pos_kds/static/tests/kds/**/*",
        ],
        "web.assets_tests": [
            "efn_pos_kds/static/tests/tours/**/*",
        ],
    },
    "installable": True,
    "application": False,
}
