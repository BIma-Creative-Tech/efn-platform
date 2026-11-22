# efn_setup_supermarket — tahap A (G44) + tahap B (G45) + G47

Modul **data-only** untuk konfigurasi bisnis Supermarket (DC + 5 outlet) di PT Supermarket Demo. Isinya Odoo CE 19
standar ditambah data untuk modul gap `efn_assortment`, `efn_replenishment`, dan `efn_expiry_monitor`.

- Tidak ada model, field, atau hook. Python hanya ada di dua migrasi: `migrations/19.0.2.0.0/post-migrate.py`
  (menerapkan ulang data tahap B pada DB tahap A) dan `migrations/19.0.3.0.0/post-migrate.py` (G47, DB demo).
  Lihat "Upgrade".
- Dasar: fit-gap `docs/fit-gap/G45_SUPERMARKET.md` (S-01..S-16, §5) dan `docs/04_SERVICE_CONTRACTS.md` §28.1, §28.4,
  §28.5, §28.5a, §28.10.

**Versi:** 19.0.3.0.0 (G47: outlet dua langkah + persona Insight demo). ADR-041 §3.4 (**Proposed**) untuk persona.

**ADR terkait:**
- ADR-045: DC satu PT + restriksi assortment (**Proposed**).
- ADR-046: forecast/replenishment (**Proposed**).
- ADR-031: tanpa modul pihak ketiga.
- ADR-001: satu DB multi-company.
- ADR-027: pajak configurable.

**Risiko yang dimitigasi atau tercatat:**
- **R-L14**: struktur DC dalam PT yang sama vs perizinan gudang/PPN/NITKU. Menunggu Q-SM5 dan Q-SM20.
- R-L03: tarif pajak masih placeholder.
- R-L07: traceability lot.

**Referensi desain:** tidak ada untuk modul ini. Butir CE tertutup standar; butir gap dirujuk di README modul gap
masing-masing.

## Apa yang dikonfigurasi

### Data (selalu dimuat, `noupdate`)

| Butir | Konfigurasi | File |
|---|---|---|
| Company | `res.company` **PT Supermarket Demo (PLACEHOLDER)**, `efn_brand_code = SMD`, root, IDR, NPWP fiktif, alamat fiktif, `vat_check_vies = False` | `data/res_company_data.xml` |
| PPN | CoA `l10n_id` (`try_loading('id')`), pajak jual default `tax_ST4` (PPN 11% efektif), label pajak Indonesia, `round_globally`. **PB1 tidak dikonfigurasi** | `data/res_company_data.xml` |
| Nomenklatur | Company memakai GS1. Per company SMD ada `ir.default` `pos.config.fallback_nomenclature_id` = EFN Retail (IDR) | `data/res_company_data.xml` |
| Grup inventory | Lots & Serial, Storage Locations, Multi-Step Routes | `data/stock_config_data.xml` |
| DC | `stock.warehouse` `SMDDC`, `two_steps` + `pick_ship`, **`efn_is_dc = True`** (tahap B; transfer DC → outlet dicek assortment) | `data/stock_config_data.xml` |
| Kategori | 16 `product.category` dengan FEFO di setiap kategori | `data/product_category_data.xml` |
| **Ambang kedaluwarsa (tahap B)** | Untuk 16 kategori: `efn_near_expiry_days`, `efn_markdown_days`, `efn_min_receipt_days`. **PLACEHOLDER** (tabel di bawah) | `data/product_category_expiry_data.xml` |

### Demo (`--with-demo`, `noupdate`)

