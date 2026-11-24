# Laporan efn-lead

Laporan kerja Lead Agent (konvensi CLAUDE.md "Tim agent"). Bagian "Status sesi" diperbarui oleh `/handoff` dan dibaca oleh `/lanjut`.

## Status sesi

**Folder kerja (21 Nov 2026):** hanya `E:\Projects\efn-platform` (semua development, satu branch) dan `E:\Projects\efn-platform-smd`
(rumah env `efn-prod` sesuai runbook multi-env-local §2: secret, CA, `.env`, override lokal; 28 container bind-mount dari sini; checkout
detached ke commit yang diuji, jangan dikembangkan, jangan dihapus). Worktree fitur lain dihapus (branch tetap ada). `main` lokal bisa
tertinggal: selalu branch dari `origin/main`.

**Selesai sesi ini (21 Nov 2026) — branch `fix/insight-review` (review pemilik proyek atas Insight, 11 poin):**
- `19c1fac` "Kembali ke Odoo" ke web client `/odoo` (sebelumnya memutar balik ke Insight).
- `0fcbd0d` view `efn_insight.v_esb_movement_daily` (rekonsiliasi kiriman semua kelompok barang, asal/tujuan) + `v_esb_coverage` (cakupan
  hari per entitas), row policy `efn_insight_scope`. Diterapkan ke efn-prod (`clickhouse-init` exit 0); sanity: FIN dari CK di movement =
  `ck_out` flow (25.105); user efn_insight tanpa scope ditolak.
- `50c83d4` insight-web: nav 3 grup lipat (Ringkasan / F&B / Supermarket, Forecast tanpa "segera"), filter langsung diterapkan + tanggal
  terbawa ke semua halaman, toolbar baru, cabang non-penjualan (HEAD OFFICE, gudang, damage) keluar dari filter/peringkat, kartu kiriman
  per asal, kolom Keterangan "kirim > produksi", banner cakupan + delta n/a, cari/saring tabel + kolom kategori, Keterangan status,
  legend chip, glosarium. Verifikasi: vitest 697 lulus, typecheck, lint (1 warning lama), build; 41 query katalog dijalankan ke
  ClickHouse efn-prod tanpa galat; render server-side 18 halaman dengan sesi uji eis2 (exec PB+SMD): `pt=PB` tidak memuat data SMD.
- `f96e501` esb-extract: slice sales mengisi `dim_esb_menu` dari baris penjualan (`/menu/browse` tidak memuat 664/740 kode terjual).
  Backfill dari `raw_doc` di efn-prod: +1.004 kode, kategori kosong 94% -> 0%. pytest + ruff 0.16.10 lulus.

**Temuan data (bukan bug tampilan):**
- Extractor ESB terjadwal tidak berjalan di efn-prod (overlay `docker-compose.esb.yml` tidak dipakai; env tanpa kredensial ESB; §1a
  runbook esb-extract butuh DPO/Legal untuk APP_ENV=production). Data masuk lewat pipeline DR sesi `efn-platform-d4`
  (`clickhouse-dr` -> `copy2prod.sh`). Per 21 Nov malam: sales 17–20 Nov selesai di dr; non-sales 1 tahun (production, delivery, transfer,
  payment, fin_sold 2025-11-17..2026-11-16) berjalan, top-up efn-prod ±04:00–06:00 WIB 22 Nov. Payment 17–18 Nov galat di ESB.
- Alur barang PB: CK mengirim FIN + WIP; bahan baku/kemasan/supplies dari gudang WHProd/Warehouse Palem (juga ke CK); KACS
  mendistribusikan bahan baku. FIN000028 diproduksi di CKWH Alam Sutera tetapi dikirim dari Central Kitchen.

**Sedang dikerjakan:** review keamanan `efn-security-reviewer` atas branch; lalu push + satu PR (user merge manual).

