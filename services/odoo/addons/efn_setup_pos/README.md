# efn_setup_pos

Modul konfigurasi untuk POS **retail (PT Retail)** dan **restoran (PT F&B)** di Odoo CE 19. Isinya **bundel dependensi** (modul CE yang benar) + data konfigurasi objektif + data demo; pengaturan `pos.config` per outlet dilakukan lewat UI mengikuti checklist di bawah karena bergantung keputusan klien.

- **ADR:** ADR-013 (printer dapur dulu, KDS custom fase 2, kiosk *pay after meal*), ADR-017 (promo/poin ber-OTP mati saat offline), ADR-020 (poin member di ledger BFF, bukan Odoo), ADR-026 (gateway-agnostic), ADR-031 (tanpa modul pihak ketiga). Semua *Proposed*.
- **Fit-gap:** `docs/08_ODOO_FIT_GAP.md` F-04..F-11, F-23, **§6 Fase 1 pilot retail (F-31..F-40)**.
- **Risiko dimitigasi:** R-O05 (void/refund), R-O06 (cash management), R-B06/SEC-12 (fraud diskon/harga kasir), R-O02 (migrasi, lihat `docs/templates/migrasi/`).

## Modul yang di-install (semua terverifikasi ada di 19.0)
`point_of_sale`, `pos_restaurant`, `pos_self_order`, `pos_loyalty` (promo, kupon, gift card), `pos_mrp`, `pos_hr` (login kasir per karyawan + PIN, peran), `pos_discount` (diskon global), dan otomatis `l10n_id_pos`.

## Data (`data/`, noupdate, berlaku di semua DB)
| File | Isi | Sumber 19.0 |
|---|---|---|
| `pos_config_defaults.xml` | `ir.default` pos.config baru: `restrict_price_control = True`, `manual_discount = False` | `pos_config.py` |
| `pos_retail_defaults.xml` (G13) | `ir.default`: `order_edit_tracking = True` (void baris pada order tersinkron dicatat), `set_maximum_difference = True` + `amount_authorized_diff = 0` (setiap selisih kas penutupan butuh supervisor); `ir.config_parameter point_of_sale.limited_product_count = 20000` (**PLACEHOLDER**, default CE 5000; produk di luar batas tidak bisa di-scan saat offline) | `pos_config.py` (`get_limited_product_count`), `closing_popup.js`, pos_hr `closing_popup.js` |
| `pos_audit_filters.xml` | 7 filter tersimpan: diskon baris tanpa program, harga manual, harga manual non-timbang (G13), baris negatif non-promo (E-24: dinonaktifkan & diganti versi tanpa baris "Poin EFN" bila `efn_pos_loyalty_ledger` terpasang), **refund tanpa struk** (G13), **order refund per kasir** (G13), **order diedit/void baris** (G13) | `ir.filters`; `pos.order(.line)` |
| `pos_payment_method_data.xml` | *Kartu Debit/Kredit (EDC manual)* RTL & FNB. Tunai dibuat otomatis per outlet. QRIS `id_qr` tidak dikonfigurasi (pakai `efn_pos_payment_gateway`) | `pos_payment_method.py` |
| `barcode_nomenclature_data.xml` (G13) | Nomenklatur **EFN Retail (IDR)**, dipasang ke PT Retail: `23.....{NNNNN}` harga Rupiah bulat, `21.....{NNDDD}` berat kg, `042` pelanggan, `041` karyawan, `043\|044` kupon, `.*` produk. **Tanpa** aturan diskon `22{NN}` (vektor fraud: memberi diskon tanpa cek peran/`manual_discount`) dan tanpa aturan lot `10` (salah baca EAN impor) | `barcodes`, `point_of_sale`/`stock`/`pos_loyalty` `default_barcode_patterns.xml`, `product_screen.js` |
| `pos_bill_idr_data.xml` (G13) | Pecahan Rupiah (Rp100..Rp100.000) untuk hitung kas buka/tutup; 13 pecahan USD bawaan dihapus (`forcecreate="0"` → tidak muncul lagi saat upgrade) | `pos_bill.py`, `point_of_sale_data.xml` |

**Catatan upgrade:** semua blok `noupdate` (termasuk `<function>` dan penetapan nomenklatur PT Retail) hanya dijalankan saat **install**. DB yang sudah memasang `efn_setup_pos` 19.0.1.1.0: filter audit baru ikut dibuat saat `-u`, tetapi default/nomenklatur/pecahan harus diterapkan manual (Settings POS) atau DB pilot di-install bersih.

