# G57 — Kas kecil outlet (imprest) & uang muka karyawan. Spesifikasi: docs/fit-gap/G57_G58_ACCOUNTING_PORT.md §2.
# ADR-049: port & adaptasi dari odoo-platform `addons/ee_gap/custom_petty_cash` (19.0.0.5.0) milik pemilik proyek;
# provenance + daftar perubahan di README.md. ADR-031: hanya CE + efn_* (tanpa hr, custom_*, OCA, Enterprise).
{
    "name": "EFN Kas Kecil & Uang Muka",
    "summary": "Kas kecil outlet (imprest), uang muka karyawan, realisasi, pengisian ulang; empat mata.",
    "version": "19.0.1.0.0",
    "category": "EFN/Accounting",
    "author": "Era Food and Nourishment",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    "depends": [
        "account",
        "mail",
        "efn_audit",
        "efn_setup_menu",
    ],
    "data": [
        "security/efn_petty_cash_groups.xml",
        "security/ir.model.access.csv",
        "security/efn_petty_cash_rules.xml",
        "data/ir_sequence_data.xml",
        "data/mail_template_data.xml",
        "data/ir_cron_data.xml",
        "data/efn_audit_rule_data.xml",
        "views/petty_cash_type_views.xml",
        "views/petty_cash_fund_views.xml",
        "views/petty_cash_request_views.xml",
        "views/petty_cash_realization_views.xml",
        "wizard/petty_cash_report_wizard_views.xml",
        "report/petty_cash_vouchers.xml",
        "report/petty_cash_report_templates.xml",
        "views/petty_cash_menus.xml",
    ],
    "installable": True,
    "application": False,
}
