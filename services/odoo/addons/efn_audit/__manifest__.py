{
    "name": "EFN Audit Log",
    "summary": (
        "Log audit nilai lama/baru per aturan model (create/write/unlink), PII ter-mask, "
        "immutable, multi-company, retensi otomatis. Pengganti OCA auditlog (ADR-031)."
    ),
    "version": "19.0.1.1.0",
    "category": "EFN",
    "author": "Era Food and Nourishment",
    # LGPL-3. Tanpa dependency OCA/Cybrosys (ADR-031). OCA `auditlog` (AGPL-3) dan Cybrosys
    # `user_audit` (LGPL-3) hanya dipelajari sebagai referensi desain; tidak ada kode yang disalin
    # (lihat README bagian Referensi). Spesifikasi: docs/08_ODOO_FIT_GAP.md §5.1 (F-17).
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # efn_core: mask_phone (UU PDP). Aturan per model (product/stock/loyalty) ada di efn_setup_audit.
    "depends": [
        "base",
        "efn_core",
    ],
    "data": [
        "security/efn_audit_groups.xml",
        "security/ir.model.access.csv",
        "security/efn_audit_security.xml",
        "views/efn_audit_log_views.xml",
        "views/efn_audit_rule_views.xml",
        "views/efn_audit_menus.xml",
    ],
    "installable": True,
    "application": False,
}
