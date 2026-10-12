# Laporan efn-odoo-functional — 2026-10-05

## Ringkasan
- Fit-gap fase 1–2: `docs/08_ODOO_FIT_GAP.md` — 26 kebutuhan: **19 Standar CE**, **3 OCA**, **4 Custom** (OTP promo, poin member BFF↔POS, KDS, QC intake petani).
- Modul konfigurasi data-only baru (tanpa Python, belum pernah di-install):

| Modul | Lisensi | depends (terverifikasi 19.0) | Isi |
|---|---|---|---|
| `efn_setup_company` | LGPL-3 | `efn_core`, `l10n_id` | IDR aktif; 4 company EFN/RTL/FNB/FARM (nama placeholder); CoA `l10n_id` via `try_loading`; demo: PB1 10% PLACEHOLDER di PT F&B + jadi pajak default, RTL harga termasuk pajak, Holding = agen penagih |
| `efn_setup_security` | LGPL-3 | `auth_totp`, `auth_totp_mail`, `auth_password_policy` | `auth_totp.policy = employee_required`, `auth_password_policy.minlength = 12` |
| `efn_setup_audit` | AGPL-3 | `auditlog` (OCA), `product`, `stock`, `loyalty` | 8 aturan auditlog: pricelist, pricelist item, product.template, loyalty program/reward/rule, stock.quant (opname saja), res.partner |
| `efn_setup_pos` | LGPL-3 | `point_of_sale`, `pos_restaurant`, `pos_self_order`, `pos_loyalty`, `pos_mrp`, `pos_hr`, `pos_discount`, `efn_setup_company` | `ir.default` pos.config: `restrict_price_control=True`, `manual_discount=False`; 3 filter audit baris POS; demo: kategori stasiun, 2 printer ePOS, denah meja; checklist pos.config di README |
| `efn_setup_mrp` | LGPL-3 | `mrp`, `product_expiry`, `efn_kitting` | Lot/serial, Storage Locations, Multi-Step Routes; kategori *Perishable (FEFO)*; demo: paket lot+expiry, 2 reorder rule Manufacture |

`efn_setup_audit` dipisah dari `efn_setup_security` karena lisensi AGPL dan dependensi berat.

## Tindak lanjut pesan efn-lead (sisa risiko SEC-07/SEC-12)
1. **Kasir mengubah nomor HP pelanggan** — standar 19.0 (terverifikasi): ACL `res.partner` untuk `base.group_user` hanya read; write/create hanya `base.group_partner_manager`, yang tidak di-imply grup POS; frontend POS menyimpan partner lewat ORM biasa. → Kontrol = **user terminal POS tanpa *Contacts: Creation*** (didokumentasikan di README `efn_setup_pos`) + audit `res.partner` (`efn_setup_audit`). Gap tersisa: bila kasir harus bisa **membuat** pelanggan baru tapi tidak mengubah HP pelanggan lama → tidak ada setting standar (lihat rekomendasi E-6).
2. **Harga/diskon manual** — `restrict_price_control` ada (19.0, hanya harga) → default True; `manual_discount` default False (diskon baris mati, diskon hanya lewat program promo). Keduanya `ir.default` untuk pos.config baru.
3. **Audit baris diskon tanpa `reward_id`** — 3 `ir.filters` bersama di Order Lines POS: diskon tanpa `reward_id`, `price_type='manual'`, baris negatif non-reward (diskon global).
4. **2FA** — `auth_totp.policy = employee_required` di `efn_setup_security` (terverifikasi; `auth_totp_mail_enforce` 404 di 19.0).

## Audit modul custom & rekomendasi untuk efn-odoo-engineer

### `efn_pos_otp_gate` — **PERTAHANKAN** (tidak menduplikasi pos_loyalty), sederhanakan sedikit
- Modul ini memakai program/reward/rule standar dan hanya menambah flag + verifikasi server; perhitungan reward tetap oleh `pos_loyalty`. Bukan duplikasi.
- Alternatif standar yang dievaluasi: program `coupons` + `loyalty.card` sekali pakai, divalidasi server oleh `pos.config.use_coupon_code` (cek `expiration_date`, `partner_id`, poin). **Tidak cukup**: tidak ada batas percobaan (kasir bisa brute force kode pendek), tidak mengirim WA, tidak terhubung ke ledger BFF / consume token per order. Jadi OTP tetap custom.
- E-1 *Sederhanakan:* pengecualian kiosk/self-order lebih baik lewat konfigurasi standar `loyalty.program.pos_config_ids` (program ber-OTP tidak ditautkan ke config self-order), bukan logika tolak/hapus; pertahankan pengecekan server sebagai *defense in depth*.
- E-2 *Batasi* `efn_requires_otp` ke `program_type` yang bermakna (`promotion`, `buy_x_get_y`, `promo_code`, `coupons`); untuk `gift_card`/`ewallet`/`loyalty` sembunyikan/tolak — `loyalty`/`ewallet` tidak boleh dipakai untuk poin member (ADR-020).
- E-3 Saklar global `efn.pos.otp_required_for_promo` + flag per program = dua lapis; boleh dipertahankan sebagai *kill switch*, tetapi dokumentasikan bahwa mematikannya **membuka** promo tanpa OTP (risiko fraud) dan catat perubahannya di audit (ir.config_parameter belum diaudit).

### `efn_kitting` — **PERTAHANKAN guard, SEDERHANAKAN sisanya**
- Guard "paket CK tidak boleh punya BoM Kit/Phantom & harus storable" tidak ada di CE → custom kecil yang sah.
- E-4 `mrp.bom.efn_kitting_role` (stored compute) redundan — sepenuhnya turunan `type` + `efn_is_kitting_package`. Hapus, atau jadikan non-stored bila hanya untuk tampilan.
- E-5 **Koreksi README** langkah 2: di 19.0 route Manufacture `product_selectable=False` (route gudang via `manufacture_to_resupply`); tidak dipasang di form produk.
- E-7 **Konflik demo:** demo `efn_kitting` membuat company kode `FNB`; `efn_setup_company` juga → UNIQUE `efn_brand_code` gagal bila keduanya di-install dengan demo. Usulan: `efn_kitting` demo depends `efn_setup_company` dan memakai `efn_setup_company.company_fnb` (lalu `efn_setup_mrp` demo ikut ref yang sama). UoM Porsi boleh tetap di `efn_kitting`.
- Demo paket kini bertracking lot (oleh `efn_setup_mrp` demo) — uji manual MO perlu lot; test otomatis `efn_kitting` tidak terdampak (membuat produk sendiri).

### `efn_core.res_company` — **PERTAHANKAN kode, SEDERHANAKAN turunan**
- `efn_brand_code`: `res.company` 19.0 tidak punya field kode → pertahankan.
- E-8 `efn_business_type`: relasi 1:1 tetap dengan kode (dipaksa constraint) → redundan. Jadikan computed dari `efn_brand_code` (atau hapus). Rezim pajak harus berasal dari konfigurasi standar (`account_sale_tax_id`, fiscal position), bukan field ini.
- E-9 `efn_is_merchant_of_record`: ADR-019 memakai istilah "pemegang akun gateway/agen penagih" dan hanya satu company → tambahkan constraint maksimal satu company True atau ganti dengan parameter `efn.gateway_holder_company_code`.
- Jangan memakai `res.company.parent_id` (Branches) untuk PT.

### Gap baru untuk engineer
- E-6 (bila klien memilih kasir boleh membuat pelanggan) kunci field `phone`/`mobile` partner yang sudah ada untuk user non-manager (override `write` kecil atau field-level group), tetap dengan audit.
- E-10 Integrasi poin member POS ↔ ledger BFF (F-25): redeem sebagai baris diskon dengan token BFF (pola sama dengan OTP gate), earn setelah sync.
- E-11 Bila `try_loading` dalam data XML gagal di CI: pindahkan ke `post_init_hook` kecil di `efn_setup_company`.
- E-12 (opsional) `post_init_hook` pembuat `pos.config` per outlet dengan `with_company(...)` setelah Q-18 dijawab.
- E-13 Keamanan: QRIS CE menyimpan `l10n_id_qris_api_key` di DB (`res.partner.bank`) — bila dipakai, batasi akses field ke grup akuntan/admin dan masukkan ke runbook rotasi; ini menyimpang dari prinsip secret di `/run/secrets` → butuh keputusan/pengecualian tercatat.
- E-14 Tambahkan `efn_setup_audit` ke daftar install CI setelah `scripts/fetch-oca.sh` (sudah mengambil `auditlog`).

