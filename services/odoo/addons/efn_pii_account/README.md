# efn_pii_account (19.0.1.0.0)

Modul penghubung `auto_install` untuk `efn_pii` + `account` (CE). `account` mendefinisikan ulang `res.partner.name`
sebagai `fields.Char(tracking=True)`, sehingga tanpa modul ini nama partner terlindungi tidak terenkripsi dan
`efn_pii` menolak penulisan (CRITICAL `field_not_encrypted`). Ditemukan pada run test lead 18 Okt 2026 (E-38a).

- ADR terkait: ADR-036 (Accepted, §0), ADR-031.
- Risiko: **R-T18** (definisi ulang field PII oleh modul lain) — bukti nyata kedua setelah `base_vat`.
- Cakupan ADR-036 §8 untuk modul ini (nama tampilan faktur `invoice_partner_display_name`, `filter_domain` view
  faktur/PO) **belum** dikerjakan; saat ini hanya mengembalikan kelas field `name`.
- Audit modul CE yang mendefinisikan ulang field PII `res.partner` (image `efn-odoo:ci`, 18 Okt 2026): `mail`
  (dimuat sebelum `efn_pii`), `base_vat` (→ `efn_pii_base_vat`), `account` (→ modul ini). Modul `l10n_*` selain
  Indonesia dan `website_customer` tidak dipasang.
