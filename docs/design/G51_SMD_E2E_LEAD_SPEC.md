# G51 — SMD end-to-end: data demo lengkap + SMD di halaman F&B Insight (spesifikasi lead)

Status: **kontrak kerja lead 21 Nov 2026** (keputusan pemilik proyek 21 Nov: "lengkapi data end to end"; default
pertanyaan 1–4 diterima). Diformalkan efn-architect ke `docs/04_SERVICE_CONTRACTS.md` §28.14 + addendum ADR-041.
Semua agent G51 WAJIB memakai nama view/kolom/route di bawah persis; perubahan lewat lead.

Prinsip yang tetap berlaku: PostgreSQL sumber kebenaran, ClickHouse hanya turunan ETL (ADR-003) — seed **tidak**
menulis ke ClickHouse; supermarket independen dari modul/data brand F&B; data pribadi tidak masuk view Insight;
flag default OFF (prod), dinyalakan hanya di prod-lokal/demo.

## Bagian A — data demo SMD (seed, `scripts/dev/seed_demo_supermarket.py`, idempoten, hanya PT SMD)

| ID | Data | Hasil yang diharapkan di mart |
|---|---|---|
| A1 | Transfer DC (SMDDC) → outlet 90 hari terakhir dari usulan replenishment yang disetujui; ±5–8% baris kurang kirim, sebagian backorder | `fact_dc_fill_rate` terisi (halaman DC + Distribusi SMD) |
| A2 | PO supplier → DC 90 hari, diterima; ±10% terlambat; baris terhubung orderpoint bila ada | `fact_dc_purchase_line`, `dim_sm_supplier` |
| A3 | Scrap di outlet (alasan: kedaluwarsa, rusak, kualitas; tag `stock.scrap.reason.tag` CE) ±1–3/outlet/hari 90 hari, bernilai HPP | `fact_scrap` → waste % Direksi/Toko |
| A4 | Pembayaran POS untuk order sintetis (tunai, QRIS, debit, kredit) bila belum ada `pos.payment` | `fact_pos_payment` SMD (halaman Pembayaran) |
| A5 | Umur simpan produk realistis per kategori (`product.template.expiration_time` hari, mis. sayur 3–5, susu 7–14, kering 180–365) | kolom `shelf_life_days` |
| A6 | Histori forecast 8 minggu: run forecast **as-of** tiap Senin 8 minggu lalu (worker sungguhan bila mendukung as-of; selain itu jalur yang setara dan terdokumentasi) | `fact_forecast_accuracy` terisi (WAPE) |

| A7 | Keluar stok POS 90 hari terakhir: satu picking selesai per outlet per hari (lokasi stok outlet → Customers), qty = penjualan POS sintetis hari itu, lot FEFO | `stock_move` historis untuk rekonstruksi B6 |

Histori stok (GMROI, stok > 30 hari) **tidak** diseed ke ClickHouse. Lihat B6.

Keputusan lead 21 Nov (review architect): A6 = worker `--as-of YYYY-MM-DD --out <file>` (hitung saja, tanpa RPC) +
loader terpisah `scripts/dev/load_forecast_asof.py` (ORM, `is_active=False`, `generated_at` = Minggu 23:00 WIB, tanpa
hook usulan replenishment); demo `ETL_FORECAST_ACCURACY_WEEKS=8`.

## Bagian B — Insight

