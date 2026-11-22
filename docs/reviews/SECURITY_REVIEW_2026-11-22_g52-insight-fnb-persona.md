# Security Review — G52 Insight persona F&B + sidebar dual menu (22 Nov 2026)

Reviewer: efn-security-reviewer (read-only). Branch: `feat/insight-nav-dual-menu` (worktree `efn-platform-nav`).
Cakupan: docs/04 §28.15, ADR-041 §3.6 (Proposed); `efn_insight` 19.0.3.0.0, `efn_insight_sm`, `efn_role`, `efn_audit`;
`infra/clickhouse` (sig3, kelas PT/BR, 4 view toko, `v_esb_store_receipt_daily`); `services/insight-web` (eit3/eis3,
`fnbRouteDenial`, proxy, ekspor, `smRouteDenial`, commit 0aab9ab `scopedQuery`/`navItemActive`).
Metode: baca kode dari tree branch (tanpa `git diff`, tanpa menjalankan test/harness ClickHouse).

## Ringkasan eksekutif

Kontrol inti G52 sudah benar. Kepala toko F&B store-only kehilangan cakupan PT penuh di semua versi token, juga saat flag mati.
`brs` ditandatangani (HMAC token + sig3). Row policy kelas PT menolak sesi ber-cabang, dan kelas BR mengikat `brand:branch_id`
ke kode PT. Tidak ditemukan jalur yang memberi eit2/eis2 cakupan PT penuh kepada user store-only dari Odoo baru.
Tidak ada Critical/High. Ada satu **Medium**: flag baru tidak diteruskan docker-compose ke container, sehingga
`INSIGHT_ACCEPT_EIT2=false` (penutup jalur eit2) tidak bisa diterapkan dan G52 tidak bisa diaktifkan. Ada juga tiga Low/Info:
kombinasi persona tertentu menghasilkan HTTP 500, ada residu sesi eis2 saat rollout, dan proxy memakai denylist rute.
**Verdict: boleh merge dengan syarat** SEC-G52-01 diperbaiki di PR yang sama (perubahan kecil di compose). Syarat ini wajib
sebelum flag diaktifkan di env mana pun.

## Tabel temuan

| ID | Severity | Lokasi | Ringkas | Risiko |
|---|---|---|---|---|
| SEC-G52-01 | Medium | `infra/compose/docker-compose.yml:982`, `:984`, `:1826-1828` | `INSIGHT_FNB_PERSONA_ENABLED`, `INSIGHT_ACCEPT_EIT2` tidak diteruskan ke container Odoo/insight-web; default `EFN_INSIGHT_TOKEN_VERSION` compose = 2 (kontrak = 3) | R-S22, R-S05 |
| SEC-G52-02 | Low | `efn_insight/controllers/main.py:175-179`, `:299-305`; `lib/insight_token.py:421` | exec+store+marketing (F&B) atau exec+store+category/dc (SMD) → T5 gagal → `ValueError` tak tertangkap → HTTP 500 | R-S22 (ketersediaan) |
| SEC-G52-03 | Low | `insight-web/lib/session.ts:36,130,199`; urutan deploy §28.15.10 | Residu rollout: sesi eis2 dari Odoo pra-G52 untuk user yang kini store-only tetap PT penuh ≤ 2 jam setelah upgrade (dan selama jeda langkah 1→2) | R-S22 |
| SEC-G52-04 | Low | `insight-web/lib/fnb-personas.ts:49`, `:70` | Penutupan halaman untuk sesi ber-cabang di proxy = denylist; halaman F&B baru otomatis terbuka di lapis 1 | R-S22 |
| SEC-G52-05 | Info | `efn_insight/models/efn_insight_esb_branch.py:49-52` | Unik global `(esb_brand, esb_branch_id)` → admin PT lain (BCF/BCR) bisa tahu keberadaan/menyerobot pemetaan; data tetap fail closed (E9) | R-S22 |
| SEC-G52-06 | Info | `infra/clickhouse/access/insight-esb-policy.sh:55`; SEC-IR-01 | Kunci scope tetap literal di `USING`; setting baru `SQL_efn_brs` menambah satu pemicu galat `getSetting` (E6) saat versi campuran | R-S05 |

## Detail

### SEC-G52-01 (Medium) — Flag G52 tidak sampai ke container; `INSIGHT_ACCEPT_EIT2=false` mustahil diterapkan

**Bukti.** `docker-compose.yml` memakai daftar `environment:` eksplisit tanpa `env_file` (grep `env_file` di `infra/compose` = 0).
- Odoo (`:977-986`): ada `INSIGHT_ENABLED`, `EFN_INSIGHT_TOKEN_VERSION: ${EFN_INSIGHT_TOKEN_VERSION:-2}`, dan `INSIGHT_SM_ENABLED`.
  **Tidak ada** `INSIGHT_FNB_PERSONA_ENABLED`.
