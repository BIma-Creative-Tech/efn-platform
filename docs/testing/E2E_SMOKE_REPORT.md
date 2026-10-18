# Laporan Smoke E2E — stack dev dengan TLS/mTLS aktif (Gelombang 8 + regresi Gelombang 10, 11 & 12, 6–7 Okt 2026)

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

---

# Gelombang 10 — Regresi penuh setelah gelombang 9–10 (6 Okt 2026)

Cakupan perubahan yang diregresi: BFF SEC-31 (tombstone acak) / R2 (saldo POS via POST) / R6 (receipt DSAR),
frontend receipt DSAR (Next.js), Odoo `efn_audit` (pengganti OCA `auditlog`, ADR-031), object storage SILO di
belakang `s3-gateway` (ADR-030), access key acak (§8.12), Ollama 0.35.1 (R1).

## G10.1 Lingkungan & persiapan

- Docker Desktop (WSL2) 16 CPU / 16 GB. Profil `core` + `data` + `ai` + `observability` + `ops` semuanya jalan
  (RAM cukup); tidak ada container `unhealthy`.
- Build ulang (`$DC build api-gateway superapp-web ocr-worker etl postgres minio odoo`): hanya `api-gateway` dan
  `superapp-web` yang berubah; lainnya dari cache (image odoo tanpa OCA sudah dibangun lead). Container
  di-recreate dengan `up -d`.
- Migrasi SQL `04-platform-v3-6.sql` dijalankan ulang (idempoten, exit 0); `dsar_request.member_hash` nullable.
- Odoo: DB `efn_odoo` sudah dimigrasi `auditlog → efn_audit` (runbook §3a; `auditlog` *uninstalled*).
  `-u efn_audit,efn_setup_audit,efn_privacy,efn_pos_otp_gate,efn_core,efn_pos_payment_gateway,efn_setup_company,
  efn_setup_security,efn_setup_pos` → exit 0, 102 modul, 0 baris ERROR/Traceback. Versi terpasang = manifest.
- Volume: **tidak ada yang dihapus/di-reset**. Volume Ollama kini berisi `qwen2.5vl:3b` (3,2 GB). `.env` lokal
  (gitignored) diubah `OLLAMA_VISION_MODEL=qwen2.5vl:3b`.

## G10.2 Hasil

| Run | Cakupan | Hasil |
|---|---|---|
| Run 1 (a b c d f e g) | 106 cek (32 cek baru) | 104 lolos, 2 gagal: keduanya bug di skrip baru saya (G10.4), bukan bug produk. Ringkasan akhir terpotong (`syntax error` baris 321) karena orkestrator diedit saat berjalan |
| **Run 2** (a b c d f e g, bersih) | **106 cek** | **106 lolos, 0 gagal** (`RINGKASAN: PASS=106 FAIL=0`, exit 0) |
| Skenario `v` (VLM nyata) | 13 cek | run pertama `report_vlm.model_used` gagal (ReadTimeout 90 s, G10.3); dengan `OLLAMA_TIMEOUT_SECONDS=240` (dev): **13/13**, 2× berturut-turut |

Run 2 per skenario: a 21, b 17, c 15, d 7, f 5, e 24, g 17.

### Cek baru gelombang 10 (semua lolos di run 2)

| Cek | Bukti |
|---|---|
| `b.upload_via_s3_gateway` | log HAProxy untuk id report run ini: `PUT /market-scout/reports/2026/10/<id>.jpg 200` (BFF) dan `GET … 200` (ocr-worker). BFF/worker tidak berada di network `objstore` (`efn_cache efn_data efn_egress efn_internal` / `efn_data efn_internal`) |
| `b.old_access_keys_rejected` | `efn-bff`, `efn-ocr`, `pgbackup` + secret masing-masing lewat `s3-gateway` → `The Access Key Id you provided does not exist` |
| `b.random_access_keys_authenticate` | access key acak bff/ocr → terautentikasi; `ListBucket` ditolak `Access Denied` (policy least-privilege) |
| `c.audit_price_logged` | `product.template.list_price` 50000→51000→50000 tercatat 2 baris `efn.audit.log.line` |
| `c.audit_phone_masked` | HP partner A diganti → baris `phone` `is_masked=True`, nilai baru `62813****790`, digit tengah tidak ada di log |
| `c.audit_partner_e_logged` | email partner E (subjek DSAR) diubah → 1 log `res.partner` sebelum erasure |
| `c.pos_balance_post_200` | `efn.bff.client.post("/v1/pos/loyalty/balance", {member_ref})` (service key Odoo) → 200 `{points_balance: 10}` = saldo Super-App |
| `c.pos_balance_old_get_404` / `c.pos_balance_invalid_400` | GET lama → 404; `member_ref` bukan nomor → 400 |
| `e.tombstone_random_unlinkable` | tombstone 64 hex, ≠ `sha256("erased:" + request_id)` (desain lama), ≠ member_hash; dipakai juga oleh 2 baris ledger (sama dengan report); 0 baris `dsar_request` memuatnya |
| `e.dsar_member_hash_nulled` | setelah `completed`: `dsar_request.member_hash` NULL (1/1) |
| `e.odoo_audit_logs_purged` | log `res.partner` partner E = 0, baris ref lintas model = 0, log `purge` baru `note="erasure: 3 log dihapus, 0 baris ref dikosongkan"` tanpa `res_id` |
| `e.odoo_pending_markers_cleared` | `efn.privacy.erasure.pending` = 0 setelah complete |
| `dsar.receipt_status` / `_wrong_404` / `_missing_404` / `_no_pii` | BFF `GET /v1/dsar/requests/:id` + `X-DSAR-Receipt` → 200 `{id,type,status=completed,requested_at,scheduled_at,completed_at}` tanpa nomor HP; receipt diubah 1 karakter → 404; tanpa header → 404 |
| `web.dsar_receipt_status` / `_wrong_404` / `_missing_404` / `_cross_site_rejected` | Next.js `/api/dsar/requests/:id` dengan cookie `efn_dsar_receipt` → 200 hanya `{id,status,requested_at,scheduled_at,completed_at}` (tanpa HP/receipt); cookie salah/hilang → 404; `Sec-Fetch-Site: cross-site` → 403 |
| `report_vlm.model_used` (`v`) | foto ber-noise (Tesseract < 0,80) → VLM `qwen2.5vl:3b` → `valid / price_and_name_match`, worker `vlm=ok ms=69340` / `71391` |

PII di log selama run (regex `628(12|13)\d{7,8}` / `08(12|13)\d{7,8}`, 5000 baris terakhir): `api-gateway`,
`ocr-worker`, `etl`, `superapp-web`, `odoo`, `s3-gateway`, **`traefik` = 0** (R2 gelombang 8 tertutup: probe
saldo kini POST body).

## G10.3 Status VLM (R1)

- `ollama pull qwen2.5vl:3b` di Ollama 0.35.1: manifest diterima. Batas 20 menit habis di 86 % (2,7/3,2 GB,
  ±1–2 MB/s); pull dilanjutkan (resume blob) dan selesai ±10 menit kemudian → `success`.
- Inferensi CPU (8 thread, tanpa GPU): foto baru 1200×800 = ±1300 token prompt @ ±19 tok/s → **±70 s**; foto yang
  sama (cache) ±5 s. Hasil benar: `{"product_name": "INDOMIE GORENG", "price": "Rp 3.500"}`.
- Default worker `OLLAMA_TIMEOUT_SECONDS=90` → run pertama `ReadTimeout` → `flagged/vlm_unavailable` (fallback
  benar). Perbaikan dev: `infra/compose/docker-compose.dev.yml` service `ocr-worker`
  `OLLAMA_TIMEOUT_SECONDS: ${OLLAMA_TIMEOUT_SECONDS:-240}` → `vlm=ok`.
- Staging/produksi: 70 s per foto di CPU tidak layak sebagai throughput (prefetch 2 → ±1,7 foto/menit). Butuh GPU
  atau resize gambar sebelum VLM (G10.5 butir 1).

## G10.4 Perbaikan

| # | File | Perubahan | Alasan |
|---|---|---|---|
| 1 | `infra/compose/docker-compose.dev.yml` (ocr-worker) | + `OLLAMA_TIMEOUT_SECONDS: ${OLLAMA_TIMEOUT_SECONDS:-240}` | G10.3; `docker compose config -q` dev (semua profil) & base lolos |
| 2 | `scripts/smoke/run-e2e-smoke.sh` | cek baru b/c/e (tabel G10.2) + skenario opsional `v` | regresi gelombang 9–10 |
| 3 | `scripts/smoke/odoo_pos_smoke.py` | langkah `audit`, `audit_partner`, `balance` | idem |
| 4 | `scripts/smoke/bff_smoke.py` | receipt via Next.js, `dsar.receipt_no_pii`, `report_vlm.model_used` | idem |
| 5 | `scripts/smoke/README.md`, `docs/runbooks/dev-stack-boot.md` §3b | dokumentasi cek baru, `run-e2e-smoke.sh v`, waktu pull & inferensi CPU | — |

Bug di skrip baru saya yang muncul di run 1 (sudah diperbaiki): (a) `partner_member_ref()` mengembalikan tuple
`(member_ref, error)` → body `{member_ref: [...]}` → BFF 400 (penolakan yang benar); (b) `docker logs --since` tidak
mengembalikan baris baru untuk container `s3-gateway` di Docker Desktop, diganti `--tail` + id report.
**Tidak ada bug produk yang ditemukan di gelombang ini.**

## G10.5 Sisa / observasi (bukan blocker)

1. **data-ai / devsecops**: VLM di CPU ±70 s per foto. Usulan: resize gambar sebelum dikirim ke VLM (mis. sisi
   terpanjang 768 px) untuk memangkas token gambar, dan putuskan GPU untuk host Ollama produksi (ADR-008).
2. **devsecops**: `log-format` di `infra/s3-gateway/haproxy.cfg` memakai `%Tt` (durasi sesi termasuk idle
   keep-alive), sehingga log menunjukkan 30 s untuk scrape metrik dan 12 s untuk PUT yang sebenarnya cepat.
   Usulan: tambah `%Ta` (waktu aktif request). Sekali tercatat watchdog HAProxy `thread 10 has stopped processing
   traffic for 739 milliseconds` (02:30Z, sebelum restart 07:27Z; tidak berulang di run ini).
3. **devsecops**: `docker logs --since` untuk container `s3-gateway` tidak andal di Docker Desktop (hanya
   mengembalikan file lama sampai 02:53Z), sedangkan `--tail` benar. Perlu dicek apakah promtail mengirim log
   HAProxy terbaru ke Loki.
4. **frontend**: `superapp-web` dev memakai 395/512 MiB (77 %) saat smoke (`next dev`); bila sering OOM, naikkan
   limit di dev override.
5. Belum diverifikasi: frontend POS OWL, halaman status penghapusan di browser (route handler diuji langsung),
   webhook payment provider nyata, VLM di GPU, export DSAR besar.

## G10.6 Test unit

