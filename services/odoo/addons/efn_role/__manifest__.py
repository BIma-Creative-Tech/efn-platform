{
    "name": "EFN Role & Segregation of Duties",
    "summary": (
        "Peran per PT (jabatan -> himpunan grup), pemisahan tugas (SoD) ditegakkan pada grup efektif, "
        "pengecualian SoD oleh Superadmin, dan review akses bulanan (snapshot) per PT."
    ),
    "version": "19.0.1.0.0",
    "category": "EFN",
    "author": "Era Food and Nourishment",
    # LGPL-3. Tanpa dependency OCA/Cybrosys (ADR-031). OCA `base_user_role` (LGPL-3) hanya dipelajari sebagai
    # referensi desain (peran = himpunan grup, baris peran bertanggal); tidak ada kode yang disalin. Lihat README.
    # Kontrak: docs/04_SERVICE_CONTRACTS.md §13.3 + §13.7 L-64, L-65. Spesifikasi: docs/08_ODOO_FIT_GAP.md §12.3-§12.5.
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # hr: hr.job (jabatan) + hr.employee (sinkron peran dari jabatan). mail: aktivitas ke auditor/Superadmin.
    # efn_audit: grup Auditor + aturan audit. Grup modul lain (point_of_sale, sales_team, account,
    # efn_loyalty_accounting, purchase) dirujuk OPSIONAL lewat xmlid (env.ref raise_if_not_found=False).
    "depends": [
        "hr",
        "mail",
        "efn_audit",
    ],
    "data": [
        "security/efn_role_groups.xml",
        "security/ir.model.access.csv",
        "security/efn_role_security.xml",
        "data/efn_audit_rule_data.xml",
        "data/ir_cron.xml",
        "views/efn_role_views.xml",
        "views/efn_sod_views.xml",
        "views/efn_access_review_views.xml",
        "views/efn_role_menus.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
}
