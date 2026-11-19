# Security & Compliance Review — Gelombang 43 (migrasi ESB → Odoo, company IMD/CUP)

- **Tanggal:** 18 Nov 2026 · **Reviewer:** efn-security-reviewer (read-only terhadap kode)
- **Cakupan:** branch `feat/wave-43-esb-migration`, commit `aca9289` (fase 1) + `ee4f452` (fase 2) di atas `bd36680`.
- **Acuan:** CLAUDE.md, ADR-042 (Accepted), ADR-043 (Proposed), docs/04 §27, docs/fit-gap/G43_MIGRASI_ESB_ODOO.md,
  runbook `migrasi-esb-odoo.md` + `vps-bima.md`.
- **Metode:** baca kode dan konfigurasi saja; tidak ada eksekusi. Hasil verifikasi lead (pytest 77, Jest 945, Odoo 31,
  ClickHouse 24.8 grant, Trivy, compose/checkov/shellcheck) diterima apa adanya dan tidak diulang.

## Ringkasan eksekutif

Kriptografi bundel (AES-256-GCM, header sebagai AAD, HKDF dengan pemisahan domain, HMAC manifest, perbandingan waktu
konstan), pertahanan tar/gzip, parameterisasi SQL (ClickHouse dan `bulk.py`), record rule multi-company, dan least
privilege `efn_mig` dibangun dengan baik. **Tidak ada temuan Critical.** Ada satu temuan **P1 integritas**: pemecahan
segmen histori per *cabang ESB*, bukan per *gudang Odoo*. Akibatnya irisan `sales.line` gudang induk bisa terhapus
diam-diam oleh part berikutnya, dan rekonsiliasi tidak menangkapnya. Tiga temuan P2 menyangkut replay bundel lama
(rollback data dan tanggal cutover), user `efn_mig` dan kunci bundel yang otomatis aktif di host VPS B (melanggar desain
"hanya selama jendela migrasi"), serta bundel tanpa batas ukuran yang diproses di dalam container Odoo produksi. Sisanya
P3, berupa pengerasan dan kebersihan runbook.

## Tabel temuan

| ID | Prioritas / Severity | Judul | Lokasi | Risiko |
|---|---|---|---|---|
| SEC-197 | **P1 / High** (integritas) | Segmen histori dipecah per cabang ESB; cabang `location` dan gudang induknya bisa jatuh di part berbeda, lalu part berikutnya menghapus atau menimpa irisan part sebelumnya tanpa terdeteksi rekonsiliasi | `scripts/migration/efn_mig/segments.py:98-114`, `efn_mig/export.py:299-334`, `efn_mig/mapping.py:233-242`, `esb_import/importer.py:772-792,827-840,878-922` | R-O12, R-O13 |
| SEC-198 | P2 / Medium | Tidak ada perlindungan replay/rollback: bundel lama yang valid bisa diimpor ulang setelah yang baru. Master dan histori kembali ke data lama, dan `cutover-param` menulis ulang tanggal cutover | `esb_import/importer.py:1086-1148,925-933`, `efn_mig/bundle.py:267-293` | R-O02, R-O13 |
| SEC-199 | P2 / Medium | `gen-secrets.sh` selalu membuat nilai NYATA `clickhouse_password_mig` + `migration_bundle_key`. Runbook VPS B menjalankannya di host data klien, sehingga `clickhouse-init` membuat user `efn_mig` permanen (SELECT customer/AR/AP) dan kunci bundel tersedia di host pihak ketiga | `scripts/gen-secrets.sh:78-93`, `docs/runbooks/vps-bima.md:815-822`, `infra/clickhouse/clickhouse-init.sh:235-248` | R-S01, R-L01, R-L12 |
| SEC-200 | P2 / Medium | Bundel tanpa batas ukuran: dibaca utuh ke memori (sekitar 3× ukuran berkas) di dalam container `odoo` yang sedang melayani POS (lewat `exec`), sehingga OOM bisa mematikan worker Odoo produksi | `efn_mig/bundle.py:247-278,330-331`, `scripts/migration/odoo_import_esb.py:102`, `run-import.sh:76-77` | R-O13 (ketersediaan) |
| SEC-201 | P3 / Low | Laporan validator (`validation_<run>.json`) tidak diautentikasi. Gerbang §27.5-1 cukup dipenuhi berkas JSON buatan siapa pun yang bisa menulis ke direktori pertukaran | `esb_import/importer.py:186-191`, `scripts/migration/validate_import.py:43` | R-O02 |
| SEC-202 | P3 / Low | Penulisan `.part` mengikuti symlink (tanpa `O_NOFOLLOW`/`O_EXCL`) di direktori pertukaran yang bisa ditulis dua container | `scripts/migration/odoo_import_esb.py:129-135`, `efn_mig/bundle.py:218-223` | R-O02 |
| SEC-203 | P3 / Low | `std_ref()` me-resolve xmlid UoM apa pun tanpa memeriksa model `uom.uom`, sehingga typo di `uom_map.csv` bisa diam-diam memakai id record model lain sebagai UoM | `esb_import/importer.py:410-418,535`, `esb_import/mapping.py:228,426`, `efn_mig/mapping.py:169` | R-O02 |
| SEC-204 | P3 / Low | Retensi ≤ 90 hari tidak tuntas: perintah hapus runbook tidak mencakup `odoo_before_*.dump` (dump DB penuh yang disarankan disimpan di direktori yang sama), dan "crypto-erase" tidak menyebut pemusnahan salinan tersegel `[SA]` kunci bundel | `docs/runbooks/migrasi-esb-odoo.md:54,223,230,249-253` | R-L01, R-L12 |
| SEC-205 | P3 / Low | Siklus hidup user teknis `efn_migration` (dibuat tanpa password, `active=False` di luar jendela) belum ada di runbook buka/tutup jendela | `docs/runbooks/migrasi-esb-odoo.md:84-86,227-236`; ADR-042:222 | R-S16 |
| SEC-206 | P3 / Info | `efn_t2s_definer` punya `SELECT ON efn_dwh.*`, yang kini ikut mencakup `dim_esb_customer`, `fact_esb_ap_open`/`ar_open`, `map_esb_odoo` | `infra/clickhouse/access/text2sql-access.sql:28` | R-S05, R-L01 |
| SEC-207 | P3 / Info | Satu kunci simetris untuk dua arah (bundel data dan bundel hasil). Pihak yang memegang kunci di sisi Odoo bisa membuat `kind=bundle`, dan sebaliknya | `efn_mig/bundle.py:65-75,202-215` | R-O02 |

