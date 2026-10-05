# FIT-GAP ODOO CE 19 — KEBUTUHAN FASE 1–2

- **Tanggal:** 2026-10-05 · **Revisi Gelombang 5:** 2026-10-06 (pajak configurable, payment gateway-agnostic, privasi UU PDP) · **Penyusun:** efn-odoo-functional
- **Metode:** verifikasi source primer `raw.githubusercontent.com/odoo/odoo/19.0/...` dan `OCA/<repo>/19.0` (WebFetch). Melengkapi `docs/06_ODOO_CE19_MODULE_AUDIT.md` (ketersediaan modul) dengan **setting/field persis** dan keputusan konfigurasi.
- **Prinsip:** konfigurasi CE dulu → OCA → custom (ADR-015, D-15). Semua ADR masih *Proposed*.
- **Modul konfigurasi:** `efn_setup_company`, `efn_setup_security`, `efn_setup_audit`, `efn_setup_pos`, `efn_setup_mrp` (`services/odoo/addons/`). **Belum satu pun di-install** (tidak ada Odoo lokal) — validasi di CI/PoC.

> **Ringkasan manajemen.** Dari 30 kebutuhan fase 1–2, **19 tertutup fitur standar Odoo Community 19** dengan konfigurasi, **3 oleh modul OCA**, dan **8 perlu dibangun** (verifikasi OTP promo, poin member, layar dapur KDS, QC intake petani, pembayaran online di platform EFN, **pembayaran QRIS kasir lewat gateway pilihan EFN** (dua baris: F-21 dan F-30), **penghapusan data pelanggan UU PDP di Odoo**). Gelombang 5 sesuai keputusan user: (1) **tarif PB1 per kota dan harga menu include/exclude kini diatur admin lewat menu standar** (pajak + fiscal position per outlet), tanpa developer; (2) **kasir siap untuk penyedia payment gateway mana pun** — Odoo hanya bicara ke platform EFN (BFF), penyedia dipilih di BFF; sementara kasir memakai tunai dan EDC manual; (3) **permintaan hapus data pelanggan dari Super-App ikut menganonimkan data pelanggan di Odoo** secara otomatis.

Legenda verifikasi: **[V]** field/xmlid terverifikasi di source 19.0 · **[M]** modul terverifikasi ada, detail belum · **[B]** belum terverifikasi (uji di PoC) · Kategori: **S** Standar CE · **O** OCA · **C** Custom.

## 1. Tabel fit-gap

