# efn_setup_company

Modul konfigurasi **data-only** (Python hanya `hooks.py`: presisi IDR Rp1, W-03) untuk struktur multi-company EFN dan pajak di Odoo CE 19.

- **ADR:** ADR-001 (satu DB multi-company), ADR-019 (unified cart, Holding = agen penagih), ADR-015 (penamaan; bagian "OCA dulu" digantikan ADR-031: tanpa modul pihak ketiga). *Proposed* kecuali prinsip ADR-031 (diputuskan klien).
- **Risiko dimitigasi:** R-T01/R-O01 (master data company konsisten dengan kontrak BFF), R-L03 (PB1 vs PPN dipisah per company; tarif & include/exclude **configurable**, bukan ditetapkan developer).
- **Fit-gap:** `docs/08_ODOO_FIT_GAP.md` baris F-01, F-02, F-27, F-28.

## Apa yang dikonfigurasi

| Bagian | Isi | File | Sifat |
|---|---|---|---|
| Mata uang | `base.IDR` diaktifkan; **presisi Rp1** (`rounding = 1` → 0 desimal, W-03/Q-ON6, sejak 19.0.1.4.0) | `data/res_company_data.xml`, `hooks.py` (`pre_init_hook`) | hanya saat install; **DB baru tanpa demo** (lihat di bawah) |
| Company | 4 `res.company`: `company_efn` (EFN, *Holding EFN*), `company_rtl` (RTL, *PT Grand Lucky*), `company_fnb` (FNB, *PT Paris Baguette* + Central Kitchen), `company_farm` (FARM). Negara Indonesia, IDR, `efn_brand_code`. `efn_business_type` **tidak** diisi (compute dari kode sejak `efn_core` 19.0.1.2.0, temuan QA U6). **G20 (18-27):** nama tampilan mengikuti "setiap brand punya PT sendiri"; xmlid & kode RTL/FNB tetap (kontrak BFF/ETL). Dua entitas **Bacha Coffee (Retail, F&B)** belum dibuat — rencana `docs/08 §11.3` | `data/res_company_data.xml` | noupdate; **nama = placeholder nama legal** |
| Pembulatan pajak (G20, 18-6) | `tax_calculation_rounding_method = round_globally` (*Round per Tax*, per dokumen) eksplisit di 4 PT (default CE sama; template `l10n_id` tidak mengubah) | `data/account_tax_rounding_data.xml` | noupdate — Finance boleh mengubah |
| Akses admin | `base.user_admin` mendapat ke-4 company | `data/res_company_data.xml` | noupdate |
| Fiscal localization | `account.chart.template.try_loading('id', company)` → CoA `l10n_id`, jurnal (Cash, Bank, Sales, …), pajak PPN template | `data/res_company_data.xml` | hanya saat install |
| **PB1 (PT F&B)** | Grup pajak **PB1** (`tax_group_pb1_fnb`), pajak **PB1 Default 10% (PLACEHOLDER)** (`tax_pb1_10_fnb`, `price_include_override` kosong = ikut setting company) dan dijadikan `account_sale_tax_id` PT F&B | `data/account_tax_pb1_data.xml` | noupdate — tarif/nama/akun boleh diubah admin |
| **Pajak F&B per layanan (W-14, G19, sejak 19.0.1.5.0)** | Harga menu PT F&B **exclude** (`account_price_include`); grup + pajak **Service Charge 5% (dine-in)** (`sequence` 5, `include_base_amount` → masuk DPP PB1); **PB1 Dine-in 10%** dan **PB1 Non-Dine-in 0%** (G20, keputusan 18-1); fiscal position **F&B Dine-in (PB1 + SC 5%)** dan **F&B Non-Dine-in (tanpa SC, PB1 0%)**, keduanya `auto_apply` mati. Produk menu cukup memakai PB1 default | `data/account_tax_fnb_service_data.xml` | noupdate — tarif dine-in PLACEHOLDER; non-dine-in 0 % **bisa diubah tanpa kode** (Amount pajak) |
| Ilustrasi PB1 per kota | Pajak *PB1 Kota Contoh Non-Dine-in 0% (DEMO)* + fiscal position *Outlet Kota Contoh (DEMO)* (= non-dine-in kota contoh); pasangan dine-in: *PB1 Kota Contoh Dine-in 5% (DEMO)* + *Outlet Kota Contoh Dine-in (DEMO)* (PB1 kota + SC) | `demo/` | hanya DB demo; 5% = angka ilustrasi |
| Harga termasuk pajak | `account_price_include = tax_included` di PT Retail | `demo/` | menunggu Q-9/Q-28 |
| Agen penagih | `efn_is_merchant_of_record = True` di Holding | `demo/` | menunggu Q-1, ADR-019 |

