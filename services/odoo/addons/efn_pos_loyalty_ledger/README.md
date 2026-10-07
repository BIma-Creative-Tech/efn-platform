# efn_pos_loyalty_ledger

Poin EFN di POS Odoo CE 19: **earn** dari penjualan, **tukar poin** (wajib OTP pelanggan) di layar pembayaran, dan **clawback** saat retur. Saldo dan ledger tetap tunggal di BFF (`platform.loyalty_ledger`); Odoo **tidak** menyimpan saldo.

- **Fit-gap:** F-39 / G13-1 / E-22 (`docs/08 §6.3`), perluasan E-23 di `efn_pos_otp_gate`, filter audit E-24.
- **Kontrak:** `docs/04 §9` v4.0 + §9.6 (K-1..K-8) + §9.7 (v4.2).
- **ADR:** ADR-017 (OTP offline, opsi a: fitur poin nonaktif saat offline — *Proposed*), ADR-020 (ledger tunggal BFF — *Proposed*), ADR-029, ADR-031 (tanpa dependency vendor), ADR-022 (TLS).
- **Risiko dimitigasi:** R-T02 (fraud promo/poin dari kasir: baris poin divalidasi server terhadap reservasi ber-OTP), R-B06 (saldo ganda/hilang: outbox transaksional + idempotency key), POS tidak berhenti saat BFF mati (semua panggilan ledger lewat outbox).
- **Depends:** `point_of_sale`, `pos_loyalty`, `mail`, `efn_core`, `efn_pos_otp_gate` (semua CE / efn_*). `pos_loyalty` hanya dipakai untuk menetralkan baris poin dari perhitungan program promo — **bukan** `loyalty.card`/`program_type='loyalty'` (D-05).

## Data & konfigurasi
- Produk **"Poin EFN"** (`efn_pos_loyalty_ledger.product_efn_points`): `default_code = EFN-POINTS` (konvensi ETL, env `ETL_POINTS_PRODUCT_CODES`), layanan, tanpa pajak default, tanpa company & kategori POS; dimuat ke POS lewat `pos.config._get_special_products()` (tidak tampil di grid).
- **Kategori tanpa poin (K-2):** field `product.category.efn_loyalty_earn_excluded` (Boolean, berlaku untuk subkategori). Field ini juga sumber ETL untuk memisahkan penjualan tanpa poin.
- **Per POS:** `pos.config.efn_loyalty_earn_enabled`, `efn_loyalty_redeem_enabled` (default mati; *Settings → Point of Sale → Pricing → Poin EFN*). Redeem ditolak untuk kiosk self-order (constraint).
- **Parameter (`ir.config_parameter`):** `efn.pos.loyalty.outbox_max_hours` (48), `efn.pos.loyalty.reservation_retention_days` (400, PLACEHOLDER Finance), `efn.pos.loyalty.balance_rate_per_minute` (20). Timeout HTTP memakai parameter bersama `efn.pos.otp_timeout_seconds` (klien `efn_core`). Nilai poin, minimum & maks % **tidak disalin** — diambil dari `GET /v1/pos/loyalty/params` (cache 5 menit per worker).
- Company wajib punya *Kode Company EFN* (`efn_brand_code`).

