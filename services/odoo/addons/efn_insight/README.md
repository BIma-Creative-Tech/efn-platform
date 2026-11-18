# efn_insight — Dashboard Insight (SSO akun Odoo, G42)

**Versi:** 19.0.1.0.0 · **Lisensi:** LGPL-3 · **Depends:** `efn_core` (CE saja, tanpa modul pihak ketiga — ADR-031)

## Tujuan
Staf EFN membuka dashboard `insight-web` (Next.js, mart ESB) memakai akun Odoo. Menu **"Dashboard Insight"** memanggil
`GET /efn/insight/open`; Odoo menghitung cakupan PT user, membuat token SSO `eit1` sekali pakai (HMAC-SHA256, umur
60 detik), lalu merender halaman kecil dengan **form POST** auto-submit ke `<EFN_INSIGHT_URL>/sso`. Token tidak pernah
lewat query string (tidak masuk access log Traefik / riwayat browser). Odoo tidak dihubungi insight-web dan tidak
diekspos ke internet.

## ADR & kontrak
- **ADR-041** (Proposed) — Insight web + SSO Odoo. Pilihan ini murah dibalik: modul hanya menambah 2 grup, 1 menu,
  1 controller; tidak ada model/data bisnis.
- `docs/04_SERVICE_CONTRACTS.md` §26.2 (perilaku controller), §26.3 (format token byte-exact + vektor uji bersama).
- Fit-gap: `docs/agent-reports/efn-odoo-functional.md` bagian "Gelombang 42" (CE tidak punya IdP/OIDC provider).

## Risiko yang dimitigasi
- Kebocoran lintas PT: cakupan dihitung server dari `res_company_users_rel` (PT arsip dan PT tanpa kode ESB tidak ikut);
  ClickHouse menegakkan lapis kedua lewat row policy bertanda tangan (§26.7).
- Token bocor/replay: umur 60 detik, `jti` acak (insight-web menolak `jti` ulang), dikirim lewat POST, halaman
  `Cache-Control: no-store`, `Referrer-Policy: strict-origin` pada halaman form (Origin wajib terkirim ke `/sso`, C1;
  halaman info: `no-referrer`).
- XSS/injeksi di halaman perantara: nilai hanya lewat `t-att-*`/`t-out` (auto-escape), tanpa `t-raw`/`Markup`;
  `EFN_INSIGHT_URL` divalidasi ketat (skema+host+port, path hanya `/`); CSP
  `default-src 'none'; script-src 'self'; form-action <origin insight>; base-uri 'none'; frame-ancestors 'none'`;
  auto-submit lewat file JS statis (bukan inline script).
- Plaintext: `EFN_INSIGHT_URL` wajib `https://`; `http://` hanya bila `plaintext_allowed()` (efn_core: dev/ci eksplisit).
- Minimisasi PDP: payload tanpa login/email/telepon; hanya `sub` (uid) dan nama tampilan (dibersihkan, maks 64).
- Permukaan RPC: tidak ada metode model baru; pembuat token = fungsi murni di `lib/insight_token.py`.

## Konfigurasi (env / secret)
| Nama | Wajib | Keterangan |
|---|---|---|
| `INSIGHT_ENABLED` | – | Kill switch, default mati. Hanya string `true` (persis, sama dengan insight-web) = aktif; `1/yes/on/True` = mati. Mati → halaman "belum diaktifkan" (menu tetap ada). |
| `EFN_INSIGHT_URL` | ya | Origin insight-web, mis. `https://insight.<domain>` (tanpa path/query/kredensial). Salah → 503 "konfigurasi". |
| `EFN_INSIGHT_SSO_KEY_FILE` | – | Default `/run/secrets/insight_sso_key`. Isi: hex ≥ 64 karakter. Dibaca tiap request. |
| `APP_ENV`, `EFN_ALLOW_PLAINTEXT` | – | Dipakai `plaintext_allowed()` (efn_core). |

**Kunci HMAC = byte ASCII string hex setelah `strip()`, BUKAN hasil hex-decode** (sama di insight-web; vektor §26.3).

