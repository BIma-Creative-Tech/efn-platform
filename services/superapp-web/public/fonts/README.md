# Font brand (self-host, CSP `font-src 'self'`)

Dipakai lewat `app/fonts.css`; peran per brand di `lib/brands.ts` (`fontDisplay`, `fontHeading`, `fontBody`, `fontLabel`,
`fontNav`). Diukur dari situs resmi (getComputedStyle, Playwright) 24 Okt 2026.

| Folder | Font | Sumber | Lisensi |
|---|---|---|---|
| `parisbaguette/` | PB Signature Bold Display (h1, menu, tombol), PB Special Elite (h2/h3, label) | parisbaguette.com `wp-content/themes/paris-baguette-2024/lib/fonts/` | Proprietary Paris Baguette. Dipakai atas keputusan pemilik proyek 24 Okt 2026 (EFN pemegang hak brand PB di Indonesia; risiko lisensi ditanggung pemilik proyek). Ganti dengan file berlisensi resmi dari tim brand bila ada. |
| `bacha/` | ITC Quorum 300/500/700 (judul), Lora 400/400i/500/600 (teks; family `Bacha Lora`) | bachacoffee.com `App_Data/Sitefinity/WebsiteTemplate/BachaCore/App_Themes/BachaCore/fonts/` | ITC Quorum: proprietary (Monotype/ITC), keputusan sama seperti di atas. Lora: OFL-1.1. |
| `roboto/` | Roboto variable 300–700 (+ italic), latin & latin-ext | Google Fonts | OFL-1.1 |
| `lato/` | Lato 400/700/900, latin & latin-ext | Google Fonts | OFL-1.1 |
| `londrina-solid/` | Londrina Solid 300/400/900, latin | Google Fonts | OFL-1.1 |

Font Geist / Geist Mono / Fraunces (EFN holding) dari paket `@fontsource-variable/*` (OFL-1.1), bukan folder ini.
