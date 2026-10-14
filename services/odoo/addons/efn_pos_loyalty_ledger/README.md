# efn_pos_loyalty_ledger

Poin EFN di POS Odoo CE 19: **earn** dari penjualan, **tukar poin** (wajib OTP pelanggan) di layar pembayaran, dan **clawback** saat retur. Saldo dan ledger tetap tunggal di BFF (`platform.loyalty_ledger`); Odoo **tidak** menyimpan saldo.

- **Fit-gap:** F-39 / G13-1 / E-22 (`docs/08 §6.3`), perluasan E-23 di `efn_pos_otp_gate`, filter audit E-24.
- **Kontrak:** `docs/04 §9` v4.0 + §9.6 (K-1..K-8) + §9.7 (v4.2).
- **ADR:** ADR-017 (OTP offline, opsi a: fitur poin nonaktif saat offline — *Proposed*), ADR-020 (ledger tunggal BFF — *Proposed*), ADR-029, ADR-031 (tanpa dependency vendor), ADR-022 (TLS).
- **Risiko dimitigasi:** R-T02 (fraud promo/poin dari kasir: baris poin divalidasi server terhadap reservasi ber-OTP), R-B06 (saldo ganda/hilang: outbox transaksional + idempotency key), POS tidak berhenti saat BFF mati (semua panggilan ledger lewat outbox).
- **Depends:** `point_of_sale`, `pos_loyalty`, `mail`, `efn_core`, `efn_pos_otp_gate` (semua CE / efn_*). `pos_loyalty` hanya dipakai untuk menetralkan baris poin dari perhitungan program promo — **bukan** `loyalty.card`/`program_type='loyalty'` (D-05).

## G18 — E-30: tukar poin = metode bayar (versi 19.0.2.0.0)
> **Ringkasan manajemen.** Penukaran Poin EFN di kasir kini menjadi **cara bayar** "Poin EFN" (seperti EDC),
> bukan diskon. Harga, DPP, dan PPN/PB1 tetap atas harga penuh (keputusan pemilik proyek P-8). Kasir tetap wajib
> OTP pelanggan; nominal poin tidak bisa diubah kasir dan diperiksa ulang server.

- **Kontrak:** `docs/04 §9.9` v6 K-18-1..K-18-6, L-23, L-24 (BFF). **Fit-gap:** `docs/08 §9.2` (E-30). ADR-020 (catatan
  10 Okt), ADR-017, ADR-031. Risiko: R-B06/R-T02 (fraud kasir), Q-F18-3/4 (Finance).
- **Metode bayar:** `pos.payment.method.efn_is_loyalty_points` + constraint (jurnal bank, bukan kas, tanpa
  terminal/QR, tanpa split, satu aktif per company). `pos.config.efn_loyalty_points_method` (id, dimuat ke POS).
  **Data per PT** (jurnal bank "Poin EFN" tanpa rekening, `outstanding_account_id` = akun liabilitas/kliring poin
  PLACEHOLDER, metode di `payment_method_ids` config) diisi `efn_setup_pos` (functional). Tanpa metode di config,
  tombol tukar disembunyikan dan reserve ditolak.
- **Frontend:** tombol "Tukar Poin EFN" (OTP `pos_redeem`, online saja) -> reserve -> baris pembayaran Poin EFN
  senilai `value_idr`; metode tidak tampil sebagai pilihan manual; numpad tidak mengubah nominalnya; menghapus baris
  = dialog "Batalkan tukar poin?" -> release. Refund berpoin: layar refund menambah pembayaran Poin EFN negatif
  (`efn_loyalty_refund_preview`, alokasi proporsional kumulatif) + sisa uang ke metode asal (E-31).
- **Server saat sync (toleransi 0):** pembayaran poin tanpa reservasi, nominal != `value_idr`, nominal <= 0, partner
  beda, > `redeem_max_pct` x total incl. pajak (hitung server), kembalian dari porsi non-tunai -> `UserError`.
  Refund: poin hanya negatif, hanya bila order asal dibayar poin, tidak melebihi sisa poin asal (`UserError`);
  selisih alokasi > Rp1 -> reverse `hold` + review (SEC-60). Baris produk "Poin EFN" lama ditolak.