## Demo (`demo/`, hanya DB demo)
- `pos_demo.xml` (F&B): kategori stasiun dapur, 2 printer ePOS (IP placeholder), denah meja.
- `pos_retail_demo.xml` (G13): kategori *Fresh (Timbang)*; *Tomat Merah per kg* (barcode berat `2100001000004`, label 1,250 kg = `2100001012502`); *Daging Sapi* berlabel harga (`2300002000007`, label Rp45.500 = `2300002455005`); pricelist *Harga Member (DEMO)* 5% (PLACEHOLDER).

## SOP kasir retail — apa yang standar CE 19 (terverifikasi source 19.0, 2026-10-07)

### Peran `pos_hr` (pengganti "approval supervisor")
`pos.config.minimal_employee_ids` / `basic_employee_ids` / `advanced_employee_ids`; karyawan yang user-nya POS Manager otomatis *manager*. Login karyawan di POS memakai **PIN** (`hr.employee.pin`) atau kartu barcode `041`.

| Aksi | minimal | basic (kasir) | manager (supervisor) | Sumber |
|---|---|---|---|---|
| Ubah harga | tidak | tidak bila `restrict_price_control` | ya | `pos_store.cashierHasPriceControlRights`, pos_hr `order_summary.js` |
| Diskon baris % | tidak | hanya bila `manual_discount` | hanya bila `manual_discount` | `product_screen.js` |
| Tombol `-` (qty negatif = retur tanpa struk) | tidak | ya | ya | pos_hr `order_summary.js` |
| Refund dari daftar order (dengan struk) | tidak (panel refund disembunyikan) | ya | ya | pos_hr `ticket_screen.xml` |
| Ubah pembayaran order lama | tidak | tidak | ya | pos_hr `canEditPayment` |
| Tutup sesi dengan selisih > batas | tidak | tidak | ya (user POS Manager) | pos_hr `closing_popup.js` |
| Kas masuk/keluar | — | hanya user POS Manager atau grup *Invoicing* | ya | `res.users._has_cash_move_permission` |

**"Approval" di CE = supervisor mengambil alih kasir sementara**: ketuk nama kasir → pilih supervisor → PIN → lakukan aksi → kembali ke kasir. Tidak ada dialog persetujuan inline dan tidak ada catatan "disetujui oleh" per baris (order hanya mencatat `employee_id` pembuat). Bila klien butuh persetujuan inline + jejak per aksi → gap **G13-2** (`docs/08 §6`). Rekomendasi pilot: kasir = **minimal** (bila refund wajib supervisor, Q-27) atau **basic**; supervisor = **manager**.

Keamanan PIN: hash PIN karyawan dimuat ke browser POS (dipakai offline) → PIN 4 digit mudah ditebak oleh siapa pun yang memegang perangkat; PIN adalah kontrol operasional, **bukan** autentikasi kuat. Pakai PIN ≥ 6 digit, unik, ganti saat karyawan keluar; user Odoo terminal POS tetap ber-2FA (`efn_setup_security`).

### Retur/refund
- **Dengan struk:** *Orders* → cari order → pilih baris & qty → *Refund*. Order refund menyalin pelanggan, pricelist, fiscal position; `refunded_orderline_id` mencegah refund melebihi qty asal.
- **Tanpa struk:** tombol `-` pada qty (baris negatif tanpa order asal) → filter audit *Refund tanpa struk*. Matikan untuk kasir dengan peran **minimal**.
- **Ke metode bayar asal:** CE **tidak** memaksa metode bayar refund = metode asal (layar pembayaran menampilkan semua metode config). SOP + audit; penegakan sistem = gap **G13-4**. QRIS gateway tidak mendukung reversal (`supports_reversals = false`) → refund QRIS lewat tunai/transfer sesuai SOP Finance.
- **Poin member:** refund memicu clawback poin (gap `efn_pos_loyalty_ledger`, kontrak docs/04 §9.4).

