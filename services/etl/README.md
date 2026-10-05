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

### Erasure DSAR (docs/04 §8.4)
`efn_etl/erasure.py`: per request hitung baris `user_hash`/`member_hash` cocok → `ALTER TABLE ... DELETE` (mutation, `mutations_sync=2`; menghapus fisik, bukan sekadar mask seperti lightweight `DELETE`) → verifikasi `count()=0` → publish `{schema_version: 2, idempotency_key: "erasure:<request_id>:clickhouse", request_id, store: "clickhouse"}` (amqps, user `efn-etl`, properti `user_id`/`message_id`). Belum 0 / error → request tetap `processing`, diulang siklus berikutnya. Hash yang sedang dihapus dikecualikan dari job incremental pada siklus itu (overlap window tidak memuat ulang); bila `dsar_request` tidak terbaca, job ber-hash dilewati (fail closed). ETL tidak mendeklarasikan queue (milik BFF).

### Retensi
TTL mart default 5 tahun (`CH_MART_RETENTION_YEARS` di service `clickhouse-init`, 1..30), diterapkan `infra/clickhouse/clickhouse-init.sh` setiap deploy.

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
- Belum ada metrik Prometheus untuk ETL (lag watermark) — disarankan berikutnya.