- **Outbox:** `commit` + `payment_amount` (K-18-3; BFF `409 payment_mismatch` -> final, reservasi & order `review`);
  `reverse` + `points_restored_value` (integer >= 0, K-18-5). Earn (K-18-4): `Σ eligible tanpa pajak × (1 −
  bayar_poin ÷ total incl pajak)`; flag `earn_exclude_point_payment` dari `GET /v1/pos/loyalty/params` (cache, tidak
  memanggil BFF saat sync), fallback `ir.config_parameter` `efn.pos.loyalty.earn_exclude_point_payment` (True).
- **Tutup sesi:** selisih (`bank_payment_method_diffs`) untuk metode Poin EFN ditolak (`pos.session._validate_session`).
- **Migrasi dari desain lama:** produk diskon "Poin EFN" (`product_efn_points`) **diarsipkan** (tetap ada agar xmlid &
  baris order historis dev/staging terbaca; ETL `EFN-POINTS` historis tetap); baris lama tidak dikonversi (belum ada
  produksi); filter audit pengganti E-24 dihapus dan filter `efn_setup_pos` dipulihkan
  (`migrations/19.0.2.0.0/post-migrate.py`). Hook install/uninstall E-24 dihapus.
- **Rekonsiliasi (K-18-6):** Σ `pos.payment` Poin EFN per PT/tanggal vs ledger BFF -> ETL (`loyalty_recon.py`).

