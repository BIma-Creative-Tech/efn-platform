{
    "name": "EFN POS Refund ke Metode Asal",
    "summary": "E-31: refund POS hanya ke metode bayar order asal (nominal ≤ dibayar per metode, alokasi "
    "proporsional); retur tanpa struk tunai saja.",
    "version": "19.0.1.0.0",
    "category": "EFN/Point of Sale",
    "author": "Era Food and Nourishment",
    # LGPL-3. Tanpa dependency modul pihak ketiga (ADR-031): hanya Odoo CE 19 + efn_*.
    # Referensi desain (ide saja, tidak disalin, 19.0 belum diverifikasi): OCA/pos modul retur POS.
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # Fit-gap W-24 / E-31 (docs/08 §9.3), default L-23 (Q-F18-4/5/6) docs/04 §9.9.
    "depends": [
        "point_of_sale",
        "efn_core",
    ],
    "data": [
        "data/ir_config_parameter.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "efn_pos_refund_origin/static/src/**/*",
        ],
        "web.assets_tests": [
            "efn_pos_refund_origin/static/tests/tours/**/*",
        ],
    },
    "installable": True,
    "application": False,
}
