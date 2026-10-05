# ADR-002: Traefik sebagai Edge Proxy + NestJS sebagai BFF (bukan Kong / Golang)

- **Status:** Proposed
- **Tanggal:** 2026-10-05
- **Pemutus (klien):** IT Lead (teknis), PO (konfirmasi)
- **Referensi review:** D-01, R-S03, R-S04, R-T12 di `docs/01_REVIEW_RISK_GAP_ANALYSIS.md`

> **Ringkasan manajemen.** Tiga dokumen menyebut tiga teknologi "API Gateway" yang berbeda. Kami merekomendasikan dua komponen open source yang sudah ada di repo: Traefik (pintu masuk, TLS, rate limit) dan NestJS (logika bisnis untuk aplikasi pelanggan). Tidak menambah lisensi, tidak menambah server, dan tim cukup menguasai satu bahasa backend (TypeScript) di luar Odoo.

## Konteks
- Blueprint & Charter: *"API Gateway (Node.js Express/NestJS)"*.
- Arsitektur v2: gateway ditulis dengan **Golang**.
- Cetak Biru: **Kong** sebagai API gateway.
- Repo saat ini sudah memakai Traefik v3 (`infra/traefik/`) dan NestJS (`services/api-gateway/`). Keputusan ini mengunci bahasa tim backend dan cara Odoo dilindungi dari internet (R-S03).

## Opsi yang dipertimbangkan

| Opsi | Kelebihan | Kekurangan | Biaya / waktu |
|---|---|---|---|
| A. **Traefik + NestJS BFF** | Traefik sudah terkonfigurasi (Let's Encrypt, middleware rate limit); NestJS = TypeScript sama dengan Next.js/Expo, ekosistem SDK payment/WA lengkap | Rate limit Traefik per-IP saja; rate limit per nomor HP tetap di BFF (Redis) | Rp0 lisensi; 0 minggu tambahan (sudah di-scaffold) |
| B. Kong (OSS) + NestJS | Plugin auth/rate limit kaya, admin API | Komponen ekstra + DB Kong (Postgres) atau mode DB-less; fitur enterprise (dev portal, RBAC) berbayar; menduplikasi peran Traefik | +1–2 minggu setup & hardening; +1 container kritis yang harus di-HA |
| C. Gateway Golang custom | Performa tinggi | Bahasa ketiga (Python, TS, Go) untuk tim kecil; semua fitur gateway ditulis sendiri | +4–8 minggu; biaya SDM Go |

## Keputusan
Opsi A. Traefik v3 menangani TLS, routing per domain/brand, header keamanan, rate limit kasar, dan blok path Odoo sensitif (`/web/database/*`, `/xmlrpc`, `/jsonrpc` dari publik). NestJS (Fastify adapter) adalah satu-satunya BFF/orchestrator untuk Super-App, brand apps, dan endpoint server-to-server dari Odoo (`/v1/pos/*`, lihat `docs/04_SERVICE_CONTRACTS.md`).

## Konsekuensi
- **Positif:** satu bahasa untuk BFF + web + mobile; tidak ada komponen tambahan; Odoo tetap tertutup.
- **Negatif / utang teknis:** tidak ada developer portal / API key management ala Kong; bila suatu saat ada partner eksternal (B2B API), evaluasi ulang.
- **Dampak dokumen:** Arsitektur v2 (Golang) dan Cetak Biru (Kong) dianggap tidak berlaku untuk layer ini.

## Risiko terkait
R-S03 (Odoo terekspos), R-S04 (brute force OTP — rate limit dua lapis: Traefik + Redis di BFF), R-T12 (monorepo — mengurangi jumlah bahasa).

## Pertanyaan terbuka untuk klien
- **Q-53**: komposisi & skill tim IT internal (Node/TypeScript tersedia?).
- Apakah ada rencana membuka API ke partner pihak ketiga dalam 24 bulan? (bila ya → tinjau ulang Kong/APISIX.)

## Kapan ditinjau ulang
Saat ada kebutuhan API publik untuk partner eksternal, atau trafik > ~1.000 req/detik berkelanjutan di BFF.
