# efn_setup_company

Modul konfigurasi **data-only** (tanpa Python) untuk struktur multi-company EFN dan pajak di Odoo CE 19.

- **ADR:** ADR-001 (satu DB multi-company), ADR-019 (unified cart, Holding = agen penagih), ADR-015 (penamaan; bagian "OCA dulu" digantikan ADR-031: tanpa modul pihak ketiga). *Proposed* kecuali prinsip ADR-031 (diputuskan klien).
- **Risiko dimitigasi:** R-T01/R-O01 (master data company konsisten dengan kontrak BFF), R-L03 (PB1 vs PPN dipisah per company; tarif & include/exclude **configurable**, bukan ditetapkan developer).
- **Fit-gap:** `docs/08_ODOO_FIT_GAP.md` baris F-01, F-02, F-27, F-28.

## Apa yang dikonfigurasi

| Bagian | Isi | File | Sifat |
|---|---|---|---|
| Mata uang | `base.IDR` diaktifkan | `data/res_company_data.xml` | noupdate |
| Company | 4 `res.company`: `company_efn` (EFN), `company_rtl` (RTL), `company_fnb` (FNB), `company_farm` (FARM). Negara Indonesia, IDR, `efn_brand_code`. `efn_business_type` **tidak** diisi (compute dari kode sejak `efn_core` 19.0.1.2.0, temuan QA U6) | `data/res_company_data.xml` | noupdate; **nama = placeholder** |
| Akses admin | `base.user_admin` mendapat ke-4 company | `data/res_company_data.xml` | noupdate |
| Fiscal localization | `account.chart.template.try_loading('id', company)` → CoA `l10n_id`, jurnal (Cash, Bank, Sales, …), pajak PPN template | `data/res_company_data.xml` | hanya saat install |
| **PB1 (PT F&B)** | Grup pajak **PB1** (`tax_group_pb1_fnb`), pajak **PB1 Default 10% (PLACEHOLDER)** (`tax_pb1_10_fnb`, `price_include_override` kosong = ikut setting company) dan dijadikan `account_sale_tax_id` PT F&B | `data/account_tax_pb1_data.xml` | noupdate — tarif/nama/akun boleh diubah admin |
| Ilustrasi PB1 per kota | Pajak *PB1 Kota Contoh 5% (DEMO)* (`original_tax_ids` = PB1 default) + fiscal position *Outlet Kota Contoh (DEMO)* | `demo/` | hanya DB demo; 5% = angka ilustrasi |
| Ilustrasi service charge | Grup *Service Charge* + pajak *Service Charge 5% (DEMO)* (`sequence` 5, `include_base_amount` True → masuk dasar PB1) | `demo/` | menunggu Q-22 |
| Harga termasuk pajak | `account_price_include = tax_included` di PT Retail | `demo/` | menunggu Q-9/Q-28 |
| Agen penagih | `efn_is_merchant_of_record = True` di Holding | `demo/` | menunggu Q-1, ADR-019 |

### Pajak dari template `l10n_id` (terverifikasi di `l10n_id/data/template/account.tax-id.csv`, 19.0)
- `tax_ST4` "12% (Non-Luxury Good)" — penjualan, amount 11 (12% × DPP nilai lain 11/12), **default penjualan**; `tax_PT4` padanannya untuk pembelian.
- `tax_ST3`/`tax_PT3` 12% (barang mewah), `tax_ST1`/`tax_PT1` 11%, `tax_ST0`/`tax_ST2` 0%/bebas, varian "Pemungut PPN".
- **Tidak ada PB1** dan **tidak ada akun liabilitas PB1/pajak daerah** di CoA template (`account.account-id.csv` 19.0 hanya punya VAT/PPh payable).
- Template **tidak** men-set `account_price_include` (default CE `tax_excluded`).

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

### Service charge
CE 19 **tidak** punya fitur service charge POS (modul di Apps Store adalah pihak ketiga; OCA/pos 19.0 tidak menyediakan). Opsi standar yang disarankan: **"pajak" Service Charge** (lihat demo) yang dipasang di produk menu bersama PB1. Alternatif tanpa efek ke PB1: produk "Service Charge" yang ditambahkan manual oleh kasir (rawan lupa/fraud) atau produk tip (`pos.config.iface_tipproduct`) — keduanya tidak masuk dasar PB1 otomatis. Keputusan masuk/tidaknya SC ke DPP PB1 = konsultan pajak (Q-22).

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
- Perilaku POS: penerapan `default_fiscal_position_id` pada pajak yang dipetakan, interaksi preset vs default fiscal position, perhitungan `include_base_amount` di frontend POS, dan posting SC tanpa akun → **uji di PoC**.
- `pos.preset` tidak punya `company_id`: preset yang memakai fiscal position PT F&B jangan dipasang di POS PT lain.
- `try_loading` saat instalasi modul mencetak peringatan "without a fully loaded registry"; bila CI gagal, pindahkan ke `post_init_hook` (E-11, efn-odoo-engineer).
- Konflik demo `efn_kitting` (E-7) sudah diselesaikan engineer: demo `efn_kitting` mencari company `efn_brand_code = 'FNB'` yang ada.
- Company bawaan (`base.main_company`) tidak diubah; arsipkan atau pakai sebagai company teknis.
- Inter-company belum ada (F-03: modul custom `efn_intercompany` fase 1, `docs/08` §5.2).