| ID | Kebutuhan | Kat. | Standar CE (modul + setting persis) | OCA | Custom (alasan) | Konfigurasi diterapkan | Verifikasi | Keputusan klien |
|---|---|---|---|---|---|---|---|---|
| F-01 | Multi-company 4 PT (EFN/RTL/FNB/FARM), 1 DB | S | `base` `res.company` (tanpa field kode → `efn_core.efn_brand_code`; `efn_business_type` compute dari kode). **Jangan** pakai `parent_id` (*Branches*) | `base_multi_company`, `product_multi_company` bila katalog per PT (Q-8) | — | `efn_setup_company`: 4 company, IDR, negara ID, admin akses | [V]; [B] install | Q-1, Q-8 |
| F-02 | CoA & pajak: PPN (RTL/FARM) vs PB1 (FNB) | S | `l10n_id` template `id`: `tax_ST4` (12% × 11/12) default penjualan, dll. **PB1 & akun liabilitas PB1 tidak ada** di template → grup pajak PB1 + pajak PB1 default; `res.company.account_sale_tax_id` PT F&B = PB1 | OCA/l10n-indonesia 19.0 kosong | — | `efn_setup_company` **data**: grup PB1 + *PB1 Default 10% (PLACEHOLDER)* + default PT F&B (noupdate, editable) | [V]; [B] tarif | Q-22, Q-2 (akun PB1 = go-live blocker) |
| F-03 | Inter-company SO/PO/invoice | O | Enterprise-only `account_inter_company_rules` (404 CE) | `purchase_sale_inter_company`, `account_invoice_inter_company` | Glue stok bila PoC gagal | Belum (Q-3) | [V] field OCA; [B] stok | Q-3, Q-4 |
| F-04 | POS retail: barcode, sesi, offline | S | `point_of_sale`; `l10n_id_pos` (auto-install) | `pos_session_sequence`, `pos_edit_order_line` | — | `efn_setup_pos` | [M]; **[B] offline** | Q-55, Q-56 |
| F-05 | Cash control | S | `pos.config.cash_control` (compute dari metode tunai); metode kas **satu outlet** (`_check_cash_method_single_shop`), dibuat otomatis saat pos.config baru | — | — | Checklist README `efn_setup_pos` | [V] | Q-27 |
| F-06 | Retur/refund POS | S | Refund bawaan (`refunded_orderline_id`); hak POS Manager | — | — | Filter audit | [V]; [B] approval UI | Q-27, R-O05 |
| F-07 | POS restoran: meja, printer dapur, split bill | S | `pos_restaurant` (`floor_ids`, `iface_splitbill`, `iface_printbill`, `default_screen`); `pos.printer` | `pos_printing_qztray` | KDS → F-26 | demo `efn_setup_pos` | [V]; [B] ePOS | Q-18, Q-19, Q-20, Q-24 |
| F-08 | Kiosk / QR self-order | S | `pos_self_order` (`self_ordering_mode`, `self_ordering_service_mode`, `self_ordering_pay_after='meal'`) | — | — | Checklist README | [V] | Q-23 |
| F-09 | Promo & loyalty mekanik toko | S | `loyalty` + `pos_loyalty` | — | OTP → F-24 | Audit (F-17) | [V] | Q-28 |
| F-10 | Member/pelanggan | S | `res.partner.phone`; kasir tanpa `base.group_partner_manager` | `pos_partner_birthdate` | Identitas member di BFF (ADR-020) | README `efn_setup_pos`; audit | [V] ACL | Q-12, Q-16 |
| F-11 | Multi-harga | S | `product.pricelist`, `pos.config.available_pricelist_ids` | — | — | Audit pricelist | [M] | Q-9 |
| F-12 | MRP kitting CK | S | `mrp` BoM normal + MO; `pos_mrp` | `mrp_multi_level` | Guard `efn_kitting` | `efn_setup_mrp` | [V] | Q-35, Q-21 |
| F-13 | Route Manufacture + reordering | S | route gudang `manufacture_to_resupply`; `stock.warehouse.orderpoint` | — | — | `efn_setup_mrp` demo | [V] | Q-35 |
| F-14 | Transfer CK → outlet | S | resupply (multi-step routes); beda PT → F-03 | — | — | `efn_setup_mrp` | [V]; [B] | Q-30, Q-3 |
| F-15 | Lot/expiry, FEFO | S | `product_expiry`, `removal_fefo` | — | — | `efn_setup_mrp` | [V]; [B] POS | Q-34 |
| F-16 | QC intake farming | C | Receipt 2/3 langkah | `quality_control_oca` (evaluasi) | `efn_agri` (K-07) | — | [M] | Q-31..Q-33 |
| F-17 | Audit log | O | — | `auditlog` 19.0 | — | `efn_setup_audit` | [V] | Q-49 |
| F-18 | 2FA + password | S | `auth_totp.policy = employee_required`, `auth_password_policy.minlength` | — | — | `efn_setup_security` | [V] | Q-54 |
| F-19 | Laporan konsolidasi | O | (Enterprise) | `mis_builder`, `account_financial_report` | — | Belum | [M] | Q-6 |
| F-20 | e-Faktur Coretax | S | `l10n_id_efaktur_coretax` | — | — | via `l10n_id` | [M] | konsultan pajak |
| F-21 | QRIS di kasir (**revisi G5**) | C | CE: `payment_method_type='qr_code'` + `qr_code_method='id_qr'` = **satu provider (qris.online)**, API key di `res.partner.bank` (DB). **Tidak dipakai**: provider belum ditentukan & secret harus di `/run/secrets` | — | → F-30 (gateway-agnostic lewat BFF) | `efn_setup_pos` sengaja tidak mengonfigurasi `id_qr` | [V] | ADR-016 (provider) |
| F-22 | Pembayaran online Super-App (**revisi G5**) | C (BFF) | `payment_xendit`/`pos_online_payment` = Odoo sebagai pemegang provider; **tidak dipakai** karena checkout & provider berada di BFF (§8.3) | — | Adapter `PaymentGateway` di BFF (mock/xendit/midtrans) | — | [M] | ADR-016, KYB |
| F-23 | Anti-fraud harga/diskon | S | `restrict_price_control`, `manual_discount`, `pos_hr`, `price_type` | — | — | `efn_setup_pos` | [V]; [B] | Q-27, Q-28 |
| F-24 | Promo wajib OTP | C | (coupon tanpa batas percobaan) | — | `efn_pos_otp_gate` | — | [V] | ADR-017 |
| F-25 | Poin member di POS | C | `loyalty` tidak dipakai (ADR-020) | — | Integrasi POS ↔ ledger BFF | — | — | Q-13 |
| F-26 | KDS | C | Enterprise (404) | — | `efn_pos_kds` fase 2 | Printer dapur | [V] 404 | Q-19 |
| F-27 | **PB1 per kota & include/exclude configurable** (G5) | S | Pajak per tarif di grup PB1 + `account.tax.original_tax_ids` ("Replaces") + `account.fiscal.position.tax_ids` (`tax_map`); `pos.config.default_fiscal_position_id`; `res.company.account_price_include` + `account.tax.price_include_override`; takeaway: `pos.preset.fiscal_position_id` | — | — | `efn_setup_company` data (PB1 default) + demo (kota contoh); panduan admin README | [V] field; [B] perilaku POS | Q-22 (tarif per kota, include/exclude) |
| F-28 | **Service charge & dasar PB1** (G5) | S | Tidak ada fitur SC di CE 19 (Apps Store pihak ketiga; OCA/pos 19.0 tidak ada). Opsi standar: "pajak" SC (`sequence` < PB1, `include_base_amount=True`; PB1 `is_base_affected=True`) dipasang di produk menu | — | — (custom hanya bila SC harus per-meja/per-preset dinamis) | `efn_setup_company` demo SC 5% | [V] field; [B] posting & struk | Q-22 (SC di DPP PB1?) |
| F-29 | **Penghapusan data pelanggan (UU PDP) di Odoo** (G5) | C | `privacy_lookup` (auto-install, depends `mail`): wizard **manual** cari nama+email (tanpa HP), hanya arsip/hapus (bukan anonimisasi), `privacy.log` sebagai jejak | — | `efn_privacy`: `res.partner.efn_member_hash` + cron tarik/selesaikan erasure §8.4 + anonimisasi partner & salinan kontak | — (lihat §4.2) | [V] privacy_lookup | DPO: retensi, faktur ber-NPWP |
| F-30 | **Pembayaran POS gateway-agnostic** (QRIS, G5) | C | Kerangka terminal standar: `pos.payment.method.payment_method_type='terminal'` + `use_payment_terminal` (selection dari `_get_payment_terminal_selection`), frontend `PaymentInterface` + `register_payment_method`; layar pelanggan membaca `order.qrPaymentData` | — | `efn_pos_payment_gateway` (memanggil BFF §8.3; tidak ada modul CE/OCA untuk BFF sendiri) | `efn_setup_pos`: EDC manual per PT, tanpa `id_qr` | [V] kerangka; [B] `qrPaymentData` | ADR-016, ADR-019 |

