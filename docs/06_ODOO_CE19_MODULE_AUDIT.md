# AUDIT MODUL ODOO CE 19 vs ENTERPRISE + REFERENSI (OCA / Cybrosys)

- **Tanggal verifikasi:** CE & OCA 2026-10-05; **revisi ADR-031 + Cybrosys 2026-10-06**
- **Kebijakan (ADR-031, diputuskan klien 6 Okt 2026):** yang dipasang hanya **Odoo CE 19** + modul **`efn_*`** milik EFN. OCA dan Cybrosys **tidak dipasang**; keduanya hanya **referensi desain** (dibaca, tidak disalin). Panduan lisensi & clean implementation: ADR-031.
- **Metode:** pengecekan langsung ke sumber primer:
  - CE: `https://raw.githubusercontent.com/odoo/odoo/19.0/addons/<modul>/__manifest__.py` (200 = ada, 404 = tidak ada di CE) dan isi file model terkait.
  - OCA: `https://api.github.com/repos/OCA/<repo>/contents?ref=19.0` dan `__manifest__.py` per modul (versi, lisensi, `installable`).
  - Cybrosys: `https://raw.githubusercontent.com/CybroOdoo/CybroAddons/19.0/<modul>/__manifest__.py` (+README bila perlu); GitHub API `repos/CybroOdoo/CybroAddons` (lisensi repo `null` → lisensi **per modul**); modul berbayar di `apps.odoo.com` (OPL-1, tidak ada di repo publik). Daftar direktori 19.0 lewat tree API **terpotong**, sehingga hanya modul yang namanya diketahui yang bisa dicek.
  - Enterprise: repo `odoo/enterprise` **privat** → status Enterprise hanya **diindikasikan**.
- **Belum dilakukan:** instalasi nyata di sandbox. PoC fase 0 (ADR-021) wajib mengonfirmasi.
- Terkait: ADR-013, ADR-015 (sebagian digantikan), ADR-018, ADR-020, ADR-021, **ADR-031**, R-T03, R-T04, R-L08.

> **Ringkasan manajemen.** Sebagian besar kebutuhan inti tersedia gratis di Odoo Community 19: POS retail & restoran, kiosk/QR order, loyalty & gift card, manufaktur (kitting), lot & kedaluwarsa, pajak Indonesia termasuk e-Faktur Coretax, dan ekspor Excel. Sesuai keputusan klien 6 Okt 2026, modul komunitas (OCA) dan vendor (Cybrosys) **tidak dipasang**, hanya dijadikan contoh desain. Akibatnya **audit log, inter-company, dan laporan keuangan/konsolidasi** yang sebelumnya diambil dari OCA kini dibangun sendiri atau dipindah ke Metabase (±10–16 minggu-engineer tambahan, ADR-031). Kesenjangan lama tetap: **layar dapur (KDS)** dibangun sendiri (±4–6 minggu), **booking meja** ditunda.

Legenda status: **[ADA]** terverifikasi ada di branch tersebut · **[TIDAK]** terverifikasi tidak ada (404 / tidak ada di daftar) · **[INDIKASI]** indikasi, belum terverifikasi · **[BELUM]** belum terverifikasi.
Kolom "Referensi" berisi modul yang **boleh dipelajari**, bukan dipasang. Lisensi ditulis apa adanya dari manifest; "lisensi belum diverifikasi" berarti manifest belum dibaca.

## 1. Tabel kebutuhan → modul

