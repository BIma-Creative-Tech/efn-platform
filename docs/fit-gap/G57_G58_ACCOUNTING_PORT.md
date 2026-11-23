# G57 / G58 — Port accounting `odoo-platform` → `efn_*` (aset tetap, kas kecil, rekonsiliasi, impor mutasi, batch pembayaran)

Status: **spesifikasi** (efn-odoo-functional, 23 Nov 2026), belum ada kode. Worktree `efn-platform-odoo`, branch
`feat/odoo-completion`. ADR: **ADR-049** (port & adaptasi kode milik pemilik proyek; diputuskan 23 Nov 2026, konfirmasi
kepemilikan tertulis masih wajib sebelum production), ADR-031 (tanpa dependency non-CE), ADR-001 (satu DB
multi-company), ADR-022 (enkripsi). Terkait: `G61_KATEGORI_PRODUK_PB.md`, `ODOO_IMPLEMENTATION_GAP_REVIEW.md` (G63, baris
P2 "Accounting port Levi's"), `G54_G55_G61_ODOO_SETUP.md` §G55 (menu Finance/Accounting), `G59_G60_ACCOUNTING_PORT.md`
(paralel: PPh, MDR estimasi + analitik outlet, persetujuan payment). Pembagian: G58 = UI rekonsiliasi + penerapan
`account.reconcile.model`; G60 = tarif MDR + plan analitik "Outlet" + persetujuan `account.payment`.

> **Ringkasan manajemen.**
> - **Lima modul baru**: aset tetap (G57), kas kecil & uang muka (G57), rekonsiliasi bank & akun (G58), impor mutasi
>   bank dari file CSV (G58), dan batch pembayaran vendor dengan file transfer bank (G58).
> - **Asal kode.** Semuanya disalin dari modul milik pemilik proyek di `odoo-platform`, lalu disesuaikan untuk EFN.
>   Yang dibuang: ketergantungan ke modul internal platform itu, koneksi langsung ke bank (H2H), data Levi's/Erajaya,
>   dan mesin persetujuan generik. Persetujuan diganti pola **empat mata** yang sudah dipakai EFN.
> - **Aset tetap terhubung ke kategori produk G61.** Tagihan vendor untuk barang berkategori `Aset Tetap / …` otomatis
>   membuat **draf aset**. Finance tinggal memeriksa lalu mengonfirmasi, dan penyusutan berjalan otomatis tiap bulan.
>   LVA tetap dibebankan langsung (keputusan G61 A6).
> - **Kas kecil outlet**: dana tetap per outlet (imprest). Kepala toko mencatat belanja, Finance mengisi ulang setelah
>   disetujui orang lain (bukan pemohon). Fitur ini juga melayani uang muka karyawan (perjalanan, pembelian mendesak).
> - **Akun dan umur ekonomis masih placeholder** dari CoA standar Indonesia (`l10n_id`). Finance dan konsultan pajak
>   perlu menetapkan nilai finalnya (pertanyaan di §9).
> - **Perkiraan ukuran**: ±7.000–8.500 baris (kode + view + test), ±3–4 minggu kerja satu engineer.

Legenda verifikasi: **[T]** terverifikasi di source (path:baris; `odoo19src` = subset CE 19 lokal, `GH` = GitHub
`odoo/odoo@19.0` via WebFetch 23 Nov 2026), **[B]** belum terverifikasi. Path sumber port: `S/` =
`…/scratchpad/odoo-platform-main/addons/` (snapshot `origin/main`; commit hash dicatat engineer di README provenance).

---

## 0. Aturan port yang berlaku untuk kelima modul (ADR-049)

| Aturan | Penerapan |
|---|---|
| Nama | `efn_<domain>`, model `efn.*` (bukan `custom.*`), field tambahan pada model CE berawalan `efn_`, method privat `_efn_*` |
| Lisensi / header | LGPL-3; README memuat **provenance**: modul asal, commit `odoo-platform`, daftar perubahan (bagian "Port" tiap modul di bawah) |
| Dependency | Hanya modul CE + `efn_*`. **Dilarang**: `custom_*`, `l10n_erajaya`, OCA, Enterprise |
| Multi-company | `company_id` wajib di setiap model baru (stored, `required` kecuali disebut lain) + `_check_company_auto = True` + record rule `[('company_id', 'in', company_ids)]` (global rule, bukan per grup) |
| ACL | `ir.model.access.csv` lengkap; **tidak ada** baris `base.group_user` read-all (sumber memberi semua user internal akses baca aset — dibuang, least privilege) |
| Audit | `pdp.audited.mixin` → **tanpa mixin**: tambah record `efn.audit.rule` (data `noupdate=1`) per model sensitif; hook `efn_audit` di `base` sudah mencatat create/write/unlink |
| RPC | Setiap method publik baru wajib masuk `efn_core/tests/test_rpc_surface.py::RPC_ALLOWLIST` dengan cek akses eksplisit di server (daftar per modul di bawah). Method yang tidak perlu dipanggil dari UI → awalan `_` |
| Log | Tanpa nama orang, nomor rekening, atau isi baris mutasi di `_logger`. Pakai id record. Masking: `efn_audit.models.audit_policy.mask_identifier` (rekening → `****123`), `mask_person_name`, `mask_free_text` |
| Odoo 19 | `name_get` → `_compute_display_name`; `_sql_constraints` → `models.Constraint` (sumber sudah); `res.users.groups_id` → `group_ids` |
| Dilarang tersalin | ikon `static/description/icon.png` sumber (pakai ikon CE `account` atau tanpa ikon), nama tenant di kode/komentar (`levis`, `ARKA`, `Erajaya`, `prd_*`), skrip `scripts/tenants/*`, migrasi `migrations/19.0.*` sumber (EFN mulai dari versi `19.0.1.0.0`, tanpa data lama) |

### 0.1 Ketergantungan sumber yang diganti atau dibuang

Hasil grep pemakaian nyata di kode sumber (bukan hanya `depends`):

| Dependency sumber | Dipakai untuk apa | Keputusan EFN |
|---|---|---|
| `custom_core` | tidak ada pemakaian di kode lima modul ini (hanya `depends`) | **Buang.** `efn_core` ikut otomatis lewat `efn_audit` |
| `custom_pdp_audit` (`pdp.audited.mixin`) | klasifikasi PDP + log audit di `custom.fixed.asset`, `petty.cash.request`, `petty.cash.realization`, log impor | **Ganti** dengan `efn_audit` (rule data per model, §0) |
| `custom_pdp_core` | tidak ada pemakaian di kode petty cash | **Buang.** Masking memakai `efn_audit.audit_policy`; rekening orang sudah dienkripsi `efn_pii`/`efn_pii_account` |
| `custom_accounting_full` | tidak ada pemakaian di kode aset (hanya `depends`); `custom.reconcile.rule` disebut di dokumen impor sebagai konsumen hilir | **Buang.** Auto-match = `efn_account_reconcile` |
| `custom_accounting_reports` (`custom.report.engine`, `custom.report.wizard.mixin`, `report_header`, `REPORT_MODEL_MAP`, `BUCKETS`) | laporan Daftar Aset (XLSX/PDF), Kartu Uang Muka, Outstanding, Aging | **Ganti**: XLSX langsung dengan `xlsxwriter` (pola `efn_account_reports/wizard/efn_financial_report.py:291` `action_xlsx`), PDF QWeb `web.external_layout`, aging bucket konstanta lokal `(0-30, 31-60, 61-90, >90)`. Laporan keuangan & konsolidasi **tidak** diport (sudah `efn_account_reports`) |
| `custom_approval_engine` (`approval.mixin`, `_approval_request_or_proceed`, `x_custom_approval_*`) | persetujuan pengajuan kas kecil | **Ganti** dengan pola empat mata `efn_loyalty_accounting/models/efn_loyalty_policy.py:392-404` (`maker_id`, `last_editor_id`, `editor_ids`, `_efn_check_approver`) |
| `custom_tax_id` (`x_custom_withholding_category_id`, bukti potong PPh) | PPh pada baris realisasi pihak ketiga | **Buang di G57.** PPh = gelombang G59/G60. Sisakan titik sambung: realisasi pihak ketiga membuat vendor bill biasa, sehingga modul PPh nanti cukup meng-extend `account.move` |
| `custom_adapter_framework` + H2H (`bank_h2h_*`) | koneksi API bank | **Buang** (G58: CSV saja) |
| `hr` (`hr.employee`, `hr.job`) | pemegang uang muka + plafon per jabatan + analitik per karyawan | **Buang** — lihat §2.2 keputusan K-PC1 |
| `custom_levis_localization` (narasi bank, MID, `levis.clearing.config`) | rekonsiliasi settlement POS Levi's | **Buang**; ide generiknya diport ke `efn_account_reconcile` (§3.3) |
| `l10n_erajaya` / seed `custom_levis_asset_accounts` | akun aset Erajaya | **Ganti** seed kelompok aset F&B/supermarket ke akun `l10n_id` (§1.4) |

Catatan snapshot: Glob tidak menampilkan folder `data/` (gitignore), tetapi file ada dan bisa dibaca langsung (mis.
`S/ee_gap/custom_accounting_asset/data/ir_cron_data.xml`).

---

## 1. G57 — `efn_account_asset` (aset tetap)

**Asal:** `S/ee_gap/custom_accounting_asset` (19.0.0.7.0) + pola seed `S/_tenants/custom_levis_asset_accounts`.
**CE 19:** tidak ada register aset (`account_asset` = Enterprise). Yang sudah dicakup CE dan **tidak** diduplikasi:
akun beban per kategori produk (`product.category.property_account_expense_categ_id`, company-dependent
[T] `odoo19src/account/models/product.py:24`). Lewat akun ini, baris tagihan untuk kategori `Aset Tetap / …` sudah
terjurnal ke akun aset (G61). Pembalikan jurnal memakai `account.move._reverse_moves` [T] `account_move.py:5497`.

### 1.1 Model

