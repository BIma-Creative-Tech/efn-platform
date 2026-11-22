{
    "name": "EFN Dashboard Insight (SSO akun Odoo)",
    "summary": "G42 (ADR-041 Proposed, docs/04 §26): menu 'Dashboard Insight' membuka insight-web dengan token SSO "
    "eit1 sekali pakai (HMAC-SHA256, umur 60 dtk) berisi cakupan PT user; dikirim lewat POST form, bukan query.",
    # 19.0.2.0.0 (G47, docs/04 §28.13, ADR-041 §3.4): token eit2 (cakupan supermarket wids/persona/cats) default;
    # eit1 untuk rollback (EFN_INSIGHT_TOKEN_VERSION=1); hook privat res.users._efn_insight_sm_scope (kosong) yang
    # ditimpa efn_insight_sm; info no_outlet/scope_too_large; audit v2 (jumlah wids/cats saja).
    # 19.0.3.0.0 (G52, docs/04 §28.15, ADR-041 §3.6): 5 grup persona dipindah dari efn_insight_sm (xmlid name tetap,
    # pre-migrate rename ir_model_data.module); model efn.insight.esb.branch + res.users.efn_insight_esb_branch_ids;
    # token eit3 (+ brs, T1-T8) default; kepala toko F&B store-only hanya cabangnya (pencabutan PT penuh tanpa flag);
    # INSIGHT_FNB_PERSONA_ENABLED; info no_branch. Upgrade wajib `-u efn_insight,efn_insight_sm` satu perintah.
    "version": "19.0.3.0.0",
    "category": "EFN",
    "author": "Era Food and Nourishment",
    # LGPL-3. Tanpa dependency modul pihak ketiga (ADR-031). `web` ikut lewat efn_core -> base_setup.
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    "depends": ["efn_core"],
    "data": [
        "security/efn_insight_security.xml",
        "security/ir.model.access.csv",
        "views/efn_insight_actions.xml",
        "views/efn_insight_templates.xml",
        "views/efn_insight_esb_branch_views.xml",
    ],
    # static/src/js/insight_autosubmit.js sengaja TIDAK didaftarkan di bundle aset: dimuat langsung oleh halaman
    # controller lewat route statis /efn_insight/static/... (CSP script-src 'self', tanpa inline script).
    "installable": True,
    "application": False,
}