| # | Kebutuhan EFN | CE 19 (odoo/odoo) | Enterprise | Referensi (OCA / Cybrosys) — **tidak dipasang** | Status CE 19.0 | Rencana (ADR-031) |
|---|---|---|---|---|---|---|
| 1 | POS retail (scan barcode, sesi, offline) | `point_of_sale` | — | OCA/pos 19.0: `pos_edit_order_line`, `pos_session_sequence`, `pos_printing_qztray`, `pos_partner_birthdate` [ADA] (lisensi belum diverifikasi); OCA/pos 18.0: `pos_full_refund`, `pos_cash_control_extension`, `pos_blind_session_closing`, `pos_payment_terminal`, `pos_product_expiry`, `pos_lot_barcode`, `pos_margin`, `pos_product_multi_barcode`. Cybrosys 19.0: banyak modul `pos_*` (mis. `pos_all_orders`, `pos_access_right_hr`, `pos_customer_restrict`) — **per modul belum terverifikasi** | [ADA] | Standar CE. Ekstensi hanya bila PoC membuktikan perlu → `efn_pos_*` kecil (2–5 hari/modul). Offline & cash control [BELUM] di PoC. |
| 2 | POS restoran (meja, split bill, print bill, kitchen printer) | `pos_restaurant` (LGPL-3): *"Bill Printing … Bill Splitting … Kitchen Order Printing"*; `floor_ids`, `iface_splitbill`, `iface_printbill` | — | OCA `pos_printing_qztray` (19.0 [ADA]); Cybrosys `pos_book_order` **19.0.1.0.0, AGPL-3** (depends `base`, `point_of_sale`; *pickup/delivery order*, bukan meja) | [ADA] | Standar CE. Printer dapur ePOS jaringan [BELUM]; bila gagal → `efn_pos_print_bridge` (QZ Tray) setelah Q-19. IoT box [INDIKASI] Enterprise. |
| 3 | KDS / Preparation Display | [TIDAK] `pos_preparation_display` 404 | [INDIKASI] | OCA: [TIDAK] (19.0 & 18.0). Cybrosys **`pos_kitchen_screen_odoo` 19.0.1.0.0, LGPL-3** (depends `web`, `pos_restaurant`; tahap Cooking/Ready/Completed; juga tersedia 15.0–18.0 di apps.odoo.com). `kitchen_screen` 404 di 19.0 & 18.0 | [TIDAK] | **`efn_pos_kds`** fase 2 (4–6 minggu); printer dapur dulu (ADR-013). |
| 4 | Kiosk / self-order / QR menu | `pos_self_order` (LGPL-3): `consultation`, `mobile`, `kiosk` | *pay after "Each Order"* = *"(require Odoo Enterprise)"* | — | [ADA] | Standar CE; *pay after meal* atau kiosk bayar di muka. Pembayaran lewat BFF (ADR-026, F-30). |
| 5 | Table booking | [TIDAK] `pos_appointment` 404; toggle `module_pos_appointment` ada | [INDIKASI] | Cybrosys `pos_restaurant_table_booking` **404 di 19.0**; padanan Cybrosys lain **belum terverifikasi** | [TIDAK] | Ditunda (fase 6) atau form reservasi di Super-App. |
| 6 | Waitlist | [BELUM] | [BELUM] | Cybrosys `odoo_queue_manager` 19.0.1.0.0 AGPL-3 = antrean/token pelanggan berbasis `website_sale` (relevansi untuk restoran **belum dinilai**) | [BELUM] | Keluar dari MVP. |
| 7 | Loyalty, kupon, gift card, eWallet | `loyalty`, `pos_loyalty`, `sale_loyalty` (LGPL-3) | — | Cybrosys `advanced_loyalty_management` **19.0.1.0.0, LGPL-3** (depends `loyalty`, `pos_loyalty`, `sale`, `point_of_sale`; mis. pencabutan poin saat refund) | [ADA] | Poin member **tidak** di Odoo (ADR-020: ledger BFF); referensi Cybrosys berguna untuk aturan refund→poin di ledger BFF. Promo toko = standar CE. |
| 8 | Diskon global POS | `pos_discount` | — | — | [ADA] | Standar. |
| 9 | Login kasir per karyawan | `pos_hr` | — | Cybrosys `pos_access_right_hr` (19.0, ada di daftar; manifest belum dibaca) | [ADA] | Standar. |
| 10 | POS ↔ Sales Order | `pos_sale` | — | — | [ADA] | Standar. |
| 11 | MRP / Kitting (BoM Manufacture, MO) | `mrp` (LGPL-3), `pos_mrp` | — | OCA/manufacture 19.0 (mis. `mrp_multi_level`); Cybrosys **`pos_automated_manufacturing` 19.0.1.0.0, LGPL-3** (MO dari POS — **berbeda** dari ADR-018 yang membuat MO dari permintaan outlet) | [ADA] | Standar CE + `efn_kitting` (ADR-018). Perencanaan multi-level (Q-35) → standar MRP dulu. |
| 12 | Multi-company (1 DB) | inti `base` | — | OCA/multi-company 19.0: `base_multi_company`, `product_multi_company`, … (lisensi belum diverifikasi); Cybrosys: **belum terverifikasi** | [ADA] | Standar CE (`company_id` kosong = dibagi semua company). Subset company (Q-8) → `efn_multi_company_share` (±1 minggu) bila perlu. |
| 13 | Inter-company PO→SO & invoice | [TIDAK] `account_inter_company_rules` 404 | [INDIKASI] | OCA `purchase_sale_inter_company` 19.0.1.0.1 (AGPL-3), `account_invoice_inter_company` 19.0.1.0.3 (AGPL-3); `purchase_sale_stock_inter_company` [TIDAK] di 19.0. Cybrosys **`inter_company_synchronization` 19.0.1.0.0, AGPL-3** (depends `purchase`, `sale_management`, `stock`; README: SO/PO saja, tanpa invoice/picking) | [TIDAK] | **`efn_intercompany`** (4–6 minggu): PO↔SO, invoice→bill, sinkron picking/lot, idempotensi. |
| 14 | Lokalisasi Indonesia (CoA, pajak) | `l10n_id` v1.3 | — | OCA/l10n-indonesia 19.0 **kosong** | [ADA] | Standar; PB1 via konfigurasi (ADR-027). |
| 15 | e-Faktur Coretax | `l10n_id_efaktur_coretax` (LGPL-3); `l10n_id_efaktur` lama 404 | — | — | [ADA] | Standar; validasi konsultan pajak (R-L03). |
| 16 | QRIS | `l10n_id` (`qris_transaction.py`, `res_bank.py`) | — | — | [ADA] file; fungsi [BELUM] | Tidak dipakai; QRIS lewat BFF (`efn_pos_payment_gateway`, ADR-026). |
| 17 | POS lokalisasi ID | `l10n_id_pos` | — | — | [ADA] | Isi fitur [BELUM]. |
| 18 | Payment gateway online | `payment_xendit` (LGPL-3); `payment_midtrans` 404; `pos_xendit` 404 | — | OCA `pos_payment_terminal` hanya 18.0 | [ADA] Xendit | Tidak dipakai; checkout di BFF (ADR-026). |
| 19 | Konsolidasi & laporan keuangan | [TIDAK] `account_consolidation` 404; CE tanpa laporan Neraca/L-R (Enterprise `account_reports`, [INDIKASI]) | [INDIKASI] | OCA `mis_builder` 19.0.1.2.1 (AGPL-3; depends `report_xlsx`, `date_range`), `account_financial_report` **19.0.0.0.26, AGPL-3** (depends `account`, `date_range`, `report_xlsx`). Cybrosys **`base_accounting_kit` 19.0.3.3.1, LGPL-3** & **`dynamic_accounts_report` 19.0.1.1.2, LGPL-3** (keduanya depends `cybrosys_support_client` = asisten dukungan Cybrosys; README tidak menyebut multi-company/konsolidasi). Berbayar: `account_reports_xlsx` 19.0 **OPL-1** (USD 22,40) — **hanya deskripsi publik** | [TIDAK] | Konsolidasi → **ClickHouse/Metabase** (docs/01 §3.6, ADR-003). Laporan per PT → Q-V3: (a) pivot `account.move.line` + ekspor XLSX CE + Metabase, atau (b) `efn_account_reports` (4–6 minggu). |
| 20 | Audit log | — (CE: `mail` tracking per field `tracking=True`, terbatas pada field yang ditandai) | — | OCA `auditlog` **19.0.2.0.0, AGPL-3** (bug ORM 19 → `efn_setup_audit/patches.py`; manifest 6 Okt masih 19.0.2.0.0). Cybrosys **`user_audit` 19.0.1.0.0, LGPL-3** (depends `web`; "create, write, read"; nilai lama/baru **belum terverifikasi**) | [TIDAK] | **`efn_audit`** (2–3 minggu), menggantikan `efn_setup_audit` + OCA. |
| 21 | Lot / serial & expiry, FEFO | `stock`, `product_expiry` (LGPL-3) | — | OCA `pos_product_expiry`, `pos_lot_barcode` hanya 18.0. Cybrosys **`pos_auto_lot_selection` 19.0.1.0.1, AGPL-3** (depends `point_of_sale`, `mrp`, `mrp_product_expiry`, `product`) | [ADA] | Standar; FEFO di POS [BELUM] (PoC). Gap → `efn_pos_lot` bila perlu. |
| 22 | Batch transfer warehouse | `stock_picking_batch` (CE, LGPL-3) | — | — | [ADA] | Standar. |
| 23 | QC Intake (farming) | — (`quality_control` [INDIKASI] Enterprise) | [INDIKASI] | OCA `quality_control_oca` 19.0.1.0.0 (AGPL-3); Cybrosys **belum terverifikasi** | [TIDAK] | **`efn_agri`** dari nol (+1–2 minggu dari estimasi lama). |
| 24 | Job async di Odoo | — | — | OCA `queue_job` **19.0.2.1.5, LGPL-3**. (Cybrosys `odoo_queue_manager` bukan padanan — antrean pelanggan) | — | Tidak dipakai: RabbitMQ/BFF + `ir.cron`. |
| 25 | Customer display | [BELUM] | [BELUM] | — | [BELUM] | PoC (OTP, ADR-017; QR pembayaran F-30). |
| 26 | Timbangan / barcode berat | [BELUM] (barcode nomenclature) | IoT [INDIKASI] | OCA `pos_barcode_rule_priced_with_change_rate` hanya 18.0 | [BELUM] | Q-25. |
| 27 | Food marketplace (GoFood/GrabFood) | [BELUM] | [BELUM] | — | [BELUM] | Fase 6 (R-B04). |
| 28 | Ekspor Excel / laporan XLSX | **`web`**: `ExcelExport`, route `/web/export/xlsx` (`addons/web/controllers/export.py`, `xlsxwriter` lazy import) | — | OCA `report_xlsx` 19.0.1.0.2 (AGPL-3); Cybrosys `advanced_excel_reports` 19.0.1.0.0 (LGPL-3), `excel_report_designer` 19.0.1.0.0 (AGPL-3) | [ADA] | Standar CE; laporan khusus pakai `xlsxwriter` di modul `efn_*` (1–3 hari/laporan). |
| 29 | Laporan POS (produk/kategori/pelanggan terlaris, sesi) | `point_of_sale` reporting standar | — | Cybrosys `advanced_pos_reports` 19.0.1.0.1 (LGPL-3), `pos_analysis_report` 19.0.1.0.0 (LGPL-3) | [ADA] dasar | Analitik lintas outlet di Metabase (ADR-003); laporan di Odoo cukup standar. |
| 30 | Filestore di object storage | — | — | OCA/storage 19.0 `fs_attachment_s3`, `fs_storage` (lisensi belum diverifikasi) | — | Tetap disk lokal LUKS (ADR-030); `efn_fs_s3` hanya bila dipicu (≥ fase 6). |