Tidak ada kode service yang diubah (hanya dev compose, skrip smoke, dokumentasi), sehingga test unit
BFF/Next.js/Odoo/pytest tidak diulang. `ruff check` + `ruff format --check` (0.16.10) `scripts/smoke/*.py` bersih;
`shellcheck` `run-e2e-smoke.sh` bersih; `docker compose config -q` dev & base lolos.

```bash
bash scripts/smoke/run-e2e-smoke.sh        # a b c d f e g (±20 menit)
bash scripts/smoke/run-e2e-smoke.sh v      # VLM nyata (butuh model; ±2 menit di CPU)
```

# Gelombang 11 — Penutupan: regresi penuh + sink audit nyata + SEC-44 lewat RPC (6 Okt 2026)

Basis `26110ef` (SEC-41/42/43/44/45/47/48). Tidak ada commit. Tidak ada volume yang dihapus.

## G11.1 Lingkungan & persiapan

- Docker Desktop (WSL2) 16 CPU / 16 GB; profil `core` + `data` + `ai` + `observability` + `ops` (Ollama dengan
  `qwen2.5vl:3b` termuat). 37 container jalan, tidak ada yang `unhealthy` (8 tanpa healthcheck: exporter, Loki,
  Prometheus, Alertmanager, Grafana).
- `$DC --profile core --profile data --profile ai --profile observability --profile ops build`: semua image lokal
  (base dipin `tag@sha256`, SEC-47) dibangun ulang sukses; hanya `odoo`, `minio`, `minio-init` yang berubah sehingga
  di-recreate oleh `up -d` (sidecar `odoo-tls`/`odoo-ws-tls` ikut di-recreate bersamaan).
- Upgrade Odoo (Odoo dihentikan dulu): `odoo -d efn_odoo --stop-after-init -u efn_audit,efn_setup_audit,efn_pos_otp_gate`
  → **exit 0, 0 baris ERROR/CRITICAL, 0 WARNING**. Post-migrate `19.0.1.1.0` jalan: 2 rule lama
  (`efn_audit_log_rule_company`, `efn_audit_log_line_rule_company`) dihapus. Versi terpasang: `efn_audit 19.0.1.1.0`,
  `efn_setup_audit 19.0.2.0.0`, `efn_pos_otp_gate 19.0.1.5.0`. Setelah start: `healthy`, 0 baris ERROR di log Odoo
  sepanjang sesi.

## G11.2 Smoke E2E penuh

`bash scripts/smoke/run-e2e-smoke.sh` (a b c d f e g, satu run, ±7 menit): **`RINGKASAN: PASS=108 FAIL=0`, exit 0**.
Per skenario: a 21, b 17, c 16, d 7, f 5, e 24, g 19 (termasuk cek s3-gateway gelombang 11 dari devsecops:
`g.s3gw_encoded_variants_blocked`, `g.s3gw_encoded_value_get_forwarded`). PII di log (regex HP 62/08 + 812/813/877/899,
5000 baris terakhir) `api-gateway`, `ocr-worker`, `etl`, `superapp-web`, `odoo`, `traefik`, `s3-gateway` = 0.

## G11.3 Sink audit nyata (SEC-43): Odoo → Promtail → Loki → ruler → Alertmanager

Perubahan dilakukan lewat **jalur staf nyata**: `curl` host → Traefik `wg-https` (`https://erp.efn.local:9443`) →
`odoo-tls` (mTLS) → Odoo; login form `/web/login` + **TOTP** (`/web/login/totp`, kebijakan `employee_required`
aktif) sebagai user QA `qa_g11_sysadmin` (`base.group_system`, uid 5), lalu `/web/dataset/call_kw` (jalur UI).
User QA (`qa_g11_sysadmin`, `qa_g11_cashier`, `qa_g11_posmgr`, company PT EFN F&B) dibuat di DB dev dengan
password + secret TOTP acak yang disimpan hanya di `/tmp` container Odoo (0600), tidak pernah dicetak.

| Langkah | Hasil |
|---|---|
| `ir.config_parameter.set_param('efn.audit.retention_days', '400')` | OK (730 → 400) |
| `set_param(..., '30')` | **ditolak** `odoo.exceptions.ValidationError`: "Retensi log audit minimal 365 hari (SEC-43; 0 = simpan selamanya). Nilai 30 ditolak." Nilai tetap 400; **tidak ada** baris sink untuk transaksi yang di-rollback (postcommit) |
| `ir.actions.server.create` (server action dummy `QA G11 dummy server action`, id 591, `code: pass`) | OK |
| stdout Odoo | 3 baris `odoo.addons.efn_audit.sink: EFN_AUDIT_SINK {...}`: `write ir.config_parameter fields=[value] uid=5 sudo=false log_id=21`; `create ir.actions.server fields=[code,model_id,name,state] log_id=22`; `write ir.actions.server fields=[update_path] sudo=true log_id=23` (tanpa nilai/PII) |
| Loki `query_range {stream="efn_audit_sink"}` lewat mTLS (dari container Grafana, sertifikat klien `grafana`, CA internal) | 3 baris, 2 stream: `{audit_op="create"/"write", container="efn-odoo-1", service="odoo", stream="efn_audit_sink"}` |
| Loki tanpa sertifikat klien / HTTP polos | `tlsv13 alert certificate required` / koneksi ditutup |
| Alertmanager `GET /alertmanager/api/v2/alerts` (mTLS) | **3 alert `EfnAuditControlChange`** (severity warning, team security, receiver `ops-webhook`, `startsAt 10:08:42Z` = evaluasi ruler pertama ±16 s setelah perubahan): `write ir.config_parameter uid 5 sudo=false`, `create ir.actions.server uid 5`, `write ir.actions.server uid 5 sudo=true` |
| Pemulihan | `efn.audit.retention_days` dikembalikan ke 730 lewat RPC yang sama (perubahan kontrol ke-4, tercatat) |

Tidak dijalankan: **purge lewat server action** (memicu `EfnAuditUnexpectedPurge`). Langkah ini (mengubah kode
server action agar memanggil `_efn_audit_purge_logs`, termasuk percobaan pada log terlindung) **ditolak oleh
pengaman izin sesi agen** karena berupa penghapusan log audit. Jalur alert purge sudah diuji devsecops dengan
baris sintetis; uji dengan Odoo nyata menunggu izin eksplisit user. Server action dummy id 591 dibiarkan
(`code: pass`, tidak terikat ke menu/otomasi).

## G11.4 SEC-44 lewat RPC nyata (kunci HP member)

Partner member = SMOKE Member C (id 49, punya riwayat `efn.pos.otp.challenge`). Kasir `qa_g11_cashier`
(`point_of_sale.group_pos_user` + `base.group_partner_manager`/Contact Creation; tanpa grup itu kasir sudah ditolak
ACL standar untuk create/write partner apa pun, konsisten dengan `efn_setup_pos/README.md`), login TOTP lewat Traefik.

| # | Panggilan (kasir) | Hasil |
|---|---|---|
| T1 | `res.partner.write([49], {phone})` | **ditolak** `AccessError` "Nomor HP pelanggan ini sudah dipakai untuk OTP/promo member… (SEC-07)" |
| T2 | idem + context `{"efn_member_phone_checked": [49]}` (meniru penanda) | **ditolak** (penanda di `cr.cache`, bukan context) |
| T3 | `web_save` (jalur form UI) | **ditolak** |
| T4 | `write({phone: false})` (mengosongkan) | **ditolak** |
| T4b | `write([49, 60], ...)` campur member + non-member | **ditolak** seluruhnya (atomik) |
| T4c | `write` HP yang sama (tanpa perubahan) | OK (bukan perubahan) |
| T5 | `write` field lain (`street`) partner member | OK |
| T6/T7 | `create` partner baru ber-HP; ubah HP partner non-member | OK |
| **T8 (jalur sudo)** | `POST /my/address/submit` (portal standar: `partner_sudo.write(address_values)` = **sudo dengan uid kasir**), partner milik kasir sendiri (id 58) dijadikan member | **ditolak**: HTTP 403, log `EFN POS: user 6 ditolak mengubah HP 1 partner member (SEC-07)`, transaksi di-rollback (HP & zip tidak berubah). Sebelum SEC-44 jalur ini lolos karena pengecualian `env.su` |
| T8b | idem untuk partner lain (49) | 403 `Forbidden` standar portal (`_can_be_edited_by_current_customer`) |
| Kontrol | `qa_g11_posmgr` (`group_pos_manager` + Contact Creation) `write([49], {phone})` | OK + chatter "Nomor HP member diubah oleh QA G11 posmgr (SEC-07). phone: 62812\*\*\*\*049 → 62812\*\*\*\*981" (ter-mask); `efn.audit.log` uid 7 ter-mask. HP dikembalikan ke nilai asal |

Tidak ditemukan method RPC standar POS 19.0 lain yang menulis HP partner dengan `sudo()` (`point_of_sale/models/res_partner.py`
tidak punya `create_from_ui`); jalur sudo nyata yang dapat dijangkau kasir adalah portal `/my/address/submit` (T8).

## G11.5 Bug / observasi

**Tidak ada bug produk atau integrasi yang ditemukan; tidak ada file kode yang diubah.**

1. (security-reviewer, observasi) Respons error JSON-RPC Odoo menyertakan `data.debug` = traceback Python lengkap
   untuk user yang login (perilaku standar Odoo; hanya terjangkau dari `wg-https`). Pertimbangkan apakah perlu
   disaring di produksi.
2. (runbook) Mengubah grup user lewat `odoo shell` (proses terpisah) tidak meng-invalidasi cache `has_group`
   worker Odoo yang berjalan; perubahan baru berlaku setelah perubahan lewat UI/RPC atau restart. Ubah hak akses
   lewat UI/RPC.
3. (functional, konfirmasi PoC) `group_pos_user` saja tidak bisa membuat/mengubah partner (ACL standar:
   Contact/Creation, Inventory/Administrator, Manufacturing/Administrator). Onboarding pelanggan di kasir butuh
   `base.group_partner_manager`: keputusan konfigurasi (butir "cek di PoC" README `efn_setup_pos` kini terkonfirmasi
   di runtime).
4. Data QA tertinggal di DB dev: user `qa_g11_*` (id 5–7, partner 57–59), partner `QA G11 walk-in` (60), challenge
   OTP `QA-G11-SEC44` untuk partner 58, server action 591. Hapus bila perlu.

## G11.6 Belum diverifikasi
- `EfnAuditUnexpectedPurge` / `EfnAuditRetentionPurgeBurst` dengan Odoo nyata (G11.3; butuh izin user).
- Pengiriman Alertmanager ke receiver nyata (`ops-webhook` dev); retensi 400 hari stream (konfigurasi, tidak diuji waktu).
- Test unit service tidak diulang (tidak ada kode yang diubah); CI GitHub belum dijalankan untuk `26110ef`.

# Gelombang 12 — Penutupan: GL nyata, salinan audit + uji tamper, SEC-34 ALTCHA, smoke penuh + VLM (7 Okt 2026)

