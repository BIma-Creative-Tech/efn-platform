# Security & PDP Review — V-01 Log Kunjungan Visitor Anonim

- **Tanggal:** 23 Nov 2026
- **Reviewer:** efn-security-reviewer (read-only)
- **Cakupan:** worktree `efn-platform-visitor` branch `feat/visitor-log` vs `origin/main` (BFF `src/behavior/*`, `campaign/segment-ingest.service.ts`, PG `20-platform-g50a.sql`, ClickHouse `16-g50-behavior.sql`, `23-v01-visitor-insight.sql`, `access/insight-visitor-*`, `segment-access.sql`, `clickhouse-init.sh`, ETL `efn_etl/behavior.py`, insight-web `lib/{visitor,scope,clickhouse,queries-visitor,ch-params-visitor}.ts` + `app/(vis)/*`, superapp-web `lib/tracking*.ts`, `components/engage/*`, `app/api/events/route.ts`) dan repo `efn-mobile` branch `feat/visitor-tracking` (`apps/superapp/src/lib/visits.ts`, `src/state/visits.ts`).
- **Acuan:** kontrak V-01 (scratchpad lead), ADR-047 §6b, docs/04 §28.6/§28.6a, CLAUDE.md.
- **Tidak di-review ulang:** keputusan pemilik proyek "kunjungan anonim dicatat tanpa consent" (diterima apa adanya). Batasan: review statis, tidak ada test yang dijalankan.

## Ringkasan eksekutif

Batas V-01 di sisi server sudah ditegakkan dengan baik dan berlapis. Subjek anonim memakai domain HMAC terpisah, tidak pernah membuat `device_link`/`subject_member`, dan dicegah oleh CHECK PG, validasi consumer, serta fail-closed ETL. View segmen memfilter `consented = 1`, erasure tetap 403, dan Insight `/visitors` memakai row policy bertanda tangan sendiri. Ada **satu temuan Medium** di klien web: saat versi teks consent naik, browser yang pernah setuju mengirim event "anonim" memakai **device_id consent**. Akibatnya BFF menautkan event itu ke device/member ber-consent, berlawanan dengan janji UI dan batas ADR-047 §6b butir 2. Sisanya Low/Info: urutan deploy ETL, pengenal anonim yang persisten (R-L13), k-anonim yang bisa dibobol dengan banyak device_id palsu, performa filter segmen, dan pemberitahuan di app. **Tidak ada temuan High.** Rekomendasi: perbaiki SEC-V01-01 sebelum demo memakai browser yang pernah memberi consent.

## Tabel temuan

| ID | Severity | Judul | Lokasi utama | Risiko |
|---|---|---|---|---|
| SEC-V01-01 | **Medium** | Event "anonim" web memakai device_id consent saat versi teks berubah, sehingga tertaut ke member | `services/superapp-web/lib/tracking-client.ts:108-111,153-160,222-227`; `services/api-gateway/src/behavior/behavior.service.ts:191-211` | R-L13, R-L01 |
| SEC-V01-02 | Low | `fact_visitor_event.consented DEFAULT 1` + urutan demo "BFF dulu", sehingga ETL lama bisa memuat baris anonim sebagai consented | `infra/clickhouse/init/16-g50-behavior.sql:49,64`; `services/etl/README.md:313-321` | R-L13 |
| SEC-V01-03 | Low | Pengenal anonim persisten (web 180 hari, app tanpa kedaluwarsa/bertahan reinstall iOS), padahal Insight hanya butuh satuan perangkat-hari | `services/api-gateway/src/behavior/subject-key.ts:20-22`; `efn-mobile/apps/superapp/src/state/visits.ts:37-57` | R-L13 |
| SEC-V01-04 | Low | k-anonim dapat dibobol dengan banyak device_id palsu (Sybil); untuk produk, k dihitung atas Σ perangkat-hari | `behavior.py:296-309`; `services/insight-web/lib/queries-visitor.ts:76-97`; `behavior.service.ts:104-114` | R-L13, R-S04 |
| SEC-V01-05 | Low (PLAUSIBLE) | Filter pertahanan berlapis segment-ingest `NOT EXISTS event_raw ... NOT consented` (char(64) vs text) berpotensi scan 7 hari staging per chunk | `services/api-gateway/src/campaign/segment-ingest.service.ts:123-128` | R-S04 (ketersediaan) |
| SEC-V01-06 | Low | App mobile mencatat kunjungan tanpa pemberitahuan apa pun di app (web punya teks di banner/`/privasi`) | `efn-mobile/apps/superapp/app/_layout.tsx:45-47` | R-L13 |
| SEC-V01-07 | Low (PLAUSIBLE) | Rate limit per IP tanpa agregasi prefix IPv6 (/64), sehingga jalur anonim tanpa prasyarat lebih mudah dibanjiri | `behavior.service.ts:107` | R-S04 |
| SEC-V01-08 | Info | Funnel Insight mencampur viewer anonim dengan `purchase` yang hanya dari member consented, sehingga konversi bias rendah | `behavior.py:277-293`; `purchase-events.service.ts:76-90` | — |

