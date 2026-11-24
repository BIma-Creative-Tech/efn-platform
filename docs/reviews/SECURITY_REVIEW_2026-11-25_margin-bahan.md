# Security Review — G67 "Margin bahan" (`feat/insight-margin-bahan`, belum di-commit) — 25 Nov 2026

Reviewer: efn-security-reviewer (read-only). Lingkup: `infra/clickhouse/init/24-margin-bahan.sql`,
`infra/clickhouse/access/{esb-access.sql,insight-access.sql,insight-esb-policy.sh}`, `infra/clickhouse/clickhouse-init.sh`,
`infra/compose/docker-compose.esb.yml`, `services/esb-extract/efn_esb/{margin,config,runner,__main__}.py`, `tests/test_margin.py`,
`services/insight-web/app/(app)/margin-bahan/page.tsx`, `services/insight-web/lib/{margin-bahan,queries,fnb-personas,sm-personas,page-filters}.ts`.
Konteks yang ikut dibaca: `efn_esb/{aggregates,store}.py`, `insight-web/{proxy.ts,lib/fnb-data.ts,lib/claims.ts,app/api/export/[name]/route.ts}`,
`efn_insight/lib/insight_token.py`, access file konsumen lain, runbook `esb-extract.md` §6b, docs/04 §31.
Kontrak: docs/04 §31 (L-259..L-264). ADR-040, ADR-041 (Proposed), ADR-022. Risiko: R-S16, R-S17, R-L12, docs/04 §7.3.

## Ringkasan eksekutif

Kontrol utama sudah benar. `insight_reader` tetap tanpa hak di `efn_dwh`. Tiga wrapper `efn_insight.*` berupa view DEFINER kelas PT, tercakup
row policy dan pagar fail-closed. esb_writer hanya punya `SELECT, INSERT` per tabel. Tiga tabel mart dibuat admin dengan
`MergeTree` + `storage_policy='encrypted'` dan dijaga pagar init. SQL memakai templat konstan + parameter. Log tanpa isi data. Halaman ditutup
untuk non-exec di proxy dan di halaman, dan tidak ada jalur ekspor CSV. Satu temuan **Medium**: job mengasumsikan "satu PT per brand"
tanpa menegakkannya. Untuk brand multi-PT (Bacha: BCF + BCR), penjualan, COGS, dan harga beli kedua PT digabung lalu diberi label satu PT.
Akibatnya data BCR bisa terbaca oleh pengguna yang hanya punya cakupan BCF. Saat ini temuan ini laten (efn-prod hanya `pb`, L-259), tetapi bisa
terpicu lewat satu env atau CLI `--brand bacha`.
**Verdict: boleh merge ke dev (tidak ada Critical/High). SEC-MB-01 sebaiknya ditutup di PR ini (perbaikan fail-closed ±5 baris + test);
paling lambat wajib sebelum brand multi-PT dimasukkan ke `ESB_MARGIN_BRANDS`.**

## Tabel temuan

| ID | Severity | Lokasi | Ringkas | Risiko |
|---|---|---|---|---|
| SEC-MB-01 | Medium | `services/esb-extract/efn_esb/margin.py:125-147,160-165,626-632,676`; `__main__.py:103,305` | Brand multi-PT: semua PT digabung dan dilabel PT mayoritas → data PT minoritas terbaca oleh pengguna PT mayoritas (kebocoran lintas PT teragregasi) | multi-company, R-L12 |
| SEC-MB-02 | Low | `margin.py:697-700`; `store.py:65-67`; `24-margin-bahan.sql:86-108` | Versi terbaru dipilih per tabel secara terpisah: INSERT gagal di tengah atau tabel bervolume nol menampilkan kombinasi versi lama + baru tanpa tanda di halaman | integritas data keuangan Insight |
| SEC-MB-03 | Low | `infra/clickhouse/access/insight-access.sql:214-216`; `insight-esb-policy.sh:40-42` | Wrapper membuka harga beli absolut (`receipt_qty, value, price, price_4m`) yang tidak dipakai halaman; gerbang persona hanya di lapisan aplikasi | L-260, R-L12 |
| SEC-MB-04 | Low (pra-ada, PLAUSIBLE) | `insight-web/lib/fnb-personas.ts:85,94`; `lib/claims.ts:21,188`; `efn_insight/lib/insight_token.py:58,73` | Peran `exec` satu himpunan untuk SM dan F&B: exec Supermarket yang juga punya kode PT F&B (sebagai `user`/marketing) lolos ke `/margin-bahan` | R-S16 |
| SEC-MB-05 | Info | `efn_esb/__main__.py:151-152`; `config.py:320-330` | Salah konfigurasi fitur opsional (`ESB_MARGIN_BRANDS` tanpa `purchase_line`) menghentikan loop ekstraksi harian (fail-fast saat start) | ketersediaan ekstraksi |
| SEC-MB-06 | Info | `docs/04_SERVICE_CONTRACTS.md:6768-6769,6799-6800` | Repo publik (L-262) memuat kesimpulan analisis klien dan nama sub-kategori ESB nyata | L-264 |

