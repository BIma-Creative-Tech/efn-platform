# etl — PostgreSQL -> ClickHouse incremental

**Tujuan:** memuat data operasional ke data mart ClickHouse `efn_dwh` setiap 15 menit.
**ADR:** ADR-003 (batch 15 menit, D-02), ADR-007/D-06 (PostgreSQL source of truth), ADR-020 (ledger poin). Status `Proposed`.
**Risiko:** R-O03 (ETL membebani Odoo -> incremental + keyset + replica + statement_timeout), R-L01 (UU PDP -> hash member, geo dibulatkan).

## Job

| Job | Sumber | Target | Cursor |
|---|---|---|---|
| `competitor_prices` | `platform.price_hunter_report` (status akhir) | `competitor_prices` | `updated_at`, `id` |
| `loyalty_events` | `platform.loyalty_ledger` (append-only) | `loyalty_events` | `created_at`, `id` |
| `sales_fact_pos` (`ETL_ENABLE_ODOO=true`, kolom Odoo 19.0) | Odoo `pos_order_line` + `pos_order` | `fact_sales_line` | `l.write_date`, `l.id` |
| `privacy_erasure` (`ETL_ERASURE_ENABLED`, default `true`) | `platform.dsar_request` (`processing`, `clickhouse` ∈ `stores_pending`) | `ALTER TABLE ... DELETE` di `competitor_prices`, `loyalty_events` + publish `privacy.erasure.completed` | tiap siklus, sebelum job lain |
| `companies`, `gl_accounts`, `gl_lines`, `gl_lines_by_move` (`ETL_ENABLE_GL=true`, G12) | Odoo `res_company`, `account_account` (+ rel company), `account_move_line` + `account_move` | `dim_company`, `dim_gl_account`, `fact_gl_line` | `write_date` (`gl_lines_by_move`: `account_move.write_date`) |
| `audit_copy` (`ETL_AUDIT_COPY_ENABLED=true`, G12) | Odoo `efn_audit_log` + `efn_audit_log_line` | `audit_log_copy`, `audit_log_line_copy` (MergeTree append-only), `audit_recon_run` | id (keyset) + jendela settle/late |
| `pos_outlets`, `products`, `pos_payments` (`ETL_ENABLE_ODOO=true`, G13) | Odoo `pos_config`; `product_product`+`product_template`; `pos_payment`+`pos_payment_method` | `dim_outlet`, `dim_product`, `fact_pos_payment` | `write_date` (produk: `GREATEST(variant, template)`) |
| `loyalty_recon` (`ETL_LOYALTY_RECON_ENABLED`, default = `ETL_ENABLE_ODOO`, G14) | platform `loyalty_pos_earn`, `loyalty_ledger` (`pos_*`), `loyalty_reservation` + Odoo `pos_order`, baris Poin EFN, `efn_pos_loyalty_outbox`, `efn_pos_loyalty_reservation` | `loyalty_recon_run`, `loyalty_recon_finding` + log `EFN_LOYALTY_RECON_ETL` | harian (`ETL_LOYALTY_RECON_INTERVAL_SECONDS`), jendela 72 jam |
| `stock_snapshot` (`ETL_ENABLE_STOCK`, default = `ETL_ENABLE_ODOO`, G13) | Odoo `stock_quant` (lokasi internal) + `stock_lot` + `stock_warehouse_orderpoint` | `fact_stock_snapshot`, `fact_orderpoint_snapshot`, penanda `stock_snapshot_run` | snapshot penuh tiap `ETL_STOCK_SNAPSHOT_INTERVAL_SECONDS` (3600) + saat ganti tanggal WIB |

