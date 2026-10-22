{
    "name": "EFN Restoran - Reservasi meja (BFF)",
    "summary": "W-35 / G23: reservasi meja PB & Bacha Coffee F&B dari Super-App lewat BFF. Ketersediaan dari jam buka "
    "(resource.calendar) + kursi meja pos_restaurant + bentrok reservasi; staf mengelola di Odoo; event ke BFF.",
    "version": "19.0.1.0.0",
    "category": "EFN/Point of Sale",
    "author": "Era Food and Nourishment",
    # LGPL-3. Tanpa dependency modul pihak ketiga (ADR-031). CE 19 tidak punya `pos_appointment` (Enterprise, ADR-013)
    # -> modul custom. Kontrak: docs/04_SERVICE_CONTRACTS.md §15.1-§15.3, §15.8, §15.9 (L-85, L-86, L-90).
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    "depends": [
        "pos_restaurant",
        "efn_core",
        "efn_audit",
        "efn_online_order",
        "efn_pos_bff_order",
    ],
    "data": [
        "security/efn_table_booking_security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence.xml",
        "data/ir_cron.xml",
        "views/efn_table_booking_views.xml",
    ],
    "external_dependencies": {"python": ["requests"]},
    "installable": True,
    "application": False,
}
