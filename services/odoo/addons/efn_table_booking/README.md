# efn_table_booking (W-35 / G23) — reservasi meja PB & Bacha Coffee

> **Ringkasan manajemen.** Modul ini memungkinkan pelanggan memesan meja di gerai Paris Baguette dan Bacha Coffee
> (kafe) dari Super-App, web brand, dan aplikasi. Ketersediaan dihitung dari **jam buka gerai** dan **meja nyata di
> POS restoran**, jadi tidak ada angka kapasitas yang diketik manual. Staf gerai melihat dan mengelola reservasi di
> Odoo (kalender, konfirmasi, datang, tidak datang). Data tamu minimal: hanya nama panggilan dan catatan pendek,
> tanpa nomor HP atau email. Fitur **mati di produksi** sampai diaktifkan (`BOOKING_ENABLED=false` di BFF).

- Versi `19.0.1.0.0`, LGPL-3. `depends`: `pos_restaurant`, `efn_core`, `efn_audit`, `efn_online_order`,
  `efn_pos_bff_order`. Tanpa modul pihak ketiga (ADR-031). CE 19 tidak punya `pos_appointment` (Enterprise,
  ADR-013), sehingga modul ini dibuat custom.
- Kontrak: `docs/04_SERVICE_CONTRACTS.md` §15.1–§15.3, §15.5, §15.8, §15.9 (L-85, L-86, L-90; pertanyaan terbuka
  Q-BK1..Q-BK7). Rencana: `docs/10_RENCANA_GELOMBANG_18_PLUS.md` §A.8 (W-35).
- ADR: ADR-013 (dibuka G23), ADR-029 (tanpa PII), ADR-031, ADR-034 (BFF + `/json/2` terfilter).

## Model

| Model | Isi | Multi-company |
| --- | --- | --- |
| `efn.booking.settings` | Satu record per `efn.online.outlet` (kanal F&B saja): POS restoran (`pos_config_id`, sumber meja), jam buka (`calendar_id`, zona waktu = zona kalender), `slot_minutes` 15/30/60, `duration_minutes` 30–360 (default 90), `lead_minutes` (60), `horizon_days` 1–60 (14), tamu `min_party`..`max_party` ≤ 50 (1..8), `confirm_mode` `staff`/`auto` (default `staff`), `hold_minutes` (15). | `company_id` related outlet (stored) + record rule |
| `efn.table.booking` | Reservasi: nomor `BKG/<tahun>/00001`, `platform_booking_ref` (UUID dari BFF, unik per PT), `start_at`/`end_at` (UTC), `party_size`, `table_id` (dipilih otomatis), `state`, `cancel_reason`, `guest_label` (≤ 40), `note` (≤ 140), `seq`. Kunci idempotensi & hash permintaan hanya terlihat `base.group_system`. | `company_id` + record rule |
| `efn.booking.event.outbox` | Antrean event status ke BFF (`confirmed`, `seated`, `done`, `cancelled`, `no_show`). | `company_id` + record rule |

Status: `requested` → `confirmed` → `seated` → `done`; `requested`/`confirmed` → `cancelled` (pelanggan, staf,
`expired`) atau `no_show`. Semua transisi menaikkan `seq` (BFF menolak event dengan `seq` lebih kecil).

## RPC untuk BFF (`/json/2/efn.table.booking/<metode>`, path gateway 16–19)

| # | Metode | Keterangan |
| --- | --- | --- |
| 16 | `efn_booking_availability(outlet_code, date, party_size)` | Slot satu hari lokal: `[{start_at, available}]` + `timezone`, `slot_minutes`, `duration_minutes`, `min_party`, `max_party`, `horizon_days`. |
| 17 | `efn_booking_create(outlet_code, booking)` | Kunci yang diizinkan: `idempotency_key`, `platform_booking_ref`, `start_at`, `party_size`, `guest_label`, `note`. Idempoten (kunci/ref sama + isi sama → hasil sama; isi beda → `idempotency_conflict`). Ketersediaan dihitung ulang di bawah kunci baris settings. |
| 18 | `efn_booking_cancel(outlet_code, platform_booking_ref, idempotency_key)` | Batal oleh pelanggan, hanya `requested`/`confirmed` yang belum mulai. Tanpa event (BFF sudah tahu). |
| 19 | `efn_booking_status(outlet_code, platform_booking_refs)` | Rekonsiliasi, maks 100 ref. Ref milik outlet lain tidak muncul. |

- **Pemanggil**: user teknis per PT F&B (`efn_booking_pb`, `efn_booking_bcf`) dengan grup
  `group_booking_bff` saja (api key 35 hari). `_efn_booking_check_caller` menolak (AccessError seragam) bila user
  punya grup lain, lebih dari satu company, atau outlet bukan milik company-nya. Context klien dibuang kecuali
  `lang` yang di-whitelist.
