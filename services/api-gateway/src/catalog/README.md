# catalog — katalog online publik (Fase 3)

Kontrak: `docs/04` §10.3.2, §10.8.2, §10.8.3, **§11.3 (G19)**. ADR-034 (**Proposed**, keputusan 3: pull delta 5 menit).

- `GET /v1/catalog/channels|categories|products|products/:product_id|images/:image_sha|stock` — publik (tanpa login), rate limit per IP ter-HMAC (`ONLINE_RL_CATALOG_*`, `ONLINE_RL_STOCK_*`), `Cache-Control: public, max-age=60` (stok `no-store`, gambar `public, max-age=31536000, immutable`). Kill switch `ONLINE_CATALOG_ENABLED`.
- Data dari salinan BFF (`platform.online_catalog_*`); pencarian `q` di salinan (ILIKE ter-escape), cursor keyset opak. Tidak ada URL Odoo di respons. Harga **indikatif** — tagihan selalu dari quote Odoo.
- Stok live `efn_online_stock`, cache Redis `ocat:stk:<kanal>:<outlet>:<produk>` 30 s; `qty` tidak diteruskan.
- Gambar: job `CatalogSyncService` menarik `efn_online_image` 512 & 1024 → magic bytes JPEG/PNG/WebP → re-encode sharp (JPEG q85, tanpa metadata, semaphore) → bucket `catalog` key `<kanal>/<sha>/<size>.jpg` lewat `s3-gateway` (TLS CA internal). Endpoint gambar hanya melayani hash yang dirujuk item aktif & sudah siap (`image_ready_sha`).
- Sinkron: advisory lock per kanal, `since`=`server_time` halaman pertama, `deleted` → `deleted_at`; item terhapus > 30 hari dihapus beserta gambarnya. Gagal → `last_error_code`, metrik `efn_online_catalog_sync_age_seconds`.

## Kanal per brand (G19, W-21/E-37a, §11.3)

- **G20 §12.1 (L-48)**: kode kanal = kode PT — `GL`, `PB`, `BCR`, `BCF`, `WTZ` (G24 §16.1, brand `WTZ`, nonaktif default) (`ONLINE_CHANNELS` default `GL`); kanal G19 `RTL-GL`/`RTL-BACHA`/`FNB-PB`/`FNB-BACHA` dihapus. `company_code` blok channel wajib salah satu `EFN|GL|PB|BCR|BCF|FARM` (kode lama `RTL`/`FNB` → kanal disembunyikan, fail-closed). Regex §10.2 `^[A-Z][A-Z0-9-]{1,15}$` tetap (hifen masih valid secara format).
- Blok `channel` hasil `efn_online_catalog` membawa `brand_code` (`^[A-Z][A-Z0-9]{1,9}$`, tidak unik, `false`/kosong → `null`; bentuk lain → `invalid_response`). Salinan `channel_data` dari sinkron pra-G19 (tanpa field) → `null`.
- `GET /v1/catalog/channels?brand=<brand_code>` — tanpa parameter = semua kanal aktif; nilai tidak cocok regex (termasuk kosong, huruf kecil, parameter berulang) → `400 invalid_request`; brand tak dikenal → `{ "items": [] }`. Ini **pembatasan UI** — cart/quote/order tidak memakai brand (BFF tidak mempercayai domain).

Respons `GET /v1/catalog/channels?brand=BACHA`:

```json
{ "items": [
  { "channel_code": "BCR", "company_code": "BCR", "brand_code": "BACHA", "name": "Bacha Coffee Butik",
    "fulfilment": { "pickup": true, "delivery": false },
    "outlets": [{ "outlet_code": "TOKO-01", "public_name": "Bacha Senayan", "pickup_enabled": true, "prep_minutes": 30 }],
    "payment_modes": ["qris", "counter"] },
  { "channel_code": "BCF", "company_code": "BCF", "brand_code": "BACHA", "name": "Bacha Coffee Kafe", "fulfilment": { "pickup": true, "delivery": false },
    "outlets": [], "payment_modes": ["counter"] }
] }
```

## G20 — metode bayar & berat (§12.2, §12.3, L-52, L-55)
- `GET /v1/catalog/channels` item bertambah `payment_modes` (array string `qris|counter`, tipe nullable; dari Odoo wajib array, L-57): (blok `channel` Odoo `payment_modes` ?? `ONLINE_PAYMENT_METHODS`) ∩ `ONLINE_PAYMENT_METHODS`. Array kosong = kanal belum bisa dipesan dengan metode yang aktif.
- Produk (`GET /v1/catalog/products`, `…/products/:id`) bertambah `weight_kg` (kg, nullable; salinan pra-G20 → `null`), disalin dari `efn_online_catalog` ke `online_catalog_item.data` (jsonb, tanpa migrasi).
- Risiko: tampilan `payment_modes` dari salinan katalog bisa tertinggal ≤ interval sinkron (5 menit) + cache 30 s — checkout tetap divalidasi ulang terhadap salinan yang sama dan Odoo menolak mode yang tidak diizinkan saat place.

Risiko: R-S03, R-S06 (gambar), R-T10, R-T14 (tenant per domain — brand hanya filter tampilan). Belum: push hint katalog (fase 3b); E-37b pricelist per outlet (G21+).
