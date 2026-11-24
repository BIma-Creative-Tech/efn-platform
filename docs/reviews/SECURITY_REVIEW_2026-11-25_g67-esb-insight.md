# Security Review G67: ESB P&L, Waste, Fill Rate -> Insight (2026-11-25)

> **Catatan penomoran ulang (25 Nov 2026, rekonsiliasi merge #165):** gelombang yang direview di sini **dinomori ulang
> menjadi G68** karena label G67 sudah dipakai Insight "Margin bahan" (#165). Padanan: docs/04 §31 → **§32** (§31.x →
> §32.x), L-260..L-268 → **L-270..L-278**, `24-g67-esb-pl-waste-fillrate.sql` → `25-g68-esb-pl-waste-fillrate.sql`,
> `v_esb_g67_quality` → `v_esb_g68_quality`, `INSIGHT_G67_ENABLED` → `INSIGHT_G68_ENABLED`, modul/test `*_g67*` →
> `*_g68*`, ID temuan **SEC-G67-xx = SEC-G68-xx** (kode memakai SEC-G68-01). Jumlah policy ESB setelah merge = 25 (bukan
> 22). Isi review di bawah tidak diubah (rekaman historis); nama file dipertahankan.

Reviewer: efn-security-reviewer (read-only, kode/konfigurasi saja; tidak ada eksekusi test).
Lingkup: worktree `efn-platform-g67`, branch `feat/esb-insight-pl-waste-fillrate`, commit `78aaa8b` vs `origin/main` `8738838`.
Acuan: docs/04 §31, ADR-040 §6b (amandemen G67), ADR-041 (Proposed), CLAUDE.md (PDP, least privilege, multi-company).
Hasil harness ClickHouse 24.8 milik lead (DDL 2x, `g67_views_harness.sh` termasuk negatif row policy lima view) **tidak** dijalankan ulang oleh reviewer; diterima sebagai bukti.

## Ringkasan eksekutif

- Tidak ada temuan Critical/High. Jalur ESB tetap GET-only, whitelist PDP diterapkan sebelum raw/mart, tabel G67 hanya ditulis `esb_writer`, insight membaca lewat view DEFINER ber-row-policy, semua flag default mati dan masuk gerbang production.
- **Satu temuan Medium (SEC-G67-01):** baris P&L "total brand" selalu diberi kode PT default brand. Untuk brand multi-PT (Bacha: BCF + override BCR) eksekutif BCF dapat menurunkan laba rugi agregat cabang BCR lewat baris "Tidak teralokasi ke outlet". Saat ini laten (PB satu PT; override kosong secara default), tetapi perilaku ini sudah dikunci di kontrak dan test.
- Dua temuan Low (nama akun P&L verbatim bisa memuat nama orang; gerbang persona exec untuk P&L hanya di aplikasi, tidak di ClickHouse) dan dua Info.
- Verdict: **boleh merge dengan syarat** SEC-G67-01 diperbaiki (atau dikunci fail-closed) sebelum ESB Bacha + override diaktifkan di environment mana pun yang memuat data nyata; tidak memblokir aktivasi PB.

## Tabel temuan

| ID | Severity | Lokasi | Judul | Risiko |
|---|---|---|---|---|
| SEC-G67-01 | Medium | `services/esb-extract/efn_esb/extract_g67.py:69`; `services/insight-web/lib/queries.ts:1165-1177`; `services/insight-web/lib/pnl.ts:225-243`; `services/esb-extract/tests/test_g67.py:257`; `docs/04_SERVICE_CONTRACTS.md:6800` | Total brand P&L ber-`company_code` PT default -> kebocoran agregat lintas PT pada brand multi-PT | R-S (multi-company / kerahasiaan data keuangan), L-108, L-267 |
| SEC-G67-02 | Low (PLAUSIBLE) | `services/esb-extract/efn_esb/normalize.py:153-158` (`description` di `PL_FIELDS`), `:163` (`requestTemplateName`), `:160` (`locationName`) | Nilai teks bebas (nama akun, nama template/lokasi) disimpan verbatim | UU PDP (minimisasi), L-110 |
| SEC-G67-03 | Low | `infra/clickhouse/access/insight-esb-policy.sh:40,64`; `services/insight-web/lib/g67-access.ts:46` | Pembatasan "P&L hanya Eksekutif" hanya ditegakkan insight-web; row policy kelas PT mengizinkan semua sesi ber-cakupan PT | Defense in depth, L-267 |
| SEC-G67-04 | Info | `services/insight-web/lib/g67-access.ts:7`, `lib/queries.ts:1212-1297` | `/waste` menampilkan nilai HPP ke semua persona F&B ber-cakupan PT (termasuk marketing) | Konfirmasi kebijakan |
| SEC-G67-05 | Info | `services/esb-extract/efn_esb/config.py:296,300` | Backfill ±61 rb GET pada 2 rps ≈ 8,5 jam > jendela 22:00-05:00 (7 jam) dan > `ESB_BACKFILL_MAX_CALLS` 60.000 | Beban ESB (operasional) |

## Detail

### SEC-G67-01 (Medium): total brand P&L dilabeli PT default -> agregat lintas PT

**Fakta kode (terverifikasi):**
- `extract_g67.py:69`: `company = cfg.company_for(branch_id) if branch_id > 0 else cfg.company_code` -> slice `scope='brand'` (GET tanpa `branchIDs`, memuat **semua** cabang brand) selalu ber-`company_code` = PT default brand.
- `config.py:143-157,212-213`: override per cabang (`ESB_BRANCH_COMPANY_OVERRIDES`, contoh resmi `bacha:6=BCR` di `docker-compose.esb.yml:86`) membuat satu brand melayani dua PT.
- `planning.py:188-189`: `pl_scopes` selalu menambah `BRAND_SCOPE` tanpa memeriksa override.
- `queries.ts:1165-1177` (`pnl_statement`) mengambil baris `scope='brand'` bila filter cabang kosong; `pnl.ts:225-243` menampilkan `brand - Σ cabang terlihat` sebagai baris "Tidak teralokasi ke outlet: <pos>".
- `test_g67.py:257` mengunci perilaku ini (Bacha total brand -> `{"BCF"}`), dan kontrak `docs/04:6800` menyatakan "`scope='brand'` -> PT default brand".

**Skenario eksploit:** Bacha diaktifkan dengan `ESB_BRANCH_COMPANY_OVERRIDES=bacha:6=BCR`. Eksekutif yang hanya punya kode `BCF` membuka `/pnl` tanpa filter cabang. Row policy meloloskan baris brand (company_code `BCF`), cabang 6 tidak terlihat (BCR). Baris "Tidak teralokasi ke outlet: netProfitLoss" = total brand - Σ cabang BCF = **laba bersih cabang BCR + biaya pusat**, per bulan, untuk semua pos summary (penjualan bersih, laba kotor, EBITDA, laba bersih). Artinya data keuangan PT BCR terbaca oleh pengguna PT BCF tanpa cakupan BCR. Kebalikannya, eksekutif BCR tidak melihat baris total brand sama sekali (inkonsisten).

**Dampak:** pelanggaran pemisahan multi-company untuk data paling sensitif G67 (laba rugi). Saat ini laten: G67 mulai dari PB (satu PT) dan override kosong secara default, tetapi kode dan test menjadikannya perilaku yang diharapkan saat Bacha masuk.

**Rekomendasi (pilih satu, yang paling sederhana dulu):**
1. Di `extract_g67.py`, bila `cfg.branch_company` memuat kode PT selain `cfg.company_code`, tulis slice brand dengan `company_code=''` (row policy `company_code != ''` di `insight-esb-policy.sh:58` otomatis membuangnya; tetap tersedia untuk `v_esb_g67_quality` admin). Halaman lalu tidak menampilkan baris "Tidak teralokasi" untuk brand multi-PT (fail closed).
2. Atau lewati `BRAND_SCOPE` di `pl_scopes` untuk brand multi-PT (hemat 1 GET/tahun, tetapi kehilangan pembanding Q-ESB33).
3. Perbarui `docs/04:6800`, ganti asersi `test_g67.py:257` menjadi kasus negatif (Bacha + override -> brand row tidak ber-PT), dan tambah satu kasus negatif di `g67_views_harness.sh`: sesi `codes=BCF` tidak melihat baris `scope='brand'` brand multi-PT.

### SEC-G67-02 (Low, PLAUSIBLE): teks bebas disimpan verbatim

**Fakta kode:** P&L memakai `whitelist_tree` dengan `description` diizinkan di semua tingkat (`normalize.py:153-158`); nilainya tidak diperiksa. Jurnal item menyimpan `requestTemplateName` (maks 128) dan `locationName` (`transform_g67.py:302-305`). Kunci pelaku (`createdBy`, `creator*`, `editor*`, `authorizer*`, `additionalInfo`) **benar** dibuang (whitelist), dan `raw_rows()` menjalankan `redact()` lagi sehingga `description` tidak masuk `raw_doc`.

**Skenario:** COA ESB klien memakai nama akun seperti "Piutang Karyawan - <nama>" atau "Sewa Gedung a.n. <nama pemilik>"; nama itu masuk `fact_esb_profit_loss.account_name` dan tampil di tabel "Akun per seksi" `/pnl`. Tidak bisa dipastikan tanpa data nyata (karena itu PLAUSIBLE).

**Rekomendasi:** setelah backfill PB, lead/efn-data-ai-engineer menjalankan `SELECT DISTINCT account_name` (dan `template_name`, `location_name`) sekali, cek manual, catat hasilnya di laporan W-A dan catatan DPO. Bila ada nama orang: masking berbasis daftar akun (mapping `account_key -> label`) di transform, bukan regex nama.

### SEC-G67-03 (Low): gerbang persona exec P&L hanya di lapis aplikasi

**Fakta kode:** `v_esb_pl_monthly` berkelas `PT` (`insight-esb-policy.sh:40`); predikat PT hanya memeriksa kode PT, tanda tangan sig3, dan `brs=''` (`:58-64`). `roles` tidak ikut ditandatangani ke ClickHouse. Pembatasan "hanya Eksekutif" ada di `proxy.ts:84-88` + `g67-page.ts:29` (dua lapis, keduanya insight-web). Semua SQL konstanta (`queries.ts:1-11`), jadi tidak ada jalur bagi pengguna non-exec untuk memilih query P&L; tidak ada endpoint ekspor CSV untuk tabel G67 (`app/api/export/[name]/route.ts` hanya `TABLES`).

**Skenario:** hanya relevan bila proses insight-web disusupi (RCE/SSRF ke ClickHouse dengan setting sesi marketing yang sah): P&L PT itu terbaca. Tidak ada eksploit dari browser.

**Rekomendasi:** terima sebagai risiko residual untuk G67 (catat di L-267). Untuk gelombang berikutnya: kelas policy `PT_EXEC` dengan `SQL_efn_roles` ikut di sig (perubahan kontrak §28.15.7, butuh revisi docs/04).

### SEC-G67-04 (Info): nilai HPP di `/waste` untuk semua persona PT

`/waste` dan `/fill-rate` sengaja terbuka bagi semua pengguna F&B ber-cakupan PT (`g67-access.ts:6-7`), termasuk persona marketing; `waste_kpi`/`waste_top_products` menampilkan nilai HPP. Konsisten dengan `/sales` yang sudah menampilkan cogs, jadi bukan regresi. Minta konfirmasi pemilik proyek bila HPP seharusnya hanya Eksekutif/Finance.

### SEC-G67-05 (Info): beban backfill

Backfill ±61 rb GET > `ESB_BACKFILL_MAX_CALLS` default 60.000 dan pada `ESB_RATE_PER_SEC=2` butuh ±8,5 jam (> jendela 7 jam). Karena backfill resumable dan berhenti di batas jendela/anggaran (`runner.py:458-508`), akibatnya hanya berlangsung dua malam; tidak perlu menaikkan rate. Cantumkan di runbook agar operator tidak memakai `--ignore-window`.

## Kontrol yang sudah benar

- **GET-only:** `http.py:210-217,365-375` menolak method selain GET (kecuali POST login) sebelum koneksi; nomor dokumen detail di-`quote(num, safe='')` (`extract_g67.py:106`) sehingga tidak ada path traversal; `check_detail_num` menolak respons salah alamat.
- **Retransform tanpa ESB:** `run_retransform` (`runner.py:547-596`) hanya membaca `raw_doc` lewat query berparameter (`store.py:101-110`), tidak login, entitas dibatasi `RETRANSFORM_ENTITIES=("delivery_ref",)` (`__main__.py:211-216`), data tidak lengkap -> slice tidak ditulis (fail closed).
- **PDP:** whitelist per endpoint sebelum raw/mart (`normalize.py:140-178`), `whitelist()` membuang nilai dict/list bersarang (kontak bersarang, `transferDetailAssets`); fixture test sintetis (`test_g67.py:67-69`), termasuk asersi kunci pelaku tidak tersimpan.
- **Fail closed transform:** label bulan/prefix cabang/`area.id`/tahun tidak cocok -> `ValueError` -> slice error tanpa checkpoint (`transform_g67.py:79-142`); angka lewat `Decimal(str(x))`.
- **Log:** hanya brand/entity/scope/hari/jumlah dan `type(exc).__name__` (`runner.py:227-252,579-594`); tanpa payload, nama akun, atau kredensial.
- **Least privilege ClickHouse:** tabel G67 hanya `SELECT, INSERT` untuk `esb_writer` (`esb-access.sql:56-62`), tanpa DDL; tidak di-grant ke `metabase_reader`, text2sql, forecast, mig (tidak ada grant wildcard `efn_dwh.*` di `infra/clickhouse/access`); `v_esb_g67_quality` dan view internal tidak dibungkus `efn_insight`.
- **Row policy:** lima view G67 kelas PT (`insight-esb-policy.sh:40-44`), jumlah policy diverifikasi `ESB_POLICY_EXPECTED=22` (`clickhouse-init.sh:446`), sesi ber-cabang -> 0 baris; harness negatif lead lulus.
- **Query insight-web:** semua SQL konstanta, nilai lewat placeholder bertipe (`{supplier:Array(String)}`, `{uclass:Array(String)}`, `{m_from:Date}`, `{m_to:Date}`); `parseSuppliers`/`parseUsageClasses`/`parseMonthRange` memvalidasi bentuk dan batas (maks 10 pemasok, 24 bulan, tidak melewati bulan ini); setiap query tetap memfilter `has({pt}, company_code)` (lapis 1). `PL_SUM` menyisipkan kunci dari konstanta `PL_KEYS`, bukan input. Tidak ditemukan SQL injection.
- **Gerbang akses:** `/pnl` = persona exec + flag F&B; `/waste`, `/fill-rate` ditolak untuk sesi ber-cabang di proxy, halaman, dan `BRANCH_BLOCKED_PATHS` (fail closed walau flag mati); sesi < eis3 -> buka ulang; halaman `/g67-belum-aktif` tanpa data.
- **Flag:** `INSIGHT_G67_ENABLED` aktif hanya string persis `"true"` (`config.ts:117`), default `false` di compose dan `deploy.sh:418`, divalidasi `^(true|false)$` dan masuk `EFN_GATED_FEATURES` (`deploy-validate.sh:80`: production butuh referensi persetujuan). Entitas G67 opt-in (`DEFAULT_ENTITIES` tidak memuatnya; compose `ESB_ENTITIES` default sama).
- **Transport:** ClickHouse HTTPS 8443 dengan CA internal (`docker-compose.esb.yml:105-112`); secret dari `/run/secrets/*`.

## Belum diverifikasi

- Reviewer tidak menjalankan pytest/vitest/harness; mengandalkan laporan lead.
- Isi nyata COA/template ESB (SEC-G67-02) dan nilai `ESB_BRANCH_COMPANY_OVERRIDES` di tiap environment lokal.
