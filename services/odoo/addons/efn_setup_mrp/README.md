# efn_setup_mrp

Modul konfigurasi **data-only** untuk Central Kitchen (kitting via BoM Manufacture), lot & kedaluwarsa, dan FEFO di Odoo CE 19.

- **ADR:** ADR-018 (kitting = BoM Manufacture + MO, *Proposed*), ADR-001. **Risiko:** R-T01, R-L07 (traceability pangan), R-O01.
- **Fit-gap:** `docs/08_ODOO_FIT_GAP.md` F-11..F-14.

## Apa yang dikonfigurasi
| Bagian | Isi | File |
|---|---|---|
| Lot/serial | `base.group_user` imply `stock.group_production_lot` (= Settings *Lots & Serial Numbers*) | `data/` |
| Lokasi & route | imply `stock.group_stock_multi_locations`, `stock.group_adv_location` (*Storage Locations*, *Multi-Step Routes*) — dibutuhkan untuk resupply CK → outlet | `data/` |
| Kedaluwarsa | modul `product_expiry` (depends; tidak auto-install di 19.0) | manifest |
| FEFO | kategori **Perishable (FEFO)** dengan `removal_strategy_id = product_expiry.removal_fefo` | `data/` |
| Paket CK (demo) | `Paket Ayam Geprek` & `Paket Nasi Putih` (demo `efn_kitting`): `tracking = lot`, `use_expiration_date`, umur simpan placeholder, kategori FEFO | `demo/` |
| Reorder rule (demo) | `stock.warehouse.orderpoint` paket di company demo CK: min 20 / max 100 Porsi, `route_id` kosong → route manufacture gudang company (auto), nasi (manual) | `demo/` |

## Fakta Odoo 19 yang mengubah panduan lama
- Route **Manufacture** (`mrp.route_warehouse0_manufacture`) di 19.0 adalah route **gudang**: `product_selectable = False`, `warehouse_selectable = True`. Aktif per gudang lewat `stock.warehouse.manufacture_to_resupply` (default `True`), dan rule manufacture hanya berlaku untuk produk yang punya BoM tipe `normal`. **Tidak perlu (dan tidak bisa) memasang route Manufacture di form produk** — README `efn_kitting` langkah 2 perlu dikoreksi.
- Reorder rule: `warehouse_id`/`location_id` dihitung otomatis (precompute) dari company; `trigger` `auto`/`manual`; bila `route_id` kosong dan produk punya BoM, `_get_default_route()` memilih route manufacture.
- `qty_multiple` tidak ada lagi di orderpoint 19.0 (pembulatan lewat `replenishment_uom_id`).

## Alur CK → outlet (konfigurasi, tanpa kode)
1. Gudang CK (company PT F&B): *Manufacture to Resupply* aktif; `manufacture_steps` sesuai SOP (1/2/3 langkah).
2. Paket: storable, UoM Porsi (`efn_kitting.uom_porsi`), `tracking = lot`, kategori FEFO, BoM Manufacture.
3. Gudang outlet (company sama): *Resupply From* = gudang CK (field `resupply_wh_ids` — nama di 19.0 belum diverifikasi; butuh Multi-Step Routes) → reorder rule outlet memicu transfer CK→outlet; reorder rule CK memicu MO.
4. Outlet beda PT → inter-company lewat modul custom `efn_intercompany` (fase 1, `docs/08` §5.2, ADR-031 — tanpa OCA), bukan resupply route (lihat `docs/08` F-03, F-12).

## Keputusan klien yang dibutuhkan
- **Q-34** FEFO, umur simpan per paket, alert, markdown mendekati expiry, waste logging.
- **Q-35** jumlah/kapasitas CK, produksi forecast vs pesanan outlet (menentukan min/max; OCA `mrp_multi_level` hanya referensi desain, ADR-031).
- **Q-30** jumlah gudang & lokasi, multi-step receipt.
- **Q-3** transfer pricing bila outlet beda PT.

## Cara install
```bash
odoo -d <db> -i efn_setup_mrp      # menarik mrp, product_expiry, efn_kitting
```

## Batasan / belum terverifikasi
- Belum pernah di-install. FEFO di POS (pemilihan lot saat jual) dan perilaku scheduler **belum diuji** (PoC).
- Demo memakai company yang sudah ada dengan `efn_brand_code = 'FNB'` (mis. `efn_setup_company.company_fnb`), fallback `base.main_company` — sama dengan demo `efn_kitting` (gelombang 3b). Orderpoint demo tidak lagi memasang `route_id = mrp.route_warehouse0_manufacture` (route itu milik gudang company utama → risiko `_check_company` pada company FNB); route manufacture gudang company dipilih otomatis.
- Demo data belum dieksekusi di CI (job `odoo-test` tidak memuat demo); validasi orderpoint demo masih manual.
- Paket demo kini bertracking lot: uji manual di README `efn_kitting` (MO "Produce All") perlu mengisi lot.
- Kategori FEFO tidak diberi parent; xmlid kategori induk 19.0 belum diverifikasi.
