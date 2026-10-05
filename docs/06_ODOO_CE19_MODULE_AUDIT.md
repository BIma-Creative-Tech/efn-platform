# AUDIT MODUL ODOO CE 19 vs ENTERPRISE vs OCA

- **Tanggal verifikasi:** 2026-10-05
- **Metode:** pengecekan langsung ke sumber primer GitHub:
  - CE: `https://raw.githubusercontent.com/odoo/odoo/19.0/addons/<modul>/__manifest__.py` (200 = ada, 404 = tidak ada di CE) dan isi file model terkait.
  - OCA: `https://api.github.com/repos/OCA/<repo>/contents?ref=19.0` (daftar direktori) dan `__manifest__.py` per modul (versi, lisensi, `installable`).
  - Enterprise: repo `odoo/enterprise` **privat** → status Enterprise hanya dapat **diindikasikan** (modul tidak ada di CE, ada toggle/label "Enterprise" di kode CE), bukan diverifikasi.
- **Belum dilakukan:** instalasi nyata di sandbox. Ada di repo ≠ terbukti berfungsi untuk skenario EFN. PoC fase 0 (ADR-021) wajib mengonfirmasi.
- Terkait: ADR-013, ADR-015, ADR-018, ADR-020, ADR-021, R-T03, R-T04.

> **Ringkasan manajemen.** Kabar baik: hampir semua kebutuhan inti tersedia gratis di Odoo Community 19 atau OCA versi 19 — POS retail & restoran, kiosk/QR order, loyalty & gift card, manufaktur (kitting), lot & kedaluwarsa, pajak Indonesia termasuk e-Faktur Coretax dan QRIS, inter-company, laporan konsolidasi, dan audit log. Dua kesenjangan nyata: **layar dapur (KDS)** dan **booking meja** hanya ada di Enterprise dan belum ada padanan OCA; serta ekstensi POS dari komunitas untuk versi 19 masih sangat sedikit (4 modul vs 28 di versi 18). Rencana: printer dapur dulu, KDS dibangun sendiri (±4–6 minggu), booking ditunda.

Legenda status: **[ADA]** terverifikasi ada di branch 19.0 · **[TIDAK]** terverifikasi tidak ada (404 / tidak ada di daftar) · **[INDIKASI]** indikasi, belum terverifikasi · **[BELUM]** belum terverifikasi.

## 1. Tabel kebutuhan → modul