## Pertanyaan untuk klien (prioritas)
1. **Q-22 + konsultan pajak:** PB1 per kota (tarif), include/exclude harga menu, service charge di DPP; status PKP tiap PT; PPN hasil pertanian (PT Farming); PT F&B yang juga menjual barang kemasan.
2. **Penyedia QRIS di kasir:** qris.online (bawaan CE, siap) vs Xendit (ADR-016; butuh custom POS).
3. **Q-18/Q-19/Q-20/Q-24:** jenis layanan, stasiun dapur & routing, layout meja, printer per outlet.
4. **Q-1/Q-2:** nama legal & NPWP 4 PT; CoA seragam? akun liabilitas PB1, dana titipan, poin.
5. **Q-27/Q-28:** approval void/refund/diskon; apakah kasir boleh diskon baris manual (default kini mati).
6. **Q-12:** kasir boleh mendaftarkan pelanggan baru di POS, atau hanya lewat Super-App?
7. **Q-34/Q-35:** umur simpan paket, FEFO, waste; produksi CK berbasis forecast atau pesanan.
8. **Q-54:** kebijakan password/SSO; SMTP untuk kode 2FA email.
9. **Q-3/Q-4:** transfer pricing & settlement inter-company (memblokir `efn_setup_intercompany`).

## Gelombang 5 — 2026-10-06 (keputusan user: pajak configurable, gateway agnostik, erasure UU PDP)

### Konfigurasi yang diterapkan
| Modul | Perubahan |
|---|---|
| `efn_setup_company` 19.0.1.1.0 | Baris `efn_business_type` dihapus dari 4 company (QA U6). **Baru `data/account_tax_pb1_data.xml`** (noupdate, dimuat setelah CoA): grup pajak *PB1 (Pajak Restoran / PBJT)*, pajak *PB1 Default 10% (PLACEHOLDER)* tanpa `price_include_override` (ikut `res.company.account_price_include`), dijadikan `account_sale_tax_id` PT F&B — xmlid lama dipertahankan. **Demo**: pajak *PB1 Kota Contoh 5%* (`original_tax_ids` = PB1 default) + fiscal position *Outlet Kota Contoh*; grup + pajak *Service Charge 5%* (`sequence` 5, `include_base_amount` → masuk dasar PB1); RTL `tax_included`; Holding merchant of record. README: panduan admin menambah tarif PB1 kota, include/exclude, opsi service charge |
| `efn_setup_pos` 19.0.1.1.0 | **Baru `data/pos_payment_method_data.xml`**: *Kartu Debit/Kredit (EDC manual)* untuk RTL & FNB (jurnal bank PT via `search`). Tunai tidak dibuat (satu metode kas per outlet, dibuat otomatis). QRIS `id_qr` sengaja tidak dikonfigurasi. README: checklist fiscal position/preset/payment per outlet |

### Spesifikasi gap untuk efn-odoo-engineer (detail: `docs/08` §4)
- **E-15 `efn_pos_payment_gateway`** (F-30): metode terminal `use_payment_terminal='efn_gateway'`; RPC create/status/cancel ke BFF §8.3 (cek grup POS); model `efn.pos.payment.intent` (multi-company); **validasi server saat sinkron order** (intent `paid`, amount & order cocok, tidak dipakai ulang); JS `PaymentInterface` + `register_payment_method`, nonaktif saat offline, polling 2 s, QR di layar pelanggan via `order.qrPaymentData`; cancel; tanpa reversal; cron rekonsiliasi; `efn.bff.client.get`.
- **E-16 `efn_privacy`** (F-29): `res.partner.efn_member_hash` (compute stored indexed, pepper dari `EFN_MEMBER_HASH_PEPPER_FILE`, vektor uji bersama BFF); cron pull → anonimisasi partner + `pos.order.email/mobile` + chatter/auditlog → `privacy.log` → complete; retensi faktur/pos.order dipertahankan.
- **E-17** jangan membuat logika pajak/field tarif custom; `efn_business_type` bukan penentu rezim pajak.
- E-13 (QRIS API key di DB) **gugur** bila E-15 dipakai dan `id_qr` tetap tak terkonfigurasi.

### Usulan kontrak ke efn-lead
- §8.3 tidak punya **refund** QRIS; fase 1 refund via metode lain (SOP). Bila dibutuhkan: `POST /v1/pos/payments/:id/refund`.
- §8.4: sebutkan bahwa Odoo juga menghapus salinan kontak `pos.order` dan log audit partner; nama secret file default `member_hash_pepper`.

### Asumsi belum terverifikasi
Penerapan `default_fiscal_position_id`/preset & `include_base_amount` di frontend POS; posting pajak tanpa akun (jatuh ke akun pendapatan); atribut `search` + `ref()` di XML; bentuk `order.qrPaymentData`; label menu CE untuk pajak/fiscal position; `phone_sanitized` (`mail.thread.phone`) tidak dipakai. Tarif PB1, SC, akun PB1 = keputusan konsultan pajak (Q-22, Q-2).

### Pertanyaan klien tambahan
Q-22 diperluas: daftar kota outlet + tarif PBJT Perda, perlakuan takeaway/delivery, SC dipungut di outlet mana & masuk DPP? Q-2: akun liabilitas PB1 & pendapatan SC. DPO: retensi partner POS, faktur atas nama perorangan.

## Batasan
Tidak ada yang di-install/dijalankan. Field, model, dan xmlid diverifikasi pada source 19.0 via WebFetch (dirangkum per file di `docs/08`); perilaku runtime (offline POS, ePOS, QRIS, FEFO di POS, inter-company stok) menunggu PoC. Modul `efn_core`/`efn_pos_otp_gate`/`efn_kitting` tidak diubah.

## Gelombang 10 — 2026-10-06 (kebijakan baru: tanpa dependency OCA/Cybrosys/vendor)

### Perubahan fit-gap (`docs/08`)
| ID | Sebelum | Sesudah |
|---|---|---|
| F-03 Inter-company | **O** — OCA `purchase_sale_inter_company` + `account_invoice_inter_company` | **C** — `efn_intercompany` fase 1 (§5.2). CE 19 hanya `account_payment_interco` (kliring pembayaran online `payment.transaction`), lokasi *Inter-company transit*, `ref_company_ids` |
| F-17 Audit log | **O** — OCA `auditlog` (+ `patches.py`) | **C** — `efn_audit` **segera** (§5.1); standar CE parsial (chatter tracking, `stock.move.is_inventory`, `restrictive_audit_trail`) |
| F-19 Konsolidasi | **O** — `mis_builder`, `account_financial_report` (+ `report_xlsx`, `date_range`) | **S + jalur data** — Invoice Analysis/Journal Items + ekspor xlsx standar; konsolidasi di Metabase/ClickHouse; custom Odoo hanya bila Q-6 menolak |
| F-01 | saran `base_multi_company`/`product_multi_company` | `product.template.company_id` standar; OCA hanya referensi bila Q-8 = subset PT |
| F-04, F-10 | saran `pos_session_sequence`, `pos_edit_order_line`, `pos_partner_birthdate` | dihapus (tidak dibutuhkan) |
| F-07, F-12, F-16, F-26 | OCA sebagai opsi | jadi kolom *Referensi* (+ Cybrosys `pos_kitchen_screen_odoo` untuk KDS) |
Rekap: Standar 19→**20**, OCA 3→**0**, Custom 8→**10**. Temuan baru §2 no. 11–17.

