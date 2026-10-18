# efn_pii (19.0.1.0.0)

> **Status: PoC, belum untuk produksi.** Modul ini adalah gerbang PoC E-38 (`docs/04_SERVICE_CONTRACTS.md` §11.5).
> Jangan dimasukkan ke daftar install produksi atau ke `EFN_TEST_MODULES` CI sebelum lead memutuskan PoC lulus.
> ADR terkait masih **Proposed**: ADR-036 (enkripsi kolom PII Odoo), ADR-031 (tanpa modul pihak ketiga).

## Tujuan
HP (`phone`), email (`email`), dan NPWP (`vat`) milik partner **terlindungi** (`res.partner.efn_pii_protected`)
dienkripsi oleh ORM Odoo sebelum sampai ke PostgreSQL. Dump DB, backup, role SQL yang salah grant, dan log query
hanya melihat ciphertext. User Odoo yang berhak tetap membaca plaintext lewat ORM (form, laporan, ekspor).

- Risiko yang dimitigasi: R-L01 (UU PDP, langkah teknis pelindungan), R-L10 (PII pembeli di Odoo, prasyarat W-06).
- Risiko baru yang dipantau: R-T18 (hook enkripsi terlewati jalur tulis baru), R-T08 (restore butuh key ring
  lengkap), R-B11 (kunci fatal di tangan Superadmin), R-T13 (hook ORM harus diuji ulang tiap rilis mayor).

## Mekanisme (M1, ADR-036 §8.2)
`models/fields_pii.py`: kelas `EfnPiiChar(fields.Char)` yang didefinisikan ulang di `_inherit = "res.partner"`
untuk `phone`, `email`, `vat`. Tanpa monkeypatch. Atribut definisi standar (`tracking` dari `mail`, `index` dari
`vat`) tetap digabung oleh ORM karena kelasnya turunan `Char`.

| Jalur Odoo 19.0 | Hook | Perilaku |
|---|---|---|
| INSERT (`create`, `copy`, `load` impor, `sudo()`) | `convert_to_column_insert(value, record, stored)` | enkripsi bila `stored["efn_pii_protected"]` |
| UPDATE flush (`write`, `rec.f = x`, `load` update, flush compute) | `get_column_update` -> `convert_to_column_insert(validate=False)` | enkripsi bila record terlindungi (dibaca sudo) |
| Baca (`__get__`, `read`, `web_read`, `search_read`, ekspor, `mapped`, QWeb) | `convert_to_record` (cache menyimpan nilai mentah DB) | dekripsi `efnpii1:` |
| Domain | `condition_to_sql` | `=`/`in` -> blind index; `ilike`/`like`/ketidaksamaan tidak cocok untuk partner terlindungi (tanpa error); `not ...` = negasi |

Override model kecil (pelengkap M1, bukan M2 penuh) di `models/res_partner.py`:
- `create`/`write`: tolak nilai berawalan `efnpii1:` (`ValidationError`), tolak tulis saat terkunci (`UserError`),
  penanda satu arah, dan enkripsi nilai lama saat partner **baru menjadi** terlindungi (SQL langsung, karena ORM
  tidak mem-flush nilai yang tidak berubah).
- `copy_data`: duplikat partner terlindungi tetap terlindungi. Ini **menyimpang** dari `copy=False` murni di
  ADR-036 §4.1: tanpa ini `copy()` menghasilkan salinan plaintext. Akibatnya user non-admin tidak bisa
  menduplikasi partner terlindungi (AccessError pada field admin).
- `_compute_email_normalized`, `_compute_phone_sanitized`: kosong untuk partner terlindungi (salinan plaintext
  standar `mail` / `phone_validation`).
- `_mail_track`: HP/email/NPWP partner terlindungi tidak ditulis ke `mail.tracking.value`.
- `_register_hook`: log CRITICAL bila modul lain mendefinisikan ulang field dengan `fields.Char` biasa (R-T18),
  dan satu baris status key ring saat start.

