# PoC E-38: enkripsi kolom PII Odoo (ADR-036 §8, docs/04 §11.5)

- **Tanggal:** 18 Okt 2026 (Gelombang 19, branch `chore/wave-19`)
- **Pelaksana:** efn-odoo-engineer (B)
- **Modul:** `services/odoo/addons/efn_pii` 19.0.1.0.0, LGPL-3, **PoC, belum untuk produksi**
- **Status hasil:** kode + test ditulis; **test belum dijalankan** (sesi ini tanpa shell/Docker). Lulus/tidaknya
  gerbang ditentukan lead setelah run di container (perintah di §6).

> **Ringkasan manajemen.** Uji coba teknis membuktikan (di atas kertas dan lewat test otomatis yang siap dijalankan)
> bahwa nomor HP, email, dan NPWP pelanggan terlindungi bisa disimpan terenkripsi oleh Odoo tanpa mengubah cara
> staf melihat data. Pencarian dengan nilai lengkap tetap jalan; pencarian sebagian tidak. Angka beban kinerja
> keluar saat test dijalankan. Keputusan "E-38 penuh masuk G19 atau G20" menunggu hasil run itu.

## 1. Kriteria lulus (ADR-036 §8.2) dan bukti

| # | Kriteria | Bukti (test) | Status |
|---|---|---|---|
| 1 | Semua jalur tulis menghasilkan `efnpii1:` di SQL mentah | `test_pii_partner.py`: `test_create_encrypts_in_sql_and_clears_derived_copies` (create), `test_write_and_setattr_encrypt` (write, `rec.f = x`, tulis nilai sama), `test_sudo_and_non_admin_paths` (sudo, user non-admin tanpa sudo), `test_import_load_create_and_update` (`load` create + update `.id`), `test_becoming_protected_encrypts_existing_values` (partner lama ditandai), `test_orm_read_paths_return_plaintext` (`copy`) | Ditulis, belum dijalankan |
| 1b | Salinan plaintext turunan kosong | `email_normalized`, `phone_sanitized` NULL (test create & becoming); `mail.tracking.value` tanpa nilai partner terlindungi, kontrol partner biasa tetap ter-track (`test_tracking_never_stores_protected_plaintext`) | Ditulis |
| 2 | Baca ORM, form, `web_read`, `_load_pos_data`, QWeb faktur, ekspor Coretax = plaintext | `test_orm_read_paths_return_plaintext` (`__get__`, `read`, `web_read`, `search_read`, `export_data`, `mapped`, `email_formatted`), `test_qweb_render_plaintext` (QWeb `t-out`/`t-field` generik) | **Sebagian**: `_load_pos_data` asli, laporan QWeb faktur `account`, ekspor Coretax **belum** (modul PoC tidak bergantung pada `point_of_sale`/`account`/`l10n_id_efaktur_coretax`) |
| 3 | `=`/`in` ditulis ulang ke blind index; `ilike` tidak error | `test_search_equal_and_in_use_blind_index` (HP `0812…`/`62…`/`+62…`, email beda huruf, NPWP bertanda baca, `!=`, `not in`, `= False`, `filtered_domain`), `test_same_vat_duplicate_detection_via_blind_index` (`_compute_same_vat_partner_id` standar), `test_ilike_does_not_error_and_excludes_protected` (`ilike`, `like`, `=ilike`, `not ilike`, `name_search`), `test_sudo_and_non_admin_paths` (user non-admin mencari tanpa AccessError ke kolom blind index) | Ditulis |
| 4 | Test vector BFF ↔ Odoo | `test_pii_crypto.py`: `test_shared_aead_vectors_seal_and_open` (vektor publik spesifikasi GCM TC14 & TC16 dalam format blob §7.2, versi kunci 1 dan 2), `test_shared_vector_tc14_via_public_api`, HKDF RFC 5869 A.3, blind index vs implementasi HKDF/HMAC independen | Sisi Odoo ditulis. **Sisi BFF belum** (di luar batas file saya, lihat §5) |
| 5 | Overhead < 5 % pada `_load_pos_data` 1.000 partner | `test_pii_overhead.py`: `search_read` 1.000 partner terlindungi vs tidak (7 field, min dari 5 putaran, cache ORM dikosongkan tiap putaran), juga waktu create dan `search in` 100 HP. Tanpa assert ketat, angka di log `EFN_PII_POC overhead` | Angka **belum ada**. Catatan: ambang 5 % diukur pada `search_read` (setara loader), bukan `_load_pos_data` asli |

