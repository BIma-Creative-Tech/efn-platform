"""G55: label menu EFN untuk SEMUA bahasa terpasang (docs/fit-gap/G54_G55_G61_ODOO_SETUP.md §G55.3).

`ir.ui.menu.name` translate=True: record XML hanya menulis en_US, sehingga id_ID tetap memakai terjemahan CE
("Manufaktur"/"Faktur"). `update_field_translations` menolak bahasa yang belum aktif, jadi daftar bahasa diambil dari
`res.lang.get_installed()` saat dijalankan (dipanggil `<function>` di data/menu_data.xml setiap install/upgrade).
"""

from odoo import api, models

EFN_MENU_LABELS = {
    "account.menu_finance": "Finance",
    "efn_setup_menu.menu_accounting_root": "Accounting",
    "account.menu_finance_entries": "Jurnal & Penutupan",
    "mrp.menu_mrp_root": "Produksi",
}


class IrUiMenu(models.Model):
    _inherit = "ir.ui.menu"

    @api.model
    def _efn_apply_menu_labels(self):
        """Tulis label EFN ke en_US + setiap bahasa terpasang. Idempoten; menu yang tidak ada dilewati."""
        langs = {code for code, _name in self.env["res.lang"].get_installed()} | {"en_US"}
        for xmlid, label in EFN_MENU_LABELS.items():
            menu = self.env.ref(xmlid, raise_if_not_found=False)
            if menu and menu._name == "ir.ui.menu":
                menu.sudo().update_field_translations("name", dict.fromkeys(langs, label))
