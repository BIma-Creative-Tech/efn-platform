{
    "name": "EFN Produksi Central Kitchen (mode produksi, resupply, waste, yield)",
    "summary": "G28 (K25-08, K25-12): mode produksi per produk/CK (pesanan outlet MTO dan/atau forecast MTS), "
    "resupply outlet dari CK, scrap beralasan dengan nilai waste, yield & varians konsumsi MO dengan ambang "
    "peringatan, by-product untuk pemecahan bahan besar.",
    "version": "19.0.1.0.0",
    "category": "EFN/Manufacturing",
    "author": "Era Food and Nourishment",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # ADR-018 (kitting = BoM Manufacture), K25-08 (waste + yield), K25-12 (dua mode produksi). Semua di atas fitur CE:
    # stock.scrap + stock.scrap.reason.tag, rute MTO + Manufacture, resupply_wh_ids, mrp.bom.byproduct.
    "depends": ["mrp", "stock_account", "mrp_account", "mail", "efn_core", "efn_kitting"],
    "data": [
        "security/efn_ck_production_security.xml",
        "security/ir.model.access.csv",
        "data/scrap_reason_data.xml",
        "views/res_company_views.xml",
        "views/product_views.xml",
        "views/stock_warehouse_views.xml",
        "views/stock_scrap_views.xml",
        "views/mrp_production_views.xml",
        "views/efn_ck_report_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
    "application": False,
}
