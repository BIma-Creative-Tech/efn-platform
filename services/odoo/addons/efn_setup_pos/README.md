# efn_setup_pos

Modul konfigurasi untuk POS **retail (PT Retail)** dan **restoran (PT F&B)** di Odoo CE 19. Isinya **bundel dependensi** (modul CE yang benar) + data konfigurasi objektif + data demo; pengaturan `pos.config` per outlet dilakukan lewat UI mengikuti checklist di bawah karena bergantung keputusan klien.

- **ADR:** ADR-013 (printer dapur dulu, KDS custom fase 2, kiosk *pay after meal*), ADR-017 (promo/poin ber-OTP mati saat offline), ADR-020 (poin member di ledger BFF, bukan Odoo), ADR-026 (gateway-agnostic), ADR-031 (tanpa modul pihak ketiga). Semua *Proposed*.
- **Fit-gap:** `docs/08_ODOO_FIT_GAP.md` F-04..F-11, F-23, **§6 Fase 1 pilot retail (F-31..F-42)**, **§7 Fase 2 F&B/KDS/QR order (F-43..F-54, Gelombang 14)**.
- **Risiko dimitigasi:** R-O05 (void/refund), R-O06 (cash management), R-B06/SEC-12 (fraud diskon/harga kasir), R-O02 (migrasi, lihat `docs/templates/migrasi/`).

## Modul yang di-install (semua terverifikasi ada di 19.0)
`point_of_sale`, `pos_restaurant`, `pos_self_order`, `pos_loyalty` (promo, kupon, gift card), `pos_mrp`, `pos_hr` (login kasir per karyawan + PIN, peran), `pos_discount` (diskon global), dan otomatis `l10n_id_pos`.

