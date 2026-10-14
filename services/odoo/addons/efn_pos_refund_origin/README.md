# efn_pos_refund_origin (E-31) — refund POS ke metode bayar asal

> **Ringkasan manajemen.** Uang refund di kasir hanya bisa dikembalikan lewat cara bayar yang dipakai pelanggan
> saat membeli (mis. dibayar kartu -> dikembalikan ke kartu), maksimal sebesar yang dibayar dengan cara itu. Retur
> tanpa struk hanya tunai. Mencegah kasir mengubah refund kartu menjadi uang tunai.

- **Fit-gap:** W-24 / E-31 (`docs/08 §9.3`). **Kontrak/keputusan:** `docs/04 §9.9` L-23 (Q-F18-4 proporsional,
  Q-F18-5 retur tanpa struk tunai saja, Q-F18-6 QRIS statis dicatat atas metode asal + transfer manual SOP Finance).
  ADR-031 (tanpa modul pihak ketiga; ide dari OCA/pos modul retur — tidak disalin, 19.0 belum diverifikasi).
- **Risiko dimitigasi:** R-O05 (retur/refund), R-B06 (fraud kasir: refund kartu dicairkan tunai).
- **Depends:** `point_of_sale`, `efn_core` (CE + efn_*). LGPL-3. **`auto_install: True`** (SEC-122, versi 19.0.1.1.0).

## Review r11 (L-25a/d/e/j, versi 19.0.1.1.0)
- **SEC-122: terpasang otomatis.** Dipilih `auto_install` (bukan dependency `efn_setup_pos`): modul ikut terpasang di
  setiap DB yang punya `point_of_sale` + `efn_core` (semua DB POS EFN), tidak bergantung pada modul konfigurasi
  functional atau daftar instal. Kontrol E-31/SEC-113/116/117 tidak bisa absen diam-diam; menambah dependency di modul
  setup tetap mungkin kemudian tanpa konflik.
- **SEC-113: pembayaran order POS terkunci di luar sync** (`models/pos_payment.py`). ACL CE memberi kasir CRUD penuh
  `pos.payment` (terverifikasi `call_kw` kasir di Odoo 19 nyata: write metode/nominal, create, unlink pada order paid
  semuanya DITERIMA sebelum perbaikan). Kini `create`/`unlink`/`write` (`amount`, `payment_method_id`, `pos_order_id`,
  `is_change`) untuk order **bukan draft** ditolak (`UserError`), kecuali di jalur sync resmi
  (`pos.order._process_order`, penanda per cursor `efn_core/scope.py`; tidak bisa diisi context RPC) atau kode server
  ber-`sudo` (penutupan sesi, pembayaran online core). Order draft mengikuti alur standar (wizard pembayaran backend).
  Pembayaran Poin EFN lebih ketat lagi (juga draft) di `efn_pos_loyalty_ledger`.
- **SEC-116: `is_change` dihitung ulang server** (`_efn_recompute_change_flags`, sync draft & final, sebelum E-31).
  Sah hanya untuk pembayaran **tunai** negatif dengan kumulatif |kembalian| <= min(jumlah tender tunai positif, jumlah
  tender positif - total baris order), ditambah satu satuan pembulatan tunai bila config memakai cash rounding.
  Selebihnya `is_change = False` sehingga dinilai sebagai uang keluar (refund kartu yang "dikembalikan tunai bertanda
  kembalian" ditolak). Berlaku juga untuk baris kembalian yang dibuat core dari `amount_return` payload.
- **SEC-117: refund mengunci order asal** (`_efn_lock_origin`: `SELECT ... FOR UPDATE`) sebelum menghitung sisa per
  metode; refund paralel atas satu struk dari dua terminal diserialkan.

## Aturan server (saat sync order, `pos.order._process_saved_order`)
- Hanya pembayaran **negatif** (uang keluar, bukan baris kembalian `is_change`) yang dinilai.
- **Refund ber-struk** (baris `refunded_orderline_id`): setiap metode harus ada di order asal; nominal per metode ≤
  dibayar bersih di order asal (kembalian = pembayaran tunai negatif core) − refund tersinkron sebelumnya.
- **Retur tanpa struk** (barang qty < 0 tanpa asal): `efn.pos.refund.unlinked_return_mode` = `cash` (default, tunai
  saja) | `deny` (ditolak).
- Pelanggaran -> `UserError` (sync ditolak). Penjualan & tukar barang tanpa uang keluar tidak terpengaruh.
- QRIS statis / gateway tanpa reversal: dicatat atas metode asal; pengembalian dana fisik lewat transfer manual (SOP).
- Bersama E-30 (Poin EFN): metode Poin EFN diperlakukan sama (subset + batas); nilai poinnya divalidasi E-30.

## RPC & frontend
- `pos.config.efn_refund_origin_allocation(original_order_id, refund_amount)` (di `RPC_ALLOWLIST`): grup POS user +
  akses config + company + sesi terbuka; order asal dibaca dengan record rule. Mengembalikan alokasi proporsional
  kumulatif per metode (`amount`, `max_amount`).
- Layar bayar refund: hanya metode asal (retur tanpa struk: hanya tunai), nominal terisi otomatis; offline: disaring
  dari pembayaran order asal yang termuat, tanpa isi otomatis.

## Uji
- Unit (`tests/test_refund_origin.py`, tag `efn`): metode asal OK, metode lain ditolak, split + batas + kumulatif,
  kembalian tunai, retur tanpa struk (cash/deny), RPC & akses.
- Unit r11 (`tests/test_payment_integrity.py`, tag `efn`): SEC-113 (kasir & manajer POS ditolak ubah/buat/hapus
  pembayaran order paid, context tidak membuka jalur, draft & sudo tetap jalan), SEC-116 (is_change / amount_return
  palsu pada refund kartu ditolak, kembalian sah tetap), SEC-117 (kunci sebelum hitung sisa), SEC-122.
- RPC nyata r11 (`tests/test_payment_integrity_rpc.py`, tag `efn_tour`, `call_kw` sebagai kasir): SEC-113, SEC-116,
  dan probe ACL `res.partner.bank` untuk SEC-121 (milik `efn_setup_pos`, hanya dicatat di log).
- Tour (`EfnPosRefundOriginTour`, tag `efn_tour`): order dibayar Bank -> refund -> hanya Bank ditawarkan, terisi otomatis.
- Manual: jual split tunai+kartu -> Orders -> refund sebagian -> cek alokasi & coba ganti metode (ditolak saat sync).

## Belum / batasan
- Exchange (refund + barang baru) dinilai pada pembayaran negatifnya saja.
- Order asal dari sesi/POS lain yang belum termuat saat offline: tanpa saringan klien; server tetap menolak.
- SEC-117: kunci baris menyerialkan sync paralel; terverifikasi lewat spy (urutan kunci -> hitung sisa), belum lewat
  uji dua koneksi nyata (data uji tidak ter-commit sehingga tidak terlihat koneksi lain).
- Persetujuan supervisor untuk retur tanpa struk (Q-F18-5 "hanya tunai + supervisor") belum ada (butuh keputusan
  Ops; kini cukup tunai).
