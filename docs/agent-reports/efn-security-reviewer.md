
## 21 Nov 2026 — review pra-merge `fix/insight-review`

Laporan lengkap: `docs/reviews/SECURITY_REVIEW_2026-11-21_insight-review.md`. Rekomendasi: boleh di-merge (tidak ada Critical/High
dari perubahan branch).
- **SEC-IR-01 (High, pre-existing):** kunci scope (`insight_scope_key`, `text2sql_scope_key`, sig2 SM) ditanam literal di `USING` row
  policy (`clickhouse-init.sh`, `access/insight-sm-policy.sh`); galat `Unknown setting SQL_efn_*` dan `system.query_log.exception`
  menampilkannya. insight-web/text2sql tidak meneruskan teks galat. Tindak lanjut: rotasi kunci efn-prod lokal; default `''` untuk
  setting `SQL_efn_*` (tanpa galat); struktural: komitmen hash per PT (revisi ADR-041). Belum dikerjakan.
- **SEC-IR-02 (Low):** fallback PT default brand di `v_esb_movement_daily` -> **diperbaiki lead** (fail-closed `ob.company_code`, test
  `test_movement_company_code_fail_closed`).
- **SEC-IR-03 (Info):** dampak company_code = PT asal dicatat di docs/04 §26.7a.
- **SEC-IR-04 (Info):** `dim_esb_menu` dari baris penjualan: whitelist 8 kolom, tanpa PII.

## 22 Nov 2026 — review pra-merge `feat/insight-loading-screen` (703e6e0)

Laporan lengkap: `docs/reviews/SECURITY_REVIEW_2026-11-22_insight-loading-screen.md`. **Verdict: boleh merge** (tanpa Critical/High/
Medium). Perubahan murni UI klien; tidak ada inline `<style>`/`<script>` baru (hanya atribut `style` dari angka internal, sesuai
`style-src-attr`), tidak ada data sesi/PII/secret/log baru, `isInternalNavClick` tidak bernavigasi (tidak ada open redirect).
- **SEC-LS-01 (Low, PLAUSIBLE):** `lib/loading-store.ts:51-61` — `useBusy` tanpa batas aman; transisi RSC yang menggantung mengunci
  halaman tanpa batas (pulih hanya dengan reload). Saran: batas `SAFETY_MS` juga untuk `pendingCount` atau tombol "Muat ulang" setelah ±60 dtk.
- **SEC-LS-02 (Low, laten):** `components/LoadingOverlay.tsx:139-151` — listener capture di `document` jalan sebelum React, jadi
  `defaultPrevented` tidak melihat `preventDefault` dari `onClick` React; juga redirect ke URL sama -> kunci 90 dtk. Saat ini tidak ada
  `<a>` yang memanggil `preventDefault`. Saran: cek `defaultPrevented` setelah dispatch (`setTimeout 0`).
- **SEC-LS-03 (Info):** tombol Keluar (`Shell.tsx:70-74`) tidak terjangkau saat memuat; durasi terbatas, tab bisa ditutup. Diterima.
- **SEC-LS-04 (Info):** perangkap fokus dialog modal memang diniatkan; fokus tidak dikembalikan ke pemicu (belum diverifikasi di browser).
- Belum terverifikasi: perilaku di browser (CSP violation report, `inert`, fokus); `git diff` tidak dijalankan (file dibaca dari tree branch).

## 22 Nov 2026 — review pra-merge G52 persona F&B + sidebar (`feat/insight-nav-dual-menu`)

Laporan lengkap: `docs/reviews/SECURITY_REVIEW_2026-11-22_g52-insight-fnb-persona.md`. **Verdict: boleh merge dengan syarat
SEC-G52-01 diperbaiki** (wajib sebelum flag diaktifkan). Tidak ada Critical/High. Pencabutan PT penuh untuk kepala toko berlaku
tanpa flag; `brs` ditandatangani (HMAC + sig3); kelas PT/BR benar; tidak ada jalur eit2/eis2 PT penuh untuk store-only dari Odoo baru.
- **SEC-G52-01 (Medium):** `docker-compose.yml` tidak meneruskan `INSIGHT_FNB_PERSONA_ENABLED` (Odoo + insight-web) dan
  `INSIGHT_ACCEPT_EIT2` (insight-web); default `EFN_INSIGHT_TOKEN_VERSION` compose = 2. Akibatnya `INSIGHT_ACCEPT_EIT2=false` tidak
  berefek (jalur eit2/eis2 terbuka selamanya) dan G52 tidak bisa diaktifkan.
- **SEC-G52-02 (Low):** exec+store+marketing / exec+store+category|dc → T5 gagal → `ValueError` tak tertangkap → HTTP 500 (fail closed).
  Saran: T5' `store ∈ P ⇒ exec ∈ P ∨ P = {store}` dua sisi + tangkap ValueError.
- **SEC-G52-03 (Low):** residu rollout: eis2 pra-G52 PT penuh ≤ 2 jam / selama jeda deploy 1→2. Saran: rotasi `insight_session_key`
  setelah upgrade Odoo, lalu `INSIGHT_ACCEPT_EIT2=false`.
- **SEC-G52-04 (Low):** penutupan rute sesi ber-cabang di proxy berupa denylist; saran allowlist.
- **SEC-G52-05 (Info):** unik global `(esb_brand, esb_branch_id)` → oracle/penyerobotan pemetaan antar PT Bacha; data tetap fail closed.
- **SEC-G52-06 (Info):** SEC-IR-01 masih terbuka; `SQL_efn_brs` menambah pemicu galat saat versi campuran.
- Belum terverifikasi: test/harness tidak dijalankan; override compose lokal efn-prod tidak diperiksa; upgrade DB demo nyata.