| Butir | Konfigurasi | File |
|---|---|---|
| Outlet | `SMD01`..`SMD05` di 5 provinsi, `resupply_wh_ids` = DC, **`reception_steps = two_steps`** (G47, Q-G47-2). POS memakai lot yang ada dan tidak membuat lot | `demo/stock_warehouse_demo.xml` |
| Harga | `account_price_include = tax_included` | `demo/res_company_demo.xml` |
| **Capability & kebijakan** | Matriks di bawah. SMD02 punya baris *deny* Impor Premium. SMD03 `closed` dengan *allow* untuk 8 kategori | `demo/efn_assortment_demo.xml` |
| **Zona + cycle count + putaway** | Zona Fresh/Chiller/Freezer/Dry di bawah Stock setiap outlet (SMD05 tanpa Freezer), dengan `cyclic_inventory_frequency`. Putaway rule per kategori ke zona. Stock DC dihitung tiap 30 hari | `demo/stock_location_demo.xml` |
| **Jadwal kirim** | 44 `efn.delivery.schedule`: satu per outlet × kategori yang lolos assortment | `demo/efn_replenishment_demo.xml` |
| **User per persona** | Kepala toko SMD01..05 (scope outlet sendiri), buyer DC, markdown manager, supervisor ops. Login placeholder, **tanpa password** | `demo/res_users_demo.xml` |
| **Persona Insight (G47)** | Kepala toko → `store`; buyer DC → `dc`; markdown manager → `category` + kategori dikelola (Fresh, Dairy & Chilled, Frozen); user baru **Direksi SMD** → `exec`, **Marketing SMD** → `marketing`. `kpi_admin` tidak diberikan (Q-G47-1) | `demo/res_users_insight_demo.xml` |
| **Sakelar** | `efn_replenishment.enabled = true` (hanya demo) | `demo/ir_config_parameter_demo.xml` |

### Ambang per kategori (hari)

Semua nilai adalah **PLACEHOLDER**, menunggu dua jawaban:
- **Q-SM28**: nilai awal dari category manager.
- **Q-SM9**: satuan hari atau % umur simpan, dan siapa yang boleh override.

| Kategori | near-expiry | markdown | sisa umur min. saat terima | Rasional |
|---|---|---|---|---|
| Supermarket (root, fallback) | 30 | 14 | 30 | konservatif; menangkap produk yang keliru dipasang di induk |
| Fresh (induk), Sayur, Buah | 2 | 1 | 2 | umur 3–7 hari |
| Daging, Ikan & Seafood, Bakery | 1 | 1 | 1 | umur 1–3 hari, markdown H-1 |
| Dairy & Chilled | 5 | 3 | 5 | umur 7–21 hari |
| Frozen | 30 | 14 | 60 | umur 6–12 bulan |
| Dry Grocery, Minuman | 60 | 30 | 90 | umur 6–24 bulan |
| Snack | 45 | 21 | 60 | lebih pendek dari dry (minyak/tengik) |
| Personal Care, Household, Alkohol | 90 | 45 | 120 | umur 1–3 tahun |
| Impor Premium | 30 | 14 | 45 | lead time impor panjang; penerimaan lebih longgar |

Aturan yang dijaga test:
- `markdown <= near-expiry`;
- `min receipt >= near-expiry`.

`efn_min_receipt_days` juga dibaca `efn_replenishment` untuk shelf-life cap usulan (L-151).

### Capability outlet (demo)

| Outlet | chiller | freezer | alcohol_license | non_halal_allowed | kebijakan | Skenario yang bisa didemokan |
|---|---|---|---|---|---|---|
| SMD01 Jakarta | v | v | v | v | open | semua lolos |
| SMD02 Bandung | v | v | | | open | `denied` (Impor Premium), `missing_capability:alcohol_license` |
| SMD03 Tangerang | v | v | | | **closed** | `not_listed` (Alkohol, Impor Premium) |
| SMD04 Surabaya | v | v | v | v | open | outlet kedua berizin alkohol |
| SMD05 Denpasar | v | | | v | open | `missing_capability:freezer` |

Izin alkohol dan non-halal di atas adalah placeholder: daftar nyata menunggu Q-SM13 (Legal) dan Q-SM21. Kekurangan
`alcohol_license` atau `non_halal_allowed` **tidak bisa di-override** (L-150).

**Syarat capability produk diisi oleh seed G48, bukan modul ini.** Modul ini tidak membuat produk, dan
`efn_required_capability_ids` hanya ada di `product.template` (tidak ada versi per kategori). Rekomendasi untuk seed:

| Kategori | Capability wajib |
|---|---|
| Frozen | `freezer` |
| Dairy & Chilled, Daging, Ikan & Seafood | `chiller` |
| Alkohol | `alcohol_license`, ditambah `non_halal_allowed` bila Q-SM21 menetapkan alkohol non-halal |
| Impor Premium | per produk, sesuai jenisnya |

### Jadwal kirim (demo, PLACEHOLDER Q-SM11)

| Kelompok | Hari | Cut-off (jam lokal, H-lead) | Lead |
|---|---|---|---|
| Fresh, Dairy & Chilled | setiap hari | 21:00 | 1 hari (SMD05: 2) |
| Frozen | Sel, Jum | 17:00 | 1 hari |
| Ambient (Dry Grocery, Minuman, Snack, Personal Care, Household, Alkohol, Impor Premium) | Sen, Rab, Jum | 17:00 | 1 hari (SMD05: 2) |

