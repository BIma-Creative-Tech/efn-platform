{
    "name": "EFN PII + account",
    "summary": "Mempertahankan enkripsi `res.partner.name` (efn_pii) saat account mendefinisikan ulang field itu.",
    "version": "19.0.1.0.0",
    "category": "EFN",
    "author": "Era Food and Nourishment",
    # LGPL-3. Tanpa dependency modul pihak ketiga (ADR-031, CLAUDE.md).
    "license": "LGPL-3",
    "depends": ["efn_pii", "account"],
    "data": [],
    "auto_install": True,
    "installable": True,
    "application": False,
}