### Presisi Rupiah Rp1 (W-03, keputusan pemilik proyek 10 Okt 2026 #19; R-T17)
- Semua nilai uang (faktur, SO, POS, jurnal, `amount_payable` online) bulat Rupiah; harga satuan tetap 2 desimal (`decimal.precision` "Product Price"). Bukti PoC + dampak per area: `docs/08_ODOO_FIT_GAP.md` §9.1.
- Odoo **menolak** menaikkan presisi bila sudah ada jurnal IDR (`account/models/res_currency.py` `write`). Karena itu `pre_init_hook` (`hooks.py`) mengubahnya sebelum data/CoA modul ini dimuat → berlaku **saat install di DB baru tanpa demo** saja. DB `--with-demo`: demo `l10n_id` sudah membuat jurnal IDR ("ID Company") → hook melewati dengan WARNING, presisi tetap 0,01 (DB demo hanya untuk dev). **Cek pra-go-live:** `SELECT rounding FROM res_currency WHERE name='IDR'` = 1. DB yang sudah ada (mis. dev `efn_odoo`) tetap 0,01 sampai dibuat ulang; **DB produksi wajib dibuat dari versi ≥ 19.0.1.4.0**.
- Cash rounding POS/online tidak diperlukan lagi. Catatan pajak: PPN jurnal (11 %) vs e-Faktur Coretax (12 % × DPP 11/12 per baris) bisa selisih Rp1 per baris (Q-F18-2). Test: `tests/test_currency_idr.py`.

### Pajak dari template `l10n_id` (terverifikasi di `l10n_id/data/template/account.tax-id.csv`, 19.0)
- `tax_ST4` "12% (Non-Luxury Good)" — penjualan, amount 11 (12% × DPP nilai lain 11/12), **default penjualan**; `tax_PT4` padanannya untuk pembelian.
- `tax_ST3`/`tax_PT3` 12% (barang mewah), `tax_ST1`/`tax_PT1` 11%, `tax_ST0`/`tax_ST2` 0%/bebas, varian "Pemungut PPN".
- **Tidak ada PB1** dan **tidak ada akun liabilitas PB1/pajak daerah** di CoA template (`account.account-id.csv` 19.0 hanya punya VAT/PPh payable).
- Template **tidak** men-set `account_price_include` (default CE `tax_excluded`).

### Label pajak ke pelanggan (G17-QA-04, `data/account_tax_label_data.xml`, noupdate: hanya saat install)
Template `l10n_id` memberi `invoice_label` "12%" pada `tax_ST4` (tarif efektif 11%) dan nama grup berbahasa Inggris ("Non-luxury Good Taxes"). Keduanya tampil ke pelanggan: struk, invoice, katalog & quote online E-28 (label baris = `invoice_label`, label subtotal = nama grup / `tax_totals.group_name`). Modul ini menimpa nilai en_US (bahasa sumber = fallback semua bahasa, termasuk `lang=id_ID` dari BFF selama id_ID belum aktif) di 4 PT:

| Pajak penjualan | `invoice_label` | Grup | Nama grup |
|---|---|---|---|
| `tax_ST4`, `tax_ST5` | PPN 11% | `l10n_id_tax_group_non_luxury_goods` | PPN 11% |
| `tax_ST3`, `tax_ST6` | PPN 12% | `l10n_id_tax_group_luxury_goods` | PPN 12% |
| `tax_luxury_sales`, `tax_luxury_sales_pemungut_ppn` | PPnBM 20% | `l10n_id_tax_group_stlg` | PPnBM |
| `tax_ST0`, `tax_ST7` | PPN 0% | `l10n_id_tax_group_0` | PPN 0% |
| `tax_ST2` | Bebas PPN | `l10n_id_tax_group_exempt` | Bebas PPN |
| — | — | `default_tax_group` | Pajak |

PB1 PT F&B sudah berlabel `PB1` (grup "PB1 (Pajak Restoran / PBJT)"). Grup dipakai bersama pajak pembelian (nama ikut berubah di vendor bill). Label = PLACEHOLDER yang boleh diubah admin (form Pajak → *Label on Invoices*; form Grup Pajak → *Name*); tarif tetap menunggu konsultan pajak (Q-22, R-L03). DB yang sudah terpasang sebelum 19.0.1.3.0 tidak berubah saat upgrade (noupdate) → isi lewat UI atau langkah satu kali (lihat laporan efn-odoo-functional G17 lanjutan).

