{
    "name": "EFN Setup - Kanal Online Supermarket (SMD, pickup + pre-order)",
    "summary": (
        "Konfigurasi data-only kanal Super-App PT Supermarket Demo (kode kanal/brand SMD): kategori katalog online "
        "(pos.category SMD), partner generik & tim Super-App SMD; demo: jurnal titipan, pricelist, user teknis "
        "efn_online_smd, kanal SMD mode confirm, 5 outlet pickup (gudang toko SMD01..SMD05) dengan slot pre-order."
    ),
    # Keputusan user 23 Nov 2026: SMD punya kanal belanja online sendiri (ambil di toko + pre-order berjadwal), TANPA
    # dine-in/QR meja/reservasi. Kontrak: docs/04 §10 (E-28), §12.1 (kode kanal = kode PT), §15.4 (slot), §28 (SMD,
    # §28.6 outlet_code <= 16). ADR-034 (kanal online via BFF, Proposed), ADR-019 A1 (jurnal titipan Holding),
    # ADR-045 (SMD, Proposed), ADR-031 (tanpa modul pihak ketiga).
    # Modul terpisah dari efn_setup_supermarket agar modul inti SMD tidak bergantung pada modul online, dan TIDAK
    # bergantung pada efn_setup_online (partner/tim/kanal GL/PB/BCR/BCF/WTZ = data brand lain; SMD independen).
    "version": "19.0.1.0.0",
    "category": "EFN/Setup",
    "author": "Era Food and Nourishment",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # Semua depends = modul CE 19 standar atau efn_*:
    # - point_of_sale: pos.category (kategori katalog online = product.template.pos_categ_ids[:1]).
    # - sale_stock: crm.team (sales_team), pricelist, SO online dari gudang outlet.
    # - resource: resource.calendar jam ambil / slot.
    # - efn_online_order: efn.online.channel / efn.online.outlet / grup user teknis.
    # - efn_online_slot: field slot_* outlet (pre-order berjadwal, generik ritel/F&B).
    # - efn_setup_supermarket: company_smd (data) + warehouse_smd01..05 (demo).
    "depends": [
        "point_of_sale",
        "sale_stock",
        "resource",
        "efn_online_order",
        "efn_online_slot",
        "efn_setup_supermarket",
    ],
    "data": [
        "data/pos_category_data.xml",
        "data/efn_online_smd_data.xml",
    ],
    "demo": [
        "demo/efn_online_smd_demo.xml",
    ],
    "installable": True,
    "application": False,
}
