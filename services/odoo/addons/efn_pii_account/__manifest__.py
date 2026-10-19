{
    "name": "EFN PII + account",
    "summary": "efn_pii untuk modul account: kelas field res.partner.name & rekening bank dipulihkan, "
    "invoice_partner_display_name faktur partner terlindungi terenkripsi (SEC-153/AC-10).",
    "version": "19.0.2.0.0",
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