**22 Nov pagi — "Data tidak dapat dimuat":** log insight-web 07:58–08:00 WIB = ClickHouse code 159 (timeout 30 dtk profil
`insight_readonly`) + code 241 (memori > 2 GB per query) + antrean semaphore habis, terutama `exec_*`, `flow_*`, `list_categories`.
Penyebab: setelah top-up 1 tahun, view `v_esb_sales_branch_daily` / `v_esb_sales_daily` / `v_esb_flow_daily` menghitung `FINAL` +
filter checkpoint + `uniqExact` + JOIN atas seluruh fakta setiap query; Docker Desktop juga sempat tidak responsif (tekanan memori).
Mitigasi UI: layar memuat yang memblokir (README insight-web, belum di-commit, sudah ter-deploy di efn-prod). Perbaikan akar
(belum): tabel agregat harian (mis. `agg_esb_sales_branch_daily`, `agg_esb_flow_daily`) diisi saat ekstraksi/copy, view membaca agregat.

**Langkah berikutnya:**
1. ~~Top-up efn-prod dari sesi d4~~ **selesai 22 Nov 04:50 WIB**: `v_esb_coverage` PB 2025-11-17..2026-11-20 = 369 hari untuk
   sales/production/delivery/fin_sold, transfer 368 (1 Agt 2026 galat ESB), payment 350 (19 tanggal ditolak ESB permanen).
   Isi ulang `dim_esb_menu` dari `raw_doc`: +16 kode (2.317 -> 2.333); kategori kosong 0,1% baris / 0% net (1 tahun).
   Berikutnya dari d4: sales 2025-02-01..2025-11-16 lalu top-up kedua -> jalankan ulang SQL yang sama.
2. Uji browser oleh user (toolbar, popover tanggal, sidebar ciut, legend, popover istilah) — belum diuji di browser nyata.
3. ~~Rotasi `insight_scope_key` env efn-prod~~ **selesai 21 Nov 20:23 WIB** (fingerprint lama 8b1061dae150 -> baru 3d6e8c3f6b01;
   43/43 row policy `efn_insight_scope` memuat kunci baru; halaman memuat data; edge 9443 = 200). Kunci lama tidak berlaku lagi,
   jadi baris `query_log` lama tidak perlu dihapus. Perbaikan struktural SEC-IR-01 (komitmen hash, revisi ADR-041) belum.
4. Usulan infra: 14 sidecar TLS `network_mode: "service:<x>"` hanya `depends_on: [x]` -> recreate service utama membuat sidecar
   menempel ke netns container lama (gejala: Insight 502 Bad Gateway 21 Nov). Tambah `depends_on: {x: {condition: service_started,
   restart: true}}` atau selalu recreate sidecar bersama service utama.

**Blocker / pertanyaan untuk user:**
1. Menyalakan extractor ESB harian langsung di efn-prod butuh kredensial ESB di env itu + pemenuhan §1a (DPO, user API read-only, Legal).
2. (lama) Command global `~/.claude/commands/{handoff,lanjut,security-check}.md` menimpa versi project EFN.

**Gotcha:**
- Recreate `insight-web` (atau service lain ber-sidecar TLS) WAJIB diikuti `up -d --no-deps --force-recreate insight-web-tls`,
  kalau tidak edge mengembalikan 502 (sidecar memakai netns container lama).
- Container insight-web rootfs read-only: skrip uji lewat stdin (`node --input-type=module -`).
- `openssl rand -hex` di Git Bash Windows menulis CRLF: normalisasi (`tr -dc 0-9a-f`) sebelum menjadi file secret.
- ClickHouse: `FINAL` + `ARRAY JOIN` pada `raw_doc` gagal; alias agregat tidak boleh sama dengan kolom dalam yang dipakai di WHERE.
- Sesi uji eis2 butuh peran dasar `user` + persona, `wids` memuat 0 hanya untuk persona tingkat PT (lib/claims.ts scopeConsistent).
- gitleaks CI memindai semua branch; squash merge memunculkan fingerprint baru → inline `gitleaks:allow` untuk fixture.

**Kandidat keputusan:** struktur nav 3 grup + filter auto-apply (keputusan user 21 Nov, dicatat di `docs/design/insight-web.md`);
company_code `v_esb_movement_daily` = PT cabang asal (alternatif dua baris asal/tujuan masih terbuka, docs/04 §26.7).

