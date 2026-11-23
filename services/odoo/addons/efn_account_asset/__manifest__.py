{
    "name": "EFN Aset Tetap",
    "summary": (
        "Register aset tetap per PT: jadwal penyusutan, posting bulanan (cron), revaluasi, disposal, aset pool, "
        "draf aset otomatis dari tagihan vendor berkategori 'Aset Tetap / ...' (G57)."
    ),
    "version": "19.0.1.0.0",
    "category": "EFN/Accounting",
    "author": "Era Food and Nourishment",
    # LGPL-3. Port & adaptasi modul milik pemilik proyek `odoo-platform` (ADR-049): ee_gap/custom_accounting_asset
    # + pola seed _tenants/custom_levis_asset_accounts. Provenance & daftar perubahan: README.md. Tanpa dependency
    # non-CE (ADR-031); spesifikasi docs/fit-gap/G57_G58_ACCOUNTING_PORT.md §1.
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    "depends": [
        "account",
        "mail",
        "efn_audit",
    ],
    "data": [
        "security/efn_asset_groups.xml",
        "security/ir.model.access.csv",
        "security/efn_asset_security.xml",
        "data/ir_sequence_data.xml",
        "data/ir_cron_data.xml",
        "data/efn_audit_rule_data.xml",
        "data/efn_asset_group_seed.xml",
        "views/efn_account_asset_group_views.xml",
        "views/efn_account_asset_location_views.xml",
        "views/efn_account_asset_views.xml",
        "views/efn_account_asset_revaluation_views.xml",
        "views/efn_account_asset_partial_disposal_views.xml",
        "wizards/efn_asset_disposal_wizard_views.xml",
        "wizards/efn_asset_revaluation_wizard_views.xml",
        "wizards/efn_asset_partial_disposal_wizard_views.xml",
        "wizards/efn_asset_post_wizard_views.xml",
        "wizards/efn_asset_register_wizard_views.xml",
        "report/efn_asset_register_report.xml",
        "data/server_actions.xml",
        "views/efn_account_asset_menus.xml",
    ],
    "demo": [
        "demo/efn_asset_lva_demo.xml",
    ],
    "installable": True,
    "application": False,
}
