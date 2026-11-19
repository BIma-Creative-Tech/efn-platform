# efn_setup_supermarket — tahap A (G44)

Modul **data-only** untuk konfigurasi Odoo CE 19 standar bisnis Supermarket (DC + 5 outlet) di PT Supermarket Demo.
Tidak ada model, field, atau logika Python. Dasar: fit-gap `docs/fit-gap/G45_SUPERMARKET.md` (S-01..S-07, §2) dan
`docs/04_SERVICE_CONTRACTS.md` §28.1.

- **ADR**: ADR-045 (DC satu PT + restriksi assortment, **Proposed**), ADR-031 (tanpa modul pihak ketiga), ADR-001
  (satu DB multi-company), ADR-027 (pajak configurable).
- **Risiko**: **R-L14** (struktur DC dalam PT yang sama vs perizinan gudang/PPN/NITKU, menunggu Q-SM5 & Q-SM20),
  R-L03 (tarif pajak placeholder), R-L07 (traceability lot).
- **Referensi desain**: tidak ada; semua butir tahap A tertutup CE standar.

## Apa yang dikonfigurasi

| Butir | Konfigurasi CE | File | Bisa diubah pengguna |
|---|---|---|---|
| Company | `res.company` **PT Supermarket Demo (PLACEHOLDER)**, `efn_brand_code = SMD` (jenis usaha `retail` = compute `efn_core`), root, IDR, NPWP fiktif `0000000000000000`, alamat "Jl. Contoh Fiktif", `vat_check_vies = False` | `data/res_company_data.xml` | ya (noupdate) |
| PPN | CoA `l10n_id` via `try_loading('id')`; pajak jual default `tax_ST4` (PPN 11% efektif); label pelanggan "PPN 11%"/"Bebas PPN" dsb.; `round_globally`. **PB1 tidak dikonfigurasi** (hanya bila ada deli/kafe makan di tempat) | `data/res_company_data.xml` | ya |
| Nomenklatur | company = GS1 (`barcodes_gs1_nomenclature.default_gs1_nomenclature`); `ir.default` per company SMD: `pos.config.fallback_nomenclature_id` = EFN Retail (IDR) | `data/res_company_data.xml` | ya |
| Grup inventory | Lots & Serial, Storage Locations, Multi-Step Routes (implied `base.group_user`, tanpa `mrp`) | `data/stock_config_data.xml` | ya |
| DC | `stock.warehouse` `SMDDC`, `two_steps` + `pick_ship`, alamat fiktif Kab. Bekasi | `data/stock_config_data.xml` | ya |
| Kategori | 16 `product.category` (Supermarket > Fresh > Sayur/Buah/Daging/Ikan & Seafood/Bakery; Dairy & Chilled; Frozen; Dry Grocery; Minuman; Snack; Personal Care; Household; Alkohol; Impor Premium), **FEFO di setiap kategori** (CE tidak menelusuri induk kategori) | `data/product_category_data.xml` | ya |
| Demo: outlet | 5 `stock.warehouse` `SMD01`..`SMD05` (Jakarta Selatan, Bandung, Tangerang, Surabaya, Denpasar; nama/alamat fiktif), `resupply_wh_ids = SMDDC` → rute "<outlet>: Supply Product from <DC>" lewat transit internal | `demo/stock_warehouse_demo.xml` | ya |
| Demo: lot di POS | `pos_type_id` outlet: `use_existing_lots = True`, `use_create_lots = False` | `demo/stock_warehouse_demo.xml` | ya |
| Demo: harga | `account_price_include = tax_included` | `demo/res_company_demo.xml` | ya |

Tidak ada produk: seed G48 yang mengisi katalog.

## Checklist operasional (DB nyata, bukan demo)

1. Gudang outlet baru (UI): isi **Resupply From** = DC; pada operasi *PoS Orders* gudang itu centang
   *Use Existing Lots* dan hapus centang *Create New Lots*.
2. POS config baru: buat saat company aktif = PT Supermarket (agar `ir.default` fallback berlaku) atau isi
   *Fallback Nomenclature* = "EFN Retail (IDR)" secara manual. Config yang dibuat lewat RPC wajib mengisinya eksplisit.
3. Sebelum pilot: **uji seluruh katalog barcode** dengan nomenklatur GS1. EAN-13 berawalan `13`/`15`/`17` dapat
   terbaca sebagai AI tanggal dan gagal tanpa fallback (fit-gap §2). Bila gagal, ganti nomenklatur company ke
   "EFN Retail (IDR)".
4. Produk: isi `is_storable`, `tracking = lot`, `use_expiration_date` dan umur simpan per produk. CE tidak punya
   default per kategori. Default per company lewat `ir.default` sengaja tidak dipakai karena ikut mengenai produk
   service/hadiah POS.
5. NPWP, nama legal, alamat: ganti placeholder setelah Q-SM1/Q-SM2. **Jangan** menerbitkan e-Faktur dengan NPWP fiktif.

## Apa yang belum

- **Tahap B (G45)**: depends `efn_assortment`, `efn_replenishment`, `efn_expiry_monitor`. Isinya ambang near-expiry
  per kategori, capability outlet, jadwal kirim contoh, dan sub-lokasi zona + `cyclic_inventory_frequency`.
- **Q-SM1**: kode, nama legal, NPWP, status PKP. Kode `SMD` di `efn_core` + test kontrak dikerjakan efn-odoo-engineer;
  modul ini gagal dipasang sampai nilai Selection itu ada.
- **Q-SM2**: nama/alamat/kota DC & outlet, zona, jam operasional.
- **Q-SM3**: timbangan/pemindai GS1-128 atau DataBar Expanded. Menentukan apakah nomenklatur GS1 dipertahankan.
- **Q-SM5** (konsultan pajak): kategori bebas PPN, harga include, PB1 deli/kafe, pemusatan PPN/NITKU. Terkait R-L14.
- Default tracking/umur per kategori dan FEFO otomatis di POS: gap `efn_expiry_monitor`.
- Belum diuji di instance Odoo berjalan (lihat laporan `docs/agent-reports/efn-odoo-functional.md`).
