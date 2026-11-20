# efn_insight — Dashboard Insight (SSO akun Odoo, G42 + G47)

**Versi:** 19.0.2.0.0 · **Lisensi:** LGPL-3 · **Depends:** `efn_core` (CE saja, tanpa modul pihak ketiga — ADR-031)

## G47 (19.0.2.0.0): token `eit2` + cakupan supermarket
Kontrak `docs/04_SERVICE_CONTRACTS.md` §28.13.3–§28.13.4 (v20.3, L-186..L-189), fit-gap
`docs/fit-gap/G47_INSIGHT_SM.md` §7.1, ADR-041 §3.4 (Proposed).
- **`EFN_INSIGHT_TOKEN_VERSION`** (`1|2`, default `2`; nilai lain → `2`). `2` = token **`eit2`**: 13 kunci (tambahan
  `wids`, `cats`; `v=2`; `codes ⊆ ESB_CODES ∪ SM_CODES`; `roles` = satu dari `user`/`all_brands` + 0..5 persona).
  Aturan konsistensi diperiksa pembuat (`lib/insight_token.validate_claims_v2`): `wids` ada ⇔ ada persona ⇔ ada kode
  SM; `0 ∈ wids` ⇔ persona exec/category/dc/marketing; `cats` hanya untuk `category`; `payload_b64 ≤ 3000`.
  `1` = rollback: `eit1` persis §26.3, bagian SM diabaikan. **Deploy insight-web sebelum Odoo** (L-186).
- **`INSIGHT_SM_ENABLED`** (string persis `true`, default mati) dicek di controller, bukan di hook, agar modul
  penimpa tidak bisa melewatinya.
- Hook privat `res.users._efn_insight_sm_scope()` mengembalikan cakupan kosong; `efn_insight_sm` (modul jembatan,
  auto_install) menimpanya. Tidak ada metode publik baru (SEC-58); model pertama modul ini hanya `_inherit`, tanpa ACL.
- Halaman info baru: `no_outlet` (persona Kepala toko tanpa outlet sah, 200 tanpa token) dan `scope_too_large`
  (`wids > 200`, `cats > 50`, atau `payload_b64 > 3000`; log `insight_scope_too_large uid=… wids_n=… cats_n=…`).
- Audit `ir.logging`: `insight_open uid=… v=2 codes=… cids=… roles=… wids_n=… cats_n=… jti=…` — tanpa daftar
  wids/cats (minimisasi; dapat direkonstruksi dari Odoo). Jalur v1 menulis `v=1` tanpa jumlah.
- Privilege "EFN Dashboard Insight" diberi `sequence=10` agar tampil sebelum privilege persona (20..70).
- Vektor uji `eit2` A/B/C + sig2 (§28.13.4/§28.13.6) dihitung di `tests/test_insight_token_v2.py` dan **dicetak ke
  log** (`EFN_G47_VECTOR …`) untuk dibandingkan efn-lead dengan builder Node; setelah sepakat, nilainya dijadikan
  konstanta assert byte-exact.

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
| `INSIGHT_SM_ENABLED` | – | G47: bagian supermarket token `eit2`. Hanya `true` persis. Mati → persona diabaikan (user SM-saja mendapat "Tidak ada PT"). |
| `EFN_INSIGHT_TOKEN_VERSION` | – | G47: `1` = rollback `eit1`; lainnya/kosong = `eit2`. |
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
Modul tidak membuat model baru (G47 hanya `_inherit = "res.users"` untuk hook privat), jadi tidak ada
`ir.model.access.csv` maupun `ir.rule` (konvensi CLAUDE.md berlaku bila kelak log audit dijadikan model:
`company_id` + record rule).

## Cara uji manual
1. Set env Odoo `INSIGHT_ENABLED=true`, `EFN_INSIGHT_URL=https://insight.<domain>`, secret `insight_sso_key`.
2. Beri user grup "Insight: pengguna" dan PT PB/BCF; buka menu **Dashboard Insight** → browser POST ke insight-web.
3. DevTools: respons `/efn/insight/open` punya header CSP/no-store/strict-origin; URL tidak berisi token.
4. User tanpa grup → 403; user hanya GL/EFN → halaman "Tidak ada PT"; `INSIGHT_ENABLED=false` → "belum diaktifkan".
5. Pengaturan → Teknis → Logging: satu baris `efn_insight` per pembukaan, tanpa token/nama.

Test otomatis: `tests/test_insight_token.py` (vektor §26.3, aturan `name`, validasi URL/kunci),
`tests/test_insight_token_v2.py` (G47: vektor A/B/C + N1..N5, konsistensi, batas ukuran, sig2) dan
`tests/test_insight_sso.py` (HttpCase; default `eit2`, rollback `eit1`), tag `efn`. Test persona/cakupan SM ada di
`efn_insight_sm`.

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
