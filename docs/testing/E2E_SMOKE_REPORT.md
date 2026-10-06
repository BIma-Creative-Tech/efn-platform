# Laporan Smoke E2E — stack dev dengan TLS/mTLS aktif (Gelombang 8, 6 Okt 2026)

Penulis: integration engineer (efn-qa-engineer + efn-devsecops-engineer) di bawah efn-lead.
Skrip: `scripts/smoke/` (`run-e2e-smoke.sh`, `bff_smoke.py`, `odoo_pos_smoke.py`; README di folder itu).

## 1. Ringkasan

Semua alur bisnis yang diminta (a–g) **berjalan end-to-end di stack dev nyata** dengan TLS di setiap hop dan mTLS
di sidecar: Super-App → Traefik (TLS 1.3, sertifikat edge diverifikasi, bukan `-k`) → BFF (HTTPS) → PostgreSQL
(verify-full) / Redis (TLS+ACL) / RabbitMQ (amqps) / MinIO (HTTPS, SSE-S3) → ocr-worker → ETL → ClickHouse (TLS);
Odoo → BFF (HTTPS + CA internal + service key); ocr-worker → Ollama (mTLS sidecar).

| Run | Cakupan | Hasil |
|---|---|---|
| Run 1 (orkestrator, a b c d f e g) | 70 cek | 69 lolos + 1 false negative skrip (`b.notification_recorded`: grep karakter `→` UTF-8; notifikasi sendiri tercatat di log BFF). Bagian akhir `g` terpotong karena skrip diedit saat berjalan → `g` diulang: 7/7 |
| **Run 2** (orkestrator bersih setelah perbaikan skrip, a b c d f e g, ±25 menit) | **72 cek** | **72 lolos, 0 gagal**. Ringkasan skrip saat itu tertulis `PASS=73 FAIL=1` karena penghitung naif ikut menghitung baris setup Odoo dan body OTP-salah (`"ok": false` yang memang diharapkan); penghitung diperbaiki ke level cek, `g` diulang: `PASS=7 FAIL=0`, exit 0 |
| Manual tambahan | jalur VLM tanpa model, mTLS Ollama, agregat ledger PG vs ClickHouse, log tanpa HP mentah | lolos |

Satu bug integrasi nyata ditemukan dan diperbaiki (**Ollama menolak 403 semua panggilan VLM**, §4). Satu blocker
desain/infra untuk agent lain: **Ollama 0.5.4 yang di-pin tidak bisa memuat model default `qwen2.5vl`** (§5 R1).

## 2. Lingkungan

- Docker Desktop 29.8.1 (WSL2), 16 CPU / 16 GB. Profil `core` + `data` + `ai` (Ollama **tanpa model**, lihat R1).
  `observability`/`ops` tidak di-boot ulang di gelombang ini (sudah diverifikasi gelombang 7).
- Volume `efn_*` dari gelombang 7 dipakai ulang. Modul dipasang ke DB `efn_odoo`:
  `efn_core, efn_setup_company, efn_setup_security, efn_setup_pos, efn_setup_mrp, efn_setup_audit, efn_kitting,
  efn_pos_otp_gate, efn_pos_payment_gateway, efn_privacy` → exit 0, "102 modules loaded in 79.74s", OCA `auditlog`
  ikut dari image (`/mnt/oca-addons`). Tidak ada `ERROR` Odoo; 27 baris `(ERROR/3)` dari docutils (R4).
- `efn.bff_url` = `https://api-gateway:3000` (data `efn_core`) dan env `EFN_BFF_URL` yang sama di compose.
  `efn_member_hash` dihitung otomatis saat install & saat partner dibuat; backfill tidak diperlukan.
- Data POS smoke (DB dev saja): company FNB, POS config `SMOKE POS FNB` (sesi dibuka), metode `SMOKE Cash` dan
  `SMOKE QRIS` (`use_payment_terminal = efn_gateway`), produk `SMOKE Nasi Goreng` Rp 50.000, program promosi
  `SMOKE Promo OTP` (diskon 10 %, `efn_requires_otp`), partner fiktif A–E.

## 3. Hasil per skenario

Status: **Lolos** = semua cek lolos di runtime; **Sebagian** = alur lolos tetapi ada bagian yang tidak bisa diuji.

