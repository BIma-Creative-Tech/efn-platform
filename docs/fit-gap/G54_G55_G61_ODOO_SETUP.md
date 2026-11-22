# G54 / G55 / G61 — Kesiapan POS, menu Finance/Accounting, kategori produk (spesifikasi implementasi)

Penulis: efn-odoo-functional, 23 Nov 2026. Untuk: efn-odoo-engineer. Worktree `efn-platform-odoo`, branch `feat/odoo-completion`.
Sumber verifikasi: source Odoo CE 19 build 19.0-20260926 (subset lokal: account, point_of_sale, pos_hr, mrp, stock,
stock_account, product, l10n_id) dan GitHub `odoo/odoo@19.0` (base). Legenda: **[V]** terverifikasi di source (file:baris),
**[R]** rekomendasi, **[B]** belum terverifikasi (wajib diuji/diperiksa engineer).
ADR terkait: ADR-001 (satu DB multi-company), ADR-027 (PB1 configurable per kota), ADR-031 (tanpa modul pihak ketiga),
ADR-042 (migrasi ESB). Semua *Proposed*. Prinsip: konfigurasi CE dulu. Tidak ada referensi OCA/Cybrosys yang diperlukan:
ketiga butir tertutup oleh API standar CE. Hanya ada wizard/skrip kecil yang mengotomasi langkah UI.

Fakta efn-prod (dicek efn-lead 23 Nov): PB (`efn_setup_company.company_fnb`) punya 32 `pos.config` dari importer ESB
(`efn_legacy_history/esb_import/mapping.py:245`, `payment_method_ids=()`), semuanya **tanpa metode bayar**. Akibatnya
"Open Register" ditolak `_check_payment_method_ids`. PB hanya punya EDC (`efn_setup_pos.pos_payment_method_edc_fnb`, jurnal
bank BNK1) dan Poin EFN (`pos_payment_method_points_fnb`, PEFN). Stok PB: 0 move, 0 quant.

---

## G54 — Kesiapan POS: wizard "Lengkapi konfigurasi POS"

### 1. Prasyarat buka sesi Odoo 19 (urutan eksekusi CE)
Alur: `pos.config.open_ui()` [V `pos_config.py:818-838`]:
1. Tolak bila `uid == SUPERUSER_ID` (`__system__`). Admin biasa (uid 2) boleh.
2. Bila belum ada sesi, jalankan `_check_before_creating_new_session` [V :810-816].
3. `_validate_fields(self._fields)`, yaitu **semua constraint** config.
4. `_check_company_has_fiscal_country`.
5. `_action_to_open_ui` membuat `pos.session` [V :790-792]. `pos.session.create` → constraint `_check_pos_config`,
   `_check_start_date` [V `pos_session.py:311-330`]. `set_opening_control` memberi nama dari `ir.sequence` kode
   `pos.session` [V :1804-1808].

| # | Prasyarat (kode CE) | Sumber [V] | Cara memenuhi pada company ber-CoA `l10n_id` | Wizard |
|---|---|---|---|---|
| P01 | CoA terpasang: `company_has_template` = `root_id._existing_accounting()` atau `chart_template` | `pos_config.py:327-329`, `:522-526` | `chart_template='id'` (efn_setup_company `try_loading`) | cek saja; gagal = blocker manual |
| P02 | Fiscal country: `company.account_fiscal_country_id` | `:549-552`; template `l10n_id/models/template_id.py:23` (`base.id`) | otomatis dari template | cek; perbaiki = tulis `base.id` |
| P03 | Pricelist: bila `use_pricelist`, maka `pricelist_id ∈ available_pricelist_ids`, mata uang = config, company kosong/sama | `:483-487`, `:494-497`, `:508-520` | PB `use_pricelist=False` (default): **tidak wajib** | cek saja |
| P04 | Metode bayar milik company config | `:477-481`; `pos_payment_method.py:214-218` | semua metode dibuat `company_id` = company config | cek |
| P05 | Mata uang jurnal metode/invoice = `config.currency_id` (dari `journal_id`) | `:489-499` | jurnal tanpa `currency_id` (IDR company) | cek |
| P06 | Bila `cash_control` (= ada metode `is_cash_count`), jurnal kas wajib punya `loss_account_id` **dan** `profit_account_id` | `:471-475`, compute `:322-324` | jurnal kas baru otomatis diisi dari `company.default_cash_difference_expense/income_account_id` [V `account_journal.py:981-984`] = `l10n_id_99900001` / `l10n_id_99900002` [V template :27-28] | buat jurnal → otomatis; cek jurnal lama |
| P07 | Minimal satu metode bayar | `:501-506` | lihat §3 | perbaiki |
| P08 | Metode tunai hanya di **satu** POS | `pos_payment_method.py:220-228`; `pos_config.py:528-534` | satu metode tunai per config | perbaiki |
| P09 | Satu jurnal kas hanya untuk **satu** metode tunai | `pos_config.py:535-536`; domain `pos_payment_method.py:38` | satu jurnal kas per metode | perbaiki |
| P10 | `picking_type_id` wajib (`required=True`), domain `code='outgoing'` & gudang milik company | `pos_config.py:76-82` | importer: `wh.pos_type_id` per gudang (37 gudang) | cek; kosong = blocker |
| P11 | `journal_id` (jurnal sesi POS, tipe general/sale, `check_company`) | `:83-89`; default `_ensure_company_account_journal` (kode `POSS`) `account_journal.py:48-60` (pos) | dibuat CE saat config dibuat bila `env.company` benar | cek; kosong/salah company → `with_company(c)._ensure_company_account_journal()` |
| P12 | `invoice_journal_id` tipe sale, company sama | `:90-95`, `:35-39` | jurnal template `INV` | cek; kosong → jurnal `sale` pertama company |
| P13 | Sequence order per config (`order_seq_id`, `order_backend_seq_id`, `order_line_seq_id`, `device_seq_id`) | `:97-100`, `_create_sequences` `:572-604` dipanggil di `create` `:565` | ada bila config dibuat lewat ORM | cek; hilang **dan** 0 `pos.order` → `config.sudo()._create_sequences()`; ada order → blocker manual (menimpa = nomor ulang) |
| P14 | Sequence sesi global `point_of_sale.seq_pos_session` (kode `pos.session`, `company_id=False`) | `views/point_of_sale_sequence.xml:4-10`, `pos_session.py:1804-1806` | data CE | cek |
| P15 | Tidak ada sesi lain yang belum ditutup untuk config ini | `pos_session.py:311-319` | — | cek; config bersesi terbuka dilewati (payment_method_ids tidak bisa diubah, `pos_config.py:642-659`) |
| P16 | Tanggal mulai sesi tidak melanggar lock date | `pos_session.py:321-330` | lock date PB kosong | cek (`company._get_violated_lock_dates(today, True, journal)`) |
| P17 | Penutupan sesi (bukan pembukaan, tapi memblokir hari pertama): akun piutang POS `company.account_default_pos_receivable_account_id` | `pos_session.py:867`, `:927`, `:1460`, `:1746` | template `l10n_id_11210011` "Piutang Usaha (PoS)" [V template :29] | cek; kosong → `chart_template.ref('l10n_id_11210011')` |
| P18 | Penutupan: selisih kas → `cash_journal_id.loss/profit_account_id` | `pos_session.py:497-512` | = P06 | — |
| P19 | User pembuka: grup `point_of_sale.group_pos_user`, company POS ada di `company_ids`, bukan superuser | `pos_config.py:828-829` | user kasir per outlet (efn_setup_role) | cek (minimal 1 user non-superuser) → peringatan |

