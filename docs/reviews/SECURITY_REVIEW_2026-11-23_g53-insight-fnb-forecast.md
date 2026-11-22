# Security Review — G53 Insight Forecast F&B (`/forecast`, `v_esb_forecast_daily`)

Tanggal: 2026-11-23 · Reviewer: efn-security-reviewer (read-only, review cepat ±20 menit)
Cakupan: branch `feat/insight-fnb-forecast` (satu commit di atas `origin/main`): `infra/clickhouse/init/22-g53-insight-fnb-forecast.sql`,
`infra/clickhouse/access/insight-access.sql`, `insight-esb-policy.sh`, `clickhouse-init.sh`, `services/insight-web/{lib/queries.ts,
lib/fnb-forecast.ts, lib/config.ts, lib/odoo-links.ts, app/(app)/forecast/*}`, test `services/esb-extract/tests/test_insight_*access.py`,
`infra/compose/docker-compose.yml`, docs/04 §28.16.

## Ringkasan eksekutif

Tidak ada temuan Critical, High, atau Medium. View baru `efn_insight.v_esb_forecast_daily` mengikuti pola view PT yang sudah ada
(DEFINER + row policy `efn_insight_scope` kelas PT + verifikasi fail-closed di init). `company_code` selalu terisi kode ESB karena
INNER JOIN ke `dim_fc_warehouse` yang difilter `IN ('PB','WTZ','BCF','BCR','IMD','CUP')`, jadi baris SMD dan PT tanpa kode tidak
masuk. Query di insight-web memakai parameter bertipe, `LIMIT` literal, dan `dc_codes` dari env yang divalidasi regex. Ada dua catatan
Low (verifikasi perilaku di ClickHouse belum mencakup view baru; `company_code` di dim gudang bisa basi bila kode PT di Odoo diubah)
dan satu Info.

## Tabel temuan

| ID | Severity | Judul | Lokasi | Risiko |
|---|---|---|---|---|
| SEC-G53-01 | Low | Harness perilaku row policy (E1-E9) tidak menjalankan view baru | `services/esb-extract/tests/clickhouse/g52_row_policy_harness.sh` (tidak ada `v_esb_forecast_daily`); `infra/clickhouse/access/insight-esb-policy.sh:37` | R-S (kebocoran lintas PT), L-243 |
| SEC-G53-02 | Low (PLAUSIBLE) | `company_code` di `dim_fc_warehouse` tidak ikut berubah bila `res.company.efn_brand_code` diubah | `services/etl/efn_etl/forecast.py:67-77`; `infra/clickhouse/init/22-g53-insight-fnb-forecast.sql:45-49` | R-S (lintas PT), UU PDP (minimisasi akses) |
| SEC-G53-03 | Info | `isDcCode` melakukan `trim()`, sedangkan SQL `has()` membandingkan apa adanya | `services/insight-web/lib/fnb-forecast.ts:25-27` vs `lib/queries.ts:949,973` | fungsional, bukan keamanan |

## Detail

### SEC-G53-01 — Low — Uji perilaku ClickHouse belum mencakup `v_esb_forecast_daily`

- **Fakta**: `insight-esb-policy.sh:37` menambahkan `v_esb_forecast_daily PT`. Predikat dibangkitkan oleh fungsi yang sama
  (`esb_policy_predicate`, `:45-64`), jadi teksnya identik dengan 9 view PT lain. Test statis (`test_insight_fnb_access.py:26-37`,
  `test_insight_access.py:17,42,80`) dan hitungan `ESB_POLICY_EXPECTED=15` (`clickhouse-init.sh:446`) diperbarui. Namun harness
  perilaku `g52_row_policy_harness.sh` hanya membuat fixture untuk view sampel (sales_branch, payment, store_*, branch_list) dan
  tidak pernah menjalankan SELECT ke view forecast dengan sesi `brs` terisi, codes tanpa PT, atau sig salah.
- **Skenario**: risikonya bukan predikat yang salah. Risikonya adalah perilaku DEFINER-plus-policy pada view dengan subquery
  `IN (SELECT argMax ...)` dan JOIN tiga tabel `FINAL` yang belum pernah diuji di ClickHouse 24.8. Policy pada view luar seharusnya
  tetap berlaku, tetapi itu belum dibuktikan untuk view ini.
- **Rekomendasi**: tambahkan fixture minimal `fact_forecast_daily`/`dim_forecast_run`/`dim_fc_warehouse` (PB + SMD + satu baris
  `company_code=''`) ke harness, lalu uji: (a) sesi PB → hanya PB; (b) `brs` terisi → 0 baris; (c) codes `SMD` → 0 baris;
  (d) sig salah → 0 baris/galat. Jalankan di container sebelum merge, atau catat sebagai belum terverifikasi.

### SEC-G53-02 — Low (PLAUSIBLE) — `company_code` gudang bisa basi setelah kode PT diubah

- **Fakta**: ETL `SQL_FC_WAREHOUSES` (`forecast.py:67-77`) mengambil `c.efn_brand_code` saat baris gudang ditarik, tetapi cursor
  inkremental hanya `w.write_date`. Kalau `res.company.efn_brand_code` diubah, `stock_warehouse.write_date` tidak ikut berubah,
  sehingga `dim_fc_warehouse.company_code` tetap memakai kode lama. View G53 (`22-g53...sql:47-49`) dan row policy memakai kolom ini
  sebagai satu-satunya penentu PT.