## Detail temuan

### SEC-197 — P1/High (integritas) — Segmen histori dipecah per cabang ESB, bukan per gudang Odoo

**Fakta di kode (terverifikasi):**
- `build_branches` memetakan cabang ESB bertipe `location` ke xmlid gudang **induk**: `index.wh_key[branch_id] = parent`
  (`efn_mig/mapping.py:239-242`). Semua baris histori cabang lokasi diekspor dengan `branch_key` = gudang induk
  (`flow_row`/`sales_daily_row`/`sales_line_row`, `mapping.py:572,591,615`).
- `plan_parts` membagi part per **`branch_id` ESB**, greedy dan urut id (`segments.py:98-114`). Cabang induk (mis. 4) dan
  cabang lokasi (mis. 30, fixture `tests/conftest.py:20`) bisa jatuh di part berbeda bila satu company×bulan melebihi
  `EFN_MIG_SEGMENT_MAX_ROWS` (default 500.000; `sales_line` PB ±8 juta baris per 24 bulan, jadi pemecahan sangat
  mungkin terjadi).
- Importer menambah gudang induk ke `wh_by_key` dari `branch_ids` + `branch_map.csv` (`_segment_branch_keys`,
  `importer.py:772-792`). Lalu, untuk setiap gudang:
  - `legacy_sales_line`: `bulk_replace_slice` menjalankan **DELETE company × gudang × rentang lalu INSERT**
    (`importer.py:890-916`, `bulk.py:484-499`). Part 2 (hanya baris lokasi 30) menghapus semua baris gudang 4 yang
    dimuat part 1.
  - `legacy_flow_daily`/`legacy_sales_daily`: upsert per kunci `(day, company, warehouse, product|menu)`
    (`importer.py:827-876`). Nilai part 2 **menimpa** nilai part 1 untuk kunci yang sama, bukan menjumlahkan.