- insight-web (`:1824-1828`): ada `INSIGHT_ENABLED`, `INSIGHT_ACCEPT_EIT1`, dan `INSIGHT_SM_ENABLED`. **Tidak ada**
  `INSIGHT_FNB_PERSONA_ENABLED` dan `INSIGHT_ACCEPT_EIT2`.
- `scripts/deploy.sh:414-417` dan `scripts/lib/deploy-validate.sh:26,363,408` sudah menulis/memvalidasi keempat variabel di `.env`,
  tetapi interpolasi compose tidak pernah merujuknya, kecuali `EFN_INSIGHT_TOKEN_VERSION`.

**Dampak.**
1. Langkah (3) urutan deploy §28.15.10 (`INSIGHT_ACCEPT_EIT2=false` setelah Odoo 19.0.3.0.0) tidak berefek.
   `config.ts:81` jatuh ke default `true`, sehingga `/sso` menerima eit2 dan cookie eis2 diterima selamanya. Ini mengulang pola
   SEC-G47-02 yang untuk eit1 sudah ditutup lewat compose (`:1825-1826`).
2. `INSIGHT_FNB_PERSONA_ENABLED` selalu `false` di Odoo dan insight-web. G52 tidak bisa diaktifkan, dan kepala toko store-only
   kehilangan semua akses ESB (fail closed, bukan kebocoran).
3. `make up` atau compose tanpa `deploy.sh` → Odoo menerbitkan eit2 (default compose 2), padahal kontrak dan `_token_version()`
   (`main.py:84-88`) default 3.

Skenario: operator mengikuti runbook dan menyetel `INSIGHT_ACCEPT_EIT2=false` di `.env`, lalu menganggap jalur eit2 tertutup.
Faktanya insight-web tetap menerima eit2 dan eis2, termasuk eis2 lama berisi PT penuh (lihat SEC-G52-03) dan token eit2 dari
instance Odoo yang belum di-upgrade dengan kunci SSO yang sama.

**Rekomendasi.** Tambahkan ke `docker-compose.yml`:
- Odoo: `INSIGHT_FNB_PERSONA_ENABLED: ${INSIGHT_FNB_PERSONA_ENABLED:-false}`, dan ubah default
  `EFN_INSIGHT_TOKEN_VERSION: ${EFN_INSIGHT_TOKEN_VERSION:-3}`.
- insight-web: `INSIGHT_FNB_PERSONA_ENABLED: ${INSIGHT_FNB_PERSONA_ENABLED:-false}` dan
  `INSIGHT_ACCEPT_EIT2: ${INSIGHT_ACCEPT_EIT2:-true}`, beri komentar SEC-G47-02/§28.15.10.
- Tambahkan test CI (pola `docker compose config`) yang memastikan setiap variabel di `deploy-validate.sh` punya rujukan di
  compose. Periksa juga file override lokal (mis. `prod-local.overrides` di env efn-prod). Reviewer tidak melihatnya, jadi ini
  PLAUSIBLE: override itu mungkin sudah meneruskan sebagian flag.

### SEC-G52-02 (Low) — Kombinasi exec+store+{marketing|category|dc} menghasilkan HTTP 500

**Bukti.** Di `_esb_scope` (`main.py:175-179`), untuk user non-store-only:
`personas = G' ∩ {exec, marketing}`, lalu `store` ditambahkan bila `store ∈ G'` dan `exec ∈ G'`. Akibatnya:
- G = {exec, marketing, store} → persona ESB {exec, marketing, store}.
- G = {exec, store, category} dengan SMD → hook SM (`efn_insight_sm/models/res_users.py:61-67`) tetap menerbitkan store karena
  exec ada di `org`, sehingga `roles` = {category, exec, store}.

Kedua hasil melanggar T5 (`store ∈ P ⇒ P ⊆ {exec, store}`, `insight_token.py:421`). `build_payload_v3` dipanggil **di luar**
`try` (`main.py:299-305`; hanya `encode_token_v3` yang dibungkus), sehingga `ValueError("bad_claims")` naik menjadi HTTP 500
dengan traceback di log server. Tabel §28.15.11 tidak memuat baris ini, dan `test_insight_g52.py` tidak mengujinya.

**Dampak.** Akses ditolak (fail closed); tidak ada kebocoran. Direksi yang juga ditandai kepala toko tidak bisa membuka Insight
sama sekali, dan setiap percobaan menghasilkan error 500 di log.

