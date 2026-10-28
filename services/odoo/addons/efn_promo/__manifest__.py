{
    "name": "EFN Promo - banner slider, pop-up & push",
    "summary": "G27: banner slider hero dan pop-up promo Super-App (web & app) dikelola tim marketing di Odoo; "
    "push promo hanya ke member ber-consent marketing (dikirim BFF). RPC feed/gambar/klaim push untuk BFF.",
    "version": "19.0.1.0.0",
    "category": "EFN/Marketing",
    "author": "Era Food and Nourishment",
    # LGPL-3. Tanpa dependency modul pihak ketiga (ADR-031). Kontrak: docs/04_SERVICE_CONTRACTS.md §18.
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    "depends": ["mail", "product", "efn_core"],
    "data": [
        "security/efn_promo_security.xml",
        "security/ir.model.access.csv",
        "views/efn_promo_views.xml",
    ],
    "installable": True,
    "application": False,
}