Basis `ce7e2b0`. Tidak ada commit. Tidak ada volume yang dihapus. Odoo engineer paralel (SEC-22) bekerja di harness
terpisah; tidak ditunggu.

## G12.1 Lingkungan & persiapan

| Langkah | Hasil |
|---|---|
| `$DC ... build api-gateway ocr-worker etl odoo` (Odoo target runtime, tanpa Chrome) | sukses |
| Upgrade Odoo (odoo dihentikan): `-u efn_audit,efn_setup_audit,efn_pos_otp_gate,efn_pos_payment_gateway` | **exit 0, 0 ERROR/CRITICAL** (1 WARNING kosmetik `--without-demo=all` di Odoo 19); `efn_audit 19.0.1.2.0`, kolom `efn_audit_log.origin_user_id` ada; `up -d --force-recreate odoo odoo-tls odoo-ws-tls` → healthy, 0 ERROR di log Odoo sesudahnya |
| Grant `services/etl/sql/odoo-readonly-grants.sql` sebagai `efn_odoo` (pola `deploy.sh`) | rc 0 (NOTICE: index sudah ada) |
| Recreate `api-gateway`, `ocr-worker` (+2 sidecar metrik), `etl`; `up -d` semua profil `core data ai observability ops` | 40 container jalan, 0 unhealthy; Prometheus **34/34 target up** |
| ETL | `jobs=[..., companies, gl_accounts, gl_lines, gl_lines_by_move] audit_copy=True`; container nyata tidak pernah mencatat `job=audit_copy gagal` |

## G12.2 Mart konsolidasi GL dengan jurnal nyata

Data dev dibuat lewat `odoo shell` (ORM standar, `action_post`): PT EFN Retail (3) `INV/2026/00001` 1.500.000 ke
partner PT EFN F&B (inter-company), PT EFN F&B (4) `BILL/2026/10/0001` 1.500.000 dari partner Retail (IC), Retail
`INV/2026/00002` 250.000 ke partner fiktif "QA G12 Pelanggan Fiktif" (tanpa pajak agar angka mudah dicek). Siklus ETL
berikutnya: `gl_lines rows=6`, `gl_lines_by_move rows=6`.

Dibaca sebagai **`efn_metabase`** (clickhouse-client TLS, password dari file secret lewat stdin) dari `v_gl_consolidated`:

| company | akun | IC | lawan | balance | balance_after_elimination |
|---|---|---|---|---|---|
| 3 | 11210010 receivable | 0 | – | 250.000 | 250.000 |
| 3 | 11210010 receivable | 1 | 4 | 1.500.000 | 0 |
| 3 | 41000010 income | 0 | – | −250.000 | −250.000 |
| 3 | 41000010 income | 1 | 4 | −1.500.000 | 0 |
| 4 | 21100010 payable | 1 | 3 | −1.500.000 | 0 |
| 4 | 51000010 expense | 1 | 3 | 1.500.000 | 0 |

Konsolidasi per akun setelah eliminasi: receivable 250.000, income −250.000, payable/expense IC 0 — benar. `period`
2026-10-01, `fiscal_year` 2026. Negatif: `efn_metabase` SELECT `audit_log_copy` dan INSERT `fact_gl_line` → **497
ACCESS_DENIED**. Koneksi database ClickHouse di UI Metabase tidak dikonfigurasi/diuji; yang diverifikasi adalah hak user
`efn_metabase` yang dipakai koneksi itu.

## G12.3 Salinan audit tamper-evident + uji tamper nyata

Rantai sebelum uji: company 0 seq 14, company 4 seq 6 (20 log). Rekonsiliasi baseline (one-shot; ETL utama dihentikan
agar tidak ada dua instance): semua kelas 0.

Uji (DB **dev**, superuser, izin lead): `UPDATE efn_audit_log SET res_name='QA-G12 TAMPER' WHERE id=17` (log
`write product.template` company 4, sudah tersalin).

| Langkah | Hasil |
|---|---|
| Run 1 (one-shot `compose run --rm`, `ETL_AUDIT_RECON_INTERVAL_SECONDS=1`) | `WARNING ... EFN_AUDIT_RECON {"modified":1,"sample_ids":[17]}` (kelas lain 0) — tetapi baris **tidak sampai ke Loki** (container `--rm` hilang sebelum discovery Promtail) → tidak ada alert. Observasi, bukan bug produk |
| Run 2 (instance ETL sementara long-running `efn-etl-qa-recon`, label `service=etl`, recon 60 s; ETL utama berhenti) | 04:30:50 recon bersih → tamper 04:30:57 → **04:31:51 `WARNING EFN_AUDIT_RECON {"modified":1,"sample_ids":[17]}`** |
| Loki `{service="etl"} \|= "EFN_AUDIT_RECON"` (mTLS) | 3 baris dari `efn-etl-qa-recon`, termasuk WARNING |
| Alertmanager `GET /alertmanager/api/v2/alerts` (mTLS, SAN grafana) | **`EfnAuditReconAnomaly` active, critical, `startsAt 04:32:06Z`** (±15 s setelah log) |
| Pemulihan | baris dikembalikan (`res_name='SMOKE Nasi Goreng'`; `write_date` tidak tersentuh) 04:31:56; recon berikut 04:32:51 semua 0; alert resolved setelah jendela 15 menit. Instance sementara dihapus, `efn-etl-1` dinyalakan lagi |
| Setelah smoke (DSAR `e` mem-purge log partner) | recon one-shot: `max_log_id 46, checked 28`, semua kelas 0 (log yang di-purge sebelum lewat `ETL_AUDIT_SETTLE_SECONDS` memang tidak pernah tersalin) |

`EfnAuditCopyFailing` yang aktif sejak 03:54 berasal dari container sintetis devsecops (`efn-g12-fake-etl`, baris
03:50/03:55); resolved 04:56 ketika baris itu keluar dari jendela 1 jam. Log baru dari smoke (`odoo shell`, uid 2)
tersalin dengan `origin_user_id = NULL` — sesuai desain SEC-46 (tanpa sesi HTTP/helper elevate tidak ada uid pemicu).

## G12.4 Smoke E2E penuh (+ VLM) dan SEC-34

`bash scripts/smoke/run-e2e-smoke.sh a b c d f e g v`:

- Run 1: `PASS=108 FAIL=4` — keempatnya di `v` (`consent.*`, `report_vlm.submit` 401): `v` memakai token akun skenario
  `a` yang sudah **dihapus** oleh DSAR di `e`. Bug urutan di skrip smoke (G12.5), bukan regresi produk.
- Run 2 (setelah perbaikan, satu run): **`RINGKASAN: PASS=121 FAIL=0`, exit 0**. Per skenario: a 21, b 17, c 16, d 7,
  f 5, e 24, g 19, v 13. VLM: `report_vlm.final` `valid / price_and_name_match`, `model_used` OK (qwen2.5vl:3b, image
  ocr-worker G12).
- ALTCHA: solver smoke lolos (`maxnumber 500000` = level 0, PoW ±0,25 s per login).
- **SEC-34 binding IP** (uji tambahan; dua container hidup bersamaan di `efn_edge`, 172.30.0.3 dan .4, lewat Traefik):
  solusi dari A dikirim dari B → **400 `captcha_required`**; solusi dari A dikirim dari A → 202. Catatan: dua
  `docker run --rm` berurutan bisa mendapat IP yang sama (percobaan pertama memberi 202 positif-palsu).
- PII di log (regex HP 62/08 + 812/813/877/899, 5000 baris terakhir): api-gateway, ocr-worker, etl, superapp-web, odoo,
  traefik, s3-gateway = **0**.

## G12.5 Bug / perbaikan

1. **`scripts/smoke/run-e2e-smoke.sh` skenario `v` setelah `e`** (diperbaiki): `scenario_e` menandai `ERASED=1`;
   `scenario_v` login dengan nomor fiktif baru bila akun sudah dihapus (sebelumnya hanya login bila `state.json`
   kosong → token akun terhapus → 401). shellcheck v0.11.0 bersih; dibuktikan run 2 (121/121).

Tidak ada bug produk/integrasi lain. Observasi (bukan bug):
- `EfnPublicEndpointDown` (api/app.efn.local) dan `EfnWgCertProbeFailed` (traefik:8443) firing permanen di dev
  (sertifikat edge self-signed; diketahui devsecops). Pertimbangkan silence/inhibit khusus dev agar alert nyata tidak
  tenggelam.
- Job ETL membaca ulang baris `>= watermark − overlap` tiap siklus (mis. `gl_accounts rows=523` tiap 120 s karena semua
  akun ber-`write_date` sama saat instalasi). Aman (ReplacingMergeTree + `FINAL`), tetapi `count()` tanpa FINAL
  menggandakan (fact_gl_line 12 fisik / 6 logis) — query ad hoc harus memakai view/FINAL.
- Log ETL dari container `compose run --rm` tidak masuk Loki; operasi manual ETL yang harus bisa memicu alert jangan
  memakai one-shot `--rm`.

## G12.6 Data QA tertinggal di DB dev
3 jurnal posted (`INV/2026/00001`, `INV/2026/00002` company 3; `BILL/2026/10/0001` company 4), partner "QA G12
Pelanggan Fiktif", data `SMOKE *` run ini. Log audit id 17 sudah dikembalikan ke nilai asal.

## G12.7 Belum diverifikasi
- Koneksi UI Metabase → ClickHouse (hanya hak user `efn_metabase`); GL tahun fiskal non-Desember, cancel/reset-to-draft,
  multi-currency.
- Kelas rekonsiliasi `erasure`/`retention` untuk log yang sudah tersalin lalu di-purge (butuh menunggu settle 600 s
  sebelum purge; tidak dijalankan).
- Tour POS (OWL) G12 tidak dijalankan di stack ini (hanya harness odoo engineer); CI GitHub belum dijalankan.

# Gelombang 13 — Penutupan: Fase 1 loyalty POS end-to-end + 3 dashboard Metabase (7 Okt 2026)

Basis `c1b770a`. Tidak ada commit, tidak ada volume yang dihapus. Selama sesi ini agent lain mengubah working tree
(SEC-58 dkk.: `efn_core`, `efn_pos_loyalty_ledger`, `efn_pos_otp_gate`, BFF `src/pos`, `06-platform-loyalty-guard.sql`,
mulai 07:14 UTC). Karena itu run final dijalankan terhadap **snapshot `c1b770a`** (lihat G13.3).

## G13.1 Lingkungan & persiapan

| Langkah | Hasil |
|---|---|
| `$DC ... build api-gateway odoo etl metabase` | sukses (metabase: driver ClickHouse 1.52.0 ber-checksum) |
| Upgrade Odoo (odoo dihentikan): `-u efn_audit,efn_pos_otp_gate,efn_setup_pos -i efn_pos_loyalty_ledger` | **exit 0, 0 ERROR/CRITICAL** (1 WARNING `--without-demo=all`); 103 modul; `efn_audit 19.0.1.3.0`, `efn_pos_otp_gate 19.0.1.6.0`, `efn_setup_pos 19.0.1.2.0`, `efn_pos_loyalty_ledger 19.0.1.0.0` installed |
| `up -d` semua profil `core data ai observability ops` | semua container healthy; odoo/api-gateway/etl di-recreate; log Odoo sesudah start 0 ERROR |
| SQL 05 | `platform.loyalty_reservation`, `platform.loyalty_pos_earn` ada (diterapkan devsecops 06:37) |
| Secret baru ter-mount | api-gateway `migration_api_key`; odoo & etl `efn_audit_sink_key`; metabase-provision `metabase_admin_password` |
| `metabase-provision` | exit 0, semua kartu `completed` |