## Detail

### SEC-MB-01 (Medium) — Asumsi "satu PT per brand" tidak ditegakkan; brand multi-PT digabung dan dilabel satu PT

**Lokasi:**
- `margin.py:142-147` (`SALES_SQL`: `WHERE brand = …` tanpa `company_code`) dan `:125-141` (`PURCHASE_SQL`: sama).
- `margin.py:160-165` + `:626-632` (`pick_company`: PT dengan cabang aktif terbanyak).
- `margin.py:676` (satu `company` untuk semua baris).
- Jalur pemicu: `__main__.py:103` (`--brand` = semua `KNOWN_BRANDS`), `:305`; env `ESB_MARGIN_BRANDS`.
- `test_margin.py:255-256` justru mengunci perilaku ini (`[("BCF", 5), ("BCR", 2)] → "BCF"`).

**Fakta pendukung:** brand `bacha` memiliki dua PT. Defaultnya `BCF`, dan cabang tertentu menjadi `BCR` lewat `ESB_BRANCH_COMPANY_OVERRIDES`
(docs/04 baris 2424; `docker-compose.esb.yml:84-89`). `fact_esb_purchase_line` dan `agg_esb_sales_daily` menyimpan `company_code` per
cabang (`23-g65-esb-purchase-stock.sql:20-24`), tetapi margin.py tidak memakainya.

**Skenario:** operator menjalankan `refresh-margin --brand bacha` (CLI tidak dibatasi `ESB_MARGIN_BRANDS`) atau menambahkan `bacha` ke
`ESB_MARGIN_BRANDS`. Hasilnya:
1. Semua baris ketiga mart diberi label `company_code='BCF'`. Isinya adalah penerimaan PO + penjualan + COGS cabang BCF **dan** BCR.
2. Exec yang sesinya hanya `codes=['BCF']` lolos row policy kelas PT. Ia melihat margin kotor gabungan, indeks biaya, dan tabel 20 bahan
   (nama bahan + nilai pakai Rp/bulan) yang dipengaruhi pembelian dan penjualan BCR (PT Retail). Ini juga menyentuh harga transfer
   BCR↔BCF (R-L04).
3. Exec yang hanya punya `codes=['BCR']` tidak melihat apa pun, padahal datanya ikut terhitung.

Data yang bocor berupa agregat (bukan baris transaksi), tetapi isinya data komersial sensitif (L-260) milik PT lain. Ini melanggar prinsip
isolasi multi-company yang sama dengan row policy. Engineer mencatat konsekuensi "PT minoritas tidak melihat"
(`docs/agent-reports/efn-data-ai-engineer.md:47-50`), tetapi belum mencatat sisi kebocorannya.

**Rekomendasi (pilih satu, dengan test):**
1. **Paling sederhana (disarankan untuk PR ini):** buat `pick_company` / `refresh_brand` fail-closed. Bila `COMPANY_SQL` mengembalikan
   > 1 PT aktif, lempar `MarginError("multi_company")`: tidak ada tulisan, status `error` tercatat. Ubah `test_pick_company_most_active_branches`
   menjadi uji penolakan. Tambahkan catatan di runbook §6b: brand multi-PT belum didukung.
2. Bila Bacha memang dibutuhkan: hitung **per PT**. Tambahkan `AND company_code = {company:String}` di `PURCHASE_SQL` dan `SALES_SQL`, lalu
   jalankan `compute` sekali per PT (BOM tetap per brand). Duplikasi angka tidak terjadi karena baris PT berbeda tidak saling menimpa. Halaman
   menjumlahkan lintas PT yang dipilih, dan itu benar karena setiap baris hanya berisi data PT-nya.

### SEC-MB-02 (Low) — Konsistensi versi antar-tabel tidak dijamin

