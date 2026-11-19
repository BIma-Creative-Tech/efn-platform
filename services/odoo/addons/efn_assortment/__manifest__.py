{
    "name": "EFN Assortment Outlet (restriksi DC -> outlet)",
    "summary": "G44 (ADR-045 Proposed, docs/04 §28.5, fit-gap G45 S-11/S-16): capability outlet (chiller, freezer, izin "
    "alkohol, boleh non-halal), kebijakan open/closed per gudang, baris izin/larangan per produk/kategori + mode pasok "
    "DC/DSD + kapasitas rak, kontrak region supplier, cakupan outlet kepala toko. Ditegakkan saat konfirmasi transfer "
    "DC -> outlet dan di usulan efn_replenishment; override teraudit (tidak untuk alkohol/halal); laporan harian.",
    # 19.0.1.1.0 (G44 perbaikan): SEC-208 guard + audit field assortment gudang/produk; QA-1 post_init_hook DC.
    "version": "19.0.1.1.0",
    "category": "EFN/Inventory",
    "author": "Era Food and Nourishment",
    # LGPL-3. Tanpa dependency modul pihak ketiga (ADR-031). OCA product_assortment hanya referensi konsep.
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    "depends": ["stock", "purchase_stock", "mail", "efn_core", "efn_region", "efn_audit"],
    "data": [
        "security/efn_assortment_security.xml",
        "security/ir.model.access.csv",
        "data/efn_assortment_data.xml",
        "data/efn_audit_rule_data.xml",
        "views/efn_assortment_views.xml",
        "wizard/efn_assortment_override_wizard_views.xml",
    ],
    # QA-1: tandai DC efn_setup_supermarket.warehouse_smd_dc (bila ada) sebagai efn_is_dc.
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
}
