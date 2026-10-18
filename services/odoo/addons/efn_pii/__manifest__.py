{
    "name": "EFN PII (PoC enkripsi kolom PII Odoo)",
    "summary": "PoC E-38 / ADR-036: phone/email/vat partner terlindungi dienkripsi AES-256-GCM "
    "(key ring) + blind index HMAC. Belum untuk produksi.",
    "version": "19.0.1.0.0",
    "category": "EFN",
    "author": "Era Food and Nourishment",
    # LGPL-3. Tanpa dependency modul pihak ketiga (ADR-031, CLAUDE.md).
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # mail: email_normalized + tracking; phone_validation: phone_sanitized (ADR-036 §1.3).
    # efn_core: normalisasi HP (normalize_member_ref). efn_privacy/point_of_sale/l10n_id
    # sengaja TIDAK di-depend di PoC (modul E-38 penuh: efn_pii_pos, efn_pii_l10n_id).
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