**Skenario:** brand PB, Okt 2026, 700k baris. Part 1 = cabang 1..20 (termasuk gudang 4), part 2 = cabang 21..40
(termasuk lokasi 30, induk 4). Impor part 1 menghasilkan 300k baris `sales.line` untuk gudang 4. Impor part 2 menghapus
300k baris itu, lalu menyisipkan 2k baris lokasi 30. Hasil akhir: arsip penjualan gudang 4 untuk bulan itu hilang.
Mengimpor ulang part 1 saja tidak memperbaikinya, karena hasil kembali bergantung pada urutan.

**Kenapa tidak tertangkap:** `odoo_totals` dihitung dari tabel tepat setelah part bersangkutan diimpor
(`importer.py:954-1044`). Untuk `sales.line`, Σ hasil part 1 + Σ hasil part 2 = Σ bundel part 1 + Σ bundel part 2,
sehingga rekonsiliasi lulus walaupun tabel akhir hanya berisi data part 2. `bulk_check` hanya memeriksa company yang
salah, bukan baris yang hilang.

**Rekomendasi:**
1. Di `plan_parts`, kelompokkan menurut **gudang target** (`wh_key`). Satu gudang Odoo beserta semua cabang ESB yang
   dipetakan kepadanya selalu berada di satu part. Tambahkan test dengan fixture lokasi 30 → 4 dan `max_rows` kecil.
2. Pertahanan berlapis di importer: tolak (exit 3) bila satu gudang target menerima baris dari `branch_ids` yang tidak
   lengkap. Caranya: manifest menyertakan `warehouse_keys` per part, lalu validator memeriksa bahwa himpunan gudang
   antar-part sesama `parent_run_id` saling lepas.
3. Rekonsiliasi lintas segmen: hitung ulang Σ dari tabel Odoo **setelah semua part** selesai per `parent_run_id`
   (query baru, bukan penjumlahan `odoo_totals` per part).

Status: terverifikasi di kode. Terjadi atau tidaknya bergantung data: perlu cabang `location` yang memiliki histori dan
segmen yang dipecah.

### SEC-198 — P2/Medium — Replay atau rollback bundel lama

**Fakta:** `open_bundle` hanya memverifikasi integritas dan kecocokan header↔manifest (`bundle.py:267-293`).
`run_import` tidak menyimpan atau memeriksa `run_id`, `created_at`, `parent_run_id`, maupun `source.snapshot` dari run
yang sudah diterapkan (`importer.py:1086-1148`). Run_id histori hanya disimpan per baris `flow/sales.daily`, dan tidak
dipakai sebagai gerbang.

**Skenario:** operator (atau skrip otomatis yang salah memilih berkas) menjalankan
`run-import.sh <run_lama>.efnmig --validation validation_<run_lama>.json --apply`. Validasi lama tetap cocok dengan
`manifest_sha256`-nya sendiri. Akibatnya:
- harga menu, nama, BoM yang terakhir ditulis `efn_migration` kembali ke snapshot lama (`importer.py:497-499`);
- irisan histori diganti data lama (`bulk_replace_slice`);
- dengan `--step cutover`, `efn_mig_esb.cutover_date.<PT>` ditulis ulang dengan tanggal manifest lama
  (`importer.py:925-933`). Tanggal ini menggeser batas forecast (`efn_demand_forecast.py:21-24`) dan batas validasi
  histori (`bulk.py:243-247`).

Bundel lama tetap valid sampai kunci dirotasi (jendela migrasi bisa berbulan-bulan per brand).

**Rekomendasi:** catat run yang diterapkan di Odoo (mis. `ir.config_parameter`
`efn_mig_esb.last_run.<brand>.<company>[.<YYYYMM>]` = `created_at` + `run_id`, atau model kecil read-only). Tolak
(exit 4) bundel dengan `created_at` lebih tua dari run yang sudah diterapkan untuk cakupan yang sama, kecuali dengan
flag eksplisit `--allow-older-run` yang dicatat di laporan. Untuk `cutover-param`, tolak perubahan tanggal bila
parameter sudah ada dan nilainya berbeda tanpa `--confirm-cutover-change`.

### SEC-199 — P2/Medium — User `efn_mig` dan kunci bundel otomatis aktif di host VPS B

**Fakta:**
- `scripts/secrets.list:83-84` menandai kedua secret `host:disabled`, tetapi `gen-secrets.sh:82-93` **selalu** membuat
  nilai acak nyata untuk keduanya (tanpa memeriksa `APP_ENV`). Komentar di skrip menyebut ini perilaku "dev/CI".
