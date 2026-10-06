# scripts/smoke — smoke E2E stack dev (TLS/mTLS aktif)

Tujuan: membuktikan alur bisnis lintas service berjalan di stack dev nyata (bukan mock): login OTP Super-App,
consent + Price Hunter, POS OTP gate dari Odoo, QRIS POS mock, DSAR hapus akun lintas Platform → Odoo → ClickHouse,
ETL, dan regresi negatif enkripsi. Hasil gelombang 8: `docs/testing/E2E_SMOKE_REPORT.md`.

- ADR terkait: ADR-022 (enkripsi/mTLS), ADR-017 (OTP POS), ADR-008 (OCR), ADR-016/019 (payment, Proposed), ADR-020.
- Risiko dimitigasi: regresi integrasi kontrak `docs/04` yang tidak tertangkap test unit (semua test unit memakai mock).

| File | Dijalankan di | Isi |
|---|---|---|
| `run-e2e-smoke.sh` | host (Git Bash/Linux) | orkestrator skenario `a b c d f e g` (default semua), ringkasan PASS/FAIL, exit 1 bila gagal |
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

## Belum
- Tidak masuk CI (butuh stack penuh + ±15 menit). Kandidat job nightly pada runner self-hosted.
- Frontend POS (OWL) tidak disentuh: skrip memanggil metode server yang sama dengan frontend.
- Jalur VLM dengan model nyata: subperintah `report_vlm` tidak termasuk run default (butuh model `qwen2.5vl:3b`
  ±3,2 GB, lihat E2E_SMOKE_REPORT gelombang 10).