**Lokasi:** `margin.py:697-700` (tiga INSERT berurutan: group → menu → price), `store.py:65-67` (`insert` dilewati bila `rows` kosong),
`24-margin-bahan.sql:86-108` (setiap view memilih `max(_version)` dari tabelnya sendiri).

**Skenario:**
- INSERT `MENU_TABLE` gagal (timeout/OOM) setelah `GROUP_TABLE` berhasil. Grafik dan kontribusi kelompok tampil dari versi baru, sedangkan KPI
  margin dan tabel menu dari versi lama. Halaman tidak memberi tanda karena insight tidak membaca `aggregate_refresh` (README insight-web:169).
- Refresh `ok` dengan `result.price == []` (mis. tidak ada daun resep yang punya harga PO) tidak menulis apa pun ke tabel harga. Tabel bahan
  tetap menampilkan versi minggu lalu, sementara kelompok dan menu sudah versi baru.

Masalah ini sudah diakui di header DDL dan runbook §6b:310. Ini bukan jalur serangan, tetapi angka keuangan bisa tampil campur tanpa tanda.

**Rekomendasi:**
1. Di `refresh_brand`, wajibkan ketiga hasil tidak kosong (`no_price` / `no_group` → `MarginError`) agar setiap versi selalu lengkap.
2. Tabel harga ditulis terakhir. View `v_esb_ing_group_monthly` dan `v_esb_menu_cost_monthly` memfilter
   `(t.brand, t._version) IN (SELECT brand, max(_version) FROM efn_dwh.mart_esb_ing_price_monthly GROUP BY brand)`. Dengan begitu versi yang
   tampil hanya versi yang tabel terakhirnya sudah tertulis. Definer sudah punya `SELECT` pada tabel harga, jadi tidak perlu grant baru.
   Perbarui `test_views_show_latest_version_per_brand_without_version_column`.

### SEC-MB-03 (Low) — Minimisasi kolom wrapper; persona hanya ditegakkan di aplikasi

**Lokasi:** `insight-access.sql:214-216` (wrapper harga mengekspos `receipt_qty, value, price, price_4m`); `queries.ts:1104-1111` hanya
memakai `idx, usage_value`; `insight-esb-policy.sh:40-42` (kelas PT: tanda tangan `sig3` mencakup `cids|codes|brs`, bukan `roles`).

Harga terima per satuan dan volume pembelian adalah data paling sensitif di fitur ini (posisi tawar terhadap pemasok). Saat ini halaman tidak
memakainya, tetapi kolom-kolom itu tersedia bagi user ClickHouse `efn_insight` yang dipakai bersama oleh semua persona. ClickHouse tidak tahu
persona. Pembatasan exec sepenuhnya bergantung pada insight-web, dan itu benar untuk desain sekarang. Risikonya: query katalog baru yang
memakai view ini di halaman non-exec, atau insight-web yang disusupi, langsung membuka harga beli seluruh PT dalam cakupan sesi.

**Rekomendasi:**
1. Pangkas kolom wrapper `efn_insight.v_esb_ing_price_monthly` sesuai kebutuhan halaman. Kolom harga absolut tetap ada di mart untuk analisis
   admin dan dibuka lagi hanya bila halaman benar-benar membutuhkannya.
2. Tambahkan test statis di insight-web: query `mb_*` hanya dirujuk dari `app/(app)/margin-bahan/`, dan rute itu ada di `EXEC_ONLY_PATHS`.
   Ini mencegah regresi akibat pemakaian ulang query di halaman lain.
3. (Gelombang berikutnya, opsional) Masukkan peran ke tanda tangan untuk kelas "EXEC". Ini butuh perubahan `eit` dan sudah disebut di
   §31.5 sebagai pekerjaan berikutnya.

### SEC-MB-04 (Low, pra-ada, PLAUSIBLE) — Peran `exec` tidak terikat per PT/domain

**Lokasi:** `fnb-personas.ts:85,94` (gerbang `/margin-bahan` = `esbCodesOf(codes).length > 0 && roles.includes('exec')`); `claims.ts:21,188`
(`exec` termasuk `PERSONAS` SM dan `FNB_PERSONAS`); `insight_token.py:58,73` (satu himpunan `roles` untuk semua company; lihat juga
catatan SEC-G47-01 di `insight_token.py:380`).