- `vps-bima.md:815-822` (B6) memerintahkan `env -u APP_ENV bash scripts/gen-secrets.sh` di **host B** (data klien,
  pemroses pihak ketiga), dengan catatan "secret service yang tidak dijalankan ikut terbuat (tidak berbahaya)".
- `clickhouse_password_mig` di-mount ke `clickhouse-init` di **base** compose (`docker-compose.yml:1554`), dan
  `clickhouse-init.sh:235-248` membuat user `efn_mig` bila secret bukan placeholder. Langkah B8.1 (`up clickhouse-init`)
  karena itu membuat `efn_mig` (role `mig_rw`: SELECT `dim_esb_customer`, AP/AR, COA) secara permanen di B.
- ADR-043 §3.4 (baris 150) menetapkan bahwa skrip migrasi G43 tidak dijalankan dari VPS, dan desain G43 menetapkan
  `efn_mig` hanya ada selama jendela migrasi.

**Dampak:** satu kredensial baru bertahan lama di host pihak ketiga dengan hak baca data customer/keuangan, di luar
jendela, tanpa entri kustodi. `migration_bundle_key` juga ada di B, sehingga bundel yang dibuat di B dengan kunci itu
bisa dibuka siapa pun yang memegang `secrets/` B. Pola yang sama berlaku untuk environment non-CI lain yang memakai
`gen-secrets.sh` (mis. gladi prod lokal K25-06).

**Rekomendasi:**
1. `gen-secrets.sh`: buat nilai nyata hanya bila `APP_ENV` ∈ {dev, ci, test} **atau** `EFN_MIG_ENABLE=1`. Selain itu
   tulis `disabled`.
2. `vps-bima.md` B6: tambahkan langkah eksplisit
   `printf disabled > secrets/clickhouse_password_mig.txt; printf disabled > secrets/migration_bundle_key.txt`, plus
   pemeriksaan "harus `disabled`" di checklist B0 dan di skrip pemeriksaan tahap A (`vps-bima.md:490`). Tambahkan uji
   `SHOW USERS` (tidak boleh ada `efn_mig`) di B8.1.
3. Tegaskan di ADR-043/runbook bahwa `esb_to_odoo export` juga tidak dijalankan di B. Data dipindah ke infrastruktur
   EFN dulu (baris "Pemindahan" ADR-043 §3.4), lalu ekspor di sana.

### SEC-200 — P2/Medium — Bundel tanpa batas ukuran diproses di container Odoo produksi

**Fakta:** `parse_header` membatasi header 4 KB, tetapi seluruh berkas dibaca ke memori (`read_bytes`). Setelah itu
plaintext tar (salinan kedua) dan anggota tar (salinan ketiga) juga di memori (`bundle.py:271-278,296-311`). Batas
gzip 512 MB berlaku per berkas, bukan total, dan `count_lines` mendekompresi semua berkas. Importer berjalan lewat
`docker compose exec ... odoo odoo shell` (`run-import.sh:76-77`), sehingga berbagi batas memori cgroup dengan worker
Odoo yang sedang melayani POS dan backoffice.

**Skenario:** segmen besar (mis. `EFN_MIG_SEGMENT_MAX_ROWS` dinaikkan ke 5 juta sesuai batas `settings.py:80`, atau
satu cabang > batas yang tetap jadi satu part, `segments.py:99`) menghasilkan bundel ratusan MB. Pemrosesan membutuhkan
memori sekitar 3× ukuran bundel ditambah batch `bulk_replace_slice` penuh per gudang (`bulk.py:462-473`). Container
`odoo` terkena OOM-kill dan semua worker ikut mati. POS online gagal (kasir offline-mode tetap jalan, tetapi OTP, poin,
dan MGM mati).

**Rekomendasi:**
1. `MAX_BUNDLE_BYTES` (mis. 256 MB) diperiksa dari `stat()` sebelum `read_bytes()` di `read_bundle`/importer, plus
   batas total byte terdekompresi per bundel.
2. Jalankan importer di container sekali jalan terpisah (image Odoo yang sama, `docker compose run --rm` dengan batas
   memori sendiri, DB yang sama), bukan `exec` ke service `odoo` yang hidup. Bila tetap `exec`, wajibkan di runbook
   bahwa impor nyata hanya di jendela pemeliharaan, dengan pemantauan memori.

### SEC-201 — P3/Low — Laporan validator tidak diautentikasi