---

## Detail temuan

### SEC-V01-01 — Medium — Event "anonim" web memakai device_id consent sehingga tertaut ke member

**Lokasi**
- `services/superapp-web/lib/tracking-client.ts:108-111` (`readDeviceId` membaca `efn_did`, satu-satunya kunci ID), `:153-160` (`ensureDeviceId` memakai ulang ID yang ada), `:222-227` (`trackEvent`: bila `!isTrackingActive()` dan `visitLog` aktif, ID yang ada dipakai untuk mode anonim).
- `services/superapp-web/lib/tracking.ts:75-77` (`trackingActive` = false bila `choice.version !== TRACKING_TEXT.version`).
- `services/api-gateway/src/behavior/behavior.service.ts:191-211` (tanpa Bearer, baris `device_consent` terbaru `granted=true` dari versi teks mana pun menghasilkan subjek **consented**, lalu `device_link` ke member yang consent akunnya aktif, lalu subjek **member**).

**Skenario kegagalan**
1. Pengguna web setuju consent `tracking@2026-11-20`. Cookie `efn_tc=1.tracking@2026-11-20` dan `efn_did=D` (ID consent) tersimpan, dan saat login ber-consent akun BFF menulis `device_link(member M, HMAC(D))`.
2. Teks consent naik versi (`TRACKING_TEXT.version` baru, wajib setiap kali `body` diubah). `trackingActive()` menjadi false dan banner tampil lagi dengan teks V-01: "catatan anonim ini tidak dikaitkan dengan akun".
3. Selama pengguna belum memilih (atau mengabaikan banner), `trackEvent` mengambil jalur anonim. `ensureDeviceId()` mengembalikan **D** (ID consent lama) dan event dikirim tanpa `?link=1`.
4. BFF: tanpa Bearer, `device_consent` terbaru untuk HMAC(D) = `granted=true`. `device_link` ditemukan dan consent akun M aktif, sehingga subjek = `memberSubjectKey(M)`, `consented=true`. Event masuk profil member, segmen, dan kampanye. Respons `mode:"consented"` diabaikan klien.

Kasus serupa terjadi bila cookie/localStorage pilihan `efn_tc` hilang sementara `efn_did` masih ada (pembersihan parsial). Sebelum V-01, klien diam pada kondisi ini (tidak mengirim apa pun). V-01 membuatnya mengirim dengan identitas consent. Ini melanggar ADR-047 §6b butir 2 ("tidak digabung dengan subjek consent") dari sudut pandang pengguna: pengguna diberi tahu sedang anonim, padahal datanya tertaut ke akun.

**Rekomendasi**
- Primer (klien): pisahkan penyimpanan. ID anonim di kunci sendiri (mis. `efn_vid`, cookie + localStorage, 180 hari), dan ID consent tetap `efn_did`. `trackEvent` mode anonim **hanya** memakai `efn_vid`, tidak pernah `efn_did`. Saat `grantDeviceConsent` tidak ada perubahan (ID consent tetap baru). Saat `declineDeviceConsent` hapus `efn_did` saja.
- Tambahan: di `flush`, bila server membalas `mode` berbeda dari mode yang dimaksud klien (mis. klien anonim tetapi `mode:"consented"`), kosongkan antrean dan putar ulang ID anonim.
- Opsional (server, perlu konfirmasi kebijakan): perlakukan consent device dengan `version` yang bukan versi aktif sebagai belum-memilih untuk penautan member (jalur `device_link`), sama seperti klien.
- Test: unit `tracking-client` "choice granted versi lama + visit_log, event anonim memakai ID ≠ efn_did" dan spec BFF yang mendokumentasikan perilaku versi lama.

---

### SEC-V01-02 — Low — Default ClickHouse `consented = 1` + urutan demo "BFF dulu"