| Model (asal → EFN) | Field kunci | company / rule |
|---|---|---|
| `custom.fixed.asset` → **`efn.account.asset`** | `name`, `code` (sequence, unik per company), `company_id`, `currency_id` (related company), `group_id`, `location_id`, `custodian_id` (res.users), `acquisition_date`, `posting_date`, `depreciation_date_mode`, `acquisition_value`, `salvage_value`, `quantity`/`original_quantity` (aset pool), `useful_life_months`, `depreciation_method` (straight_line/declining/none), `declining_factor`, akun `asset_account_id`/`depreciation_account_id`/`expense_account_id`, `journal_id`, `depreciation_line_ids`, field revaluasi/retired/opening/merged, `state` (draft/running/disposed/cancelled), field disposal. **Baru (EFN):** `source_move_line_id` (M2o `account.move.line`, readonly, index), `source_move_id` (related stored), `product_id` (related dari baris sumber) | `company_id` required + rule company |
| `custom.fixed.asset.group` → **`efn.account.asset.group`** | `name`, `code` (unik per company), `company_id` (**jadikan required**; sumber opsional), `default_useful_life_months`, 4 akun default + 4 akun revaluasi, `default_journal_id`. **Baru:** `categ_ids` (M2m `product.category`; constraint: satu kategori hanya boleh di satu kelompok per company), `auto_create` (selection `no`/`draft`, default `draft`) | rule company |
| `custom.fixed.asset.location` → **`efn.account.asset.location`** | `name`, `parent_id`, `_parent_store`, `complete_name`. **Baru:** `company_id` (required) | rule company |
| `custom.fixed.asset.depreciation.line` → **`efn.account.asset.depreciation.line`** | `asset_id` (cascade), `company_id` (related stored), `sequence`, `date`, `amount`, `posted`, `reversed`, `move_id` | rule company |
| `custom.fixed.asset.revaluation` → **`efn.account.asset.revaluation`** | riwayat revaluasi (IAS 16 / PSAK 16) | `company_id` related stored + rule |
| `custom.fixed.asset.partial.disposal` → **`efn.account.asset.partial.disposal`** | riwayat pelepasan sebagian (aset pool) | idem |
| wizard `…disposal.wizard`, `…revaluation.wizard`, `…partial.disposal.wizard`, `…post.wizard`, `custom.report.asset.register.wizard` → `efn.account.asset.{disposal,revaluation,partial.disposal,post,register}.wizard` | sama dengan sumber | transient; `company_id` diambil dari aset/pilihan user; cek `company_id in env.companies` |
| `account.move` (`_inherit`) | sinkron baris penyusutan saat jurnal di-draft/hapus (`_levis_asset_depreciation_lines` → `_efn_asset_depreciation_lines`) + **baru**: pembuatan draf aset dari tagihan (§1.3) | — |

Catatan lokasi: `warehouse_id` tidak ditambahkan agar modul tidak bergantung `stock`. Lokasi aset = pohon bebas
(mis. `PB / Outlet Kemang / Dapur`), diisi Finance.

### 1.2 Fitur sumber yang diport apa adanya (logika)

Jadwal garis lurus/saldo menurun, mode tanggal (`specific`/`next_month`/`end_following_month`), cron posting
penyusutan, posting manual massal (wizard + server action list), revaluasi prospektif dengan pemisahan surplus/rugi,
disposal (laba/rugi vs NBV, transfer surplus ke laba ditahan), aset pool + pelepasan sebagian,
`_merge_assets_into_pool`, pembalikan baris (penuh atau parsial bila jurnal dipakai bersama), dan sinkronisasi saat
jurnal di-draft atau dihapus. Pengelompokan jurnal penyusutan per tanggal (`_group_depreciation_moves`, default ON) tetap
dipakai. Parameter `custom_accounting_asset.group_depreciation_moves` diganti `efn_account_asset.group_depreciation_moves`.

Disederhanakan atau dibuang:
- Guard serial/rental di `_merge_assets_into_pool` (`lot_id`, `rental_asset_ids`): field itu tidak ada di EFN. Hapus
  cabang tersebut beserta komentar ARKA.
- `custom_report_asset_register.py` (engine sumber) ditulis ulang: `efn.account.asset.register.wizard` →
  `action_xlsx()` (xlsxwriter) dan `action_pdf()` (QWeb `web.external_layout`). Kolomnya sama: kode, nama, kelompok,
  tanggal perolehan, umur, nilai perolehan, akumulasi awal tahun, Jan–Des, YTD, akumulasi akhir, nilai buku.
  - **Perbaiki bug sumber:** nilai buku di laporan sumber = `acquisition_value - accum` dan mengabaikan
    `revaluation_value`, `retired_accumulated_depreciation`, `opening_accumulated_depreciation`, serta baris
    `reversed`. Gunakan `net_book_value`/`accumulated_depreciation` model per tanggal laporan, dan kecualikan
    `merged_into_id != False`.

### 1.3 Tautan ke kategori G61: tagihan vendor → draf aset (baru, bukan dari sumber)

Sumber tidak punya fitur ini (`custom_asset_from_receipt` tidak termasuk paket port). Spesifikasi:

1. Override `account.move._post(soft=True)` [T] `odoo19src/account/models/account_move.py:5571`. Setelah `super()`,
   untuk move `move_type == 'in_invoice'` yang berhasil diposting, panggil `_efn_asset_create_from_bill()`.
2. Baris yang memenuhi syarat: `display_type == 'product'`, punya `product_id`, dan kategori produk (atau leluhurnya,
   pakai `parent_path`) ada di `efn.account.asset.group.categ_ids`. Kelompok yang dipakai: milik company move dengan
   `auto_create == 'draft'`. Bila lebih dari satu leluhur cocok, ambil yang paling spesifik.
3. Satu baris tagihan menjadi **satu draf aset pool**:
   - `quantity = line.quantity`;
   - `acquisition_value = abs(line.balance)` (mata uang company, DPP tanpa PPN — PPN masukan tetap di akun pajak);
   - `acquisition_date = move.invoice_date or move.date`;
   - `name = line.name or product.display_name`;
   - `group_id` diisi, dan default kelompok diterapkan **di server** (lihat poin 6);
   - `source_move_line_id = line.id`.
4. Idempoten: lewati baris yang sudah punya aset dengan `source_move_line_id` sama (`search_count`).
5. Tagihan di-reset ke draft/dibatalkan (`button_draft`/`button_cancel`): aset yang masih `draft` dari move itu
   dihapus. Bila ada aset `running`, **tolak** dengan pesan "batalkan/disposal aset dulu".
6. Gotcha sumber "default kelompok hanya lewat onchange" diperbaiki. Pindahkan logika `_onchange_group_id` ke helper
   server `_efn_apply_group_defaults()`, lalu panggil dari `create`, dari `write` bila `group_id` berubah dan nilainya
   kosong, dan dari onchange.
7. Nota kredit vendor (`in_refund`) **tidak** membuat atau mengurangi aset (Q-G57-5, default manual oleh Finance).
8. Tagihan dalam mata uang asing: nilai dari `balance` (sudah dikonversi). Aset tetap dalam mata uang company (batasan
   sumber, dipertahankan).
9. Aset dibuat di bawah hak user yang memposting tagihan. User Billing tidak punya ACL create aset, jadi pembuatan
   memakai `sudo()` **hanya** untuk `create` aset draft, setelah memastikan `line.move_id.company_id in env.companies`.
   Aset draft tidak menjurnal apa pun. Catat di chatter tagihan.

**LVA (G61 A6):** kategori `Aset Tetap / LVA` **tidak** dipetakan ke kelompok mana pun, sehingga tetap dibebankan
(`63110120`). Bila Finance memutuskan kapitalisasi, langkahnya:
- ubah akun kategori LVA ke akun aset;
- aktifkan kelompok `LVA` dari data `demo/` (umur 12–48 bulan, placeholder);
- tambahkan kategori LVA ke `categ_ids`.

Tidak perlu kode baru.

### 1.4 Seed kelompok aset (data, `noupdate=1`, idempoten) — pengganti `custom_levis_asset_accounts`

Fungsi `efn.account.asset.group._efn_seed_groups()` dipanggil `<function>` (pola sumber
`S/_tenants/custom_levis_asset_accounts/models/fixed_asset_group.py:71-150`). Perilakunya:
- untuk setiap company yang memakai CoA `l10n_id`, upsert berdasarkan `(code, company_id)`;
- hanya mengisi field yang kosong, sehingga koreksi Finance tidak tertimpa;
- akun dicari per company lewat `env.ref('account.<company_id>_l10n_id_<kode>')` atau
  `search([('code','=',kode), ('company_ids','in',company.id)])` (gotcha sumber: `code` company-dependent di 19 →
  selalu dipasangkan dengan `company_ids`);
- jurnal penyusutan `DEPR` (type `general`, nama "Penyusutan Aset") dibuat per company bila belum ada (gotcha sumber:
  tanpa jurnal, `action_confirm` gagal).

Akun yang sudah diverifikasi di `GH addons/l10n_id/data/template/account.account-id.csv` [T]:
- aset: `12210010` Bangunan Kantor, `12210020` Kendaraan, `12210030` Peralatan Kantor;
- akumulasi penyusutan: `12281010`, `12281020`, `12281030`;
- beban penyusutan (`expense_depreciation`): `67100010`, `67100020`, `67100030`;
- laba/rugi pelepasan: `81100050` Keuntungan Atas Penjualan Aktiva Tetap, `91100030` Kerugian Atas Penjualan Aktiva
  Tetap;
- laba ditahan: `31510010`;
- **tidak ada** akun mesin, software/aset tak berwujud, amortisasi, atau surplus revaluasi.

