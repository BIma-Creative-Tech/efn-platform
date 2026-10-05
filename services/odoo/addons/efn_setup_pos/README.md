# efn_setup_pos

Modul konfigurasi untuk POS **retail (PT Retail)** dan **restoran (PT F&B)** di Odoo CE 19. Isinya terutama **bundel dependensi** (modul CE yang benar) + data demo; pengaturan `pos.config` per outlet dilakukan lewat UI mengikuti checklist di bawah karena bergantung keputusan Q-18.

- **ADR:** ADR-013 (printer dapur dulu, KDS custom fase 2, kiosk *pay after meal*), ADR-017 (promo ber-OTP mati saat offline), ADR-020 (poin member tidak di Odoo). Semua *Proposed*.
- **Fit-gap:** `docs/08_ODOO_FIT_GAP.md` F-04..F-09.

## Modul yang di-install (semua terverifikasi ada di 19.0)
`point_of_sale`, `pos_restaurant` (meja, split bill, cetak bill, kitchen printer), `pos_self_order` (QR menu / QR order / kiosk), `pos_loyalty` (promo, kupon, gift card), `pos_mrp` (menu combo Kit BoM meng-explode ke paket CK), `pos_hr` (login kasir per karyawan), `pos_discount` (diskon global), dan otomatis `l10n_id_pos`.

## Data (`data/`, noupdate, berlaku di semua DB)
| File | Isi | Sumber 19.0 |
|---|---|---|
| `pos_config_defaults.xml` | `ir.default` untuk **pos.config baru**: `restrict_price_control = True` (hanya POS Manager boleh ubah harga), `manual_discount = False` (diskon baris mati; diskon hanya lewat program promo) | `point_of_sale/models/pos_config.py`; `ir.default.set` (@api.model) |
| `pos_payment_method_data.xml` | `pos.payment.method` **Kartu Debit/Kredit (EDC manual)** untuk PT Retail & PT F&B (`payment_method_type='none'`, jurnal Bank PT dari template `l10n_id`). Tunai **tidak** dibuat (metode kas hanya boleh untuk satu outlet; pos.config baru membuat kas + jurnalnya sendiri). QRIS `l10n_id` (`id_qr`/qris.online) **tidak** dikonfigurasi — QRIS/e-wallet lewat modul gap `efn_pos_payment_gateway` (gateway-agnostic, BFF §8.3) | `pos_payment_method.py` (`_check_cash_method_single_shop`, `_check_payment_method`), `pos_config.py` (`_default_payment_methods`) |
| `pos_audit_filters.xml` | 3 filter tersimpan pada *Order Lines* POS: (1) diskon baris tanpa `reward_id`, (2) `price_type = 'manual'`, (3) baris negatif non-reward (diskon global `pos_discount`/produk diskon manual); dikelompokkan per order | `ir.filters` (`model_id`, `domain`, `user_ids`), `pos.order.line` (point_of_sale + pos_loyalty) |

### Anti-fraud kasir: nomor HP pelanggan (sisa risiko SEC-07)
`efn_pos_otp_gate` mengambil `member_ref` dari partner order, sehingga **siapa yang boleh mengubah `res.partner.phone`** menjadi kontrol kunci.
- Standar 19.0 (terverifikasi): `res.partner` untuk `base.group_user` hanya **read**; write/create hanya `base.group_partner_manager` (*Contacts: Creation*), yang **tidak** di-imply oleh `group_pos_user`/`group_pos_manager`. Frontend POS 19.0 menyimpan partner lewat ORM biasa (tidak ada `create_from_ui` ber-`sudo` di `point_of_sale/models/res_partner.py`).
- **Konfigurasi wajib:** user Odoo yang dipakai terminal POS **tidak** diberi *Contacts: Creation*. Akibatnya kasir juga tidak bisa membuat pelanggan baru di POS — pendaftaran member lewat Super-App/BFF (Q-12).
- Bila klien ingin kasir boleh **membuat** pelanggan tetapi **tidak mengubah** nomor HP pelanggan lama: tidak ada setting standar (ACL tidak membedakan field) → **gap untuk efn-odoo-engineer** (lihat laporan).
- Perubahan `res.partner` oleh back office dicatat `efn_setup_audit` (aturan `res.partner`).
- Status: pemeriksaan dilakukan pada source; modul lain (mis. `sale`, `account`) belum diperiksa apakah meng-imply `group_partner_manager` → **cek di PoC** pada form user kasir.

### Diskon & harga manual
- `restrict_price_control` hanya mengatur **harga** (teks help 19.0). Apakah tombol diskon juga dibatasi untuk non-manager **belum terverifikasi** di frontend 19.0 → karena itu `manual_discount` dimatikan secara default.
- `pos_hr`: `minimal_employee_ids` / `basic_employee_ids` / `advanced_employee_ids` ada di 19.0; fitur UI yang dibatasi tiap level **belum terverifikasi**.

## Demo (`demo/pos_demo.xml`, hanya DB demo)
- `pos.category`: *Dapur Panas*, *Bar Minuman* (dasar routing printer).
- `pos.printer` (company PT F&B): 2 printer `epson_epos` (IP placeholder 192.0.2.10/11), masing-masing dengan `product_categories_ids`.
- `restaurant.floor` *Lantai Utama* + 3 `restaurant.table`.