## 2. Daftar referensi terverifikasi (pengganti daftar `fetch-oca.sh`)

**Tidak ada modul pihak ketiga yang di-fetch atau di-deploy** (ADR-031). `scripts/fetch-oca.sh`, `services/odoo/oca/`, dan langkah fetch di CI harus dihapus (tugas devsecops/odoo-engineer). Daftar berikut hanya katalog referensi untuk spesifikasi `efn_*`.

| Vendor | Repo / sumber | Modul | Versi terverifikasi | Lisensi | Dipakai sebagai referensi untuk |
|---|---|---|---|---|---|
| OCA | server-tools | `auditlog` | 19.0.2.0.0 | AGPL-3 | `efn_audit` |
| OCA | multi-company | `purchase_sale_inter_company`, `account_invoice_inter_company` | 19.0.1.0.1 / 19.0.1.0.3 | AGPL-3 | `efn_intercompany` |
| OCA | mis-builder | `mis_builder` | 19.0.1.2.1 | AGPL-3 | konsep KPI/periode di Metabase |
| OCA | account-financial-reporting | `account_financial_report` | 19.0.0.0.26 | AGPL-3 | `efn_account_reports` (bila Q-V3 = b) |
| OCA | reporting-engine / server-ux | `report_xlsx` / `date_range` | 19.0.1.0.2 / 19.0.1.0.0 | AGPL-3 / LGPL-3 | tidak dibutuhkan (dependensi mis_builder) |
| OCA | manufacture | `quality_control_oca` | 19.0.1.0.0 | AGPL-3 | `efn_agri` |
| OCA | queue | `queue_job` | 19.0.2.1.5 | LGPL-3 | tidak dipakai |
| Cybrosys | CybroAddons 19.0 | `pos_kitchen_screen_odoo` | 19.0.1.0.0 | LGPL-3 | `efn_pos_kds` |
| Cybrosys | CybroAddons 19.0 | `user_audit` | 19.0.1.0.0 | LGPL-3 | `efn_audit` (UX) |
| Cybrosys | CybroAddons 19.0 | `inter_company_synchronization` | 19.0.1.0.0 | AGPL-3 | `efn_intercompany` |
| Cybrosys | CybroAddons 19.0 | `advanced_loyalty_management` | 19.0.1.0.0 | LGPL-3 | aturan refund→poin di ledger BFF (ADR-020) |
| Cybrosys | CybroAddons 19.0 | `base_accounting_kit`, `dynamic_accounts_report` | 19.0.3.3.1 / 19.0.1.1.2 | LGPL-3 | `efn_account_reports` (bila Q-V3 = b) |
| Cybrosys | CybroAddons 19.0 | `pos_auto_lot_selection` | 19.0.1.0.1 | AGPL-3 | FEFO/lot di POS |
| Cybrosys | CybroAddons 19.0 | `pos_automated_manufacturing` | 19.0.1.0.0 | LGPL-3 | `efn_kitting` (pembanding saja) |
| Cybrosys | CybroAddons 19.0 | `pos_book_order` | 19.0.1.0.0 | AGPL-3 | pickup/delivery POS (fase lanjut) |
| Cybrosys | CybroAddons 19.0 | `advanced_pos_reports`, `pos_analysis_report` | 19.0.1.0.1 / 19.0.1.0.0 | LGPL-3 | laporan POS (Metabase) |
| Cybrosys | CybroAddons 19.0 | `advanced_excel_reports`, `excel_report_designer` | 19.0.1.0.0 / 19.0.1.0.0 | LGPL-3 / AGPL-3 | laporan XLSX khusus |
| Cybrosys | apps.odoo.com | `account_reports_xlsx` | 19.0 | **OPL-1** (berbayar) | **hanya deskripsi publik; kode dilarang dibaca** |

