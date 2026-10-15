# Prosedur: Backup, Restore Test, dan Disaster Recovery PostgreSQL (R-T08)

Target: **RPO 15 menit, RTO 4 jam**. Cakupan: seluruh cluster PostgreSQL (`efn_odoo`, `efn_platform`, `metabase`). ClickHouse adalah turunan ETL (dibangun ulang dari PostgreSQL), tidak di-backup terpisah. MinIO `market-scout` (foto Price Hunter) — lihat §6.

## 1. Desain

| Lapisan | Mekanisme | Frekuensi | Lokasi |
|---|---|---|---|
| WAL archive | `archive_command=/usr/local/bin/efn-walg wal-push %p`, `archive_timeout=900` | kontinu, paksa ≤ 15 menit | MinIO `pg-backup/efn/wal_005/` |
| Base backup | `scripts/backup.sh` → `efn-walg backup-push` | harian 01:30 WIB | MinIO `pg-backup/efn/basebackups_005/` |
| Retensi | `efn-walg delete retain FULL 14 --confirm` | tiap backup | 14 base backup + WAL di antaranya |
| Dump logis (opsional) | `LOGICAL_DUMP=1` → `pg_dumpall` + zstd + age | sesuai kebutuhan | `pg-backup/logical/` |
| Restore test | `scripts/restore-test.sh` | Minggu pertama tiap bulan 03:00 WIB | container + volume sementara |

**Kenapa WAL-G (bukan pgBackRest):** repo S3 pgBackRest hanya mendukung HTTPS, sedangkan MinIO internal berjalan HTTP di network `data`. WAL-G mendukung endpoint HTTP + path-style, enkripsi client-side libsodium (kunci di secret `walg_libsodium_key`), kompresi zstd, `backup-fetch`/`wal-fetch` untuk PITR, dalam satu binary. ADR-001 menyebut pgBackRest sebagai contoh mitigasi; keputusan alat ini dapat dibalik tanpa mengubah aplikasi (hanya image `infra/backup/postgres`).

**Off-site (WAJIB sebelum go-live):** MinIO di host yang sama dengan PostgreSQL **bukan** DR. Arahkan `WALG_S3_ENDPOINT`/`WALG_S3_PREFIX` ke MinIO/S3 di node/lokasi lain (variabel `.env`), atau replikasi bucket `pg-backup` dengan `mc mirror --watch` / bucket replication ke site kedua. Kunci libsodium disimpan terpisah dari repo backup (password manager + salinan offline di brankas).

Image: `infra/backup/postgres/Dockerfile` = `postgres:16.6-bookworm` + WAL-G v3.0.9 (diverifikasi sha256 rilis; isi `WALG_SHA256` saat bump). Wrapper: `efn-walg` (membaca secret per pemanggilan), `efn-restore` (entrypoint container restore).

## 2. Pemasangan di host (sekali)

```bash
sudo install -d -o deploy -g deploy -m 0755 /var/lib/efn/node-exporter
sudo cp /opt/efn/backup/systemd/efn-*.service /opt/efn/backup/systemd/efn-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now efn-backup.timer efn-restore-test.timer
systemctl list-timers 'efn-*'
sudo systemctl start efn-backup.service      # base backup pertama segera setelah deploy perdana
```
Kenapa systemd timer di host, bukan GitHub Actions `schedule`: backup harus tetap jalan saat GitHub/VPN CI bermasalah, tidak butuh membuka SSH dari runner publik, dan jadwal Actions bisa tertunda/di-skip. Hasil dipantau lewat metrik textfile → alert `EfnBackup*`/`EfnRestoreTest*`.

## 3. Restore test (bulanan, juga setelah perubahan besar)

```bash
sudo systemctl start efn-restore-test.service && journalctl -u efn-restore-test.service -f
# atau manual
SECRETS_DIR=/opt/efn/secrets bash /opt/efn/scripts/restore-test.sh
# dev: make backup && make restore-test
```
Yang dilakukan skrip:
1. Menemukan container `postgres` hidup → image, env WAL-G, network `efn_data`.
2. Gagal bila base backup terbaru > `MAX_BACKUP_AGE_HOURS` (26 jam). Memaksa `pg_switch_wal()` agar data terkini ikut.
3. Membuat volume + container sementara (`--cap-drop ALL`, tidak listen TCP, **`archive_mode=off`** agar tidak menulis ke repo produksi) dengan entrypoint `efn-restore`: `backup-fetch LATEST` → `recovery.signal` → replay WAL → promote.
4. Menunggu `pg_is_in_recovery() = f` (timeout `RESTORE_TIMEOUT`, default 3 jam < RTO 4 jam).
5. Verifikasi: semua DB di instance hidup + `REQUIRED_DBS` ada; jumlah tabel per DB; `efn_platform.platform` ≥ 2 tabel + row count tabel kunci; `efn_odoo` ≥ 100 tabel, `res_company` ≥ 1, row count `res_partner`, `pos_order`, `account_move`, dst.; opsional `AMCHECK=1` (`pg_amcheck --heapallindexed`).
6. Selalu menghapus container & volume; menulis `efn_restore_test_*` (durasi = indikator RTO). Exit non-zero bila gagal.