Tambahan di luar 5 kriteria: mode terkunci (`TestEfnPiiLocked`: tanpa kunci, hanya kunci blind index hilang,
kunci dipulihkan), penanda satu arah & penolakan penanda palsu, AAD mengikat kolom (ciphertext email dipindah ke
kolom phone -> nilai pengganti + log ERROR tanpa nilai), rotasi v1 -> v2 (data v1 terbaca, tulis baru byte versi 2),
kunci hilang saat berjalan terdeteksi tanpa restart (CRITICAL), log key ring tanpa rahasia.

## 2. Mekanisme yang dipilih: M1 (+ override model kecil)

**M1** = `EfnPiiChar(fields.Char)` yang menimpa `convert_to_column_insert` (tulis), `convert_to_record` (baca), dan
`condition_to_sql` (cari). Alasannya, dari source Odoo CE 19.0 (dibaca 18 Okt 2026 via GitHub branch `19.0`):

1. Semua tulis ke kolom melewati satu fungsi: `_create` memanggil
   `field.convert_to_column_insert(stored[fname], self, stored)`, dan `_flush` memanggil `field.get_column_update(record)`,
   yang berakhir di `convert_to_column_insert(value, record, validate=False)`. `load()` memakai `create`/`write`. Flush
   compute stored juga lewat `_flush`. Jadi M1 menangkap create, write, `__set__`, sudo, impor, dan compute.
   Kekhawatiran ADR §8.2 ("M1 mungkin tidak menangkap jalur compute") tidak terbukti untuk 19.0.
2. `_fetch_query` memasukkan nilai mentah DB ke cache tanpa konversi (`field._insert_cache(fetched, values)`).
   Semua pembacaan (`__get__`, `_read_format`, `mapped`, `filtered`, `_export_rows`) lewat descriptor `__get__` ->
   `convert_to_record`. Dekripsi cukup di satu titik.
3. `DomainCondition._to_sql` memanggil `field.condition_to_sql(...)` setelah optimasi (`=` -> `in` dengan
   `OrderedSet`, `!=` -> `not in`). Rewrite blind index cukup di field, tanpa override `_search`.
4. `convert_to_column` (dipakai sebagai parameter pembanding) **tidak** ditimpa, sehingga nilai pencarian tidak
   ikut terenkripsi acak.
5. `Field.__init_subclass__` mendaftarkan tipe dengan `setdefault`, sehingga subclass tidak menggantikan `fields.Char`
   global. `Field._get_attrs` menggabungkan atribut definisi sebelumnya bila kelas baru turunan kelas lama.

**M2 penuh tidak dipakai.** Override model hanya untuk hal yang bukan konversi nilai: penolakan awal (penanda palsu,
mode terkunci, satu arah), enkripsi nilai lama saat partner baru ditandai (ORM membuang tulis nilai yang sama,
`Field._filter_not_equal`), `copy_data`, compute turunan (`email_normalized`, `phone_sanitized`), dan `_mail_track`.

## 3. Yang terbukti vs belum

| Hal | Terbukti oleh test (setelah run) | Belum |
|---|---|---|
| Jalur tulis ORM res.partner | create, write, `__set__`, sudo, non-admin, `load` create/update, becoming, copy | Modul lain yang menulis lewat SQL mentah (R-T18). `_increase_rank` dkk. hanya menulis kolom rank |
| Salinan turunan | `email_normalized`, `phone_sanitized`, tracking chatter | `pos.order.email/mobile` (efn_pii_pos), `mail.message` body bebas, `account.move` snapshot nama/alamat |
| Baca | ORM, `web_read`, `search_read`, ekspor, QWeb generik | `_load_pos_data` asli, laporan faktur, generator Coretax (§4.8 ADR, asumsi ORM) |
| Cari | `=`, `in`, `!=`, `not in`, NULL, duplikat NPWP standar, `ilike` aman, `name_search` | Domain klien POS 19 (`get_new_partner`), `phone_mobile_search` (SQL mentah) |
| Kunci | key ring v1/v2, mode terkunci, kunci hilang saat jalan, log tanpa rahasia | `EFN_ODOO_PII_RETIRED_VERSIONS`, cron re-encrypt, migrasi/dekripsi-balik, `deploy-validate` |
| Format lintas bahasa | sisi Odoo vs vektor publik | sisi BFF memakai file vektor yang sama (belum ditulis) |
| Kinerja | angka `search_read`/create/search 1.000 partner (dicetak) | `_load_pos_data` asli, beban paralel worker |

## 4. Risiko & temuan PoC

