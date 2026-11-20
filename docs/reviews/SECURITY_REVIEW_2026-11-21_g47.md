# Security review G47 — Insight supermarket (2026-11-21)

Reviewer: efn-security-reviewer (read-only). Branch `feat/wave-47-insight-supermarket` (worktree `efn-platform-g47`).
Kontrak: docs/04 §28.13 (v20.3), `docs/fit-gap/G47_INSIGHT_SM.md`, `docs/design/insight-supermarket.md`.

## Ringkasan eksekutif

- Tidak ada temuan Critical/High. Token eit2/eis2, row policy ClickHouse, ETL, dan ACL Odoo secara umum sudah benar dan fail-closed.
- **1 Medium:** persona dan `wids` tidak saling terikat. User yang memegang persona `store` + persona tingkat PT (terutama `marketing`, atau `dc` untuk markdown) bisa melihat data yang hanya boleh dilihat kepala toko, untuk **semua** outlet PT. Penyebabnya desain kontrak §28.13.3/§28.13.7, bukan bug implementasi.
- **3 Low/Info:** flag `INSIGHT_SM_ENABLED` / `INSIGHT_ACCEPT_EIT1` / `EFN_INSIGHT_TOKEN_VERSION` belum diteruskan di compose, sehingga jalur eit1/eis1 tidak bisa dimatikan; cakupan sesi tertunda hingga 8 jam setelah dicabut; tanda tangan cakupan memakai SHA256(key||msg), bukan HMAC.
- Rekomendasi merge: boleh merge selama `INSIGHT_SM_ENABLED` tetap OFF di prod. SEC-G47-01 wajib ditutup (atau kombinasi persona dilarang secara operasional dan diterima tertulis) sebelum flag dinyalakan.

Keterbatasan: reviewer tidak punya shell. `git diff origin/main...HEAD` tidak dijalankan; cakupan file ditentukan lewat glob/grep pada file G47 yang disebut lead. Harness `g47_row_policy_harness.sh` (8 test negatif, CH 24.8) tidak dijalankan ulang oleh reviewer.

## Tabel temuan

| ID | Severity | File:line | Ringkas | Risiko |
|---|---|---|---|---|
| SEC-G47-01 | Medium | `infra/clickhouse/access/insight-sm-policy.sh:83-87`; `efn_insight_sm/models/res_users.py:50-62`; `insight-web/lib/sm-filters.ts:143-146` | `hasAny(personas, PERS)` dan `has(wids, warehouse_id)` dievaluasi terpisah, dan `wids` adalah gabungan semua persona. Akibatnya store+marketing bisa melihat sales/waste/stok/expiry/markdown/usulan semua outlet, dan store+dc bisa melihat markdown semua outlet | R-S (otorisasi data), R-L01 |
| SEC-G47-02 | Low | `infra/compose/docker-compose.yml:980,1820`; `insight-web/lib/config.ts:76` | `INSIGHT_SM_ENABLED`, `INSIGHT_ACCEPT_EIT1`, `EFN_INSIGHT_TOKEN_VERSION` tidak diteruskan ke container. Akibatnya eit1/eis1 selalu diterima (default true) dan tidak bisa dimatikan setelah cutover. Fitur SM sendiri tetap OFF (fail-closed) | R-S |
| SEC-G47-03 | Low | `insight-web/lib/session.ts:33,149-153` | Sesi `eis2` stateless 8 jam. Mencabut persona atau memindahkan outlet kepala toko baru berlaku setelah cookie lama kedaluwarsa (paling lama 8 jam) | R-S |
| SEC-G47-04 | Info | `insight-web/lib/scope.ts:55`; `insight-sm-policy.sh:82` | `SQL_efn_sig2 = SHA256(key‖…)`, bukan HMAC. Length-extension hanya teoretis karena regex format menolak byte padding. Pola warisan §26.7 | R-S |

## Detail

### SEC-G47-01 (Medium): persona × wids tidak terikat; kombinasi store + persona PT memperluas akses

