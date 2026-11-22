"""Uninstall: kembalikan menu CE yang dipindah ke induk CE sebelum akar Accounting dihapus.

`ir.ui.menu.parent_id` ondelete="restrict" -> tanpa ini uninstall gagal menghapus `menu_accounting_root`.
Label (semua bahasa) dipulihkan dengan `-u account mrp` setelah uninstall (README).
"""

# xmlid menu CE -> urutan CE (account/views/account_menuitem.xml, Odoo 19).
CE_SEQUENCES = {
    "account.menu_finance_entries": 4,
    "account.account_audit_menu": 7,
    "account.menu_finance_reports": 20,
    "account.menu_finance_configuration": 35,
}


def _efn_uninstall_restore_menus(env):
    finance = env.ref("account.menu_finance", raise_if_not_found=False)
    if not finance:
        return
    for xmlid, sequence in CE_SEQUENCES.items():
        menu = env.ref(xmlid, raise_if_not_found=False)
        if menu:
            menu.write({"parent_id": finance.id, "sequence": sequence})
