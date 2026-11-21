-- G47 (docs/04 §28.13.7/§28.13.9 v20.3, ADR-041 §3.3-§3.4 Proposed): view EFN Insight supermarket untuk insight-web.
-- File terpisah dari insight-access.sql (view ESB G42 + test esb-extract yang mengunci isinya). Dijalankan
-- clickhouse-init.sh setelah insight-access.sql (urutan abjad: role insight_reader, profil, user efn_insight_definer,
-- database efn_insight, dan grant SELECT efn_insight.* dibuat di sana).
--
-- Pola §26.7: view efn_insight.v_sm_* = SQL SECURITY DEFINER (efn_insight_definer, HOST NONE) dengan kolom EKSPLISIT di
-- atas view semantik efn_dwh.v_sm_* (17-g47-insight-sm.sql). Row policy efn_insight_scope kelas W/C/F memuat kunci rahasia
-- insight_scope_key -> dibuat access/insight-sm-policy.sh (disourcing clickhouse-init.sh), BUKAN di file ini. Init gagal
-- (fail closed) bila ada view efn_insight tanpa policy itu.
--
-- Grant definer = penutupan rekursif objek efn_dwh yang dirujuk view di bawah (diuji tests/test_insight_sm_access.py di
-- services/etl). Tanpa efn_esb / fact_esb_* / fact_legacy_sales_daily (L-196), tanpa mart GL/audit/loyalty.

-- ============================================================== Grant definer (minimum)
GRANT SELECT ON efn_dwh.dim_sm_warehouse TO efn_insight_definer;
GRANT SELECT ON efn_dwh.dim_online_outlet TO efn_insight_definer;
GRANT SELECT ON efn_dwh.dim_sm_kpi_config TO efn_insight_definer;
GRANT SELECT ON efn_dwh.dim_sm_supplier TO efn_insight_definer;
GRANT SELECT ON efn_dwh.dim_product TO efn_insight_definer;
GRANT SELECT ON efn_dwh.dim_outlet TO efn_insight_definer;
GRANT SELECT ON efn_dwh.dim_scrap_reason TO efn_insight_definer;
GRANT SELECT ON efn_dwh.fact_sales_line TO efn_insight_definer;
GRANT SELECT ON efn_dwh.fact_scrap TO efn_insight_definer;
GRANT SELECT ON efn_dwh.fact_forecast_accuracy TO efn_insight_definer;
GRANT SELECT ON efn_dwh.fact_stock_snapshot TO efn_insight_definer;
GRANT SELECT ON efn_dwh.stock_snapshot_run TO efn_insight_definer;
GRANT SELECT ON efn_dwh.agg_sm_stock_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.fact_replenishment TO efn_insight_definer;
GRANT SELECT ON efn_dwh.fact_replenishment_bias TO efn_insight_definer;
GRANT SELECT ON efn_dwh.fact_dc_fill_rate TO efn_insight_definer;
GRANT SELECT ON efn_dwh.fact_dc_purchase_line TO efn_insight_definer;
GRANT SELECT ON efn_dwh.fact_lot_expiry TO efn_insight_definer;
GRANT SELECT ON efn_dwh.fact_lot_markdown TO efn_insight_definer;
GRANT SELECT ON efn_dwh.agg_sm_expiry_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.competitor_prices TO efn_insight_definer;
GRANT SELECT ON efn_dwh.agg_visitor_product_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.agg_visitor_funnel_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.agg_visitor_search_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.fact_sm_segment_run TO efn_insight_definer;
GRANT SELECT ON efn_dwh.fact_campaign_result TO efn_insight_definer;
GRANT SELECT ON efn_dwh.etl_job_run TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_kpi_config TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_kpi_settings TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_category TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_unit_cost TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_sales_product_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_replenishment_current TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_warehouse_list TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_product_list TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_sales_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_outlet_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_margin_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_gmroi_monthly TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_waste_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_forecast_accuracy_weekly TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_stock_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_oos_product_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_stock_value_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_stock_critical TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_replenishment TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_replenishment_line TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_replenishment_bias_weekly TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_dc_fill_rate TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_dc_purchase TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_lot_expiry TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_expiry_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_markdown TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_price_index TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_price_coverage TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_price_signal TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_visitor_funnel_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_search_terms TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_segment_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_campaign_result TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_freshness TO efn_insight_definer;
-- G51 (docs/design/G51_SMD_E2E_LEAD_SPEC.md §B2; view efn_dwh di init/18-g51-insight-sm.sql).
GRANT SELECT ON efn_dwh.fact_pos_payment TO efn_insight_definer;
GRANT SELECT ON efn_dwh.fact_forecast_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.dim_forecast_run TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_payment_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_product_sales_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_forecast_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_sm_lot_stock TO efn_insight_definer;

-- ============================================================== View efn_insight.v_sm_* (tabel §28.13.9)
CREATE OR REPLACE VIEW efn_insight.v_sm_warehouse_list DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT company_id, company_code, warehouse_id, warehouse_code, warehouse_name, outlet_type, active
FROM efn_dwh.v_sm_warehouse_list;

