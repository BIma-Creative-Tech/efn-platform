# infra/metabase — dashboard sebagai kode (Fase 1)

**Tujuan:** 3 dashboard Fase 1 (docs/01 §7 "ETL dasar + 3 dashboard Metabase") yang bisa di-review, di-diff, dan
di-provision ulang secara idempoten: *Penjualan Harian Outlet*, *Stok & Kedaluwarsa*, *Price Hunter*.
**ADR:** ADR-003 (ETL 15 menit), ADR-022 (TLS di setiap koneksi), ADR-031 (tanpa modul pihak ketiga Odoo; BI di luar
Odoo). **Risiko:** R-S05 (kartu hanya view semantic layer, user `efn_metabase` read-only), R-L01 (tanpa identitas
pelanggan di mart/kartu). Daftar dashboard final menunggu **Q-47** (10 dashboard C-level) — ketiganya usulan Fase 1.

## Isi

| File | Fungsi |
|---|---|
| `Dockerfile` | `metabase/metabase:v0.58.35@sha256` (LTS). G16: driver ClickHouse BAWAAN Metabase (plugin 1.53.3) -> jar eksternal 1.52.0 dihapus. **Diverifikasi 9 Okt 2026 (G16b, stack dev):** migrasi DB aplikasi 0.52.4 -> 0.58.35 sukses (Liquibase 14,1 s, data lama utuh), `provision.py` exit 0, 33/33 kartu `completed`. Migrasi tidak bisa dibalik -> backup dulu (lihat *Upgrade & rollback*). |
| `efn-entrypoint.sh` | G16b: ENTRYPOINT image (`/usr/local/bin/efn-metabase-entrypoint`). Membaca `/run/secrets/metabase_encryption_key` → `MB_ENCRYPTION_SECRET_KEY` lalu `exec /app/run_metabase.sh` resmi (root → `su metabase`, uid 2000, tetap). **Fail-closed**: secret tidak ada/kosong/< 32 karakter/berisi CR atau spasi, atau `MB_ENCRYPTION_SECRET_KEY` diset lewat env → exit 1 (semua lingkungan, termasuk dev). |
| `provision.py` | Skrip idempoten (stdlib saja): setup awal (admin dari file secret), setting (tanpa telemetri, zona Asia/Jakarta, locale id), koneksi `EFN DWH (ClickHouse)`, collection `EFN Fase 1`, kartu & dashboard, lalu menjalankan setiap kartu sekali (`--no-verify-cards` untuk melewati). |
| `dashboards/*.json` | Definisi dashboard: parameter (tanggal, PT, outlet/gudang, pesaing, produk) + kartu SQL native. |
| `tests/test_provision.py` | pytest: validasi definisi repo, SQL hanya `efn_dwh.v_*`, mapping filter, TLS wajib. |

Kartu dan dashboard dikenali lewat tag `[efn:<dashboard>/<kartu>]` di deskripsi: run ulang memperbarui (bukan
menggandakan); kartu yang dihapus dari JSON diarsipkan. Perubahan manual di UI pada dashboard ini **ditimpa** run
berikutnya — buat dashboard eksplorasi di collection lain.

## Keamanan
- Kartu: hanya `SELECT/WITH` satu statement ke view `efn_dwh.v_*`; DDL/DML/fungsi tabel (`url`, `file`, `remote`, `s3`, …)
  ditolak saat validasi. Lapisan kedua: role ClickHouse `metabase_reader` (`readonly=2`, grant kolom, tanpa
  `customer_id`/hash) — diverifikasi: `SELECT customer_id` dan `INSERT` ditolak 497.
- Koneksi Metabase → ClickHouse: `https://clickhouse:8443`, `ssl=true`, `additional-options` = `sslmode=strict&sslrootcert=/run/secrets/efn_ca_crt`,
  `enable-multiple-db=false` (hanya `efn_dwh`; default driver 1.53 = sinkron semua database). Diverifikasi ulang di v0.58.35:
  CA internal → valid, CA sistem (salah) → "Failed to create connection"; metadata tersinkron hanya skema `efn_dwh`.
- Skrip → Metabase: HTTPS terverifikasi (CA sistem / `--ca-file`), lewat Traefik `bi.<domain>` (wg-https + allowlist
  staf); mTLS opsional ke sidecar. Password admin dan `clickhouse_password_metabase` hanya dibaca dari file.
- Instance yang belum di-setup bisa "diklaim" siapa pun yang membuka `bi.` lebih dulu → jalankan `provision.py` segera
  setelah Metabase pertama kali hidup (deploy).

