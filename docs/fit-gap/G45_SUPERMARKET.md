# G45 — Fit-gap Odoo CE 19 untuk bisnis Supermarket (DC + 5 outlet)

Penulis: efn-odoo-functional, 18 Nov 2026. Dasar: rencana "Requirement terkunci" G44–G49 (18 Nov 2026),
ADR-031 (tanpa modul pihak ketiga; OCA/Cybrosys hanya referensi desain), ADR-045/046 (draf efn-architect, paralel).
Dokumen ini **tidak** menulis kode; ia memberi keputusan Standar vs Custom dan ruang lingkup minimal untuk
efn-odoo-engineer.

Legenda status: **[V]** diverifikasi di source `odoo/odoo` branch 19.0 lewat WebFetch (18 Nov 2026);
**[R]** diverifikasi di repo ini (worktree g44); **[B]** belum terverifikasi (perilaku runtime atau source tidak terbaca).
Belum ada yang diuji di instance Odoo berjalan.

---

## 0. Ringkasan temuan kunci

1. **Lot di POS CE 19 bisa diisi dari barcode GS1, tetapi tidak dari label timbang EAN-13 biasa.**
   - Jalur GS1 `_barcodeGS1Action` mengambil elemen `product` (AI 01), `lot` (AI 10), dan `quantity` (AI 310y kg).
     Lot langsung dipasang ke baris tanpa popup (`code.type === "lot"` → `lot_name`) [V].
   - AI 17 (kedaluwarsa) diurai, tetapi **diabaikan** POS [V].
   - Syaratnya: nomenklatur company = GS1 (`is_gs1_nomenclature`) dengan `pos.config.fallback_nomenclature_id` = EFN
     Retail (IDR) untuk EAN-13 biasa/label harga. Fallback hanya dipakai bila parser melempar `GS1BarcodeError` [V].
   - Label timbang format lama `21PPPPPWWWWWC` tidak punya ruang untuk lot. Timbangan harus mencetak
     **GS1-128 / GS1 DataBar Expanded** berisi (01)+(3103)+(10).
2. **POS CE 19 tidak memilih lot secara FEFO.** Untuk produk berlot yang dipindai lewat EAN-13 biasa, `editLots`:
   - mengambil lot yang ada (`pos.order.line.get_existing_lots`: quant > 0 di lokasi POS, tanpa tanggal kedaluwarsa) [V];
   - otomatis memilih lot hanya bila lotnya cuma satu, atau removal strategy kategori `fifo`/`lifo` [V];
   - **`fefo` tidak ditangani**, jadi kasir mendapat popup di setiap pindaian bila ada lebih dari 1 lot [V];
   - tidak memblokir lot yang sudah kedaluwarsa [V di jalur yang dibaca].

   → **Gap**: pemilihan lot FEFO otomatis dan blokir lot lewat `removal_date` di POS (bagian dari
   `efn_expiry_monitor`). Ini syarat agar "semua SKU berlot" tidak melambatkan kasir.
3. **Harga per lot tidak ada di CE.**
   - `product.pricelist.item` hanya berlaku per global/kategori/template/varian [V].
   - `loyalty.rule` hanya punya produk/kategori/tag/domain produk [V]. Tidak ada field lot di keduanya.

   → **Gap** `efn.lot.markdown` (lot × outlet × harga/diskon × periode), dimuat ke POS saat sesi dibuka sehingga
   tetap berlaku saat offline. Markdown diterapkan **hanya bila lot berasal dari pindaian stiker markdown GS1**
   (01+10) atau dipilih eksplisit. Pemilihan FEFO otomatis **melewati** lot markdown supaya diskon tidak bocor ke
   barang segar.
4. **Route DC→outlet = konfigurasi CE murni**, tidak memakai ulang `efn_ck_production`.
   - `stock.warehouse.resupply_wh_ids` membuat rute "Supply Product from <DC>" lewat
     `company.internal_transit_location_id` (dua picking: DC→transit, transit→outlet, sehingga stok in-transit
     terlihat) [V].
   - `efn_ck_supplier_wh_id` mewajibkan gudang pemasok bertanda Central Kitchen dan modulnya bergantung pada `mrp`
     [R]. Keduanya tidak cocok untuk DC.
   - `efn_setup_supermarket` cukup mengisi `resupply_wh_ids` di XML.
   - **DSD** = rute Buy (purchase_stock) dengan `route_id` di orderpoint outlet, atau usulan per outlet dari
     `efn_assortment` [V field `route_id`].
