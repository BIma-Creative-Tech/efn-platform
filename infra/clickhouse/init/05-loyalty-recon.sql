-- Gelombang 14 (efn-data-ai-engineer): rekonsiliasi loyalty ledger <-> pos.order (sisa SEC-61) dan verifikasi sink
-- audit Loki + jangkar rantai (SEC-51/52). Idempoten (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS / CREATE OR REPLACE
-- VIEW). Dijalankan clickhouse-init.sh setiap deploy SETELAH 01..04. Tanpa data pribadi (tanpa member_hash/partner/HP):
-- hanya jenis temuan, kode PT, hitungan, id order Odoo / id ledger (angka) sebagai sampel.

-- ============================================================== (a) Rekonsiliasi audit: kolom verifikasi sink
-- efn_etl/audit_job.py RECON_COLUMNS. sink_checked=0 = verifikasi sink tidak aktif pada run itu (LOKI_URL kosong).
ALTER TABLE efn_dwh.audit_recon_run ADD COLUMN IF NOT EXISTS sink_checked UInt8 DEFAULT 0;
ALTER TABLE efn_dwh.audit_recon_run ADD COLUMN IF NOT EXISTS sink_error UInt8 DEFAULT 0;
ALTER TABLE efn_dwh.audit_recon_run ADD COLUMN IF NOT EXISTS sink_lines UInt64 DEFAULT 0;
ALTER TABLE efn_dwh.audit_recon_run ADD COLUMN IF NOT EXISTS sink_mac_invalid UInt64 DEFAULT 0;
ALTER TABLE efn_dwh.audit_recon_run ADD COLUMN IF NOT EXISTS sink_missing_log UInt64 DEFAULT 0;
ALTER TABLE efn_dwh.audit_recon_run ADD COLUMN IF NOT EXISTS log_missing_sink UInt64 DEFAULT 0;
ALTER TABLE efn_dwh.audit_recon_run ADD COLUMN IF NOT EXISTS anchors_checked UInt64 DEFAULT 0;
ALTER TABLE efn_dwh.audit_recon_run ADD COLUMN IF NOT EXISTS anchor_mismatch UInt64 DEFAULT 0;
ALTER TABLE efn_dwh.audit_recon_run ADD COLUMN IF NOT EXISTS anchor_missing UInt64 DEFAULT 0;
ALTER TABLE efn_dwh.audit_recon_run ADD COLUMN IF NOT EXISTS anchor_absent UInt64 DEFAULT 0;

-- ============================================================== (b) Rekonsiliasi loyalty (efn_etl/loyalty_recon.py)
-- Penanda run (ditulis TERAKHIR). Retensi 400 hari = stream Loki efn_loyalty (bukti investigasi fraud poin).
CREATE TABLE IF NOT EXISTS efn_dwh.loyalty_recon_run
(
    run_at          DateTime64(6, 'UTC'),
    status          LowCardinality(String),
    lookback_hours  UInt32,
    settle_hours    UInt32,
    orders_checked  UInt64,
    ledger_checked  UInt64,
    anomalies       UInt64,
    open_items      UInt64,
    _loaded_at      DateTime DEFAULT now()
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(run_at)
ORDER BY run_at
TTL toDateTime(run_at) + INTERVAL 400 DAY
SETTINGS ttl_only_drop_parts = 1;

-- Satu baris per run x kind x PT. severity: critical (anomali integritas) | warning (antrean kerja yang menua).
CREATE TABLE IF NOT EXISTS efn_dwh.loyalty_recon_finding
(
    run_at             DateTime64(6, 'UTC'),
    kind               LowCardinality(String),
    severity           LowCardinality(String),
    company_code       LowCardinality(String),
    findings           UInt64,
    sample_order_ids   Array(UInt64),
    sample_ledger_ids  Array(UInt64),
    _loaded_at         DateTime DEFAULT now()
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(run_at)
ORDER BY (run_at, kind, company_code)
TTL toDateTime(run_at) + INTERVAL 400 DAY
SETTINGS ttl_only_drop_parts = 1;

-- ============================================================== View semantic layer (Metabase)
-- Run selesai terakhir (penanda) + temuannya. Run tanpa temuan tetap tampil lewat v_loyalty_recon_daily.
CREATE OR REPLACE VIEW efn_dwh.v_loyalty_recon_latest AS
SELECT
    f.run_at AS run_at, f.kind AS kind, f.severity AS severity, f.company_code AS company_code,
    f.findings AS findings, f.sample_order_ids AS sample_order_ids
FROM efn_dwh.loyalty_recon_finding AS f
WHERE f.run_at = (SELECT max(run_at) FROM efn_dwh.loyalty_recon_run);

-- Ringkasan per hari WIB (run terakhir hari itu).
CREATE OR REPLACE VIEW efn_dwh.v_loyalty_recon_daily AS
SELECT
    toDate(run_at, 'Asia/Jakarta') AS day,
    argMax(run_at, run_at)            AS last_run_at,
    argMax(anomalies, run_at)         AS anomalies,
    argMax(open_items, run_at)        AS open_items,
    argMax(orders_checked, run_at)    AS orders_checked,
    argMax(ledger_checked, run_at)    AS ledger_checked,
    count()                           AS runs
FROM efn_dwh.loyalty_recon_run
GROUP BY day;

-- Rekonsiliasi audit (hitungan saja, tanpa salinan log) untuk kartu kesehatan audit.
CREATE OR REPLACE VIEW efn_dwh.v_audit_recon_daily AS
SELECT
    toDate(run_at, 'Asia/Jakarta') AS day,
    argMax(run_at, run_at)                                                   AS last_run_at,
    argMax(unexplained + unseen + modified + chain_problems, run_at)         AS copy_anomalies,
    argMax(sink_checked, run_at)                                             AS sink_checked,
    argMax(sink_error, run_at)                                               AS sink_error,
    argMax(sink_lines, run_at)                                               AS sink_lines,
    argMax(sink_mac_invalid + sink_missing_log + log_missing_sink, run_at)   AS sink_anomalies,
    argMax(anchors_checked, run_at)                                          AS anchors_checked,
    argMax(anchor_mismatch + anchor_missing + anchor_absent, run_at)         AS anchor_anomalies
FROM efn_dwh.audit_recon_run
GROUP BY day;