## Enkripsi at rest DB aplikasi (G16b)
Metabase mengenkripsi detail koneksi (`metabase_database.details`, termasuk password `efn_metabase` ClickHouse) dan
setting sensitif dengan `MB_ENCRYPTION_SECRET_KEY` (AES-256 + HMAC, dokumentasi resmi "Encrypting your database
connection details at rest"). Kunci = secret `metabase_encryption_key` (`b64key32`, kustodi **Superadmin** -
`docs/runbooks/key-custody.md`), di-mount **hanya** ke service `metabase`; `scripts/check-encryption.sh` menolak compose
yang tidak me-mount secret ini, membawa `MB_ENCRYPTION_SECRET_KEY` di environment, meng-override `entrypoint` (apa pun,
termasuk `[]`/`''` yang menghapus ENTRYPOINT image, SEC-86) atau memakai image resmi `metabase/metabase`;
`deploy.sh` dan smoke e2e memeriksa `docker inspect .Config.Entrypoint` container yang berjalan.

Container `metabase` (SEC-85): `cap_drop: [ALL]` + `cap_add: [CHOWN, SETUID, SETGID]` (adduser/chown/`su metabase`
pada `run_metabase.sh` resmi), `no-new-privileges`; secret yang di-mount hanya `pg_password_metabase`,
`metabase_encryption_key`, `efn_ca_crt` (password ClickHouse hanya di `metabase-provision`, lalu tersimpan terenkripsi).

**Mengenkripsi instance yang sudah ada (sekali; sudah dilakukan di dev 9 Okt 2026).** Staging/prod belum pernah jalan
tanpa kunci (belum deploy) → di sana kunci aktif sejak start pertama, langkah ini tidak perlu.
```bash
bash scripts/gen-secrets.sh                                   # dev: membuat secrets/metabase_encryption_key.txt
$DC build metabase
$DC stop metabase-tls metabase                                # rotate wajib saat Metabase berhenti
$DC run --rm --no-deps -T --entrypoint /bin/sh metabase -c 'set -eu; unset MB_ENCRYPTION_SECRET_KEY
  MB_DB_PASS="$(cat /run/secrets/pg_password_metabase)"; export MB_DB_PASS; unset MB_DB_PASS_FILE
  exec /opt/java/openjdk/bin/java --add-opens java.base/java.nio=ALL-UNNAMED -jar /app/metabase.jar     rotate-encryption-key "$(cat /run/secrets/metabase_encryption_key)"'   # log: "Encryption key rotation OK."
$DC up -d --no-deps --force-recreate metabase metabase-tls    # log: "Database encrypted and MB_ENCRYPTION_SECRET_KEY correctly configured"
$DC run --rm --no-deps metabase-provision                     # exit 0, semua kartu completed
```
**Rotasi kunci berikutnya (SEC-87).** Perintah resmi Metabase hanya menerima kunci BARU sebagai argumen
(`MB_ENCRYPTION_SECRET_KEY=<lama> java -jar metabase.jar rotate-encryption-key <baru>`); tidak ada varian env/file.
Akibatnya kunci baru **terlihat di `/proc/<pid>/cmdline` proses java selama rotasi berjalan (±30-60 dtk), dan dari
HOST dapat dibaca user host mana pun lewat `ps -ef`** (PID namespace container tidak menyembunyikan proses dari
host). Java `@argfile` TIDAK menolong: diuji 9 Okt 2026 di image ini (JRE 21 Alpine), `/proc/self/cmdline` tetap
memuat argumen yang sudah diekspansi. Kunci LAMA lewat env proses one-off (`/proc/<pid>/environ` hanya terbaca
root/uid yang sama, tidak masuk `docker inspect` karena di-set di dalam `sh -c`). Mitigasi wajib:
1. Jalankan di host prod (akses hanya admin) saat tidak ada sesi user lain (`who` kosong selain operator), atau
   dengan `/proc` ber-`hidepid=2` (`mount -o remount,hidepid=2 /proc`; kembalikan setelahnya bila ada layanan host
   yang membutuhkannya).
2. Kunci tidak pernah diketik di shell/argumen `docker` (tidak masuk history/`docker inspect`): kunci baru dibaca dari
   file di DALAM container one-off.
