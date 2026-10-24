{
    "name": "EFN Setup - Struktur Organisasi",
    "summary": (
        "Konfigurasi data-only: templat departemen & jabatan (hr CE) per PT untuk 6 entitas EFN, "
        "tanpa nama orang. Dasar pemetaan jabatan -> peran akses (efn_role, W-31)."
    ),
    # 19.0.1.0.0 (G21 W-31, docs/04 §13.3 no. 1, keputusan 18-24/18-40, L-64).
    # 19.0.1.1.0 (G24 W-40, docs/04 §16.1): templat departemen & jabatan PT ke-7 WTZ (sufiks _wtz; noupdate, dibuat
    # juga saat upgrade karena xmlid baru).
    "version": "19.0.1.1.0",
    "category": "EFN/Setup",
    "author": "Era Food and Nourishment",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # ADR-001 (satu DB multi-company), ADR-031 (tanpa dependency vendor). Risiko: R-S14 (hak akses menumpuk),
    # R-S10 (insider). Spesifikasi & matriks peran: docs/08_ODOO_FIT_GAP.md §12.
    # depends terverifikasi di odoo/odoo 19.0 (2026-10-19): hr (hr.department, hr.job).
    # Data-only: tidak ada model, tidak ada Python selain test.
    "depends": [
        "hr",
        "efn_setup_company",
    ],
    "data": [
        "data/hr_department_data.xml",
        # Harus SETELAH departemen (department_id).
        "data/hr_job_data.xml",
    ],
    "installable": True,
    "application": False,
}