## 2026-11-23 — Review feat/odoo-completion (G54/G55/G56/G61/G62)
0 Critical / 0 High / 1 Medium / 5 Low / 2 Info. Medium SEC-G54-01 (action_run/default_get wizard di luar RPC_ALLOWLIST) dan Low SEC-G54-02, SEC-G61-02 sudah diperbaiki efn-lead di branch yang sama. Laporan: `docs/reviews/SECURITY_REVIEW_2026-11-23_odoo-completion.md`.

## 2026-11-23 — Review port accounting G57–G60
0 Critical / 2 High / 5 Medium / 5 Low / 5 Info. High SEC-G60-01 (persetujuan payment bisa ditulis lewat RPC) dan SEC-G58-01 (status batch bisa dipalsukan), serta Medium SEC-G57-01/02/03, SEC-G58-02, SEC-G60-02 diperbaiki engineer + test regresi, diverifikasi efn-lead di container. Laporan: `docs/reviews/SECURITY_REVIEW_2026-11-23_accounting-port.md`.

## 2026-11-24 — Review G66 production planning ESB (`feat/production-planning`, worktree efn-platform-prodplan)
0 Critical / 1 High / 2 Medium / 4 Low / 2 Info. Laporan: `docs/reviews/SECURITY_REVIEW_2026-11-24_prodplan.md` (SEC-225..233; cek
tabrakan nomor dengan review esb-aggregates yang belum di-commit di worktree lain). **Verdict: boleh merge selama tulis production
terkunci** (L-250 terbukti tidak bisa dilewati lewat env/konfigurasi/redirect).
- **SEC-225 (High, P1):** `outbox.py:109` rekonsiliasi hanya membaca halaman pertama daftar ESB (produksi tanpa filter marker) →
  `not_found_after_timeout` palsu → resend menggandakan dokumen produksi. Wajib ditutup sebelum PR pembuka kunci.
- **SEC-226 (Medium):** resend tidak menolak baris yang sudah punya `esb_doc_number`; `_confirm` toleran membuat baris turun ke
  `failed` → dokumen lama yatim, tidak terhapus saat cancel.
- **SEC-227 (Medium):** SoD hanya pembuat/pengaju; penyunting demand/hitung/usulan bisa menyetujui.
- Low: SEC-228 grup PP belum sensitif di `efn_role`; SEC-229 peran PP tidak per PT; SEC-230 cancel destruktif oleh satu planner + race
  cancel/claim; SEC-231 qty BOM ≤ 0 lolos ke payload. Info: SEC-232 kunci `details` tidak cocok; SEC-233 hardening.
- Belum terverifikasi: perilaku nyata ESB (page size, filter `additionalInfo`, qty negatif), `gen-pki.sh`, komponen UI PP; test tidak dijalankan.

## 22 Nov 2026 — review pra-merge `feat/esb-daily-aggregates` (a57c1a1)

Laporan lengkap: `docs/reviews/SECURITY_REVIEW_2026-11-22_esb-aggregates.md`. **Verdict: boleh merge ke dev** (tanpa Critical/High);
SEC-AG-01 dan SEC-AG-02 wajib ditutup sebelum dipakai di efn-prod/pilot. Multi-company aman: `insight_reader` tanpa hak `efn_dwh`, tiga
wrapper tetap view DEFINER bernama sama di bawah row policy `efn_insight_scope` + pagar fail-closed; `agg_esb_*` hanya di-grant ke
`esb_writer` dan `efn_insight_definer`.
- **SEC-AG-01 (Medium):** `access/esb-access.sql:13,20,64-69` — `CREATE TABLE` per tabel + `allow_ddl=1` tidak membatasi engine/SETTINGS
  (`table_engines_require_grant` default false): esb_writer bisa membuat agg dengan `storage_policy='default'` (plaintext at rest) atau
  engine non-MergeTree (Buffer/Distributed = PLAUSIBLE). Saran: `table_engines_require_grant=true` + `GRANT TABLE ENGINE ON MergeTree`,
  constraint `merge_tree_storage_policy`, pagar init engine/policy; alternatif tanpa CREATE: `REPLACE PARTITION` dari build permanen.
- **SEC-AG-02 (Medium):** `efn_esb/aggregates.py:30,187-190,264-272` — umur build = `metadata_modification_time` (waktu CREATE); refresh
  > 2 jam dihapus refresh kedua, proses pertama lanjut INSERT ke build baru -> agregat ganda/campur di-EXCHANGE dengan `status=ok`.
  Saran: verifikasi UUID build sebelum INSERT/EXCHANGE; deteksi query aktif di `system.processes`.
- **SEC-AG-03 (Low):** `aggregates.py:285-293` — 0 baris / penyusutan drastis tetap di-EXCHANGE. Saran: pagar penyusutan + `--allow-shrink`.
- **SEC-AG-04 (Info):** `CH_DATABASE`/`CH_RAW_DATABASE` disisipkan tanpa validasi identifier (pola lama). Saran: regex di config.
- **SEC-AG-05 (Info):** DROP pada agg = bisa mengosongkan Insight (setara dampak INSERT yang ada); runbook §6a belum memuat langkah pulih.
- Belum terverifikasi: perilaku grant engine/disk dinamis di ClickHouse 24.8.14; durasi refresh nyata multi-tahun. Tidak ada yang dijalankan.
