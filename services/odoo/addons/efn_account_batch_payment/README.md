# efn_account_batch_payment — batch pembayaran + file transfer bank (G58)

**Tujuan.** Batch payment adalah Enterprise. Modul ini mengelompokkan pembayaran posted satu jurnal bank lalu membuat
file transfer massal untuk diunggah manual ke portal bank (HTTPS). Spesifikasi:
`docs/fit-gap/G57_G58_ACCOUNTING_PORT.md` §5; persetujuan payment: `docs/fit-gap/G59_G60_ACCOUNTING_PORT.md` §0.

## Alur
1. Pembayaran vendor di-post (dan disetujui G60 bila `efn_account_localization` terpasang).
2. List pembayaran > Action **Tambahkan ke Batch** (atau Finance > Vendors > **Batch Pembayaran** > Baru).
3. **Validasi** (Accounting/Bookkeeper) -> nomor `BATCH/YYYY/MM/NNNN`, `validated_by_id` dicatat.
4. **Buat File Transfer** (Administrator, **bukan validator** — empat mata): format harus `generic` atau sudah
   `verified`; isi batch harus sama dengan saat validasi (digest); rekening penerima wajib terbaca & berupa angka.
   File = attachment privat milik pengekspor, diunduh langsung, **dihapus
   cron setelah 24 jam**. Yang disimpan permanen: `export_sha256`, `exported_by_id`, `exported_at`.
5. Status `Terkirim` -> `Terekonsiliasi` otomatis bila semua pembayaran `paid` dan cocok dengan mutasi bank.
6. **Reset ke Draft** setelah terkirim hanya Administrator; jejak ekspor dikosongkan (riwayat tetap di chatter dan
   `efn_audit`).

## Keamanan & PII (UU PDP)
- CSV dengan modul `csv`; memo/nama disanitasi (tanpa delimiter, CR/LF, karakter di luar `[A-Za-z0-9 .,/-]`,
  dipotong per format), sel berawalan `= + - @` diberi spasi depan.
- Rekening vendor perorangan/karyawan terenkripsi `efn_pii` (dibaca lewat ORM di proses Odoo). **Keyring terkunci
  (placeholder) / kosong / bukan angka -> ekspor ditolak**, tidak ada file. Pesan memakai nomor payment.
- List batch menampilkan rekening ter-mask (`****123`, `efn_audit.mask_identifier`). Log/chatter tanpa nomor
  rekening/nama. `efn_audit`: batch (`state`, `export_sha256`, `exported_by_id`, `validated_by_id`, `payment_ids`),
  konfigurasi format per company (`debit_account` di-mask).
- SEC-G58-01: field sistem (`state`, `validated_by_id`, digest, jejak ekspor, `name`) hanya ditulis aksi server
  (sudo + konteks privat). Isi batch (pembayaran, jurnal, arah, tanggal, format) terkunci setelah validasi, termasuk
  `account.payment.efn_batch_payment_id`. Digest isi (id, nominal, mata uang, rekening) disimpan saat validasi dan
  dicek ulang saat ekspor.
- SEC-G58-02: attachment file transfer tidak tertaut ke batch, jadi tidak tampil di chatter; CE hanya mengizinkan
  pembuatnya (pengekspor) dan admin sistem membacanya.
- Renderer privat `_efn_render` (sumber `render` publik = bisa dipanggil RPC).
- `company_id` + record rule pada batch dan konfigurasi format per company.

## Format
Seed (`noupdate`): `generic` (terverifikasi), `bca_mcm`, `mandiri_mcm`, `bni`, `bri` — baseline sumber berstatus
"BELUM DIVERIFIKASI dengan contoh portal bank"; ekspor diblokir sampai Finance mencentang **Terverifikasi** setelah uji
unggah (Q-G58-2). Corporate ID & rekening sumber per company × jurnal di tab konfigurasi format (Administrator).

## ADR & keputusan
ADR-049, ADR-031, ADR-001, ADR-022 (file tidak disimpan permanen; filestore belum terenkripsi per file), ADR-036
(efn_pii), Q-G58-2 (hanya generik sampai terverifikasi), Q-G58-5 (hash + pengekspor + waktu, attachment 24 jam).

## Provenance (ADR-049)
Snapshot `odoo-platform` `origin/main` per 23 Nov 2026, `addons/ee_gap/custom_account_batch_payment` (19.0.1.0.0;
hash commit tidak ikut di snapshot, dicatat efn-lead): `models/*`, `views/*`, `data/batch_payment_sequence.xml`,
`data/batch_export_formats.xml`, `tests/test_batch_payment.py`.

Perubahan dibanding sumber: nama `efn.*`; `company_id` stored + rule; empat mata validasi ≠ ekspor; cek grup/akses
eksplisit di setiap aksi; persetujuan G60 bila field ada; `export_file` permanen -> attachment 24 jam + hash;
`delim.join` -> `csv` + sanitasi; rekening fail-closed (keyring terkunci); `corporate_id`/`debit_account` pindah ke
`efn.account.batch.payment.format.company`; format bank diblokir sampai `verified`; seed `noupdate=1`;
`action_create_batch_from_selection` (`@api.model`, context) -> `action_efn_create_batch` (recordset).

## Uji manual
1. Buat 3 pembayaran vendor (rekening bank partner terisi), post, pilih di list > *Tambahkan ke Batch*.
2. Validasi sebagai user A. Login user B (Administrator) > *Buat File Transfer* (format Generik) -> file terunduh,
   status Terkirim, chatter memuat SHA-256. User A mencoba membuat file -> ditolak (empat mata).
3. Pilih format Mandiri MCM -> ditolak "belum diverifikasi".
4. Setelah 24 jam (atau jalankan cron *EFN: hapus file transfer batch*) -> attachment hilang, hash tetap.

## Batasan / belum
- Tata letak bank (BCA MCM, Mandiri MCM, BNI, BRI) belum diverifikasi dengan portal (Q-G58-2); belum ada H2H.
- Persetujuan payment bergantung G60; tanpa modul itu hanya empat mata batch yang berlaku.
- Cron berjalan tiap jam (spesifikasi: harian) agar file tidak hidup jauh melewati 24 jam.
- Attachment sementara tetap ada di filestore sampai cron berjalan; filestore belum terenkripsi per file (ADR-022).

**Hash commit `odoo-platform` (origin/main, dicatat efn-lead 23 Nov 2026):** `custom_account_batch_payment` `24446ab`.