### B1. SMD sebagai filter sungguhan di halaman F&B
- Chip **SMD** di "Brand / PT" menjadi **toggle filter** seperti brand lain (menggantikan chip tautan PR #133), hanya
  untuk sesi dengan persona SM **`exec`** (halaman F&B = tingkat PT). Sesi SM tanpa `exec` tetap lewat sidebar Supermarket.
- Halaman yang mendukung SMD: **Ringkasan (`/`)**, **Penjualan (`/sales`)**, **Pembayaran (`/payments`)**,
  **Distribusi (`/distribution`)**, **Forecast (`/forecast`)**. Halaman/kartu khusus F&B (Produksi vs Penjualan `/flow`,
  menu/BOM/FIN, Kualitas Data ESB) menampilkan "Tidak berlaku untuk SMD" bila hanya SMD dipilih; bila campuran,
  kartu itu hanya memuat brand ESB dengan catatan.
- Loader: query ESB (scope v1) **dan** query SM (`chQuerySm`, scope sig2 sesi) lalu digabung per kode PT. Filter
  cabang: outlet SMD tampil di daftar cabang dengan kode PT `SMD` (nilai `sm:<warehouse_id>` agar tidak bentrok id ESB).
- Angka omzet SMD di halaman F&B = **termasuk PPN** (sama dengan dashboard SM L-200); label kartu menyebutnya.

### B2. View ClickHouse baru (efn_insight, DEFINER, row policy `efn_insight_scope`, kelas sesuai §28.13.7)

| View | Kolom (urutan tetap) | Kelas | Persona |
|---|---|---|---|
| `v_sm_payment_daily` | `day Date, company_id UInt32, warehouse_id UInt32, outlet_type String, method_name String, method_type String, is_refund UInt8, amount Float64, payments UInt64` | W | exec, store |
| `v_sm_product_sales_daily` | `day Date, company_id UInt32, warehouse_id UInt32, outlet_type String, product_id UInt32, categ_id UInt32, qty_sold Float64, net_sales Float64, net_sales_incl_tax Float64` | W | category, exec, store |
| `v_sm_forecast_daily` | `company_id UInt32, warehouse_id UInt32, outlet_type String, product_id UInt32, categ_id UInt32, forecast_date Date, yhat Float64, p10 Float64, p90 Float64, run_id UInt32, generated_at DateTime` (hanya run `ok` terbaru per company) | W | category, dc, exec, store |

| `v_sm_lot_stock` | `company_id UInt32, warehouse_id UInt32, outlet_type String, product_id UInt32, categ_id UInt32, lot_id UInt32, lot_name String, expiration_date Date, days_left Int32, quantity Float64, value_cost Float64` (snapshot stok nyata terbaru, lokasi internal, qty > 0, lot bertanggal) | W | category, exec, store |

Perubahan view yang ada: `v_sm_product_list` + kolom **`shelf_life_days UInt16`** (0 = tidak dikelola) di akhir.
Jumlah policy SM menjadi **33** (dikunci di `insight-sm-policy.sh` + test). Grant ETL Odoo baru hanya kolom yang dibaca.
Tabel umur simpan (B4) memuat **semua lot** (`v_sm_lot_stock`), bukan hanya lot ber-alert (menjawab default Q-SM47).
Total gabungan ESB + SMD diberi catatan "basis campuran" (default Q-SM46).

### B3. Halaman Forecast (`/forecast`)
Untuk SMD (ESB tetap "segera"): forecast vs aktual per minggu 8 minggu (`v_sm_forecast_accuracy_weekly`), WAPE &
bias per outlet dan per kategori, forecast 14 hari ke depan (`v_sm_forecast_daily`, p10–p90), tautan ke usulan
min/max di Odoo. Juga tersedia sebagai kartu di `/exec` dan `/category` yang sudah ada (WAPE terisi setelah A6).

### B4. Umur simpan (shelf life)
Halaman **Kategori** dan **Toko**: tabel "Umur simpan & sisa umur lot" — produk, kategori, umur simpan (hari),
lot terdekat kedaluwarsa per outlet, sisa hari, qty, nilai HPP (dari `v_sm_lot_expiry` + `v_sm_product_list.shelf_life_days`).

### B5. Distribusi SMD
`/distribution` dengan SMD: fill rate DC → outlet (qty & baris), keterlambatan, top produk kurang kirim, per outlet
(`v_sm_dc_fill_rate`, `v_sm_replenishment`, `v_sm_replenishment_line`).

### B6. Histori stok untuk GMROI
ETL merekonstruksi stok harian historis dari `stock_move` (PG) mundur dari quant saat ini, nilai pada HPP (`standard_price`),
ke `agg_sm_stock_daily` — flag `ETL_SM_STOCK_RECONSTRUCT_DAYS` (default 0 = mati; demo 90). Tidak menimpa hari yang
sudah punya snapshot nyata. Dokumentasikan keterbatasan (lot, konsinyasi, revaluasi). Kolom `is_reconstructed UInt8`
di `agg_sm_stock_daily`; baris rekonstruksi tidak pernah menang atas baris snapshot nyata (urutan versi RMT aman, diuji).

## Bagian C — lain-lain
- Seed + flag demo terdokumentasi di `docs/runbooks/demo-supermarket-seed.md`.
- Test: pytest (ETL), vitest (insight-web), test Odoo bila addon berubah, harness row policy CH ditambah view baru.