`check_gates` hanya membandingkan `validation["manifest_sha256"]` dan `errors == 0` (`importer.py:190`). Berkas JSON
ditulis polos (`validate_import.py:43`). Siapa pun yang bisa menulis ke `MIG_EXCHANGE_DIR` (operator host, container
`esb-migrate`, container `odoo`) dapat membuat `validation_<run>.json` dengan `errors: 0` tanpa menjalankan validator.
SHA-256 manifest bisa dihitung oleh pemegang kunci; tanpa kunci, berkas bundel saja tidak cukup.
**Rekomendasi:** HMAC laporan validasi dengan kunci MAC turunan baru (`efn:mig-esb:val:v1`), atau bungkus laporan
sebagai bundel `kind=validation`. Importer lalu memverifikasinya. Alternatif yang lebih sederhana: importer menjalankan
ulang validator in-process (validator murni) dan tidak membaca berkas.

### SEC-202 — P3/Low — Penulisan berkas mengikuti symlink

`odoo_import_esb.py:131` memakai `os.open(tmp, O_WRONLY|O_CREAT|O_TRUNC, 0o600)` tanpa `O_NOFOLLOW`/`O_EXCL`.
`write_bundle` (`bundle.py:220-221`) memakai `write_bytes` dengan pola yang sama. Direktori pertukaran bisa ditulis
container `esb-migrate` dan `odoo` (uid sama). Pihak yang menguasai salah satu container dapat menanam symlink
`<run_id>.result.efnmig.part` → `/var/lib/odoo/...`, lalu proses lain memotong (truncate) dan menimpa berkas tujuan
(mis. filestore). Path input sudah aman (`_inside` me-resolve symlink, `odoo_import_esb.py:57-67`).
**Rekomendasi:** `os.open(..., O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW, 0o600)` setelah `unlink(missing_ok=True)` pada
`.part`, di kedua tempat.

### SEC-203 — P3/Low — `std_ref` tanpa pemeriksaan model

`_Run.std_ref` me-resolve `env.ref(full)` untuk xmlid apa pun (`importer.py:410-418`). Hasilnya dipakai sebagai
`uom_id` produk (`esb_import/mapping.py:228,246`), `product_uom_id` BoM (`mapping.py:426`), dan target alias UoM
(`importer.py:535`, yang menautkan xmlid dengan `model="uom.uom"` ke id record apa pun). `uom_map.csv` hanya divalidasi
pola `modul.nama` (`efn_mig/mapping.py:169`). Typo seperti `base.main_company` menghasilkan id 1, yang mungkin ada di
`uom_uom`, sehingga UoM salah terpasang diam-diam. Ini bukan jalur serangan (peta disetujui PIC dan bundel
diautentikasi), tetapi merusak integritas data induk.
**Rekomendasi:** `std_ref(full, model="uom.uom")` → `rec._name == model` atau None (sehingga baris tercatat `missing`).
Validator memeriksa awalan `uom.` untuk `uom_xmlid`.

### SEC-204 — P3/Low — Retensi bundel ≤ 90 hari belum tuntas

- `migrasi-esb-odoo.md:249-253` menyarankan `pg_dump` **DB Odoo penuh** ke `$MIG_EXCHANGE_DIR/odoo_before_*.dump`,
  dengan catatan "ikut aturan hapus §7". Namun perintah hapus §7 (`:223`) hanya `*.efnmig`, `*.part`, `*.json`, sehingga
  dump tidak terhapus. Dump juga tidak terenkripsi (tidak mendapat perlindungan crypto-erase dari rotasi kunci). Saat
  ini hanya dev, tetapi pola ini mudah tersalin ke staging.
- `:54` menyebut `migration_bundle_key` "`[SA]`, salinan tersegel", dan `:230` menyebut `printf disabled` sebagai
  "rotasi/crypto-erase". Crypto-erase baru sah bila **salinan tersegel Superadmin juga dimusnahkan** dan dicatat.
  `key-custody.md` belum memuat `migration_bundle_key`.

**Rekomendasi:** tambahkan `-o -name '*.dump' -o -name 'validation_*' -o -name 'reconcile_*'` (setelah diarsipkan) ke
perintah hapus, atau simpan dump di luar direktori pertukaran dengan enkripsi `age`. Tambahkan langkah "musnahkan
salinan tersegel `[SA]` + catat di key-custody" di §7.4, dan entri `migration_bundle_key` di `key-custody.md`.

