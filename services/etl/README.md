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

### Verifikasi sink audit v2 (SEC-51/52) — logika saja
`efn_etl/sink.py`: verifikasi `mac` HMAC-SHA256 baris `EFN_AUDIT_SINK` (format kanonik efn_audit) dan rekonsiliasi sink ↔ log PostgreSQL → `sink_mac_invalid`, `sink_missing_log`, `log_missing_sink`. **Belum di-wire**: butuh pembaca Loki (query_range via loki-tls mTLS) + secret `efn_audit_sink_key` di container ETL (spesifikasi: laporan efn-data-ai-engineer Gelombang 13).

### Erasure DSAR (docs/04 §8.4)
`efn_etl/erasure.py`: per request hitung baris `user_hash`/`member_hash` cocok → `ALTER TABLE ... DELETE` (mutation, `mutations_sync=2`; menghapus fisik, bukan sekadar mask seperti lightweight `DELETE`) → verifikasi `count()=0` → publish `{schema_version: 2, idempotency_key: "erasure:<request_id>:clickhouse", request_id, store: "clickhouse"}` (amqps, user `efn-etl`, properti `user_id`/`message_id`). Belum 0 / error → request tetap `processing`, diulang siklus berikutnya. Hash yang sedang dihapus dikecualikan dari job incremental pada siklus itu (overlap window tidak memuat ulang); bila `dsar_request` tidak terbaca, job ber-hash dilewati (fail closed). ETL tidak mendeklarasikan queue (milik BFF).

### Mart konsolidasi GL (F-19, docs/08 §5.3) — Gelombang 12
`efn_etl/gl.py`. Semua state baris jurnal dimuat (draft/posted/cancel, versi `GREATEST(line.write_date, move.write_date)`), dua cursor (baris & header) ke `fact_gl_line` (`ReplacingMergeTree`, partisi per company karena tanggal draft bisa berubah). `partner_id` hanya dipakai untuk menandai inter-company (`ic_company_id` = company grup lawan); label baris/`ref` tidak dibaca. View `v_gl_consolidated` (Metabase): per bulan, tahun fiskal (dari `fiscalyear_last_month/day`), company, akun (kode per company dari `code_store`), `is_intercompany`, `balance` dan `balance_after_elimination`; hanya `posted`. Konsolidasi grup = SUM `balance_after_elimination` per `account_code` lintas company. **Tidak** masuk semantic layer Text-to-SQL (laporan keuangan lintas PT tanpa pembatasan per company; keputusan Q-6).

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

### Retensi
TTL mart default 5 tahun (`CH_MART_RETENTION_YEARS` di service `clickhouse-init`, 1..30), diterapkan `infra/clickhouse/clickhouse-init.sh` setiap deploy.
Salinan audit: `CH_AUDIT_COPY_RETENTION_YEARS` (default 10, 2..30; usulan menunggu DPO/Q-49). `fact_gl_line` tanpa TTL (arsip keuangan).

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
