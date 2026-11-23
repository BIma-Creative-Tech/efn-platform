# efn_account_localization (G60)

Tiga fitur akuntansi yang tidak ada di Odoo CE 19, satu modul (spesifikasi: `docs/fit-gap/G59_G60_ACCOUNTING_PORT.md` §G60):

- **A. Payment Voucher / Payment Receipt + persetujuan 4-eyes** pembayaran keluar di atas ambang per PT.
- **B. Estimasi MDR** kartu/QRIS per metode bayar POS, dibukukan saat tutup sesi POS.
- **C. Analitik Outlet**: baris laba-rugi jurnal tutup sesi POS dicap akun analitik gudang outlet.

- **ADR:** ADR-049 (port & adaptasi kode `odoo-platform` milik pemilik proyek), ADR-031 (tanpa dependency non-CE),
  ADR-032/K25-09 (EDC berdiri sendiri, tanpa BIN), ADR-036 (PII terenkripsi). Keputusan D-5, D-6, D-7 di dokumen fit-gap.
- **Risiko dimitigasi:** R-T03/R-L08 (provenance kode, lihat bawah), SoD pembayaran keluar (4-eyes), laba-rugi outlet tidak
  lengkap sampai mutasi bank diimpor (MDR harian), kebocoran nomor rekening di cetakan (masking, UU PDP).
- **Depends:** `account`, `analytic`, `point_of_sale`, `stock`, `purchase_stock`, `efn_core` (CE + EFN saja).
  `purchase_stock` (bukan `purchase`) karena `purchase.order.picking_type_id` ada di sana (default outlet di PO, Q-G60-6).

## Provenance (ADR-049)

Sumber: `odoo-platform` (`github.com/sarangrumah/odoo-platform`), snapshot **origin/main dibaca 23 Nov 2026**, modul
`addons/_tenants/custom_levis_localization` (19.0.1.41.0, LGPL-3, `author: Custom Platform`). Hash commit tidak ikut di
salinan snapshot yang dibaca engineer; **efn-lead mengisi hash** dari repo pemilik sebelum rilis.

| File sumber | Aksi | File EFN |
|---|---|---|
| `models/terbilang.py` | Port apa adanya (fungsi murni) | `models/terbilang.py` |
| `models/account_payment.py` `_edo_line_allocations`, `_edo_row_vals`, `_edo_voucher_rows`, `_edo_recipient_bank`, `_edo_source_bank_account`, `_edo_amount_in_words` | Port + rename `_efn_voucher_*` + masking rekening tujuan | `models/account_payment.py` |
| `models/account_payment.py` `l10n_ou_analytic_id`, `l10n_override_outstanding_account_id`, `l10n_note/remark`, override akun outstanding & `_prepare_move_*` | Drop (melemahkan kontrol; OU di baris neraca tidak dibutuhkan) | — |
| `reports/payment_voucher_templates.xml`, `payment_receipt_templates.xml`, `payment_report_actions.xml`, `paperformat.xml` | Port + rename xmlid, label Bahasa Indonesia, blok persetujuan EFN, tanpa nama/branding klien lain, tanpa `attachment` | `reports/*` |
| `reports/journal_billing_templates.xml` | Tidak diport (Q-G60-4, P3) | — |
| `models/account_payment_register.py` (admin fee multi-COA, MDR via Register Payment) | Drop | — |
| `models/levis_mdr_bin.py` | Hanya ide persen + tetap + effective dating; ditulis ulang tanpa BIN | `models/efn_mdr_rate.py` |
| `models/pos_session.py` (stamping OU semua baris) | Ide ditulis ulang: hanya baris laba-rugi, satu titik pasca-pembuatan jurnal | `models/pos_session.py`, `models/outlet_analytic.py` |
| `models/purchase_order.py` `_levis_merge_ou_distribution` | Ditulis ulang plan-aware | `models/outlet_analytic.py`, `models/purchase_order_line.py` |
| `models/stock_warehouse.py` `l10n_ou_analytic_id` / `l10n_purchase_journal_id` | Rename `efn_analytic_account_id` / drop | `models/stock_warehouse.py` |
| `levis_pos_clearing*`, `levis_bank_mid_map*`, `levis_bank_narrative*`, `levis_clearing_config` | Domain G58 (tidak di sini) | — |
| `tests/test_payment_voucher_rows.py` | Ditulis ulang untuk EFN + test approval | `tests/test_payment_approval.py` |

