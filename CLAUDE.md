# CLAUDE.md — Konvensi Proyek EFN Platform

Dibaca oleh semua agent (`.claude/agents/efn-*.md`) dan oleh developer. Bahasa dokumen dan komentar: **Bahasa Indonesia**; identifier kode, nama file, commit message: Inggris.

## Sumber kebenaran

1. `docs/02_TARGET_ARCHITECTURE.md` — arsitektur yang berlaku. Menang atas 6 dokumen bisnis asli di root.
2. `docs/decisions/ADR-*.md` — keputusan. Status `Proposed` berarti **belum disetujui klien**; kode boleh mengikuti rekomendasi, tetapi wajib menyebut ADR terkait di header file/README modul dan tidak boleh mengunci pilihan yang mahal dibalik.
3. `docs/01_REVIEW_RISK_GAP_ANALYSIS.md` — risk register (R-xxx), keputusan D-01..D-16, pertanyaan gap (Q1..Q56).
4. Bagian **"Kebutuhan pemilik proyek 25 Okt 2026"** di bawah + detailnya `docs/K25_KEBUTUHAN_PEMILIK.md` menang atas `docs/02`, `docs/10`, dan ADR lama bila bertentangan, sampai ADR/kontrak terkait diperbarui.
5. Dokumen bisnis asli (`efn_*.md`, `cetak_biru_*.md`, `project_documentation_suite_efn.md`) hanya untuk konteks kebutuhan, **bukan** acuan teknis. Kesalahan konsep yang sudah diketahui: Phantom BoM untuk kitting, OCR sinkron, unified cart satu SO, multi-instance Odoo, Baileys untuk OTP/notifikasi transaksional (pengecualian terbatas promo & notify-me: ADR-044).

## Model kerja: Lead Agent

User hanya berbicara dengan **`efn-lead`** (`.claude/agents/efn-lead.md`). Di sesi utama (termasuk ekstensi VS Code), Claude bertindak sebagai efn-lead: merencanakan, menetapkan kontrak, mendelegasikan ke agent spesialis, merekonsiliasi, menjalankan QA + security review, dan melapor ke user. Dari CLI bisa juga `claude --agent efn-lead`. Command project (`.claude/commands/`): `/handoff` + `/lanjut` (status sesi di `docs/agent-reports/efn-lead.md`), `/security-check` (gerbang pra-merge), `/audit-module <path>`. Kebutuhan Odoo selalu lewat `efn-odoo-functional` (fit-gap standar CE; OCA & Cybrosys hanya referensi) sebelum `efn-odoo-engineer` menulis kode.

## Prinsip yang tidak boleh dilanggar