### SEC-205 — P3/Low — Siklus hidup user `efn_migration`

ADR-042:222 dan docs/04 §27.9 (baris 3408) menetapkan user internal tanpa password/API key, `active=False` di luar
jendela. Runbook §2.6 hanya menyebut "user ada dan anggota semua company target", tanpa langkah pembuatan (tanpa
password, tanpa grup login) maupun pengarsipan di §7.4. Importer sudah bisa memakai user yang diarsipkan
(`find_migration_user` dengan `active_test=False`, `importer.py:1080`), sehingga `active=False` bisa dipertahankan
permanen.
**Rekomendasi:** tambahkan perintah `odoo shell` di runbook §2.6 untuk membuat atau memastikan user (`active=False`,
tanpa password, tanpa API key, `company_ids` = PT target saja), dan pemeriksaan di §7.4 (tetap `active=False`,
`company_ids` dikembalikan minimum).

### SEC-206 — P3/Info — Grant wildcard `efn_t2s_definer`

`GRANT SELECT ON efn_dwh.* TO efn_t2s_definer` (`text2sql-access.sql:28`) kini juga mencakup tabel G43 yang memuat nama
customer B2B (bisa nama orang) dan data AP/AR per dokumen. Saat ini belum bisa dieksploitasi: user `HOST NONE`, view
`efn_t2s` tetap, dan `efn_text2sql` readonly=2 tanpa DDL. Risikonya baru muncul bila suatu saat view baru dibuat tanpa
review. **Rekomendasi:** ganti dengan grant per tabel/view yang dirujuk tujuh view `efn_t2s` (pola `mig-access.sql`),
lalu tambahkan test bahwa `efn_t2s_definer` tidak bisa membaca `dim_esb_customer`/`fact_esb_a?_open`.

### SEC-207 — P3/Info — Kunci tunggal dua arah

Kunci HKDF yang sama dipakai untuk `kind=bundle` (esb-migrate → Odoo) dan `kind=result` (Odoo → writeback). `kind`
terikat di AAD dan manifest dan selalu diperiksa (`expect_kind`), sehingga tidak ada kebingungan jenis. Namun container
Odoo selama jendela memegang kemampuan membuat bundel data yang sah. **Rekomendasi (opsional):** turunkan kunci MAC/enc
per arah (`info` berbeda untuk `bundle` vs `result`). Kunci sisi Odoo hanya bisa memverifikasi atau membuka `bundle`
(tetap simetris, jadi pemisahan penuh butuh tanda tangan asimetris Ed25519 untuk bundel data). Catat sebagai sisa
risiko yang diterima bila tidak dikerjakan.

## Kontrol yang sudah benar (diverifikasi di kode)

- **Kriptografi bundel** (`efn_mig/bundle.py`): AES-256-GCM dengan nonce 96-bit `os.urandom` per bundel. Header kanonik
  (format, run_id UUID, brand, kind) menjadi **AAD**, dan header non-kanonik ditolak (`:262`). HKDF-SHA256 menurunkan
  kunci enc/mac terpisah (`efn:mig-esb:enc:v1`/`mac:v1`). HMAC manifest atas canonical JSON. `hmac.compare_digest` untuk
  sig dan sha256. Urutan verifikasi: tag → struktur tar → HMAC → header=manifest → sha256 + jumlah baris sebelum data
  dipakai. Hanya satu `FORMAT`, jadi tidak ada jalur downgrade. Kunci minimal 256-bit hex, dan placeholder `disabled`
  gagal tertutup.
- **Tar/gzip**: tar tidak pernah diekstrak ke disk. Hanya anggota reguler dengan nama ber-regex ketat (tanpa `/` selain
  `maps/`, tanpa `..`). Anggota ganda dan berkas di luar manifest ditolak. Gzip didekompresi streaming dengan batas
  512 MB dan rasio 50×. NaN/Infinity ditolak.
- **SQL**: `bulk.py` memakai `odoo.tools.SQL` (identifier di-quote, nilai terikat), mengikuti kolom whitelist
  `_efn_upsert_spec`, menolak kolom tidak dikenal (fail closed), memvalidasi tipe dan rentang, serta menggantikan
  `check_company` (gudang, produk, POS milik company baris; tanggal < cutover). `DELETE` irisan dibatasi company ×
  gudang yang sudah divalidasi milik company. Irisan kosong pun divalidasi. Query ClickHouse `source.py`
  berparameter, dan nama tabel hanya dari konstanta.