### Referensi terverifikasi (WebFetch `__manifest__.py` @19.0, 2026-10-06)
| Modul | Versi | Lisensi | Dipakai untuk |
|---|---|---|---|
| OCA/server-tools `auditlog` | 19.0.2.0.0 | AGPL-3 | konsep rule/log/line, exclude field, autovacuum (cron `autovacuum(180)` nonaktif), tanpa multi-company |
| Cybrosys `user_audit` | 19.0.1.0.0 | LGPL-3 | hanya ide filter user; log dari JS, tanpa nilai lama/baru, log bisa dihapus wizard → tidak cocok |
| OCA/multi-company `purchase_sale_inter_company` | 19.0.1.0.1 | AGPL-3 | PO→SO (`button_approve`, `ref_company_ids`, `so_from_po`, user inter-company) |
| OCA/multi-company `account_invoice_inter_company` | 19.0.1.0.3 | AGPL-3 | invoice→bill (`action_post`, `auto_invoice_id`, cancel berantai) |
| Cybrosys `inter_company_synchronization` | 19.0.1.0.0 | AGPL-3 | PO→SO di `button_confirm`, cegah loop via `origin`, tanpa invoice |
| OCA/mis-builder `mis_builder` | 19.0.1.2.1 | AGPL-3 | (cadangan F-19) |
| OCA/reporting-engine `report_xlsx` | 19.0.1.0.2 | AGPL-3 | tidak perlu (ekspor xlsx standar CE) |
| OCA/server-ux `date_range` | 19.0.1.0.0 | LGPL-3 | tidak perlu |
| OCA/account-financial-reporting `account_financial_report` | 19.0.0.0.26 | AGPL-3 | (cadangan F-19) |
| Cybrosys `dynamic_accounts_report` / `base_accounting_kit` | 19.0.1.1.2 / 19.0.3.3.1 | LGPL-3 | (cadangan F-19; menarik `cybrosys_support_client`) |
| OCA `product_multi_company` | 19.0.1.0.1 | AGPL-3 | referensi F-01 |
| OCA `mrp_multi_level` | 19.0.1.0.5 | LGPL-3 | referensi F-12 |
| OCA `quality_control_oca` | 19.0.1.0.0 | AGPL-3 | referensi F-16 |
| OCA `pos_printing_qztray` | 19.0.1.0.1 | AGPL-3 | referensi F-07 |
| Cybrosys `pos_kitchen_screen_odoo` | 19.0.1.0.0 | LGPL-3 | referensi F-26 |
CybroAddons punya branch 19.0 (juga 20.0, saas-19.4). Padanan Cybrosys untuk audit log *lengkap* (nilai lama/baru) **tidak ditemukan** (listing tree terpotong + WebSearch) — "belum terverifikasi tidak ada".

### `efn_setup_audit` 19.0.2.0.0 (data-only, LGPL-3)
- `depends`: `efn_audit`, `product`, `stock`, `loyalty` (OCA `auditlog` dihapus). `__init__.py` tidak lagi mengimpor `patches.py`. **`installable: False`** sampai `efn_audit` ada.
- `data/efn_audit_rule_data.xml` (noupdate): 9 aturan `efn.audit.rule` — pricelist, pricelist item, product.template, loyalty program/reward/rule, stock.quant (hanya `inventory_quantity`, `inventory_date`, `user_id`), res.partner (`name`, `phone`, `email`, `vat`, `active`, `company_id`; **phone & email ter-mask**), ir.config_parameter (`efn.%` + `auth_totp.policy` + `auth_password_policy.minlength`). Header file = kontrak nama field model.
- `data/ir_config_parameter.xml`: `efn.audit.retention_days = 730` (PLACEHOLDER).
- README: tabel aturan, yang sudah standar, migrasi dari OCA (uninstall `auditlog` wajib karena patch tidak dimuat lagi), keputusan klien.

### Rekomendasi untuk efn-odoo-engineer
- **E-18 (segera) `efn_audit`** — spesifikasi lengkap `docs/08 §5.1`: hook di `_inherit="base"` (create/write/unlink, tanpa monkeypatch registry, tanpa `ThrowAwayCache`), rule di-cache `ormcache` sebagai data polos, filter field sebelum `read`, nilai lama via `read()` sebelum `super().write`; model rule/log/line; masking HP/email sebelum simpan; immutable (write/unlink ditolak kecuali penanda `cr.cache` purge); grup auditor/manager; record rule multi-company; `@api.autovacuum` retensi; API `_efn_audit_purge_records` untuk erasure. Lalu set `efn_setup_audit` `installable: True`.
- **E-19** `efn_privacy`: ganti cabang OCA `auditlog.log` → `_efn_audit_purge_records("res.partner", ids)`; depends `efn_audit`. `efn_pos_otp_gate`: test & docstring yang menyebut auditlog/patch registry disederhanakan (masalah urutan patch hilang).
- **E-20** `git rm efn_setup_audit/patches.py efn_setup_audit/data/auditlog_rule_data.xml` (agent ini tidak bisa menghapus file).
- **E-21 (fase 1)** `efn_intercompany` — `docs/08 §5.2`; jangan mulai sebelum Q-3/Q-4 dan konsultan pajak menjawab.
- E-14 (Gelombang 1: tambahkan `efn_setup_audit` ke CI setelah fetch OCA) **gugur**.

### Untuk efn-lead / efn-devsecops-engineer (di luar wewenang tulis agent ini)
- `.github/workflows/ci.yml` `pick()`: hapus cabang `auditlog`; selama `efn_audit` belum ada, `efn_setup_audit` ber-`installable: False` — Odoo diperkirakan melewatinya pada `-i` (state `uninstallable`), **belum diuji di 19.0**; lebih aman lewati di `pick()` bila `services/odoo/addons/efn_audit/__manifest__.py` belum ada. Sama untuk `e2e-nightly.yml` (`-i ...,efn_setup_audit,...`).
- `scripts/fetch-oca.sh`, `services/odoo/oca/README.md`, `docs/06 §2` (daftar OCA untuk fetch) perlu diselaraskan dengan kebijakan tanpa OCA (efn-architect/devsecops).
- DB dev/staging yang sudah memasang OCA `auditlog`: uninstall `auditlog` setelah `efn_audit` aktif (tanpa `patches.py`, bug flush ORM 19 kembali).
- **efn-data-ai-engineer**: ETL akuntansi (`account_move_line` dst.) + dashboard konsolidasi Metabase (`docs/08 §5.3`) — belum ada di `services/etl`.

### Pertanyaan klien (Gelombang 10)
1. **Q-6 / auditor eksternal:** apakah laporan keuangan & konsolidasi di Metabase (data warehouse, near-real-time 15 menit) dapat diterima, atau laporan wajib keluar dari Odoo?
2. **Q-49 + DPO:** retensi log audit (placeholder 2 tahun)? Apakah cukup nomor HP/email **tersamar** di log audit, atau auditor perlu nilai lengkap (butuh enkripsi kolom + persetujuan DPO)?
3. Siapa auditor internal per PT (grup *EFN Audit: Auditor*)? Perlu audit tambahan untuk pajak/fiscal position, metode pembayaran POS, hak akses user?
4. **Q-3/Q-4:** harga transfer CK→Retail (harga PO vs pricelist F&B), siapa yang membuat dokumen (PO Retail memicu SO F&B?), SO otomatis dikonfirmasi?, settlement/netting bulanan? PPN atas penjualan paket CK antar-PT (konsultan pajak).
5. **Q-8:** produk dipakai semua PT, satu PT, atau sebagian PT?
6. Biaya produk AVCO (`product.product.standard_price`) perlu diaudit? (volume: tiap penerimaan barang).

### Asumsi belum terverifikasi (Gelombang 10)
xmlid `base.field_ir_config_parameter__key/value` dan `base.field_res_partner__name/phone/email/vat/active/company_id` (konvensi, belum diuji install); perilaku `-i` atas modul `installable: False`; aktivasi *Inter-company transit*; overhead hook `base` (ukur di PoC); `restrictive_audit_trail` perilaku detail.

