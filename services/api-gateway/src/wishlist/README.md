# `src/wishlist` — wishlist, notify-me, price alert (G50-A)

**Tujuan.** Member menyimpan wishlist, minta dikabari saat stok kembali di outlet tertentu, atau saat harga turun di
bawah target. Data di PG platform (bukan Odoo), ikut DSAR export & erasure (L-161). Pelaksanaan permintaan member →
tidak butuh consent `tracking`.

- **ADR:** ADR-047 (Proposed), ADR-044 (WA unofficial), ADR-048 (email). **Kontrak:** `docs/04` §28.7–§28.8, §28.10.
- **Risiko:** R-L01 (PDP), R-B17 (spam: alert sekali kirim, batas 50 aktif, kanal email/WA hanya bila flag aktif +
  consent diperiksa notifier), R-S04.
- **Flag:** `WISHLIST_ENABLED`, `RESTOCK_ALERT_ENABLED`, `PRICE_ALERT_ENABLED` (default false → endpoint 404).

## Kontrak API (Bearer member, `Cache-Control: no-store`)
| Endpoint | Ringkas |
|---|---|
| `GET /v1/me/wishlist?channel=<kode>` | `{items:[{channel_code, product_id, added_at}]}` |
| `PUT /v1/me/wishlist/items` | `{channel_code, product_id}` → 204, idempoten; produk harus ada di katalog kanal (404); > 200 → `409 wishlist_full` |
| `DELETE /v1/me/wishlist/items/:channel_code/:product_id` | 204 |
| `GET /v1/me/restock-alerts` | alert aktif |
| `POST /v1/me/restock-alerts` | `{channel_code, outlet_code, product_id, channels[push|email|wa_unofficial]}` → `201 {id}`; produk harus `out` (stok live) selain itu `409 already_in_stock`; > 50 aktif → `409 alert_limit_reached`; kanal email/WA saat flag kanal mati → `400 channel_unavailable`; kedaluwarsa 30 hari / setelah terkirim sekali |
| `DELETE /v1/me/restock-alerts/:id` | 204 / 404 (bukan milik) |
| `GET /v1/me/price-alerts`, `POST /v1/me/price-alerts`, `DELETE /v1/me/price-alerts/:id` | `{channel_code, product_id, target_price_idr, channels?}`; target integer > 0 dan < harga saat ini (`400 invalid_target_price`); 90 hari; **`channels` opsional = usulan kontrak** (default `["push"]`) |

## Desain
- **Pengamat** (`AlertWatcherService`, `ALERT_WATCH_INTERVAL_SECONDS`=300, lock Redis ber-TTL): hanya produk dengan alert
  aktif. Stok lewat `CatalogService.stock` (cache 30 s → Odoo), harga dari salinan katalog. State di
  `platform.alert_stock_state` / `alert_price_state`; transisi `out→in_stock|low` / harga turun → **outbox**
  `notify.restock` (`restock:<ch>:<outlet>:<pid>:<seq>`) / `notify.price_alert` (`price:<ch>:<pid>:<version>`) dalam
  transaksi yang sama dengan state. Kontrak menyebut publisher "catalog-sync": dipindah ke sini agar `src/catalog` tetap.
- **Dispatcher** (`bff.alert-dispatcher`): klaim `processed_message` + tandai `sent_at`/`triggered_at` dalam satu
  transaksi, kirim setelah commit (at-most-once). Push via Expo langsung; email/WA lewat port `EmailNotifier`/
  `WaUnofficialNotifier` BFF-B (`src/campaign/channel-ports.ts`, diikat di `channel-bindings.ts`). WA kategori
  `notify_restock`/`price_alert`, kedaluwarsa ≤ 24 jam. Publisher wajib `efn-bff`.
- Privasi (`wishlist-privacy.ts`): erasure hapus 3 tabel (FK CASCADE sebagai jaring kedua), DSAR export.

## Belum / catatan
- "Kontak terverifikasi" (§28.8) tidak dicek saat membuat alert (data email ada di BFF-B); notifier menjawab
  `skipped_no_email/phone` saat kirim.
- Push tidak lewat `notify.push` (consumer itu = WhatsApp Cloud API) — usulan kontrak di laporan.
- Q-SM25 (consent `marketing` untuk email/WA notify-me) ditegakkan di notifier BFF-B.
