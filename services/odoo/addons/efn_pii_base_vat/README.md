# efn_pii_base_vat (19.0.1.0.0) — PoC

Modul penghubung `auto_install` untuk `efn_pii` + `base_vat` (CE). `base_vat` (ikut terpasang lewat `account`)
mendefinisikan ulang `res.partner.vat` sebagai `fields.Char` biasa, sehingga tanpa modul ini NPWP partner
terlindungi tersimpan **plaintext** dan `efn_pii` mencatat CRITICAL `EFN_PII_KEYRING field PII tanpa enkripsi`.
Ditemukan pada run gabungan PoC E-38 (18 Okt 2026).

- ADR terkait: ADR-036 (Proposed), ADR-031.
- Risiko: **R-T18** (hook enkripsi terlewati oleh definisi ulang field) — ini bukti nyatanya; modul E-38 penuh wajib
  punya modul penghubung untuk setiap modul CE yang mendefinisikan ulang field PII (cek di CI: run gabungan).
- Belum: test sendiri (dibuktikan oleh test `efn_pii` saat dijalankan bersama modul accounting).
