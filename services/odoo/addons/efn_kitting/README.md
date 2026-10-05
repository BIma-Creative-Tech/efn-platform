# efn_kitting

PoC koreksi konsep "Phantom BoM" FSD: **paket Central Kitchen dibuat lewat BoM tipe Manufacture + Manufacturing Order**; BoM Kit/Phantom hanya untuk menu combo POS yang terdiri dari produk paket.

- **ADR:** ADR-018 (*Proposed*). Diagram: `docs/02 §3.3`. Review: `docs/01 §3.1`.
- **Risiko dimitigasi:** R-T01 (Phantom BoM salah konsep), R-O01 (master data UoM paket).

## Isi
- UoM **Porsi** (`efn_kitting.uom_porsi`, 1 Porsi = 1 Units; Odoo 19 tanpa kategori UoM).
- `product.template.efn_is_kitting_package`: penanda paket CK. Constraint: harus storable dan tidak boleh punya BoM Kit/Phantom.
- `mrp.bom`: constraint BoM Phantom/Kit untuk produk paket ditolak. Peran BoM **tidak disimpan** (field `efn_kitting_role` dihapus di 19.0.1.1.0): paket CK = BoM `normal` pada produk `efn_is_kitting_package`; menu combo POS = BoM `phantom`. Kolom lama di-drop otomatis oleh Odoo saat upgrade.
- Demo (`demo/`, hanya DB demo): **tidak membuat company**. Memakai company yang sudah ada dengan `efn_brand_code = 'FNB'` (mis. dari `efn_setup_company`), fallback `base.main_company` (urutan install tanpa dependensi tidak dijamin; modul ini sengaja tidak bergantung pada `efn_setup_company`). Isi: Ayam Utuh (kg), Tepung Bumbu (kg), Paket Ayam Geprek (Porsi, storable, BoM Manufacture 0,2 kg ayam + 0,05 kg tepung), Paket Nasi Putih, Es Teh Manis, menu **Menu Ayam Geprek Komplit** = Kit (Paket Geprek + Nasi + Es Teh).

## MO otomatis dari replenishment — cukup konfigurasi CE (tanpa kode)
Tidak menulis ulang MRP scheduler. Di company CK:
1. *Inventory → Configuration → Settings*: aktifkan *Multi-Step Routes* bila perlu; warehouse CK: *Manufacture to Resupply* aktif (default `True` saat `mrp` terpasang).
2. **Tidak ada langkah di form produk.** Di Odoo 19 route **Manufacture** (`mrp.route_warehouse0_manufacture`) adalah route **gudang** (`product_selectable=False`, `warehouse_selectable=True`) — tidak bisa dan tidak perlu dipasang di tab *Inventory* produk. Cukup produk punya BoM tipe Manufacture dan warehouse CK punya *Manufacture to Resupply*. (Koreksi atas versi README sebelumnya.)
3. *Inventory → Operations → Replenishment*: buat reorder rule paket (min/max per lokasi CK, atau per outlet dengan route resupply dari CK); route boleh dikosongkan atau diisi route Manufacture gudang. Scheduler harian (`stock.action_procurement_compute` / tombol *Order Once*) membuat MO draft/confirmed dari BoM Manufacture.
4. Outlet: route *Resupply from CK* (internal transfer) atau inter-company (OCA `purchase_sale_inter_company`, ADR-019/ADR-001) bila outlet beda PT.
5. Penjualan menu Kit di POS meng-explode ke produk paket lewat modul CE `pos_mrp` (auto-install bila `point_of_sale` + `mrp`). Centang *Available in POS* pada menu secara manual (modul ini sengaja tidak bergantung pada `point_of_sale`).

## Uji manual (DB demo)
1. Company aktif = PT F&B (`FNB`) atau main company bila `efn_setup_company` tidak terpasang sebelum modul ini. Tambah stok Ayam 20 kg, Tepung 5 kg (bila `efn_setup_mrp` demo terpasang, paket bertracking lot: isi lot saat MO).
2. Buat MO "Paket Ayam Geprek" 100 Porsi → Confirm → Produce All → Done. Stok Ayam 0, Tepung 0, Paket 100.
3. Jual "Menu Ayam Geprek Komplit" (POS atau delivery order) → stok Paket & Nasi berkurang; Ayam/Tepung tidak berubah.
4. Coba ubah BoM paket ke tipe Kit → ditolak.

## Test otomatis
`tests/test_kitting.py` (tag `efn`): MO mengurangi bahan & menambah paket; penjualan Kit memotong paket bukan bahan mentah (via delivery picking — mekanisme explode yang sama dengan picking POS); constraint paket vs Phantom (create & ubah type); paket wajib storable; field peran BoM sudah tidak ada.

## Batasan / belum terverifikasi
- Belum pernah di-install/dijalankan. Test penjualan Kit memakai `stock.picking`, bukan sesi POS (POS explode via `pos_mrp` belum diuji otomatis).
- Demo memakai `eval` dengan `obj().search(...)` di atribut `<field model="res.company">` (konteks eval `convert.py` 19.0) untuk memilih company; belum dijalankan.
- Yield/waste, lot & expiry paket (R-L07, Q-34), modifier menu (Q-21), transfer pricing CK ↔ outlet (Q-3) di luar PoC.
