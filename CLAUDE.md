# CLAUDE.md — Konvensi Proyek EFN Platform

Dibaca oleh semua agent (`.claude/agents/efn-*.md`) dan oleh developer. Bahasa dokumen dan komentar: **Bahasa Indonesia**; identifier kode, nama file, commit message: Inggris.

## Sumber kebenaran

1. `docs/02_TARGET_ARCHITECTURE.md` — arsitektur yang berlaku. Menang atas 6 dokumen bisnis asli di root.
2. `docs/decisions/ADR-*.md` — keputusan. Status `Proposed` berarti **belum disetujui klien**; kode boleh mengikuti rekomendasi, tetapi wajib menyebut ADR terkait di header file/README modul dan tidak boleh mengunci pilihan yang mahal dibalik.
3. `docs/01_REVIEW_RISK_GAP_ANALYSIS.md` — risk register (R-xxx), keputusan D-01..D-16, pertanyaan gap (Q1..Q56).
4. Dokumen bisnis asli (`efn_*.md`, `cetak_biru_*.md`, `project_documentation_suite_efn.md`) hanya untuk konteks kebutuhan, **bukan** acuan teknis. Kesalahan konsep yang sudah diketahui: Phantom BoM untuk kitting, OCR sinkron, unified cart satu SO, multi-instance Odoo, Baileys.

## Model kerja: Lead Agent

User hanya berbicara dengan **`efn-lead`** (`.claude/agents/efn-lead.md`). Di sesi utama (termasuk ekstensi VS Code), Claude bertindak sebagai efn-lead: merencanakan, menetapkan kontrak, mendelegasikan ke agent spesialis, merekonsiliasi, menjalankan QA + security review, dan melapor ke user. Dari CLI bisa juga `claude --agent efn-lead`. Kebutuhan Odoo selalu lewat `efn-odoo-functional` (fit-gap standar CE/OCA) sebelum `efn-odoo-engineer` menulis kode.

## Prinsip yang tidak boleh dilanggar

- **Enkripsi di setiap transaksi data.** Semua koneksi antar-service TLS dengan verifikasi sertifikat CA internal (`/run/secrets/efn_ca_crt`); data pribadi & backup terenkripsi at rest. Tidak ada fallback diam-diam ke plaintext. Detail: `docs/04_SERVICE_CONTRACTS.md` §7, ADR-022.
- **Least privilege**: setiap service punya kredensial DB/broker/storage sendiri (docs/04 §7.3). Superuser/root tidak dipakai aplikasi.
- Odoo CE 19 **satu database multi-company**. Tidak ada modul Enterprise. Cek OCA sebelum menulis dari nol.
- Odoo tidak pernah diakses langsung dari internet; Super-App hanya lewat NestJS BFF.
- Async by default (RabbitMQ) untuk OCR, notifikasi, MGM settle, webhook. Semua consumer idempotent, punya DLQ.
- PostgreSQL = source of truth; ClickHouse hanya turunan via ETL. Tidak ada tulis langsung API → ClickHouse.
- AI di luar jalur transaksi. Jika Ollama/OCR mati, POS & checkout tetap jalan.
- WhatsApp **Cloud API resmi** saja. Dilarang Baileys / library unofficial.
- Tidak pernah menyimpan PAN kartu. Webhook wajib verifikasi HMAC + idempotency key + replay window.
- Secret hanya dari `/run/secrets/*` (Docker secrets) atau env non-rahasia. Tidak ada secret di repo, contoh, test fixture, atau log.
- Data pribadi (UU PDP 27/2022): nomor HP di log harus di-mask (`62812****789`), minimalkan data, sebutkan retensi.
- POS offline: fitur yang butuh server (OTP, poin, MGM) **dinonaktifkan** saat offline (rekomendasi ADR OTP-offline opsi a).

## Struktur & stack

| Path | Stack | Test |
|---|---|---|
| `services/odoo/addons/efn_*` | Odoo CE 19, Python 3.12, OWL untuk POS | `odoo.tests.TransactionCase` / `HttpCase`, tag `efn` |
| `services/api-gateway` | NestJS 10 + Fastify, TypeScript strict, ioredis, amqplib, pg | Jest `*.spec.ts` di sebelah file |
| `services/superapp-web` | Next.js 15 App Router, TypeScript | (belum ada runner; tambahkan Vitest bila perlu) |
| `services/ai-ocr-worker`, `services/etl` | Python 3.12 | pytest di `tests/`, ruff (`ruff.toml`) |
| `infra/*` | Docker Compose, Traefik v3, Prometheus/Loki/Grafana | `docker compose config` di CI |
| `.github/workflows` | GitHub Actions | path-filtered |

## Batasan lingkungan kerja

Mesin penulis **tidak punya** Docker, Git, Node, atau Python yang bisa dijalankan. Konsekuensi:
- Tidak ada yang bisa di-run lokal; validasi terjadi di CI (`.github/workflows/ci.yml`).
- Tulis kode yang konservatif: API library yang stabil dan terdokumentasi, hindari fitur eksperimental.
- Setiap dependency baru ditambahkan eksplisit ke `package.json` / `requirements.txt` dengan versi yang di-pin (caret untuk npm boleh).
- Laporkan dengan jujur apa yang **belum** terverifikasi.

## Konvensi kode

- Odoo: modul `efn_<domain>`, versi `19.0.x.y.z`, lisensi `LGPL-3` (atau AGPL-3 bila bergantung OCA AGPL), field `company_id` + record rule multi-company untuk setiap model baru, `ir.model.access.csv` lengkap.
- NestJS: satu folder per bounded context di `src/<context>/` (module, controller, service, dto, spec). Validasi input dengan DTO + `class-validator` atau guard manual. Error tidak membocorkan detail internal.
- Endpoint publik di-rate-limit; respons OTP tidak membocorkan apakah nomor terdaftar.
- Nama queue RabbitMQ: `<domain>.<event>` (mis. `ocr.requested`, `notify.push`), DLQ `<queue>.dlq`.
- Setiap modul/fitur baru punya README singkat: tujuan, ADR terkait, risiko (R-xxx) yang dimitigasi, apa yang belum.

## Tim agent

Lihat `.claude/agents/`. Ringkas:
`efn-lead` (koordinator, satu-satunya lawan bicara user), `efn-architect` (ADR, koreksi FSD, audit modul), `efn-odoo-functional` (fit-gap & modul konfigurasi `efn_setup_*`), `efn-odoo-engineer` (hanya untuk gap), `efn-bff-engineer`, `efn-frontend-engineer`, `efn-data-ai-engineer`, `efn-devsecops-engineer`, `efn-security-reviewer` (read-only, review akhir), `efn-qa-engineer`.
Setiap agent menulis laporan kerjanya ke `docs/agent-reports/<agent>.md`.
