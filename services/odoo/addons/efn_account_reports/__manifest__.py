{
    "name": "EFN Laporan Keuangan (Neraca, Laba Rugi, Neraca Saldo, Konsolidasi)",
    "summary": "G28 (W-16, Q-V3, BRD §5.1): laporan keuangan per PT dan konsolidasi grup dengan eliminasi antar-PT, "
    "dari jurnal posted Odoo CE; tampil di layar, PDF, dan XLSX.",
    "version": "19.0.1.0.0",
    "category": "EFN/Accounting",
    "author": "Era Food and Nourishment",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # CE 19 tanpa account_reports (Enterprise). Referensi desain (dibaca, tidak disalin, ADR-031): OCA
    # account_financial_report / mis_builder (AGPL-3), Cybrosys dynamic_accounts_report (LGPL-3).
    "depends": ["account", "efn_core"],
    "data": [
        "security/ir.model.access.csv",
        "wizard/efn_financial_report_views.xml",
        "report/efn_financial_report_templates.xml",
    ],
    "installable": True,
    "application": False,
}