**Bila bahasa Indonesia (id_ID) diaktifkan kemudian:** Odoo memuat terjemahan template CoA tanpa menimpa yang sudah ada (`account.chart.template._load_translations`, `overwrite=False`). Karena modul ini hanya mengisi en_US, versi id_ID nama grup menjadi terjemahan l10n_id ("Pajak Barang", "Pajak Barang Mewah", …) dan itu yang terlihat oleh BFF (`lang=id_ID`). Setelah aktivasi id_ID, admin wajib mengisi terjemahan id_ID untuk nama grup & label di atas (ikon bahasa di field). Saat ini id_ID **tidak** dipasang.

## Model pajak yang dipakai (field terverifikasi 19.0)

| Kebutuhan | Fitur standar | Field |
|---|---|---|
| Tarif PB1 per kota | Satu pajak per tarif di grup PB1 + **fiscal position per kota** | `account.tax.original_tax_ids` ("Replaces"), `account.fiscal.position.tax_ids` (pemetaan `tax_map[src]→dest`; model `account.fiscal.position.tax` sudah **tidak ada** di 19.0) |
| Fiscal position per outlet | Default fiscal position di POS | `pos.config.default_fiscal_position_id`; opsional `fiscal_position_ids` + `tax_regime_selection` (kasir boleh memilih — **jangan** dipakai untuk tarif kota) |
| Dine-in vs takeaway (bila tarif berbeda) | Preset POS | `pos.preset.fiscal_position_id`, `pos.config.use_presets`/`default_preset_id`/`available_preset_ids` |
| Harga menu include/exclude | Setting per PT + override per pajak | `res.company.account_price_include` (`tax_included`/`tax_excluded`), `account.tax.price_include_override` (kosong = ikut company). Tampilan POS: `pos.config.iface_tax_included` (`subtotal`/`total`) — hanya tampilan |
| Service charge masuk DPP PB1 | Urutan & efek dasar pajak | SC: `sequence` lebih kecil + `include_base_amount=True`; PB1: `is_base_affected=True` |
| Akun liabilitas PB1 | Grup/baris repartisi | `account.tax.group.tax_payable_account_id`, `account.tax.invoice_repartition_line_ids`/`refund_repartition_line_ids` (`account_id`) |

## Panduan admin: menambah tarif PB1 kota baru tanpa developer

Prasyarat: user dengan hak *Accounting/Invoicing: Administrator*, company aktif = PT F&B. Label menu di CE 19 belum diverifikasi pada instance berjalan; nama field teknis di atas sudah.

1. **Buat pajak kota** — *Invoicing/Accounting → Configuration → Taxes → New*:
   - *Tax Name*: `PB1 <Kota> <tarif>%` (mis. `PB1 Kota Bandung 10%`), *Tax Type*: Sales, *Tax Computation*: Percentage, *Amount*: tarif Perda.
   - *Tax Group*: **PB1 (Pajak Restoran / PBJT)**; *Label on Invoices*: `PB1`.
   - *Included in Price*: **kosongkan** (ikut setting company) kecuali kota itu khusus.
   - Tab *Distribution*: baris *Tax* → akun liabilitas PB1 (sama dengan PB1 default).
   - Field *Replaces* (`original_tax_ids`): pilih **PB1 Default**.
2. **Buat fiscal position kota** — *Configuration → Fiscal Positions → New*: nama `Outlet <Kota>`, *Detect Automatically* **mati**, tab *Taxes*: tambahkan pajak dari langkah 1.
3. **Pasang ke outlet** — *Point of Sale → Configuration → Settings*, pilih POS outlet di kota itu → *Default Fiscal Position* = `Outlet <Kota>`. Jangan aktifkan pemilihan fiscal position oleh kasir.
4. **Uji** — buka sesi POS outlet, jual satu menu: baris pajak struk harus `PB1 <Kota>`; tutup sesi dan cek jurnal pajak ke akun liabilitas PB1.
5. **Ubah tarif**: ubah *Amount* pada pajak kota (berlaku untuk transaksi baru; transaksi lama tidak berubah). Untuk jejak audit yang rapi, lebih baik **arsipkan** pajak lama dan buat pajak baru, lalu ganti di fiscal position.

Kota yang memakai tarif sama dengan PB1 default **tidak** perlu fiscal position.