Tidak wajib di CE 19 [V]: pricelist (P03), `receipt_header`/`footer` (opsional; hanya admin yang boleh mengubah,
`_check_header_footer` `:545-547`), kategori POS (`limit_categories` opsional), `pos_hr` (opsional, lihat §6).

### 2. Model & perilaku wizard [R]
Modul **`efn_setup_pos`** (sudah ber-Python; versi naik ke 19.0.1.15.0). Tambah:
- `efn.pos.setup.wizard` (TransientModel), field:
  - `company_id` (M2o `res.company`, wajib, default `env.company`, domain `[('id','in',env.user.company_ids.ids)]`);
  - `config_ids` (M2m `pos.config`, domain company; kosong = semua `pos.config` aktif company);
  - `mode` Selection `check`/`apply` (default `check`);
  - opsi Boolean, semua default True: `opt_cash`, `opt_edc`, `opt_qris_manual`, `opt_company_accounts`,
    `opt_menu_taxes`;
  - `line_ids` (O2m ke `efn.pos.setup.wizard.line`).
- `efn.pos.setup.wizard.line` (TransientModel): `wizard_id`, `config_id`, `code` (P01..P19, C-xx), `status`
  Selection `ok`/`fixed`/`warning`/`blocked`, `message` (Char).
- Tombol "Lengkapi konfigurasi POS" di form/list `pos.config` (Configuration → Point of Sale) dan menu
  `point_of_sale.menu_point_config_product`.
- **Hak:** cek = `point_of_sale.group_pos_manager`. Apply = `group_pos_manager` **dan** `account.group_account_manager`
  (membuat jurnal/akun), dicek di server (`has_group`), lalu operasi memakai `sudo().with_company(company)`. Semua
  aksi apply dicatat di chatter config (`message_post`) dan `_logger.info` (tanpa PII).
- **Idempoten:** setiap langkah dicari dulu, baru dibuat. Menjalankan dua kali tidak membuat apa pun yang baru.
  Setiap config diproses di `with self.env.cr.savepoint()`; gagal = baris `blocked` + pesan, config lain lanjut.
- **Mode check** tidak menulis apa pun. Untuk mendeteksi P04–P10 tanpa menulis, mode ini menjalankan
  `config._validate_fields(config._fields)` + `config._check_before_creating_new_session()` dalam savepoint yang di-
  rollback (`try/except (UserError, ValidationError)`).
- **Multi-company:** semua pencarian pakai `company_id = wizard.company_id` + `with_company`. SMD tetap bisa memakai
  wizard (generik), tetapi data baru di §4 **tidak** dibuat untuk SMD (aturan independensi supermarket).

### 3. Langkah apply per config (urutan)
| Kode | Langkah | Detail persis |
|---|---|---|
| C-01 | Akun company (`opt_company_accounts`) | Hanya bila kosong: `account_default_pos_receivable_account_id` ← `ref('l10n_id_11210011')`; `default_cash_difference_income_account_id` ← `ref('l10n_id_99900002')`; `default_cash_difference_expense_account_id` ← `ref('l10n_id_99900001')`. `ref` = `env['account.chart.template'].with_company(company).ref(xmlid, raise_if_not_found=False)` (pola CE `pos_config.py:1064-1065`, konteks `allowed_company_ids=company.root_id.ids`). Bila ref tidak ada (CoA lain), hasilnya `blocked` |
| C-02 | Jurnal sesi & invoice | P11/P12 (lihat tabel) |
| C-03 | Sequence | P13 |
| C-04 | **Tunai per config** (`opt_cash`) | Lewati bila config sudah punya metode `is_cash_count` yang hanya ditautkan ke config ini. Bila belum: (a) cari jurnal `type='cash'`, company, `name = "Kas <config.name>"`, tanpa `pos_payment_method_ids`; (b) bila tidak ada, buat `account.journal` `{name: "Kas <config.name>", code: <kode §3.1>, type: 'cash', company_id}`. **Jangan** isi `default_account_id`: CE membuat akun kas per jurnal berprefiks `1111` (`cash_account_code_prefix` l10n_id), loss/profit otomatis [V `account_journal.py:970-984`]; (c) buat `pos.payment.method` `{name: "Tunai <config.name>", journal_id, company_id, payment_method_type: 'none'}`; (d) `config.write({'payment_method_ids': [Command.link(m.id)]})` |
| C-05 | **EDC** (`opt_edc`) | Metode company = record ber-xmlid `efn_setup_pos.pos_payment_method_edc_<sfx>` (cari via `ir.model.data`, `module='efn_setup_pos'`, `name =like 'pos_payment_method_edc_%'`, `res_id` metode aktif company). Link bila belum. Tidak ada → `warning` |
| C-06 | **QRIS manual** (`opt_qris_manual`) | Sama dengan C-05, xmlid `efn_setup_pos.pos_payment_method_qris_manual_<sfx>` (data baru §4) |
| C-07 | `outstanding_account_id` metode bank kosong (EDC, QRIS) | ← `ref('account_journal_payment_debit_account_id')` atau `company.transfer_account_id` (pola onchange CE `pos_payment_method.py:128-130`). Tidak berlaku untuk Poin EFN (sudah diisi akun liabilitas) |
| C-08 | **Poin EFN** | Hanya bila `config.efn_loyalty_redeem_enabled` (default False, `efn_pos_loyalty_ledger/models/pos_config.py:77`): panggil `config._efn_setup_link_points_method()` (sudah ada, `efn_setup_pos/models/pos_config_points.py:38`). Wizard tidak pernah menautkan Poin EFN ke config tanpa redeem |
| C-09 | Pajak menu (`opt_menu_taxes`, per company, sekali) | Produk `company_id=company`, `sale_ok=True`, `available_in_pos=True`, tanpa pajak penjualan milik company di `taxes_id` → `taxes_id = [Command.link(company.account_sale_tax_id.id)]`. PB: `account_sale_tax_id` = `efn_setup_company.tax_pb1_10_fnb` (PB1 default 10 %, **PLACEHOLDER**, Q-22; `account_tax_pb1_data.xml:34-53`). PT ritel (GL/BCR) = PPN template `tax_ST4`. **Jangan** pasang SC atau PB1 dine-in di produk: keduanya ditambahkan lewat fiscal position preset (ADR-027, `pos_fnb_service_presets.xml`) |
| C-10 | Validasi akhir | jalankan cek P01–P19 lagi; status final per config |

