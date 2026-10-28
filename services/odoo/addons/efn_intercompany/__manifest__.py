{
    "name": "EFN Antar-PT (Inter-company)",
    "summary": "G28 (F-03/W-15, docs/08 §5.2): PO antar-PT -> SO di PT penjual (user teknis), "
    "faktur -> tagihan draft di PT pembeli, harga transfer cost-plus per pasangan PT, pembatalan berantai.",
    "version": "19.0.1.1.0",  # G29b: batal SO tanpa hak Pembelian (stock_move.py)
    "category": "EFN/Accounting",
    "author": "Era Food and Nourishment",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # CE 19 tidak membuat dokumen lawan antar-PT (aturan inter-company = Enterprise, docs/06 #13). Referensi desain
    # (dibaca, tidak disalin, ADR-031): OCA purchase_sale_inter_company / account_invoice_inter_company (AGPL-3),
    # Cybrosys inter_company_synchronization (AGPL-3).
    "depends": ["sale_stock", "purchase_stock", "account", "mail", "efn_core"],
    "data": [
        "security/efn_intercompany_security.xml",
        "security/ir.model.access.csv",
        "views/efn_ic_transfer_price_views.xml",
        "views/res_company_views.xml",
        "views/purchase_sale_views.xml",
    ],
    "installable": True,
    "application": False,
}
