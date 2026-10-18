# efn_pii (19.0.2.0.0) — E-38a tahap inti

> **Status: dibangun G20, belum untuk produksi.** Jangan dimasukkan ke daftar install produksi atau `EFN_TEST_MODULES`
> CI sebelum lead memutuskan (ADR-036 §0.11: modul baru masuk setelah semua kriteria tonggak E-38a lulus).
> ADR: **ADR-036 Accepted (§0 menang atas §1–§14)**, ADR-022 (Keputusan 5 diralat), ADR-031 (tanpa modul pihak
> ketiga). Kontrak: `docs/04_SERVICE_CONTRACTS.md` §12.5 (K-36 revisi ADR-036 §0.9).

## Tujuan
Data pribadi partner **terlindungi** dienkripsi oleh ORM Odoo sebelum sampai ke PostgreSQL. Dump DB, backup, role
SQL yang salah grant, dan log query hanya melihat ciphertext. User yang berhak tetap membaca plaintext lewat ORM
(form, laporan, ekspor, QWeb).

- Risiko yang dimitigasi: R-L01 (UU PDP), R-L10 (PII pembeli, prasyarat W-06), R-T18 (fail-closed, SEC-137),
  SEC-136 (salinan plaintext lewat jalur standar), SEC-138 (ciphertext dipindah antar-record).
- Risiko yang dipantau: R-T19 (frekuensi token nama), R-O07 (keluhan pencarian/urutan), R-T08 (restore butuh key
  ring lengkap), R-B11 (kunci fatal), R-T13 (hook ORM diuji ulang tiap rilis mayor).

## Kelas subjek (ADR-036 §0.2) — protect-by-default
`res.partner.efn_pii_protected` = Boolean **precompute** (dihitung di `create`, panggilan yang sama, sebelum INSERT),
satu arah, `groups=base.group_system`:

| Partner baru | Terlindungi? |
|---|---|
| individu (`is_company=False`) tanpa induk: member, pelanggan kasir, pembeli online, vendor perorangan | ya |
| kontak `type='contact'` di bawah perusahaan | ya |
| anak (semua `type`) dari partner terlindungi | ya |
| perusahaan, alamat `type≠'contact'` milik perusahaan | tidak |
| **K-a**: partner user internal (`res.users.create`), work contact karyawan (`efn_pii_hr`) | tidak |
| partner user portal (`share=True`) | ya (dijadikan terlindungi segera setelah user dibuat / diubah ke portal) |

K-a memakai context server `efn_pii_skip_default` dengan nilai `pii_model.SKIP_TOKEN` (subclass `str` yang hanya
bisa dibuat kode server). Context dari klien RPC (`"efn_pii_skip_default"`, `True`, `1`) **tidak** berlaku. Nilai
eksplisit `efn_pii_protected=False` untuk individu (impor/admin) dipaksa `True`. Record lama tidak pernah dihitung
ulang (install modul tidak menandai partner lama; lihat Migrasi).

Turunan & SEC-136:
- kontak yang dipindah / menerima nilai (mis. NPWP dari `_commercial_sync_*`) di bawah partner terlindungi ikut
  terlindungi **sebelum** nilai ditulis; menandai induk ikut menandai seluruh turunan (`child_of`);
- wizard merge (`base.partner.merge.automatic.wizard._update_values`): tujuan dijadikan terlindungi bila salah satu
  partner terlindungi;
- `email_normalized` **dienkripsi + blind index** (bukan dikosongkan) → `find_or_create` /
  `_find_or_create_from_emails` (email masuk) menemukan pemilik terlindungi, tidak membuat duplikat;
- `mail.tracking.value`: dicegat di titik pembuatan (`_create_tracking_values`), bukan daftar statis per modul —
  nilai field `EfnPiiChar` record terlindungi dikosongkan apa pun modul yang menambah `tracking=` (mis. `account`
  memberi `name` `tracking=True`); label many2one ke partner terlindungi (mis. `parent_id`, `partner_id` dokumen lain)
  = `Kontak terlindungi #<id>`. Lapis pertama tetap `_mail_track` (field cakupan + `contact_address_inline`).
  Riwayat lama diredaksi saat partner menjadi terlindungi. (Run nyata G20 menemukan nama partner terlindungi di
  label many2one karena `col_info` 19.0 tidak memuat `relation`; diperbaiki.)

## Field (ADR-036 §0.2 tabel field, tahap 1a)
| Field | Perlakuan |
|---|---|
| `name`, `complete_name` (compute stored) | E + token nama (`efn_name_tokens`) |
| `street`, `street2`, `city`, `zip` | E (tanpa pencarian) |
| `phone`, `email`, `vat` | E + blind index (`efn_phone_bidx`, `efn_email_bidx`, `efn_vat_bidx`) |
| `email_normalized` (compute stored, `mail`) | E + blind index `efn_email_bidx` (subkunci `email`, normalisasi sama) |
| `phone_sanitized` | dikosongkan (K) |

