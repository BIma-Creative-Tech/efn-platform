# efn_account_asset — Aset Tetap (G57)

Versi `19.0.1.0.0`, LGPL-3, Odoo CE 19. Depends: `account`, `mail`, `efn_audit` (tanpa modul non-CE, ADR-031).
Spesifikasi: `docs/fit-gap/G57_G58_ACCOUNTING_PORT.md` §1. ADR: **ADR-049** (port & adaptasi kode milik pemilik
proyek), ADR-031, ADR-001 (satu DB multi-company), ADR-022. Terkait G61 (`docs/fit-gap/G61_KATEGORI_PRODUK_PB.md`).

## Tujuan

CE 19 tidak punya register aset (`account_asset` = Enterprise). Modul ini menyediakan:

- register aset per PT: nilai perolehan, nilai sisa, umur, metode garis lurus / saldo menurun / tanpa penyusutan;
- jadwal penyusutan bulanan dengan tiga aturan tanggal (`specific`, `next_month`, `end_following_month`);
- cron harian (OdooBot) yang memposting baris jatuh tempo, satu jurnal per tanggal (parameter
  `efn_account_asset.group_depreciation_moves`, default `1`);
- posting manual massal (wizard + server action list), pembalikan baris (penuh, atau parsial bila jurnal dipakai
  bersama), sinkron otomatis bila jurnal penyusutan di-draft/dihapus dari Accounting;
- revaluasi prospektif PSAK 16 / IAS 16 (surplus ekuitas vs L/R), disposal (laba/rugi vs nilai buku, surplus ke laba
  ditahan), aset pool (N unit satu nomor) + pelepasan sebagian, `_merge_assets_into_pool` (skrip `odoo shell`);
- laporan Daftar Aset & Penyusutan (XLSX + PDF);
- **baru (G57 §1.3):** tagihan vendor (`in_invoice`) yang diposting untuk produk berkategori kelompok aset
  (`categ_ids`, mis. `Aset Tetap / Mesin`) membuat **satu draf aset pool per baris** (jumlah = qty, nilai = DPP dalam
  mata uang PT). Finance memeriksa lalu mengonfirmasi;
- seed kelompok aset per PT ber-CoA `l10n_id` (pengganti seed tenant sumber).

## Default yang dipakai (pertanyaan terbuka §9, mudah diubah Finance)

| ID | Default |
|---|---|
| Q-G57-1 | Mesin, furnitur, peralatan memakai satu akun `12210030` (akum. `12281030`, beban `67100030`) |
| A10 | Perangkat Lunak (`SOFT`) tanpa akun -> `action_confirm` menolak sampai Finance membuat akun aset tak berwujud + amortisasi |
| Q-G57-2 | Umur PLACEHOLDER: Bangunan & Renovasi 60, Mesin 96, Furnitur 48, Peralatan Kantor & Outlet 48, Perangkat Lunak 48, Kendaraan 96 bulan |
| Q-G57-3 / A6 | LVA **dibebankan** (`63110120`): kategori `Aset Tetap / LVA` tidak dipetakan ke kelompok mana pun. Kelompok `LVA` hanya di `demo/` (`auto_create = no`) |
| Q-G57-5 | Nota kredit vendor tidak membuat/mengurangi aset (Finance pakai pelepasan sebagian) |
| Q-G57-6 | Jurnal penyusutan `DEPR` ("Penyusutan Aset") per PT, dibuat seed bila belum ada |

Kapitalisasi LVA bila Finance memutuskan: ubah akun beban kategori LVA ke akun aset, set kelompok `LVA`
`auto_create = draft` + akun, tambahkan kategori LVA ke `categ_ids`. Tanpa kode baru.

Seed (`_efn_seed_groups`, `<function>` tidak noupdate, jalan tiap `-u`): upsert per `(code, company)`, hanya mengisi
field kosong, akun dicari per PT (`code` + `company_ids`), kategori G61 dicari lewat `complete_name`. Kategori yang
belum ada -> satu `_logger.warning` per PT berisi nama kategori. PT supermarket (`efn_business_type = retail`, SMD)
mendapat kelompok tanpa `categ_ids`. Tombol "Pasang ulang kategori G61" (list Kelompok Aset, Aset / Manager) menjalankan
seed untuk PT aktif user setelah G61 dijalankan.

## Keamanan & multi-company