Catatan C-09: importer menu (`mapping.py:309-329`) tidak mengisi `taxes_id`. Nilainya bergantung `env.company` saat
impor [B: periksa `SELECT count(*)` produk menu PB tanpa pajak sebelum apply].

#### 3.1 Skema kode & nama jurnal kas
- `account.journal.code`: `size=5` [V `account_journal.py:97-99`], unik per company [V `:293-296`].
- Aturan [R]: `prefix = efn_brand_code + "C"`, lalu nomor urut mengisi sisa panjang (5 − len(prefix)):
  - kode merek 2 huruf (GL, PB): `PBC01`..`PBC99`;
  - kode merek 3 huruf (BCR, BCF, WTZ, IMD, CUP, SMD): `BCRC1`..`BCRC9`, lalu `BCRCA`..`BCRCZ` (35 outlet);
  - kode merek ≥ 4 huruf (FARM, EFN-holding tidak ber-POS): `blocked`, isi manual.
- Nomor = nomor bebas terkecil (lewati kode yang sudah dipakai jurnal apa pun di company, mis. `CSH1` template, `PEFN`,
  `SMC1..5`). Identitas idempoten = tautan metode↔config, **bukan** kode.
- Nama: jurnal `Kas <config.name>`, metode `Tunai <config.name>`. Contoh PB: 32 jurnal `PBC01..PBC32` dan 32 akun kas
  `1111xxxx`. Akun kas per outlet dipilih agar rekonsiliasi setoran per outlet bisa dilakukan. Alternatif satu akun
  bersama = keputusan Finance (Q-G54-2).

### 4. Data baru `efn_setup_pos` (noupdate="1", pola file yang sudah ada)
Record noupdate dengan xmlid baru **dibuat saat `-u`** di DB lama (xmlid belum ada). Jadi efn-prod mendapatkannya tanpa migrasi.
| File | Record | Isi |
|---|---|---|
| `data/pos_payment_method_data.xml` | `pos_payment_method_edc_imd`, `pos_payment_method_edc_cup` | pola `_wtz` (`:54-60`): `payment_method_type=none`, jurnal bank pertama company (`search`) |
| `data/pos_loyalty_points_payment.xml` | `account_points_liability_imd/_cup`, `journal_points_imd/_cup`, `pos_payment_method_points_imd/_cup` | pola `_fnb` (`:43-64`), akun 21990091 **PLACEHOLDER Q-F18-3**. `_efn_setup_points_journal_accounts` generik (mencari semua metode `efn_is_loyalty_points`) [V `models/pos_payment_method_points.py:23-35`], jadi tidak perlu diubah |
| **baru** `data/pos_payment_method_qris_manual_data.xml` | per company GL/PB/BCR/BCF/WTZ/IMD/CUP: `journal_qris_manual_<sfx>` (`account.journal`, `name` "QRIS (manual)", `code` "QRIS", `type` bank) + `pos_payment_method_qris_manual_<sfx>` (`name` "QRIS (manual, cek mutasi)", `payment_method_type` **none**, `split_transactions` False) | **Bukan** `payment_method_type='qr_code'`: tipe itu mewajibkan `journal_id.bank_account_id` + `qr_code_method` [V `pos_payment_method.py:202-212`], sedangkan QRIS `id_qr` l10n_id_pos ditolak (secret di `res.partner.bank`, README `efn_setup_pos`). Kasir memverifikasi mutasi/notifikasi merchant. QRIS dinamis tetap lewat `efn_pos_payment_gateway` (Q-23/Q-29) |
| `data/pos_fnb_service_presets.xml` | `preset_imd_takeaway`, `preset_cup_takeaway` (dan dine-in bila outlet punya meja) | pola `_wtz` (`:55-59`) dengan FP `efn_setup_company.fiscal_position_imd/cup_non_dine_in` |

### 5. Pajak PB1 & fiscal position (ringkas)
- PB1 = pajak **daerah** (PBJT, UU HKPD 1/2022), per company F&B: grup `tax_group_pb1_<sfx>`, pajak default
  `tax_pb1_10_<sfx>` (10 % PLACEHOLDER) = `account_sale_tax_id` company. PPN (pusat, l10n_id) **tidak** dipasang di menu
  PT F&B. Tarif per kota memakai pajak "Replaces" + fiscal position kota (ADR-027).
- Dine-in: preset `efn_setup_pos.preset_fnb_dine_in` (FP `fiscal_position_fnb_dine_in` = PB1 dine-in + SC 5 %). Bawa
  pulang: `preset_fnb_takeaway` (FP non-dine-in, PB1 0 % keputusan 18-1). Wizard **memeriksa** (`warning`, tidak
  memperbaiki) bahwa config F&B memakai `use_presets=True`, `default_preset_id`, dan `default_fiscal_position_id` = FP
  dine-in. Pemilihan preset per outlet adalah keputusan Q-18.
- GO-LIVE blocker yang sudah tercatat: baris repartisi PB1/SC tanpa akun liabilitas (`account_tax_pb1_data.xml:24-26`).
  Wizard `warning` bila `tax_pb1_10_<sfx>.invoice_repartition_line_ids` (tipe `tax`) tidak berakun.

### 6. Hal lain yang memblokir atau merusak penjualan pertama
| Hal | Dampak | Tindakan |
|---|---|---|
| `pos_hr`: setiap `pos.config.write` menambahkan karyawan milik user POS Manager ke `advanced_employee_ids`, dan **membuat `hr.employee`** untuk user manager pertama yang belum punya karyawan di company itu [V `pos_hr/models/pos_config.py:21-34`] | wizard apply bisa membuat 1 karyawan (mis. untuk admin) di PB | dokumentasikan di README; cek setelah apply |
| `module_pos_hr=True` tanpa karyawan aktif di company | layar login karyawan kosong [B] | cek `warning` (hitung `hr.employee` company) |
| Produk menu `categ_id` NULL (1.680) | tidak memblokir POS; menjurnal ke akun pendapatan company (`income_account_id` = `l10n_id_41000010`) | G61 |
| `pos.category` (85) dari ESB | tampil di POS; tidak wajib | tidak ada |
| `receipt_header` (nama PT, NPWP, alamat) | struk tanpa identitas PT (Q-1) | isi manual oleh admin; wizard `warning` bila kosong |
| `point_of_sale.limited_product_count` = 20000 (efn_setup_pos) | 642 menu PB jauh di bawah batas | tidak ada |
| Produk storable dijual tanpa stok | quant negatif (diizinkan CE) | tidak memblokir; stok awal = G43 |
| Menu non-storable dengan BoM Kit (`pos_mrp`) | komponen storable dikurangi | sesuai ADR-018 |

