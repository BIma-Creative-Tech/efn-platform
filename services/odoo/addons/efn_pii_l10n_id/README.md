# efn_pii_l10n_id (19.0.1.0.0) — NIK, dokumen pembeli, XML Coretax terenkripsi

> Belum untuk produksi (sama dengan `efn_pii*`, L-59). `auto_install`: `efn_pii` + `l10n_id_efaktur_coretax` (CE, LGPL-3).

ADR-036 Accepted §0.2 (baris `l10n_id_*`), §0.8 (XML Coretax), keputusan 18-7 (NIK hanya untuk pembeli yang meminta
faktur/B2B), 18-8 (XML Coretax disimpan, masuk cakupan enkripsi), Q-PII12 default; ADR-031. Kontrak docs/04 §13.2.
Risiko: R-L01, R-L10 (PII pembeli W-06), R-T18.

## Isi
| Bagian | Perilaku | Sumber 19.0 (WebFetch 19 Okt 2026) |
|---|---|---|
| `res.partner.l10n_id_nik` | E + B (`efn_l10n_id_nik_bidx`) | `fields.Char('NIK')`, tanpa constraint |
| `res.partner.l10n_id_buyer_document_number` | E + B (`efn_l10n_id_buyer_document_number_bidx`) → `efn_online_order` mencari `('l10n_id_buyer_document_number', '=', …)` | `fields.Char("Document Number")` |
| XML Coretax (`ir.attachment`, `res_model='l10n_id_efaktur_coretax.document'`) | isi = bytes `efnpii2:`+base64, AAD `efn:odoo:ir_attachment.raw:<id>:v2`; plaintext tidak pernah ditulis ke filestore/DB (create tanpa isi → id → tulis ciphertext) | `efaktur_document._generate_xml`: `ir.attachment.create({'raw': …})` / `write({'raw': …})` |
| Lampiran chatter | `message_post(attachments=[(name, raw)])` di `_generate_xml` → daftar lampiran dikosongkan (`_process_attachments_for_post`), pesan tetap | mail.thread 19.0 |
| Unduhan | route `/l10n_id_efaktur_coretax/download_attachments/<…>` di-override: `check_access('read')` + grup `account.group_account_invoice` + semua lampiran milik dokumen Coretax → dekripsi di server; zip dari plaintext di memori; jejak chatter (tanpa isi) + log `EFN_PII_KEYRING` `coretax_download` (uid, jumlah) | `controllers/download_efaktur.py` |
| Isi lampiran berawalan `efnpii` dari luar | ditolak (`ValidationError`) | — |

Isi XML tetap lengkap: `_l10n_id_coretax_build_invoice_vals` membaca partner lewat ORM (terdekripsi) — test AC-3
membandingkan isi terdekripsi dengan `_generate_efaktur_invoice()` (identik) dan memastikan nama/alamat/NPWP ada.

`efn_audit` tidak punya operasi "baca"; jejak unduhan = pesan chatter dokumen + log terstruktur.

## Uji manual
1. Kunci uji; company ID (l10n_id), pelanggan perorangan PKP dengan NPWP/NIK; faktur `out_invoice` kode 01, post.
2. "Download e-Faktur" → file XML terbuka normal dan memuat nama/alamat/NPWP.
3. `psql`: `SELECT store_fname, db_datas FROM ir_attachment WHERE res_model='l10n_id_efaktur_coretax.document'` →
   isi file filestore diawali `efnpii2:`; chatter dokumen tanpa lampiran.
4. User tanpa grup Billing/Accounting membuka URL unduhan → ditolak.
5. `SELECT l10n_id_nik FROM res_partner WHERE id=<id>` → `efnpii2:`; cari NIK lengkap → ditemukan.

## Batasan / belum
- `/web/content/<id>` atas lampiran ini menyajikan ciphertext (sengaja). Lampiran yang diunggah manual ke dokumen
  Coretax juga dienkripsi.
- Lampiran Coretax lama (sebelum modul) dienkripsi oleh `post_init_hook` bila kunci ada; bila tidak, ulangi:
  `env['ir.attachment'].sudo()._efn_coretax_encrypt_legacy()`. Lampiran plaintext lama tetap bisa diunduh + CRITICAL
  `plaintext_in_protected`.
- Asumsi 19.0: `_generate_xml(regenerate=False)` dan `_process_attachments_for_post(attachments, attachment_ids,
  message_values)` (signature dari source); Coretax tidak membaca ulang lampiran (render ulang dari faktur).
- PDF faktur tetap plaintext di filestore (tahap 2).
- Test tag `efn` memakai `AccountTestInvoicingCommon.setup_country('id')` (chart l10n_id terpasang) + HttpCase unduhan.

## Referensi (ADR-031)
Tidak ada kode OCA/Cybrosys. Ditulis dari source Odoo CE 19.0 `l10n_id_efaktur_coretax` (models, controllers, tests).