Catatan Cybrosys: repo `CybroAddons` tidak memiliki lisensi induk (GitHub API `license: null`); setiap modul berlisensi sendiri — **selalu cek manifest** sebelum mencatat referensi. Modul akuntansi Cybrosys 19.0 bergantung `cybrosys_support_client` (19.0.1.0.1, LGPL-3, "Get Technical/Functional Assistance from Cybrosys without leaving your Database"); perilaku jaringannya belum diverifikasi.

## 3. Backlog custom hasil audit (direvisi ADR-031)

| Item | Jenis | Estimasi (asumsi) | Fase | Referensi utama |
|---|---|---|---|---|
| `efn_audit` (ganti OCA auditlog + `efn_setup_audit`) | Custom **baru** | 2–3 minggu | 1 | OCA `auditlog`, Cybrosys `user_audit` |
| `efn_intercompany` (PO↔SO, invoice→bill, picking) | Custom (dulu glue 1–2 mgg) | 4–6 minggu | 1 | OCA multi-company, Cybrosys `inter_company_synchronization` |
| Konsolidasi GL multi-company di ClickHouse/Metabase | Data/ETL | 3–4 minggu | 1–2 | OCA `mis_builder` (konsep) |
| Laporan keuangan per PT (Q-V3) | (a) konfigurasi + Metabase / (b) `efn_account_reports` | 1 / 4–6 minggu | 1–2 | OCA `account_financial_report`, Cybrosys `base_accounting_kit` |
| `efn_pos_kds` | Custom (OWL + bus) | 4–6 minggu | 2 | Cybrosys `pos_kitchen_screen_odoo` |
| `efn_pos_otp_gate` | Custom (sudah ada) | — | 2 | — |
| `efn_agri` (QC intake) | Custom (dulu berbasis OCA) | +1–2 minggu | 5 | OCA `quality_control_oca` |
| Ekstensi POS kecil (refund penuh, cash control, lot barcode, cetak QZ Tray) | Custom bila PoC membuktikan perlu | 2–5 hari/modul | 1–2 | OCA/pos 18.0/19.0, Cybrosys `pos_*` |
| `efn_multi_company_share` (Q-8) | Custom bila perlu | 1 minggu | 1 | OCA `product_multi_company` |
| Booking meja | Custom/ditunda | 3–4 minggu | 6 | belum ada referensi 19.0 terverifikasi |

## 4. Yang belum terverifikasi (harus diuji di PoC sandbox CE 19)
1. POS offline mode & sinkronisasi order di 19.0.
2. Koneksi printer dapur tanpa IoT box.
3. Template pajak `l10n_id` (PPN 12% × 11/12, PB1) dan alur QRIS `qris_transaction`.
4. FEFO & expiry di POS.
5. Customer display, timbangan, waitlist.
6. Status Enterprise untuk KDS, booking, inter-company rules, konsolidasi, laporan keuangan, IoT — **indikasi** saja (repo Enterprise privat).
7. **Referensi:** daftar lengkap modul CybroAddons 19.0 (tree API terpotong); padanan Cybrosys untuk QC intake, multi-company share, inter-company invoice, booking meja; cakupan nilai lama/baru `user_audit`; perilaku jaringan `cybrosys_support_client`; lisensi OCA/pos 19.0, `base_multi_company`/`product_multi_company`, OCA/storage.
8. ~~Perilaku stok OCA `purchase_sale_inter_company`~~ — tidak relevan lagi (ADR-031); diganti uji `efn_intercompany`.