## G13.2 E2E loyalty POS nyata — skenario baru `l` (`scripts/smoke/run-e2e-smoke.sh`)

Odoo (`odoo shell`, metode server yang sama dengan frontend POS) → BFF nyata (HTTPS + CA, service key). Tiga partner
fiktif baru per run (L, M, N). Parameter default BFF dev: 1 poin/Rp10.000, Rp100/poin, min 50, maks 50 %.

| # | Langkah | Hasil (run final) |
|---|---|---|
| 1 | Order ber-member 20 × Rp50.000 → outbox `earn` | order `paid`; outbox `done` (1 percobaan); `points_earned=100`; `platform.loyalty_pos_earn` 100/1.000.000; ledger `pos_earn` +100 |
| 2 | Cek saldo dari POS (`efn_loyalty_balance`) | 100, `point_value_idr=100`, `redeem_min_points=50`, member ter-mask `62812****073` |
| 3 | OTP `pos_redeem` (kode dari log console) → reserve 60 poin | verify OK; reserve `value_idr=6000`; BFF `reserved/60/6000` |
| 3b | Order 4 × Rp50.000 + baris Poin EFN −6.000 → sync | `paid`, 1 baris poin, total 194.000, outbox `earn` + `redeem_commit` |
| 3c | Outbox → commit | commit `done` delta −60, reservasi `committed`; earn 19 (Rp194.000; porsi poin tidak dapat poin, K-1); BFF `committed/60/6000`, ledger −60/+19; **saldo 59** |
| 4 | Batal di dialog (RPC release) | lokal `released` (`released_by_cashier`); BFF `released/50/5000` |
| 5 | Order draft berpoin → `action_pos_order_cancel` | baris poin tetap di draft (reservasi aktif); order `cancel`; outbox `redeem_release` `done`; BFF `released`; **saldo tetap 59** |
| 6a | OTP promo → reserve | Odoo `otp_required`; token promo langsung ke `POST /v1/pos/loyalty/redeem/reserve` → **409 `token_invalid`** |
| 6b | OTP redeem → order promo | gate promo menolak (`UserError` "Promo ... memerlukan verifikasi OTP"); token redeem ke `POST /v1/pos/discount-token/consume` → **409 `invalid`** |
| 7 | Refund 2/4 unit order 3b, baris penyesuaian dari `efn_loyalty_refund_preview` | rasio 0,5, penyesuaian Rp3.000, total refund −97.000; outbox `reverse` (`eligible` 97.000) `done` delta +20; ledger restore **+30** (floor 60×0,5), clawback **−10** (ceil 19×0,5); **saldo 79** |
| 8 | `docker stop` api-gateway → sync order 2 × Rp50.000 | order tetap `paid`; earn `pending` (`bff_unreachable`), status poin order `pending` |
| 8b | `docker start` → outbox retry (cron + backoff) | earn `done` pada percobaan ke-2, `points_earned=10`, ledger +10; **saldo 89** — earn tertunda tidak hilang |
| 9 | PDP | 0 kemunculan digit HP L/M/N di `pg_dump` `platform.loyalty_*`, `efn_pos_loyalty_*`, `efn_pos_otp_challenge`, log BFF run ini dan log Odoo 30 menit; regex HP di 5000 baris log api-gateway, odoo, etl, ocr-worker, superapp-web, traefik, metabase = **0**; WARNING Odoo saat BFF mati memuat `member=62812****073` (ter-mask) |
| 10 | ETL → ClickHouse | `fact_sales_line`: 1 baris `line_kind=points` (−6.000) untuk order 3b, 2 baris `is_refund=1` untuk refund |

## G13.3 Smoke penuh `a b c d f e g l`

| Run | Hasil | Sebab |
|---|---|---|
| `l` saja (±07:10, kode `c1b770a`) | 31/32 | 1 FAIL = ekspresi cek skenario baru (`eligible_amount` numeric `1000000.00`); data benar. Diperbaiki (`::bigint`) |
| Penuh #1 (07:16) | berhenti di `l` langkah reserve tanpa ringkasan (a–g semua PASS) | working tree berubah di tengah run: field baru `efn.pos.loyalty.reservation.redeem_max_pct` (agent lain, 07:18) dimuat `odoo shell` tanpa upgrade DB → `psycopg2.errors.UndefinedColumn`; harness berhenti diam-diam (G13.5 #1) |
| Penuh #2 (07:33) | berhenti di `b` | api-gateway baru di-recreate → Traefik 404 ±20 s (temuan load test G13) + harness berhenti (G13.5 #2) |
| **Penuh #3 (07:34–07:49)** | **`RINGKASAN: PASS=140 FAIL=0`, exit 0** | a 21, b 17, c 15, d 7, f 5, e 24, g 19, l 32 |

Run #3 memakai override compose sementara yang me-mount `services/odoo/addons` dan `services/api-gateway/src` dari
`git archive c1b770a` (odoo + sidecar dan api-gateway di-recreate dengan snapshot itu); image, DB, dan service lain
sama. Sesudahnya odoo dan api-gateway di-recreate lagi dengan compose normal (mount working tree) → healthy. `v` tidak
dijalankan. Budget OTP global (`rl:otp:budget:*`) dibersihkan sebelum run (pola `tests/load/run.sh`). Load test tidak
dijalankan.

## G13.4 Dashboard (setelah siklus ETL)

View ClickHouse (hari ini, FNB / "SMOKE POS FNB", sebelum run final): `v_pos_sales_daily` penjualan bersih 1.847.000,
poin EFN 6.000, refund 97.000, promo 50.000; `v_pos_payment_daily` 2 metode; `v_stock_expiring` 1 lot (ETLDEV-L1,
3 hari); `v_stock_below_reorder` 1; `v_stock_current` 3; `v_price_index` 16; `v_etl_freshness` semua job `ok`,
0 kegagalan 24 jam.

Metabase lewat API (admin dari secret file, mTLS ke sidecar `metabase-tls`, endpoint dashboard
`/api/dashboard/:id/dashcard/:dc/card/:c/query` seperti browser): **3 dashboard, 28 dashcard, 0 gagal**.
- Penjualan Harian Outlet: "Diskon, promo, poin EFN & refund per hari" 07-10: promo 75.000, poin EFN 6.000, refund
  97.000; "Ringkasan outlet harian" 19 order, 1 order refund.
- Stok & Kedaluwarsa: lot ETLDEV-L1 "segera", sisa 3 hari, 12 unit; 1 produk di bawah titik pesan ulang.
- Price Hunter: Indomie Goreng vs "Toko Smoke" 17 laporan, indeks 94,6; produk belum termatch 0 baris.

## G13.5 Bug / perbaikan (harness smoke; tidak ada bug produk ditemukan)

1. `run-e2e-smoke.sh` `odoo()`: traceback `psycopg2.errors.*` tidak memuat "Error" → `grep` kosong + `pipefail` →
   seluruh run berhenti tanpa ringkasan. Kini juga mencari `errors\.` dan, bila kosong, mencetak
   `SMOKE_RESULT {"ok": false, "error": "odoo_shell_no_result"}` sehingga cek gagal tercatat.
2. `scenario_b`: pipeline `gw=$(docker logs ... | grep ...)` menghentikan run bila id report kosong → `|| true`.
3. `odoo_pos_smoke.py`: helper `bff_post`/`bff_get` memakai `_efn_post`/`_efn_get` (SEC-58, working tree) bila ada,
   selain itu `post`/`get` (`c1b770a`), agar smoke jalan di kedua versi `efn_core`.
4. Skenario `l` + langkah Odoo baru `loy_partner`, `loy_balance`, `loy_release`, `loy_cancel`, `loy_token_probe`,
   `loy_refund`; `loy_sync` mendukung `SMOKE_DRAFT=1`; `loy_outbox` mengeluarkan field datar. shellcheck v0.11.0 dan
   ruff 0.16.10 (check + format) bersih.

Observasi (bukan bug):
- `v_pos_sales_daily.refunds` = uang yang dikembalikan (97.000 = barang 100.000 − penyesuaian poin 3.000). Baris
  penyesuaian poin pada refund (`line_kind=points`, `is_refund=1`) masuk `refunds`, sedangkan `points_redeemed_idr`
  hanya order non-refund, sehingga nilai poin yang dikembalikan (3.000) tidak terlihat terpisah. Usul data-AI: kolom
  refund barang kotor + poin dikembalikan.
- **Working tree saat laporan ini tidak sesuai DB dev**: perubahan agent lain (versi manifest `efn_core`,
  `efn_pos_loyalty_ledger`, `efn_pos_otp_gate`, `efn_pos_payment_gateway`, `efn_privacy`, `efn_setup_pos` naik; field
  baru `redeem_max_pct`; `06-platform-loyalty-guard.sql`) belum di-upgrade/diterapkan. Odoo dev memuat kode itu
  setelah recreate, jadi reserve poin di dev gagal `UndefinedColumn` sampai pemiliknya menjalankan `-u` + SQL 06.
  BFF dev hanya mengompilasi ulang saat container restart (bind mount Windows tidak memicu `--watch`).
- Skenario `l` mematikan api-gateway ±1 menit (alert dev BFF down bisa firing sesaat).