CREATE OR REPLACE VIEW efn_insight.v_sm_product_list DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT company_id, product_id, product_tmpl_id, default_code, product_name, categ_id, category, categ_parent_path, active,
       shelf_life_days
FROM efn_dwh.v_sm_product_list;

CREATE OR REPLACE VIEW efn_insight.v_sm_sales_daily DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT day, company_id, warehouse_id, outlet_type, categ_id, category, gross_sales, discounts, points_redeemed_idr,
       refunds, net_sales, tax, net_sales_incl_tax, qty_sold, lines
FROM efn_dwh.v_sm_sales_daily;

CREATE OR REPLACE VIEW efn_insight.v_sm_outlet_daily DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT day, company_id, warehouse_id, outlet_type, orders, net_sales, net_sales_incl_tax, refunds, avg_basket_incl_tax
FROM efn_dwh.v_sm_outlet_daily;

CREATE OR REPLACE VIEW efn_insight.v_sm_margin_daily DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT day, company_id, warehouse_id, outlet_type, categ_id, category, net_sales, cogs, gross_margin, gross_margin_pct,
       net_sales_with_cost
FROM efn_dwh.v_sm_margin_daily;

CREATE OR REPLACE VIEW efn_insight.v_sm_gmroi_monthly DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT month, company_id, warehouse_id, outlet_type, categ_id, category, gross_margin, avg_stock_value_cost_daily,
       avg_stock_value_cost_month_end, stock_days, days_in_month
FROM efn_dwh.v_sm_gmroi_monthly;

CREATE OR REPLACE VIEW efn_insight.v_sm_waste_daily DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT day, company_id, warehouse_id, outlet_type, categ_id, category, reason_id, reason_name, scraps, waste_qty,
       waste_value_cost
FROM efn_dwh.v_sm_waste_daily;

CREATE OR REPLACE VIEW efn_insight.v_sm_forecast_accuracy_weekly DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT week_start, company_id, warehouse_id, outlet_type, product_id, categ_id, days, days_with_sales, forecast_qty,
       actual_qty, abs_error
FROM efn_dwh.v_sm_forecast_accuracy_weekly;

CREATE OR REPLACE VIEW efn_insight.v_sm_stock_daily DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT day, company_id, warehouse_id, outlet_type, categ_id, sku_universe, sku_oos, sku_below_min, on_hand_qty
FROM efn_dwh.v_sm_stock_daily;

CREATE OR REPLACE VIEW efn_insight.v_sm_oos_product_daily DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT day, company_id, warehouse_id, outlet_type, product_id, product_tmpl_id, categ_id, in_universe, oos, below_min,
       on_hand_qty
FROM efn_dwh.v_sm_oos_product_daily;

CREATE OR REPLACE VIEW efn_insight.v_sm_stock_value_daily DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT day, company_id, warehouse_id, outlet_type, categ_id, stock_value_cost, value_age_0_30, value_age_31_60,
       value_age_61_90, value_age_91p, value_exp_expired, value_exp_0_7, value_exp_8_30, value_exp_31_90, value_exp_91p,
       value_no_lot
FROM efn_dwh.v_sm_stock_value_daily;

CREATE OR REPLACE VIEW efn_insight.v_sm_stock_critical DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT snapshot_ts, company_id, warehouse_id, outlet_type, product_id, product_tmpl_id, default_code, product_name,
       categ_id, on_hand_qty, free_qty, min_qty, max_qty, below_min, avg_daily_sales, days_of_cover, is_out, is_critical,
       open_proposal_id
FROM efn_dwh.v_sm_stock_critical;

CREATE OR REPLACE VIEW efn_insight.v_sm_replenishment DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT proposal_id, company_id, warehouse_id, outlet_type, supply_warehouse_id, delivery_date, cutoff_at, state,
       approved_at, picking_id, line_count, lines_excluded, lines_changed, qty_proposed_total, qty_approved_total
FROM efn_dwh.v_sm_replenishment;

CREATE OR REPLACE VIEW efn_insight.v_sm_replenishment_line DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT line_id, proposal_id, company_id, warehouse_id, outlet_type, delivery_date, state, product_id, categ_id,
       qty_proposed, qty_approved, excluded, reason_codes, change_reason
FROM efn_dwh.v_sm_replenishment_line;

CREATE OR REPLACE VIEW efn_insight.v_sm_replenishment_bias_weekly DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT week_start, company_id, warehouse_id, outlet_type, kind, entries, qty_system, qty_final, bias_qty
FROM efn_dwh.v_sm_replenishment_bias_weekly;

CREATE OR REPLACE VIEW efn_insight.v_sm_dc_fill_rate DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT line_id, proposal_id, company_id, warehouse_id, outlet_type, dc_warehouse_id, delivery_date, product_id, categ_id,
       qty_approved, qty_shipped, qty_received, qty_basis, on_time, picking_shared
FROM efn_dwh.v_sm_dc_fill_rate;

CREATE OR REPLACE VIEW efn_insight.v_sm_dc_purchase DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT line_id, order_id, company_id, warehouse_id, outlet_type, supplier_id, supplier_name, product_id, po_state,
       date_order, date_planned, product_qty, qty_received, price_subtotal, from_orderpoint, is_late
