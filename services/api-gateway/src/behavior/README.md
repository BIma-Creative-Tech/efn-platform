# `src/behavior` — event perilaku visitor (G50-A)

**Tujuan.** Mencatat perilaku belanja (produk dilihat + durasi, wishlist, keranjang, pencarian, notify-me) **hanya dengan
consent `tracking`**, tanpa PII, sebagai bahan segmen kampanye (ADR-047). PostgreSQL staging 7 hari → ETL 15 menit →
ClickHouse (ADR-003; BFF tidak pernah menulis ClickHouse).

- **ADR:** ADR-047 (Proposed), ADR-003, ADR-022. **Kontrak:** `docs/04` §28.6, §28.7 (`behavior.event`), §28.10.
- **Risiko dimitigasi:** R-L13 (tracking/profiling: consent sebelum ID dibuat, pseudonim HMAC, retensi, erasure),
  R-L01 (PDP: DSAR export/erasure), R-S04 (rate limit device + IP, batas batch/ukuran, respons tidak membocorkan status
  member).
- **Flag:** `BEHAVIOR_TRACKING_ENABLED=false` (default semua env; **production OFF** sampai DPO: Q-SM16/17/26).

## Kontrak API
| Endpoint | Auth | Ringkas |
|---|---|---|
| `POST /v1/events/consent` | publik | `{device_id (uuid v4), granted, version}` → `202 {recorded, granted}`; grant hanya versi teks aktif (`CONSENT_TRACKING_VERSION`/`_TEXT_SHA256`) → `409 stale_text` / `503 consent_unavailable`; penarikan selalu diterima + hapus tautan device. Flag mati → `202 {recorded:false, disabled:true}`. Rate limit 30/menit/IP. |
| `POST /v1/events` | publik atau Bearer | Body §28.6 (≤ 50 event, ≤ 32 KB, `additionalProperties:false`, `client_ts` ±24 jam). `202 {accepted, duplicates, rejected[{index, code: invalid|stale|unknown_channel|unknown_product}]}`. Flag mati → `202 {accepted:0, disabled:true}`. Tanpa consent → `403 tracking_consent_required` (bentuk `{error:{code,message}}`). 429 + `Retry-After`. Broker mati → 503. |
| `GET /v1/config` | publik | `{features:{tracking, wishlist, restock_alert, price_alert, campaign_inbox}, tracking_consent:{version}|null}`; `tracking` hanya true bila flag aktif **dan** teks consent dikonfigurasi. |

## Desain
- `device_key = HMAC(behavior_device_pepper, device_id)`, `subject_key = HMAC(behavior_subject_pepper, member_id)`
  (`subject-key.ts`, prefix domain, pepper wajib berbeda). Device ID mentah, IP, user agent **tidak** disimpan/di-publish
  (IP hanya kunci rate limit ter-hash `redisKeyHash`).
- Consent gate: Bearer → consent `tracking` **akun** (device consent tidak otomatis jadi consent akun); tanpa Bearer →
  baris `behavior.device_consent` terbaru `granted`. Device tertaut ke member ber-consent → subject member.
- Pengayaan server: `price_idr` dari salinan katalog (`platform.online_catalog_item`), `stock_status` dari cache Redis
  `ocat:stk:*` (tidak memanggil Odoo). Produk tidak ada di kanal → `unknown_product`.
- **PT per event** (`company_code`, revisi G50-A untuk ETL): pemetaan kanal → PT = blok `channel` Odoo yang disalin
  `CatalogSyncService` ke `platform.online_catalog_sync.channel_data` (`channel-company.ts`; syarat sama dengan katalog
  L-11: `code` = kanal, `active`, PT ∈ `COMPANY_CODES`). Kanal tak terpetakan → `unknown_channel` (tidak pernah disimpan
  tanpa PT, tidak ditebak dari kode kanal). Writer menurunkan ulang PT saat tulis; kanal yang sementara hilang dari
  salinan memakai PT tervalidasi dari pesan.
