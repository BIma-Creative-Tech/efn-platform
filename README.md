# EFN Platform

Monorepo platform digital Era Food and Nourishment: Odoo CE 19 multi-company, NestJS BFF, Next.js Super-App, worker AI/OCR, ETL ke ClickHouse, dan seluruh infrastruktur sebagai kode (Docker Compose + GitHub Actions DevSecOps).

## Mulai dari sini

| Kebutuhan | Baca |
|---|---|
| Review dokumen bisnis, risk register, gap kebutuhan, roadmap | [docs/01_REVIEW_RISK_GAP_ANALYSIS.md](docs/01_REVIEW_RISK_GAP_ANALYSIS.md) |
| Arsitektur target yang sudah dikonsolidasikan | [docs/02_TARGET_ARCHITECTURE.md](docs/02_TARGET_ARCHITECTURE.md) |
| Pipeline DevSecOps dan cara kerjanya | [docs/03_DEVSECOPS_PIPELINE.md](docs/03_DEVSECOPS_PIPELINE.md) |
| Keputusan arsitektur (ADR) | [docs/decisions/](docs/decisions/) |
| Dokumen bisnis asli (BRD, FSD, Blueprint, dsb.) | file `*.md` di root |
| Kebijakan keamanan | [SECURITY.md](SECURITY.md) |
| Inventaris lisensi & biaya non-lisensi | [LICENSES.md](LICENSES.md) |

## Struktur

```
docs/           dokumen arsitektur & ADR
infra/compose   docker-compose base + override dev/prod
infra/traefik   reverse proxy, TLS, middleware keamanan
infra/postgres  init DB platform (skema price hunter, otp audit, role read-only)
infra/clickhouse init data mart
infra/observability prometheus, loki, promtail, grafana
services/odoo   Dockerfile Odoo 19 + addons efn_* (+ OCA di-fetch saat build)
services/api-gateway   NestJS BFF
services/superapp-web  Next.js
services/ai-ocr-worker Python worker OCR (RabbitMQ -> Tesseract/VLM -> BFF)
services/etl    Python ETL incremental PostgreSQL -> ClickHouse
scripts/        deploy, backup, fetch OCA, gen secrets
.github/        workflows CI, security, codeql, build-push, deploy, dast; dependabot; CODEOWNERS
```

## Menjalankan di laptop

Prasyarat: Docker Desktop (atau engine + compose plugin), `make`, `openssl`, `jq`.

Dev = prod untuk enkripsi (docs/04 §7, §8.5): semua koneksi antar-service memakai TLS dengan PKI internal 3 tingkat
(root DEV → intermediate DEV → leaf 90 hari) yang dibuat `make secrets` (`scripts/gen-pki.sh`), termasuk mTLS
sidecar & observability. Tidak ada mode plaintext. Kunci CA dev ada di `secrets/pki-ca/` (jangan dipakai untuk
staging/prod; root/intermediate produksi dibuat Superadmin offline: `make pki-root`, `make pki-intermediate`,
lihat `docs/runbooks/key-custody.md`). `make secrets` juga menurunkan `member_hash_pepper` dari `otp_pepper`.

```bash
make secrets          # buat ./secrets/*.txt + PKI internal (bundle CA + sertifikat per service) untuk dev
make env              # salin infra/compose/.env.example -> .env
make lint-enc         # (opsional) pastikan tidak ada konfigurasi plaintext
make up               # stack inti: traefik, postgres, redis, rabbitmq, minio, odoo, api-gateway, superapp-web
make up PROFILES="--profile core --profile data"             # + clickhouse, metabase, etl
make up PROFILES="--profile core --profile data --profile ai" # + ollama, ocr-worker
make logs
```

Akses dev (sertifikat publik = default self-signed Traefik, terima peringatan browser; sertifikat internal =
CA `secrets/efn_ca_crt.txt`, impor ke trust store bila ingin akses langsung tanpa peringatan):

| URL | Service |
|---|---|
| https://erp.efn.local:9443 | Odoo lewat entrypoint Traefik `wg-https` (ADR-023; dev: 127.0.0.1:9443, allowlist bridge Docker 172.16.0.0/12). Buat DB `efn_odoo`, master password = isi `secrets/odoo_admin_password.txt` |
| https://api.efn.local/health | NestJS BFF (`PAYMENT_PROVIDER=mock` di dev) |
| https://app.efn.local | Next.js |
| https://bi.efn.local:9443, https://ops.efn.local:9443 | Metabase, Grafana (`wg-https`) |
| https://localhost:15671 | RabbitMQ management (user `efn-admin`, password `secrets/rabbitmq_password.txt`) |
| https://localhost:8443 / :3443 / :3444 / :11434 | Odoo / Next.js / Metabase / Ollama langsung: **mTLS** (sidecar hanya menerima sertifikat klien CN `traefik`, Ollama CN `ocr-worker`), mis. `curl --cacert secrets/efn_ca_crt.txt --cert secrets/tls_traefik_crt.txt --key secrets/tls_traefik_key.txt https://localhost:8443/web/health` |
| https://localhost:18443 | ClickHouse HTTPS (CA internal, password user) |
| `psql "host=localhost sslmode=verify-full sslrootcert=secrets/efn_ca_crt.txt user=efn_bff dbname=efn_platform"` | PostgreSQL (TCP hanya TLS; superuser hanya via `docker compose exec postgres psql`) |

Tambahkan ke hosts file: `127.0.0.1 efn.local erp.efn.local api.efn.local app.efn.local mart.efn.local kitchen.efn.local bi.efn.local ops.efn.local status.efn.local`.

Volume dev dari scaffold sebelum gelombang 3 (superuser `efn`, Redis/RabbitMQ/MinIO tanpa TLS, ClickHouse tanpa
disk terenkripsi) tidak kompatibel: jalankan `make clean` sekali lalu `make up`.

Model vision untuk OCR fallback diunduh sekali: `docker compose exec ollama ollama pull qwen2.5vl:7b`.

## Alur rilis

1. Buat branch, buka PR ke `main`. CI + Security + CodeQL harus hijau, CODEOWNERS harus approve.
2. Merge ke `main` memicu build image, scan, SBOM, sign, push ke GHCR, lalu deploy otomatis ke **staging** dan ZAP baseline.
3. Tag `vX.Y.Z` memicu build + deploy **production** yang menunggu approval di GitHub Environment `production`.

Konfigurasi yang dibutuhkan di GitHub: lihat [docs/03_DEVSECOPS_PIPELINE.md](docs/03_DEVSECOPS_PIPELINE.md) bagian "Setup repo".

## Status scaffold

Repo ini adalah **fondasi (Fase 0)**. Yang sudah ada: infra, pipeline, skeleton tiap service dengan health endpoint, skema awal data. Yang belum: logika domain (OTP, cart, MGM, kitting) menunggu keputusan ADR di `docs/01` Bagian 2. Konfigurasi belum divalidasi dengan `docker compose config` di mesin penulis karena Docker tidak terpasang; job `infra` di CI melakukan validasi itu pada push pertama.
