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

---

# G42 — Spesifikasi UI "EFN Insight" (18 Nov 2026)

**Hasil:** `docs/design/insight-web.md` (untuk efn-frontend-engineer, Next.js + ECharts). Tidak ada kode/CSS aplikasi yang diubah.

## Isi
Prinsip & format id-ID; token CSS light + dark (netral + aksen holding EFN, status, sekuensial teal, diverging vermilion-biru, palet kategori 7 warna basis Okabe-Ito dengan pemetaan brand tetap PB/WTZ/BCF/BCR/IMD/CUP); shell, filter global (URL params, chip aktif, reset, badge kesegaran); 7 halaman (tujuan, pertanyaan, komponen, view/kolom, interaksi, state, aturan peringatan); aturan visual & aksesibilitas chart; spesifikasi 8 komponen bersama.

## Diverifikasi
Rasio kontras tokenn utama dihitung manual dengan rumus luminans relatif WCAG (tabel §2.2 dan §2.3), semua lolos. Kolom/semantik view dibaca langsung dari `12-g41-esb.sql`.

## Perlu tindakan / keputusan
1. **Temuan data (efn-data-ai-engineer):** `v_esb_data_quality` baris `data_age_hours` & `last_run_not_ok` ber-`company_code = ''` -> terbuang oleh row policy `has(codes, company_code)`; FreshnessBadge & halaman Kualitas Data kosong. Isi kode PT per brand di view.
2. Q-INS1..4 (§8 spesifikasi): definisi/ambang sell-through, `trx_count` per baris menu (KPI Transaksi bisa menghitung ganda), tidak ada view asal-tujuan, status PB1 pada `net` ESB.
3. Target HPP % per brand dan nama/warna IMD/CUP menunggu pemilik bisnis / G43.

## Belum diverifikasi
Tidak diuji di browser, pembaca layar, atau perangkat. Kontras token yang disebut "diperiksa test" (border, soft, chrome-muted, c6/c7 gelap) belum dihitung; ambang status dan palet adalah proposal. Interaksi keyboard chart ECharts (dispatchAction) belum dicoba.