- Grup (privilege "Aset Tetap"): **Aset / Pengguna** (baca, buat/ubah draft) dan **Aset / Manager** (implies Pengguna:
  konfirmasi, disposal, revaluasi, pelepasan, posting, konfigurasi). Tanpa user default; pemetaan persona di
  `efn_setup_role` = tindak lanjut. Manager membuat jurnal dengan hak akuntansinya sendiri (tidak ada sudo untuk
  jurnal) -> persona Manager perlu grup Accounting juga.
- `account.group_account_readonly` (auditor): baca semua model + laporan.
- Rule global `company_id in company_ids` di 6 model tersimpan; `_check_company_auto` + `check_company` pada akun,
  jurnal, kelompok, lokasi.
- Cek di server, bukan view: semua tombol status/wizard memanggil `_efn_check_manager` (grup + `check_access('write')` +
  company aktif) sebelum `sudo()` apa pun. Aset non-draft hanya boleh diubah pada `name`, `location_id`,
  `custodian_id`, `note`; perubahan nilai lewat wizard (yang menulis lewat `sudo()` sesudah cek manager). Aset berjalan
  /dilepas tidak bisa dihapus.
- SEC-G57-02: field sistem (`state`, disposal, revaluasi, `retired_*`, `opening_accumulated_depreciation`,
  `original_quantity`, `merged_into_id`, `source_move_line_id`) ditolak di `create`/`write` (AccessError), juga untuk
  `sudo()` biasa, kecuali lewat `_efn_system()` (sudo + context privat) yang dipanggil modul ini sesudah cek akses.
  Nilai default (draft / nol) pada `create` tetap diterima.
- SEC-G57-03: ekspor XLSX memakai `strings_to_formulas=False`, `strings_to_urls=False`, dan `write_string` untuk teks.
- Tagihan -> aset: user Billing tidak punya ACL aset; pembuatan/penghapusan **draf** aset memakai `sudo()` hanya setelah
  company tagihan ada di company aktif user. Aset draft tidak menjurnal apa pun; dicatat di chatter tagihan.
- Reset/batal tagihan yang asetnya sudah berjalan/dilepas ditolak ("batalkan/disposal aset dulu").
- `efn_audit`: aturan untuk `efn.account.asset` (nilai, akun, umur, status, kelompok, company) dan
  `efn.account.asset.group` (semua field). Log tanpa nama orang (hanya id record).

## Risiko yang dimitigasi

- R-T03 / D-15 (ketergantungan modul pihak ketiga): port menjadi `efn_*` tanpa `custom_*`/OCA/Enterprise.
- Aset tidak tercatat dari pembelian: draf otomatis dari tagihan berkategori G61 (pemicu = kategori, karena akun
  `12210030` dipakai bersama sehingga akun saja tidak bisa membedakan kelompok).
- Nilai buku salah di laporan sumber (mengabaikan revaluasi, akumulasi awal, pelepasan sebagian, baris dibalik):
  diperbaiki, lihat docstring `wizards/efn_asset_register_wizard.py`.
- Default kelompok hanya lewat onchange (gotcha sumber): diterapkan di server (create, write `group_id`, onchange).
- Status "posted" palsu bila jurnal penyusutan di-draft/dihapus manual: baris jadwal di-unpost otomatis.

## Provenance (ADR-049)

- Asal: repo `odoo-platform` milik pemilik proyek, snapshot `origin/main` 23 Nov 2026 (hash commit tidak tersedia di
  snapshot), path `addons/ee_gap/custom_accounting_asset` (19.0.0.7.0) + pola seed
  `addons/_tenants/custom_levis_asset_accounts/models/fixed_asset_group.py`.
- Diport (logika): `models/fixed_asset*.py`, `depreciation_line.py`, `account_move.py`, semua `wizards/*`, `views/*`,
  `data/ir_cron_data.xml`, `ir_sequence_data.xml`, `server_actions.xml`, `tests/test_asset.py`,
  `tests/test_quantity_asset.py`.
