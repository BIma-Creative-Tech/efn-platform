# segment-worker (G50)

Segmentasi harian perilaku visitor supermarket untuk **usulan** kampanye (marketing yang memutuskan di Odoo).

- **ADR:** ADR-047 (Proposed — tracking perilaku, segmen, kampanye), ADR-003 (ClickHouse turunan ETL), ADR-022 (TLS).
- **Kontrak:** `docs/04_SERVICE_CONTRACTS.md` §28.7 (anggota -> BFF, ringkasan -> Odoo), §28.10 (flag), L-153/L-155.
- **Risiko dimitigasi:** R-L13 (profiling/PDP: hanya subject_key pseudonim, member saja, ringkasan Odoo tanpa data member,
  k-anonim top produk), R-B17 (kampanye tetap butuh approval marketing), R-S05 (user ClickHouse read-only + view
  whitelist).
- **Status:** dibangun G50, `SEGMENT_ENABLED=false` default. **Production OFF** sampai DPO (Q-SM16/17/26) dan
  `PROMO_CAMPAIGN_ENABLED` disetujui. Belum diuji end-to-end (BFF `src/campaign` & Odoo `efn_campaign` dibangun paralel).

## Alur

```
ClickHouse efn_segment.v_* (user efn_segment_reader, readonly)
  -> segments.compute_segments (Python murni)
  -> BFF  POST /internal/v1/segments/{run_id}/members   (mTLS CN segment-worker; <= 5000 subject_key/chunk)
  -> Odoo POST /json/2/efn.campaign/efn_campaign_proposal_upsert (bearer API key PT; tanpa data member)
```

Per PT (`SEGMENT_COMPANIES`), sekali sehari (`SEGMENT_DAILY_CRON`, default `15 2 * * *` WIB, setelah agregat ETL), data
hari yang sudah selesai (as_of = kemarin WIB). Semua anggota satu segmen dikirim ke BFF **sebelum** ringkasan ke Odoo;
segmen yang gagal di BFF tidak dikirim ke Odoo. Retry = chunk identik (anggota diurutkan + dedup), idempoten per
`(run_id, segment_code, chunk_no)`. Run baru selalu `run_id` baru.

Body anggota (SEC-G50-16, = BFF `src/campaign/segment-ingest.service.ts`, urutan field sama):

```json
{ "schema_version": 1, "company_code": "SMD", "run_id": "<uuid4>", "segment_code": "viewed_not_bought",
  "subject_keys": ["<hex64>", "..."], "chunk_no": 0 }
```

`company_code` = PT run (wajib, ∈ COMPANY_CODES, ikut hash idempotensi BFF; satu `run_id` = satu PT). Respons BFF:
`200 {ok, run_id, segment_code, chunk_no, accepted, duplicate_chunk}` (duplikat = sukses); `400 invalid_request`,
`409 chunk_conflict`, `409 run_company_mismatch` -> segmen gagal, tidak diulang. Contoh valid/invalid bersama BFF (Jest)
dan worker (pytest): `tests/fixtures/segment_members_v1.json`, diuji `tests/test_members_fixture.py`.

## Segmen

| Kode | Aturan (default) |
|---|---|
| `viewed_not_bought` | 14 hari: dilihat >= 2 kali atau >= 30 detik, tanpa `purchase`/`checkout_start` produk itu |
| `wishlist_idle` | 60 hari: wishlist aktif yang ditambahkan >= 7 hari lalu, tanpa pembelian sesudahnya |
| `oos_viewed` | 14 hari: dilihat saat stok `out`, belum dibeli |
| `price_sensitive` | viewed_not_bought dengan harga dilihat > 1,05 x median Price Hunter 30 hari (`market`) atau > 1,05 x median harga beli member di kategori sama (>= 3 pembelian, `habit`) |
| `search_no_result` | 14 hari: >= 1 pencarian tanpa hasil |