| # | Skenario | Status | Cek (contoh bukti) |
|---|---|---|---|
| a | Login OTP Super-App | **Lolos** | `GET /v1/auth/challenge` 200 (TLSv1.3 via Traefik) → PoW ALTCHA dipecahkan skrip (0,07 s, maxnumber 100000) → `POST /v1/auth/otp/request` 202 → OTP dari log `[DEV ONLY] OTP untuk 62812****633` → verify 200 `Bearer`, exp 3600 → JWT `sub` = UUID (tanpa HP) → `GET /v1/me` `{"member_ref_masked":"62812****633","points_balance":0}`. Lewat Next.js: `/api/auth/challenge` 200, `/api/auth/otp/request` 202, `/api/auth/otp/verify` 200 + cookie `efn_session` `HttpOnly; Secure; SameSite`, `/api/me/consents` dengan cookie 200 / tanpa cookie 401, Origin asing 403. Negatif: tanpa captcha 400 `captcha_required`; captcha replay 400; OTP salah 400 `attempts_left: 2`; nomor `+65` 400 `invalid_phone`; `/v1/me` tanpa token / `alg:none` 401; `/v1/pos/*`, `/metrics`, `/internal/*` di publik 404 |
| b | Consent + Price Hunter | **Lolos** (jalur Tesseract) / **Sebagian** (VLM, R1) | Consent versi lama 409 `stale_text`; versi aktif 200; daftar consent benar. Upload non-multipart 415. Foto JPEG sintetis (Pillow, teks "INDOMIE GORENG / Rp 3.500") → 202 `queued` → worker `status=valid reason=price_and_name_match vlm=not_used ms=260` → poin +10 di `GET /v1/me` (ledger `delta_points=10 source=price_hunter`) → log BFF `notify.push price_hunter_valid → 62812****310: sent` + `loyalty earn applied: 10 poin`. Foto sama lagi → `flagged/duplicate_image` (pHash). Foto ber-noise (Tesseract 0,77 < 0,80) → VLM → Ollama `404` (model tidak ada) → `flagged/vlm_unavailable` (perilaku C4 benar). DB: `pg_dump` skema `platform` **0 kemunculan** nomor HP mentah; `price_hunter_report.user_hash` 64 hex, `user_enc` 42 byte versi `0x01` (AES-GCM: 1+12+13+16); `loyalty_ledger.member_ref_enc` 42 byte; `member.member_ref_enc` terisi. MinIO `mc stat`: `Encryption: SSE-S3`, bucket auto-encrypt `sse-s3`, lifecycle expiry aktif |
| c | POS OTP gate dari Odoo | **Lolos** | `pos.config.efn_otp_request` → BFF HTTPS (CA internal) 202 → OTP di log BFF → `efn_otp_verify` ok → `sync_from_ui` order berbayar dengan reward → `gate_state=consumed`, challenge `consumed`, token dihapus. Sync ulang order yang sama tetap `consumed` (idempoten). OTP partner C lalu order partner B → `review` / `member_mismatch`. OTP Rp 45.000 lalu order Rp 135.000 → `review` / `amount_mismatch`. Order berbayar dengan reward tanpa OTP → `UserError` (ditolak, rollback). Order tanpa reward → `paid` |
| d | QRIS POS mock | **Lolos** | `efn_gateway_create` → BFF `POST /v1/pos/payments` 201 `pending` + `qr_string EFN-MOCK-QRIS|…`. Sync dengan `transaction_id` palsu → ditolak `intent_unknown`. `POST /v1/pos/payments/:id/simulate` (dev-only, lewat service key Odoo) → `paid` → `efn_gateway_status` `paid` + `transaction_id`. Sync nominal beda (Rp 100.000 vs intent Rp 50.000) → ditolak `amount_mismatch`; sync benar → order `paid`; intent dipakai order lain → ditolak `order_mismatch`. Webhook publik dengan signature salah → 401 |
| e | DSAR hapus akun lintas store | **Lolos** | `DELETE /v1/me` tanpa step-up 403 `step_up_required` → `POST /v1/me/reauth` 202 → OTP → 200 `step_up_token` → `DELETE /v1/me` 202 `{scheduled_at: +7 hari, points_forfeited: 10}`. **Hash lintas service cocok**: `res.partner.efn_member_hash` (Odoo, pepper HKDF dari secret `member_hash_pepper`) = `dsar_request.member_hash` (BFF). Masa tenggang dilewati (dev: `UPDATE scheduled_at`) → `ErasureService: 1 selesai` → `processing {odoo,clickhouse}`; baris platform dengan hash lama 0/0/0 (member aktif/report/ledger) → cron Odoo `ir_cron_efn_privacy_pull_erasures` → partner menjadi `Pelanggan dihapus 90abab37`, `phone` NULL, `active=f`, hash NULL; BFF log `store=odoo selesai (1 record)` → ETL `erasure ... status=completed deleted={'competitor_prices': 4, 'loyalty_events': 2}` → `privacy.erasure.completed` → `dsar_request` **`completed`**; ClickHouse 0/0 baris untuk hash tersebut. Agregat poin PG vs ClickHouse tetap sama setelah erasure (`price_hunter +30`, `dsar_forfeit -10` di kedua sisi) — risiko G6.3#1 tidak muncul di runtime |
| f | ETL | **Lolos** (Platform) / **Sebagian** (Odoo) | `competitor_prices` dan `loyalty_events` terisi dalam 1 siklus (dev 120 s), hanya hash (tanpa HP). Metabase + sidecar `healthy`. Job `fact_sales_line` dari Odoo **tidak aktif** (`ETL_ENABLE_ODOO` tidak diset; R5) |
| g | Negatif enkripsi (regresi) | **Lolos** | PostgreSQL `sslmode=disable` → `pg_hba.conf rejects ... no encryption`; Odoo `:8443` tanpa sertifikat klien → `tlsv13 alert certificate required`; MinIO HTTP → 400; RabbitMQ `:5672` tertutup; `pg_stat_ssl`: `efn_bff`, `efn_ocr`, `efn_odoo` = TLSv1.3; RabbitMQ `efn-bff`, `efn-ocr` = `ssl=true tlsv1.3`; ocr-worker → Ollama tanpa sertifikat klien → `SSLError`, dengan sertifikat `ocr-worker` → 200 (setelah fix §4) |