### Mart Fase 1 (Gelombang 13) — docs/01 §7 "ETL dasar + 3 dashboard Metabase"
Skema: `infra/clickhouse/init/04-fase1-marts.sql`. Dashboard: `infra/metabase/`.
- **Penjualan POS**: `fact_sales_line` + `order_id`, `is_refund` (`pos_order.is_refund` / `refunded_orderline_id`), `line_kind` (`product|discount|reward|points|tip`: produk "Poin EFN" = `default_code` di `ETL_POINTS_PRODUCT_CODES`, default `EFN-POINTS` — kontrak dengan modul POS loyalty; reward loyalty Odoo; produk diskon/tip `pos_config`), `discount_amount` (diskon persen baris, basis tanpa pajak). Metode bayar: `fact_pos_payment` (tanpa data kartu/nomor referensi). View: `v_pos_sales_daily`, `v_pos_outlet_daily`, `v_pos_sales_hourly`, `v_pos_payment_daily`.
- **Stok & kedaluwarsa**: snapshot penuh dalam satu transaksi `REPEATABLE READ READ ONLY` (Odoo menghapus quant nol, jadi incremental tidak cukup). Snapshot hanya terlihat setelah penanda `stock_snapshot_run` ditulis (snapshot setengah jadi tidak tampil). View: `v_stock_daily` (snapshot terakhir per tanggal), `v_stock_current`, `v_stock_expiring` (lot ≤ 7 hari, termasuk yang sudah lewat), `v_stock_below_reorder` (on-hand per gudang ≤ `product_min_qty`; Odoo memakai stok perkiraan per lokasi — pendekatan).
- **Price Hunter**: `v_price_index` = laporan valid yang termatch ke `dim_product` lewat `product_sku = default_code` atau nama dinormalisasi (lowercase, spasi dirapatkan); indeks = harga pesaing / `list_price` × 100, plus terhadap harga POS rata-rata 30 hari. `v_price_hunter_match_rate` menunjukkan porsi laporan yang bisa dipakai.
- **Backfill** kolom baru `fact_sales_line` setelah upgrade: tulis watermark epoch sebagai admin ClickHouse, `INSERT INTO efn_dwh.etl_watermark (job, watermark, rows_loaded, updated_at) VALUES ('sales_fact_pos', toDateTime64('1970-01-01 00:00:00', 6, 'UTC'), 0, now64(6, 'UTC'))` — siklus berikutnya memuat ulang semua baris (ReplacingMergeTree, idempoten).

### Heartbeat & dead-man (SEC-52 butir 1) — Gelombang 13
Setiap job + setiap siklus menulis satu baris log logfmt dan satu baris `efn_dwh.etl_job_run` (retensi 90 hari):

```
EFN_ETL_HEARTBEAT job=<job> status=<ok|error|partial> rows=<n> pages=<n> duration_ms=<n> watermark=<iso|-> ts=<iso>
```

`job` ∈ `cycle` (loop hidup; selalu `ok`), `competitor_prices`, `loyalty_events`, `sales_fact_pos`, `pos_outlets`, `products`, `pos_payments`, `companies`, `gl_accounts`, `gl_lines`, `gl_lines_by_move`, `stock_snapshot`, `audit_copy`, `audit_recon` (hanya saat rekonsiliasi jatuh tempo, default harian), `privacy_erasure` (`partial` bila ada request gagal). Nama job divalidasi `^[a-z0-9_]{1,64}$` (anti log injection). Alert yang diusulkan (Loki ruler, devsecops), contoh:
`absent_over_time({service="etl"} |= "EFN_ETL_HEARTBEAT" | logfmt | job="audit_copy" | status="ok" [1h])` (prod), `job="cycle"` 45m, `job="sales_fact_pos"` 45m, `job="stock_snapshot"` 3h, `job="audit_recon"` 26h. Kartu "Kesegaran data ETL" di Metabase membaca `v_etl_freshness`.

### Verifikasi sink audit + jangkar rantai (SEC-51/52) — Gelombang 14
`efn_etl/sink.py` + `efn_etl/loki.py`, dijalankan di dalam rekonsiliasi audit harian (`audit_recon`) bila
`ETL_AUDIT_SINK_ENABLED` (default: aktif bila `ETL_AUDIT_COPY_ENABLED=true` dan `LOKI_URL` di-set):
- Loki `query_range` lewat `LOKI_URL` (`https://loki:3100` = loki-tls) dengan mTLS: sertifikat klien CN `etl`
  (`LOKI_CLIENT_CERT_FILE`/`LOKI_CLIENT_KEY_FILE`), CA internal (`LOKI_CA_FILE`), hostname diverifikasi. Kunci HMAC dari
  `EFN_AUDIT_SINK_KEY_FILE` (>= 32 byte). Konfigurasi tidak lengkap = ETL gagal start (fail closed).
- Stream `{stream="efn_audit_sink"}` jendela `ETL_AUDIT_SINK_WINDOW_SECONDS` (default 25 jam): `sink_mac_invalid`
  (mac salah/null/JSON rusak), `sink_missing_log` (sink valid tanpa log di PG maupun salinan), `log_missing_sink` (log
  model kontrol/purge di PG, lebih tua dari settle, tanpa sink valid). Paginasi tidak lengkap = `sink_truncated` (alert).