**Rekap:** Standar **19** · OCA **3** · Custom **8** (total 30). F-21/F-22 berpindah dari Standar ke jalur BFF (F-22 di BFF, F-21 → F-30 di Odoo); F-27/F-28 baru dan Standar.

## 2. Temuan penting dari verifikasi source 19.0
1. Route Manufacture bukan route produk (`product_selectable=False`).
2. **PB1 & akun liabilitas pajak daerah tidak ada di `l10n_id`** (`account.account-id.csv` hanya VAT/PPh payable). Template tidak men-set `account_price_include` (default `tax_excluded`).
3. **Fiscal position 19.0 berubah**: model `account.fiscal.position.tax` tidak ada; pemetaan = pajak tujuan punya `original_tax_ids` (src) dan masuk `fiscal_position.tax_ids`; `map_tax` memakai `tax_map[src.id]`. Fiscal position tanpa pajak membuang pajak yang terikat fiscal position lain.
4. `account.tax.price_include` kini **compute** dari `price_include_override` (`tax_included`/`tax_excluded`/kosong) dan `company_price_include`.
5. **POS presets** (`pos.preset`: `fiscal_position_id`, `pricelist_id`) menggantikan takeaway lama; preset **tanpa `company_id`**.
6. 2FA via `auth_totp.policy`; QRIS CE = qris.online (API key di DB); kiosk hanya *pay after meal*; kasir tanpa *Contacts: Creation* tidak bisa ubah kontak.
7. **Terminal pembayaran 19.0**: `PaymentInterface` di `@point_of_sale/app/utils/payment/payment_interface` (`sendPaymentRequest(uuid)`, `sendPaymentCancel(order, uuid)`, `sendPaymentReversal(uuid)`, `close()`, `supports_reversals`, `fastPayments`), registrasi `register_payment_method("<key>", Cls)` dari `@point_of_sale/app/services/pos_store` (pola `pos_adyen`: RPC `this.pos.data.silentCall("pos.payment.method", ...)`, polling `setInterval`, cek `has_group('point_of_sale.group_pos_user')` di server).
8. **`pos.order` menyimpan salinan kontak** `email` dan `mobile` (compute stored, readonly=False) → harus ikut dianonimkan saat erasure.
9. `privacy_lookup` tidak mencari nomor HP dan tidak menganonimkan; `privacy.log.create` menganonimkan `anonymized_name/email` (email tanpa `@` memicu bug: `_anonymize_email` *mengembalikan* UserError) → isi email placeholder valid.
10. `restaurant.floor` tanpa `company_id`; `pos.printer` wajib `company_id`.

