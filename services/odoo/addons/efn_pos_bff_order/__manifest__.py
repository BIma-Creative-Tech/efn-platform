{
    "name": "EFN POS - Pesanan QR ponsel lewat BFF",
    "summary": "E-27: sesi meja QR dinamis (diterbitkan BFF), pesanan ponsel Super-App menjadi order draft di meja "
    "(harga dihitung Odoo), konfirmasi pelayan/auto ke dapur, outbox event bertanda tangan HMAC ke BFF.",
    # 19.0.1.1.0 (G22 r15): K1 order_uuid di submit/status/event, K2 neto refund + pos_member_attached, K3 urutan
    # refund vs close (payload dihitung ulang saat kirim, flag setelah 2xx), SEC-166 (tanpa serap order tamu
    # berikutnya), SEC-167 (print_no MAX+1), SEC-168 (filter PII catatan NFKC + Cf). Kolom baru: pos.order
    # efn_qr_eligible_amount/efn_qr_refund_netted, outbox submission_order_uuid (tanpa migrasi data).
    "version": "19.0.1.2.0",  # G31b: close.orders[].mgm_eligible_amount (docs/04 §20.10)
    "category": "EFN/Point of Sale",
    "author": "Era Food and Nourishment",
    # LGPL-3. Tanpa dependency modul pihak ketiga (ADR-031). Kontrak: docs/04_SERVICE_CONTRACTS.md §14.A + §14.7
    # (L-70, L-71, L-72, L-74, L-76; kontrak menang atas docs/08 §13.1b). ADR-032 (Accepted opsi B), ADR-023,
    # ADR-013, ADR-027, ADR-031. pos_self_order hanya untuk recompute_prices/_check_combo_lines + pos.preset.service_at
    # + product.template.self_order_available (route/mode mobile TIDAK dipakai; kunci SEC-75 tetap).
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    "depends": [
        "pos_restaurant",
        "pos_self_order",
        "efn_pos_kds",
        "efn_core",
        "efn_audit",
    ],
    "data": [
        "security/efn_pos_bff_order_security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "data/efn_audit_rule_data.xml",
        "views/efn_pos_qr_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "efn_pos_bff_order/static/src/app/**/*",
        ],
    },
    # requests: klien BFF (efn_core); reportlab: render QR (dependensi Odoo).
    "external_dependencies": {"python": ["requests", "reportlab"]},
    "installable": True,
    "application": False,
}