Catat hasil (tanggal, durasi, backup yang dipakai, jumlah baris) di log DR bulanan — bukti DoD keamanan docs/03 §5.

## 4. Disaster recovery (produksi)

Keputusan memulihkan diambil Head IT. Estimasi: fetch base backup (ukuran DB / bandwidth MinIO) + replay WAL sejak base backup terakhir.

1. **Bekukan penulisan**: `$DC stop odoo api-gateway ocr-worker etl metabase` (POS lanjut offline).
2. Pertahankan volume lama untuk forensik: `docker volume create efn_pg-data-new` (jangan `down -v`).
3. Restore ke volume baru (PITR opsional):
   ```bash
   docker run --rm --name efn-dr-restore --network efn_data \
     --env-file <(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' efn-postgres-1 | grep -E '^(WALG_|MINIO_BACKUP_ACCESS_KEY_FILE|MINIO_BACKUP_PASSWORD_FILE|POSTGRES_USER|POSTGRES_DB|PGDATA)=') \
     -v efn_pg-data-new:/var/lib/postgresql/data \
     -v /opt/efn/secrets/minio_backup_access_key.txt:/run/secrets/minio_backup_access_key:ro \
     -v /opt/efn/secrets/minio_backup_password.txt:/run/secrets/minio_backup_password:ro \
     -v /opt/efn/secrets/walg_libsodium_key.txt:/run/secrets/walg_libsodium_key:ro \
     --entrypoint bash ghcr.io/<org>/postgres:<tag> -c \
     'efn-walg backup-fetch "$PGDATA" LATEST && touch "$PGDATA/recovery.signal"'
   ```
   PITR: tambahkan ke `$PGDATA/postgresql.auto.conf` → `recovery_target_time = '2026-10-05 10:15:00+07'`, `recovery_target_action = 'promote'`, `restore_command = '/usr/local/bin/efn-walg wal-fetch %f %p'`.
   Tanpa PITR: `restore_command` saja (replay sampai WAL terakhir).
4. Arahkan service `postgres` ke volume baru (ubah `volumes:` lewat file override host sementara), `$DC up -d postgres`, tunggu promote (`SELECT pg_is_in_recovery();` = `f`).
5. **Catat titik pulih T** (UTC): target PITR, atau baris log postgres `last completed transaction was at log time …`
   saat promote. Semua yang terjadi setelah T **hilang** dari PostgreSQL (Odoo **dan** BFF — satu cluster, jadi
   keduanya konsisten satu sama lain) tetapi **masih ada** di data turunan di luar PostgreSQL. Sequence id Odoo/BFF mundur
   ke T → id yang sudah dipakai sesudah T akan dipakai ulang (G18-QA-01/02).