## 3. Yang belum terverifikasi (PoC sandbox CE 19)
Install kelima modul `efn_setup_*`; `try_loading` di data XML; atribut `search` dengan `ref()` untuk jurnal EDC; POS offline; printer ePOS; FEFO di POS; inter-company OCA; `restrict_price_control` vs diskon; level `pos_hr`; modul yang meng-imply `group_partner_manager`; retensi `auditlog`; **penerapan default fiscal position & preset di frontend POS; `include_base_amount` SC di POS; posting pajak tanpa akun; bentuk objek `order.qrPaymentData` (penulisnya di `pos_store.showQR` tidak terbaca utuh); menu UI pajak/fiscal position di CE**.

## 4. Spesifikasi gap untuk efn-odoo-engineer (Gelombang 5)

### 4.1 `efn_pos_payment_gateway` (F-30) — QRIS POS lewat BFF, provider-agnostic
- **Lisensi/depends:** LGPL-3; `point_of_sale`, `efn_core` (klien BFF, `efn_brand_code`). Tanpa `pos_restaurant`, tanpa `payment_*`, tanpa `l10n_id_pos`.
- **ADR/kontrak:** ADR-016 (provider, Proposed), ADR-019, docs/04 §8.3, §2 (service key), CLAUDE.md (PAN, offline).
- **Backend `pos.payment.method` (inherit):**
  - `_get_payment_terminal_selection()` → `super() + [('efn_gateway', 'EFN Payment Gateway (QRIS)')]`. Dipakai dengan `payment_method_type='terminal'`, `use_payment_terminal='efn_gateway'`. **Tidak** menambah field API key/secret (secret provider hanya di BFF).
  - `@api.constrains`: metode `efn_gateway` wajib `company_id.efn_brand_code`, jurnal `type='bank'` (atau jurnal clearing sesuai ADR-019), `split_transactions=False`, mata uang jurnal IDR/kosong.
  - RPC (pola `proxy_adyen_request`; tolak `AccessDenied` bila `not env.su and not user.has_group('point_of_sale.group_pos_user')`; `ensure_one`; cek metode milik `pos.config` sesi aktif user):
    - `efn_gateway_create(order_uuid, payment_uuid, amount, pos_config_id)` → `POST /v1/pos/payments` `{order_ref: order_uuid, amount, currency:"IDR", company_code, pos_config_id, method:"qris", idempotency_key: "pos:<order_uuid>:<payment_uuid>"}`; simpan intent; kembalikan `{intent_id, qr_string, expires_at}`. Validasi `amount > 0`, ≤ batas `efn.pos.gateway_max_amount` (config param), currency company = IDR.
    - `efn_gateway_status(intent_id)` → `GET /v1/pos/payments/:id`; update intent; kembalikan status final/pending.
    - `efn_gateway_cancel(intent_id)` → `POST .../cancel`.
  - Tambah metode `get` di `efn.bff.client` (sekarang hanya `post`) dengan aturan TLS/secret/masking yang sama.
