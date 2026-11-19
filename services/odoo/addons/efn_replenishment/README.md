# efn_replenishment — Usulan order outlet → DC dan qty orderpoint DC → RFQ

**Tujuan.** G44 Supermarket (ADR-046 §3.4–§3.5, ADR-045 §3.3, `docs/04` §28.4, fit-gap G45 S-12/S-13/S-16,
L-147/L-149/L-151). Setelah run forecast v2 harian ditutup `ok`, Odoo menghitung **usulan order harian outlet → DC**
dengan stok live, kepala toko menyetujui sebelum cut-off, dan procurement dibuat lewat orderpoint/rute resupply CE.
Untuk DC, modul menulis `qty_to_order_manual` pada orderpoint DC; buyer menekan **Order** CE → RFQ draft per vendor.

## Model
- `efn.delivery.schedule`: outlet, DC, `product_group` (kategori), `weekdays` (bitmask Sen=1..Min=64), `lead_days`,
  `cutoff_time` + `tz`, `review_days`. Cut-off = jam lokal pada (tanggal kirim − lead).
- `efn.supplier.schedule`: supplier, gudang (DC/outlet DSD), hari kunjungan/order, cut-off, lead (0 = `delay` supplier).
- `efn.replenishment.proposal`: `company_id`, `warehouse_id`, `supply_warehouse_id`, `schedule_id`, `delivery_date`,
  `cutoff_at` (UTC), `run_id`, `state` (`draft|proposed|approved|rejected|expired`), `approved_by`, `approved_at`,
  `picking_id`.
- `.line`: `product_id`, `qty_need`, `qty_shelf_cap`, `qty_rack_cap`, `qty_proposed`, `qty_approved`, `reason_codes`,
  `change_reason` (`local_promo|event|rack_full|other`), `changed_by` + informasi `forecast_qty`, `on_hand`,
  `in_transit`, `excluded`.
- `efn.replenishment.bias.log` (append-only): `outlet_change`, `outlet_approved`, `dc_system`, `dc_confirmed`.
- `stock.warehouse.orderpoint`: `efn_replenishment_managed`, `efn_last_system_qty`, `efn_last_run_id`.

## Rumus (per produk × outlet)
```
awal      = maks(tanggal proyeksi pertama run aktif, hari ini)                  (QA-2)
need      = Σ yhat[awal, kiriman berikutnya-setelah-ini + review) + z·σ̄·√L − on_hand − in_transit  (≥ 0)
cap_shelf = Σ yhat[tanggal kirim, ED lot DC terdekat − sisa umur minimum)   (produk use_expiration_date)
cap_rack  = shelf_capacity_qty − on_hand − in_transit                        (baris allow efn_assortment)
qty       = round_pack(min(need, cap_shelf, cap_rack))
```
- Stok live: `qty_available` & `incoming_qty` (konteks `warehouse_id`). Proyeksi di luar horizon = rata-rata seri.
- QA-2: run malam (±23:30) berisi proyeksi mulai besok; penjualan hari ini sudah tercermin di stok live sehingga
  jendela dimulai di tanggal proyeksi pertama (tidak mengisi hari ini dengan rata-rata → tidak over-order).
- Tanpa lot ber-ED di DC → ED = tanggal kirim + `expiration_time`. Sisa umur minimum = field kategori
  `efn_min_receipt_days` bila `efn_expiry_monitor` terpasang, selain itu 0 (Q-SM9).
- `round_pack`: kelipatan `replenishment_uom_id` orderpoint outlet; naik, kecuali melewati cap → turun.
- Gagal assortment → baris `excluded`, `reason_codes = assortment:<kode>`, qty 0.
- z = `efn_replenishment.safety_z` (default 1,65).
- DC: hari order = hari ini + `efn_replenishment.order_day_offset` (default 1, QA-3: kalkulasi malam untuk order
  besok); bukan hari kunjungan supplier → dilewati. `need = Σ yhat_DC[awal, order supplier berikutnya-setelah-hari-
  order + lead) + z·σ̄·√L − virtual_available DC`, cap umur simpan dari tiba (hari order + lead); tanpa jadwal =
  cakupan 7 hari + lead dari awal.