- Perubahan: rename `custom.fixed.asset*` -> `efn.account.asset*`, `_levis_*` -> `_efn_*`, parameter
  `custom_accounting_asset.*` -> `efn_account_asset.*`; `pdp.audited.mixin` -> data `efn.audit.rule`; dependency
  `custom_core`, `custom_pdp_audit`, `custom_accounting_full`, `custom_accounting_reports` dibuang;
  `company_id` wajib di kelompok & lokasi; ACL sumber `base.group_user` read-all dibuang (least privilege), wizard
  disposal/revaluasi/pelepasan hanya Manager; riwayat revaluasi/pelepasan hanya baca untuk Pengguna (spesifikasi
  menyebut `rwc-`; dipersempit karena riwayat hanya dibuat wizard); kontrol status di server; batasan write aset
  non-draft; guard serial/rental + komentar tenant dibuang di `_merge_assets_into_pool`; laporan register ditulis
  ulang (xlsxwriter + QWeb `web.external_layout`, bukan `custom.report.engine`); disposal penuh menghapus baris jadwal
  yang belum diposting (sumber membiarkannya); konfirmasi juga mewajibkan akun aset; sequence padding 5; seed akun
  Erajaya diganti seed `l10n_id`; fitur tagihan -> draf aset baru; menu dipindah ke Accounting (G55), bukan root
  "Assets" di Finance.
- Dibuang: `custom_report_asset_register.py`, `migrations/`, `MODULE_KNOWLEDGE.md`, ikon sumber, seed revaluasi Erajaya.

## Menu

Accounting > Jurnal & Penutupan (`account.menu_finance_entries`) > **Aset Tetap**: Daftar Aset, Posting Penyusutan
(Manager), Revaluasi, Pelepasan Sebagian. Accounting > Reporting > **Laporan Daftar Aset**. Accounting > Configuration >
**Aset Tetap**: Kelompok Aset, Lokasi Aset (Manager). Induk = menu CE yang dipindah `efn_setup_menu` (G55), sehingga
modul tidak bergantung `efn_setup_menu`.

## Cara uji manual

1. Pasang modul; beri user grup Aset / Manager + Accounting.
2. Jalankan G61 (kategori `Aset Tetap / …`), lalu Kelompok Aset > "Pasang ulang kategori G61"; cek `KIT-MCH` berisi
   kategori `Aset Tetap / Mesin` dan akun `12210030/12281030/67100030`.
3. Buat tagihan vendor 2 x produk kategori `Aset Tetap / Mesin`, posting -> chatter tagihan menyebut kode aset;
   Daftar Aset menampilkan draf (qty 2, nilai DPP).
4. Konfirmasi aset -> tab Jadwal Penyusutan terisi; Posting Penyusutan s.d. hari ini -> baris jatuh tempo terposting
   (satu jurnal per tanggal).
5. Reset tagihan ke draft -> ditolak (aset berjalan). Tagihan LVA -> tidak ada aset.
6. Disposal / revaluasi / lepas sebagian dari form aset; Laporan Daftar Aset -> Ekspor Excel / Cetak PDF.

## Test

`tests/` (tag `efn`, `post_install`, `-at_install`, `AccountTestInvoicingCommon`, akun buatan test): port sumber
(jadwal, posting, disposal, revaluasi, pembalikan, sinkron jurnal, aset pool, penggabungan) + baru: tagihan -> draf aset
(idempoten, kategori paling spesifik), LVA tanpa aset, nota kredit, reset tagihan, user Billing, cron bulan berjalan,
akses non-manager (AccessError), isolasi company, seed per PT tanpa akun silang, laporan XLSX/PDF + nilai buku
sesudah revaluasi/pelepasan sebagian.

## Batasan / belum

- **Belum dijalankan di Odoo 19 nyata** saat ditulis (penulis tanpa shell; lead menjalankan di container).
- Satu buku (komersial); tanpa buku pajak paralel / penyusutan fiskal PMK (batasan sumber, Q-G57-2).
- Aset dalam mata uang PT saja; tagihan valas dikonversi dari `balance`.
- Posting ke periode terkunci digeser CE ke periode terbuka (perilaku sumber, belum diuji di EFN). SOP: posting
  penyusutan sebelum lock date.
- Laporan: kolom bulanan memakai jadwal (baris belum diposting = proyeksi); nilai bruto tahun lampau dikoreksi mundur
  hanya untuk revaluasi & pelepasan sebagian; aset dilepas bernilai buku 0 sejak tanggal disposal.
- Impor register lama (Q-G57-4) belum: `opening_accumulated_depreciation` readonly di UI, diisi lewat impor/skrip.
- Pemetaan grup ke persona (`efn_setup_role`) dan akun final / umur final menunggu Finance.
- Kepemilikan kode `odoo-platform` perlu konfirmasi tertulis sebelum production (ADR-049 §Konsekuensi).

**Hash commit `odoo-platform` (origin/main, dicatat efn-lead 23 Nov 2026):** `custom_accounting_asset` `cde92d6`, `custom_levis_asset_accounts` `62b4b5d`.
