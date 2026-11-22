# G55 — Menu "Finance" (akar CE account.menu_finance, label diganti) dan "Accounting" (akar baru) terpisah;
# "Manufacturing" -> "Produksi". Spesifikasi: docs/fit-gap/G54_G55_G61_ODOO_SETUP.md §G55.
# ADR-031: hanya modul CE (account, mrp); tanpa dependency ke efn_account_reports (menunya ikut induk).
{
    "name": "EFN Setup - Menu Finance/Accounting/Produksi",
    "summary": "Pisahkan menu Finance dan Accounting; label 'Produksi' untuk Manufacturing (semua bahasa aktif).",
    "version": "19.0.1.0.0",
    "category": "EFN/Setup",
    "author": "Era Food and Nourishment",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    "depends": ["account", "mrp"],
    # noupdate=0: diterapkan ulang setiap -u (menu CE di-reset oleh -u account / -u mrp; modul ini dimuat sesudahnya).
    "data": ["data/menu_data.xml"],
    "uninstall_hook": "_efn_uninstall_restore_menus",
    "installable": True,
    "application": False,
}