- **R-T18 (konkret):** modul yang di-load setelah `efn_pii` dan mendefinisikan ulang `phone/email/vat` dengan
  `fields.Char` biasa membuang kelas `EfnPiiChar` (ORM membuang atribut bila kelas tidak kompatibel). Mitigasi PoC:
  `_register_hook` mencatat CRITICAL + test `test_field_classes_keep_standard_attributes`. Modul E-38 penuh perlu
  test yang dijalankan bersama semua modul terpasang di DB produksi.
- **Penyimpangan dari ADR-036 §4.1:** `copy()` partner terlindungi menghasilkan partner terlindungi (ADR: `copy=False`
  murni akan membuat salinan plaintext). User non-admin tidak bisa menduplikasi partner terlindungi.
- **Semantik pencarian:** `=`/`in` atas partner terlindungi cocok berdasarkan nilai **ternormalisasi** (HP `0812…` =
  `+62 812…`, NPWP tanpa tanda baca). Partner tidak terlindungi tetap exact-string seperti standar.
- **`find_or_create` / mail gateway** tidak menemukan partner terlindungi (cari `email_normalized`) -> bisa membuat
  partner duplikat tidak terlindungi. Perlu keputusan E-38 penuh (ADR §4.7 butir 3 sudah menerima mail gateway mati).
- **Sinkronisasi NPWP ke kontak anak** tidak terlindungi = salinan plaintext. Kelas terlindungi (individu) biasanya
  tanpa kontak anak; E-38 penuh sebaiknya memaksa anak ikut terlindungi.
- **Placeholder di mode terkunci** ikut terbaca oleh compute non-stored dan tracking (memori), tetapi tidak pernah
  ditulis: semua tulis PII partner terlindungi ditolak saat terkunci.
- Log `EFN_PII_KEYRING`: CRITICAL hanya bila kunci hilang saat berjalan, `EFN_ODOO_PII_REQUIRED=true`, ada partner
  terlindungi saat start, atau tulis ditolak. CI tanpa secret mendapat WARNING (gerbang log CI hanya ERROR/CRITICAL).

## 5. Test vector bersama: cara BFF memakai file yang sama

File: `services/odoo/addons/efn_pii/tests/data/efn_pii_test_vectors.json` (hanya vektor publik: spesifikasi GCM
McGrew & Viega TC14/TC16, RFC 5869 A.3; tidak ada kunci EFN).

- TC14 (versi 1, plaintext 16 byte NUL, AAD kosong): lewat API publik BFF,
  `new PiiCipher(new Map([[1, Buffer.from(v.k_hex, 'hex')]]), 1).decrypt(Buffer.from(v.expected_blob_hex, 'hex'), '')`
  harus `'\u0000'.repeat(16)`.
- TC16 (versi 2, AAD biner): BFF menguji tata letak blob dengan `node:crypto` langsung (`createDecipheriv` atas
  `blob.subarray(13, -16)`, IV `blob.subarray(1, 13)`, tag `blob.subarray(-16)`), dan arah enkripsi dengan
  `jest.mock('node:crypto')` agar `randomBytes(12)` mengembalikan `iv_hex`, lalu membandingkan `encrypt()` dengan
  `expected_blob_hex`. Untuk TC16 lewat `PiiCipher`, AAD harus string UTF-8, jadi hanya tata letak yang diuji.
- Usulan untuk efn-bff-engineer: `crypto.spec.ts` membaca file ini lewat path relatif repo (atau salinan yang dicek
  checksum di CI) agar satu sumber.
- **Belum ada** vektor ber-AAD `efn:odoo:...` dengan plaintext HP, karena sesi ini tidak bisa menjalankan kode untuk
  menghitung ciphertext. Lead dapat menambahkannya dengan satu kali run (kunci & IV uji tetap, bukan rahasia):
  `python3 -c "from cryptography.hazmat.primitives.ciphers.aead import AESGCM; k=bytes(range(32)); iv=bytes(range(12)); print('01'+iv.hex()+AESGCM(k).encrypt(iv,b'6281234567890',b'efn:odoo:res_partner.phone:v1').hex())"`
  di container Odoo, lalu menambah entri `aead_vectors` dengan `plaintext_hex`/`aad_hex` hex dari string itu.
  Test Odoo otomatis memakai entri baru.

## 6. Yang harus dijalankan lead (container Odoo 19 nyata)

Pola sama dengan job CI `odoo` (DB sementara, image `efn-odoo:ci`), tetapi modul PoC **tidak** didaftarkan di
`EFN_TEST_MODULES`:

