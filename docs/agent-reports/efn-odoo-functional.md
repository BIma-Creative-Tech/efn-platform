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