| code | Nama | Kategori G61 (`categ_ids`) | Aset | Akum. | Beban | Umur default (PLACEHOLDER) |
|---|---|---|---|---|---|---|
| `BLD-IMP` | Bangunan & Renovasi Outlet | `Aset Tetap / Bangunan & Renovasi` | 12210010 | 12281010 | 67100010 | 60 bln (renovasi gedung sewa: umumnya min(masa sewa, umur); konsultan pajak: bangunan tidak permanen 10 th / permanen 20 th — Q-G57-2) |
| `KIT-MCH` | Mesin & Peralatan Dapur | `Aset Tetap / Mesin` | 12210030 ⚠ | 12281030 | 67100030 | 96 bln |
| `FURN` | Furnitur & Perlengkapan Tetap | `Aset Tetap / Furnitur & Perlengkapan Tetap` | 12210030 | 12281030 | 67100030 | 48 bln |
| `OFC-OUT` | Peralatan Kantor & Outlet (POS, printer, kulkas display, CCTV) | `Aset Tetap / Peralatan Kantor & Outlet` | 12210030 | 12281030 | 67100030 | 48 bln |
| `SOFT` | Perangkat Lunak | `Aset Tetap / Perangkat Lunak` | — (A10) | — | — | 48 bln; **akun kosong** → `action_confirm` menolak sampai Finance membuat akun aset tak berwujud + amortisasi |
| `VEH` | Kendaraan (armada DC supermarket / CK) | — (tidak ada kategori G61) | 12210020 | 12281020 | 67100020 | 96 bln |

⚠ Mesin, furnitur, dan peralatan memakai satu akun `12210030` karena `l10n_id` hanya punya "Peralatan Kantor". Itu
sebabnya pemicu pembuatan aset memakai **kategori**, bukan akun: akun saja tidak bisa membedakan kelompok. Finance
sebaiknya membuat sub-akun (Q-G57-1). Akun revaluasi dibiarkan kosong; revaluasi menolak bila akun belum diisi (perilaku
sumber). Umur default hanya dipakai untuk aset baru dan tidak menjurnal apa pun.

Kategori dicari lewat `product.category` dengan `complete_name` persis seperti di atas. Bila belum ada (G61 belum
dijalankan di DB itu), lewati dengan `_logger.warning` berisi nama kategori saja. Seed bisa dijalankan ulang tanpa
efek samping (`-u efn_account_asset`, atau tombol "Pasang ulang kategori G61" untuk Asset Manager → method
`action_efn_reseed`, masuk allowlist).

SMD tidak memakai pohon kategori G61. Kelompok tetap terbuat di PT SMD (akun `l10n_id`), tetapi tanpa `categ_ids`
(memori: supermarket tidak boleh bergantung pada data brand lama).

### 1.5 Menu (Accounting root — `efn_setup_menu`)

Agar tidak wajib bergantung `efn_setup_menu`, induk memakai menu CE yang dipindah G55:

| xmlid | Induk | Seq | Grup | Action |
|---|---|---|---|---|
| `menu_efn_asset_section` "Aset Tetap" | `account.menu_finance_entries` (= "Jurnal & Penutupan" di bawah `efn_setup_menu.menu_accounting_root`) | 15 | `group_asset_user` | — |
| `menu_efn_asset_register` "Daftar Aset" | section | 10 | | `action_efn_asset` |
| `menu_efn_asset_post` "Posting Penyusutan" | section | 20 | `group_asset_manager` | wizard post |
| `menu_efn_asset_revaluation` "Revaluasi" | section | 30 | | |
| `menu_efn_asset_partial` "Pelepasan Sebagian" | section | 40 | | |
| `menu_efn_asset_report` "Laporan Daftar Aset" | `account.menu_finance_reports` (Accounting > Reporting) | 30 | `group_asset_user` | wizard register |
| `menu_efn_asset_config` "Aset Tetap" | `account.menu_finance_configuration` | 50 | `group_asset_manager` | — |
| `…_groups` "Kelompok Aset", `…_locations` "Lokasi Aset" | config | 10/20 | | |

Root sumber "Assets" di bawah `account.menu_finance` (Finance) **tidak** dipakai. Finance = operasional, sedangkan aset =
Accounting (G55). `efn_web_home` tidak perlu diubah karena tidak ada root baru.

### 1.6 Grup, ACL, rule, cron, sequence

- **Grup** (privilege baru `res_groups_privilege_efn_asset`, "Aset Tetap"):
  - `group_asset_user` "Aset / Pengguna": baca semua, buat/ubah draft;
  - `group_asset_manager` "Aset / Manager" (implies user): konfirmasi, disposal, revaluasi, posting, konfigurasi;
  - **Tanpa** `user_ids` admin default; pemetaan persona di `efn_setup_role` (Staf Finance → user, Finance Manager →
    manager) adalah tindak lanjut.
- **ACL** (ganti CSV sumber):
  - `account.group_account_readonly`: read semua model aset (auditor);
  - `group_asset_user`: aset/revaluasi/partial `rwc-`, line `r---`, group/location `r---`, wizard register `rwcu`;
  - `group_asset_manager`: semua `rwcu`, termasuk wizard disposal/revaluasi/partial/post;
  - wizard disposal/revaluasi/partial **tidak** untuk user (sumber memberi user; diubah karena dampak jurnal).
- **Rule:** global multi-company untuk semua model stored (6 model).
- **Kontrol status di server, bukan view:**
  - `action_confirm`, `action_cancel`, `action_reset_draft`, wizard disposal/revaluasi/partial/post →
    `_efn_check_manager()` (grup manager + `check_access('write')` + company ∈ `env.companies`);
  - `write` pada aset non-draft hanya boleh menyentuh `name`, `location_id`, `custodian_id`, `note` (selebihnya lewat
    wizard; sumber tidak membatasi).
- **Cron** `ir_cron_efn_asset_post_depreciation` (asal `cron_post_depreciation`): harian, `model._cron_post_due_depreciation()`,
  user OdooBot, noupdate.
  - Posting ke periode yang terkunci: CE menggeser tanggal jurnal ke periode terbuka berikutnya. Gotcha diterima dan
    didokumentasikan; SOP tutup buku = jalankan posting penyusutan sebelum lock date.
- **Sequence:** `efn.account.asset`, prefix `FA/%(year)s/`, padding 5, `company_id` False (kode unik per company lewat
  constraint).
- **efn_audit rule:** `efn.account.asset` (tracked: nilai, akun, umur, state, group), `efn.account.asset.group`
  (semua akun & umur).

### 1.7 RPC allowlist (method publik baru)

| (model, method) | Cek akses wajib |
|---|---|
| `efn.account.asset` `action_confirm`, `action_cancel`, `action_reset_draft`, `action_post_selected`, `action_efn_reseed` (group) | `_efn_check_manager` |
| `efn.account.asset` `action_open_dispose_wizard`, `action_open_revaluation_wizard`, `action_open_partial_disposal_wizard` | `_efn_check_manager` (hanya mengembalikan action) |
| `efn.account.asset` `action_view_partial_disposals`, `action_view_merged_assets`, `action_view_revaluations` | `check_access('read')` (hanya action) |
| `efn.account.asset.depreciation.line` `action_post_now`, `action_reverse` | `_efn_check_manager` pada aset induk |
| `efn.account.asset.{disposal,revaluation,partial.disposal,post}.wizard` `action_dispose` / `action_revalue` / `action_retire` / `action_post` | `_efn_check_manager` + `check_access('write')` aset |
| `efn.account.asset.register.wizard` `action_xlsx`, `action_pdf` | grup user/readonly + company ∈ `env.companies` sebelum baca |
| `account.move` `button_draft` | **sudah ada** di allowlist (override CE `efn_intercompany`): perbarui deskripsi "+ efn_account_asset: un-post baris penyusutan; tolak bila aset running dari tagihan" |
| `_merge_assets_into_pool` | tetap privat (dipanggil skrip `odoo shell` saja) |

### 1.8 Depends, file port, test, ukuran

- **depends:** `account`, `mail`, `efn_audit`. (`product` sudah ikut lewat `account`.)
- **Port:** `models/fixed_asset*.py`, `depreciation_line.py`, `account_move.py`, semua `wizards/*`, `views/*`,
  `report/asset_register_template.xml` (tulis ulang header), `data/ir_cron_data.xml`, `data/ir_sequence_data.xml`,
  `data/server_actions.xml`.
- **Drop:** `custom_report_asset_register.py` (ditulis ulang), `migrations/`, `MODULE_KNOWLEDGE.md` (ganti README),
  ikon.
- **Rename:** `custom.fixed.asset*` → `efn.account.asset*`, `_levis_*` → `_efn_*`, parameter
  `custom_accounting_asset.*` → `efn_account_asset.*`.
- **Test port:**
  - `tests/test_asset.py`, `tests/test_quantity_asset.py` (ganti fixture akun ke `l10n_id`/akun buatan test,
    `AccountTestInvoicingCommon`);
  - test register lama yang memakai `custom.report.asset.register` → uji `action_xlsx` mengembalikan file + nilai buku
    benar setelah revaluasi/partial.
- **Test baru (tag `efn`):**
  - tagihan dengan produk kategori `Aset Tetap / Mesin` → satu draf aset pool, qty dan nilai benar, idempoten saat post
    ulang;
  - tagihan LVA → tanpa aset;
  - reset tagihan → draf aset terhapus, aset running → ditolak;
  - multi-company: user PT A tidak melihat aset PT B, seed tidak menyilangkan akun;
  - user Billing memposting tagihan aset → aset terbuat;
  - user non-manager memanggil `action_confirm` lewat RPC → `AccessError`;
  - test registry `test_rpc_surface` hijau.
- **Ukuran:** ±1.900 baris Python (port ±1.600 + baru ±300), ±900 XML, ±900 test.

---

## 2. G57 — `efn_petty_cash` (kas kecil outlet & uang muka)

**Asal:** `S/ee_gap/custom_petty_cash` (19.0.0.5.0).

**CE 19 yang sudah mencakup sebagian (tidak diduplikasi):**
- jurnal kas (`account.journal` type `cash`) per outlet + register kas (menu `efn_setup_menu.menu_efn_cash_registers`);
- transfer internal bank → kas (`account.action_account_payments_transfer`, menu G55);
- vendor bill dengan PPN masukan.

**Yang tidak ada di CE:**
- pengajuan dan persetujuan;
- dana tetap (imprest) dengan plafon;
- uang muka karyawan beserta realisasi dan pengembalian;
- voucher kas keluar;
- kartu uang muka dan aging.

`hr_expense` (CE, "dibayar karyawan lalu diganti") hanya mencakup reimburse, bukan uang muka [B — modul tidak ada di
subset; sumber menyatakan `hr.expense.sheet` dihapus di 19].

