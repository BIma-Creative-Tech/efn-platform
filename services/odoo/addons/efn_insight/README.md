# efn_insight — Dashboard Insight (SSO akun Odoo, G42 + G47 + G52)

**Versi:** 19.0.3.0.0 · **Lisensi:** LGPL-3 · **Depends:** `efn_core` (CE saja, tanpa modul pihak ketiga — ADR-031)

## G52 (19.0.3.0.0): persona F&B, cabang ESB kepala toko, token `eit3`
Kontrak `docs/04_SERVICE_CONTRACTS.md` §28.15.2–§28.15.5 (v21.1, L-237..L-249), **ADR-041 §3.6** (addendum 22 Nov
2026, Proposed; opsi P1), fit-gap `docs/agent-reports/efn-odoo-functional.md` bagian "22 Nov 2026 — Insight F&B persona
& scope cabang ESB".

**Tujuan.** Direksi, Kepala toko, dan Marketing F&B memakai grup Odoo yang sama dengan supermarket; kepala toko F&B
hanya melihat cabang ESB yang ditugaskan (sebelumnya akses PT = semua cabang PT).

| Bagian | Isi |
|---|---|
| Grup persona (L-239) | `group_insight_{exec,store,marketing,category,dc}` + privilege `res_groups_privilege_insight_sm_*` **dipindah dari `efn_insight_sm`**, nama xmlid tetap (id grup & keanggotaan user tidak berubah). Pre-migrate `migrations/19.0.3.0.0` (+ SQL sama di `efn_insight_sm` 19.0.2.0.0, `lib/persona_xmlids.py`) mengganti `ir_model_data.module`; idempoten, baris bentrok dilewati. Label baru "Insight: Direksi/Kepala toko/Marketing/Category manager (supermarket)/DC/logistik (supermarket)". `kpi_admin` tetap di `efn_insight_sm`. Peta tunggal `models/res_users.PERSONA_GROUPS`. |
| Model `efn.insight.esb.branch` (L-240) | `company_id` (wajib, restrict, domain kode ESB), `esb_brand` (`bacha/cup/imd/pb/wtz`), `esb_branch_id` (1..4294967295), `name`, `branch_code`, `is_store`, `active`. Unik `(esb_brand, esb_branch_id)`; brand ↔ PT (`bacha`→BCF/BCR, `pb`→PB, `wtz`→WTZ, `imd`→IMD, `cup`→CUP). ACL hanya `base.group_erp_manager`; record rule `company_id in company_ids`. Menu *Settings → Users & Companies → Cabang ESB (Insight)*; impor CSV standar (`esb_brand,esb_branch_id,name,branch_code,is_store,active,company_id`) dari ekspor `efn_dwh.v_esb_branch_list`. |
| `res.users.efn_insight_esb_branch_ids` | M2M, relasi `efn_res_users_insight_esb_branch_rel`, domain `company_id in company_ids`, `groups=base.group_erp_manager`, bukan self-writeable; tab "Insight" di form user. Teraudit: `efn_audit` `CONTROL_SPECS` (field ini + model cabang: `company_id, esb_brand, esb_branch_id, is_store, active`). |
| Cakupan v3 (§28.15.4) | G = persona dari grup; **aturan store lintas domain** (`suppress_store`): store dibuang bila dipegang tanpa exec bersama category/dc/marketing → G' (hook SM menerima G'). **Store-only F&B** (store, tanpa exec/marketing/all_brands): flag mati, store dibuang, atau versi token 1/2 → bagian ESB **kosong** (pencabutan PT penuh **tanpa bergantung flag**, L-241); flag hidup + eit3 → `brs` = cabang sah user (aktif, toko, PT aktif berkode ESB di `company_ids`), `codes/cids` = PT cabang, persona `store`. Selain itu §26.2 apa adanya + persona F&B (bila flag) = G' ∩ {exec, marketing} ∪ store (hanya bersama exec). |
| Token `eit3` (§28.15.5) | 14 kunci (`eit2` + `brs`), `v=3`, `brs` unik urut ASCII, maks 50, pola `^(bacha\|cup\|imd\|pb\|wtz):[1-9][0-9]{0,9}$` ≤ 4294967295; aturan T1–T8 di `lib/insight_token.validate_claims_v3` (pembuat + rujukan verifikasi; kode rinci `bad_brs` untuk bentuk `brs`, `bad_claims` untuk T1–T7). `eit2`/`eit1` tetap untuk rollback. |
| Info baru | `no_branch` (store-only F&B, flag hidup, tanpa cabang sah, user punya PT ESB). `scope_too_large` juga untuk `brs > 50`. Audit `ir.logging` menambah `brs_n=<n>` (tanpa daftar cabang, L-249). |