- SMD05 memakai zona waktu `Asia/Makassar`.
- Ada **satu jadwal per kategori**. Jadwal yang kategorinya tumpang-tindih (mis. "semua kategori" bersama "Fresh")
  membuat produk diusulkan dua kali. `efn_replenishment` belum punya constraint yang mencegah hal ini.
- Jadwal hanya dibuat untuk kategori yang lolos assortment outlet.

### User demo

| xmlid | Login | Grup | Outlet |
|---|---|---|---|
| `user_smd0N_store_head` | `efn_demo_smd0N_kepala_toko` | Replenishment: kepala toko | SMD0N saja |
| `user_smd_buyer_dc` | `efn_demo_smd_buyer_dc` | Replenishment: buyer DC, Purchase User | — |
| `user_smd_markdown_manager` | `efn_demo_smd_markdown_manager` | `efn_expiry_monitor.group_markdown_manager` | — |
| `user_smd_supervisor_ops` | `efn_demo_smd_supervisor_ops` | `efn_expiry_monitor.group_expiry_override`, Assortment Manager | — |

- User demo tidak bisa login sampai admin menetapkan password dan user mengaktifkan 2FA (`auth_totp`).
- Tidak ada email atau nomor HP (minimisasi data pribadi).
- SoD: kepala toko tidak memegang grup Purchase atau buyer. Override dipegang supervisor, bukan kepala toko yang
  menerima barang.

### Sakelar fitur (docs/04 §28.10)

| Parameter | Di data | Di demo | Alasan |
|---|---|---|---|
| `efn_replenishment.enabled` | `false` (milik `efn_replenishment`) | `true` | jadwal dan approver demo tersedia |
| `efn_expiry_monitor.enabled` | `false` (milik `efn_expiry_monitor`) | **`false`** | syarat §28.10 = ambang diisi category manager (Q-SM28); nilai di sini masih placeholder |
| `efn_assortment.enforce` | `true` (milik `efn_assortment`) | `true` | — |

Modul ini tidak membuat ulang record parameter. Pemiliknya adalah modul masing-masing.

## Cycle count dan kapasitas rak: apa yang CE tutup

- **Cycle count (S-15), CE**: `stock.location.cyclic_inventory_frequency` (hari) menghasilkan `next_inventory_date`
  [V 19.0]. CE tidak punya frekuensi per kategori. Penggantinya adalah zona per kelompok kategori ditambah putaway
  rule per kategori (`stock.putaway.rule.category_id`, yang menelusuri kategori induk [V]). Hasilnya, jadwal hitung
  zona sama dengan jadwal hitung kategori. Opname tahunan untuk lokasi tanpa frekuensi memakai
  `res.company.annual_inventory_month/day` (default 31 Desember, tidak diubah). Gap opsional fase 2: jadwal per
  kategori lintas lokasi.
- **Kapasitas rak (S-16)**: `stock.storage.category.capacity` CE = qty per produk per kategori penyimpanan. Field ini
  dipakai putaway ke rak, bukan cap usulan order [V]. Cap usulan memakai
  `efn.assortment.line.shelf_capacity_qty` (outlet × produk), yang diisi setelah seed G48. Modul ini tidak mengisi
  kapasitas di baris kategori karena satuannya mengikuti UoM produk.

## Upgrade 19.0.1.x → 19.0.2.0.0

Semua file `noupdate`, jadi upgrade biasa tidak menulis record lama dan tidak menjalankan `<function>`. Migrasi
`post-migrate.py` menangani ini:
- menerapkan ulang `data/product_category_expiry_data.xml`, hanya bila belum ada satu pun ambang yang terisi;
- men-set `efn_is_dc` DC;
- pada DB demo: menerapkan ulang file demo tahap B dengan penjaga anti-duplikat (putaway rule dan jadwal hanya bila
  belum ada).

Depends baru (`efn_assortment`, `efn_replenishment`, `efn_expiry_monitor`) terpasang otomatis saat upgrade.

## G47 (19.0.3.0.0): penerimaan outlet dua langkah

Keputusan pemilik proyek 21 Nov 2026 (Q-G47-2): outlet SMD menerima barang **dua langkah sejak awal**.

