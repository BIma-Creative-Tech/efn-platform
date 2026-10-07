{
    "name": "EFN POS OTP Gate",
    "summary": "Promo/loyalty POS yang ditandai wajib OTP hanya berlaku dengan discount_token sekali pakai dari BFF.",
    "version": "19.0.1.7.0",
    "category": "EFN/Point of Sale",
    "author": "Era Food and Nourishment",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # ADR-017 (OTP saat POS offline, Proposed): opsi (a) promo ber-OTP nonaktif saat offline.
    "depends": [
        "point_of_sale",
        "pos_loyalty",
        "efn_core",
        # SEC-07: jejak audit perubahan HP member (ADR-031, pengganti OCA auditlog).
        "efn_audit",
    ],
    "data": [
        "security/ir.model.access.csv",
        "security/efn_pos_otp_security.xml",
        "views/loyalty_program_views.xml",
        "views/pos_order_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "efn_pos_otp_gate/static/src/**/*",
        ],
        # Gelombang 12: tour UI POS (hanya dimuat dalam mode test, HttpCase tag efn_tour).
        "web.assets_tests": [
            "efn_pos_otp_gate/static/tests/tours/**/*",
        ],
    },
    "installable": True,
    "application": False,
}
