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
- **Arus Kas metode langsung** (G36, pilihan *Metode arus kas* di wizard): setiap jurnal yang menyentuh kas & setara
  kas dipecah per baris lawan; operasi dikelompokkan menjadi penerimaan dari pelanggan, pembayaran ke pemasok,
  pembayaran beban, pajak & liabilitas lancar lain, uang muka & aset lancar lain. Total per aktivitas sama dengan
  metode tidak langsung (diuji per PT dan konsolidasi). Transfer antar-rekening bank tidak menghasilkan arus; arus kas
  antar-PT dieliminasi di konsolidasi. Tanpa drill-down (angka hanya dari jurnal kas).
- **Penyesuaian Konsolidasi EFN** (G36, menu Accounting > Reporting): jurnal tingkat grup (mis. laba belum
  terealisasi di persediaan antar-PT, eliminasi investasi vs modal, reklasifikasi) yang TIDAK diposting ke buku PT.
  Seimbang, minimal 2 PT & 2 baris, akun milik PT baris, tanpa akun kas & setara kas. Empat mata: Accounting
  Manager lain dari pembuat/penyunting terakhir yang memposting; keduanya wajib punya akses ke SEMUA PT-nya. Posted
  terkunci (hanya bisa dibatalkan). Laporan konsolidasi menyertakan penyesuaian posted bila semua PT-nya ada dalam
  cakupan dan tanggalnya s.d. tanggal laporan (opsi *Penyesuaian konsolidasi*, default aktif); daftarnya tampil di
  bawah laporan.
- **Neraca Saldo**: saldo awal, debit, kredit, saldo akhir per akun.
- **Pembanding** (G35) untuk Neraca/Laba Rugi/Arus Kas: *Periode sebelumnya* (bulan/kuartal utuh mundur sejumlah bulan
  yang sama, rentang bebas mundur sepanjang jumlah hari; Neraca = posisi akhir bulan lalu) atau *Tahun lalu*. Kolom
  Periode ini / Pembanding / Selisih di layar, PDF, XLSX; baris yang hanya ada di pembanding tetap tampil.
- **Drill-down** (G35, layar): klik angka akun membuka Journal Items posted di balik angka itu (akun, PT laporan,
  periode atau periode pembanding). Server memeriksa akses laporan + akun milik PT laporan; daftar dibuka tanpa sudo
  sehingga ACL/record rule akuntansi pengguna tetap berlaku.
- Pilih **satu PT** = laporan per PT; **beberapa PT** = konsolidasi. *Eliminasi antar-PT*: baris jurnal yang partner-nya
  PT lain dalam cakupan dikeluarkan dan ditampilkan di daftar eliminasi (audit trail).
- Tampil di layar, **PDF**, **XLSX**.

Klasifikasi memakai `account_type` CE 19 (berlaku untuk COA `l10n_id` semua PT), sumber data hanya jurnal `posted`.

**Referensi desain:** OCA `account_financial_report`, `mis_builder`; Cybrosys `dynamic_accounts_report` (dibaca,
tidak disalin; ADR-031).

**Belum:** format resmi auditor (Q-6), kepentingan non-pengendali (NCI) otomatis.
