# `src/behavior` — event perilaku visitor (G50-A)

**Tujuan.** Mencatat perilaku belanja (produk dilihat + durasi, wishlist, keranjang, pencarian, notify-me) **hanya dengan
consent `tracking`**, tanpa PII, sebagai bahan segmen kampanye (ADR-047). PostgreSQL staging 7 hari → ETL 15 menit →
ClickHouse (ADR-003; BFF tidak pernah menulis ClickHouse).

- **ADR:** ADR-047 (Proposed), ADR-003, ADR-022. **Kontrak:** `docs/04` §28.6, §28.7 (`behavior.event`), §28.10.
- **Risiko dimitigasi:** R-L13 (tracking/profiling: consent sebelum ID dibuat, pseudonim HMAC, retensi, erasure),
  R-L01 (PDP: DSAR export/erasure), R-S04 (rate limit device + IP, batas batch/ukuran, respons tidak membocorkan status
  member).
- **Flag:** `BEHAVIOR_TRACKING_ENABLED=false` (default semua env; **production OFF** sampai DPO: Q-SM16/17/26).
  `BEHAVIOR_ANON_VISIT_ENABLED=false` (V-01, default semua env) — log kunjungan anonim tanpa consent, efektif hanya
  bersama `BEHAVIOR_TRACKING_ENABLED=true`.
- **V-01 (keputusan pemilik proyek 23 Nov 2026):** "kunjungan visitor tidak masuk PDP" → kunjungan web/app dicatat
  **tanpa banner consent** sebagai subject anonim (lihat bagian V-01 di bawah). Penautan akun, segmen, dan kampanye tetap
  hanya untuk subject ber-consent `tracking`.

## Kontrak API
| Endpoint | Auth | Ringkas |
|---|---|---|
| `POST /v1/events/consent` | publik | `{device_id (uuid v4), granted, version}` → `202 {recorded, granted}`; grant hanya versi teks aktif (`CONSENT_TRACKING_VERSION`/`_TEXT_SHA256`) → `409 stale_text` / `503 consent_unavailable`; penarikan selalu diterima + hapus tautan device. Flag mati → `202 {recorded:false, disabled:true}`. Rate limit 30/menit/IP. |
| `POST /v1/events` | publik atau Bearer | Body §28.6 (≤ 50 event, ≤ 32 KB, `additionalProperties:false`, `client_ts` ±24 jam). `202 {accepted, duplicates, rejected[{index, code: invalid|stale|unknown_channel|unknown_product}], mode: consented|anonymous}`. Flag mati → `202 {accepted:0, disabled:true}`. Tanpa consent dan `visit_log` mati, atau device dengan baris erasure → `403 tracking_consent_required` (bentuk `{error:{code,message}}`). 429 + `Retry-After`. Broker mati → 503. V-01: tipe `page_view` + field `page`. |
| `GET /v1/config` | publik | `{features:{tracking, visit_log, wishlist, restock_alert, price_alert, campaign_inbox, notify_channels}, tracking_consent:{version}|null}`; `tracking` hanya true bila flag aktif **dan** teks consent dikonfigurasi; `visit_log` true bila `BEHAVIOR_TRACKING_ENABLED` **dan** `BEHAVIOR_ANON_VISIT_ENABLED` (tidak butuh teks consent). |

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

## V-01 — log kunjungan anonim (keputusan pemilik proyek 23 Nov 2026)
- **Gate** (`BehaviorService.resolveSubject`): consent device `granted` **untuk versi teks consent `tracking` aktif**
  (`CONSENT_TRACKING_VERSION`; SEC-V01-01) atau consent `tracking` akun → perilaku lama, `consented=true`. Consent device
  versi lama, atau teks aktif tidak dikonfigurasi → tidak menautkan member (anonim bila `visit_log` aktif, selain itu 403). Selain itu, bila `visit_log` aktif → subject anonim `anonSubjectKey = HMAC(behavior_device_pepper,
  "efn:anon:v1:" + device_id)` (domain berbeda dari `device_key`), `subject_kind=device`, `consented=false`, **Bearer
  diabaikan untuk identitas** (tidak ada `device_link`/`subject_member`, member id tidak pernah masuk payload). Bearer tanpa
  consent akun → anonim walau consent device ada (consent device tidak menjadi subject saat login, ADR-047 §3.3).