- Query pencarian: NFKC + lowercase, email & rangkaian ≥ 6 digit (boleh berpemisah) → `[redacted]` (`redactQuery`).
- Consumer `bff.behavior-writer`: `processed_message` + dedup `(subject_key, event_id)` di `behavior.event_dedup`
  dalam satu transaksi; insert batch `unnest` ke `behavior.event_raw` (partisi harian UTC). `ingested_at` = DEFAULT
  `clock_timestamp()` (waktu masuk staging) — cursor job ETL `behavior_events` `(ingested_at, subject_key||':'||event_id)`,
  index `(ingested_at)` di tabel induk.
- Job (`BehaviorJobsService`, 1 jam, advisory lock): partisi (fungsi SECURITY DEFINER), drop partisi > 7 hari, retensi
  dedup/tautan (180 h)/consent device (400 h, placeholder)/segmen (30 h)/`erased_subject` + `subject_erasure` (≥ 100 h,
  minimum konfigurasi 100); **`cart_abandon`** diturunkan server (cart_add terakhir 24–48 jam tanpa checkout_start;
  `event_id` UUID v8 deterministik; `channel` web|app, kanal & PT disalin dari cart_add — ETL menolak channel lain).
- Privasi (`behavior-privacy.ts`, dipanggil `src/privacy`): erasure menulis SEMUA subject_key subjek (HMAC member +
  setiap device_key tertaut) ke **`behavior.subject_erasure`** (`request_id` DSAR) — antrean job ETL `behavior_erasure`
  dan `BehaviorDsarHook` (L-154) — di transaksi ErasureService yang sama dengan transisi `dsar_request` → `processing`
  (atomik), lalu hapus staging/dedup/segmen/tautan dan catat `behavior.erased_subject` (BFF: tolak subjek terhapus di
  ingest segmen). DSAR export tautan perangkat (tanpa device_key) + event staging; cabut consent `tracking` akun → hapus
  tautan & pemetaan subject.
- **SEC-G50-07:** erasure juga menambah baris `device_consent (granted=false, version 'erasure', teks tidak diketahui)` untuk
  setiap device tertaut → event berikutnya dari perangkat itu `403 tracking_consent_required` sampai consent baru di banner.
- **Event server `purchase`** (QA G50 #7, `purchase-events.service.ts`, tiap 5 menit, advisory lock): bagian order online
  lunas & dikonfirmasi (`online_order_part.confirmed_at`, status `confirmed|ready|handed_over`, ≤ 48 jam) → satu event per
  produk (baris reward/qty 0 dibuang) untuk subject MEMBER, hanya bila consent `tracking` akun aktif; kanal/PT/outlet dari
  bagian order; lewat outbox → `behavior.event` → writer (jalur sama, `event_id`/`batch_id` UUID v8 deterministik,
  penanda `behavior.purchase_emitted`). `channel` = channel event terakhir subject di staging, tanpa jejak → `app`
  (ASUMSI: order online tidak menyimpan asal web/app; usulan kontrak: kolom asal di `online_order`). **Pembelian POS belum
  tercakup.** CHECK `event_raw.type` memuat `purchase` (ditulis ulang idempoten di migrasi 20).
- Grant ETL (di `20-platform-g50a.sql`, = `services/etl/sql/platform-behavior-grants.sql`): `efn_etl` SELECT per kolom
  `event_raw` (tanpa `batch_id`/`client_ts`) dan `subject_erasure` (`id, subject_key, request_id, created_at`); tanpa akses
  `device_link`/`device_consent`/`subject_member`/`erased_subject`.

## Belum / catatan
- Consumer memakai koneksi AMQP `efn-bff` (kontrak §28.7 menyebut user `efn-behavior`) — usulan kontrak di laporan.
- `duplicates` di respons = duplikat dalam batch saja; duplikat lintas batch didedup consumer (tidak dilaporkan).
- Penghitung agregat tanpa pengenal untuk pengunjung tanpa consent (ADR-047 §3.1 no. 5) belum dibangun (menunggu Q-SM16).
- Kelas rate limit CATALOG untuk burst (§28.6) belum diterapkan terpisah; batas event/menit device & IP berlaku.
- Job ETL `fact_visitor_event` + erasure ClickHouse milik efn-data-ai-engineer.
