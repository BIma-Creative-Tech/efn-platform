# efn_online_order (E-28) — kanal penjualan online Super-App

> **Ringkasan manajemen.** Modul ini membuat Odoo bisa menerima pesanan dari Super-App **tanpa** membuka Odoo ke
> internet dan tanpa toko online bawaan Odoo. BFF memanggil 10 fungsi khusus; Odoo menghitung harga & pajak,
> membuat pesanan penjualan per PT, membuat faktur saat dibayar, dan memberi kabar status (siap/diambil/batal)
> ke BFF dengan pesan bertanda tangan. Kode ambil pelanggan hanya disimpan sebagai hash. Semua angka (batas bayar
> 70 menit, batas jumlah, pembulatan) adalah konfigurasi kanal, bukan kode. Sejak G19 pesanan online bisa
> **mencadangkan stok** sejak checkout, menerima **kode promo** (promo otomatis per brand ikut dihitung Odoo), dan
> setiap kanal punya **kode brand**.

- Versi `19.0.1.7.0` (G22 W-04 atribut/KDS-hook + W-12 Poin EFN, lihat bagian G22), LGPL-3, `depends`: `sale_stock`, `sale_loyalty` (CE, mesin promo E-36; baru di 19.0.1.2.0 —
  upgrade memasangnya bila belum), `point_of_sale` (hanya `pos.category`), `mail`, `efn_core`.
  Tanpa `website*`, `delivery`, `payment_*`, tanpa modul pihak ketiga (ADR-031; `check-odoo-deps.sh`).
- Kontrak: `docs/04_SERVICE_CONTRACTS.md` §10 (v5.0) + **§11 (v6.0, G19) yang menang atas §10 dan docs/08**;
  delta §10.4.4 + keputusan L-1..L-7 menang atas `docs/08` §8.3. Fit-gap: `docs/08` §8 (F-55..F-73), §10.1-§10.3
  (E-35, E-36, E-37a). Keputusan lead G19: L-27, L-28, L-29, L-34, L-35, L-37.
- ADR: ADR-034 (*Proposed*, kanal = BFF + `/json/2` terfilter), ADR-019 (*Proposed* + catatan revisi; dana titipan
  A1), ADR-026 (provider `mock`), ADR-027 (pajak dari Odoo, tanpa tarif di kode), ADR-029 (tanpa PII), ADR-024
  (kredensial per PT), ADR-031. Risiko: R-T10 (saga), R-O05 (refund per SO), R-L03 (pajak per PT), R-T15 (key
  kedaluwarsa — `api_key_duration` di `efn_setup_online`), R-S03 (Odoo privat).
- Referensi desain (tidak disalin, tidak di-WebFetch): pola internal `efn_pos_loyalty_ledger` (outbox) dan
  `efn_pos_payment_gateway`; OCA/sale-workflow `sale_order_type` hanya konsep "kanal = record konfigurasi".

## G19 (19.0.1.2.0) — reservasi stok, promo online, kanal per brand
Risiko: R-T10 (saga), R-O05 (refund per SO), R-B06 (promo bocor lintas kanal/POS), R-T14 (multi-domain brand),
R-T15 (key per kanal). ADR: ADR-020 (poin Odoo tidak dipakai), ADR-019, ADR-034, ADR-031.

**E-35 reservasi saat place** (`stock_hold_mode='confirm'`, L-27). `efn_online_place`: SO dibuat → promo diterapkan →
`quote_hash` dicocokkan → `action_confirm` di savepoint (`CONFIRM_SCOPE`, total berubah/kupon ditolak →
`price_changed`) → semua move storable kepala rantai wajib `assigned` (dicoba `_action_assign` sekali). Kurang →
**seluruh savepoint place di-rollback** + `efn_online:out_of_stock` (tidak ada `efn.online.order`/SO/reservasi/kupon
terpakai). `mark_paid` untuk SO yang sudah `sale`: cek move masih `assigned` (bukan `free_qty`), dicabut POS →
`_action_assign`, tetap kurang → `stock_short` + `short_lines` (`available` = qty yang masih tercadang) → alur
kompensasi lama (cancel `stock_short` membatalkan SO `sale` + melepas reservasi). Konfirmasi ulang dilewati; total
SO ≠ total place → `review`. `action_efn_ready` menandai move tercadang `picked=True` (`_free_reservation` tidak
mencabut baris picked); serah terima menandai semua move picked sebelum validasi (tanpa backorder terpisah).
Kedaluwarsa/batal = cron/alur lama (`_action_cancel` melepas reservasi + mengembalikan kupon). Default
`payment_timeout_minutes` **70**; kanal lama mempertahankan nilai tersimpan. Mode `none` tidak berubah.

**E-36 promo online** (L-28, L-34, L-35). `efn_online_quote(..., promo_codes=None)` dan `efn_online_place` payload
`promo_codes` opsional (≤ 3, `^[A-Za-z0-9_-]{1,40}$`, huruf besar, duplikat dibuang; masuk `payload_sha256` hanya
bila tidak kosong). Program yang berlaku = `sale_ok` + tipe online + company kanal + `pricelist_ids` ∋ pricelist
kanal + bukan `efn_requires_otp` + bukan `portal_visible` (program tanpa pricelist TIDAK berlaku walau standar
`sale_loyalty` menganggapnya global). Tanpa program → `sale.order.new()` (perilaku lama). Dengan program → SO nyata
di savepoint yang selalu di-rollback (nomor SO bolong diterima). Urutan: `_update_programs_and_rewards` → tiap kode
(savepoint; kode dicocokkan case-insensitive ke `loyalty.rule.code`/`loyalty.card.code` milik program kanal —
kupon buatan Odoo berhuruf kecil tetap terpakai; `_try_apply_code` → tepat satu reward non-multi per kupon →
`_apply_program_reward`; galat apa pun → kode ditolak) → klaim reward otomatis deterministik (urutan `sequence, id`;
lewati program > 1 reward, nominatif, produk multi, reward yang sudah ada) → hitung ulang. **L-35**: tagihan grup < Rp1,
> 20 baris reward, atau baris reward positif → semua reward dari kode di-rollback, kode masuk `promo_rejected`.
Kanal mode `none` → semua kode ditolak (promo otomatis tetap). Kode tidak pernah di-log (asli maupun hash; sejak
19.0.1.3.0 log hanya `codes=<n> applied=<m>`, L-42). Error konkurensi PostgreSQL (`FOR UPDATE NOWAIT` program
`sale_loyalty`, `SerializationFailure` kunci kuota L-40) tidak ditangkap → Odoo mengulang request.
Constraint `loyalty.program` (§11.2 no. 7) + default `sale_ok=False` + `post_init_hook`/post-migrate 19.0.1.2.0
mematikan `sale_ok` program lama yang melanggar.