**Rantai DC → outlet:**
1. DC Stock → DC Output (pick);
2. DC Output → transit (ship);
3. transit → **outlet Input** (receipt; lokasi tujuan akhir Stock);
4. **Input → Stock** (storage, rule push rute penerimaan outlet).

Putaway zona berlaku di langkah 4. Fakta source 19.0 [V]: lihat komentar di `demo/stock_warehouse_demo.xml`. Rantai
runtime dikunci oleh `test_demo_resupply_chain_two_steps`.

**DC tetap `two_steps` + `pick_ship`.** Penerimaan supplier ke Input DC memberi titik cek kualitas/kuantitas sebelum
stok bisa dialokasikan. Langkah QC terpisah (`three_steps`) tetap keputusan Q-SM2.

**Konsekuensi operasional:** POS menjual dari lokasi di bawah Stock. Barang yang sudah diterima tetapi belum disimpan
(masih di Input) **belum bisa dijual**. Kepala toko perlu memvalidasi dua dokumen: *Receipt*, lalu *Storage*.

### Dampak ke modul lain

Semua dampak di bawah dibaca dari kode. Butir yang ditandai [test] dikunci test modul ini. Belum ada yang dijalankan.

| Modul | Dampak | Status |
|---|---|---|
| `efn_assortment` | Langkah transit → Input dinilai karena tujuannya gudang outlet (`_efn_transit_leg_target`). Input → Stock tidak dinilai, karena sumbernya gudang outlet non-DC dan bukan lokasi antara. Leg pick/ship DC dinilai lewat `location_final_id`/`move_dest_ids`. **Tidak ada perubahan** | aman |
| `efn_replenishment` | `qty_available`/`incoming_qty` dibaca dengan konteks `warehouse_id` outlet. Stok di Input ikut `on_hand`, receipt masuk `in_transit`, dan storage internal tidak dihitung dua kali. `proposal.picking_id` tetap picking ship DC. **Tidak ada perubahan** | aman; picking ship [test] |
| ETL `qty_received` (C-3) | Dua move `done` berujung di gudang outlet (receipt dan storage). Rumus wajib menghitung hanya move yang **melintasi** batas outlet, yaitu receipt transit → Input. Rumus kontrak lama akan menghitung dua kali | C-3 **wajib** [test] |
| `efn_expiry_monitor` | Cek sisa umur minimum berjalan di `_action_done` picking **receipt** (sumber transit → tujuan gudang outlet non-DC). Storage Input → Stock tidak dicek ulang karena sumbernya gudang outlet. Override ditautkan ke picking receipt. Alert near-expiry memindai semua lokasi internal, termasuk Input | aman, cek sekali di langkah 1 penerimaan |
| Seed `scripts/dev/seed_demo_supermarket.py` (worktree g48, belum di `main`) | Stok awal dibuat dengan `inventory_quantity` + `action_apply_inventory` di **zona di bawah Stock** (`zone_location`, fallback `lot_stock_id`), bukan di Input. Tidak bergantung pada langkah penerimaan | aman. Jangan menaruh quant di Input: barang itu tidak terjual di POS dan tidak punya move storage |

## Persona EFN Insight (G47, demo)

Field kategori dan `kpi_admin` milik `efn_insight_sm`; sejak G52 (docs/04 §28.15.2) grup persona milik `efn_insight`
(xmlid name dan id grup tetap, keanggotaan user demo lama tidak berubah).

**xmlid yang dipakai:**
- persona: `efn_insight.group_insight_{exec,category,store,dc,marketing}` (sebelum G52: `efn_insight_sm.*`);
- kpi_admin: `efn_insight_sm.group_insight_kpi_admin` (hanya diuji tidak diberikan);
- field: `res.users.efn_insight_categ_ids`.

**Koordinasi:** bila nama di modul engineer berbeda, ubah `demo/res_users_insight_demo.xml`, konstanta
`PERSONA_GROUPS` di test, dan README ini. Modul ini **gagal dipasang** bila `efn_insight_sm` belum ada di addons path.
Keduanya wajib masuk PR G47 yang sama.

Test `test_persona_groups_one_per_privilege` mengunci rekomendasi fit-gap C-2: satu privilege per persona. Alasannya,
widget hak akses Odoo 19 menghapus grup kedua di privilege yang sama.

## Upgrade 19.0.2.x → 19.0.3.0.0