NIK / dokumen pembeli (`l10n_id_*`), XML Coretax = `efn_pii_l10n_id`; `account.move.invoice_partner_display_name` =
perluasan `efn_pii_account` (**belum**, sisa E-38a).

## Mekanisme (M1, ADR-036 §8.2)
`models/fields_pii.py` `EfnPiiChar(fields.Char)` didefinisikan ulang di `_inherit` (tanpa monkeypatch):

| Jalur Odoo 19.0 | Hook | Perilaku |
|---|---|---|
| INSERT (`create`, `copy`, `load`, `sudo()`) | `convert_to_column_insert(value, self_kosong, stored)` | `efnpii1:` (id belum ada) |
| setelah `super().create()` | `pii_model.bind_new_records` (SQL, transaksi sama) | `efnpii1:` → `efnpii2:` AAD + id |
| UPDATE flush (`write`, `__set__`, flush compute) | `get_column_update` → `convert_to_column_insert(validate=False)` | langsung `efnpii2:` |
| baca | `convert_to_record` | dekripsi (cache per cursor, dibuang saat key ring berganti) |
| fetch DB | `_insert_cache` + `_fetch_query` model | nilai mentah tanpa penanda pada record terlindungi → **CRITICAL `plaintext_in_protected`** |
| domain | `condition_to_sql` | lihat Pencarian |

**SEC-137 fail-closed** (`pii_model.broken_fields`): bila field cakupan bukan lagi `EfnPiiChar` (modul lain
mendefinisikan ulang), `create`/`write` yang menyentuh record terlindungi (langsung atau lewat dependensi compute
stored) **ditolak** (`UserError` + CRITICAL `field_not_encrypted`); partner tidak terlindungi tetap bisa ditulis.
`_register_hook` mencatat CRITICAL saat registry dimuat. Modul penghubung per modul CE yang mendefinisikan ulang
field: `efn_pii_base_vat` (`vat`), `efn_pii_account` (`name`, `account/models/partner.py`, dibuat lead G20). Audit CE
lead: hanya `mail`, `base_vat`, `account` yang mendefinisikan ulang field cakupan. Run gabungan wajib (AC-4).

## Format (ADR-036 §0.3)
- Blob (identik BFF): `versi_kunci(1) || IV(12) || ciphertext || tag(16)`, AES-256-GCM.
- `efnpii2:` + base64(blob), AAD `efn:odoo:<tabel>.<kolom>:<id>:v2` (format berlaku).
- `efnpii1:` + base64(blob), AAD `efn:odoo:<tabel>.<kolom>:v1` — hanya di dalam transaksi create. Terbaca di luar
  itu (data PoC G19) → tetap didekripsi + CRITICAL `unbound_ciphertext` (kode saja); migrasi `_efn_pii_migrate`.
- Ciphertext dipindah antar-record/kolom lewat SQL → `auth_failed` + nilai pengganti (AC-7).
- Penanda apa pun `efnpii<n>:` di input create/write ditolak (`ValidationError`).

## Pencarian (ADR-036 §0.5–§0.6)
| Domain atas partner terlindungi | Perilaku |
|---|---|
| `phone`/`email`/`email_normalized`/`vat` `=`, `in`, `=ilike` | blind index (normalisasi: HP `62…`, email lowercase, NPWP digit) |
| `ilike`/`like` berisi nilai **lengkap** (email ber-`@`, HP ≥ 9 digit, NPWP 15/16 digit) | blind index |
| `phone_mobile_search` (pencarian angka POS 19) | hasil SQL mentah **tanpa** partner terlindungi (cegah cocok kebetulan atas base64) + blind index bila HP lengkap |
| `name`/`complete_name`/`display_name` `=`, `in`, `=ilike` tanpa wildcard | token `f` nama lengkap ternormalisasi |
| `name`/`complete_name`/`display_name` `ilike`/`like` | token `p`: setiap kata ≥ 3 huruf = awalan, **AND**, urutan bebas; wildcard `% * _` = pemisah; tanpa infiks ("anto" tidak menemukan "Santoso"). Catatan: untuk partner TIDAK terlindungi Odoo standar memperlakukan `*` sebagai karakter literal, jadi `*sant*` hanya menemukan partner terlindungi berkata awalan "sant" |
| `not …` | negasi semantik |
| street/street2/city/zip, operator lain | tidak cocok (tanpa error) |

`name_search`/`_rec_names_search` (`complete_name, email, ref, vat, company_registry`) bekerja otomatis.
Partner tidak terlindungi tetap memakai SQL standar.