### Void
- **Void baris sebelum bayar:** hapus baris (Backspace di numpad). Untuk order retail yang belum tersinkron **tidak ada jejak server** — `order_edit_tracking` hanya mencatat order yang sudah tersinkron (umumnya restoran). → gap **G13-3** bila klien mewajibkan log void.
- **Void order:** order draft dihapus dari daftar order (`pos.order.remove_from_ui` membatalkan lalu menghapus order draft tersinkron). Order berbayar tidak bisa di-void → harus refund.

### Cash control per shift (R-O06)
- Aktif otomatis bila metode **Tunai** (`is_cash_count`) ada di config (`cash_control` compute). Buka sesi → hitung kas awal per pecahan (`pos.bill`, data modul ini) → `cash_register_balance_start`. Kas masuk/keluar → *Cash In/Out* (alasan wajib diisi; hak di atas). Tutup → hitung per pecahan → selisih (`cash_register_difference`) diposting ke akun selisih jurnal kas (`loss_account_id`/`profit_account_id` jurnal tunai — **isi akun saat setup CoA**, [B]).
- **Laporan Z:** tombol *Daily Sale* di popup tutup sesi = laporan `point_of_sale.sale_details_report` (per sesi); backoffice *Reporting → Sales Details* untuk rentang tanggal.
- **Satu shift = satu sesi.** Ganti shift = tutup sesi + buka sesi baru (kasir berikutnya). Ganti kasir tanpa tutup sesi (pos_hr) tetap satu laci → selisih tidak bisa diatribusikan per kasir.

### Barcode berat/harga & timbangan (Q-25)
- Timbangan label (prepack): cetak label EAN-13 sesuai nomenklatur *EFN Retail (IDR)* → scan di kasir: `21` qty = berat, `23` harga = nilai label (baris `price_type = 'manual'`).
- Timbangan terhubung ke kasir: `pos.config.iface_electronic_scale` + `product.to_weight` → layar timbang; butuh IoT box/driver perangkat (**[B]**, Q-56).
- Barcode produk di master = base code (`21PPPPP00000C` / `23PPPPP00000C`), PLU 5 digit unik per toko/PT.

### Multi-UoM fresh
`product.template.uom_id` = kg untuk produk timbang (qty desimal); kemasan jual tambahan lewat `uom_ids` (*Packagings*) + barcode per kemasan di `product.uom` (`barcode`). Perilaku POS saat scan barcode kemasan (konversi qty) **[B]**.

### Lot/expiry & FEFO di POS
`efn_setup_mrp` memasang Lots, `product_expiry`, kategori *Perishable (FEFO)*. POS: produk ber-`tracking = 'lot'` membuka dialog lot saat ditambahkan (`editLots`) — friksi tinggi di kasir. Picking POS tanpa lot memakai `move.quantity = product_uom_qty` sehingga reservasi standar (removal strategy FEFO) yang memilih lot **[B]**. **Rekomendasi pilot:** produk fresh toko **tanpa** tracking lot (FEFO dijaga di gudang/CK); lot di kasir hanya untuk produk yang wajib ditelusuri (Q-34).

### Promo mekanik retail (Q-28) — `loyalty` + `pos_loyalty` + pricelist
| Mekanik | Konfigurasi standar |
|---|---|
| Buy X get Y | `program_type = 'buy_x_get_y'`, rule `minimum_qty`, reward `reward_type = 'product'` |
| Bundling | produk `type = 'combo'` (harga paket) atau promo `discount_applicability = 'specific'` |
| Tebus murah | `promotion`, rule `minimum_amount` (+ `minimum_amount_tax_mode`), reward diskon `specific` pada produk tebusan (`discount_mode = 'per_order'`/`percent`) |
| Diskon kuantitas | `product.pricelist.item.min_quantity` |
| Harga member | pricelist member (`property_product_pricelist` partner, `available_pricelist_ids`) |
| Harga promo periode | pricelist item `date_start`/`date_end` atau `promotion` ber-`date_from/date_to` |
| Cashback e-wallet | di luar Odoo (provider/BFF) |
Batasi per outlet dengan `pos_config_ids`; **jangan** `program_type = 'loyalty'`/`'ewallet'` untuk poin member (ADR-020). Promo ber-OTP: `efn_pos_otp_gate`.

### Struk & layar pelanggan
`receipt_header`/`receipt_footer` (*Custom Header & Footer*), `iface_print_auto`, `iface_print_skip_screen`, `basic_receipt`. Layar pelanggan = halaman *customer display* standar (latar `customer_display_bg_img`); cara membuka di monitor kedua **[B]**.

