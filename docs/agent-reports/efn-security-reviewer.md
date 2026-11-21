
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
