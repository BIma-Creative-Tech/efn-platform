# Template migrasi data — pilot POS retail (Fase 1)

- **Penyusun:** efn-odoo-functional · **Tanggal:** 2026-10-07 (Gelombang 13)
- **Risiko:** R-O02 (migrasi data), R-L01 (UU PDP), R-B06 (fraud harga/pajak salah). **ADR:** ADR-001 (satu DB multi-company), ADR-020 (poin di ledger BFF), ADR-029 (identitas member opak), ADR-027 (pajak configurable).
- **Fit-gap & spesifikasi validator:** `docs/08_ODOO_FIT_GAP.md` §6 (F-40, G13-7).

> **Ringkasan manajemen.** Data lama (produk, pelanggan, stok awal, poin) dimasukkan memakai fitur **Import** bawaan Odoo (CSV/Excel) — tanpa program khusus — **setelah** lolos pemeriksaan otomatis (validator) agar barcode ganda, nomor HP salah format, dan pajak yang tertukar antar-PT tertangkap sebelum masuk sistem. **Saldo poin dan persetujuan (consent) pelanggan tidak masuk Odoo**, tetapi ke platform EFN (BFF) yang menjadi satu-satunya buku poin.

File di folder ini **hanya baris judul** (template kosong). Jangan menaruh data nyata pelanggan di repo (UU PDP) — isi di laptop kerja terenkripsi, kirim lewat kanal aman, hapus setelah impor (retensi: sampai berita acara migrasi ditandatangani, maks. 30 hari — usulan, menunggu DPO).

## Urutan impor (DB pilot, company aktif = PT Retail)
| # | File | Tujuan | Menu Odoo / sistem | Prasyarat |
|---|---|---|---|---|
| 0 | — | Kategori produk, kategori POS, gudang/lokasi toko, pricelist member, pajak PT Retail | UI (konfigurasi) | `efn_setup_company`, `efn_setup_pos`, `efn_setup_mrp` |
| 1 | `01_produk.csv` | Master produk retail | *Inventory → Products → Import* (`product.template`) | #0 |
| 2 | `02_lot.csv` | Lot + tanggal kedaluwarsa (hanya produk `tracking = lot`) | *Inventory → Lots/Serial Numbers → Import* (`stock.lot`) | #1 |
| 3 | `03_stok_awal.csv` | Saldo stok awal per lokasi (+lot) | *Inventory → Physical Inventory → Import* (`stock.quant`, kolom `inventory_quantity`) → *Apply All* | #1, #2 |
| 4 | `04_pelanggan_odoo.csv` | Partner minimal untuk POS (nama, HP, pricelist) | *Contacts → Import* (`res.partner`) | #0; keputusan Q-12 |
| 5 | `05_member_bff.csv` | Member + consent | **BFF** (endpoint migrasi — belum ada di kontrak, usulan ke efn-lead) | Q-12, DPO |
| 6 | `06_saldo_poin_awal_bff.csv` | Saldo poin awal | **BFF** ledger (`source = migration_opening`) — **bukan Odoo** | Q-13, Finance |
| 7 | `07_karyawan_kasir.csv` | Karyawan untuk login kasir (`pos_hr`) | *Employees → Import* (`hr.employee`) | user Odoo terminal |

Setiap impor: (1) jalankan validator (G13-7) → laporan nol error; (2) *Test* di layar impor Odoo; (3) impor; (4) cek jumlah baris & 10 sampel; (5) simpan laporan validator (tanpa data pribadi) di berita acara.

## Kolom per template

