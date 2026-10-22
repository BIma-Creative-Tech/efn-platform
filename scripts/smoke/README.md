# scripts/smoke — smoke E2E stack dev (TLS/mTLS aktif)

Tujuan: membuktikan alur bisnis lintas service berjalan di stack dev nyata (bukan mock): login OTP Super-App,
consent + Price Hunter, POS OTP gate dari Odoo, QRIS POS mock, DSAR hapus akun lintas Platform → Odoo → ClickHouse,
ETL, dan regresi negatif enkripsi. Hasil gelombang 8: `docs/testing/E2E_SMOKE_REPORT.md`.

- ADR terkait: ADR-022 (enkripsi/mTLS), ADR-017 (OTP POS), ADR-008 (OCR), ADR-016/019 (payment, Proposed), ADR-020.
- Risiko dimitigasi: regresi integrasi kontrak `docs/04` yang tidak tertangkap test unit (semua test unit memakai mock).

| File | Dijalankan di | Isi |
|---|---|---|
| `run-e2e-smoke.sh` | host (Git Bash/Linux) | orkestrator skenario `a b c d f e g l p o` (default semua; `v` opsional), ringkasan PASS/FAIL, exit 1 bila gagal |
| `bff_smoke.py` | container `python:3.12` di network `efn_edge` (klien publik, lewat Traefik) | Super-App/BFF: ALTCHA PoW, OTP, JWT, consent, upload foto sintetis (Pillow), DSAR |
| `odoo_pos_smoke.py` | `odoo shell` di container `efn-odoo-1` | setup POS smoke (company PB; G20 dulu FNB), `efn_otp_request/verify`, `sync_from_ui`, `efn_gateway_*`, cron erasure |

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

## Skenario `o` — Penjualan online G17 (docs/04 §10, ADR-034/ADR-019 Proposed)
| File baru | Dijalankan di | Isi |
|---|---|---|
| `online_smoke.py` | container `python:3.12` (efn_edge → Traefik `api.efn.local`/`app.efn.local`; subperintah `events` di efn_internal) | katalog, cart, quote, checkout (Idempotency-Key L-8), simulate bayar, status, kode ambil, anti-enumeration, DSAR L-4, kill switch, alur Next.js, event Odoo→BFF HMAC |
| `odoo_online_smoke.py` | `odoo shell` di `efn-odoo-1` | `setup` kanal GL dev (G20; data fiktif `SMOKE`, idempoten), `stock`, `order`, `ready`/`handover` sebagai user staf non-admin, `outbox`, `apikey` |

Prasyarat (sekali per volume dev): `efn_online_order efn_setup_online` terpasang, SQL `07-platform-online.sql` +
`09-platform-g20.sql`, API key nyata user teknis di `secrets/odoo_api_key_online_gl.txt` (runbook `odoo-online-api-key.md`
§1; langkah `setup` membuat user `efn_online_gl` lebih dulu), lalu `up -d --force-recreate api-gateway`.
G20 (docs/04 §12.1): kanal/company smoke = `GL` (kode PT; dulu `RTL`), company pembanding event silang = `PB` (dulu `FNB`).
Semua skrip smoke (`run-e2e-smoke.sh`, `online_smoke.py`, `odoo_online_smoke.py`, `odoo_pos_smoke.py`) sudah memakai kode
baru (kanal `GL`, user `efn_online_gl`, `efn_brand_code` `PB`).
- Gateway + key nyata: 10 path `/json/2` → jawaban aplikasi Odoo (200 / `efn_online:<kode>` 422), bukan 401/403/deny.
- Alur utama: quote → `accept_total` salah 409 → tanpa key 400 → checkout 201 → klik ganda = order sama → body beda 409
  → bayar (webhook mock HMAC) → `confirmed` (SO + faktur) → `ready` (aksi staf) → serah terima kode ambil → `completed`;
  kode ambil terenkripsi di BFF, hash KDF di Odoo, keduanya dihapus setelah serah terima.
- Refund parsial CLI `online-refund` (L-3) → `settled` + nota kredit; refund > sisa ditolak; audit `online_admin_audit`.
- Batas bayar dipercepat lewat DB dev (`UPDATE platform.online_order SET expires_at = now() - 1 menit`) → `expired`, SO
  batal; bayar terlambat → refund `late_payment`.
- Stok dihabiskan antara checkout dan bayar → `stock_short` → batal + refund porsi penuh.
- Kode ambil salah 5× → Odoo `review` (+ status bagian BFF, lihat E2E_SMOKE_REPORT G17 temuan terbuka).
- Event Odoo→BFF: tanpa tanda tangan / secret salah / di luar window ±300 s / body diubah / tanpa service key → 401;
  seq lama → `ignored_stale`; duplikat → `duplicate`; checkout_ref asing 404; company beda 409.