- Stream `{stream="efn_audit_anchor", audit_event="chain_head"}` `ETL_AUDIT_ANCHOR_LOOKBACK_SECONDS` (8 hari):
  setiap (company, seq) yang pernah dijangkarkan dibandingkan dengan `row_hash` salinan saat ini — `anchor_mismatch`
  (ditulis ulang / dua hash untuk seq sama), `anchor_missing` (baris hilang), `anchor_absent` (tidak ada jangkar).
- Hasil masuk `EFN_AUDIT_RECON {json}` (kunci pertama tetap `max_log_id`; WARNING bila anomali atau `sink_error=1`
  = Loki/PG gagal) dan kolom baru `audit_recon_run` (`05-loyalty-recon.sql`). View `v_audit_recon_daily`.
- Batas: baris jangkar tidak ber-HMAC — jangkar palsu (injeksi log) hanya menimbulkan alarm palsu, tidak bisa
  menyembunyikan penulisan ulang. Sink dari proses Odoo di luar container utama (mis. `odoo shell` via `docker exec`)
  tidak tertangkap promtail -> `log_missing_sink` (benar: jalur itu memang tidak ter-sink).

### Rekonsiliasi loyalty ledger <-> pos.order (sisa SEC-61) — Gelombang 14
`efn_etl/loyalty_recon.py` (docstring = daftar lengkap `kind`). Critical (`anomalies`, alert): `earn_without_paid_order`
(order fiktif — tidak terlihat recon BFF), `paid_member_order_without_earn` (> `ETL_LOYALTY_RECON_SETTLE_HOURS`, 6),
`redeem_without_points_line`, `points_line_value_mismatch` (toleransi Rp1/baris), `points_line_without_redeem`,
`redeem_points_mismatch`, `reverse_without_refund_order`. Warning (`open_items`): outbox `hold`/`pending` menua
(`ETL_LOYALTY_RECON_AGED_HOURS`, 24), outbox `failed`, order/reservasi `review` menua (dalam
`ETL_LOYALTY_RECON_OPEN_DAYS`, 30). `ETL_LOYALTY_RECON_NOT_BEFORE` (ISO UTC) = go-live loyalty POS; order lama (tanpa
hook) tidak dibandingkan. Log: `<ts> (INFO|WARNING) efn_etl.loyalty_recon EFN_LOYALTY_RECON_ETL {"status":"ok",...}`
(ERROR `{"status":"error","error":"<Tipe>"}` bila gagal), tanpa PII: hanya kind, kode PT, hitungan, id order Odoo / id
ledger sampel. Tabel `loyalty_recon_run` (penanda, ditulis terakhir) + `loyalty_recon_finding`, retensi 400 hari; view
`v_loyalty_recon_latest`, `v_loyalty_recon_daily`. Grant Odoo baru: `sql/odoo-readonly-grants.sql` bagian G14.

### Redeem poin = pembayaran Poin EFN (docs/04 §9.9 K-18-1/K-18-6, Odoo E-30) — Gelombang 18
`ETL_LOYALTY_POINTS_MODE` = `payment` (**default sejak SEC-114 / L-25b**; butuh E-30 + grant `efn_is_loyalty_points` dari
`sql/odoo-readonly-grants.sql`) | `product` (redeem = baris produk Poin EFN, hanya data historis pra-E-30; **ditolak di
production** oleh ETL dan `deploy-validate`). Mode `payment`:
- kind `redeem_without_points_line`, `points_line_value_mismatch`, `points_line_without_redeem` memakai pembayaran
  metode `pos_payment_method.efn_is_loyalty_points` (toleransi `ETL_LOYALTY_POINTS_TOLERANCE_IDR`, default 0, 0..1000);
  order tanpa pembayaran Poin EFN jatuh ke baris produk lama (order pra-E-30 di jendela lookback, toleransi Rp1 x baris)
  sehingga tidak ada `redeem_without_points_line` palsu; order itu juga dikeluarkan dari perbandingan harian;
- **K-18-6 harian**: per PT x hari WIB yang sudah utuh + settle, Σ `pos.payment` Poin EFN (refund negatif) =
  Σ `value_idr` reservasi committed − Σ `points_restored_value`. Sisi BFF diatribusikan ke hari/PT order Odoo
  (`order_ref` = `pos.order.uuid`), sehingga commit BFF lewat tengah malam (order offline) tidak memicu alarm palsu.
  Beda → kind critical `points_payment_daily_mismatch` di log `EFN_LOYALTY_RECON_ETL` (WARNING, kunci `points_daily`)
  dan `loyalty_recon_finding`; semua hari ditulis ke `loyalty_points_daily` / view `v_loyalty_points_daily` (dashboard);