**Lokasi:** `infra/clickhouse/init/16-g50-behavior.sql:49,64` (`consented UInt8 DEFAULT 1`), `services/etl/README.md:313-321` (langkah 1 menyalakan BFF, langkah 4 menyebut migrasi sebelum "ETL baru").

**Skenario:** DDL 16 dan migrasi PG 20 sudah jalan dan flag BFF aktif, tetapi image ETL masih versi pra-V-01 (deploy parsial atau rollback ETL). ETL lama memilih kolom eksplisit tanpa `consented` dan melakukan INSERT tanpa kolom itu, sehingga ClickHouse mengisi `DEFAULT 1`. Baris anonim `product_view`/`search`/`cart_add` masuk `v_seg_*` sebagai consented dan bisa terbaca segment-worker. Paparan terbatas karena ETL lama menolak `page_view` (tipe tak dikenal) sehingga halaman pertama yang memuat `page_view` membatalkan job. Namun baris anonim pada halaman yang tidak memuat `page_view` sudah termuat.

**Rekomendasi:** (a) Ubah urutan runbook: ETL V-01 + DDL + migrasi dulu, flag `BEHAVIOR_ANON_VISIT_ENABLED` terakhir, dan cabut flag lebih dulu saat rollback ETL. (b) Pertimbangkan `ALTER TABLE ... MATERIALIZE COLUMN consented` lalu `MODIFY COLUMN consented UInt8 DEFAULT 0`, agar INSERT yang lupa kolom menjadi fail-safe (baris lama tetap 1 setelah dimaterialisasi). (c) Opsional: segment-worker menolak run bila `max(ingested_at)` baris `consented=0` = 0 padahal flag aktif (indikasi ETL lama).

---

### SEC-V01-03 — Low — Pengenal anonim persisten, padahal Insight hanya butuh perangkat-hari

**Lokasi:** `services/api-gateway/src/behavior/subject-key.ts:20-22` (`anonSubjectKey` deterministik per device_id), `services/superapp-web/lib/tracking-client.ts:31` (cookie 180 hari), `efn-mobile/apps/superapp/src/state/visits.ts:37-57` (SecureStore tanpa kedaluwarsa; di iOS Keychain bertahan setelah uninstall).

**Skenario:** `fact_visitor_event` menyimpan 90 hari riwayat per `subject_key` anonim yang stabil, termasuk istilah pencarian yang bisa memuat nama/alamat (redaksi hanya email dan ≥6 digit). Riwayat ini memungkinkan *singling out* (R-L13, sudah diakui ADR-047 §6b) dan melemahkan klaim "anonim" bila DPO meninjau (Q-SM48). Di app, pengenal bahkan lebih persisten daripada web.

**Rekomendasi (tanpa membuka ulang keputusan):** semua metrik Insight V-01 sudah berbasis **perangkat-hari** (`uniqExact` per `event_date`), jadi kunci anonim bisa dirotasi harian tanpa kehilangan fungsi: `anonSubjectKey = HMAC(pepper, "efn:anon:v1:" + tanggal_WIB + ":" + device_id)`. Efeknya, riwayat lintas hari tidak dapat disambung. Bila kontinuitas lintas hari tidak dibutuhkan, cukup ini saja. Di app, beri batas umur ID (mis. rotasi 180 hari seperti web) dan simpan di storage yang tidak ikut Keychain persisten (atau `keychainAccessible` + hapus saat reinstall). Catat di R-L13 sebagai mitigasi.

---

### SEC-V01-04 — Low — k-anonim dapat dibobol dengan device_id palsu; produk k atas Σ perangkat-hari

**Lokasi:** `services/etl/efn_etl/behavior.py:296-309` (`HAVING uniqExact(subject_key) >= k`), `services/insight-web/lib/queries-visitor.ts:76-83` (`HAVING viewers_n >= 10`, `viewers_n` = Σ subjek-hari × outlet), `:84-97`; `behavior.service.ts:104-114` (device_id bebas dipilih klien; batas per IP 600 event/menit).

**Skenario:**
1. *Poisoning:* penyerang mengirim `search` berisi teks sembarang (≤64 karakter, mis. ujaran ofensif atau domain phishing) dari 10 `device_id` acak dalam satu menit dari satu IP. Teks itu lolos k-anonim dan tampil di "Pencarian teratas/tanpa hasil" Insight untuk manajemen (React meng-escape, tanpa CSV, jadi tidak ada XSS atau formula injection). Hal yang sama berlaku untuk menggelembungkan jumlah pengunjung atau views kanal PT mana pun. Sebelum V-01 penyerang juga perlu `POST /v1/events/consent` per device (sama-sama murah), jadi V-01 hanya mempermudah.
2. *Privasi:* untuk produk, satu perangkat yang melihat produk 10 hari berbeda (atau di 10 `outlet_code` berbeda, yang tidak divalidasi terhadap outlet nyata) sudah memenuhi `viewers_n >= 10`. Risikonya rendah karena tidak ada identitas yang ditampilkan.

