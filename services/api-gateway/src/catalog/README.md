# catalog — katalog online publik (Fase 3)

Kontrak: `docs/04` §10.3.2, §10.8.2, §10.8.3. ADR-034 (**Proposed**, keputusan 3: pull delta 5 menit).

- `GET /v1/catalog/channels|categories|products|products/:product_id|images/:image_sha|stock` — publik (tanpa login), rate limit per IP ter-HMAC (`ONLINE_RL_CATALOG_*`, `ONLINE_RL_STOCK_*`), `Cache-Control: public, max-age=60` (stok `no-store`, gambar `public, max-age=31536000, immutable`). Kill switch `ONLINE_CATALOG_ENABLED`.
- Data dari salinan BFF (`platform.online_catalog_*`); pencarian `q` di salinan (ILIKE ter-escape), cursor keyset opak. Tidak ada URL Odoo di respons. Harga **indikatif** — tagihan selalu dari quote Odoo.
- Stok live `efn_online_stock`, cache Redis `ocat:stk:<kanal>:<outlet>:<produk>` 30 s; `qty` tidak diteruskan.
- Gambar: job `CatalogSyncService` menarik `efn_online_image` 512 & 1024 → magic bytes JPEG/PNG/WebP → re-encode sharp (JPEG q85, tanpa metadata, semaphore) → bucket `catalog` key `<kanal>/<sha>/<size>.jpg` lewat `s3-gateway` (TLS CA internal). Endpoint gambar hanya melayani hash yang dirujuk item aktif & sudah siap (`image_ready_sha`).
- Sinkron: advisory lock per kanal, `since`=`server_time` halaman pertama, `deleted` → `deleted_at`; item terhapus > 30 hari dihapus beserta gambarnya. Gagal → `last_error_code`, metrik `efn_online_catalog_sync_age_seconds`.

Risiko: R-S03, R-S06 (gambar), R-T10. Belum: push hint katalog (fase 3b); kolom tambahan `image_ready_sha` & `channel_data` (lihat 07 SQL) diusulkan ke lead.