## Perilaku `GET /efn/insight/open` (urut)
1. Belum login → redirect login Odoo (`auth='user'`).
2. Tanpa grup `efn_insight.group_insight_user` → 403.
3. `INSIGHT_ENABLED` mati → 200 info "belum diaktifkan".
4. URL/kunci salah → 503 info "konfigurasi belum lengkap" + log ERROR `insight_misconfigured` (tanpa isi kunci/URL).
5. Cakupan:
   - grup `group_insight_all_brands` → `codes` = `BCF,BCR,CUP,IMD,PB,WTZ`, `cids` = semua company ber-kode ESB,
     `roles=["all_brands"]`;
   - lainnya → company di `company_ids` user yang aktif dan `efn_brand_code ∈ ESB_CODES`, `roles=["user"]`.
   Kosong → 200 info "Tidak ada PT yang bisa ditampilkan" (tanpa token). User Holding (EFN) tanpa grup all_brands
   mendapat halaman ini.
6. Token `eit1.<payload_b64url>.<sig_b64url>` → halaman form POST auto-submit + tombol "Lanjut".

## Audit & retensi
Setiap token yang dibuat dicatat di `ir.logging` (`name=efn_insight`, `message="insight_open uid=… codes=… cids=…
roles=… jti=…"`) dan log server (Loki). Tidak pernah mencatat token, sig, kunci, nama, atau login. CE **tidak**
membersihkan `ir.logging` otomatis → retensi = **Q-INS3** (usulan 400 hari, keputusan DPO); sampai diputuskan, hapus
manual/cron admin. Penolakan 403 hanya `_logger.info("insight_denied uid=…")`.

## Tanpa model baru
Modul tidak membuat model, jadi tidak ada `ir.model.access.csv` maupun `ir.rule` (konvensi CLAUDE.md berlaku bila
kelak log audit dijadikan model: `company_id` + record rule).

## Cara uji manual
1. Set env Odoo `INSIGHT_ENABLED=true`, `EFN_INSIGHT_URL=https://insight.<domain>`, secret `insight_sso_key`.
2. Beri user grup "Insight: pengguna" dan PT PB/BCF; buka menu **Dashboard Insight** → browser POST ke insight-web.
3. DevTools: respons `/efn/insight/open` punya header CSP/no-store/strict-origin; URL tidak berisi token.
4. User tanpa grup → 403; user hanya GL/EFN → halaman "Tidak ada PT"; `INSIGHT_ENABLED=false` → "belum diaktifkan".
5. Pengaturan → Teknis → Logging: satu baris `efn_insight` per pembukaan, tanpa token/nama.

Test otomatis: `tests/test_insight_token.py` (vektor §26.3, aturan `name`, validasi URL/kunci) dan
`tests/test_insight_sso.py` (HttpCase), tag `efn`.

## Referensi desain (bukan dependency, tidak disalin — ADR-031)
OCA `server-auth` (`auth_api_key_group`: grup menentukan cakupan) dan `cross_connect_client` (AGPL-3, Akretion: menu
per grup → masuk ke aplikasi lain dengan token singkat). Diambil idenya saja; implementasi ditulis ulang.

## Batasan / belum
- Penetapan grup ke peran (`efn_setup_role`) menunggu **Q-INS1**; cakupan = semua `company_ids` (bukan hanya PT aktif
  di pemilih perusahaan) — **Q-INS2** (rekomendasi functional).
- Pencegahan replay `jti` ada di insight-web (cache memori, replika = 1); multi-replika butuh Redis (G berikutnya).
- Audit di `ir.logging` bisa dihapus admin sistem; event akses anti-hapus di `efn_audit` = G berikutnya bila Auditor
  memerlukannya.
- Sejak G43 (`efn_setup_company` 19.0.1.9.0) IMD/CUP adalah company Odoo (`company_imd`/`company_cup`): `all_brands`
  otomatis memasukkan id-nya ke `cids`, dan user biasa mendapat kode IMD/CUP bila company itu ada di `company_ids`-nya
  (tanpa perubahan kode modul ini).
- Batas `cids` maks 32 (§26.3); bila company ber-kode ESB lebih dari itu, pembuatan token gagal (500).