### 7. Test wajib (engineer)
`efn_setup_pos/tests/test_g54_pos_setup_wizard.py` (tag `efn`): company uji dengan CoA `id`, 2 config tanpa metode.
- check → `blocked` P07;
- apply → setiap config punya 1 metode tunai eksklusif + EDC + QRIS, jurnal kas punya loss/profit;
- `open_ui()` sebagai user POS non-superuser berhasil membuat sesi;
- apply kedua → tidak ada record baru (hitung jurnal/metode);
- config bersesi terbuka dilewati;
- user tanpa `account.group_account_manager` ditolak saat apply;
- Poin EFN hanya pada config `efn_loyalty_redeem_enabled`.

---

## G55 — Menu "Finance" dan "Accounting" terpisah; "Manufacturing" → "Produksi"

### 1. Menu akun CE 19 (semua di `account/views/account_menuitem.xml` kecuali disebut) [V]
| xmlid | Label CE | Induk | Grup CE | Seq | Tujuan |
|---|---|---|---|---|---|
| `account.menu_finance` | Invoicing | (root) | `group_account_readonly`, `group_account_invoice`; `web_icon="account,static/description/icon.png"` | 55 | **root Finance** (ganti label) |
| `account.menu_board_journal_1` | Dashboard | menu_finance | `group_account_basic` | 1 | Finance |
| `account.menu_finance_receivables` | Customers (Invoices, Credit Notes, Payments, Products, Customers) | menu_finance | — | 2 | Finance |
| `account.menu_finance_payables` | Vendors (Bills, Refunds, Payments, Products, Vendors) | menu_finance | — | 3 | Finance |
| `account.menu_finance_entries` | Accounting | menu_finance | `group_account_readonly` | 4 | **→ Accounting**, label "Jurnal & Penutupan" |
| ↳ `account.account_transactions_menu` (Journal Items `menu_action_move_journal_line_form`, Analytic Items `menu_action_analytic_lines_tree`) | Transactions | menu_finance_entries | readonly | 0 | ikut induk |
| ↳ `account.account_closing_menu` (+ `account_secure_entries_wizard.xml:46`) | Closing | menu_finance_entries | readonly | 20 | ikut induk |
| `account.account_audit_menu` (Control → `menu_action_account_moves_all`; Logs → `account_audit_trail_menu` `mail_message_views.xml:75`) | Review | menu_finance | readonly | 7 | **→ Accounting** |
| `account.menu_finance_reports` | Reporting | menu_finance | readonly, invoice | 20 | **→ Accounting** |
| ↳ `account_reports_partners_reports_menu`, `account_reports_taxes_and_fiscal_menu`, `account_reports_legal_statements_menu` (readonly, basic) | folder | menu_finance_reports | — | 3/4/1 | ikut induk |
| ↳ `account.account_reports_management_menu` (Invoice Analysis, Analytic Report; **`efn_account_reports.menu_efn_financial_report`**, **`efn_account_reports.menu_efn_consolidation_adjustment`**) | Management | menu_finance_reports | — | 5 | ikut induk |
| `account.menu_finance_configuration` (Settings, Accounting [Chart, Taxes, Journals, …, `efn_intercompany` transfer price], Invoicing [Payment Terms, Incoterms, Product Categories], Online Payments, Analytic) | Configuration | menu_finance | `group_account_manager` | 35 | **→ Accounting** |

Menu efn yang ikut pindah otomatis karena induknya pindah, **tanpa** dependency ke modulnya [V grep repo]:
- `efn_account_reports`: kedua menu di bawah `account.account_reports_management_menu`;
- `efn_intercompany`: menu di bawah `account.account_account_menu`.

Keputusan: `efn_setup_menu` **tidak** depend ke `efn_account_reports`.

Menu modul lain yang terpasang di efn-prod tetapi tidak ada di subset lokal (mis. `l10n_id_efaktur_coretax`,
`account_payment`): ikut induknya [B]. Engineer wajib menjalankan kueri ini di efn-prod sebelum/sesudah:
```sql
SELECT m.id, d.module||'.'||d.name AS xmlid, m.parent_path, m.name->>'en_US'
FROM ir_ui_menu m LEFT JOIN ir_model_data d ON d.model='ir.ui.menu' AND d.res_id=m.id
WHERE m.parent_path LIKE (SELECT id::text||'/%' FROM ir_ui_menu WHERE id=(SELECT res_id FROM ir_model_data
      WHERE module='account' AND name='menu_finance'))
ORDER BY m.parent_path;
```

### 2. Struktur target [R]
**Finance** = `account.menu_finance` (record yang sama, label diganti; grup & ikon CE dipertahankan):
- Dashboard;
- Customers;
- Vendors;
- **Kas & Bank** (baru, `efn_setup_menu.menu_efn_cash_bank`, seq 4, grup `account.group_account_invoice`) berisi:
  - "Rekening Koran Bank" → `account.action_bank_statement_tree` [V `account_bank_statement_views.xml:50`];
  - "Register Kas" → `account.action_view_bank_statement_tree` [V :123];
  - "Transfer Internal" → `account.action_account_payments_transfer` [V `account_payment_view.xml:385`; isi domain belum dibaca, B].
- Folder masa depan (petty cash, batch payment, import/rekonsiliasi bank) ditaruh di bawah Finance oleh modul
  `efn_*` terkait.

**Accounting** = root baru `efn_setup_menu.menu_efn_accounting_root`:
| Field `ir.ui.menu` (19.0: `name`, `parent_id`, `sequence`, `group_ids`, `web_icon`, `action`) [V GitHub `base/models/ir_ui_menu.py`] | Nilai |
|---|---|
| `name` | "Accounting" (id_ID juga "Accounting", keputusan user) |
| `parent_id` | kosong (root) |
| `sequence` | 56 (tepat setelah Finance 55) |
| `group_ids` | `account.group_account_readonly`, `account.group_account_manager`. Alasan: `group_account_manager` hanya meng-*imply* `group_account_invoice` [V `account_security.xml:73-79`]. Tanpa grup manager di root, admin/manager tanpa `group_account_user` tidak melihat root. Peran efn: finance_staff = `group_account_user` (⇒ readonly), director/auditor = readonly (`efn_setup_role` test) |
| `web_icon` | `account,static/description/icon.png` (ada di 19.0 [V GitHub listing]). Ikon terpisah = file sendiri `efn_setup_menu/static/description/icon_accounting.png` (opsional, Q-G55-1) |
| anak (via `parent_id`) | `account.menu_finance_entries` (seq 1), `account.account_audit_menu` (seq 5), `account.menu_finance_reports` (seq 10), `account.menu_finance_configuration` (seq 90) |