5. **Usulan PO DC→supplier sebagian besar CE.**
   - Orderpoint DC (`trigger=manual`, `qty_to_order_manual`) + `product.supplierinfo` (`delay`, `min_qty`,
     `product_uom_id`, `price`/`discount` bertingkat) [V], lalu "Order" membuat RFQ draft per vendor. RFQ draft itulah
     usulan yang disetujui buyer (confirm).
   - `qty_multiple` **sudah tidak ada** di 19.0; kelipatan pack = `replenishment_uom_id` [V].
   - Gap hanya: jadwal kunjungan supplier, cap shelf life, dan log bias. Usulan order **outlet** (approval kepala
     toko + cut-off) tetap gap (`efn_replenishment`).

---

## 1. Fit-gap per kebutuhan

| # | Kebutuhan | Fit (CE 19 standar: modul, model/field) | Gap (`efn_*`, lingkup minimal) | Referensi desain (bukan dependency) | Status |
|---|---|---|---|---|---|
| S-01 | Company "PT Supermarket Demo" (placeholder), PPN | `res.company` + `account.chart.template.try_loading('id', company)` (pola `efn_setup_company`) [R]; PPN template `tax_ST4` dkk. [R]; `account_price_include = tax_included` (ritel). | Kode company: `efn_brand_code` adalah Selection tetap 7 kode di `efn_core` dan dikunci test kontrak [R], sehingga kode baru (usul `SMD`) butuh revisi docs/04 §12.1 + `efn_core` (**efn-architect**, bukan modul setup). `efn_business_type = retail` sudah tersedia [R]. | — | [R] |
| S-02 | DC = warehouse di PT yang sama + 5 outlet warehouse | `stock.warehouse` per lokasi (`code` pendek, `reception_steps` one/two/three_steps, `delivery_steps` ship_only/pick_ship/pick_pack_ship) [V]. Usul DC `two_steps` + `pick_ship`, outlet `one_step` + `ship_only`. | — (data-only `efn_setup_supermarket`). | — | [V] |
| S-03 | Route resupply DC→outlet | `resupply_wh_ids` pada warehouse outlet → `create_resupply_routes`; transit = `company.internal_transit_location_id`; rute `warehouse_selectable`, masuk `route_ids` outlet [V]. Orderpoint outlet `route_id` = rute itu [V]. | Tidak perlu `efn_ck_production` (alasan: syarat `efn_is_central_kitchen`, depends `mrp`) [R]. Bila kelak dibutuhkan "satu DC per outlet" sebagai field ramah pengguna, cukup constraint kecil di `efn_assortment`, bukan ketergantungan CK. | OCA `stock_warehouse_out_pull` (19.0 ada) — hanya pembanding pola rute. | [V] |
| S-04 | DSD (supplier kirim langsung ke outlet, bypass DC) | Rute Buy (`purchase_stock`), `stock.warehouse.buy_to_resupply` [B nama field 19.0], orderpoint outlet `route_id` = Buy; RFQ `picking_type_id` = penerimaan outlet. `product.supplierinfo` per vendor [V]. | `efn_assortment`: flag per outlet×produk `supply_mode` (`dc`/`dsd`) + `dsd_partner_id`; usulan order memakai rute sesuai flag. | OCA `product_assortment` (18.0) — konsep filter per partner. | [V]/[B] |
| S-05 | Lot + expiry untuk **semua** SKU | `product.template.tracking = lot`, `use_expiration_date`, `expiration_time`, `use_time`, `removal_time`, `alert_time` (hari) [V]. `stock.lot.expiration_date/use_date/removal_date/alert_date` (compute stored, bisa diedit), `product_expiry_alert` [V]. Grup `stock.group_production_lot` (sudah di `efn_setup_mrp`) [R]. | Default umur per kategori (template mewarisi saat dibuat) → `efn_expiry_monitor` (field kategori + onchange/create). | Cybrosys `odoo_product_expiry_dashboard` (App Store 19.0, AGPL-3) — ide dashboard. | [V] |
| S-06 | FEFO | `product.removal` `product_expiry.removal_fefo` pada `product.category.removal_strategy_id` / `stock.location.removal_strategy_id` [R][V]. Validasi picking dengan lot kedaluwarsa memunculkan wizard `product_expiry.confirm_expiry_view` (semua tipe picking) [V]. | POS tidak FEFO → lihat §2. | — | [V] |
| S-07 | Fresh: label timbang berat variabel + lot di POS | Nomenklatur GS1 (`barcodes_gs1_nomenclature.default_gs1_nomenclature`: 01 GTIN, 10 lot, 17 expiry, 310y kg) [V]; `pos.config.fallback_nomenclature_id` [V]; qty dari AI 310y hanya bila UoM produk = kg [V]; `iface_electronic_scale` [V]. | Tidak ada gap untuk parsing. Gap: kepastian lot (lihat §2) dan uji katalog barcode. | OCA `pos_lot_barcode` (18.0; 19.0 tidak ada) — memindai lot di POS. | [V] parsing; [B] perangkat |
| S-08 | Ambang near-expiry per kategori + notifikasi | Cron `stock.lot._alert_date_exceeded`: satu aktivitas To-Do per lot (sekali, `product_expiry_reminded`) ke `responsible_id` produk, fallback superuser [V]. Ambang = `alert_time` per **template**, bukan kategori/outlet [V]. | `efn_expiry_monitor`: ambang per kategori (`efn_near_expiry_days`, `efn_markdown_days`), model `efn.expiry.alert` (lot × outlet × qty × sisa hari × status), cron harian, `mail.activity` ke kepala toko outlet + ringkasan ke buyer/category manager. | Cybrosys `product_expiry_warning` (≤14.0) — ide peringatan; OCA: tidak ada di 19.0. | [V] CE; desain gap |
| S-09 | Markdown manual per lot + "kirim ke member" + berlaku offline | Pricelist/loyalty **tanpa** lot [V]. `sale_loyalty`/`pos_loyalty` bisa dipakai untuk voucher ke member (S-16). | `efn.lot.markdown` + aset POS (lihat §3). Tombol "Kirim ke member" → outbox → BFF → `promo.campaign.requested` (kontrak architect). | Tidak ada referensi OCA/Cybrosys 19.0 yang terverifikasi untuk harga per lot. | [V] gap |
| S-10 | Sisa umur minimum saat terima dari DC (per kategori) | CE hanya memperingatkan lot **sudah** kedaluwarsa / melewati removal date [V]. | `efn_expiry_monitor`: `efn_min_receipt_days` (atau % umur) per kategori; cek saat validasi penerimaan outlet (blok, override oleh grup tertentu dengan alasan, teraudit). **Juga di sisi DC**: reservasi transfer ke outlet melewati lot yang tidak memenuhi minimum outlet (hook pada gather/reservasi [B nama metode]), agar DC tidak mengirim barang yang pasti ditolak. | — (tidak ditemukan yang terverifikasi) | desain gap |
| S-11 | Assortment/restriksi per outlet (izin SKU/kategori, kemampuan outlet, region supplier) | CE tidak punya. `res.partner.efn_region_id` (kab/kota) sudah ada [R] → region outlet = `warehouse.partner_id.efn_region_id`. | `efn_assortment` (§5). Ditegakkan **hanya** di order/replenishment ke DC + constraint transfer manual DC→outlet (keputusan terkunci). | OCA `product_assortment`, `product_pricelist_assortment` (18.0). | desain gap |
| S-12 | Jadwal kirim DC→outlet, cut-off, usulan order harian, approval kepala toko | Orderpoint outlet (`trigger`, `product_min_qty/max`, `qty_to_order_manual`, `lead_days`, `deadline_date`, `route_id`) [V]. Tidak ada jadwal hari kirim, cut-off, approval, atau log perubahan. | `efn_replenishment`: `efn.delivery.schedule` (outlet × kelompok produk: hari, lead time, cut-off jam + TZ), `efn.replenishment.proposal(.line)` (qty sistem vs qty disetujui, alasan, user, waktu → bias). Setelah disetujui, procurement dibuat lewat orderpoint/rute CE (bukan membuat picking sendiri). Cap: shelf life, kapasitas rak, kelipatan `replenishment_uom_id`. | OCA `stock_warehouse_calendar` (19.0 ada: kalender pada lead time), `stock_request` + `stock_request_tier_validation` (18.0: permintaan stok berapproval), `stock_demand_estimate` (19.0). | [V] CE; desain gap |
| S-13 | Usulan PO DC→supplier (lead time, MOQ, packaging, jadwal kunjungan, diskon volume, konsinyasi) | `product.supplierinfo`: `partner_id`, `min_qty`, `delay`, `price`, `discount`, `product_uom_id`, `date_start/end`, `company_id` [V]; beberapa baris per `min_qty` = harga bertingkat. Orderpoint DC + Buy → RFQ per vendor; `allowed_replenishment_uom_ids` memuat UoM supplierinfo [V]. Approval: RFQ draft → confirm; opsional double validation PO [B field 19.0]. Konsinyasi: owner stok (`stock.quant.owner_id`, grup tracking owner) [B nama grup 19.0]. Retur ED ke supplier = return picking. | `efn_replenishment`: `efn.supplier.schedule` (hari kunjungan/order, cut-off), cap shelf life, worker menulis `qty_to_order_manual`, log bias pada RFQ. **Jangan** membuat objek usulan PO sendiri. Settlement konsinyasi (terjual → tagihan vendor) = fase berikut, menunggu Q-SM6. | OCA purchase-workflow (belum dicek per modul). | [V]/[B] |
| S-14 | Transfer antar outlet (near-expiry/overstock) | Picking internal dari outlet A ke lokasi stok outlet B (satu PT) [B runtime]; alternatif dua langkah lewat transit. Resupply silang antar-outlet (5×4 rute) **tidak** disarankan. | `efn_expiry_monitor`: usulan transfer (lot near-expiry A dengan laju jual rendah → outlet B dengan forecast lebih tinggi), membuat picking draft. Fase 1: satu langkah A→B. | — | desain gap |
| S-15 | Stock opname / cycle count, jadwal per kategori | `stock.quant` mode inventory (`inventory_quantity`, `inventory_date`, `user_id`) [B field list]; wizard `stock.request.count` (`inventory_date`, `user_id`) [V; catatan: `user_id` ditulis sebagai tuple karena koma di belakang → uji]; `stock.location.cyclic_inventory_frequency`, `next_inventory_date`, `last_inventory_date` [V]. | **Tanpa gap bila** tiap outlet dipecah menjadi sub-lokasi zona (Dry, Chiller, Freezer, Fresh) dengan frekuensi masing-masing (data). Gap opsional (fase 2): jadwal per kategori lintas lokasi. | OCA `stock_cycle_count` (18.0), `stock_inventory` (19.0). | [V]/[B] |
| S-16 | Kapasitas rak / planogram (cap usulan order) | `stock.storage.category` + kapasitas produk (dipakai putaway; field kapasitas dirujuk `product_capacity_ids`) [V nama relasi]. Butuh lokasi per rak → berat untuk toko. | Field `shelf_capacity_qty` pada baris `efn_assortment` (outlet × produk), dibaca `efn_replenishment`. | OCA `stock_storage_category_capacity_name` (18.0). | desain gap |
| S-17 | Voucher personal kampanye; kanal POS/kiosk/online | `loyalty.program` tipe kupon; wizard `loyalty.generate.wizard` mode `selected` + `customer_ids`/`customer_tag_ids`, `valid_until`, `points_granted` → `loyalty.card.partner_id` [V]. POS `use_coupon_code`: kupon hanya untuk partner kosong/sama, cek kedaluwarsa & periode program [V] (member harus diidentifikasi di order). | `efn_campaign` (§5): usulan segmen dari worker, approval marketing, cap frekuensi & anggaran (default nonaktif), outbox ke BFF. Kanal online (`sale_loyalty` di sale.order) [B]; kiosk `pos_self_order` + kupon [B]. | — | [V] POS; [B] online/kiosk |
| S-18 | Kalender forecast & perluasan musim | `resource.calendar.leaves` CE bisa menyimpan libur global [B], tetapi tanpa jenis libur/faktor. `efn.forecast.season` (period/monthly/weekday, factor, kategori) [R]. | Di `efn_demand_forecast`: model `efn.forecast.calendar` (tanggal, jenis: libur nasional/cuti bersama/Lebaran/override, sumber `holidays`/manual, company opsional). Season mendapat kind baru `payday` (N hari terakhir + N hari pertama, mengikuti panjang bulan) dan `holiday` (jendela H-x..H+y relatif terhadap jenis kalender). `monthly` 25..5 lama tetap berlaku. | — | [R] |
| S-19 | Proyeksi penjualan & min/max (output forecast 1 & 4) | Orderpoint CE; `efn.demand.forecast` v1 (outflow stok, approval planner, `trigger` auto/manual) [R]. | Worker G44 menulis lewat API. **Aturan anti-dobel**: SKU×outlet yang dikelola `efn_replenishment` → orderpoint `trigger=manual`. Scheduler CE `auto` hanya untuk SKU di luar jangkauannya. | — | [R] |