FROM efn_dwh.v_sm_dc_purchase;

CREATE OR REPLACE VIEW efn_insight.v_sm_lot_expiry DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT alert_id, company_id, warehouse_id, outlet_type, lot_id, lot_name, product_id, product_tmpl_id, product_name,
       categ_id, stage, state, detected_date, last_run_date, days_left, expiration_date, qty, value_cost, value_retail,
       markdown_id, markdown_state, markdown_effective_pct, markdown_price, markdown_date_to, display_status
FROM efn_dwh.v_sm_lot_expiry;

CREATE OR REPLACE VIEW efn_insight.v_sm_expiry_daily DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT day, company_id, warehouse_id, outlet_type, categ_id, stage, lots, qty, value_cost
FROM efn_dwh.v_sm_expiry_daily;

CREATE OR REPLACE VIEW efn_insight.v_sm_markdown DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT markdown_id, company_id, warehouse_id, outlet_type, lot_id, product_id, alert_id, state, price, discount_pct,
       effective_discount_pct, date_from, date_to, qty_cap, qty_sold, notify_members
FROM efn_dwh.v_sm_markdown;

CREATE OR REPLACE VIEW efn_insight.v_sm_price_index DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT company_id, warehouse_id, outlet_type, product_id, product_tmpl_id, default_code, product_name, categ_id,
       our_price, market_median_30d, market_min_30d, market_max_30d, sample_n_30d, competitors_n_30d, last_sample_date,
       diff_pct
FROM efn_dwh.v_sm_price_index;

CREATE OR REPLACE VIEW efn_insight.v_sm_price_coverage DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT company_id, warehouse_id, outlet_type, categ_id, sku_active, sku_with_sample, sku_with_sample_3
FROM efn_dwh.v_sm_price_coverage;

CREATE OR REPLACE VIEW efn_insight.v_sm_price_signal DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT company_id, warehouse_id, outlet_type, product_id, categ_id, viewers_30d, views_30d, cart_adds_30d,
       purchases_30d, our_price, market_median_30d, diff_pct
FROM efn_dwh.v_sm_price_signal;

CREATE OR REPLACE VIEW efn_insight.v_sm_visitor_funnel_daily DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT day, company_id, warehouse_id, outlet_type, channel, channel_code, subjects, viewers, carters, checkout_starters,
       purchasers, searchers_no_result
FROM efn_dwh.v_sm_visitor_funnel_daily;

CREATE OR REPLACE VIEW efn_insight.v_sm_search_terms DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT day, company_id, warehouse_id, outlet_type, channel, channel_code, query, searches, no_result, subjects
FROM efn_dwh.v_sm_search_terms;

CREATE OR REPLACE VIEW efn_insight.v_sm_segment_daily DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT day, company_id, warehouse_id, outlet_type, segment_code, member_count, run_id, campaign_id, campaign_state
FROM efn_dwh.v_sm_segment_daily;

CREATE OR REPLACE VIEW efn_insight.v_sm_campaign_result DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT campaign_id, company_id, warehouse_id, outlet_type, name, kind, origin, state, voucher_mode, channels, date_from,
       date_to, approved_at, sent_at, approval_rev, budget_idr, segment_code, segment_member_count, delivered, skipped,
       push_sent, email_queued, wa_queued, stop_reason, vouchers_issued, vouchers_used, orders_with_voucher,
       discount_idr, revenue_with_voucher_idr, computed_at
FROM efn_dwh.v_sm_campaign_result;

CREATE OR REPLACE VIEW efn_insight.v_sm_freshness DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT mart, freshness_class, data_as_of, source_as_of, last_run_at, last_status
FROM efn_dwh.v_sm_freshness;

CREATE OR REPLACE VIEW efn_insight.v_sm_kpi_config DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT company_id, key, value_num, value_str, is_default, effective_since
FROM efn_dwh.v_sm_kpi_config;

-- ============================================================== G51 (spesifikasi lead G51 §B2, urutan kolom tetap)
CREATE OR REPLACE VIEW efn_insight.v_sm_payment_daily DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT day, company_id, warehouse_id, outlet_type, method_name, method_type, is_refund, amount, payments
FROM efn_dwh.v_sm_payment_daily;

CREATE OR REPLACE VIEW efn_insight.v_sm_product_sales_daily DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT day, company_id, warehouse_id, outlet_type, product_id, categ_id, qty_sold, net_sales, net_sales_incl_tax
FROM efn_dwh.v_sm_product_sales_daily;

CREATE OR REPLACE VIEW efn_insight.v_sm_forecast_daily DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT company_id, warehouse_id, outlet_type, product_id, categ_id, forecast_date, yhat, p10, p90, run_id, generated_at
FROM efn_dwh.v_sm_forecast_daily;

CREATE OR REPLACE VIEW efn_insight.v_sm_lot_stock DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT company_id, warehouse_id, outlet_type, product_id, categ_id, lot_id, lot_name, expiration_date, days_left,
       quantity, value_cost
FROM efn_dwh.v_sm_lot_stock;
