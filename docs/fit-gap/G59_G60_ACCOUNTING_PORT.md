# G59 / G60 — Port accounting `odoo-platform` → `efn_*` (PPh, deferred, recurring, voucher, MDR, outlet analytic)

- **Penulis:** efn-odoo-functional, 23 Nov 2026. **Status:** spesifikasi untuk efn-odoo-engineer (dokumen saja, belum ada kode).
- **ADR:** ADR-049 (port & adaptasi; aturan rename/dependency), ADR-031 (tanpa dependency non-CE), ADR-027 (tarif pajak = konfigurasi,
  bukan kode), ADR-036 (PII terenkripsi). Risiko: R-L03 (pajak), R-T03/R-L08 (provenance kode), R-T18 (redefinisi field PII).
- **Terkait:** `docs/fit-gap/ODOO_IMPLEMENTATION_GAP_REVIEW.md` §2 (P2 "Accounting port Levi's"), `docs/fit-gap/G54_G55_G61_ODOO_SETUP.md`
  (wizard POS, menu Finance/Accounting), **`docs/fit-gap/G57_G58_ACCOUNTING_PORT.md`** (agent lain, paralel: aset, kas kecil,
  rekonsiliasi/impor bank, batch payment).
- **Sumber yang dibaca:** snapshot `odoo-platform` origin/main (LGPL-3, `author: Custom Platform`): `ee_gap/custom_tax_id` (19.0.0.8.0),
  `ee_gap/custom_account_deferred` (19.0.1.0.0), `ee_gap/custom_accounting_recurring` (19.0.0.1.0), `ee_gap/custom_accounting_full`
  (19.0.0.7.0, hanya bagian analytic/branch), `_tenants/custom_levis_localization` (19.0.1.41.0, hanya fitur #4 voucher, #9 Operating
  Unit, #11 Card BIN/MDR, #15 POS Clearing sebagai pembanding). **Catatan:** folder `data/` semua modul `ee_gap` **tidak ada** di
  snapshot (seed kategori PPh 108 baris, cron, sequence) — tidak bisa dibandingkan; tidak dibutuhkan untuk desain di bawah.
- **Penanda verifikasi:** **[V]** dibaca di subset source CE 19 lokal (`scratchpad/odoo19src`, 23 Nov 2026, nomor baris disebut);
  **[G]** diverifikasi di `github.com/odoo/odoo@19.0` lewat WebFetch 23 Nov 2026; **[B]** belum terverifikasi — wajib dibuktikan
  engineer di test/PoC sebelum dianggap benar.

> **Ringkasan manajemen.** Dari tujuh fitur accounting yang diminta, **tiga cukup dengan pengaturan Odoo standar** (PPh potong di
> tagihan vendor, entri/tagihan berulang, voucher hadiah sebagai utang) sehingga tidak perlu menyalin kode Levi's; yang diport hanya
> bagian yang memang tidak ada di Odoo Community: **biaya dibayar di muka / pendapatan diterima di muka** (dibagi per bulan otomatis),
> **dokumen Payment Voucher/Receipt + persetujuan dua orang untuk pembayaran besar**, **biaya MDR kartu/QRIS per metode bayar**, dan
> **pemisahan laba-rugi per outlet**. Hasilnya tiga modul EFN baru (bukan empat), ±9–11 hari engineer termasuk tes, tanpa lisensi.
> Tarif PPh dan MDR tetap diisi Finance lewat layar (placeholder sampai konsultan pajak/bank mengonfirmasi).

---

## 0. Keputusan inti dan asumsi koordinasi

| # | Keputusan | Alasan singkat |
|---|---|---|
| D-1 | **PPh 23 / 4(2) / 26 = pajak negatif standar CE** di tagihan vendor (`account.tax` `amount < 0`, `type_tax_use='purchase'`), bukan engine aturan `custom_tax_id` | CE menghitung, membukukan (Dr Beban / Cr Hutang PPh / Cr AP neto) dan melaporkan PPh di jurnal tagihan itu sendiri; tax grid, refund, reset-to-draft, lock date ikut aturan CE. Engine Levi's bahkan **melewati** baris yang sudah punya pajak negatif (`account_move_inherit.py` L263–267) — berarti jalur standar sudah dianggap benar oleh penulisnya |
| D-2 | **Tidak port** DPP Nilai Lain, validasi NPWP/NIK, sinkron `vat`↔NPWP, Faktur Pengganti, bulk validation | CE `l10n_id` sudah punya PPN 12% efektif 11% (`tax_ST4`/`tax_PT4` amount 11) **[G]**, dan `l10n_id_efaktur_coretax` sudah mengalikan DPP 11/12 di XML (`models/account_move_line.py` L63–70) **[V]** → engine DPP kedua = DPP dikali dua kali. NIK/PKP sudah ada di CE (`res.partner.l10n_id_nik`, `l10n_id_pkp`) **[V]** dan dienkripsi `efn_pii_l10n_id` |
| D-3 | **`efn_account_recurring` tidak dibuat** | CE 19 `account.move.auto_post` = `no/at_date/monthly/quarterly/yearly` + `auto_post_until` + `_copy_recurring_entries()` (`account/models/account_move.py` L294–309, L4818–4866) **[V]** menutup entri jurnal **dan** tagihan vendor berulang (sewa, langganan). Template pembayaran berulang Levi's = uang keluar tanpa tagihan → bertentangan SoD; tidak diport |
| D-4 | Voucher hadiah/gift card = **akun liabilitas di produk**, bukan deferral berbasis waktu | Pendapatan voucher diakui saat ditukar, bukan dibagi per bulan. Konfigurasi: akun pendapatan produk voucher = `l10n_id_28110030` *Deferred Revenue* (atau akun liabilitas voucher Finance). Poin EFN sudah ditangani `efn_loyalty_accounting` |
| D-5 | MDR: **estimasi saat tutup sesi POS (per metode bayar) + true-up saat settlement** lewat reconcile model CE | Laba-rugi outlet harian lengkap tanpa menunggu impor bank; angka akhir tetap persis sama dengan potongan bank. Tanpa input BIN di kasir (lihat §G60.2) |
| D-6 | Outlet = **plan analitik "Outlet"**, satu akun analitik per **gudang outlet** (`stock.warehouse`), dicap otomatis ke baris P&L jurnal tutup sesi POS | Satu outlet bisa punya beberapa `pos.config` (kasir 1/2, kiosk) yang berbagi `warehouse_id` (`pos_config.py` L180) **[V]** |
| D-7 | Persetujuan pembayaran = **pola 4-eyes `efn_loyalty_accounting`** (pembuat ≠ penyetuju, `account.group_account_manager`), bukan `custom_approval_engine` | ADR-049: `custom_approval_engine` diganti pola 4-eyes yang ada |

**Asumsi koordinasi dengan G57/G58 (agent lain, `docs/fit-gap/G57_G58_ACCOUNTING_PORT.md`):**
1. G58 memiliki rekonsiliasi bank (UI + reconcile model + impor mutasi). **True-up MDR (D-5) adalah konfigurasi `account.reconcile.model`
   di atas UI G58**; G60 tidak membangun UI rekonsiliasi dan tidak memport `levis.pos.clearing`/`levis.bank.mid.map`/`levis.bank.narrative`.
   Bila G58 memport POS Clearing, ia boleh membaca `efn.mdr.rate` (model G60) sebagai tarif acuan — tidak sebaliknya.
2. Bila G58 membuat batch payment, **gerbang persetujuan ada di `account.payment` (G60)**; batch hanya berisi payment yang sudah
   `efn_approval_state in ('not_required','approved')`. Modul batch G58 depend ke `efn_account_localization` **atau** memeriksa field
   lewat `'efn_approval_state' in payment._fields` (pilih satu; rekomendasi: depend, karena pasangan modul ini selalu dipasang bersama).
3. Lock date / penutupan tahun (`custom_accounting_full` wizard lock date) **tidak** di G59/G60. Gap P2 "Lock date & tahun fiskal" di
   review G63 tetap milik efn-lead untuk ditempatkan (CE 19 sudah punya field lock date; yang kurang hanya UI ringkas + SOP).
4. Tidak ada modul basis bersama baru. Ketiga modul G59/G60 hanya depend ke CE + `efn_core`. Audit memakai aturan data `efn_audit`
   (`efn.audit.rule`) yang ditambahkan di `efn_setup_audit`, bukan mixin.

---

## G59-A. `efn_tax_withholding` — PPh potong (disederhanakan dari `custom_tax_id`)

### Fit-gap

| Kebutuhan | Standar CE 19 | Gap → custom | Status |
|---|---|---|---|
| PPh 23 jasa (2%) dipotong di tagihan vendor | `account.tax` purchase, `amount=-2`, repartition invoice/refund ke `l10n_id_21210030` *Tax Payable PPh 23* **[G]**; produk jasa `supplier_taxes_id` | — | Standar |
| PPh 4(2) sewa outlet (10%) | idem, akun `l10n_id_21210050` *Tax Payable 4(2)* **[G]**; produk "Sewa Outlet" | — | Standar |
| PPh 26 vendor luar negeri (20%) | `account.fiscal.position` "Vendor Luar Negeri" (`auto_apply`, country group selain ID) memetakan PPh 23 → PPh 26 (ADR-027 §Konteks: di 19 pemetaan ada di pajak tujuan, `original_tax_ids`/`fiscal_position_ids`) **[G]**; akun `l10n_id_21210060` | — | Standar |
| Vendor tanpa NPWP: tarif PPh 23 ×2 (2%→4%) | fiscal position "Vendor tanpa NPWP" (manual di partner, tanpa auto) memetakan PPh 23 2% → PPh 23 4% | — | Standar (⚠ konfirmasi konsultan pajak apakah masih berlaku sejak NIK=NPWP) |
| PPh dipotong **oleh pelanggan** (katering B2B memotong PPh 23 dari EFN) | pajak sale `amount=-2` ke `l10n_id_11510030` *Prepaid Tax PPh 23* **[G]**, atau selisih saat Register Payment | — | Standar |
| Alternatif: PPh dipotong saat **pembayaran** | modul CE `l10n_account_withholding_tax` (`is_withholding_tax_on_payment`, `withholding_sequence_id`, depends `account`) **[G]** | — | Opsi (tidak dipakai default; tidak boleh dicampur dengan D-1) |
| Kode objek pajak e-Bupot (mis. `24-104-01`) per pajak | tidak ada | field di `account.tax` | **Custom** |
| Daftar pemotongan per masa untuk input e-Bupot Unifikasi Coretax | tidak ada (CE `l10n_id_efaktur_coretax` hanya e-Faktur keluaran) **[V]** | list view `account.move.line` + export XLSX standar | **Custom (ringan, tanpa model baru)** |
| Nomor & tanggal bukti potong dari Coretax | tidak ada | field di baris pajak | **Custom** |
| XML e-Bupot | tidak ada | **tidak dibangun** (risiko format DJP berubah; input/impor manual di Coretax dari XLSX) | Ditunda (Q-G59-3) |

### Desain

**Model (semua `_inherit`, tidak ada model baru → tidak ada record rule baru; aturan multi-company `account.tax`/`account.move.line` CE berlaku):**

| Model | Field | Tipe / aturan |
|---|---|---|
| `account.tax` | `efn_pph_kind` | Selection `pph_23`, `pph_4_2`, `pph_26`, `pph_22`, `pph_15`; kosong = bukan PPh. Constraint: bila terisi → `amount_type='percent'` dan `amount < 0` |
| `account.tax` | `efn_bupot_object_code` | Char; constraint regex `^\d{2}-\d{3}-\d{2}$` bila terisi; wajib bila `efn_pph_kind` terisi dan `active` |
| `account.move.line` | `efn_bupot_number`, `efn_bupot_date` | Char / Date, `copy=False`; hanya relevan di baris `display_type='tax'` dengan `tax_line_id.efn_pph_kind`; boleh diedit setelah posted (field informasi, bukan nilai akuntansi) — tambahkan ke `_get_integrity_hash_fields`? **Tidak** (bukan field hash) **[B]** cek bahwa CE mengizinkan write field custom pada aml posted tanpa `skip_readonly_check` |
| `account.move.line` | `efn_pph_partner_vat` | related non-stored `partner_id.commercial_partner_id.vat`, readonly — dibaca lewat ORM sehingga dekripsi `efn_pii_base_vat` berlaku; **jangan** SQL view (kolom `vat` berisi ciphertext `efnpii2:`) |

**View & menu**
- Form pajak: grup "PPh (e-Bupot)" berisi dua field `account.tax` (inherit `account.view_tax_form`).
- Action `action_efn_withholding_register` (`account.move.line`, list + pivot), domain
  `[('tax_line_id.efn_pph_kind','!=',False), ('parent_state','=','posted')]`, filter masa (bulan), group by pajak/partner. Kolom: tanggal,
  nomor tagihan, partner, NPWP (`efn_pph_partner_vat`), kode objek, `tax_base_amount` (DPP), `balance` (PPh), nomor/tanggal bupot (editable
  inline untuk `account.group_account_user`).
- Menu: **Accounting → Reporting → Taxes & Fiscal** (`parent="account.account_reports_taxes_and_fiscal_menu"`) **[V]**, nama "Daftar
  Pemotongan PPh", `groups="account.group_account_user"`.

**Grup/ACL:** tidak ada model baru → `ir.model.access.csv` kosong (file tetap ada, header saja). Hak baca daftar = ACL `account.move.line` CE.

**Data**
- `data/`: tidak ada tarif. Hanya view/action/menu.
- `demo/account_tax_pph_demo.xml` (noupdate): untuk company demo `efn_setup_company` — PPh 23 2% (`24-104-01`), PPh 23 4% tanpa NPWP,
  PPh 4(2) sewa 10% (`28-403-01`), PPh 26 20% (`27-100-99`), fiscal position "Vendor Luar Negeri" + "Vendor tanpa NPWP". Semua bernama
  `... (PLACEHOLDER)`; kode objek **placeholder** sampai konsultan pajak (Q-G59-1).
- Produksi: Finance membuat pajak per PT lewat UI mengikuti tabel checklist di README modul (nama, tarif, akun `l10n_id_212100x0`, kode
  objek). Tidak ada `post_init_hook` yang membuat pajak per company (ADR-027: tarif bukan kode).

**Depends:** `account`, `l10n_id`, `efn_core`. (Tidak ke `l10n_id_efaktur_coretax`, `purchase`.)

**Port vs drop (sumber `custom_tax_id`)**

| File sumber | Aksi | Catatan |
|---|---|---|
| `models/withholding_category.py`, `withholding_rule.py`, `account_move_withholding_line.py` | **Drop** | Diganti pajak negatif CE + kode objek di `account.tax` |
| `models/account_move_inherit.py` (`_post`, `_custom_apply_withholding`, `_custom_post_withholding_entry`, unwind) | **Drop** | Jurnal PPh dari engine CE |
| `models/account_move_inherit.py` stash/restore label & nominal saat `button_draft` | **Ditunda (opsional P3)** | Keluhan pengguna Levi's "nominal PPN/PPh yang diketik hilang saat reset draft". Port hanya bila Finance EFN mengalaminya (Q-G59-4); bila diport: method privat `_efn_stash_tax_*` + test `test_reset_to_draft.py` (6 test) |
| `models/account_tax_inherit.py` (DPP Nilai Lain, override `_eval_tax_amount_*`) | **Drop** | D-2 (dobel dengan Coretax CE) |
| `models/res_partner_inherit.py`, `res_company_inherit.py`, `res_country_inherit.py`, `uom_inherit.py`, `product_template_inherit.py` | **Drop** | NPWP=`vat`, NIK/PKP = `l10n_id_efaktur_coretax`; kategori PPh produk diganti `supplier_taxes_id` |
| `wizards/faktur_pengganti_wizard.py`, `bulk_validation_wizard.py` | **Drop** | Domain e-Faktur keluaran = CE Coretax; bergantung `custom_coretax` |
| `migrations/19.0.0.8.0` | **Drop** | Data Levi's |
| `views/*` | Tulis ulang kecil | Hanya form pajak + register |

**Rename:** prefiks field `x_custom_*` → `efn_*`; tidak ada model `tax.withholding.*`.

**Test (tag `efn`, `AccountTestInvoicingCommon`)** — test asal hampir semua menguji engine yang di-drop; tulis baru:
1. tagihan jasa + PPh 23 −2% → AP = neto, Hutang PPh 23 = 2% DPP, `tax_base_amount` benar;
2. fiscal position luar negeri memetakan PPh 23 → PPh 26;
3. constraint `efn_pph_kind` dengan `amount ≥ 0` gagal; regex kode objek;
4. register hanya memuat baris PPh posted dan menghormati company (user PT A tidak melihat PT B);
5. `efn_bupot_number` bisa ditulis setelah posted oleh `group_account_user`, ditolak untuk user tanpa grup akuntansi;
6. NPWP tampil terdekripsi via ORM untuk user berhak (pakai kunci uji `efn_pii`).

**RPC publik:** tidak ada (0 entri `RPC_ALLOWLIST`).

**PII:** NPWP vendor perorangan = data pribadi; tampil hanya di list (ORM, terdekripsi untuk `group_account_user`). Export XLSX = data keluar →
tambahkan `efn.audit.rule` untuk export `account.move.line` dari action ini **[B]** (cek apakah `efn_audit` menangkap export; bila tidak,
catat sebagai sisa risiko di README). Retensi mengikuti retensi dokumen pajak (10 tahun, UU KUP — ⚠ konfirmasi Legal).

**Ukuran:** ±80 baris Python, ±150 baris XML, ±180 baris test. **1,5 hari.**

---

## G59-B. `efn_account_deferred` — biaya dibayar di muka & pendapatan diterima di muka (port `custom_account_deferred`)

### Fit-gap

| Kebutuhan | Standar CE 19 | Gap | Status |
|---|---|---|---|
| Sewa outlet dibayar 1–5 tahun di muka → beban per bulan | Tidak ada di CE (fitur EE `account_accountant`); field `deferred_*` **tidak ada** di `account/models` CE **[V]** (grep 0 hasil) | Port | **Custom** |
| Asuransi, lisensi tahunan, iklan kontrak | idem | Port | **Custom** |
| Pendapatan diterima di muka berbasis waktu (sewa ruang/konsinyasi, langganan katering) | idem | Port | **Custom** |
| Voucher/gift card | lihat D-4 (akun liabilitas di produk) | — | Standar |
| Posting otomatis entri pengakuan bulanan | `auto_post='at_date'` + cron CE `_autopost_draft_entries` (L6462) **[V]** | tanpa cron custom (sudah begitu di sumber) | Standar |

### Desain

**Model (`_inherit` saja; tidak ada model baru → tanpa record rule baru):**

| Model | Field (setelah rename) | Catatan |
|---|---|---|
| `account.move.line` | `efn_deferred_start_date`, `efn_deferred_end_date` (Date, `copy=False`) | Constraint sumber dipertahankan (keduanya atau tidak; end ≥ start) |
| `account.move` | `efn_deferred_origin_move_id` (M2o, `index='btree_not_null'`), `efn_deferred_entry_type` (`deferral`/`recognition`), `efn_deferred_generated_ids`, `efn_deferred_generated_count` | |
| `res.company` | `efn_deferred_expense_account_id`, `efn_deferred_revenue_account_id`, `efn_deferred_journal_id` (`check_company=True`) | Default yang disarankan Finance: `l10n_id_11210040` *Prepaid Expense* / `l10n_id_28110030` *Deferred Revenue* **[G]** — diisi lewat Settings, bukan data |
| `res.config.settings` | related ke tiga field company | Blok di Settings → Accounting |

**Perubahan wajib terhadap sumber (bukan sekadar rename):**
1. **Analitik ikut terbawa**: setiap baris deferral & recognition menyalin `analytic_distribution` baris asal (sumber tidak menyalin,
   `account_move.py` L105–162) — tanpa ini beban sewa per outlet hilang dari P&L outlet (G60-C).
2. Perlakuan **PPh 4(2)** pada sewa: hanya baris `display_type='product'` yang di-defer (sudah begitu, `_is_deferrable`); baris pajak PPh
   tetap di tagihan. Tambah test.
3. Pesan `UserError` diterjemahkan Indonesia; nama entri "Penangguhan …"/"Pengakuan … (bulan)".
4. `button_draft` tetap memanggil `button_draft` + `unlink` entri turunan dan **tidak** melewati lock date (perilaku sumber dipertahankan);
   tambahkan pesan jelas bila entri pengakuan bulan terkunci sudah posted.

**View & menu**
- Kolom opsional "Mulai/Selesai Tangguhan" di tab baris tagihan & faktur (inherit `account.view_move_form`), smart button "Entri
  Tangguhan" di form move.
- Menu **Accounting → Jurnal & Penutupan** (`parent="account.menu_finance_entries"`, seq 14) **[V]** "Entri Tangguhan",
  `groups="account.group_account_user"`.

**Grup/ACL:** tidak ada model baru; `ir.model.access.csv` header saja (sama dengan sumber).

**Cron:** tidak ada (memakai cron autopost CE).

**Data:** tidak ada di `data/`; `demo/` mengisi tiga akun company demo dari CoA `l10n_id`.

**Depends:** `account`, `efn_core`.

**Port vs drop**

| File sumber | Aksi |
|---|---|
| `models/account_move.py`, `account_move_line.py`, `res_company.py`, `res_config_settings.py` | **Port** + rename + perubahan 1–4 |
| `views/account_move_views.xml`, `deferred_views.xml`, `res_config_settings_views.xml` | **Port** (ganti xmlid `custom_` → `efn_`, parent menu sama) |
| `tests/test_deferred.py` (3 test) | **Port** + test baru: analitik terbawa, PPh 4(2) tidak di-defer, multi-company (akun company B tidak dipakai untuk move company A), lock date |

**RPC publik (masuk `RPC_ALLOWLIST`):**
- `("account.move", "action_efn_open_deferred_entries")` — rename dari `action_open_deferred_entries`; cek: `self.check_access('read')`,
  `ensure_one`, mengembalikan act_window domain `efn_deferred_origin_move_id = self.id` **tanpa sudo**.

**PII:** tidak ada field PII baru (partner disalin dari baris asal; nama partner perorangan terenkripsi `efn_pii_account` tetap berlaku).

**Ukuran:** ±230 baris Python, ±120 XML, ±220 test. **1,5 hari.**

---

## G59-C. Recurring — **tidak ada modul baru** (konfigurasi CE + menu)

| Kebutuhan | Standar CE 19 | Konfigurasi |
|---|---|---|
| Jurnal akrual bulanan tetap (mis. akrual listrik estimasi) | `account.move.auto_post='monthly'` + `auto_post_until` (L294–309) **[V]** | Buat entri, set Auto-post "Monthly" + "Auto-post until" |
| Tagihan sewa/langganan bulanan | idem pada `in_invoice` (copy via `_copy_recurring_entries`, L4818) **[V]** | Tagihan pertama diberi Auto-post Monthly; salinan berikutnya draft→posted otomatis pada tanggalnya |
| Pembayaran berulang | **Tidak** (lihat D-3) | Bayar dari tagihan berulang lewat Register Payment / batch payment G58 |

Tambahan kecil, **data-only di `efn_setup_menu`** (bukan modul baru): action `account.move` domain
`[('auto_post','in',('monthly','quarterly','yearly'))]` + menu "Entri Berulang" di `account.menu_finance_entries`, grup
`account.group_account_user`. ±20 baris XML, 0,25 hari. Tidak ada test Python (cukup test menu yang sudah ada di `efn_setup_menu`).

**Drop seluruh `custom_accounting_recurring`** (template jurnal, template pembayaran, sequence, cron). Alasan: duplikasi CE; template
pembayaran tanpa dokumen sumber melemahkan SoD.

---

## G60. `efn_account_localization` — voucher & persetujuan, MDR, analitik Outlet

Satu modul sesuai permintaan; tiga bagian independen. Bila engineer menilai lebih rapi dipecah, pemecahan yang direkomendasikan:
`efn_account_voucher` (A) + `efn_pos_outlet_accounting` (B+C). Spesifikasi di bawah berlaku untuk keduanya.

**Depends:** `account`, `analytic`, `point_of_sale`, `stock`, `purchase`, `efn_core`.
(`purchase` hanya untuk default outlet di PO — bila Q-G60-6 dijawab "tidak perlu", hapus dependency itu.)

### G60-A. Payment Voucher / Payment Receipt + persetujuan pembayaran keluar

**Fit-gap**

| Kebutuhan | Standar CE 19 | Gap | Status |
|---|---|---|---|
| Bukti pembayaran ke pelanggan | `account.report_payment_receipt` (CE, `_get_payment_receipt_report_values` L1107) **[V]** | Tidak menampilkan jurnal COA debit/kredit, terbilang, blok tanda tangan | **Custom (port)** |
| Voucher pengeluaran kas/bank untuk vendor (COA, terbilang, rekening tujuan, disiapkan/diperiksa/disetujui/diterima) | tidak ada | Port `report_payment_voucher` | **Custom (port)** |
| Persetujuan dua orang sebelum pembayaran keluar besar | `account.payment.state` hanya `draft/in_process/paid/canceled/rejected` (L36–42) **[V]**; tidak ada approval | 4-eyes | **Custom** |
| Rekening vendor terverifikasi sebelum bayar | `require_partner_bank_account` + `allow_out_payment` di `action_post` (L1130–1144) **[V]** | — | Standar (aktifkan per metode bayar bank) |

**Model & field**

| Model | Field | Aturan |
|---|---|---|
| `res.company` | `efn_payment_approval_threshold` (Monetary, default 0 = nonaktif) | per PT; nilai diisi Finance (Q-G60-1) |
| `res.config.settings` | related | Settings → Accounting |
| `account.payment` | `efn_approval_state` Selection `not_required`/`to_approve`/`approved`/`rejected`, default `not_required`, `copy=False`, `tracking=True` | |
| `account.payment` | `efn_approval_requested_by_id`, `efn_approved_by_id` (M2o `res.users`), `efn_approved_at` (Datetime), `efn_approval_note` (Char) | readonly di view, `copy=False` |

**Aturan gerbang** (`account.payment`, method privat `_efn_needs_approval()`): `payment_type == 'outbound'` **dan** `not pos_session_id`
**dan** `company_id.efn_payment_approval_threshold > 0` **dan** `abs(amount_company_currency_signed) >= threshold`
**dan** bukan transfer internal **[B]** (nama field transfer internal di 19: cek `account.payment` — `is_internal_transfer` ada/tidak).
- Override `action_post`: bila `_efn_needs_approval()` dan `efn_approval_state != 'approved'` → `UserError` "Pembayaran ≥ ambang perlu
  persetujuan". Payment POS (`pos_session_id`, dibuat `_create_combine_account_payment` L1160–1186 **[V]**) dan inbound tidak tersentuh.
- Wizard Register Payment: override `account.payment.register._post_payments(to_process, edit_mode)` (L1172–1185 **[V]**) — payment yang
  butuh persetujuan **tidak** di-post, ditandai `to_approve`, pesan ke chatter + notifikasi; sisanya di-post normal. **[B]** CE 19 menautkan
  payment draft ke tagihan (`matched_payment_ids`, L1216) sehingga rekonsiliasi terjadi saat payment di-post kemudian — buktikan di test.
- `action_efn_request_approval()`: dari `draft` → `to_approve`, isi `requested_by`.
- `action_efn_approve()`: grup `account.group_account_manager`, `check_access('write')`, company user memuat company payment,
  `env.user != efn_approval_requested_by_id` dan `!= create_uid` (pola `efn_loyalty_accounting` `action_approve`), state `to_approve` →
  `approved` lalu `action_post()`.
- `action_efn_reject()`: cek sama, → `rejected` + `action_reject()` CE (L1153) **[V]** atau tetap draft (Q-G60-2; default: tetap draft
  agar bisa diperbaiki).

**Laporan (port)**
- `ir.actions.report` `action_report_efn_payment_voucher` (outbound) dan `action_report_efn_payment_receipt` (inbound), `binding_model_id`
  = `account.model_account_payment`, **tanpa field `attachment`** (PDF tidak disimpan di filestore — filestore belum terenkripsi, lihat
  `efn_pii_account` README §Batasan).
- Voucher outbound yang `efn_approval_state='to_approve'` dicetak dengan tanda "BELUM DISETUJUI"; blok tanda tangan memakai `create_uid`
  (disiapkan), `efn_approval_requested_by_id` (diperiksa), `efn_approved_by_id` (disetujui), kosong (diterima).
- Rekening tujuan: `partner_bank_id.acc_number` **dimasking** `****1234` kecuali user `account.group_account_manager` (Q-G60-3). Data
  rekening terenkripsi `efn_pii_account`.
- Paperformat baru `paperformat_efn_voucher_landscape` (A4 landscape) — **jangan** salin nama "Levi's …".
- Logo = `company_id.logo` (logo PT EFN sendiri). Tidak ada aset/branding Levi's/Erajaya.

**Menu:** **Finance → Vendors** (`parent="account.menu_finance_payables"`, seq 25) **[V]** "Pembayaran Menunggu Persetujuan" (action
`account.payment` domain `efn_approval_state='to_approve'`), `groups="account.group_account_manager"`.

**Grup/ACL:** tidak ada model baru di bagian A. Tidak ada grup baru (maker = `account.group_account_invoice`/`group_account_user`,
approver = `account.group_account_manager`; selaras `efn_setup_role`).

**Port vs drop (sumber `custom_levis_localization`)**

| Sumber | Aksi |
|---|---|
| `models/terbilang.py` | **Port** apa adanya (fungsi murni), rename modul; tambah test unit |
| `models/account_payment.py` helper `_edo_line_source_doc`, `_edo_line_allocations`, `_edo_row_vals`, `_edo_voucher_rows`, `_edo_recipient_bank`, `_edo_source_bank_account`, `_edo_amount_in_words` | **Port** → `_efn_voucher_*` (privat) + masking rekening |
| `models/account_payment.py` `l10n_ou_analytic_id`, `l10n_override_outstanding_account_id`, `l10n_note`, `l10n_remark`, override `_compute_outstanding_account_id`, `_prepare_move_*_lines` | **Drop** (override akun outstanding = melemahkan kontrol; OU di baris B/S payment tidak dibutuhkan untuk P&L outlet) |
| `reports/payment_voucher_templates.xml`, `payment_receipt_templates.xml`, `payment_report_actions.xml`, `paperformat.xml` | **Port** + rename xmlid + hapus komentar/nama Levi's + blok approval |
| `reports/journal_billing_templates.xml` (#7 GL voucher tagihan vendor) | **Opsional P3** (Q-G60-4) |
| `models/account_payment_register.py` admin fee multi-COA (#8), `levis.payment.register.fee` | **Drop** (CE write-off satu baris cukup untuk biaya transfer; minta ulang bila Finance butuh) |
| `tests/test_payment_voucher_rows.py` | **Port** + test baru approval |

**Test:** baris voucher = jurnal payment (debit/kredit seimbang, dokumen AP rekonsiliasi), terbilang (0, 1.000.000, 1.234.567,89); gerbang:
outbound ≥ ambang ditolak `action_post`; penyetuju = pembuat ditolak; user tanpa `group_account_manager` ditolak; payment POS & inbound tidak
terpengaruh; wizard Register Payment meninggalkan payment `to_approve` draft lalu approve → tagihan `paid`/`in_payment` **[B]**; multi-company
(manager PT A tidak bisa approve payment PT B).

**RPC publik (masuk `RPC_ALLOWLIST`):**
- `("account.payment", "action_efn_request_approval")` — `check_access('write')` + company user + state `draft` + `_efn_needs_approval()`.
- `("account.payment", "action_efn_approve")` — `group_account_manager` + `check_access('write')` + company + state `to_approve` + 4-eyes
  (≠ peminta, ≠ pembuat).
- `("account.payment", "action_efn_reject")` — sama dengan approve.

**PII:** nomor rekening & nama pemilik rekening (terenkripsi) tampil di PDF → masking default; cetak voucher dicatat `efn.audit.rule`
**[B]** (cek apakah render `ir.actions.report` bisa diaudit `efn_audit`; bila tidak, catat sisa risiko).

### G60-B. MDR kartu/QRIS per metode bayar POS

**Fit-gap**

| Kebutuhan | Standar CE 19 | Gap | Status |
|---|---|---|---|
| Penjualan EDC/QRIS → piutang ke acquirer | Tutup sesi: per metode bank dibuat `account.payment` (Dr `outstanding_account_id` / Cr piutang POS) — `_create_account_move` L872–897 → `_create_bank_payment_moves` L1112–1138 → `_create_combine_account_payment` L1160–1186 / `_create_split_account_payments` L1231–1248 **[V]** | — | Standar |
| Settlement bank neto (bruto − MDR) | Rekonsiliasi mutasi vs outstanding; `account.reconcile.model.line` `amount_type` `percentage`/`percentage_st_line`/`regex` + `analytic.mixin` (`account_reconcile_model.py` L8–34) **[V]** → selisih ke akun MDR dengan analitik outlet | UI rekonsiliasi = G58 | Standar + G58 |
| Beban MDR tercatat **hari itu** per outlet & per jenis (debit/kredit/QRIS) sebelum mutasi bank diimpor | tidak ada | Estimasi saat tutup sesi | **Custom** |
| Tarif MDR berlaku per tanggal (perubahan kontrak bank/ketentuan BI QRIS) | tidak ada | `efn.mdr.rate` | **Custom** |
| BIN kartu → acquirer/tarif (`levis.mdr.bin`) | — | **Tidak diport** | Drop: kasir EFN memakai EDC berdiri sendiri (tidak terintegrasi, K25-09/ADR-032), input BIN manual menambah beban kasir, mati saat offline, dan memperluas jejak data kartu (prinsip "tidak menyimpan PAN"). Jenis kartu dibedakan dengan **metode bayar terpisah** (mis. "EDC BCA Debit", "EDC BCA Kredit", "QRIS") — Q-G60-5 |

**Model baru `efn.mdr.rate`** (`_check_company_auto = True`, `_order = 'payment_method_id, date_start desc'`)

| Field | Tipe | Aturan |
|---|---|---|
| `company_id` | M2o `res.company`, required, default `env.company`, index | record rule |
| `payment_method_id` | M2o `pos.payment.method`, required, `check_company=True`, domain `type = 'bank'` | |
| `name` | Char compute/store dari metode + tanggal | |
| `mdr_percent` | Float digits (16,4), 0–100 | PLACEHOLDER sampai kontrak bank (Q-G60-5) |
| `mdr_fixed` | Monetary per transaksi, ≥ 0 | |
| `account_id` | M2o `account.account`, required, `check_company=True`, domain beban | default disarankan `l10n_id_65110020` *Bank Administration Fees* **[G]** atau akun MDR khusus Finance |
| `date_start` (required), `date_end` | Date | constraint: tidak tumpang tindih per `payment_method_id` |
| `active` | Boolean | |

Metode privat: `_efn_rate_for(method, date)` (rate berlaku), `_efn_compute(amount, count)` = `round(amount × % / 100 + fixed × count)`
dengan presisi mata uang company.

**Field tambahan**
- `res.company.efn_mdr_journal_id` (M2o `account.journal` type `general`, `check_company`) — default jurnal umum pertama; diisi Settings.
- `pos.session.efn_mdr_move_ids` (O2m `account.move` via `account.move.efn_mdr_pos_session_id`, M2o index `btree_not_null`, `copy=False`).

**Hook (inti desain)** — override `pos.session._create_bank_payment_moves(self, data)`:
1. `data = super()._create_bank_payment_moves(data)` (payment per metode sudah dibuat & posted; outstanding = bruto).
2. Untuk setiap metode di `self.payment_method_ids.filtered(lambda m: m.type == 'bank')` yang punya rate berlaku pada
   `self.stop_at or today`: hitung `amount = sum(pos.payment.amount)` dan `count = jumlah pos.payment` untuk metode itu di
   `self.order_ids.payment_ids` (sumber langsung, bukan `data`, agar `split_transactions` dan combine sama).
3. Bila `amount_mdr != 0`: buat **satu** `account.move` (`move_type='entry'`, jurnal `efn_mdr_journal_id`, tanggal = tanggal move sesi,
   `ref = "MDR estimasi " + session.name + " " + metode`, `efn_mdr_pos_session_id = session`), baris: **Dr `rate.account_id`**
   (`analytic_distribution` = outlet, G60-C) / **Cr `payment_method.outstanding_account_id`**; `_post()`.
4. Konfigurasi tidak lengkap (rate ada tetapi akun/jurnal kosong) → **tidak memblok tutup sesi**: lewati + `message_post` di sesi +
   log WARNING tanpa data pribadi. Tutup sesi POS adalah jalur transaksi; MDR bukan.
5. Refund (amount negatif): MDR dihitung atas neto metode (default; Q-G60-7).

Konteks: `_validate_session` (L424–484) memanggil `_create_account_move` dengan `sudo()` untuk user POS **[V]** → hook berjalan sudo;
semua pencarian rate wajib difilter `company_id = session.company_id`.

**True-up saat settlement (konfigurasi, G58):** per outlet/MID satu `account.reconcile.model` (jurnal bank settlement) dengan baris
`account_id` = akun MDR yang sama, `amount_type='regex'` (ambil fee dari narasi bila bank mencantumkannya) atau `percentage` sisa selisih,
`analytic_distribution` = outlet. Total MDR = estimasi + true-up = potongan bank persis.

**Rekomendasi konfigurasi terkait (`efn_setup_pos`, bukan G60):** wizard G54 kini mengisi **satu** akun outstanding untuk semua EDC/QRIS
per PT (`_efn_fix_outstanding_accounts`, `efn_pos_setup_wizard.py` L262–287). Untuk rekonsiliasi per acquirer, Finance sebaiknya memberi
**akun kliring per acquirer/jenis** (mis. "Piutang Settlement BCA", "Piutang Settlement QRIS"). Desain MDR tetap benar dengan satu akun.

**Menu:** **Accounting → Configuration → Accounting** (`parent="account.account_account_menu"`, seq 20) **[V]** "Tarif MDR",
`groups="account.group_account_manager"`. Smart button "Jurnal MDR" di form `pos.session` (grup `account.group_account_readonly`).

**ACL `efn.mdr.rate`:** `account.group_account_readonly` r; `account.group_account_manager` rwcu. POS user tidak perlu ACL (hook sudo).
**Record rule:** `[('company_id', 'in', company_ids)]` (global, multi-company).

**Data:** `demo/` — rate PLACEHOLDER untuk metode EDC & QRIS manual demo (`efn_setup_pos` xmlid `pos_payment_method_edc_*`,
`pos_payment_method_qris_manual_*`). Tidak ada tarif di `data/`.

**Port vs drop:** `levis_mdr_bin.py` → **tidak diport sebagai BIN**; hanya ide `_compute_mdr` (persen + tetap) dan effective dating yang
dipakai ulang (ditulis ulang di `efn.mdr.rate`). `account_payment_register.py` MDR via Register Payment → **drop**.
`levis_pos_clearing*`, `levis_bank_mid_map*`, `levis_bank_narrative*`, `levis_clearing_config` → **domain G58**, tidak di G60.

**Test:** sesi dengan EDC Rp1.000.000 (3 transaksi), rate 1,5% + Rp500 → jurnal MDR Rp16.500, outstanding neto Rp983.500; rate tidak
berlaku (di luar tanggal) → tidak ada jurnal; konfigurasi kurang → sesi tetap tertutup + pesan; `split_transactions=True` sama hasilnya;
company B tidak memakai rate company A; analitik outlet ada di baris MDR; tumpang tindih tanggal ditolak.

**RPC publik:** tidak ada.

**PII:** tidak ada (tanpa BIN/PAN, tanpa data pelanggan).

### G60-C. Plan analitik "Outlet" → P&L per outlet

**Fit-gap**

| Kebutuhan | Standar CE 19 | Gap | Status |
|---|---|---|---|
| Dimensi outlet | `account.analytic.plan` + `account.analytic.account` (company opsional); hierarki plan **[V]** | — | Standar (data) |
| Analitik di jurnal tutup sesi POS | POS CE **tidak** menyentuh analitik sama sekali (grep `analytic` di `point_of_sale/*.py` = 0) **[V]**; distribution model CE hanya kunci produk/kategori/partner/prefiks akun/company (`analytic_distribution_model.py`) **[V]** → tidak bisa per outlet | Hook `pos.session` | **Custom** |
| Analitik di tagihan vendor manual | widget `analytic_distribution` per baris (standar) | — | Standar |
| Default outlet di PO → tagihan | `purchase.order.line._compute_analytic_distribution` (L369) memakai distribution model **[V]**; analitik PO mengalir ke tagihan | default dari gudang tujuan PO | **Custom kecil (opsional)** |
| Expense karyawan outlet | `hr.expense._compute_analytic_distribution` memakai distribution model dengan `partner_id`/`partner_category_id` = kontak kerja karyawan (L745–756) **[V]** | — | **Standar**: satu distribution model per outlet dengan *partner category* "Outlet X" + tag di kontak kerja karyawan |
| Laporan P&L per outlet (pivot) | **Accounting → Reporting → Management → Analytic Report** (`account.action_analytic_reporting`, `account.analytic.line`, filter default akun P&L) **[V]** | — | Standar |
| Laporan Laba Rugi resmi (format `efn_account_reports`) per outlet | `efn_account_reports` **tidak** punya filter analitik (query `_efn_lines` hanya `company_id`/tanggal, `efn_financial_report_engine.py` L122–160) | **Gap** | Lihat rekomendasi di bawah |

**Model & field**

| Model | Field | Aturan |
|---|---|---|
| `stock.warehouse` | `efn_analytic_account_id` M2o `account.analytic.account`, `check_company=True`, domain plan = plan Outlet | satu per outlet |
| `res.company` | `efn_outlet_analytic_plan_id` M2o `account.analytic.plan` (plan global; disimpan di company agar bisa beda per PT bila perlu) | default diisi wizard |
| TransientModel `efn.outlet.analytic.wizard` | `company_id` (required), baris pratinjau (gudang, akun analitik yang akan dibuat/ditautkan, status) | pola wizard G54 (`check` / `apply`, idempoten, per company) |

**Hook POS** (`pos.session`, method privat `_efn_outlet_analytic()` = `config_id.warehouse_id.efn_analytic_account_id`):
override dan merge (helper `_efn_merge_distribution(distribution, analytic_id)`, ditulis ulang dari `_levis_merge_ou_distribution`:
tambahkan id outlet ke setiap kunci distribusi; kosong → `{id: 100}`):
- `_get_sale_vals` (L1479), `_get_rounding_difference_vals` (L1420), `_get_stock_expense_vals` (L1528) — baris P&L **[V]**;
- `_post_statement_difference` (L486) — selisih kas (P&L) lewat statement line: **[B]** cara paling bersih menyuntik analitik ke baris
  lawan statement line (kemungkinan override `account.bank.statement.line` dengan konteks sesi); bila rumit, terima sebagai sisa gap;
- jurnal MDR (G60-B) memakai helper yang sama.
- **Default: hanya baris P&L** (bukan pajak/piutang seperti Levi's) agar analytic line tidak membengkak; B/S per outlet = Q-G60-8.
- Sesi tanpa outlet terpetakan → tidak dicap + peringatan di chatter sesi (tidak memblok tutup sesi).
- Batas: jurnal valuasi stok dari picking POS (bukan dari jurnal sesi) pada valuasi real-time **tidak** tercap — valuasi EFN saat ini
  periodik (G61), tetapi bila Q-ESB16 memilih real-time, perlu hook tambahan di `stock_account` **[B]**.

**Default outlet di PO (opsional, Q-G60-6):** override `purchase.order.line._compute_analytic_distribution`: setelah super, bila
`order_id.picking_type_id.warehouse_id.efn_analytic_account_id` ada dan distribusi belum memuat plan Outlet → merge. Hanya baris produk.

**Data**
- `data/analytic_plan_data.xml` (noupdate=1): plan "Outlet" (`efn_account_localization.analytic_plan_outlet`), `default_applicability`
  `optional`.
- `data/res_groups_data.xml`: aktifkan analitik — `base.group_user` `implied_ids` (4, `analytic.group_analytic_accounting`) (setara centang
  "Analytic Accounting" di Settings; tanpa itu menu analitik & widget tersembunyi). ⚠ ini mengubah tampilan untuk semua user internal —
  alternatif: imply dari `account.group_account_invoice` saja (Q-G60-9; default: `account.group_account_invoice`).
- Akun analitik per outlet dibuat **wizard** (bukan data), nama = nama gudang, kode = kode gudang, `company_id` = company gudang.
  Gudang DC/CK (bukan outlet) bisa dikecualikan di wizard (default: semua gudang yang dipakai `pos.config`).

**Menu:** wizard di **Accounting → Configuration → Analytic Accounting** (`parent="account.menu_analytic_accounting"`, seq 40) **[V]**
"Siapkan Analitik Outlet", `groups="account.group_account_manager"`.

**ACL:** `efn.outlet.analytic.wizard` (+ baris) → `account.group_account_manager` rwcu. Field `stock.warehouse.efn_analytic_account_id`
`groups="account.group_account_readonly"`.

**Port vs drop:** `pos_session.py` Levi's → **port** ide hook (ditulis ulang; nama field/helper `efn_`); `stock_warehouse.py`
`l10n_ou_analytic_id` → `efn_analytic_account_id`; `l10n_purchase_journal_id` (jurnal pembelian per toko) → **drop** (analitik cukup;
jurnal per toko memecah penomoran); `account_move_line.py` `l10n_ou_analytic_id` + `_compute_account_id` trade/non-trade → **drop**
(widget CE cukup; trade/non-trade Levi's-spesifik); `custom_accounting_full` branch analytic (`x_custom_branch_*`) → **drop** (CE 19 hierarki
ada di plan: sub-plan per brand/region bila perlu). `tests/test_trade_ou_split.py` → hanya bagian stamping POS yang dijadikan acuan.

**Test:** tutup sesi → baris penjualan, pembulatan, beban stok bercap outlet; baris pajak/piutang tidak (default); distribusi bawaan produk
(plan lain) tetap ada dan outlet ditambahkan, bukan ditimpa; dua config satu gudang → akun analitik sama; wizard idempoten (jalankan dua
kali, 0 duplikat), per company; PO ke gudang outlet → baris tagihan bercap (bila fitur PO aktif); Analytic Report menampilkan P&L per outlet.

**RPC publik (masuk `RPC_ALLOWLIST`):**
- `("efn.outlet.analytic.wizard", "action_check")` — `group_account_manager` + company wizard ∈ `env.user.company_ids`; tidak menulis.
- `("efn.outlet.analytic.wizard", "action_apply")` — sama + idempoten + savepoint per gudang (pola G54).

**PII:** tidak ada.

**Rekomendasi untuk `efn_account_reports` (gap, bukan bagian G60; tugas efn-odoo-engineer gelombang terpisah):** tambahkan filter opsional
`analytic_account_ids` pada laporan Laba Rugi. Sumber angka: `account.analytic.line` (kolom plan Outlet, `general_account_id`, `amount`) —
**bukan** JSON `account_move_line.analytic_distribution` (persentase multi-kunci, rawan salah agregasi). Neraca tidak difilter. Eliminasi
antar-PT tidak berlaku saat filter outlet aktif. Sampai itu dibangun, P&L per outlet = Analytic Report CE (pivot) + export XLSX.

**Ukuran G60:** A ±300 Py + ±400 XML (QWeb) + ±250 test; B ±200 Py + ±80 XML + ±200 test; C ±250 Py + ±120 XML + ±250 test.
**±6–7 hari** total.

---

## Ringkasan modul, urutan pasang, estimasi

| Urut | Modul | Depends | Model baru | RPC publik | Estimasi |
|---|---|---|---|---|---|
| 1 | `efn_account_deferred` | `account`, `efn_core` | — | 1 | 1,5 hari |
| 2 | `efn_tax_withholding` | `account`, `l10n_id`, `efn_core` | — | 0 | 1,5 hari |
| 3 | `efn_account_localization` | `account`, `analytic`, `point_of_sale`, `stock`, `purchase`, `efn_core` | `efn.mdr.rate`, `efn.outlet.analytic.wizard` (+line) | 5 | 6–7 hari |
| 4 | `efn_setup_menu` (tambahan data "Entri Berulang") | (tetap) | — | 0 | 0,25 hari |
| 5 | `efn_setup_audit` (tambahan `efn.audit.rule`: `account.tax` PPh, `efn.mdr.rate`, field approval `account.payment`, `res.company` ambang) | (tetap) | — | 0 | 0,25 hari |

Urutan pasang di efn-prod: setelah `efn_setup_pos` (metode EDC/QRIS ada) dan `efn_setup_menu` (menu induk Accounting). Modul G57/G58 boleh
dipasang sebelum atau sesudah; batch payment G58 setelah `efn_account_localization` (asumsi koordinasi 2).

**README tiap modul wajib memuat provenance (ADR-049):** modul asal, commit `odoo-platform` (snapshot origin/main yang dibaca — engineer
isi hash commit dari repo pemilik), daftar file diport/di-drop (tabel di atas), perubahan (rename `custom_`/`x_custom_`/`l10n_`/`levis` →
`efn_`, dependency `custom_core`→`efn_core`, `pdp.audited.mixin`→`efn.audit.rule`, analitik terbawa di deferral). Dilarang tersalin: nama
"Levi's"/EAL/Erajaya di paperformat, komentar, data bank 181 baris (`res.bank.csv` Levi's), kode akun Levi's (`1106000101..110`).

---

## Pertanyaan untuk klien (default dipakai sampai dijawab)

| ID | Pertanyaan | Default |
|---|---|---|
| Q-G59-1 | Daftar jenis PPh yang dipotong EFN + kode objek pajak e-Bupot + tarif (konsultan pajak) | PPh 23 jasa 2%, PPh 4(2) sewa 10%, PPh 26 20%; kode objek placeholder |
| Q-G59-2 | PPh dipotong saat tagihan (akrual) atau saat bayar? | Saat tagihan (pajak negatif CE, D-1) |
| Q-G59-3 | Perlu file impor e-Bupot (XML/XLSX format DJP) atau cukup daftar XLSX untuk input manual? | Daftar XLSX; XML ditunda |
| Q-G59-4 | Apakah Finance mengetik ulang nominal PPN/PPh di tagihan (agar sama dengan faktur)? | Tidak → fitur "pertahankan nominal saat reset draft" tidak diport |
| Q-G59-5 | Tarif ganda PPh 23 untuk vendor tanpa NPWP masih dipakai? | Ya, lewat fiscal position manual |
| Q-G60-1 | Ambang persetujuan pembayaran keluar per PT | 0 = nonaktif sampai diisi Finance |
| Q-G60-2 | Pembayaran ditolak: dibatalkan (`rejected`) atau kembali draft? | Kembali draft |
| Q-G60-3 | Nomor rekening vendor di voucher: lengkap atau dimasking? | Dimasking (`****1234`), lengkap untuk Accounting Manager |
| Q-G60-4 | Perlu cetakan "Journal Billing" (voucher GL tagihan vendor)? | Tidak (P3) |
| Q-G60-5 | Daftar acquirer & tarif MDR per jenis (debit, kredit, QRIS, per bank); metode EDC dipecah per jenis/bank? | Satu EDC + satu QRIS per PT, tarif placeholder |
| Q-G60-6 | Tagihan vendor/PO otomatis membawa outlet dari gudang tujuan PO? | Ya (opsional, bisa dimatikan dengan menghapus hook) |
| Q-G60-7 | MDR atas refund kartu dikembalikan bank? | MDR dihitung atas neto (refund mengurangi MDR) |
| Q-G60-8 | Perlu neraca per outlet (pajak/piutang bercap outlet) atau cukup laba-rugi? | Cukup laba-rugi |
| Q-G60-9 | Siapa yang melihat analitik: semua user internal atau hanya user akuntansi? | Hanya user akuntansi (`account.group_account_invoice`) |

## Belum terverifikasi
- Write field custom (`efn_bupot_*`) pada `account.move.line` posted tanpa memicu proteksi CE.
- Rekonsiliasi otomatis payment draft (`to_approve`) yang ditautkan `matched_payment_ids` saat di-post belakangan (wizard Register Payment).
- Nama field transfer internal di `account.payment` 19.0.
- Injeksi analitik ke baris selisih kas `_post_statement_difference`; cap analitik pada valuasi stok real-time picking POS.
- Apakah `efn_audit` menangkap export list dan render `ir.actions.report`.
- Kode objek pajak, tarif PPh/MDR, retensi dokumen pajak (konsultan pajak/Legal).
- Data seed `custom_tax_id/data/*` tidak ada di snapshot (tidak dibutuhkan untuk desain ini).