- **Isolasi lintas company di importer**: company manifest ⊆ target (default per brand), xmlid dihitung ulang, dan semua
  referensi `*_key` wajib berawalan `efn_mig_esb.<brand>_<company>_<entitas>_` (`esb_import/mapping.py:103-147`).
  Histori: kunci baris persis (kolom PII tambahan = exit 3), referensi milik brand/company segmen, gudang harus milik
  company. `_load_xmlids` per prefix brand mencegah tabrakan antar-brand. Skema xmlid (`[A-Z0-9_]` kanonik, selain itu
  ditambah sha1) mencegah tabrakan antar-kode.
- **Odoo histori**: model read-only (`create/write/unlink` hanya `env.su` + konteks impor). Helper bulk berupa fungsi
  modul atau metode berawalan `_`, sehingga tidak bisa diakses RPC. Record rule **global** `company_id in company_ids`
  pada ketiga model. ACL hanya baca. Grup pembaca terpisah. Tidak ada grup tulis.
- **Least privilege ClickHouse**: role `mig_rw` = SELECT per objek + INSERT hanya `map_esb_odoo`, tanpa `raw_doc`, tanpa
  DDL (`allow_ddl=0 CONST`), dengan `REVOKE ALL` defensif. User dihapus otomatis bila secret placeholder. Tabel G43
  tidak di-grant ke `metabase_reader`/`insight_reader`/`text2sql_reader`.
- **PII**: customer dan AR/AP di-whitelist sebelum disimpan, dan `raw_doc` di-redaksi dua kali. Bundel tidak membawa
  alamat/telepon/NPWP. Error entitas customer/supplier hanya mencatat nama tipe exception. `map.jsonl` dan laporan
  hanya berisi kode, bukan nama. `sales_num` = nomor dokumen ESB tanpa data pelanggan. Retensi ≤ 90 hari dan rotasi
  kunci dirancang (lihat SEC-204 untuk celah).
- **Container `esb-migrate`**: hanya network `esb` (internal, tanpa egress, tanpa jalur ke Odoo), `read_only`,
  `cap_drop: ALL`, `no-new-privileges`, non-root, tmpfs untuk plaintext, TLS verify ke ClickHouse tanpa fallback
  plaintext di production, image dipin digest dan Trivy bersih. Overlay `docker-compose.migration.yml` tidak menambah
  port/network/Traefik, kode dipasang hanya-baca, dan `create_host_path: false`.
- **Argumen importer**: path dibatasi di dalam direktori pertukaran (resolve symlink). `run-import.sh` memvalidasi semua
  argumen dengan regex sebelum menyusun JSON. Production + run nyata wajib `--confirm-brand`. Dry-run = savepoint
  rollback. Hasil dry-run ditolak oleh writeback. Langkah saldo awal adalah stub yang gagal tertutup. `EFN_MIG_SALDO_BLOCKED`
  (IMD/CUP) ditegakkan di export dan importer.
- **Platform SQL G43**: idempoten, transaksional, `SET LOCAL ROLE efn_platform_owner`, identifier lewat `format('%I')`.
  CHECK QR-meja/reservasi sengaja tidak dilebarkan.

## Catatan kepatuhan (UU PDP 27/2022)

- Inventaris PDP "Bundel migrasi ESB" dan persetujuan DPO sudah menjadi prasyarat produksi di runbook §2.7. Ini wajib
  ditegakkan sebelum impor nyata produksi.
- Nama customer B2B perorangan dan nama supplier perorangan (UMKM) adalah data pribadi yang berpindah ESB → ClickHouse →
  bundel → Odoo. Dasar pemrosesan (kontrak/kepentingan sah) dan retensi di Odoo (mengikuti master partner) perlu
  disebut di entri inventaris.
- Di VPS B (pemroses pihak ketiga), SEC-199 harus ditutup sebelum gerbang G-9 ADR-043 dinyatakan lulus.

## Yang belum diverifikasi

- Perilaku nyata pada data ESB: apakah cabang bertipe `location` di `branch_map.csv` PB memiliki baris histori
  (SEC-197). Ukuran bundel aktual per segmen (SEC-200).
- Runbook belum pernah dijalankan end-to-end (diakui runbook).
- `check-encryption.sh` render `prod-migration` dan job CI tidak dibaca ulang. Hasil lead diterima.