### 01_produk.csv (`product.template`, field terverifikasi 19.0)
| Kolom | Isi | Aturan |
|---|---|---|
| `id` | External ID stabil, mis. `efn_mig.rtl_prod_000123` | Wajib; impor ulang memperbarui, bukan menduplikasi |
| `name` | Nama produk | Wajib |
| `default_code` | SKU internal | Unik per PT (disarankan) |
| `barcode` | EAN-13/EAN-8/UPC/internal. Produk timbang: **base code** `21PPPPP00000C` (berat) atau `23PPPPP00000C` (harga) | Unik per PT (`_check_barcode_uniqueness`: per company + produk tanpa company); check digit valid; tidak berawalan `22`/`041`–`044` |
| `type` | `consu` (barang) / `service` / `combo` | Retail = `consu` |
| `is_storable` | `True` untuk barang berstok | |
| `tracking` | `none` / `lot` / `serial` | Pilot: `none` untuk fresh kecuali Q-34 |
| `use_expiration_date`, `expiration_time` | `True` + umur simpan (hari) | Hanya bila `tracking = lot` |
| `uom_id/id` | `uom.product_uom_unit` (Unit), `uom.product_uom_kgm` (kg) | Produk timbang wajib kg |
| `categ_id` | Path kategori, mis. `All / Retail / Sayur` | Kategori sudah ada (#0); perishable → *Perishable (FEFO)* |
| `pos_categ_ids` | Nama kategori POS | |
| `list_price` | Harga jual (Rupiah bulat) | Termasuk/tidak termasuk PPN mengikuti `res.company.account_price_include` PT Retail (Q-9) |
| `standard_price` | Harga pokok awal | Untuk valuasi stok awal (Finance) |
| `taxes_id/id` | XML ID pajak penjualan **milik PT yang sama**, mis. `account.<id_company>_tax_ST4` | Format xmlid pajak per company **[B]** — cek di *Settings → Technical → External Identifiers*. Jangan pakai nama pajak (nama sama di 4 PT) |
| `supplier_taxes_id/id` | idem, pajak pembelian | |
| `company_id/id` | `efn_setup_company.company_rtl` | Kosong = produk dipakai semua PT (Q-8) |
| `available_in_pos`, `to_weight` | `True`/`False` | `to_weight = True` untuk produk timbang |
| `sale_ok`, `purchase_ok` | `True`/`False` | |

Kemasan tambahan (karton, pak) = `uom_ids` + barcode per kemasan (`product.uom`) — impor terpisah setelah #1, format menyusul hasil PoC.

### 02_lot.csv (`stock.lot`)
`id` (mis. `efn_mig.lot_000045`), `name` (nomor lot), `product_id/id` (id dari #1), `company_id/id`, `expiration_date` (`YYYY-MM-DD HH:MM:SS`, UTC). `use_date`/`removal_date`/`alert_date` dihitung otomatis dari produk.

### 03_stok_awal.csv (`stock.quant`, mode inventaris)
`location_id` (nama lengkap lokasi, mis. `TK01/Stock`), `product_id/id`, `lot_id/id` (kosong bila tanpa lot), `inventory_quantity` (dalam UoM produk). Setelah impor: *Apply All* → membuat `stock.move` ber-`is_inventory`. Lakukan **setelah** stock opname fisik dan **sebelum** sesi POS pertama; nilai stok awal = `standard_price` (koordinasi Finance untuk jurnal saldo awal).

### 04_pelanggan_odoo.csv (`res.partner`) — minimal (UU PDP)
`id` (mis. `efn_mig.cust_000777`), `name` (boleh nama panggilan), `phone` (**E.164 `+62…`**), `company_id/id` (kosong = semua PT, Q-12), `property_product_pricelist/id` (pricelist member, **company-dependent**: impor saat company aktif = PT Retail). **Tidak** mengimpor alamat, tanggal lahir, email, NIK kecuali ada dasar hukum (DPO). Consent **tidak** disimpan di Odoo.

### 05_member_bff.csv (BFF, bukan Odoo)
`source_ref` (id sistem lama, untuk idempotensi), `phone_e164`, `display_name`, `consent_marketing` (`true`/`false`), `consent_channel` (`whatsapp`/`sms`/`email`), `consent_at` (ISO 8601), `consent_evidence_ref` (rujukan bukti: formulir/rekaman). Member tanpa bukti consent → `consent_marketing = false`.

### 06_saldo_poin_awal_bff.csv (BFF ledger)
`source_ref`, `phone_e164`, `company_code` (`RTL`/`FNB`, PT penanggung liabilitas), `points` (bilangan bulat ≥ 0), `expires_at` (ISO 8601 atau kosong = kebijakan default `LOYALTY_POINT_EXPIRY_DAYS`). Diposting sebagai entri ledger `source = migration_opening`, `idempotency_key = migration:<source_ref>` (usulan kontrak). Total poin per PT direkonsiliasi dengan saldo liabilitas poin di neraca (Finance).

### 07_karyawan_kasir.csv (`hr.employee`)
`id`, `name`, `company_id/id`, `barcode` (kartu karyawan berawalan `041`, opsional), `user_id/id` (hanya untuk supervisor yang punya user Odoo POS Manager). **PIN tidak diimpor** — setiap karyawan mengisi sendiri di form karyawan / diisi HR langsung di Odoo; jangan pernah menaruh PIN di file. Peran POS (minimal/basic/manager) diatur di `pos.config`, bukan di file ini.

## Validator pra-impor (wajib, G13-7)
Spesifikasi lengkap untuk engineer: `docs/08_ODOO_FIT_GAP.md` §6.4. Ringkas — error memblokir impor:
1. Barcode: duplikat di file, bentrok dengan DB (per company + produk tanpa company), check digit EAN-13/EAN-8/UPC-A salah, awalan terlarang (`22`, `041`–`044`), produk timbang tidak sesuai pola base code `2[13]\d{5}0{5}\d`, PLU ganda.
2. Nomor HP: normalisasi ke `+62` lalu `^\+628\d{7,12}$`; duplikat HP (dua pelanggan satu nomor) → error; nomor asing → peringatan.
3. Pajak: setiap `taxes_id/id` milik company yang sama dengan `company_id/id`; produk PT Retail tidak boleh membawa pajak grup PB1; produk tanpa company tidak boleh membawa pajak satu PT saja.
4. UoM/tracking: `to_weight = True` ⇒ `uom_id = kg`; `use_expiration_date` ⇒ `tracking = lot`; lot/stok merujuk produk & lokasi yang ada.
5. Stok: qty ≥ 0, lot wajib untuk produk `tracking = lot`, lokasi milik company yang sama.
6. Poin: `points` bulat ≥ 0, HP ada di file member, `company_code` valid.
Laporan validator **tidak** boleh memuat nomor HP utuh (mask `62812****789`).
