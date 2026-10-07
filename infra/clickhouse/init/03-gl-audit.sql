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

-- View konsolidasi untuk Metabase: per periode (bulan) x company x akun x lawan inter-company, hanya posted.
-- Konsolidasi grup = SUM(balance_after_elimination) lintas company per account_code (bagan akun sama: l10n_id).
-- Eliminasi sederhana: baris dengan partner = company grup lain dikeluarkan (kedua sisi transaksi inter-company
-- memakai partner company lawan). Selisih eliminasi (mis. beda kurs/timing) terlihat dari SUM(balance) baris
-- is_intercompany per pasangan company — dashboard rekonsiliasi IC.
CREATE OR REPLACE VIEW efn_dwh.v_gl_consolidated AS
SELECT
    toStartOfMonth(l.date) AS period,
    if(toMonth(l.date) * 100 + toDayOfMonth(l.date) > c.fy_last_month * 100 + c.fy_last_day,
       toYear(l.date) + 1, toYear(l.date)) AS fiscal_year,
    l.company_id AS company_id,
    c.name AS company_name,
    c.brand AS brand,
    c.currency AS currency,
    l.account_id AS account_id,
    a.code AS account_code,
    a.name AS account_name,
    a.account_type AS account_type,
    a.internal_group AS internal_group,
    isNotNull(l.ic_company_id) AS is_intercompany,
    l.ic_company_id AS counterparty_company_id,
    sum(l.debit) AS debit,
    sum(l.credit) AS credit,
    sum(l.balance) AS balance,
    sumIf(l.balance, isNull(l.ic_company_id)) AS balance_after_elimination,
    count() AS lines
FROM efn_dwh.fact_gl_line AS l FINAL
LEFT JOIN (SELECT company_id, name, brand, currency, fy_last_month, fy_last_day FROM efn_dwh.dim_company FINAL) AS c
    ON c.company_id = l.company_id
LEFT JOIN (SELECT company_id, account_id, code, name, account_type, internal_group FROM efn_dwh.dim_gl_account FINAL) AS a
    ON a.company_id = l.company_id AND a.account_id = l.account_id
WHERE l.parent_state = 'posted'
GROUP BY period, fiscal_year, company_id, company_name, brand, currency, account_id, account_code, account_name,
         account_type, internal_group, is_intercompany, counterparty_company_id;

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