- **SEC-118**: per order refund, `points_restored_value` (Rupiah dari Odoo) − `points_restored` x `LOYALTY_POINT_VALUE_IDR`
  (poin yang benar-benar dikembalikan BFF, `platform.loyalty_pos_reverse`) harus di [0, nilai 1 poin) = sisa pembulatan
  floor BFF. Di luar itu -> kind critical terpisah `points_restore_value_mismatch` (sampel id order refund Odoo), kunci
  log `points_daily.restore_mismatched`, kolom `bff_restored_points_idr` / `restore_value_mismatched` di
  `loyalty_points_daily`. Env `LOYALTY_POINT_VALUE_IDR` (default 100, 1..10^9) **harus sama** dengan BFF.
- `fact_pos_payment.is_loyalty_points` = 1 untuk pembayaran Poin EFN (mode `product`: selalu 0).
**Ketergantungan BFF (usulan kontrak, belum ada):** `platform.loyalty_pos_reverse` (`refund_order_ref`,
`original_order_ref`, `company_code`, `points_restored_value` bigint, `created_at`) + grant SELECT kolom ke `efn_readonly`.
Tanpa tabel itu mode `payment` gagal di siklus recon (heartbeat `loyalty_recon` error), job lain tetap jalan.

### Erasure DSAR (docs/04 §8.4)
`efn_etl/erasure.py`: per request hitung baris `user_hash`/`member_hash` cocok → `ALTER TABLE ... DELETE` (mutation, `mutations_sync=2`; menghapus fisik, bukan sekadar mask seperti lightweight `DELETE`) → verifikasi `count()=0` → publish `{schema_version: 2, idempotency_key: "erasure:<request_id>:clickhouse", request_id, store: "clickhouse"}` (amqps, user `efn-etl`, properti `user_id`/`message_id`). Belum 0 / error → request tetap `processing`, diulang siklus berikutnya. Hash yang sedang dihapus dikecualikan dari job incremental pada siklus itu (overlap window tidak memuat ulang); bila `dsar_request` tidak terbaca, job ber-hash dilewati (fail closed). ETL tidak mendeklarasikan queue (milik BFF).

### Mart konsolidasi GL (F-19, docs/08 §5.3) — Gelombang 12
`efn_etl/gl.py`. Semua state baris jurnal dimuat (draft/posted/cancel, versi `GREATEST(line.write_date, move.write_date)`), dua cursor (baris & header) ke `fact_gl_line` (`ReplacingMergeTree`, partisi per company karena tanggal draft bisa berubah). `partner_id` hanya dipakai untuk menandai inter-company (`ic_company_id` = company grup lawan); label baris/`ref` tidak dibaca. View `v_gl_consolidated` (Metabase): per bulan, tahun fiskal (dari `fiscalyear_last_month/day`), company, akun (kode per company dari `code_store`), `is_intercompany`, `balance` dan `balance_after_elimination`; hanya `posted`. Konsolidasi grup = SUM `balance_after_elimination` per `account_code` lintas company. **Tidak** masuk semantic layer Text-to-SQL (laporan keuangan lintas PT tanpa pembatasan per company; keputusan Q-6).

### Enam entitas (W-32, docs/04 §12.1, ADR-001 revisi 18 Okt) — Gelombang 20
`efn_etl/entities.py` (logika murni): `COMPANY_CODES` = `EFN, GL, PB, BCR, BCF, FARM`; kode lama `RTL`/`FNB` **ditolak** (fail closed) di `loyalty_events.company_code` dan `dim_company`. `dim_company` bertambah `business_type` (`EFN`→holding, `GL`/`BCR`→retail, `PB`/`BCF`→fnb, `FARM`→farm; company tanpa kode → '') dan `brand_group` (`BCR`+`BCF` = `BACHA`). DDL + view di `infra/clickhouse/init/08-g20-entities.sql` (view `v_gl_consolidated` dipindah dari 03 karena membaca kolom baru):
- `v_gl_consolidated` bertambah `business_type`, `brand_group`, `counterparty_brand`, `ic_relation` (`tp_holding` = Holding↔PT, `tp_bacha` = BCR↔BCF, `non_tp` = pasangan lain/Q-AD1-1, `unknown` = kode company lawan tidak dikenal). **Eliminasi tetap untuk semua pasangan** (semua baris ber-`ic_company_id`); label hanya untuk rekonsiliasi dasar harga.
- `v_gl_ic_recon`: per bulan × pasangan tak berurut, `balance_a`, `balance_b`, `net_balance` (≠ 0 = selisih eliminasi yang perlu dijelaskan Finance).
- Tidak ada migrasi: DB dev dibuat ulang; ledger dev lama ber-`RTL`/`FNB` membuat halaman `loyalty_events` gagal (buang sesuai `docs/runbooks/dev-reset.md`). Rumus `ic_relation` dicerminkan di SQL; `tests/test_entities.py` memeriksa keselarasan.