## Checklist `pos.config` outlet pilot retail (PT Retail)
| Field | Nilai usulan | Catatan |
|---|---|---|
| `company_id` | PT Retail | buat config saat company aktif = PT Retail |
| `module_pos_restaurant` | False | |
| `module_pos_hr` | True | + isi `minimal/basic/advanced_employee_ids` (Q-27) |
| `restrict_price_control` | True (default) | |
| `manual_discount` / `module_pos_discount` | False / False | diskon hanya lewat promo (Q-28) |
| `order_edit_tracking` | True (default G13) | |
| `set_maximum_difference` / `amount_authorized_diff` | True / 0 → nilai Q-27 | ditegakkan di frontend saja |
| `default_bill_ids` | kosong (pecahan IDR global) | |
| `cash_rounding` / `rounding_method` / `only_round_cash_method` | sesuai kebijakan pembulatan tunai (Q-29) | `account.cash.rounding` |
| `iface_tax_included` | `total` | harga rak termasuk PPN (Q-9) |
| `pricelist_id`, `available_pricelist_ids` | umum + member | Q-9 |
| `payment_method_ids` | Tunai (otomatis), EDC manual, QRIS (EFN Gateway) | QRIS mati saat offline |
| `iface_electronic_scale` | sesuai Q-25/Q-56 | |
| `limit_categories` / `iface_available_categ_ids` | opsional | |
| `iface_print_auto`, `iface_cashdrawer`, `epson_printer_ip` | sesuai perangkat (Q-56) | |
| `receipt_header` / `receipt_footer` | nama PT, NPWP, alamat, kebijakan retur | Q-1 |
| `default_fiscal_position_id` | kosong | PPN dari pajak produk |
| Company PT Retail → Barcode nomenclature | *EFN Retail (IDR)* (otomatis saat install) | |
| User terminal POS | tanpa *Contacts: Creation*, 2FA aktif | SEC-07 |
| Uji offline | buka sesi online → putus jaringan → scan 10 SKU, bayar tunai/EDC → online → order tersinkron | Q-55 |

## Checklist `pos.config` restoran (PT F&B)
| Field | Nilai usulan | Catatan |
|---|---|---|
| `module_pos_restaurant` | True | |
| `default_fiscal_position_id` | fiscal position kota outlet; kosong bila tarif = PB1 default | README `efn_setup_company` |
| `fiscal_position_ids` / `tax_regime_selection` | kosong / False | |
| `use_presets`, `available_preset_ids` | Q-18 | preset tanpa `company_id` |
| `payment_method_ids` | Tunai, EDC manual, QRIS (EFN Gateway) | |
| `floor_ids`, `iface_splitbill`, `iface_printbill`, `default_screen` | Q-20/Q-18 | |
| `is_order_printer`, `printer_ids` | Q-19, Q-24 | |
| `self_ordering_mode` / `_service_mode` / `_pay_after` | `mobile`/`kiosk`, `table`/`counter`, **`meal`** | ADR-013 |
| Promo OTP | program `efn_requires_otp` tidak ditautkan ke config kiosk | |

## Keputusan klien yang dibutuhkan
Q-7 (jumlah SKU → `limited_product_count`), Q-9, Q-25 (timbangan & format label), Q-27 (peran kasir, refund/void wajib supervisor?, toleransi selisih kas, pecahan), Q-28 (mekanik promo), Q-29 (metode bayar, pembulatan tunai, refund ke metode asal), Q-34 (lot di kasir), Q-55, Q-56 (perangkat, timbangan, laci, printer), toko pilot.

## Cara install
```bash
odoo -d <db> -i efn_setup_pos     # menarik efn_setup_company (4 company + CoA)
```

## Batasan / belum terverifikasi
- Belum di-install dengan data G13 (CI berikutnya). `<delete>` pecahan bawaan & penetapan nomenklatur di blok noupdate — perilaku saat install [B].
- Check digit barcode demo dihitung manual; scan nyata, timbangan, IoT, mode offline, ePOS, layar pelanggan: PoC perangkat.
- FEFO/lot di POS, scan barcode kemasan (`product.uom`), akun selisih kas jurnal tunai `l10n_id`: [B].
- `pos.config` tidak dibuat via data (default dihitung dari `env.company`).
- `restaurant.floor` 19.0 tanpa `company_id`.
