# cart — keranjang & quote online (Fase 3)

Kontrak: `docs/04` §10.3.3–10.3.4, §10.12. ADR-034, ADR-019 (**Proposed**).

- `GET|PUT|DELETE /v1/cart`, `POST /v1/cart/quote` — Bearer member, `no-store`, kill switch `ONLINE_ENABLED`, body ≤ 16 KB, rate limit per member (`ONLINE_RL_CART_*`, `ONLINE_RL_QUOTE_*`).
- Cart = Redis hash `cart:<member_id>` (`v` versi, `b` JSON grup), TTL 7 hari diperpanjang tiap tulis; **tanpa harga** (field lain ditolak 400). Tulis = compare-and-set atomik (Lua) → `409 cart_version_conflict`. Validasi kanal/outlet/fulfilment/produk dari salinan katalog; produk hilang dibuang + `warnings: item_unavailable`; qty ≤ `max_qty`.
- Quote: `efn_online_quote` per kanal (paralel, 8 s); harga/pajak/label apa adanya dari Odoo; baris quote wajib sama persis dengan cart (selain itu `invalid_response`). Disimpan `oq:<quote_id>` (TTL = min(`valid_until` Odoo, `ONLINE_QUOTE_TTL_SECONDS`)); `quote_hash` tidak pernah ke klien. Satu kanal gagal → `503 online_unavailable`.

Risiko: R-B (manipulasi harga — harga tidak pernah dari klien), R-T10. Belum: quote parsial bila satu kanal gagal (ditunda §10.14).