Konsekuensi yang perlu diketahui user:
- "Invoice Analysis" (di Reporting → Management) pindah ke Accounting, jadi user Billing murni (`group_account_invoice`
  tanpa readonly) kehilangan akses menunya (Q-G55-2).
- Configuration hanya untuk `group_account_manager` (seperti CE).

### 3. Mekanisme & ketahanan upgrade [V/R]
- Menu CE dideklarasikan dengan `<menuitem>` di file **noupdate=0**. Setiap `-u account` / `-u mrp`, `parent_id`,
  `name` (en_US), dan `sequence` di-reset ke nilai CE.
- `button_upgrade` 19.0 ikut meng-upgrade **semua modul terpasang yang bergantung** pada modul yang di-upgrade, secara
  rekursif [V GitHub `base/models/ir_module.py` `button_upgrade`]. Karena `efn_setup_menu` depend ke `account` & `mrp`,
  modul ini dimuat ulang **sesudah** keduanya dan menerapkan perubahannya lagi.
- Terjemahan dimuat per modul **setelah** data modul itu (`module._update_translations(overwrite=config
  overwrite_existing_translations)`) [V GitHub `odoo/modules/loading.py`]. Urutannya: data `mrp` → terjemahan `mrp`
  (id: "Manufacturing" → terjemahan CE) → data `efn_setup_menu`. Jadi label yang ditulis `efn_setup_menu` menang.
- `ir.ui.menu.name` `translate=True` (JSONB per bahasa). Record XML hanya menulis en_US, sehingga id_ID tetap
  menampilkan terjemahan CE. **Wajib** menulis terjemahan untuk semua bahasa aktif.
- `update_field_translations(field_name, translations)` publik di 19.0, tetapi menolak bahasa yang belum aktif
  (`UserError "The following languages are not activated"`) [V GitHub `odoo/orm/models.py`]. Karena itu `<function>` XML
  dengan dict tetap `{'id_ID': …}` akan gagal di DB tanpa id_ID. Perlu **hook kecil** yang memfilter bahasa terpasang.

Implementasi [R] — modul **`efn_setup_menu`** (19.0.1.0.0, LGPL-3, `depends: ["account", "mrp"]`):
- `data/menu_data.xml` (**noupdate="0"**, harus diterapkan ulang setiap upgrade):
  - `<record id="account.menu_finance_entries" model="ir.ui.menu"><field name="parent_id" ref="menu_efn_accounting_root"/><field name="sequence">1</field></record>`;
  - record serupa untuk `account.account_audit_menu`, `account.menu_finance_reports`, `account.menu_finance_configuration`;
  - root `menu_efn_accounting_root` dan `menu_efn_cash_bank` dengan `<menuitem>` biasa;
  - di akhir file: `<function model="ir.ui.menu" name="_efn_apply_menu_labels"/>`. Pola memanggil method privat dari
    `<function>` sudah dipakai repo ini: `efn_setup_pos/data/pos_loyalty_points_link.xml` →
    `pos.payment.method._efn_setup_points_journal_accounts`.
- `models/ir_ui_menu.py` (satu-satunya Python):
  - konstanta `EFN_MENU_LABELS = {"account.menu_finance": "Finance", "mrp.menu_mrp_root": "Produksi", "account.menu_finance_entries": "Jurnal & Penutupan", "efn_setup_menu.menu_efn_accounting_root": "Accounting"}`;
  - `@api.model _efn_apply_menu_labels()`: untuk setiap xmlid, `self.env.ref(x, raise_if_not_found=False)`, lalu
    `menu.update_field_translations('name', {code: label for code, _n in self.env['res.lang'].get_installed()} | {'en_US': label})`.
- Tidak perlu `post_init_hook`: `<function>` juga berjalan saat install.
- README: setelah **mengaktifkan bahasa baru**, jalankan `-u efn_setup_menu`. Pemasangan bahasa memuat terjemahan CE
  ("Manufaktur"/"Faktur") ke kunci bahasa yang masih kosong [B].
- Test `tests/test_menu_labels.py`:
  - label en_US & id_ID (bila terpasang) = "Finance"/"Accounting"/"Produksi";
  - `parent_id` keempat menu = root Accounting;
  - menu efn_account_reports berada di bawah root Accounting bila modulnya terpasang (skip bila tidak);
  - user `group_account_invoice` saja tidak melihat root Accounting (`_visible_menu_ids`).
- Uji manual di runbook: `-u mrp` lalu `-u account`, label & induk tetap. Ini tidak bisa diuji di TransactionCase.

Alternatif yang ditolak:
- (a) record XML `name` saja: id_ID tetap "Manufaktur";
- (b) file `i18n/id.po` di efn_setup_menu: bergantung `overwrite_existing_translations` dan tidak menangani bahasa lain;
- (c) root Finance baru + menu_finance dikosongkan: menu modul lain yang menempel ke `menu_finance` tidak ikut pindah
  dan root kosong tetap ada.

Root `mrp.menu_mrp_root` [V `mrp/views/mrp_views_menus.xml:3-7`]: label "Manufacturing", grup
`mrp.group_mrp_user,group_mrp_manager`, ikon `mrp,static/description/icon.png`, seq 145. Hanya label yang diganti. File
`mrp/i18n/id.po` tidak ada di subset lokal; teks terjemahan CE-nya [B].

---

## G61 — Restrukturisasi kategori produk per peran akuntansi

### 1. Fakta CE 19 [V]
- `product.category` **tidak** punya `company_id`: satu pohon untuk semua company. Properti akuntansi bersifat
  `company_dependent` (nilai per company):
  - `property_account_income_categ_id`, `property_account_expense_categ_id` (`account/models/product.py:17-24`);
  - `property_valuation` (`periodic`/`real_time`), `property_cost_method` (`standard`/`fifo`/`average`),
    `property_stock_journal`, `property_stock_valuation_account_id`, `property_price_difference_account_id`
    (`stock_account/models/product.py:731-766`);
  - `account_stock_variation_id` = related ke akun valuasi.
  - **Odoo 19 tidak lagi punya** `property_stock_account_input/output_categ_id`.
- Fallback company: `inventory_valuation` (default `periodic`), `cost_method` (default `standard`)
  (`stock_account/models/res_company.py:29-47`); `income_account_id` / `expense_account_id` / `account_stock_valuation_id`
  dari template `l10n_id` (`template_id.py:36-39`).
