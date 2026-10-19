# Prosedur: Rotasi secret

Sumber tunggal daftar secret: `scripts/secrets.list`. Nilai staging/prod ada di **GitHub Environments** (`staging`, `production`) dengan nama UPPERCASE; `scripts/deploy.sh` menulisnya ke `/opt/efn/secrets/<nama>.txt` (direktori 0700, file 0644) dan compose me-mount ke `/run/secrets/<nama>`.

## Prinsip
- Rotasi terjadwal: 12 bulan (atau segera saat dicurigai bocor, staf keluar, atau vendor berganti).
- Rotasi = ubah GitHub Environment secret → jalankan workflow **Deploy** (`workflow_dispatch`, tag yang sedang berjalan) → **recreate** service pemakai. Docker Compose tidak me-recreate container saat isi file secret berubah, dan `deploy.sh` mengganti file secara atomik (inode baru), jadi container lama tetap melihat nilai lama:
  ```bash
  $DC up -d --force-recreate <service...>
  ```
- Jangan pernah menempel nilai secret di chat/tiket/log. Buat nilai baru: `openssl rand -base64 36` (password/HMAC) atau `openssl rand -hex 32` (API key).
- Dev: hapus `secrets/<nama>.txt` lalu `make secrets`.

## Per secret

| Secret | Dipakai oleh | Langkah khusus | Dampak saat rotasi |
|---|---|---|---|
| `pg_password` | postgres (superuser, hanya socket lokal) | Image postgres hanya memakai `POSTGRES_PASSWORD_FILE` saat init → `docker exec efn-postgres-1 psql -U postgres -d postgres` lalu `\password postgres`, update secret + deploy | tidak ada (aplikasi tidak memakai superuser) |
| `pg_password_{odoo,bff,ocr,etl,metabase,exporter}` | postgres (00-roles.sh) + pemakai masing-masing | Update secret → Deploy: `deploy.sh` menjalankan `00-roles.sh` (ALTER ROLE ... PASSWORD) sebelum `up`, lalu `$DC up -d --force-recreate <pemakai>` | koneksi baru pemakai gagal di antara ALTER ROLE dan recreate (detik–menit); koneksi pool lama tetap jalan |
| `rabbitmq_password_bff`, `rabbitmq_password_ocr` | rabbitmq (definitions), api-gateway / ocr-worker | Update + recreate `rabbitmq` (definitions diimpor ulang saat boot) lalu pemakai | publish/consume terputus singkat (outbox & DLQ menampung) |
| `redis_password_bff`, `redis_password_exporter` | redis (ACL), api-gateway / redis-exporter | Update + recreate `redis` (ACL dibangkitkan ulang) lalu pemakai. Wajib alfanumerik ≥ 32 (generator hex32) | OTP/rate limit gagal beberapa detik |
| `minio_bff_secret`, `minio_ocr_secret` | minio-init (upsert user), api-gateway / ocr-worker | Update + deploy (minio-init meng-upsert user) + recreate pemakai | upload/ambil foto gagal singkat |
| `minio_kms_key` | minio (SSE-S3, enkripsi IAM/config) | **Jangan rotasi sembarangan**: objek & konfigurasi terenkripsi dengan kunci ini. Rotasi = migrasi ke KMS (KES) atau re-encrypt (`mc admin kms key`), rencanakan dengan vendor | data tidak terbaca bila kunci hilang |
| `clickhouse_password_{etl,text2sql,metabase}` | clickhouse-init (ALTER USER), etl / text2sql / Metabase UI | Update + deploy (clickhouse-init) + recreate etl; Metabase: `$DC run --rm --no-deps metabase-provision` (PUT koneksi `EFN DWH`, tersimpan terenkripsi `metabase_encryption_key`) | ETL tertunda satu siklus; kartu Metabase gagal sampai provision |
| `metabase_encryption_key` [SA] (G16b) | metabase (`MB_ENCRYPTION_SECRET_KEY` lewat `efn-metabase-entrypoint`, fail-closed) | **Jangan hanya ganti nilai** (Metabase tak bisa membaca detail koneksi lama). Urutan: `$DC stop metabase-tls metabase` → one-off `rotate-encryption-key` dengan kunci LAMA di env dan kunci BARU sebagai argumen dibaca dari file di dalam container ([infra/metabase/README.md](../../infra/metabase/README.md) §Rotasi kunci; SEC-87: argumen terlihat `ps` host selama rotasi → jalankan tanpa sesi user lain atau dengan `/proc` `hidepid=2`) → update secret + deploy → `up -d --no-deps --force-recreate metabase metabase-tls` → `metabase-provision`. Arsipkan kunci lama sampai dump `metabase` lama melewati retensi | BI (staf) tidak tersedia beberapa menit; bila kunci hilang: koneksi ClickHouse dibuat ulang oleh `metabase-provision` (detail lain yang terenkripsi hilang) |
| `clickhouse_disk_key` | clickhouse (disk encrypted) | **Jangan ganti nilai**: tambah kunci baru `<key_hex id="1">` + `current_key_id` (koordinasi data-ai, encrypted-storage.xml); kunci lama tetap sampai semua part ditulis ulang | data DWH tidak terbaca bila kunci lama hilang (DWH bisa dibangun ulang dari PostgreSQL) |
| `pii_encryption_key` | api-gateway | Prosedur key ring: [pii-key-rotation.md](pii-key-rotation.md) | — |
| `efn_ca_crt`, `tls_*` | semua service | [internal-pki.md](internal-pki.md) | — |
| `odoo_admin_password` | odoo (master password DB manager) | update + recreate odoo (conf dirender ulang) | tidak ada (DB manager diblokir dari internet) |
| `jwt_secret` | api-gateway | update + recreate api-gateway | semua sesi Super-App logout (token lama invalid) — lakukan di luar jam sibuk |
| `rabbitmq_password` | rabbitmq (user admin `efn-admin`, ops saja) | update + recreate rabbitmq (definitions) | tidak ada untuk aplikasi |
| `minio_root_password` | minio, minio-init (root hanya untuk bootstrap) | update + recreate minio lalu minio-init | tidak ada untuk aplikasi |
| `minio_root_user` (SEC-48, access key acak) | minio (`MINIO_ROOT_USER_FILE`), minio-init | update + recreate minio lalu minio-init (diuji gelombang 11 pada volume lama: IAM/bucket utuh, root lama ditolak) | tidak ada untuk aplikasi |
| `clickhouse_password` | clickhouse (admin `efn`), clickhouse-init | update + recreate clickhouse lalu clickhouse-init | tidak ada untuk aplikasi |
| `otp_pepper` | api-gateway (ETL tidak lagi memegangnya, SEC-16) | update + recreate | OTP aktif (≤ 180 s) gagal diverifikasi; `member_hash` diturunkan dari pepper ini (docs/04 §1) → rotasi memutus join hash lama (ledger, DWH): rencanakan re-hash dengan BFF + data-ai sebelum rotasi |
| `discount_token_secret` | api-gateway | update + recreate | discount token yang belum dikonsumsi (≤ 600 s) ditolak → kasir minta OTP ulang |
| `odoo_bff_api_key` | api-gateway **dan** odoo | update + recreate **keduanya bersamaan**: `$DC up -d --force-recreate api-gateway odoo` | OTP gate POS gagal beberapa detik–menit (Odoo restart). Usulan BFF: terima 2 key (current + previous) agar rotasi tanpa jeda |
| `loyalty_policy_signing_key` (G21, docs/04 §13.1 no. 3) | odoo (penanda tangan outbox kebijakan poin) **dan** api-gateway (pemverifikasi) | update GitHub secret `LOYALTY_POLICY_SIGNING_KEY` (`openssl rand -base64 36`) → deploy → `$DC up -d --force-recreate api-gateway odoo` **bersamaan** | publikasi kebijakan yang terkirim di jendela recreate ditolak `401` dan di-retry outbox Odoo (backoff); kebijakan aktif di BFF tidak berubah. Rotasi terjadwal 12 bulan atau segera bila bocor (pemegang kunci dapat memasang kebijakan poin palsu dalam plafon `LOYALTY_POLICY_CEILING_*`) |
| `odoo_pii_key_v1`, `odoo_pii_bidx_key`, `odoo_pii_name_key` (G21 AC-13, host:* [SA]) | odoo saja | **Bukan** lewat GitHub/deploy: prosedur khusus [pii-key-rotation.md](pii-key-rotation.md#kunci-pii-odoo) + [key-custody.md](key-custody.md#pii-odoo) | lihat runbook tersebut (menimpa key ring = PII tidak terbaca permanen) |
| `whatsapp_token` | api-gateway | Buat token System User baru di Meta Business Manager (permission `whatsapp_business_messaging`), update + recreate, cabut token lama | tidak ada bila token lama dicabut setelah recreate |
| `webhook_payment_secret` | api-gateway | Koordinasi dengan payment gateway; ideal: BFF menerima 2 secret selama jendela transisi | webhook ditolak (provider retry) bila tidak sinkron |
| `minio_backup_password` | postgres (efn-walg), minio-init | update + deploy (minio-init meng-upsert user `pgbackup`) + `$DC up -d --force-recreate postgres` di jendela maintenance | WAL archive gagal antara upsert user dan recreate postgres → lakukan berurutan cepat; PostgreSQL me-retry archive |
| `walg_libsodium_key` | postgres (efn-walg), restore-test | **Arsipkan kunci lama** (offline, berlabel tanggal) — backup lama hanya bisa didekripsi dengan kunci lama. Update + recreate postgres, lalu **segera** base backup baru (`systemctl start efn-backup.service`). Hapus kunci lama setelah semua backup lama melewati retensi (14 base backup) | tidak ada; restore backup lama memakai kunci lama (`SECRETS_DIR` khusus) |
| `grafana_admin_password` | grafana | Env hanya berlaku saat inisialisasi DB Grafana: `docker exec efn-grafana-1 grafana cli admin reset-admin-password '<baru>'`, lalu update secret | tidak ada |
| `alertmanager_webhook_url` | alertmanager | update + recreate alertmanager; URL wajib `https://` (deploy.sh menolak selain itu); uji: kirim alert uji dari Prometheus (`amtool` di container tidak membawa CA internal untuk endpoint HTTPS Alertmanager) | notifikasi tertahan beberapa detik |

## Setelah rotasi
1. `$DC ps` semua sehat; dashboard tanpa target down.
2. Uji fungsional singkat: login OTP Super-App, OTP gate POS (staging), upload Price Hunter, `make`/systemd backup manual untuk secret backup.
3. Catat tanggal rotasi di register secret (tanpa nilai).