- **Skenario**: PT demo atau PT uji diberi kode `PB`. Kodenya kemudian dikosongkan atau dipindah ke PT produksi (constraint
  `UNIQUE(efn_brand_code)` di `efn_core/models/res_company.py:79` memaksa pemindahan seperti ini). Gudang PT lama tetap tercatat
  `PB` di ClickHouse. Kalau worker forecast pernah menghasilkan run `ok` untuk PT lama (`company_id` lama), forecast-nya akan tampil
  ke user ber-scope PB. Dampaknya terbatas: hanya nama gudang/produk dan qty agregat, tanpa data pribadi. Syaratnya ada perubahan
  konfigurasi oleh admin yang jarang terjadi, jadi temuan ini PLAUSIBLE, bukan terbukti.
- **Catatan**: perilaku ini bawaan ETL G44/G46, bukan kode baru. G53 menambah permukaan paparannya ke persona F&B.
- **Rekomendasi**: (pilih satu) tambahkan `GREATEST(w.write_date, c.write_date)` sebagai cursor, atau lakukan full refresh kecil
  `dim_fc_warehouse` (tabelnya kecil) di setiap siklus. Opsi lain: di view, join ke `company_id` yang dipetakan dari sumber kode PT
  yang segar. Tambahkan juga ke runbook bahwa perubahan `efn_brand_code` mengharuskan re-sync dim gudang.

### SEC-G53-03 — Info — Inkonsistensi `trim()` pada pencocokan kode DC

`isDcCode` (`fnb-forecast.ts:26`) memakai `code.trim()`, sedangkan SQL `NOT has({dc_codes:Array(String)}, warehouse_code)`
membandingkan persis. Akibatnya, kode gudang Odoo yang berspasi di ujung akan ikut dihitung di total SQL tetapi diberi label DC di
tabel. Ini hanya soal konsistensi angka, tidak ada dampak keamanan. Samakan kedua sisi dengan perbandingan persis (worker juga
case/space-sensitive, lihat `forecast.py:83-84`).

## Kontrol yang sudah benar

- **Isolasi PT di DB (lapis 2)**: view DEFINER `efn_insight_definer` (`insight-access.sql:173-176`) + row policy kelas PT. Predikatnya
  `company_code != ''`, validasi format codes/cids/brs, `has(codes, company_code)`, sig3 HMAC-like SHA256 dengan kunci secret, dan
  `brs = ''` (`insight-esb-policy.sh:51-60`). Sesi bercabang mendapat 0 baris.
- **`company_code` selalu terisi kode ESB**: INNER JOIN `dim_fc_warehouse` dengan filter `IN ('PB','WTZ','BCF','BCR','IMD','CUP')`
  (`22-g53...sql:45-49`). Baris SMD (`company_code='SMD'`) dan PT tanpa kode tidak pernah masuk view. `efn_brand_code` bertipe
  Selection dengan `UNIQUE` (`efn_core/models/res_company.py:45,79`), jadi satu kode memetakan satu PT.
- **Grant definer tidak bocor ke pembaca**: `fact_forecast_daily`, `dim_forecast_run`, `dim_fc_warehouse`, dan `dim_product`
  (memuat semua PT termasuk SMD) hanya di-grant ke `efn_insight_definer` (`insight-access.sql:78-82`). `insight_reader` hanya punya
  `efn_insight.*` (`:163`).
- **Fail-closed init**: setiap tabel di `efn_insight` tanpa policy `efn_insight_scope` membuat init mencabut grant dan gagal
  (`clickhouse-init.sh:454-464`). Jumlah policy dikunci di 15 (`:446`, `insight-esb-policy.sh:80-82`).
- **Query aman (lapis 1)**: ketiga query (`queries.ts:942-977`) hanya membaca `efn_insight.v_esb_forecast_daily`, menyertakan filter
  `has({pt:Array(String)}, company_code)`, memakai placeholder bertipe `{today:Date}`, `{until:Date}`, `{dc_codes:Array(String)}`,
  dengan `LIMIT` literal 60/500/20, dan tanpa interpolasi input user. `dc_codes` berasal dari env, difilter
  `^[A-Za-z0-9_-]{1,16}$` (`fnb-forecast.ts:10-23`), lalu diserialisasi dengan escape (`ch-params.ts:47-51`). Test mencakup
  penolakan injeksi tanggal (`tests/fnb-forecast.test.ts:53`).
- **Gating halaman**: `/forecast` ada di `BRANCH_BLOCKED_PATHS` (`fnb-personas.ts:49`) dan ditegakkan di `proxy.ts:80`. Sesi
  store-only ditolak sebelum render, dan di DB sesi ber-`brs` tetap 0 baris. Query ESB hanya jalan bila `mode !== 'only-smd' &&
  sel.esb` (`page.tsx:67`). Sesi khusus supermarket tidak memicu query, dan seandainya terpicu, codes-nya tidak memuat kode ESB.
- **Tautan Odoo**: hanya action whitelist `efn.forecast.daily` (`odoo-links.ts:26`; action ada di
  `efn_demand_forecast/views/efn_forecast_v2_views.xml:140`), origin divalidasi, record rule Odoo tetap berlaku.
- **PDP**: view hanya memuat gudang, produk, dan angka agregat. Tidak ada kolom identitas atau nomor HP. Nama gudang dan produk
  dirender oleh React (ter-escape), tooltip ECharts memakai `escapeHtml`.
- **Secret/data klien di repo**: compose hanya `INSIGHT_FNB_DC_CODES: ${INSIGHT_FNB_DC_CODES:-}` (default kosong, non-rahasia).
  Tidak ada kode gudang klien baru di kode/test (test memakai `DC01`/`X1`). Kode PT ESB (PB, WTZ, dst.) sudah publik di docs
  sebelumnya.

## Belum terverifikasi

Semua temuan di atas berasal dari analisis statis. Saya tidak menjalankan ClickHouse, Jest, atau pytest dalam review ini.
