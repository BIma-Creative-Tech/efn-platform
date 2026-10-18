# cart — keranjang & quote online (Fase 3)

Kontrak: `docs/04` §10.3.3–10.3.4, §10.12, **§11.2 (G19, menang atas §10)**. ADR-034, ADR-019 (**Proposed**).

- `GET|PUT|DELETE /v1/cart`, `POST /v1/cart/quote` — Bearer member, `no-store`, kill switch `ONLINE_ENABLED`, body ≤ 16 KB, rate limit per member (`ONLINE_RL_CART_*`, `ONLINE_RL_QUOTE_*`).
- Cart = Redis hash `cart:<member_id>` (`v` versi, `b` JSON grup), TTL 7 hari diperpanjang tiap tulis; **tanpa harga** (field lain ditolak 400). Tulis = compare-and-set atomik (Lua) → `409 cart_version_conflict`. Validasi kanal/outlet/fulfilment/produk dari salinan katalog; produk hilang dibuang + `warnings: item_unavailable`; qty ≤ `max_qty`. Cart **tidak pernah** menerima baris reward (hanya `product_id` + `qty` produk katalog).
- Quote: `efn_online_quote` per kanal (paralel, 8 s); harga/pajak/label apa adanya dari Odoo; baris **produk** quote wajib sama persis dengan cart (selain itu `invalid_response`). Disimpan `oq:<quote_id>` (TTL = min(`valid_until` Odoo, `ONLINE_QUOTE_TTL_SECONDS`)); `quote_hash` tidak pernah ke klien. Satu kanal gagal → `503 online_unavailable`.

## Promo online (G19, W-09/E-36, §11.2)

| Hal | Perilaku |
|---|---|
| Body | `{}` atau `{ "promo_codes": ["KODE1"] }` — maks 3 elemen (dihitung sebelum dedupe), tiap kode `^[A-Za-z0-9_-]{1,40}$`, dinormalisasi HURUF BESAR, duplikat dibuang. Format salah atau kunci lain → `400 invalid_request`. |
| Ke Odoo | `promo_codes` yang sama dikirim ke **setiap** kanal (`[]` eksplisit bila tanpa kode). Odoo hanya menerapkan program pricelist kanal itu. |
| Validasi hasil Odoo | `promo_applied`/`promo_rejected` harus ⊆ kode yang diminta dan tidak beririsan; baris reward ≤ 0 dan ≤ 20 per grup; selain itu `503 online_unavailable` + anomali `online_config_error` (fail-closed). |
| Respons | `promo: { applied, invalid }`; `invalid` = tidak terpakai di grup **mana pun** — satu kategori untuk semua penyebab (anti-enumerasi). Kode invalid **tidak** memblokir checkout (bukan `issues`). Baris reward diteruskan apa adanya dengan `reward: true`. |
| Redis `oq:<id>` | + `promo_codes` (ternormalisasi) dan per grup `promo_applied`; place mengirim `promo_applied` grup itu (bukan dari klien) + hanya baris produk. Hash beda di place → `409 price_changed`. Quote pra-G19 tanpa field → `[]`. |
| Rate limit | **per KODE** (SEC-134/L-45): quote dengan n kode (setelah dedupe) mengonsumsi n unit di `ONLINE_RL_PROMO_MEMBER` (10 kode / 900 s / member) dan `ONLINE_RL_PROMO_IP` (30 kode / 900 s / IP ber-HMAC) → `429 rate_limited`. Permintaan yang ditolak tidak memakan kuota; IP menolak → unit member dikembalikan. Validasi format dulu (tanpa memakan kuota). |
| Observability | `efn_online_promo_codes_total{result="applied\|invalid"}` tanpa label kode. Kode **tidak pernah** di-log, termasuk hash/tag-nya (SEC-132/L-42: tag SHA-256 tanpa kunci dihapus); log debug hanya memuat jumlah terpakai/tidak terpakai. |
| Issue `qty_limit` | SEC-131: Odoo menandai baris dengan qty > batas per baris kanal sebagai `issues: [{ product_id, code: "qty_limit" }]` (diteruskan apa adanya ke klien, seperti `out_of_stock`). Checkout → `409 cart_has_issues`. Baris ber-`qty_limit` boleh dikembalikan Odoo dengan qty dipangkas (< qty cart); selain itu baris ≠ cart tetap `503`. |

Kontrak respons quote (klien):

```json
{ "quote_id": "<uuid>", "valid_until": "<iso>", "cart_version": 7,
  "groups": [{ "channel_code": "RTL-GL", "company_code": "RTL", "outlet_code": "TOKO-01", "fulfilment": "pickup",
    "lines": [
      { "product_id": 11, "name": "Beras", "qty": 1, "price_unit_incl": 79900, "subtotal_incl": 79900, "tax_labels": ["PPN 11%"], "available": true },
      { "product_id": 9001, "name": "Promo Gajian", "qty": 1, "price_unit_incl": -5000, "subtotal_incl": -5000, "tax_labels": ["PPN 11%"], "available": true, "reward": true }
    ],
    "tax_groups": [{ "label": "PPN 11%", "base": 67477.48, "amount": 7422.52 }],
    "amount_total": 74900, "amount_payable": 74900, "issues": [] }],
  "amount_payable_total": 74900,
  "promo": { "applied": ["GAJIAN"], "invalid": ["NOPE42"] } }
```

Risiko: R-B (manipulasi harga — harga & diskon tidak pernah dari klien), R-B06/R-B03 (penyalahgunaan promo/kupon — rate limit terpisah, anti-enumerasi, kupon terkonsumsi saat place di Odoo), R-T10.

Belum / asumsi: quote parsial bila satu kanal gagal (ditunda §10.14). Normalisasi huruf besar mengasumsikan Odoo mencocokkan kode **tanpa membedakan huruf besar/kecil** atau membuat kode huruf besar (kode kupon bawaan Odoo berhuruf kecil — dicek builder Odoo E-36). Refund CS per baris memakai harga baris produk (diskon reward tidak dibagi ke baris) sebagai perkiraan pre-check; nominal final tetap dari nota kredit Odoo.