**Env baru/berubah:** `INSIGHT_FNB_PERSONA_ENABLED` (hanya `true` persis; default mati; prod mati sampai Q-INS-F2 +
QA + review security G52) dan `EFN_INSIGHT_TOKEN_VERSION` = `1|2|3`, **default `3`** (nilai lain → 3). Urutan deploy:
ClickHouse + insight-web satu rilis → Odoo `-u efn_insight,efn_insight_sm` **satu perintah** → `INSIGHT_ACCEPT_EIT2=false`.

**Risiko yang dimitigasi.** R-S22 (lanjutan): cakupan cabang bocor (kepala toko melihat cabang lain/PT penuh) —
pencabutan tanpa flag, T5/T6 di pembuat, `brs` ditandatangani; lapis ClickHouse (sig3, kelas PT/BR) di luar modul ini.
R-S22: hilangnya keanggotaan grup saat relokasi modul — xmlid name tetap, pre-migrate di dua modul, test simulasi.
SEC-G47-01: store + marketing tidak menerbitkan `store`.

**Vektor uji bersama** `tests/vectors_g52.json` (D–G, N6–N14, input sig3 D/E). **insight-web wajib memakai input yang
identik dari file ini** (kunci = `"ab" * 32`). `payload_b64`, `sig_b64`, `sig3_hex` masih `null`: test Odoo
(`test_insight_token_v3.py`) menghitung dengan `hashlib`/`hmac`, mencetaknya ke log (`EFN_G52_VECTOR …`) dan
meng-assert byte-exact begitu nilai diisi efn-lead setelah dibandingkan dengan Node.

**Penyimpangan kecil yang disengaja (dicatat untuk efn-lead):**
- `no_branch` hanya bila user memang punya PT ESB di `company_ids`; kepala toko SMD tanpa PT ESB tetap mendapat
  `no_outlet`/`no_company` (pesan cabang ESB menyesatkan baginya). Kontrak butir 6 tidak membedakan.
- Pencabutan store-only juga diterapkan di jalur rollback `eit1` (kontrak hanya menyebut `eit2`); fail closed.
- `esb_branch_id` = `fields.Integer` (PostgreSQL `int4`): nilai > 2147483647 ditolak database sebelum constraint
  1..4294967295 berjalan. ID cabang ESB nyata kecil; bila perlu > int4, kolom harus diubah (belum terverifikasi
  terhadap data ESB nyata).
