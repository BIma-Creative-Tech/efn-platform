# efn_pii_hr (19.0.1.0.0) — K-a work contact (E-38a)

Modul jembatan `auto_install` (`efn_pii` + `hr`). **Belum untuk produksi/CI** (keputusan lead).
ADR-036 §0.2 pengecualian **K-a** (Q-PII7 default, keputusan 18-40: nama karyawan/user internal plaintext), ADR-031.

## Tujuan
`hr.employee._create_work_contacts` (Odoo 19.0) membuat `res.partner` work contact. Tanpa modul ini, work contact
(individu) menjadi **terlindungi** oleh protect-by-default `efn_pii`, sehingga nama karyawan terenkripsi dan
pemilihan karyawan/kasir di banyak modul berubah. Modul ini memasang context server `efn_pii_skip_default`
(`SKIP_TOKEN`, tidak bisa dibuat klien RPC) **hanya** di jalur itu.

## Belum (E-38b)
Data **pribadi** karyawan (`hr.employee.private_phone/private_email/emergency_*`, `hr.version.private_*`,
`identification_id` + blind index, `ssnid`, `passport_id`) dan redaksi tracking-nya = tahap E-38b.

## Uji
`tests/test_pii_hr.py` (tag `efn`, `efn_pii`). Manual: buat karyawan tanpa user → `SELECT name FROM res_partner WHERE id
= <work_contact_id>` plaintext; buat pelanggan perorangan → terenkripsi.

## Referensi
Ditulis dari source Odoo CE 19.0 `hr/models/hr_employee.py`. Tidak ada kode pihak ketiga.
