{
    "name": "EFN Online Order - Bayar di kasir (POS)",
    "summary": "E-45: pesanan online Super-App dilunasi di kasir POS dengan kode bayar (tunai/EDC/QRIS statis). "
    "Baris layanan 'Pelunasan Pesanan Online' terkunci, validasi server saat sync, faktur SO dibayar di jurnal "
    "kliring PT (kliring bersih 0). POS tidak memuat SO (SEC-105 tetap).",
    "version": "19.0.1.1.0",
    "category": "EFN/Point of Sale",
    "author": "Era Food and Nourishment",
    # LGPL-3. Tanpa dependency modul pihak ketiga (ADR-031): hanya Odoo CE 19 + efn_*. Tidak bergantung pada
    # pos_sale (settle SO di POS sengaja tidak dipakai, docs/08 §11.8).
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # Kontrak: docs/04_SERVICE_CONTRACTS.md §12.2 + L-50, L-52, L-55, L-56; spesifikasi docs/08 §11.8 (W-30).
    # 19.0.1.1.0 (r13 SEC-145 / L-59): refund POS wajib otorisasi BFF + outlet sama (RPC baru, lihat README).
    # ADR-034 (Proposed), ADR-019 (Proposed; titipan Holding TIDAK dipakai untuk counter, L-55), ADR-031.
    "depends": [
        "efn_online_order",
        "point_of_sale",
    ],
    "data": [
        "security/ir.model.access.csv",
        "security/efn_online_pos_counter_security.xml",
        "data/ir_config_parameter.xml",
        "views/efn_online_pos_counter_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "efn_online_pos_counter/static/src/**/*",
        ],
        "web.assets_tests": [
            "efn_online_pos_counter/static/tests/tours/**/*",
        ],
    },
    # Tanpa post_init_hook: data kliring PLACEHOLDER per PT dibuat saat kanal company itu pertama kali diberi metode
    # 'counter' (efn.online.channel create/write) -> company tanpa kanal counter tidak berubah.
    # Modul jembatan: ikut terpasang begitu efn_online_order + point_of_sale ada.
    "auto_install": True,
    "installable": True,
    "application": False,
}
