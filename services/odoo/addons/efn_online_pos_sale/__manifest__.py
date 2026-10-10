{
    "name": "EFN Online Order - POS Sales guard",
    "summary": "SEC-105: SO kanal online tidak dimuat/di-settle di POS bila pos_sale terpasang (modul jembatan).",
    "version": "19.0.1.0.0",
    "category": "EFN/Sales",
    "author": "Era Food and Nourishment",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # Re-review G17 SEC-105 / docs/04 L-19 (b). Modul jembatan auto-install: efn_online_order sengaja TIDAK
    # bergantung pada pos_sale (yang menarik sale_management); bila pos_sale terpasang, modul ini ikut terpasang
    # agar override-nya berada di atas pos_sale dalam MRO. ADR-034, ADR-031 (hanya CE + efn_*).
    "depends": ["efn_online_order", "pos_sale"],
    "data": [],
    "auto_install": True,
    "installable": True,
    "application": False,
}
