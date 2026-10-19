# efn_pii_hr (19.0.2.0.0) — K-a work contact (E-38a) + data pribadi karyawan (E-38b)

ADR-036 Accepted §0.2 (kelas K, K-a), Q-PII7 default, keputusan 18-33/18-40; ADR-031. Risiko: R-L01 (UU PDP data
karyawan), R-T18. `auto_install` (`efn_pii` + `hr`).

## Perlakuan (Odoo 19.0, WebFetch 19 Okt 2026)
| Model.field | Perlakuan |
|---|---|
| `hr.employee.private_phone`, `private_email`, `emergency_contact`, `emergency_phone`, `place_of_birth`, `visa_no`, `permit_no`, `private_car_plate` | **E** (semua baris, `_efn_pii_always`) |
| `hr.version.private_street`, `private_street2`, `private_city`, `private_zip`, `ssnid`, `passport_id`, `spouse_complete_name` | **E** (setiap versi) |
| `hr.version.identification_id` (NIK/KTP) | **E + B** (`efn_identification_id_bidx`, normalisasi huruf-angka huruf besar) → cari NIK lengkap dari `hr.version` maupun `hr.employee` |
| nama karyawan, jabatan, email/telepon kerja, work contact | plaintext (identitas kerja, 18-40, K-a) |
| `birthday`, `spouse_birthdate` (Date), `private_country_id`/`private_state_id`/`country_of_birth` (Many2one), `sex`, `marital`, `children` | **tidak** dienkripsi (tipe bukan Char) — residu dicatat |
| `bank_account_ids` → `res.partner.bank` | terenkripsi oleh `efn_pii` (rekening partner bukan-perusahaan, termasuk work contact) |

Tracking: field di atas tidak ditulis ke `mail.tracking.value` (`_mail_track` + `_efn_pii_tracking_scope = "fields"`:
hanya field terenkripsi yang dikosongkan, perubahan nama/jabatan tetap terekam). `post_init_hook`: baris lama
dienkripsi + tracking lama field itu diredaksi (perlu kunci; tanpa kunci → WARNING, ulangi dari shell:
`env['hr.employee'].sudo()._efn_pii_encrypt_legacy(); env['hr.version'].sudo()._efn_pii_encrypt_legacy()`).

Mode terkunci: membuat karyawan tanpa data pribadi tetap jalan; menulis data pribadi → `UserError` + CRITICAL.

## Uji manual
1. Kunci uji terpasang; buat karyawan dengan HP pribadi, alamat rumah, NIK.
2. `psql`: `SELECT private_phone FROM hr_employee`, `SELECT private_street, identification_id FROM hr_version` →
   `efnpii2:`; nama karyawan plaintext.
3. Cari karyawan dengan NIK lengkap (filter) → ditemukan; potongan NIK → tidak.
4. Ubah alamat → chatter tidak menampilkan nilai lama/baru alamat.

## Batasan / belum
- Field Date/Many2one pribadi (tanggal lahir, negara lahir) tidak terenkripsi (perlu tipe field terenkripsi baru).
- Pencarian `private_*` selain NIK lengkap tidak didukung.
- Asumsi: field pribadi di atas didefinisikan langsung di `hr.employee` 19.0 (bukan diwarisi dari `hr.version`);
  diverifikasi dari source, belum dari registry runtime. Modul `hr_*` lain yang mendefinisikan ulang field ini akan
  terdeteksi `field_not_encrypted` (CRITICAL) saat registry dimuat.
- Pencarian `hr.employee` atas `identification_id` lewat `_inherits` (`version_id`) — diuji, belum dijalankan.

## Referensi (ADR-031)
Tidak ada kode OCA/Cybrosys. Ditulis dari source Odoo CE 19.0 `hr/models/{hr_employee,hr_version}.py`.