## Review r11 (L-25a/c/e/f/g, versi 19.0.2.1.0)
- **SEC-113:** pembayaran metode Poin EFN hanya dibuat/diubah/dihapus di jalur sync resmi (`models/pos_payment.py`,
  penanda per cursor `efn_core/scope.py`), **juga pada order draft** (wizard pembayaran backend tidak bisa "membayar
  dengan poin" tanpa OTP + reservasi), termasuk mengganti metode baris lain menjadi Poin EFN. Kode server ber-sudo
  dikecualikan. Guard umum order non-draft: `efn_pos_refund_origin` (auto_install).
- **SEC-115:** reservasi redeem dan earn non-skipped menandai partner sebagai member (`efn_member_locked_at`,
  `efn_pos_otp_gate`); migrasi `19.0.2.1.0` mengisi penanda dari reservasi/order berpoin yang ada.
- **SEC-117:** validasi pembayaran Poin EFN pada refund mengunci order asal (`_efn_loyalty_lock_origin`, `FOR UPDATE`)
  sebelum menghitung sisa poin.
- **SEC-118:** commit mengirim `payment_amount = round(jumlah pembayaran Poin EFN aktual order)` (bukan `value_idr`
  reservasi), sehingga cek BFF `payment_amount == value_idr` (K-18-3) membandingkan dua sumber berbeda.
- **SEC-119:** `efn_is_loyalty_points` hanya bisa diisi/diubah admin (`base.group_system`, log peringatan; create ber-flag
  juga); bila metode sudah punya pembayaran, flag (dua arah), jurnal, dan pengarsipan metode Poin EFN tidak bisa diubah
  siapa pun selain superuser (migrasi/data modul). Aturan audit `pos.payment.method` di `efn_setup_audit` (rekomendasi
  review) = ranah functional, belum ditambahkan.

## Data & konfigurasi
- Produk **"Poin EFN"** (`product_efn_points`, desain G13): **diarsipkan sejak 19.0.2.0.0** (lihat bagian G18); tidak lagi dimuat ke POS.
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
6. **Sync server** (`pos.order._process_saved_order`, sebelum langkah standar & consume token promo): baris poin dideteksi dari **produk** (SEC-12). Paid tanpa reservasi aktif → `UserError` (rollback). **SEC-62 (13c): validasi preventif** — order berpoin selalu disinkron online (ADR-017 opsi a), jadi sync **ditolak** (`UserError`, rollback, reservasi tetap `reserved` sampai dilepas/expired) bila: baris poin bertanda ≥ 0; |Σ incl| ≠ `value_idr` (toleransi Rp1 × jumlah baris, dihitung ulang server dan dari nilai kiriman); partner ≠ partner reservasi; nilai poin > `redeem_max_pct` × total barang hitungan server (`redeem_max_pct` disimpan di reservasi saat reserve dari params BFF §9.1, fallback `efn.pos.loyalty.redeem_max_pct_fallback` = 50); total akhir < 0. `review` hanya tersisa sebagai backstop. Cocok → outbox `redeem_commit`. Reservasi aktif tanpa baris poin → outbox `redeem_release`. Draft: baris poin tanpa reservasi dihapus.
7. **Jenis order ditentukan server (SEC-59, 13c)** — `_efn_loyalty_order_kind`, bukan flag `is_refund` kiriman klien (Odoo 19 menyimpannya apa adanya): baris barang ber-`refunded_orderline_id` dari **tepat satu** order asal → `refund`; tanpa baris refund, tanpa qty < 0, tanpa `is_refund` → `sale`; barang qty < 0 tanpa order asal (retur tanpa struk, tombol `-`, termasuk tukar barang) atau `is_refund` tanpa baris refund → `return_unlinked`; baris refund dari > 1 order asal atau bercampur barang lain → `refund_ambiguous`. Dua jenis terakhir: **tanpa earn, tanpa reverse**, order `review` + catatan chatter (bila menyangkut member/poin; `pos.order` CE tanpa mixin aktivitas → tampil di filter *Review Poin*) — tidak lagi dilewati diam-diam. Catatan: core Odoo 19 sudah menolak refund dari > 1 order asal (*You can only refund products from the same order*); jalur ini pertahanan berlapis.
8. **Earn:** order `sale` ber-partner pada config `earn_enabled` → outbox `earn` (§9.7: termasuk eligible 0). Body earn membawa `pos_config_id` (§9.8 v4.3) agar batas per kasir/jam BFF aktif. Dasar earn **tanpa `abs()`**: Σ bertanda ≤ 0 → 0. Refund tidak pernah menambah poin. Partner tanpa HP Indonesia/dianonimkan → `skipped`.
9. **Refund:** order asal yang ikut loyalty → outbox `reverse` (`pos_reverse:<uuid refund>`) dengan `refund_ratio` kumulatif ≤ 1. Bila order asal memakai poin, layar refund otomatis menambah baris penyesuaian "Poin EFN" (nilai positif = round(rasio × nilai poin asal), dihitung server lewat `efn_loyalty_refund_preview`); server memvalidasi. **SEC-60 (13c):** selisih/hilang → outbox `reverse` berstatus **`hold`** (tidak pernah dikirim cron; restore poin redeem ditahan) + order `review` + aktivitas; rasio yang ditahan tetap dihitung dalam batas kumulatif. POS manager company terkait memutuskan di form outbox: *Setujui & kirim reverse* (dikirim apa adanya, termasuk restore) atau *Batalkan reverse* (`skipped`/`supervisor_cancelled`, penyesuaian manual). Kasir tidak bisa memutuskan (cek grup, record rule, company di server). Baris poin asal tidak bisa dipilih manual di layar refund; baris poin negatif pada refund → `UserError`.
10. **Pembatalan draft** (`action_pos_order_cancel`, `remove_from_ui`) dengan reservasi aktif → outbox `redeem_release`.

### `eligible_amount` (K-1)
Σ subtotal **tanpa pajak** baris barang setelah diskon baris & reward promo (baris reward ikut mengurangi), tidak termasuk produk "Poin EFN" dan kategori `efn_loyalty_earn_excluded`, **dikalikan faktor uang** = 1 − (nilai poin tanpa pajak / nilai barang tanpa pajak) — "bagian yang dibayar poin tidak menghasilkan poin". Dibulatkan ke bawah ke Rupiah. Satu fungsi `pos.order._efn_loyalty_eligible_amount()` (mudah diubah bila Finance/Q-13 memutuskan lain).
`refund_ratio` = nilai barang eligible yang direfund / nilai barang eligible order asal (faktor uang sama di kedua sisi); `refunded_eligible_amount` = barang eligible refund × faktor uang order asal. Bila eligible asal 0 → rasio nilai barang incl. pajak.

### Outbox (`efn.pos.loyalty.outbox`)
Dibuat dalam transaksi sync; `idempotency_key` unik (= kunci kontrak BFF). **Payload tidak disimpan** (nomor HP dibaca dari partner saat kirim). Cron *EFN Loyalty: kirim outbox poin POS ke BFF* tiap 1 menit + `_trigger()` setelah sync; baris dikunci `FOR UPDATE SKIP LOCKED`, commit per record (`ir.cron._commit_progress`). Retry (backoff 1 m → maks 1 j) hanya untuk error jaringan/5xx, 429, `409 earn_pending`; 4xx lain final → `failed` + order `review` + aktivitas To-Do ke POS manager. `404` pada release = selesai. > `outbox_max_hours` → `failed`. `reverse` menunggu `earn`/`redeem_commit` order asal selesai. Cron harian: reservasi `reserved` > 24 jam → `expired`; outbox `pending` > 24 jam → aktivitas.

### Batas earn BFF & runbook retur (§9.8 v4.3, SEC-61)
- `409 earn_limit_exceeded` (batas per order / harian per member / per PT & kasir per jam; default placeholder 5000 / 2000 / 50000 / 10000 poin) = **final**, bukan retry: outbox `failed`, order `review`, aktivitas To-Do ke POS manager. Tidak ada poin yang masuk untuk order itu.
- **Retur atas order yang earn-nya ditolak/gagal:** reverse tetap diantre (clawback 0 + kemungkinan restore redeem). BFF menjawab `409 earn_pending` (tidak ada earn tercatat) → dicoba ulang dengan backoff sampai `efn.pos.loyalty.outbox_max_hours` (48 jam) → `failed` (`max_age:earn_pending`) + order refund `review`. **Tidak ada restore/clawback otomatis.** Supervisor: cek order asal di *Review Poin*; bila order asal memakai poin, putuskan restore manual lewat Finance/BFF sesuai uang yang dikembalikan; bila tidak, tutup review (tidak ada poin yang perlu dikembalikan).
- Hal yang sama terjadi bila earn order asal sudah melewati retensi `loyalty_pos_earn` BFF (400 hari).

## Keamanan & data
- **SEC-58 (13c):** semua panggilan BFF lewat metode privat `efn.bff.client._efn_post/_efn_get` (tidak bisa dipanggil RPC). Metode publik modul ini (RPC kasir `pos.config.efn_loyalty_*`, tombol supervisor `action_efn_hold_*`) tercatat di `RPC_ALLOWLIST` (`efn_core/tests/test_rpc_surface.py`) dan diuji lewat `call_kw` nyata (`tests/test_rpc_access.py`).
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
`tests/test_loyalty_ledger.py` (TestPoSCommon, tag `efn`, BFF di-mock; termasuk SEC-59 retur tanpa struk/flag palsu/2 order asal, SEC-60 hold + keputusan supervisor, SEC-62 penolakan sync), `tests/test_rpc_access.py` (HttpCase `call_kw`, tag `efn_tour` karena fixture `TestPointOfSaleHttpCommon`: user biasa & kasir ditolak memanggil klien BFF & RPC POS tanpa grup/sesi/company; jalur sah kasir tetap jalan) dan `tests/test_loyalty_tour.py` (HttpCase + 4 tour `static/tests/tours/efn_loyalty_tour.js`, tag `efn_tour`: sukses, OTP salah, batal → release, offline diblok).

## Referensi
Tidak ada kode vendor (OCA/Cybrosys) yang dipelajari atau dipakai. Pola internal: `efn_pos_otp_gate` (gate server, deteksi baris dari produk) dan `efn_pos_payment_gateway` (cermin status BFF, rekonsiliasi). Ide pembagian diskon per kelompok pajak dari core `pos_loyalty` (`_getDiscountableOnOrder`, LGPL-3, ditulis ulang).

## Batasan / belum terverifikasi
- Perlakuan PPN penukaran poin (potongan harga vs alat bayar) menunggu konsultan pajak (P-8): implementasi memperlakukan poin sebagai potongan harga per kelompok pajak.
- Rate limit cek saldo di Odoo bersifat per worker (in-process); batas efektif ada di BFF.
- Promo `pos_loyalty` yang dihitung ulang setelah baris poin terpasang memakai `priceIncl` order (termasuk poin) untuk `totalFactor`; alur UI mencegahnya (tukar poin langkah terakhir, kembali = batal), tetapi belum diuji dengan promo persentase + poin pada order yang sama.
- Refund berpoin saat offline: penyesuaian tidak ditambahkan → reverse `hold` + `review` di server (SEC-60).
- SEC-60 rekomendasi lanjutan (BFF, belum): field `restore_points` eksplisit di kontrak reverse (restore dari nilai baris penyesuaian aktual, bukan rasio) agar supervisor bisa menyetujui clawback tanpa restore.
- SEC-62: order berpoin yang ditolak sync tertahan di POS sampai kasir/supervisor menghapus penukaran (alur UI standar menampilkan error sync). Uji tour untuk kasus tolak belum ada.
- SEC-59: retur tanpa struk dari member tidak meng-clawback earn order lama (tidak ada tautan); hanya `review` + aktivitas.
- SEC-61 (plafon earn, batas harian, alert, rekonsiliasi) di BFF/ETL — bukan modul ini.
- Integrasi dengan BFF nyata (stack dev) belum dijalankan; kontrak diikuti dari `docs/04 §9.7` dan DTO BFF.