- **Model baru `efn.pos.payment.intent`:** `company_id` (required, record rule multi-company), `pos_config_id`, `payment_method_id`, `pos_order_uuid` (index), `payment_uuid`, `amount` (Monetary), `currency_id`, `bff_payment_id` (unique, index), `provider`, `status` (selection `pending/paid/expired/failed/cancelled`), `qr_string` (dikosongkan setelah final), `expires_at`, `paid_at`, `provider_ref`, `idempotency_key` (unique). ACL: POS user read saja (tulis lewat RPC `sudo` terkontrol), POS manager read, akuntan read. Tidak ada data kartu/PAN; `pos.payment.card_no/cardholder_name` tidak diisi.
- **Validasi saat sinkron order (anti-fraud, wajib):** override `pos.order._process_payment_lines` (atau `_process_order`, signature 19.0 terverifikasi) — untuk setiap pembayaran ber-metode `efn_gateway`: `transaction_id` = `bff_payment_id` intent dengan `status='paid'` (refresh `GET` bila masih pending), `pos_order_uuid` sama, `amount` sama, belum dipakai order lain. Gagal → tolak sinkron order (error jelas), jangan diam-diam.
- **Frontend** `static/src/app/utils/payment/payment_efn_gateway.js` (aset `point_of_sale._assets_pos`): `class PaymentEfnGateway extends PaymentInterface`; `register_payment_method("efn_gateway", PaymentEfnGateway)`.
  - `sendPaymentRequest(uuid)`: bila `this.pos.data.network.offline` → notifikasi "QRIS tidak tersedia saat offline" dan `return false` (metode nonaktif offline, CLAUDE.md). Panggil `silentCall(..."efn_gateway_create"...)`, set status baris `waitingCard`, tampilkan QR di layar kasir dan **layar pelanggan** dengan mengisi `order.qrPaymentData` (format mengikuti `pos_store.showQR` 19.0 — **verifikasi bentuknya**), polling `efn_gateway_status` tiap 2 s (kontrak §8.3) sampai final atau `expires_at`; `paid` → `line.transaction_id = payment_id`, kosongkan `qrPaymentData`, `return true`.
  - `sendPaymentCancel(order, uuid)`: hentikan polling, panggil cancel, kosongkan QR. `close()`: hentikan polling.
  - `supports_reversals = false`. Refund QRIS fase 1 = metode lain (tunai/transfer) mengikuti SOP; endpoint refund **tidak ada** di §8.3 → usulan kontrak ke efn-lead bila dibutuhkan.
- **Rekonsiliasi:** jurnal metode per PT (`outstanding_account_id` standar); cron harian `efn_gateway_reconcile`: intent `paid` tanpa `pos.payment` (yatim → aktivitas ke akuntan + alert), intent `pending` lewat `expires_at` → `cancel`/`expired`; laporan settlement provider dibandingkan di BFF (bukan di Odoo). Penutupan sesi & jurnal POS **standar**.
- **Test (`tag efn`):** mock `efn.bff.client`: idempotensi create (key sama → satu intent), pemetaan status, `AccessDenied` untuk non-POS user, multi-company rule, tolak sinkron order dengan intent pending/amount beda/intent dipakai ulang, cron rekonsiliasi; tour/QUnit opsional untuk offline → false.
- **JANGAN dikustom:** layar pembayaran, model baris pembayaran, perhitungan kembalian, penutupan sesi/jurnal POS, cash control, `QRPopup`/QRIS `l10n_id_pos` (biarkan tak terkonfigurasi), `customer_display` (cukup isi `qrPaymentData`), `payment.provider`/`pos_online_payment`.