### Mengubah include/exclude harga menu
- Satu saklar per PT: *Settings → Invoicing/Accounting → Taxes* — field `account_price_include` (*Default Sales Price Include*). Ubah **sebelum** harga menu diisi; mengubahnya kemudian mengubah arti `list_price` seluruh produk (harga tidak dikonversi otomatis).
- Pengecualian per pajak: field *Included in Price* pada pajak.
- Pemetaan fiscal position antara pajak *include* dan *exclude* mengubah total yang dibayar pelanggan — pakai perlakuan yang sama untuk PB1 default dan PB1 kota.

### Service charge & pajak per layanan (W-14, keputusan pemilik proyek #15/#37)
CE 19 **tidak** punya fitur service charge POS (modul di Apps Store adalah pihak ketiga; OCA/pos 19.0 tidak menyediakan). Dipakai **"pajak" Service Charge** non-negara yang **ditambahkan oleh fiscal position dine-in**, bukan dipasang di produk:

| Layanan | Fiscal position | Pajak hasil pemetaan (dari PB1 default produk) | Contoh harga 100.000 |
|---|---|---|---|
| Tanpa fiscal position | — | PB1 default | 110.000 |
| Dine-in | *F&B Dine-in (PB1 + SC 5%)* | SC 5% → PB1 Dine-in 10% (DPP 105.000) | **115.500** |
| Bawa pulang / 3PL / pickup / online | *F&B Non-Dine-in (tanpa SC, PB1 0%)* | PB1 Non-Dine-in 0% | **100.000** |

- Cara kerja (19.0): `tax_map[src] = pajak di fiscal position yang "Replaces"-nya memuat src`; pajak sumber yang dipetakan **diganti**, sehingga PB1 dine-in harus pajak tersendiri. Sumber wajib pajak domestik (tidak berada di fiscal position mana pun) → **jangan** memasukkan PB1 default ke fiscal position.
- **PB1 non-dine-in = 0 % (keputusan pemilik proyek 18 Okt 2026, butir 18-1, sejak 19.0.1.6.0).** Risiko **R-L09 diterima pemilik proyek** (UU HKPD/perda kota umumnya mengenakan PBJT atas bawa pulang); disarankan konfirmasi tertulis konsultan pajak per kota sebelum go-live. Bila kota tertentu memungut: ubah *Amount* pajak *PB1 Non-Dine-in* (atau buat pajak non-dine-in kota). Tanpa kode. DB yang sudah terpasang sebelum versi ini (noupdate) tetap 10 % → ubah Amount lewat UI atau buat ulang DB dev (sudah wajib karena W-03).
- **Kota lain (sejak 18-1):** cukup **satu** pasangan dine-in per kota (pajak *PB1 <Kota> Dine-in* + FP *Dine-in <Kota>* + preset). Non-dine-in boleh memakai FP *F&B Non-Dine-in* bersama (0 %) selama kota itu tidak memungut PBJT bawa pulang.
- POS: preset *Makan di tempat (F&B)* dan *Bawa pulang (F&B)* (`efn_setup_pos` data) membawa fiscal position di atas; pos.config PT F&B memakai preset itu, **bukan** preset bawaan Takeout/Dine In (tanpa fiscal position → jatuh ke default config). Kanal online F&B: fiscal position outlet = non-dine-in kota outlet.
- SC masuk DPP PB1: bila konsultan menyatakan sebaliknya, `include_base_amount` SC = False.
- Akun: baris repartisi SC → akun *Pendapatan Service Charge*; PB1 (3 pajak) → akun liabilitas PB1 (Q-2, go-live blocker).
- **Kota lain:** per kota buat **dua** pajak (Dine-in & Non-Dine-in, "Replaces" = PB1 default) + **dua** fiscal position (*Dine-in <Kota>* = SC + PB1 kota dine-in; *Non-Dine-in <Kota>* = PB1 kota non-dine-in) + dua preset POS. Satu pajak kota tidak boleh dipakai di kedua fiscal position (tax_map dihitung dari pajak tujuan).
- Alternatif tanpa efek ke PB1 (tidak dipakai): produk "Service Charge" manual (rawan lupa/fraud) atau produk tip (`pos.config.iface_tipproduct`).

## Tanpa egress internet (R3, Gelombang 9)
Odoo EFN tidak punya akses internet (hanya jaringan internal, SEC-01). Fitur standar yang memanggil layanan Odoo S.A. dimatikan lewat **konfigurasi** (`data/no_egress.xml`, `noupdate="0"` agar diterapkan ulang setiap upgrade):

