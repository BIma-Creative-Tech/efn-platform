"""post_init_hook efn_insight_sm (G47, docs/04 §28.13.8c): baris default KPI per PT supermarket (SM_CODES)."""


def post_init_hook(env):
    env["efn.insight.sm.kpi"].sudo()._efn_seed_defaults()