---

## 2. Lot di POS CE 19: perilaku dan rekomendasi

**Fakta source 19.0 [V]:**
- `product_template.js isTracked()` = `tracking ∈ {serial, lot}` **dan** (`use_create_lots` atau `use_existing_lots`)
  pada picking type POS. Bila kedua flag mati, POS tidak menanyakan lot sama sekali.
- `pos_store.js addLineToOrder`:
  - produk berlot + barcode dengan `code.type === "lot"` → `pack_lot_ids = [{lot_name}]` tanpa popup;
  - selain itu `editLots` → `get_existing_lots(company, config, product)`.
- `get_existing_lots` = `read_group` quant per lot (lokasi anak dari lokasi sumber POS, qty > 0). Hasilnya tanpa
  urutan eksplisit dan tanpa tanggal kedaluwarsa.
- Pemilihan otomatis: 1 lot, atau `fifo` (lot pertama dalam daftar), atau `lifo` (terakhir). **Tidak ada `fefo`.**
  Catatan: "fifo" di sini berarti urutan hasil query, bukan `in_date` [B].
- `canCreateLots = use_create_lots || !use_existing_lots`; bila tidak ada lot dan pembuatan dilarang → alert.
- Server (`stock_picking.py _create_production_lots_for_pos_order`): lot yang belum ada hanya dibuat bila
  `use_create_lots`. Bila nama lot tidak ditemukan, move line bisa tanpa `lot_id` (kasus tepi, perlu diuji) [B].