- **Aman mengubah `type`/`is_storable` saat 0 move & 0 quant:**
  - `product.template.write` stock hanya bereaksi saat `is_storable` False→True (`_clean_reservations` + `_reset_inventory`
    dari move done; tanpa move = tanpa efek) (`stock/models/product.py:1149-1159`);
  - `type != 'consu'` memaksa `is_storable=False` (compute `:911-913`);
  - tidak ada constraint tipe di point_of_sale/mrp/product;
  - komponen BoM boleh `service` (`mrp_bom.py:684`, di-skip saat explode `:393`, `stock_move.py:598`).
- Mengubah `categ_id`/`property_cost_method` memanggil `_update_standard_price` untuk produk yang metode biayanya
  berubah (`stock_account/models/product.py:81-89`, `:775-787`):
  - `standard` → tidak berubah;
  - `average` → `_run_average_batch(force_recompute=True)`; tanpa move diperkirakan tidak mengubah `standard_price` [B].
  - **Mitigasi:** skrip menyimpan `standard_price` per produk sebelum apply, membandingkan sesudahnya, dan menulis ulang
    bila berubah (dengan `disable_auto_revaluation=True`).
- Archive/unlink produk POS ditolak saat ada sesi terbuka (`point_of_sale/models/product_template.py:304-323`).
  Skrip tidak mengarsipkan apa pun, tetapi tetap **wajib dijalankan tanpa sesi POS terbuka**.

### 2. Pohon target (`complete_name`) [R]
Pohon bersama, tanpa prefiks merek, karena properti akuntansinya per company. Pohon yang sama dipakai IMD/CUP saat
migrasi G43 (nama kategori ESB sama). **SMD tidak disentuh** (tetap `Supermarket / …`, aturan independensi).
| Peran | Path | Flag produk (`type`, `is_storable`, `sale_ok`, `purchase_ok`, `available_in_pos`) |
|---|---|---|
| Persediaan | `Persediaan / Bahan Baku / Impor` · `/ Lokal - Dry` · `/ Lokal - Fresh` · `/ Lokal - Frozen` · `/ Lokal - Chill` · `/ Food & Beverage` · `/ Bahan Penolong` | consu, True, False, True, False |
| Persediaan | `Persediaan / Kemasan` | consu, True, False, True, False |
| Persediaan | `Persediaan / WIP` | consu, True, False, **aturan data** (lihat A2), False |
| Persediaan | `Persediaan / Barang Jadi` | consu, True, False, **aturan data**, False |
| Persediaan | `Persediaan / Merchandise` | consu, True, True, True, nilai sekarang |
| Perlengkapan | `Perlengkapan / Operasional Toko` · `/ Produksi` · `/ Kebersihan & Keselamatan` · `/ Kemasan Habis Pakai` · `/ Peralatan Makan & Dapur` · `/ Seragam` · `/ Suku Cadang` · `/ Pemasaran` · `/ Kantor & ATK` | consu, **False**, False, True, False |
| Beban / Jasa | `Beban / Jasa / Pemeliharaan & Instalasi` · `/ Utilitas` · `/ Pengiriman` · `/ Biaya Impor` · `/ Pemasaran & Cetak` · `/ Perizinan & Sertifikasi` · `/ Sewa` · `/ Lain-lain` | **service**, False, False, True, False |
| Aset Tetap | `Aset Tetap / Bangunan & Renovasi` · `/ Mesin` · `/ Peralatan Kantor & Outlet` · `/ Furnitur & Perlengkapan Tetap` · `/ Perangkat Lunak` · `/ LVA` | consu, **False**, False, True, False |
| Menu | `Menu / <nama pos.category akar>` · `Menu / Lainnya` | consu (combo tetap combo), False, True, **False**, nilai sekarang (642 True dipertahankan) |

Kategori induk (`Persediaan`, `Persediaan / Bahan Baku`, `Perlengkapan`, `Beban`, `Beban / Jasa`, `Aset Tetap`,
`Menu`) tidak berisi produk, tetapi tetap membawa properti default peran (anak tidak mewarisi properti company-dependent
dari induk di CE; **setiap** kategori diisi sendiri oleh skrip).

### 3. Pemetaan 52 kategori ESB PB → target
| Kategori ESB | Jml | Target | Catatan |
|---|---|---|---|
| Inventory - Food & Beverage | 737 | Persediaan / Bahan Baku / Food & Beverage | A1 |
| Inventory - WIP | 661 | Persediaan / WIP | A2: digabung dengan "WIP" |
| FA LVA | 423 | Aset Tetap / LVA | A6 |
| Packaging Material | 241 | Persediaan / Kemasan | A3 |
| FA Office & Outlet Equipment | 209 | Aset Tetap / Peralatan Kantor & Outlet | |
| FA Furniture & Fixture | 183 | Aset Tetap / Furnitur & Perlengkapan Tetap | |
| Local - Dry | 158 | Persediaan / Bahan Baku / Lokal - Dry | |
| Supplies | 105 | Perlengkapan / Operasional Toko | A4 |
| Import Ingredient | 102 | Persediaan / Bahan Baku / Impor | |
| WIP | 83 | Persediaan / WIP | A2 |
| Indirect Material | 79 | Persediaan / Bahan Baku / Bahan Penolong | A5 |
| Expense - Store Supplies | 69 | Perlengkapan / Operasional Toko | |
| Local - Fresh | 58 | Persediaan / Bahan Baku / Lokal - Fresh | |
| Uniform | 56 | Perlengkapan / Seragam | |
| Tableware & Kitchenware | 53 | Perlengkapan / Peralatan Makan & Dapur | A4 |
| FIN | 52 | Persediaan / Barang Jadi | A2 (`is_fin` → `purchase_ok=False` di importer) |
| Sparepart Machine | 52 | Perlengkapan / Suku Cadang | A4 |
| Packaging | 47 | Persediaan / Kemasan | A3 |
| Local - Frozen | 44 | Persediaan / Bahan Baku / Lokal - Frozen | |
| Marketing | 39 | Perlengkapan / Pemasaran | A7 |
| Local - Chill | 22 | Persediaan / Bahan Baku / Lokal - Chill | |
| Supplies - Cleaning | 19 | Perlengkapan / Kebersihan & Keselamatan | |
| Supplies - Production | 16 | Perlengkapan / Produksi | |
| FA Building & Improvements | 12 | Aset Tetap / Bangunan & Renovasi | |
| FA Machinery | 12 | Aset Tetap / Mesin | |
| Supplies - Selling | 12 | Perlengkapan / Operasional Toko | |
| Expense - Equipment Maintenance | 6 | Beban / Jasa / Pemeliharaan & Instalasi | |
| Supplies - Packaging | 6 | Perlengkapan / Kemasan Habis Pakai | A3 |
| Freight - Import | 5 | Beban / Jasa / Biaya Impor | A8 |
| FA Office & Outlet Equipment - Metal | 5 | Aset Tetap / Peralatan Kantor & Outlet | |
| Supplies - Safety | 4 | Perlengkapan / Kebersihan & Keselamatan | |
| Supplies - Marketing | 3 | Perlengkapan / Pemasaran | |
| Expense - Water & Gas | 3 | Beban / Jasa / Utilitas | A9 |
| Expense - Other | 3 | Beban / Jasa / Lain-lain | |
| Expense - Building Maintenance | 2 | Beban / Jasa / Pemeliharaan & Instalasi | |
| Inventory - Merchandise | 2 | Persediaan / Merchandise | |
| Licensing / Sertification | 2 | Beban / Jasa / Perizinan & Sertifikasi | |
| Expense - Maintenance Fee | 2 | Beban / Jasa / Pemeliharaan & Instalasi | |
| Expense - Stationaries | 2 | Perlengkapan / Kantor & ATK | |
| Expense - Office Supplies | 1 | Perlengkapan / Kantor & ATK | |
| EXP - DOMESTIC DISTRIBUTION | 1 | Beban / Jasa / Pengiriman | |
| Expense - Household | 1 | Perlengkapan / Operasional Toko | |
| Expense - Furniture Maintenance | 1 | Beban / Jasa / Pemeliharaan & Instalasi | |
| Expense - Computer supplies | 1 | Perlengkapan / Kantor & ATK | |
| Expense - Printing | 1 | Beban / Jasa / Pemasaran & Cetak | |
| FA - Software | 1 | Aset Tetap / Perangkat Lunak | A10 |
| ONGKOS KIRIM | 1 | Beban / Jasa / Pengiriman | |
| Expense - Reklame | 1 | Beban / Jasa / Pemasaran & Cetak | |
| Expense - Production Supplies | 1 | Perlengkapan / Produksi | |
| Sewa Peralatan | 1 | Beban / Jasa / Sewa | |
| Expense - Printing Marketing | 1 | Beban / Jasa / Pemasaran & Cetak | |
| JASA INSTALASI EQUIPMENT | 1 | Beban / Jasa / Pemeliharaan & Instalasi | A11 |

