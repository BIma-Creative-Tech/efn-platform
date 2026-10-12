# efn_pos_refund_origin (E-31) — refund POS ke metode bayar asal

> **Ringkasan manajemen.** Uang refund di kasir hanya bisa dikembalikan lewat cara bayar yang dipakai pelanggan
> saat membeli (mis. dibayar kartu -> dikembalikan ke kartu), maksimal sebesar yang dibayar dengan cara itu. Retur
> tanpa struk hanya tunai. Mencegah kasir mengubah refund kartu menjadi uang tunai.

- **Fit-gap:** W-24 / E-31 (`docs/08 §9.3`). **Kontrak/keputusan:** `docs/04 §9.9` L-23 (Q-F18-4 proporsional,
  Q-F18-5 retur tanpa struk tunai saja, Q-F18-6 QRIS statis dicatat atas metode asal + transfer manual SOP Finance).
  ADR-031 (tanpa modul pihak ketiga; ide dari OCA/pos modul retur — tidak disalin, 19.0 belum diverifikasi).
- **Risiko dimitigasi:** R-O05 (retur/refund), R-B06 (fraud kasir: refund kartu dicairkan tunai).
- **Depends:** `point_of_sale`, `efn_core` (CE + efn_*). LGPL-3.

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
- Tour (`EfnPosRefundOriginTour`, tag `efn_tour`): order dibayar Bank -> refund -> hanya Bank ditawarkan, terisi otomatis.
- Manual: jual split tunai+kartu -> Orders -> refund sebagian -> cek alokasi & coba ganti metode (ditolak saat sync).

## Belum / batasan
- Exchange (refund + barang baru) dinilai pada pembayaran negatifnya saja.
- Order asal dari sesi/POS lain yang belum termuat saat offline: tanpa saringan klien; server tetap menolak.
- Persetujuan supervisor untuk retur tanpa struk (Q-F18-5 "hanya tunai + supervisor") belum ada (butuh keputusan
  Ops; kini cukup tunai).
