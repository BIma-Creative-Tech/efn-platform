
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