### Salinan audit tamper-evident (ADR-031, SEC-43) — Gelombang 12
`efn_etl/audit.py` (logika murni) + `audit_job.py`. Setiap log Odoo disalin sekali (append-only) dengan `payload_hash = sha256(canonical_row)` (kolom log + digest baris) dan `row_hash = sha256(prev_hash || payload_hash)` berantai per company (`chain_company`, `chain_seq`). Late commit (id kecil commit belakangan) disalin dengan `ingest_kind=late`; log baru disalin setelah `ETL_AUDIT_SETTLE_SECONDS`. Kepala rantai dicatat ke log `EFN_AUDIT_CHAIN_HEAD` (Loki = jangkar eksternal). Isi log orang / baris ter-mask / ref ke model orang **tidak** disalin (hanya hash) agar erasure UU PDP di Odoo tidak tertahan di salinan. User ClickHouse `efn_etl` hanya `SELECT, INSERT` pada tabel ini (tanpa ALTER/DELETE/TRUNCATE/DROP/OPTIMIZE — diverifikasi ditolak 497 di dev).

Rekonsiliasi (`ETL_AUDIT_RECON_INTERVAL_SECONDS`, default harian) -> baris `audit_recon_run` + log `EFN_AUDIT_RECON {json}` (level WARNING bila anomali):

| Kelas | Arti | Alert |
|---|---|---|
| `retention` | log hilang di PG, umur ≥ 365 hari, dalam anggaran total catatan purge `retensi: N log` | tidak |
| `erasure` | log model orang hilang, ada purge `erasure: N log` model itu setelah log dibuat, dalam anggaran N | tidak |
| `erasure_redacted` | baris log kini kosong total (pola `_efn_audit_purge_records`) dan ada purge erasure | tidak |
| `unexplained` | hilang di luar pola di atas, termasuk log `purge` / model kontrol (tidak pernah dihapus sah) | **ya** |
| `modified` | isi log/baris di PG berbeda dari hash saat disalin | **ya** |
| `unseen` | ada di PG (id ≤ max tersalin) tetapi tidak di salinan (sisipan backdated / commit sangat terlambat) | **ya** |
| `chain_problems` | rantai salinan rusak (baris salinan diubah/dihapus admin ClickHouse) | **ya** |

Kebijakan: gap id **di PostgreSQL sendiri** (sequence) bukan indikasi — rollback transaksi Odoo menghabiskan id; yang dibandingkan hanya log yang pernah tersalin. Log yang dihapus sebelum sempat disalin (< settle + interval) tidak terdeteksi oleh salinan; jalur itu ditutup sink Loki `EFN_AUDIT_SINK` untuk model kontrol. Alert Loki yang diusulkan (devsecops): `{service="etl"} |= "EFN_AUDIT_RECON" |= "WARNING"` dan `|= "job=audit_copy gagal"`.

### Retensi (G18/W-19, PDP v7 §0a/§3 — Q-6/Q-49, Q-P7, Q-P8)
Diterapkan `infra/clickhouse/clickhouse-init.sh` setiap deploy (idempoten; env di service `clickhouse-init`):

| Env | Default | Rentang | Efek |
|---|---|---|---|
| `CH_MART_RETENTION_YEARS` | **10** (sebelumnya 5) | 1..30 | TTL baris semua mart (`competitor_prices`, `loyalty_events`, `fact_sales_line`, `fact_pos_payment`, snapshot stok) |
| `CH_PSEUDONYM_RETENTION_YEARS` | **5** | 1..`CH_MART_RETENTION_YEARS` | TTL kolom: `competitor_prices.user_hash` & `loyalty_events.member_hash` -> `''`, `fact_sales_line.customer_id` -> NULL |
| `CH_GEO_RETENTION_DAYS` | **90** | 1..3650 | TTL kolom: `competitor_prices.geo_lat/geo_lng` -> NULL |

