# G58 — rekonsiliasi akun & bank untuk Odoo CE 19 (widget rekonsiliasi = Enterprise).
# Spesifikasi: docs/fit-gap/G57_G58_ACCOUNTING_PORT.md §3; true-up MDR: docs/fit-gap/G59_G60_ACCOUNTING_PORT.md D-5.
# ADR-049: port & adaptasi dari odoo-platform `ee_gap/custom_account_reconcile` + bagian generik
# `_tenants/custom_levis_bank_reconcile` (provenance di README). ADR-031: depends hanya modul CE.
{
    "name": "EFN Rekonsiliasi Akun & Bank",
    "summary": "Ringkasan akun terbuka per PT, wizard rekonsiliasi item jurnal & mutasi bank, penjaga rekonsiliasi.",
    "version": "19.0.1.0.0",
    "category": "Accounting/Accounting",
    "author": "Era Food and Nourishment",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    "depends": ["account"],
    "data": [
        "security/ir.model.access.csv",
        "security/efn_account_reconcile_security.xml",
        "views/efn_reconcile_account_views.xml",
        "views/efn_bank_reconcile_views.xml",
        "views/efn_unapplied_payment_views.xml",
        "views/account_account_views.xml",
        "views/efn_reconcile_menus.xml",
    ],
    "installable": True,
    "application": False,
}