- PDP: kode ambil & HP tidak ada di log BFF/Odoo/odoo-rpc-gateway/Traefik, Loki, maupun pg_dump tabel online Odoo.
- Kill switch: overlay sementara `ONLINE_ENABLED=false` → recreate api-gateway → cart/quote/order 503, katalog & `/v1/me`
  tetap; recreate kembali. Skenario ini me-recreate api-gateway dua kali (±3 menit), karena itu `o` dijalankan terakhir.
- G17 r10/L-19 (10 Okt): skenario 4b — picking di luar wizard & context `efn_online_internal` palsu ditolak; batal SO
  oleh manajer `smoke_online_mgr` → refund `approval_required` → CLI approve (peran `cs` ditolak, approver lain →
  settled); refund CS (`approval_required`, pembuat/peran cs ditolak), settle manual SEC-112; stok non-katalog 404;
  label pajak `PPN 11%`. Identitas/peran CLI disimulasikan lewat env `EFN_CS_OPERATOR`/`EFN_CS_ROLE` (dev).

## Skenario `q` — G20 bayar di kasir (docs/04 §12.2, L-50..L-58) — ditulis QA, BELUM dijalankan

Dijalankan setelah `o` di stack yang sama (memakai API key kanal GL + gateway; `q` menjalankan ulang `setup` yang idempoten).
Langkah baru: `online_smoke.py` `counter_channel|checkout_counter|counter_view|counter_cancel`; `odoo_online_smoke.py`
`counter_setup|counter_lookup|counter_pay|counter_refund` (kasir `smoke_counter_cashier` = POS user non-admin, POS config
di gudang outlet SMKOL, sesi dibuka). Alur: katalog mengiklankan `counter` → checkout `counter` (metode asing 400, replay =
kode sama; `payment` PERSIS `{method,counter_code,expires_at}`) → kode bayar terenkripsi di BFF / hanya digest di Odoo →
lookup POS salah = `not_found`, benar = persis `{order_label, amount_payable}` → sync order POS pelunasan (tunai) →
`confirmed` + faktur lunas + pembayaran di jurnal kliring, re-sync idempoten → BFF `fulfilling` & kode bayar dihapus →
kode ambil ≠ kode bayar → serah terima → `completed`. Refund L-58: CLI `online-refund --full` + approve → `manual_required`
(`counter_refund_via_pos`, belum settled) → refund order POS → event `refund_settled` → BFF `settled` (`pos:<uuid>`).
Bayar setelah batal (L-57): lookup → pelanggan batal → kasir tetap bayar → Odoo `review`
(`counter_paid_after_cancel`, tanpa faktur) → BFF bagian `review`, order DB tetap `cancelled`, pelanggan melihat `review`.
Sebelum `q` pertama: Odoo modul `efn_online_pos_counter` terpasang, `ONLINE_PAYMENT_METHODS` berisi `counter` (default dev).

## Skenario `r` — G21 kebijakan poin per PT + akrual harian (docs/04 §13.1, L-61, L-66) — ditulis QA, BELUM dijalankan

Opsional (tidak di default): `bash scripts/smoke/run-e2e-smoke.sh r`. Skrip Odoo baru `odoo_loyalty_accounting_smoke.py`
(`policy|accounts|accrual`). Prasyarat: `efn_loyalty_accounting` terpasang di `efn_odoo`; secret `loyalty_policy_signing_key`
ada di `secrets/` (gen-secrets) dan ter-mount ke odoo + api-gateway; BFF membawa migrasi `10-platform-g21.sql`; kebijakan
env BFF default (plafon `LOYALTY_POLICY_CEILING_*` = env lama). Alur: user fiktif pembuat (Loyalty Manager) + penyetuju
(Accounting Manager) PT GL → draft nilai acak (`redeem_min_points` 51..99, `redeem_max_pct` 41..49) → pembuat tidak bisa
menyetujui (empat mata) → penyetuju menyetujui → outbox mengirim `PUT /v1/pos/loyalty/policies/GL/<v>` bertanda tangan →
`published` + baris append-only di `platform.loyalty_policy` (UPDATE ditolak trigger) → `pos.config._efn_loyalty_params()`
(jalur POS, `GET params?company_code=GL`) mengembalikan nilai baru. Akrual: akun dev PT GL (`21990091`, `SMKLY1..3`) → cron
`_efn_run_all()` dijalankan manual dua kali → tidak ada akrual berstatus `error`, baris `EFN_LOYALTY_RECON` tertangkap dan
berformat 5 kunci, jumlah jurnal `EFN-LOY-*` tidak bertambah pada run kedua. Keterbatasan: shell `odoo shell` tidak menulis
ke log container, jadi baris RECON tidak sampai ke Loki lewat skenario ini (periksa Loki setelah cron 02:00 nyata:
`{stream="efn_loyalty", loyalty_event="liability_recon"}`); jumlah `mismatch` hanya informasi (jurnal POS dev sebelum jendela
lookback tidak ikut diakrual).