## Data (`data/`, noupdate, berlaku di semua DB)
| File | Isi | Sumber 19.0 |
|---|---|---|
| `pos_config_defaults.xml` | `ir.default` pos.config baru: `restrict_price_control = True`, `manual_discount = False` | `pos_config.py` |
| `pos_retail_defaults.xml` (G13) | `ir.default`: `order_edit_tracking = True` (void baris pada order tersinkron dicatat), `set_maximum_difference = True` + `amount_authorized_diff = 0` (setiap selisih kas penutupan butuh supervisor); `ir.config_parameter point_of_sale.limited_product_count = 20000` (**PLACEHOLDER**, default CE 5000; produk di luar batas tidak bisa di-scan saat offline) | `pos_config.py` (`get_limited_product_count`), `closing_popup.js`, pos_hr `closing_popup.js` |
| `pos_audit_filters.xml` | 8 filter tersimpan: **label harga produk timbang (barcode `23`, SEC-63, 13c — dikelompokkan per produk untuk membandingkan harga satuan dengan harga jual)**, diskon baris tanpa program, harga manual, harga manual non-timbang (G13), baris negatif non-promo (E-24: dinonaktifkan & diganti versi tanpa baris "Poin EFN" bila `efn_pos_loyalty_ledger` terpasang), **refund tanpa struk** (G13), **order refund per kasir** (G13), **order diedit/void baris** (G13) | `ir.filters`; `pos.order(.line)` |
| `pos_payment_method_data.xml` | *Kartu Debit/Kredit (EDC manual)* per PT ber-POS: GL (`_rtl`), PB (`_fnb`), **BCR**, **BCF** (G20 W-32, 19.0.1.11.0). Tunai dibuat otomatis per outlet. QRIS `id_qr` tidak dikonfigurasi (pakai `efn_pos_payment_gateway`) | `pos_payment_method.py` |
| `barcode_nomenclature_data.xml` (G13) | Nomenklatur **EFN Retail (IDR)**, dipasang ke PT ritel GL dan (G20) BCR — asumsi format label timbangan sama (Q-25): `23.....{NNNNN}` harga Rupiah bulat, `21.....{NNDDD}` berat kg, `042` pelanggan, `041` karyawan, `043\|044` kupon, `.*` produk. **Tanpa** aturan diskon `22{NN}` (vektor fraud: memberi diskon tanpa cek peran/`manual_discount`) dan tanpa aturan lot `10` (salah baca EAN impor) | `barcodes`, `point_of_sale`/`stock`/`pos_loyalty` `default_barcode_patterns.xml`, `product_screen.js` |
| `barcode_hardening_data.xml` (13c, **noupdate=0**) | SEC-63: setiap `barcode.rule` bertipe `discount` di **semua** nomenklatur (termasuk default `point_of_sale.barcode_rule_discount` `22{NN}` yang dipakai PT F&B/EFN/FARM dan aturan buatan admin) diubah menjadi aturan **produk** berawalan `22` → barcode `22..` hanya dicari sebagai barcode produk, tidak pernah memberi diskon. Dijalankan ulang setiap install/upgrade modul (berlaku juga untuk DB yang sudah memasang modul). Record tidak dihapus (record noupdate `point_of_sale` yang dihapus dibuat ulang saat upgrade) | `barcodes/models/barcode_rule.py` (tanpa field `active`), `product_screen.js` `_barcodeDiscountAction` |
| `pos_fnb_defaults.xml` (G14, noupdate) | Preset master `pos_restaurant.pos_delivery_preset` → `available_in_self = False` (pesan-antar lewat Super-App/BFF, alamat pelanggan tidak dikumpulkan di halaman self-order); `pos_restaurant.pos_takeout_preset` → `use_timing = False` (slot bawaan memakai kalender demo Odoo Selasa–Sabtu milik main company) | `pos_restaurant/data/scenarios/restaurant_preset.xml` (data manifest), `pos_self_order/data/preset_data.xml`, `pos_preset.py` |
| `pos_bill_idr_data.xml` (G13) | Pecahan Rupiah (Rp100..Rp100.000) untuk hitung kas buka/tutup; 13 pecahan USD bawaan dihapus (`forcecreate="0"` → tidak muncul lagi saat upgrade) | `pos_bill.py`, `point_of_sale_data.xml` |
| `pos_kds_defaults.xml` (G18, W-22) | `ir.default` stasiun KDS baru: `warn_minutes = 5`, `late_minutes = 10` (keputusan pemilik proyek #14, P-13); retensi tiket 90 h tetap parameter `efn.kds.retention_days` milik `efn_pos_kds`; **tanpa stasiun expo** | `efn_pos_kds/models/efn_kds_station.py` |
| `pos_member_price_data.xml` (G18, L-23) | **Harga member = promo `pos_loyalty` ber-OTP** (`efn_requires_otp`), bukan pricelist pilihan kasir: template *Harga Member 5% - wajib OTP (PLACEHOLDER)* PT Grand Lucky (GL) dan, sejak G20, Bacha Coffee Retail (`loyalty_program_member_price_bcr`), `promotion` otomatis, diskon 5 % order, **nonaktif** (`active=False`, `pos_ok=False`) sampai admin menautkan POS kasir (constraint `efn_pos_otp_gate`). Pricelist demo *Harga Member* diarsipkan | `loyalty`, `efn_pos_otp_gate/models/loyalty_program.py` |
| `pos_loyalty_points_payment.xml` + `pos_loyalty_points_link.xml` (G18, W-01/E-30, 19.0.1.8.0) | Metode bayar **"Poin EFN"** per PT ber-POS (GL, PB; G20: + BCR `_bcr`, BCF `_bcf`, akun liabilitas per PT): jurnal bank *Poin EFN* (PEFN, tanpa rekening), akun `liability_current` *Liabilitas / Kliring Poin EFN* 21990091 (**PLACEHOLDER Q-F18-3**) sebagai `outstanding_account_id`, `efn_is_loyalty_points=True`, `payment_method_type=none`, tanpa split (noupdate). Penautan ke `payment_method_ids` POS yang `efn_loyalty_redeem_enabled`: otomatis saat POS dibuat/diubah dan setiap install/upgrade (`models/pos_config_points.py`; POS bersesi terbuka dilewati + log; hanya menambah). Default CE `_default_payment_methods` (semua metode non-tunai company ke POS baru) dikecualikan untuk Poin EFN, sehingga POS tanpa redeem/kiosk tidak mendapatkannya. Tanpa metode di POS, redeem ditolak server | `efn_pos_loyalty_ledger/models/pos_payment_method.py`, `pos_session.py` |
| `pos_fnb_service_presets.xml` (G19, W-14, 19.0.1.10.0) | Preset **Makan di tempat (F&B)** (`service_at=table`, fiscal position *F&B Dine-in* = PB1 + SC 5 %, SC masuk DPP PB1) dan **Bawa pulang (F&B)** (`counter`, *F&B Non-Dine-in* = tanpa SC, **PB1 0 %** — keputusan pemilik proyek 18 Okt 2026 butir 18-1, bisa diubah tanpa kode di pajak *PB1 Non-Dine-in*). `available_in_self=False` (SEC-75). Preset tanpa `company_id` → pasang hanya di POS PT F&B. **G20 W-32:** *Makan di tempat (Bacha F&B)* (`preset_bcf_dine_in`) & *Bawa pulang (Bacha F&B)* (`preset_bcf_takeaway`) memakai fiscal position company BCF → pasang hanya di POS Bacha Coffee F&B | `pos_preset.py`, `pos_self_order/models/pos_preset.py`, `account_fiscal_position.js` |
| `pos_cashier_groups.xml` + `models/res_partner.py` (G18, W-25; **SEC-121** 19.0.1.9.0) | Grup **"Kasir: boleh membuat pelanggan"** (`group_efn_cashier_contact_creation`) = POS User + ACL `res.partner` **baca + buat saja** (tanpa ubah/hapus) + record rule: hanya partner **individu**, tanpa induk, `company_id` = company POS aktif (default diisi `default_get`). Tidak mewarisi *Contact Creation* standar → tidak bisa mengubah partner bersama lintas PT atau rekening `res.partner.bank`. Rule pembanding untuk `base.group_partner_manager` (domain semua) agar user Contact Creation standar tidak ikut terbatas. **Hanya untuk akun kasir**: user Sales/Purchase/Stock/MRP manager yang diberi grup ini ikut terbatas (gagal tertutup). Mengedit pelanggan lama = supervisor/back office. Diverifikasi `call_kw` HTTP sebagai kasir (docs/08 §9.5) | `base/security/ir.model.access.csv` baris 75–78, `base_security.xml` `res_partner_rule` |

**Catatan upgrade:** semua blok `noupdate` (termasuk `<function>` dan penetapan nomenklatur PT Retail) hanya dijalankan saat **install**. DB yang sudah memasang `efn_setup_pos` 19.0.1.1.0: filter audit baru ikut dibuat saat `-u`, tetapi default/nomenklatur/pecahan harus diterapkan manual (Settings POS) atau DB pilot di-install bersih.

## Demo (`demo/`, hanya DB demo)
- `pos_demo.xml` (F&B): kategori stasiun dapur, 2 printer ePOS (IP placeholder), denah meja.
- `pos_fnb_demo.xml` (G14): lantai *Teras* (3 meja), modifier tanpa stok sebagai atribut `no_variant` (*Level Pedas*, *Permintaan Dapur* multi, *Gula*), menu *Nasi Goreng EFN* (Dapur Panas) & *Es Kopi Susu* (Bar) ber-PB1 default, preset *Bungkus – Kota Contoh* (fiscal position kota demo; ilustrasi aturan preset per kota).
- `pos_retail_demo.xml` (G13): kategori *Fresh (Timbang)*; *Tomat Merah per kg* (barcode berat `2109001000007`, label 1,250 kg = `2109001012505`; G17: semula `2100001000004`, bentrok dengan demo CE "Newspaper Rack"); *Daging Sapi* berlabel harga (`2300002000007`, label Rp45.500 = `2300002455005`); pricelist *Harga Member (DEMO)* 5% (PLACEHOLDER).
- `pos_kds_demo.xml` (G18, W-22): kategori *Bakery / Pastry* & *Bar Kopi*, printer cadangan ePOS per stasiun (IP placeholder), stasiun KDS *Bakery / Pastry (DEMO)* & *Bar Kopi (DEMO)* (PT F&B, `kds_printer`, warn 5 / late 10); stasiun expo demo `efn_pos_kds` diarsipkan (tanpa expo).
- `pos_fnb_menu_demo.xml` (**G22, W-27**, 19.0.1.12.0; keputusan 18-22 "modifier configurable", detail `docs/08 §13.3`): contoh **empat pola standar** untuk PB (`company_fnb`) dan BCF (`company_bcf`): (1) instruksi tanpa harga = atribut `no_variant` (*Penyajian*, *Gula*); (2) modifier berharga tanpa stok = nilai atribut `default_extra_price` (*Tambah 1 shot espresso* +Rp6.000); (3) **add-on berbahan** = produk jual non-stok + **BoM Kit** (`phantom`) ke bahan storable (*Tambah Keju* → *Keju Slice*; *Ganti Susu Oat* → *Susu Oat 200 ml*) sehingga `pos_mrp` memotong stok bahan; (4) **paket pilihan** = produk `type='combo'` + `product.combo` (*Paket Sarapan PB*: roti + minuman, croissant +Rp3.000, latte +Rp8.000; *Paket Kopi & Kue Bacha*). Semua harga/nama PLACEHOLDER. Test `tests/test_fnb_menu_demo.py` (dilewati tanpa demo).
- `pos_promo_demo.xml` (G18, W-10): promo standar `pos_loyalty` PT Retail — *Beli 2 Gratis 1 Air Mineral*, *Belanja Rp200 rb diskon 5%* (otomatis), *Kode Promo 10%* (`EFNDEMO10`, kode demo). Daftar promo nyata dari Marketing. **R-B06 → L-23 (diputuskan lead):** harga member = promo ber-OTP (`data/pos_member_price_data.xml`); pricelist member tidak ditawarkan ke kasir.

**Gelombang 20 (W-32/E-43, `docs/04 §12.1`, L-48; ADR-001 revisi 18 Okt; 19.0.1.11.0):** PT ber-POS = GL (`company_rtl`), PB (`company_fnb`), **BCR** (`company_bcr`, ritel PPN), **BCF** (`company_bcf`, F&B PB1 + SC). Data baru per PT: EDC manual, Poin EFN (jurnal PEFN + akun 21990091 PLACEHOLDER per PT), nomenklatur ritel BCR, preset Bacha F&B, template harga member BCR (nonaktif). Semua noupdate → **DB baru** (tanpa migrasi, §12.1). Test: `test_g18_config.py` (Poin EFN 4 PT, POS BCF hanya mendapat metode PT-nya, template BCR), `test_fnb_defaults.py` (`test_bacha_fnb_presets_use_bcf_fiscal_positions`, `TestEfnG20EntityPosData`). Belum: pos.config outlet Bacha (UI, checklist), printer/KDS Bacha (demo hanya PB).

**Gelombang 18 (`docs/08 §9`):** depends baru `efn_pos_kds` (default stasiun) dan `efn_pos_otp_gate` (kunci HP member untuk grup kasir pembuat pelanggan). Presisi IDR Rp1 (W-03, `efn_setup_company` 19.0.1.4.0) membuat `cash_rounding` POS tidak diperlukan. Test: `tests/test_g18_config.py`.

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

**Risiko PIN `pos_hr` (SEC-63, r6) — diterima sebagai kontrol operasional, bukan autentikasi.** Hash PIN karyawan (SHA-1 **tanpa salt**, docs/08 §6.2 #18) dimuat ke browser POS agar login kasir jalan offline. Pemegang perangkat dapat mengambil hash dari devtools/penyimpanan browser dan menebak PIN 4–6 digit secara offline dalam hitungan detik → kasir bisa "menjadi manager" di UI (refund, ubah harga, tutup sesi dengan selisih). Tidak ada perbaikan di CE tanpa mengubah core POS. Mitigasi wajib pilot:
1. **PIN manager ≥ 8 digit acak, unik per orang**; PIN kasir ≥ 6 digit. Jangan pakai tanggal lahir/NIK/nomor HP.
2. **Rotasi**: PIN manager diganti tiap 90 hari, segera saat karyawan/manager keluar atau perangkat hilang, dan setelah setiap insiden selisih kas; PIN kasir saat rotasi shift tetap.
3. **Manager login dengan badge `041`** (kartu karyawan, nomenklatur EFN Retail) bila perangkat mendukung; badge disimpan manager, tidak ditinggal di kasir.
4. Perangkat kasir: profil browser khusus, tanpa akses devtools (kebijakan Chrome `DeveloperToolsAvailability=2` / mode kiosk), user Odoo terminal POS ber-2FA (`efn_setup_security`).
5. Deteksi: filter audit *refund per kasir*, *harga manual*, *label harga produk timbang*, *order diedit/void* ditinjau harian; aksi manager di luar jam kehadiran manager = anomali.
6. Kontrol yang masih hanya di browser (batas selisih kas #21, void sebelum sinkron #24, refund ke metode lain #25) tetap terbuka; penegakan server = gap **G13-2** (approver diverifikasi server) dan **G13-6** (batas selisih di server), keputusan **Q-27**.

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
- **SEC-63:** aksi barcode harga CE (`setUnitPrice`) **tidak** memeriksa `restrict_price_control` → siapa pun yang bisa mencetak label `23` dapat menurunkan harga produk berlabel. Mitigasi: printer/timbangan label hanya di area produksi (bukan di kasir), filter audit *Label harga produk timbang (barcode 23)* ditinjau harian. Penegakan sistem (tolak label di bawah harga jual × berat minimum) = gap, belum dibuat. Barcode diskon `22` dinetralkan di semua PT (`barcode_hardening_data.xml`).
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

### Profil perangkat kasir (G20, keputusan 18-23; detail `docs/08 §11.9`)
Semua profil = **standar CE 19**, tanpa modul baru; dipilih per `pos.config` (satu config per perangkat/kasir). Route layar pelanggan 19.0 (terverifikasi `point_of_sale/controllers/customer_display.py`): `/pos_customer_display/<config_id>/<device_uuid>?access_token=…`, `auth="public"`, token = `pos.config.access_token` (dibandingkan `consteq`) → hanya dibuka di jaringan POS (WireGuard, ADR-023); jangan memotret/membagikan URL.

| Profil | Perangkat | Pengaturan `pos.config` | Catatan |
|---|---|---|---|
| **A. PC kasir dual screen** | PC + monitor kasir + monitor pelanggan | layar pelanggan dibuka di monitor kedua dari menu POS; `customer_display_bg_img` (logo brand) opsional; `iface_print_auto`, `epson_printer_ip`, `iface_cashdrawer` sesuai perangkat | Default ritel. Mekanisme pembaruan layar lokal vs jarak jauh **[B]** (uji perangkat) |
| **B. PC kasir single screen** | PC + satu monitor | tanpa layar pelanggan; total ditunjukkan di struk/monitor kasir | Paling murah; dipakai outlet kecil |
| **C. Tablet** | Tablet (Android/iPad) di browser | `iface_big_scrollbars` opsional; printer **Epson ePOS** lewat HTTPS (sertifikat otomatis, nomor seri di `epson_printer_ip`); laci kas lewat printer; tanpa timbangan (`iface_electronic_scale` mati) | Peer WireGuard di perangkat (ADR-023); user terminal ber-2FA; profil browser/kiosk tanpa devtools (SEC-63). Layar pelanggan opsional = tablet kedua (profil A jarak jauh) |

Tidak dipakai: IoT box (`iface_print_via_proxy`, `proxy_ip`, `is_posbox`) — perangkat non-Epson tidak didukung tanpa IoT box. Profil per outlet dicatat di ADR-023 (W-23).

**G21 W-23 — kenapa profil tidak dijadikan data modul (verifikasi `point_of_sale/models/pos_config.py` 19.0, 2026-10-19):** `pos.config` 19.0 **tidak** punya field jenis layar pelanggan (`customer_display_type` versi lama tidak ada; yang ada hanya `customer_display_bg_img`/`_name`), sehingga dual vs single screen = cara membuka halaman layar pelanggan, bukan nilai field. Perbedaan profil lain hanya nilai per perangkat (`epson_printer_ip` = IP/nomor seri printer outlet, `iface_cashdrawer`, `iface_print_auto`, `iface_big_scrollbars`, `iface_electronic_scale`) yang baru diketahui saat survei outlet. CE tidak punya model "templat config", dan menambah `pos.config` contoh per profil di demo akan memunculkan config/sesi tambahan di DB uji tanpa manfaat konfigurasi. Jadi profil = **checklist** tabel di atas + ADR-023 per outlet. Field `other_devices` (*Connect devices to your PoS without an IoT Box*) ada di 19.0; perilakunya untuk perangkat non-Epson **belum terverifikasi** (uji perangkat).

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
| `cash_rounding` / `rounding_method` / `only_round_cash_method` | **False** — IDR sudah Rp1 (W-03, keputusan #19/#43); aktifkan hanya bila Finance meminta pembulatan tunai Rp100 | `account.cash.rounding` |
| `iface_tax_included` | `total` | harga rak termasuk PPN (Q-9) |
| `pricelist_id`, `available_pricelist_ids` | **umum saja** — pricelist member TIDAK dimasukkan (L-23, R-B06: tombol Pricelist bisa dipakai kasir siapa pun) | harga member = promo OTP (baris berikut) |
| Harga member (L-23) | aktifkan template *Harga Member 5% - wajib OTP* (`loyalty_program_member_price_rtl`): Active, Point of Sale, `pos_config_ids` = POS kasir (bukan kiosk), nilai dari Marketing/Finance | `efn_pos_otp_gate`; offline otomatis mati |
| `payment_method_ids` | Tunai (otomatis), EDC manual, QRIS (EFN Gateway), **Poin EFN** (otomatis bila redeem aktif; jangan dihapus manual) | QRIS & Poin EFN mati saat offline |
| `iface_electronic_scale` | sesuai Q-25/Q-56 | |
| `limit_categories` / `iface_available_categ_ids` | opsional | |
| `iface_print_auto`, `iface_cashdrawer`, `epson_printer_ip` | sesuai perangkat (Q-56) | |
| `receipt_header` / `receipt_footer` | nama PT, NPWP, alamat, kebijakan retur | Q-1 |
| `default_fiscal_position_id` | kosong | PPN dari pajak produk |
| Company PT Retail → Barcode nomenclature | *EFN Retail (IDR)* (otomatis saat install) | |
| User terminal POS | kasir yang mendaftarkan pelanggan: grup **Kasir: boleh membuat pelanggan** (W-25, sempit SEC-121: buat pelanggan individu di company POS saja); lainnya POS User saja; JANGAN beri *Contact Creation*; 2FA aktif | SEC-07/SEC-44/SEC-115/SEC-121 |
| Uji offline | buka sesi online → putus jaringan → scan 10 SKU, bayar tunai/EDC → online → order tersinkron | Q-55 |

## SOP restoran — apa yang standar CE 19 (terverifikasi source 19.0, 2026-10-07; detail `docs/08 §7`)
| Kebutuhan | Standar CE 19 | Sumber |
|---|---|---|
| Denah & meja | `restaurant.floor` (tanpa `company_id`) + `restaurant.table` (`table_number`, `seats`, `shape`, posisi); `pos.config.floor_ids`, `default_screen = tables` | `pos_restaurant/models/pos_restaurant.py`, `pos_config.py` |
| Open bill | order `draft` menempel ke meja (`pos.order.table_id`, `customer_count`) sampai dibayar; *Bill* sebelum bayar = `iface_printbill` | `pos_order.py`, `pos_config.py` |
| Split bill | `iface_splitbill` (layar split → order terpisah) | `pos_config.py`, `pos_store.js continueSplitting` |
| Pindah / gabung meja | frontend `transferOrder`, `mergeTableOrders`, `mergeOrders`; gabung fisik meja = `restaurant.table.parent_id` | `pos_restaurant/static/src/app/services/pos_store.js` |
| Course (hidangan bertahap) | `restaurant.order.course` (`index`, `fired`, `fired_date`, `line_ids`); frontend `addCourse`, `fireCourse`, `printCourseTicket` | `restaurant_order_course.py` |
| Catatan | baris: `note` (dapur), `customer_note`; order: `internal_note`, `general_customer_note` | `pos_order.py` |
| Modifier | atribut `no_variant` → `pos.order.line.attribute_value_ids` (+ `custom_attribute_value_ids` untuk teks bebas); **tidak** mengubah stok/BoM | `product_attribute.py`, `pos_order.py` |
| Paket/combo pilihan | produk `type = 'combo'` + `product.combo`/`product.combo.item` (`extra_price`) → baris anak `combo_parent_id`/`combo_line_ids`, tiap anak dirutekan ke stasiun sesuai kategorinya | `product_combo*.py`, `pos_order.py` |
| Dine-in / takeaway / delivery | `pos.preset` master (Dine In/Takeout/Delivery, tidak bisa dihapus), `use_presets`, `default_preset_id`, `available_preset_ids`; preset bisa membawa `fiscal_position_id`/`pricelist_id` | `pos_preset.py`, `restaurant_preset.xml` |
| Printer dapur | `pos.printer` `printer_type` **hanya** `iot` (IoT box, tidak dipakai) atau `epson_epos`; routing `product_categories_ids`; `is_order_printer` + `printer_ids` | `point_of_sale/models/pos_printer.py` |
| Service charge | **tidak ada** di CE 19 (diverifikasi ulang G14: tidak ada field di `pos.config`/`res.config.settings` `point_of_sale` & `pos_restaurant`) → pajak SC (demo `efn_setup_company`) | F-28 |
| Tip | `iface_tipproduct` + `tip_product_id`; `set_tip_after_payment` (terminal) | `pos_config.py` |

**Aturan preset & pajak (temuan G14):** `setPreset()` memakai `preset.fiscal_position_id` **atau** `config.default_fiscal_position_id` — tidak digabung. Preset ber-fiscal-position (mis. takeaway tanpa SC) harus memuat pemetaan **PB1 kota** juga → satu preset takeaway **per kota** (contoh demo *Bungkus – Kota Contoh*). Preset tanpa fiscal position mengikuti fiscal position kota outlet. Preset tidak punya `company_id`: batasi dengan `available_preset_ids`.

**Printer dapur & enkripsi:** POS dibuka lewat HTTPS → browser hanya boleh mengirim ke printer lewat **HTTPS** (mixed content). Epson ePOS mendukung sertifikat otomatis: isi `epson_printer_ip` dengan **nomor seri** printer (opsi *Automatic Certificate Update* di printer) → Odoo mengubahnya ke domain `<hash>.omnilinkcert.epson.biz` (`format_epson_certified_domain`). Butuh resolusi DNS domain Epson dari perangkat kasir & dukungan model printer — **[B] PoC perangkat** (Q-24/Q-56). Jangan pakai HTTP polos ke printer. Printer non-Epson (ESC/POS biasa) **tidak** didukung CE tanpa IoT box.

**Offline:** order, cetak dapur (browser → printer LAN), split/pindah meja di perangkat yang sama tetap jalan; perangkat kasir lain tidak melihat perubahan meja sampai online; KDS (`efn_pos_kds`) dan QR order butuh server.

## Checklist outlet F&B pilot (PT F&B, Fase 2)
| # | Langkah / field | Nilai usulan | Keputusan |
|---|---|---|---|
| 1 | Company aktif PT F&B; buat `pos.config` lewat UI | 1 config per kasir/area | P-11 |
| 2 | `module_pos_restaurant` | True | — |
| 3 | `floor_ids`, `default_screen` | denah outlet; `tables` (dine-in dominan) / `register` (takeaway dominan) | Q-20 |
| 4 | `iface_printbill`, `iface_splitbill` | True / True | Q-20 |
| 5 | `use_presets` = True, `default_preset_id`, `available_preset_ids` | **G19 W-14:** *Makan di tempat (F&B)* (default bila dine-in dominan) + *Bawa pulang (F&B)* untuk kota default; kota lain = sepasang preset kota (dine-in & bawa pulang). **Jangan** tawarkan preset bawaan *Dine In*/*Takeout* (tanpa fiscal position → mengikuti default config). Delivery hanya bila kasir menerima pesan-antar telepon (pakai fiscal position non-dine-in) | #15/#37, R-L09 |
| 6 | `default_fiscal_position_id` | = fiscal position **dine-in** kota outlet (*F&B Dine-in* untuk kota default) agar order tanpa preset tetap kena SC | #15 |
| 7 | `fiscal_position_ids` / `tax_regime_selection` | kosong / False (kasir tidak memilih pajak) | — |
| 8 | Pajak menu | **PB1 default saja** (pajak penjualan default PT F&B) di setiap produk menu & produk anak combo. SC **tidak** dipasang di produk — ditambahkan fiscal position dine-in | #15 |
| 9 | `res.company.account_price_include` PT F&B | `tax_excluded` (data `efn_setup_company` 19.0.1.5.0, #15) | — |
| 10 | `iface_tax_included` | `total` bila harga termasuk pajak | Q-22 |
| 11 | Kategori POS = stasiun dapur | 1 kategori per stasiun (Dapur Panas, Bar, …) | Q-19 |
| 12 | `pos.printer` per stasiun (company PT F&B) | `epson_epos`, nomor seri/IP, `product_categories_ids` stasiun | Q-24, Q-56 |
| 13 | `is_order_printer`, `printer_ids` | True; semua printer stasiun (tetap aktif sebagai cadangan saat KDS ada) | Q-24 |
| 14 | Modifier | atribut `no_variant` untuk instruksi (harga tambahan = `default_extra_price`, tanpa stok); add-on berbahan = **produk add-on + BoM Kit** ke bahan storable (stok bahan turun lewat `pos_mrp`); jangan memakai atribut untuk bahan (contoh `demo/pos_fnb_menu_demo.xml`, docs/08 §13.3) | Q-21, 18-22 |
| 15 | Menu paket | combo pilihan = `type='combo'`; menu tetap dari paket CK = Kit BoM (`efn_kitting`) | Q-21, ADR-018 |
| 16 | `res.company.point_of_sale_update_stock_quantities` | `real` (default) agar stok paket CK turun per transaksi | Q-35 |
| 17 | `payment_method_ids` | Tunai, EDC manual, QRIS (EFN Gateway), Poin EFN (otomatis bila redeem aktif) | Q-23, Q-29 |
| 18 | `module_pos_hr` + peran | kasir **minimal/basic**, supervisor **manager** (lihat SOP retail) | Q-27 |
| 19 | `order_edit_tracking` | True (default G13) — void baris setelah kirim ke dapur tercatat | Q-27 |
| 20 | Self-order | `self_ordering_mode = 'nothing'` atau `kiosk` (**dikunci teknis** SEC-75: ir.default + constraint, `mobile`/`consultation` ditolak — tetap berlaku setelah ADR-032 Accepted opsi B: QR ponsel lewat Super-App/BFF + E-27 `efn_pos_bff_order`, **bukan** mode `mobile`); kiosk hanya di jaringan POS (WireGuard), `pay_after = 'meal'` | ADR-032, ADR-023 |
| 21 | Promo OTP | program `efn_requires_otp` tidak ditautkan ke config kiosk | — |
| 22 | Poin EFN | `efn_loyalty_redeem_enabled` False untuk kiosk (constraint) | Q-13 |
| 23 | Perangkat | kasir + KDS + kiosk = peer WireGuard (B-a ≤ 3 perangkat, B-b > 3) | Q-W1 |
| 24 | Uji pilot | dine-in 2 course + split + pindah meja; takeaway kota; printer mati → pesan ulang; offline 15 menit → sinkron; PB1/SC di struk & jurnal | PoC |

### QR order meja & kiosk (F-49) — **jangan aktifkan `mobile` tanpa keputusan**
`pos_self_order` mode `consultation`/`mobile` melayani pelanggan lewat route **publik Odoo** (`/pos-self/...`, `/pos-self-order/process-order/...`, `auth='public'`, token `pos.config.access_token` 16 heksa + `restaurant.table.identifier` 8 heksa, tanpa rate limit). Ponsel pelanggan harus menjangkau Odoo → **bertentangan dengan ADR-023/CLAUDE.md** (Odoo tidak diakses dari internet). Mode **kiosk** di jaringan POS (peer WireGuard) aman dipakai. QR dari ponsel pelanggan = keputusan klien + ADR baru (opsi di `docs/08 §7.4`).

**Kunci teknis (Gelombang 15, SEC-75, versi 19.0.1.5.0)** — `models/pos_config.py`,
`data/pos_self_order_lock.xml` (modul ini kini memuat sedikit Python, khusus penegakan ini):
- `ir.default pos.config.self_ordering_mode = 'nothing'` (default field 19.0 sudah `nothing` dan
  `create` tidak mengisi `mobile` — terverifikasi di image 2026-10-09; anggapan PLAUSIBLE r8 tidak
  berlaku di 19.0, kunci tetap dipasang sebagai pertahanan berlapis).
- Constraint menolak `mobile`/`consultation` kecuali `ir.config_parameter`
  `efn.pos.self_order_mobile_allowed` = benar. Parameter hanya bisa diubah `base.group_system` (ACL
  CE) dan **selalu diaudit** `efn_audit` (parameter kontrol, `CONTROL_SPECS`; log tidak ikut GC).
  Membuka sakelar = ADR Q-23 dulu.
- Setiap install/upgrade (data `noupdate=0`) dan saat sakelar dimatikan/dihapus: config ber-mode
  `mobile`/`consultation` dinormalkan ke `nothing` (log INFO berisi id config).
- Cek pra-go-live tetap: `SELECT id, name, self_ordering_mode FROM pos_config WHERE
  self_ordering_mode NOT IN ('nothing','kiosk')` = 0 baris. Test: `tests/test_self_order_lock.py`.
- Belum: rate-limit khusus `/pos-self` di Traefik (devsecops, SEC-75 butir 5).

## Keputusan klien yang dibutuhkan
**F&B (G14):** P-11 outlet pilot, Q-18 (layanan & preset), Q-19 (stasiun & routing), Q-20 (denah, split/merge/open bill), Q-21 (modifier & dampak resep), Q-22 + konsultan pajak (PB1 kota, SC, include/exclude, takeaway), Q-23 (QR order: siapa menutup bill; ponsel pelanggan atau kiosk), Q-24 (printer & cadangan KDS), Q-56/Q-W1 (perangkat). Lihat `docs/08 §7.6`.

**Retail:** Q-7 (jumlah SKU → `limited_product_count`), Q-9, Q-25 (timbangan & format label), Q-27 (peran kasir, refund/void wajib supervisor?, toleransi selisih kas, pecahan), Q-28 (mekanik promo), Q-29 (metode bayar, pembulatan tunai, refund ke metode asal), Q-34 (lot di kasir), Q-55, Q-56 (perangkat, timbangan, laci, printer), toko pilot.

## Cara install
```bash
odoo -d <db> -i efn_setup_pos     # menarik efn_setup_company (4 company + CoA)
```

## Batasan / belum terverifikasi
- SEC-63: aturan diskon yang dibuat admin **setelah** upgrade baru dinetralkan pada upgrade `efn_setup_pos` berikutnya (barcode.rule belum masuk aturan `efn_audit`). Test: `tests/test_barcode_hardening.py`.
- Belum di-install dengan data G13 (CI berikutnya). `<delete>` pecahan bawaan & penetapan nomenklatur di blok noupdate — perilaku saat install [B].
- Check digit barcode demo dihitung manual; scan nyata, timbangan, IoT, mode offline, ePOS, layar pelanggan: PoC perangkat.
- FEFO/lot di POS, scan barcode kemasan (`product.uom`), akun selisih kas jurnal tunai `l10n_id`: [B].
- `pos.config` tidak dibuat via data (default dihitung dari `env.company`).
- `restaurant.floor` 19.0 tanpa `company_id`.