| # | Kebutuhan EFN | CE 19 (odoo/odoo) | Enterprise | OCA 19.0 | Status 19.0 | Gap & rencana |
|---|---|---|---|---|---|---|
| 1 | POS retail (scan barcode, sesi, offline) | `point_of_sale` | — | OCA/pos: `pos_edit_order_line`, `pos_session_sequence`, `pos_printing_qztray`, `pos_partner_birthdate` | [ADA] CE (dasar); [ADA] 4 modul OCA | Offline & cash control bawaan perlu dibuktikan di PoC ([BELUM]). Ekstensi OCA 18.0 yang belum ada di 19.0: `pos_full_refund`, `pos_cash_control_extension`, `pos_blind_session_closing`, `pos_payment_terminal`, `pos_product_expiry`, `pos_lot_barcode`, `pos_margin`, `pos_product_multi_barcode` → porting bila dibutuhkan (±2–5 hari/modul). |
| 2 | POS restoran (meja, split bill, print bill, kitchen printer) | `pos_restaurant` (LGPL-3): *"Bill Printing … Bill Splitting … Kitchen Order Printing"*; field `floor_ids`, `iface_splitbill`, `iface_printbill` | — | — | [ADA] | Koneksi printer dapur (network/ePOS vs IoT box) [BELUM] — IoT box kemungkinan Enterprise ([INDIKASI]). OCA `pos_printing_qztray` [ADA] sebagai alternatif cetak. |
| 3 | KDS / Preparation Display | [TIDAK] `pos_preparation_display` 404 di CE 19.0 | [INDIKASI] Enterprise | [TIDAK] tidak ada di OCA/pos 19.0 maupun 18.0 | [TIDAK] | **Custom `efn_pos_kds`** fase 2 (4–6 minggu); printer dapur dulu (ADR-013). |
| 4 | Kiosk / self-order / QR menu | `pos_self_order` (LGPL-3), `self_ordering_mode`: `consultation` (QR menu), `mobile` (QR + ordering), `kiosk` | Opsi *pay after "Each Order"* berlabel *"(require Odoo Enterprise)"* | — | [ADA] | Pakai mode *pay after meal* atau kiosk bayar di muka. Pembayaran online self-order via `pos_online_payment` [ADA] (dependensi `account_payment`). |
| 5 | Table booking / online booking | [TIDAK] `pos_appointment` 404 di CE 19.0; toggle CE `module_pos_appointment = "Online Booking"` ada di `point_of_sale` | [INDIKASI] Enterprise (indikasi dari toggle) | — | [TIDAK] | Ditunda (fase 6) atau form reservasi sederhana di Super-App. |
| 6 | Waitlist | [BELUM] tidak ditemukan modul khusus | [BELUM] | — | [BELUM] | Keluar dari MVP. |
| 7 | Loyalty, kupon, gift card, eWallet | `loyalty` (*"discounts, gift card, eWallets and loyalty programs"*), `pos_loyalty`, `sale_loyalty` (semua LGPL-3) | — | — | [ADA] | **Tidak** dipakai untuk poin member (ADR-020: ledger di BFF). Boleh untuk promo mekanik toko. |
| 8 | Diskon global POS | `pos_discount` | — | — | [ADA] | — |
| 9 | Login kasir per karyawan | `pos_hr` | — | — | [ADA] | — |
| 10 | POS ↔ Sales Order | `pos_sale` | — | — | [ADA] | — |
| 11 | MRP / Kitting (BoM Manufacture, MO) | `mrp` (LGPL-3), `pos_mrp` (*"link module between Point of Sale and Mrp"*) | — | OCA/manufacture 19.0: 20 modul, mis. `mrp_multi_level`, `mrp_bom_tracking`, `mrp_production_note`, `quality_control_oca` | [ADA] | Sesuai ADR-018. `mrp_multi_level` untuk perencanaan CK dievaluasi bila produksi berbasis forecast (Q-35). |
| 12 | Multi-company (1 DB) | inti `base` | — | OCA/multi-company 19.0: `base_multi_company`, `product_multi_company`, `product_tax_multicompany_default`, `mail_multicompany`, `res_company_active`, `res_company_search_view`, `calendar_event_type_multi_company` | [ADA] | — |
| 13 | Inter-company PO→SO & invoice | [TIDAK] `account_inter_company_rules` 404 di CE 19.0 | [INDIKASI] Enterprise (inter-company rules) | `purchase_sale_inter_company` **19.0.1.0.1** (AGPL-3; depends `sale`, `purchase`, `account_invoice_inter_company`), `account_invoice_inter_company` **19.0.1.0.3** (AGPL-3) | [ADA] OCA | **Gap:** `purchase_sale_stock_inter_company` (sinkron picking) **tidak ada** di daftar OCA/multi-company 19.0. Perilaku stok (delivery PT-A ↔ receipt PT-B) [BELUM] → uji di PoC; bila perlu glue di `efn_intercompany`. |
| 14 | Lokalisasi Indonesia (CoA, pajak) | `l10n_id` v1.3 (*"generic Indonesian chart of accounts … tax structure"*) | — | OCA/l10n-indonesia branch 19.0 **kosong** (hanya `.github`) | [ADA] CE | PB1 (pajak daerah) dikonfigurasi manual sebagai pajak di PT F&B (template PB1 di `l10n_id` [BELUM]). |
| 15 | e-Faktur Coretax | `l10n_id_efaktur_coretax` (LGPL-3; *"CoreTax … CSV to XML … 11/12"*) ; [TIDAK] `l10n_id_efaktur` lama 404 di 19.0 | — | — | [ADA] | Validasi dengan konsultan pajak (R-L03). |
| 16 | QRIS | `l10n_id` memuat `models/qris_transaction.py` dan `res_bank.py` | — | — | [ADA] file ada; fungsi [BELUM] | Detail (QRIS dinamis/statis, provider) diverifikasi di PoC. Alternatif QRIS via gateway (ADR-016). |
| 17 | POS lokalisasi ID | `l10n_id_pos` (*"Indonesian Point of Sale"*) | — | — | [ADA] | Isi fitur [BELUM]. |
| 18 | Payment gateway online | `payment_xendit` (LGPL-3, *"Indonesia and the Philippines"*); [TIDAK] `payment_midtrans` 404; [TIDAK] `pos_xendit` 404 | — | — | [ADA] Xendit | Checkout Super-App lewat BFF (ADR-016); EDC di POS: OCA `pos_payment_terminal` hanya 18.0. |
| 19 | Konsolidasi keuangan | [TIDAK] `account_consolidation` 404 di CE 19.0 | [INDIKASI] Enterprise | `mis_builder` **19.0.1.2.1** (AGPL-3; depends `account`, `board`, `report_xlsx`, `date_range`); OCA/account-financial-reporting 19.0: `account_financial_report`, `account_tax_balance`, `mis_builder_cash_flow`, `partner_statement` | [ADA] OCA | **Dependensi transitif:** `report_xlsx` (OCA/reporting-engine 19.0.1.0.2 [ADA]) dan `date_range` (OCA/server-ux 19.0.1.0.0 [ADA]) **belum** ada di `scripts/fetch-oca.sh` → harus ditambahkan. |
| 20 | Audit log | — | — | `auditlog` (OCA/server-tools) **19.0.2.0.0** (AGPL-3) | [ADA] | Konfigurasi rule untuk pricelist, product, stock quant, account move (R-S10). |
| 21 | Lot / serial & expiry, FEFO | `stock` (lot inti), `product_expiry` (LGPL-3, depends `stock`) | — | OCA `pos_product_expiry`, `pos_lot_barcode` hanya 18.0 | [ADA] CE | Strategi FEFO di 19.0 [BELUM] (diuji PoC). |
| 22 | Batch transfer warehouse | `stock_picking_batch` (CE, LGPL-3) | — | — | [ADA] | Catatan: `services/odoo/oca/README.md` menyebut modul ini dari OCA — keliru, ini modul CE. |
| 23 | QC Intake (farming) | — (`quality_control` [INDIKASI] Enterprise, tidak dicek) | [INDIKASI] | `quality_control_oca` **19.0.1.0.0** (AGPL-3, depends `product`) | [ADA] OCA | Evaluasi sebagai dasar `efn_agri`. |
| 24 | Job async di Odoo | — | — | OCA/queue 19.0: `queue_job`, `queue_job_cron`, `queue_job_batch`, … | [ADA] | Opsional (mis. jurnal liabilitas poin, sinkron BFF). |
| 25 | Customer display | [BELUM] | [BELUM] | — | [BELUM] | Verifikasi di PoC (kebutuhan input OTP oleh pelanggan, ADR-017). |
| 26 | Timbangan / barcode berat | [BELUM] (barcode nomenclature) | IoT [INDIKASI] | `pos_barcode_rule_priced_with_change_rate` hanya 18.0 | [BELUM] | Q-25. |
| 27 | Food marketplace (GoFood/GrabFood) | [BELUM] | [BELUM] | — | [BELUM] | Bergantung program partner (R-B04); fase 6. |