**Token nama** (`tools/name_tokens.py`): N(s) = NFKC → casefold → NFKD tanpa diakritik → `[^a-z0-9]`→spasi.
Jenis `f` (nama lengkap & complete_name), `w` (kata ≥ 2), `p` (awalan 3–15). HMAC-SHA256 (subkunci HKDF
`efn:odoo:nametok:<f|w|p>:v1` dari `odoo_pii_name_key`) dipotong 128 bit, base64url 22 karakter. Token diurutkan +
dipadatkan ke kelipatan 16 dengan token acak. Kolom `efn_name_tokens` (admin saja, `prefetch=False`) + indeks GIN
ekspresi `res_partner_efn_name_tokens_gin` = `gin (string_to_array(efn_name_tokens, ' '))`.

## Kunci (tidak pernah di DB, `ir.config_parameter`, log, image, atau repo)
| Env | Default | Isi |
|---|---|---|
| `EFN_ODOO_PII_KEY_DIR` | `/run/secrets` | folder `odoo_pii_key_v<n>`, `odoo_pii_bidx_key`, `odoo_pii_name_key` |
| `EFN_ODOO_PII_KEY_V<n>_FILE` | `<dir>/odoo_pii_key_v<n>` | override path kunci versi n |
| `EFN_ODOO_PII_BIDX_KEY_FILE` | `<dir>/odoo_pii_bidx_key` | override path kunci blind index |
| `EFN_ODOO_PII_NAME_KEY_FILE` | `<dir>/odoo_pii_name_key` | override path kunci token nama (dev compose devsecops: `/run/secrets/odoo_pii_name_key`) |
| `EFN_ODOO_PII_ACTIVE_KEY_VERSION` | `1` | versi untuk enkripsi baru |
| `EFN_ODOO_PII_REQUIRED` | kosong | `true` = mode terkunci dicatat CRITICAL walau belum ada data terlindungi |

**Mode terkunci** = salah satu dari kunci aktif / blind index / token nama tidak terbaca: membuat atau mengubah data
partner terlindungi (termasuk **membuat pelanggan perorangan baru**) → `UserError` + CRITICAL, tanpa fallback
plaintext; baca → `•••• (kunci PII tidak tersedia)`; partner perusahaan & penjualan POS tanpa pelanggan baru tetap
jalan; `pos.order` tidak menyalin email/HP (efn_pii_pos).

> **Konsekuensi untuk run test gabungan / instalasi**: karena individu terlindungi sejak dibuat, **setiap** modul
> atau test yang membuat partner perorangan memerlukan ketiga kunci. Run gabungan `efn_pii` + `EFN_TEST_MODULES`
> wajib memasang kunci uji global, mis.:
> ```bash
> mkdir -p /tmp/efn-pii-ci && for f in odoo_pii_key_v1 odoo_pii_bidx_key odoo_pii_name_key; do
>   openssl rand -base64 32 > /tmp/efn-pii-ci/$f; done   # di container, folder sementara, bukan repo
> docker run ... -e EFN_ODOO_PII_KEY_DIR=/tmp/efn-pii-ci -v /tmp/efn-pii-ci:/tmp/efn-pii-ci:ro ...
> ```
> Test `efn_pii*` sendiri memasang kunci acak per test/kelas (`tests/common.py`) dan memulihkan cache sesudahnya.

## Migrasi / operasi (shell Superadmin)
- DB PoC G19 / partner bertanda dengan kolom plaintext atau `efnpii1:`:
  `env['res.partner'].sudo()._efn_pii_migrate(); env.cr.commit()` → `efnpii2:` + hitung ulang token, blind index,
  `email_normalized`. Juga dipakai setelah **rotasi `odoo_pii_name_key`** (token dihitung ulang).
- Partner perorangan lama yang belum bertanda (DB dibuat sebelum E-38a): tandai lewat `write({'efn_pii_protected':
  True})` per batch (mengenkripsi nilai lama + turunan + redaksi tracking). Tidak otomatis: L-36-1 mensyaratkan DB
  produksi dibuat setelah E-38a.

## Uji manual (stack dev, data uji)
1. Pasang tiga kunci (`EFN_ODOO_PII_KEY_DIR` atau secret dev compose), install `efn_pii`.
2. UI Kontak: buat "Budi Santoso" (individu) dengan HP/email/alamat. `psql`:
   `SELECT name, street, phone, email_normalized, efn_name_tokens FROM res_partner WHERE id = <id>` → `efnpii2:…` ×4,
   token 22 karakter (jumlah kelipatan 16).
3. Cari "budi", "sant", "budi sant", "santoso budi" → ditemukan; "anto" → tidak. Cari HP `0812…` lengkap → ditemukan.
4. Buat perusahaan "PT Uji" → kolom plaintext; tambah kontak di bawahnya → kontak terenkripsi.
5. Hapus satu file kunci, tunggu 10 detik: form menampilkan nilai pengganti; buat pelanggan baru ditolak; log CRITICAL.

