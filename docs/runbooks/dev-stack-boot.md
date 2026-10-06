# Runbook: menyalakan stack dev EFN (Docker Desktop / Linux)

Diverifikasi di runtime nyata gelombang 7 (6 Okt 2026, Docker Desktop 29.8.1 + WSL2): profil `core`, `data`,
`observability`, `ops` semuanya healthy; 33/33 target Prometheus `up`. Gelombang 8: profil `ai` (Ollama tanpa model +
ocr-worker) di-boot dan smoke E2E lintas service lolos (docs/testing/E2E_SMOKE_REPORT.md).

## 1. Siapkan secret, PKI, .env

```bash
bash scripts/gen-secrets.sh          # idempoten; juga gen-pki.sh leaf + sertifikat edge dev (secrets/dev_edge_*.txt)
cp infra/compose/.env.example infra/compose/.env
```

Git Bash (Windows): jangan set `MSYS_NO_PATHCONV` saat menjalankan skrip di atas. Sejak gelombang 7 skrip membuang
`\r` dari keluaran openssl Windows. Secret lama yang dibuat sebelum perbaikan berisi CR, sehingga 00-roles.sh menolak
password ("memuat karakter di luar ...") dan redis-entrypoint gagal ("harus alfanumerik"). Perbaikannya:
`for f in $(grep -l $'\r' secrets/*.txt); do tr -d '\r' < "$f" > "$f.t" && mv "$f.t" "$f"; done; bash scripts/derive-secrets.sh secrets`,
lalu buat ulang volume dev (`down -v`), karena role Postgres dibuat saat init.

## 2. Build & up

```bash
DC="docker compose -f infra/compose/docker-compose.yml -f infra/compose/docker-compose.dev.yml --env-file infra/compose/.env"
$DC --profile core up -d --build
$DC --profile core --profile data --profile observability --profile ops up -d
```

Build pertama makan waktu: image object storage dikompilasi dari source tag **SILO** (`infra/minio/Dockerfile`,
fork komunitas MinIO, ADR-030; Go 1.27.1, ±10 menit). Image `minio/minio` dan `minio/mc` sudah tidak ada di Docker
Hub dan upstream diarsipkan.

### 2b. Gelombang 9: migrasi stack dev lama (sekali)

1. `bash scripts/gen-secrets.sh` — membuat 4 access key acak (`secrets/minio_*_access_key.txt`, docs/04 §8.12) dan,
   karena service PKI baru `s3-gateway`, **membuat ulang CA dev** (lama diarsip ke `secrets/pki-ca.old-*`) + semua leaf.
2. `$DC --profile core build postgres minio` (WAL-G membaca `MINIO_BACKUP_ACCESS_KEY_FILE`; server SILO).
3. Volume `minio-data` lama (MinIO `RELEASE.2025-10-15`) **dipakai ulang langsung** oleh SILO — diuji gelombang 9:
   boot healthy, bucket/objek/versi/lock lama terbaca. Tidak perlu reset. Rollback: image `efn/minio:legacy-2025-10-15`.
4. Migrasi skema idempoten BFF sebelum BFF baru start (volume `pg-data` lama):
   ```bash
   $DC --profile core up -d postgres
   MSYS_NO_PATHCONV=1 $DC exec -T postgres psql -X -q -v ON_ERROR_STOP=1 -U postgres -d postgres \
     -f /docker-entrypoint-initdb.d/04-platform-v3-6.sql      # produksi: otomatis di deploy.sh
   ```
5. `$DC --profile core --profile data --profile observability --profile ops up -d --force-recreate` (semua container
   memegang bundle CA lama). `minio-init` membuat user ber-access-key acak dan **menghapus** user lama `pgbackup`,
   `efn-bff`, `efn-ocr`, `efn-prometheus` (log: "user lama bernama tetap dihapus: 4").

Topologi baru object storage: klien (BFF, ocr-worker, WAL-G, Prometheus) → `https://s3-gateway:9000` (filter L7
HAProxy, `infra/s3-gateway/haproxy.cfg`) → `https://minio:9000` di network `objstore` saja. Akses dari laptop:
`https://localhost:9000` (filter; admin API diblok). Admin: `docker exec efn-minio-1 sh -c 'mc alias set l
https://localhost:9000 efnadmin "$(cat /run/secrets/minio_root_password)" && mc admin info l'`.

## 3. Inisialisasi database Odoo (sekali per volume `pg-data`)

`list_db = False` + `dbfilter ^efn_.*$`. Tanpa database `efn_odoo`, `/web/login` mengarah ke `/web/database/selector`,
yang diblok (404).

```bash
$DC --profile core run --rm --no-deps -e ODOO_DEV_MODE= odoo odoo -d efn_odoo -i base --stop-after-init --without-demo=all
```

Modul EFN (gelombang 8, terverifikasi: exit 0, 102 modul termuat ±85 s). Hentikan `odoo` dulu agar tidak ada dua
proses yang menulis registry:

```bash
$DC --profile core stop odoo odoo-tls odoo-ws-tls
$DC --profile core run --rm --no-deps -e ODOO_DEV_MODE= odoo odoo -d efn_odoo --stop-after-init --without-demo=all \
  -i efn_core,efn_setup_company,efn_setup_security,efn_setup_pos,efn_setup_mrp,efn_setup_audit,efn_kitting,efn_pos_otp_gate,efn_pos_payment_gateway,efn_privacy
$DC --profile core up -d odoo odoo-tls odoo-ws-tls
```