`migrations/19.0.3.0.0/post-migrate.py` hanya bekerja di DB demo:
- outlet demo yang masih `one_step` diubah ke `two_steps` lewat `write()` ORM. Langkah ini mengaktifkan Input dan
  memperbarui picking type & rule penerimaan; rute resupply tidak berubah. Nilai lain, mis. `three_steps`, tidak
  disentuh. Transfer masuk yang sudah terbuka tetap satu langkah;
- menerapkan ulang `demo/res_users_insight_demo.xml` (`mode="init"`, idempoten).

## Checklist operasional (DB nyata, bukan demo)

1. Gudang outlet baru (UI): isi **Resupply From** = DC dan **Incoming Shipments = Receive and Store (2 steps)**
   (Q-G47-2). Pada operasi *PoS Orders*, centang *Use Existing Lots* dan hapus centang *Create New Lots*.
2. POS config baru: buat saat company aktif = PT Supermarket, atau isi *Fallback Nomenclature* = "EFN Retail (IDR)".
3. Sebelum pilot, **uji seluruh katalog barcode** dengan nomenklatur GS1 (prefiks `13`/`15`/`17`, fit-gap §2).
4. Produk: isi `is_storable`, `tracking = lot`, `use_expiration_date`, umur simpan, dan
   `efn_required_capability_ids` (lihat tabel capability).
5. Outlet baru: buat zona Fresh/Chiller/Freezer/Dry di bawah Stock, frekuensi hitung, dan putaway rule per kategori
   (pola `demo/stock_location_demo.xml`). Isi capability dan kebijakan assortment (Assortment Manager).
6. Jadwal kirim: satu jadwal per kategori yang saling lepas per outlet. **Jangan** membuat jadwal tanpa kategori bila
   sudah ada jadwal berkategori.
7. Kepala toko: isi *Outlet dikelola* (`efn_outlet_warehouse_ids`) dan grup *Replenishment: kepala toko*. Jangan beri
   grup Purchase.
8. Ganti ambang placeholder setelah Q-SM28/Q-SM9, **baru** nyalakan `efn_expiry_monitor.enabled`.
9. NPWP, nama legal, alamat: ganti placeholder setelah Q-SM1/Q-SM2. **Jangan** menerbitkan e-Faktur dengan NPWP fiktif.

## Keputusan klien yang dibutuhkan

| Pertanyaan | Isi |
|---|---|
| Q-SM1 | kode, nama legal, NPWP, PKP |
| Q-SM2 | outlet/DC, zona, jam operasional |
| Q-SM3 | timbangan/pemindai GS1 |
| Q-SM5 | pajak, terkait R-L14 |
| **Q-SM9** | sisa umur minimum: hari atau %, dan siapa yang boleh override |
| **Q-SM10** | frekuensi cycle count per zona dan tanggal opname tahunan |
| **Q-SM11** | jadwal kirim dan cut-off per kelompok produk; kelayakan pasok fresh dari DC ke outlet luar Jawa (SMD05) |
| **Q-SM13 / Q-SM21** | outlet berizin alkohol dan outlet boleh non-halal; apakah alkohol juga dihitung non-halal |
| **Q-SM28** | ambang near-expiry/markdown per kategori |
| **Q-SM29 / Q-SM4** | pemetaan persona (kepala toko per outlet, buyer, markdown manager) dan persona Insight per jabatan |
| **Q-G47-1** | siapa pengelola definisi KPI Insight (`kpi_admin`) dan SoD terhadap kepala toko/DC |
| Q-G47-2 | *diputuskan 21 Nov 2026*: outlet penerimaan dua langkah |

## Apa yang belum

- **Belum diuji di instance Odoo berjalan**, termasuk install, upgrade/migrasi, dan test.
- `efn_expiry_monitor` sedang dibangun paralel. Modul ini mengikuti kontrak nama field dan grup (§28.5a); bila nama
  berubah, install gagal.
- Kapasitas rak per produk dan syarat capability produk: seed G48.
- Jadwal supplier (`efn.supplier.schedule`) dan supplierinfo DC: butuh supplier dan produk, jadi masuk seed G48.
- Zona DC dan langkah QC DC (`three_steps`): Q-SM2.
- G47: belum dijalankan. Yang belum terverifikasi:
  - rantai dua langkah runtime: receipt mendarat di Input dengan `location_final_id` Stock, push storage dibuat setelah
    receipt, dan putaway di langkah storage;
  - migrasi `write(reception_steps)` pada DB demo yang sudah berisi transaksi;
  - install bersama `efn_insight_sm` (belum ada di worktree saat ditulis).