- GS1: `barcode_reader_service._scan` → bila hasil array tanpa produk → `GS1BarcodeError` → `fallbackParser`
  (nomenklatur fallback config). Error lain (check digit, tanggal tidak valid) **dilempar ulang** dan pindaian gagal.

**Risiko nomenklatur GS1 sebagai primer:**
- EAN-13 yang diawali `13` (prefiks GS1 AS 130–139) cocok dengan AI 13 (tanggal kemas). Bila 6 digit berikutnya
  bukan tanggal valid, parser melempar `Error` biasa (bukan `GS1BarcodeError`), sehingga **tidak jatuh ke fallback**.
- Hal yang sama berlaku untuk pola `15`/`17`.
- Wajib uji seluruh katalog barcode (±400 SKU simulasi + barcode impor nyata) di PoC sebelum mengganti nomenklatur.

**Rekomendasi (mode "lot di POS"):**
1. Picking type POS outlet: `use_existing_lots = True`, `use_create_lots = False`. Kasir tidak boleh membuat lot
   baru; lot dibuat saat penerimaan dari DC/DSD.
2. Company PT Supermarket: nomenklatur **GS1**, `pos.config.fallback_nomenclature_id` = `efn_setup_pos` EFN Retail
   (IDR) untuk EAN-13 dan label harga `23`. Lakukan ini hanya setelah uji katalog lolos. Bila gagal, tetap EFN Retail
   dan lot fresh lewat FEFO otomatis (butir 3).
