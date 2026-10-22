{
    "name": "EFN Online - Jembatan KDS (F&B online)",
    "summary": "E-29a: pesanan online F&B (SO) dibayar masuk layar dapur KDS per stasiun; semua tiket siap -> "
    "pesanan 'siap diambil' (event ready ke BFF); batal/refund -> tiket batal. Tanpa data pelanggan di tiket.",
    "version": "19.0.1.0.0",
    "category": "EFN/Point of Sale",
    "author": "Era Food and Nourishment",
    # LGPL-3. Tanpa dependency modul pihak ketiga (ADR-031): hanya Odoo CE 19 + efn_*.
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # Kontrak: docs/04_SERVICE_CONTRACTS.md §14.B (W-04, B-6/B-7) + §14.7 L-72 (D-22-8, D-22-9); spesifikasi docs/08
    # §10.6 + §13.2 (penyesuaian #1-#6). ADR-013 (KDS custom, Proposed), ADR-034 (kanal online, Proposed), ADR-031.
    "depends": [
        "efn_online_order",
        "efn_pos_kds",
    ],
    "data": [
        "views/efn_online_kds_bridge_views.xml",
    ],
    # Jembatan: ikut terpasang begitu kanal online + KDS ada.
    "auto_install": True,
    "uninstall_hook": "uninstall_hook",
    "installable": True,
    "application": False,
}
