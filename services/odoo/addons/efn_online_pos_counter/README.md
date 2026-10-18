# efn_online_pos_counter (E-45) — bayar pesanan online di kasir POS

> **Ringkasan manajemen.** Selama vendor payment belum dipilih, pelanggan bisa memesan di Super-App lalu **membayar
> di kasir** toko (tunai/EDC/QRIS statis) dengan menunjukkan **kode bayar** (berbeda dari kode ambil barang). Kasir
> mengetik kode, POS menampilkan nomor pesanan dan nominalnya saja (tanpa data pelanggan), lalu kasir menerima uang.
> Odoo otomatis mengonfirmasi pesanan, membuat faktur (pajak hanya di faktur), dan mencatat pelunasan di jurnal
> kliring PT sehingga saldo kliring kembali nol. Barang tetap diserahkan lewat kode ambil (tidak berubah).

- Versi `19.0.1.0.0`, LGPL-3, `depends`: `efn_online_order` (≥ 19.0.1.5.0), `point_of_sale`; **auto_install**. Tanpa
  `pos_sale` (settle SO di POS sengaja ditolak, docs/08 §11.8), tanpa modul pihak ketiga (ADR-031).
- Kontrak: `docs/04_SERVICE_CONTRACTS.md` §12.2 + **L-50** (satu PT per pesanan, kode bayar ≠ kode ambil, lookup tanpa
  data pelanggan), **L-52**, **L-55** (counter tidak memakai titipan Holding), **L-56** (kode bayar dibuat BFF, Odoo
  menyimpan digest saja; bayar setelah batal = `review`). Spesifikasi: `docs/08_ODOO_FIT_GAP.md` §11.8 (W-30).
- ADR: ADR-034 (*Proposed*), ADR-019 (*Proposed*; titipan A1 hanya untuk `qris`), ADR-027 (tanpa tarif pajak di kode),
  ADR-031. Risiko yang dimitigasi: R-T10 (saga bayar), R-O05 (refund per SO), R-B06 (penyalahgunaan diskon/nominal di
  kasir), SEC-105 (barang online keluar di luar serah terima).
- Referensi desain: hanya pola internal `efn_pos_refund_origin` (E-41: RPC + jejak + rate limit) dan
  `efn_pos_loyalty_ledger` (validasi sync E-30). Tidak ada kode OCA/Cybrosys yang dipelajari/disalin.

## Komponen
| Bagian | Isi |
|---|---|
| `res.company` | `efn_online_counter_product_id` (produk layanan *Pelunasan Pesanan Online*, tanpa pajak, `available_in_pos=False`, akun pendapatan = **akun kliring**), `efn_online_counter_journal_id` (jurnal Bank *Kliring Bayar di Kasir*, metode masuk & keluar `payment_account_id` = akun kliring). Dibuat **lazy** (`_efn_online_counter_ensure`) saat kanal company itu diberi metode `counter`; akun `1199xx` `asset_current`, jurnal `KKSR`. Semua **PLACEHOLDER** — Finance meninjau per PT. |
| `efn.online.channel` (inherit) | create/write dengan `counter` → siapkan data PT; constraint: `counter` hanya bila PT siap. |
| `efn.online.order` (inherit) | `_efn_payment_journal` → jurnal kliring PT untuk order `counter`. |
| `pos.config` | `efn_online_counter_enabled` (PT siap + kanal `counter` aktif dengan outlet di gudang config), `efn_online_counter_product_id` (dimuat ke POS); produk pelunasan = *special product* (dimuat walau tidak tampil); RPC `efn_online_counter_lookup`. |
| `pos.order.line` | `efn_online_order_label` (nomor SO dari lookup; bukan PII). |
| `pos.order` | validasi sync + jalur internal `mark_paid` (lihat bawah). |
| `efn.online.counter.lookup` | jejak setiap lookup (company, config, sesi, kasir, pesanan bila cocok, hasil) — rate limit + klaim; tanpa kode. Retensi 180 hari. |
| `efn.online.counter.settlement` | tautan pesanan online ↔ order POS pelunasan (unik per pesanan & per uuid) + refund POS-nya. |