- Hook SM tetap menerapkan `STORE_COVERING_PERSONAS` sendiri (idempoten dengan G'); konstanta itu dibaca
  `services/etl/tests/test_insight_sm_access.py`.

**Belum / batasan G52.** Perilaku `_process_end` Odoo 19 saat xmlid pindah modul dan bila hanya satu modul di-upgrade
belum diuji di DB nyata (test hanya mensimulasikan rename di transaksi); wajib uji upgrade DB demo SMD
`-u efn_insight,efn_insight_sm`. Bila operator hanya menjalankan `-u efn_insight_sm`, grup tetap aman (pre-migrate
efn_insight_sm) tetapi label/privilege baru baru termuat saat `efn_insight` di-upgrade. Langkah runbook impor CSV
(`docs/runbooks/esb-extract.md`) belum ditulis (di luar cakupan engineer Odoo). Kaitan cabang → `stock.warehouse`
pasca-cutover ditunda (YAGNI). Persona berlaku untuk semua PT user (Q-INS-F1).

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
- **ADR-041** (Proposed) — Insight web + SSO Odoo, addendum §3.4 (G47) dan **§3.6 (G52)**. Masih murah dibalik:
  grup persona + satu model konfigurasi kecil (cabang ESB), tanpa data bisnis.
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
| `INSIGHT_SM_ENABLED` | – | G47: bagian supermarket token `eit2`/`eit3`. Hanya `true` persis. Mati → persona SM diabaikan (user SM-saja mendapat "Tidak ada PT"). |
| `INSIGHT_FNB_PERSONA_ENABLED` | – | G52: persona F&B (exec/marketing/store) + `brs`. Hanya `true` persis. Mati → tanpa persona F&B; kepala toko F&B store-only tetap **tanpa** cakupan PT penuh. |
| `EFN_INSIGHT_TOKEN_VERSION` | – | G52: `1` = rollback `eit1`; `2` = rollback `eit2`; lainnya/kosong = `eit3`. |
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

## Model & keamanan
G52 menambah satu model, `efn.insight.esb.branch` (`company_id` wajib, `_check_company_auto`), dengan
`security/ir.model.access.csv` (hanya `base.group_erp_manager`) dan record rule multi-company
`efn_insight_esb_branch_rule_company`. `group_insight_user` tidak punya akses ke model ini; cakupan dihitung controller
dengan `sudo` hanya untuk cabang milik user yang membuka Insight.

## Cara uji manual
1. Set env Odoo `INSIGHT_ENABLED=true`, `EFN_INSIGHT_URL=https://insight.<domain>`, secret `insight_sso_key`.
2. Beri user grup "Insight: pengguna" dan PT PB/BCF; buka menu **Dashboard Insight** → browser POST ke insight-web.
3. DevTools: respons `/efn/insight/open` punya header CSP/no-store/strict-origin; URL tidak berisi token.
4. User tanpa grup → 403; user hanya GL/EFN → halaman "Tidak ada PT"; `INSIGHT_ENABLED=false` → "belum diaktifkan".
5. Pengaturan → Teknis → Logging: satu baris `efn_insight` per pembukaan, tanpa token/nama.
6. G52: *Settings → Users & Companies → Cabang ESB (Insight)* → Impor CSV dua cabang PB. User dengan persona
   "Insight: Kepala toko" saja + PT PB, tab "Insight" isi satu cabang. `INSIGHT_FNB_PERSONA_ENABLED=true` → token
   `eit3` berisi `brs` cabang itu dan `codes=["PB"]`; kosongkan cabang → halaman "belum ditautkan ke cabang ESB";
   flag `false` → "Tidak ada PT" (bukan PT penuh). Tambahkan persona Direksi → PT penuh, `roles` exec+store, `brs=[]`.
7. Upgrade DB lama: `-u efn_insight,efn_insight_sm` → user demo SMD tetap memegang persona (id grup sama).

Test otomatis (tag `efn`): `tests/test_insight_token.py` (vektor §26.3), `tests/test_insight_token_v2.py` (G47: A/B/C,
N1..N5, sig2), `tests/test_insight_token_v3.py` (G52: D–G byte-exact dari `vectors_g52.json`, N6–N14, T1–T8, sig3,
aturan persona), `tests/test_insight_g52.py` (relokasi grup + simulasi rename xmlid dengan keanggotaan tetap,
constraint/ACL/record rule/impor CSV model cabang, matriks cakupan §28.15.11, pencabutan tanpa flag/rollback) dan
`tests/test_insight_sso.py` (HttpCase; default `eit3`, rollback `eit2`/`eit1`, kepala toko F&B, `no_branch`). Test
persona/cakupan SM ada di `efn_insight_sm`; audit cabang di `efn_audit/tests/test_audit_g52.py`.

## Referensi desain (bukan dependency, tidak disalin — ADR-031)
OCA `server-auth` (`auth_api_key_group`: grup menentukan cakupan) dan `cross_connect_client` (AGPL-3, Akretion: menu
per grup → masuk ke aplikasi lain dengan token singkat). G52: OCA `openupgradelib` `rename_xmlids` (konsep rename
`ir_model_data.module`; lisensi belum diverifikasi). Diambil idenya saja; implementasi ditulis ulang.

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
