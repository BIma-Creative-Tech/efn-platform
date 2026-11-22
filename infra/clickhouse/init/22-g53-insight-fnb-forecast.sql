-- G53 (23 Nov 2026; ADR-046 Proposed, ADR-041 §3.6 Proposed): forecast brand F&B di insight-web `/forecast`.
-- Proyeksi harian run forecast `ok` (kind daily) TERBARU per PT (menurut generated_at, sama seperti
-- v_sm_forecast_daily 18-g51) untuk PT brand F&B (kode ESB). Dibaca HANYA lewat efn_insight.v_esb_forecast_daily
-- (SQL SECURITY DEFINER efn_insight_definer, access/insight-access.sql) + row policy `efn_insight_scope` kelas PT
-- (access/insight-esb-policy.sh; sesi bercabang/brs terisi -> 0 baris).
-- Termasuk baris gudang pusat/DC (agregat outlet di worker, aggregate_dc): tidak ada penanda DC di mart, aplikasi
-- membuang kode DC dari total (env insight-web INSIGHT_FNB_DC_CODES, sama dengan FORECAST_DC_CODES worker).
-- Horizon penuh run (default 28 hari); aplikasi memfilter 14 hari ke depan. Satuan = qty produk, bukan Rupiah.
-- Idempoten (CREATE OR REPLACE VIEW), tanpa tabel baru. PDP: tanpa data pribadi (gudang, produk, angka agregat).
CREATE OR REPLACE VIEW efn_dwh.v_esb_forecast_daily AS
SELECT
    w.company_code AS company_code,
    multiIf(w.company_code IN ('BCF', 'BCR'), 'bacha', lower(w.company_code)) AS brand,
    f.warehouse_id AS warehouse_id,
    w.code AS warehouse_code,
    w.name AS warehouse_name,
    f.product_id AS product_id,
    ifNull(p.default_code, '') AS product_code,
    ifNull(p.name, '') AS product_name,
    ifNull(p.category, '') AS category,
    f.forecast_date AS forecast_date,
    toFloat64(f.yhat) AS yhat,
    toFloat64(f.p10) AS p10,
    toFloat64(f.p90) AS p90,
    toString(f.model) AS model,
    toDateTime(r.latest_at, 'UTC') AS generated_at
FROM
(
    SELECT run_id, company_id, warehouse_id, product_id, forecast_date, yhat, p10, p90, model
    FROM efn_dwh.fact_forecast_daily FINAL
    WHERE run_id IN
    (
        SELECT argMax(run_id, generated_at) FROM efn_dwh.dim_forecast_run FINAL
        WHERE state = 'ok' AND kind = 'daily'
        GROUP BY company_id
    )
) AS f
INNER JOIN
(
    SELECT company_id, argMax(run_id, generated_at) AS latest_run, max(generated_at) AS latest_at
    FROM efn_dwh.dim_forecast_run FINAL
    WHERE state = 'ok' AND kind = 'daily'
    GROUP BY company_id
) AS r ON r.company_id = f.company_id AND r.latest_run = f.run_id
INNER JOIN
(
    SELECT warehouse_id, company_id, code, name, company_code FROM efn_dwh.dim_fc_warehouse FINAL
    WHERE company_code IN ('PB', 'WTZ', 'BCF', 'BCR', 'IMD', 'CUP')
) AS w ON w.warehouse_id = f.warehouse_id AND w.company_id = f.company_id
LEFT JOIN
(
    SELECT product_id, default_code, name, category FROM efn_dwh.dim_product FINAL
) AS p ON p.product_id = f.product_id;
