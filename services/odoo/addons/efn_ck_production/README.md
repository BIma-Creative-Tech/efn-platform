# efn_ck_production — Produksi Central Kitchen (G28)

**Tujuan.** Menutup K25-08 (waste + yield dimonitor) dan K25-12 (produksi CK dua mode) di atas fitur Odoo CE 19,
tanpa modul pihak ketiga (ADR-031). Terkait ADR-018 (kitting = BoM Manufacture).

| Fitur | Cara kerja | Fitur CE yang dipakai |
|---|---|---|
| Mode produksi per produk | `efn_ck_production_mode`: `mts` (reorder/forecast), `mto` (pesanan outlet memicu MO), `both` (stok dulu, kekurangan diproduksi) → rute produk disetel otomatis | rute *Replenish on Order (MTO)* + *Manufacture* |
| Resupply outlet ← CK | Gudang CK ditandai `efn_is_central_kitchen`; outlet memilih `efn_ck_supplier_wh_id` (satu PT) → rute *Supply Product from CK* | `resupply_wh_ids` |
| Waste beralasan | Scrap wajib alasan (Susut, Rusak, Gagal masak, Kedaluwarsa, Sisa produksi); nilai waste dari valuasi stok | `stock.scrap` + `stock.scrap.reason.tag` |
| Yield & varians MO | Yield = hasil/rencana; varians per komponen aktual vs BoM (qty, %, nilai); di luar ambang PT → tanda + aktivitas | `mrp.production` |
| Disassembly | BoM 1 bahan → produk utama + by-product dengan `cost_share` (contoh: ayam utuh → dada + paha) | `mrp.bom.byproduct` (grup By-Products) |
| Laporan | Inventory > Reporting > Analisis Waste (graph/pivot per gudang, alasan, kategori, hari); Manufacturing > Reporting > Analisis Yield | SQL view read-only |

Ambang per PT: Settings > Companies > tab **Ambang CK EFN** (yield min %, varians %, nilai waste/hari).

**Risiko dimitigasi:** R-T01 (paket CK bukan phantom), susut tidak tercatat, boros bahan tidak terlihat.

**Belum:** mart ClickHouse `fact_scrap`/`fact_mo_yield` + dashboard Metabase (kolom laporan Odoo sudah disiapkan
dengan nama yang sama); notifikasi WhatsApp ambang; ambang nilai waste per hari baru tampil di laporan, belum alert.