**Rekomendasi.** Pilih salah satu dan catat di kontrak (dua sisi):
- Revisi T5 menjadi `store ∈ P ⇒ (exec ∈ P ∨ P = {store})`. Invarian keamanannya sama dengan SEC-G51-01 ("setiap view yang
  mengizinkan store juga mengizinkan exec"), jadi `store` di samping `exec` tidak menambah akses.
- Atau, di Odoo, buang `store` dari P bila P memuat persona selain exec/store.

Selain itu, bungkus `build_payload_v3`/`build_payload_v2` agar `ValueError` menjadi halaman info generik plus log kode tanpa
data, bukan 500. Tambahkan test kombinasi tersebut.

### SEC-G52-03 (Low) — Residu rollout: eis2 PT penuh untuk user yang kini store-only

**Jawaban pertanyaan downgrade.** Dengan kode Odoo baru, **tidak ada** jalur yang memberi user store-only eit2/eis2
berisi PT penuh. `_esb_scope` (`main.py:159-162`) mengosongkan bagian ESB untuk `version != 3`, `¬F`, atau `store_suppressed`,
berdasarkan `held` (bukan flag). Versi token ditentukan env server, bukan user. Aturan konsistensi eit2 juga melarang persona
tanpa kode SM, sehingga eit2 tidak bisa membawa persona F&B.

**Residu.** Sebelum G52, user F&B dengan grup store (tanpa SMD) mendapat eit2 `roles=["user"]` dengan PT penuh. Cookie eis2
hasilnya tetap sah sampai `SESSION_TTL_SEC` = 7200 dtk (`session.ts:36`) selama `acceptV2` true (`session.ts:130`). Sesi itu
dipetakan `brs=[]` (`session.ts:199`), sehingga lolos kelas PT. Jendela lebih panjang terjadi antara langkah (1) dan (2) urutan
deploy: Odoo lama masih menerbitkan eit2 PT penuh yang diterima insight-web baru. Ini tingkat akses lama, bukan eskalasi baru.

**Rekomendasi.** Tambahkan ke runbook rilis G52: setelah `-u efn_insight,efn_insight_sm`, (a) rotasi `insight_session_key`
untuk membatalkan semua sesi stateless, (b) set `INSIGHT_ACCEPT_EIT2=false` (bergantung SEC-G52-01), dan (c) persingkat jeda
langkah 1→2.

### SEC-G52-04 (Low) — Penutupan rute sesi ber-cabang memakai denylist

**Bukti.** `fnbRouteDenial` (`fnb-personas.ts:67-74`) menolak sesi ber-cabang hanya pada `BRANCH_BLOCKED_PATHS`
(`/sales`, `/payments`, `/flow`, `/distribution`, `/forecast`, `/data-quality`). Rute lain non-persona lolos lapis 1.

**Dampak saat ini:** tidak ada. Semua halaman F&B saat ini memakai `pageCtx` (`page-ctx.ts:26`, redirect bila `brs ≠ []`), dan
query ke view kelas PT mengembalikan 0 baris (lapis 3). Risikonya laten: halaman baru yang tidak memakai `pageCtx` dan membaca
sumber di luar `efn_insight.v_esb_*` (mis. `efn_forecast`) akan terbuka untuk kepala toko.

**Rekomendasi.** Ubah ke allowlist untuk sesi ber-cabang: `/fnb/store`, `/glosarium`, rute persona SM (yang tetap diperiksa
`smRouteDenial`), `/` (dialihkan), dan path publik. Tambahkan test yang mengiterasi semua `app/(app)/**/page.tsx`.

### SEC-G52-05 (Info) — Unik global pemetaan cabang

`UNIQUE(esb_brand, esb_branch_id)` (`efn_insight_esb_branch.py:49-52`) berlaku lintas company, sedangkan record rule
(`efn_insight_security.xml:107-113`) menyembunyikan record PT lain. Untuk Bacha (BCF+BCR satu akun), admin BCR bisa:
- mengetahui bahwa `bacha:15` sudah dipetakan (pesan duplikat), atau
- lebih dulu membuat `bacha:15` di BCR, sehingga admin BCF tidak bisa memetakannya.

Data tetap aman. Odoo hanya memberi `codes=[BCR]`, dan row policy `has(codes, company_code)` menolak baris BCF (negatif E9).
Diterima. Catat di README bahwa koreksi pemetaan lintas PT butuh admin dengan kedua PT.

### SEC-G52-06 (Info) — SEC-IR-01 masih terbuka; G52 menambah satu pemicu galat

Predikat `insight-esb-policy.sh:55` tetap menanam `insight_scope_key` literal. Tanpa `SQL_efn_brs`, `getSetting` melempar galat
(E6, sengaja fail closed). Selama versi campuran (insight-web lama dengan policy baru), galat itu masuk
`system.query_log.exception`, kanal yang sama dengan SEC-IR-01. Perbaikan tetap mengikuti SEC-IR-01 (default `''` untuk
setting `SQL_efn_*` + komitmen hash). Pastikan langkah deploy (1) atomik.

## Kontrol yang sudah benar (diverifikasi dari kode)

- **Pencabutan PT penuh tanpa flag (L-241):** `main.py:159-167` memakai `held` (grup), bukan flag atau versi. Untuk v1/v2,
  `¬F`, atau store tersupresi, bagian ESB kosong. `group_insight_user` eksplisit tidak memulihkan PT penuh.
- **`brs` hanya dari record sah:** `_user_branches` (`main.py:132-146`) membaca sudo hanya relasi milik user, lalu memeriksa
  `active`, `is_store`, company aktif berkode ESB, company ada di `res_company_users_rel`, dan brand ↔ kode PT.
- **Token eit3:** prefix ikut di input HMAC (pemisahan domain eit1/2/3 dengan kunci sama). Kunci persis 14, `brs` regex + ≤ MAX_ID
  + urut ASCII unik, T1–T7 di pembuat dan penerima (`claims.ts:184-212` identik dengan `insight_token.py:397-429`), jti replay,
  TTL 60 dtk.
- **sig3 dan kelas policy:** `KEY|cids|codes|brs`. Regex format melarang `|`, sehingga tidak tertukar dengan sig v1/sig2. Kelas
  PT mewajibkan `brs=''`. Kelas BR memakai `concat(brand,':',branch_id) ∈ brs` sekaligus `company_code ∈ codes`. Ada 14 policy
  dengan jumlah terkunci. Init gagal tertutup bila ada view `efn_insight` tanpa policy (`clickhouse-init.sh:456-464`). Fallback
  sig v1 dihapus.
- **View toko** tanpa `cogs`/`mdr`/`net` pembayaran. `v_esb_store_payment_daily` membuang `branch_id=0`.
  `v_esb_store_receipt_daily` = PT/cabang **tujuan** via INNER JOIN `dim_esb_branch` (cabang tak dikenal tidak muncul). Kolom
  identitas/member tidak ada (PDP L-249).
- **insight-web:** ekspor menolak sesi ber-cabang (`route.ts:47`). `pageCtx` dan layout tidak menjalankan query PT untuk sesi
  ber-cabang. `branch` dipotong ke `brs` (`fnb-store.ts:49-68`). `pt` = kode sesi. `smRouteDenial` kini mewajibkan kode SM.
  `/fnb/*` menolak `v < 3`.
- **`scopedQuery`/`navItemActive` (0aab9ab):** murni pembentuk tautan/penanda aktif. `pt`/`branch` dari URL tetap divalidasi
  server (`parseFilters`) terhadap kode sesi. Tidak ada dampak keamanan.
- **Odoo ACL/audit:** `efn_insight_esb_branch_ids` `groups=base.group_erp_manager`, bukan SELF_WRITEABLE (user tidak bisa
  menugaskan cabang sendiri). Model hanya ACL erp_manager + rule multi-company. Keduanya masuk `efn_audit.CONTROL_SPECS`.
  `efn_role` merujuk xmlid baru, dan `store` sengaja tidak sensitif (cakupan sempit).
- **Migrasi:** `rename_persona_xmlids` hanya mengubah `ir_model_data.module` (id grup dan keanggotaan tetap), idempoten, dan
  `NOT EXISTS` mencegah bentrok. Dipanggil dari kedua modul. Hasilnya bersih: tidak ada user yang naik hak. Yang berkurang
  hanya kepala toko yang dulu mendapat PT penuh, dan itu disengaja (L-241).
- **Log:** Odoo `ir.logging` dan insight-web hanya mencatat `brs_n`, roles, sub, dan jti, tanpa nama, token, atau daftar cabang.

## Belum terverifikasi

- Test Odoo/Jest/pytest dan harness ClickHouse E1–E9 tidak dijalankan reviewer. `git diff` tidak dijalankan (file dibaca dari
  tree branch).
- Nilai kolom `brand` di mart diasumsikan huruf kecil (`pb`, `bacha`), sesuai harness. Bila berbeda, akibatnya fail closed
  (0 baris), bukan bocor.
- Upgrade nyata dari DB demo SMD (keanggotaan grup sebelum/sesudah) belum diamati.
- Override compose lokal di luar repo (efn-prod) tidak diperiksa (SEC-G52-01).