## Checklist `pos.config` (field terverifikasi di source 19.0)
**Retail (PT Retail):**
| Field | Nilai usulan | Catatan |
|---|---|---|
| `module_pos_restaurant` | False | |
| `module_pos_hr` | True | login karyawan + PIN; approval supervisor via hak akses (Q-27) |
| `manual_discount` / `module_pos_discount` | False (default modul ini) / sesuai Q-28 | |
| `restrict_price_control` | True (default modul ini) | hanya manajer boleh ubah harga (anti-fraud) |
| `iface_tax_included` | `total` | harga rak termasuk PPN (Q-9) |
| `pricelist_id`, `available_pricelist_ids` | pricelist umum + member | harga member = pricelist (Q-9) |
| `payment_method_ids` | Tunai (dibuat otomatis per outlet → `cash_control`), *Kartu Debit/Kredit (EDC manual)* (data modul ini), *QRIS (EFN Gateway)* setelah `efn_pos_payment_gateway` tersedia | **Jangan** memakai `qr_code_method='id_qr'` (qris.online, API key di DB) — lihat `docs/08` F-21/F-30 |
| `default_fiscal_position_id` | kosong (retail) | PPN dari pajak produk |
| `iface_cashdrawer`, `iface_print_auto` | sesuai perangkat (Q-56) | |

**Restoran (PT F&B):**
| Field | Nilai usulan | Catatan |
|---|---|---|
| `module_pos_restaurant` | True | |
| `default_fiscal_position_id` | fiscal position kota outlet (mis. `Outlet <Kota>`); kosong bila tarif kota = PB1 default | pola PB1 configurable, panduan di README `efn_setup_company` |
| `fiscal_position_ids` / `tax_regime_selection` | kosong / False | jangan biarkan kasir memilih rezim pajak |
| `use_presets`, `available_preset_ids` | sesuai Q-18 (dine-in/takeaway/delivery) | `pos.preset.fiscal_position_id` hanya bila takeaway berbeda pajaknya; preset tanpa `company_id` → buat preset per PT/kota |
| `iface_tax_included` | sesuai `account_price_include` PT F&B | hanya tampilan |
| `payment_method_ids` | Tunai (otomatis), EDC manual, QRIS (EFN Gateway) bila sudah ada | QRIS gateway nonaktif saat offline |
| `floor_ids` | denah outlet | Q-20 |
| `iface_splitbill`, `iface_printbill` | True | split bill & pre-bill |
| `default_screen` | `tables` (dine-in) / `register` (takeaway) | Q-18 |
| `is_order_printer`, `printer_ids` | True, printer per stasiun | Q-19, Q-24 |
| `self_ordering_mode` | `mobile` (QR order di meja) atau `kiosk` | `consultation` = menu saja |
| `self_ordering_service_mode` | `table` / `counter` | |
| `self_ordering_pay_after` | **`meal`** | `each` berlabel *require Odoo Enterprise* (ADR-013) |
| Promo OTP | program `efn_requires_otp` **tidak** dicentang untuk config kiosk/self-order (`loyalty.program.pos_config_ids`) | kiosk tidak punya alur OTP |

**Loyalty/promo (`loyalty.program`, pos_loyalty):** pakai `program_type` standar (`promotion`, `buy_x_get_y`, `promo_code`, `coupons`, `gift_card`) dan batasi per outlet dengan `pos_ok`/`pos_config_ids`. **Jangan** memakai `program_type='loyalty'`/`ewallet` untuk poin member (ADR-020).

## Keputusan klien yang dibutuhkan
Q-18 (layanan per outlet), Q-19 (stasiun dapur), Q-20 (meja, merge/split), Q-23 (bayar di meja via QRIS), Q-24 (printer), Q-25 (timbangan), Q-27 (approval void/refund), Q-28 (mekanik promo), Q-29 (metode bayar, EDC terintegrasi?), Q-56 (perangkat), Q-55 (internet outlet).

## Cara install
```bash
odoo -d <db> -i efn_setup_pos     # menarik efn_setup_company (4 company + CoA)
```

## Batasan / belum terverifikasi
- Belum pernah di-install. Mode offline POS, printer ePOS tanpa IoT box, dan QRIS POS **belum diuji** (PoC fase 0).
- Metode EDC memakai jurnal `type='bank'` **pertama** milik PT (atribut `search`). Bila PT punya beberapa rekening, ganti jurnal lewat UI. Bila ADR-019 (Holding = merchant of record) disetujui, uang QRIS diterima Holding → jurnal metode QRIS PT F&B/Retail harus jurnal *clearing/piutang ke Holding*, bukan bank PT (Q-2/Q-4).
- `pos.config` tidak dibuat via data (lihat komentar di XML). Bila diinginkan, efn-odoo-engineer dapat menambah `post_init_hook` kecil yang membuat config dengan `with_company(company_fnb)`.
- `restaurant.floor` di 19.0 tidak punya `company_id`; pembatasan per company lewat `pos_config_ids`.