- Penafsiran Q-P7 vs Q-6 (rencana G18 §3): *baris fakta 10 th, kolom pseudonim dikosongkan setelah 5 th*.
- TTL kolom diterapkan server saat merge. Bila definisi TTL berubah, init menjalankan `MATERIALIZE TTL` sekali per
  tabel (mutation async) agar part lama ikut diproses; deploy ulang dengan env sama = tanpa mutation.
- **Lapisan kedua di ETL** (`efn_etl/jobs.py` `AgeOut`): baris yang dimuat (ulang) sudah dikosongkan saat transformasi
  bila melewati batas yang sama, sehingga backfill tidak menghidupkan kembali hash/lokasi lama sampai merge berikutnya.
  ETL membaca env yang **sama** (`CH_PSEUDONYM_RETENTION_YEARS`, `CH_GEO_RETENTION_DAYS`) — set di service `etl` juga.
- `loyalty_events` kini `ORDER BY (event_id)` (TTL kolom tidak boleh pada kolom kunci; diuji 24.8.14) + skip index
  `bloom_filter` pada `member_hash` untuk erasure. Volume lama dimigrasi otomatis oleh init (salin -> `EXCHANGE TABLES`
  -> salin baris jendela migrasi -> `DROP` tabel lama). **Hentikan `etl` selama deploy pertama yang memuat migrasi ini**
  (rekomendasi; ETL yang tetap jalan sudah diuji di dev tanpa kehilangan baris).
- Sumber PostgreSQL: `platform.price_hunter_report.geo_*` di-NULL setelah 90 h oleh job retensi **BFF** (pemilik
  tabel, `PRICE_HUNTER_GEO_RETENTION_DAYS`), dengan `updated_at` diperbarui sehingga ETL membawa versi tanpa lokasi.
- Salinan audit: `CH_AUDIT_COPY_RETENTION_YEARS` (default 10, 2..30) — tidak diubah. `fact_gl_line` tanpa TTL (arsip keuangan).

### Chain epoch salinan audit — restore DB Odoo (Gelombang 18)
Setelah DB Odoo dipulihkan, id `efn.audit.log` mundur; tanpa epoch, log baru ber-id ≤ watermark tidak pernah tersalin.
`efn_etl/audit.py` `decide_epoch` + `audit_job.resolve_epoch` (setiap siklus, sebelum penyalinan):
- **Penanda runbook**: `ir.config_parameter` `efn_audit.chain_epoch` (`^[A-Za-z0-9_.:-]{1,64}$`), dibaca lewat view
  baris-tunggal `efn_etl_audit_epoch` (`sql/odoo-readonly-grants.sql`; efn_etl tidak punya akses ke
  `ir_config_parameter`). Penanda berubah -> **epoch baru**: rantai hash dari GENESIS + watermark baru, seluruh log
  sumber disalin ulang; salinan epoch lama tetap (bukti) dan rantainya tetap diverifikasi rekonsiliasi.
- **Deteksi otomatis**: `max(id)` sumber < watermark epoch (`id_regressed`), atau log tersalin terbaru (bukan model
  orang) ada di sumber dengan identitas lain (`create_date`/model/operasi; `head_reused` = id dipakai ulang).
  Tanpa perubahan penanda -> **diblokir**: ERROR `EFN_AUDIT_EPOCH {"event":"divergence_blocked",...}`, heartbeat
  `audit_copy` error, tidak ada penyalinan sampai runbook mengubah penanda. Log terbaru yang hanya HILANG (tanpa id
  mundur) bukan restore: penyalinan lanjut, rekonsiliasi menandai `unexplained`.
- `ETL_AUDIT_EPOCH_AUTO=true` (default false; **ditolak di production**) = epoch baru otomatis saat divergensi (reset DB
  dev). `ETL_AUDIT_EPOCH_ANCHORS` (20) = jumlah log terbaru yang dicek identitasnya.
- Epoch baru: WARNING `EFN_AUDIT_EPOCH {"event":"new_epoch","epoch":N,"reason":"marker",...}` (alert Loki yang
  diusulkan: `|= "EFN_AUDIT_EPOCH"`). Riwayat: tabel `audit_chain_epoch`; kolom `chain_epoch` di `audit_log_copy`,
  `audit_log_line_copy`, `audit_recon_run` (`infra/clickhouse/init/07-audit-epoch.sql`). Jangkar Loki epoch ≥ 1:
  `EFN_AUDIT_CHAIN_HEAD {"company":..,"seq":..,"row_hash":"..","epoch":N}` (epoch 0 = format lama).
