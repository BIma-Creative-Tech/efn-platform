{
    "name": "EFN Setup - Supermarket (tahap A: company, DC, kategori FEFO, nomenklatur)",
    "summary": (
        "Konfigurasi data-only PT Supermarket Demo (SMD, PLACEHOLDER): company + CoA l10n_id/PPN, gudang DC SMDDC, "
        "kategori supermarket FEFO, nomenklatur GS1 + fallback EFN Retail; demo 5 outlet SMD01..SMD05 dipasok DC."
    ),
    # G44 tahap A (docs/04 §28.1, fit-gap docs/fit-gap/G45_SUPERMARKET.md S-01..S-07). ADR-045 (DC satu PT +
    # assortment, Proposed), ADR-031 (tanpa modul pihak ketiga). Risiko: R-L14 (struktur DC satu PT vs perizinan
    # gudang/PPN), R-L03 (tarif pajak placeholder). Tahap B (G45) menambah depends efn_assortment/efn_replenishment/
    # efn_expiry_monitor + data ambang/capability/jadwal; SENGAJA tidak di sini agar seed G48 bisa jalan lebih dulu.
    "version": "19.0.1.0.0",
    "category": "EFN/Setup",
    "author": "Era Food and Nourishment",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # Semua depends = modul CE 19 standar (terverifikasi ada di odoo/odoo 19.0) atau efn_*:
    # - barcodes_gs1_nomenclature: record default_gs1_nomenclature (is_gs1_nomenclature=True) [V 18 Nov 2026].
    # - product_expiry: product.removal removal_fefo (dipakai juga efn_setup_mrp) [R].
    # - purchase_stock: rute Buy DC (buy_to_resupply) untuk usulan PO DC -> supplier (S-13).
    # - efn_setup_pos: nomenklatur EFN Retail (IDR) sebagai fallback POS (fit-gap §2 butir 2).
    # efn_core HARUS memuat kode Selection 'SMD' (+ pemetaan SMD -> retail) — dikerjakan efn-odoo-engineer (§12.1).
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
    ],
    "data": [
        # Urutan penting: company + CoA (try_loading) dulu, lalu gudang DC (butuh lokasi transit company).
        "data/res_company_data.xml",
        "data/stock_config_data.xml",
        "data/product_category_data.xml",
    ],
    "demo": [
        "demo/res_company_demo.xml",
        "demo/stock_warehouse_demo.xml",
    ],
    # QA-1 (G44): set efn_is_dc pada SMDDC bila efn_assortment sudah terpasang (tanpa depends baru).
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
}