- **Enkripsi di setiap transaksi data.** Semua koneksi antar-service TLS dengan verifikasi sertifikat CA internal (`/run/secrets/efn_ca_crt`); data pribadi & backup terenkripsi at rest. Tidak ada fallback diam-diam ke plaintext. Detail: `docs/04_SERVICE_CONTRACTS.md` §7, ADR-022.
- **Least privilege**: setiap service punya kredensial DB/broker/storage sendiri (docs/04 §7.3). Superuser/root tidak dipakai aplikasi.
- Odoo CE 19 **satu database multi-company**. Tidak ada modul Enterprise. **Tidak ada dependency ke modul Odoo pihak ketiga** (OCA, Cybrosys, vendor lain): yang tidak tertutup Odoo CE standar dibangun sendiri sebagai `efn_*`. OCA (github.com/OCA) dan Cybrosys (github.com/CybroOdoo/CybroAddons) hanya **referensi desain** — jangan menyalin kodenya verbatim; tulis ulang dan catat referensinya di README modul (ADR-031; dikonfirmasi ulang pemilik proyek 25 Okt 2026).
- Odoo tidak pernah diakses langsung dari internet; Super-App hanya lewat NestJS BFF.
- Async by default (RabbitMQ) untuk OCR, notifikasi, MGM settle, webhook. Semua consumer idempotent, punya DLQ.
- PostgreSQL = source of truth; ClickHouse hanya turunan via ETL **incremental 15 menit** dari replica (ADR-003, disetujui 25 Okt 2026; tanpa CDC/Debezium). Tidak ada tulis langsung API → ClickHouse. **Pengecualian terbatas (ADR-040, Proposed, G41):** data legacy ESB Core (sistem eksternal klien, tidak pernah ada di PostgreSQL) mendarat lewat `services/esb-extract` (GET-only, satu user API per brand) ke `efn_esb.*` dan `efn_dwh.*esb*` sebagai sumber dashboard, migrasi Odoo (G43), dan forecast (G44); Odoo tetap sumber kebenaran setelah cutover.
- AI di luar jalur transaksi. Jika gateway LLM/Ollama/OCR mati, POS & checkout tetap jalan. Penyedia LLM diatur di K25-01.
- WhatsApp **Cloud API resmi** untuk OTP, pesanan, pembayaran, loyalty, dan semua pesan transaksional. **Pengecualian terbatas (ADR-044, Proposed, K18-01):** library tidak resmi (Baileys) hanya untuk promo personal & notify-me/price alert supermarket, di service terisolasi `services/wa-unofficial` dengan nomor terpisah, `WA_UNOFFICIAL_ENABLED=false` default dan **OFF di production** sampai penerimaan risiko tertulis pemilik proyek + Legal (Q-SM14). Library unofficial lain tetap dilarang.
- Tidak pernah menyimpan PAN kartu. Webhook wajib verifikasi HMAC + idempotency key + replay window.
- Secret hanya dari `/run/secrets/*` (Docker secrets) atau env non-rahasia. Tidak ada secret di repo, contoh, test fixture, atau log.
- Data pribadi (UU PDP 27/2022): nomor HP di log harus di-mask (`62812****789`), minimalkan data, sebutkan retensi.
- POS offline: fitur yang butuh server (OTP, poin, MGM) **dinonaktifkan** saat offline (rekomendasi ADR OTP-offline opsi a).

## Kebutuhan pemilik proyek 25 Okt 2026 (K25)

Hasil review user atas `ARSITEKTUR_DAN_REVIEW_EFN_ODOO_CE_OSS.md`. Butir yang sudah tercakup di aturan di atas (OCA hanya referensi, ETL 15 menit) tidak diulang di sini. Setiap butir yang mengubah kode/kontrak wajib dibuatkan ADR atau revisi kontrak `docs/04` sebelum implementasi; status "belum dibangun" berarti belum ada kode.

Ringkasan satu baris; **detail lengkap (konsekuensi teknis, status gelombang, fakta CE 19) di `docs/K25_KEBUTUHAN_PEMILIK.md` — wajib dibaca sebelum menyentuh butir terkait.**