3. **Gap `efn_expiry_monitor` (aset POS):**
   - perluas data lot POS dengan `expiration_date`/`removal_date`;
   - pilih lot FEFO otomatis (lewati lot ber-markdown dan lot yang melewati `removal_date`);
   - blok lot kedaluwarsa (override supervisor, teraudit);
   - di server, terapkan pemilihan lot yang sama untuk order yang tersinkron tanpa lot.
4. Label timbang fresh: GS1-128 atau DataBar Expanded `(01)GTIN(3103)berat(10)lot`, dengan **lot di akhir** (AI
   panjang variabel tanpa pemisah FNC1). Lot = nama `stock.lot` Odoo yang diterima outlet (≤ 20 karakter set GS1).
   Model timbangan & pemindai (dukungan DataBar Expanded, FNC1→GS) = Q-SM3 [B].
5. Alternatif CE murni tanpa lot di POS (kedua flag mati → POS tidak bertanya; server mengisi qty move): **ditolak**.
   Alasannya:
   - lot per outlet akan menyimpang;
   - markdown per lot tidak mungkin;
   - pemilihan lot oleh server lewat removal strategy belum terverifikasi (`_set_quantity` → `_set_quantity_done`)
     [B].
6. Kiosk (`pos_self_order`) tidak memfilter produk berlot (domain tambahan hanya `self_order_available`) [V].
   Perilaku lot pada order kiosk belum diverifikasi [B]. Usul: produk fresh berlot tidak dijual di kiosk sampai diuji
   (Q-SM8).

