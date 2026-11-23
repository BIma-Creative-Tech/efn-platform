# efn_petty_cash — Kas Kecil Outlet & Uang Muka (G57)

Versi `19.0.1.0.0`, LGPL-3, Odoo 19 CE. Spesifikasi: `docs/fit-gap/G57_G58_ACCOUNTING_PORT.md` §2.

## Tujuan

- **Dana kas kecil outlet (imprest)** per outlet: plafon tetap, kustodian (Store Manager) mencatat realisasi
  belanja, pengisian ulang senilai realisasi yang sudah diposting, disetujui orang lain lalu dicairkan Finance.
- **Uang muka karyawan** (perjalanan, belanja mendesak CK): pengajuan -> persetujuan -> pencairan -> realisasi ->
  kembalikan sisa / ganti kekurangan -> settle (saldo nol, direkonsiliasi otomatis).
- Voucher PDF (pencairan, pertanggungjawaban, penyelesaian) dan laporan Kartu Uang Muka / Outstanding / Aging
  (XLSX + PDF).

Jurnal (akun uang muka dari **jenis**, partner = pemegang):

| Langkah | Jurnal |
|---|---|
| Cair (dana awal / isi ulang / uang muka) | Dr Uang Muka / Cr Bank |
| Realisasi beban | Dr Beban (termasuk pajak non-kreditabel) / Cr Uang Muka |
| Realisasi pihak ketiga | Tagihan vendor (PPN masukan) lalu entri jurnal umum Dr Utang Usaha / Cr Uang Muka, direkonsiliasi ke tagihan |
| Kembalikan sisa / ganti kekurangan | Dr Bank / Cr Uang Muka; Dr Uang Muka / Cr Bank |
| Settle / tutup dana | baris uang muka bersaldo nol direkonsiliasi (tutup dana: sisa dikembalikan ke bank dulu) |

Saldo akun uang muka per fund = kas fisik yang seharusnya ada di outlet (dicocokkan dengan hitung kas).

## ADR & keputusan

- **ADR-049** (port & adaptasi kode `odoo-platform` milik pemilik proyek), **ADR-031** (hanya CE + `efn_*`;
  tidak ada dependency `hr`, `custom_*`, OCA, Enterprise), ADR-001 (satu DB multi-company), ADR-022.
- Keputusan fit-gap K-PC1..K-PC8. Default pertanyaan terbuka yang dipakai:
  - **Q-PC1**: akun uang muka adalah placeholder yang **wajib dibuat Finance** (l10n_id tidak punya akun aset lancar
    reconcilable yang cocok). Modul **fail-closed**: tanpa akun (atau akun kas/non-reconcilable) pencairan/posting
    ditolak dengan `UserError` yang jelas; tanpa jurnal bank/beban/pelunasan juga ditolak. Tidak ada entri setengah
    jadi. Tidak ada data demo/akun bawaan.
  - **Q-PC2**: kas kecil dicatat sebagai **uang muka kustodian** (mesin sumber). Mode "jurnal kas outlet"
    (`cash_journal_id`) **tidak dibangun**.
  - **Q-PC4**: SoD Penyetuju ⟂ Finance per user = report-only (belum dibangun di `efn_setup_role`, lihat Belum).
  - **Q-PC5**: pemegang boleh partner tanpa user Odoo; rule "milik sendiri" berlaku lewat pembuat.

## Provenance (ADR-049)

- **Asal:** `odoo-platform` `addons/ee_gap/custom_petty_cash` (manifest 19.0.0.5.0), snapshot `origin/main`
  23 Nov 2026 (salinan baca di scratchpad efn-lead; hash commit tidak tersedia di snapshot — efn-lead mencatatnya
  saat konfirmasi kepemilikan tertulis).
- **Diport (diubah nama `petty.cash.*` -> `efn.petty.cash.*`, label Bahasa Indonesia):** model jenis, pengajuan,
  rincian, realisasi, baris realisasi, `account.move` (penanda), voucher PDF (`reports/petty_cash_vouchers.xml`,
  aturan `<style>` di dalam `<main>` dipertahankan), sequence, template email pengingat, cron pengingat, logika
  multi-mata uang (`_pc_conv`/`_pc_leg` -> `common.convert`/`move_leg`, K-PC7), plafon jenis.
- **Diganti:** `hr.employee` -> `holder_partner_id` + `holder_user_id` (K-PC1); `custom_approval_engine` -> empat
  mata pola `efn_loyalty_accounting` (pembuat/penyunting/pemegang ≠ penyetuju; pencair ≠ pembuat/pemegang; pemosting
  realisasi ≠ pembuat/penyunting/pemegang); `pdp.audited.mixin` -> data `efn.audit.rule`; `custom_accounting_reports`
  -> satu wizard `efn.petty.cash.report.wizard` (xlsxwriter + QWeb); `account.payment.register` + penulisan ulang
  akun outstanding metode pembayaran jurnal -> entri jurnal umum + rekonsiliasi (gotcha "jurnal dedicated" sumber
  hilang; `payment_journal_id` kini jurnal umum).
