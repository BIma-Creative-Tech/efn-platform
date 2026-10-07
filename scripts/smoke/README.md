# scripts/smoke — smoke E2E stack dev (TLS/mTLS aktif)

Tujuan: membuktikan alur bisnis lintas service berjalan di stack dev nyata (bukan mock): login OTP Super-App,
consent + Price Hunter, POS OTP gate dari Odoo, QRIS POS mock, DSAR hapus akun lintas Platform → Odoo → ClickHouse,
ETL, dan regresi negatif enkripsi. Hasil gelombang 8: `docs/testing/E2E_SMOKE_REPORT.md`.

- ADR terkait: ADR-022 (enkripsi/mTLS), ADR-017 (OTP POS), ADR-008 (OCR), ADR-016/019 (payment, Proposed), ADR-020.
- Risiko dimitigasi: regresi integrasi kontrak `docs/04` yang tidak tertangkap test unit (semua test unit memakai mock).

| File | Dijalankan di | Isi |
|---|---|---|
| `run-e2e-smoke.sh` | host (Git Bash/Linux) | orkestrator skenario `a b c d f e g l` (default semua; `v` opsional), ringkasan PASS/FAIL, exit 1 bila gagal |
| `bff_smoke.py` | container `python:3.12` di network `efn_edge` (klien publik, lewat Traefik) | Super-App/BFF: ALTCHA PoW, OTP, JWT, consent, upload foto sintetis (Pillow), DSAR |
| `odoo_pos_smoke.py` | `odoo shell` di container `efn-odoo-1` | setup POS smoke (company FNB), `efn_otp_request/verify`, `sync_from_ui`, `efn_gateway_*`, cron erasure |

```bash
bash scripts/smoke/run-e2e-smoke.sh          # semua skenario (~15 menit; DSAR menunggu 1-2 siklus ETL 120 s)
bash scripts/smoke/run-e2e-smoke.sh a g      # sebagian
```

Prasyarat: stack dev `core` + `data` + `ai` jalan dan modul `efn_*` terpasang (`docs/runbooks/dev-stack-boot.md`).

## Batas & pelonggaran khusus dev (semua terikat APP_ENV=dev)
- OTP dibaca dari log `ConsoleWhatsAppSender` BFF — hanya dicetak saat `APP_ENV=dev`/`WHATSAPP_ENABLED=false`.
- Masa tenggang DSAR dilewati dengan `UPDATE platform.dsar_request SET scheduled_at = now()` lewat superuser
  PostgreSQL (socket lokal container). Tidak ada perubahan kode/env untuk ini.
- `POST /v1/pos/payments/:id/simulate` (404 di produksi) dipanggil lewat `efn.bff.client` Odoo (service key dari
  `/run/secrets`, tidak pernah keluar dari container).
- `odoo_pos_smoke.py` menolak jalan bila `APP_ENV` bukan `dev`; membuat data `SMOKE *` (POS config, produk, metode
  bayar, program promo ber-OTP, partner fiktif) di DB dev.
- Tidak ada secret di file mana pun: password dibaca di dalam container. Nomor HP = nomor fiktif acak per run.

## Cek tambahan gelombang 10
- `b`: upload foto lewat `s3-gateway` (PUT BFF + GET ocr-worker untuk id report run ini di log HAProxy); access key
  lama bernama tetap (`efn-bff`, `efn-ocr`, `pgbackup`) → `InvalidAccessKeyId`, access key acak → terautentikasi
  (`AccessDenied` untuk ListBucket = least privilege). `docker logs --since` tidak andal untuk container
  s3-gateway di Docker Desktop, jadi skrip memakai `--tail`.
- `c`: `efn.audit.log` mencatat `list_price` produk dan HP partner ter-mask (`is_masked`, `****`, digit tengah tidak
  ada); `POST /v1/pos/loyalty/balance` dari Odoo (service key) 200 = saldo Super-App, GET lama 404, body salah 400.
- `e`: tombstone acak (≠ `sha256("erased:"||request_id)`, ≠ member_hash, sama di report & ledger, tidak ada di
  `dsar_request`); `dsar_request.member_hash` NULL setelah `completed`; log audit partner subjek terhapus + log
  `purge` tanpa id; penanda `efn.privacy.erasure.pending` kosong; receipt DSAR via BFF dan via Next.js
  `/api/dsar/requests/:id` (cookie `efn_dsar_receipt`): 200 tanpa PII, receipt salah/hilang 404, lintas situs 403.

