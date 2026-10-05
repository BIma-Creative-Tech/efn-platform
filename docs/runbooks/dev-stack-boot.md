# Runbook: menyalakan stack dev EFN (Docker Desktop / Linux)

Diverifikasi di runtime nyata gelombang 7 (6 Okt 2026, Docker Desktop 29.8.1 + WSL2): profil `core`, `data`,
`observability`, `ops` semuanya healthy; 33/33 target Prometheus `up`. Profil `ai` (Ollama + ocr-worker) BELUM di-boot.

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

Build pertama makan waktu: image MinIO dikompilasi dari source (`infra/minio/Dockerfile`). Image `minio/minio` dan
`minio/mc` sudah tidak ada di Docker Hub.

## 3. Inisialisasi database Odoo (sekali per volume `pg-data`)

`list_db = False` + `dbfilter ^efn_.*$`. Tanpa database `efn_odoo`, `/web/login` mengarah ke `/web/database/selector`,
yang diblok (404).

```bash
$DC --profile core run --rm --no-deps -e ODOO_DEV_MODE= odoo odoo -d efn_odoo -i base --stop-after-init --without-demo=all
```

Modul EFN dipasang terpisah oleh efn-odoo-engineer/functional (`-i efn_...`).

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

## 6. Matikan

```bash
$DC --profile core --profile data --profile ai --profile observability --profile ops down      # volume dipertahankan
# reset total dev: tambahkan -v (menghapus volume efn_* SAJA)
```
