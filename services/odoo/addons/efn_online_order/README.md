# efn_online_order (E-28) — kanal penjualan online Super-App

> **Ringkasan manajemen.** Modul ini membuat Odoo bisa menerima pesanan dari Super-App **tanpa** membuka Odoo ke
> internet dan tanpa toko online bawaan Odoo. BFF memanggil 10 fungsi khusus; Odoo menghitung harga & pajak,
> membuat pesanan penjualan per PT, membuat faktur saat dibayar, dan memberi kabar status (siap/diambil/batal)
> ke BFF dengan pesan bertanda tangan. Kode ambil pelanggan hanya disimpan sebagai hash. Semua angka (batas bayar
> 70 menit, batas jumlah, pembulatan) adalah konfigurasi kanal, bukan kode. Sejak G19 pesanan online bisa
> **mencadangkan stok** sejak checkout, menerima **kode promo** (promo otomatis per brand ikut dihitung Odoo), dan
> setiap kanal punya **kode brand**.

- Versi `19.0.1.3.0` (putaran perbaikan r12, lihat bagian di bawah), LGPL-3, `depends`: `sale_stock`, `sale_loyalty` (CE, mesin promo E-36; baru di 19.0.1.2.0 —
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

## Metode RPC (13, semua di `RPC_ALLOWLIST` `efn_core/tests/test_rpc_surface.py`)
BFF (`/json/2/<model>/<metode>` lewat `odoo-rpc-gateway`): `efn.online.channel` → `efn_online_catalog`,
`efn_online_stock`, `efn_online_image`, `efn_online_quote`; `efn.online.order` → `efn_online_place`,
`efn_online_mark_paid`, `efn_online_cancel`, `efn_online_refund`, `efn_online_refund_settled`, `efn_online_status`.
Staf (web client): `efn.online.order.action_efn_ready` (Inventory user), `action_efn_retry_events` (Sales manager),
`efn.online.handover.wizard.action_confirm` (Inventory user).

`efn_online_catalog` juga mengembalikan blok **`channel`** (docs/04 §10.17 L-11): `{ code, name, company_code,
currency, outlets: [{ code, public_name, pickup_enabled, prep_minutes, active }] }` — outlet dari
`efn.online.outlet` milik kanal (termasuk nonaktif, bertanda `active`), bukan dari `prices[]`, di setiap halaman.

`_efn_online_check_caller(channel_code)`: grup user teknis + user ∈ `channel.user_ids` + company kanal ∈ company
user (gagal = `AccessError` seragam, termasuk kanal tak dikenal) → kanal nonaktif = `efn_online:channel_inactive`
→ **context klien dibuang kecuali `lang` (`id_ID`/`en_US`)** → `sudo` + `with_company(kanal)`.
Error bisnis = `UserError("efn_online:<kode>")` persis (`price_changed`, `out_of_stock`, `amount_mismatch`,
`invalid_state`, `idempotency_conflict`, `not_found`, `invalid_input`, `channel_inactive`).

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