**Bukti.**
- Hook Odoo `efn_insight_sm/models/res_users.py:50-62`: `wids |= {0} | wh_all` untuk persona PT, ditambah `wids |= own` untuk store. Hasilnya satu daftar gabungan.
- Predikat `insight-sm-policy.sh:83` hanya mensyaratkan `hasAny(personas, PERS_view)`, lalu baris 85/87 mensyaratkan `warehouse_id ∈ wids` (gabungan).
- Daftar view baris 24-41: `v_sm_sales_daily`, `v_sm_outlet_daily`, `v_sm_waste_daily`, `v_sm_stock_critical`, `v_sm_replenishment*`, `v_sm_lot_expiry`, `v_sm_expiry_daily`, `v_sm_dc_fill_rate`, `v_sm_forecast_accuracy_weekly`, `v_sm_oos_product_daily`, `v_sm_stock_daily` memuat `store` tetapi tidak memuat `marketing`; `v_sm_markdown` memuat `store` tetapi tidak memuat `dc`.
- UI ikut melebar: halaman `/store` memakai `effectiveWh()` = semua `wids` sesi (`sm-filters.ts:143-146`).

**Skenario.** Admin memberi kepala toko SMD01 grup "Insight SM: Marketing" (wajar di tim kecil), atau memberi staf marketing grup Kepala toko untuk satu outlet. Token yang terbit: `roles=[marketing,store,user]`, `wids=[0,DC,SMD01..SMD05]`. Query `sm_sales_net` ke `v_sm_sales_daily` lolos lewat persona `store` dan lolos filter `wids` karena semua outlet ada (berkat `marketing`). Hasilnya omzet, waste HPP, near-expiry, dan usulan replenishment kelima outlet terlihat, padahal tidak ada satu pun persona yang memberi itu. Kasus store+dc membuka `v_sm_markdown` semua outlet. Kombinasi dengan exec/category tidak menambah apa pun karena keduanya sudah superset.

**Perbaikan (pilih satu, keduanya butuh revisi §28.13.3/§28.13.7):**
1. *Jangka pendek, Odoo saja:* di `_efn_insight_sm_scope`, bila `store ∈ held` dan `held ∩ ORG_PERSONAS ≠ ∅`, persona `store` **tidak** dimasukkan ke `roles`. Persona PT sudah memberi semua outlet untuk view yang dia miliki; view khusus store untuk outlet sendiri hilang, dan itu pilihan konservatif. Tambah test di `efn_insight_sm/tests/test_scope.py` dan test negatif ke-9 di harness (`personas=marketing,store` → 0 baris `v_sm_sales_daily` untuk outlet di luar `efn_outlet_warehouse_ids`).
2. *Benar secara model:* pisahkan cakupan per jenis. Klaim baru `swids` (outlet store) dan setting `SQL_efn_store_wids` ikut ditandatangani. Predikat kelas W menjadi `(hasAny(personas, PERS ∩ ORG) AND has(wids_org, warehouse_id)) OR (has(personas,'store') AND 'store' ∈ PERS AND has(store_wids, warehouse_id))`. Halaman `/store` hanya memakai `store_wids`.

### SEC-G47-02 (Low): flag G47 tidak diteruskan di compose

**Bukti.** `docker-compose.yml:980` (odoo) dan `:1820` (insight-web) hanya meneruskan `INSIGHT_ENABLED`. Grep `INSIGHT_SM|ACCEPT_EIT1|TOKEN_VERSION` di `infra/`, `scripts/`, `.github/` tidak menemukan apa pun. `config.ts:76`: tidak diisi = `acceptEit1 = true`.

**Dampak.** Jalur downgrade eit1/eis1 (§28.13.5) tidak bisa ditutup di env mana pun. Risiko langsungnya kecil, karena eit1 tetap ber-HMAC, menolak `SMD`, dan prefix ikut ditandatangani sehingga tidak ada konfusi eit1↔eit2. Namun kontrol "matikan eit1 setelah cutover" tidak bisa dijalankan. Fitur SM juga tidak bisa dinyalakan (fail-closed; masalah fungsional).

**Perbaikan.** Tambahkan `INSIGHT_SM_ENABLED: ${INSIGHT_SM_ENABLED:-false}` ke kedua service, `EFN_INSIGHT_TOKEN_VERSION: ${EFN_INSIGHT_TOKEN_VERSION:-2}` ke odoo, dan `INSIGHT_ACCEPT_EIT1: ${INSIGHT_ACCEPT_EIT1:-true}` ke insight-web. Lengkapi `.env.example`. Tambahkan juga pemeriksaan di `scripts/lib/deploy-validate.sh` yang menolak `INSIGHT_SM_ENABLED=true` di prod tanpa `INSIGHT_ENABLED=true`. Setelah semua Odoo menerbitkan eit2, set `INSIGHT_ACCEPT_EIT1=false`.

### SEC-G47-03 (Low): pencabutan cakupan tertunda hingga 8 jam