### Riwayat
- 21 Nov 2026 — konsolidasi worktree; review Insight 11 poin (`fix/insight-review`).
- 20–21 Nov 2026 — adopsi Claude Standard Kit (#120), gitleaks main hijau (#129).

### G65 — migrasi ESB → Odoo PB: pembelian, harga beli supplier, HPP cutover, stok awal (24 Nov 2026)
- Keputusan user 24 Nov: semua yang mungkin termasuk histori pembelian; PB saja; gladi efn-prod; cutover uji
  2026-12-01; saldo awal = stok awal saja (TB & AR/AP tetap NOT_BUILT, Q-ESB21).
- Probe GET prod PB: pricelist & cogs-history kosong, inventory-valuation 401; sumber stok/HPP = baris `Beginning`
  `/report/stock-movement` (limit 100, filter branchCode); pembelian = purchase-recapitulation (limit 1000) + header PO
  (join purchaseNum); `currencyID` 1 = IDR → disimpan `currencySign`.
- Alur: fit-gap G65 (functional) + ADR-042 §0b & docs/04 §27.13 (architect) → W-A esb-extract, W-B scripts/migration,
  W-C Odoo (paralel) → QA (2 bug P2 satuan harga diperbaiki) + security (0 C/H/M, 4 Low ditutup) → putaran perbaikan.
- Verifikasi lead: ruff check/format 3 paket, pytest esb-extract & scripts/migration hijau, DDL ClickHouse 24.8 2×
  tanpa error, 39/39 test Odoo efn_legacy_history, check-odoo-deps 0 terlarang. check-encryption.sh belum (butuh
  docker compose di skrip; CI). Commit a2212b2, 6e37d19, 4d14116 di `feat/esb-migration-purchase-stock` (worktree
  efn-platform-esbmig), belum push/PR.
- Berikutnya (butuh OK user): deploy ke efn-prod (merge lokal smd, image esb-extract & Odoo, DDL 23-g65, -u
  efn_legacy_history), backfill purchase_line 24 bln (jendela 22:00–05:00 WIB), snapshot stock_opening tanggal hari
  ini untuk dry-run; impor nyata stok + geser cutover 17 Nov → 1 Des pada 1–2 Des.
- Terbuka: Q-ESB25..29, Q-G65-1..7, SEC-G65-04 (DPO), ADR-042 §0b Proposed.

#### G65 gladi efn-prod (24 Nov 2026, lanjutan)
- Keputusan user: tarik data ESB di luar jendela malam (izin beban Q-ESB7 untuk gladi ini); cutover gladi digeser
  17 Nov → 1 Des (override eksplisit `--allow-cutover-shift`, 5ac0ddc); cabang PB 39/40 (Makassar) tanpa kartu stok
  ESB → `--stock-empty-branch` (81aa4dc).
- Termuat nyata di efn-prod: 723 hari purchase_line, 884 `product.supplierinfo`, 11 HPP, histori pembelian 24 bulan
  (70.128 baris; reconcile 5e 1.695 irisan ok, 0 pemblokir). Perbaikan dari gladi: kode produk opsional di arsip
  (222 baris PO aset tetap/seragam tanpa kode, 4d27700).
- Latihan stok 17 Nov (dry-run) ditolak validator karena data: 5 gudang damage tanpa location_map (Q-ESB25), 534 baris
  satuan stok ESB ≠ UoM produk Odoo (EA 229, BOX 188, PACK 57, GR 55; butuh konversi per produk), 709 item cost
  `stock_opening` dengan uom kosong (perlu dicek), 1.235 saldo negatif (default tidak dimigrasi, Q-ESB27).
- Temuan lama (G43): `dim_esb_product.uom` kosong untuk semua produk PB → ekspor master penuh gagal PRODUCT_UOM;
  G65 diekspor per langkah (supplierinfo, cost, legacy_purchase_line) karena master PB sudah ada sejak 22 Nov.
## G66 — Production planning di atas ESB (24 Nov 2026, branch `feat/production-planning`)

**Lingkup:** ADR-051 (Proposed) + docs/04 §30; service baru `services/prodplan` (baca ESB, rencana di PostgreSQL, tulis
transfer/produksi ke ESB lewat allowlist + outbox); modul Produksi di insight-web; grup `pp_planner`/`pp_warehouse` di
`efn_insight`; overlay `docker-compose.prodplan.yml`; runbook `docs/runbooks/prodplan.md`; UAT `docs/qa/G66_UAT.md`.

**Terverifikasi (dijalankan lead):** ruff + pytest prodplan (lulus, 3 integrasi PG di-skip); insight-web lint/typecheck/
1.052 test/build/audit; Odoo `efn_role`+`efn_insight` 139 test; `check-encryption.sh` OK; stack terisolasi `efn-pp`
(postgres + prodplan + sidecar + insight-web) boot; E2E nyata ke ESB staging: PR → permintaan, ledakan BOM, kemasan utuh
+ usulan batch, usulan transfer antar lokasi/cabang, SoD, posting transfer & produksi (dokumen muncul di ESB), cancel
(dokumen terhapus), segarkan stok saat approved, pemulihan baris tersangkut; token `ppa1` TS↔Python; semua halaman
`/produksi` dirender nyata.

**Belum terverifikasi:** UI di browser sungguhan (klik, polling, a11y); login lewat Odoo SSO untuk peran PP; BOM
multi-level pada data nyata; mode costing `standard`; fase produksi setelah simple-transfer di-authorize manusia di ESB;
kirim ulang dokumen yang ditolak ESB (hanya unit test); ESB production (hanya GET); CI GitHub.

**Pelajaran:** ESB memakai ulang nomor dokumen yang dihapus; dokumen produksi via API langsung Authorized; authorize
simple-transfer via API tidak mengubah status; volume postgres baru di stack terisolasi perlu `chown 999:999`;
`check-encryption.sh` butuh `jq` (jalankan di container `docker:27-cli` + `gen-secrets.sh` + `.env` dari contoh).

**Menunggu pemilik proyek:** Q-PP1 (akun API non-admin), Q-PP2 (terima produksi tanpa tahap draft di ESB), Q-PP3 (mode
costing production), Q-PP6 (lokasi sumber transfer), Q-PP13, Q-PP14. Tulis ke ESB production terkunci di kode.

## G66b — Login per pengguna ESB untuk modul Produksi (25 Nov 2026, branch `feat/prodplan-esb-login`)

**Keputusan pemilik proyek 25 Nov:** Q-PP2 diterima; Q-PP1 diganti login per pengguna ke ESB (hanya ada akun admin);
login ESB diduplikasi ke Odoo; POST dari akun admin ke ESB production ditahan; Q-PP6 = izin cabang pengguna di ESB;
SSO sungguhan dan login Odoo lewat ESB ditunda. ADR-051 A1 (§8) + docs/04 §30.17 (termasuk .17 dan .18).

**Lingkup:** prodplan (sesi ESB di memori, `ppa2`, peran dari `/user/access`, cabang dari `/branch/list/user`, outbox
memakai token pemicu, `needs_login`, tanpa akun service), insight-web (`/produksi/masuk`, cookie `__Host-epp`),
modul Odoo baru `efn_esb_identity` (shadow user arsip), overlay `docker-compose.prodplan-odoo.yml`, migrasi `24-*`.

**Terverifikasi (dijalankan lead):** ruff + pytest prodplan; insight-web lint/typecheck/1.159 test/build; Odoo
`efn_core,efn_role,efn_insight,efn_esb_identity` 285 test 0 gagal; `check-encryption.sh` OK; migrasi `24-*` di
PostgreSQL nyata (volume baru); E2E nyata ke ESB staging lewat stack terisolasi `efn-pp`: login, peran, 20 cabang,
cabang asing ditolak, rencana, hitung, submit, `sod_violation`, logout, alur web lengkap, lockout per sumber; log
tanpa password/token/username.

**Belum terverifikasi:** approve/posting dua akun + `needs_login`/`resume-posting` (butuh akun staging kedua, Q-PP17);
provisioning ke Odoo nyata lewat gateway; isolasi cookie terhadap halaman Insight lain (unit test saja); browser
sungguhan; migrasi `24-*` di atas data lama; ESB production.

**Pelajaran:** ESB mengirim `flagActive` sebagai int 1 (bukan boolean) — semua login ditolak sampai diperbaiki, hanya
tertangkap oleh uji staging; `SessionStore` ber-`__len__` membuat `x or default` salah di test; Git Bash mengubah
`--addons-path=/mnt/...` (pakai `MSYS_NO_PATHCONV=1`) sehingga test Odoo "0 tests" palsu; sidecar ghostunnel harus
di-restart setelah prodplan di-restart.

**Menunggu pemilik proyek:** akun ESB staging kedua (Q-PP17); penerimaan risiko credential relay (SEC-240) sebelum
login ke ESB production; Q-PP3, Q-PP13..16, Q-ESBU1..6.
