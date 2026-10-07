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
- Belum ada metrik Prometheus untuk ETL (lag watermark) — disarankan berikutnya.
