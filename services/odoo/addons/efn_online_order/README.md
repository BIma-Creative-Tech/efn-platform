# efn_online_order (E-28) — kanal penjualan online Super-App

> **Ringkasan manajemen.** Modul ini membuat Odoo bisa menerima pesanan dari Super-App **tanpa** membuka Odoo ke
> internet dan tanpa toko online bawaan Odoo. BFF memanggil 10 fungsi khusus; Odoo menghitung harga & pajak,
> membuat pesanan penjualan per PT, membuat faktur saat dibayar, dan memberi kabar status (siap/diambil/batal)
> ke BFF dengan pesan bertanda tangan. Kode ambil pelanggan hanya disimpan sebagai hash. Semua angka (batas bayar
> 25 menit, batas jumlah, pembulatan) adalah konfigurasi kanal, bukan kode.

- Versi `19.0.1.0.0`, LGPL-3, `depends`: `sale_stock`, `point_of_sale` (hanya `pos.category`), `mail`, `efn_core`.
  Tanpa `website*`, `delivery`, `payment_*`, tanpa modul pihak ketiga (ADR-031; `check-odoo-deps.sh`).
- Kontrak: `docs/04_SERVICE_CONTRACTS.md` §10 (v5.0) — **delta §10.4.4 + keputusan L-1..L-7 menang atas
  `docs/08` §8.3**. Fit-gap: `docs/08` §8 (F-55..F-73).
- ADR: ADR-034 (*Proposed*, kanal = BFF + `/json/2` terfilter), ADR-019 (*Proposed* + catatan revisi; dana titipan
  A1), ADR-026 (provider `mock`), ADR-027 (pajak dari Odoo, tanpa tarif di kode), ADR-029 (tanpa PII), ADR-024
  (kredensial per PT), ADR-031. Risiko: R-T10 (saga), R-O05 (refund per SO), R-L03 (pajak per PT), R-T15 (key
  kedaluwarsa — `api_key_duration` di `efn_setup_online`), R-S03 (Odoo privat).
- Referensi desain (tidak disalin, tidak di-WebFetch): pola internal `efn_pos_loyalty_ledger` (outbox) dan
  `efn_pos_payment_gateway`; OCA/sale-workflow `sale_order_type` hanya konsep "kanal = record konfigurasi".

## Model
| Model | Isi |
|---|---|
| `efn.online.channel` | kanal per PT/brand (`code` unik, pricelist, partner generik tanpa HP/email/NPWP, tim, `cash_rounding_id`, `journal_titipan_id`, cek stok `free_qty` + buffer + ambang "low", `payment_timeout_minutes` 25, batas baris/qty/nominal, `invoice_on_paid`, `user_ids` user teknis [hanya `base.group_system`], `pickup_max_attempts` 5, `pickup_reminder_hours` 48). Delivery & reservasi stok ditolak constraint (belum didukung). |
| `efn.online.outlet` | outlet per kanal (`code` unik per kanal, gudang, fiscal position outlet — wajib bila kanal `require_outlet_fiscal_position`, tidak boleh auto-apply). |
| `efn.online.order` | satu bagian = satu SO: refs BFF (`platform_order_ref`=part_id, `checkout_ref`=order_id), `idempotency_key` + `payload_sha256` + `quote_hash`, state `placed/paid/confirmed/stock_short/ready/picked_up/cancelled/review`, nominal, `pickup_code_hash` (pbkdf2-sha512), `event_seq`. Tanpa partner/HP/nama/alamat. |
| `efn.online.refund` | refund per order: nota kredit standar (reversal faktur + qty disesuaikan), kumulatif ≤ dibayar, `refund_ref` UUID, settle → pembayaran keluar jurnal titipan. |
| `efn.online.event.outbox` | event ke BFF (`seq` monoton, kunci `online:<id>:<event>:<seq>`), retry 1 m → 1 j, final pada 400/401/403/404/409/422, umur > 48 j → gagal + aktivitas. |
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

## Belum / batasan
- `/json/2` nyata lewat `odoo-rpc-gateway` + API key belum diuji end-to-end (HttpCase bearer menggantung di harness,
  catatan SEC-58); status HTTP per exception json2 19.0 [B].
- Dua place paralel di dua cursor nyata tidak diuji (TransactionCase); jalur UniqueViolation → `ConcurrencyError`
  diuji dengan simulasi.
- `stock_hold_mode=confirm` (reservasi) dan delivery: ditolak constraint (Q-ON3/Q-ON4).
- Refund `restock`: hanya aktivitas ke staf gudang (retur picking standar manual). Pembulatan per nota kredit parsial
  bisa membuat Σ refund melebihi porsi Rp1 → ditolak `amount_mismatch` [B].
- `review` diselesaikan lewat BFF (`efn_online_cancel reason=review`) atau admin; tidak ada tombol "lanjutkan" di
  Odoo (di luar 13 metode yang disetujui).
- `invoice_on_paid=False` → tidak ada pembayaran tercatat di Odoo. Produk `invoice_policy=delivery` → `review`
  (`nothing_to_invoice`).
- Otomasi jurnal Holding/settlement (ADR-019 A2) ditunda; titik ganti tunggal `_efn_register_payment`.
- Event `catalog.changed` (push hint) = fase 3b.