### 2.1 Alur EFN

| Jenis (`efn.petty.cash.type.kind`) | Alur | Jurnal |
|---|---|---|
| `petty_cash` (kas kecil outlet, **imprest**) | Fund outlet (plafon mis. Rp 5 jt) → kustodian (Store Manager) mencatat **realisasi** belanja (nota) → **pengisian ulang** = pengajuan senilai realisasi yang sudah diposting sejak pengisian terakhir → disetujui (empat mata) → dicairkan Finance | Cair: Dr Uang Muka Kas Kecil (partner kustodian) / Cr Bank. Realisasi biaya: Dr Beban / Cr Uang Muka. Realisasi pihak ketiga: vendor bill (PPN masukan) lalu Dr AP / Cr Uang Muka. Saldo akun uang muka per fund = kas fisik yang seharusnya ada (dicocokkan dengan hitung kas) |
| `cash_advance` (uang muka karyawan: perjalanan, belanja mendesak CK) | Sama dengan sumber: pengajuan → setuju → cair → realisasi → kembalikan sisa / tambah kekurangan → **settle** (saldo nol, rekonsiliasi otomatis) | Tabel akuntansi sumber (`MODULE_KNOWLEDGE.md` §Accounting), tanpa PPh |

Imprest dipetakan ke mesin sumber tanpa logika akuntansi baru:
- fund menyimpan plafon dan akun/jurnal;
- setiap pengisian ulang adalah `efn.petty.cash.request` ber-`fund_id`;
- realisasi bisa dibuat terhadap **fund** (bukan hanya terhadap satu request), sehingga belanja harian tidak menunggu
  request baru;
- fund tidak pernah di-*settle* ke nol kecuali saat ditutup (`action_close_fund`: kembalikan sisa ke bank, lalu
  settle).

### 2.2 Keputusan desain

| ID | Keputusan | Alasan |
|---|---|---|
| **K-PC1** | **Buang dependency `hr`.** Pemegang dana = `holder_partner_id` (res.partner, default `env.user.partner_id`) + `holder_user_id` (res.users, opsional, untuk rule "milik sendiri"). Plafon per karyawan/jabatan (`hr.employee.pc_advance_limit`, `hr.job.pc_advance_limit`) dibuang; plafon per **jenis** dan per **fund** tetap | `hr` memang terpasang di efn-prod, tetapi hanya ada 1 `hr.employee` per PB/SMD (G63). Kustodian outlet sudah punya user (persona Store Manager). Field `hr.employee` dienkripsi `efn_pii_hr`, dan analitik per karyawan sumber membuat analytic account bernama orang (PII di CoA analitik). Bila nanti perlu, bisa dibuat modul jembatan `efn_petty_cash_hr` (`auto_install`) tanpa mengubah modul ini |
| K-PC2 | `custom_approval_engine` → empat mata. Approve: grup `group_petty_cash_approver` + `check_access` + company + pemohon/penerima/penyunting ≠ penyetuju. Disburse: grup `group_petty_cash_finance` + pencair ≠ pemohon/penerima. Posting realisasi: Finance ≠ pembuat realisasi/penerima | Pola teruji `efn_loyalty_accounting` (SEC-157 himpunan penyunting) |
| K-PC3 | `post_init_hook` sumber (semua user internal otomatis "Petty Cash / User") **dibuang** | Least privilege. Grup diberikan lewat `efn_setup_role` (Store Manager, Staf CK yang berhak uang muka) |
| K-PC4 | `res.company.petty_cash_*` + migrasi 0.5.0 (rantai fallback request → type → company) **dibuang**; rantai EFN = request → fund/type | Tanpa data lama. Tiga level konfigurasi tidak diperlukan |
| K-PC5 | Kartu Uang Muka, Outstanding, Aging = **list/pivot `account.move.line`** dengan filter akun uang muka + group by partner/fund + bucket umur (computed non-stored di wizard XLSX). Voucher PDF (`reports/petty_cash_vouchers.xml`) **diport** | `custom_accounting_reports` tidak diport; CE 19 tidak punya partner ledger (`account_reports` = EE) |
| K-PC6 | Analitik Operating Unit (`l10n_ou_analytic_id`, `pc_ou_allowed_ids`, parameter plan) **dibuang**; ganti `analytic_distribution` biasa (mixin CE) di fund, diteruskan ke baris jurnal. Finance mengisinya dengan akun analitik outlet dari plan "Outlet" G60-C (`stock.warehouse.efn_analytic_account_id`), tanpa dependency ke `efn_account_localization` | Satu sumber analitik outlet (G60). Fund tetap berfungsi tanpa analitik |
| K-PC7 | Multi-mata uang (`_pc_conv`, `_pc_leg`, tag selisih kurs) **diport** apa adanya | Murah dipertahankan. Uang muka perjalanan luar negeri mungkin |
| K-PC8 | Kas laci POS **tidak** dipakai untuk belanja outlet (SOP) | Kas POS = penjualan. Cash-out POS untuk belanja mengaburkan selisih kas kasir |

### 2.3 Model

| Model (asal → EFN) | Field kunci | company / rule |
|---|---|---|
| `petty.cash.type` → **`efn.petty.cash.type`** | `name`, `code` (unik per company), `kind` (`petty_cash`/`cash_advance`/`other`), `company_id`, `advance_account_id` (reconcile=True), `bank_out_journal_id`, `payment_journal_id` (**dedicated**, gotcha sumber), `expense_journal_id`, `sequence_id`, `limit_enforcement` (off/warn/block), `limit_per_request`, `limit_outstanding`, `max_open_requests`, `block_when_overdue`, `realization_days`, `allow_third_party`, `is_default` | rule company |
| **baru** `efn.petty.cash.fund` | `name` (mis. "Kas Kecil PB Kemang"), `company_id`, `type_id` (kind=petty_cash), `cash_journal_id` (opsional; bila diisi, cair = transfer ke jurnal kas outlet, bukan akun uang muka; lihat Q-PC2), `holder_partner_id`, `holder_user_id`, `float_amount` (plafon imprest), `limit_per_transaction`, `analytic_distribution`, `state` (active/closed), computed `balance` (saldo akun uang muka partner kustodian per fund), `unreplenished_amount` (realisasi posted sejak pengisian terakhir), `last_replenish_date` | rule company + rule "kustodian lihat fund sendiri" |
| `petty.cash.request` → **`efn.petty.cash.request`** | field sumber minus `employee_id`/OU/`x_custom_approval_*` + `holder_partner_id`, `holder_user_id`, `fund_id`, `is_replenishment`, jejak empat mata `maker_id`, `last_editor_id`, `editor_ids`, `approved_by_id`, `approved_at`, `disbursed_by_id`. State sumber dipertahankan (`draft → to_approve → approved → disbursed → in_realization → settled`, + `cancelled`) | rule company + rule "milik sendiri" (holder_user/maker = user) untuk grup user; approver/finance = semua di company |
| `petty.cash.request.line` → `efn.petty.cash.request.line` | rincian estimasi | `company_id` related stored |
| `petty.cash.realization` → **`efn.petty.cash.realization`** | `request_id` **atau** `fund_id` (constraint tepat satu), `date`, `line_ids`, `amount_total`, `state`, `bill_ids`; jejak empat mata `maker_id`, `posted_by_id` | rule company + milik sendiri |
| `petty.cash.realization.line` → `efn.petty.cash.realization.line` | `line_type` (`third_party`/`expense`), `partner_id`, `product_id`, `account_id`, `tax_ids`, `price_unit`, `quantity`, `attachment_ids` (wajib untuk pihak ketiga — sumber). `x_custom_withholding_category_id` **dibuang** | related stored |
| `account.move` (`_inherit`) | `efn_petty_cash_request_id`, `efn_petty_cash_realization_id`, `efn_petty_cash_fund_id` | — |
| wizard laporan → `efn.petty.cash.report.wizard` (satu wizard, `report_kind` statement/outstanding/aging) | `action_xlsx`, `action_pdf` | transient |

### 2.4 Menu

Kustodian outlet tidak punya grup akuntansi, sehingga tidak bisa melihat root Finance (`account.menu_finance`, grup
readonly/invoice [T] `account_menuitem.xml:4-8`). Karena itu tetap ada **root sendiri**, seperti sumber:

| xmlid | Induk | Seq | Grup |
|---|---|---|---|
| `menu_efn_petty_cash_root` "Kas Kecil" (ikon CE `account`) | — | 57 (sesudah Accounting 56) | `group_petty_cash_user` |
| `…_funds` "Dana Kas Kecil" | root | 5 | user (rule: milik sendiri) |
| `…_requests` "Pengajuan" | root | 10 | user |
| `…_realizations` "Realisasi" | root | 20 | user |
| `…_to_approve` "Menunggu Persetujuan" | root | 25 | `group_petty_cash_approver` |
| `…_reporting` "Laporan" (Kartu Uang Muka, Outstanding, Aging) | root | 30 | `group_petty_cash_finance` |
| `…_config` "Konfigurasi" (Jenis, Fund) | root | 90 | `group_petty_cash_finance` |
| `…_finance_shortcut` "Kas Kecil & Uang Muka" | `efn_setup_menu.menu_efn_cash_bank` (Finance > Kas & Bank) | 40 | `group_petty_cash_finance` |

Menu pintasan Finance memerlukan `efn_setup_menu`. Agar dependency tidak keras, file menu pintasan dipisah ke modul
jembatan `auto_install` **atau** dependency `efn_setup_menu` diterima. Default: terima dependency, karena modul itu
dipasang di semua env. Root baru perlu didaftarkan ke `efn_web_home` `ROOT_MENU_GROUPS` kelompok "finance" (tindak
lanjut kecil).

### 2.5 Grup, ACL, rule, cron, sequence

