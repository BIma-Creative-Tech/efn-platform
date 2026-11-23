# efn_account_deferred (19.0.1.0.0) — G59-B biaya dibayar di muka & pendapatan diterima di muka

> **Ringkasan manajemen.** Sewa outlet, asuransi, atau lisensi yang dibayar sekaligus untuk beberapa bulan/tahun
> otomatis dibebankan per bulan. Finance cukup mengisi tanggal mulai-selesai di baris tagihan; saat tagihan diposting
> Odoo membuat satu jurnal penangguhan dan satu jurnal pengakuan per bulan. Pendapatan diterima di muka (sewa ruang,
> langganan katering) diperlakukan sama. Analitik outlet ikut terbawa sehingga laba-rugi per outlet tetap benar.

- **Spesifikasi:** `docs/fit-gap/G59_G60_ACCOUNTING_PORT.md` §G59-B.
- **ADR:** ADR-049 (port kode `odoo-platform` milik pemilik proyek), ADR-031 (hanya CE + `efn_*`).
- **Risiko dimitigasi:** beban sewa/asuransi menumpuk di satu bulan (P&L bulanan & per outlet tidak wajar, G60-C);
  R-T03/R-L08 (provenance kode tercatat di bawah).
- **Depends:** `account`, `efn_core`. Tidak ada model baru → `ir.model.access.csv` hanya header, tanpa record rule baru;
  entri yang dibuat = `account.move` standar (record rule multi-company CE).

## Cara kerja
1. **Pengaturan per PT** (Pengaturan → Akuntansi → *Entri Tangguhan (EFN)*, grup Accounting Manager):
   `efn_deferred_expense_account_id` (aset lancar/prepayment, saran `l10n_id_11210040` *Prepaid Expense*),
   `efn_deferred_revenue_account_id` (liabilitas, saran `l10n_id_28110030` *Deferred Revenue*), `efn_deferred_journal_id`
   (Miscellaneous). Constraint: akun/jurnal harus milik PT itu (atau induknya); jurnal tipe `general`.
2. Di baris tagihan/faktur isi kolom opsional **Mulai/Selesai Tangguhan** (keduanya atau tidak sama sekali; selesai ≥ mulai).
3. **Posting** tagihan/faktur/nota kredit → (a) jurnal *Penangguhan* tanggal tagihan: seluruh nilai baris dipindah dari
   akun laba-rugi ke akun tangguhan; (b) jurnal *Pengakuan* per akhir bulan, prorata jumlah hari, sisa pembulatan di bulan
   terakhir. Bulan ≤ hari ini langsung posted; bulan depan draft `auto_post='at_date'` → diposting cron CE
   `_autopost_draft_entries` (tidak ada cron custom).
4. Hanya baris `display_type='product'` berakun pendapatan/beban yang ditangguhkan. Baris pajak (PPN, **PPh 4(2) sewa**)
   tetap di tagihan.
5. **Analitik:** setiap baris penangguhan & pengakuan menyalin `analytic_distribution` baris asal (perubahan terhadap
   sumber) → Analytic Report per outlet menampilkan beban sewa per bulan.
6. **Reset to draft** tagihan asal → entri turunan di-draft lalu dihapus; posting ulang membuat jadwal baru. Bila ada entri
   turunan posted di periode terkunci (fiscal year / hard lock), reset ditolak dengan pesan yang menyebut entrinya; lock date
   tidak pernah dilewati.
7. Smart button **Entri Tangguhan** di form move; menu **Accounting → Jurnal & Penutupan → Entri Tangguhan**
   (`account.group_account_user`).

## Provenance (ADR-049)
- **Asal:** `odoo-platform` `addons/ee_gap/custom_account_deferred` 19.0.1.0.0 (LGPL-3, `author: Custom Platform`),
  snapshot `origin/main` dibaca 23 Nov 2026 (hash commit: belum dicatat — snapshot tanpa metadata git; efn-lead mengisi
  dari repo pemilik).
- **Diport:** `models/account_move.py`, `account_move_line.py`, `res_company.py`, `res_config_settings.py`,
  `views/account_move_views.xml`, `deferred_views.xml`, `res_config_settings_views.xml`, `tests/test_deferred.py` (3 test).
- **Perubahan:** rename `deferred_*` → `efn_deferred_*`, `action_open_deferred_entries` → `action_efn_open_deferred_entries`
  (+ `check_access('read')`), xmlid `custom_` → `efn_`; dependency `account` + `efn_core`; analitik terbawa; pembulatan
  memakai mata uang company (sumber memakai mata uang dokumen untuk nilai `balance` mata uang company); pesan & label
  Indonesia; pra-cek lock date dengan pesan jelas; constraint company pada akun/jurnal; demo via fungsi (PT ber-CoA
  l10n_id). Nama `efn_*` juga menghindari tabrakan dengan field EE `deferred_expense_account_id` yang diisi template CoA
  `l10n_id` CE.
- **Tidak diport:** laporan jadwal tangguhan per akun/bulan yang disebut di deskripsi sumber (tidak ada di snapshot; gunakan
  list Entri Tangguhan group by Asal / pivot Journal Items).

## Uji manual (DB dev)
1. Install; PT Grand Lucky → Pengaturan → isi tiga field tangguhan.
2. Tagihan vendor sewa outlet Rp12.000.000, baris dengan analitik outlet, Mulai 1 Jan, Selesai 31 Des, pajak PPh 4(2)
   → Post. Smart button = 13 entri (1 penangguhan + 12 pengakuan); baris PPh tetap di tagihan.
3. Accounting → Reporting → Analytic Report: beban sewa outlet Rp1.000.000 per bulan.
4. Reset to draft → entri turunan hilang; Post lagi → jadwal baru.
5. Kunci periode sampai bulan pengakuan yang sudah posted → Reset to draft ditolak dengan pesan periode terkunci.

## Test
`tests/test_deferred.py` (tag `efn`, `AccountTestInvoicingCommon`): 3 test port (setahun, reset, tanpa tanggal) +
prorata & sisa bulan terakhir, analitik terbawa (distribution + analytic line), PPh 4(2) tidak ditangguhkan, faktur
pendapatan, multi-company (PT B belum dikonfigurasi ditolak, akun PT A ditolak constraint, akun/jurnal PT B dipakai),
lock date, smart button, constraint tanggal, jurnal kosong.

## Batasan / belum
- Hapus entri turunan saat reset mengikuti aturan CE: user non-manager bisa ditolak (`_unlink_forbid_parts_of_chain`,
  urutan nomor) dan PT dengan *Restrictive Audit Trail* menolak penghapusan entri yang pernah posted → reset harus lewat
  nota kredit. Perilaku sumber dipertahankan (spesifikasi §G59-B butir 4).
- Tagihan berulang (`auto_post` monthly) tidak menyalin tanggal tangguhan (`copy=False`).
- Mengubah tanggal tangguhan setelah posting memerlukan reset to draft.
- Aturan `efn.audit.rule` untuk tiga field `res.company` belum ada (milik `efn_setup_audit`, spesifikasi tabel ringkasan
  urutan 5).
- Modul perlu ditambahkan ke `EFN_TEST_MODULES` CI (efn-lead).

**Hash commit `odoo-platform` (origin/main, dicatat efn-lead 23 Nov 2026):** `custom_account_deferred` `7e9d66b`.
