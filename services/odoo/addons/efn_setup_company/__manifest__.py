{
    "name": "EFN Setup - Company & Pajak",
    "summary": (
        "Konfigurasi data-only: 4 company (Holding EFN, PT Grand Lucky, PT Paris Baguette, FARM), IDR, CoA l10n_id; "
        "PB1 configurable (grup + default + fiscal position per kota/layanan); pembulatan pajak per dokumen."
    ),
    # G20 (keputusan pemilik proyek 18 Okt 2026): 18-1 PB1 Non-Dine-in 0 %, 18-6 round_globally eksplisit,
    # 18-27 nama tampilan company (struktur entitas Bacha = rencana, docs/08 §11.3).
    "version": "19.0.1.6.0",
    "category": "EFN/Setup",
    "author": "Era Food and Nourishment",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # ADR-001 (satu DB multi-company, Proposed), ADR-019 (unified cart / agen penagih, Proposed).
    # Data-only: tidak ada model. Satu hook kecil (hooks.py, W-03): presisi IDR Rp1 sebelum jurnal pertama.
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
        # G19 W-14: SC 5% dine-in + fiscal position per layanan (dine-in / non-dine-in), setelah PB1 default.
        "data/account_tax_fnb_service_data.xml",
        # G17-QA-04: label pajak ke pelanggan (invoice_label + nama grup), setelah CoA dimuat.
        "data/account_tax_label_data.xml",
        # G20 18-6: tax_calculation_rounding_method = round_globally eksplisit di semua PT (noupdate).
        "data/account_tax_rounding_data.xml",
    ],
    "demo": [
        "demo/account_tax_demo.xml",
    ],
    "pre_init_hook": "pre_init_hook",
    "installable": True,
    "application": False,
}