- **Grup** (privilege `res_groups_privilege_efn_petty_cash`):
  - `group_petty_cash_user` "Kas Kecil / Pemohon & Kustodian";
  - `group_petty_cash_approver` "Kas Kecil / Penyetuju" (implies user);
  - `group_petty_cash_finance` "Kas Kecil / Finance" (implies user, **tidak** implies approver);
  - `group_petty_cash_limit_override` (tidak diberikan ke siapa pun);
  - tanpa `user_ids` admin default (sumber memberi admin);
  - SoD baru di `efn_setup_role`: `SOD-PC1` approver ⟂ finance (report_only, Q-PC4).
- **ACL:**
  - user: type/fund `r`, request/line/realization/line `rwc-`;
  - approver: request `rw--`;
  - finance: semua `rwcu` kecuali hapus request non-draft (dicegah `@api.ondelete`);
  - wizard laporan: finance `rwcu`;
  - `account.group_account_readonly`: read semua (auditor).
- **Rule:** global company untuk semua model. Rule grup user: `['|', ('holder_user_id','=',user.id),
  ('maker_id','=',user.id)]` (request/realization), `[('holder_user_id','=',user.id)]` (fund). Approver/finance:
  `[(1,'=',1)]` dibatasi rule global company. Rule sumber "Finance all" tanpa batas company diganti.
- **Cron** `ir_cron_efn_petty_cash_reminder` (asal `cron_realization_reminder`), harian:
  - aktivitas ke `holder_user_id` untuk request lewat tenggat realisasi;
  - aktivitas ke kustodian bila `unreplenished_amount ≥ 70% float_amount` (parameter `efn_petty_cash.replenish_threshold_pct`,
    default 70).
- **Sequence:** per type (sumber). Default `efn.petty.cash.request` `KK/%(year)s/` padding 5,
  `efn.petty.cash.realization` `KKR/%(year)s/`.
- **Parameter:** `efn_petty_cash.disburse_via_payment` (default 0 = jurnal entry; sumber), `efn_petty_cash.realization_days` (14).
- **Data contoh di `demo/`** (bukan `data/`; nilai keputusan klien):
  - jenis "Kas Kecil Outlet" dan "Uang Muka Perjalanan" per PT;
  - akun uang muka **placeholder**: `l10n_id` tidak punya akun uang muka karyawan yang reconcilable. `11110010` Kas
    Kecil = `asset_cash` (tidak bisa jadi akun uang muka); `21100013` "Piutang Karyawan" ternyata liabilitas
    `liability_current` [T]. Jadi Finance harus membuat akun aset lancar reconcilable, mis. "Uang Muka Kas Kecil" dan
    "Uang Muka Karyawan" (Q-PC1);
  - satu fund demo Rp 5.000.000.
- **efn_audit rule:** `efn.petty.cash.type` (akun, jurnal, plafon, enforcement), `efn.petty.cash.fund` (float, holder,
  jurnal), `efn.petty.cash.request` (amount, state, holder).

### 2.6 RPC allowlist

| (model, method) | Cek akses wajib |
|---|---|
| `efn.petty.cash.request` `action_submit`, `action_cancel`, `action_reset_to_draft` | `check_access('write')` (rule milik sendiri) + state |
| `…request` `action_approve`, `action_reject` | `_efn_check_approver` (grup approver + check_access + company + empat mata) |
| `…request` `action_disburse`, `action_return_balance`, `action_reimburse_shortfall`, `action_settle` | `_efn_check_finance` (grup finance + check_access + company + pencair ≠ pemohon/holder) + cek plafon (`_pc_check_limits` → `_efn_check_limits`) |
| `…request` `action_open_realization`, `action_view_moves`, `action_view_realizations` | `check_access('read')` (hanya action) |
| `efn.petty.cash.realization` `action_submit`, `action_cancel`, `action_reset_to_draft` | `check_access('write')` + state |
| `…realization` `action_post` | `_efn_check_finance` + pemosting ≠ pembuat/holder |
| `…realization` `action_view_bills`; `efn.petty.cash.type` `action_view_requests`; `efn.petty.cash.fund` `action_view_*` | `check_access('read')` |
| `efn.petty.cash.fund` `action_request_replenishment` | `check_access('read')` fund + user = holder atau finance; membuat request draft dengan hak user |
| `efn.petty.cash.fund` `action_close_fund` | `_efn_check_finance` |
| `efn.petty.cash.report.wizard` `action_xlsx`, `action_pdf` | grup finance/readonly + company ∈ `env.companies` |

Semua `sudo()` sumber hanya membaca `ir.config_parameter` dan `account.analytic.plan`. Yang kedua dibuang (K-PC6), yang
pertama aman.

### 2.7 Depends, file port, test, ukuran

- **depends:** `account`, `mail`, `efn_audit`, `efn_setup_menu`.
- **Port:** `models/petty_cash_{type,request,request_line,realization,realization_line}.py`, `account_move.py`,
  `views/*` (minus field approval/OU/PPh), `reports/petty_cash_vouchers.xml` (gotcha sumber: `<style>` di dalam
  `<main>`), `data/ir_sequence_data.xml`, `data/mail_template_data.xml`, `data/ir_cron_data.xml`.
- **Drop:** `hooks.py`, `models/hr_employee.py`, `models/hr_job.py`, `models/res_company.py`,
  `models/res_config_settings.py` + view (parameter cukup di `ir.config_parameter`), `models/report_*.py`,
  `reports/petty_cash_templates.xml`, `wizard/*` (diganti satu wizard), `migrations/`.
- **Baru:** `efn.petty.cash.fund`, realisasi terhadap fund, empat mata, wizard laporan.
- **Test port:**
  - `test_request_flow.py` (ganti employee → holder partner; catatan sumber: `test_third_party_requires_attachment`
    gagal di tenant ber-`hr_expense`, jadi pastikan hijau di EFN);
  - `test_limits.py` (hapus kasus job/employee);
  - `test_advance_type.py`, `test_multicurrency.py`;
  - `test_statement_report.py` → uji wizard XLSX/aging.
- **Test baru:**
  - empat mata: pemohon = penyetuju → `UserError`; pencair = pemohon → ditolak;
  - fund imprest: realisasi → `unreplenished_amount` → pengisian ulang → saldo kembali ke float;
  - kustodian PT A tidak melihat fund PT B, kustodian lain di PT sama tidak melihat fund orang lain;
  - RPC `action_disburse` oleh user biasa → `AccessError`.
- **Ukuran:** ±2.300 Python (port ±1.800 setelah buang hr/approval/report + baru ±500), ±1.100 XML, ±1.100 test.

---

## 3. G58 — `efn_account_reconcile` (rekonsiliasi akun & bank)

**Asal:** `S/ee_gap/custom_account_reconcile` (19.0.3.0.0, depends `account` saja) + bagian generik
`S/_tenants/custom_levis_bank_reconcile`.

**CE 19 yang sudah ada [T]** (tidak diduplikasi):
- `account.move.line.reconcile()` (`account_move_line.py:3142`) dan `remove_move_reconcile()` (`:3146`);
- server action unreconcile (`account/wizard/account_unreconcile_view.xml:5`);
- model rekonsiliasi `account.reconcile.model` (`trigger` manual/auto_reconcile, `account_reconcile_model.py:110`), tetapi
  **mesin penerapannya** (widget rekonsiliasi bank) ada di Enterprise;
- `account.bank.statement.line.action_undo_reconciliation` (`account_bank_statement_line.py:460`);
- peringatan pembayaran ganda `account.payment.duplicate_payment_ids` / `_fetch_duplicate_reference` (`account_payment.py:197,793`,
  hanya state draft/in_process).

Tidak ada wizard rekonsiliasi di `account/wizard/` CE 19 [T].

### 3.1 Model & fitur

| Komponen (asal → EFN) | Isi | company / keamanan |
|---|---|---|
| `custom.reconcile.account` (SQL view) → **`efn.reconcile.account`** | ringkasan akun reconcilable dengan item terbuka. **Perubahan wajib:** `GROUP BY ml.company_id, aa.id`, `id = row_number()`/kombinasi, tambah kolom `company_id`, `currency_id` = mata uang company baris | Sumber menjumlah **semua company** (bocor nilai antar-PT; gotcha sumber). EFN: kolom `company_id` + record rule company. `init()` masuk allowlist ("hook ORM init, tanpa argumen") |
| `custom.account.reconcile.wizard` → `efn.account.reconcile.wizard` | rekonsiliasi item jurnal terpilih (+ write-off mata uang company) | transient; `default_get` memvalidasi satu company & satu akun (sumber) |
| `custom.bank.reconcile.wizard(.line)` → `efn.bank.reconcile.wizard(.line)` | pencocokan baris mutasi dengan kandidat (skor), "Cari lagi", write-off | transient |
| `account.bank.statement.line` (`_inherit`) | `_get_match_candidates` → `_efn_get_match_candidates`, `_get_auto_match_candidate` → `_efn_…`, `_reconcile_with_amls` → `_efn_reconcile_with_amls`, tombol `action_efn_open_match_wizard`, `action_efn_auto_match` | lihat §3.4 |
| `account.move` (`_inherit`) `button_draft` | unreconcile dulu sebelum `super()`. **Terverifikasi perlu:** `button_draft` CE 19 tidak memanggil `remove_move_reconcile` [T] `account_move.py:6271-6286` | override bersama `efn_intercompany`/`efn_account_asset` (MRO; test urutan) |
| `account.move.line` (`_inherit`) `write` | tolak ubah `account_id`/`partner_id` pada baris yang masih punya partial; escape `skip_reconciliation_guard` (context) **hanya bila `env.su`** (sumber: context saja, bisa diisi klien RPC) | |
| `account.payment` (`_inherit`) | `efn_duplicate_checked` (Boolean) + blokir posting bila ada kembaran posted; `efn_is_unapplied` (stored compute) | **Sederhanakan:** pakai `_fetch_duplicate_reference(matching_states=('in_process','paid'))` CE alih-alih `search` sendiri. CE hanya memberi peringatan; port memblokir |

### 3.2 Menu

| xmlid | Induk | Seq | Grup |
|---|---|---|---|
| `menu_efn_reconcile_section` "Rekonsiliasi" | `account.menu_finance_entries` (Accounting > Jurnal & Penutupan) | 10 | `account.group_account_user` |
| `…_accounts` "Rekonsiliasi Akun" | section | 10 | readonly bisa lihat; tombol hanya user |
| `…_bank` "Rekonsiliasi Bank" | section | 20 | `account.group_account_user` |
| `…_unapplied` "Pembayaran Belum Teralokasi" | section | 30 | `account.group_account_readonly` |

