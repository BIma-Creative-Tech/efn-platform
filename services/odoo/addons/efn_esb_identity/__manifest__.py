{
    "name": "EFN Identitas ESB (shadow user JIT)",
    "summary": "G66b (ADR-051 A1 Proposed, docs/04 §30.17.11): login ESB yang berhasil di modul Produksi diduplikasi "
    "ke Odoo sebagai user internal yang DIARSIPKAN (tanpa password/email, hanya Internal User); peran PP hanya "
    "dicerminkan sebagai data. Dipanggil prodplan lewat /json/2 efn.esb.identity/efn_esb_provision.",
    "version": "19.0.1.0.1",
    "category": "EFN",
    "author": "Era Food and Nourishment",
    # LGPL-3. Tanpa dependency OCA/Cybrosys/Enterprise (ADR-031). Referensi desain: auth_ldap
    # `_get_or_create_user` (CE) — konsep saja, tidak ada kode yang disalin. Lihat README.
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # efn_insight: efn.insight.esb.branch (cabang ESB -> PT), ESB_BRANDS / PP_ROLES / MAX_ID.
    "depends": ["efn_insight"],
    "data": [
        "security/efn_esb_identity_security.xml",
        "security/ir.model.access.csv",
        "data/efn_esb_identity_data.xml",
        "views/efn_esb_identity_views.xml",
    ],
    "installable": True,
    "application": False,
}
