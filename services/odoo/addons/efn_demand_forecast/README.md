# efn_demand_forecast — Forecast min/max musiman (G28) + API forecast v2 (G44)

**Tujuan.** K25-12 / Q-35: produksi CK (dan pasok outlet) bisa berdasarkan forecast. Odoo CE hanya punya reorder rule
statis; modul ini membuat **usulan** min/max yang wajib disetujui planner. G44 (ADR-046 *Proposed*, `docs/04`
§28.2–§28.3, L-145..L-148): menerima hasil forecast-worker (statsforecast di `efn-data`) lewat `/json/2`.

## Rumus v1 (tanpa ML)
- Permintaan dasar harian = keluar stok gudang (ke pelanggan, produksi, gudang lain) selama N hari, tiap hari dibagi
  faktor musim hari itu.
- Faktor musim horizon = rata-rata faktor hari-hari ke depan (lead time + periode tinjau).
- `min = d × faktor × lead_days + z × σ × faktor × √lead_days`; `max = min + d × faktor × review_days`.
- z dari tingkat layanan (90/95/98/99 %).

## Musim (G44)
- Jenis baru `payday`: **7 hari terakhir + 7 hari pertama bulan** (mengikuti panjang bulan), dari parameter
  `efn_forecast.payday_tail_days` / `efn_forecast.payday_head_days` (default 7/7; 0..15; kunci bersufiks kode PT,
  mis. `efn_forecast.payday_tail_days.SMD`, menang atas global). Record bawaan `season_payday` kini jenis ini
  (DB lama dikonversi migrasi 19.0.2.0.0 hanya bila masih "tgl 25 s.d. 5").
- Jenis baru `holiday`: jendela H-x..H+y di sekitar entri `efn.forecast.calendar` jenis tertentu (company yang sama).
- Musim `monthly`, `weekday`, `period` lama tetap berlaku.

## API forecast-worker (docs/04 §28.2)
User teknis `efn_forecast_<kode>` = Internal User + `efn_demand_forecast.group_forecast_bot` saja, **tepat satu
company**; API key `/run/secrets/odoo_api_key_forecast_<kode>`; gateway mTLS CN `forecast-worker`.

| Path `/json/2/...` | Idempotensi |
|---|---|
| `efn.forecast.calendar/efn_forecast_calendar_upsert` | `(company, date, kind, name)`; entri `manual` tidak ditimpa; `lib` nonaktif tetap nonaktif |
| `efn.forecast.run/efn_forecast_run_open` | `run_id` (UUIDv4) — buka ulang → status (`created: false`) |
| `efn.forecast.daily/efn_forecast_ingest` | `(run_id, chunk_no)` → `duplicate_chunk: true` |
| `efn.demand.forecast.line/efn_minmax_ingest` | `(run_id, chunk_no)` |
| `efn.forecast.run/efn_forecast_run_close` | tutup dua kali → `duplicate_close: true` |

- Argumen = kunci objek JSON kontrak (body json2 = kwargs), `additionalProperties: false`, `schema_version: 1`,
  ≤ 500 entri/baris, payload ≤ 512 KB, angka finite ≥ 0, `p10 ≤ yhat ≤ p90`, tanggal dalam [hari ini, hari ini +
  horizon] (zona user teknis, default Asia/Jakarta), gudang (kode) & produk milik company (produk bersama boleh).
- Baris tidak valid ditolak per baris: `rejected[{index, code}]` dengan kode `invalid_line`, `unknown_warehouse`,
  `unknown_product`, `invalid_date`, `invalid_number`, `invalid_quantile`, `invalid_model`, `duplicate_line`,
  `invalid_minmax`, `invalid_service_level`, `value_too_large` (SEC-211: nilai > `efn_forecast.max_qty_per_line`,
  default 100000) (kalender: `invalid_entry`, `invalid_date`, `invalid_kind`,
  `invalid_name`, `invalid_source`, `duplicate_entry`).
- Galat seluruh panggilan: `UserError("efn_forecast:<kode>")` → **HTTP 422** json2 Odoo 19 (L-15), kode ∈
  `invalid_input`, `unsupported_schema`, `too_many_items`, `payload_too_large`, `unknown_run`, `run_conflict`,
  `run_closed`, `kind_mismatch`, `invalid_chunk`, `incomplete_run`. Company tidak cocok →
  `AccessError("efn_forecast:company_mismatch")` → **403**. Pemanggil bukan user teknis sah → 403
  `efn_forecast:access_denied`.
- `run_close(ok)` hanya bila semua chunk `0..chunk_total-1` sudah diterima (selain itu `incomplete_run`, run tetap
  terbuka). Run `ok` menjadi aktif per (company, kind); `failed` tidak mengubah run aktif. Run terbuka > 6 jam →
  `failed` (cron per jam).
- Min/max v2: gudang `efn_forecast_engine = v2` → usulan `draft` (approval planner seperti v1); `v1` (default) →
  `shadow` (pembanding, tidak bisa diterapkan). Cron v1 melewati gudang `v2`.
- SEC-212: `state`, `source`, `forecast_run_id`, `approved_by_id`, `approved_at`, `v2_service_level` di
  `efn.demand.forecast` hanya berubah lewat aksi/RPC internal (write/create user → `AccessError
  efn_demand_forecast:readonly`); usulan `shadow` tidak bisa dihidupkan. `warehouse_code` dicocokkan persis.
- Proyeksi run non-aktif dihapus autovacuum setelah `efn_forecast.daily_retention_days` (default 35, min 7).

## Pemakaian
Inventory > **Forecast EFN**: *Kalender Musim*, *Kalender Forecast* (manual = planner/Inventory Administrator,
teraudit `efn_audit`), *Hitung Forecast* (wizard v1, model kini `efn.forecast.run.wizard`), *Usulan Min/Max*,
*Proyeksi Harian (v2)*, *Run Forecast (v2)*. Gudang: *Ikut forecast mingguan*, *Engine forecast min/max*.

## Penyesuaian / penyimpangan kontrak
- Model `efn.forecast.run` dulu wizard v1 (TransientModel); kini model run v2 sesuai kontrak (migrasi pre 19.0.2.0.0
  mengosongkan tabel wizard). Wizard = `efn.forecast.run.wizard` (xmlid view/action tetap).
- `efn.demand.forecast.line` = baris mentah min/max v2 per run (jejak), masing-masing membuat satu
  `efn.demand.forecast` (`source=v2`).
- Kontrak menulis "400" untuk galat validasi; Odoo json2 memetakan `UserError` ke 422.

## Belum
Akurasi v2 belum bisa dibuktikan sebelum ada histori nyata (R-T21, Q-SM27). Akhir pekan untuk v2 diambil worker dari
kalender/`dim_calendar` (tidak ada parameter weekend di Odoo; v1 memakai musim `weekday`).
Catatan merge G43: branch G43 menambah hook `_efn_legacy_outflow` (19.0.1.1.0); signature publik v1 tidak diubah di
sini — rekonsiliasi versi/migrasi saat rebase.
