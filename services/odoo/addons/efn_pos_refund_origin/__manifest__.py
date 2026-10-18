{
    "name": "EFN POS Refund ke Metode Asal",
    "summary": "E-31: refund POS hanya ke metode bayar order asal (nominal ≤ dibayar per metode, alokasi "
    "proporsional). E-41: retur tanpa struk tunai saja dengan persetujuan Store Manager (PIN diverifikasi server).",
    "version": "19.0.1.3.0",
    "category": "EFN/Point of Sale",
    "author": "Era Food and Nourishment",
    # LGPL-3. Tanpa dependency modul pihak ketiga (ADR-031): hanya Odoo CE 19 + efn_*.
    # Referensi desain (ide saja, tidak disalin): OCA/pos modul retur POS (19.0 belum diverifikasi); Cybrosys
    # pos_refund_password@19.0 (AGPL-3, kata sandi global sisi klien — hanya ide, ditolak: verifikasi harus server).
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # Fit-gap W-24 / E-31 (docs/08 §9.3), default L-23 (Q-F18-4/5/6) docs/04 §9.9.
    # E-41 (docs/08 §11.6, docs/04 §12.4, L-51, keputusan 18-15).
    "depends": [
        "point_of_sale",
        "efn_core",
    ],
    # passlib sudah dependensi Odoo (hash password res.users); dipakai untuk hash PIN persetujuan retur.
    "external_dependencies": {"python": ["passlib"]},
    "data": [
        "security/ir.model.access.csv",
        "security/efn_pos_return_approval_security.xml",
        "data/ir_config_parameter.xml",
        "views/efn_pos_return_approval_views.xml",
        "views/efn_pos_return_pin_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "efn_pos_refund_origin/static/src/**/*",
        ],
        "web.assets_tests": [
            "efn_pos_refund_origin/static/tests/tours/**/*",
        ],
    },
    # SEC-122 (L-25j): terpasang otomatis begitu point_of_sale + efn_core ada (semua DB POS EFN), tanpa
    # bergantung pada modul konfigurasi functional; kontrol E-31/E-41/SEC-113/116/117 tidak bisa absen diam-diam.
    "auto_install": True,
    "installable": True,
    "application": False,
}