**Rekomendasi:** dokumentasikan bahwa k-anonim melindungi terhadap re-identifikasi, bukan terhadap injeksi. Untuk Insight, filter istilah dengan pola URL/domain (`\.[a-z]{2,}`) dan pertimbangkan k berbasis `uniqExact` lintas periode (butuh agregat tanpa subject_key, mis. HLL `uniqState` per hari). Validasi `outlet_code` terhadap katalog kanal di `enrich` (tolak `unknown_outlet`). Pertimbangkan batas jumlah device_id baru per IP per jam.

---

### SEC-V01-05 — Low (PLAUSIBLE) — Filter `NOT EXISTS event_raw ... NOT consented` di segment-ingest

**Lokasi:** `services/api-gateway/src/campaign/segment-ingest.service.ts:123-128`, index `infra/postgres/init/20-platform-g50a.sql:117` (`ix_event_raw_subject (subject_key char(64), received_at)`).

**Skenario:** `k` bertipe `text` (dari `unnest($3::text[])`), sedangkan `subject_key` bertipe `char(64)`. PostgreSQL memilih `text = text` (bpchar di-cast), sehingga index bpchar kemungkinan tidak dipakai. Dengan V-01, mayoritas baris staging anonim. Planner kemungkinan memilih hash anti-join yang men-scan seluruh baris `NOT consented` 7 hari per chunk, di dalam transaksi yang memegang advisory lock per run. Filter ini juga secara logis tidak pernah menolak apa pun, karena kunci anonim tidak mungkin keluar dari view `consented = 1`.

**Rekomendasi:** cast eksplisit `$3::char(64)[]` / `r.subject_key = k::char(64)` (pola sama untuk `erased_subject`), atau hapus filter staging dan andalkan jaminan utama (view CH + domain HMAC). Verifikasi dengan `EXPLAIN` pada data demo.

---

### SEC-V01-06 — Low — App mobile tanpa pemberitahuan log kunjungan

**Lokasi:** `efn-mobile/apps/superapp/app/_layout.tsx:45-47` (`initVisits()` saat start), tidak ada teks di layar privasi app (grep "anonim/kunjungan" hanya mengenai modul visits).

**Skenario:** web menampilkan `VISIT_LOG_TEXT` di banner, di `/privasi`, dan di `/privasi/pelacakan`, sedangkan app mengumpulkan device_id dan riwayat kunjungan tanpa pemberitahuan sama sekali. ADR-047 §6b R-L13 meminta DPO mempertimbangkan pemberitahuan di kebijakan privasi. Selain itu, label privasi App Store dan Data Safety Google Play wajib menyebut "Device or other IDs — Analytics".

**Rekomendasi:** tambahkan paragraf `VISIT_LOG_TEXT.notice` di layar privasi app dan masukkan ke checklist rilis (label toko). Kebijakan consent tidak berubah.

---

### SEC-V01-07 — Low (PLAUSIBLE) — Rate limit per IP tanpa agregasi IPv6

**Lokasi:** `services/api-gateway/src/behavior/behavior.service.ts:107` (`redisKeyHash(pepper, ip)` atas alamat penuh).

**Skenario:** klien IPv6 dengan /64 bisa memutar alamat tanpa batas, sehingga batas 600 event/menit/IP praktis hilang. Jalur anonim tidak butuh prasyarat apa pun dan menulis ke staging, ClickHouse, dan agregat. Pola ini sudah ada sejak G50, tetapi V-01 memperbesar permukaannya.

**Rekomendasi:** normalisasi IPv6 ke prefix /64 sebelum hash untuk kunci rate limit (berlaku juga untuk `beh:consent:ip`). Tambahkan batas global per menit untuk event anonim (circuit breaker) bila volume melampaui ambang ADR-047 §7.

---

### SEC-V01-08 — Info — Konversi funnel bias

`purchase` hanya diterbitkan untuk member ber-consent `tracking` (`purchase-events.service.ts:85-86`), sedangkan `viewers`/`carters` mencakup anonim (`behavior.py:277-293`). Akibatnya "Konversi lihat produk ke beli" di `/visitors` bias rendah. Ini bukan isu keamanan. Beri label di UI atau hitung konversi hanya atas subjek consented.

