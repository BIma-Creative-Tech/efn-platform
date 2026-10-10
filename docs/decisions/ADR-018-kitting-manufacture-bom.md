# ADR-018: Kitting Central Kitchen Memakai BoM Tipe *Manufacture* + MO; Phantom (Kit) Hanya untuk Menu Combo POS

- **Status:** **Accepted** — pemilik proyek, 2026-10-10 (sesi tanya-jawab via efn-lead, butir 5 "ADR Gelombang 1: Accepted semua"; nama & jabatan penandatangan dilengkapi klien di register keputusan). Sebelumnya: Proposed.
- **Tanggal:** 2026-10-05
- **Pemutus (klien):** PO + Operasional Central Kitchen + Finance (costing)
- **Referensi review:** §3.1, R-T01 (skor 20), R-O01, R-L07
- **Terkait:** ADR-001, ADR-010, `docs/05_FSD_CORRECTIONS.md` K-01, `docs/02 §3.3`

> **Ringkasan manajemen.** FSD memakai istilah "Phantom BoM" untuk mengubah ayam & tepung menjadi "Paket Ayam Geprek". Di Odoo, Phantom BoM justru **tidak** menghasilkan paket dan malah memotong stok ayam & tepung di kasir — kebalikan dari tujuan bisnis. Yang benar: Central Kitchen membuat **Manufacturing Order** sungguhan sehingga "Paket" menjadi barang berstok, dikirim ke outlet, dan kasir hanya memotong 1 paket. Tidak ada biaya lisensi; modul `mrp` ada di Community 19.

## Konteks
- FSD §1 langkah 1: *"Phantom BoM: Di-set di Odoo bahwa 1 Porsi Item C = 0.2 Kg Item A + 0.05 Kg Item B."*
- FSD §1 langkah 3: *"Saat barang diterima (Goods Receipt) di Central Kitchen, Odoo MRP secara otomatis memicu Manufacturing Order (MO) berstatus Done ... (Phantom BoM effect)."*
- Fakta Odoo: BoM tipe *Kit* (phantom) tidak pernah membuat MO, produk kit tidak punya stok sendiri; saat dijual (termasuk POS) Odoo meng-explode kit dan memotong stok komponen.
- Audit: `mrp` (LGPL-3) dan `pos_mrp` (*"link module between Point of Sale and Mrp"*) ada di CE 19.0.

## Opsi yang dipertimbangkan

| Opsi | Kelebihan | Kekurangan | Biaya / waktu |
|---|---|---|---|
| A. **BoM *Manufacture* di CK + MO (manual/terjadwal/otomatis dari replenishment); paket = storable; outlet stok paket; menu combo = Kit BoM berisi paket** | Sesuai tujuan bisnis; stok paket nyata di CK & outlet; costing per paket akurat; yield/waste tercatat | MO harus dikonfirmasi & diselesaikan (SOP CK); butuh UoM Porsi | PoC 2 hari; konfigurasi 1–2 minggu; `efn_kitting` opsional 1–2 minggu |
| B. Phantom/Kit BoM (FSD) | Tanpa MO | Outlet memotong ayam/tepung per transaksi → outlet harus stock opname gramasi (tujuan bisnis gagal); paket tidak berstok | Rework penuh saat ketahuan |
| C. Auto-MO "Done" saat goods receipt (FSD langkah 3 secara harfiah) | Otomatis | Menganggap semua bahan langsung jadi paket (tidak ada WIP, tidak ada yield/waste); stok bahan lenyap saat diterima walau belum diolah | Data stok & HPP menyimpang |

## Keputusan
Opsi A. Alur: PT Retail → (inter-company SO/PO, OCA) → CK menerima bahan → **MO Manufacture** dibuat dari permintaan outlet (replenishment/reorder rule atau jadwal produksi) dan diselesaikan saat produksi fisik selesai → stok "Paket … (Porsi)" → internal transfer (atau inter-company bila outlet beda PT) → POS outlet menjual produk paket biasa (tanpa BoM di outlet). **Phantom/Kit BoM** hanya untuk menu combo POS yang terdiri dari beberapa produk paket. Diagram: `docs/02 §3.3`.

## Konsekuensi
- **Positif:** outlet tidak mengurus gramasi; HPP paket akurat; yield & waste CK terukur; lot/expiry paket bisa dilacak (`product_expiry`, R-L07).
- **Negatif:** CK wajib disiplin menyelesaikan MO; modifier menu (Q-21) yang mengubah bahan perlu desain tersendiri.
- **Dampak dokumen:** FSD §1 langkah 1–3 ditulis ulang (`docs/05` K-01); Blueprint "Modul Kitting custom" → konfigurasi `mrp` + opsional `efn_kitting`.

## Risiko terkait
R-T01 (skor 20), R-O01 (master data UoM/SKU paket), R-L07 (traceability), R-L04 (transfer pricing CK ↔ outlet beda PT).

## Pertanyaan terbuka untuk klien
- **Q-35** jumlah & kapasitas CK, produksi berbasis forecast atau pesanan outlet.
- **Q-21** modifier menu dan dampaknya ke resep.
- **Q-34** FEFO, expiry, waste logging untuk paket.
- **Q-3** harga transfer CK → outlet bila beda PT.

## Kapan ditinjau ulang
Setelah PoC 2 hari di sandbox CE 19 (fase 0) dan setelah 4 minggu pilot outlet F&B (akurasi stok paket ±2%).
