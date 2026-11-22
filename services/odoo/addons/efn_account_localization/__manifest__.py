{
    "name": "EFN Account Localization (voucher, MDR, analitik Outlet)",
    "summary": "G60: Payment Voucher/Receipt + persetujuan 4-eyes pembayaran keluar, estimasi MDR per metode "
    "bayar POS, analitik Outlet pada baris laba-rugi jurnal sesi POS.",
    # 19.0.1.0.0 (G60, docs/fit-gap/G59_G60_ACCOUNTING_PORT.md §G60): port & adaptasi dari odoo-platform
    # `_tenants/custom_levis_localization` (ADR-049); provenance di README.md.
    "version": "19.0.1.0.0",
    "category": "EFN",
    "author": "Era Food and Nourishment",
    # LGPL-3. Hanya CE + efn_core (ADR-031, ADR-049).
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # purchase_stock (bukan purchase saja): `purchase.order.picking_type_id` (default outlet di PO, Q-G60-6) ada di
    # purchase_stock. Bila Q-G60-6 dijawab "tidak perlu", hapus models/purchase_order_line.py + dependency ini.
    "depends": [
        "account",
        "analytic",
        "point_of_sale",
        "stock",
        "purchase_stock",
        "efn_core",
    ],
    "data": [
        "security/ir.model.access.csv",
        "security/efn_account_localization_security.xml",
        "data/analytic_plan_data.xml",
        "data/res_groups_data.xml",
        "reports/paperformat.xml",
        "reports/payment_voucher_templates.xml",
        "reports/payment_report_actions.xml",
        "views/account_payment_views.xml",
        "views/efn_mdr_rate_views.xml",
        "views/pos_session_views.xml",
        "views/stock_warehouse_views.xml",
        "views/res_config_settings_views.xml",
        "wizard/efn_outlet_analytic_wizard_views.xml",
    ],
    "demo": [
        "demo/efn_mdr_rate_demo.xml",
    ],
    "installable": True,
    "application": False,
}