## G13.6 Data QA tertinggal di DB dev
Partner fiktif `SMOKE Member L/M/N` (run `l`, penuh #1, penuh #3), order/reservasi/ledger poin terkait, data `SMOKE *`
skenario lain. Tidak ada nomor asli.

## G13.7 Belum diverifikasi
- Frontend POS (OWL) tidak disentuh: skrip memanggil RPC server yang sama dengan tombol Cek Poin/Tukar Poin/refund.
  Tour refund berpoin belum ada (catatan odoo-engineer).
- `reservation_expired` (> 24 jam), redeem saat POS offline, refund berpoin offline, reverse kumulatif > 1 refund.
- Kode working tree pasca-`c1b770a` (SEC-58 dkk.) tidak termasuk run ini.

# Gelombang 13c — Penutupan: SQL 06, upgrade Odoo, alert loyalty, regresi penuh (7 Okt 2026)

Basis `6b7b7fc` (working tree bersih di awal). Dijalankan efn-devsecops + efn-qa (satu agent). Tidak ada commit, tidak
ada volume yang dihapus. Waktu UTC.

## G13c.1 Lingkungan & persiapan

| Langkah | Hasil |
|---|---|
| Backup `pg_dump -Fc efn_odoo` (scratchpad, di luar repo) | 6,2 MB |
| `06-platform-loyalty-guard.sql` ke PG dev (superuser, socket lokal), diulang 2× | sukses; run ke-2 hanya NOTICE "already exists" (idempoten). `platform.erasure_suppression` (efn_bff `ard` + `w(expires_at)`), indeks `ix_loyalty_pos_earn_member_created`, `ix_erasure_suppression_expires` ada. Peringatan BFF "relation platform.erasure_suppression does not exist" (tiap menit sejak 07:52) berhenti |
| `$DC build odoo api-gateway` | sukses |
| `-u efn_core,efn_pos_otp_gate,efn_pos_loyalty_ledger,efn_pos_payment_gateway,efn_privacy,efn_setup_pos` (odoo dihentikan) | **exit 0, 0 ERROR/CRITICAL**; 13 WARNING `Skipping deletion for missing XML ID point_of_sale.<pecahan>` (kosmetik). Versi: efn_core 1.6.0, otp_gate 1.7.0, loyalty_ledger 1.1.0, payment_gateway 1.1.0, privacy 1.3.0, setup_pos 1.3.0. SEC-63: 0 `barcode.rule` tipe `discount` tersisa |
| Recreate `api-gateway odoo odoo-tls odoo-ws-tls promtail loki loki-tls alertmanager prometheus` | semua healthy. Log BFF tanpa kode ANSI (`NO_COLOR=1`) |
| Heartbeat rekonsiliasi | `EFN_LOYALTY_RECON {"status":"ok","anomalies":0,...}` tiap 5 menit (dev) → Loki `{stream="efn_loyalty", loyalty_event="recon", recon_status="ok"}`; gauge Prometheus `efn_loyalty_recon_last_success_timestamp_seconds` terisi; rule `EfnLoyaltyReconStale` health ok |
| BFF untuk run | sementara `MIGRATION_ENABLED=true` (recreate dengan env shell), **dikembalikan ke `false`** setelah run (diverifikasi `docker inspect`) |

## G13c.2 Smoke penuh `a b c d f e g l` (satu run, 08:15–08:31)

**RINGKASAN: PASS=153 FAIL=0** (G13: 140; +13 cek baru). Log: scratchpad `g13c/smoke1.log`.

| Cek baru | Hasil |
|---|---|
| (a) SEC-58 `l.sec58_rpc_internal_bff_client_rejected`, `l.sec58_rpc_cashier_bff_client_rejected` | user `base.group_user` dan kasir POS FNB, login nyata password + TOTP lewat Traefik wg-https (`erp.efn.local`, sertifikat diverifikasi), kontrol `res.partner.search_count` OK; `efn.bff.client._efn_post`/`_efn_get` → `AccessError: Private methods ... cannot be called remotely`; `post`/`get` → ditolak (metode tidak ada, pesan disamarkan "Internal server error"). 8 baris ERROR `odoo.http` di log Odoo = penolakan ini (diharapkan) |
| (b) `l.earn_daily_limit_rejected`, `l.earn_daily_limit_no_bff_earn` | partner O, order 401 × Rp50.000 = 2005 poin > 2000/hari: outbox earn `failed` `earn_limit_exceeded`, order `review` (`earn:earn_limit_exceeded`), 0 `loyalty_pos_earn`/ledger |
| (b) `l.anomaly_log_line` | `WARN [PosLoyaltyService] EFN_LOYALTY_ANOMALY {"kind":"earn_limit_member_day","severity":"warning","company":"FNB","order_ref":"<uuid>","points":2005,"earned_today":0,"limit":2000}`, tanpa ANSI, tanpa HP |
| (b) `l.anomaly_in_loki` | stream `{stream="efn_loyalty", loyalty_event="anomaly", kind="earn_limit_member_day", severity="warning"}` 1 baris |
| (b) `l.anomaly_alert_in_alertmanager` | `EfnLoyaltyAnomalyWarning` **active** (labels kind=earn_limit_member_day, severity=warning, team=finance) dalam ±1 menit |
| `l.recon_heartbeat_in_loki` | 3 heartbeat ok dalam 30 menit |
| (c) `l.refund_noreceipt_review`, `l.refund_noreceipt_no_bff_effect` | retur tanpa struk (qty −1 tanpa `refunded_orderline_id`) member L: order `paid`, `efn_loyalty_state=review` (`return_unlinked`), tanpa outbox; ledger/earn BFF 0, saldo tetap 89 |
| (d) `e.migration_member_deleting_409` | DSAR delete terjadwal: `POST /internal/v1/migration/members` dan `/opening-balance` → **409 `member_deleting`** (keduanya) |
| (d) `e.migration_member_erased_409` | setelah erasure selesai: members → **409 `member_erased`**, member aktif dengan hash lama 0 |
| (d) `e.migration_anomaly_logged` | 3 baris `EFN_LOYALTY_ANOMALY migration_erased_member` |

Semua cek lama (a–g, l) tetap lolos, termasuk PDP (0 kemunculan digit HP partner L/M/N/O di tabel & log) dan ETL poin.

## G13c.3 Bug / perbaikan
- Tidak ada bug produk ditemukan. Perubahan harness: `odoo_rpc_smoke.py`, `obs_smoke.py` (baru), langkah Odoo
  `rpc_users`, `loy_refund_noreceipt`, `loy_outbox` kini menyertakan `loyalty_note`; cek migrasi otomatis dilewati
  bila BFF tidak `MIGRATION_ENABLED=true`.
- Temuan saat menulis konfigurasi: selector promtail berisi regex dalam string LogQL bertanda kutip ganda wajib backslash
  ganda (`\\[` di YAML); string backtick di selector match promtail ditolak ("unexpected IDENTIFIER"). Diperbaiki sebelum run.

## G13c.4 Belum diverifikasi
- Alert `EfnLoyaltyAnomalyCritical` (page) hanya divalidasi sintaks + evaluasi ruler (tidak ada kejadian critical yang
  dipicu; butuh manipulasi ledger / batas PT 50.000 poin/jam). Dead-man `EfnLoyaltyReconMissing` tidak ditunggu 2 jam;
  `EfnLoyaltyReconStale` diuji `promtool test rules` (alert pada 2h20m tanpa sukses, tidak ada alert palsu saat gauge 0).
- Log ruler Loki menampilkan `status=500` di baris metrik untuk SEMUA rule (lama dan baru) walau query manual 200 dan
  alert terkirim; tampaknya artefak logging ruler lokal — belum diselidiki.
- `deploy.sh` (cek migrasi wajib 04/05/06) belum dijalankan di host Linux nyata. CI GitHub belum dijalankan.

# Gelombang 17 — Integrasi nyata penjualan online Fase 3 + smoke `o` (9 Okt 2026)

Dasar: docs/04 §10 (v5.0) + L-1..L-13, ADR-034/ADR-019 (Proposed). Runtime nyata: Docker Desktop (WSL2), stack dev
profil core + data + ai + observability + ops. Pelaksana: efn-qa-engineer di bawah efn-lead. Tanpa commit/push.

## G17.1 Integrasi stack dev (langkah yang dijalankan)
1. Backup DB `efn_odoo` + `efn_platform`: `pg_dump -Fc | openssl enc -aes-256-cbc -pbkdf2 -iter 200000` ke scratchpad
   lokal di luar repo (kunci acak terpisah, tidak di repo). Tidak ada dump plaintext.
2. Build ulang image `odoo`, `api-gateway`, `superapp-web` (exit 0).
3. `stop odoo odoo-tls odoo-ws-tls odoo-rpc-gateway` → `run --rm odoo ... -i efn_online_order,efn_setup_online`:
   exit 0, **0 baris ERROR/CRITICAL**; ikut terpasang `sale`, `sale_stock`, `sale_loyalty`, `sale_mrp`
   (`pos_sale`/`sale_management` tetap tidak terpasang).
4. Konfigurasi kanal dev lewat `scripts/smoke/odoo_online_smoke.py setup` (data fiktif `SMOKE`, idempoten, dipakai ulang
   tiap run): pembulatan Rp1, gudang `SMKOL`, jurnal bank titipan `SMKTT`, pricelist IDR, user teknis `efn_online_rtl`
   (base.group_user + group_online_channel, satu company RTL), kanal `RTL` (25 menit) + outlet `TOKO-01`, 4 produk
   (PPN l10n_id, 2 dengan gambar), stok awal, staf outlet `smoke_online_staff` (Inventory user, bukan admin).
5. API key: **prosedur runbook `odoo-online-api-key.md` §1 dijalankan apa adanya** (heredoc `with_user`, kedaluwarsa
   +85 hari) → 41 byte (40 hex + LF) → `secrets/odoo_api_key_online_rtl.txt` (git-ignored; nilai tidak pernah dicetak).
   Runbook terverifikasi bekerja.
6. Migrasi `07-platform-online.sql` (exit 0; 8 tabel `platform.online_*`).
7. `up -d --force-recreate` odoo, odoo-tls, odoo-ws-tls, odoo-rpc-gateway, api-gateway, superapp-web, superapp-web-tls:
   semua healthy. BFF langsung menyinkronkan katalog dari E-28 nyata: `Katalog RTL: 4 item diperbarui, 2 gambar`.
8. `infra/odoo-rpc-gateway/test-gateway.sh`: **97/97 PASS** (sebelum dan sesudah smoke). Path sah tetap 401 di skrip itu
   karena token ujinya palsu (desain skrip); dengan **key nyata** ke-10 path diuji di smoke `o` (G17.2 no. 0).

## G17.2 Smoke `o` (`scripts/smoke/run-e2e-smoke.sh o`; file baru `online_smoke.py` + `odoo_online_smoke.py`)
| # | Skenario §10.15 / permintaan lead | Hasil |
|---|---|---|
| 0 | 10 path `/json/2` lewat gateway + key nyata | catalog/stock/cancel (`absent`)/status **200**; image/quote/place/mark_paid/refund/refund_settled dengan argumen sengaja salah → **422 `efn_online:<kode>`** (jawaban aplikasi); 0 deny, 0 × 401/403 |
| 1 | katalog → cart → quote → checkout → bayar (webhook mock HMAC) → confirmed → ready (aksi staf, user non-admin) → serah terima kode ambil → completed | lulus; SO draft + hash KDF saat place, `pickup_code_enc` AES-GCM v1 di BFF; confirmed = SO `sale` + faktur posted; picking `done`; hash Odoo & ciphertext BFF dihapus setelah serah terima; gambar katalog JPEG re-encode `immutable`; stok tanpa `qty` |
| 2 | Idempotensi klik ganda (L-8) | key `chk1_…` sama → order sama (201), 1 baris di daftar; body beda → 409 `idempotency_conflict`; tanpa key → 400 `idempotency_key_required` |
| 3 | `accept_total` salah | 409 `price_changed` |
| 4 | Timeout 15 menit (dipercepat: `expires_at` di DB dev) | sweeper → order `expired`, bagian `cancelled`; Odoo `cancelled/payment_expired`, SO `cancel` |
| 5 | Bayar terlambat | intent `cancelled→paid` → refund `late_payment` **settled** = porsi; order tetap `expired` |
| 6 | Stok kurang setelah bayar | `stock_short` → Odoo `cancelled/stock_short`, SO `cancel` → refund `full` **settled** = porsi penuh |
| 7 | Kode ambil salah 5× | Odoo `review` (`pickup_code_attempts`, 5 percobaan) ✔; **bagian BFF tetap `confirmed`** ✘ (G17-QA-03) |
| 8 | Event Odoo HMAC salah / replay | tanpa tanda tangan, secret salah, `t` −600 s dan +600 s, body diubah → 401 `invalid_signature`; tanpa service key → 401; seq lama → 200 `ignored_stale`; duplikat → 200 `duplicate`; `checkout_ref` asing 404; company beda 409 `company_mismatch` |
| 9 | Kill switch `ONLINE_ENABLED=false` | overlay sementara + recreate: cart GET/PUT, quote, daftar order, checkout → **503 `online_unavailable`**; katalog & `/v1/me` tetap 200; dipulihkan (ONLINE_ENABLED=true, katalog 200) |
| 10 | Anti-enumeration | GET/cancel/pickup-code/simulate order member lain, UUID acak, non-UUID, `1 or 1=1` → semua **404 `not_found`**; tanpa token 401 |
| 11 | DSAR `DELETE /v1/me` | **409 `open_online_orders`** (setelah perbaikan G17-QA-01), akun tetap aktif; `GET /v1/me/data` memuat order tanpa kode ambil/QR |
| 12 | Log tanpa kode ambil/HP | 0 kemunculan kode (dengan & tanpa strip) di log BFF, Odoo, odoo-rpc-gateway, Traefik, Loki (kontrol positif Loki 200 baris); 0 digit HP di log BFF/Odoo/Loki; 0 di `pg_dump` `efn_online_*` + `sale_order`; heartbeat `EFN_ONLINE_RECON` ada di Loki |
| + | Refund parsial CLI `online-refund` (L-3) | `settled` Rp5.550 + nota kredit Odoo; refund > sisa ditolak (exit 1, `invalid_input`), `refunded_amount` tidak berubah; `online_admin_audit` tercatat (setelah perbaikan G17-QA-02) |
| + | Next.js `https://app.efn.local/api/*` (FE↔BFF) | login cookie → katalog → PUT cart (harga klien dibuang; Origin asing 403) → quote → `POST /api/orders` 201, klik ganda dengan header browser berbeda → **order sama** (key server-side L-8) → GET order (QR ada) → simulate → confirmed → pickup-code route 200; UUID asing 404 |

## G17.3 Smoke penuh `a b c d f e g l o` (satu run, ±37 menit)
**PASS=241 FAIL=1.** Per tahap: a 21/0 · b 17/0 · c 15/0 · d 7/0 · f 6/0 · e 24/0 · g 19/0 · l 42/0 (tahap lama
**151/151**, tanpa regresi setelah `sale`/`sale_stock` terpasang) · **o 90/1**. Satu-satunya FAIL =
`o.status[wrong]=fulfilling/review` (G17-QA-03, menunggu keputusan kontrak — sengaja dibiarkan merah).

## G17.4 Bug yang ditemukan runtime & diperbaiki QA
| ID | Bukti | Perbaikan |
|---|---|---|
| G17-QA-01 (BFF, **PDP**) | `DELETE /v1/me` → **202**, hapus akun dijadwalkan walau bagian `confirmed`; log tidak pernah memuat `Pra-hapus akun`. `dist/privacy/dsar.service.js`: `design:paramtypes` parameter terakhir = `Object` (tipe `OnlinePrivacyService \| null`) → `@Optional()` menyuntik undefined → L-4 mati diam-diam (unit test membuat service dengan `new`) | `services/api-gateway/src/privacy/dsar.service.ts:76-78` `@Optional() @Inject(OnlinePrivacyService)`; test regresi `src/privacy/dsar-injection.spec.ts`. Runtime: 409 `open_online_orders`. Jest 42 suite / 501 test lulus, build + lint 0 error |
| G17-QA-02 (Odoo E-28) | CLI `online-refund` → `HTTP 422`, log Odoo `Invalid language code: id_ID`. BFF selalu mengirim `context.lang=id_ID`; `efn_online_refund` / `efn_online_refund_settled` memakai `self.env` (context klien) alih-alih env kanal yang sudah dibersihkan → `env.lang` melempar UserError bila bahasa id_ID tidak terpasang (DB dev: tidak terpasang). Metode lain lolos karena memakai env kanal | `services/odoo/addons/efn_online_order/models/efn_online_order.py` ±587–607 (`order.env[...]`) & ±620 (`channel.env[...]`); test baru `test_refund_with_bff_lang_context_not_installed`. Container test: efn_online_order 61 + efn_setup_online 7 test, **0 failed / 0 error / 0 baris ERROR**; ruff check + format lolos |

Perbaikan harness (bukan produk): ekspektasi `bool_and` → `true`; `|| true` pada substitusi CLI (set -e + pipefail);
`/v1/me/data` butuh `X-Step-Up-Token`; tunggu router Traefik setelah recreate api-gateway (±10 s 404 tanpa origin
walau container healthy).

## G17.5 Temuan terbuka (untuk lead / builder)
| ID | Pemilik | Temuan |
|---|---|---|
| G17-QA-03 | lead (kontrak §10.6.2) → BFF | Odoo memindah order `confirmed\|ready → review` (kode ambil salah 5×; docs/08 §8.3) dan mengirim event `review` (outbox `done`), tetapi BFF memberi peringkat sama (`confirmed = review = 3`, `ready = 4`) sehingga transisi diabaikan **namun dicatat `applied`** di `online_order_event`. Akibat: pelanggan masih melihat `confirmed` dan masih bisa meminta kode ambil walau Odoo mengunci pesanan; rekonsiliasi pull tidak memperbaikinya. Diagram §10.6.2 hanya punya `paying → review`. Perlu keputusan: izinkan `confirmed\|ready → review` sebagai pengecualian monoton (seperti `review → confirmed\|cancelled`) + outcome `ignored_stale` bila tidak diterapkan |
| G17-QA-04 | efn-odoo-functional | Label pajak ke pelanggan = `Non-luxury Good Taxes` (nama pajak l10n_id berbahasa Inggris; `invoice_label` kosong; bahasa id_ID tidak terpasang). Kontrak mencontohkan `PPN 11%`. Pasang bahasa Indonesia / isi `invoice_label` pajak lewat `efn_setup_*`. BFF selalu mengirim `lang=id_ID` — produksi sebaiknya memasang id_ID |
| G17-QA-05 | devsecops (runbook) | Dev: `nest start --watch` di container **tidak** mendeteksi perubahan bind mount dari Windows (Docker Desktop) → perubahan `src/` butuh `up -d --force-recreate api-gateway`. Sebelum integrasi, BFF dev lama berstatus `unhealthy` (kompilasi TS tertahan di tengah perubahan G17) |
| G17-QA-06 | lead (§10.14) | Terverifikasi: json2 19.0 memetakan `UserError` → **HTTP 422** dengan body `{"name":"odoo.exceptions.UserError","message":"efn_online:<kode>",…}`; `X-Odoo-Database` paksa gateway bekerja pada server satu DB; `efn_online_cancel` ref tak dikenal → 200 `absent`. Butir "belum terverifikasi" §10.14 terkait bisa ditutup |

## G17.6 Data QA tertinggal di DB dev
- Odoo `efn_odoo`: kanal `RTL` + outlet `TOKO-01`, produk/gudang/jurnal/pricelist `SMOKE*`, user `efn_online_rtl` (1 API
  key aktif s.d. +85 hari, nama `efn-online-RTL-20261009`) dan `smoke_online_staff`; SO `S00001..S00010` + faktur/nota
  kredit. **Artefak diagnosis:** satu refund Odoo `RINV/2026/00001` (Rp5.550) pada SO `S00001` dibuat langsung lewat
  gateway saat mendiagnosis G17-QA-02 — tidak tercatat di BFF (`online_refund`), sehingga Σ refund Odoo ≠ BFF untuk
  bagian itu (data dev saja).
- Platform: order/bagian/refund online; 1 permintaan DSAR terjadwal dari run sebelum perbaikan G17-QA-01 (member
  fiktif; diproses normal setelah masa tenggang).
- Rollback: dump terenkripsi pra-G17 di scratchpad lokal, atau reset volume dev.

## G17.7 Belum diverifikasi
- Browser nyata (render QR, hitung mundur, polling, pembaca layar) — smoke hanya route handler Next.js.
- Notifikasi WA/push `online_order_ready` ke perangkat (template belum terdaftar; hanya event diterima BFF).
- Circuit breaker & alert `EfnOnline*` (Odoo mati, key kedaluwarsa → `EfnOnlineOdooAuthFailure`) tidak dipicu.
- CLI `online-resolve` dan `online-refund-settle` (jalur `manual_required`) tidak dijalankan; provider selain mock.
- Dua place paralel lintas proses BFF; DLQ `order.*` di bawah beban; retensi 7 hari kode ambil (hanya NULL saat final).

## G17.8 Run akhir setelah L-14..L-20 dan perbaikan r10/re-review (10 Okt 2026, HEAD `32686c3`)
Kode yang diuji: commit `32686c3` (SEC-95..112: QA-03 review, L-16 refund `approval_required`, label pajak, gateway
maxconn, watcher polling, L-19/L-20 peran CS + `claim_token` + pool json2 terpisah, guard Odoo `cr.cache` + level
`stock.move`, SEC-112 settle). Satu-satunya perubahan yang belum di-commit adalah harness smoke QA ini.

**Integrasi:**
- Image odoo/api-gateway/superapp-web dibangun ulang.
- `-u efn_online_order,efn_setup_online,efn_setup_company,efn_setup_pos`: exit 0, 0 ERROR.
- Label pajak dev diisi sekali lewat `odoo shell`, mengikuti tabel README `efn_setup_company` (noupdate). Hasilnya
  `tax_ST4` = `PPN 11%`, grup `PPN 11%`.
- Migrasi 07 versi baru (idempoten, termasuk `claim_token`).
- Recreate odoo, odoo-tls, odoo-ws-tls, odoo-rpc-gateway, api-gateway, superapp-web, superapp-web-tls: semua healthy.
- `test-gateway.sh` 97/97.
- `efn_online_pos_sale` tidak dipasang di dev. `pos_sale` tidak terpasang, jadi jalur POS↔SO online tidak diuji smoke.

**Smoke penuh `a b c d f e g l o`: PASS=267 FAIL=0.**

| Tahap | PASS/FAIL |
|---|---|
| a | 21/0 |
| b | 17/0 |
| c | 15/0 |
| d | 7/0 |
| f | 6/0 |
| e | 24/0 |
| g | 19/0 |
| l | 42/0 |
| **o** | **116/0** |

Cek baru atau berubah di tahap `o`:
- **Review (QA-03 / L-14):** kode ambil salah 5× membuat order **dan** bagian menjadi `review`. `GET pickup-code`
  dijawab 409 `order_in_review`.
- **Label pajak (QA-04):** quote memuat label baris dan grup `PPN 11%`, di 5 quote.
- **Stok (SEC-96):** id di luar katalog → 404, juga bila dicampur dengan id sah dan saat cache negatif.
- **Serah terima (SEC-95/104/105):** validasi picking SO online di luar wizard ditolak, dengan maupun tanpa context
  palsu `efn_online_handover_picking_ids`/`efn_online_internal`.
- **Context palsu (SEC-104):** staf dengan context `efn_online_internal` palsu gagal membatalkan SO (`AccessError`).
  Superuser yang menulis `state` SO langsung ditolak (`UserError`).
- **Batal oleh Odoo (SEC-95):**
  - Batal tanpa alasan ditolak.
  - Manajer membatalkan dengan alasan: Odoo `cancelled/ops`, nota kredit terbit, event `initiator=odoo`.
  - Refund BFF berstatus `approval_required` (pemicu `odoo-uid:<n>`), bagian `cancelled`, `refunded_amount` 0.
  - Approve oleh peran `cs` ditolak. Approve oleh approver lain → `settled` = porsi penuh. Kedua aksi teraudit.
- **Refund CS (L-19/L-20):**
  - Operator `cs` membuat refund → `approval_required`.
  - Approve oleh pembuat yang sama (walau berperan approver) ditolak `rejected_same_actor`.
  - Approve oleh peran `cs` lain ditolak `rejected_role`.
  - Approve oleh approver lain → `settled` Rp5.550 + nota kredit Odoo.
  - Refund melebihi pesanan ditolak.
- **Settle manual (SEC-112):** peran `cs` ditolak (`rejected_role`) dan approver yang juga pembuat ditolak
  (`rejected_same_actor`). Status refund tidak berubah. Identitas dan peran CLI di dev disimulasikan lewat env
  `EFN_CS_OPERATOR`/`EFN_CS_ROLE`; di host keduanya diisi wrapper `efn-online-cs` (wrapper tidak dijalankan di smoke).

Harness:
- Skenario 4b, langkah Odoo `ops_cancel`, `picking_direct`, `fake_ctx`, manajer `smoke_online_mgr`.
- Helper `cs()` untuk menjalankan CLI CS dengan operator dan peran.
- Recreate api-gateway kini menunggu 5 jawaban 200 berturut-turut lewat Traefik, karena router dilepas ±30–40 s saat
  status health berubah. Inilah penyebab 404 pada kill switch di run sebelumnya.
- Catatan: dua run antara (o3, full2) tidak sah. Kode dan skrip berubah saat run berjalan (commit paralel builder dan
  recreate tepat sebelum run), sehingga hasilnya dibuang.

Sisa:
- Pemisahan tugas untuk refund dari pembatalan Odoo bertumpu pada peran: pemicu dicatat `odoo-uid`, dan uid Odoo tidak
  bisa dipetakan ke akun host. Ini sesuai L-19c.
- `efn_online_pos_sale` dan jalur POS tidak diuji di stack dev.
- Wrapper host `efn-online-cs` dengan sudo dan docker asli belum diuji.

# Gelombang 18 — Fondasi pilot ritel: Rp1, Poin EFN = alat bayar, refund ke metode asal, privasi (15 Okt 2026)

Dasar: `docs/10_RENCANA_GELOMBANG_18_PLUS.md`, docs/04 §9.9 (K-18-1..8, L-23..L-26) dan §10.17 (L-21, L-22), review r11 +
re-review G18 (SEC-113..129). Kode diuji: HEAD `bc0a06a` branch `chore/wave-18`; satu-satunya perubahan non-harness di
working tree adalah `infra/compose/docker-compose.dev.yml` (lihat G18.4). Runtime nyata: Docker Desktop (WSL2), stack
dev penuh. Pelaksana: efn-qa-engineer. Tanpa commit/push.

## G18.1 Stack dev (DB Odoo BARU, presisi Rp1)
1. **Backup.** `efn_odoo` dan `efn_platform` lama di-dump `pg_dump -Fc | openssl enc -aes-256-cbc -pbkdf2` ke scratchpad
   lokal di luar repo, dengan kunci acak terpisah. Tidak ada dump plaintext.
2. **Build.** Image odoo, api-gateway, etl, superapp-web dibangun ulang dari HEAD.
3. **DB baru.** `DROP DATABASE efn_odoo` + filestore dibersihkan, lalu `-i base`, lalu satu kali `-i` 17 modul `efn_*`
   (pola CI + `efn_setup_*` + `efn_setup_security` + online + `efn_online_pos_sale` + `efn_pos_refund_origin`).
   - Exit 0, **0 baris ERROR**.
   - `res_currency` IDR: `rounding = 1`, `decimal_places = 0`.
   - 2 metode Poin EFN per PT terbentuk. `efn_pos_refund_origin`, `pos_sale`, `efn_pos_loyalty_ledger`, `efn_pos_otp_gate`
     berstatus `installed`.
4. **Migrasi.** 07 dan 08 versi terbaru (exit 0). Grant ETL Odoo (`odoo-readonly-grants.sql`) diulang.
5. **`.env` dev diisi nilai G18 dari `.env.example`.** Isinya: `DSAR_OPEN_ORDER_MAX_DEFER_DAYS=14`,
   `ERASURE_SUPPRESSION_DAYS=365`, `MEMBER_MIN_AGE=17`, `PRICE_HUNTER_GEO_RETENTION_DAYS=90`,
   `CH_PSEUDONYM_RETENTION_YEARS=5`, `CH_GEO_RETENTION_DAYS=90`, `MB_AUDIT_MAX_RETENTION_DAYS=3650`,
   `ONLINE_ADMIN_AUDIT_RETENTION_DAYS=3650`, `ODOO_RPC_*`, `LOYALTY_EARN_EXCLUDE_POINT_PAYMENT=true`,
   `LOYALTY_RESTORE_TOLERANCE_PCT=10`, `LOYALTY_COMMITTED_RESERVATION_RETENTION_DAYS=3650`, `LOYALTY_POINT_VALUE_IDR=100`,
   `ETL_LOYALTY_POINTS_MODE=payment`, dan `ETL_LOYALTY_RECON_NOT_BEFORE` = waktu reset.
6. **Recreate.** Semua profil di-recreate (`up -d --force-recreate`) dan semua healthy.
7. **Kanal online.** Disiapkan ulang lewat `odoo_online_smoke.py setup`: label pajak `PPN 11%` langsung dari
   `efn_setup_company` (DB baru). API key user teknis berlaku 30 hari (ditulis ke `secrets/`, nilai tidak dicetak).
8. **Data turunan yang ikut di-reset (dev).** Id Odoo dimulai ulang dari 1, jadi data turunan lama bertabrakan:
   - salinan katalog BFF kanal RTL;
   - mart ClickHouse sumber Odoo (`fact_sales_line` source=pos, `fact_pos_payment`, `dim_*`, `fact_gl_line`) beserta
     watermark job Odoo;
   - `processed_message`/`online_order_event` online lama (lihat temuan G18-QA-01).

## G18.2 Smoke penuh `a b c d f e g l p o` (satu run bersih, ±50 menit): **PASS=305 FAIL=0**

| Tahap | PASS/FAIL |
|---|---|
| a | 22/0 |
| b | 17/0 |
| c | 15/0 |
| d | 7/0 |
| f | 6/0 |
| e | 24/0 |
| g | 19/0 |
| l | 43/0 |
| **p (baru, G18 POS)** | **31/0** |
| o | 121/0 |

**Tahap `l` (diperbarui ke desain E-30):**
- Redeem dicatat sebagai baris **pembayaran** Poin EFN Rp6.000. Total order tetap Rp200.000 (DPP penuh), dan commit
  membawa `payment_amount`.
- Draft berpoin mempertahankan pembayaran poin.
- Refund 2/4 dicatat sebagai pembayaran Poin EFN −Rp3.000, dengan `points_restored_value` 3.000 di outbox Odoo dan di
  `platform.loyalty_pos_reverse` (30 poin).
- Produk "Poin EFN" lama diarsipkan, dan metode Poin EFN non-tunai.
- Di ETL, `fact_sales_line` u2 tidak punya baris `points`. `fact_pos_payment.is_loyalty_points` mencatat +6.000 / −3.000.

**Tahap `p` (baru), berurutan:**
1. **W-03.** Order ber-PB1 (Rp12.345 × 3) menghasilkan total Rp40.739 dan PB1 Rp3.704 (bulat).
2. **E-30.** Redeem 50 poin Rp5.000 sebagai pembayaran: pajak tetap Rp3.704, dan reservasi BFF `committed/50/5000`.
3. **E-31.**
   - Refund ke EDC atas order tunai ditolak ("Refund harus ke metode bayar asal").
   - Refund tunai melebihi yang dibayar ditolak; refund tunai sesuai lolos.
   - Retur tanpa struk ke EDC ditolak ("hanya tunai").
4. **L-26 (SEC-128).**
   - Refund dengan nilai restore salah (Rp5.000 untuk 1 dari 3 unit; preview Rp1.667) ditahan Odoo
     (`refund_points_allocation_mismatch`).
   - Supervisor menyetujui apa adanya, lalu BFF menolak `restore_value_mismatch` dan reverse ditahan lagi.
   - Supervisor memakai "setujui nilai restore": status `done`, `restore_approved` = true di Odoo dan di
     `platform.loyalty_pos_reverse`.
   - Barang kategori tanpa poin yang dibayar sebagian poin: refund sah lolos (restore 2.500).
5. **SEC-125 draft.** Draft dengan nominal poin ≠ reservasi: baris poin dibuang.
6. **SEC-116.** `is_change` palsu (EDC 2× + tunai −total) tersimpan False. Refund order EDC ke tunai yang berlabel
   `is_change` ditolak.
7. **E-34 / K-18-8.**
   - `pos_enroll` tanpa centang usia ditolak lokal (`age_confirmation_required`, tanpa OTP terkirim).
   - Dengan centang: OTP, lalu `enrolled`, challenge `consumed`, partner terkunci (SEC-115), dan BFF
     `member.age_confirmed_source='pos'`.
8. **Probe RPC web client NYATA sebagai kasir** (password + TOTP lewat Traefik wg-https).

   | Aksi kasir | Hasil |
   |---|---|
   | Write `amount` / `payment_method_id` / unlink `pos.payment` order paid (SEC-113) | ditolak `UserError` |
   | Write HP member terkunci (SEC-115) | ditolak `AccessError` |
   | Create pelanggan individu (SEC-121) | OK (company PT F&B) |
   | Create perusahaan (SEC-121) | ditolak |
   | `action_pos_order_paid`, `write state=paid`, `create` order paid (SEC-125) | ditolak |
   | `sync_from_ui` dengan `payment_ids` `[1|2|4, id]` milik order lain (SEC-126) | ditolak |

   Setelah semua probe, pembayaran, HP, dan draft tidak berubah.
9. **K-18-6 / SEC-118.** Rekonsiliasi harian mode `payment` untuk hari ini dijalankan dengan fungsi ETL yang sama
   (`etl_points_recon_smoke.py`, read-only).
   - Semua order berpoin run ini **0 selisih** dan `points_restore_value_mismatch` = 0.
   - Total hari itu mencatat 1 order tidak cocok dari run yang terputus sebelumnya: refund dengan reverse `hold` yang
     tidak pernah diputuskan supervisor. Ini **temuan yang benar** (lihat G18.5), bukan bug.

**Tahap `o` (tambahan G18):**
- Label pajak quote `PPN 11%` (baris + grup).
- **L-21a.** `DELETE /v1/me` dijawab 409 `open_online_orders` (14 hari). Konfirmasi `?confirm_open_orders=true` dengan
  token step-up yang sama dijawab **202** dengan `open_online_orders=1` dan `erasure_deadline`.
- **L-22.** Status lewat receipt dan `GET /v1/me/requests` memuat `erasure_deadline` yang sama.
- Checkout dengan quote sah selama hapus akun aktif dijawab 409 `account_deleting`.
- **L-21c.** Saat jendela kill switch, MIGRATION_ENABLED dinyalakan sementara lewat overlay:
  - `source_ref` `legacy-001`, nomor HP, UUID v1, dan hex berisi angka saja → 400 `invalid_source_ref`;
  - UUID v4 → 200.
- **Login (tahap a).** Verify tanpa `age_confirmed` dijawab 400 `age_confirmation_required`, dan challenge tidak hangus
  (attempts_left tetap 2).

## G18.3 Perbaikan harness (bukan produk)
- **`odoo_pos_smoke.py`.** Diubah ke desain baru: POS dibuat dengan redeem aktif sehingga metode Poin EFN tertaut,
  redeem/refund lewat pembayaran, dan metode EDC non-tunai. Ditambah 9 langkah `g18_*`.
  - Sync memakai `sync_with_retry`. Server HTTP Odoo mengulang otomatis pada `SerializationFailure` (SEC-127
    `touch_row` / cron outbox), tetapi `odoo shell` tidak.
- **`odoo_rpc_smoke.py`.** Ditambah probe kasir G18 (target id dikirim lewat pipe yang sama, tanpa PII).
- **File baru `etl_points_recon_smoke.py`.**
- **`bff_smoke.py`.**
  - Set kunci status DSAR ditambah `open_online_orders`/`erasure_deadline` (L-22, aditif).
  - Foto sintetis diberi token huruf acak. Satu run sempat `flagged/duplicate_image` karena pHash bertabrakan dengan
    foto run lama setelah ratusan run.
- **`online_smoke.py`.**
  - Konfirmasi hapus akun, status, dan checkout saat menghapus.
  - Cek tanpa `Idempotency-Key` dipindah ke member c (batas checkout 5/10 menit/member).
- **`run-e2e-smoke.sh`.** Tahap `p` baru, cek ETL `l` disesuaikan ke E-30, dan overlay kill switch juga menyalakan
  migrasi untuk uji `source_ref`.

## G18.4 Perubahan konfigurasi dev oleh QA
- `infra/compose/docker-compose.dev.yml` etl: `ETL_LOYALTY_RECON_NOT_BEFORE` kini
  `${ETL_LOYALTY_RECON_NOT_BEFORE:-2026-10-07T07:09:00Z}`.
  - Sebelumnya bernilai literal sehingga `.env` diabaikan. Akibatnya instruksi "set NOT_BEFORE setelah reset" tidak
    mungkin dijalankan, dan data uji BFF dari DB Odoo lama ikut direkonsiliasi.
  - Perlu ditinjau devsecops.

## G18.5 Temuan
| ID | Pemilik | Temuan |
|---|---|---|
| G18-QA-01 | lead → odoo-engineer + bff | Kunci idempotensi event online Odoo→BFF `online:<id efn.online.order>:<event>:<seq>` memakai id lokal DB Odoo. Setelah DB Odoo dibuat ulang/dipulihkan, id berulang, dan event pesanan baru dianggap `duplicate` lalu **diam-diam diabaikan**. Terlihat di dev: event `ready` pesanan S00001 baru di-skip, pelanggan tetap `confirmed`. Di produksi bisa terjadi bila Odoo dipulihkan dari backup lama sementara BFF tidak (retensi `processed_message` 14 hari). Usul: kunci memakai `platform_order_ref` (UUID BFF) atau BFF menolak duplikat yang `platform_order_ref`-nya berbeda. Di dev diatasi dengan menghapus `processed_message`/`online_order_event` sebelum reset. |
| G18-QA-02 | devsecops (runbook) | Reset DB Odoo dev wajib disertai reset data turunan: salinan katalog BFF, mart ClickHouse sumber Odoo + watermark-nya (tanpa reset, `fact_sales_line` order id 7 berisi baris dari 3 DB berbeda), dan event online (G18-QA-01). Belum ada di `dev-stack-boot.md`. |
| G18-QA-03 | CS (operasional dev) | Rekonsiliasi menandai order refund `ba24000b…` (Odoo id 54) dari run yang terputus: reverse `hold restore_value_mismatch` belum diputuskan supervisor, sehingga pembayaran Poin EFN −Rp5.000 di Odoo tidak punya pasangan restore di BFF. Perilaku benar (K-18-6). Selesaikan lewat `action_efn_restore_approve` atau `action_efn_hold_cancel` bila ingin hari itu bersih. |
| G18-QA-04 | — (catatan) | Saat transisi status health container api-gateway, router Traefik dilepas ±30–40 detik (404 tanpa origin). Harness menunggu 5×200 setelah recreate (sejak G17). |

**Bug produk baru di run ini: tidak ada.** Semua perilaku r11/re-review terverifikasi sesuai kontrak.

## G18.6 Data dev tertinggal
- DB Odoo `efn_odoo` baru (15 Okt) berisi data `SMOKE*` fiktif; order 54 dalam keadaan hold (G18-QA-03).
- Satu permintaan hapus akun terjadwal per run tahap `o` (member fiktif, batas 14 hari).
- Dump terenkripsi pra-G18 ada di scratchpad lokal.
- Folder kerja smoke `/tmp/tmp.*` dari banyak run masih ada di host. Isinya token JWT dan kode ambil data dev fiktif;
  sebaiknya dihapus manual.

## G18.7 Belum diverifikasi
- UI POS di browser untuk tombol bayar Poin EFN, refund, dan dialog usia (dicakup tour CI 27, bukan smoke).
- POS offline.
- Erasure paksa hari 3/10/14 (eskalasi `defer_escalation`) tanpa manipulasi waktu.
- Rotasi API key otomatis via systemd/journald di host nyata (diuji devsecops dengan docker).
- Housekeeping Metabase harian dan TTL kolom ClickHouse 5 th/90 h pada data nyata.
- Jalur POS ↔ SO online `efn_online_pos_sale` lewat UI.
- Akrual jurnal liabilitas poin (Q-F18-3).

## G18.8 Run konfirmasi sebelum PR (15 Okt 2026, HEAD `bea9be4`)

Perubahan sejak run 305/0:
- **G18-QA-01, BFF** (`bf5c5f9`): dedup event memakai `odoo-ev:<platform_order_ref>:<event>:<seq>[:<event_id>]` dan sidik
  isi. Kunci sama dengan isi berbeda menghasilkan `409 event_conflict`. Kunci lama yang terpakai ulang diproses dengan
  anomali `odoo_event_key_reused`.
- **G18-QA-01, Odoo** (`bea9be4`): kunci `online:<platform_order_ref>:<event>:<seq>`, `event_id` uuid persisten, dan seq
  berbasis waktu.
- **Epoch rantai audit** di ETL dan ops (`a769202`, `b817aa8`).
- **Runbook reset dev dan DR** (`fd6d862`).

**1. Suite Odoo pola CI** (image dibangun dari HEAD, PostgreSQL 16.6 terpisah)

| Bagian | Hasil |
|---|---|
| Unit | 16 modul `EFN_TEST_MODULES` (termasuk `efn_online_pos_sale` + `pos_sale`, `efn_pos_refund_origin`) + `sale_stock`, tag `efn`: **501 test, 0 failed, 0 error, 0 baris ERROR** |
| `check-odoo-deps` | 17 modul, 0 terlarang |
| `efn_setup_security` (install-after) | exit 0 |
| Tour | 6 modul `EFN_TOUR_MODULES`, tag `efn_tour`: **29 test, 0 failed, 0 error, 0 ERROR, 0 skip browser** |

**2. Stack dev**
- Image odoo, api-gateway, dan etl dibangun ulang.
- `-u efn_online_order` (19.0.1.1.0), `efn_pos_loyalty_ledger`, `efn_pos_otp_gate` selesai dengan exit 0 dan 0 ERROR.
- Migrasi 08 (versi diperluas) exit 0; grant ETL diulang.
- ETL berjalan di **epoch audit 1** (`job=audit_copy … epoch=1`, status ok, tanpa `divergence_blocked`).
- Docker Desktop sempat mati di tengah run pertama. Stack dinyalakan ulang dan di-recreate penuh (sidecar `-tls` bersama
  service utamanya). Network uji lama `efn-tourci-*` dibersihkan.

**3. Smoke `online_smoke.py events` dengan format kunci baru**

| Kasus | Hasil |
|---|---|
| Tanpa tanda tangan, secret salah, di luar window ±600 s, body diubah, tanpa service key | 401 |
| seq lama | `ignored_stale` |
| Duplikat (event_id sama) | `duplicate` |
| `checkout_ref` asing | 404 |
| PT beda | 409 `company_mismatch` |
| **Baru:** kunci sama (ref + event + seq + event_id) dengan isi berbeda | **409 `event_conflict`** |
| **Baru:** kunci format lama (id lokal) milik bagian lain yang sudah tercatat BFF (simulasi restore) | diproses (200) + anomali **`odoo_event_key_reused`** di log BFF |

**4. Smoke penuh** `a b c d f e g l p o` (run 18 Okt 2026): **PASS=307, FAIL=0**.

| Tahap | PASS/FAIL |
|---|---|
| a | 22/0 |
| b | 17/0 |
| c | 15/0 |
| d | 7/0 |
| f | 6/0 |
| e | 24/0 |
| g | 19/0 |
| l | 43/0 |
| p | 31/0 |
| o | 123/0 |

Tahap `o` bertambah 2 cek (`event_same_key_other_payload_409`, `event_legacy_key_reused_processed_with_anomaly`). Rekonsiliasi harian Poin EFN (mode payment): 0 selisih untuk order run ini.

Docker Desktop dua kali berhenti sendiri di tengah run pada 15 Okt. Run ke-6 terhenti di tahap `o` dengan **296 PASS / 0 FAIL** sampai titik itu (termasuk ketujuh cek event baru). Run ini lalu diulang penuh setelah stack di-recreate.

**Bug produk baru: tidak ada.** Temuan G18-QA-01 (kunci event) dan G18-QA-02 (runbook reset) **ditutup**: perbaikannya terverifikasi di runtime, dan runbook reset/DR sudah ada (`fd6d862`).
