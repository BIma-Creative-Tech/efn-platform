-- Gelombang 22 (efn-data-ai-engineer): (a) mart KPI dapur dari tiket KDS Odoo (POS + F&B online E-29a, docs/04 §14.B,
-- docs/08 §7.3 F-54), (b) rekonsiliasi poin online per kanal (docs/04 §14.C.5).
-- Idempoten (CREATE TABLE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS / CREATE OR REPLACE VIEW). Dijalankan
-- clickhouse-init.sh setiap deploy setelah 01..09.
--
-- PDP: tiket KDS TANPA label meja, nomor antrean, catatan dapur, nama produk, atau data pelanggan (efn_etl/kds.py;
-- PDP_DATA_INVENTORY §2.2b). Rekonsiliasi poin: nominal agregat per PT x hari, tanpa member/partner/part_id.

-- ============================================================== (a) KDS
-- Satu baris per tiket (order x stasiun x pengiriman). Versi = write_date Odoo (bump/recall menulis ulang tiket).
-- Durasi detik (NULL = belum terjadi / cap waktu tidak konsisten): queue = mulai - kirim, cook = siap - mulai,
-- wait = siap - kirim (KPI F-54), serve = disajikan - siap. channel: pos (POS/QR) | online (SO via jembatan E-29a).
CREATE TABLE IF NOT EXISTS efn_dwh.fact_kds_ticket
(
    ticket_id        UInt64,
    company_id       UInt32,
    brand            LowCardinality(String),
    channel          LowCardinality(String),
    station_id       UInt32,
    store_id         UInt32,                       -- pos.config (0 = tidak ada, mis. tiket online tanpa config)
    order_id         UInt64,                       -- pos.order (0 untuk online)
    online_order_id  UInt64,                       -- efn.online.order (0 untuk POS)
    preset_name      LowCardinality(String),       -- preset POS (makan di tempat / bawa pulang), bukan data pribadi
    course_index     Int32,
    sequence_no      UInt32,
    state            LowCardinality(String),       -- new | in_progress | ready | served | cancelled
    is_rush          UInt8,
    printed_offline  UInt8,
    sent_at          DateTime('Asia/Jakarta'),
    started_at       Nullable(DateTime('Asia/Jakarta')),
    ready_at         Nullable(DateTime('Asia/Jakarta')),
    served_at        Nullable(DateTime('Asia/Jakarta')),
    cancelled_at     Nullable(DateTime('Asia/Jakarta')),
    queue_seconds    Nullable(UInt32),
    cook_seconds     Nullable(UInt32),
    wait_seconds     Nullable(UInt32),
    serve_seconds    Nullable(UInt32),
    lines            UInt32,
    items            Decimal(14, 3),                -- Σ qty baris positif (tambah)
    items_cancelled  Decimal(14, 3),                -- Σ qty baris negatif (batal/kurang), positif
    write_date       DateTime64(6, 'UTC'),
    _loaded_at       DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(write_date)
PARTITION BY toYYYYMM(sent_at)
ORDER BY (company_id, station_id, ticket_id);

-- Stasiun dapur: nama + target menit (PLACEHOLDER Q-19). Token kanal/perangkat tidak disalin.
CREATE TABLE IF NOT EXISTS efn_dwh.dim_kds_station
(
    station_id    UInt32,
    name          String,
    company_id    UInt32,
    mode          LowCardinality(String),         -- kds | kds_printer | printer
    warn_minutes  UInt32,
    late_minutes  UInt32,
    active        UInt8,
    write_date    DateTime64(6, 'UTC'),
    _loaded_at    DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(write_date)
ORDER BY station_id;

-- KPI dapur per hari x PT x kanal x outlet x stasiun (Metabase). Tiket batal tidak ikut waktu tunggu.
-- late = waktu tunggu > late_minutes stasiun SAAT INI (target historis tidak disimpan; ubah target = ubah histori).
CREATE OR REPLACE VIEW efn_dwh.v_kds_ticket_daily AS
SELECT
    t.day AS day, t.company_id AS company_id, t.brand AS brand, t.channel AS channel,
    t.store_id AS store_id,
    multiIf(t.store_id = 0, 'Online', o.name = '', concat('POS #', toString(t.store_id)), o.name) AS store_name,
    t.station_id AS station_id, if(s.name = '', concat('Stasiun #', toString(t.station_id)), s.name) AS station_name,
    t.tickets AS tickets, t.tickets_cancelled AS tickets_cancelled, t.tickets_ready AS tickets_ready,
    t.tickets_rush AS tickets_rush, t.tickets_offline AS tickets_offline, t.items AS items,
    t.avg_queue_seconds AS avg_queue_seconds, t.avg_cook_seconds AS avg_cook_seconds,
    t.avg_wait_seconds AS avg_wait_seconds, t.p50_wait_seconds AS p50_wait_seconds,
    t.p90_wait_seconds AS p90_wait_seconds, t.avg_serve_seconds AS avg_serve_seconds,
    t.late_minutes AS late_minutes, t.tickets_late AS tickets_late,
    if(t.tickets_ready = 0 OR t.late_minutes = 0, NULL, round(t.tickets_late / t.tickets_ready * 100, 1)) AS late_pct
FROM
(
    SELECT
        toDate(f.sent_at) AS day, f.company_id AS company_id, f.brand AS brand, f.channel AS channel,
        f.store_id AS store_id, f.station_id AS station_id,
        count()                                                             AS tickets,
        countIf(f.state = 'cancelled')                                      AS tickets_cancelled,
        countIf(f.state != 'cancelled' AND f.wait_seconds IS NOT NULL)      AS tickets_ready,
        countIf(f.is_rush = 1)                                              AS tickets_rush,
        countIf(f.printed_offline = 1)                                      AS tickets_offline,
        sumIf(f.items, f.state != 'cancelled')                              AS items,
        round(avgIf(f.queue_seconds, f.state != 'cancelled'), 0)            AS avg_queue_seconds,
        round(avgIf(f.cook_seconds, f.state != 'cancelled'), 0)             AS avg_cook_seconds,
        round(avgIf(f.wait_seconds, f.state != 'cancelled'), 0)             AS avg_wait_seconds,
        quantileExactIf(0.5)(f.wait_seconds, f.state != 'cancelled')        AS p50_wait_seconds,
        quantileExactIf(0.9)(f.wait_seconds, f.state != 'cancelled')        AS p90_wait_seconds,
        round(avgIf(f.serve_seconds, f.state != 'cancelled'), 0)            AS avg_serve_seconds,
        max(st.late_minutes)                                                AS late_minutes,
        countIf(f.state != 'cancelled' AND st.late_minutes > 0 AND f.wait_seconds > st.late_minutes * 60)
                                                                            AS tickets_late
    FROM
    (
        SELECT sent_at, company_id, brand, channel, store_id, station_id, state, is_rush, printed_offline, items,
               queue_seconds, cook_seconds, wait_seconds, serve_seconds
        FROM efn_dwh.fact_kds_ticket FINAL
    ) AS f
    LEFT JOIN (SELECT station_id, late_minutes FROM efn_dwh.dim_kds_station FINAL) AS st
        ON st.station_id = f.station_id
    GROUP BY day, company_id, brand, channel, store_id, station_id
) AS t
LEFT JOIN (SELECT station_id, name FROM efn_dwh.dim_kds_station FINAL) AS s ON s.station_id = t.station_id
LEFT JOIN (SELECT store_id, name FROM efn_dwh.dim_outlet FINAL) AS o ON o.store_id = t.store_id;

-- Beban dapur per jam (WIB) x kanal x stasiun: tiket masuk & rata-rata waktu tunggu.
CREATE OR REPLACE VIEW efn_dwh.v_kds_ticket_hourly AS
SELECT
    t.day AS day, t.hour AS hour, t.weekday AS weekday, t.company_id AS company_id, t.channel AS channel,
    t.station_id AS station_id, if(s.name = '', concat('Stasiun #', toString(t.station_id)), s.name) AS station_name,
    t.tickets AS tickets, t.avg_wait_seconds AS avg_wait_seconds
FROM
(
    SELECT
        toDate(sent_at) AS day, toHour(sent_at) AS hour, toDayOfWeek(sent_at) AS weekday,
        company_id, channel, station_id,
        countIf(state != 'cancelled')                         AS tickets,
        round(avgIf(wait_seconds, state != 'cancelled'), 0)   AS avg_wait_seconds
    FROM efn_dwh.fact_kds_ticket FINAL
    GROUP BY day, hour, weekday, company_id, channel, station_id
) AS t
LEFT JOIN (SELECT station_id, name FROM efn_dwh.dim_kds_station FINAL) AS s ON s.station_id = t.station_id;

-- ============================================================== (b) Rekonsiliasi poin per kanal (§14.C.5)
-- Baris lama (G18..G21) = POS. Kanal online: odoo_points_payment_idr = Σ pembayaran Poin EFN faktur − nota kredit SO,
-- bff_redeem_idr = Σ online_redeem x nilai poin, bff_restored_idr = bff_restored_points_idr = Σ online_restore x nilai
-- poin (nilai Rupiah restore online tidak dibuka BFF ke efn_readonly -> selisih kecil < nilai poin per refund wajar),
-- orders = jumlah bagian online (part), orders_mismatched = bagian yang redeem-nya tidak cocok.
ALTER TABLE efn_dwh.loyalty_points_daily ADD COLUMN IF NOT EXISTS channel LowCardinality(String) DEFAULT 'pos';

-- POS saja (perilaku view G18 dipertahankan untuk dashboard lama; tanpa filter, argMax mencampur kanal).
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
    argMax(orders_mismatched, run_at)        AS orders_mismatched,
    argMax(bff_restored_points_idr, run_at)  AS bff_restored_points_idr,
    argMax(restore_value_mismatched, run_at) AS restore_value_mismatched
FROM efn_dwh.loyalty_points_daily
WHERE channel = 'pos'
GROUP BY day, company_code;

-- Per kanal (pos | online), hasil run terakhir per hari x PT x kanal.
CREATE OR REPLACE VIEW efn_dwh.v_loyalty_points_daily_channel AS
SELECT
    day,
    company_code,
    channel,
    argMax(run_at, run_at)                   AS last_run_at,
    argMax(odoo_points_payment_idr, run_at)  AS odoo_points_payment_idr,
    argMax(bff_redeem_idr, run_at)           AS bff_redeem_idr,
    argMax(bff_restored_idr, run_at)         AS bff_restored_idr,
    argMax(diff_idr, run_at)                 AS diff_idr,
    argMax(orders, run_at)                   AS orders,
    argMax(orders_mismatched, run_at)        AS orders_mismatched,
    argMax(bff_restored_points_idr, run_at)  AS bff_restored_points_idr,
    argMax(restore_value_mismatched, run_at) AS restore_value_mismatched
FROM efn_dwh.loyalty_points_daily
GROUP BY day, company_code, channel;

-- Persamaan kontrak §14.C.5 per PT x hari: Σ jurnal Poin EFN (POS + faktur SO online) vs Σ redeem − Σ restore.
-- Kolom sumber diberi prefiks `c_` di subquery: alias keluaran yang sama dengan nama kolom sumber membuat ClickHouse
-- 24.8 menyulih alias ke dalam argumen agregat (ILLEGAL_AGGREGATION, Code 184).
CREATE OR REPLACE VIEW efn_dwh.v_loyalty_points_daily_total AS
SELECT
    c.c_day                                                  AS day,
    c.c_company_code                                         AS company_code,
    max(c.c_last_run_at)                                     AS last_run_at,
    sum(c.c_odoo_idr)                                        AS odoo_points_payment_idr,
    sum(c.c_redeem_idr)                                      AS bff_redeem_idr,
    sum(c.c_restored_idr)                                    AS bff_restored_idr,
    sum(c.c_diff_idr)                                        AS diff_idr,
    sumIf(c.c_odoo_idr, c.c_channel = 'pos')                 AS pos_odoo_points_payment_idr,
    sumIf(c.c_odoo_idr, c.c_channel = 'online')              AS online_odoo_points_payment_idr,
    sum(c.c_orders_mismatched)                               AS orders_mismatched,
    sum(c.c_restore_mismatched)                              AS restore_value_mismatched
FROM
(
    SELECT
        day AS c_day, company_code AS c_company_code, channel AS c_channel, last_run_at AS c_last_run_at,
        odoo_points_payment_idr AS c_odoo_idr, bff_redeem_idr AS c_redeem_idr, bff_restored_idr AS c_restored_idr,
        diff_idr AS c_diff_idr, orders_mismatched AS c_orders_mismatched,
        restore_value_mismatched AS c_restore_mismatched
    FROM efn_dwh.v_loyalty_points_daily_channel
) AS c
GROUP BY c.c_day, c.c_company_code;