Tidak ada `custom_core`/`custom_pdp_*`/`custom_approval_engine` di modul ini (4-eyes = pola `efn_loyalty_accounting`).
Tidak tersalin: nama Levi's/EAL/Erajaya, data bank `res.bank.csv`, kode akun klien lain, folder `_seed_data/`.

## A. Voucher & persetujuan

- `res.company.efn_payment_approval_threshold` (Settings > Akuntansi > blok EFN). **Default 0 = nonaktif** (Q-G60-1).
- Gerbang: `payment_type = outbound`, bukan payment POS (`pos_session_id`), bukan transfer internal, nilai mata uang PT
  >= ambang. `action_post` menolak sampai `efn_approval_state = approved`.
- Alur: maker **Minta Persetujuan** (`to_approve`) → Accounting Manager lain **Setujui & Konfirmasi** (post) atau **Tolak**
  (tetap draft, Q-G60-2). Penyetuju ≠ peminta ≠ pembuat. Reset ke draft setelah disetujui = persetujuan gugur.
- Wizard Register Payment: payment di atas ambang **tidak di-post**, ditandai `to_approve` (tetap tertaut ke tagihan via
  `matched_payment_ids`); saat disetujui, payment di-post lalu direkonsiliasi dengan tagihan.
- Menu **Akuntansi > Vendors > Pembayaran Menunggu Persetujuan** (Accounting Manager).
- Cetak: *Payment Voucher (EFN)* / *Payment Receipt (EFN)* (A4 landscape), terbilang, blok Disiapkan/Diperiksa/Disetujui,
  tanda **BELUM DISETUJUI**. Nomor rekening tujuan dimasking `****1234` kecuali Accounting Manager (Q-G60-3).
- SEC-G60-01: field persetujuan (`efn_approval_state`, `efn_approval_requested_by_id`, `efn_approved_by_id`,
  `efn_approved_at`) ditolak di `write` dan dibuang di `create` (termasuk `default_*` konteks) kecuali `sudo` + konteks
  privat `efn_payment_approval_system` yang dipasang kode modul setelah cek akses. `action_post` juga mewajibkan penyetuju
  terisi dan bukan pembuat/peminta. Pengecualian POS hanya di alur sudo tutup sesi; transfer internal hanya bila partner =
  partner PT **dan** akun tujuan = akun transfer.
- SEC-G60-02: perubahan nilai isi (nominal, partner, rekening tujuan, jurnal, tanggal, akun tujuan, dll.) pada payment
  `to_approve`/`approved` menggugurkan persetujuan (`not_required`, wajib minta ulang).

## B. MDR

- **Akuntansi > Konfigurasi > Akuntansi > Tarif MDR** (`efn.mdr.rate`): per metode bayar bank, persen (4 desimal) +
  biaya tetap per transaksi, akun beban, periode berlaku (tidak boleh tumpang tindih). Tarif = **PLACEHOLDER** (Q-G60-5;
  default satu EDC + satu QRIS per PT). Demo: EDC 1,5 %, QRIS 0,7 % untuk metode `efn_setup_pos` (tanpa dependency).
- Tutup sesi: per metode bank bertarif berlaku → satu jurnal `Dr beban MDR (analitik Outlet) / Cr akun outstanding metode`
  di jurnal `efn_mdr_journal_id` (kosong = jurnal umum pertama PT), `ref = "MDR estimasi <sesi> <metode>"`, smart button
  **Jurnal MDR** di sesi. MDR = neto metode × % + tetap × jumlah transaksi penjualan (refund mengurangi, Q-G60-7).
