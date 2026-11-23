-- V-01 (keputusan pemilik proyek 23 Nov 2026): view EFN Insight halaman "Pengunjung" lintas PT (SMD + PT F&B, mis. PB).
-- Pemilik: efn-data-ai-engineer. Dijalankan clickhouse-init.sh setelah insight-access.sql dan insight-sm-access.sql
-- (urutan abjad; role insight_reader, profil, definer efn_insight_definer, database efn_insight, grant SELECT
-- efn_insight.* dibuat di insight-access.sql).
--
-- Pola §26.7: view efn_insight.v_vis_* = SQL SECURITY DEFINER (efn_insight_definer, HOST NONE) dengan kolom EKSPLISIT di
-- atas view semantik efn_dwh.v_vis_* (init/23-v01-visitor-insight.sql). Row policy efn_insight_scope (memuat kunci
-- insight_scope_key) dibuat access/insight-visitor-policy.sh (disourcing clickhouse-init.sh), BUKAN di file ini; init
-- gagal (fail closed) bila ada view efn_insight tanpa policy.
-- Grant definer = penutupan rekursif objek efn_dwh yang dirujuk (diuji services/etl/tests/test_insight_visitor_access.py).
-- Tidak ada fact_visitor_event (per subjek): hanya agregat anonim.

-- ============================================================== Grant definer (minimum)
GRANT SELECT ON efn_dwh.agg_visitor_page_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.agg_visitor_funnel_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.agg_visitor_product_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.agg_visitor_search_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.dim_product TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_vis_page_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_vis_funnel_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_vis_product_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_vis_search_daily TO efn_insight_definer;

-- ============================================================== View efn_insight.v_vis_*
CREATE OR REPLACE VIEW efn_insight.v_vis_page_daily DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT day, company_code, channel, page, visitors, views
FROM efn_dwh.v_vis_page_daily;

CREATE OR REPLACE VIEW efn_insight.v_vis_funnel_daily DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT day, company_code, channel, subjects, viewers, carters, checkout_starters, purchasers, searchers_no_result
FROM efn_dwh.v_vis_funnel_daily;

CREATE OR REPLACE VIEW efn_insight.v_vis_product_daily DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT day, company_code, channel, product_id, product_name, views, viewers, cart_adds, purchases
FROM efn_dwh.v_vis_product_daily;

CREATE OR REPLACE VIEW efn_insight.v_vis_search_daily DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT day, company_code, channel, query, searches, no_result, subjects
FROM efn_dwh.v_vis_search_daily;