- SEC-210: `qty_approved` kepala toko wajib angka finite ≥ 0 dan ≤ maks(`qty_proposed` ×
  `efn_replenishment.max_approve_factor` (default 3), cap rak bila ada); melewati butuh grup *administrator*
  (sekaligus kepala toko outlet itu).
- Run `ok` tanpa satu pun baris diterima → aktivitas To-Do ke *Replenishment: administrator* PT itu.

## Alur
1. `efn_forecast_run_close(status=ok)` (kind `daily`) → hook menandai `efn_proposal_pending` + `cron._trigger()`
   (tidak dihitung di request RPC).
2. Cron "hitung usulan" (antrean, juga tiap 30 menit): hanya bila `efn_replenishment.enabled=true` (default `false`)
   dan run ditutup **hari ini** (L-147). Satu usulan per (jadwal, tanggal kirim); usulan `draft/proposed` dihitung ulang
   bila ada run baru, `approved/rejected/expired` tidak disentuh.
3. Kepala toko (grup *Replenishment: kepala toko*, outlet di `efn_outlet_warehouse_ids`) mengubah qty (alasan wajib,
   tercatat bias) lalu **Setujui** sebelum `cutoff_at` → `_efn_create_supply_document()`: orderpoint outlet
   `trigger=manual` + rute dari `resupply_route_ids` (supplier = DC) + `qty_to_order_manual` → `action_replenish` CE.
4. Lewat cut-off → `expired` (cron 15 menit, juga saat tombol ditekan); tidak dikirim otomatis (Q-SM23).
5. RFQ DC dari orderpoint terkelola dikonfirmasi buyer → log bias `dc_confirmed`.

## Keamanan / SoD
- Grup: *kepala toko*, *buyer DC*, *administrator* (jadwal). Record rule: kepala toko hanya outlet sendiri; buyer &
  admin semua outlet PT; global multi-company.
- Kepala toko **tidak** bisa mengonfirmasi/menyetujui PO yang barisnya berasal dari orderpoint terkelola
  (`button_confirm` dan jalur double validation `button_approve`, SEC-213).
- Usulan hanya berubah status lewat tombol (write field lain ditolak); baris hanya `qty_approved`/`change_reason`.

## Risiko yang dimitigasi
R-T21 (forecast tidak akurat → approval manusia + log bias), R-T11 (beban Odoo: dihitung di cron, bukan request),
R-O14 (filter assortment), dobel order dengan scheduler CE (`trigger=manual`, fit-gap §6).

## Uji manual
1. Pasang `efn_assortment`, `efn_replenishment`; outlet dengan `resupply_wh_ids` = DC; DC `efn_is_dc`.
2. Buat jadwal kirim (setiap hari, lead 1, cut-off 12:00). Set `efn_replenishment.enabled=true`.
3. Kirim run lewat forecast-worker (atau buat `efn.forecast.run` + `efn.forecast.daily` di shell) dan tutup `ok`.
4. Replenishment EFN > Usulan Order Outlet: periksa qty, cap, kode alasan; ubah qty + alasan; Setujui → transfer DC.
5. Orderpoint DC: `To Order` terisi; tekan **Order** → RFQ draft; konfirmasi sebagai buyer → Log Bias.

## Batasan / belum
- DSD (rute Buy per outlet) belum dihitung otomatis: produk `supply_mode=dsd` hanya dikecualikan dari usulan DC
  (`assortment:dsd_only`).
- Usulan dihitung ulang (dan perubahan kepala toko terhapus) bila run `daily` kedua ditutup pada hari yang sama untuk
  tanggal kirim yang sama.
- Diskon volume, MOQ supplier: dari RFQ CE (`product.supplierinfo`), tidak dimasukkan ke rumus.
- Penentuan "hari ini" DC memakai zona `Asia/Jakarta`.
- Perilaku `action_replenish` CE 19 dengan rute resupply 2 langkah belum diuji di Odoo nyata.

## Referensi desain (ADR-031)
OCA `stock_request`, `stock_warehouse_calendar`, `stock_demand_estimate` — hanya konsep (approval permintaan stok,
kalender lead time); tidak ada kode yang disalin. Lisensi LGPL-3.