## 2. Daftar modul OCA 19.0 yang direkomendasikan untuk `scripts/fetch-oca.sh`

| Repo | Modul | Versi terverifikasi | Lisensi |
|---|---|---|---|
| OCA/multi-company | `account_invoice_inter_company`, `purchase_sale_inter_company` | 19.0.1.0.3 / 19.0.1.0.1 | AGPL-3 |
| OCA/mis-builder | `mis_builder` | 19.0.1.2.1 | AGPL-3 |
| OCA/reporting-engine | `report_xlsx` (dependensi mis_builder) | 19.0.1.0.2 | AGPL-3 |
| OCA/server-ux | `date_range` (dependensi mis_builder) | 19.0.1.0.0 | LGPL-3 |
| OCA/server-tools | `auditlog` | 19.0.2.0.0 | AGPL-3 |
| OCA/manufacture | `quality_control_oca` (fase 5, evaluasi) | 19.0.1.0.0 | AGPL-3 |

`fetch-oca.sh` saat ini hanya mengambil multi-company, server-tools, mis-builder → **`mis_builder` akan gagal di-install** karena `report_xlsx` dan `date_range` tidak ikut. Ditugaskan ke efn-odoo-engineer / efn-devsecops-engineer (agent architect hanya menulis di `docs/`).

## 3. Backlog custom/porting hasil audit

| Item | Jenis | Estimasi (asumsi) | Fase |
|---|---|---|---|
| `efn_pos_kds` | Custom (OWL + bus) | 4–6 minggu | 2 |
| `efn_pos_otp` | Custom | 2–3 minggu | 2 |
| Glue stok inter-company (bila PoC membuktikan perlu) | Custom/porting | 1–2 minggu | 1 |
| Porting ekstensi OCA/pos 18.0 yang dibutuhkan (refund penuh, cash control, payment terminal, lot barcode) | Porting | 2–5 hari/modul | 1–2 |
| Booking meja | Custom/ditunda | 3–4 minggu | 6 |

## 4. Yang belum terverifikasi (harus diuji di PoC sandbox CE 19)
1. POS offline mode & sinkronisasi order di 19.0.
2. Koneksi printer dapur tanpa IoT box.
3. Perilaku stok OCA `purchase_sale_inter_company` 19.0 (apakah picking tersinkron).
4. Template pajak `l10n_id` (PPN 12% × 11/12, PB1) dan alur QRIS `qris_transaction`.
5. FEFO & expiry di POS.
6. Customer display, timbangan, waitlist.
7. Status Enterprise untuk KDS, booking, inter-company rules, konsolidasi, IoT — **indikasi** saja karena repo Enterprise privat.