- **Galat bisnis** = `UserError("efn_booking:<kode>")`, kode: `invalid_input`, `not_found`, `slot_unavailable`,
  `outside_hours`, `party_size`, `too_late`, `too_far`, `idempotency_conflict`, `invalid_state`, `booking_disabled`.
  BFF memetakannya ke HTTP (docs/04 §15.3) tanpa detail internal.
- **Ketersediaan**: slot sejajar kelipatan `slot_minutes` sejak tengah malam lokal, seluruh durasi di dalam jam
  buka, ≥ `lead_minutes` dari sekarang. Slot `available` bila ada meja aktif di lantai POS dengan kursi ≥ jumlah
  tamu dan tanpa bentrok reservasi aktif (meja dengan kursi paling sedikit yang cukup dipilih lebih dulu).
- **Konkurensi** (SEC-127): create/cancel mengunci baris `efn_booking_settings` (`FOR UPDATE` + `touch_row`) sehingga
  dua reservasi paralel ke meja terakhir tidak bisa sama-sama lolos.

## Data tamu (L-86, ADR-029)
`guest_label` dan `note` disaring dengan aturan catatan SEC-168 (sama dengan BFF `sanitizeNote`): tanpa `@`, tanpa
deret ≥ 8 digit. Tidak ada partner/HP/email. Teks tamu tidak pernah di-log dan tidak dikirim di event.

## Event ke BFF (`POST /v1/pos/booking/events`)
Dibuat di transaksi yang sama dengan perubahan status. Payload tanpa PII: `schema_version`, `idempotency_key`
(`booking:<ref>:<event>:<seq>`), `seq`, `platform_booking_ref`, `company_code`, `outlet_code`, `event`, `state`,
`occurred_at`. Ditandatangani `X-EFN-Signature: t=<unix>,v1=<HMAC-SHA256>` dengan secret
`odoo_booking_event_hmac` dari file `EFN_BOOKING_EVENT_HMAC_FILE` (default `/run/secrets/odoo_booking_event_hmac`,
dibaca setiap kirim; tanpa secret = tidak dikirim, antrean menunggu). 2xx → `done`; 400/401/403/404/409/422 →
`failed` final + log alert `EFN_BOOKING_EVENT_FAILED`; 429/5xx/timeout → retry 1 menit s.d. 1 jam; umur > 48 jam →
`failed`. Urutan per reservasi dijaga.

## Cron
- `ir_cron_efn_booking_outbox` (tiap 1 menit): kirim antrean event.
- `ir_cron_efn_booking_expire`: `requested` yang belum dikonfirmasi saat jam mulai → `cancelled` (`expired`) + event.
  `no_show` selalu keputusan staf.

## Hak akses
| Grup | Settings | Reservasi | Outbox |
| --- | --- | --- | --- |
| `point_of_sale.group_pos_user` | baca | baca | – |
| `point_of_sale.group_pos_manager` | CRUD | baca + tombol status | baca |
| `base.group_system` | – | – | baca/tulis/hapus |
| `group_booking_bff` (user teknis) | – (hanya lewat RPC, sudo terkontrol) | – | – |

Tombol staf (`action_efn_confirm`, `action_efn_seat`, `action_efn_done`, `action_efn_no_show`, `action_efn_cancel`)
mewajibkan manajer POS dan company yang sama. `no_show` baru boleh setelah jam mulai + `hold_minutes`.

## Tampilan
Point of Sale → **Reservasi meja** (list, kalender harian per outlet, form dengan tombol status) dan Point of Sale →
Konfigurasi → **Pengaturan reservasi** (per outlet).

## Wiring (belum dikerjakan — wajib sebelum diaktifkan)
1. Secret `odoo_booking_event_hmac` (≥ 32 karakter) di Odoo **dan** BFF.
2. User teknis `efn_booking_pb` / `efn_booking_bcf` (grup `group_booking_bff` saja, satu company) + API key
   `odoo_api_key_booking_<pt>` untuk BFF.
3. `efn.booking.settings` per outlet (POS restoran + kalender jam buka), lalu `BOOKING_ENABLED=true` di BFF.

## Uji
`tests/test_table_booking.py` (tag `efn`, base `EfnOnlineCommon`): pemanggil, ketersediaan, bentrok meja, idempotensi,
batas waktu/tamu, filter PII, transisi staf, cron kedaluwarsa, outbox (tanda tangan, retry, final). Jalankan:
`odoo --test-tags /efn_table_booking` (G23: 249/249 lulus bersama `efn_core`, `efn_online_order`, `efn_online_slot`).
