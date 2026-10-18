# efn_pii_base_vat (19.0.1.0.0)

Modul penghubung `auto_install` untuk `efn_pii` + `base_vat` (CE). `base_vat` (ikut terpasang lewat `account`)
mendefinisikan ulang `res.partner.vat` sebagai `fields.Char` biasa. Tanpa modul ini, sejak E-38a (G20) `efn_pii`
**menolak** tulis partner terlindungi (SEC-137 fail-closed, `UserError` + CRITICAL `field_not_encrypted`), bukan lagi
sekadar mencatat log. Ditemukan pada run gabungan PoC E-38 (18 Okt 2026).

- ADR terkait: ADR-036 (Accepted, §0), ADR-031.
- Risiko: **R-T18** (definisi ulang field) — modul E-38 wajib punya modul penghubung untuk setiap modul CE yang
  mendefinisikan ulang field cakupan (`_efn_pii_fields`); run gabungan AC-4 menemukannya.
- Test: dibuktikan oleh test `efn_pii` (`test_field_classes_keep_standard_attributes` memeriksa
  `pii_model.broken_fields == []`) saat dijalankan bersama modul accounting.
- Belum masuk CI/produksi (keputusan lead).