`SESSION_TTL_SEC = 28800` (`session.ts:33`). Cookie `eis2` membawa `wids`/persona yang ditandatangani. Contoh: kepala toko dipindah dari SMD01 ke SMD02, atau grup Direksi dicabut. Selama paling lama 8 jam ia tetap melihat cakupan lama. Ini warisan §26.5; satu-satunya pencabutan darurat adalah rotasi `insight_session_key`, yang mengeluarkan semua user.
**Perbaikan.** TTL `eis2` 1–2 jam (re-SSO dari Odoo murah), atau klaim `srev` (counter perubahan cakupan per user di Odoo) yang dicek saat query. Minimal, catat prosedur rotasi kunci di runbook akses.

### SEC-G47-04 (Info): skema tanda tangan cakupan

`SHA256(KEY|cids|codes|wids|personas|cats)` adalah konstruksi prefix-MAC. Pemisah `|` dilarang di setiap field dan regex `match()` menolak byte non-ASCII/padding, sehingga length-extension tidak menghasilkan setting yang lolos. Pada revisi kontrak berikutnya, sebaiknya beralih ke `lower(hex(HMAC('SHA256', msg, KEY)))`, karena fungsi `HMAC` tersedia di ClickHouse ≥ 24.x; ini perlu verifikasi versi.

## Kontrol yang sudah benar (diverifikasi di kode)

- **Token eit2** (`insight_token.py`, `token.ts`): format regex panjang terbatas; HMAC dihitung atas `payload_b64` yang diterima dan dibandingkan `timingSafeEqual`/`compare_digest`; prefix ikut ditandatangani (tidak ada konfusi eit1↔eit2); tepat 13 kunci; `v/aud/iss`; TTL ≤ 60 detik, skew 30 detik; jti hex-32 dicek sebelum dan disimpan hanya setelah semua validasi; daftar wajib unik & urut (bentuk kanonik); aturan konsistensi 1–3 dicek di pembuat dan penerima; eit1 tetap menolak `SMD`.
- **Cakupan tidak bisa diperlebar dari browser**: `scopeSettingsSm` hanya dari sesi terverifikasi; `wh` disaring ⊆ `wids` sesi (`sm-filters.ts:83-85`); `cat` hanya filter (L-189, bukan pembatas); semua query katalog konstanta + `{param:Type}`; serializer menolak kutip/backslash/kontrol.
- **Row policy**: view `DEFINER` dengan kolom eksplisit; grant definer minimum tanpa ESB/GL/loyalty; policy dibuat dengan `--log_queries=0`; kunci divalidasi hex ≥ 64; jumlah policy dikunci 29; setting hilang → galat (fail-closed); `company_id != 0`; persona store tidak pernah mendapat `wids` 0.
- **Odoo**: hook cakupan privat (bukan RPC); company dari `res_company_users_rel` (bukan switcher), PT arsip disaring; `efn_outlet_warehouse_ids ∩ WH_ALL`; `efn_insight_categ_ids` hanya di form `group_erp_manager`; `efn.insight.sm.kpi` punya record rule `company_ids`, ACL tulis hanya `kpi_admin`, validasi registri tertutup, kunci/PT immutable, audit create/write/unlink; `res.users` CONTROL_SPECS melacak `efn_outlet_warehouse_ids` + `efn_insight_categ_ids`; 4 persona + kpi_admin masuk `SENSITIVE_GROUP_XMLIDS`; audit `ir.logging` tanpa token/nama/daftar wids.
- **Migrasi `efn_setup_supermarket` 19.0.3.0.0**: dijaga `module.demo`; user demo baru tanpa password/email/HP; kpi_admin tidak diberikan.
- **ETL**: kode PT divalidasi ⊆ SM_CODES sebelum jadi literal SQL; parameter cursor lewat `%(...)s`; grant PG per kolom tanpa kolom pelaku (`approved_by`, `changed_by`, `activated_by`, `maker_id`) dan tanpa `res_partner` (view `efn_etl.purchase_partner` hanya nama perusahaan); `22-platform-g47.sql` view tanpa `member_id`/payload; CH `etl_writer` hanya SELECT/INSERT, log bias append-only dicabut ALTER/DROP.
- **insight-web**: galat CH generik + ref; SSO cek Origin sebelum rate limit; body ≤ 4096; log tanpa token/nama/wids; export CSV tetap khusus ESB (sesi SM-saja → 403); tautan Odoo memakai `rel="noopener noreferrer"`; tidak ada nomor HP di view/mart SM.