## Skenario `l` — Poin EFN di POS (gelombang 13, docs/04 §9)
Butuh `efn_pos_loyalty_ledger` terpasang dan SQL `05-platform-loyalty-pos.sql` di DB platform. Tiga partner fiktif
baru (L/M/N) per run; nilai poin memakai default BFF dev (1 poin/Rp10.000, Rp100/poin, min 50, maks 50 %).
- Earn order 20 × Rp50.000 → 100 poin (outbox Odoo → `POST /v1/pos/loyalty/earn`, `platform.loyalty_pos_earn` + ledger).
- Saldo dari POS (`efn_loyalty_balance`) = 100.
- Redeem: OTP `pos_redeem` (kode dari log console BFF) → reserve 60 poin (Rp6.000) → order dengan baris Poin EFN →
  sync → outbox commit; saldo 100 − 60 + 19 (earn atas Rp194.000) = 59; reservasi BFF `committed/60/6000`.
- Batal: RPC release (dialog kasir) dan order draft berpoin → `action_pos_order_cancel` → outbox release; BFF `released`.
- Lintas purpose: OTP promo → reserve ditolak Odoo (`otp_required`) dan token promo langsung ke BFF reserve → 409
  `token_invalid`; OTP redeem → order promo ditolak gate (`UserError`) dan token redeem ke consume promo → 409.
- Refund 2/4 unit order berpoin (penyesuaian Rp3.000 dari `efn_loyalty_refund_preview`) → reverse rasio 0,5:
  restore 30, clawback 10 → saldo 79.
- `docker stop` api-gateway saat sync → order tetap `paid`, earn `pending` (`bff_unreachable`); setelah start, outbox
  mengirim ulang (backoff) → saldo 89. Skenario ini **mematikan api-gateway ±1 menit**.
- PDP: 0 kemunculan digit HP partner L/M/N di `platform.loyalty_*`, `efn_pos_loyalty_*`, `efn_pos_otp_challenge`,
  log BFF dan log Odoo; ETL: baris `line_kind=points` (−6000) dan baris refund masuk `fact_sales_line`.
- Batas OTP per member 3/15 menit: partner L memakai tepat 3 OTP redeem.

## Cek tambahan gelombang 13c (SEC-58/59/61/65)
| File baru | Dijalankan di | Isi |
|---|---|---|
| `odoo_rpc_smoke.py` | container `python:3.12` (efn_edge → Traefik wg-https `erp.efn.local`, CA edge dev) | login nyata password + TOTP, `POST /web/dataset/call_kw` |
| `obs_smoke.py` | container `python:3.12` (efn_internal, sertifikat klien Grafana read-only) | query Loki `query_range` & Alertmanager `api/v2/alerts` (mTLS) |

- `l` (awal): langkah Odoo `rpc_users` membuat `smoke_rpc_internal` (base.group_user) dan `smoke_rpc_cashier`
  (+ POS user) dengan password & TOTP acak per run (file 0600 di container Odoo → stdin `odoo_rpc_smoke.py` → dihapus).
  `efn.bff.client._efn_post/_efn_get/post/get` lewat `call_kw` → semua ditolak (AccessError metode privat /
  metode tidak ada); kontrol positif `res.partner.search_count` membuktikan sesi valid.
- `l` (8b): retur tanpa struk (langkah `loy_refund_noreceipt`) → order `review`, tanpa outbox earn/reverse, ledger BFF
  tidak berubah.
- `l` (8c): partner O, order 401 × Rp50.000 = 2005 poin > batas harian 2000 → outbox earn `failed`
  (`earn_limit_exceeded`), order `review`, tanpa `loyalty_pos_earn`; baris `EFN_LOYALTY_ANOMALY` (tanpa ANSI, tanpa HP)
  di log BFF → Loki `{stream="efn_loyalty", kind="earn_limit_member_day"}` → alert `EfnLoyaltyAnomalyWarning` aktif di
  Alertmanager (±1–2 menit); heartbeat `EFN_LOYALTY_RECON` di Loki (dev: rekonsiliasi tiap 5 menit).
- `e`: bila container BFF dijalankan dengan `MIGRATION_ENABLED=true`, migrasi K-7 (member + saldo pembuka) subjek
  yang sedang DSAR delete → `409 member_deleting`; setelah erasure selesai → `409 member_erased`, member tidak hidup
  kembali, ≥ 3 baris anomali `migration_erased_member`. Tanpa flag itu cek dilewati (endpoint 404). Kunci migrasi dibaca
  di dalam container curl (`-H @file`), tidak di argv. Menyalakan sementara:
  `MIGRATION_ENABLED=true $DC --profile core up -d --no-deps --force-recreate api-gateway`, lalu ulangi tanpa variabel.

## Belum
- Tidak masuk CI (butuh stack penuh + ±15 menit). Kandidat job nightly pada runner self-hosted.
- Frontend POS (OWL) tidak disentuh: skrip memanggil metode server yang sama dengan frontend.
- Jalur VLM dengan model nyata: subperintah `report_vlm` tidak termasuk run default (butuh model `qwen2.5vl:3b`
  ±3,2 GB, lihat E2E_SMOKE_REPORT gelombang 10). `v` boleh dijalankan setelah `e` (`a b c d f e g v`): akun skenario
  `a` sudah dihapus DSAR, sehingga `v` login dengan nomor fiktif baru (gelombang 12).
