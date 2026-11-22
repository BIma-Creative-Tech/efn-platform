# forecast-worker — forecast permintaan supermarket (G44)

**ADR-046 (Proposed)** · kontrak `docs/04` §28.2 (byte-exact) · §28.1/§28.10 · L-145..L-148 · K25-02 (stack data
terpisah) · ADR-003 (ClickHouse turunan) · ADR-010 (AI/forecast di luar jalur transaksi). Risiko dimitigasi: **R-T21**
(akurasi forecast: censoring OOS, kalender override manual, backtest + baseline, approval manusia di Odoo), R-T11 (CPU
di mesin data, tulis massal ≤ 500 baris/panggilan), R-T06 (p10/p90 + WAPE dikirim bersama angka).

## Tujuan
Batch harian (default 23:30 WIB) per PT: baca ClickHouse `efn_forecast.*` (read-only) → model per SKU × gudang →
kirim ke Odoo lewat `odoo-rpc-gateway` `/json/2` (mTLS CN `forecast-worker`, API key user teknis per PT):
1. `efn_forecast_calendar_upsert` — seed libur nasional + cuti bersama (library `holidays`, kode `ID`, tahun berjalan
   + 1, source `lib`; entri `manual` di Odoo tidak pernah ditimpa — ditegakkan Odoo).
2. `efn_forecast_run_open` → `efn_forecast_ingest` (proyeksi 28 hari `yhat/p10/p90/sigma`, chunk ≤ 500 berurutan) →
   `efn_forecast_run_close` (metrik `series/duration_s/wape_backtest/failed_series`).
3. Mingguan (FORECAST_MINMAX_WEEKDAY, default Minggu, setelah batch harian): run `weekly_minmax` →
   `efn_minmax_ingest`.

Worker **tidak pernah** menulis ClickHouse atau DB Odoo (L-145). Hasil turun ke mart lewat ETL (`ETL_ENABLE_FORECAST`,
`fact_forecast_daily`, `fact_forecast_accuracy`).

**Histori ESB (G46, docs/04 §28.2a, L-165):** untuk PT eks-ESB, `v_sales_daily` juga memuat penjualan menu ESB
sebelum cutover (Odoo `efn.legacy.sales.daily` → ETL `ETL_ENABLE_FORECAST_LEGACY` → `fact_legacy_sales_daily`).
Worker tidak membedakan sumber; hari × gudang yang sudah punya POS Odoo memakai POS saja. Hari ESB tidak kena
censoring OOS (tidak ada snapshot stok Odoo sebelum cutover).

## Model (efn_forecast/pipeline.py, factors.py, models.py)
- **Penyesuaian kalender multiplikatif** (diestimasi pada agregat gudang × kategori → fallback gudang → PT, disusutkan
  ke 1 sebesar n/(n+5)): akhir pekan (configurable), hari dalam pekan, **minggu gajian** (7 hari terakhir + 7 hari
  pertama bulan, configurable), libur nasional, cuti bersama, `custom`, Ramadan (30 hari sebelum 1 Syawal atau entri
  manual), 5 bucket Lebaran (H−14..H−8, H−7..H−4, H−3..H−1, H..H+2, H+3..H+7). 1 Syawal dari libur "Idul Fitri"
  library/Odoo; override: entri manual `lebaran_window`, env `FORECAST_LEBARAN_DATES`.
- **Censoring OOS**: stok akhir hari ≤ 0 (snapshot ada, SKU ber-stok dalam 14 hari sebelumnya) → hari itu bukan
  permintaan nol (diimputasi rata-rata 28 hari).
- **Per seri** (setelah dibagi faktor): `regular` → AutoETS (default ruang model `ZNZ` = tanpa tren,
  `FORECAST_ETS_MODEL`) / AutoARIMA (orde kecil + approximation, 182 hari terakhir) dipilih rolling-origin CV
  4 × 7 hari (MAE) bila kandidat > 1; hari nol > 60 % → CrostonOptimized (CV² < 0,49) / ADIDA (lumpy); histori < 56
  hari → `category_mean` = rata-rata penjualan mentah 14 hari valid terakhir seri sendiri (tanpa faktor kalender);
  SKU baru tanpa jual → median level kategori di gudang × faktor. Model gagal → level seri sendiri + `failed_series`.