Action kontekstual "Rekonsiliasi" di list `account.move.line` dan "Cocokkan otomatis" di list baris mutasi (binding
`account.model_account_move_line`, `account.model_account_bank_statement_line`, `groups_id` = `account.group_account_user`).

### 3.3 Bagian generik dari `custom_levis_bank_reconcile` yang diport

| Ide (asal) | Versi EFN |
|---|---|
| Kandidat tidak boleh bertanggal **setelah** tanggal mutasi (`_get_default_amls_matching_domain` + `date <= st_line.date`) | diport sebagai default, dengan parameter `efn_account_reconcile.date_ceiling` (default 1) |
| Settlement kartu/QRIS masuk **neto MDR**: target = bruto, biaya MDR diisi otomatis sebagai write-off ke akun MDR + analitik | **Diselaraskan dengan G60 D-5** (`docs/fit-gap/G59_G60_ACCOUNTING_PORT.md` §G60-B). G60 mengestimasi MDR saat tutup sesi POS (`efn.mdr.rate`); sisa selisih (*true-up*) = **`account.reconcile.model` CE** (trigger manual, baris `percentage`/`percentage_st_line`/`fixed`/`regex` + `analytic_distribution`). Karena mesin penerapan model ada di Enterprise, G58 menambah field `reconcile_model_id` di wizard pencocokan. Model ini (domain: company + jurnal mutasi, `trigger='manual'`) mengisi **baris write-off** dari baris model atas sisa selisih, sehingga `_efn_reconcile_with_amls` menerima **daftar** write-off (bukan satu dict seperti sumber). G58 **tidak** membuat model konfigurasi MDR sendiri dan tidak bergantung pada `efn.mdr.rate` |
| Setoran tunai = gabungan beberapa hari: tombol **"Isi otomatis"** (terbesar dulu, tidak melebihi nominal; sisa tetap di suspense) | diport generik (`action_efn_suggest`) untuk semua baris |
| Setoran tunai **tidak pernah** dicocokkan otomatis | diport: field `account.account.efn_no_auto_match` (Boolean, Finance mencentang akun outstanding kas POS). `_efn_get_auto_match_candidate` mengembalikan kosong bila kandidat ada di akun bertanda itu |
| Halaman penuh (`target=current`) untuk wizard pencocokan + kolom "No. Transaksi" (ref baris → ref move → payment_reference → origin) | diport (view `form` halaman + field related `efn_transaction_ref` di `account.move.line`, non-stored) |
| Parser narasi MID, "Store Not Identified", "Re-read Bank Narrative", OU Levi's, `levis.clearing.config` | **dibuang** (tergantung format narasi bank EFN; tindak lanjut setelah contoh mutasi QRIS/EDC per bank — Q-G58-3) |

### 3.4 RPC allowlist & keamanan

| (model, method) | Cek akses wajib |
|---|---|
| `account.bank.statement.line` `action_efn_open_match_wizard` | grup `account.group_account_user` + `check_access('write')` + company ∈ `env.companies` |
| `account.bank.statement.line` `action_efn_auto_match` | sama. Sumber tanpa cek grup: user Billing yang punya write `account.move` bisa merekonsiliasi lewat RPC |
| `efn.bank.reconcile.wizard` `action_reconcile`, `action_search_more`, `action_efn_suggest`, `default_get` | grup user + `check_access('write')` baris mutasi; `default_get` membaca dengan hak user (tanpa sudo) |
| `efn.account.reconcile.wizard` `action_reconcile`, `default_get` | grup user + `check_access('write')` item + satu company |
| `efn.reconcile.account` `init` | hook ORM init |
| `efn.reconcile.account` `action_open_lines` | `check_access('read')` (action tanpa sudo; ACL CE berlaku) |
| `account.move` `button_draft` | deskripsi allowlist diperbarui (+ unreconcile) |
| `account.payment` `action_post` | **entri baru**: "override CE (efn_account_reconcile: blokir kembaran posted kecuali `efn_duplicate_checked`)" |

- Konteks `force_delete/skip_readonly_check/skip_account_move_synchronization` di `_efn_reconcile_with_amls` dipakai
  dengan hak user (tanpa sudo).
- Lock date tetap dicek CE saat write move.
- PII: kandidat menampilkan partner (nama vendor/pelanggan) sesuai ACL CE. Tidak ada log isi `payment_ref`.

### 3.5 Depends, file, test, ukuran

- **depends:** `account` saja (sama dengan sumber). Bukan `efn_bank_import`.
- **Port:** semua `models/*`, `wizard/*`, `views/*`. Dari Levi's: potongan `wizard/bank_reconcile_wizard.py`
  (suggest, default write-off, halaman), `models/account_move_line.py` (`levis_transaction_ref` →
  `efn_transaction_ref`), override domain tanggal.
- **Drop:** semua yang memanggil `levis_*`/`l10n_ou_*`.
- **Test port:** `test_bank_reconcile.py`, `test_reconciliation_guards.py`, dan dari Levi's
  `test_bank_reconcile_tender.py` dalam versi generik (write-off dari `account.reconcile.model` baris `percentage`/`fixed`
  + analitik, suggest, tanpa auto-match tunai). `amount_type` CE 19 (`percentage`, `percentage_st_line`, `regex`, …)
  [T] `odoo19src/account/models/account_reconcile_model.py:25-30`.
- **Test baru:** view ringkasan tidak menampilkan nilai PT lain; RPC `action_efn_auto_match` oleh user Billing →
  `AccessError`; `skip_reconciliation_guard` dari context non-su diabaikan.
- **Ukuran:** ±1.000 Python, ±450 XML, ±700 test.

---

## 4. G58 — `efn_bank_import` (impor mutasi bank CSV)

**Asal:** `S/ee_gap/custom_bank_import` (19.0.0.3.0).

**CE 19:**
- impor file mutasi (`account_bank_statement_import*`) = Enterprise;
- `unique_import_id` tidak ada di `account.bank.statement.line` CE 19 [T] (grep `account_bank_statement_line.py`);
- CE `base_import` bisa mengimpor `account.bank.statement.line` dari CSV generik dengan template pemetaan tersimpan.
  Ini cukup untuk file yang rapi, tetapi **tidak** menangani format BCA KlikBCA Bisnis (tanggal tanpa tahun, nominal
  dan tanda DB/CR dalam satu sel), dedup file, dan validasi lock date. Itulah alasan port.

### 4.1 Lingkup (keputusan: CSV saja)

| Diport | Dibuang |
|---|---|
| Template generik `custom.bank.import.template` → `efn.bank.import.template` (encoding, delimiter, header, format tanggal, indeks kolom 1-based, pemisah desimal/ribuan, kolom bertanda vs debit/kredit) | H2H: `bank_h2h_adapter.py`, `bank_h2h_connection.py`, view/menu/cron H2H, `custom_adapter_framework` |
| Deteksi otomatis **BCA korporat (KlikBCA Bisnis CSV)**: `_is_bca_corp`, `_parse_bca_corp`, `_bca_corp_amount` | Parser XLS/XLSX: **BRI BRISIM** (XLSX), **BNI Transaction Inquiry** (XLS), **Mandiri Acc_Statement** (XLS), yaitu `_read_xls`, `_read_xlsx`, `_is_brisim`/`_parse_brisim`, `_is_trx_inquiry`/`_parse_trx_inquiry`, `_is_acc_statement`/`_parse_acc_statement`. Ditunda ke fase 2 (Q-G58-1). `xlrd 2.0.1`/`openpyxl 3.1.2` ada di requirements CE 19 [T GH requirements.txt]; terpasang di image `efn-odoo` [B] |
| Log impor `custom.bank.import.log` → `efn.bank.import.log` (hash SHA-256, status, jumlah baris/error) | Bypass lock date (`BYPASS_LOCK_CHECK`, `S/…/wizard/bank_import_csv_wizard.py:135`) |
| Wizard `…csv.wizard` → `efn.bank.import.wizard` | `raw_payload` H2H |

Mandiri/BNI/BRI di fase 1: pakai template generik bila bank menyediakan ekspor CSV. Format resmi per bank
**belum ada contoh** di repo (Q-G58-1).

### 4.2 Perubahan wajib dibanding sumber

1. **Lock date fail-closed.** Sumber menulis ulang tanggal transaksi ke periode yang soft-locked dengan
   `bypass_lock_check`, sehingga melewati kontrol tutup buku. EFN: tolak seluruh impor bila ada tanggal ≤
   `fiscalyear_lock_date` **atau** `hard_lock_date`, dengan pesan jumlah dan tanggal terburuk. Finance membuka lock date
   atau memotong file.
2. **Dedup per company dan per baris.**
   - Pencarian hash file sumber memakai sudo lintas company. EFN: `[('file_hash','=',h), ('company_id','=',journal.company_id.id)]`.
   - Tambah field `efn_import_fingerprint` (Char, index) di `account.bank.statement.line` = sha256(journal | tanggal |
     nominal | ref | saldo | nomor kemunculan). Baris dengan fingerprint yang sudah ada di jurnal itu dilewati dan
     dihitung di log, sehingga mutasi yang tumpang tindih antar file tidak tergandakan.
3. **Template:** `company_id` required + rule; kode unik per company (`models.Constraint`).
4. **Encoding:** `errors="replace"` sumber merusak ref. EFN: coba `utf-8-sig`, lalu `cp1252`. Bila gagal, tolak dengan
   pesan.
5. **Baris nol** tetap dilewati (sumber), tetapi dihitung `skipped_zero` di log.
6. **Pesan error log** = `baris N: <kode error>` (`invalid_date`, `invalid_amount`, `missing_column`). Tanpa isi sel,
   karena narasi mutasi memuat nama dan nomor rekening lawan transaksi. Dipotong 200 baris.