## Test
`tests/` (tag `efn`, `efn_pii`): `test_pii_partner` (protect-by-default, kelas, K-a, jalur tulis, baca, blind index,
rotasi, swap AC-7, mode terkunci), `test_pii_tokens` (AC-9 + GIN), `test_pii_sec` (SEC-136/137/138 + migrasi efnpii1),
`test_pii_crypto` (format v1/v2, key ring, `EFN_ODOO_PII_NAME_KEY_FILE`), `test_pii_vectors` (AC-2 sisi Odoo),
`test_pii_overhead` (angka di log `EFN_PII_POC overhead`).

## Vektor bersama BFF ↔ Odoo (AC-2)
`tools/shared_vectors.py` membangun vektor deterministik dari **label publik** (kunci uji = SHA-256 label, bukan
rahasia; JSON tidak memuat kunci): AEAD `efnpii1` & `efnpii2` (AAD EFN `…:v1` dan `…:<id>:v2`, termasuk `pos_order`),
blind index, token nama, normalisasi. File: `tests/data/efn_pii_shared_vectors.json`.
- **Status:** nilai `expected_*` masih `null` (penulis tidak bisa menjalankan kode). Isi dengan salah satu:
  `docker run --rm --entrypoint python3 efn-odoo:ci /mnt/extra-addons/efn_pii/tools/shared_vectors.py > efn_pii_shared_vectors.json`
  atau salin JSON dari log test (`grep EFN_PII_SHARED_VECTORS`). Setelah terisi, `test_expected_values_or_emit`
  membandingkan secara ketat.
- **BFF (Jest)**: baca file yang sama; kunci = `createHash('sha256').update(labels.aes_key).digest()`, IV =
  `createHash('sha256').update(iv_label).digest().subarray(0, 12)`, blob = `Buffer.from(expected_text.slice(8), 'base64')`;
  `new PiiCipher(new Map([[1, key]]), 1).decrypt(blob, aad)` == `plaintext`; arah enkripsi dengan `randomBytes`
  di-mock → IV. Blind index: HKDF-SHA256(ikm = SHA-256(labels.bidx_ikm), salt kosong, info) lalu HMAC hex. Token
  nama: HMAC 16 byte pertama, base64url tanpa padding. Vektor publik lama (`efn_pii_test_vectors.json`) tetap.

## Batasan / belum
- **Urutan (O1)**: daftar partner terurut `complete_name` = urutan ciphertext untuk partner terlindungi (tidak abjad);
  laporan QWeb yang butuh abjad wajib mengurutkan di Python. `read_group` per kota/kode pos/nama tidak bermakna.
- Pencarian: tanpa infiks, tanpa fonetik/sinonim; nama seluruhnya non-Latin tidak punya token. Kata > 15 huruf
  dicocokkan dengan 15 huruf pertama (bisa sedikit melebar).
- Kueri `ilike` gabungan (`plain OR token`) tetap seq scan pada cabang plaintext (sama dengan Odoo standar tanpa
  trigram); GIN hanya dipakai cabang token. AC-1 (200 rb partner, p95 ≤ 300 ms) **belum diukur**.
- Residu: `mail.message` (body/email_from, termasuk pesan log merge yang memuat nama & email sumber),
  `mail.mail`, `res.users.login` (portal wajib ID opak, Q-PII8), tuple mati plaintext partner portal (dibuat K-a lalu
  ditandai di transaksi yang sama) sampai VACUUM, tracking many2one lama di model lain yang dibuat sebelum partner
  ditandai, `privacy_lookup` (cari `ilike`).
- `filter_domain` view pencarian Accounting/Purchase, `auth_signup` reset password, mass mailing/blacklist SQL join:
  belum diuji.
- Field cakupan yang dihitung ulang tanpa `write` pada record itu (mis. `complete_name` anak saat nama induk
  berubah) tidak dicegat SEC-137 bila field itu didefinisikan ulang; terdeteksi registry + alarm baca.

## Referensi desain (ADR-031)
Tidak ada kode OCA/Cybrosys yang disalin. Pola "field terenkripsi di layer ORM" dan "blind index" dikenal di ekosistem
Odoo/umum (mis. CipherSweet), tetapi implementasi ini ditulis ulang dari source Odoo CE 19.0 (`odoo/orm/fields.py`,
`models.py`, `domains.py`, `addons/base/models/res_partner.py`, `wizard/base_partner_merge.py`,
`addons/mail/models/{res_partner,mail_tracking_value}.py`, `addons/phone_validation/models/mail_thread_phone.py`)
dan kontrak BFF (`services/api-gateway/src/common/pii/crypto.ts`).
