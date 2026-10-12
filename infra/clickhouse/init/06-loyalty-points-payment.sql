-- Gelombang 18 (efn-data-ai-engineer): redeem poin = alat bayar POS (docs/04 §9.9 K-18-1, K-18-6; Odoo E-30).
-- Idempoten (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS / CREATE OR REPLACE VIEW). Dijalankan clickhouse-init.sh setelah 01..05.
-- Tanpa data pribadi: tidak ada member_hash/partner/HP; id order Odoo hanya di loyalty_recon_finding (sampel).

-- (a) Penanda pembayaran Poin EFN di mart pembayaran POS (`pos_payment_method.efn_is_loyalty_points`, E-30).
-- Diisi ETL hanya bila ETL_LOYALTY_POINTS_MODE=payment (kolom Odoo baru ada setelah E-30); sebelumnya 0.
ALTER TABLE efn_dwh.fact_pos_payment ADD COLUMN IF NOT EXISTS is_loyalty_points UInt8 DEFAULT 0;

-- (b) Rekonsiliasi harian K-18-6 per PT x tanggal WIB (efn_etl/loyalty_recon.py `daily_points_recon`):
--     odoo_points_payment_idr = Σ pos.payment metode Poin EFN (refund bernilai negatif)
--     bff_redeem_idr          = Σ value_idr reservasi committed (pos_redeem)
--     bff_restored_idr        = Σ points_restored_value reverse (K-18-5)
--     diff_idr                = odoo − (redeem − restored); ≠ 0 -> temuan `points_payment_daily_mismatch`.
-- Tanggal = tanggal WIB pembayaran Poin EFN di Odoo (sisi BFF dipetakan lewat order_ref = pos.order.uuid; hanya
-- yang tidak punya order Odoo memakai tanggal BFF). Satu baris per run x hari x PT; view membaca run terakhir.
-- Retensi 400 hari = tabel rekonsiliasi lain (05-loyalty-recon.sql).
CREATE TABLE IF NOT EXISTS efn_dwh.loyalty_points_daily
(
    run_at                   DateTime64(6, 'UTC'),
    day                      Date,
    company_code             LowCardinality(String),
    odoo_points_payment_idr  Decimal(18, 2),
    bff_redeem_idr           Decimal(18, 2),
    bff_restored_idr         Decimal(18, 2),
    diff_idr                 Decimal(18, 2),
    orders                   UInt32,
    orders_mismatched        UInt32,
    _loaded_at               DateTime DEFAULT now()
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(day)
ORDER BY (day, company_code, run_at)
TTL day + INTERVAL 400 DAY
SETTINGS ttl_only_drop_parts = 1;

-- Hasil terakhir per hari x PT (hari yang sama direkonsiliasi ulang oleh run berikutnya selama masih di lookback).
CREATE OR REPLACE VIEW efn_dwh.v_loyalty_points_daily AS
SELECT
    day,
    company_code,
    argMax(run_at, run_at)                   AS last_run_at,
    argMax(odoo_points_payment_idr, run_at)  AS odoo_points_payment_idr,
    argMax(bff_redeem_idr, run_at)           AS bff_redeem_idr,
    argMax(bff_restored_idr, run_at)         AS bff_restored_idr,
    argMax(diff_idr, run_at)                 AS diff_idr,
    argMax(orders, run_at)                   AS orders,
    argMax(orders_mismatched, run_at)        AS orders_mismatched
FROM efn_dwh.loyalty_points_daily
GROUP BY day, company_code;
