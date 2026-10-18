{
    "name": "EFN PII + POS (E-38a)",
    "summary": "ADR-036 §0: pos.order email/mobile terenkripsi (semua order), partner terlindungi tidak di-preload "
    "ke kasir, pencarian kasir via HP lengkap / nama (token). Belum untuk produksi.",
    "version": "19.0.1.0.0",
    "category": "EFN",
    "author": "Era Food and Nourishment",
    # LGPL-3. Tanpa dependency modul pihak ketiga (ADR-031, CLAUDE.md).
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    "depends": ["efn_pii", "point_of_sale"],
    "data": [],
    "post_init_hook": "post_init_hook",
    "auto_install": True,
    "installable": True,
    "application": False,
}
