{
    "name": "EFN Agri (Farming Hub & QC Gate)",
    "summary": "G28 (K25-13, BRD §5.4): komoditas dengan parameter QC yang diatur user, grade -> harga, mitra tani "
    "(lahan, prediksi panen, harga dasar), inspeksi QC input manual yang mengunci transfer QC gudang 3 langkah, "
    "harga beli PO mengikuti grade, traceability lot (asal petani, tanggal panen, sertifikat QC).",
    "version": "19.0.1.0.0",
    "category": "EFN/Inventory",
    "author": "Era Food and Nourishment",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # quality_control = Enterprise. QC gate = penerimaan 3 langkah CE (Input -> QC -> Stock); efn_agri hanya menambah
    # inspeksi & grade. Referensi desain (dibaca, tidak disalin, ADR-031): OCA quality_control_oca (AGPL-3).
    "depends": ["purchase_stock", "product_expiry", "mail", "efn_core"],
    "data": [
        "security/efn_agri_security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence.xml",
        "views/efn_agri_commodity_views.xml",
        "views/efn_agri_farmer_views.xml",
        "views/efn_agri_harvest_views.xml",
        "views/efn_agri_inspection_views.xml",
        "views/stock_views.xml",
        "report/efn_agri_reports.xml",
        "views/menus.xml",
    ],
    "installable": True,
    "application": True,
}
