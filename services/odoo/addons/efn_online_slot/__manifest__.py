{
    "name": "EFN Online - Slot ambil pre-order",
    "summary": "W-36 / G23: pre-order kue/hampers dengan jadwal ambil. Slot per outlet dari jam buka + kapasitas, "
    "disimpan di sale.order.commitment_date standar; RPC efn_online_slots untuk BFF.",
    "version": "19.0.1.0.0",
    "category": "EFN/Sales",
    "author": "Era Food and Nourishment",
    # LGPL-3. Tanpa dependency modul pihak ketiga (ADR-031). Kontrak: docs/04_SERVICE_CONTRACTS.md §15.4, §15.9 L-87.
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    "depends": ["efn_online_order"],
    "data": ["views/efn_online_outlet_views.xml"],
    "installable": True,
    "application": False,
}