---

## Kontrol yang sudah benar (diverifikasi)

**(1) Tidak tertaut ke member**
- `anonSubjectKey` memakai domain `efn:anon:v1:` yang berbeda dari `efn:device:v1:`, dan pepper subjek member terpisah (`subject-key.ts`).
- Jalur anonim tidak menulis `device_link`/`subject_member`. Bearer tanpa consent akun diabaikan untuk identitas (`behavior.service.ts:188-198`).
- Pertahanan berlapis: consumer menolak `consented=false` dengan `subject_kind≠device` atau tipe member (`behavior-message.ts:81-84,109`), CHECK PG `event_raw_anon_check` (`20-platform-g50a.sql:164-168`), dan ETL fail-closed termasuk `NULL → 0` (`behavior.py:161-201`).
- `cart_abandon` hanya dari `consented` (`behavior-jobs.service.ts:113`), dan `purchase` hanya untuk member ber-consent.
- Web hanya meneruskan Bearer bila `?link=1` dan consent perangkat aktif (`app/api/events/route.ts:54-55`, `tracking-client.ts:173-175`). App mengirim `auth:'none'`. ID consent baru dibuat saat setuju (`tracking-client.ts:309`). Untuk arah sebaliknya, lihat SEC-V01-01.

**(2) Segmen hanya consented**
- Ketiga view `v_seg_*` yang membaca fakta memfilter `consented = 1` (`16-g50-behavior.sql:195,212,222`).
- `segment_reader` hanya punya grant per view `efn_segment.*` dan tidak punya akses `efn_dwh` (`segment-access.sql`). Test `test_segment_views_only_read_consented_events` menjaga aturan ini.

**(3) Tanpa data mentah**
- `page` berupa enum tertutup di BFF, consumer, CHECK PG, ETL, dan klien. `pageForPath` hanya membaca segmen path, tidak pernah URL, query, atau referrer.
- Payload dibatasi allowlist `additionalProperties:false`. IP hanya menjadi kunci Redis ter-hash. `redactQuery` di BFF dan `sanitize_query` di ETL. Route web tidak me-log body.

**(4) Erasure**
- Baris `version='erasure'` tetap menghasilkan 403 juga dengan Bearer (`behavior.service.ts:197`, spec `:323`). Consumer menolak batch subjek yang sudah di-erase.

**(5) Abuse**
- Batas body 32 KB, batch ≤ 50, rate limit berbobot per device dan per IP sebelum consent gate, fail-closed bila Redis mati.
- `channel_code` wajib terpetakan ke PT (`unknown_channel`). `product_id` wajib ada di katalog (`unknown_product`). Origin check (CSRF) di route web.

**(6) Insight `/visitors`**
- Row policy `efn_insight_scope` dengan domain tanda tangan `|visitor|` yang terpisah dari sig/sig2/sig3, dengan regex vcodes. `clickhouse-init.sh` gagal bila ada view `efn_insight` tanpa policy.
- vcodes dihitung dari sesi terverifikasi: ESB hanya untuk sesi tanpa `brs`, dan SMD hanya untuk persona category/exec/marketing. Invarian claims "0 ∈ wids ⇔ persona tingkat PT" membuat sesi ber-outlet tidak mendapat data tingkat PT.
- Filter URL `pt` hanya mempersempit (`chQueryVis` menolak kode di luar sesi). Query berupa konstanta dengan parameter bertipe. View hanya membaca agregat tanpa `subject_key`. k ≥ 10 untuk produk dan pencarian (catatan di SEC-V01-04).

**(7) Secret/log**
- Tidak ada secret baru. Pepper dibaca dari AppConfig/secret yang sudah ada. Policy CH dibuat dengan `--log_queries=0`. Log BFF/Insight tidak memuat device_id, subject_key, atau body. Tidak ada `console.*` di klien.
- Flag `BEHAVIOR_ANON_VISIT_ENABLED` default `false` dan efektif hanya bersama `BEHAVIOR_TRACKING_ENABLED`, dengan test.

## Yang belum diverifikasi
- Tidak ada test, `EXPLAIN`, atau uji row policy ClickHouse 24.8 yang dijalankan (review statis). `queries-visitor.ts` sendiri menandai "belum dijalankan terhadap ClickHouse".
- Halaman `/privasi` web dan isi `TrackingSettings` hanya dicek sekilas (keberadaan teks), tidak ditinjau isinya.
