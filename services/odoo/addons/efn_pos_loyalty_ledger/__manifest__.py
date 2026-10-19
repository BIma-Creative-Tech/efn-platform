{
    "name": "EFN POS Loyalty Ledger",
    "summary": "Earn, tukar (OTP), dan clawback Poin EFN di POS lewat ledger BFF (outbox transaksional).",
    # 19.0.2.3.0 (G21 E-44): GET /v1/pos/loyalty/params?company_code=<PT> (kebijakan aktif per PT, docs/04 §13.1).
    # 19.0.2.4.0 (G22 L-80, docs/04 §14.A.4): refund order sesi QR (`efn_qr_points_eligible`, efn_pos_bff_order,
    # dibaca tanpa depends) -> reverse `earn_channel: "qr"` tanpa member_ref; field outbox `earn_channel`.
    # 19.0.2.4.1 (G22 r15 K3, SEC-164): reverse QR hanya bila sesi meja sudah close (`_efn_qr_refund_mode`); refund
    # saat sesi open/expired dinetokan di event close (tanpa reverse); rasio reverse terhadap dasar neto close.
    "version": "19.0.2.4.1",
    "category": "EFN/Point of Sale",
    "author": "Era Food and Nourishment",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # Fit-gap F-39 / G13-1 / E-22 (docs/08 §6.3), kontrak docs/04 §9 v4.1 (K-1..K-8).
    # G18 E-30 (docs/08 §9.2, kontrak §9.9 K-18-1..K-18-6, keputusan pemilik proyek P-8): redeem = metode bayar
    # "Poin EFN" (pos.payment.method.efn_is_loyalty_points), bukan produk diskon; produk lama diarsipkan.
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
        # Re-review G18 SEC-125/126/129: guard integritas order & pembayaran POS; dependency (bukan hanya
        # auto_install) agar `-u` DB lama ikut memasang modul ini.
        "efn_pos_refund_origin",
    ],
    "data": [
        "security/ir.model.access.csv",
        "security/efn_pos_loyalty_security.xml",
        "data/product_data.xml",
        "data/ir_config_parameter.xml",
        "data/ir_cron.xml",
        "data/pos_audit_filters.xml",
        "views/efn_pos_loyalty_views.xml",
        "views/pos_payment_method_views.xml",
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
    "installable": True,
    "application": False,
}
