{
    "name": "EFN Insight Supermarket (persona, cakupan outlet, definisi KPI per PT)",
    "summary": "G47 (ADR-041 §3.3-§3.4 Proposed, docs/04 §28.13, fit-gap docs/fit-gap/G47_INSIGHT_SM.md): "
    "5 grup persona Insight SM (Direksi, Category manager, Kepala toko, DC, Marketing) + pengelola definisi KPI, "
    "masing-masing satu privilege; field kategori dikelola user; cakupan wids/persona/cats untuk token eit2; "
    "model efn.insight.sm.kpi (39 kunci, validasi registri tertutup, teraudit, multi-company) dengan baris default "
    "per PT supermarket.",
    "version": "19.0.1.0.0",
    "category": "EFN",
    "author": "Era Food and Nourishment",
    # LGPL-3. Tanpa dependency modul pihak ketiga (ADR-031). OCA base_user_role / mis_builder hanya referensi konsep.
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # Modul jembatan (L-187): efn_insight tetap terpasang di instalasi F&B tanpa modul supermarket.
    "depends": ["stock", "efn_insight", "efn_assortment", "efn_audit"],
    "data": [
        "security/efn_insight_sm_security.xml",
        "security/ir.model.access.csv",
        "data/efn_audit_rule_data.xml",
        "views/res_users_views.xml",
        "views/efn_insight_sm_kpi_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "auto_install": True,
    "installable": True,
    "application": False,
}
