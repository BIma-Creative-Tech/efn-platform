# efn_assortment — Restriksi assortment outlet (DC → outlet)

**Tujuan.** G44 Supermarket (ADR-045 *Proposed*, `docs/04` §28.5, fit-gap `docs/fit-gap/G45_SUPERMARKET.md` S-11/S-16,
L-150). Menentukan barang apa yang boleh dikirim DC ke outlet mana: daftar izin/larangan per produk/kategori,
kemampuan outlet (chiller, freezer, izin alkohol, boleh non-halal), kontrak supplier per wilayah, dan DSD (supplier
kirim langsung ke outlet). Odoo CE tidak punya fitur ini.

## Model & field
| Model / field | Isi |
|---|---|
| `efn.assortment.capability` | `name`, `code` (huruf kecil), `company_id` opsional (kosong = bersama). Data: `chiller`, `freezer`, `non_halal_allowed`, `alcohol_license` |
| `stock.warehouse` | `efn_is_dc` (transfer dari gudang ini ditegakkan), `efn_assortment_policy` (`open` default / `closed`), `efn_capability_ids` |
| `product.template` | `efn_required_capability_ids` |
| `efn.assortment.line` | `company_id`, `warehouse_id`, `product_tmpl_id` **atau** `categ_id`, `mode` allow/deny, `supply_mode` dc/dsd, `dsd_partner_id`, `shelf_capacity_qty` (0 = tanpa batas; dibaca `efn_replenishment`), `date_from/to` |
| `product.supplierinfo` | `efn_region_ids` (kontrak wilayah; region outlet = `warehouse.partner_id.efn_region_id`) |
| `res.users` | `efn_outlet_warehouse_ids` (outlet kepala toko; record rule `efn_replenishment` + sumber `wids` Insight §28.11) |
| `efn.assortment.override` | override per transfer × produk × kode; append-only, teraudit |
| `efn.assortment.violation` | laporan pelanggaran harian (cron), satu aktivitas To-Do per PT ke Assortment Manager |

## Keputusan tunggal
`env["efn.assortment.line"]._efn_assortment_allowed(product, warehouse) -> (bool, alasan)` (juga
`warehouse._efn_assortment_allowed(product)`). Urutan: `denied` → `not_listed` (policy closed tanpa allow) →
`missing_capability:<code>` (capability terlindungi dilaporkan lebih dulu) → `supplier_region` → `dsd_only`. Baris
produk lebih spesifik dari kategori; kategori lebih dalam lebih spesifik.

Ditegakkan di: (1) usulan `efn_replenishment` (baris dikecualikan `assortment:<kode>`), (2) **konfirmasi move dari
gudang `efn_is_dc` ke gudang lain PT yang sama** (`stock.move._action_confirm`, mencakup transfer manual dan hasil
orderpoint/procurement) → `UserError efn_assortment:blocked` berisi produk, kode outlet, dan kode alasan, (3) laporan
harian. **Tidak** di POS (keputusan pemilik proyek).

Override: tombol *Override assortment* di transfer draft (grup **Assortment Manager**), alasan ≥ 10 karakter, satu
record per produk; tidak tersedia untuk `alcohol_license` / `non_halal_allowed` (L-150, Q-SM22). Parameter
`efn_assortment.enforce` default `true`; hanya nilai persis `false`/`0` yang mematikan (fail closed).

## Risiko yang dimitigasi
R-O14 (restriksi salah → OOS / alkohol-non-halal-frozen ke outlet tidak mampu), R-L07 (keamanan pangan: chiller/
freezer). Audit `efn_audit`: baris assortment, capability, override.