## Gelombang 13 — 2026-10-07 (Fase 1: pilot POS retail 1 toko, loyalty POS §9, migrasi)

Detail lengkap: `docs/08_ODOO_FIT_GAP.md` §6 (F-31..F-42, G13-1..G13-7, P-1..P-10, K-1..K-8).

### Konfigurasi standar yang diterapkan (`efn_setup_pos` 19.0.1.1.0 → 19.0.1.2.0)
| File | Isi | Mengapa |
|---|---|---|
| `data/barcode_nomenclature_data.xml` (baru) | Nomenklatur *EFN Retail (IDR)* → `res.company.nomenclature_id` PT Retail: harga Rupiah `23.....{NNNNN}`, berat `21.....{NNDDD}`, pelanggan 042, karyawan 041, kupon 043\|044, produk `.*` | Aturan harga bawaan `{NNNDD}` maks 999,99; **aturan diskon bawaan `22{NN}` memberi diskon tanpa cek peran/`manual_discount` (fraud) — tidak dibuat**; aturan lot `10` berisiko salah baca EAN impor |
| `data/pos_bill_idr_data.xml` (baru) | Pecahan Rp100..Rp100.000; 13 pecahan USD bawaan dihapus (`forcecreate="0"` → tidak kembali) | Hitung kas buka/tutup per pecahan (R-O06) |
| `data/pos_retail_defaults.xml` (baru) | `ir.default` `order_edit_tracking=True`, `set_maximum_difference=True`, `amount_authorized_diff=0`; `point_of_sale.limited_product_count=20000` (PLACEHOLDER) | Void tersinkron tercatat; setiap selisih kas butuh supervisor; katalog offline (default CE 5000) |
| `data/pos_audit_filters.xml` | +4 filter: refund tanpa struk, order refund per kasir, order diedit/void baris, harga manual non-timbang | R-O05; label harga timbangan menandai `price_type='manual'` (positif palsu filter lama) |
| `demo/pos_retail_demo.xml` (baru) | Kategori *Fresh (Timbang)*, produk kg berbarcode berat & berlabel harga, pricelist *Harga Member (DEMO)* 5% | Q-25/Q-9 belum dijawab |
| `README.md` | Matriks peran `pos_hr` (minimal/basic/manager) per aksi, SOP refund/void/kas/Z, barcode timbangan, multi-UoM, FEFO, mekanik promo, checklist outlet pilot retail | |

Catatan: blok `noupdate` (default, nomenklatur PT Retail, hapus pecahan) hanya berlaku saat **install**; DB dev yang sudah memasang 19.0.1.1.0 perlu install bersih atau set manual.

Template migrasi (baru): `docs/templates/migrasi/` — `01_produk.csv`, `02_lot.csv`, `03_stok_awal.csv`, `04_pelanggan_odoo.csv`, `05_member_bff.csv`, `06_saldo_poin_awal_bff.csv`, `07_karyawan_kasir.csv` (judul kolom saja) + `README.md` (urutan impor, kolom, aturan UU PDP, ringkasan validator). Saldo poin & consent → BFF, bukan Odoo. PIN karyawan tidak pernah di file.

### Temuan standar CE 19 (terverifikasi source 19.0)
- "Approval supervisor" CE = supervisor mengambil alih kasir via PIN (`pos_hr`); tidak ada dialog persetujuan inline atau jejak "disetujui oleh". Peran **minimal** memblokir refund, tombol `-`, harga, diskon; **basic** bisa refund; **manager** bisa ubah pembayaran & tutup sesi berselisih.
- Hash PIN karyawan (SHA-1) dimuat ke browser → PIN pendek mudah ditebak pemegang perangkat (**untuk efn-security-reviewer**).
- Batas selisih kas hanya ditegakkan frontend; `cash_register_difference` tidak stored.
- Kas masuk/keluar hanya POS Manager atau grup *Invoicing*.
- Refund tidak dibatasi ke metode bayar asal; void sebelum sinkron tidak tercatat; `remove_from_ui` menghapus draft tersinkron (`sudo().unlink()`).
- `product.template.taxes_id` tanpa `check_company` → risiko pajak PT lain saat impor.

### Rekomendasi untuk efn-odoo-engineer
- **E-22 (WAJIB fase 1) `efn_pos_loyalty_ledger`** — `docs/08 §6.3`: produk "Poin EFN" via `_get_special_products`; model `efn.pos.loyalty.reservation` & `efn.pos.loyalty.outbox` (transactional outbox, idempotency key unik, payload dibangun saat kirim — HP tidak disimpan, backoff, `_trigger`, urutan reverse setelah earn); RPC balance/reserve/release; tombol "Tukar Poin EFN" di layar pembayaran (OTP `pos_redeem`, baris per kelompok pajak, `price_type: "automatic"`), dialog batal saat kembali ke layar produk, blokir offline; validasi server saat sync (deteksi baris poin dari produk — SEC-12, `UserError` tanpa reservasi, review bila nilai/partner beda); earn/reverse via outbox dengan ratio; test & tour terdaftar di spesifikasi.
- **E-23** perluasan kecil `efn_pos_otp_gate`: `efn.pos.otp.challenge.purpose` + parameter `purpose` di `efn_otp_request`/`efn_otp_flow.js`; gate promo hanya menerima `pos_promo` (cegah challenge tukar poin membuka promo).
- **E-24** setelah E-22: filter audit "Baris negatif non-promo" (`efn_setup_pos`) akan menampilkan baris Poin EFN — modul baru menyediakan filter pengganti yang mengecualikan produk poin (efn_setup_pos tidak bisa me-`ref` produk modul yang belum ada).
- **E-25 (WAJIB sebelum migrasi) validator pra-impor** — `docs/08 §6.4` (skrip CLI, bukan modul Odoo; pemilik diputuskan efn-lead).
- **Opsional menunggu Q-27/Q-29:** G13-2 `efn_pos_supervisor_approval`, G13-3/5 `efn_pos_void_log`, G13-4 refund ke metode asal, G13-6 batas selisih kas di server (`docs/08 §6.5`). **Jangan dibangun** sebelum klien menjawab; pilot bisa jalan dengan standar.
- **Jangan dikustom:** peran `pos_hr`, layar refund/tutup sesi, `pos.bill`, nomenklatur barcode (konfigurasi), `loyalty.program` untuk poin.

### Usulan kontrak ke efn-lead (docs/04 §9) — K-1..K-8 di `docs/08 §6.7`
Ringkas: definisi `eligible_amount` ambigu (K-1); kategori tanpa poin harus di Odoo (K-2); commit membawa `member_ref`/`value_idr` dan diterima setelah kedaluwarsa bila belum di-release (K-3); token `pos_redeem` di-consume saat reserve (K-4); release idempoten (K-5); reverse sebelum earn → 409 retryable (K-6); endpoint migrasi member+consent & saldo poin awal (K-7); rate limit/OTP untuk balance (K-8).

### Pertanyaan klien yang memblokir pilot (P-1..P-10, `docs/08 §6.6`)
Toko pilot & jumlah SKU; Q-25 timbangan & format label; Q-27 peran kasir/refund/void/toleransi selisih/setoran; Q-28 daftar promo; Q-29 metode bayar, pembulatan, refund ke metode asal; Q-55/Q-56 internet & perangkat; Q-13 + Finance parameter poin & retur berpoin; konsultan pajak (PPN atas penukaran poin); Q-5/Q-12 sumber data & consent; Q-34 lot di kasir.

### Asumsi belum terverifikasi (Gelombang 13)
Install modul dengan data baru belum dijalankan (`<delete>` pada blok noupdate, penulisan `nomenclature_id` ke company milik modul lain, `eval` `obj(...)` untuk `taxes_id` demo); check digit barcode demo dihitung manual; perilaku POS untuk barcode kemasan `product.uom`, FEFO otomatis tanpa lot, layar timbangan/IoT, monitor pelanggan, offline dengan 20.000 produk; format xmlid pajak per company (`account.<id>_tax_ST4`); akun selisih jurnal tunai `l10n_id`; produk khusus `_get_special_products` dimuat walau tanpa kategori POS. Tidak ada test/CI yang dijalankan oleh agent ini.