Model baru ber-`company_id`, `_check_company_auto`, record rule `company_id in company_ids`, ACL baca saja (POS
manager; pelunasan juga Sales manager & Billing); ditulis sudo oleh server setelah cek.

## RPC `pos.config.efn_online_counter_lookup(code)` (`RPC_ALLOWLIST`)
Dipanggil `[[config_id], code]`. Cek: grup POS user, `check_access('read')` config, company config ∈ company user,
sesi POS terbuka, POS mengaktifkan fitur. Rate limit: **5** lookup gagal per **sesi** atau per **kasir** dalam
**15 menit** (`efn_online_counter.lookup_max_failures` / `lookup_window_minutes`) → `rate_limited` (kode benar pun
ditolak sampai jendela lewat). Kode dinormalisasi (huruf besar, buang spasi/strip) → digest KDF per company → pesanan
`counter` company config **dan** outlet pesanan = gudang config. Hasil sukses **persis** `{order_label,
amount_payable}`; gagal `{error_code, message}` dengan `invalid_format` | `not_found` (kode salah, PT lain, outlet
lain — tidak dibedakan) | `not_payable` (kedaluwarsa/belum dibayar tapi lewat batas) | `rate_limited` | `disabled`.
Kode tidak pernah disimpan/di-log (diuji).

## Sync order POS (`_process_saved_order`, tidak draft)
**Pelunasan** — ditolak (`UserError`, sync rollback) kecuali: tepat satu baris = produk pelunasan PT order POS, qty 1,
diskon 0, tanpa pajak; `price_unit` = subtotal = subtotal incl = total order = `amount_payable` (toleransi 0); tanpa
pelanggan & `to_invoice`; label → pesanan `counter` company sama, outlet = gudang config; ada lookup `ok` untuk config
+ pesanan itu; pesanan belum dilunasi order POS lain. Setelah order POS lunas → `efn.online.order._efn_counter_mark_paid`
(`payment_ref` = `pos.order.uuid`, idempotensi `pos_counter:<uuid>`) → alur `mark_paid` biasa: stok → konfirmasi →
faktur → pembayaran di **jurnal kliring** → `confirmed` | `stock_short` | `review`; event `paid` membawa
`payment: {"method": "counter"}`. **L-56:** pesanan sudah `cancelled` atau `placed` lewat `expires_at` saat sync →
pembayaran dicatat + `review` (`counter_paid_after_cancel` / `counter_paid_after_expiry`), tanpa faktur; dana kembali
lewat refund POS setelah BFF membatalkan. Sync ulang uuid sama = diabaikan core (idempoten); uuid lain untuk pesanan
yang sama = ditolak.

**Kliring bersih 0:** penutupan sesi POS mengkredit akun kliring (pendapatan produk pelunasan) sebesar pelunasan;
pembayaran faktur SO di jurnal kliring mendebit akun yang sama. Pajak hanya di faktur SO.

**Refund (§12.2 no. 8)** — refund order POS pelunasan (tiket → refund, satu baris qty −1, seluruh nominal, sekali)
hanya bila: (a) Σ refund online `requested` (nota kredit dari alur CS + approver BFF) = nominal → refund itu di-settle
(pembayaran keluar jurnal kliring; kliring tetap 0), atau (b) pesanan `cancelled` tanpa faktur. Metode uang keluar
dijaga E-31 (`efn_pos_refund_origin`). BFF `efn_online_refund_settled` untuk order counter → `invalid_state`.

**SEC-105 tetap:** POS tidak memuat SO; baris pelunasan = produk layanan non-stok tanpa `sale_order_origin_id`; barang
keluar hanya lewat wizard serah terima kode ambil.