- Runbook restore (setelah DB pulih, sebelum ETL jalan lagi): jalankan ulang `sql/odoo-readonly-grants.sql`, lalu
  `odoo shell`: `env['ir.config_parameter'].sudo().set_param('efn_audit.chain_epoch', 'restore-<UTC>'); env.cr.commit()`
  (lewat ORM agar tercatat di log audit sebagai model kontrol).

### Grant Odoo
`sql/odoo-readonly-grants.sql` — grant kolom minimum + index `(write_date, id)`; dijalankan devsecops pasca-install/upgrade Odoo sebagai `efn_odoo`.

Mekanisme: watermark per job di tabel ClickHouse `etl_watermark` (dibaca `argMax`). Run mulai dari `watermark - 5 menit` (overlap untuk late commit), lalu halaman keyset `(_cursor_ts, _cursor_key) > cursor ORDER BY ... LIMIT ETL_BATCH_SIZE`. Watermark ditulis setelah insert halaman sukses. Target `ReplacingMergeTree(<versi>)`, jadi baris yang terbaca ulang tidak menggandakan data setelah merge/`FINAL`; query analitik memakai view ber-`FINAL` (`infra/clickhouse/init/02-loyalty-etl.sql`).

PDP (SEC-16, docs/04 §7.2): ETL **tidak memegang `otp_pepper`** dan tidak menghitung hash. `competitor_prices.user_hash` disalin dari `platform.price_hunter_report.user_hash`, `loyalty_events.member_hash` dari `platform.loyalty_ledger.member_ref_hash` — keduanya sudah dihitung BFF dengan rumus §1 sehingga tetap bisa di-join. Nilai yang bukan hex sha256 64 karakter (mis. nomor HP dari skema lama) membatalkan halaman (fail closed, watermark tidak maju). Kolom `*_enc` dan `user_id` tidak pernah dibaca. Hash tetap data pribadi pseudonim menurut UU PDP. Koordinat dibulatkan 3 desimal. `loyalty_events.company_code` = PT penanggung liabilitas poin (ADR-020).

## Enkripsi in transit (docs/04 §7.1)

- PostgreSQL: `sslmode=verify-full` + `sslrootcert` CA internal (`PG_SSLMODE`, `PG_SSLROOTCERT`; Odoo replica `ODOO_PG_SSLMODE`/`ODOO_PG_SSLROOTCERT`, default ikut `PG_*`).
- ClickHouse: HTTPS `8443`, `verify=True`, `ca_cert=CH_CA_FILE` (bukan bundle certifi).
- RabbitMQ: `amqps://efn-etl@rabbitmq:5671` + `AMQP_TLS_CA_FILE` (hanya bila erasure aktif).
- ETL **gagal start** (`TlsConfigError`) bila TLS tidak terkonfigurasi/CA tidak terbaca. Plaintext hanya `EFN_ALLOW_PLAINTEXT=true` dengan `APP_ENV` ∈ {dev, development, local, test, ci}; `APP_ENV` kosong = production (SEC-27).

## Konfigurasi