## Uji manual
1. Inventory > Assortment EFN > Capability: pastikan 4 capability ada. Gudang DC: centang *Distribution Center*.
2. Outlet: kebijakan *Tertutup*, capability *Chiller*. Produk es krim: wajib *Freezer*.
3. Assortment Outlet: izinkan kategori grocery untuk outlet.
4. Buat transfer internal DC → outlet berisi es krim → *Mark as To Do* → ditolak `missing_capability:freezer`.
5. Sebagai Assortment Manager: *Override assortment* + alasan → konfirmasi berhasil; ulangi dengan produk ber-
   capability `alcohol_license` → tetap ditolak.
6. Jalankan cron "EFN assortment: laporan pelanggaran harian" setelah ada stok produk terlarang di outlet → baris
   *Pelanggaran* + aktivitas.

## Batasan / belum
- Penegakan hanya untuk gudang sumber bertanda `efn_is_dc`; gudang CK/F&B tidak terpengaruh. Penentuan outlet tujuan:
  `location_dest_id`, `location_final_id` (bila field ada di 19.0), atau move tujuan berantai — perilaku runtime rute
  resupply 2 langkah belum diuji di Odoo nyata.
- Perubahan `efn_assortment.enforce` teraudit lewat aturan `efn_setup_audit` (kunci `efn_*`, SEC-207, 19.0.2.1.0)
  bila modul itu terpasang; selain itu hanya log WARNING.
- SEC-208: `efn_is_dc`, `efn_assortment_policy`, `efn_capability_ids` (gudang) dan `efn_required_capability_ids`
  (produk) hanya boleh ditulis **Assortment Manager** (guard `create`/`write` server-side; sudo/data modul lolos).
  Gudang diaudit (aturan `audit_rule_stock_warehouse_assortment`, 3 field); produk diaudit aturan
  `efn_setup_audit.audit_rule_product_template` (semua field).
- QA-1: `post_init_hook` men-set `efn_is_dc=True` pada `efn_setup_supermarket.warehouse_smd_dc` bila ada — kini
  hanya pengaman untuk DB lama: sejak G45 data `efn_setup_supermarket` mengisi `efn_is_dc=True` langsung (hook di sana
  dihapus).
- SEC-209 (G45, 19.0.1.2.0): transfer dua langkah DC → transit lalu transit → outlet **dicek di langkah kedua**
  (`stock.move._efn_transit_leg_target`): move bersumber lokasi `transit` milik PT yang sama ke gudang non-DC, hanya
  di PT yang punya gudang `efn_is_dc` (PT F&B/CK tanpa DC tidak terpengaruh). Langkah pertama DC → transit tanpa
  tujuan akhir tetap lolos (tidak ada outlet untuk dinilai); keputusan jatuh di picking transit → outlet (override
  Assortment Manager berlaku di picking itu). Transit antar-PT (lokasi tanpa company) dilewati. Pada rute resupply
  CE, langkah transit → outlet kini ikut dinilai juga (keputusan sama dengan langkah DC, jadi tidak mengubah hasil).
  Belum diuji: `warehouse_id` lokasi transit CE 19 di runtime (test memakai `company.internal_transit_location_id`).
- SEC-221 (G45 security, 19.0.1.3.0): lokasi antara juga mencakup lokasi `internal` tanpa gudang dan transit antar-PT
  (company kosong; kalimat "transit antar-PT dilewati" di atas tidak berlaku lagi). Sisa: mengubah `location_dest_id`
  setelah konfirmasi tidak dicek ulang (penegakan hanya di `_action_confirm`); laporan harian menangkap stoknya.
- Override wajib per transfer draft; transfer hasil procurement yang gagal karena restriksi harus dibuat ulang setelah
  data diperbaiki.
- `efn.assortment.override.picking_id` wajib → transfer yang punya override tidak bisa dihapus (jejak audit).
- Nilai nyata capability per outlet = fit-gap Q-SM13 (izin alkohol), Q-SM21 (non-halal); data seed = `efn_setup_supermarket` tahap B.

## Referensi desain (ADR-031)
OCA `product_assortment` (18.0) — hanya konsep filter per partner; tidak ada kode yang disalin. Lisensi LGPL-3.