## Format (identik dengan BFF, docs/04 §7.2)
- Blob: `versi_kunci (1 byte) || IV 12 byte acak || ciphertext || tag 16 byte`, AES-256-GCM.
- Teks kolom: `efnpii1:` + base64 standar (blob). `efnpii1:` adalah penanda format, bukan versi kunci.
- AAD: `efn:odoo:res_partner.<kolom>:v1`. Prefix `odoo:` mencegah ciphertext BFF dipindah ke Odoo dan sebaliknya.
- Blind index: `hex(HMAC-SHA256(HKDF-SHA256(odoo_pii_bidx_key, salt kosong, info "efn:odoo:bidx:<field>:v1"),
  normalize(v)))` di kolom `efn_phone_bidx`, `efn_email_bidx`, `efn_vat_bidx` (stored, index, admin saja).
  Normalisasi: HP via `efn_core.normalize_member_ref` (`62...`; luar negeri = digit saja bila diawali `+`/`00`),
  email via `odoo.tools.email_normalize(strict=False)`, NPWP = digit saja.

## Kunci (tidak pernah di DB, `ir.config_parameter`, log, image, atau repo)
| Env | Default | Isi |
|---|---|---|
| `EFN_ODOO_PII_KEY_DIR` | `/run/secrets` | folder `odoo_pii_key_v<n>` dan `odoo_pii_bidx_key` |
| `EFN_ODOO_PII_KEY_V<n>_FILE` | `<dir>/odoo_pii_key_v<n>` | override path kunci versi n |
| `EFN_ODOO_PII_BIDX_KEY_FILE` | `<dir>/odoo_pii_bidx_key` | override path kunci blind index |
| `EFN_ODOO_PII_ACTIVE_KEY_VERSION` | `1` | versi untuk enkripsi baru |
| `EFN_ODOO_PII_REQUIRED` | kosong | `true` = mode terkunci dicatat CRITICAL walau belum ada data terlindungi |

Isi file: 32 byte acak base64 (`openssl rand -base64 32`). Cache per proses, dicek ulang (stat) paling lambat
10 detik, sehingga rotasi file terbaca tanpa restart. `EFN_ODOO_PII_RETIRED_VERSIONS` belum dibaca (PoC).

**Mode terkunci** (kunci aktif atau kunci blind index tidak terbaca):
1. tulis HP/email/NPWP partner terlindungi, atau menandai partner baru terlindungi: `UserError`, tanpa fallback plaintext;
2. baca: nilai pengganti `•••• (kunci PII tidak tersedia)` (bukan `False`);
3. ciphertext & blind index lama tidak berubah; partner tidak terlindungi dan POS tetap jalan;
4. log `EFN_PII_KEYRING {json}` (skema ADR-036 §4.15) tanpa nilai, ciphertext, kunci, atau id partner.
   Level: CRITICAL bila kunci hilang saat berjalan, bila `EFN_ODOO_PII_REQUIRED=true`, bila ada partner terlindungi
   saat start, dan setiap tulis yang ditolak. Dev/CI tanpa secret dan tanpa data terlindungi: WARNING.

## Uji manual (stack dev, data uji, bukan data pribadi asli)
1. Pasang kunci uji di container Odoo, mis. `EFN_ODOO_PII_KEY_DIR=/tmp/efn-pii` berisi `odoo_pii_key_v1` dan
   `odoo_pii_bidx_key` (`openssl rand -base64 32`), lalu install `efn_pii`.
2. `odoo shell`: `p = env['res.partner'].create({'name': 'Uji', 'efn_pii_protected': True, 'phone': '0812 0000 0001'}); env.cr.commit()`.
3. `psql`: `SELECT phone, email_normalized, phone_sanitized, efn_phone_bidx FROM res_partner WHERE id = <id>` ->
   `efnpii1:...`, NULL, NULL, 64 hex.
4. UI Kontak: form menampilkan nomor asli; cari `0812 0000 0001` (filter `=` lewat domain) ditemukan; cari `0000`
   (ilike) tidak menemukan partner terlindungi.