## Frontend POS (OWL, `point_of_sale._assets_pos`)
Tombol **Bayar pesanan online** (menu tombol lain; tampil bila `efn_online_counter_enabled`, **nonaktif saat
offline**) → order harus kosong & tanpa pelanggan → dialog kode bayar → RPC lookup → baris pelunasan
(`addLineToCurrentOrder`, `merge: false`) **terkunci** (patch `PosOrderline.setQuantity/setUnitPrice/setDiscount`;
hapus baris tetap boleh). Layar bayar: validasi order pelunasan/refund-nya diblokir saat offline, bila ada baris lain,
pelanggan, atau faktur POS.

## Uji
- `tests/test_online_pos_counter.py` (tag `efn`, `TestPoSCommon`; BFF tidak dipanggil — event hanya di outbox): data
  PLACEHOLDER per PT; place menyimpan digest saja (tidak dikembalikan, replay idempoten); aturan input place; BFF
  `mark_paid`/`refund_settled` ditolak; lookup → tepat 2 kunci; kode salah / PT lain / outlet lain → `not_found`;
  rate limit + reset setelah 15 menit; kode tidak di-log; kedaluwarsa/batal → ditolak; akses RPC; sync sukses
  (faktur dibayar di jurnal kliring, bukan titipan; event `paid` `payment.method=counter`; kliring = 0 setelah tutup
  sesi; baris tanpa `sale_order_origin_id`); nominal/qty/baris tambahan/pelanggan/label diubah → ditolak; tanpa klaim
  lookup → ditolak; dobel sync idempoten + order POS kedua ditolak; bayar setelah kedaluwarsa / batal → `review`;
  refund POS hanya setelah refund online / batal (kliring tetap 0).
- `tests/test_counter_tour.py` (tag `efn_tour`): tour `EfnOnlineCounterPayTour` — kode salah (pesan) → kode benar →
  baris pelunasan → tunai → struk tersinkron → pesanan `confirmed`.

Uji manual: Kanal online → *Metode bayar* `qris,counter` (data kliring PT dibuat otomatis, cek di Akuntansi) → place
lewat smoke BFF dengan `payment_mode: counter` → buka POS di gudang outlet → *Bayar pesanan online* → ketik kode →
bayar tunai → pesanan online `Dikonfirmasi`, faktur lunas di jurnal *Kliring Bayar di Kasir*; tutup sesi → saldo akun
kliring 0.

## Batasan / belum
- Akun/jurnal/produk = PLACEHOLDER (kode akun `1199xx`, jurnal `KKSR`); Finance wajib meninjau per PT sebelum go-live.
  Mengganti akun kliring di produk tanpa mengganti `payment_account_id` jurnal membuat kliring tidak lagi 0.
- Refund **parsial** pesanan counter lewat POS belum didukung (baris pelunasan qty 1 → refund POS selalu penuh);
  refund online parsial tetap tercatat `requested` → SOP Finance (kas keluar manual) sampai desain lanjutan.
- Pesanan kanal `counter` dengan `invoice_on_paid=False` ditolak constraint (kliring butuh faktur).
- Rate limit dihitung tanpa kunci baris: lookup paralel dari sesi yang sama bisa melewati batas beberapa kali (ruang
  kode 31⁸ ≈ 8,5·10¹¹ tetap tidak praktis di-brute force).
- Digest kode bayar deterministik per company (salt dari `database.secret`): pembaca dump DB lengkap bisa mencoba
  31⁸ × 210 000 iterasi PBKDF2 per company — tidak praktis, dan digest dihapus saat dibayar/batal/review (umur ≤ 70
  menit).
- Pemetaan POS ↔ outlet online memakai gudang (`picking_type_id.warehouse_id`); satu gudang = satu outlet per kanal.
- Perilaku frontend Odoo 19 (`addLineToCurrentOrder` dengan `price_unit`, penyembunyian *special product*, nama metode
  `PosOrderline`) diverifikasi dari source via WebFetch, belum dijalankan; tour menjadi buktinya.
- File `hooks.py` tidak dipakai (boleh dihapus).
