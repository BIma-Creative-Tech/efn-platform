{
    "name": "EFN PII + e-Faktur Coretax (NIK, dokumen pembeli, XML terenkripsi)",
    "summary": "ADR-036 §0.2/§0.8, 18-8: l10n_id_nik & l10n_id_buyer_document_number terenkripsi + blind index; "
    "XML Coretax disimpan efnpii2 dan didekripsi hanya saat diunduh user Accounting. Belum untuk produksi.",
    "version": "19.0.1.0.0",
    "category": "EFN",
    "author": "Era Food and Nourishment",
    # LGPL-3. Tanpa dependency modul pihak ketiga (ADR-031, CLAUDE.md). l10n_id_efaktur_coretax = modul CE (LGPL-3).
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    "depends": ["efn_pii", "l10n_id_efaktur_coretax"],
    "data": [],
    "post_init_hook": "post_init_hook",
    "auto_install": True,
    "installable": True,
    "application": False,
}