Total 3.602 produk ESB-kategori + 1.680 menu = 5.282. Skrip menolak (`blocked`) nama kategori ESB yang tidak ada di
tabel (fail closed).

**Ambiguitas (default dipakai sampai dijawab, Q-G61-x):**
- **A1** "Inventory - Food & Beverage": bahan baku atau barang dagang jadi (mis. minuman botol dijual apa adanya)?
  Default bahan baku. Produk yang `available_in_pos` atau punya BoM Menu → dilaporkan.
- **A2** WIP/FIN `purchase_ok`: diproduksi CK atau dibeli setengah jadi (mis. adonan beku impor)?
  - Aturan data: `purchase_ok = True` bila produk punya `product.supplierinfo` **atau** pernah ada di PO ESB; selain itu False.
  - "Inventory - WIP" dan "WIP" digabung (asumsi beda nama di ESB saja).
- **A3** "Packaging" vs "Packaging Material": digabung ke Persediaan / Kemasan (storable). "Supplies - Packaging" =
  habis pakai non-stok (plastik/kantong toko). Konfirmasi Finance.
- **A4** Tableware, Sparepart, Supplies: dibebankan saat bill (non-storable). Bila CK menghitung stok suku cadang →
  jadikan Persediaan.
- **A5** Indirect Material (gas, minyak goreng, dll.): storable sebagai bahan penolong. Alternatif: Perlengkapan.
- **A6** LVA: dikapitalisasi (aset) atau dibebankan? Default akun beban perlengkapan (lihat §4) sampai ada kebijakan
  Finance dan `efn_account_asset`.
- **A7** Marketing (39): barang POSM (Perlengkapan) atau jasa agensi (Beban / Jasa)? Default Perlengkapan.
- **A8** Freight - Import: landed cost. Default `service`, kategori Biaya Impor.
  - Bila Finance ingin biaya impor masuk ke nilai persediaan, butuh modul CE `stock_landed_costs` (flag produk
    `landed_cost_ok`) [B: modul & field tidak ada di subset lokal; verifikasi di 19.0 sebelum dipakai]. Tanpa modul itu = beban.
- **A9** Water & Gas: utilitas (jasa) atau LPG dapur (barang)? Default jasa.
- **A10** Software: aset tak berwujud, tidak ada akun di CoA l10n_id. Finance membuat akun.
- **A11** Jasa instalasi peralatan: beban atau ikut dikapitalisasi ke aset? Default beban.

### 4. Menu (1.680 produk `sale_ok`, `categ_id` NULL)
- Target = `Menu / <akar pos.category>`: `pos_categ_ids` diurutkan (`sequence`, `id`), ambil yang pertama, naik lewat
  `parent_id` sampai akar, lalu cari/buat kategori `Menu / <akar.name>`.
- Produk tanpa `pos_categ_ids` → `Menu / Lainnya`. Produk dengan > 1 akar berbeda → dilaporkan (ambil yang pertama).
- Flag: `sale_ok=True`, `purchase_ok=False`, `is_storable=False`, `available_in_pos` **tidak diubah**, `type` tidak
  diubah (`combo` tetap).
- Pajak: lihat G54 C-09 (PB1 default). Skrip G61 tidak menyentuh `taxes_id`.

### 5. Properti akuntansi per peran — per company, **PLACEHOLDER sampai Finance (Q-ESB16)**
`ref(x)` = `env['account.chart.template'].with_company(PB).ref(x)` (xmlid template l10n_id [V `account.account-id.csv`]).
| Peran | `property_cost_method` | `property_valuation` | `property_stock_valuation_account_id` | `property_account_expense_categ_id` | `property_account_income_categ_id` |
|---|---|---|---|---|---|
| Persediaan / Bahan Baku, Kemasan | `average` | **kosong** (= company `periodic`); `real_time` hanya setelah Q-ESB16 | `l10n_id_11300180` Persediaan Lainnya | `l10n_id_51000020` Pembelian Bahan Baku | — |
| Persediaan / WIP | `average` | idem | `l10n_id_11300180` (Finance: akun WIP sendiri + `company.account_production_wip_account_id`) | `l10n_id_51000020` | — |
| Persediaan / Barang Jadi, Merchandise | `average` | idem | `l10n_id_11300180` | `l10n_id_51000010` HPP | `l10n_id_41000010` Penjualan |
| Perlengkapan (semua) | — | — | — | `l10n_id_63110140` Keperluan Lain-lain; `Kantor & ATK` → `l10n_id_63110120` Perlengkapan Kantor | — |
| Beban / Jasa / Pemeliharaan & Instalasi | — | — | — | `l10n_id_65110080` Pemeliharaan Aset (bangunan: `l10n_id_65110060`) | — |
| Beban / Jasa / Pengiriman, Biaya Impor | — | — | — | `l10n_id_65110090` Biaya Pengiriman | — |
| Beban / Jasa / Perizinan & Sertifikasi | — | — | — | `l10n_id_65110010` Biaya Perizinan | — |
| Beban / Jasa / Sewa | — | — | — | `l10n_id_65110040` Biaya Sewa | — |
| Beban / Jasa / Utilitas, Pemasaran & Cetak, Lain-lain | — | — | — | `l10n_id_63110140` (tidak ada akun air/iklan di CoA l10n_id) | — |
| Aset Tetap / Bangunan & Renovasi | — | — | — | `l10n_id_12210010` Bangunan Kantor (asset_fixed) | — |
| Aset Tetap / Mesin, Peralatan, Furnitur | — | — | — | `l10n_id_12210030` Peralatan Kantor (asset_fixed); Finance membuat akun Mesin/Furnitur | — |
| Aset Tetap / Perangkat Lunak | — | — | — | **kosong → blocked** sampai Finance membuat akun (A10) | — |
| Aset Tetap / LVA | — | — | — | `l10n_id_63110120` (dibebankan; A6) | — |
| Menu | — | — | — | `l10n_id_51000010` HPP | `l10n_id_41000010` Penjualan |

