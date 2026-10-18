# efn_setup_online — konfigurasi kanal online (data-only)

Tujuan: konfigurasi standar untuk E-28 `efn_online_order` (docs/08 §8.4, docs/04 §10.1). ADR-034, ADR-019 (A1),
ADR-027, ADR-031 (semua *Proposed*). Risiko: R-T15 (API key kedaluwarsa), temuan #36/#37/#40/#42.

## Data (noupdate)
- `group_online_channel.api_key_duration` = **35 hari** (W-11, keputusan pemilik proyek 10 Okt 2026: key 30 hari + rotasi otomatis Superadmin, margin 5 hari; tanpa ini key user teknis hanya 1 hari). **noupdate=0** sejak 19.0.1.1.0: nilai ditegakkan setiap install/upgrade (nilai lama 90 diturunkan). Key yang sudah dibuat sebelum upgrade tetap memakai `expiration_date` lamanya → rotasi pertama segera setelah upgrade (runbook `odoo-online-api-key.md`, devsecops).
- **Durasi efektif = maksimum semua grup user** (`_check_expiration_date`: `max(group.api_key_duration for group in all_group_ids)`). CE 19 memberi `base.group_user` 90 hari → user teknis (Internal User + grup kanal) efektif 90. Sejak 19.0.1.1.0 `base.group_user.api_key_duration = 0` (noupdate=0; `-u base` ikut meng-upgrade modul ini sehingga dinormalkan lagi) → efektif **35**. **Dampak staf:** user internal tanpa grup berdurasi kini maksimal **1 hari** (fallback CE); admin/Settings & `sudo()` tetap bebas batas (is_system) — rotasi wajib `with_user(<user teknis>)` (skrip `efn-rotate-online-key` sudah). Test `TestEfnOnlineApiKeyDuration`. Smoke `scripts/smoke/odoo_online_smoke.py` default `SMOKE_KEY_DAYS` 85 → 30.
- Partner generik "Pelanggan Super-App" per PT (RTL, FNB) **tanpa HP/email/NPWP**; tim "Super-App" per PT.
- `ir.default loyalty.program.sale_ok = False` (setiap install/upgrade) + `post_init_hook` mematikan `sale_ok` pada
  program yang sudah ada (temuan #37; E-28 tetap membandingkan total saat konfirmasi).
- Tidak diset: `sale.automatic_invoice` (jalur `payment.transaction` sale tidak dipakai), *Lock Confirmed Sales*
  (SO terkunci tidak bisa dibatalkan, temuan #36).

## Demo (PLACEHOLDER, menunggu Q-ON1..Q-ON13)
Kanal `RTL` aktif (outlet `TOKO-01` = gudang demo `OL01` PT Retail, batas bayar Odoo **70 menit** sejak G19 = BFF 60 + 10; semula 25 = L-1),
kanal `FNB` **nonaktif** (outlet `RESTO-01` wajib FP kota contoh), jurnal bank "Titipan Online - Holding" per PT
(akun default dibuat Odoo; pemetaan ke akun piutang titipan = Finance, Q-2), pembulatan Rupiah bulat
(`biggest_tax`, Q-ON6), pricelist online per PT, 3 produk RTL (pajak PPN template l10n_id `tax_ST4`), user teknis
`efn_online_rtl` **tanpa API key** (key dibuat lewat runbook `docs/runbooks/odoo-online-api-key.md`, tidak pernah di
repo).

### G19 (19.0.1.2.0, docs/08 §10)
- **W-02:** kanal demo memakai `payment_timeout_minutes = 70` (BFF `ONLINE_PAYMENT_TIMEOUT_SECONDS=3600` + margin 10, keputusan #18). Default field (25) **tidak** diubah lewat `ir.default` (berlaku DB-wide dan mengubah kanal test `efn_online_order`) → default field kini 70 di `efn_online_order` 19.0.1.2.0 (E-35); kanal lama mempertahankan nilai tersimpan. Kanal produksi: isi 70 di checklist. Smoke `scripts/smoke/odoo_online_smoke.py` kini 70 + mode confirm.
- **W-21:** 4 kanal **per brand** (demo, nonaktif, tanpa outlet): `RTL-GL` Grand Lucky, `RTL-BACHA` Bacha butik (PT Retail), `FNB-PB` Paris Baguette, `FNB-BACHA` Bacha kafe (PT F&B); pricelist & tim penjualan per brand; partner generik & jurnal titipan per PT dipakai bersama. Kode kanal = **usulan** (kontrak docs/04 §10, lead). Kanal lama `RTL`/`FNB` tetap.
- **W-14:** outlet kanal F&B wajib fiscal position **non-dine-in** kota outlet (tanpa SC; PB1 TODO R-L09) — `efn_setup_company` 19.0.1.5.0.
- Test baru: `test_demo_fnb_outlet_is_non_dine_in`, `TestEfnOnlineBrandChannels` (bentuk kanal per brand; katalog GL vs Bacha terpisah dalam satu PT).

### G19 lanjutan (19.0.1.3.0, docs/04 §11, L-37)
- Kanal ritel demo `RTL`, `RTL-GL`, `RTL-BACHA` = `stock_hold_mode=confirm` (E-35, reservasi saat checkout); F&B tetap
  `none` (produk menu non-storable, G20).
- `brand_code` demo: `RTL-GL`=GL, `RTL-BACHA`=BACHA, `FNB-PB`=PB, `FNB-BACHA`=BACHA (E-37a; tidak unik).
- **L-37: satu user teknis per kanal** (`efn_online_order` kini menolak user yang sama di dua kanal): user demo baru
  `efn_online_rtl_gl`, `efn_online_rtl_bacha`, `efn_online_fnb_pb`, `efn_online_fnb_bacha` (Internal User + grup kanal,
  SEC-101, tanpa API key). `RTL` tetap `efn_online_rtl`; `FNB` tanpa user (nonaktif).
- Demo = noupdate: DB dev lama (user `efn_online_rtl` di 3 kanal) tidak dikoreksi otomatis → buat ulang DB dev, atau
  lepas user dari `RTL-GL`/`RTL-BACHA` manual. Constraint hanya berjalan saat kanal/user ditulis.
- Test: `test_brand_codes_hold_mode_and_one_user_per_channel`; katalog per brand memakai user per kanal.

### Putaran r12 (19.0.1.4.0, docs/04 §11.7 L-41 / SEC-131)
- Kanal demo mode confirm (`RTL`, `RTL-GL`, `RTL-BACHA`) diberi `max_order_amount` **5.000.000** dan `max_line_qty`
  **20** (PLACEHOLDER pilot lead; nilai bisnis dari Marketing/Ops sebelum go-live). Wajib karena `efn_online_order`
  19.0.1.3.0 menolak mode confirm tanpa batas nominal.
- Demo = noupdate: DB dev lama tidak memuat ulang nilai ini; post-migrate `efn_online_order` 19.0.1.3.0 mengisi
  placeholder yang sama untuk kanal confirm ber-nominal 0, dan `max_line_qty` terisi default kolom (20).
- Test: `test_brand_codes_hold_mode_and_one_user_per_channel` (semua kanal confirm punya batas), `test_demo_channels`
  (`max_qty` katalog RTL = 20).
- Checklist go-live: isi **batas nominal** dan **batas qty per baris (reservasi)** kanal produksi mode confirm.

## Checklist go-live kanal
**Satu user teknis per kanal** (L-37; hanya Internal User + grup kanal, satu company, 2FA kebijakan) → API key via runbook → secret BFF
`odoo_api_key_online_<kanal>` → kanal (pricelist, partner generik, jurnal titipan, pembulatan, batas; mode confirm:
`max_order_amount` > 0 + `max_line_qty`, L-41) → outlet (gudang,
FP outlet, jam ambil) → produk terpublikasi (tab "Online (Super-App)") → secret `odoo_online_event_hmac` dimount ke
Odoo & BFF → `ONLINE_ENABLED` BFF.

Panduan Finance (A1): faktur online di PT penjual; pembayaran tercatat di jurnal titipan (Dr akun jurnal titipan /
Cr piutang pelanggan); jurnal Holding & settlement manual (docs/08 §8.5) sampai opini pajak.

## Belum
Nilai akun/kode akun, MDR, settlement Holding (Q-ON9, Q-2); kanal F&B (Q-ON2); delivery (Q-ON3).