## 3. Harga per lot (markdown): opsi dan rekomendasi

| Opsi | Cara | Kelebihan | Kekurangan | Keputusan |
|---|---|---|---|---|
| A | Pricelist/promo `pos_loyalty` per produk di outlet | CE murni | Berlaku untuk **semua** lot produk itu, termasuk barang segar di rak yang sama | Ditolak |
| B | Produk/varian "Markdown" terpisah (relabel: pindah stok ke produk lain) | Harga CE biasa | Jurnal/stok ganda, lot & ED harus dipindah, merusak forecast | Ditolak |
| C | Label harga EAN-13 `23` (harga tertanam) dicetak ulang | CE murni (`efn_setup_pos` sudah punya aturan) | Lot tidak terbawa; harga bisa dipalsukan via cetak label; tidak ada periode | Hanya darurat |
| **D** | **`efn.lot.markdown`** (lot, warehouse/outlet, `price` atau `discount_pct`, `date_from/to`, `qty_cap`, `state`, `notify_members`) + aset POS | Tepat per lot, teraudit, bisa offline | Butuh kode (gap) | **Dipilih** |

Rincian opsi D untuk engineer:
- **Aktivasi**:
  - staf/kepala toko membuat markdown dari `efn.expiry.alert`;
  - batas diskon per peran (Q-SM4);
  - mencetak stiker GS1 `(01)(10)` untuk lot tersebut (laporan label; ketersediaan opsi GS1 di label lot CE 19 [B]).
- **POS**:
  - data markdown aktif untuk warehouse config dimuat lewat loader data POS saat sesi dibuka, dan diperbarui saat
    online (notifikasi bus) [B mekanisme 19.0];
  - saat baris mendapat lot (dari pindaian GS1 atau pilihan eksplisit kasir) dan ada markdown aktif → harga/diskon
    diterapkan dan baris ditandai "Markdown lot X";
  - **FEFO otomatis tidak pernah memilih lot markdown**;
  - offline: markdown yang sudah tersinkron tetap berlaku (keputusan terkunci), periode dicek dengan jam perangkat.
- **Server**:
  - order tersinkron diterima apa adanya (sudah dibayar);
  - selisih (markdown kedaluwarsa/dibatalkan saat offline, qty > `qty_cap`) dicatat untuk audit, bukan ditolak.
- **"Kirim ke member"**:
  - centang di markdown → record outbox → BFF klaim (pola `efn_promo_push_claim`, RPC user teknis satu company)
    [R pola] → BFF menerbitkan `promo.campaign.requested`;
  - Odoo tidak menerbitkan langsung ke RabbitMQ: belum ada modul efn yang melakukannya [R];
  - nama event & payload = kontrak docs/04 (architect).
- **Kanal online**: lot tidak dipilih saat pesanan online, jadi markdown per lot **tidak** berlaku online di fase 1
  (Q-SM12).

## 4. Pajak (placeholder sampai konsultan pajak menjawab)

- **PPN (pusat, `l10n_id`)**:
  - harga include (ritel);
  - barang kebutuhan pokok yang dibebaskan PPN → pajak `tax_ST2` "Bebas PPN" per produk/kategori.
    **Daftar kategori bebas PPN = keputusan konsultan pajak** (R-L03);
  - pemusatan PPN dengan banyak tempat usaha (NITKU per outlet di Coretax): dukungan CE 19 `l10n_id` / e-Faktur
    Coretax per outlet [B].
- **PB1/PBJT (daerah)** hanya bila outlet punya area makan siap saji (deli/kafe) yang dikenai PBJT. Pola fiscal
  position per kota `efn_setup_company` [R]. Tidak dikonfigurasi default.

> **Keputusan 18 Nov 2026 (S-17):** mode voucher configurable per kampanye — `anonymous_code` (default, kartu tanpa partner), `public_code` (kode bersama), `member_bound` (kartu per `partner_id`, khusus member). `efn_campaign` wajib mendukung ketiganya; lihat ADR-047 §3.4 dan `docs/04` L-164.

## 5. Modul usulan