5. Hapus file kunci, tunggu 10 detik: form menampilkan nilai pengganti, ubah HP ditolak, log CRITICAL.

## Test
`tests/` (tag `efn`, `efn_pii_poc`): format & test vector bersama (`tests/data/efn_pii_test_vectors.json`),
SQL mentah setelah create/write/`__set__`/sudo/user non-admin/impor `load` (create & update)/partner menjadi
terlindungi/`copy`, ORM/`web_read`/`search_read`/ekspor/QWeb plaintext, `=`/`in`/`!=`/`not in` lewat blind index,
deteksi duplikat NPWP standar, `ilike`/`name_search` tanpa error, tracking chatter, AAD/tamper, rotasi v1->v2,
mode terkunci, log tanpa rahasia, overhead 1.000 partner (angka di log `EFN_PII_POC overhead`).

Test vector dipakai BFF dengan cara berikut (file yang sama, tanpa kunci rahasia):
- `gcm-spec-tc14-*`: `new PiiCipher(new Map([[1, Buffer.from(k_hex, 'hex')]]), 1).decrypt(Buffer.from(expected_blob_hex, 'hex'), '')`
  harus `'\u0000'.repeat(16)`.
- `gcm-spec-tc16-*` (versi kunci 2, AAD biner): `createDecipheriv('aes-256-gcm', key, blob.subarray(1, 13))` +
  `setAAD(aad)` + `setAuthTag(blob.subarray(-16))` atas `blob.subarray(13, -16)` == `plaintext_hex`; arah enkripsi
  diuji dengan `jest.mock` `randomBytes` -> `iv_hex`.

## Batasan / belum (PoC)
- Cakupan PoC hanya `phone`, `email`, `vat`. `street`, `street2`, `l10n_id_nik`, `l10n_id_buyer_document_number`,
  `pos.order.email/mobile`, preload & pencarian POS (`efn_pii_pos`), Coretax (`efn_pii_l10n_id`), rotasi/migrasi/
  dekripsi-balik batch, erasure `efn_privacy` + blind index, audit `efn_audit` penandaan manual: **belum**.
- Penandaan otomatis (member OTP, kasir, pembeli W-06) belum ada; PoC hanya menyediakan field.
- `_load_pos_data` asli, laporan QWeb faktur, dan ekspor Coretax belum diuji (modul tidak bergantung pada
  `point_of_sale`/`account`/`l10n_id_efaktur_coretax`). Overhead diukur dengan `search_read` setara loader POS.
- Pencarian sebagian, sorting, group by atas field terenkripsi tidak bermakna untuk partner terlindungi. Pencarian
  standar `phone_mobile_search` (SQL mentah `REGEXP_REPLACE`) tidak ditulis ulang; secara teori bisa cocok kebetulan
  dengan teks base64 (peluang sangat kecil).
- Mail gateway / `find_or_create` (cari `email_normalized`) tidak menemukan partner terlindungi -> bisa membuat
  partner duplikat tidak terlindungi.
- Sinkronisasi NPWP ke kontak anak (`_commercial_fields`): kontak anak yang tidak terlindungi menerima NPWP
  plaintext dari induk terlindungi.
- Modul yang di-load setelah `efn_pii` dan mendefinisikan ulang `phone/email/vat` dengan `fields.Char` biasa
  menghapus enkripsi; dideteksi `_register_hook` (log CRITICAL), tidak dicegah.
- Ketersediaan `cryptography` (AESGCM, HKDF) di image `odoo:19.0` dideklarasikan lewat `external_dependencies`;
  instalasi gagal jelas bila tidak ada.

## Referensi desain (ADR-031)
Tidak ada kode OCA/Cybrosys yang disalin. Pola "field terenkripsi di layer ORM" dikenal dari ekosistem Odoo
(mis. modul enkripsi field komunitas), tetapi implementasi ini ditulis ulang dari source Odoo CE 19.0
(`odoo/orm/fields.py`, `fields_textual.py`, `models.py`, `domains.py`) dan kontrak BFF
(`services/api-gateway/src/common/pii/crypto.ts`).