7. **Ukuran file** maks 10 MB, maks 20.000 baris (parameter). Tolak file lain dengan pesan yang jelas.
8. `account_number`/`partner_name` baris mutasi CE **tidak** diisi parser (sumber juga tidak). Narasi masuk
   `payment_ref` (≤255).

### 4.3 Model, menu, grup

| Model | Field kunci | company / rule |
|---|---|---|
| `efn.bank.import.template` | lihat 4.1 + `bank_id` (res.bank), `active`, `sample_file` **dibuang** (contoh mutasi asli = data rahasia) | rule company |
| `efn.bank.import.log` (`mail.thread`) | `company_id` (related jurnal, stored), `journal_id`, `statement_id`, `template_id`, `filename`, `file_hash`, `state` (imported/partial/failed), `line_count`, `duplicate_count`, `skipped_zero`, `error_count`, `error_message` | rule company; **immutable** setelah dibuat (`write` hanya `active`) |
| `efn.bank.import.wizard` | `journal_id` (domain bank, company user), `template_id` (company sama), `file`, `filename`, `statement_name` | transient |
| `account.bank.statement.line` (`_inherit`) | `efn_import_fingerprint`, `efn_import_log_id` | — |

- **Menu:**
  - `menu_efn_bank_import` "Impor Mutasi Bank" di `efn_setup_menu.menu_efn_cash_bank` (Finance > Kas & Bank), seq 15,
    grup `account.group_account_user`;
  - `…_logs` "Log Impor Mutasi" seq 16;
  - "Template Mutasi Bank" di `account.menu_finance_configuration`, grup `account.group_account_manager`.
- **Grup/ACL:** tanpa grup baru.
  - template: user `r`, manager `rwcu`;
  - log: readonly `r`, user `rc`, tanpa write/unlink kecuali `active`;
  - wizard: user `rwcu`.
- **Cron:** tidak ada (H2H dibuang).
- **Data:** template `bca_corp_csv` (deteksi otomatis) + `generic_signed_csv` contoh per company di `data/` (struktur,
  bukan data klien).
- **efn_audit rule:** `efn.bank.import.template` (semua field).

### 4.4 RPC allowlist

| (model, method) | Cek |
|---|---|
| `efn.bank.import.wizard` `action_import` | grup `account.group_account_user` + `check_access('write')` jurnal (`journal.check_access`) + company jurnal ∈ `env.companies` + template milik company yang sama; log ditulis `sudo()` **setelah** cek |
| `efn.bank.import.template` `parse_csv` → **jadikan privat** `_efn_parse(file_bytes)` | tidak masuk allowlist (sumber publik: siapa pun bisa memanggil parser lewat RPC) |

### 4.5 Depends, file, test, ukuran

- **depends:** `account`, `efn_audit`, `efn_setup_menu`.
- **Port:** `models/bank_import_template.py` (bagian CSV + BCA), `models/bank_import_log.py`,
  `wizard/bank_import_csv_wizard.py`, view template/log/wizard, `security/*`.
- **Drop:** H2H, adapter, `data/cron.xml`, bagian XLS/XLSX.
- **Test port:** `test_bank_import.py` (kasus CSV generik + BCA korporat; buang kasus XLSX/H2H).
- **Test baru:** impor ganda file → ditolak; file tumpang tindih → baris lama dilewati; tanggal ≤ lock date → ditolak;
  PT B tidak melihat log PT A; pesan error tanpa isi sel; parser tidak bisa dipanggil RPC.
- **Ukuran:** ±650 Python, ±250 XML, ±450 test.

---

## 5. G58 — `efn_account_batch_payment` (batch pembayaran + file transfer bank)

**Asal:** `S/ee_gap/custom_account_batch_payment` (19.0.1.0.0, depends `account`).

**CE 19:**
- batch payment = Enterprise;
- yang dipakai dari CE [T]: `account.payment.is_sent`, `mark_as_sent`/`unmark_as_sent` (`account_payment.py:54,1124,1127`),
  `partner_bank_id` (`:59`), `memo` (`:107`), `is_matched` (`:52`).

### 5.1 Model

| Model (asal → EFN) | Field kunci | company / rule |
|---|---|---|
| `custom.account.batch.payment` → **`efn.account.batch.payment`** (`mail.thread`) | `name` (sequence), `journal_id` (bank, check_company), `company_id` (**stored**; sumber related), `currency_id`, `date`, `batch_type` (outbound/inbound), `payment_ids`, `payment_count`, `amount_total`, `export_format_id`, `state` (draft/validated/sent/reconciled), **baru**: `export_sha256`, `exported_by_id`, `exported_at`, `validated_by_id` | rule company |
| `custom.batch.payment.format` → **`efn.account.batch.payment.format`** | `name`, `code`, `bank_label`, `encoding`, `delimiter`, `date_format`, `file_extension`, `include_header`, `note`. **Pindah ke per-company:** `corporate_id`, `debit_account` → model baru `efn.account.batch.payment.format.company` (format × company × jurnal: kode korporat & rekening sumber) | format global (tanpa data rahasia); konfigurasi per company + rule |
| `account.payment` (`_inherit`) | `efn_batch_payment_id` (index `btree_not_null`), `efn_partner_bank_masked` (computed non-stored `****123` untuk list batch) | — |

### 5.2 Format file & PII (UU PDP)

- **Seed format** (`data/`, noupdate): `generic`, `bca_mcm`, `mandiri_mcm`, `bni`, `bri`. Tata letak sumber berstatus
  **"unverified baselines"** (`MODULE_KNOWLEDGE.md` sumber), sehingga setiap format ditandai `note = "BELUM DIVERIFIKASI
  dengan contoh portal bank"`.
  - Ekspor ke bank selain `generic` **diblokir** sampai Finance mencentang `verified` pada format, setelah uji unggah
    di portal bank (Q-G58-2).
- **CSV benar:** ganti `delim.join` sumber dengan modul `csv` (quoting minimal).
  - Memo/nama disanitasi: buang delimiter, CR/LF, dan karakter di luar `[A-Za-z0-9 .,/-]`; panjang dipotong per format.
  - Nilai yang diawali `= + - @` diberi spasi depan, untuk mencegah injeksi formula bila dibuka di spreadsheet.
- **Nomor rekening penerima:**
  - Rekening vendor perorangan/karyawan disimpan terenkripsi `efn_pii` (`res.partner.bank.acc_number` = `EfnPiiChar`,
    `efn_pii/models/res_partner_bank.py:48`). Ekspor membaca lewat ORM (terdekripsi di proses Odoo).
  - Bila keyring terkunci, nilainya `PLACEHOLDER`. **Wajib fail-closed**: tolak ekspor bila ada `acc_number` yang
    placeholder/kosong/bukan digit. Jangan pernah menulis placeholder ke file.
- **File tidak disimpan** (keputusan default, beda dengan sumber yang menyimpan `export_file` sebagai attachment):
  - file dibuat saat diunduh (`ir.actions.act_url` ke controller download dengan cek grup, **atau** attachment
    sementara `res_model=efn.account.batch.payment`, `public=False`, dihapus cron harian setelah 24 jam);
  - yang disimpan hanya `export_sha256`, `exported_by_id`, `exported_at`;
  - alasan: filestore belum terenkripsi at rest per file (prinsip CLAUDE.md), dan file berisi nomor rekening orang.
  - **Default dipilih: attachment sementara 24 jam** (tanpa controller baru = tanpa permukaan route baru).
- **Log:**
  - tanpa nomor rekening atau nama;
  - pesan "rekening kosong" memakai nomor payment (sumber sudah).
- **Transmisi:** user mengunggah manual ke portal bank lewat HTTPS. Tidak ada H2H.

### 5.3 Alur & empat mata

Sumber: draft → validate → generate file (state `sent`, `mark_as_sent`) → `reconciled` otomatis bila semua payment
`paid` + `is_matched`. EFN menambah:
- `action_validate` oleh `account.group_account_user`;
- `action_generate_export_file` oleh `account.group_account_manager` **dan** user ≠ `validated_by_id` (empat mata
  sebelum uang keluar);
- `action_draft` setelah `sent` hanya manager. Langkah ini meng-`unmark_as_sent` dan mengosongkan jejak ekspor (jejak
  lama tetap di chatter/efn_audit).
- **Koordinasi G60** (`G59_G60_ACCOUNTING_PORT.md` §0 asumsi 2): persetujuan pembayaran ada di `account.payment`
  (`efn_approval_state`, modul `efn_account_localization`).
  - `action_validate` dan `action_efn_create_batch` menolak payment dengan `efn_approval_state not in ('not_required',
    'approved')` **bila field itu ada** (`'efn_approval_state' in payment._fields`), tanpa dependency keras.
  - Empat mata validasi ≠ ekspor tetap berlaku: yang dilindungi adalah rilis file, bukan persetujuan per payment.

### 5.4 Menu, grup, ACL, sequence

- **Menu:**
  - `menu_efn_batch_payment` "Batch Pembayaran" di `account.menu_finance_payables` (Finance > Vendors), seq 25,
    grup `account.group_account_user`;
  - "Format File Transfer Bank" di `account.menu_finance_configuration`, grup manager;
  - server action "Tambahkan ke Batch" di list `account.payment` (`groups_id` user).
- **Grup:** tanpa grup baru.
- **ACL:**
  - batch: readonly `r`, user `rwc-` (hapus hanya draft lewat `@api.ondelete`), manager `rwcu`;
  - format: readonly `r`, manager `rwcu`;
  - format.company: manager `rwcu`, user `r`.
- **Sequence:** `efn.account.batch.payment` prefix `BATCH/%(year)s/%(month)s/` padding 4 (sumber).
- **efn_audit rule:**
  - `efn.account.batch.payment`: `state`, `export_sha256`, `exported_by_id`, `payment_ids`;
  - `efn.account.batch.payment.format.company`: `debit_account` di-mask otomatis karena nama field mengandung
    `account`. Kebijakan `acc_number` hanya cocok untuk nama field `acc_number`, jadi tambahkan `debit_account` ke
    `masked_field_ids` rule.

### 5.5 RPC allowlist

