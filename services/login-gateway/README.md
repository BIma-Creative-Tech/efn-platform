# login-gateway

Halaman masuk staf EFN (Next.js, App Router, TypeScript strict). Pengguna memilih tujuan (**ERP (Odoo)** atau **Insight**),
lalu diarahkan (302) ke halaman masuk sistem tujuan. Service ini **tidak pernah menerima password**, tidak memakai cookie,
dan tidak memegang secret.

## ADR dan asal kode
- ADR-050 (keputusan gateway, opsi 2), ADR-049 (porting dari repo milik pemilik proyek), ADR-002/022 (Traefik, TLS).
- Pola mengikuti `login-gateway` EAL di repo odoo-platform milik pemilik proyek (halaman bermerek, server memilih tujuan,
  password tetap di Odoo). **Dibuang:** branding/logo Erajaya, pemilih vertical/environment, pencarian nama DB/tenant
  (EFN satu DB Odoo, ADR-001). Halaman ditulis ulang dari spesifikasi ADR-050 (bukan disalin); referensi pola:
  `odoo-platform` `login-gateway/` commit `eea3d86` (origin/main, 23 Nov 2026).
- Tooling/Dockerfile/CSP mengikuti `services/insight-web` (Next 16 + `proxy.ts`; ADR menyebut Next 15, versi disamakan
  dengan insight-web).

## Rute
| Path | Fungsi |
|---|---|
| `/` | Panel masuk; tautan `/go/erp` dan `/go/insight` (tujuan nonaktif bila env tidak valid) |
| `/go/[dest]` | 302 ke URL allowlist env; `dest` tak dikenal 404; env kosong/tidak valid 503 |
| `/healthz` | `200 {"status":"ok"}` untuk probe |

## Environment
| Variabel | Contoh | Catatan |
|---|---|---|
| `LOGIN_GATEWAY_ERP_URL` | `https://erp.efn.local:9443/web/login` | wajib https absolut tanpa kredensial |
| `LOGIN_GATEWAY_INSIGHT_URL` | `https://insight.efn.local:9443/` | idem |
| `HOSTNAME` / `PORT` | `127.0.0.1` / `3005` | plain HTTP loopback; TLS oleh sidecar ghostunnel |
| `NODE_ENV`, `NEXT_TELEMETRY_DISABLED` | `production`, `1` | |

Tidak ada `NEXT_PUBLIC_*`. Validasi di `lib/config.ts` (satu modul); `instrumentation.ts` mencatat nama variabel yang
bermasalah saat boot (tanpa nilai).

## Aset logo
`public/brand/` memuat `ebn.png` (logo EFN) dan logo brand: `parisbaguette.png`, `grandlucky.png`, `bacha.svg`,
`wetzels.svg` (asal: `services/superapp-web/public/brands/<brand>/logo.*`, `efn/ebn.png`).

## Risiko
- Open redirect: dimitigasi allowlist env; `dest` hanya kunci. Diuji di `tests/go-route.test.ts`.
- Phishing: bila env diarahkan salah, pengguna dikirim ke host salah; env hanya diubah lewat compose/secret review.
- Rate-limit dan header HSTS tambahan ada di Traefik (ADR-050), bukan di service ini.

## Belum
- `package-lock.json` (dibuat lead lewat `npm install` di container).
- Uji di browser (kontras, fokus, tanpa JS), uji CSP nonce di build production.
- Compose/Traefik/sidecar TLS, penyesuaian gaya formulir login Odoo (modul Odoo), docs/04 bagian login-gateway.

## Perintah
`npm run lint | typecheck | test | build`
