-- Gelombang 12 (efn-data-ai-engineer): mart konsolidasi GL (F-19, docs/08 §5.3) dan salinan audit tamper-evident
-- (ADR-031, SEC-43; usulan efn-odoo-engineer G11). Idempoten (IF NOT EXISTS / CREATE OR REPLACE VIEW). Dijalankan
-- clickhouse-init.sh setiap deploy SETELAH 01/02.

-- ============================================================== GL (sumber: Odoo account_move_line, efn_etl/gl.py)
-- Semua state dimuat (draft|posted|cancel); versi terbaru per line_id menang (ReplacingMergeTree(version_ts)).
-- PARTITION BY company_id (bukan tanggal): tanggal baris draft bisa berubah, sedangkan ReplacingMergeTree hanya
-- men-dedup dalam satu partisi -> partisi per tanggal akan meninggalkan versi lama. Company baris tidak berubah.
-- partner_id TIDAK disimpan (PII); ic_company_id = company grup lawan transaksi (eliminasi inter-company).
CREATE TABLE IF NOT EXISTS efn_dwh.fact_gl_line
(
    line_id          UInt64,
    company_id       UInt32,
    move_id          UInt64,
    move_name        String,
    move_type        LowCardinality(String),
    journal_id       UInt32,
    account_id       UInt32,
    date             Date,
    parent_state     LowCardinality(String),
    debit            Decimal(18, 2),
    credit           Decimal(18, 2),
    balance          Decimal(18, 2),
    amount_currency  Decimal(18, 2),
    currency_id      Nullable(UInt32),
    ic_company_id    Nullable(UInt32),
    version_ts       DateTime64(6, 'UTC'),
    _loaded_at       DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(version_ts)
PARTITION BY company_id
ORDER BY (company_id, line_id);

-- Kode akun per company (Odoo 19: account_account.code_store per company root).
CREATE TABLE IF NOT EXISTS efn_dwh.dim_gl_account
(
    account_id      UInt32,
    company_id      UInt32,
    code            String,
    name            String,
    account_type    LowCardinality(String),
    internal_group  LowCardinality(String),   -- asset|liability|equity|income|expense|off_balance|other
    active          UInt8,
    write_date      DateTime64(6, 'UTC'),
    _loaded_at      DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(write_date)
ORDER BY (company_id, account_id);

CREATE TABLE IF NOT EXISTS efn_dwh.dim_company
(
    company_id     UInt32,
    name           String,
    brand          LowCardinality(String),
    parent_id      Nullable(UInt32),
    currency       LowCardinality(String),
    fy_last_month  UInt8,
    fy_last_day    UInt8,
    write_date     DateTime64(6, 'UTC'),
    _loaded_at     DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(write_date)
ORDER BY company_id;

-- View konsolidasi `v_gl_consolidated` DIPINDAH ke 08-g20-entities.sql (G20): view baru membaca kolom
-- dim_company.business_type/brand_group yang ditambahkan di 08 (ALTER ... ADD COLUMN), sehingga harus dibuat
-- setelah kolom itu ada. File ini hanya membuat tabel.

-- ============================================================== Salinan audit tamper-evident (efn_etl/audit*.py)
-- APPEND-ONLY: MergeTree biasa (BUKAN Replacing/Collapsing -> merge tidak pernah membuang/menggabung baris).
-- efn_etl hanya SELECT+INSERT (etl-access.sql): tidak ada ALTER/DELETE/TRUNCATE/DROP. Tanpa partisi agar satu
-- INSERT = satu part (atomik); volume log audit kecil.
-- row_hash = sha256(prev_hash || payload_hash), payload_hash = sha256(canonical_row) berantai per chain_company
-- (company_id log; 0 = tanpa company) urut chain_seq. Kepala rantai juga dicatat ke Loki (EFN_AUDIT_CHAIN_HEAD).
-- PII: log orang (is_personal / model orang) -> res_id & res_name NULL; nilai baris yang ter-mask atau menunjuk model
-- orang -> NULL (redacted=1); hanya hash yang disimpan, sehingga erasure UU PDP di Odoo tidak menyisakan isi subjek
-- di sini. user_id/origin_user_id = id user Odoo pelaku (karyawan; dasar: kewajiban audit).
-- Retensi: TTL diatur clickhouse-init.sh (CH_AUDIT_COPY_RETENTION_YEARS, default 10) — usulan menunggu DPO (Q-49).
CREATE TABLE IF NOT EXISTS efn_dwh.audit_log_copy
(
    chain_company    UInt32,
    chain_seq        UInt64,
    log_id           UInt64,
    create_date      DateTime64(6, 'UTC'),
    user_id          UInt32,
    origin_user_id   Nullable(UInt32),
    is_sudo          UInt8,
    operation        LowCardinality(String),
    model_name       LowCardinality(String),
    res_id           Nullable(UInt64),
    res_name         Nullable(String),
    company_id       Nullable(UInt32),
    prev_company_id  Nullable(UInt32),
    is_personal      UInt8,
    note             String,
    line_count       UInt32,
    lines_digest     String,
    payload_hash     String,
    prev_hash        String,
    row_hash         String,
    ingest_kind      LowCardinality(String),   -- new | late
    _loaded_at       DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (chain_company, chain_seq);

CREATE TABLE IF NOT EXISTS efn_dwh.audit_log_line_copy
(
    line_id      UInt64,
    log_id       UInt64,
    create_date  DateTime64(6, 'UTC'),
    field_name   LowCardinality(String),
    field_label  String,
    old_value    Nullable(String),
    new_value    Nullable(String),
    old_ref      Nullable(String),
    new_ref      Nullable(String),
    is_masked    UInt8,
    redacted     UInt8,
    line_hash    String,
    _loaded_at   DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (log_id, line_id);

-- Hasil rekonsiliasi berkala (tanpa PII: hanya hitungan + contoh id log). Dibaca Metabase/Grafana untuk alert.
CREATE TABLE IF NOT EXISTS efn_dwh.audit_recon_run
(
    run_at            DateTime64(6, 'UTC'),
    max_log_id        UInt64,
    checked           UInt64,
    retention         UInt64,
    erasure           UInt64,
    unexplained       UInt64,
    unseen            UInt64,
    modified          UInt64,
    erasure_redacted  UInt64,
    chain_problems    UInt64,
    sample_ids        Array(UInt64)
)
ENGINE = MergeTree
ORDER BY run_at;
