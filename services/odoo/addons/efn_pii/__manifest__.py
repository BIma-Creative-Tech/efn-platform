{
    "name": "EFN PII (enkripsi kolom data pribadi Odoo, E-38a)",
    "summary": "ADR-036 §0: partner perorangan terlindungi sejak dibuat; nama/alamat/kota/kode pos/HP/email/NPWP "
    "dienkripsi AES-256-GCM (efnpii2, AAD terikat id) + blind index + token nama. Belum untuk produksi.",
    "version": "19.0.2.0.0",
    "category": "EFN",
    "author": "Era Food and Nourishment",
    # LGPL-3. Tanpa dependency modul pihak ketiga (ADR-031, CLAUDE.md).
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # mail: email_normalized + tracking; phone_validation: phone_sanitized/phone_mobile_search (ADR-036 §1.3).
    # efn_core: normalisasi HP (normalize_member_ref). POS/hr/l10n_id lewat modul jembatan efn_pii_*.
    "depends": [
        "base",
        "mail",
        "phone_validation",
        "efn_core",
    ],
    "data": [],
    "external_dependencies": {"python": ["cryptography"]},
    "installable": True,
    "application": False,
}