| ID | Kebutuhan (ringkas) | Status / aturan kunci | Terkait |
|---|---|---|---|
| K25-01 | Gateway LLM = 9router (API key `/run/secrets/ninerouter_api_key`), fallback Ollama; satu `LlmClient` | Payload tanpa PII + catatan DPO; gagal = `flagged`, bukan error transaksi. Dibangun G37 (OCR), G38 (Text-to-SQL), G39 (agent); prod menunggu DPO | ADR-037..039 |
| K25-02 | Data stack terpisah (`efn-data`), bisa di mesin lain | TLS verify-full + WireGuard; stack inti jalan bila `efn-data` mati | ADR-004 |
| K25-03 | Mobile: Super-App dulu (Expo) | App brand dibekukan; pedoman UI `docs/design/superapp-concept.md` §5 | docs/10 W-38 |
| K25-04 | Printer termal jaringan apa pun | `efn-print-bridge` (HTTPS, LAN outlet, ESC/POS TCP 9100); PoC belum | W-23 |
| K25-05 | `loyalty-service` terpisah (OTP, poin, MGM, Eraspace) | Jalur `/v1/pos/otp/*`, `/v1/pos/loyalty/*` tidak berubah; ekstraksi bertahap, test kontrak tetap hijau | ADR-020, docs/04 §9 |
| K25-06 | Tiga environment lokal (`efn-dev` ≤8 GB, `efn-stg` ≤12 GB, `efn-prod` gladi) | Secret/CA/volume per env; **tidak ada deploy ke VPS** | ADR-004, docs/02 §4 |
| K25-07 | Pesanan online wajib lunas sebelum diproses | `counter` dilarang untuk online; `bank_transfer` + Verifikator Pembayaran (SoD); gateway mock + Xendit Test Mode | docs/04 §17, ADR-016/026 |
| K25-08 | Waste + yield dimonitor | Mart + dashboard dibangun G32 (`ETL_ENABLE_WASTE=false` default) | ADR-018, docs/04 §21 |
| K25-09 | Kiosk + self-order; bayar di kasir / EDC / gateway | Tidak ke dapur sebelum lunas; EDC terblokir spesifikasi ECR bank; mati saat offline | ADR-013/032, docs/04 §17.11 |
| K25-10 | MGM: L1/L2 %, L3+ flat, kedalaman maksimum wajib | Dibangun G30–G33; `MGM_ENABLED=false`, prod OFF sampai Finance + Legal | ADR-009, docs/04 §19–20 |
| K25-11 | Dual membership Eraspace + internal; tier di internal | `MembershipProvider`; spesifikasi API Eraspace ditahan → antarmuka + mock saja | ADR-020 |
| K25-12 | Produksi CK: make-to-order dan/atau make-to-stock | Configurable per produk/CK; min/max forecast disetujui planner | ADR-018, Q-35 |
| K25-13 | Standar QC per komoditas = data konfigurasi user | Input manual, QC 3 langkah CE + `efn_agri`; belum dibangun | Q-31 |
| K18-01 | Supermarket demo `SMD` (DC + 5 outlet), siap pilot | Fitur berisiko OFF prod (tracking, WA unofficial); belum dibangun | docs/04 §28, ADR-044..048 |

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

Status mesin penulis (per 6 Okt 2026): **Git + Git Bash + OpenSSL + Docker Desktop (WSL2) berjalan**; Node/Python tidak dipasang di host — semua build/test dijalankan di container (lihat `docs/runbooks/dev-stack-boot.md`). Catatan Git Bash: `MSYS_NO_PATHCONV=1` + `pwd -W` untuk `docker run -v`; jangan set itu untuk skrip yang memanggil git/openssl native. Konsekuensi:
- Validasi lokal = job CI yang dijalankan di container; CI GitHub tetap gerbang akhir.
- Tulis kode yang konservatif: API library yang stabil dan terdokumentasi, hindari fitur eksperimental.
- Setiap dependency baru ditambahkan eksplisit ke `package.json` / `requirements.txt` dengan versi yang di-pin (caret untuk npm boleh).
- Laporkan dengan jujur apa yang **belum** terverifikasi.

## Konvensi kode

- Odoo: modul `efn_<domain>`, versi `19.0.x.y.z`, lisensi `LGPL-3`, field `company_id` + record rule multi-company untuk setiap model baru, `ir.model.access.csv` lengkap.
- NestJS: satu folder per bounded context di `src/<context>/` (module, controller, service, dto, spec). Validasi input dengan DTO + `class-validator` atau guard manual. Error tidak membocorkan detail internal.
- Endpoint publik di-rate-limit; respons OTP tidak membocorkan apakah nomor terdaftar.
- Nama queue RabbitMQ: `<domain>.<event>` (mis. `ocr.requested`, `notify.push`), DLQ `<queue>.dlq`.
- Setiap modul/fitur baru punya README singkat: tujuan, ADR terkait, risiko (R-xxx) yang dimitigasi, apa yang belum.

## Tim agent

Lihat `.claude/agents/`. Ringkas:
`efn-lead` (koordinator, satu-satunya lawan bicara user), `efn-architect` (ADR, koreksi FSD, audit modul), `efn-odoo-functional` (fit-gap & modul konfigurasi `efn_setup_*`), `efn-odoo-engineer` (hanya untuk gap), `efn-bff-engineer`, `efn-frontend-engineer`, `efn-uiux-designer` (audit UX/a11y, token & panduan visual per brand), `efn-data-ai-engineer`, `efn-devsecops-engineer`, `efn-security-reviewer` (read-only, review akhir), `efn-qa-engineer`.
Setiap agent menulis laporan kerjanya ke `docs/agent-reports/<agent>.md`.
