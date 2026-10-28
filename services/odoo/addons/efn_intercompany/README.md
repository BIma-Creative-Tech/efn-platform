# efn_intercompany — transaksi antar-PT (G28)

**Tujuan.** Odoo CE 19 tidak membuat dokumen lawan antar-company (aturan inter-company = Enterprise). Modul ini
menutup gap F-03/W-15 (`docs/08` §5.2) tanpa modul pihak ketiga (ADR-031).

## Alur
1. **PO → SO.** PO di PT pembeli dengan partner = partner company PT lain, saat masuk state `purchase`, membuat SO di
   PT penjual bila `efn_ic_so_from_po` aktif di PT penjual. SO dibuat **sebagai user teknis PT penjual**
   (`efn_ic_user_id`, grup *User teknis antar-PT*), bukan `sudo` penuh. Tautan dua arah, idempoten (UNIQUE).
2. **Konfirmasi otomatis** (`efn_ic_auto_confirm_so`) → pengiriman/produksi standar PT penjual (mis. CK membuat MO).
3. **Faktur → tagihan.** Faktur PT penjual yang barisnya berasal dari SO antar-PT, saat di-post, membuat **Vendor Bill
   DRAFT** di PT pembeli (`efn_ic_bill_from_invoice`). Baris dibangun dari baris PO asal → pajak pembelian PT pembeli
   dan `purchase_line_id` terisi (3-way match standar). Selisih total → catatan + aktivitas.
4. **Pembatalan.** PO batal → SO draft batal (SO terkonfirmasi: aktivitas). Faktur batal/di-reset → tagihan draft batal
   (tagihan posted: aktivitas; tanpa reverse otomatis).
5. **Harga transfer cost-plus** (`efn.ic.transfer.price`): per pasangan PT penjual→pembeli (+ kategori opsional).
   Harga = `standard_price` produk di PT penjual × (1 + markup). Diterapkan saat PO dikonfirmasi; pasangan tanpa aturan
   memakai harga PO. Menu: Accounting > Configuration > Harga Transfer Antar-PT.

## Konfigurasi minimum per PT
Settings > Companies > tab **Antar-PT EFN**: user teknis (internal, satu company, grup *User teknis antar-PT*), centang
peran penjual/pembeli, gudang SO (mis. Central Kitchen).

## Terkait
ADR-001 (satu DB multi-company), ADR-031, `docs/08` §5.2, `docs/10` W-15, keputusan 18-3 (TP hanya Bacha F&B ↔ Retail
dan Holding ↔ PT). Referensi desain (tidak disalin): OCA `purchase_sale_inter_company`, `account_invoice_inter_company`.

## Belum
- Sinkron qty terkirim ↔ qty diterima otomatis (fase 1b); penerimaan di PT pembeli tetap dokumen stok standar.
- Netting/settlement pembayaran antar-PT (manual standar).
- **Markup final dan opini pajak harga transfer** (Finance + konsultan pajak) — angka demo adalah PLACEHOLDER.
