{
    "name": "EFN Online - Transfer bank + bukti (Verifikator)",
    "summary": "G26 (K25-07): pesanan online dibayar transfer ke rekening PT penjual, pelanggan mengunggah bukti, "
    "staf Verifikator Pembayaran menyetujui/menolak di Odoo sebelum pesanan diproses.",
    "version": "19.0.1.0.0",
    "category": "EFN/Sales",
    "author": "Era Food and Nourishment",
    # LGPL-3. Tanpa dependency modul pihak ketiga (ADR-031). Kontrak: docs/04_SERVICE_CONTRACTS.md §17 (L-95..L-100).
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    "depends": ["efn_online_order", "efn_role", "efn_audit", "account"],
    "data": [
        "security/efn_online_transfer_security.xml",
        "security/ir.model.access.csv",
        "data/efn_audit_rule_data.xml",
        "wizard/efn_online_transfer_decide_views.xml",
        "views/efn_online_transfer_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
}
