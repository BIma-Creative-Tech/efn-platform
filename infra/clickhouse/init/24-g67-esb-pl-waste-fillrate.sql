-- Gelombang 67 (docs/04 §31.5-§31.6, ADR-040 amandemen G67, ADR-041, L-260..L-268): laba rugi outlet, waste &
-- pemakaian internal, fill rate outlet -> CK dari ESB Core (PB dulu). Idempoten (CREATE ... IF NOT EXISTS / CREATE OR
-- REPLACE VIEW; tanpa ALTER tabel G41/G65). Dijalankan clickhouse-init.sh setiap deploy setelah 01..23.
-- Penulis: esb-extract (role esb_writer). Pembaca: efn_insight_definer lewat view efn_insight.* ber-row-policy kelas PT
-- (access/insight-access.sql + insight-esb-policy.sh). TIDAK di-grant ke metabase_reader, efn_t2s/text2sql,
-- forecast_reader, mig_rw (L-267: P&L = data keuangan sensitif per outlet).
-- PDP (L-110): whitelist per endpoint SEBELUM disimpan (creator/editor/authorizer/createdBy/additionalInfo dibuang);
-- tanpa data orang. Retensi: ikut DWH (#59); raw_doc TTL 36 bln.
-- Pembaca wajib pola §25.4: FINAL + hanya versi slice ber-checkpoint per entitas (view di bawah).
-- [B] belum diverifikasi (laporan efn-data-ai-engineer): struktur summaries.details, konvensi tanda beban, arti hpp,
-- arti qty GTR, arah origin/destination GTR, daftar status (ITEM_JOURNAL_COUNTED_STATUS, GTR_COUNTED_STATUS), arti
-- bypassGoodsDelivery, referenceNumber header vs baris.

-- ============================================================== tabel
-- Satu slice = brand x cabang (scope 'branch') atau total brand (scope 'brand', branch_id 0) x TAHUN (slice_year;
-- checkpoint day = 1 Januari). Tarik ulang mengganti SEMUA bulan tahun itu (view menyaring versi slice).
-- line_type 'account' = baris akun per seksi top-level; 'summary' = pos summaries ESB (section = nama pos, account_no '').
-- source_lines > 1 = akun sama muncul > 1 kali dalam seksi x bulan (dijumlah; cek dup_account).
CREATE TABLE IF NOT EXISTS efn_dwh.fact_esb_profit_loss
(
    brand          LowCardinality(String),
    company_code   LowCardinality(String),
    period_month   Date,                            -- tanggal 1
    scope          LowCardinality(String),          -- branch | brand
    branch_id      UInt32,                          -- 0 untuk scope 'brand'
    line_type      LowCardinality(String),          -- account | summary
    section        LowCardinality(String),
    account_no     String,                          -- "4 1 10 10" (spasi tunggal), '' untuk summary
    account_key    String,                          -- "4.1.10.10"
    account_name   String,
    amount         Decimal(20, 2),                  -- tanda apa adanya (konvensi tanda beban [B], Q-ESB34)
    slice_year     UInt16,
    source_lines   UInt16,
    _version       DateTime64(3, 'UTC'),
    _loaded_at     DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY toYear(period_month)
ORDER BY (company_code, brand, scope, branch_id, period_month, line_type, section, account_no);

-- Satu slice = brand x hari jurnal (journal_date = hari slice). Kunci baris: detail_id = `ID` detail ESB (line_no 0);
-- bila ada ID kosong/0/ganda dalam dokumen -> seluruh dokumen detail_id 0, line_no 1..n stabil (L-113).
-- value = qty x hpp (Decimal(18,2) ROUND_HALF_UP; qty negatif = keluar). Semua status disimpan.
CREATE TABLE IF NOT EXISTS efn_dwh.fact_esb_item_journal_line
(
    brand              LowCardinality(String),
    company_code       LowCardinality(String),
    journal_date       Date,
    journal_num        String,
    detail_id          UInt64,
    line_no            UInt32,
    branch_id          UInt32,
    location_id        UInt32,
    location_name      String,
    status_id          Int32,
    status_name        LowCardinality(String),
    template_name      String,                      -- requestTemplateName (maks 128)
    purpose_id         UInt32,
    purpose_name       LowCardinality(String),
    product_id         UInt64,
    product_detail_id  UInt64,
    product_name       String,
    uom                LowCardinality(String),
    qty                Decimal(18, 3),
    hpp                Decimal(18, 4),              -- biaya per satuan baris [B]
    value              Decimal(18, 2),
    _version           DateTime64(3, 'UTC'),
    _loaded_at         DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY toYYYYMM(journal_date)
ORDER BY (company_code, brand, journal_date, journal_num, detail_id, line_no);

-- Satu slice = brand x hari permintaan (transfer_date = hari slice). Satu baris per (dokumen, product_code, uom);
-- baris ganda dijumlah. requester = destinationBranchID, supplier = originBranchID [B]. company_code = PT peminta.
CREATE TABLE IF NOT EXISTS efn_dwh.fact_esb_transfer_request_line
(
    brand                 LowCardinality(String),
    company_code          LowCardinality(String),
    transfer_date         Date,
    transfer_num          String,
    requester_branch_id   UInt32,
    supplier_branch_id    UInt32,
    status_id             Int32,
    status_name           LowCardinality(String),
    category_type         LowCardinality(String),
    bypass_gd             UInt8,
    finished              UInt8,
    purchase_request_num  String,
    product_id            UInt64,
    product_detail_id     UInt64,
    product_code          String,                   -- '' bila tak terpetakan (cek kualitas)
    product_name          String,
    uom                   LowCardinality(String),
    request_qty           Decimal(18, 3),           -- diminta outlet (pembanding fill rate, Q-ESB31)
    qty                   Decimal(18, 3),           -- qty dokumen ("disetujui"? [B])
    pending_qty           Decimal(18, 3),
    available_qty         Decimal(18, 3),
    stock_qty             Decimal(18, 3),
    _version              DateTime64(3, 'UTC'),
    _loaded_at            DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY toYYYYMM(transfer_date)
ORDER BY (company_code, brand, transfer_date, transfer_num, product_code, uom);

-- Referensi goods delivery -> transfer request (L-262; tabel baru, fact_esb_delivery TIDAK diubah). Ditulis slice
-- `delivery` (checkpoint entitas delivery_ref sendiri) dan mode retransform dari raw_doc. qty kirim/terima TERPISAH.
CREATE TABLE IF NOT EXISTS efn_dwh.fact_esb_delivery_ref_line
(
    brand             LowCardinality(String),
    company_code      LowCardinality(String),         -- PT cabang asal (sama fact_esb_delivery)
    delivery_date     Date,
    doc_num           String,
    reference_num     String,                         -- transferNum GTR (awalan TF)
    trans_type        LowCardinality(String),         -- 'Transfer Request'
    ref_date          Nullable(Date),
    origin_branch_id  UInt32,
    dest_branch_id    UInt32,
    product_code      String,
    uom               LowCardinality(String),
    qty_sent          Decimal(18, 3),                 -- Σ deliveryQty
    qty_received      Decimal(18, 3),                 -- Σ receiptQty (0 bila belum diterima)
    _version          DateTime64(3, 'UTC'),
    _loaded_at        DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY toYYYYMM(delivery_date)
ORDER BY (company_code, brand, delivery_date, doc_num, reference_num, product_code, uom);

-- Pemetaan productID ESB -> productCode (detail jurnal item tanpa productCode). Dimensi: versi terbaru menang, tanpa
-- filter checkpoint. source 'gtr' (detail transfer request); 'master'/'stock_opening' dicadangkan.
CREATE TABLE IF NOT EXISTS efn_dwh.dim_esb_product_id
(
    brand         LowCardinality(String),
    company_code  LowCardinality(String),
    product_id    UInt64,
    product_code  String,
    source        LowCardinality(String),
    _version      DateTime64(3, 'UTC'),
    _loaded_at    DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
ORDER BY (brand, product_id);

-- ============================================================== view (INVOKER; nama & kolom = docs/04 §31.6)
-- Bulan x brand x PT x scope x cabang x seksi x akun. Laba memakai baris summary ESB (sumber resmi), bukan rumus EFN.
-- is_partial = bulan berjalan WIB (angka sementara).
CREATE OR REPLACE VIEW efn_dwh.v_esb_pl_monthly AS
SELECT
    p.period_month AS period_month, p.brand AS brand, CAST(p.company_code, 'String') AS company_code,
    CAST(p.scope, 'String') AS scope, p.branch_id AS branch_id,
    if(p.scope = 'brand', '(Total brand)',
       if(b.branch_name = '', concat('Cabang #', toString(p.branch_id)), b.branch_name)) AS branch_name,
    CAST(p.line_type, 'String') AS line_type, CAST(p.section, 'String') AS section, p.account_no AS account_no,
    p.account_key AS account_key, p.account_name AS account_name, p.amount AS amount,
    toUInt8(p.period_month = toStartOfMonth(toDate(now(), 'Asia/Jakarta'))) AS is_partial
FROM
(
    SELECT period_month, brand, company_code, scope, branch_id, line_type, section, account_no, account_key,
           account_name, amount
    FROM efn_dwh.fact_esb_profit_loss FINAL
    WHERE (brand, if(scope = 'brand', '*', toString(branch_id)), makeDate(slice_year, 1, 1), _version) IN
          (SELECT brand, scope_key, day, finished_at FROM efn_esb.extract_checkpoint FINAL
           WHERE entity = 'profit_loss')
) AS p
LEFT JOIN (SELECT brand, branch_id, branch_name FROM efn_dwh.dim_esb_branch FINAL) AS b
    ON b.brand = p.brand AND b.branch_id = p.branch_id;

-- Internal (tidak dibungkus efn_insight): baris jurnal item versi slice terakhir + kelas purpose + status dihitung.
-- ITEM_JOURNAL_COUNTED_STATUS = ('authorized', 'finished', 'closed') (nama status, lower/trim) -- default sementara
-- [B]; diganti dari distribusi status hasil backfill (laporan W-A). Kelas purpose (L-265, ubah lewat PR):
--   WASTE -> waste; PRODUCTION/PACKAGING/SELLING/CONSUMABLES/CLEANING/SAFETY/STATIONARY SUPPLIES + UNIFORM ->
--   internal_use (PRODUCTION SUPPLIES menunggu Q-ESB30); RESEARCH & DEVELOPMENT -> rnd; OTHER RECEIVABLE -> other;
--   lainnya -> unclassified.
CREATE OR REPLACE VIEW efn_dwh.v_esb_item_journal_line AS
SELECT
    j.journal_date AS journal_date, j.brand AS brand, CAST(j.company_code, 'String') AS company_code,
    j.branch_id AS branch_id, j.journal_num AS journal_num, CAST(j.status_name, 'String') AS status_name,
    toUInt8(lower(trimBoth(CAST(j.status_name, 'String'))) IN ('authorized', 'finished', 'closed')) AS is_counted,
    multiIf(
        j.pn = 'WASTE', 'waste',
        j.pn IN ('PRODUCTION SUPPLIES', 'PACKAGING CONSUMPTION', 'SELLING SUPPLIES', 'CONSUMABLES SUPPLIES',
                 'CLEANING SUPPLIES', 'SAFETY SUPPLIES', 'STATIONARY SUPPLIES', 'UNIFORM'), 'internal_use',
        j.pn = 'RESEARCH & DEVELOPMENT', 'rnd',
        j.pn = 'OTHER RECEIVABLE', 'other',
        'unclassified') AS usage_class,
    j.pn AS purpose_name, j.product_id AS product_id, j.product_name AS product_name,
    CAST(j.uom, 'String') AS uom, j.qty AS qty, j.value AS value
FROM
(
    SELECT journal_date, brand, company_code, branch_id, journal_num, status_name, product_id, product_name, uom,
           qty, value, upperUTF8(trimBoth(CAST(purpose_name, 'String'))) AS pn
    FROM efn_dwh.fact_esb_item_journal_line FINAL
    WHERE (brand, journal_date, _version) IN
          (SELECT brand, day, finished_at FROM efn_esb.extract_checkpoint FINAL
           WHERE entity = 'item_journal' AND scope_key = '*')
) AS j;

-- Hari x brand x PT x cabang x kelas x purpose. Hanya ITEM_JOURNAL_COUNTED_STATUS. value_out = nilai keluar (qty < 0,
-- positif), value_in = nilai masuk (qty > 0), value_net = -Σ value (positif = biaya keluar). docs = dokumen unik
-- (TIDAK aditif lintas purpose/kelas).
CREATE OR REPLACE VIEW efn_dwh.v_esb_item_usage_daily AS
SELECT
    a.journal_date AS journal_date, a.brand AS brand, a.company_code AS company_code, a.branch_id AS branch_id,
    if(b.branch_name = '', concat('Cabang #', toString(a.branch_id)), b.branch_name) AS branch_name,
    a.usage_class AS usage_class, a.purpose_name AS purpose_name, a.docs_s AS docs, a.lines_s AS lines,
    a.out_s AS value_out, a.in_s AS value_in, a.net_s AS value_net
FROM
(
    SELECT l.journal_date AS journal_date, l.brand AS brand, l.company_code AS company_code, l.branch_id AS branch_id,
           l.usage_class AS usage_class, l.purpose_name AS purpose_name, uniqExact(l.journal_num) AS docs_s,
           count() AS lines_s, -sumIf(l.value, l.qty < 0) AS out_s, sumIf(l.value, l.qty > 0) AS in_s,
           -sum(l.value) AS net_s
    FROM efn_dwh.v_esb_item_journal_line AS l
    WHERE l.is_counted = 1
    GROUP BY journal_date, brand, company_code, branch_id, usage_class, purpose_name
) AS a
LEFT JOIN (SELECT brand, branch_id, branch_name FROM efn_dwh.dim_esb_branch FINAL) AS b
    ON b.brand = a.brand AND b.branch_id = a.branch_id;

-- Bulan x brand x PT x cabang x kelas x produk x satuan. product_code di-resolve SAAT QUERY lewat dim_esb_product_id
-- (L-268: pemetaan yang dipelajari belakangan berlaku untuk data lama); tak terpetakan -> 'ID:<product_id>'.
-- qty_out = -Σ qty per uom (tidak dijumlah lintas satuan).
CREATE OR REPLACE VIEW efn_dwh.v_esb_item_usage_product_monthly AS
SELECT
    g.month AS month, g.brand AS brand, g.company_code AS company_code, g.branch_id AS branch_id,
    g.usage_class AS usage_class, g.purpose_name AS purpose_name, g.p_code AS product_code, g.p_name AS product_name,
    g.uom AS uom, g.qty_s AS qty_out, g.net_s AS value_net
FROM
(
    SELECT toStartOfMonth(l.journal_date) AS month, l.brand AS brand, l.company_code AS company_code,
           l.branch_id AS branch_id, l.usage_class AS usage_class, l.purpose_name AS purpose_name,
           if(m.product_code = '', concat('ID:', toString(l.product_id)), m.product_code) AS p_code,
           any(l.product_name) AS p_name, l.uom AS uom, -sum(l.qty) AS qty_s, -sum(l.value) AS net_s
    FROM efn_dwh.v_esb_item_journal_line AS l
    LEFT JOIN (SELECT brand, product_id, product_code FROM efn_dwh.dim_esb_product_id FINAL) AS m
        ON m.brand = l.brand AND m.product_id = l.product_id
    WHERE l.is_counted = 1
    GROUP BY month, brand, company_code, branch_id, usage_class, purpose_name, p_code, uom
) AS g;

-- Internal (tidak dibungkus efn_insight): satu baris per baris permintaan GTR (versi slice terakhir), L-263:
--   qty_sent / qty_received = Σ fact_esb_delivery_ref_line trans_type 'Transfer Request' dengan reference_num =
--     transfer_num, product_code DAN uom sama, versi slice delivery_ref terakhir, SEMUA tanggal kirim;
--   uom_mismatch = ada kiriman produk itu untuk permintaan itu tetapi tidak satu pun bersatuan sama (tanpa konversi);
--   first_delivery_date = kiriman pertama bersatuan sama dengan qty_sent > 0 (GD qty 0 diabaikan); NULL bila tidak
--     ada atau bila kiriman pertama itu bertanggal SEBELUM tanggal permintaan (data tidak wajar, QA P3);
--   is_mature = finished = 1 ATAU transfer_date <= hari ini WIB - FILL_RATE_MATURE_DAYS (7);
--   is_counted = matang DAN status_id IN GTR_COUNTED_STATUS (3, 6, 8: 8 = Finished, data PB 25 Nov) [B] DAN bypass_gd = 0 DAN request_qty > 0 DAN
--     product_code != '' (kode tak terpetakan tidak bisa dicocokkan ke GD; dihitung gtr_product_unmapped, QA P2#4).
CREATE OR REPLACE VIEW efn_dwh.v_esb_fill_rate_line AS
SELECT
    r.transfer_date AS transfer_date, r.brand AS brand, CAST(r.company_code, 'String') AS company_code,
    r.requester_branch_id AS requester_branch_id, r.supplier_branch_id AS supplier_branch_id,
    r.transfer_num AS transfer_num, r.product_code AS product_code, r.product_name AS product_name,
    r.uom_s AS uom, r.request_qty AS request_qty, s.sent_s AS qty_sent, s.recv_s AS qty_received,
    toUInt8(a.hit_any = 1 AND s.hit = 0) AS uom_mismatch,
    if(s.pos_hit = 1 AND s.first_d >= r.transfer_date, s.first_d, NULL) AS first_delivery_date,
    toUInt8(r.finished = 1 OR r.transfer_date <= toDate(now(), 'Asia/Jakarta') - 7) AS is_mature,
    toUInt8((r.finished = 1 OR r.transfer_date <= toDate(now(), 'Asia/Jakarta') - 7)
            AND r.status_id IN (3, 6, 8) AND r.bypass_gd = 0 AND r.request_qty > 0 AND r.product_code != '') AS is_counted
FROM
(
    SELECT transfer_date, brand, company_code, requester_branch_id, supplier_branch_id, transfer_num, product_code,
           product_name, CAST(uom, 'String') AS uom_s, request_qty, status_id, bypass_gd, finished
    FROM efn_dwh.fact_esb_transfer_request_line FINAL
    WHERE (brand, transfer_date, _version) IN
          (SELECT brand, day, finished_at FROM efn_esb.extract_checkpoint FINAL
           WHERE entity = 'transfer_request' AND scope_key = '*')
) AS r
LEFT JOIN
(
    SELECT brand, reference_num, product_code, CAST(uom, 'String') AS uom_s, sum(qty_sent) AS sent_s,
           sum(qty_received) AS recv_s, minIf(delivery_date, qty_sent > 0) AS first_d,
           toUInt8(countIf(qty_sent > 0) > 0) AS pos_hit, toUInt8(1) AS hit
    FROM efn_dwh.fact_esb_delivery_ref_line FINAL
    WHERE trans_type = 'Transfer Request'
      AND (brand, delivery_date, _version) IN
          (SELECT brand, day, finished_at FROM efn_esb.extract_checkpoint FINAL
           WHERE entity = 'delivery_ref' AND scope_key = '*')
    GROUP BY brand, reference_num, product_code, uom_s
) AS s
    ON s.brand = r.brand AND s.reference_num = r.transfer_num AND s.product_code = r.product_code
       AND s.uom_s = r.uom_s
LEFT JOIN
(
    SELECT brand, reference_num, product_code, toUInt8(1) AS hit_any
    FROM efn_dwh.fact_esb_delivery_ref_line FINAL
    WHERE trans_type = 'Transfer Request'
      AND (brand, delivery_date, _version) IN
          (SELECT brand, day, finished_at FROM efn_esb.extract_checkpoint FINAL
           WHERE entity = 'delivery_ref' AND scope_key = '*')
    GROUP BY brand, reference_num, product_code
) AS a
    ON a.brand = r.brand AND a.reference_num = r.transfer_num AND a.product_code = r.product_code;

-- Tanggal permintaan (kohort) x brand x PT peminta x outlet peminta x pemasok. Semua kolom ADITIF; rasio dihitung di
-- query (§31.6.1): fill rate baris = Σ line_ratio_sum / Σ lines; % baris penuh = Σ lines_full / Σ lines;
-- % permintaan penuh = Σ requests_full / Σ requests; waktu kirim = Σ lead_days_sum / Σ lead_days_n.
--   lines/lines_full/lines_zero/line_ratio_sum/lead_* = baris is_counted dan uom_mismatch = 0 (lead_* hanya baris
--   dengan first_delivery_date terisi);
--   line_ratio = least(qty_sent / request_qty, 1) (kirim lebih tidak menaikkan skor);
--   requests = permintaan dengan >= 1 baris terhitung; requests_full = semua baris terhitungnya penuh;
--   lines_uom_mismatch = baris is_counted dengan uom_mismatch = 1 (di luar rasio);
--   lines_open = baris belum matang dengan request_qty > 0 (semua status; "masih berjalan").
CREATE OR REPLACE VIEW efn_dwh.v_esb_fill_rate_daily AS
SELECT
    t.transfer_date AS transfer_date, t.brand AS brand, t.company_code AS company_code,
    t.requester_branch_id AS branch_id,
    if(rb.branch_name = '', concat('Cabang #', toString(t.requester_branch_id)), rb.branch_name) AS branch_name,
    t.supplier_branch_id AS supplier_branch_id,
    if(sb.branch_name = '', concat('Cabang #', toString(t.supplier_branch_id)), sb.branch_name) AS supplier_name,
    t.requests_s AS requests, t.requests_full_s AS requests_full, t.lines_s AS lines, t.lines_full_s AS lines_full,
    t.lines_zero_s AS lines_zero, t.ratio_s AS line_ratio_sum, t.mismatch_s AS lines_uom_mismatch,
    t.open_s AS lines_open, t.lead_sum_s AS lead_days_sum, t.lead_n_s AS lead_days_n
FROM
(
    SELECT q.transfer_date AS transfer_date, q.brand AS brand, q.company_code AS company_code,
           q.requester_branch_id AS requester_branch_id, q.supplier_branch_id AS supplier_branch_id,
           countIf(q.lines_q > 0) AS requests_s, countIf(q.lines_q > 0 AND q.full_q = q.lines_q) AS requests_full_s,
           sum(q.lines_q) AS lines_s, sum(q.full_q) AS lines_full_s, sum(q.zero_q) AS lines_zero_s,
           sum(q.ratio_q) AS ratio_s, sum(q.mismatch_q) AS mismatch_s, sum(q.open_q) AS open_s,
           sum(q.lead_sum_q) AS lead_sum_s, sum(q.lead_n_q) AS lead_n_s
    FROM
    (
        SELECT l.transfer_date AS transfer_date, l.brand AS brand, l.company_code AS company_code,
               l.requester_branch_id AS requester_branch_id, l.supplier_branch_id AS supplier_branch_id,
               l.transfer_num AS transfer_num,
               countIf(l.is_counted = 1 AND l.uom_mismatch = 0) AS lines_q,
               countIf(l.is_counted = 1 AND l.uom_mismatch = 0 AND l.qty_sent >= l.request_qty) AS full_q,
               countIf(l.is_counted = 1 AND l.uom_mismatch = 0 AND l.qty_sent = 0) AS zero_q,
               sumIf(least(toFloat64(l.qty_sent) / toFloat64(l.request_qty), 1.0),
                     l.is_counted = 1 AND l.uom_mismatch = 0) AS ratio_q,
               countIf(l.is_counted = 1 AND l.uom_mismatch = 1) AS mismatch_q,
               countIf(l.is_mature = 0 AND l.request_qty > 0) AS open_q,
               sumIf(toInt64(dateDiff('day', l.transfer_date, assumeNotNull(l.first_delivery_date))),
                     l.is_counted = 1 AND l.uom_mismatch = 0 AND l.first_delivery_date IS NOT NULL) AS lead_sum_q,
               countIf(l.is_counted = 1 AND l.uom_mismatch = 0 AND l.first_delivery_date IS NOT NULL) AS lead_n_q
        FROM efn_dwh.v_esb_fill_rate_line AS l
        GROUP BY transfer_date, brand, company_code, requester_branch_id, supplier_branch_id, transfer_num
    ) AS q
    GROUP BY transfer_date, brand, company_code, requester_branch_id, supplier_branch_id
) AS t
LEFT JOIN (SELECT brand, branch_id, branch_name FROM efn_dwh.dim_esb_branch FINAL) AS rb
    ON rb.brand = t.brand AND rb.branch_id = t.requester_branch_id
LEFT JOIN (SELECT brand, branch_id, branch_name FROM efn_dwh.dim_esb_branch FINAL) AS sb
    ON sb.brand = t.brand AND sb.branch_id = t.supplier_branch_id;

-- Bulan permintaan x brand x PT x outlet peminta x produk x satuan; hanya is_counted dan uom_mismatch = 0.
-- Fill rate qty = sent_capped / request_qty (per produk-satuan saja); sent_capped = Σ least(qty_sent, request_qty).
CREATE OR REPLACE VIEW efn_dwh.v_esb_fill_rate_product_monthly AS
SELECT
    g.month AS month, g.brand AS brand, g.company_code AS company_code, g.branch_id AS branch_id,
    g.product_code AS product_code, g.p_name AS product_name, g.uom AS uom, g.lines_s AS lines,
    g.request_s AS request_qty, g.capped_s AS sent_capped, g.sent_s AS qty_sent, g.recv_s AS qty_received
FROM
(
    SELECT toStartOfMonth(l.transfer_date) AS month, l.brand AS brand, l.company_code AS company_code,
           l.requester_branch_id AS branch_id, l.product_code AS product_code, any(l.product_name) AS p_name,
           l.uom AS uom, count() AS lines_s, sum(l.request_qty) AS request_s,
           sum(if(l.qty_sent < l.request_qty, l.qty_sent, l.request_qty)) AS capped_s, sum(l.qty_sent) AS sent_s,
           sum(l.qty_received) AS recv_s
    FROM efn_dwh.v_esb_fill_rate_line AS l
    WHERE l.is_counted = 1 AND l.uom_mismatch = 0
    GROUP BY month, brand, company_code, branch_id, product_code, uom
) AS g;

-- Cek kualitas G67 (bentuk kolom = v_esb_data_quality). View TERPISAH (tidak dibungkus efn_insight, tidak di-grant
-- ke role aplikasi mana pun): dibaca admin/lead. Jendela 90 hari untuk cek harian.
--   pl_branch_vs_brand        : summary total brand - Σ cabang per pos-bulan (= tidak teralokasi ke outlet, Q-ESB33)
--   pl_sign_check             : porsi baris akun bernilai negatif per seksi beban (0 = beban positif) + selisih
--                               |netProfitLoss - rumus fallback| per brand (0 = rumus §31.1.1 cocok)
--   dup_account               : baris akun dengan source_lines > 1
--   journal_product_unmapped  : product_id jurnal item (dihitung) tanpa pemetaan dim_esb_product_id
--   unknown_status            : status di luar ITEM_JOURNAL_COUNTED_STATUS / GTR_COUNTED_STATUS
--   fill_rate_uom_mismatch    : baris GTR terhitung dengan kiriman beda satuan
--   delivery_ref_unmatched    : dokumen GD ber-referensi 'TF' tanpa GTR yang cocok
--   gtr_without_delivery_mature: baris GTR terhitung tanpa kiriman sama sekali (qty_sent = 0)
--   gtr_bypass_gd             : baris GTR bypassGoodsDelivery = 1 (dikeluarkan dari rasio)
--   gtr_product_unmapped      : baris GTR dengan product_code ''
CREATE OR REPLACE VIEW efn_dwh.v_esb_g67_quality AS
SELECT 'pl_branch_vs_brand' AS check_name, d.brand AS brand, d.c_code AS company_code,
       concat(d.section, ' ', toString(d.period_month)) AS item, toFloat64(d.diff) AS value, d.period_month AS last_day
FROM
(
    SELECT x.brand AS brand, x.section AS section, x.period_month AS period_month,
           anyIf(x.company_code, x.scope = 'brand') AS c_code,
           sumIf(x.amount, x.scope = 'brand') - sumIf(x.amount, x.scope = 'branch') AS diff
    FROM efn_dwh.v_esb_pl_monthly AS x
    WHERE x.line_type = 'summary'
    GROUP BY brand, section, period_month
    HAVING countIf(x.scope = 'brand') > 0
) AS d
UNION ALL
SELECT 'pl_sign_check', s.brand, s.c_code, concat('neg_share:', s.section), s.neg_share, s.last_m
FROM
(
    SELECT x.brand AS brand, any(x.company_code) AS c_code, x.section AS section,
           countIf(x.amount < 0) / count() AS neg_share, max(x.period_month) AS last_m
    FROM efn_dwh.v_esb_pl_monthly AS x
    WHERE x.line_type = 'account' AND x.scope = 'branch'
      AND x.section IN ('cogs', 'operationalExpenses', 'otherExpenses', 'depreciationAmortizationExpenses',
                        'interests', 'taxes')
    GROUP BY brand, section
) AS s
UNION ALL
SELECT 'pl_sign_check', f.brand, f.c_code, 'formula_abs_diff', f.abs_diff, f.last_m
FROM
(
    SELECT y.brand AS brand, any(y.company_code) AS c_code, sum(abs(y.np - y.formula)) AS abs_diff,
           max(y.period_month) AS last_m
    FROM
    (
        SELECT x.brand AS brand, x.company_code AS company_code, x.branch_id AS branch_id,
               x.period_month AS period_month,
               toFloat64(sumIf(x.amount, x.section = 'netProfitLoss')) AS np,
               toFloat64(sumIf(x.amount, x.section = 'sales') - sumIf(x.amount, x.section = 'discounts')
                         - sumIf(x.amount, x.section = 'cogs') - sumIf(x.amount, x.section = 'operationalExpenses')
                         - sumIf(x.amount, x.section = 'otherExpenses')
                         - sumIf(x.amount, x.section = 'depreciationAmortizationExpenses')
                         - sumIf(x.amount, x.section = 'interests') - sumIf(x.amount, x.section = 'taxes')) AS formula
        FROM efn_dwh.v_esb_pl_monthly AS x
        WHERE x.line_type = 'summary' AND x.scope = 'branch'
        GROUP BY brand, company_code, branch_id, period_month
    ) AS y
    GROUP BY brand
) AS f
UNION ALL
SELECT 'dup_account', u.brand, u.c_code, concat(u.section, ' ', u.account_no), toFloat64(u.n), u.last_m
FROM
(
    SELECT brand, any(company_code) AS c_code, section, account_no, count() AS n, max(period_month) AS last_m
    FROM efn_dwh.fact_esb_profit_loss FINAL
    WHERE source_lines > 1
      AND (brand, if(scope = 'brand', '*', toString(branch_id)), makeDate(slice_year, 1, 1), _version) IN
          (SELECT brand, scope_key, day, finished_at FROM efn_esb.extract_checkpoint FINAL
           WHERE entity = 'profit_loss')
    GROUP BY brand, section, account_no
) AS u
UNION ALL
SELECT 'journal_product_unmapped', j.brand, j.company_code, 'product_id', toFloat64(j.n), j.last_d
FROM
(
    SELECT l.brand AS brand, l.company_code AS company_code, uniqExact(l.product_id) AS n,
           max(l.journal_date) AS last_d
    FROM efn_dwh.v_esb_item_journal_line AS l
    LEFT JOIN (SELECT brand, product_id, toUInt8(1) AS hit FROM efn_dwh.dim_esb_product_id FINAL) AS m
        ON m.brand = l.brand AND m.product_id = l.product_id
    WHERE l.is_counted = 1 AND m.hit = 0 AND l.journal_date >= today() - 90
    GROUP BY brand, company_code
) AS j
UNION ALL
SELECT 'unknown_status', k.brand, k.company_code, concat('item_journal:', k.status_name), toFloat64(k.n), k.last_d
FROM
(
    SELECT brand, company_code, status_name, count() AS n, max(journal_date) AS last_d
    FROM efn_dwh.v_esb_item_journal_line
    WHERE is_counted = 0 AND journal_date >= today() - 90
    GROUP BY brand, company_code, status_name
) AS k
UNION ALL
SELECT 'unknown_status', g.brand, g.c_code, concat('transfer_request:', toString(g.status_id), ' ', g.status_name),
       toFloat64(g.n), g.last_d
FROM
(
    SELECT brand, CAST(company_code, 'String') AS c_code, status_id, CAST(status_name, 'String') AS status_name,
           count() AS n, max(transfer_date) AS last_d
    FROM efn_dwh.fact_esb_transfer_request_line FINAL
    WHERE status_id NOT IN (3, 6, 8) AND transfer_date >= today() - 90
      AND (brand, transfer_date, _version) IN
          (SELECT brand, day, finished_at FROM efn_esb.extract_checkpoint FINAL
           WHERE entity = 'transfer_request' AND scope_key = '*')
    GROUP BY brand, c_code, status_id, status_name
) AS g
UNION ALL
SELECT 'fill_rate_uom_mismatch', m.brand, m.company_code, 'lines', toFloat64(m.n), m.last_d
FROM
(
    SELECT brand, company_code, count() AS n, max(transfer_date) AS last_d
    FROM efn_dwh.v_esb_fill_rate_line
    WHERE is_counted = 1 AND uom_mismatch = 1 AND transfer_date >= today() - 90
    GROUP BY brand, company_code
) AS m
UNION ALL
SELECT 'delivery_ref_unmatched', z.brand, z.c_code, 'docs', toFloat64(z.n), z.last_d
FROM
(
    SELECT d.brand AS brand, CAST(d.company_code, 'String') AS c_code, uniqExact(d.doc_num) AS n,
           max(d.delivery_date) AS last_d
    FROM
    (
        SELECT brand, company_code, delivery_date, doc_num, reference_num
        FROM efn_dwh.fact_esb_delivery_ref_line FINAL
        WHERE startsWith(reference_num, 'TF') AND delivery_date >= today() - 90
          AND (brand, delivery_date, _version) IN
              (SELECT brand, day, finished_at FROM efn_esb.extract_checkpoint FINAL
               WHERE entity = 'delivery_ref' AND scope_key = '*')
    ) AS d
    LEFT JOIN
    (
        SELECT DISTINCT brand, transfer_num, toUInt8(1) AS hit
        FROM efn_dwh.fact_esb_transfer_request_line FINAL
        WHERE (brand, transfer_date, _version) IN
              (SELECT brand, day, finished_at FROM efn_esb.extract_checkpoint FINAL
               WHERE entity = 'transfer_request' AND scope_key = '*')
    ) AS t
        ON t.brand = d.brand AND t.transfer_num = d.reference_num
    WHERE t.hit = 0
    GROUP BY brand, c_code
) AS z
UNION ALL
SELECT 'gtr_without_delivery_mature', w.brand, w.company_code, 'lines', toFloat64(w.n), w.last_d
FROM
(
    SELECT brand, company_code, count() AS n, max(transfer_date) AS last_d
    FROM efn_dwh.v_esb_fill_rate_line
    WHERE is_counted = 1 AND uom_mismatch = 0 AND qty_sent = 0 AND transfer_date >= today() - 90
    GROUP BY brand, company_code
) AS w
UNION ALL
SELECT 'gtr_bypass_gd', v.brand, v.c_code, 'lines', toFloat64(v.n), v.last_d
FROM
(
    SELECT brand, CAST(company_code, 'String') AS c_code, count() AS n, max(transfer_date) AS last_d
    FROM efn_dwh.fact_esb_transfer_request_line FINAL
    WHERE bypass_gd = 1 AND transfer_date >= today() - 90
      AND (brand, transfer_date, _version) IN
          (SELECT brand, day, finished_at FROM efn_esb.extract_checkpoint FINAL
           WHERE entity = 'transfer_request' AND scope_key = '*')
    GROUP BY brand, c_code
) AS v
UNION ALL
SELECT 'gtr_product_unmapped', e.brand, e.c_code, 'lines', toFloat64(e.n), e.last_d
FROM
(
    SELECT brand, CAST(company_code, 'String') AS c_code, count() AS n, max(transfer_date) AS last_d
    FROM efn_dwh.fact_esb_transfer_request_line FINAL
    WHERE product_code = '' AND transfer_date >= today() - 90
      AND (brand, transfer_date, _version) IN
          (SELECT brand, day, finished_at FROM efn_esb.extract_checkpoint FINAL
           WHERE entity = 'transfer_request' AND scope_key = '*')
    GROUP BY brand, c_code
) AS e;