6. **Data turunan** (sebelum menyalakan `etl`, `api-gateway`, `odoo`) — `chq` = klien ClickHouse admin di container
   (TLS, password dari secret, tidak di argv host):
   ```bash
   chq() { $DC exec -T clickhouse sh -c 'CLICKHOUSE_USER="${CLICKHOUSE_USER:-efn}" CLICKHOUSE_PASSWORD="$(cat /run/secrets/clickhouse_password)" clickhouse-client --host clickhouse --multiquery'; }
   T='2026-10-05 03:15:00'   # UTC, titik pulih
   chq <<SQL
   SET mutations_sync = 2;
   -- a) baris turunan transaksi yang hilang (versi > T) -> hapus; sisanya dimuat ulang penuh di langkah b
   ALTER TABLE efn_dwh.fact_sales_line DELETE WHERE write_date > '$T';
   ALTER TABLE efn_dwh.fact_pos_payment DELETE WHERE write_date > '$T';
   ALTER TABLE efn_dwh.dim_outlet DELETE WHERE write_date > '$T';
   ALTER TABLE efn_dwh.dim_company DELETE WHERE write_date > '$T';
   ALTER TABLE efn_dwh.dim_gl_account DELETE WHERE write_date > '$T';
   ALTER TABLE efn_dwh.dim_product DELETE WHERE version_ts > '$T';
   ALTER TABLE efn_dwh.fact_gl_line DELETE WHERE version_ts > '$T';
   ALTER TABLE efn_dwh.loyalty_events DELETE WHERE created_at > '$T';
   ALTER TABLE efn_dwh.competitor_prices DELETE WHERE updated_at > '$T';
   ALTER TABLE efn_dwh.fact_stock_snapshot DELETE WHERE snapshot_ts > '$T';
   ALTER TABLE efn_dwh.fact_orderpoint_snapshot DELETE WHERE snapshot_ts > '$T';
   -- b) watermark semua job inkremental -> epoch: muat ulang PENUH dari DB pulih (ReplacingMergeTree = idempoten).
   --    Wajib, bukan "T - overlap": versi lama baris yang diubah setelah T bisa sudah ter-merge hilang di ClickHouse.
   INSERT INTO efn_dwh.etl_watermark (job, watermark, rows_loaded, updated_at)
   SELECT job, toDateTime64('1970-01-01 00:00:00', 6, 'UTC'), 0, now64(6) FROM efn_dwh.etl_watermark GROUP BY job;
   SQL
   ```
   - **Jangan** mengubah `audit_log_copy`/`audit_log_line_copy`/`*_recon_*`/`loyalty_points_daily` (salinan
     tamper-evident & riwayat = bukti). ⚠ **Celah terbuka (data-AI):** job `audit_copy` melanjutkan dari `max(id)`
     tersalin → log audit Odoo baru ber-id ≤ id lama **tidak tersalin** dan rekonsiliasi audit akan anomali
     (`EfnAuditReconAnomaly`) — sampai ada prosedur "epoch rantai" dari data-AI, catat jendela ini di register insiden;
     jejak sink audit di Loki (`{stream="efn_audit_sink"}`) tetap berjalan.
   - **Redis** (cache, bukan sumber kebenaran; prefiks `otp:` `dtok:` `rl:` `altcha:used:` `ms:` `cart:` `oq:` `ocat:`):
     token/kuota/keranjang/quote/cache katalog yang dibuat setelah T menunjuk data yang hilang. Tidak ada user ACL
     Redis dengan `FLUSHALL` (sengaja) → kosongkan volume AOF: `$DC stop redis && docker volume rm efn_redis-data &&
     $DC up -d redis` (ACL dibangkitkan ulang dari secret). Efek: OTP/keranjang/quote berjalan harus diulang.
   - **RabbitMQ**: pesan/DLQ yang menunjuk id setelah T → setelah naik, tinjau DLQ ([rabbitmq-dlq.md](rabbitmq-dlq.md));
     consumer idempoten, tetapi kunci inbox event online memakai id Odoo lokal (G18-QA-01, perbaikan di odoo-engineer/BFF)
     → event sah bisa tercatat `duplicate`; cocokkan status bagian online dengan Odoo (`online-resolve --action=sync`).
   - **Katalog BFF** (`online_catalog_*`) dan event online ikut pulih konsisten dengan Odoo (cluster sama) — tidak perlu
     reset. **Payment provider**: pembayaran setelah T ada di provider tetapi tidak di BFF → rekonsiliasi laporan
     provider vs `payment_intent`, refund/selesaikan manual ([online-orders.md](online-orders.md)).
   - `ETL_LOYALTY_RECON_NOT_BEFORE` **tidak dimajukan** di produksi (menyembunyikan temuan); temuan rekonsiliasi di
     jendela (T, sekarang) ditinjau manual terhadap order POS offline yang tersinkron ulang.
7. Nyalakan: `$DC up -d` (ETL terakhir: `$DC up -d etl` setelah `clickhouse-init` exit 0). Verifikasi seperti §3
   langkah 5; uji POS sinkron, login OTP, saldo poin. Tunggu muat ulang penuh ETL (`v_etl_freshness` semua `ok`,
   `etl_watermark` maju) lalu **satu siklus rekonsiliasi** loyalty (`ETL_LOYALTY_RECON_INTERVAL_SECONDS`) + audit;
   tinjau `loyalty_recon_finding` & `EFN_AUDIT_RECON` sebelum membuka kembali kanal online (`ONLINE_ENABLED`).
8. **Segera** jalankan base backup baru (timeline baru): `sudo systemctl start efn-backup.service`.
9. Post-mortem + perbarui runbook.

Reset DB Odoo di **dev** (bukan restore): [dev-stack-boot.md §3e](dev-stack-boot.md#reset-db-odoo).

## 5. Migrasi volume dari image alpine lama

Image lama `postgres:16.6-alpine` (uid 70, collation musl) tidak kompatibel dengan image bookworm (uid 999, glibc). Untuk lingkungan yang sudah berisi data:
```bash
docker exec efn-postgres-1 pg_dumpall -U efn > /secure/efn-pre-migrate.sql   # di image lama
$DC down; docker volume rm efn_pg-data   # SETELAH dump terverifikasi
$DC up -d postgres && cat /secure/efn-pre-migrate.sql | docker exec -i efn-postgres-1 psql -U efn -d postgres
```
Dev: `make clean && make up` (data dev dibuang).

## 6. Objek MinIO (foto Price Hunter)

Foto bukan source of truth transaksi; retensi mengikuti kebijakan UU PDP (usulan 180 hari). Untuk DR, replikasi bucket `market-scout` ke site kedua (`mc mirror`) bersama `pg-backup`.
