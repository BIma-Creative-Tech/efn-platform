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

## V-01 log kunjungan visitor anonim (23 Nov 2026)

- Keputusan pemilik proyek: kunjungan web+app dicatat tanpa banner (anonim), tampil di Insight; demo SMD + PB; kanal online
  PB diaktifkan di efn-prod lokal; Insight diisi data kunjungan DEMO. Amandemen ADR-047 §6b, docs/04 v21.4 (L-250..L-255).
- Branch `feat/visitor-log` (worktree `efn-platform-visitor`) + efn-mobile `feat/visitor-tracking` (apps/superapp). Belum
  di-push/PR.
- Verifikasi: jest BFF 1153 (fixture lintas service hijau dgn repo lengkap), vitest web 630, vitest insight 911, pytest ETL
  hijau + ruff, jest mobile 153 + tsc; DDL ClickHouse 24.8 idempoten (2x) + SQL agregat diuji di container sekali pakai.
- Review: QA (3 perbaikan kecil), security (0 High, SEC-V01-01 Medium diperbaiki: ID anonim `efn_vid` terpisah + server
  hanya mengakui consent versi teks aktif; SEC-V01-05 filter segmen dihapus). Sisa Low: SEC-V01-03 rotasi ID harian,
  -04 k-anon bisa dipalsukan, -06 pemberitahuan app/label store, -07 IPv6 /64.
- efn-prod: smd = merge lokal `b4a3a7c` (a03d89f + V-01). Migrasi PG 20, clickhouse-init, image `:e2e` baru (rollback
  `:pre-v01`), flag di `prod-local.overrides` (backup `.bak-20261123`): ONLINE_CHANNELS=SMD,PB, BEHAVIOR_TRACKING_ENABLED,
  BEHAVIOR_ANON_VISIT_ENABLED, ETL_ENABLE_BEHAVIOR, ETL_BEHAVIOR_AGG_INTERVAL_SECONDS=900. Kanal PB Odoo id 6 (298 menu = semua menu POS terjual sejak 1 Agu 2026,
  harga median ESB di pricelist demo, `stock_check=none`), user `efn_online_pb` + key 30 hari (rotasi manual ±23 Des).
- Data demo: `scripts/dev/seed_demo_visitors.py` (14 hari, `subject_key` `demo:`, consented=0). WAJIB cleanup 3 langkah
  (docstring skrip) sebelum gladi data nyata. Rekening transfer PB = PLACEHOLDER.
- Belum: APK app belum dibangun ulang; UI `/visitors` belum dilihat dengan login; segment-worker tidak dinyalakan (tidak
  ada data consented); DPO/Legal Q-SM48.
