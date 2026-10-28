# efn_demand_forecast — Forecast min/max musiman (G28)

**Tujuan.** K25-12 / Q-35: produksi CK (dan pasok outlet) bisa berdasarkan forecast. Odoo CE hanya punya reorder rule
statis; modul ini membuat **usulan** min/max yang wajib disetujui planner.

## Rumus v1 (tanpa ML)
- Permintaan dasar harian = keluar stok gudang (ke pelanggan, produksi, gudang lain) selama N hari, tiap hari dibagi
  faktor musim hari itu.
- Faktor musim horizon = rata-rata faktor hari-hari ke depan (lead time + periode tinjau).
- `min = d × faktor × lead_days + z × σ × faktor × √lead_days`; `max = min + d × faktor × review_days`.
- z dari tingkat layanan (90/95/98/99 %).

## Pemakaian
Inventory > **Forecast EFN**: *Kalender Musim* (akhir pekan, gajian, Natal-Tahun Baru, Imlek, Ramadan, Lebaran —
tanggal & faktor awal = PLACEHOLDER), *Hitung Forecast* (pilih gudang/produk/parameter), *Usulan Min/Max* (planner
mengubah angka bila perlu lalu **Setujui & Terapkan** → reorder rule diperbarui/dibuat). Cron mingguan untuk gudang
bercentang *Ikut forecast mingguan*.

**Prinsip:** di luar jalur transaksi — bila cron gagal, reorder rule terakhir tetap berlaku. Hanya Inventory
Administrator yang bisa menerapkan.

**Belum:** v2 (statsforecast/Prophet) di worker data setelah ada histori nyata; akurasi belum bisa dibuktikan
sebelum ada data penjualan produksi.
