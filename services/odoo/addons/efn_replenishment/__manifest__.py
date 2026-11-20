{
    "name": "EFN Replenishment (usulan order outlet -> DC, qty orderpoint DC -> RFQ)",
    "summary": "G44 (ADR-046 §3.4-§3.5, ADR-045 §3.3, docs/04 §28.4, fit-gap G45 S-12/S-13/S-16): jadwal kirim DC -> "
    "outlet & jadwal supplier, usulan order harian outlet dari forecast v2 + stok live (cap umur simpan, cap rak, "
    "kelipatan pack, filter assortment), approval kepala toko sebelum cut-off (lewat = expired), procurement lewat "
    "orderpoint/rute resupply CE (trigger manual), qty_to_order_manual orderpoint DC -> RFQ draft CE, log bias "
    "append-only, SoD kepala toko vs PO supplier.",
    # 19.0.1.1.0 (G44 perbaikan): SEC-210 batas qty_approved, SEC-213 SoD button_approve, QA-2 jendela dari tanggal
    # proyeksi pertama, QA-3 offset hari order supplier, aktivitas run tanpa baris diterima.
    # 19.0.1.2.0 (G45): constraint jadwal kirim tumpang-tindih per outlet; sisa umur minimum mewarisi kategori induk
    # (helper efn_expiry_monitor, bila terpasang).
    # 19.0.1.2.1 (G47, fit-gap G47 §8): satu privilege per grup (widget hak akses Odoo 19); hak efektif tetap.
    "version": "19.0.1.2.1",
    "category": "EFN/Inventory",
    "author": "Era Food and Nourishment",
    # LGPL-3. Tanpa dependency modul pihak ketiga (ADR-031). OCA stock_request / stock_warehouse_calendar /
    # stock_demand_estimate hanya referensi konsep; tidak ada kode yang disalin.
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    "depends": [
        "stock",
        "purchase_stock",
        "product_expiry",
        "mail",
        "efn_core",
        "efn_audit",
        "efn_demand_forecast",
        "efn_assortment",
    ],
    "data": [
        "security/efn_replenishment_security.xml",
        "security/ir.model.access.csv",
        "data/efn_replenishment_data.xml",
        "views/efn_replenishment_views.xml",
    ],
    "installable": True,
    "application": False,
}
