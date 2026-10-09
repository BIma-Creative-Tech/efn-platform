# efn_setup_online — konfigurasi kanal online (data-only)

Tujuan: konfigurasi standar untuk E-28 `efn_online_order` (docs/08 §8.4, docs/04 §10.1). ADR-034, ADR-019 (A1),
ADR-027, ADR-031 (semua *Proposed*). Risiko: R-T15 (API key kedaluwarsa), temuan #36/#37/#40/#42.

## Data (noupdate)
- `group_online_channel.api_key_duration` = **90 hari** (PLACEHOLDER; tanpa ini key user teknis hanya 1 hari).
- Partner generik "Pelanggan Super-App" per PT (RTL, FNB) **tanpa HP/email/NPWP**; tim "Super-App" per PT.
- `ir.default loyalty.program.sale_ok = False` (setiap install/upgrade) + `post_init_hook` mematikan `sale_ok` pada
  program yang sudah ada (temuan #37; E-28 tetap membandingkan total saat konfirmasi).
- Tidak diset: `sale.automatic_invoice` (jalur `payment.transaction` sale tidak dipakai), *Lock Confirmed Sales*
  (SO terkunci tidak bisa dibatalkan, temuan #36).

## Demo (PLACEHOLDER, menunggu Q-ON1..Q-ON13)
Kanal `RTL` aktif (outlet `TOKO-01` = gudang demo `OL01` PT Retail, batas bayar Odoo 25 menit = BFF 15 + 10, L-1),
kanal `FNB` **nonaktif** (outlet `RESTO-01` wajib FP kota contoh), jurnal bank "Titipan Online - Holding" per PT
(akun default dibuat Odoo; pemetaan ke akun piutang titipan = Finance, Q-2), pembulatan Rupiah bulat
(`biggest_tax`, Q-ON6), pricelist online per PT, 3 produk RTL (pajak PPN template l10n_id `tax_ST4`), user teknis
`efn_online_rtl` **tanpa API key** (key dibuat lewat runbook `docs/runbooks/odoo-online-api-key.md`, tidak pernah di
repo).

## Checklist go-live kanal
User teknis per PT (hanya Internal User + grup kanal, satu company, 2FA kebijakan) → API key via runbook → secret BFF
`odoo_api_key_online_<kanal>` → kanal (pricelist, partner generik, jurnal titipan, pembulatan, batas) → outlet (gudang,
FP outlet, jam ambil) → produk terpublikasi (tab "Online (Super-App)") → secret `odoo_online_event_hmac` dimount ke
Odoo & BFF → `ONLINE_ENABLED` BFF.

Panduan Finance (A1): faktur online di PT penjual; pembayaran tercatat di jurnal titipan (Dr akun jurnal titipan /
Cr piutang pelanggan); jurnal Holding & settlement manual (docs/08 §8.5) sampai opini pajak.

## Belum
Nilai akun/kode akun, MDR, settlement Holding (Q-ON9, Q-2); kanal F&B (Q-ON2); delivery (Q-ON3).
