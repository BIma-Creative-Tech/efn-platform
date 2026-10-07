{
    "name": "EFN POS Payment Gateway (QRIS via BFF)",
    "summary": (
        "Metode terminal POS `efn_gateway`: QRIS lewat BFF EFN (provider-agnostic), validasi server saat sync order."
    ),
    "version": "19.0.1.1.0",
    "category": "EFN/Point of Sale",
    "author": "Era Food and Nourishment",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # Fit-gap F-30 / E-15 (docs/08 §4.1). ADR-016 (provider, Proposed), ADR-019 (merchant of
    # record). Kontrak docs/04 §8.3. Tanpa pos_restaurant, payment_*, l10n_id_pos (QRIS CE
    # `id_qr` sengaja tidak dipakai). `mail` hanya untuk aktivitas rekonsiliasi intent yatim.
    "depends": [
        "point_of_sale",
        "mail",
        "efn_core",
    ],
    "data": [
        "security/ir.model.access.csv",
        "security/efn_pos_payment_gateway_security.xml",
        "data/ir_config_parameter.xml",
        "data/ir_cron.xml",
        "views/efn_pos_payment_intent_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "efn_pos_payment_gateway/static/src/**/*",
        ],
        # Gelombang 12: tour UI POS (hanya mode test, HttpCase tag efn_tour).
        "web.assets_tests": [
            "efn_pos_payment_gateway/static/tests/tours/**/*",
        ],
    },
    "installable": True,
    "application": False,
}