- **Backtest** (holdout `FORECAST_BACKTEST_DAYS`): SKU yang belum terjual sampai titik potong tidak diramal (seperti
  run nyata) dan dinilai sebagai ramalan 0; aktualnya tetap masuk penyebut WAPE (G46c).
- p10/p90 = yhat ∓ 1,2816·sigma (dipotong di 0); sigma dari interval 80 % statsforecast atau residual CV / std 56 hari.
- **DC** (`FORECAST_DC_CODES`, default `SMDDC`) = permintaan langsung DC + Σ ramalan outlet per produk (hanya bila tepat
  satu DC di PT).
- **Backtest** holdout 28 hari: WAPE model vs baseline v1 (rata-rata 56 hari) vs seasonal naive (log
  `EFN_FORECAST_RUN`); `wape_backtest` dikirim di `run_close`.
- **Min/max**: d, σ = rata-rata 7 hari ramalan; `min = d·L + z(SL)·σ·√L`, `max = min + d·R`; L = 2 hari outlet / 7
  hari DC, R = 7. Service level 0,95 diturunkan linear sampai 0,85 bila waste kategori (scrap/(scrap+terjual),
  56 hari) > 5 % (penuh pada 10 %).

## Kegagalan
Validasi skema lokal (`contract.py`) atas SEMUA payload sebelum `run_open`. Error apa pun → `run_close status=failed`
(bila belum dibuka: `run_open chunk_total=1` lalu close), log kode error saja, tunggu jadwal berikutnya — tidak ada
crash berulang. Retry/backoff (1,2,4,8,16,30 s + jitter) hanya untuk jaringan/timeout/429/5xx (gateway maxconn 1 → 503);
semua metode idempoten (`run_id`, `(run_id, chunk_no)`). `FORECAST_ENABLED=false` → proses idle tanpa secret.

## CLI
```
python -m efn_forecast run --company SMD                       # loop terjadwal (compose)
python -m efn_forecast run --company SMD --dry-run             # satu batch, tanpa Odoo (hanya baca ClickHouse)
python -m efn_forecast run --company SMD --once [--kind weekly_minmax]   # butuh FORECAST_ENABLED=true
python -m efn_forecast seed-calendar --company SMD [--dry-run]
python -m efn_forecast run --company SMD --as-of 2026-09-28 --out /out/asof.json   # G51 A6: hitung saja, tanpa RPC
python -m efn_forecast run --company SMD,PB --once             # G46d: beberapa PT bergiliran (compose: FORECAST_COMPANIES)
```
`--as-of` (G51 A6, hanya demo): batch daily seolah Minggu sebelumnya 23:00 WIB, payload tervalidasi ditulis ke file
lalu dimuat oleh `scripts/dev/load_forecast_asof.py` (odoo shell, run `ok` nonaktif, tanpa usulan replenishment).
Runbook: `docs/runbooks/demo-supermarket-seed.md` §8. Tanpa `--as-of` perilaku tidak berubah.
Beberapa PT (G46d, ADR-046 amandemen 22 Nov 2026): `--company SMD,PB` menjalankan batch per PT berurutan; key per PT
(`ODOO_API_KEY_FORECAST_<PT>_FILE`); PT yang gagal (mis. key placeholder) di-log dan PT berikutnya tetap jalan; exit 1
bila ada yang gagal. `--as-of` hanya satu PT.

