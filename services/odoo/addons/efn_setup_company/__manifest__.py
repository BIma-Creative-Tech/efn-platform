{
    "name": "EFN Setup - Company & Pajak",
    "summary": (
        "Konfigurasi data-only: 4 company (EFN/RTL/FNB/FARM), IDR, CoA l10n_id; "
        "PB1 configurable (grup + default + fiscal position per kota)."
    ),
    "version": "19.0.1.2.0",
    "category": "EFN/Setup",
    "author": "Era Food and Nourishment",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # ADR-001 (satu DB multi-company, Proposed), ADR-019 (unified cart / agen penagih, Proposed).
    # Data-only: tidak ada model/logika Python. Lihat README.md.
    # depends terverifikasi di odoo/odoo 19.0 (2026-10-05): l10n_id (account, base_iban, base_vat).
    "depends": [
        "efn_core",
        "l10n_id",
        # R3 (no_egress.xml): cron yang dinonaktifkan milik modul ini (sudah ikut l10n_id/efn_core).
        "base_vat",
        "mail",
    ],
    "data": [
        "data/res_company_data.xml",
        # R3: cron standar yang memanggil layanan internet Odoo S.A. dinonaktifkan.
        "data/no_egress.xml",
        # Harus SETELAH res_company_data.xml (butuh CoA/fiscal country dari try_loading).
        "data/account_tax_pb1_data.xml",
    ],
    "demo": [
        "demo/account_tax_demo.xml",
    ],
    "installable": True,
    "application": False,
}
