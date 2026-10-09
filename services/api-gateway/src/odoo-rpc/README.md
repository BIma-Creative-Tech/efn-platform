# odoo-rpc — klien json2 Odoo untuk kanal online (Fase 3)

Kontrak: `docs/04` §10.4 (+ delta §10.4.4), §10.10, §10.11. ADR-034 (**Proposed**), ADR-022/024 (enkripsi & kredensial), ADR-023 (Odoo privat).

| Komponen | File | Catatan |
|---|---|---|
| 10 metode | `odoo-online.types.ts` (`ODOO_ONLINE_PATHS`) | Hanya 10 path persis = allowlist `odoo-rpc-gateway`. Tidak ada cara memanggil model/metode lain. |
| Klien | `json2-client.ts` | `POST {ODOO_RPC_URL}/json/2/<model>/<metode>`, HTTPS + **mTLS** (CA `ODOO_RPC_CA_FILE`, sertifikat klien `ODOO_RPC_CLIENT_CERT_FILE/KEY_FILE`, default `TLS_CERT_FILE/KEY_FILE`), verify-full, tanpa fallback plaintext. `Authorization: bearer <odoo_api_key_online_<kanal>>` — key **per kanal** (= per PT), placeholder `disabled` ditolak. Body = argumen bernama + `context:{lang:"id_ID"}` saja. Retry hanya jaringan/timeout/429/5xx, maks 3× (0,5/1/2 s + jitter); UserError/401/403/404 tidak di-retry. Circuit breaker per kanal (5 gagal → 30 s). |
| Validasi respons | `odoo-online.parse.ts` | Bentuk & tipe dicek; `amount_payable`/refund wajib bilangan bulat Rupiah; `image_sha` hex 40/64 (L-9); qty stok tidak diteruskan. Gagal → `invalid_response` → 503 + anomali `online_config_error`. |
| Peta error | `online-errors.ts` | §10.3.7 (K-ON5). Pesan generik; detail/traceback Odoo tidak pernah diteruskan/di-log. |
| Metrik & anomali | `online-metrics.ts` | Semua metrik §10.11 + `efn_online_refunds_open{status}`, `efn_online_backward_transitions_total{source}`; log `EFN_ONLINE_ANOMALY {json}` (logger `OnlineAnomaly`, field whitelist). |
| Stub dev | `odoo-online.mock.ts` | `ODOO_ONLINE_MOCK=true` **hanya** `APP_ENV=dev|test` (fail-closed: staging/production → gagal start, L-7). Data demo PLACEHOLDER: produk 13 → `stock_short` saat bayar, 14 → `out_of_stock` saat quote. |

Risiko: R-T10 (saga), R-S03 (Odoo publik — dicegah), R-T15 (API key kedaluwarsa → `efn_online_odoo_auth_failures_total`).

## Belum / usulan kontrak
- L-11: blok `channel` wajib dari E-28; tanpa blok kanal disembunyikan (fail-closed). E-28 belum mengirimnya per 9 Okt.
- Status HTTP json2 per jenis exception belum terverifikasi; klien mengandalkan `message` `efn_online:<kode>`.