- **Penarikan vs erasure:** baris `device_consent granted=false` biasa → tetap boleh anonim (penarikan = berhenti
  penautan/profiling). Baris erasure akun (`version='erasure'`, SEC-G50-07) → `403` sampai consent baru. Flag mati → `403`
  lama.
- **Tipe:** anonim boleh semua tipe klien kecuali `wishlist_add|wishlist_remove|notify_me_request|price_alert_set`
  (fitur member) → `rejected invalid`. `page_view` wajib `page` ∈ `home|category|product|search|cart|checkout|promo|
  store|account|order|other` (enum, bukan URL); `product_id` hanya boleh bila `page=product` dan **diabaikan** (disimpan
  null — `product_view` tetap event utama produk, tidak ada hitung ganda/unknown_product). `page` di tipe lain → invalid.
- **Antrean & staging:** pesan `behavior.event` membawa `consented` (pesan lama tanpa field = `true`) dan `page` per event;
  consumer menolak (DLQ) anonim non-device, anonim + tipe fitur member, `page` tidak konsisten. `behavior.event_raw`
  kolom `consented boolean NOT NULL DEFAULT true`, `page text` + CHECK enum, CHECK `(type='page_view') = (page IS NOT
  NULL)`, CHECK anonim ⇒ device & bukan tipe member/turunan kampanye; grant ETL per kolom + `consented, page`.
- **Tidak dipakai segmen/kampanye/DSAR:** `cart_abandon` hanya dari `cart_add` `consented`; `purchase` hanya member
  ber-consent (`consented=true`). Segmen: jaminan = view segmen ClickHouse `v_seg_*` memfilter `consented = 1` + ETL
  gagal-tutup (milik efn-data-ai-engineer); ingest segmen BFF tidak memfilter (SEC-V01-05: kunci anonim beda domain HMAC,
  filter staging tidak pernah menolak apa pun), dan `anonSubjectKey` tidak pernah ada di `subject_member` sehingga
  dispatcher tidak dapat memetakannya ke member. DSAR export/erasure hanya memakai kunci dari `subject_member`/`device_link` + HMAC
  member → event anonim tidak tersentuh (tidak tertaut ke orang).
- Retensi sama (staging 7 hari, mentah ClickHouse 90 hari); IP/UA/URL/referrer tidak disimpan; query tetap `redactQuery`.
- **Risiko:** R-L13 (profiling: anonim tidak pernah ditautkan/segmen), R-L01 (keputusan pemilik bahwa kunjungan anonim di
  luar PDP belum dikonfirmasi DPO/Legal — pseudonim device tetap berpotensi data pribadi menurut ADR-047 §1.2), R-S04.

## Belum / catatan
- Consumer memakai koneksi AMQP `efn-bff` (kontrak §28.7 menyebut user `efn-behavior`) — usulan kontrak di laporan.
- `duplicates` di respons = duplikat dalam batch saja; duplikat lintas batch didedup consumer (tidak dilaporkan).
- Penghitung agregat tanpa pengenal untuk pengunjung tanpa consent (ADR-047 §3.1 no. 5) digantikan V-01 (log anonim
  ber-pseudonim); kontrak docs/04 §28.6 + ADR-047 belum diperbarui (architect).
- V-01: ETL (`services/etl/efn_etl/behavior.py` `EVENT_TYPES`, kolom `consented`/`page`), DDL ClickHouse, view segmen
  `consented = 1`, `deploy-validate.sh`/`check-encryption.sh` untuk flag baru → agent lain.
- Kelas rate limit CATALOG untuk burst (§28.6) belum diterapkan terpisah; batas event/menit device & IP berlaku.
- Job ETL `fact_visitor_event` + erasure ClickHouse milik efn-data-ai-engineer.