3. Kunci tidak pernah dicetak (log Metabase hanya "Encryption key rotation OK.").
```bash
# Superadmin (root di host prod; dev: user biasa). Kunci LAMA = secret aktif metabase_encryption_key.
SECRETS_DIR=/opt/efn/secrets   # prod (BASE deploy.sh); dev: SECRETS_DIR=secrets (root repo)
( umask 077; openssl rand -base64 32 | tr -d '\n' > "$SECRETS_DIR/metabase_encryption_key.new" )   # 44 karakter
$DC stop metabase-tls metabase
$DC run --rm --no-deps -T -v "$SECRETS_DIR/metabase_encryption_key.new:/run/efn-new-key:ro" \
  --entrypoint /bin/sh metabase -c 'set -eu
  MB_ENCRYPTION_SECRET_KEY="$(cat /run/secrets/metabase_encryption_key)"; export MB_ENCRYPTION_SECRET_KEY
  MB_DB_PASS="$(cat /run/secrets/pg_password_metabase)"; export MB_DB_PASS; unset MB_DB_PASS_FILE
  exec /opt/java/openjdk/bin/java --add-opens java.base/java.nio=ALL-UNNAMED -jar /app/metabase.jar \
    rotate-encryption-key "$(cat /run/efn-new-key)"'                   # log: "Encryption key rotation OK."
# Ganti secret: prod = GitHub Environment METABASE_ENCRYPTION_KEY (kustodi Superadmin, key-custody.md) lalu deploy;
# dev = mv metabase_encryption_key.new -> secrets/metabase_encryption_key.txt. Arsipkan kunci lama (offline).
$DC up -d --no-deps --force-recreate metabase metabase-tls             # "MB_ENCRYPTION_SECRET_KEY correctly configured"
$DC run --rm --no-deps metabase-provision                              # exit 0
shred -u "$SECRETS_DIR/metabase_encryption_key.new" 2>/dev/null || rm -f "$SECRETS_DIR/metabase_encryption_key.new"
```
File kunci baru milik root 0600 tetap terbaca root di container walau `cap_drop: [ALL]` (pemilik file = uid 0, tanpa
DAC_OVERRIDE). Jangan `docker compose up` sebelum secret diganti: wrapper akan memakai kunci LAMA dan Metabase gagal
membaca detail koneksi (fail-closed, bukan plaintext).
Status uji (9 Okt 2026): enkripsi awal (blok di atas) diuji di dev; prosedur ROTASI ini belum diuji end-to-end
(rotasi kunci dev perlu persetujuan Superadmin) -> uji di dev sebelum dipakai di staging/prod.

Verifikasi tanpa mencetak nilai (dev G16b: kedua baris `f|f`, password ClickHouse tidak ditemukan di `details`/`setting`):
```bash
docker exec efn-postgres-1 psql -U postgres -d metabase -Atc   "select id, details like '%password%', left(details,1)='{' from metabase_database order by id"
```

## Menjalankan (dev)
```bash
python infra/metabase/provision.py --url https://bi.efn.local:9443 --connect-host 127.0.0.1 \
  --ca-file secrets/dev_edge_crt.txt --admin-email admin@efn.local \
  --admin-password-file secrets/metabase_admin_password.txt \
  --ch-password-file secrets/clickhouse_password_metabase.txt
```

## Upgrade & rollback Metabase (DB aplikasi)
Migrasi Liquibase berjalan otomatis saat image baru start dan **tidak bisa dibalik**. Urutan (dev/staging/prod):
```bash
# 1. backup DB aplikasi TERENKRIPSI (age, pola scripts/backup.sh LOGICAL_DUMP; JANGAN tulis dump plaintext ke disk)
#    + tag image lama untuk rollback. AGE_RECIPIENT = public key age Superadmin (kunci privat offline).
docker exec efn-postgres-1 pg_dump -U postgres -d metabase -Fc | age -r "$AGE_RECIPIENT" -o <folder-backup>/metabase-pre.dump.age
age -d -i <identitas-superadmin> <folder-backup>/metabase-pre.dump.age | docker exec -i efn-postgres-1 pg_restore --list | wc -l   # harus terbaca
docker tag ghcr.io/efn/metabase:dev efn/metabase:rollback-<versi-lama>
# 2. build + recreate (sidecar ikut), tunggu healthy (log: "Migration complete", "Initialization COMPLETE")
$DC --profile core --profile data build metabase
$DC --profile core --profile data up -d --no-deps --force-recreate metabase metabase-tls
# 3. provision + verifikasi kartu (exit != 0 bila ada kartu gagal)
$DC --profile core --profile data run --rm --no-deps metabase-provision
```
Rollback: `stop metabase metabase-tls` → `dropdb`/`createdb metabase -O efn_metabase` → `age -d -i <identitas> <dump>.age |
docker exec -i efn-postgres-1 pg_restore -U postgres -d metabase --no-owner --role=efn_metabase` → kembalikan `FROM` Dockerfile ke v0.52.4 + jar driver
(`git show 4aa2b8a:infra/metabase/Dockerfile`) atau `IMAGE_METABASE=efn/metabase:rollback-<versi-lama>` → `up -d
--force-recreate metabase metabase-tls`. Dump DB `metabase` berisi akun staf (hash password),
sesi, dan - sebelum G16b - detail koneksi plaintext → **selalu** terenkripsi age, akses Superadmin, hapus setelah
upgrade dinyatakan stabil. Lapisan utama tetap backup cluster WAL-G (libsodium, `docs/runbooks/backup-restore.md`).
Rollback belum diuji coba (tidak perlu di G16b).

