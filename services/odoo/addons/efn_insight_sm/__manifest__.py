{
    "name": "EFN Insight Supermarket (persona, cakupan outlet, definisi KPI per PT)",
    "summary": "G47 (ADR-041 §3.3-§3.4 Proposed, docs/04 §28.13, fit-gap docs/fit-gap/G47_INSIGHT_SM.md): "
    "pengelola definisi KPI (grup persona dimiliki efn_insight sejak G52); field kategori dikelola user; cakupan "
    "wids/persona/cats untuk token eit2/eit3; "
    "model efn.insight.sm.kpi (39 kunci, validasi registri tertutup, teraudit, multi-company) dengan baris default "
    "per PT supermarket.",
    # 19.0.2.0.0 (G52, docs/04 §28.15.2, L-239): 5 grup persona + privilege dipindah ke efn_insight (xmlid name tetap;
    # pre-migrate rename ir_model_data.module, sama dengan efn_insight 19.0.3.0.0). kpi_admin + model KPI tetap di sini.
    # Hook menerima G' dari controller. Upgrade wajib `-u efn_insight,efn_insight_sm` satu perintah.
    "version": "19.0.2.0.0",
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
