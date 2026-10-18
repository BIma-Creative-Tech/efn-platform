{
    "name": "EFN Setup - Kanal Online (Super-App)",
    "summary": "Konfigurasi data-only kanal online E-28: masa berlaku API key user teknis, partner generik & tim "
    "Super-App per PT, sale_loyalty dimatikan untuk SO; demo kanal RTL (FNB nonaktif).",
    "version": "19.0.1.3.0",
    "category": "EFN/Setup",
    "author": "Era Food and Nourishment",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # docs/08 §8.4 + §10 (G19: demo batas bayar 70 menit W-02, kanal per brand W-21) + docs/04 §10.1/§10.16.
    # ADR-034, ADR-019 A1, ADR-027, ADR-031.
    # Satu-satunya Python: post_init_hook (sale_ok=False pada loyalty.program yang sudah ada, temuan #37).
    "depends": [
        "sale_stock",
        "sale_loyalty",
        "efn_online_order",
        "efn_setup_company",
    ],
    "data": [
        "data/efn_online_setup_data.xml",
    ],
    "demo": [
        "demo/efn_online_demo.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
}