**Rollback ke v0.52.4 setelah G16b.** Dump pra-upgrade dev (plaintext, 9 Okt) sudah DIHAPUS setelah enkripsi
terverifikasi. Jalur yang tersisa: image `efn/metabase:rollback-v0.52.4` sebagai base (`FROM` Dockerfile ini diganti
ke tag itu; baris `COPY efn-entrypoint.sh` + `ENTRYPOINT` dipertahankan agar kunci enkripsi tetap aktif) + DB `metabase` kosong (dropdb/createdb) + `metabase-provision` (membuat ulang admin, koneksi, 3 dashboard dari kode).
Yang hilang: dashboard/kartu buatan manual di UI dan akun staf selain admin. Downgrade DB 0.58 → 0.52 tidak
didukung Metabase.

Catatan log v0.58 saat start pertama: puluhan `WARN task.impl :: Deleting job ... class not found` = job Quartz
versi lama yang kelasnya diganti nama; normal, dibuat ulang oleh 0.58.

## Retensi riwayat & akun tidak aktif (G18/W-19, PDP v7 Q-P14)
Diperiksa di image v0.58.35 (stack dev, 10 Okt 2026):
- **Riwayat query** = tabel `query_execution` di DB aplikasi `metabase`. Dibersihkan task OSS
  `TruncateAuditTables` menurut setting `audit-max-retention-days` — setting **internal** (`GET /api/setting/...` ->
  "Pengaturan :audit-max-retention-days internal"), tanpa setter: **hanya** dari env `MB_AUDIT_MAX_RETENTION_DAYS`
  pada service `metabase` (0 = tak terbatas, minimum 30; default bawaan Metabase 720 hari menurut dokumentasi
  upstream — konstanta di jar tidak terbaca sebagai string, belum diverifikasi). Target 10 th =
  **`MB_AUDIT_MAX_RETENTION_DAYS=3650`** (env compose milik devsecops; `provision.py` tidak bisa mengaturnya).
- OSS **hanya** memangkas `query_execution` (docstring jar: "OSS implementation only truncates `query_execution`");
  `audit_log`/`view_log` adalah fitur Pro/Enterprise dan di dev kosong (0 baris).
- **Akun tidak aktif 90 hari**: tidak ada fitur bawaan OSS. `provision.py` menonaktifkan (bukan menghapus) akun yang
  `last_login` (atau `date_joined` bila belum pernah login) lebih tua dari `--deactivate-inactive-days` (default 90,
  0 = mati); admin provisioning yang sedang login tidak pernah dinonaktifkan. Aktif ulang: Admin > People.
  Karena provision hanya jalan saat deploy, jadwalkan harian (devsecops), contoh timer host:
  `$DC run --rm --no-deps metabase-provision <argumen sama> --housekeeping-only`.
  Diuji di dev: akun uji `date_joined` 120 hari -> dinonaktifkan (`is_active=f`), exit 0; run penuh 2x exit 0.

## Belum
- Filter PT/outlet/gudang = teks (cocok sebagian, tidak peka huruf besar), bukan dropdown (butuh field filter
  dengan id field hasil sync).
- SSO/kebijakan password staf (Q-54), izin per grup (collection `EFN Fase 1` masih terlihat semua user Metabase).
- ~~`MB_ENCRYPTION_SECRET_KEY` belum diset~~ → selesai G16b (lihat *Enkripsi at rest*). Sisa: password
  `efn_metabase` (ClickHouse, read-only) dev sempat tersimpan plaintext di DB & dump dev → rotasi disarankan
  (opsional di dev; `docs/runbooks/secret-rotation.md`). Staging/prod tidak terdampak (belum pernah deploy).
