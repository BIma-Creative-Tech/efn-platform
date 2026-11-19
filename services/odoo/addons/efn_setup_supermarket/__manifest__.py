{
    "name": "EFN Setup - Supermarket (company, DC, kategori FEFO, ambang kedaluwarsa, assortment & jadwal demo)",
    "summary": (
        "Konfigurasi data-only PT Supermarket Demo (SMD, PLACEHOLDER). Tahap A: company + CoA l10n_id/PPN, DC SMDDC, "
        "kategori FEFO, nomenklatur GS1 + fallback; demo 5 outlet dipasok DC. Tahap B: ambang near-expiry/markdown/"
        "sisa umur minimum per kategori (placeholder Q-SM28/Q-SM9), DC assortment; demo capability outlet, kebijakan "
        "assortment, zona + cycle count + putaway, jadwal kirim DC -> outlet, user demo per persona."
    ),
    # G44 tahap A + G45 tahap B (docs/04 §28.1, §28.4, §28.5, §28.5a; fit-gap docs/fit-gap/G45_SUPERMARKET.md S-01..S-16,
    # §5). ADR-045 (DC satu PT + assortment, Proposed), ADR-046 (replenishment, Proposed), ADR-031 (tanpa modul pihak
    # ketiga). Risiko: R-L14 (struktur DC satu PT vs perizinan gudang/PPN), R-L03 (tarif pajak placeholder), R-L07
    # (traceability lot).
    # 19.0.2.0.0: tahap B; post_init_hook dihapus (efn_is_dc di XML); migrations/19.0.2.0.0 untuk DB tahap A.
    "version": "19.0.2.0.0",
    "category": "EFN/Setup",
    "author": "Era Food and Nourishment",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # Semua depends = modul CE 19 standar (terverifikasi ada di odoo/odoo 19.0) atau efn_*:
    # - barcodes_gs1_nomenclature: record default_gs1_nomenclature (is_gs1_nomenclature=True) [V 18 Nov 2026].
    # - product_expiry: product.removal removal_fefo (dipakai juga efn_setup_mrp) [R].
    # - purchase_stock: rute Buy DC (buy_to_resupply) untuk usulan PO DC -> supplier (S-13); grup purchase buyer demo.
    # - efn_setup_pos: nomenklatur EFN Retail (IDR) sebagai fallback POS (fit-gap §2 butir 2).
    # - efn_assortment: stock.warehouse.efn_is_dc/efn_capability_ids/efn_assortment_policy, efn.assortment.line,
    #   res.users.efn_outlet_warehouse_ids (tahap B).
    # - efn_replenishment: efn.delivery.schedule + grup kepala toko/buyer (tahap B, demo).
    # - efn_expiry_monitor: field kategori efn_near_expiry_days/efn_markdown_days/efn_min_receipt_days + grup
    #   group_markdown_manager/group_expiry_override (tahap B; kontrak docs/04 §28.5a, dibangun efn-odoo-engineer G45).
    "depends": [
        "stock",
        "purchase_stock",
        "product_expiry",
        "point_of_sale",
        "barcodes_gs1_nomenclature",
        "l10n_id",
        "base_vat",
        "efn_core",
        "efn_setup_company",
        "efn_setup_pos",
        "efn_assortment",
        "efn_replenishment",
        "efn_expiry_monitor",
    ],
    "data": [
        # Urutan penting: company + CoA (try_loading) dulu, lalu gudang DC (butuh lokasi transit company).
        "data/res_company_data.xml",
        "data/stock_config_data.xml",
        "data/product_category_data.xml",
        # Tahap B: ambang per kategori (file terpisah agar migrasi bisa menerapkannya ulang tanpa menyentuh nama).
        "data/product_category_expiry_data.xml",
    ],
    "demo": [
        "demo/res_company_demo.xml",
        "demo/stock_warehouse_demo.xml",
        # Tahap B (urutan: capability/kebijakan -> zona/putaway -> jadwal -> user -> parameter).
        "demo/efn_assortment_demo.xml",
        "demo/stock_location_demo.xml",
        "demo/efn_replenishment_demo.xml",
        "demo/res_users_demo.xml",
        "demo/ir_config_parameter_demo.xml",
    ],
    "installable": True,
    "application": False,
}