Subjek `device` (belum tertaut akun) tidak dikirim kecuali `SEGMENT_INCLUDE_DEVICE=true`. Segmen < `SEGMENT_MIN_SIZE`
(10) dilewati. Ringkasan Odoo (bentuk = validator `efn_campaign`, QA G50 #1):

```json
{ "schema_version": 1, "company_code": "SMD", "run_id": "<uuid4>", "segment_code": "price_sensitive",
  "member_count": 25,
  "metrics": { "lookback_days": 14, "product_count": 3, "as_of_yyyymmdd": 20261119, "k_min": 10,
               "top_product_count": 2, "top_product_subjects": 35, "reason_habit": 15, "reason_market": 12 },
  "summary": "2 produk teratas (>= 10 subjek): id 10 (20), id 12 (15)",
  "generated_at": "2026-11-20T02:15:30Z" }
```

`metrics` hanya angka (<= 20 kunci `^[a-z][a-z0-9_]{0,39}$`, |nilai| < 1e12); `reason_*` hanya untuk
`price_sensitive`. `summary` <= 256 karakter, hanya produk dengan >= `SEGMENT_K_MIN` subjek; id/jumlah >= 6 digit tidak
ditulis (pola PII Odoo) dan dihitung `+n lainnya`. `generated_at` = waktu run (UTC). Paritas dengan source Odoo diuji
`tests/test_odoo_contract_parity.py`.

## Konfigurasi

| Env | Default |
|---|---|
| `SEGMENT_ENABLED` | `false` (idle tanpa secret) |
| `SEGMENT_COMPANIES`, `SEGMENT_CODES` | `SMD`, semua 5 kode |
| `SEGMENT_DAILY_CRON`, `SEGMENT_TZ` | `15 2 * * *`, `Asia/Jakarta` |
| `SEGMENT_LOOKBACK_DAYS`, `SEGMENT_WISHLIST_LOOKBACK_DAYS`, `SEGMENT_WISHLIST_IDLE_DAYS` | 14 (1..80), 60 (7..85), 7 |
| `SEGMENT_MIN_VIEWS`, `SEGMENT_MIN_VIEW_SECONDS`, `SEGMENT_PRICE_MARGIN`, `SEGMENT_MIN_CATEGORY_PURCHASES`, `SEGMENT_MIN_NO_RESULT` | 2, 30, 0.05, 3, 1 |
| `SEGMENT_MIN_SIZE`, `SEGMENT_K_MIN`, `SEGMENT_TOP_PRODUCTS`, `SEGMENT_CHUNK_SIZE`, `SEGMENT_INCLUDE_DEVICE` | 10, 10, 10, 5000 (maks), `false` |
| `CH_HOST`, `CH_PORT`, `CH_USER`, `CH_DATABASE`, `CH_PASSWORD_FILE`, `CH_CA_FILE` | `clickhouse`, 8443, `efn_segment_reader`, `efn_segment`, secret `clickhouse_password_segment`, `/run/secrets/efn_ca_crt` |
| `SEGMENT_BFF_URL`, `SEGMENT_BFF_CA_FILE`, `SEGMENT_BFF_TIMEOUT_SECONDS`, `SEGMENT_BFF_MAX_RETRIES` | `https://api-gateway:3000`, CA internal, 25, 6 |
| `ODOO_RPC_URL`, `ODOO_RPC_CA_FILE`, `ODOO_DB`, `ODOO_RPC_TIMEOUT_SECONDS`, `ODOO_RPC_MAX_RETRIES` | `https://odoo-rpc-gateway:8443`, CA internal, —, 25, 6 |
| `SEGMENT_CLIENT_CERT_FILE`, `SEGMENT_CLIENT_KEY_FILE` | `/run/secrets/tls_segment_worker_crt`, `/run/secrets/tls_segment_worker_key` (CN `segment-worker`) |
| `ODOO_API_KEY_SEGMENT_<KODE>_FILE` | `/run/secrets/odoo_api_key_segment_<kode>` (user teknis `efn_segment_<kode>`, satu company; terpisah dari user BFF `efn_campaign_<kode>` — keputusan devsecops G50) |

Plaintext (`http://`, `CH_SECURE=false`) hanya `EFN_ALLOW_PLAINTEXT=true` + `APP_ENV` dev/test.

## Perintah

```
python -m efn_segment run                 # loop terjadwal
python -m efn_segment run --once          # sekali, semua PT (butuh SEGMENT_ENABLED=true)
python -m efn_segment run --dry-run       # hitung + validasi tanpa BFF/Odoo
```

## Dependency & test

`requirements.txt` (pin langsung) -> `requirements.lock` ber-hash (dibuat di container:
`pip-compile --generate-hashes --allow-unsafe --output-file=requirements.lock requirements.txt`); Dockerfile memasang
dengan `--require-hashes`. Test: `pip install -r requirements-dev.txt && pytest` (tanpa ClickHouse/BFF/Odoo).

## Belum / batasan

- Bentuk ringkasan Odoo mengikuti validator `efn_campaign` (diuji paritas); kode segmen dan respons BFF masih asumsi
  G50 (kontrak §28.7 belum merinci) — lihat `docs/agent-reports/efn-data-ai-engineer.md` bagian G50.
- `purchase` (event server) belum ada di kontrak §28.6; tanpa itu "sudah beli" hanya dari `checkout_start`.
- Pembanding Price Hunter kosong sampai laporan Price Hunter dipetakan ke SKU (`competitor_prices.product_sku`).
- Belum ada metrik Prometheus; pantau log `EFN_SEGMENT_RUN` (status `failed`/`partial`).
