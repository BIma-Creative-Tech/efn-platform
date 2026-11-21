# Security Review G51 — SMD end-to-end (data demo + SMD di halaman F&B Insight)

- Tanggal: 21 Nov 2026 · Reviewer: efn-security-reviewer (read-only)
- Cakupan: worktree `E:/Projects/efn-platform-g47`, branch `feat/wave-51-smd-e2e`, commit `cba9491` (termasuk PR #131/#132/#133)
- Acuan: `docs/design/G51_SMD_E2E_LEAD_SPEC.md`, `docs/04_SERVICE_CONTRACTS.md` §28.13–§28.14, ADR-041 §3.5, L-216 (SEC-G47-01)
- Metode: baca kode statis. **Tidak ada test yang dijalankan** dalam review ini (tidak ada shell); harness row policy CH, pytest, dan vitest belum diverifikasi ulang oleh reviewer.

## Ringkasan eksekutif

Validasi `pt=SMD` / `branch=sm:<id>` di server sudah benar: hanya sesi eis2 + persona `exec` + kode SM + `INSIGHT_SM_ENABLED`, outlet dibatasi ke `wids` sesi, dan halaman F&B tidak mengirim query SM (atau ESB) bila sisi itu tidak terpilih. Tidak ditemukan kebocoran yang bisa dieksploitasi lewat UI saat ini. Ada satu regresi lapis kedua (row policy): view baru `v_sm_payment_daily` tidak memuat persona `category`. Akibatnya pola SEC-G47-01 muncul lagi untuk kombinasi `category + store`, yang oleh L-216 dianggap aman. Dua temuan Low menyangkut guard skrip demo dan file keluaran as-of yang belum di-gitignore.
**Rekomendasi merge:** boleh merge selama `INSIGHT_SM_ENABLED=false` di prod. SEC-G51-01 wajib ditutup sebelum flag dinyalakan di lingkungan yang memakai kombinasi persona category + store.

## Tabel temuan

| ID | Severity | Lokasi | Ringkas | Risiko |
|---|---|---|---|---|
| SEC-G51-01 | Medium | `infra/clickhouse/access/insight-sm-policy.sh:51`; `services/odoo/addons/efn_insight_sm/models/res_users.py:19,62`; `docs/04_SERVICE_CONTRACTS.md:5161` | `v_sm_payment_daily` = `exec,store` (tanpa `category`), padahal `STORE_COVERING_PERSONAS = {exec, category}` mengasumsikan exec dan category mencakup semua view store. Sesi `category + store` lolos `hasAny` lewat `store`, sedangkan `wids` (gabungan, dari category) memuat semua outlet. Akibatnya pembayaran semua outlet terbaca di lapis row policy | R-L01 (UU PDP: otorisasi/minimalisasi), R-S (otorisasi data), L-216 |
| SEC-G51-02 | Low | `scripts/dev/load_forecast_asof.py:32-33,68-73` | Guard demo hanya env `EFN_ASOF_CONFIRM=demo`; tidak ada cek DB/PT demo. Skrip `sudo()` bisa menulis run forecast `ok` ke DB produksi atau ke PT mana pun | R-O (integritas data), D-xx demo/prod separation |
| SEC-G51-03 | Low | `.gitignore` (tidak ada `var/`); `docs/runbooks/demo-supermarket-seed.md:294-305` | `var/forecast-asof/*.json` (5–15 MB × 8, angka forecast per SKU × gudang) belum di-ignore, jadi rawan ter-commit | R-S (data bisnis di repo) |
| SEC-G51-04 | Info | `services/forecast-worker/efn_forecast/__main__.py:160`; `asof.py:146-147` | `--out` menerima path apa pun dan menimpa tanpa cek. Ini input operator CLI (bukan input tak tepercaya), container prod `read_only` + uid 65534 | — |
| SEC-G51-05 | Info | `services/etl/sql/odoo-readonly-grants.sql:612`; `docs/04_SERVICE_CONTRACTS.md:5171,5175` | Kontrak menyebut grant `expiration_time` bersyarat `\if` dan jumlah policy 32, sedangkan kode memakai grant tanpa syarat dan policy 33 (sesuai spesifikasi lead). Selisih dokumen, bukan celah | — |

## Detail

### SEC-G51-01 (Medium): `v_sm_payment_daily` membuka lagi celah persona × wids untuk `category + store`

**Kode.** `insight-sm-policy.sh:51`:
```
v_sm_payment_daily W exec,store
```
Predikat kelas W (`insight-sm-policy.sh:87-91`) = `hasAny(personas, ['exec','store']) AND has(cids, company_id) AND has(wids, warehouse_id)`. Persona dan `wids` dievaluasi terpisah.

Perbaikan jangka pendek SEC-G47-01 di Odoo (`res_users.py:19,62`) hanya mencabut `store` bila persona PT yang dipegang **bukan** `exec`/`category`:
```python
STORE_COVERING_PERSONAS = frozenset({"exec", "category"})
...
if "store" in held and org and not org & STORE_COVERING_PERSONAS:
    held = held - {"store"}
```
Asumsinya, exec dan category tercantum di **setiap** view yang mengizinkan store. G51 melanggar asumsi itu: kontrak §28.14.3 (baris 5161, 5177 test negatif (9)) sengaja menolak category di view pembayaran.

**Skenario.** User Odoo memegang grup Insight `category` + `store` (outlet sendiri = W22). Token eit2 berisi `personas=category,store` dan `wids=0,<semua warehouse SMD>`. Query apa pun ke `efn_insight.v_sm_payment_daily` dengan sig2 sesi itu lolos `hasAny` (lewat `store`) dan `has(wids, …)` untuk semua outlet. Hasilnya: nominal dan jumlah transaksi per metode untuk semua outlet. Ini justru data yang menurut kontrak tidak boleh dibaca category (test (9)), dan kepala toko tidak boleh membaca outlet lain.

**Keterjangkauan saat ini.** Lewat UI **belum** terjangkau. `sm_fb_pay_*` (`queries-sm.ts:354-369`) hanya dipanggil `/payments` bila `smdActive(sel)`, dan `sel.smd` hanya true untuk sesi `exec` (`sm-personas.ts:97-107`, `filters.ts:119,126`). Halaman `/store` tidak memakai view ini. Jadi ini regresi lapis kedua (defense in depth), persis kelas temuan SEC-G47-01. Kartu atau halaman berikutnya yang memakai view ini untuk store langsung bocor.

**Rekomendasi (pilih satu, plus test).**
1. Paling sederhana dan konsisten dengan kontrak "category tidak membaca pembayaran": ubah `STORE_COVERING_PERSONAS` menjadi `frozenset({"exec"})` di `res_users.py:19`, sehingga `category + store` tidak menerbitkan `store` (perlakuan sama dengan marketing/dc). Perbarui test `efn_insight_sm/tests/test_scope.py` dan L-216 di docs/04.
2. Atau tambahkan `category` ke persona `v_sm_payment_daily`. Ini mengubah kontrak (test (9) harus dibalik) dan perlu keputusan lead/architect.
3. Jangka panjang: klaim `swids` terpisah + `SQL_efn_store_wids` ditandatangani (sudah dicatat L-216).

Wajib ditambahkan test invarian statis di `services/etl/tests/test_insight_sm_access.py`, dekat `test_policy_manifest_matches_contract` (baris 390-400): untuk setiap view di manifest yang memuat `store`, semua persona di `STORE_COVERING_PERSONAS` (dibaca dari `res_users.py`) harus ada di daftar persona view itu. Test ini mencegah regresi yang sama di gelombang berikutnya.

### SEC-G51-02 (Low): guard `load_forecast_asof.py` terlalu lemah

**Kode.** `load_forecast_asof.py:32-33` hanya memeriksa `os.environ["EFN_ASOF_CONFIRM"] == "demo"`. `company_code` dari file hanya divalidasi regex `[A-Z]{2,8}` lalu `res.company.search([("efn_brand_code","=",code)])` (baris 68-73). Penulisan memakai `sudo()` + `env.cr.commit()`.

**Skenario.** Operator salah target (`-d efn` atau container prod-lokal `efn-prod`) sambil menyalin perintah runbook lengkap dengan `EFN_ASOF_CONFIRM=demo`. Skrip lalu membuat `efn.forecast.run` `state=ok` di DB produksi. Bila PT itu belum punya run harian nyata, `v_sm_forecast_daily` (argMax `generated_at`) menampilkan run sintetis sebagai forecast "terbaru", dan `fact_forecast_accuracy` tercemar. File dengan `company_code` brand F&B juga diterima selama gudang/produknya cocok. Butuh akses `odoo shell` (sudah setara superuser), jadi severity Low. Ini pemisahan demo/prod, bukan eskalasi.

**Rekomendasi.** Samakan guard dengan `seed_demo_supermarket.py:557-572`:
- tolak bila `code != "SMD"`;
- wajib `env["ir.module.module"].search([("name","=","efn_setup_supermarket")]).demo` dan `env.ref("efn_setup_supermarket.warehouse_smd01", raise_if_not_found=False)` ada (DB dibuat `--with-demo`);
- opsional: `env.cr.dbname` ∈ allowlist (`efn_smd_demo`) atau ditolak bila `ir.config_parameter` lingkungan = production.

`seed_demo_supermarket.py` sudah memadai: ia mensyaratkan data demo `efn_setup_supermarket` (xmlid `company_smd`/`warehouse_smd01` hanya ada di `demo/`, `__manifest__.py:60-71`), memeriksa `efn_brand_code == SMD`, dan semua tahap G51 di-scope `company_id = SMD`.

### SEC-G51-03 (Low): keluaran as-of belum di-gitignore

Runbook (`demo-supermarket-seed.md:294-305`) menulis 8 file `var/forecast-asof/asof-SMD-*.json` (±5–15 MB) di root repo. `.gitignore` tidak memuat `var/`. Isinya tanpa PII (id, kode gudang, angka), tetapi berupa data bisnis yang membengkakkan repo, dan `git add -A` akan memasukkannya. Komentar "jangan commit" di runbook tidak bisa diandalkan sebagai kontrol.
**Rekomendasi:** tambahkan `var/` ke `.gitignore` (atau arahkan `--out` ke folder scratch di luar repo).

### SEC-G51-04 (Info): `--out` tanpa batas direktori

`batch_as_of` menulis ke `Path(args.out)` dengan `write_text` (menimpa). Ini input operator CLI, jadi bukan path traversal dari input tak tepercaya. Container runtime `read_only: true`, uid `65534`, `cap_drop: [ALL]` (`docker-compose.forecast.yml:55,102,106`), dan jalur as-of hanya hidup bila `--as-of` dan `--out` diberikan bersama (`__main__.py:151-161`).
Opsional: tolak file yang sudah ada (`open(path, "x")`) dan wajibkan sufiks `.json`.

### SEC-G51-05 (Info): selisih kontrak vs kode

- `odoo-readonly-grants.sql:612` memberi grant `product_template.expiration_time` tanpa syarat, sedangkan kontrak §28.14.3 (baris 5175, 5168) meminta blok `\if` dan pembacaan bersyarat `information_schema`. Ini dampak ketersediaan (grant gagal pada DB tanpa `product_expiry`), bukan keamanan. Perlu disepakati lalu diselaraskan salah satu sisi.
- docs/04 baris 5171 dan L-224 menyebut 32 policy, sedangkan kode dan test mengunci 33 (spesifikasi lead menambah `v_sm_lot_stock`). Perbarui kontrak.

## Kontrol yang sudah benar (terverifikasi di kode)

- **Validasi filter di server.** `parseFilters` (`lib/filters.ts:117-169`) menerima `SMD` hanya bila `opts.smd`. `sm:<id>` diterima hanya bila `smdAllowed && smdOn` dan id ⊆ `wids` sesi tanpa 0 (`page-ctx.ts:28-31`). `Filters.pt/branch` tetap hanya ESB, jadi query ESB tidak berubah. Sesi tanpa exec atau store-only: `pt=SMD` / `branch=sm:…` ditolak (`adjusted=true`) dan tidak ada query SM.
- **Satu penentu otorisasi.** `smdAllowed` (`sm-personas.ts:97-107`) = eis2 + `exec` + kode SM + flag. Dipakai pageCtx, layout/FilterBar, dan shell-data (`shell-data.ts:51`, daftar outlet hanya untuk sesi itu).
- **Tidak ada query yang tidak perlu.** Semua query ESB di halaman F&B lewat `runEsb/loadEsbTable(sel.esb, …)`. `/flow` dan `/data-quality` kembali lebih awal bila `!sel.esb`. Query SM lewat `smdActive(sel)`. Sesi exec tanpa PT F&B tidak pernah memanggil `chQuery` ESB. Ekspor CSV (`api/export/[name]/route.ts:44-46`) tetap ESB-only dan menolak sesi tanpa kode ESB.
- **Cakupan query SM.** Cakupan sig2 berasal dari sesi terverifikasi (`smd-data.ts:21-42`, `cids`/`wh`/`whs` dari sesi, `extra` hanya konstanta server). Semua SQL konstanta dan berparameter bertipe (`queries-sm.ts:55-64`), dengan filter `company_id` + `warehouse_id` lapis 1.
- **Halaman umur simpan.** `sm_shelf_lots` memakai `SCOPE` (`wh` = `effectiveWh`, ⊆ wids). Store-only hanya melihat outletnya sendiri. `v_sm_lot_stock` = `category,exec,store`, konsisten dengan L-216. `value_cost` per lot setara `v_sm_lot_expiry` yang sudah boleh dibaca store.
- **View CH baru.** DEFINER dengan kolom eksplisit, tanpa partner/member/kasir/kartu. `fact_pos_payment` memang tidak memuat kolom kartu (grant ETL `pos_payment` mengecualikan `card_no`, `cardholder_name`, `transaction_id`, `payment_ref_no`). `v_sm_product_sales_daily` tanpa `cogs` (L-193). Inner join `dim_sm_warehouse` mencegah data F&B/ESB masuk (L-196).
- **Grant definer = penutupan rekursif persis.** Ini diuji (`test_insight_sm_access.py:369-379`): tanpa ESB/legacy/GL/audit/loyalty dan tanpa `GRANT ALL/INSERT/...`. `insight_reader` tidak pernah membaca `efn_dwh`. Policy dibuat dengan `--log_queries=0` (kunci tidak masuk query_log), dan jumlah 33 dikunci fail-closed.
- **ETL.** `sm_stock_hist.py` memakai SQL berparameter (`company_filter` tervalidasi), tidak membaca `owner_id` (konsinyasi), default mati (`ETL_SM_STOCK_RECONSTRUCT_DAYS=0`, rentang 0..400). Baris rekonstruksi `computed_at=2000-01-01` tidak pernah menang atas snapshot nyata. Grant baru hanya `expiration_time`; kolom lain sudah ada di blok G13/G47.
- **forecast-worker as-of.** Tanpa `--as-of` alur produksi tidak berubah (`run_id` default UUIDv4, `now` default `datetime.now`). Jalur as-of tanpa RPC (`RecordingOdoo` menolak `calendar_upsert`/`minmax_ingest`), tanggal divalidasi (Senin, lampau, ≤400 hari), histori dibatasi `as_of`, file tanpa PII. Loader memvalidasi ulang setiap baris dengan validator Odoo, `is_active=False`, dan tidak memicu usulan replenishment.
- **Seed demo.** Mensyaratkan DB dengan data demo `efn_setup_supermarket` dan kode PT `SMD`, semua tahap di-scope company SMD, tanpa password atau secret, member/telepon fiktif (`+62 800-0000-xxxx`).
- **Compose/env.** `INSIGHT_SM_ENABLED` default `false`, `ETL_SM_STOCK_RECONSTRUCT_DAYS` default `0`, `ETL_FORECAST_ACCURACY_WEEKS` default `2`. Tidak ada secret baru di repo atau `.env.example`.

## Belum diverifikasi

- Harness row policy ClickHouse (`g47_row_policy_harness.sh`) untuk 4 view baru dan test negatif (9)–(11) tidak dijalankan reviewer.
- pytest ETL, vitest insight-web, dan test Odoo `efn_insight_sm` tidak dijalankan.
- Skenario SEC-G51-01 disimpulkan dari predikat dan kode penerbit token, belum diuji live di CH 24.8.
