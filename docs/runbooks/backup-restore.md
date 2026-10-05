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
     --env-file <(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' efn-postgres-1 | grep -E '^(WALG_|MINIO_BACKUP_USER|POSTGRES_USER|POSTGRES_DB|PGDATA)=') \
     -v efn_pg-data-new:/var/lib/postgresql/data \
     -v /opt/efn/secrets/minio_backup_password.txt:/run/secrets/minio_backup_password:ro \
     -v /opt/efn/secrets/walg_libsodium_key.txt:/run/secrets/walg_libsodium_key:ro \
     --entrypoint bash ghcr.io/<org>/postgres:<tag> -c \
     'efn-walg backup-fetch "$PGDATA" LATEST && touch "$PGDATA/recovery.signal"'
   ```
   PITR: tambahkan ke `$PGDATA/postgresql.auto.conf` → `recovery_target_time = '2026-10-05 10:15:00+07'`, `recovery_target_action = 'promote'`, `restore_command = '/usr/local/bin/efn-walg wal-fetch %f %p'`.
   Tanpa PITR: `restore_command` saja (replay sampai WAL terakhir).
4. Arahkan service `postgres` ke volume baru (ubah `volumes:` lewat file override host sementara), `$DC up -d postgres`, tunggu promote (`SELECT pg_is_in_recovery();` = `f`).
5. Verifikasi seperti §3 langkah 5, lalu `$DC up -d`. Uji POS sinkron, login OTP, saldo poin.
6. **Segera** jalankan base backup baru (timeline baru): `sudo systemctl start efn-backup.service`.
7. Post-mortem + perbarui runbook.

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