## Gelombang 14 — 2026-10-08 (Fase 2: POS F&B + KDS + QR order 1 outlet, kitting CK)

Detail: `docs/08_ODOO_FIT_GAP.md` §7 (F-43..F-54, temuan #29–35, E-26 spesifikasi §7.3, opsi QR §7.4, P-11..P-18).

### Konfigurasi diterapkan (`efn_setup_pos` 19.0.1.3.0 → 19.0.1.4.0)
| File | Isi | Mengapa |
|---|---|---|
| `data/pos_fnb_defaults.xml` (baru, noupdate) | preset master Delivery `available_in_self=False`; Takeout `use_timing=False` | pesan-antar lewat Super-App/BFF + minimisasi alamat; slot takeout bawaan = kalender demo Odoo (Selasa–Sabtu, main company) |
| `demo/pos_fnb_demo.xml` (baru) | lantai Teras (3 meja), atribut no_variant Level Pedas/Permintaan Dapur/Gula, 2 menu ber-PB1 default (Dapur Panas, Bar), preset *Bungkus – Kota Contoh* | Q-18..Q-22 belum dijawab |
| `tests/test_fnb_defaults.py` (baru) | 3 test default preset | regresi data |
| `README.md` | tabel SOP restoran standar CE 19, aturan preset × fiscal position, printer HTTPS, checklist outlet F&B pilot (24 langkah), peringatan QR order | |

### Temuan standar CE 19 (terverifikasi source 19.0)
- Meja/denah, open bill, split, pindah/gabung meja (`transferOrder`, `mergeTableOrders`, `restaurant.table.parent_id`), **course** (`restaurant.order.course`), catatan baris/order, modifier atribut, combo, preset dine-in/takeout/delivery: semua standar.
- **Service charge tetap tidak ada** di CE 19 (cek ulang `res.config.settings` point_of_sale & pos_restaurant).
- `setPreset()` mengganti fiscal position config (tidak digabung) → **preset takeaway per kota**.
- `pos.printer` CE hanya `iot`/`epson_epos`; HTTPS ke printer lewat domain sertifikat Epson dari nomor seri (`omnilinkcert.epson.biz`) — [B].
- **QR order ponsel** (`pos_self_order` mobile/consultation) = route `auth='public'` di Odoo, tanpa rate limit → **bertentangan ADR-023/CLAUDE.md**; kiosk di jaringan WireGuard aman. `pos_self_order` ter-auto-install bersama `pos_restaurant`.
- **Bus Odoo 19 tidak membatasi kanal string** → kanal KDS wajib token acak (`pos.bus.mixin`); pola Cybrosys (`pos_order_created_{config_id}`) tidak aman — **untuk efn-security-reviewer**.

### Rekomendasi untuk efn-odoo-engineer
- **E-26 (Fase 2, WAJIB) `efn_pos_kds`** — `docs/08 §7.3`: `efn.kds.station` (pos.bus.mixin, kategori, mode `kds`/`kds_printer`/`printer`, printer cadangan, SLA, user perangkat, heartbeat), `efn.kds.ticket`(+line) per order × stasiun × pengiriman, delta qty dihitung server (`pos.order.line.efn_kds_sent_qty`) di `_process_saved_order` saat `last_order_preparation_change` berubah, course yang di-*fire*, bump/recall via RPC dengan cek user perangkat, `_notify` ke kanal token, layar OWL `/efn_kds/<id>` `auth='user'`, grup `group_kds_device` + 2FA, fallback printer di `sendOrderInPreparation`, autovacuum, tanpa data pelanggan; test & tour terdaftar.
- **E-27 (opsional, menunggu Q-23 + ADR)** QR order via Super-App/BFF (`efn.pos.bff.order.create`) — opsi B §7.4; jangan dikerjakan sebelum ADR.
- **Jangan dikustom:** service charge, routing printer, model meja/denah/preset, status `pos.order`, `updateLastOrderChange`.
- **Audit modul yang ada:** `efn_kitting` tetap (guard paket vs phantom; tidak menduplikasi `pos_mrp`). Saran: menu lintas stasiun pakai `type='combo'`, bukan Kit BoM (Kit tercetak/ditiket sebagai satu menu di satu stasiun).

### Untuk efn-lead
- Hapus file sisa sesi terputus: `services/odoo/addons/efn_setup_pos/README.md.tmp.30200.6cb4dbb0441e` (duplikat README; agent ini tidak bisa menghapus file).
- ADR baru QR order ponsel (opsi §7.4) — pemilik efn-architect.
- ETL `efn_kds_ticket` → ClickHouse untuk KPI dapur (efn-data-ai-engineer) setelah E-26.

### Pertanyaan klien (P-11..P-18, `docs/08 §7.6`)
Outlet pilot & perangkat; layanan & takeaway (Q-18); stasiun & target menit (Q-19); peran split/gabung/course (Q-20); modifier berbahan (Q-21); PB1 kota/SC/include (Q-22, konsultan pajak); QR kiosk vs ponsel, bayar di kasir vs online (Q-23); printer Epson ePOS & layar KDS, kebijakan cetak cadangan (Q-24/Q-56).

### Asumsi belum terverifikasi (Gelombang 14)
Install data/demo baru belum dijalankan (penulisan field `pos_self_order` ke xmlid `pos_restaurant`; `taxes_id` ref PB1; atribut line pada template demo); picking anak combo & lot paket CK di POS; self-order menulis `last_order_preparation_change`; tombol UI rotasi token QR; HTTPS printer Epson + DNS; saklar UI course; perilaku multi-perangkat meja saat offline. Tidak ada test/CI yang dijalankan.

## Gelombang 17 — 2026-10-09 (Fase 3: penjualan online Super-App)

Detail: `docs/08_ODOO_FIT_GAP.md` §8 (F-55..F-74, temuan #36–45, spesifikasi E-28 §8.3, `efn_setup_online` §8.4, titipan Holding §8.5, default pilot §8.6, Q-ON1..Q-ON13 §8.7, usulan kontrak K-ON1..K-ON5 §8.8). Verifikasi dari source Odoo 19 CE di container dev `efn-odoo-1` + query baca-saja DB `efn_odoo` (tanpa perubahan data). Tidak ada modul yang dibuat/diubah di gelombang ini; `efn_setup_online` baru dibuat setelah E-28 ada (bergantung model kanal).

### Standar vs Custom
- **Standar CE (konfigurasi):** `sale`/`sale_stock` (SO per PT, picking outlet, faktur, nota kredit via qty negatif/retur, `client_order_ref`), pricelist per kanal, mesin pajak + fiscal position **outlet pemenuhan** (PPN RTL / PB1 FNB, ADR-027), `free_qty` per gudang outlet, `pos.category` + `image_1920` untuk katalog, jurnal/akun titipan Holding (A1), `account.cash.rounding`, API key `/json/2` + grup + record rule.
- **Tidak dipakai:** `website*` (route publik; flag/kategori/pickup website tidak tersedia), `delivery` (pilot pickup), `payment.transaction` jalur sale (1 SO per transaksi), `sale_loyalty` (dimatikan).
- **Custom wajib:** **E-28 `efn_online_order`** (kanal & outlet, flag katalog, ekspor katalog/stok/gambar, quote, place idempoten, mark_paid saga, cancel, refund parsial + settled, status outbox, kode ambil). **Opsional:** E-29 dapur F&B online (Q-ON2). **Ditunda:** ongkir/slot/3PL (BFF, Q-39/40), otomasi jurnal Holding (A2 `account_payment_interco`, PoC setelah opini pajak).

### Temuan penting (terverifikasi source 19.0)
- Memasang `sale` meng-auto-install `sale_loyalty`, `pos_sale`(+`sale_management`), `sale_mrp`, `sale_stock_product_expiry`; **`sale_loyalty.action_confirm` dapat mengubah total SO setelah pelanggan membayar** dan `loyalty.program.sale_ok` default True (program POS yang ada ikut berlaku) → `sale_ok=False` + assert total di E-28.
- `action_cancel` menolak SO `locked` → jangan aktifkan *Lock Confirmed Sales*.
- API key `/json/2`: masa berlaku maks dari `res.groups.api_key_duration`, **default 1 hari** → grup user teknis wajib mengatur durasi + runbook rotasi.
- IDR dev `rounding=0.01` → total bersen vs QRIS/VA Rupiah bulat → keputusan Finance (Q-ON6).
- `account_payment_interco` (CE 19) sudah mengimplementasikan kliring agen penagih lintas company — kandidat otomasi ADR-019 opsi A, [B] perlu PoC.
- `sale` membawa route portal publik `/my/orders` (aman selama Odoo privat; BFF tidak boleh meneruskan URL portal).

### Rekomendasi untuk efn-odoo-engineer
- **E-28 `efn_online_order`** sesuai §8.3 (model, metode, alur saga, keamanan, test). Perkiraan 4–5 minggu. Prasyarat lingkungan: `sale_stock` terpasang di DB dev/CI (akan memicu auto-install di atas — jalankan seluruh test `efn` setelahnya).
- Tambahkan entri `RPC_ALLOWLIST` (efn_core/tests/test_rpc_surface.py) — **13 metode**: `efn.online.channel`: `efn_online_catalog`, `efn_online_stock`, `efn_online_image`, `efn_online_quote`; `efn.online.order`: `efn_online_place`, `efn_online_mark_paid`, `efn_online_cancel`, `efn_online_refund`, `efn_online_refund_settled`, `efn_online_status` (semua `_efn_online_check_caller`), `action_efn_ready` (grup stok + check_access), `action_efn_retry_events` (sales manager); `efn.online.handover.wizard`: `action_confirm` (grup stok + check_access order).
- Jangan dikustom: harga/pajak, picking/reservasi/retur, faktur/nota kredit, state SO.

### Untuk efn-lead
- **Kontrak** K-ON1..K-ON5 (§8.8) untuk docs/04 + baris identitas Odoo per service di §7.3.
- **efn-devsecops:** router internal path-allowlist `/json/2/efn.online.*` (mTLS CN `api-gateway`) di depan `odoo-tls`; secret `odoo_api_key_online_rtl`/`_fnb`; runbook pembuatan & rotasi API key user teknis.
- **efn-bff-engineer:** klien Odoo (`/json/2`, TLS CA internal, timeout, retry hanya untuk langkah idempoten), saga per SO, cache katalog pull 5 menit, BFF **tidak** menghitung harga/pajak (hanya Σ `amount_payable`).
- **efn-architect:** catatan di ADR-019 (opsi A1 vs A2 `account_payment_interco`; F&B online = SO atau `pos.order`), keputusan Q-ON2 bersama ADR-032.
- **efn-security-reviewer:** permukaan `/json/2` user teknis (`base.group_user` masih bisa membaca model umum bila path tidak difilter), kode ambil (brute force), route portal `sale`.

### Keputusan/asumsi default pilot (configurable, §8.6)
PT Retail saja, pickup 1 toko, delivery & FNB online mati; cek stok tanpa reservasi, batas bayar 15 menit, stok kurang setelah bayar → batal + refund penuh porsi PT; faktur saat bayar; partner generik tanpa PII; promo = pricelist; harga tampil termasuk pajak; pembulatan kas Rp1; jurnal titipan A1 manual di Holding.

### Pertanyaan klien (Q-ON1..Q-ON13, `docs/08 §8.7`)
PT/outlet pilot & SKU; jalur F&B online; delivery/ongkir/slot (Q-39/40); reservasi stok & kebijakan stok kurang; harga termasuk pajak; pembulatan Rupiah; faktur ber-NPWP; waktu faktur; MDR & settlement titipan; mekanik promo online; kebijakan refund; katalog per PT/brand; pesanan tidak diambil.

### Asumsi belum terverifikasi (Gelombang 17)
Compute `sale.order.new()` untuk quote di 19; perilaku `account.cash.rounding` pada pembayaran jurnal titipan; retur picking pickup → nota kredit `final=True`; alur A2 `account_payment_interco` lintas company; pembuatan API key user teknis lewat shell & batas `api_key_duration`; dampak auto-install `pos_sale`/`sale_loyalty` terhadap test POS yang ada; OCA `sale_order_type` 19.0 (tidak di-WebFetch). Tidak ada test/CI yang dijalankan.

## Gelombang 17 lanjutan — 2026-10-10 (G17-QA-04 label pajak, demo `--with-demo`)

### G17-QA-04 — label pajak ke pelanggan
- **Akar masalah (terverifikasi di kode & DB dev):** `efn_online_order` mengambil label baris dari `account.tax.invoice_label` (fallback `name`) dan label subtotal dari `tax_totals.group_name` = `account.tax.group.name`. Field yang dipakai **sudah benar** — tidak ada perubahan modul gap. Template `l10n_id` 19.0 mengisi `invoice_label = "12%"` (bukan kosong seperti tertulis di laporan QA) untuk `tax_ST4` yang tarif efektifnya 11% (12% × DPP 11/12), dan nama grup Inggris "Non-luxury Good Taxes" — yang terakhir itulah yang dilihat QA di `tax_groups`.
- **Solusi (data-only, ADR-027):** `efn_setup_company/data/account_tax_label_data.xml` (versi 19.0.1.3.0, `noupdate="1"`, dimuat setelah CoA) menimpa nilai en_US di 4 PT: `invoice_label` pajak penjualan (`PPN 11%`, `PPN 12%`, `PPnBM 20%`, `PPN 0%`, `Bebas PPN`) dan nama grup (`PPN 11%`, `PPN 12%`, `PPnBM`, `PPN 0%`, `Bebas PPN`, `Pajak`). PB1 F&B sudah `PB1`. README §"Label pajak ke pelanggan".
- **Test:** `efn_setup_company/tests/test_tax_labels.py` (4 test: label pajak, nama grup, pajak default per PT termasuk PB1, `group_name` dari mesin `tax_totals`).
- **Bahasa id_ID — rekomendasi: JANGAN dipasang di gelombang ini (keputusan lead/klien).** Alasan: (1) memasang bahasa memuat terjemahan semua modul dan mengubah teks yang dilihat BFF (`lang=id_ID`) untuk UoM/kategori/dll. — perlu cek kontrak katalog; (2) bila id_ID diaktifkan, `account.chart.template._load_translations` (overwrite=False) akan mengisi versi id_ID nama grup dari template ("Pajak Barang", …) karena modul ini hanya menulis en_US → regresi G17-QA-04 di jalur BFF. `update_field_translations` menolak bahasa yang belum aktif dan `context lang` tidak valid memicu UserError, jadi tidak bisa disiapkan sebelumnya dari data. Bila id_ID diputuskan: aktifkan **sebelum** install `efn_setup_company` di DB baru lalu efn-odoo-engineer menambahkan penulisan terjemahan id_ID, atau admin mengisi terjemahan id_ID setelah aktivasi (README). Staf POS memakai bahasa user masing-masing; tour/test lama memakai user en_US sehingga tidak terdampak oleh aktivasi bahasa saja (belum diuji).
- **DB dev `efn_odoo` TIDAK diubah** (noupdate → upgrade tidak menerapkan). Untuk smoke ulang, lead dapat menerapkan sekali lewat UI atau `odoo shell` (tulis `invoice_label`/`name` en_US pada record `account.<company_id>_tax_ST4`, `..._l10n_id_tax_group_non_luxury_goods`, dst.).

### Demo `--with-demo`
- `efn_setup_pos/demo/pos_retail_demo.xml` (19.0.1.5.1): barcode tomat `2100001000004` bentrok dengan demo CE `point_of_sale` "Newspaper Rack" → diganti blok kode 09xxx: `2109001000007` (label 1,250 kg = `2109001012505`, check digit EAN-13 dihitung ulang). README diperbarui.
- Ditemukan saat verifikasi: `efn_setup_mrp/demo/mrp_demo.xml` (19.0.1.0.1) gagal — company FNB (dibuat sebelum `stock` terpasang) tidak punya gudang (orderpoint `warehouse_id` NOT NULL), dan produk paket `efn_kitting` jatuh ke `base.main_company` (crossover company). Perbaikan data-only: gudang demo `Central Kitchen (DEMO)` (CK01) + orderpoint memakai company **produk** paket dan `warehouse_id` eksplisit.
- **Verifikasi:** DB baru `--with-demo -i efn_setup_company,efn_setup_pos,efn_setup_mrp,efn_setup_audit,efn_setup_online` → exit 0, tanpa ERROR/"demo data failed"; barcode baru ada, 6 orderpoint, label pajak RTL sesuai tabel.

### Hasil test (container `efn-odoo:ci-g17qa` + addons working tree, PostgreSQL 16.15 terpisah; DB dev tidak disentuh)
- Suite efn penuh (14 modul `EFN_TEST_MODULES` CI, `--test-tags efn`), DB baru: **0 failed, 0 error dari 405 test** (termasuk 4 test baru `TestTaxLabels`).
- Run pertama (tepat setelah Docker Desktop restart, working tree sedang diubah agent lain) mencatat 1 fail + 1 error di `efn_online_order` (`test_ops_cancel_from_sale_order_ui`, `test_handover_other_company_denied` — hak akses grup, tidak terkait pajak); tidak terulang pada run ulang DB baru. Baseline HEAD (tanpa perubahan ini): 0 failed dari 391.
- Load demo DB baru `--with-demo`: exit 0, tanpa ERROR.
- Belum: `efn_setup_security` install-after (tidak disentuh), tour POS `odoo-tour`, struk POS dengan label baru (dilihat di UI), smoke online ulang di DB dev.

## Gelombang 18 — 2026-10-10 s.d. 12 (fit-gap W-03, W-01, W-24, W-20, W-25; konfigurasi W-22, W-10, W-11, W-25, W-03)

Detail: `docs/08_ODOO_FIT_GAP.md` §9 (PoC W-03 §9.1, desain W-01 + usulan kontrak K-18-1..K-18-8 §9.2/§9.5, E-31 §9.3, GS1 §9.4, W-25 + usia 17+ §9.5, konfigurasi §9.6, gap E-30..E-34 §9.7, Q-F18-1..7 §9.9). PoC nyata di PostgreSQL 16.15 terpisah (network `efn-fg18`). DB dev `efn_odoo` tidak disentuh. Container, network, dan DB uji sudah dihapus.

### Hasil PoC W-03 (IDR 0,01 vs Rp1, transaksi identik)
- Faktur PPN exclude: 98.388,18 → **98.388**. Faktur PPN include: total 81.963, PPN 8.122. **e-Faktur Coretax menghasilkan Σ PPN 8.123 (selisih Rp1)** karena DPP 11/12 dibulatkan per baris (Q-F18-2).
- Faktur F&B dengan SC 5 % + PB1 10 %: 89.679,98 → **89.680**. SC tetap masuk DPP PB1.
- SO + pricelist member 5 %: 52.273,75 → **52.274**. Harga satuan tetap 2 desimal.
- POS tunai: **84.346,25** (tidak bisa dibayar tunai) → **84.346**. Tutup sesi seimbang di kedua DB.
- Odoo menolak menaikkan presisi bila sudah ada jurnal IDR (`account/models/res_currency.py` `write`), jadi perubahan hanya berlaku di DB baru. Demo `l10n_id` sudah membuat jurnal IDR sebelum `efn_setup_company` terpasang, sehingga nilai di XML membuat install `--with-demo` gagal. Karena itu nilai dipindah ke `pre_init_hook` kecil: pada DB demo hook melewati perubahan dengan WARNING.
- Cash rounding POS dan online tidak diperlukan lagi.

### Desain W-01 (ringkas)
"Poin EFN" menjadi `pos.payment.method` per PT: jurnal bank, `payment_method_type='none'`, `outstanding_account_id` = akun liabilitas atau kliring poin. **PoC: Dr Liabilitas Poin 5.000 / Cr Piutang PoS 5.000; penjualan dan PPN tetap atas harga penuh; sesi ditutup sukses.**
Isi E-30:
- penanda `efn_is_loyalty_points`;
- tombol kasir menambah baris pembayaran yang terkunci;
- validasi server: Σ = `value_idr` (toleransi 0), partner, batas %, reservasi;
- larangan selisih saat tutup sesi;
- refund poin negatif proporsional + `points_restored_value`;
- earn = `eligible × (1 − bayar_poin/total)` (menunggu keputusan Finance);
- offline tetap mati.

Usulan kontrak §9 v6: K-18-1..K-18-7, ditambah K-18-8 (`age_confirmed` untuk POS `pos_enroll`).

### Gap untuk efn-odoo-engineer
| Kode | Isi | Estimasi |
|---|---|---|
| E-30 | Poin EFN = metode bayar (W-01) | 1,5–2 minggu |
| E-31 | `efn_pos_refund_origin`, refund ke metode asal (W-24) | 1 minggu |
| E-32 | (opsional) pricelist member hanya untuk member (R-B06) | 2–3 hari |
| E-33 | (opsional) cek kedaluwarsa lot + AI harga GS1 di POS (W-20) | 1,5–2 minggu |
| E-34 | OTP `pos_enroll` + centang pernyataan usia 17+ → `age_confirmed` ke BFF (W-25) | 2 hari (+0,5 BFF) |

### Konfigurasi diterapkan
| Modul | Versi | Perubahan |
|---|---|---|
| `efn_setup_company` | 19.0.1.4.0 | `hooks.py` `pre_init_hook`: IDR `rounding = 1` di DB baru tanpa jurnal. Test `test_currency_idr.py` (3) |
| `efn_setup_pos` | 19.0.1.6.0 | depends baru `efn_pos_kds` dan `efn_pos_otp_gate`. Data: `pos_kds_defaults.xml` (warn 5 / late 10), `pos_cashier_groups.xml` (grup "Kasir: boleh membuat pelanggan"). Demo: `pos_kds_demo.xml` (Bakery/Pastry + Bar Kopi, expo diarsipkan), `pos_promo_demo.xml` (3 promo standar). Test `test_g18_config.py` (5). README diperbarui |
| `efn_setup_online` | 19.0.1.1.0 | `api_key_duration` grup kanal = 35 (noupdate=0). **`base.group_user.api_key_duration = 0`** (temuan devsecops: durasi efektif = maksimum dari semua grup; CE memberi Internal User 90 hari). Test `TestEfnOnlineApiKeyDuration` (4); test lama diperketat ke = 35 |
| `scripts/smoke/odoo_online_smoke.py` | — | default `SMOKE_KEY_DAYS` 85 → 30 (85 hari kini ditolak Odoo) |

**Dampak W-11 ke user manusia:**
- User internal tanpa grup berdurasi kini hanya bisa membuat API key maksimal 1 hari (fallback CE).
- Admin/Settings dan `sudo()` tetap tidak dibatasi (`is_system`). Karena itu rotasi key wajib memakai `with_user(<user teknis>)`; skrip `efn-rotate-online-key` sudah demikian.
- Tidak ada layanan EFN lain yang memakai API key user manusia (dicek dengan grep `odoo_api_key*`).
- `-u base` mengembalikan nilai 90, tetapi `efn_setup_online` ikut ter-upgrade sebagai modul dependen sehingga nilai dinormalkan lagi.

### Hasil verifikasi (image `efn-odoo:ci` / `efn-odoo:ci-test` + addons working tree, DB baru)
- Suite efn penuh pola CI (15 modul `EFN_TEST_MODULES` termasuk `efn_online_pos_sale`, `--test-tags efn`): **0 failed, 0 error dari 430 test**. Tes baru: IDR 3, G18 POS 5, durasi API key 4. Hasil DB: IDR `rounding` 1,0; hanya grup kanal yang punya durasi (35).
- `efn_setup_security` di-install setelah suite di DB yang sama: exit 0, tanpa ERROR.
- Tour POS (`efn_tour`, 5 modul CI): **0 failed, 0 error dari 20 test**. Tour dijalankan sebelum perubahan W-11 `base.group_user`; DB tour tidak memasang `efn_setup_*`, jadi tidak terdampak.
- Install `--with-demo` untuk 5 modul `efn_setup_*`: exit 0, tanpa ERROR. Stasiun KDS 5/10, expo diarsipkan, 3 promo demo terbentuk. Di DB demo, IDR tetap 0,01 (WARNING hook, sesuai desain).
- ruff check + format: bersih (170 file).

### Belum / catatan untuk lead
- DB dev `efn_odoo` tetap IDR 0,01 sampai dibuat ulang. W-11 ikut diterapkan saat `-u efn_setup_online`. DB produksi wajib dibuat dari versi ini tanpa demo; cek `SELECT rounding FROM res_currency WHERE name='IDR'` = 1.
- Nomenklatur GS1 untuk PT Retail **belum** diterapkan; menunggu model scanner/timbangan dan label supplier, lalu PoC scan di perangkat nyata.
- Data metode bayar "QRIS (statis)" dan "Poin EFN" belum dibuat: QRIS butuh akun merchant, Poin EFN butuh field dari E-30.
- Belum diuji: smoke online BFF di atas IDR Rp1 (bff/QA), struk POS di UI, rumus dua tahap `account_tax_python` (Q-F18-2), FEFO pada picking POS tanpa lot.
- `scripts/check-odoo-deps.sh` tidak bisa dijalankan lokal karena butuh Python di host. Depends baru hanya `efn_*`.
- Pertanyaan klien: Q-F18-1..7 (`docs/08 §9.9`).

### Gelombang 18 lanjutan — 2026-10-12 (L-23 harga member = promo OTP; draf data Poin EFN)
- **L-23 (Q-F18-7)** di `efn_setup_pos` 19.0.1.7.0:
  - `data/pos_member_price_data.xml` berisi template promo *Harga Member 5% - wajib OTP (PLACEHOLDER)* untuk PT Retail: `promotion` otomatis, diskon 5 % per order, `efn_requires_otp`.
  - Template **nonaktif** (`active=False`, `pos_ok=False`). Alasannya, constraint `efn_pos_otp_gate` mewajibkan program OTP ditautkan ke `pos_config_ids` POS kasir, sedangkan pos.config tidak dibuat lewat XML. Nonaktif juga mencegah `sale_loyalty` menerapkannya ke SO.
  - Pricelist demo *Harga Member* diarsipkan. Checklist README: `available_pricelist_ids` = pricelist umum saja; promo diaktifkan per outlet.
  - Test `TestEfnMemberPriceOtp` (3): bentuk template, aktivasi wajib POS kasir, tidak ada pricelist "member" yang ditawarkan ke POS mana pun.
  - E-32 tidak diperlukan.
  - Sisa risiko: admin POS masih bisa menambahkan pricelist apa pun ke POS (kontrol prosedural + review akses).
- **Draf data Poin EFN (W-01/E-30):** `efn_setup_pos/data/drafts/pos_loyalty_points_payment.xml.draft`, **tidak dimuat** manifest.
  - Isi per PT Retail & PT F&B: jurnal bank PEFN, akun `liability_current` PLACEHOLDER (Q-F18-3), metode "Poin EFN" (`none`, bukan tunai, `outstanding_account_id` = akun poin).
  - Penanda field E-30 masih `__E30_FLAG_FIELD__`. Working tree E-30 saat ini memakai `efn_is_loyalty_points`, tetapi belum final, jadi tidak saya kunci.
  - Draf (tanpa baris penanda) sudah dicoba dimuat via `convert_file` di DB uji lalu di-rollback: OK, jurnal bank, akun liabilitas, bukan tunai.
  - Langkah aktivasi ada di komentar file.
- **Verifikasi** (DB baru, PostgreSQL 16.15 terpisah). Working tree saat itu memuat pekerjaan engineer lain yang sedang berjalan: E-30 di `efn_pos_loyalty_ledger`, E-31 `efn_pos_refund_origin` baru, perubahan `efn_pos_otp_gate`, dan test `efn_core`.
  - **Working tree penuh:** suite 440 test → 2 failed + 2 error, tour 3 failed dari 20. Semua kegagalan ada di kode E-30 yang sedang dikerjakan engineer: `test_rpc_surface` (metode publik baru belum di-allowlist), 3 test `test_loyalty_ledger`, 3 tour Poin EFN (tombol `.efn-redeem-points` sudah tidak ada). **Tidak ada yang terkait `efn_setup_*`.**
  - **Isolasi** (HEAD untuk semua modul + `efn_setup_company`/`efn_setup_pos`/`efn_setup_online` dari working tree): suite **0 failed / 0 error dari 433 test**, `efn_setup_security` install-after exit 0, tour **0 failed / 0 error dari 20 test**, ruff bersih.
  - Container, network, DB uji, dan salinan addons isolasi sudah dihapus.

### Gelombang 18 lanjutan 2 — 2026-10-12 (data Poin EFN final setelah E-30 `fe56a07`)
- `efn_setup_pos` 19.0.1.8.0, depends baru `efn_pos_loyalty_ledger`:
  - `data/pos_loyalty_points_payment.xml` (noupdate), per PT Retail & PT F&B: jurnal bank *Poin EFN* (PEFN, tanpa rekening), akun `liability_current` 21990091 *Liabilitas / Kliring Poin EFN* (PLACEHOLDER Q-F18-3) sebagai `outstanding_account_id`, dan metode "Poin EFN" (`efn_is_loyalty_points=True`, `payment_method_type='none'`, tanpa split).
  - Penautan ke POS yang redeem-nya aktif (pos.config tidak dibuat lewat XML) ada di `models/pos_config_points.py`:
    - otomatis saat POS dibuat atau diubah dengan `efn_loyalty_redeem_enabled`;
    - `data/pos_loyalty_points_link.xml` (noupdate=0) menautkan ulang setiap install/upgrade;
    - POS dengan sesi terbuka dilewati (dicatat di log); hanya menambah, tidak pernah menghapus.
  - Override `_default_payment_methods`: default CE memasang semua metode non-tunai company ke POS baru (ditemukan dari test yang gagal), sehingga Poin EFN dikecualikan dan POS tanpa redeem/kiosk tidak mendapatkannya.
  - Draf `.xml.draft` dihapus. README (tabel data + checklist) dan `docs/08 §9.6` diperbarui.
  - Test `TestEfnPointsPaymentMethodData` (4): metode per PT, ditautkan saat dibuat, ditautkan saat redeem diaktifkan belakangan (bukan sebelumnya, dan bukan metode PT lain), fungsi upgrade menautkan ulang.
- **Hasil uji** (working tree, DB baru, PostgreSQL 16.15 terpisah):
  - suite efn penuh 16 modul (termasuk `efn_pos_refund_origin`): **0 failed / 0 error dari 456 test**;
  - `efn_setup_security` install-after: exit 0;
  - tour 6 modul CI: **0 failed / 0 error dari 22 test**. Tour dijalankan sebelum override `_default_payment_methods`; DB tour tidak memasang `efn_setup_pos`, jadi tidak terdampak;
  - ruff bersih (182 file).
- Container, network, dan DB uji sudah dihapus.
- Catatan: rename draf sempat ter-stage oleh `git mv`; index sudah dikembalikan (`git restore --staged`), sehingga perubahan hanya ada di working tree. Working tree juga berisi `efn_pos_loyalty_ledger/static/tests/tours/efn_loyalty_tour.js` yang diubah agent lain (bukan saya).