`efn.bff_url` tidak perlu diset manual: env `EFN_BFF_URL=https://api-gateway:3000` (compose) mengalahkan parameter.
`efn_member_hash` dihitung saat install untuk partner yang sudah ada dan otomatis untuk partner baru; server action
"Recompute member hash" hanya diperlukan setelah pepper berubah. Baris `(ERROR/3) Undefined substitution` di log
install berasal dari docutils yang merender README.md modul sebagai RST (kosmetik, lihat E2E_SMOKE_REPORT).

### 3b. Profil `ai` (Ollama + ocr-worker)

Profil `ai` bergantung pada service `core` (rabbitmq, postgres): perintah compose harus menyertakan `--profile core`
(`--profile ai` saja gagal validasi "depends on undefined service", termasuk `build`).

```bash
$DC --profile core --profile data --profile ai build ocr-worker
$DC --profile core --profile data --profile ai up -d
```

Tanpa model vision, laporan yang ragu (confidence Tesseract < 0.80) berakhir `flagged/vlm_unavailable` (perilaku yang
diharapkan). Gelombang 9 (R1): image dinaikkan ke `ollama/ollama:0.35.1` (dipin digest); manifest `qwen2.5vl`
kini diterima (0.5.4 menolak dengan 412). Model terkecil yang layak untuk dev: `qwen2.5vl:3b` (±3,2 GB):

```bash
docker exec efn-ollama-1 ollama pull qwen2.5vl:3b
# .env lokal: OLLAMA_VISION_MODEL=qwen2.5vl:3b, lalu: $DC --profile core --profile ai up -d ocr-worker
bash scripts/smoke/run-e2e-smoke.sh b      # + subperintah report_vlm (scripts/smoke/README.md)
```

`hostname: ollama` di base compose tetap wajib (proteksi DNS rebinding Ollama, gelombang 8).

## 3d. Grant ETL Odoo (R5)

Dev override menyetel `ETL_ENABLE_ODOO=true`. Grant baca level-kolom `efn_etl` di DB `efn_odoo` diterapkan otomatis oleh
smoke skenario `f` (dan oleh `deploy.sh` di staging/prod). Manual:
`MSYS_NO_PATHCONV=1 docker exec -i efn-postgres-1 psql -X -q -v ON_ERROR_STOP=1 -U efn_odoo -d efn_odoo -f - < services/etl/sql/odoo-readonly-grants.sql`.
Sebelum grant, job `sales_fact_pos` gagal per siklus (permission denied) tanpa memengaruhi job lain.

## 3c. Smoke E2E

`bash scripts/smoke/run-e2e-smoke.sh` (±15 menit, dev saja; lihat `scripts/smoke/README.md`).

## 4. Verifikasi cepat

| Cek | Perintah | Hasil yang diharapkan |
|---|---|---|
| API via Traefik | `curl -k --resolve api.efn.local:443:127.0.0.1 https://api.efn.local/health` | 200 |
| Web | `curl -k --resolve app.efn.local:443:127.0.0.1 https://app.efn.local/` | 200 |
| Odoo (wg-https) | `curl -k --resolve erp.efn.local:9443:127.0.0.1 https://erp.efn.local:9443/web/login` | 200 |
| Odoo di publik | `... https://erp.efn.local/web/login` (443) | 404 |
| RPC Odoo | `.../xmlrpc/2/common`, `/jsonrpc` | 403 |
| BFF internal via publik | `/metrics`, `/v1/pos/*` di api.efn.local | 404 |

## 5. Jebakan yang ditemukan di runtime

- File konfigurasi yang di-bind-mount satu per satu (rabbitmq.conf, encrypted-storage.xml, promtail.yml, secrets)
  dan diedit dengan `sed -i` atau ditulis ulang: lakukan `up -d --force-recreate <svc>`, bukan sekadar `restart`.
- Docker Desktop: propagasi `rslave` untuk `/` ditolak, sehingga dev override node-exporter memakai `/:/host:ro`.
- `nest start --watch` butuh heap lebih dari 250 MB. Dev override memberi `NODE_OPTIONS=--max-old-space-size=1024` + limit 1536M.
- Dev Postgres memakai `log_statement=all`. Odoo (psycopg2) menginterpolasi parameter di sisi klien, jadi nilai
  literal (bisa berisi nomor HP) tercetak di log dev. Jangan pakai data pribadi asli di dev.
- Ollama yang bind ke loopback (pola sidecar) menolak `403` setiap request dengan Host selain localhost/hostname
  container. Service `ollama` wajib `hostname: ollama` (sudah di base compose sejak gelombang 8); tanpa itu semua
  panggilan VLM dari ocr-worker gagal diam-diam menjadi `flagged/vlm_unavailable`.
- Git Bash: `docker run -v` dan `docker exec ... <path absolut>` butuh `MSYS_NO_PATHCONV=1` (kalau tidak, `/bin/sh`
  menjadi `C:/Program Files/Git/...`).

## 6. Matikan

```bash
$DC --profile core --profile data --profile ai --profile observability --profile ops down      # volume dipertahankan
# reset total dev: tambahkan -v (menghapus volume efn_* SAJA)
```