### 4.2 `efn_privacy` (F-29) — erasure UU PDP §8.4 di Odoo
- **Lisensi/depends:** LGPL-3; `base`, `mail`, `privacy_lookup`, `point_of_sale`, `efn_core`. (`auditlog` opsional: deteksi `'auditlog.log' in env`.)
- **Field `res.partner.efn_member_hash`:** `Char(64)`, `compute` dari `phone` (+`mobile` bila field ada) via `efn_core.models.efn_bff_client.partner_member_ref` (normalisasi `^62`), `store=True`, `index=True`, `readonly=True`, `copy=False`, `groups="base.group_system"`; **tidak** dimuat ke POS (`_load_pos_data_fields` tidak diubah). Rumus persis docs/04 §1: `hex(sha256(pepper_hex + member_ref))`, `pepper_hex` dibaca dari file env `EFN_MEMBER_HASH_PEPPER_FILE` (default `/run/secrets/member_hash_pepper`), di-cache per proses, tidak pernah di-log. Pepper tak terbaca → nilai `False` + `_logger.error` (jangan blokir simpan partner/POS) dan cron erasure **gagal keras**. Metode admin `_efn_recompute_member_hash()` (server action) untuk rotasi pepper/backfill.
- **Cron `efn_privacy_pull_erasures`** (tiap 15 menit, `ir.cron`, user OdooBot): `GET /v1/pos/privacy/erasures?store=odoo` → untuk tiap `{request_id, member_hash}`: cari `res.partner` `with_context(active_test=False).sudo()` lintas company dengan hash cocok →
  - partner: `name` = `Pelanggan dihapus <request_id[:8]>`, kosongkan `phone`, `mobile` (bila ada), `email`, `street`, `street2`, `city`, `zip`, `state_id`, `vat`, `website`, `function`, `comment`, `image_1920`, `efn_member_hash`; `active=False`. Tidak `unlink` (FK pos.order/account.move).
  - `pos.order` partner tsb: kosongkan `email`, `mobile` (salinan kontak stored).
  - hapus `mail.message`/`mail.tracking.value` pada partner yang memuat nilai lama kontak; hapus `auditlog.log` (+lines) `model=res.partner, res_id` bila auditlog terpasang (aturan `efn_setup_audit` mencatat HP lama).
  - jejak: `privacy.log.create({anonymized_name: 'Member', anonymized_email: 'erased@efn.invalid', execution_details: 'request <id>: n partner'})` — tanpa hash/HP.
  - `POST /v1/pos/privacy/erasures/:request_id/complete {store:"odoo", records:n}` (n=0 tetap complete → idempoten). Error per request tidak menghentikan request lain; log ber-mask.
- **Retensi:** `account.move` (faktur ber-NPWP) & `pos.order` dipertahankan (kewajiban pembukuan 10 th) dengan partner teranonim; pengecualian lain (faktur atas nama perorangan) menunggu DPO. Opsional `efn.privacy.partner_retention_days` (default 0 = mati) untuk menganonimkan partner POS-only tanpa transaksi > N hari — hanya setelah DPO menyetujui.
- **Test:** vektor uji hash **sama dengan BFF** (pepper uji dari file sementara, bukan secret nyata) untuk `0812…`, `+62 812…`, `62812…`; compute ulang saat `phone` berubah; index ada; cron dengan BFF mock: anonimkan partner aktif & arsip lintas company, salinan kontak pos.order kosong, auditlog line hilang, `complete` dipanggil dengan jumlah benar, rerun idempoten, log tidak memuat HP; pepper hilang → partner tetap tersimpan, cron error.
- **JANGAN dikustom:** wizard DSAR sendiri (pakai `privacy_lookup` untuk permintaan manual nama/email), model log sendiri (pakai `privacy.log`), mekanisme arsip partner standar.

### 4.3 Tidak perlu kode (G5)
PB1 per kota, include/exclude, service charge, EDC manual, fiscal position per outlet → semuanya konfigurasi (F-27, F-28, `efn_setup_pos`). Jangan menambah field tarif PB1 di `pos.config`/`res.company` atau logika pajak custom; `efn_business_type` tidak boleh dipakai untuk memilih rezim pajak.