`l` langkah 8b' (G20 E-41, `odoo_pos_smoke.py ret_approval`): retur tanpa struk dengan PIN Store Manager — tanpa persetujuan
ditolak, kasir bukan approver, PIN salah, PIN benar sekali pakai (token tidak keluar), nominal melebihi, 5 salah = terkunci.
`setup` POS menyetel mode `cash` agar langkah SEC-59 (`loy_refund_noreceipt`) tetap menguji loyalty, bukan persetujuan.

## Skenario `p` — G18 POS (docs/04 §9.9 K-18, L-23..L-26; review r11/re-review)
Butuh DB Odoo baru (IDR `rounding = 1`) dengan `efn_pos_refund_origin` terpasang, migrasi 08, dan `.env` mode
`ETL_LOYALTY_POINTS_MODE=payment`. Langkah Odoo `g18_*` di `odoo_pos_smoke.py`; probe RPC kasir di `odoo_rpc_smoke.py`
(id target lewat pipe stdin yang sama, tanpa PII); rekonsiliasi harian hari ini lewat `etl_points_recon_smoke.py`
(dijalankan di container ETL, read-only, `now` disimulasikan besok 07:00 WIB; dinilai: order berpoin RUN INI).
- Rp1 + pajak bulat; redeem = pembayaran Poin EFN dgn pajak tetap; refund ke metode asal (EDC ditolak, tunai berlebih
  ditolak); retur tanpa struk hanya tunai; L-26 hold -> BFF 409 -> restore_approve; barang excluded; draft nominal
  poin salah dibuang; `is_change` palsu; `pos_enroll` usia 17+; probe kasir SEC-113/115/121/125/126.
- Reset DB Odoo dev: reset juga salinan katalog BFF, mart ClickHouse sumber Odoo + watermark, dan event online lama
  (E2E_SMOKE_REPORT G18-QA-01/02), lalu set `ETL_LOYALTY_RECON_NOT_BEFORE` di `.env`.

## Skenario G23 — reservasi meja + slot pre-order (docs/04 §15) — DIJALANKAN 22 Okt 2026 di stack dev

| File | Jalan di | Isi |
|---|---|---|
| `booking_smoke.py` | container `python:3.12` (efn_edge → Traefik `api.efn.local`, CA edge dev) | subperintah `login public create limit status cancel list`: gerai, ketersediaan, batas tamu, buat (Idempotency-Key, idempoten, konflik isi, field asing, HP di catatan, tanpa login), batas aktif per member, status setelah konfirmasi staf, batal + idempoten, daftar |
| `booking_slot_smoke.py` | sama (butuh state login `booking_smoke.py`) | cart `pickup_slot` → quote → checkout → detail pesanan `pickup_slot`, kapasitas slot berkurang (cache 30 s), slot di luar jam → issue `slot_unavailable`, slot salah bentuk 400, tanpa slot = quote lama |
| `odoo_booking_staff_smoke.py` | `odoo shell` di `efn-odoo-1` (env `BK_OP=confirm\|seat\|done\|cancel\|noop`, `BK_REF=<booking_id BFF>`) | aksi staf sebagai manajer POS PB dev (`efn_dev_pos_mgr_pb`) → outbox → kirim event HMAC ke BFF; cetak state/seq/outbox |

Prasyarat dev: migrasi `12-platform-g23.sql`, modul `efn_table_booking` + `efn_online_slot` terpasang, pengaturan
reservasi + slot outlet `RESTO-01` (PB), user teknis `efn_booking_pb` + key di `secrets/odoo_api_key_booking_pb.txt`
([odoo-booking-api-key.md](../../docs/runbooks/odoo-booking-api-key.md)), `.env` `BOOKING_ENABLED=true`,
`BOOKING_COMPANIES=PB`, `ONLINE_SLOTS_ENABLED=true`, compose + `-f docker-compose.online-booking-pb.yml`.

Hasil 22 Okt 2026: reservasi 22/22 cek lulus (termasuk event `confirmed` Odoo → BFF `done` pada percobaan pertama),
slot 9/9 setelah perbaikan format `pickup_slot` di detail pesanan (`...T05:00:00Z`, tanpa milidetik).
