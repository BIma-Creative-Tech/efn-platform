-- Gelombang 20 (efn-data-ai-engineer, W-32): fondasi 6 entitas root (docs/04 §12.1, ADR-001 revisi 18 Okt 2026).
-- Idempoten (ADD COLUMN IF NOT EXISTS / CREATE OR REPLACE VIEW). Dijalankan clickhouse-init.sh setelah 01..07.
-- Kode PT: EFN (holding) | GL (retail) | PB (fnb) | BCR (retail) | BCF (fnb) | FARM (farm). Kode lama RTL/FNB dihapus;
-- tidak ada migrasi data (DB dev dibuat ulang, docs/runbooks/dev-reset.md). Sumber kebenaran pemetaan & rumus
-- ic_relation: services/etl/efn_etl/entities.py (test test_entities.py memeriksa view ini tetap selaras).

-- ============================================================== dim_company: atribut entitas (diisi ETL)
-- business_type: holding | retail | fnb | farm ('' = company tanpa kode PT, mis. company bawaan Odoo).
-- brand_group  : grup laporan brand; BCR + BCF = BACHA, lainnya = kode PT.
ALTER TABLE efn_dwh.dim_company ADD COLUMN IF NOT EXISTS business_type LowCardinality(String) DEFAULT '';
ALTER TABLE efn_dwh.dim_company ADD COLUMN IF NOT EXISTS brand_group LowCardinality(String) DEFAULT '';

-- ============================================================== v_gl_consolidated (dipindah dari 03-gl-audit.sql)
-- Per periode (bulan) x company x akun x lawan inter-company, hanya posted.
-- Konsolidasi grup = SUM(balance_after_elimination) lintas company per account_code (bagan akun sama: l10n_id).
-- Eliminasi: baris dengan partner = company grup lain (ic_company_id, efn_etl/gl.py) dikeluarkan, untuk SEMUA
-- pasangan dari 6 entitas (6x5 arah). ic_relation hanya memberi label dasar harga untuk rekonsiliasi:
--   tp_holding = Holding EFN <-> PT mana pun (TP cost-plus, 18-3)
--   tp_bacha   = BCR <-> BCF (TP cost-plus, 18-3)
--   non_tp     = pasangan lain, mis. FARM -> PT, PB <-> GL (harga daftar/pasar, Q-AD1-1 belum diputuskan)
--   unknown    = salah satu company tanpa kode PT dikenal / kode sama (konfigurasi company salah; tetap dieliminasi)
-- Selisih eliminasi (beda kurs/timing/sisi belum dibukukan) per pasangan: v_gl_ic_recon di bawah.
-- Penanda transaksi efn_intercompany (W-15) belum ada di sumber; eliminasi masih berbasis partner company lawan.
CREATE OR REPLACE VIEW efn_dwh.v_gl_consolidated AS
SELECT
    toStartOfMonth(l.date) AS period,
    if(toMonth(l.date) * 100 + toDayOfMonth(l.date) > c.fy_last_month * 100 + c.fy_last_day,
       toYear(l.date) + 1, toYear(l.date)) AS fiscal_year,
    l.company_id AS company_id,
    c.name AS company_name,
    c.brand AS brand,
    c.business_type AS business_type,
    c.brand_group AS brand_group,
    c.currency AS currency,
    l.account_id AS account_id,
    a.code AS account_code,
    a.name AS account_name,
    a.account_type AS account_type,
    a.internal_group AS internal_group,
    isNotNull(l.ic_company_id) AS is_intercompany,
    l.ic_company_id AS counterparty_company_id,
    cp.brand AS counterparty_brand,
    multiIf(
        isNull(l.ic_company_id), '',
        c.brand NOT IN ('EFN', 'GL', 'PB', 'BCR', 'BCF', 'FARM')
            OR cp.brand NOT IN ('EFN', 'GL', 'PB', 'BCR', 'BCF', 'FARM')
            OR c.brand = cp.brand, 'unknown',
        c.brand = 'EFN' OR cp.brand = 'EFN', 'tp_holding',
        (c.brand = 'BCR' AND cp.brand = 'BCF') OR (c.brand = 'BCF' AND cp.brand = 'BCR'), 'tp_bacha',
        'non_tp') AS ic_relation,
    sum(l.debit) AS debit,
    sum(l.credit) AS credit,
    sum(l.balance) AS balance,
    sumIf(l.balance, isNull(l.ic_company_id)) AS balance_after_elimination,
    count() AS lines
FROM efn_dwh.fact_gl_line AS l FINAL
LEFT JOIN (SELECT company_id, name, brand, business_type, brand_group, currency, fy_last_month, fy_last_day
           FROM efn_dwh.dim_company FINAL) AS c
    ON c.company_id = l.company_id
LEFT JOIN (SELECT company_id, brand FROM efn_dwh.dim_company FINAL) AS cp
    ON cp.company_id = l.ic_company_id
LEFT JOIN (SELECT company_id, account_id, code, name, account_type, internal_group FROM efn_dwh.dim_gl_account FINAL) AS a
    ON a.company_id = l.company_id AND a.account_id = l.account_id
WHERE l.parent_state = 'posted'
GROUP BY period, fiscal_year, company_id, company_name, brand, business_type, brand_group, currency, account_id,
         account_code, account_name, account_type, internal_group, is_intercompany, counterparty_company_id,
         counterparty_brand, ic_relation;

-- ============================================================== v_gl_ic_recon: rekonsiliasi eliminasi per pasangan
-- Satu baris per periode x pasangan entitas tak berurut (company_a_id < company_b_id). Bila kedua sisi membukukan
-- transaksi yang sama (piutang A = utang B, pendapatan A = beban B), SUM(balance) kedua sisi = 0. net_balance <> 0
-- = selisih eliminasi yang harus dijelaskan Finance (kurs, timing, sisi belum diposting, partner salah).
CREATE OR REPLACE VIEW efn_dwh.v_gl_ic_recon AS
SELECT
    period,
    least(company_id, assumeNotNull(counterparty_company_id)) AS company_a_id,
    greatest(company_id, assumeNotNull(counterparty_company_id)) AS company_b_id,
    if(company_id < assumeNotNull(counterparty_company_id), brand, counterparty_brand) AS company_a_brand,
    if(company_id < assumeNotNull(counterparty_company_id), counterparty_brand, brand) AS company_b_brand,
    ic_relation,
    sumIf(balance, company_id < assumeNotNull(counterparty_company_id)) AS balance_a,
    sumIf(balance, company_id > assumeNotNull(counterparty_company_id)) AS balance_b,
    sum(balance) AS net_balance,
    sum(lines) AS lines
FROM efn_dwh.v_gl_consolidated
WHERE is_intercompany
GROUP BY period, company_a_id, company_b_id, company_a_brand, company_b_brand, ic_relation;
