{
    "name": "EFN Online Order (kanal Super-App)",
    "summary": "E-28 + G19 (E-35/E-36/E-37a): kanal penjualan online per PT/brand lewat BFF (/json/2 terfilter): "
    "katalog, quote berpromo, SO idempoten dengan reservasi stok, saga bayar-konfirmasi-ambil, refund per SO, "
    "outbox event bertanda tangan HMAC.",
    "version": "19.0.1.2.0",
    "category": "EFN/Sales",
    "author": "Era Food and Nourishment",
    # LGPL-3. Tanpa dependency modul pihak ketiga (ADR-031): hanya Odoo CE 19 + efn_*.
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # Kontrak: docs/04_SERVICE_CONTRACTS.md §10 (v5.0) + §11 (v6.0, G19; §11 menang atas §10 dan docs/08).
    # ADR-034 (Proposed: kanal online = BFF + /json/2 terfilter), ADR-019 (Proposed + catatan revisi 9 Okt),
    # ADR-020 (poin Odoo tidak dipakai), ADR-026, ADR-027 (pajak dari Odoo, tanpa tarif di kode), ADR-029, ADR-031.
    # point_of_sale hanya untuk `pos.category` (kategori katalog). sale_loyalty (CE) = mesin promo online (E-36).
    # TANPA website*, delivery, payment_*.
    "depends": [
        "sale_stock",
        "sale_loyalty",
        "point_of_sale",
        "mail",
        "efn_core",
    ],
    "data": [
        "security/efn_online_security.xml",
        "security/ir.model.access.csv",
        "data/ir_config_parameter.xml",
        "data/ir_cron.xml",
        "views/efn_online_channel_views.xml",
        "wizard/efn_online_handover_wizard_views.xml",
        "views/efn_online_order_views.xml",
        "views/product_template_views.xml",
        "views/sale_order_views.xml",
        "views/efn_online_menus.xml",
    ],
    # passlib sudah dependensi Odoo (hash password res.users); dipakai untuk hash KDF kode ambil.
    "external_dependencies": {"python": ["requests", "passlib"]},
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
}
