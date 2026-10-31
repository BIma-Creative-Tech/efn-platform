# efn_account_reports — Laporan keuangan per PT & konsolidasi (G28)

**Tujuan.** W-16 / Q-V3 / BRD §5.1: Odoo CE 19 tidak punya Neraca/Laba Rugi (`account_reports` = Enterprise).

Accounting > Reporting > **Laporan Keuangan EFN**:
- **Neraca** per tanggal: aset, liabilitas, ekuitas, laba tahun berjalan, laba tahun lalu yang belum ditutup; baris
  *Selisih* tampil merah bila tidak seimbang.
- **Laba Rugi** per periode: pendapatan, HPP, beban operasional, penyusutan, pendapatan/beban lain → laba kotor,
  operasional, bersih.
- **Arus Kas** (G34) per periode, metode tidak langsung: laba bersih + penyusutan + perubahan modal kerja = operasi;
  aset tetap/tidak lancar = investasi; liabilitas jangka panjang & ekuitas = pendanaan. Akun bisa dipindah kategori
  dengan tag CE *Operating/Investing/Financing Activities*. Kas & setara kas = akun kas/bank + akun outstanding
  receipts/payments + akun transfer likuiditas; baris *Selisih* merah bila kenaikan kas tidak cocok dengan saldo kas.
- **Neraca Saldo**: saldo awal, debit, kredit, saldo akhir per akun.
- Pilih **satu PT** = laporan per PT; **beberapa PT** = konsolidasi. *Eliminasi antar-PT*: baris jurnal yang partner-nya
  PT lain dalam cakupan dikeluarkan dan ditampilkan di daftar eliminasi (audit trail).
- Tampil di layar, **PDF**, **XLSX**.

Klasifikasi memakai `account_type` CE 19 (berlaku untuk COA `l10n_id` semua PT), sumber data hanya jurnal `posted`.

**Referensi desain:** OCA `account_financial_report`, `mis_builder`; Cybrosys `dynamic_accounts_report` (dibaca,
tidak disalin; ADR-031).

**Belum:** arus kas metode langsung, perbandingan periode, drill-down ke jurnal, penyesuaian konsolidasi manual (mis.
eliminasi investasi/ekuitas), format resmi auditor (Q-6).