**E-37a** `efn.online.channel.brand_code` (`^[A-Z][A-Z0-9]{1,9}$`, tidak unik, opsional) → blok `channel` katalog
`brand_code` (`null` bila kosong). **L-37**: satu user teknis hanya untuk satu kanal (constraint `_check_efn_users`).

Bentuk respons json2 (aditif): quote per grup + `promo_applied: [str]`, `promo_rejected: [str]` (selalu ada;
gabungan = `promo_codes` ternormalisasi, tidak beririsan); baris reward di `lines[]` membawa `reward: true` (baris
biasa tanpa kunci `reward`), `name` = nama program, `subtotal_incl ≤ 0`, `available: true`. `quote_hash` memuat kode
terpakai (urut) + `reward_id` per baris reward hanya bila ada (tanpa promo hash sama dengan 19.0.1.1.0). Place:
bentuk sama; mode confirm → SO `sale` sejak place.

Referensi desain (tidak disalin): `website_sale_loyalty` (CE 19.0, `_auto_apply_rewards`, `_try_pending_coupon`) —
pola klaim otomatis ditulis ulang; tidak ada kode OCA/Cybrosys.

## Putaran perbaikan r12 (19.0.1.3.0) — keputusan lead L-40..L-44 (docs/04 §11.7)
Sumber: `docs/reviews/SECURITY_REVIEW_2026-10-18_r12.md` (SEC-130/131/132/139/141) + QA G19 (#1, #3).
Risiko: R-B06 (promo/stok disalahgunakan), R-S10, R-O05 (refund per SO).

- **L-40 / SEC-130 kuota promo paralel.** Jalur **place** (bukan quote), sebelum SO dibuat dan sebelum kuota
  dihitung: `_efn_online_lock_quota_programs` menjalankan `UPDATE loyalty_program SET write_date = write_date WHERE
  id IN %s` untuk program ber-`limit_usage` yang bisa berlaku (promo **otomatis** kanal + pemilik kode yang dikirim).
  Odoo memakai REPEATABLE READ: place kedua yang paralel menunggu kunci baris lalu mendapat `SerializationFailure`
  → Odoo mengulang request dengan snapshot baru → kuota habis → `price_changed`. Error tidak ditangkap. Efek samping:
  selama place berpromo berkuota belum commit, quote lain dengan kode yang sama mendapat `LockNotAvailable` (NOWAIT
  `sale_loyalty`) dan diulang Odoo; promo otomatis berkuota menyerialkan semua place kanal itu.
- **L-41 / SEC-131 batas mode confirm.** Constraint: `stock_hold_mode='confirm'` wajib `max_order_amount > 0`;
  nominal negatif ditolak di semua mode. Field baru **`max_line_qty`** (wajib, default 20, 1..9999), hanya berlaku di
  mode confirm: qty satu produk > batas → quote `issues` `{"product_id": <id>, "code": "qty_limit"}`; place →
  `efn_online:invalid_input` (pola issue lain). Katalog `max_qty` = `min(max_qty_per_line, max_line_qty)` di mode
  confirm; perubahan kanal (`write_date`) kini memicu katalog penuh. Post-migrate 19.0.1.3.0: kanal confirm dengan
  `max_order_amount` 0 diisi placeholder L-41 **Rp5.000.000** + WARNING (ganti sebelum go-live).
- **L-42 / SEC-132.** Tag hash kode promo dihapus dari log Odoo (`promo_code_ref` dihapus).
- **L-43 / SEC-139.** `sale.order._prepare_invoice` (dipakai `_create_invoices` **dan** wizard uang muka) menolak SO
  online tanpa pembayaran tercatat (`paid_idempotency_key` kosong) → `UserError`. `_efn_do_cancel` tidak membuat
  refund/nota kredit untuk order yang tidak pernah dibayar; faktur ter-posting seperti itu (data lama) → WARNING +
  aktivitas Accounting (reversal manual).
- **L-44 / QA #3.** Reward produk hadiah storable yang membuat kebutuhan > stok bebas outlet dilepas di savepoint
  klaimnya: dari kode → kode masuk `promo_rejected`; otomatis → dilewati. Tidak pernah menjadi `issues` (dulu
  `out_of_stock` untuk produk hadiah). Quote dan place memakai logika yang sama (hash cocok).
- **SEC-141.** Deskripsi `RPC_ALLOWLIST` `efn_online_quote` diperbarui (savepoint SO berpromo, nomor urut, kunci).

Perubahan respons json2: hanya **issue baru `qty_limit`** di `efn_online_quote.issues` (kanal mode confirm) dan nilai
`max_qty` katalog yang lebih kecil di mode confirm. `issues` tidak lagi memuat `out_of_stock` untuk produk hadiah.
Tidak ada kunci/metode baru.

Uji (`tests/test_online_g19.py::TestEfnOnlineSecurityR12`): kunci kuota kode + otomatis (urutan kunci sebelum promo,
`xmin` baris program berubah hanya di place), constraint batas confirm, `qty_limit`/`order_limit`, faktur ditolak
sebelum bayar (`_create_invoices`, `_prepare_invoice`, wizard), batal/kedaluwarsa tanpa refund, reward hadiah tanpa
stok (kode & otomatis), buy X get Y dengan PB1 exclude (**fixture lokal**, bukan data `efn_setup_company`).

Uji manual: Kanal online → mode *Reservasi saat place* tanpa *Batas nominal* → simpan ditolak; isi batas qty 2 →
quote qty 3 → issue `qty_limit`; SO online belum dibayar → *Create Invoice* → pesan "belum dibayar".

Uji manual G19: Konfigurasi → Kanal online → *Reservasi saat place*; buat program *Promo Code* `sale_ok` dengan
pricelist kanal; quote `promo_codes` lewat smoke `SMOKE10` (`scripts/smoke/odoo_online_smoke.py setup`); place →
Inventory menampilkan picking `Ready`; jual barang yang sama di POS → bayar → `stock_short`; tandai siap → move picked.

## G20 (19.0.1.4.0) — batas berat pesanan W-33/E-35b + kode kanal = kode PT (docs/04 §12.1, §12.3)
ADR-034, ADR-031 (*Proposed*); keputusan pemilik proyek 18-16; docs/08 §11.7. Risiko: R-O05 (pesanan ambil sendiri
terlalu berat/tidak terlayani), R-B06 (penyalahgunaan reservasi lewat pesanan besar).

- **Field kanal:** `max_order_weight_kg` (Float kg, `digits='Stock Weight'`, default 0 = mati, 0..100000) dan
  `weight_missing_policy` (`block` default | `ignore`, hanya berlaku bila batas aktif). Di form kanal (tab batas).
- **Rumus:** Σ `line.product_id.weight × line.product_qty` atas baris SO (sama dengan `delivery.carrier._match_weight`
  19.0, diverifikasi WebFetch 2026-10-18; termasuk baris reward produk hadiah), dikonversi ke kg bila
  `product.weight_in_lbs = '1'` (`product.template._get_weight_uom_id_from_ir_config_parameter`, diverifikasi 19.0;
  lb → kg via `uom._compute_quantity`). Pembanding `float_compare(..., precision_digits=3)`: tepat di batas = boleh.
- **Issue quote** (setelah `order_limit`): `{"product_id": <id>, "code": "weight_missing"}` per produk tanpa berat
  (kebijakan `block`; baris reward `sale_loyalty` dan produk `service` dikecualikan), lalu
  `{"product_id": null, "code": "weight_limit"}` bila total > batas. Place → `efn_online:invalid_input` (pola issue
  lain; jalur promo diperiksa dari SO tersimpan setelah cek hash).
- **`quote_hash`:** bila batas aktif, hash memuat blok `weight` = `{limit, total (3 desimal), policy, missing[]}` →
  berat produk/batas/kebijakan berubah antara quote dan place = `price_changed`. Batas 0 → hash **identik** dengan
  19.0.1.3.0 (quote yang berjalan saat deploy tetap sah).
- **Katalog:** item bertambah `weight_kg` (float kg 3 desimal, `null` bila produk tanpa berat).
- **L-46 / SEC-144 (sisa L-47):** constraint kode kanal kini juga menolak akhiran `-FILE`.
- Help kode kanal memakai contoh `GL, PB, BCR, BCF, WTZ` (kode kanal = kode PT, L-48; WTZ sejak G24 §16.1). `company_code` di katalog,
  quote, dan event = `efn_brand_code` company (kini `GL`/`PB`/`BCR`/`BCF`/`WTZ`/…; tanpa perubahan kode).
- Tidak ada migrasi: kolom baru memakai default (0 / `block`) → kanal lama tidak berubah perilaku.
- **Koordinasi:** builder C (E-45 `payment_mode`) juga menyentuh modul ini; versi berikutnya ≥ 19.0.1.5.0.

Perubahan respons json2 (aditif): `efn_online_catalog.items[].weight_kg` (number | null); kode issue baru
`weight_limit` (`product_id` null) dan `weight_missing` di `efn_online_quote.issues`. Tidak ada metode/argumen baru.

Uji (`tests/test_online_g20_weight.py`): default & constraint, batas 0 tidak mengubah hash, `weight_limit` quote +
place ditolak tanpa SO, tepat di batas boleh, `weight_missing` block vs ignore (+ jasa dikecualikan, kebijakan masuk
hash), perubahan berat/batas → hash beda + `price_changed`, `weight_kg` katalog, database lb → kg, mode confirm + promo
otomatis (place ditolak tanpa reservasi tersisa; reward diskon tidak memicu `weight_missing`).
Uji manual: Kanal → *Batas berat pesanan* 10 → produk Beras berat 5 kg → quote 3 unit → issue `weight_limit`.

## G20 (19.0.1.5.0) — bayar di kasir E-45 (docs/04 §12.2, L-50/L-52/L-55/L-56)
ADR-034, ADR-019 (titipan hanya `qris`), ADR-031. Risiko: R-T10, R-O05. Jembatan POS: **`efn_online_pos_counter`**
(auto-install; produk pelunasan, jurnal kliring, lookup POS, validasi sync).

- **Kanal** `payment_modes` (Char dipisah koma dari `qris`, `counter`; default `qris`; constraint format; `counter`
  butuh `invoice_on_paid`). Pilot tanpa vendor = `counter` saja (konfigurasi `efn_setup_online`, L-55).
- **Place**: kunci opsional `payment_mode` (`qris` default | `counter`; tidak diizinkan kanal → `invalid_input`) dan
  `counter_code` (dibuat BFF, alfabet §10.2, 8 karakter; wajib bila `counter`, dilarang selain itu, ≠ `pickup_code`).
  `payment_mode: "counter"` ikut `payload_sha256` (qris tidak → hash lama sama); `counter_code` **tidak** (pola kode
  ambil). Disimpan sebagai `counter_code_hash` = digest PBKDF2-SHA512 **deterministik per company** (salt dari
  company + `database.secret`, putaran `efn_online.counter_code_kdf_rounds` default 210 000 tersimpan di digest) agar
  kasir bisa mencari dengan kode saja; digest bentrok dengan pesanan terbuka → `invalid_input`. Tidak dikembalikan,
  tidak di-log; dihapus saat dibayar/batal/review. Field `payment_mode` di pesanan (default `qris`).
- **BFF `mark_paid` / `refund_settled`** untuk pesanan `counter` → `efn_online:invalid_state`.
- **Jalur internal** `_efn_counter_mark_paid(payment_ref, idempotency_key, amount_paid)` (privat, dipanggil sync POS):
  idempoten per kunci; `placed` & belum kedaluwarsa → langkah bersama `_efn_apply_payment` (refactor dari
  `efn_online_mark_paid`, perilaku QRIS tidak berubah) → `confirmed` | `stock_short` | `review`; sudah `cancelled`
  atau `placed` lewat batas → pembayaran dicatat + `review` (L-56); sudah dibayar kunci lain → `invalid_state`.
- **Jurnal pembayaran** lewat hook `_efn_payment_journal` (QRIS = titipan; counter = kosong di modul ini → `review`
  `journal_missing`, diisi jurnal kliring oleh jembatan; tidak pernah jatuh ke titipan).
- **Event** `paid` pesanan counter membawa `payment: {"method": "counter"}` (QRIS tanpa kunci ini).

Perubahan json2 (aditif): katalog `channel.payment_modes: string[]` (kanonik, mis. `["qris","counter"]`);
`efn_online_place` `order.payment_mode` + `order.counter_code` (input); hasil place **tidak** berubah; event `paid`
`payment`. Uji: `tests/test_online_g20_payment_mode.py` (bagian tanpa POS) + `efn_online_pos_counter/tests`.

## G21 (19.0.1.6.0) — faktur atas nama pembeli E-39 (docs/04 §13.5, L-63; docs/08 §10.7)
ADR-036 §0 (Accepted: enkripsi PII Odoo), ADR-034, ADR-031. Risiko: R-L01/R-L10 (PII pembeli, UU PDP), R-T18
(fail-closed enkripsi).

- **RPC baru** `efn.online.order/efn_online_set_invoice_party(channel_code, platform_order_ref, profile,
  idempotency_key)` (path gateway ke-12) → `{"state": "set"}`. `profile = {buyer_type: "npwp"|"nik"|"passport",
  tax_id, name, address, email?}`; kunci lain ditolak. Validasi server: NPWP/NIK 16 digit (titik/strip/spasi dibuang),
  paspor 5–20 alfanumerik, nama 2–100, alamat 5–255 (spasi beruntun dirapikan, karakter kontrol ditolak), email RFC
  dasar ≤ 254 (opsional, huruf kecil). Kunci idempotensi yang disarankan: `online:invoice_party:<part_id>`.
- **Urutan cek & kode error:** pemanggil (`AccessError`) → input (`invalid_input`) → order kanal (`not_found`) →
  **`invoice_not_available`** (kode baru) bila PT bukan PPN (`efn_business_type != retail`, yaitu GL/BCR saja) **atau**
  `efn_pii` tidak terpasang / field partner `name, street, email, vat` (+ `l10n_id_buyer_document_number` untuk
  NIK/paspor, `efn_pii_l10n_id`) bukan field terenkripsi `EfnPiiChar` (mis. `base_vat` tanpa `efn_pii_base_vat`) /
  mode terkunci kunci PII → idempotensi (kunci sama + profil sama → `set`; selain itu `idempotency_conflict`) →
  `invalid_state` bila order bukan `placed` atau sudah ada faktur. L-63: profil **tidak pernah** ditulis ke Odoo tanpa
  enkripsi aktif. `efn_pii` tidak di `depends` (dicek saat runtime lewat kelas field).
- **Partner pembeli:** perorangan (`is_company=False`, tanpa induk) per company, `efn_online_invoice_buyer=True`
  (penanda baru), terlindungi otomatis oleh `efn_pii` (dicek setelah create; tidak terlindungi → rollback +
  `invoice_not_available`). NPWP → `vat` (+ `l10n_id_buyer_document_type=TIN` bila `l10n_id_efaktur_coretax`
  terpasang) + negara Indonesia; NIK/paspor → `l10n_id_buyer_document_type` NIK/Passport + `_number` (tanpa `vat`).
  Dipakai ulang hanya bila partner bertanda milik company sama dan nama, alamat, email, nomor identitas sama persis
  (pencarian `vat =` / nomor dokumen `=` → blind index efn_pii, lalu dicocokkan terdekripsi). Pembuatan diserialkan
  per company (advisory lock tanpa PII).
- **SO:** `partner_invoice_id` = pembeli; `partner_id` tetap partner generik kanal. Faktur saat `mark_paid` memakai
  `partner_invoice_id` (standar `_prepare_invoice`) → Coretax membaca NPWP dari `commercial_partner_id` = pembeli.
- **Tanpa PII** di hasil RPC, log (hanya id order/partner + `buyer_type`), hash, outbox/event. Pesan validasi Odoo
  (`base_vat` memuat nilai) tidak diteruskan/di-log.
- **Field baru:** `res.partner.efn_online_invoice_buyer` (Boolean), `efn.online.order.invoice_party_idempotency_key`
  (Char, `base.group_system`).
- **Batasan:** NIK/paspor butuh `efn_pii_l10n_id` (builder lain, G21) — tanpa itu `invoice_not_available`; ekspor
  Coretax pembeli NIK/paspor tetap butuh `vat` di `download_efaktur` CE 19.0 (validasi "NPWP … hasn't been filled") →
  **[B]** keputusan Finance/Legal (Q-F19-6 default: NPWP saja). Penghapusan/anonimisasi partner pembeli (erasure,
  kecuali data wajib faktur 10 th) belum. `account.move.invoice_partner_display_name` terenkripsi = E-38a sisa
  (`efn_pii_account`, SEC-153).

Perubahan json2 (aditif): metode baru di atas; kode error baru `efn_online:invoice_not_available`. Metode lain tidak
berubah. Uji: `tests/test_online_g21_invoice_party.py` (kelas tanpa efn_pii selalu jalan; kelas dengan efn_pii di-skip
bila efn_pii/efn_pii_base_vat tidak terpasang).

## G22 (19.0.1.7.0) — F&B online (W-04) + Poin EFN online (W-12)
Kontrak `docs/04` §14.B, §14.C, §14.7 L-72 (D-22-8, D-22-9, D-22-11), L-78 (identitas baris quote). Fit-gap `docs/08`
§10.6, §13.2, §13.5. ADR-034, ADR-020 (ledger poin tunggal di BFF), ADR-027, ADR-031. Risiko: R-T10, R-O05, R-B06,
R-L01. Jembatan KDS: **`efn_online_kds_bridge`** (auto-install bila `efn_pos_kds` ada).

**B-4 atribut `no_variant`.** Baris cart/quote/place = `{product_id, qty, attribute_value_ids?: int[] ≤ 10}`;
identitas baris = `(product_id, sorted(attribute_value_ids))` (produk sama dengan modifier berbeda = dua baris; identitas
atau PTAV ganda → `invalid_input`). PTAV harus milik baris atribut `no_variant` template produk, `ptav_active`, bukan
`is_custom`; atribut non-`multi` wajib tepat satu nilai. Tidak sah → issue quote `{product_id, code:"attribute_invalid"}`
(BFF `409 cart_has_issues`), place → `invalid_input`. Baris SO memakai field standar
`product_no_variant_attribute_value_ids` → `price_extra` dihitung `sale.order.line` (`_get_product_price_context` →
`no_variant_attributes_price_extra`, [V] source 19.0). `quote_hash` memuat `["ptav", ids]` per baris **hanya bila ada**
(hash tanpa atribut sama dengan 19.0.1.6.0); `payload_sha256` place: baris `[pid, qty]` atau `[pid, qty, [ptav]]`.
Batas qty reservasi, stok, dan menu habis dihitung per produk (Σ semua barisnya).

**B-2/B-3 katalog & stok.** Item katalog + `attributes` (`[{attribute_id, name, display_type, required, values:[{id,
name, price_extra_excl}]}]`, `[]` bila tidak ada; `required` = `display_type != "multi"`; nilai kustom/nonaktif tidak
dikirim). Perubahan PTAV/baris atribut memicu katalog penuh. Blok `channel` + `business_type` (`fnb` bila
`company.efn_business_type == "fnb"`, selain itu `retail`) dan `loyalty: {earn, redeem}` (field kanal baru
`loyalty_earn`, `loyalty_redeem`, default mati). Produk `type='combo'` tidak dipublikasikan di **semua** kanal (D-22-9;
SO online tidak membangun baris anak combo). `product.template.efn_online_sold_out` (toggle "Habis (online)" di tab
Online) → `efn_online_stock` `out` + issue `out_of_stock`; kanal F&B → `in_stock` selain itu, tanpa `qty`.

**B-6/B-7 hook KDS & event.** Hook no-op `_efn_after_confirmed`, `_efn_after_cancelled`, `_efn_after_refund(refund)`
(diisi jembatan). `action_efn_ready` → `_efn_set_ready("manual")`; jembatan → `_efn_set_ready("kds")`. Field
`ready_source`, `kitchen_started_at`. Event `ready` + `ready_source: "kds"|"manual"`; event baru opsional **`preparing`**
(`state` = `confirmed`, + `kitchen_started_at` ISO Z; sekali per pesanan; BFF lama → `ignored_unknown_event`).

**W-12 Poin EFN** (redeem = porsi pembayaran, bukan diskon; DPP/pajak atas harga penuh):
- Place: kunci opsional `points_payment: {points, value_idr}` (int > 0, kunci lain ditolak; ikut `payload_sha256`
  hanya bila ada). Validasi (setelah total dihitung, di savepoint place): kanal `loyalty_redeem`; kebijakan PT
  `efn.loyalty.policy` **published** & berlaku + nilai tukar global company `EFN` (fail-closed bila tidak ada);
  `points ≥ redeem_min_points`; `value_idr == points × point_value_idr`; `value_idr ≤ floor(amount_payable ×
  redeem_max_pct / 100)`; porsi tunai ≥ Rp1. Gagal → **`efn_online:points_invalid`** (kode baru; BFF → `409
  exceeds_max_pct`). Hasil place + **`amount_due`** (= `amount_payable − value_idr`; tanpa poin = `amount_payable`).
- `mark_paid`: `payment.points_value` opsional (harus = nilai saat place, selain itu `review` `points_mismatch`);
  `amount_paid` dibanding `amount_due` (beda → `review` `amount_mismatch`). Faktur dibayar dua kali: tunai di jurnal
  titipan/kliring + Poin EFN di jurnal metode POS "Poin EFN" PT (`pos.payment.method.efn_is_loyalty_points`).
  **L-79:** modul ini tidak pernah menulis konfigurasi akun. Jurnal siap = metode punya `outstanding_account_id` dan
  semua baris metode masuk/keluar jurnal punya `payment_account_id` (diisi data `efn_setup_pos` 19.0.1.13.0 / Finance).
  Tidak siap → place berpoin ditolak `points_invalid` + log ERROR (id company saja) + aktivitas To-Do untuk Accounting
  Manager di jurnal Poin EFN, dibuat di cursor baru lewat `cr.postrollback` (request sudah di-rollback), sekali per
  company per hari (`ir.config_parameter` `efn_online.points_account_alert.<company_id>`). Akun hilang di antara place
  dan bayar → `review` `points_journal_missing` (pertahanan kedua).
- Refund (`efn_online_refund`, nota kredit pembatalan): `points_value = round(min(ΣR, T) × V ÷ T) − Σ sebelumnya`
  (setengah ke atas, dibatasi sisa V), `cash_amount = amount − points_value` (Σ ≤ porsi tunai dibayar, selain itu
  `amount_mismatch`), `eligible_amount_refunded = floor(subtotal tanpa pajak baris berhak poin × porsi uang)`. Settle →
  pembayaran keluar tunai + Poin EFN (restore di BFF saat `settled`, D-22-11).
- Earn: saat serah terima `eligible_amount = floor((Σ subtotal tanpa pajak baris SO berhak poin − Σ baris nota kredit) ×
  (1 − V/T bila earn_on_money_only))` dibekukan; kategori `product.category.efn_loyalty_earn_excluded` (bila modul POS
  loyalty ada) dikecualikan; `earn_on_money_only` dari kebijakan PT (fallback parameter
  `efn.pos.loyalty.earn_exclude_point_payment`).
- `efn_loyalty_accounting` & `efn_pos_loyalty_ledger` **tidak** di `depends` (dibaca saat runtime, pola efn_pii).

**Perubahan json2/event persis (aditif):**
- `efn_online_catalog.items[].attributes`; `channel.business_type`, `channel.loyalty`.
- `efn_online_quote.lines[]` + `attribute_value_ids` (int[] naik) + `attributes_text` (string, `""` tanpa atribut);
  issue `attribute_invalid`; baris input menerima `attribute_value_ids`.
- `efn_online_place`: input `lines[].attribute_value_ids`, `points_payment`; hasil + `amount_due`.
- `efn_online_mark_paid`: input `payment.points_value` (opsional). Hasil tidak berubah.
- `efn_online_refund` / `efn_online_cancel.refund` / `efn_online_status.refunds[]` + `points_value`, `cash_amount`,
  `eligible_amount_refunded`; `efn_online_status[]` + `eligible_amount` (null sebelum `picked_up`).
- Event: `refund` (refunded/cancelled/refund_settled) + 3 kunci di atas; `paid` counter `payment: {method:"counter",
  points_value}`; `ready` + `ready_source`; `preparing` (baru) + `kitchen_started_at`; `picked_up` + `loyalty:
  {eligible_amount}` **hanya** bila kanal `loyalty_earn`. Event `placed`/QRIS `paid` tidak berubah.
- Kode error baru `efn_online:points_invalid`. Tidak ada metode RPC baru.

Uji: `tests/test_online_g22.py` (kebijakan/metode Poin EFN di-patch; pembacaan kebijakan diuji dengan stub).
Uji manual: produk menu dengan atribut *Gula* (radio) + *Ekstra* (multi, +Rp6.000) → quote dua baris latte
berbeda modifier → harga baris kedua +6.000; kanal *Poin EFN: tukar* + kebijakan PT dipublikasikan → place dengan
`points_payment` → faktur menampilkan dua pembayaran.

**Batasan G22:** `exclude_for` atribut tidak divalidasi; nilai kustom ditolak (bukan teks bebas). Pembulatan nota
kredit parsial bisa membuat alokasi poin berbeda ±Rp1 dari hitungan BFF (BFF toleransi ±Rp1). Pesanan `stock_short`
yang dibatalkan sebelum faktur tidak punya refund Odoo → restore penuh dihitung BFF. Kebijakan dibaca dari Odoo
(published); bila BFF memakai env karena kebijakan belum ada, Odoo menolak poin (fail-closed, perlu kebijakan PT +
global dipublikasikan sebelum mengaktifkan `loyalty_redeem`).

## Model
| Model | Isi |
|---|---|
| `efn.online.channel` | kanal per PT/brand (`code` unik, pricelist, partner generik tanpa HP/email/NPWP, tim, `cash_rounding_id`, `journal_titipan_id`, cek stok `free_qty` + buffer + ambang "low", `payment_timeout_minutes` 70 (G19), `stock_hold_mode` none/confirm, `brand_code`, batas baris/qty/nominal, `invoice_on_paid`, `user_ids` user teknis [hanya `base.group_system`; satu user = satu kanal, L-37], `pickup_max_attempts` 5, `pickup_reminder_hours` 48). Delivery ditolak constraint (belum didukung). |
| `efn.online.outlet` | outlet per kanal (`code` unik per kanal, gudang, fiscal position outlet — wajib bila kanal `require_outlet_fiscal_position`, tidak boleh auto-apply). |
| `efn.online.order` | satu bagian = satu SO: refs BFF (`platform_order_ref`=part_id, `checkout_ref`=order_id), `idempotency_key` + `payload_sha256` + `quote_hash`, state `placed/paid/confirmed/stock_short/ready/picked_up/cancelled/review`, nominal, `pickup_code_hash` (pbkdf2-sha512), `event_seq`. Tanpa partner/HP/nama/alamat. |
| `efn.online.refund` | refund per order: nota kredit standar (reversal faktur + qty disesuaikan), kumulatif ≤ dibayar, `refund_ref` UUID, settle → pembayaran keluar jurnal titipan. |
| `efn.online.event.outbox` | event ke BFF. G18-QA-01, versi 19.0.1.1.0: kunci `online:<platform_order_ref>:<event>:<seq>` tanpa id lokal, ditambah `event_id` UUID v4 per event yang disimpan sehingga retry mengirim nilai yang sama. `seq` = max(seq terakhir + 1, detik sejak 2026-01-01 UTC), jadi tetap naik setelah DB dipulihkan dan BFF tidak mengabaikannya sebagai `seq <= last_seq`; asumsi jam NTP. `409 event_conflict` = final dan pesanan masuk `review`. Migrasi mengisi `event_id` dan mengganti kunci event `pending`. Retry 1 m → 1 j, final pada 400/401/403/404/409/422, umur > 48 j → gagal + aktivitas. |
| `product.template` | `efn_online_channel_ids` (constraint: company & pajak cocok, `sale_ok`), `efn_online_description` (teks polos ≤ 2.000), `efn_online_sequence`. |
| `sale.order` | `efn_online_order_id`; override privat `_action_cancel` → batal dari UI Odoo = event `cancelled` (`initiator=odoo`) + nota kredit bila sudah difaktur. |
| `efn.online.handover.wizard` | serah terima: verifikasi kode → validasi picking standar → `picked_up`. |

Semua model ber-`company_id`, `_check_company_auto`, record rule `company_id in company_ids`; ACL di
`security/ir.model.access.csv`. Grup `group_online_channel` **tanpa satu pun ACL** (BFF tidak bisa CRUD dokumen).

## Metode RPC (semua di `RPC_ALLOWLIST` `efn_core/tests/test_rpc_surface.py`)
BFF (`/json/2/<model>/<metode>` lewat `odoo-rpc-gateway`): `efn.online.channel` → `efn_online_catalog`,
`efn_online_stock`, `efn_online_image`, `efn_online_quote`; `efn.online.order` → `efn_online_place`,
`efn_online_mark_paid`, `efn_online_cancel`, `efn_online_refund`, `efn_online_refund_settled`, `efn_online_status`,
`efn_online_set_invoice_party` (G21 E-39).
Staf (web client): `efn.online.order.action_efn_ready` (Inventory user), `action_efn_retry_events` (Sales manager),
`efn.online.handover.wizard.action_confirm` (Inventory user).

`efn_online_catalog` juga mengembalikan blok **`channel`** (docs/04 §10.17 L-11): `{ code, name, company_code,
currency, outlets: [{ code, public_name, pickup_enabled, prep_minutes, active }] }` — outlet dari
`efn.online.outlet` milik kanal (termasuk nonaktif, bertanda `active`), bukan dari `prices[]`, di setiap halaman.

`_efn_online_check_caller(channel_code)`: grup user teknis + user ∈ `channel.user_ids` + company kanal ∈ company
user (gagal = `AccessError` seragam, termasuk kanal tak dikenal) → kanal nonaktif = `efn_online:channel_inactive`
→ **context klien dibuang kecuali `lang` (`id_ID`/`en_US`)** → `sudo` + `with_company(kanal)`.
Error bisnis = `UserError("efn_online:<kode>")` persis (`price_changed`, `out_of_stock`, `amount_mismatch`,
`invalid_state`, `idempotency_conflict`, `not_found`, `invalid_input`, `channel_inactive`, `invoice_not_available`).

Alur: quote (`sale.order.new()`, tanpa tulis DB) → place (advisory lock + unik; hitung ulang, `quote_hash` beda →
`price_changed`; SO draft partner generik, `client_order_ref`=part_id, `origin`="Super-App <order_id>", gudang &
FP outlet; `expires_at` = now + `payment_timeout_minutes`) → mark_paid (FOR UPDATE; idempoten; intent lain →
`review`; nominal ≠ `amount_payable` → `review`; SO sudah batal → `refund_required: full`; stok kurang →
`stock_short` + `short_lines`, SO tetap draft; `action_confirm` dalam savepoint, total berubah [sale_loyalty] →
rollback + `review`; faktur + `invoice_cash_rounding_id` kanal + pembayaran `account.payment.register` di jurnal
titipan, gagal → `review`) → siap → serah terima. Cancel: ref tak dikenal → `{"order_state": "absent"}`; setelah
faktur → nota kredit penuh + `refund`. Cron: kedaluwarsa (2 menit), pengingat tidak diambil (1 jam, tanpa batal
otomatis), outbox (1 menit + `_trigger`).

## Secret & TLS (docs/04 §10.5, L-2)
Event `POST <efn.bff_url>/v1/pos/online/order-events` memakai helper `efn.bff.client` (https wajib, CA internal
`EFN_CA_FILE`, tanpa redirect, service key `EFN_BFF_API_KEY_FILE`) + `X-EFN-Signature: t=<unix>,v1=<hex
HMAC-SHA256(secret, "<t>." + rawBody)>`; secret dari file `EFN_ONLINE_EVENT_HMAC_FILE` (default
`/run/secrets/odoo_online_event_hmac`, ≥ 32 karakter, dibaca per kirim). Secret tidak ada → **tidak dikirim**
(retry, `hmac_missing`); tidak ada fallback tanpa tanda tangan. Body tanpa PII/kode ambil (diuji).

## Kode ambil (delta §10.4.4 no. 1)
Diterima sekali di place (8 karakter alfabet §10.2), langsung di-hash pbkdf2-sha512 (passlib, putaran
`efn_online.pickup_code_kdf_rounds`, default 600 000; parameter hanya admin sistem), tidak masuk `payload_sha256`
maupun log; hash dihapus saat `picked_up`/`cancelled`. Wizard tidak melempar error saat kode salah agar penghitung
ter-commit; salah ke-5 → `review` + aktivitas manajer.

## Uji
`tests/` (tag `efn`, BFF di-mock): cek pemanggil (tanpa grup / bukan user kanal / company lain / kanal tak dikenal /
nonaktif), user teknis tanpa akses dokumen, context dibersihkan, katalog whitelist + paginasi + delta `deleted`,
stok/gambar, quote (tanpa tulis DB, Rupiah bulat, issues), place idempoten/konflik/`price_changed`/stok,
UniqueViolation → `ConcurrencyError`, kode ambil tidak tersimpan teks asli, mark_paid (faktur + titipan, idempoten,
nominal beda, intent kedua, kedaluwarsa, stok kurang, **sale_loyalty mengubah total → review**), cancel
(absent/sebelum/sesudah faktur), refund parsial kumulatif + settle + PT lain tak tersentuh, status, batal dari UI SO,
record rule multi-company, serah terima (5× salah → review, sukses → picking done), outbox (HMAC, tanpa PII, urutan
seq, 503/timeout retry, 409 final, secret hilang tidak terkirim, URL http ditolak), aksi staf.

Uji manual: Penjualan → Pesanan → **Pesanan online** (filter "Perlu disiapkan") → *Siap diambil* → *Serahkan
(kode ambil)*; konfigurasi di Penjualan → Konfigurasi → **Kanal online**.

## Review keamanan r10 (L-16, 10 Okt 2026)
- **SEC-95**: batal SO online dari UI Odoo untuk bagian yang sudah dibayar (`paid/confirmed/stock_short/ready/review`
  atau sudah ada pembayaran) hanya `sales_team.group_sale_manager` + field **Alasan batal (online)** (min. 10
  karakter; teks hanya di chatter Odoo). Event `cancelled` membawa `refund.initiator=odoo` + `cancel_reason: "ops"`
  (kode, tanpa teks bebas) -> BFF tidak me-refund otomatis (L-16). Bagian yang barangnya sudah keluar tidak bisa
  dibatalkan. Picking keluar SO online hanya bisa divalidasi lewat wizard serah terima: `stock.picking._action_done`
  menolak tanpa penanda di **cache transaksi cursor** yang diisi `_efn_handover` setelah kode valid (context klien
  tidak berlaku). Perilaku UI CE 19: tombol *Cancel* SO hanya dialog konfirmasi JS generik ("Are you sure…"),
  tanpa cek faktur/grup; CE hanya membatalkan faktur draf (faktur ter-posting tetap), juga lewat wizard mass-cancel
  -> semua jalur lewat `_action_cancel` yang di-hook.
- **SEC-99**: hash kode ambil dihapus saat `picked_up`, `cancelled`, masuk `review`, dan oleh cron (bagian
  `confirmed/ready` > `efn_online.pickup_code_max_days`, default 7 hari sejak siap/dibayar) + aktivitas staf;
  serah terima sesudahnya = verifikasi manual manajer (di luar Odoo).
- **SEC-101**: user teknis kanal hanya boleh `base.group_user` + `group_online_channel` (+ implied keduanya);
  ditegakkan di kanal, `res.users` (grup/company berubah), dan `res.groups` (implied/user berubah).
- **SEC-103**: wizard serah terima tidak melempar exception setelah membaca kode (akses ditolak/kode salah/validasi
  gagal -> pesan), sehingga `code` selalu dikosongkan dan ter-commit.

## Re-review G17 (SEC-104/105, L-19)
- **SEC-104**: tidak ada lagi penanda internal berbasis context. Konfirmasi/batal dari saga ditandai lewat
  `internal_scope(env, CONFIRM_SCOPE|CANCEL_SCOPE, so_ids)` di `cr.cache` (hanya kode server di transaksi yang sama,
  dibersihkan di `finally`). `efn_online_internal` dari context RPC diabaikan. `sale.order.write({'state': ...})`
  pada SO online di luar scope ditolak. `_efn_handover` menolak (order -> `review`, kode `handover_no_picking`)
  bila SO bukan `sale`, tidak punya move ke pelanggan yang masih terbuka, atau sebagian sudah keluar; sukses hanya
  bila semua move pelanggan `done` di dalam wizard.
- **SEC-105**: guard serah terima pindah ke `stock.move._action_done`: move ber-`sale_line_id` SO online menuju
  lokasi `customer`/`transit` (`location_dest_id` atau `location_final_id`) hanya boleh selesai dalam
  `HANDOVER_SCOPE` (diisi `_efn_handover` setelah kode valid). Tertangkap: ubah `picking_type_id`, pindah move ke
  picking manual, backorder/parsial, batch/mass validate. `pos.order.line.create` menolak `sale_order_origin_id` SO
  online (settle/uang muka `pos_sale`). Modul jembatan auto-install **`efn_online_pos_sale`** (bila `pos_sale`
  terpasang) mengeluarkan SO online dari data POS dan menolak `load_sale_order_from_pos`.
- Audit pola serupa di modul ini: context yang tersisa hanya `lang` (whitelist, dibersihkan di caller check),
  `ir_cron_progress_id` (metode cron privat, tidak RPC) dan context standar Odoo (`send_email=False`,
  `skip_backorder`, `mail_create_nosubscribe`) yang tidak membuka guard.

## Re-review akhir r10 (SEC-110)
- `stock.move.write` menolak perubahan `sale_line_id`, `location_dest_id`, `location_final_id`, `picking_type_id`,
  `picking_id` pada move milik SO online, juga memasang move lain ke baris SO online. `stock.move.line.write`
  menolak `location_dest_id`, `picking_id`, `move_id`. `stock.picking.write` menolak `location_dest_id`,
  `picking_type_id` (field move computed-stored ikut berubah lewat picking).
- Pengecualian hanya jalur internal E-28 di `cr.cache` (konfirmasi, batal, serah terima: `in_any_scope`); context
  diabaikan. Tulis nilai yang sama (no-op) dan field lain tetap boleh. Reservasi standar tidak menulis field ini.
- Guard `stock.move._action_done` (SEC-105) tetap sebagai jaring kedua (diuji dengan perubahan langsung DB).

## Belum / batasan
- `/json/2` nyata lewat `odoo-rpc-gateway` + API key belum diuji end-to-end (HttpCase bearer menggantung di harness,
  catatan SEC-58); status HTTP per exception json2 19.0 [B].
- Dua place paralel di dua cursor nyata tidak diuji (TransactionCase); jalur UniqueViolation → `ConcurrencyError`
  diuji dengan simulasi.
- Delivery: ditolak constraint (Q-ON3). Reservasi (`confirm`) didukung sejak 19.0.1.2.0; kasir POS tetap bisa
  menjual stok tercadang sampai pesanan "siap" (risiko diterima L-27, SOP Q-F19-2).
- G19 [B]: perilaku `sale_loyalty` 19.0 diverifikasi dari source (WebFetch), bukan runtime: penghitungan
  `total_order_count` (kuota), pengembalian poin kupon saat batal, persaingan dua diskon global (kode + otomatis —
  kode tercatat `applied` walau reward-nya diganti diskon global yang lebih baik), dan `_free_reservation` saat
  penyesuaian inventaris. Refund parsial per baris pada SO berpromo tidak mengalokasikan diskon order ke baris.
  Constraint opsional "batas bayar ≥ parameter BFF + 10" (docs/08 §10.1 no. 5) tidak dibuat. Buy X get Y + PB1
  exclude diuji dengan fixture lokal (pajak `PB1 uji`), belum dengan data `efn_setup_company` + fiscal position kota.
- r12: kunci kuota L-40 belum diuji dengan dua koneksi nyata (TransactionCase hanya membuktikan UPDATE terjadi
  sebelum promo); perlu skrip staging dua cursor. Pemakaian kuota di luar place online (SO manual staf) tidak
  mengunci baris program. Reward produk yang qty-nya dinaikkan `_update_programs_and_rewards` setelah klaim tidak
  dicek ulang terhadap stok (mode confirm: tertangkap reservasi → `out_of_stock`). `max_line_qty` tidak menghitung
  qty baris reward. Batas akumulatif per member lintas order = BFF (SEC-131 rekomendasi 2).
- Refund `restock`: hanya aktivitas ke staf gudang (retur picking standar manual). Pembulatan per nota kredit parsial
  bisa membuat Σ refund melebihi porsi Rp1 → ditolak `amount_mismatch` [B].
- `review` diselesaikan lewat BFF (`efn_online_cancel reason=review`) atau admin; tidak ada tombol "lanjutkan" di
  Odoo (di luar 13 metode yang disetujui).
- `invoice_on_paid=False` → tidak ada pembayaran tercatat di Odoo. Produk `invoice_policy=delivery` → `review`
  (`nothing_to_invoice`).
- Otomasi jurnal Holding/settlement (ADR-019 A2) ditunda; titik ganti tunggal `_efn_register_payment`.
- Event `catalog.changed` (push hint) = fase 3b.