- **Dibuang:** `hooks.py` (post_init_hook yang memberi grup kas kecil ke SEMUA user internal — K-PC3, least
  privilege), `models/hr_employee.py`, `models/hr_job.py` (plafon per karyawan/jabatan + analitik per karyawan),
  `models/res_company.py` + `res_config_settings` (rantai fallback company, K-PC4; parameter cukup
  `ir.config_parameter`), analitik Operating Unit (K-PC6), PPh `x_custom_withholding_category_id` (G59/G60),
  `models/report_*.py`, `reports/petty_cash_templates.xml`, `wizard/*`, `migrations/`, ikon sumber.
- **Baru:** `efn.petty.cash.fund` (imprest), realisasi terhadap fund, pengisian ulang bertaut realisasi,
  tutup dana, rule multi-company + "milik sendiri", jejak empat mata.

## Risiko yang dimitigasi

- Fraud kas kecil / uang muka (pemohon menyetujui sendiri, pencair = pemohon): empat mata per record; status dan
  jejak persetujuan hanya ditulis tombol (`sudo` + konteks sistem, tidak bisa lewat `write` RPC).
- Kas outlet melebihi plafon: invarian saldo + pengajuan tertunda ≤ plafon (selalu blokir), batas per transaksi,
  realisasi tidak boleh melebihi saldo fund.
- Salah akun/jurnal diam-diam: fail-closed (tanpa fallback ke jurnal umum pertama seperti sumber).
- Kebocoran lintas PT: rule global `company_id in company_ids` di semua model; kustodian hanya melihat fund dan
  dokumennya sendiri. Log hanya berisi id record, bukan nama orang.
- Least privilege: grup tidak diberikan otomatis ke siapa pun (termasuk admin).

## Grup & menu

- Pemohon & Kustodian (`group_petty_cash_user`), Penyetuju (`group_petty_cash_approver`, imply user), Finance
  (`group_petty_cash_finance`, imply user + `account.group_account_invoice`, **tidak** imply penyetuju),
  Lewati Plafon (`group_petty_cash_limit_override`, untuk plafon jenis saja). Auditor = `account.group_account_readonly`.
- Root menu **Kas Kecil** (seq 57): Dana Kas Kecil, Pengajuan, Realisasi, Menunggu Persetujuan, Laporan, Konfigurasi.
  Pintasan Finance > Kas & Bank > Kas Kecil & Uang Muka.
- Parameter: `efn_petty_cash.realization_days` (14), `efn_petty_cash.disburse_via_payment` (0),
  `efn_petty_cash.replenish_threshold_pct` (70).

## Cara uji manual

1. Finance membuat akun aset lancar reconcilable "Uang Muka Kas Kecil" dan "Uang Muka Karyawan" (Accounting >
   Konfigurasi > Akun).
2. Kas Kecil > Konfigurasi > Jenis: buat "Kas Kecil Outlet" (jenis imprest) dan "Uang Muka Perjalanan"; isi akun,
   jurnal bank keluar, jurnal beban, jurnal pelunasan (jurnal umum).
3. Konfigurasi > Dana Kas Kecil: plafon Rp 5.000.000, batas per transaksi Rp 1.000.000, kustodian = user Store Manager.
4. Kustodian: Pengajuan baru, pilih fund, Rp 5.000.000, Ajukan. Penyetuju (user lain): Setujui. Finance: Cairkan.
   Cek entri Dr Uang Muka / Cr Bank dan saldo fund 5.000.000.
5. Kustodian: Realisasi baru terhadap fund (beban + pembelian pihak ketiga dengan nota), Ajukan; Finance: Posting.
   Saldo fund turun, "Belum Diisi Ulang" naik. Di fund: Ajukan Pengisian Ulang -> setujui -> cairkan -> saldo kembali
   ke plafon.
6. Coba setujui pengajuan sendiri / cairkan pengajuan sendiri / jenis tanpa akun -> ditolak.
7. Laporan > Outstanding / Aging / Kartu Uang Muka -> XLSX & PDF.

Test otomatis: `tests/test_request_flow.py`, `tests/test_fund.py`, `tests/test_security.py` (tag `efn`, post_install).

## Batasan / belum

- Mode `cash_journal_id` (fund sebagai jurnal kas outlet, Q-PC2 opsi b) tidak dibangun.
- `efn_setup_role`: pemberian grup ke persona (Store Manager, Staf CK) dan aturan `SOD-PC1` belum dibuat.
- Root menu belum didaftarkan di `efn_web_home` `ROOT_MENU_GROUPS` (kelompok "finance").
- Tidak ada data demo (akun uang muka harus diputuskan Finance).
- Pencairan lewat `account.payment` (`disburse_via_payment=1`) diport tetapi tidak diuji; Odoo 18+ tidak membuat
  entri pembayaran bila metode pembayaran tanpa akun outstanding -> ditolak dengan UserError.
- Multi-mata uang diport apa adanya dari sumber, tanpa test valas di EFN.
- Nota pemasok tetap di baris realisasi (tidak disalin ke tagihan vendor).
- Voucher penyelesaian membaca entri jurnal: hanya untuk user dengan hak baca akuntansi (Finance/auditor).
- Kartu/Outstanding/Aging dihitung dari baris jurnal posted ≤ tanggal (aging berdasarkan tanggal pencairan pertama,
  fund imprest dikeluarkan dari aging).

**Hash commit `odoo-platform` (origin/main, dicatat efn-lead 23 Nov 2026):** `custom_petty_cash` `39684f1`.