| (model, method) | Cek |
|---|---|
| `efn.account.batch.payment` `action_validate`, `action_open_payments` | grup user + `check_access('write'/'read')` + company |
| `…batch.payment` `action_generate_export_file` | grup manager + `check_access('write')` + empat mata + format terverifikasi + rekening valid (fail-closed) |
| `…batch.payment` `action_draft` | grup manager + `check_access('write')` |
| `account.payment` `action_create_batch_from_selection` → `action_efn_create_batch` | grup user + `check_access('write')` payment + satu company/jurnal/arah (sumber) |
| `efn.account.batch.payment.format` `render` → **privat** `_efn_render(batch)` | tidak masuk allowlist |

### 5.6 Depends, file, test, ukuran

- **depends:** `account`, `efn_audit`.
- **Port:** `models/*`, `views/*`, `data/batch_payment_sequence.xml`, `data/batch_export_formats.xml`.
- **Drop:** penyimpanan permanen `export_file`.
- **Test port:** `test_batch_payment.py`.
- **Test baru:**
  - memo dengan koma/newline/`=` tetap satu baris yang valid;
  - keyring terkunci → ekspor ditolak, tanpa file;
  - validator = pengekspor → ditolak;
  - format belum terverifikasi → ditolak;
  - PT B tidak melihat batch PT A;
  - log/attachment tidak memuat nomor rekening setelah 24 jam (cron).
- **Ukuran:** ±550 Python, ±250 XML, ±400 test.

---

## 6. Urutan pemasangan & grafik dependency

```
account (CE) ─┬─ efn_core ── efn_audit ─┬─ efn_account_asset
              │                         ├─ efn_account_batch_payment
              │                         ├─ efn_bank_import ──────────┐
              │                         └─ efn_petty_cash ───────────┤
              ├─ mrp (CE) ── efn_setup_menu ─────────────────────────┘ (menu Kas & Bank)
              └─ efn_account_reconcile   (account saja)
```

Tidak ada dependency keras antar kelima modul. Urutan gelombang implementasi yang disarankan (nilai bisnis & risiko):

1. `efn_account_reconcile`. Kecil, depends `account` saja, dan langsung memperbaiki `button_draft` CE 19 yang membiarkan
   rekonsiliasi.
2. `efn_bank_import`. Mutasi BCA masuk, lalu dicocokkan dengan (1).
3. `efn_account_asset`. Memerlukan G61 sudah dijalankan agar `categ_ids` terisi; seed bisa dijalankan ulang.
4. `efn_petty_cash`.
5. `efn_account_batch_payment`. Terakhir, karena format bank belum terverifikasi (Q-G58-2).

**Interaksi yang wajib diuji:**
- `account.move.button_draft` dioverride `efn_intercompany`, `efn_account_reconcile`, `efn_account_asset`. Semua harus
  memanggil `super()`. Test: draft jurnal penyusutan yang direkonsiliasi → unreconcile + baris un-post, di kedua urutan
  pemasangan.
- `account.payment.action_post`: `efn_account_reconcile` (blok duplikat) + pencairan `efn_petty_cash` bila
  `disburse_via_payment=1`. Pencairan kas kecil berulang dengan nominal dan tanggal sama ke kustodian yang sama akan
  terblokir sebagai duplikat, jadi isi `efn_duplicate_checked` otomatis bila `efn_petty_cash_request_id` berbeda.
- `account.move._post`: `efn_account_asset` (buat draf aset) + modul lain yang meng-override `_post`
  (`efn_l10n_id_coretax_rounding` [B] — cek apakah meng-override `_post`).

**Daftar modul baku env** (G63 P1): tambahkan kelima modul setelah lulus QA. `efn_setup_role` perlu memetakan grup baru
(aset, kas kecil) ke persona (tindak lanjut kecil, data-only).

---

## 7. Ringkasan RPC allowlist yang harus ditambahkan ke `efn_core/tests/test_rpc_surface.py`

Entri baru ±45 (model, method), dirinci di §1.7, §2.6, §3.4, §4.4, §5.5. Entri yang **diperbarui deskripsinya**:
`("account.move", "button_draft")`. Entri baru untuk override CE: `("account.payment", "action_post")`, dan
`default_get` setiap wizard yang meng-override-nya. Prinsip: setiap method tombol memanggil helper cek
(`_efn_check_manager`, `_efn_check_approver`, `_efn_check_finance`) **sebelum** `sudo()` apa pun. Method yang hanya
mengembalikan action tetap memanggil `check_access('read')`.

---

## 8. PII & enkripsi (UU PDP 27/2022, ADR-022, ADR-036)

| Data | Di mana | Aturan |
|---|---|---|
| Nomor rekening penerima (vendor perorangan, karyawan) | `res.partner.bank` (CE) dibaca batch export | Terenkripsi `efn_pii` at rest. Dekripsi hanya di proses Odoo saat ekspor; keyring terkunci → tolak. File transfer tidak disimpan permanen (§5.2). UI batch menampilkan `****123` |
| Rekening sumber perusahaan (`debit_account`) | `efn.account.batch.payment.format.company` | rekening badan usaha (bukan data pribadi), tetap di-mask di log audit |
| Narasi mutasi (nama & rekening lawan transaksi) | `account.bank.statement.line.payment_ref` | data keuangan perusahaan; tidak di-log, tidak dikirim ke luar Odoo; error impor tanpa isi sel; tidak mengisi `account_number`/`partner_name` |
| File mutasi asli | wizard impor | tidak disimpan (hanya hash), sama dengan sumber; `sample_file` template dibuang |
| Nama kustodian/pemohon, nota | `efn.petty.cash.*`, lampiran | akses lewat rule milik sendiri/company; nama tidak di-log; nama di log audit = inisial (efn_audit) |
| Retensi | — | jurnal & dokumen pendukung mengikuti retensi dokumen pajak/perusahaan (10 tahun, UU KUP — [B], konfirmasi Legal/Finance); attachment ekspor batch sementara 24 jam |

Koneksi: tidak ada koneksi keluar baru (H2H dibuang). Unduh/unggah file lewat web Odoo di belakang Traefik TLS.

---

## 9. Pertanyaan terbuka (default sudah dipakai di spesifikasi)

| ID | Pertanyaan | Default |
|---|---|---|
| Q-G57-1 | Sub-akun aset untuk mesin, furnitur, peralatan, dan akun software/amortisasi (A10)? | 1 akun `12210030` bersama; software tanpa akun (konfirmasi aset diblokir) |
| Q-G57-2 | Umur ekonomis per kelompok (komersial vs fiskal PMK, renovasi gedung sewa = masa sewa?) | 60/96/48/48/48/96 bulan (placeholder, satu buku; tanpa buku pajak paralel — batasan sumber) |
| Q-G57-3 | LVA tetap dibebankan (G61 A6)? | ya; kelompok LVA hanya di `demo/` |
| Q-G57-4 | Ada register aset lama (ESB/Excel) untuk saldo awal? | impor CSV draft + `opening_accumulated_depreciation` oleh Finance, tanpa jurnal (GL dari saldo awal ADR-042) |
| Q-G57-5 | Nota kredit vendor atas aset: kurangi aset otomatis? | tidak; Finance pakai pelepasan sebagian/manual |
| Q-G57-6 | Jurnal penyusutan sendiri `DEPR` atau `MISC`? | `DEPR` per PT |
| Q-PC1 | Akun uang muka (kas kecil & karyawan) — l10n_id tidak punya akun aset reconcilable yang cocok | Finance membuat 2 akun; demo memakai akun buatan test |
| Q-PC2 | Kas kecil outlet dicatat sebagai **uang muka kustodian** (default) atau **jurnal kas outlet** sendiri (`cash_journal_id`)? | uang muka kustodian (mesin sumber); jurnal kas opsional |
| Q-PC3 | Plafon fund outlet dan batas per transaksi per brand? | demo Rp 5 jt / Rp 1 jt; `limit_enforcement=warn` |
| Q-PC4 | SoD: penyetuju ≠ Finance pencair wajib (block) atau laporan saja? | report_only |
| Q-PC5 | Uang muka untuk staf tanpa user Odoo (kitchen, driver)? | ya, `holder_partner_id` bebas; rule milik sendiri berlaku via pemohon |
| Q-G58-1 | Contoh file mutasi Mandiri/BNI/BRI (CSV atau XLS?) dan aktifkan parser XLS/XLSX sumber? | fase 1 CSV (generik + BCA); XLS/XLSX fase 2 |
| Q-G58-2 | Spesifikasi resmi file bulk transfer BCA MCM / Mandiri MCM / BNI / BRI per kontrak EFN? | hanya `generic` yang boleh diekspor sampai format diverifikasi |
| Q-G58-3 | Contoh narasi settlement QRIS/EDC per bank (untuk identifikasi outlet & MDR otomatis) | true-up MDR lewat `account.reconcile.model` manual (G60 D-5); tanpa parser narasi |
| Q-G58-4 | Blokir pembayaran ganda (bukan hanya peringatan CE)? | blokir, dengan centang override |
| Q-G58-5 | File transfer disimpan permanen untuk audit? | tidak; simpan hash + pengekspor + waktu, attachment sementara 24 jam |

## 10. Belum terverifikasi

- `hr_expense` 19.0 (tidak di subset); klaim sumber "`hr.expense.sheet` dihapus di 19" [B].
- `xlrd`/`openpyxl` terpasang di image `efn-odoo` [B] (hanya relevan fase 2).
- Apakah `efn_l10n_id_coretax_rounding` atau modul EFN lain meng-override `account.move._post` / `button_draft` selain
  `efn_intercompany` [B — grep saat implementasi].
- Perilaku CE 19 saat cron memposting penyusutan bertanggal di periode soft-lock (digeser ke tanggal terbuka) — dari
  catatan sumber, belum diuji di EFN [B].
- Dampak bisnis `button_draft` tanpa unreconcile (status "Paid" palsu) — kode CE terverifikasi [T], skenario dari
  catatan sumber, diuji ulang lewat test port.
- Retensi 10 tahun dokumen keuangan [B, Legal].
- Kepemilikan kode `odoo-platform` (ADR-049 §Konsekuensi) — konfirmasi tertulis sebelum production.