- Properti ditulis `with_company(PB)` saja. Company lain (IMD/CUP saat migrasi) memakai skrip yang sama dengan
  `--company`.
- `cost_method` diubah saat 0 move: aman dan bisa dibalik.
- `real_time` mengaktifkan jurnal valuasi saat bill/invoice. Jangan diaktifkan sebelum akun persediaan per peran dibuat
  Finance.

### 6. Implementasi [R]
1. **Sumber pemetaan tunggal** `scripts/migration/efn_mig/category_role_map.csv` (kolom: `esb_category`, `target_path`,
   `role`, `type`, `is_storable`, `sale_ok`, `purchase_ok_rule` (`true`/`false`/`supplierinfo`), `note`). Dibaca oleh:
   - **efn_mig** (`mapping.py:298` `build_categories` dan `:423-426` flag produk): ekspor berikutnya memakai path
     target + flag peran. Ini **wajib**, karena importer ESB memiliki (`owned`) `categ_id`, `type`, `is_storable`,
     `sale_ok`, `purchase_ok` (`efn_legacy_history/esb_import/mapping.py:286-295`) dan nama/induk kategori (`:262-266`).
     Tanpa perubahan ini, impor ulang mengembalikan struktur lama. Root "PB" tidak dibuat lagi; kategori target memakai
     xmlid `efn_mig_esb.<brand>_<company>_category_<path>` dengan path baru.
   - **Skrip efn-prod** `scripts/migration/g61_recategorize.py` (odoo shell; `--company PB`; default `--dry-run`,
     `--apply`).
2. Langkah skrip (idempoten, satu transaksi per fase, laporan CSV ke stdout tanpa PII):
   - (a) prasyarat: 0 `pos.session` belum tutup di company, 0 `stock.move` produk terdampak (bila ada → batal);
   - (b) buat/cari kategori target menurut `complete_name` (pencarian `('complete_name','=',path)`; pembuatan berjenjang);
   - (c) simpan `standard_price`;
   - (d) tulis `categ_id` + flag per produk (batch 500, `with_company(PB)`, konteks `tracking_disable=True`);
   - (e) menu §4;
   - (f) properti §5;
   - (g) cek ulang `standard_price`;
   - (h) kategori lama di bawah root "PB" yang kosong (0 produk, 0 anak, tidak dirujuk `product.pricelist.item`,
     `stock.putaway.rule`, `stock.route`) dihapus beserta `ir.model.data`-nya. Kategori yang masih dirujuk dilaporkan;
   - (i) laporan: jumlah per target, produk `available_in_pos` di luar peran Menu, komponen BoM yang menjadi `service`,
     produk ambigu A1–A11.
3. Data skeleton opsional untuk DB baru: tidak dibuat dulu (YAGNI). Pohon lahir dari importer/skrip. Bila DB demo
   membutuhkannya, buat `efn_setup_product` terpisah nanti.
4. Test:
   - pytest efn_mig: setiap kategori ESB PB terpetakan; CSV tanpa duplikat; flag konsisten dengan peran;
   - TransactionCase skrip (fungsi inti diekspor ke modul yang bisa diimpor test, mis. `efn_legacy_history/esb_import/recategorize.py` [R]): produk storable tanpa move → non-storable; `standard_price` tetap; SMD tidak tersentuh.

### 7. Urutan eksekusi di efn-prod
1. Backup DB (runbook `efn-prod-odoo-reset.md`).
2. `-u efn_setup_pos` (data §4 G54) dan `-i efn_setup_menu` (G55).
3. G61 dry-run → review Finance → `--apply`.
4. Wizard G54 *check* → *apply* untuk PB.
5. Buka satu sesi uji per config sebagai user kasir. Lakukan transaksi tunai Rp1 + EDC, lalu tutup sesi dan cek jurnal
   (PB1, piutang POS, kas).

---

## Pertanyaan untuk user / klien
| ID | Pertanyaan | Default |
|---|---|---|
| Q-G54-1 | QRIS: pakai metode manual (cek mutasi) sampai gateway hidup? | ya, `payment_method_type=none` |
| Q-G54-2 | Akun kas: satu akun per outlet (32 akun 1111xxxx) atau satu akun "Kas Outlet" bersama? | per outlet |
| Q-G54-3 | Config F&B: preset default dine-in atau bawa pulang per outlet PB (Q-18)? | dine-in bila outlet punya meja |
| Q-G55-1 | Ikon Accounting: pakai ikon akun CE (sama dengan Finance) atau ikon sendiri? | ikon CE dulu |
| Q-G55-2 | User Billing saja kehilangan "Invoice Analysis" (pindah ke Accounting). Diterima? | ya |
| Q-G61-1 | Pohon kategori tanpa prefiks merek (dipakai bersama PB/IMD/CUP/BCF)? | ya; SMD tetap terpisah |
| Q-G61-2 | A1–A11 di §3 | default di tabel |
| Q-ESB16 | Valuasi perpetual (`real_time`) + AVCO, dan akun persediaan per peran | AVCO sekarang, periodic sampai dijawab |

## Belum terverifikasi
- Teks terjemahan id CE untuk `menu_mrp_root`/`menu_finance` (i18n tidak ada di subset lokal).
- Perilaku pemasangan bahasa baru terhadap label.
- Isi `action_account_payments_transfer`.
- Menu modul non-subset di efn-prod (kueri §G55.1).
- `_run_average_batch` tanpa move.
- Layar login `pos_hr` tanpa karyawan.
- Pajak menu PB saat ini.
- `stock_landed_costs` di 19.0.