### 3.1 Run 2 per skenario (72/72)

| Skenario | Cek lolos | Catatan |
|---|---|---|
| a | 21/21 | termasuk 6 cek route handler Next.js dan 9 cek negatif |
| b | 14/14 | report `valid` +10 poin, duplikat `flagged`, `pg_dump` 0 HP mentah, SSE-S3, notifikasi tercatat |
| c | 9/9 | 3 verifikasi OTP nyata (A, C, D) + consume/resync/member_mismatch/amount_mismatch/tanpa OTP/reguler |
| d | 7/7 | create/forged/simulate/status/amount_mismatch/paid/reuse |
| f | 3/3 | competitor_prices, loyalty_events, Metabase |
| e | 11/11 | step-up (4 cek BFF), hash Odoo = BFF, processing, tombstone 0/0/0, Odoo anonim, completed, ClickHouse 0/0, `/v1/me/requests` 401 (R6) |
| g | 7/7 | lihat tabel di atas |

## 4. Bug yang diperbaiki

| # | File | Sebelum → sesudah | Bukti |
|---|---|---|---|
| 1 | `infra/compose/docker-compose.yml` service `ollama` | tanpa `hostname` → `hostname: ollama` (+ komentar) | Ollama yang bind ke `127.0.0.1` (pola sidecar ghostunnel) menolak Host selain localhost / `os.Hostname()` (proteksi DNS rebinding). Hostname container = id (`38347c4eaa14`), klien mengirim `Host: ollama:11434` → **403** untuk semua panggilan ocr-worker, sehingga jalur VLM selalu `flagged/vlm_unavailable` meski model ada. Reproduksi: dari ocr-worker `GET https://ollama:11434/api/tags` (mTLS) → 403; dengan header `Host: localhost` → 200. Setelah fix + `--force-recreate`: `hostname` = `ollama`, request yang sama → 200; `/api/generate` → 404 "model not found" (jalur sampai ke Ollama). `docker compose config -q` dev & prod lolos |

Perubahan lain (bukan bug produk): skrip smoke baru `scripts/smoke/*` (ruff 0.16.10 check + format bersih,
shellcheck bersih), `docs/runbooks/dev-stack-boot.md` (instal modul, profil `ai`, smoke, jebakan baru).
Tidak ada perubahan kode service, sehingga test unit service tidak perlu diulang (lihat §7).

## 5. Sisa untuk agent lain (dengan bukti)

