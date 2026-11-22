# efn_bank_import — impor mutasi bank CSV (G58)

**Tujuan.** Impor file mutasi (`account_bank_statement_import*`) di Odoo 19 adalah Enterprise. `base_import` CE bisa
mengimpor baris mutasi dari CSV yang rapi, tetapi tidak menangani format BCA KlikBCA Bisnis (tanggal tanpa tahun,
nominal + DB/CR satu sel), dedup file/baris, dan lock date. Modul ini mengisinya. Spesifikasi:
`docs/fit-gap/G57_G58_ACCOUNTING_PORT.md` §4.

## Fitur
- **Finance > Kas & Bank > Impor Mutasi Bank** (wizard) dan **Log Impor Mutasi**; **Accounting > Configuration >
  Template Mutasi Bank** (Administrator).
- Template generik per company: encoding (otomatis = `utf-8-sig` lalu `cp1252`; gagal = ditolak), delimiter, judul,
  format tanggal, indeks kolom 1-based, pemisah desimal/ribuan, kolom bertanda atau debit/kredit.
- Deteksi otomatis **BCA korporat (KlikBCA Bisnis CSV)** apa pun template yang dipilih (preamble, `DD/MM` + tahun dari
  "Periode", `1,234.00 DB/CR`, baris penutup saldo, periode Des->Jan).
- **Lock date fail-closed**: satu tanggal saja <= `fiscalyear_lock_date` / `hard_lock_date` -> seluruh file ditolak
  (pesan: jumlah tanggal & tanggal terakhir yang terkunci).
- **Dedup**: file yang sama (sha256) per company ditolak; baris yang sidiknya (`efn_import_fingerprint`) sudah ada di
  jurnal dilewati dan dihitung di log (file tumpang tindih aman). Baris identik dalam satu file tetap dua mutasi.
- Log immutable (hanya arsip), `company_id` + rule; error = `baris N: invalid_date|invalid_amount|missing_column`
  (tanpa isi sel), maks 200 baris; baris nol dihitung. File asli tidak disimpan (hanya hash).
- Batas: 10 MB (`efn_bank_import.max_bytes`), 20.000 baris (`efn_bank_import.max_rows`).
- Template diaudit `efn_audit` (semua field). Data contoh per company: `generic_signed_csv`, `bca_corp_csv`.

## ADR & keputusan
ADR-049, ADR-031, ADR-001, ADR-022 (tidak ada koneksi keluar baru; H2H dibuang), Q-G58-1 (CSV saja di fase 1).

## Provenance (ADR-049)
Snapshot `odoo-platform` `origin/main` per 23 Nov 2026, `addons/ee_gap/custom_bank_import` (19.0.0.3.0; hash commit
tidak ikut di snapshot, dicatat efn-lead): `models/bank_import_template.py` (bagian CSV + BCA korporat),
`models/bank_import_log.py`, `wizard/bank_import_csv_wizard.py`, view template/log/wizard, `security/*`,
`tests/test_bank_import.py` (kasus CSV generik + BCA; fixture diganti data sintetis).

Perubahan dibanding sumber: parser privat `_efn_parse` (sumber `parse_csv` publik = bisa dipanggil RPC); lock date
menolak (sumber menulis ulang tanggal dengan `bypass_lock_check`); dedup hash per company (sumber sudo lintas company)
+ sidik baris; encoding ketat (sumber `errors="replace"`); kode error tanpa isi sel; `skipped_zero`, `duplicate_count`;
log gagal tetap tersimpan (sumber membuat log lalu `raise` -> ter-rollback); `sample_file` dibuang; akses dicek sebelum
`sudo()`. Dibuang: H2H (`bank_h2h_*`, `custom_adapter_framework`, cron, `raw_payload`), parser XLS/XLSX (BRI BRISIM,
BNI Transaction Inquiry, Mandiri Acc_Statement), migrasi, ikon, nama/MID tenant di fixture.

## Risiko yang dimitigasi
- Kontrol tutup buku dilewati lewat impor mutasi (sumber).
- Mutasi tergandakan karena file tumpang tindih / diimpor ulang.
- Kebocoran narasi mutasi (nama & rekening lawan transaksi) ke log/pesan error (UU PDP).
- Parser dipanggil lewat RPC (SEC-58); dedup lintas PT (ADR-001).

## Uji manual
1. Finance > Kas & Bank > Impor Mutasi Bank: jurnal bank, template `bca_corp_csv`, unggah ekspor CSV KlikBCA Bisnis
   -> log *Berhasil*, rekening koran baru; Accounting > Rekonsiliasi Bank menampilkan barisnya.
2. Unggah file yang sama lagi -> ditolak. Unggah file periode tumpang tindih -> log menunjukkan baris duplikat dilewati.
3. Set *Lock Date* setelah tanggal di file -> impor ditolak seluruhnya.
4. File dengan baris rusak -> log *Sebagian* berisi `baris N: <kode>` tanpa isi sel.

## Batasan / belum
- Parser XLS/XLSX Mandiri/BNI/BRI fase 2 (Q-G58-1); format resmi per bank belum ada contoh di repo — pakai template
  generik bila bank menyediakan ekspor CSV.
- `account_number`/`partner_name` baris mutasi tidak diisi; partner dicocokkan di rekonsiliasi.
- Sidik baris memakai sha256 narasi+nominal+tanggal tanpa kunci rahasia (narasi plaintext tetap ada di `payment_ref`).
- Pengecualian lock date per user (`account.lock_exception`) tidak dipertimbangkan — selalu menolak.
- Hanya cek `check_access('read')` jurnal + `create` baris mutasi: ACL CE memberi write `account.journal` hanya ke
  Administrator, jadi `check_access('write')` jurnal (spesifikasi §4.4) akan menolak semua Bookkeeper.

**Hash commit `odoo-platform` (origin/main, dicatat efn-lead 23 Nov 2026):** `custom_bank_import` `4704e07`.
