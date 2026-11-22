# efn_account_reconcile — rekonsiliasi akun & bank (G58)

**Tujuan.** Odoo CE 19 punya mesin `account.move.line.reconcile()` dan model `account.reconcile.model`, tetapi UI
rekonsiliasi manual dan widget pencocokan mutasi bank adalah Enterprise. Modul ini menyediakan UI tersebut dan menutup
dua celah CE yang membuat rekonsiliasi hilang diam-diam. Spesifikasi: `docs/fit-gap/G57_G58_ACCOUNTING_PORT.md` §3;
true-up MDR: `docs/fit-gap/G59_G60_ACCOUNTING_PORT.md` D-5.

## Fitur
- **Accounting > Jurnal & Penutupan > Rekonsiliasi**
  - *Rekonsiliasi Akun* (`efn.reconcile.account`, view SQL): akun reconcilable dengan item terbuka, **satu baris per
    (company, akun)** + record rule company. Tombol *Rekonsiliasi* membuka item terbuka akun itu di company baris.
  - *Rekonsiliasi Bank*: baris mutasi posted yang belum cocok. Tombol *Cocokkan* membuka halaman penuh pencocokan
    (kandidat berskor, *Isi otomatis*, *Cari lagi*, model rekonsiliasi, write-off). Action *Cocokkan otomatis* di list.
  - *Pembayaran Belum Teralokasi*: pembayaran posted yang tidak melunasi dokumen apa pun.
- Action **Rekonsiliasi** di list Item Jurnal (wizard; write-off opsional ke jurnal umum).
- **Reset ke draft membatalkan rekonsiliasi dulu** (`button_draft` CE 19 tidak memanggil `remove_move_reconcile`) dan
  mencatat dokumen yang dilepas di chatter.
- **Akun/partner item yang masih direkonsiliasi tidak bisa diubah** (state apa pun).
- **Pembayaran ganda diblokir** saat post (partner, nominal, tanggal, arah, company sama; pakai
  `_fetch_duplicate_reference` CE) kecuali dicentang *Sudah Dicek Bukan Duplikat*.
- Kandidat mutasi **tidak pernah bertanggal setelah tanggal mutasi** (parameter `efn_account_reconcile.date_ceiling`,
  default `1`; isi `0` untuk mematikan).
- Akun bertanda **EFN: Jangan Cocokkan Otomatis** (mis. outstanding kas POS) tidak pernah dicocokkan otomatis.
- **True-up MDR (G60 D-5)**: pilih `account.reconcile.model` (trigger manual, jurnal mutasi) di halaman pencocokan;
  baris model `percentage` / `percentage_st_line` / `fixed` / `regex` + `analytic_distribution` (outlet) membukukan
  sisa selisih. Tidak pernah melebihi sisa; sisa yang tidak terserap tetap di suspense. Baris model berpajak ditolak.

## ADR & keputusan
ADR-049 (port kode milik pemilik proyek), ADR-031 (depends hanya `account`), ADR-001 (satu DB multi-company),
Q-G58-3 (tanpa parser narasi), Q-G58-4 (blokir duplikat dengan centang override).

## Provenance (ADR-049)
Snapshot `odoo-platform` `origin/main` per 23 Nov 2026 (hash commit tidak ikut di snapshot; dicatat efn-lead).
- `addons/ee_gap/custom_account_reconcile` (19.0.3.0.0): `models/*`, `wizard/*`, `views/*`, `tests/*`.
- `addons/_tenants/custom_levis_bank_reconcile`: bagian generik `wizard/bank_reconcile_wizard.py` (halaman penuh,
  *Isi otomatis*), `models/account_move_line.py` (`levis_transaction_ref` -> `efn_transaction_ref`), batas tanggal
  `_get_default_amls_matching_domain`, `tests/test_bank_reconcile_tender.py` (versi generik).

Perubahan dibanding sumber:
- nama `custom.*` -> `efn.*`, field `efn_*`, method privat `_efn_*`; menu & teks Bahasa Indonesia;
- ringkasan akun `GROUP BY company, akun` + `company_id` + record rule (sumber menjumlah lintas PT);
- tombol & wizard memeriksa grup `account.group_account_user` + `check_access('write')` + company aktif
  (sumber tanpa cek grup); wizard bank memvalidasi ulang item terpilih terhadap domain kandidat (anti injeksi RPC);
- `skip_reconciliation_guard` hanya dihormati bila `env.su`;
- duplikat memakai `_fetch_duplicate_reference` CE; write-off berupa daftar (model rekonsiliasi CE);
- dibuang: parser narasi/MID, "Store Not Identified", "Re-read Bank Narrative", OU, `levis.clearing.config`,
  migrasi, ikon, nama tenant.

## Risiko yang dimitigasi
- Status "Paid" palsu setelah pembayaran di-reset ke draft (neraca saldo vs umur hutang tidak cocok).
- Pembayaran ganda ke vendor yang sama.
- Kebocoran nilai antar-PT di ringkasan rekonsiliasi (R multi-company, ADR-001).
- Rekonsiliasi lewat RPC oleh user tanpa hak (SEC-58).
- Selisih MDR "misterius" di suspense: dibukukan eksplisit ke akun MDR + analitik outlet.

## Uji manual
1. Buat faktur 500.000, mutasi bank 490.000 di jurnal bank. Accounting > Jurnal & Penutupan > Rekonsiliasi > Rekonsiliasi Bank > *Cocokkan*:
   pilih faktur, centang *Write-off sisa* ke akun biaya bank -> faktur lunas, biaya 10.000.
2. Buat `account.reconcile.model` (manual, jurnal bank, baris `percentage` 100 ke akun MDR + analitik outlet);
   cocokkan settlement neto -> baris MDR dengan analitik outlet, tanpa sisa suspense.
3. Registrasi pembayaran tagihan, lalu *Reset to Draft* pembayaran -> tagihan kembali *Not Paid*, chatter menyebut
   tagihan yang dilepas.
4. Post dua pembayaran vendor identik -> yang kedua ditolak sampai dicentang *Sudah Dicek Bukan Duplikat*.
5. Login user dua PT: Rekonsiliasi Akun menampilkan baris terpisah per PT.

## Batasan / belum
- Pencocokan mutasi valas: skor dan sisa memakai nominal mata uang company/jurnal (sama dengan sumber).
- Baris model rekonsiliasi berpajak belum didukung (ditolak). Kondisi model (`match_label`, `match_amount`,
  `match_partner_ids`) tidak dievaluasi — model dipilih manual oleh Finance; `trigger='auto_reconcile'` tidak dijalankan.
- `fixed` memakai nilai absolut (arah mengikuti sisa selisih); regex dua grup = bulat + desimal, satu grup =
  heuristik pemisah ribuan/desimal (asumsi, lihat `_efn_normalize_number`).
- Parser narasi QRIS/EDC per bank (identifikasi outlet & MDR otomatis) menunggu contoh mutasi (Q-G58-3).
- Uji pemasangan bersama `efn_intercompany` / `efn_account_deferred` / `efn_account_asset` (override `button_draft`)
  dijalankan efn-lead pada daftar modul gabungan.

**Hash commit `odoo-platform` (origin/main, dicatat efn-lead 23 Nov 2026):** `custom_account_reconcile` `5ebc32f`, `custom_levis_bank_reconcile` `889ce24`.
