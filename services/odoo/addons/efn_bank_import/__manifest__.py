# G58 — impor mutasi bank dari CSV (template generik + deteksi otomatis BCA korporat / KlikBCA Bisnis).
# Spesifikasi: docs/fit-gap/G57_G58_ACCOUNTING_PORT.md §4. Impor file mutasi CE 19 = Enterprise.
# ADR-049: port & adaptasi dari odoo-platform `ee_gap/custom_bank_import` (provenance di README); H2H dan parser
# XLS/XLSX tidak diport (Q-G58-1). ADR-031: depends hanya CE + efn_*.
{
    "name": "EFN Impor Mutasi Bank (CSV)",
    "summary": "Impor mutasi bank CSV: dedup file & baris, tolak periode terkunci, log tanpa isi sel.",
    "version": "19.0.1.0.0",
    "category": "Accounting/Accounting",
    "author": "Era Food and Nourishment",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    "depends": ["account", "efn_audit", "efn_setup_menu"],
    "data": [
        "security/ir.model.access.csv",
        "security/efn_bank_import_security.xml",
        "data/efn_bank_import_data.xml",
        "views/efn_bank_import_template_views.xml",
        "views/efn_bank_import_log_views.xml",
        "wizard/efn_bank_import_wizard_views.xml",
        "views/efn_bank_import_menus.xml",
    ],
    "installable": True,
    "application": False,
}
