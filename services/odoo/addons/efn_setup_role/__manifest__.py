{
    "name": "EFN Setup - Peran per Jabatan & SoD",
    "summary": (
        "Konfigurasi data-only: peran efn_role per PT x jabatan (matriks docs/08 §12.2b), peran Terminal POS, "
        "daftar grup sensitif tambahan, dan pemastian aturan SoD bawaan (dirujuk lewat kode, tidak dibuat ulang)."
    ),
    # 19.0.1.0.0 (G22, docs/08 §13.4; spesifikasi §12.2b, §12.6; kontrak docs/04 §13.3 no. 2-3, L-64, L-65, L-67, L-69).
    # 19.0.1.1.0 (G24 W-40, docs/04 §16.1): peran per jabatan PT ke-7 WTZ (+ Terminal POS; tanpa CK -> tanpa mrp).
    "version": "19.0.1.1.0",
    "category": "EFN/Setup",
    "author": "Era Food and Nourishment",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # ADR-001 (satu DB multi-company), ADR-031 (tanpa dependency vendor) - keduanya Proposed.
    # Risiko: R-S14 (hak akses menumpuk), R-S10 (insider), R-B06 (fraud refund/promo), R-L01 (ekspor data pribadi).
    # Data-only: tidak ada model/field/logika Python (selain test). Satu <function> memanggil metode efn_role yang
    # sudah ada (_efn_ensure_default_rules, idempoten) agar SOD-03/SOD-05 tersedia walau efn_role terpasang lebih dulu
    # daripada efn_loyalty_accounting/purchase.
    # Grup yang dirujuk terverifikasi odoo/odoo 19.0 (WebFetch 2026-10-19): account_security.xml,
    # point_of_sale_security.xml, sales_team_security.xml, mrp_security.xml; purchase/stock dari G21 (docs/08 §12).
    "depends": [
        "efn_role",
        "efn_setup_org",
        "efn_audit",
        "efn_loyalty_accounting",
        "account",
        "point_of_sale",
        "sales_team",
        "stock",
        "purchase",
        "mrp",
    ],
    "data": [
        # Daftar grup sensitif tambahan HARUS dimuat sebelum peran (constraint sync_from_job membacanya).
        "data/ir_config_parameter.xml",
        "data/efn_role_data.xml",
        "data/efn_sod_rule_data.xml",
    ],
    "installable": True,
    "application": False,
}