**Skenario:** pengguna Odoo yang menjadi exec Supermarket (SMD) dan juga punya akses PT PB sebagai pengguna biasa atau marketing mendapat token
`codes=['PB','SMD'], roles=['exec','marketing',…]`. Aturan T1..T9 tidak menolak kombinasi ini. Pengguna itu bisa membuka `/fnb/exec` dan
`/margin-bahan` untuk PB, padahal L-260 bermaksud "manajemen F&B". Celah ini tidak diperkenalkan PR ini (gerbang sama dengan `/fnb/exec`),
tetapi taruhannya naik karena data harga beli dan resep. Statusnya PLAUSIBLE karena bergantung pada penetapan grup di Odoo (apakah grup
Insight exec SM dan F&B memang terpisah).

**Rekomendasi:** pastikan dengan efn-odoo-functional bahwa grup Odoo yang menghasilkan `exec` untuk F&B tidak diberikan otomatis ke exec SM.
Untuk jangka panjang, pisahkan peran (`fnb_exec` atau peran Finance §31.5) lewat revisi `eit`. Catat sebagai risiko terbuka di R-S16.

### SEC-MB-05 (Info) — Salah konfigurasi margin menghentikan ekstraksi harian

**Lokasi:** `__main__.py:151-152` (`validate_margin` sebelum loop); `config.py:320-330`.
Bila `ESB_MARGIN_BRANDS=pb` diset tetapi `ESB_ENTITIES` default (`docker-compose.esb.yml:93`, tanpa `purchase_line`), `run` gagal start dan
container masuk restart loop. Akibatnya **semua** ekstraksi harian berhenti karena fitur opsional mingguan. Perilaku ini disengaja dan
terdokumentasi (runbook §6b:260), serta langsung terlihat saat deploy, jadi bisa diterima. Alternatif yang lebih tahan gagal: catat
`log.error` lalu matikan margin saja (`margin = MarginSettings()`), agar kontrak §31.2 "gagal tidak menghentikan ekstraksi harian" juga
berlaku untuk salah konfigurasi. Keputusan ada di lead.

### SEC-MB-06 (Info) — Fakta analisis klien di dokumen repo publik

**Lokasi:** `docs/04_SERVICE_CONTRACTS.md:6768-6769` (angka hasil analisis klien) dan `:6799-6800`
(nama sub-kategori ESB nyata dan contoh bahan klien).
Sensitivitasnya rendah dan tidak ada nama produk, pemasok, atau angka Rupiah. Namun L-262/L-264 menyatakan repo publik dan laporan analisis
tetap lokal. **Saran:** ganti dengan contoh generik (mis. `Sub A`, `Sub B`, seperti di test) dan hapus kesimpulan analisis dari kontrak.
Kode, test, README, dan runbook sudah bersih: test memakai data sintetis (`BHN-*`, "Roti Bakar Sintetis") dan tidak ada nama produk atau
pemasok yang di-hardcode.

## Hal yang sudah benar (kontrol yang ada)

- **Isolasi PT di ClickHouse:** `insight_reader` tetap `REVOKE ALL ON efn_dwh.*` / `efn_esb.*` (`insight-access.sql:42-43`). Tiga wrapper
  `efn_insight.v_esb_*_monthly` adalah `DEFINER = efn_insight_definer SQL SECURITY DEFINER` dengan kolom eksplisit termasuk `company_code`
  (`:214-224`). Ketiganya masuk `ESB_POLICY_VIEWS` kelas PT (`insight-esb-policy.sh:40-42`), sehingga sesi ber-cabang ditolak di DB.
  `ESB_POLICY_EXPECTED=20` dikunci di `clickhouse-init.sh:482` dan di test. Pagar "view efn_insight tanpa policy → cabut grant + init gagal"
  (`clickhouse-init.sh:492-500`) mencakup view baru secara otomatis.
- **Grant definer minimum:** hanya `SELECT` pada 3 view + 3 tabel dasarnya (`insight-access.sql:97-102`), sesuai kebutuhan view INVOKER
  `efn_dwh.v_esb_*`.
- **Tidak ada konsumen lain:** tidak ada grant wildcard `efn_dwh.*` atau `*.*` di access file mana pun (text2sql, forecast, mig, segment
  memakai `REVOKE ALL` + grant per objek). Metabase tidak mendapat view atau tabel margin. Hal ini dijaga test
  `test_margin_objects_not_granted_to_other_roles`.
- **Least privilege esb_writer:** hanya `GRANT SELECT, INSERT` pada 3 tabel (`esb-access.sql:92-94`), tanpa CREATE/DROP/ALTER/TRUNCATE.
  Semua sumber hitung sudah di-grant `SELECT` per nama, dan test `test_templates_only_read_objects_granted_to_esb_writer` memverifikasi
  rujukan templat ⊆ grant.