- Konfigurasi kurang/gagal → **tidak memblok tutup sesi**: dilewati + pesan di chatter sesi + log WARNING (tanpa PII).
- **Untuk G58 (true-up saat settlement):** `account.reconcile.model` per outlet/MID di jurnal bank settlement, baris ke akun
  beban MDR yang sama dengan `analytic_distribution` outlet. Yang tersedia untuk G58: `efn.mdr.rate` (tarif acuan,
  `_efn_rate_for(method, date)`, `_efn_compute(amount, count)`), `account.move.efn_mdr_pos_session_id` (estimasi per sesi),
  `stock.warehouse.efn_analytic_account_id` (outlet). Batch payment G58 hanya boleh memuat payment
  `efn_approval_state in ('not_required', 'approved')`.

## C. Analitik Outlet

- Data: plan **Outlet** (`analytic_plan_outlet`, optional). Analitik diaktifkan untuk user akuntansi
  (`account.group_account_invoice` imply `analytic.group_analytic_accounting`, Q-G60-9).
- **Akuntansi > Konfigurasi > Analytic Accounting > Siapkan Analitik Outlet** (Accounting Manager): Cek (tidak menulis) /
  Terapkan (idempoten): satu akun analitik per gudang yang dipakai POS (opsi semua gudang), nama/kode = gudang.
- Tutup sesi: baris laba-rugi (`internal_group` income/expense: penjualan, pembulatan, beban stok) dicap outlet, digabung
  dengan distribusi bawaan (plan lain tetap). Baris pajak/piutang tidak dicap (Q-G60-8). Gudang belum terpetakan → tidak
  dicap; peringatan chatter bila PT sudah menjalankan wizard.
- PO ke gudang outlet: baris PO membawa analitik outlet → tagihan (Q-G60-6).
- Laporan: **Akuntansi > Reporting > Analytic Report** (pivot per akun analitik Outlet).

## Cara uji manual

1. Settings > Akuntansi: ambang 1.000.000 (PT uji). Buat pembayaran vendor 1.500.000 sebagai user Invoicing → Confirm
   ditolak → Minta Persetujuan → login Accounting Manager lain → Setujui → posted. Cetak voucher sebagai user Invoicing:
   rekening `****xxxx`.
2. Buat Tarif MDR untuk "Kartu Debit/Kredit (EDC manual)" 1,5 % + 500. Jalankan wizard Siapkan Analitik Outlet. Buka sesi
   POS, 3 transaksi EDC total 1.000.000, tutup sesi → smart button Jurnal MDR = 16.500; baris penjualan jurnal sesi
   ber-analitik outlet; Analytic Report menampilkan pendapatan per outlet.

## Test

`tests/test_payment_approval.py` (AccountTestInvoicingCommon) dan `tests/test_pos_mdr_outlet.py` (TestPoSCommon, sesi
POS nyata), tag `efn`, `post_install`.

## Batasan / belum

- Selisih kas penutupan (`_post_statement_difference`, baris statement kas) **belum** dicap outlet; jurnal valuasi stok
  real-time dari picking POS tidak tercap (valuasi EFN periodik, G61).
- Cetak voucher belum diaudit `efn_audit` (render `ir.actions.report` tidak lewat hook ORM tulis); sisa risiko: siapa
  mencetak voucher tidak tercatat. Aturan `efn.audit.rule` (`efn.mdr.rate`, field approval, ambang) ditambahkan di
  `efn_setup_audit` (di luar modul ini).
- Persetujuan hanya pesan chatter (tanpa aktivitas/notifikasi ke daftar approver).
- Transfer internal dideteksi dari `paired_internal_transfer_payment_id` / akun transfer PT (CE 19 tanpa
  `is_internal_transfer`) — [B] bila ada pola transfer lain.
- Filter outlet pada laporan Laba Rugi `efn_account_reports` belum ada (rekomendasi spesifikasi, gelombang terpisah).
- Upgrade modul `account` dapat menulis ulang `implied_ids` grup Invoicing; upgrade modul ini memulihkan.

**Hash commit `odoo-platform` (origin/main, dicatat efn-lead 23 Nov 2026):** `custom_levis_localization` `17200d4`.