## Alur
1. **Cek Poin** (layar pembayaran, online, pelanggan dipilih): RPC `pos.config.efn_loyalty_balance(partner_id)` → `POST /v1/pos/loyalty/balance {member_ref, pos_config_id, company_code}` → hanya saldo (K-8). Rate limit per sesi (in-process per worker) + rate limit BFF.
2. **Tukar Poin EFN** (layar pembayaran = langkah terakhir; online; bukan refund): saldo → popup jumlah poin (menampilkan nilai Rp, batas min/maks dari params) → OTP `purpose="pos_redeem"` (`efn_otp_flow.js`, nomor dari `res.partner`) → RPC `efn_loyalty_redeem_reserve(order_uuid, partner_id, points, amount_total)`: wajib challenge `pos_redeem` terverifikasi untuk (config, uuid, partner) → `POST /v1/pos/loyalty/redeem/reserve` → reservasi lokal `efn.pos.loyalty.reservation`, challenge `consumed` (K-4). Browser hanya menerima id lokal, poin, nilai.
3. Frontend memasang baris "Poin EFN" per kelompok pajak (proporsional atas baris non-poin, `qty=1`, `price_type: "automatic"`, total incl. pajak = −`value_idr`, selisih pembulatan ke kelompok terbesar).
4. **Batal:** kembali ke layar produk, ganti pelanggan, atau hapus order saat ada tukar poin → dialog "Batalkan tukar poin?" → `efn_loyalty_redeem_release` (`POST …/release`; gagal → antre outbox) + hapus baris.
5. **Offline:** tombol disembunyikan; validasi order berbaris poin diblokir ("Tunggu online" / "Hapus poin").
6. **Sync server** (`pos.order._process_saved_order`, sebelum langkah standar & consume token promo): baris poin dideteksi dari **produk** (SEC-12). Paid tanpa reservasi aktif → `UserError` (rollback). Nilai |Σ incl| ≠ `value_idr` (toleransi Rp1 × jumlah baris, dihitung ulang server dari harga & pajak dan dari nilai kiriman) atau partner berbeda → reservasi & order `review`, tanpa commit. Cocok → outbox `redeem_commit`. Reservasi aktif tanpa baris poin → outbox `redeem_release`. Draft: baris poin tanpa reservasi dihapus.
7. **Earn:** setiap order paid non-refund ber-partner pada config `earn_enabled` → outbox `earn` (§9.7: termasuk eligible 0). Partner tanpa HP Indonesia/dianonimkan → `skipped`.
8. **Refund:** order asal yang ikut loyalty → outbox `reverse` (`pos_reverse:<uuid refund>`) dengan `refund_ratio` kumulatif ≤ 1. Bila order asal memakai poin, layar refund otomatis menambah baris penyesuaian "Poin EFN" (nilai positif = round(rasio × nilai poin asal), dihitung server lewat `efn_loyalty_refund_preview`); server memvalidasi, selisih → `review`. Baris poin asal tidak bisa dipilih manual di layar refund; baris poin negatif pada refund → `UserError`.
9. **Pembatalan draft** (`action_pos_order_cancel`, `remove_from_ui`) dengan reservasi aktif → outbox `redeem_release`.

### `eligible_amount` (K-1)
Σ subtotal **tanpa pajak** baris barang setelah diskon baris & reward promo (baris reward ikut mengurangi), tidak termasuk produk "Poin EFN" dan kategori `efn_loyalty_earn_excluded`, **dikalikan faktor uang** = 1 − (nilai poin tanpa pajak / nilai barang tanpa pajak) — "bagian yang dibayar poin tidak menghasilkan poin". Dibulatkan ke bawah ke Rupiah. Satu fungsi `pos.order._efn_loyalty_eligible_amount()` (mudah diubah bila Finance/Q-13 memutuskan lain).
`refund_ratio` = nilai barang eligible yang direfund / nilai barang eligible order asal (faktor uang sama di kedua sisi); `refunded_eligible_amount` = barang eligible refund × faktor uang order asal. Bila eligible asal 0 → rasio nilai barang incl. pajak.

### Outbox (`efn.pos.loyalty.outbox`)
Dibuat dalam transaksi sync; `idempotency_key` unik (= kunci kontrak BFF). **Payload tidak disimpan** (nomor HP dibaca dari partner saat kirim). Cron *EFN Loyalty: kirim outbox poin POS ke BFF* tiap 1 menit + `_trigger()` setelah sync; baris dikunci `FOR UPDATE SKIP LOCKED`, commit per record (`ir.cron._commit_progress`). Retry (backoff 1 m → maks 1 j) hanya untuk error jaringan/5xx, 429, `409 earn_pending`; 4xx lain final → `failed` + order `review` + aktivitas To-Do ke POS manager. `404` pada release = selesai. > `outbox_max_hours` → `failed`. `reverse` menunggu `earn`/`redeem_commit` order asal selesai. Cron harian: reservasi `reserved` > 24 jam → `expired`; outbox `pending` > 24 jam → aktivitas.

