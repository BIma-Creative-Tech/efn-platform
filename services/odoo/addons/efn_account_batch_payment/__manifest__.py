# G58 — batch pembayaran + file transfer bank (batch payment CE 19 = Enterprise).
# Spesifikasi: docs/fit-gap/G57_G58_ACCOUNTING_PORT.md §5; persetujuan payment G60 (efn_account_localization,
# `efn_approval_state`) diperiksa bila field ada — tanpa dependency keras (G59_G60 §0 asumsi 2).
# ADR-049: port & adaptasi dari odoo-platform `ee_gap/custom_account_batch_payment` (provenance di README).
# ADR-031: depends hanya CE + efn_*. ADR-022/UU PDP: file transfer tidak disimpan permanen (attachment 24 jam).
{
    "name": "EFN Batch Pembayaran",
    "summary": "Batch pembayaran vendor per jurnal bank + file transfer (empat mata, fail-closed, file 24 jam).",
    "version": "19.0.1.0.0",
    "category": "Accounting/Accounting",
    "author": "Era Food and Nourishment",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    "depends": ["account", "efn_audit"],
    "data": [
        "security/ir.model.access.csv",
        "security/efn_account_batch_payment_security.xml",
        "data/efn_batch_payment_data.xml",
        "views/efn_account_batch_payment_views.xml",
        "views/efn_account_batch_payment_format_views.xml",
        "views/account_payment_views.xml",
        "views/efn_batch_payment_menus.xml",
    ],
    "installable": True,
    "application": False,
}
