{
    "name": "EFN Histori ESB (arsip read-only)",
    "summary": "G43/G65 (ADR-042 §0b, docs/04 §27.8/§27.13): arsip histori sistem lama ESB Core di Odoo — aliran "
    "produk harian (input forecast), penjualan harian per menu, baris penjualan, dan baris pembelian — read-only, "
    "multi-company, BUKAN transaksi Odoo (tanpa pos.order/purchase.order/account.move/stock.move). Hook forecast "
    "memakai aliran legacy untuk hari sebelum cutover.",
    "version": "19.0.1.1.0",
    "category": "EFN/Inventory",
    "author": "Era Food and Nourishment",
    # LGPL-3. Tanpa dependency modul pihak ketiga (ADR-031, CLAUDE.md).
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # point_of_sale: menu Point of Sale > Reporting + grup POS manager (penjualan per menu); menarik `account`
    # (grup account.group_account_readonly untuk analis). efn_demand_forecast: hook `_efn_legacy_outflow`.
    # purchase (G65, fit-gap G65 §1.2): grup purchase.group_purchase_user + menu root Purchase untuk histori pembelian.
    "depends": ["efn_core", "stock", "point_of_sale", "purchase", "efn_demand_forecast"],
    "data": [
        "security/efn_legacy_history_security.xml",
        "security/ir.model.access.csv",
        "data/ir_config_parameter.xml",
        "views/efn_legacy_flow_daily_views.xml",
        "views/efn_legacy_sales_views.xml",
        "views/efn_legacy_purchase_views.xml",
        "views/menus.xml",
    ],
    "installable": True,
    "application": False,
}
