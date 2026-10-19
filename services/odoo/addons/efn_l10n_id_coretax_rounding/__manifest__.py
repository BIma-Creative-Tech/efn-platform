{
    "name": "EFN - Pembulatan PPN Coretax (selisih Rp1 per faktur)",
    "summary": "E-40 (W-34, 18-5): PPN faktur yang diekspor Coretax mengikuti rumus Coretax per baris; selisih "
    "terhadap jurnal PPN Odoo dibukukan otomatis ke akun Selisih Pembulatan Pajak per PT (piutang tidak berubah).",
    "version": "19.0.1.0.0",
    "category": "EFN/Accounting",
    "author": "Era Food and Nourishment",
    # LGPL-3. Tanpa dependency modul pihak ketiga (ADR-031): hanya Odoo CE 19 + efn_*.
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # Spesifikasi: docs/08_ODOO_FIT_GAP.md §11.2 (E-40) butir 1-6; kontrak docs/04_SERVICE_CONTRACTS.md §13.4.
    # ADR-027 (pajak dari Odoo, tanpa tarif di kode), ADR-031. Rumus Coretax TIDAK ditulis ulang: dipanggil dari
    # l10n_id_efaktur_coretax (`account.move.line._l10n_id_coretax_build_invoice_line_vals`).
    "depends": [
        "l10n_id_efaktur_coretax",
        "efn_core",
    ],
    "data": [
        "views/res_config_settings_views.xml",
        "views/account_move_views.xml",
    ],
    "installable": True,
    "application": False,
}