| Modul | Depends | Model/field utama | Record rule multi-company | Urutan |
|---|---|---|---|---|
| **`efn_assortment`** | `stock`, `purchase_stock`, `efn_core`, `efn_region` | `efn.assortment.capability` (name, code, company_id opsional), `stock.warehouse.efn_capability_ids`, `stock.warehouse.efn_assortment_policy` (`open`/`closed`), `product.template.efn_required_capability_ids` (mis. chiller, freezer, *boleh non-halal*, *izin alkohol*), `efn.assortment.line` (company_id, warehouse_id, product_tmpl_id **atau** categ_id, `mode` allow/deny, `supply_mode` dc/dsd, `dsd_partner_id`, `shelf_capacity_qty`, date_from/to), `product.supplierinfo.efn_region_ids` (kontrak region), `res.users.efn_outlet_warehouse_ids` (cakupan outlet, juga dibaca Insight ADR-041 revisi). Fungsi `_efn_assortment_allowed(product, warehouse)` → (bool, alasan). Constraint transfer manual DC→outlet. | `company_id in company_ids` untuk line & capability (capability kosong = bersama). Rule tambahan grup kepala toko: `warehouse_id in user.efn_outlet_warehouse_ids`. | 1 (dipindah ke awal G44 karena `efn_replenishment` bergantung padanya) |
| **`efn_demand_forecast`** (perluasan) | tetap | `efn.forecast.calendar`; kind season `payday`, `holiday` | sama dengan season (company kosong = semua PT) | 2 |
| **`efn_replenishment`** | `stock`, `purchase_stock`, `product_expiry`, `mail`, `efn_core`, `efn_demand_forecast`, `efn_assortment` | `efn.delivery.schedule`, `efn.supplier.schedule`, `efn.replenishment.proposal` + `.line` (forecast s/d kirim berikutnya + safety − on-hand − in-transit; cap shelf life, rak, kelipatan; qty sistem vs disetujui; alasan), approval kepala toko sebelum cut-off → procurement via orderpoint/rute CE; tulis `qty_to_order_manual` DC + log bias pada RFQ; API untuk worker (user teknis, satu company). | `company_id in company_ids`; proposal: kepala toko hanya outlet sendiri; buyer: semua outlet PT. | 3 |
| **`efn_expiry_monitor`** | `stock`, `product_expiry`, `point_of_sale`, `mail`, `efn_core`, `efn_assortment` | Field kategori (`efn_near_expiry_days`, `efn_markdown_days`, `efn_min_receipt_days`, default umur produk), `efn.expiry.alert` (mail.activity.mixin), `efn.lot.markdown`, outbox "kirim ke member", usulan transfer antar outlet, cek sisa umur saat terima + filter reservasi DC; aset POS: FEFO otomatis, blok kedaluwarsa, harga markdown. | `company_id in company_ids`; alert/markdown: kepala toko hanya outlet sendiri. | 4 (backend dulu, lalu POS) |
| **`efn_setup_supermarket`** (data-only) | Tahap A: `stock`, `purchase_stock`, `product_expiry`, `point_of_sale`, `pos_loyalty`, `barcodes_gs1_nomenclature`, `l10n_id`, `efn_core`, `efn_setup_company`, `efn_setup_pos`. Tahap B: + tiga modul gap. | Tahap A: company (noupdate, nama placeholder, `try_loading('id')` seperti `efn_setup_company`), grup lot/multi-lokasi/multi-step (implied, sama dengan `efn_setup_mrp`, tanpa bergantung `mrp`), warehouse DC (data), 10 kategori standar dengan FEFO, nomenklatur. Di `demo/`: 5 outlet + `resupply_wh_ids` = DC, sub-lokasi zona + `cyclic_inventory_frequency`, picking type POS `use_existing_lots`. Tahap B (`demo/`): ambang per kategori, capability, jadwal kirim contoh. | n/a (data). | 0 (tahap A, agar seed G48 bisa jalan) dan 5 (tahap B) |
| **`efn_campaign`** | `loyalty`, `pos_loyalty`, `sale_loyalty`, `mail`, `efn_core`, `efn_promo` | `efn.campaign` (segmen dari worker, program kupon, approval marketing, cap frekuensi/anggaran default off, status), pembuatan `loyalty.card` per partner lewat wizard CE, outbox ke BFF (tanpa PII di Odoo selain partner_id). | `company_id in company_ids`. | 6 (G46) |

