{
    "name": "EFN Setup - Kanal Online (Super-App)",
    "summary": "Konfigurasi data-only kanal online E-28: masa berlaku API key user teknis, partner generik & tim "
    "Super-App per PT (GL, PB, BCR, BCF, WTZ), sale_loyalty dimatikan untuk SO; demo kanal = kode PT (GL aktif; PB, "
    "BCR, BCF, WTZ nonaktif).",
    # 19.0.1.5.0 (G20 W-32/E-43, docs/04 §12.1, L-48): kode kanal = kode PT, user teknis efn_online_<kode>,
    # partner/tim per PT baru; kanal RTL/FNB/RTL-GL/RTL-BACHA/FNB-PB/FNB-BACHA dihapus (DB dev dibuat ulang).
    # 19.0.1.6.0 (G22 W-04, docs/08 §13.2): demo outlet kafe BCF (CAFE-01, gudang OL03) ber-FP non-dine-in BCF.
    # 19.0.1.7.0 (G24 W-40, docs/04 §16.1, L-91..L-93): partner/tim Super-App PT WTZ; demo kanal WTZ nonaktif
    # (brand WTZ, user efn_online_wtz, outlet kios WTZ-01 gudang OL04 ber-FP non-dine-in WTZ).
    "version": "19.0.1.7.0",
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
