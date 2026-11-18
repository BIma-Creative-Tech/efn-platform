{
    "name": "EFN Dashboard Insight (SSO akun Odoo)",
    "summary": "G42 (ADR-041 Proposed, docs/04 §26): menu 'Dashboard Insight' membuka insight-web dengan token SSO "
    "eit1 sekali pakai (HMAC-SHA256, umur 60 dtk) berisi cakupan PT user; dikirim lewat POST form, bukan query.",
    "version": "19.0.1.0.0",
    "category": "EFN",
    "author": "Era Food and Nourishment",
    # LGPL-3. Tanpa dependency modul pihak ketiga (ADR-031, CLAUDE.md). `web` ikut lewat efn_core -> base_setup.
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    "depends": ["efn_core"],
    "data": [
        "security/efn_insight_security.xml",
        "views/efn_insight_actions.xml",
        "views/efn_insight_templates.xml",
    ],
    # static/src/js/insight_autosubmit.js sengaja TIDAK didaftarkan di bundle aset: dimuat langsung oleh halaman
    # controller lewat route statis /efn_insight/static/... (CSP script-src 'self', tanpa inline script).
    "installable": True,
    "application": False,
}
