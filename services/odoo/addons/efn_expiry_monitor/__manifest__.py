{
    "name": "EFN Expiry Monitor (near-expiry, markdown per lot, FEFO POS)",
    "summary": "G45 Supermarket (docs/04 §28.5a/§28.10, fit-gap G45 S-05..S-10/S-14, ADR-045/046 Proposed): ambang "
    "kedaluwarsa per kategori, alert near-expiry harian per lot x outlet, markdown per lot (efn.lot.markdown) dengan "
    "batas diskon per peran, cek sisa umur saat terima dari DC, usulan transfer antar outlet, dan aset POS: FEFO "
    "otomatis, blokir lot kedaluwarsa (override supervisor teraudit), harga markdown hanya untuk lot GS1/pilihan "
    "eksplisit, berlaku offline. Semua di belakang parameter efn_expiry_monitor.enabled (default false).",
    # 19.0.1.1.0 (G45 security r1): SEC-216..222, SEC-224 — lihat README "Perbaikan security G45".
    # 19.0.1.1.1 (G47, fit-gap G47 §8): Markdown Manager & Supervisor override di privilege terpisah; hak efektif tetap.
    "version": "19.0.1.1.1",
    "category": "EFN/Inventory",
    "author": "Era Food and Nourishment",
    # LGPL-3. Tanpa dependency modul pihak ketiga (ADR-031). Referensi desain (ide saja, tidak disalin):
    # Cybrosys odoo_product_expiry_dashboard (19.0, AGPL-3), OCA pos_lot_barcode / pos_product_expiry (18.0).
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    "depends": [
        "stock",
        "product_expiry",
        "point_of_sale",
        "mail",
        "efn_core",
        "efn_audit",
        "efn_assortment",
        # PIN supervisor diverifikasi server dengan hash `res.users.efn_return_pin_hash` (E-41, tidak pernah ke
        # browser). Modul ini auto_install di semua DB POS EFN.
        "efn_pos_refund_origin",
    ],
    "data": [
        "security/efn_expiry_monitor_security.xml",
        "security/ir.model.access.csv",
        "data/efn_expiry_monitor_data.xml",
        "data/efn_audit_rule_data.xml",
        "views/efn_expiry_alert_views.xml",
        "views/efn_lot_markdown_views.xml",
        "views/efn_expiry_event_views.xml",
        "views/inherited_views.xml",
        "wizard/efn_expiry_transfer_wizard_views.xml",
        "wizard/efn_expiry_receipt_override_wizard_views.xml",
        "views/efn_expiry_menus.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "efn_expiry_monitor/static/src/**/*",
        ],
        # G48: tour POS (tests/test_expiry_tour.py, tag efn_tour), pola efn_pos_refund_origin.
        "web.assets_tests": [
            "efn_expiry_monitor/static/tests/tours/**/*",
        ],
    },
    "installable": True,
    "application": False,
}
