# G59-A: PPh potong (23 / 4(2) / 26) = pajak negatif standar CE di tagihan vendor + kode objek e-Bupot + Daftar
# Pemotongan PPh per masa. Spesifikasi docs/fit-gap/G59_G60_ACCOUNTING_PORT.md §G59-A (D-1, D-2).
# ADR-049: disederhanakan dari odoo-platform `addons/ee_gap/custom_tax_id` (engine aturan di-drop, lihat README).
# ADR-027: tarif & kode objek = konfigurasi Finance per PT (tidak ada pajak di data/). ADR-031: hanya CE + efn_core.
{
    "name": "EFN - PPh Potong (e-Bupot)",
    "summary": "Kode objek pajak e-Bupot di pajak PPh (negatif), nomor/tanggal bukti potong di baris pajak, "
    "Daftar Pemotongan PPh per masa untuk input Coretax.",
    "version": "19.0.1.0.0",
    "category": "EFN/Accounting",
    "author": "Era Food and Nourishment",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    "depends": ["account", "l10n_id", "efn_core"],
    "data": [
        "security/ir.model.access.csv",
        "views/account_tax_views.xml",
        "views/withholding_register_views.xml",
    ],
    "demo": ["demo/account_tax_pph_demo.xml"],
    "installable": True,
    "application": False,
}