```bash
docker build -t efn-odoo:ci services/odoo
# Postgres sementara seperti CI (user odoo / odoo-ci-only), lalu:
docker run --rm --network host --entrypoint odoo efn-odoo:ci \
  --db_host=127.0.0.1 --db_port=5432 --db_user=odoo --db_password=odoo-ci-only \
  --addons-path=/mnt/extra-addons,/usr/lib/python3/dist-packages/odoo/addons \
  -d efn_test_pii -i efn_pii --test-tags efn_pii_poc --stop-after-init --log-level=test --workers=0 \
  --max-cron-threads=0 2>&1 | tee odoo-pii-test.log
grep -E '^[0-9-]+ [0-9:,]+ [0-9]+ (ERROR|CRITICAL) ' odoo-pii-test.log   # harus kosong
grep 'EFN_PII_POC overhead' odoo-pii-test.log                              # angka kriteria 5
grep -E 'Ran [0-9]+ test|failed|error' odoo-pii-test.log
```

Disarankan juga run gabungan dengan modul EFN lain terpasang (`-i efn_pii,<EFN_TEST_MODULES>` di DB terpisah,
`--test-tags efn`) untuk memastikan override `res.partner` tidak memecah test modul lain (efn_privacy, POS) dan
tidak ada redefinisi field yang membuang `EfnPiiChar` (log `field PII tanpa enkripsi`).

Cek ruff: `ruff check services/odoo/addons/efn_pii` dan `ruff format --check` (konfigurasi `services/odoo/ruff.toml`).

## 7. Rekomendasi gerbang (untuk lead)
- Lulus bila: run di atas 0 failed, 0 baris ERROR/CRITICAL, dan overhead baca 1.000 partner < 5 % (atau selisih
  absolut yang diterima lead bila waktu dasar sangat kecil, misalnya < 20 ms).
- Sebelum E-38 penuh: tambah `efn_pii_pos` (preload, `get_new_partner`, `pos.order.email/mobile`) dan uji
  `_load_pos_data` asli; `efn_pii_l10n_id` + ekspor Coretax; street/street2; job rotasi/migrasi; integrasi erasure.

## Hasil nyata (dijalankan lead, 18 Okt 2026, image `efn-odoo:ci`, PostgreSQL 16.15)

| Run | Hasil |
|---|---|
| `-i efn_pii --test-tags efn_pii_poc` | 28 test, 0 gagal, 0 ERROR |
| Gabungan `efn_pii` + semua `EFN_TEST_MODULES` (`--test-tags efn`), sebelum perbaikan | 8 gagal + CRITICAL `field PII tanpa enkripsi: ['vat']` — `base_vat` (via `account`) mendefinisikan ulang `vat` setelah `efn_pii` (**R-T18 nyata**) |
| Sama, setelah modul penghubung `efn_pii_base_vat` (auto_install) | 574 test, 0 gagal, 0 ERROR/CRITICAL |
| Overhead (`EFN_PII_POC overhead`, n=1000, 5 ronde) | read_plain 0,018–0,023 s; read_protected 0,070–0,103 s (+282–432 %); create +3–17 %; `search in` 100 = 0,005–0,007 s |

Perbaikan lead saat run: `copy_data` tidak lagi menerima `default={'efn_pii_protected': False}` (bypass penanda satu arah → salinan plaintext; test ditambah); `copy_data` masuk `ORM_OVERRIDES` scanner RPC `efn_core`.

Penilaian kriteria & keputusan: lihat docs/04 §11.7 **L-39** (lulus bersyarat; E-38 penuh ke G20).

---

## E-38a tahap inti (G20, 18 Okt 2026, branch `chore/wave-20`) — ADR-036 §0, docs/04 §12.5

- **Pelaksana:** efn-odoo-engineer (B). **Test belum dijalankan** (sesi tanpa shell); lead menjalankan di container.
- **Modul:** `efn_pii` 19.0.2.0.0, `efn_pii_pos` 19.0.1.0.0 (baru, auto_install `efn_pii`+`point_of_sale`),
  `efn_pii_hr` 19.0.1.0.0 (baru, auto_install `efn_pii`+`hr`, hanya K-a), `efn_pii_base_vat` (README).

