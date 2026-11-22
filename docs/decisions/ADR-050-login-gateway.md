# ADR-050: Halaman masuk staf EFN lewat `login-gateway` (Next.js) tanpa memegang password

- **Status:** Diputuskan pemilik proyek 23 Nov 2026 (pendekatan gateway seperti EAL). Rincian teknis Proposed sampai review
  `efn-security-reviewer` atas G62.
- **Tanggal:** 2026-11-23
- **Pemutus:** Pemilik proyek; Security (review G62)
- **Referensi review:** R-S01 (secret), R-S15 (permukaan serangan)
- **Terkait:** ADR-002 (Traefik edge), ADR-022 (TLS verify), ADR-041 (SSO Odoo → Insight), ADR-049 (port kode odoo-platform)

> **Ringkasan manajemen.** Staf EFN akan masuk lewat satu halaman bermerek EFN (`login.<domain>`), memilih tujuan (ERP Odoo
> atau Insight), lalu diarahkan ke formulir masuk Odoo. Halaman ini **tidak pernah menerima atau menyimpan password**, sehingga
> risiko kebocoran kredensial tidak bertambah. Biaya ±1 minggu-engineer, satu container kecil tambahan.

## Konteks
- Permintaan pemilik proyek 23 Nov 2026: halaman login custom untuk Odoo EFN "seperti yang diterapkan odoo-platform untuk EAL".
- EAL ternyata memakai app Next.js terpisah (`login-gateway/` di `odoo-platform`, Next 15.5): memilih vertical + environment,
  server mencari nama DB, lalu mengarahkan ke formulir login standar Odoo. Password tidak lewat gateway; nama DB tidak sampai ke
  browser.
- EFN memakai **satu DB Odoo** (ADR-001), jadi pemilih DB/tenant tidak diperlukan. Nilai gateway bagi EFN: identitas visual EFN
  + brand, satu pintu masuk ke ERP dan Insight.
- Alternatif yang lebih sederhana (override template `web.login_layout` di modul Odoo) ditawarkan dan **tidak dipilih**.

## Opsi yang dipertimbangkan
1. Modul Odoo `efn_web_login` (override QWeb) — tanpa service baru; tidak dipilih pemilik proyek.
2. **Gateway Next.js, redirect ke formulir Odoo** — pola EAL; tidak memegang password.
3. Gateway yang menampung formulir dan memanggil `/web/session/authenticate` di server — tampilan penuh custom, tetapi password
   melewati service tambahan, 2FA/TOTP Odoo harus ditangani ulang, dan cookie sesi harus di-relay. Ditolak.

## Keputusan
**Opsi 2**, dengan aturan:
- Service `services/login-gateway` (Next.js 15, TypeScript strict), host `login.${TRAEFIK_DOMAIN}`, di belakang Traefik + sidecar
  TLS (pola `insight-web-tls`), rootfs read-only, user non-root.
- Tujuan redirect hanya dari **allowlist** konfigurasi (URL ERP `/web/login`, URL Insight); parameter dari browser tidak pernah
  menjadi URL tujuan (cegah open redirect).
- Tidak ada formulir password, tidak ada cookie sesi, tidak ada secret di service ini. CSP ketat, rate-limit di Traefik.
- Formulir login Odoo tujuan diberi gaya ringan (logo/warna EFN) oleh modul Odoo tanpa mengubah alur autentikasi.

## Konsekuensi
- Positif: tampilan masuk bermerek tanpa menambah risiko kredensial; satu pintu ERP + Insight.
- Negatif: satu container + sidecar TLS + route tambahan untuk dipelihara; formulir password tetap milik Odoo (dua halaman).
- Dampak dokumen: `docs/04_SERVICE_CONTRACTS.md` (bagian login-gateway), runbook boot dev, `docs/02` daftar service.
