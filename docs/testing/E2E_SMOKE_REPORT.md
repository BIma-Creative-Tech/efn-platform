# Laporan Smoke E2E — stack dev dengan TLS/mTLS aktif (Gelombang 8 + regresi Gelombang 10 & 11, 6 Okt 2026)

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
