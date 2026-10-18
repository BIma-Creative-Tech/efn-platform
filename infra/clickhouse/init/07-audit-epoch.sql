-- Gelombang 18 (efn-data-ai-engineer): chain epoch salinan audit — restore DB Odoo (celah runbook restore, devsecops).
-- Idempoten (ADD COLUMN IF NOT EXISTS / IF NOT EXISTS). Dijalankan clickhouse-init.sh setelah 01..06.
--
-- Setelah restore, id efn.audit.log mundur; ETL memulai EPOCH baru (efn_etl/audit.py `decide_epoch`): rantai hash
-- (GENESIS per company) dan watermark (max log_id) dihitung PER EPOCH, salinan epoch lama tetap ada sebagai bukti.
-- Baris lama = epoch 0 (DEFAULT, tanpa penulisan ulang part). ORDER BY tidak diubah (tidak bisa tanpa membuat ulang
-- tabel); query ETL selalu memfilter chain_epoch.
ALTER TABLE efn_dwh.audit_log_copy ADD COLUMN IF NOT EXISTS chain_epoch UInt32 DEFAULT 0;
ALTER TABLE efn_dwh.audit_log_line_copy ADD COLUMN IF NOT EXISTS chain_epoch UInt32 DEFAULT 0;
ALTER TABLE efn_dwh.audit_recon_run ADD COLUMN IF NOT EXISTS chain_epoch UInt32 DEFAULT 0;

-- Riwayat epoch (append-only; efn_etl SELECT+INSERT saja). reason: init | marker | auto:<sinyal>.
-- marker = nilai ir.config_parameter `efn_audit.chain_epoch` (runbook restore) saat epoch dimulai/didaftarkan.
-- prev_max_log_id/source_max_log_id/anchors_* = bukti deteksi (watermark epoch lama vs sumber saat itu).
-- Tanpa TTL: kecil (satu baris per restore) dan dibutuhkan selama salinan epoch mana pun masih disimpan.
CREATE TABLE IF NOT EXISTS efn_dwh.audit_chain_epoch
(
    epoch              UInt32,
    marker             String,
    reason             LowCardinality(String),
    started_at         DateTime64(6, 'UTC'),
    prev_max_log_id    UInt64,
    source_max_log_id  UInt64,
    anchors_checked    UInt32,
    anchors_matched    UInt32,
    _loaded_at         DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (epoch, started_at);
