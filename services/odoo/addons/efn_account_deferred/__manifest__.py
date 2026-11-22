# G59-B: biaya dibayar di muka / pendapatan diterima di muka (deferred expense & revenue).
# Port ADR-049 dari odoo-platform `addons/ee_gap/custom_account_deferred` (19.0.1.0.0, LGPL-3, milik pemilik proyek);
# spesifikasi docs/fit-gap/G59_G60_ACCOUNTING_PORT.md §G59-B. ADR-031: hanya CE + efn_core.
{
    "name": "EFN - Biaya & Pendapatan Tangguhan",
    "summary": "Baris tagihan/faktur dengan tanggal mulai-selesai dibagi per bulan lewat akun tangguhan "
    "(jurnal penangguhan + pengakuan bulanan, analitik ikut terbawa).",
    "version": "19.0.1.0.0",
    "category": "EFN/Accounting",
    "author": "Era Food and Nourishment",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    "depends": ["account", "efn_core"],
    "data": [
        "security/ir.model.access.csv",
        "views/account_move_views.xml",
        "views/res_config_settings_views.xml",
        "views/deferred_views.xml",
    ],
    "demo": ["demo/deferred_demo.xml"],
    "installable": True,
    "application": False,
}