## Konfigurasi (env; secret hanya lewat `*_FILE`)
| Env | Default |
|---|---|
| `FORECAST_ENABLED` | `false` (idle) |
| `FORECAST_DAILY_CRON`, `FORECAST_TZ`, `FORECAST_MINMAX_WEEKDAY` | `30 23 * * *`, `Asia/Jakarta`, `6` (Minggu; `-1` = mati) |
| `FORECAST_HORIZON_DAYS`, `FORECAST_CHUNK_SIZE` | `28` (7..60), `500` (1..500) |
| `FORECAST_HISTORY_DAYS`, `FORECAST_STOCK_DAYS`, `FORECAST_FIT_DAYS` | `800`, `400`, `365` |
| `FORECAST_MODELS`, `FORECAST_CV_WINDOWS`, `FORECAST_MIN_HISTORY_DAYS`, `FORECAST_INTERMITTENT_ZERO_RATIO` | `AutoETS` (pilihan `AutoETS,AutoARIMA`), `4`, `56`, `0.6` |
| `FORECAST_ETS_MODEL` | `ZNZ` (galat `A/M/Z`, tren `N/A/Z`, musim `N/A/M/Z`); `ZZZ` = ruang model lama. Benchmark data nyata PB 1 tahun (G46c): ZNZ 8x lebih cepat dari ZZZ, WAPE setara/lebih baik |
| `FORECAST_JOBS` | `1` (1..32): proses paralel per gudang (G46b, `spawn`; hasil identik dengan 1). Log `forecast gudang i/N` per gudang. Default `AutoETS` tanpa CV; `AutoETS,AutoARIMA` + CV 4 = 4-5 fit per kandidat per seri (lihat angka G46c di bawah) |
| `FORECAST_INACTIVE_DAYS` | `28` (0..365; 0 = mati): seri tanpa jual dan tanpa hari OOS selama N hari terakhir → ramalan 0 (`inactive`), **tidak dikirim** ke Odoo (tanpa usulan forecast/min-max), tetap dihitung di backtest (L-168). Level `category_mean` = median seri pembanding |
| `FORECAST_PAYDAY_TAIL_DAYS`, `FORECAST_PAYDAY_HEAD_DAYS`, `FORECAST_WEEKEND_DAYS` | `7`, `7`, `5,6` (0=Senin) — **samakan dengan parameter Odoo per company** |
| `FORECAST_LEBARAN_DATES`, `FORECAST_FACTOR_SHRINK_DAYS` | kosong, `5` |
| `FORECAST_SERVICE_LEVEL`, `FORECAST_SERVICE_LEVEL_MIN`, `FORECAST_WASTE_HIGH_RATIO`, `FORECAST_WASTE_LOOKBACK_DAYS` | `0.95`, `0.85` (keduanya 0.5..0.999 = rentang Odoo [0,5; 1)), `0.05`, `56` |
| `FORECAST_MINMAX_LEAD_DAYS`, `FORECAST_MINMAX_DC_LEAD_DAYS`, `FORECAST_MINMAX_REVIEW_DAYS` | `2`, `7`, `7` |
| `FORECAST_DC_CODES`, `FORECAST_WAREHOUSE_CODES` | `SMDDC`, kosong (semua gudang aktif PT); kode dipakai persis (case-sensitive) |
| `FORECAST_MAX_QTY_PER_LINE` | `100000` = parameter Odoo `efn_forecast.max_qty_per_line`; baris di atasnya dibuang di worker (`rejected_local`; kode Odoo `value_too_large`) |
| `FORECAST_BACKTEST_ENABLED`, `FORECAST_BACKTEST_DAYS`, `FORECAST_SEED_CALENDAR`, `FORECAST_INCLUDE_TODAY` | `true`, `28`, `true`, `true` |
| `ODOO_RPC_URL`, `ODOO_RPC_CA_FILE`, `ODOO_RPC_CLIENT_CERT_FILE`, `ODOO_RPC_CLIENT_KEY_FILE` | `https://odoo-rpc-gateway:8443`, `/run/secrets/efn_ca_crt`, `/run/secrets/tls_forecast_worker_crt`, `/run/secrets/tls_forecast_worker_key` |
| `ODOO_RPC_TIMEOUT_SECONDS`, `ODOO_RPC_MAX_RETRIES`, `ODOO_DB` | `25`, `6`, kosong |
| `ODOO_API_KEY_FORECAST_<KODE>_FILE` | `/run/secrets/odoo_api_key_forecast_<kode>` (placeholder `disabled` ditolak) |
| `CH_HOST`, `CH_PORT`, `CH_USER`, `CH_PASSWORD_FILE`, `CH_DATABASE`, `CH_SECURE`, `CH_CA_FILE`, `CH_QUERY_TIMEOUT_SECONDS` | `clickhouse`, `8443`, `efn_forecast_reader`, secret `clickhouse_password_forecast`, `efn_forecast`, `true`, `/run/secrets/efn_ca_crt`, `300` |
| `NUMBA_CACHE_DIR` | `/tmp/numba` (rootfs read-only) |

