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

**Langkah berikutnya:**
1. Setelah top-up efn-prod dari sesi d4: cek `v_esb_coverage` (production dst. 1 tahun) dan jalankan ulang SQL isi `dim_esb_menu` dari
   `raw_doc` bila raw sales baru ikut disalin (SQL di `docs/agent-reports/efn-data-ai-engineer.md`, koreksi lead).
2. Uji browser oleh user (toolbar, popover tanggal, sidebar ciut, legend, popover istilah) — belum diuji di browser nyata.
3. Rotasi `insight_scope_key` env efn-prod (tercetak di transkrip lewat pesan galat ClickHouse; temuan pre-existing, lihat laporan security).

**Blocker / pertanyaan untuk user:**
1. Menyalakan extractor ESB harian langsung di efn-prod butuh kredensial ESB di env itu + pemenuhan §1a (DPO, user API read-only, Legal).
2. (lama) Command global `~/.claude/commands/{handoff,lanjut,security-check}.md` menimpa versi project EFN.

**Gotcha:**
- Container insight-web rootfs read-only: skrip uji lewat stdin (`node --input-type=module -`).
- ClickHouse: `FINAL` + `ARRAY JOIN` pada `raw_doc` gagal; alias agregat tidak boleh sama dengan kolom dalam yang dipakai di WHERE.
- Sesi uji eis2 butuh peran dasar `user` + persona, `wids` memuat 0 hanya untuk persona tingkat PT (lib/claims.ts scopeConsistent).
- gitleaks CI memindai semua branch; squash merge memunculkan fingerprint baru → inline `gitleaks:allow` untuk fixture.

**Kandidat keputusan:** struktur nav 3 grup + filter auto-apply (keputusan user 21 Nov, dicatat di `docs/design/insight-web.md`);
company_code `v_esb_movement_daily` = PT cabang asal (alternatif dua baris asal/tujuan masih terbuka, docs/04 §26.7).

### Riwayat
- 21 Nov 2026 — konsolidasi worktree; review Insight 11 poin (`fix/insight-review`).
- 20–21 Nov 2026 — adopsi Claude Standard Kit (#120), gitleaks main hijau (#129).