| # | Pemilik | Temuan | Bukti / reproduksi | Usulan |
|---|---|---|---|---|
| R1 | devsecops + data-ai (+ architect, ADR-008) | **Ollama `0.5.4` yang di-pin tidak bisa memuat model default `qwen2.5vl:7b`** (`OLLAMA_VISION_MODEL`). Akibatnya di semua lingkungan, laporan yang ragu tidak pernah divalidasi VLM → selalu `flagged` (beban review manual) | `docker exec efn-ollama-1 ollama pull qwen2.5vl:3b` → `Error: pull model manifest: 412: The model you are attempting to pull requires a newer version of Ollama` | Naikkan image Ollama ke rilis yang mendukung `qwen2.5vl` (≥ 0.7, pin digest) lalu ulang test mTLS/healthcheck; atau ganti model default yang kompatibel. Smoke `report_vlm` siap dipakai untuk verifikasi |
| R2 | architect + bff | **Nomor HP di query string**: kontrak §3.4 `GET /v1/pos/loyalty/balance?member_ref=<HP>`. URL tercatat utuh oleh access log proxy | Probe publik smoke (`/v1/pos/loyalty/balance?member_ref=62812…` → 404) tercatat di access log Traefik dengan nomor lengkap (2 baris). Jalur normal Odoo → BFF tidak lewat Traefik, tetapi pola ini tetap PII-in-URL | Ubah ke `POST` body atau `member_hash`; sementara, pertimbangkan Traefik `accessLog` tanpa query untuk host `api.` |
| R3 | odoo-functional | Odoo mencoba `https://vies.api.odoo.com/api/vies/1/check_update` (tidak ada egress) → traceback `NameResolutionError` di log | `docker logs efn-odoo-1` (1x per start/cron) | Nonaktifkan pengecekan/update VIES (tidak relevan untuk NPWP Indonesia) atau beri catatan di runbook agar tidak memicu alert log ERROR |
| R4 | odoo-engineer | README.md modul dirender docutils sebagai RST saat install → 27 baris `(ERROR/3) Undefined substitution referenced: "---"` | `odoo-install.log` gelombang 8 | Tambah `description` singkat di manifest atau README.rst; kosmetik, tetapi mengotori hitungan ERROR di Loki |
| R5 | data-ai + devsecops | ETL `fact_sales_line` (POS Odoo → ClickHouse) tidak aktif di dev (`ETL_ENABLE_ODOO` kosong) sehingga penjualan POS smoke tidak masuk mart; grant `efn_etl` ke DB `efn_odoo` belum diuji runtime | `docker logs efn-etl-1`: `jobs=['competitor_prices', 'loyalty_events']` | Aktifkan di dev setelah `services/etl/sql/odoo-readonly-grants.sql` diterapkan, lalu tambah cek di smoke `f` |
| R6 | frontend + architect | Setelah DSAR `completed`, `GET /v1/me/requests` dengan token lama → 401 (member sudah dihapus), sehingga pengguna tidak bisa melihat status "selesai" | smoke `dsar.requests` → `401 unauthorized` | Putuskan apakah status akhir cukup dikomunikasikan lewat WA/email; bila perlu, endpoint status berbasis `request_id` + token sekali pakai |
| R7 | devsecops | Smoke E2E belum di CI | — | Job nightly/self-hosted runner: boot stack, instal modul, `run-e2e-smoke.sh` |

Catatan yang **bukan** bug: Next.js `/api/me` hanya `DELETE` (GET profil dirender server-side); `docker compose
--profile ai` saja gagal validasi karena `ocr-worker` bergantung pada service profil `core` (runbook diperbarui);
log PostgreSQL dev (`log_statement=all`) memuat nomor HP partner Odoo (sudah tercatat di runbook gelombang 7).

## 6. PII di log (UU PDP)

Selama run, log `api-gateway`, `ocr-worker`, `etl`, `superapp-web`, `odoo` **0 baris** dengan nomor HP mentah
(regex `628(12|13)\d{8}` / `08(12|13)\d{8}`); BFF hanya menulis mask `62812****633`. Pengecualian: access log Traefik
(R2) dan log PostgreSQL dev (dikenal).

## 7. Test unit

Tidak ada kode service yang diubah gelombang ini (hanya compose, runbook, dan skrip smoke baru), jadi test unit
BFF/Odoo/pytest tidak perlu diulang. Validasi yang dijalankan: `ruff check` + `ruff format --check` (0.16.10) untuk
`scripts/smoke/*.py` bersih; `shellcheck` (koalaman/shellcheck:stable) untuk `run-e2e-smoke.sh` bersih;
`docker compose config -q` dev & prod (semua profil) lolos.

## 8. Cara mengulang

```bash
bash scripts/smoke/run-e2e-smoke.sh            # semua (±15 menit)
bash scripts/smoke/run-e2e-smoke.sh g          # hanya negatif enkripsi
```