- **Enkripsi at rest:** tabel dibuat admin dengan `ENGINE = MergeTree … SETTINGS storage_policy = 'encrypted'` (`24-margin-bahan.sql:40-43,
  62-65,80-83`). Pagar fail-closed SEC-AG-01 diperluas ke `MARGIN_TABLES` (`clickhouse-init.sh:507-519`): penyimpangan berarti hak
  esb_writer dicabut dan init gagal. Koneksi ke ClickHouse lewat HTTPS 8443 dengan CA internal (`docker-compose.esb.yml:109-115`).
- **SQL injection:** semua SQL berupa konstanta modul. Yang disisipkan hanya `$mart`/`$raw`, dan keduanya divalidasi ulang dengan
  `^[a-z_][a-z0-9_]{0,63}$` (`margin.py:174-179`; ini menutup SEC-AG-04 untuk jalur ini). Nilai brand, tanggal, dan penanda batal dikirim
  lewat parameter bertipe ClickHouse (`{brand:String}`, `{start:Date}`, `{cancel:Array(String)}`). `refresh_name` divalidasi
  `^[a-z0-9]{2,16}$`. INSERT memakai `client.insert` dengan nama tabel dan kolom konstan. `clean_product_name` memakai `re.escape`. Di
  insight-web, query `mb_*` berasal dari katalog bernama dengan fragmen `${PT}` yang sama dengan halaman lain, tanpa input bebas.
- **Gerbang halaman:** `/margin-bahan` diperiksa di `proxy.ts:82-83` (lapis 1) dan `fnbRangePage('exec')` → `guard` (lapis 2,
  `fnb-data.ts:22-27`). Pemeriksaannya mencakup flag F&B, sesi ≥ eis3, kode ESB, peran exec, dan penolakan sesi ber-cabang. Pencocokan rute
  tepat per segmen (`/margin-bahanx` tidak cocok). Menu hanya untuk exec. Tidak ada jalur samping: `app/api/export/[name]` hanya melayani
  `TABLES` (tanpa `mb_*`) dan halaman tidak memasang ekspor.
- **Atomisitas tulis (per refresh):** semua data dibaca dan dihitung lebih dulu, lalu `lock.ensure()`, baru INSERT
  (`margin.py:672-700`). Prasyarat yang kosong (`no_company|no_bom|no_purchase|no_sales`), galat ClickHouse, atau `lock_lost` berarti tidak
  ada tulisan (diuji). Snapshot BOM yang gagal berarti tidak dihitung dengan resep basi (`runner.py:476-481`). Versi-insert + view versi
  terakhir membuat refresh yang gagal tidak menghapus tampilan lama.
- **Ketersediaan ekstraksi:** `MarginSchedule.run_due` tidak pernah melempar (try/except berlapis). Jadwal dicoba paling banyak sekali per
  brand per hari WIB. Kunci heartbeat bersama dengan `refresh-aggregates` (tidak berjalan bersamaan). `AuthBreaker` dihormati. Snapshot
  memakai entitas P1 `bom_detail` yang sudah terverifikasi.
- **PDP & log:** tidak ada kolom supplier (dicek test `"supplier" not in code`). Log hanya `margin=<nama> status rows months seconds` +
  `error_label` (kelas + kode, tanpa pesan atau potongan query). Kegagalan klien hanya mencatat `type(exc).__name__`. Retensi TTL 60 hari
  per baris, dan halaman kosong bila refresh berhenti (fail-visible).
- **Default aman:** `ESB_MARGIN_BRANDS` kosong berarti fitur mati. CLI tanpa brand tidak membuka koneksi ClickHouse. Rentang env divalidasi
  (`config.py:306-317`). Container tetap `read_only`, `cap_drop: ALL`, dengan memori dibatasi 512M.

## Belum terverifikasi

- Tidak ada yang dijalankan (review statis; `git diff` tidak dijalankan, file dibaca dari working tree).
- Perilaku nyata ClickHouse 24.8: view INVOKER bersarang di bawah wrapper DEFINER dengan subquery `max(_version)`, dan TTL pada
  `DateTime64` via `toDateTime`.
- Apakah ada cabang PB di `dim_esb_branch` dengan `company_code` selain `PB` (menentukan apakah SEC-MB-01 sudah relevan untuk PB).
- Penetapan grup Odoo untuk peran `exec` SM vs F&B (SEC-MB-04).