| Env | Default |
|---|---|
| `APP_ENV`, `EFN_ALLOW_PLAINTEXT` | —, `false` |
| `PG_HOST`, `PG_PORT`, `PG_USER`, `PG_PASSWORD_FILE`, `PG_DB` (fallback `PLATFORM_DB`) | `postgres`, `5432`, `efn_etl`, secret `pg_password_etl`, `efn_platform` |
| `PG_SSLMODE`, `PG_SSLROOTCERT` | `verify-full`, `/run/secrets/efn_ca_crt` |
| `ODOO_PG_HOST`, `ODOO_PG_PORT`, `ODOO_PG_USER`, `ODOO_PG_PASSWORD_FILE`, `ODOO_DB`, `ODOO_PG_SSLMODE`, `ODOO_PG_SSLROOTCERT` | ikut `PG_*`; `efn_odoo` — arahkan ke read replica di produksi |
| `ETL_ENABLE_ODOO` | `false` |
| `ETL_ENABLE_GL` | `false` (butuh grant G12) |
| `ETL_AUDIT_COPY_ENABLED`, `ETL_AUDIT_SETTLE_SECONDS`, `ETL_AUDIT_LATE_WINDOW_SECONDS`, `ETL_AUDIT_BATCH_SIZE` | `false`, `600`, `86400`, `2000` |
| `ETL_AUDIT_RECON_INTERVAL_SECONDS`, `ETL_AUDIT_RECON_CONTENT` | `86400`, `true` (bandingkan hash isi; `false` = hanya id) |
| `ETL_AUDIT_SINK_ENABLED`, `LOKI_URL`, `LOKI_CA_FILE`, `LOKI_CLIENT_CERT_FILE`, `LOKI_CLIENT_KEY_FILE`, `EFN_AUDIT_SINK_KEY_FILE` | aktif bila audit copy + `LOKI_URL`; secret `tls_etl_crt/key`, `efn_audit_sink_key` |
| `ETL_AUDIT_SINK_WINDOW_SECONDS`, `ETL_AUDIT_ANCHOR_LOOKBACK_SECONDS` | `90000`, `691200` |
| `ETL_LOYALTY_RECON_ENABLED`, `ETL_LOYALTY_RECON_INTERVAL_SECONDS`, `ETL_LOYALTY_RECON_LOOKBACK_HOURS`, `ETL_LOYALTY_RECON_SETTLE_HOURS`, `ETL_LOYALTY_RECON_AGED_HOURS`, `ETL_LOYALTY_RECON_OPEN_DAYS`, `ETL_LOYALTY_RECON_NOT_BEFORE` | `ETL_ENABLE_ODOO`, `86400`, `72`, `6`, `24`, `30`, — |
| `CH_HOST`, `CH_PORT`, `CH_USER`, `CH_PASSWORD_FILE`, `CH_DATABASE`, `CH_SECURE`, `CH_CA_FILE` | `clickhouse`, `8443`, `efn_etl`, secret `clickhouse_password_etl`, `efn_dwh`, `true`, `/run/secrets/efn_ca_crt` |
| `ETL_INTERVAL_SECONDS`, `ETL_OVERLAP_SECONDS`, `ETL_BATCH_SIZE`, `ETL_MAX_PAGES_PER_RUN` | `900`, `300`, `5000`, `200` |
| `ETL_STATEMENT_TIMEOUT_MS`, `ETL_RUN_ONCE`, `LOG_LEVEL` | `300000`, `false`, `INFO` |
| `ETL_ERASURE_ENABLED`, `ETL_ERASURE_BATCH_SIZE`, `ETL_ERASURE_MUTATION_TIMEOUT_SECONDS` | `true`, `100`, `600` |
| `AMQP_URL`, `RABBITMQ_PASSWORD_FILE`, `AMQP_TLS_CA_FILE` | `amqps://efn-etl@rabbitmq:5671`, secret `rabbitmq_password_etl`, `/run/secrets/efn_ca_crt` |

Koneksi PostgreSQL memakai `default_transaction_read_only=on`, `TimeZone=UTC`. User `efn_etl` (anggota `efn_readonly`, grant level kolom; butuh `price_hunter_report.user_hash` dan `loyalty_ledger.company_code`). Hak ClickHouse user `efn_etl`: `infra/clickhouse/access/etl-access.sql`.

## Test

```
pip install -r requirements-dev.txt
pytest
```

## Belum / batasan

- Job Odoo: kolom dicek terhadap sumber 19.0 tetapi belum dijalankan ke DB nyata; perubahan yang hanya menyentuh `pos_order.write_date` tidak tertangkap; `sale_order_line` (online) belum.
- Hard delete di sumber tidak tersinkron (tabel platform tidak menghapus; ledger append-only).
- Salinan audit: satu instance ETL saja (dua instance = rantai bercabang, terdeteksi sebagai `seq_gap`). Rekonsiliasi harian membaca seluruh log + baris (cukup untuk volume ratusan ribu; untuk jutaan log, batasi `ETL_AUDIT_RECON_CONTENT=false` atau tambah jendela). Hash isi yang diredaksi bisa dikonfirmasi dengan tebakan (mis. nama) oleh pembaca salinan — akses dibatasi role `audit_copy_reader` (belum diikat ke user).
- GL: belum diuji dengan baris jurnal nyata (DB dev belum punya `account_move_line`); eliminasi inter-company hanya berbasis partner = company grup (kontak anak company tidak terdeteksi); akun company cabang memakai kode company root.
- Belum ada metrik Prometheus untuk ETL; heartbeat via log Loki + `etl_job_run` (G13). Endpoint `/metrics` bila nanti diperlukan.
- Zona waktu bisnis mart = WIB; outlet di WITA/WIT (mis. Bali) belum dibedakan per outlet.
- `dim_product`/`dim_outlet`: perubahan nama kategori/metode bayar tidak menimpa baris fakta lama (nama disalin saat muat).