## Keamanan & data
- ACL: POS user **tanpa akses** langsung ke reservasi/outbox (RPC `sudo` terkontrol); POS manager & akuntan **baca**; `bff_reservation_id` hanya `base.group_system`. Record rule multi-company untuk kedua model.
- Log & tampilan hanya nomor ter-mask (`62812****890`); outbox hanya kode error.
- Menu *Point of Sale → Orders → Review Poin / Reservasi Poin EFN / Outbox Poin EFN*. Filter audit: *Baris negatif non-promo (… tanpa Poin EFN)* (E-24, menggantikan filter `efn_setup_pos` yang dinonaktifkan `post_init_hook`; diaktifkan lagi saat uninstall) dan *Order dengan tukar Poin EFN*.
- Struk: poin ditukar dicetak; poin diperoleh tidak (dihitung server setelah sync) — teks "Poin EFN masuk setelah transaksi tersinkron".

## Uji manual
1. Aktifkan earn & redeem di POS, BFF dev berjalan (`efn.bff_url`, CA internal), member ber-HP +62.
2. Order → pelanggan → Bayar → *Cek Poin* → *Tukar Poin EFN* → 100 → OTP WhatsApp → baris "Poin EFN" → bayar → menu *Outbox Poin EFN*: `earn` & `redeem_commit` `done`.
3. Ulangi lalu tekan *Back* → dialog → reservasi `released`.
4. DevTools offline → tombol hilang; redeem lalu offline → validasi diblok.
5. Refund 1 dari 2 barang order berpoin → baris penyesuaian otomatis → `reverse` dengan rasio 0,5.
Langkah smoke `loy_*` di `scripts/smoke/odoo_pos_smoke.py` (belum dirangkai di `run-e2e-smoke.sh`).

## Test otomatis
`tests/test_loyalty_ledger.py` (TestPoSCommon, tag `efn`, BFF di-mock) dan `tests/test_loyalty_tour.py` (HttpCase + 4 tour `static/tests/tours/efn_loyalty_tour.js`, tag `efn_tour`: sukses, OTP salah, batal → release, offline diblok).

## Referensi
Tidak ada kode vendor (OCA/Cybrosys) yang dipelajari atau dipakai. Pola internal: `efn_pos_otp_gate` (gate server, deteksi baris dari produk) dan `efn_pos_payment_gateway` (cermin status BFF, rekonsiliasi). Ide pembagian diskon per kelompok pajak dari core `pos_loyalty` (`_getDiscountableOnOrder`, LGPL-3, ditulis ulang).

## Batasan / belum terverifikasi
- Perlakuan PPN penukaran poin (potongan harga vs alat bayar) menunggu konsultan pajak (P-8): implementasi memperlakukan poin sebagai potongan harga per kelompok pajak.
- Rate limit cek saldo di Odoo bersifat per worker (in-process); batas efektif ada di BFF.
- Promo `pos_loyalty` yang dihitung ulang setelah baris poin terpasang memakai `priceIncl` order (termasuk poin) untuk `totalFactor`; alur UI mencegahnya (tukar poin langkah terakhir, kembali = batal), tetapi belum diuji dengan promo persentase + poin pada order yang sama.
- Refund berpoin saat offline: penyesuaian tidak ditambahkan, order ditandai `review` di server.
- Integrasi dengan BFF nyata (stack dev) belum dijalankan; kontrak diikuti dari `docs/04 §9.7` dan DTO BFF.