| Sumber (19.0, terverifikasi di source) | Perilaku tanpa egress | Konfigurasi |
|---|---|---|
| Cron `base_vat.vies_iap_check_update` → `POST https://vies.api.odoo.com/api/vies/1/check_update` (selalu, tidak bergantung `vat_check_vies`) | traceback `NameResolutionError` (ERROR) tiap hari | cron `active = False` |
| Cron `mail.ir_cron_module_update_notification` (publisher warranty) → `POST http://services.odoo.com/publisher-warranty/` dengan nama/email/telepon company + jumlah user | cron gagal (`cron_mode=None` → exception) + transfer data ke luar negeri bila ada egress | cron `active = False` |
| VIES saat mengisi NPWP (`res.company.vat_check_vies`) | tidak dipanggil bila tidak ada company dengan flag ini | `vat_check_vies = False` di 4 company (install); default CE juga False |

Hanya dipicu aksi pengguna (bukan cron) — dibiarkan, didokumentasikan untuk runbook/pelatihan: `partner_autocomplete` (auto-install; enrich company & autocomplete nama/NPWP → `partner-autocomplete.odoo.com`, gagal = WARNING; bila ingin bersih: uninstall modul ini lewat Apps), `snailmail`/`sms` (hanya bila dipakai; `stock_sms` default nonaktif: `res.company.stock_text_confirmation = False`), `web_unsplash`/`html_editor` (gambar & AI teks, hanya saat dipakai), `google_gmail`/`microsoft_outlook`/`google_address_autocomplete` (hanya bila dikonfigurasi), menu Apps (`apps.odoo.com`). Untuk mengaktifkan lagi cron di atas (hanya bila egress disetujui): ubah `no_egress.xml`, bukan UI.

## Keputusan klien yang dibutuhkan
- **Q-1** daftar PT, nama legal, NPWP, alamat (ganti nama placeholder lewat UI; tidak ditimpa upgrade).
- **Q-2** CoA seragam atau berbeda per PT; **akun liabilitas PB1** (go-live blocker), akun pendapatan service charge, akun dana titipan (ADR-019), akun liabilitas poin (ADR-020).
- **Q-22** tarif PB1/PBJT per kota outlet (Perda), include/exclude harga menu, service charge di DPP PB1, perlakuan takeaway/delivery.
- **Konsultan pajak (R-L03):** PPN untuk PT Farming, PT F&B yang juga menjual barang kemasan (PPN + PB1 dalam satu PT), status PKP tiap PT.

## Cara install
```bash
odoo -d <db> -i efn_setup_company            # produksi/staging: PB1 default ikut (data), tanpa contoh kota/SC
odoo -d <db_demo> -i efn_setup_company       # DB demo: + contoh kota, service charge, RTL tax-included
```
Prasyarat: `efn_core` (≥ 19.0.1.2.0) di addons path. Install pada DB **baru** (company & CoA dibuat sekali).

## Batasan / belum terverifikasi
- **Belum pernah di-install** (tidak ada Odoo lokal). Nama field/model diverifikasi terhadap source `odoo/odoo` 19.0 (2026-10-06); perilaku runtime belum.
- Perilaku POS: penerapan `default_fiscal_position_id` pada pajak yang dipetakan, interaksi preset vs default fiscal position, perhitungan `include_base_amount` di frontend POS, dan posting SC tanpa akun → **uji di PoC**. G19: pemetaan & perhitungan SC→PB1 diverifikasi di **source** 19.0 (JS POS = cermin `map_tax`, helper pajak bersama) dan diuji di sisi server (`tests/test_fnb_service_taxes.py`); **tour POS belum dijalankan** (docs/08 §10.4).
- `pos.preset` tidak punya `company_id`: preset yang memakai fiscal position PT F&B jangan dipasang di POS PT lain.
- `try_loading` saat instalasi modul mencetak peringatan "without a fully loaded registry"; bila CI gagal, pindahkan ke `post_init_hook` (E-11, efn-odoo-engineer).
- Konflik demo `efn_kitting` (E-7) sudah diselesaikan engineer: demo `efn_kitting` mencari company `efn_brand_code = 'FNB'` yang ada.
- Company bawaan (`base.main_company`) tidak diubah; arsipkan atau pakai sebagai company teknis.
- Inter-company belum ada (F-03: modul custom `efn_intercompany` fase 1, `docs/08` §5.2).