## Lisensi dependency (33 paket `requirements.lock`, metadata paket terpasang, 19 Nov 2026)
Diperiksa dari metadata `License-Expression`/`License`/classifier setelah `pip install --require-hashes` (bukan dari
repo primer). Semua permisif; tidak ada copyleft kuat; tidak ada kode vendor yang disalin.
- **Apache-2.0**: adagio, clickhouse-connect, coreforecast, fugue, pyarrow, requests, statsforecast, triad, tzdata,
  utilsforecast; packaging (Apache-2.0 OR BSD-2-Clause).
- **BSD (2/3-Clause)**: cloudpickle, fsspec, idna, llvmlite, lz4, numba, numpy, pandas, patsy, python-dateutil, scipy,
  statsmodels, threadpoolctl, zstandard.
- **MIT**: charset-normalizer, holidays, narwhals, pytz, six, urllib3.
- **MPL-2.0** (copyleft lemah per file, tidak dimodifikasi): certifi; tqdm (MPL-2.0 AND MIT).

## Test
```
pip install -r requirements-dev.txt
pytest                 # termasuk backtest statsforecast (marker slow; JIT numba pertama kali puluhan detik)
pytest -m "not slow"   # tanpa statsforecast
```

## Backtest data nyata (G46c, 21 Nov 2026)
Histori sales 1 tahun satu brand F&B (±7.200 seri gudang × produk), holdout 28 hari, `pipeline.backtest` (metrik yang
dikirim ke Odoo). Angka hanya agregat; data klien tidak ada di repo.

| Holdout | Konfigurasi | Waktu forecast (1 proses kecuali disebut) | WAPE | mean56 | naive |
|---|---|---|---|---|---|
| Okt–Nov | lama (ETS `ZZZ`, seri pendek campuran kategori) | 413 s | 0,581 | 0,665 | 0,586 |
| Okt–Nov | baru (ETS `ZNZ`, level 14 hari seri sendiri) | 61 s (22 s dengan 4 jobs) | 0,551 | 0,665 | 0,586 |
| Okt–Nov | baru + `FORECAST_MODELS=AutoETS,AutoARIMA` (CV 4) | 2.000 s dengan 4 jobs | 0,545 | 0,665 | 0,586 |
| Mar–Apr (memuat Lebaran) | lama | 167 s | 0,774 | 0,644 | 0,781 |
| Mar–Apr (memuat Lebaran) | baru | 36 s | 0,694 | 0,644 | 0,781 |

AutoARIMA dengan setelan lama tidak selesai dalam 5,5 jam (1 proses). Holdout Lebaran masih kalah dari mean56 karena
histori belum memuat Lebaran sebelumnya: faktor Lebaran tidak bisa diestimasi (butuh histori ≥ 1 Lebaran, mis. 24 bulan).

## Belum
- **Tuning**: bobot penyusutan faktor, ambang intermiten, kandidat model, AutoARIMA dengan regressor eksogen (ADR §3.3
  menyebut exog; versi ini memakai penyesuaian multiplikatif sebelum model), promo (`promo_active`) belum dipakai.
- **Produksi**: akurasi belum terbukti (histori supermarket nyata belum ada, Q-SM27); durasi batch & RAM untuk > 2.400
  seri belum diukur (ADR-046 §7: tinjau bila > 2 jam / > 4 GB); stok ditarik harian per SKU (volume besar untuk
  50.000 seri — perlu agregasi sisi ClickHouse).
- Parameter gajian dibaca dari env, bukan dari `ir.config_parameter` Odoo (belum ada jalur baca) — harus disamakan
  manual. Relasi resupply outlet → DC tidak ada di mart (DC = Σ semua outlet PT). Waste memakai `fact_scrap` G32
  (kosong bila `ETL_ENABLE_WASTE` mati → service level tidak diturunkan). Penjualan online belum masuk
  `v_fc_sales_daily`.
- Diselaraskan dengan `efn_demand_forecast` (18 Nov 2026): body = payload sebagai argumen bernama; galat 422
  `efn_forecast:<kode>` / 403 company_mismatch|access_denied tidak diulang; run gagal sebelum data siap dibuka dengan
  `chunk_total=1` lalu ditutup failed (Odoo menolak 0); `wape_backtest` dihilangkan bila tidak ada (Odoo menolak null).
  Belum diuji end-to-end lewat odoo-rpc-gateway.