Semua modul: LGPL-3, versi `19.0.1.0.0`, `ir.model.access.csv` lengkap, README dengan ADR-031/045/046 dan referensi
desain. Kode OCA/Cybrosys tidak disalin.

## 6. Audit modul existing (duplikasi standar)

- `efn_ck_production`: **jangan** diperluas untuk DC. Rute pasok CK tetap untuk F&B. DC memakai `resupply_wh_ids`
  langsung.
- `efn_demand_forecast` v1: tetap untuk min/max. Risiko **dobel order**:
  - orderpoint `trigger=auto` (scheduler CE) dan proposal `efn_replenishment` sama-sama memicu pasokan;
  - mitigasi: proposal memaksa `trigger=manual` pada orderpoint yang dikelola.
- `efn_promo`: banner/push saja; tidak ada duplikasi `sale_loyalty` [R]. Pola outbox-klaimnya dipakai ulang.
- `efn_setup_pos` nomenklatur EFN Retail: tetap sebagai fallback. Aturan lot `10` CE sengaja tidak dibuat di sana
  [R], dan itu tetap benar untuk nomenklatur non-GS1.

## 7. Pertanyaan klien

- **Q-SM1** Kode company untuk PT Supermarket (usul `SMD`), nama legal, NPWP, status PKP (efn-architect, docs/04 §12.1).
- **Q-SM2** Nama/alamat/kab-kota 5 outlet dan DC; jam operasional; zona (chiller/freezer/fresh) per outlet.
- **Q-SM3** Model timbangan label & pemindai: bisa mencetak/membaca GS1-128 atau DataBar Expanded berisi lot?
  Bila tidak, fresh memakai FEFO otomatis tanpa lot di label.
- **Q-SM4** Siapa yang boleh mengaktifkan markdown, diskon maksimum per peran, dan apakah perlu approval di atas X %.
- **Q-SM5** (Konsultan pajak) Kategori bebas PPN, harga include, PB1 untuk deli/kafe, pemusatan PPN/NITKU.
- **Q-SM6** Konsinyasi: barang mana, cara settlement (terjual vs diterima), retur ED.
- **Q-SM7** Transfer antar outlet: satu langkah atau lewat transit (in-transit terlihat)?
- **Q-SM8** Kiosk di supermarket: produk apa yang dijual (fresh berlot dikecualikan sampai diuji)?
- **Q-SM9** Sisa umur minimum saat terima: dalam hari atau % umur simpan? Siapa yang boleh override?
- **Q-SM10** Frekuensi cycle count per zona; opname tahunan (bulan/tanggal).
- **Q-SM11** Jadwal kirim DC→outlet per kelompok produk dan jam cut-off.
- **Q-SM12** Markdown per lot berlaku juga di kanal online?
- **Q-SM13** (Legal) Izin penjualan minuman beralkohol per outlet → capability `izin alkohol`.

## 8. Belum terverifikasi

- Runtime semua butir (tidak ada Odoo lokal yang dijalankan untuk G45).
- Nama field `stock.warehouse.buy_to_resupply`, grup owner/konsinyasi, PO double validation, dan field
  `stock.quant` mode inventory di 19.0.
- Server POS: lot tidak ditemukan → move line tanpa lot; pemilihan lot oleh server saat flag lot mati.
- Urutan `get_existing_lots` (arti "fifo" di POS); mekanisme refresh data POS saat online (bus).
- Kupon personal di `sale_loyalty` (online) dan `pos_self_order` (kiosk); perilaku lot di order kiosk.
- Opsi GS1 pada laporan label lot/produk CE 19; dukungan perangkat timbangan/pemindai.
- Bug `stock.request.count._get_values_to_write` (`user_id` tuple) — perlu diuji; bila benar, hindari assign user
  lewat wizard.
- `resource.calendar.leaves` sebagai penyimpan libur global.
- Referensi OCA: `pos_lot_barcode`, `pos_product_expiry`, `product_assortment`, `stock_cycle_count`, `stock_request*`
  hanya ada di 18.0 (bukan 19.0). `stock_warehouse_calendar`, `stock_demand_estimate`, `stock_inventory`,
  `stock_warehouse_out_pull`, `stock_warehouse_security` ada di 19.0. Isi modul tidak dibaca (hanya nama/deskripsi).
  Cybrosys: hanya `odoo_product_expiry_dashboard` (App Store 19.0, AGPL-3); repo CybroAddons 19.0 tidak memuat modul
  expiry yang terlihat (listing terpotong).