### Yang berubah dari PoC
| Area | PoC G19 | E-38a |
|---|---|---|
| Penandaan | manual (`efn_pii_protected=True`) | **protect-by-default** (precompute di create); K-a via context server `SKIP_TOKEN` |
| Field | phone, email, vat | + name, complete_name, street, street2, city, zip, email_normalized (E + blind index) |
| Format | `efnpii1:` AAD tanpa id | **`efnpii2:` AAD `…:<id>:v2`**; `efnpii1:` hanya transisi INSERT → ditulis ulang di create yang sama; baca `efnpii1:` lama = CRITICAL `unbound_ciphertext` + migrasi |
| Cari nama | tidak cocok | token `f`/`w`/`p` HMAC-128 (`odoo_pii_name_key`), GIN, AND multi-kata, awalan 3–15 |
| Cari kontak | `=`/`in` | + `=ilike`, `ilike` nilai lengkap, `phone_mobile_search` (POS) tanpa cocok kebetulan |
| R-T18 | log CRITICAL | **fail-closed** (`UserError`) + alarm baca `plaintext_in_protected` |
| SEC-136 | — | anak/commercial sync, merge, email masuk (`email_normalized` bidx), tracking (redaksi + label m2o) |
| POS | — | `pos.order.email/mobile` terenkripsi semua order; preload tanpa partner terlindungi |

### Perintah (lead)
```bash
docker build -t efn-odoo:ci services/odoo
# kunci uji di folder sementara container (bukan repo)
K=/tmp/efn-pii-ci; mkdir -p $K; for f in odoo_pii_key_v1 odoo_pii_bidx_key odoo_pii_name_key; do openssl rand -base64 32 > $K/$f; done
# 1) modul E-38a sendiri (pos + hr ikut sebagai dependensi untuk modul jembatan)
docker run --rm --network host -e EFN_ODOO_PII_KEY_DIR=$K -v $K:$K:ro --entrypoint odoo efn-odoo:ci \
  --db_host=127.0.0.1 --db_port=5432 --db_user=odoo --db_password=odoo-ci-only \
  --addons-path=/mnt/extra-addons,/usr/lib/python3/dist-packages/odoo/addons \
  -d efn_test_pii2 -i efn_pii,efn_pii_pos,efn_pii_hr --test-tags efn_pii --stop-after-init --log-level=test \
  --workers=0 --max-cron-threads=0 2>&1 | tee odoo-pii2.log
grep -E '^[0-9-]+ [0-9:,]+ [0-9]+ (ERROR|CRITICAL) ' odoo-pii2.log      # harus kosong
grep -E 'EFN_PII_POC overhead|EFN_PII_SHARED_VECTORS' odoo-pii2.log     # angka + JSON vektor (salin ke tests/data)
# 2) gabungan (AC-4 awal): efn_pii* + semua EFN_TEST_MODULES, --test-tags efn, kunci global sama (-e/-v seperti di atas)
```
Tanpa kunci global, instalasi/test modul lain yang membuat partner perorangan **gagal** (fail-closed, disengaja).
Tag test berubah `efn_pii_poc` → **`efn_pii`**.

### Kriteria ADR-036 §0.11 — status sisi Odoo
| AC | Bukti test | Status |
|---|---|---|
| AC-1 | `test_pii_overhead` (1.000 partner, + `name_token_search`), EXPLAIN GIN di `test_pii_tokens` | **belum**: `_load_pos_data` nyata & 200 rb partner tidak diukur |
| AC-2 | `test_pii_vectors` + `tools/shared_vectors.py` + `tests/data/efn_pii_shared_vectors.json` | sisi Odoo siap; nilai `expected_*` **null** sampai generator dijalankan; BFF belum |
| AC-5 | `test_pii_sec.TestEfnPiiSec136` (anak, commercial sync, pindah induk, turunan, merge, email masuk), `test_becoming_…_redacts_tracking`, `test_tracking_never_stores_protected_plaintext` | ditulis |
| AC-6 | `TestEfnPiiSec137` (field didefinisikan ulang → `UserError`, compute dependency, alarm plaintext), `test_redefined_order_field_refuses_writes` | ditulis |
| AC-7 | `test_swapped_ciphertext_between_records_fails_auth`, `TestEfnPiiSec138.test_no_efnpii1_left_after_any_create_path` | ditulis |
| AC-9 | `test_pii_tokens` (normalisasi, awalan ≥ 3, AND, tanpa infiks, padding 16, ganti nama/erasure, rotasi `odoo_pii_name_key`) | ditulis |
| AC-10 (P) | member/pelanggan kasir/pembeli (individu), portal, `pos.order.email/mobile` | ditulis; `account.move.invoice_partner_display_name` **belum** |
| AC-3, AC-4, AC-8, AC-11..14 | — | di luar tahap ini / agent lain |
