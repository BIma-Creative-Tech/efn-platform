# Laporan efn-uiux-designer — Tenant & tema per brand + audit UX (20 Okt 2026)

Lingkup: `services/superapp-web` (UI saja). Tidak mengubah logika bisnis, kontrak BFF, pajak, cart, atau auth. Tidak ada dependency baru. Tidak commit.

## 1. Dibuat / diubah

| File | Perubahan |
|---|---|
| `.claude/agents/efn-uiux-designer.md` | Agent baru (audit UX/a11y, token, panduan visual, spesifikasi komponen; patuh R-T14 & CSP) |
| `.claude/agents/efn-lead.md`, `CLAUDE.md` | Baris tim ditambah (edit minimal) |
| `lib/brands.ts` | `BrandId` = `efn`/`grandlucky`/`parisbaguette`/`bacha`; `subdomainAliases` (`mart`→GL, `kitchen`→PB); `brandCode` GL/PB/BACHA (efn null); `logoSrc` (null) + `safeLogoSrc()`; `hero` copy per brand; `BrandTheme` + `accentSoft`, `focus`, font stack, heading weight/spacing, gaya logo, radius, bayangan; `themeToCssVars` memvalidasi tiap nilai dengan pola ketat (nilai tak valid dibuang) |
| `app/globals.css` | `var(--font-body)`/`var(--font-heading)` untuk body & h1–h3; token fallback baru di `:root`; fokus `--color-focus`; garis aksen header; `aria-current` nav; `.hero`; bayangan kartu; `select` di `.field`; min-height tombol; `scroll-padding-bottom` |
| `components/NavLink.tsx` (baru), `components/SiteHeader.tsx` | `aria-current="page"`; logo SVG opsional (same-origin) atau teks |
| `app/page.tsx` | Hero per brand dari config; kalimat pajak benar per jenis PT; langkah belanja |
| `app/layout.tsx` | Footer: "bagian dari Erajaya Food & Nourishment (EFN)" untuk tenant brand |
| `app/belanja/[id]/page.tsx` | Remah roti membawa `channel` (UX-09) |
| `components/online/online.module.css` | Kartu produk: bayangan/hover/fokus, nama produk font heading, catatan pajak 13 px |
| `tests/brands.test.ts` | Host/alias baru, brandCode per tenant (L-33 diperbarui), validasi token berbahaya, kontras ≥ 4.5:1 / fokus ≥ 3:1 dihitung per brand, `safeLogoSrc` |
| `proxy.ts` | Komentar `?brand=` |
| `README.md` (root), `services/superapp-web/README.md` | Hosts dev baru, tabel tenant, cara `?brand=` |
| `infra/compose/docker-compose.yml` | Router Traefik `web` + `grandlucky.`/`parisbaguette.`/`bacha.` (alias lama tetap) |
| `docs/04_SERVICE_CONTRACTS.md` | L-33 diperbarui (keputusan user 20 Okt) |
| `docs/design/brand-tokens.md`, `docs/reviews/uiux-audit-2026-10-20.md` | Token & kontras; audit UX-01..UX-20 |

## 2. Verifikasi (nyata, di container `efn/superapp-web:dev`)
- `tsc --noEmit` exit 0; `eslint` file yang diubah exit 0; `vitest run`: **23 file, 484 test lulus** (`brands.test.ts` 41).
- Render `next dev` (setelah `docker restart efn-superapp-web-1` — bind mount Windows tidak memicu hot-reload): `/`, `/belanja`, `/keranjang`, `/masuk`, `/meja`, `/privasi` → 200 dengan `data-brand` benar untuk `grandlucky`/`parisbaguette`/`bacha`; `mart.`→grandlucky, `kitchen.`→parisbaguette, host asing→efn; `?brand=bacha` men-set cookie `efn_dev_brand=bacha`. `/belanja` = "Belum ada toko online" (BFF dev tanpa kanal ber-`brand_code`; wajar, lihat UX-10).

## 3. Keputusan desain yang butuh konfirmasi tim brand
- Warna turunan (accentSoft, surfaceAlt, textMuted, border) & aksen GrandLucky `#9aa1dc` (dekoratif) — warna promo resmi GL belum diketahui.
- Copy hero ketiga brand (DRAF).
- Font resmi berlisensi (PB Signature, ITC Quorum, Lato/Inter/Lora self-host) → `public/fonts` + `@font-face`.
- Logo SVG resmi → `public/brands/<id>/logo.svg` + `logoSrc`.

## 4. Belum diverifikasi / risiko
- Tidak ada uji visual browser, pembaca layar, atau perangkat nyata; kartu produk & keranjang terisi tidak bisa dirender (katalog dev kosong).
- **UX-10:** dengan `brandCode` per tenant, kanal Odoo tanpa `brand_code` tidak tampil di domain brand → isi `brand_code` kanal sebelum domain brand dibuka.
- Sertifikat edge dev `*.efn.local` sudah mencakup host baru; `docker compose -f infra/compose/docker-compose.yml config -q` tanpa galat, tetapi routing Traefik ke host baru belum diuji lewat HTTPS.
- Ditunda: UX-11 (sebagian), 12 (sebagian), 13–20.
- Brand Curry Up, CHAGEE, Wetzel's Pretzels: belum ada company Odoo → tidak ada tenant.
