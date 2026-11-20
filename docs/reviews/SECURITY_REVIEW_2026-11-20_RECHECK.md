# Security Re-review — perbaikan G50 (kampanye supermarket)

Tanggal: 2026-11-20 · Reviewer: efn-security-reviewer (read-only) · Worktree: `E:\Projects\efn-platform-g50`, branch
`feat/wave-50-supermarket-campaign` (target commit `8e0df2e`; hash tidak diverifikasi karena tanpa akses git/shell).
Acuan: review awal `docs/reviews/SECURITY_REVIEW_2026-11-20.md` (SEC-G50-01..15).

## Ringkasan eksekutif

1. **Kedua blokir merge awal sudah tertutup.** SEC-G50-01 (bypass approval lewat `write()` sudo) ditutup dengan allowlist
   field konten dan penulisan sebagai user. SEC-G50-02 (kontrak payload) ditutup dengan satu format dan fixture bersama.
2. **Satu regresi baru memblokir merge, sifatnya fungsional (SEC-G50-16).** Perbaikan SEC-G50-08 mewajibkan
   `company_code` di body ingest segmen BFF. Segment-worker tidak mengirimnya, dan validatornya justru menolak key
   tambahan. Akibatnya setiap ingest anggota segmen mendapat 400, sehingga tidak ada usulan kampanye segmen yang sampai ke
   Odoo. Kegagalan ini fail-closed: tidak ada data bocor, tetapi fitur inti G50 mati.
3. **Medium production lainnya sudah tertutup:** SEC-G50-03 (kode voucher), -04 (TTL DLQ WA) dan -05 (DSAR). Temuan Low
   -06, -07, -10 dan -11 juga tertutup.
4. **Kode baru (event `purchase`, inbox Super-App) aman secara teknis.** Klien tidak bisa menyuntik `purchase` atau UUID v8.
   Race dengan erasure aman (FK + `erased_at`). Kode voucher tidak masuk log, dan respons inbox `no-store`. Sisa temuannya
   kepatuhan: teks consent `tracking` belum menyebut riwayat pembelian (SEC-G50-17), dan inventaris PDP belum memuat tabel
   G50-B (SEC-G50-18). Keduanya memblokir production, tidak memblokir merge.

Batasan: review statis. Tidak ada test, `docker compose config`, atau diff git yang dijalankan. SEC-G50-09 dan -12..-15
tidak diperiksa ulang (di luar brief), jadi statusnya tetap seperti review awal.

## Status temuan awal

| ID | Status | Bukti (file:baris) | Catatan |
|---|---|---|---|
| SEC-G50-01 | **CLOSED** | `efn_campaign/models/efn_campaign.py:69-90, 332-349, 351-367, 369-388, 396-398`; test `tests/test_campaign.py:284-331` | Residual PLAUSIBLE: SEC-G50-19 |
| SEC-G50-02 | **CLOSED** | builder `efn_campaign.py:649-689`; parser `api-gateway/src/campaign/odoo-campaign.ts:97-173`; fixture `tests/fixtures/campaign_payload_v1.json`; `campaign-payload-fixture.spec.ts:21-98`; test Odoo `test_campaign.py:373-420` | Normalizer lama tidak ada lagi (grep); bentuk lama ditolak (`odoo-campaign.spec.ts:61-64`). Info CI: SEC-G50-20 |
| SEC-G50-03 | **CLOSED** (cakupan modul) | `security/ir.model.access.csv:1-7` (tanpa `loyalty.card`); `card_ids groups="base.group_system"` `efn_campaign.py:203-205`; `card_count` sudo `:276-281`; test `test_campaign.py:333-338` | Residual terdokumentasi `README.md:88-93`: user Marketing yang juga POS/Sales user tetap membaca kode lewat ACL CE `pos_loyalty`/`sale_loyalty` (PLAUSIBLE, belum dicek di source). Mitigasi = SoD peran, bukan kode |
| SEC-G50-04 | **CLOSED** | `infra/rabbitmq/rabbitmq-entrypoint.sh:106-112, 146-153` | Policy `message-ttl` 24 jam + `max-length` drop-head untuk `notify.wa_unofficial(.dlq)`; `behavior.event.dlq` 7 hari. Escaping heredoc → JSON → regex benar. RabbitMQ 3.13 mendukung TTL/overflow quorum |
| SEC-G50-05 | **CLOSED** (kode) | `privacy/dsar.service.ts:216-217, 283-288`; `notification/email/channel-privacy.ts:67-153` | Email didekripsi untuk subjek sendiri (gagal → `readable:false`, export tidak gagal); jejak email/WA tanpa isi; batas 1000 + `truncated`. Menyimpang kecil: `to_hash` mentah ikut diekspor (risiko rendah, pseudonim milik subjek sendiri). Dokumentasi retensi PDP: SEC-G50-18 |
| SEC-G50-06 | **CLOSED** | `docker-compose.wa-unofficial.yml:108, 128-174, 227-232, 257-264`; `infra/wa-egress/admin-relay.cfg:35, 43, 47`; `prometheus.yml:103-119`; `traefik/dynamic/internal-tls.yml:42-46`; `scripts/check-encryption.sh:577-631` | wa-unofficial hanya di `rmq`/`wa-egress`/`wa-admin`. Relay hanya bind di IP `wa-ingress`, jadi dari sisi `wa-admin` tidak ada listener. TLS end-to-end diverifikasi (`serverName wa-unofficial`). Invarian dicek CI |
| SEC-G50-07 | **CLOSED** | `behavior/behavior-privacy.ts:57-74`; gate `behavior.service.ts:163-166` | Baris `device_consent(granted=false, 'erasure')` ditulis di transaksi erasure yang sama untuk setiap device tertaut. Gate membaca baris terbaru (`created_at DESC, id DESC`), sehingga event berikutnya mendapat 403 sampai ada consent baru |
| SEC-G50-08 | **PARTIAL** | DB `infra/postgres/init/20-platform-g50a.sql:180-202`; ingest `campaign/segment-ingest.service.ts:41-60, 75-80`; dispatcher `campaign-dispatcher.consumer.ts:123-130` | Sisi BFF/DB/dispatcher benar (PT wajib, satu PT per `run_id` → 409 `run_company_mismatch`, dispatcher memfilter PT). **Segment-worker dan docs/04 §28.7c belum diubah** → SEC-G50-16. Catatan: `company_code` dinyatakan sendiri oleh worker (sertifikat sama untuk semua PT), jadi kontrol ini defense-in-depth, bukan pengikatan kriptografis |
| SEC-G50-10 | **CLOSED** | `scripts/lib/deploy-validate.sh:72-75, 667, 732-734` | `EMAIL_ENABLED` masuk `EFN_GATED_FEATURES` (izin Superadmin), dan production wajib `EMAIL_DPA_APPROVAL_REF` |
| SEC-G50-11 | **CLOSED** | `common/pii/pii-columns.ts:45-62`; `notification/email/email.constants.ts:19-35`; `email-sender.consumer.ts:134-135` (`row.id` = `member.id`); `campaign/campaign-sync.service.ts:16-27, 146`; `campaign-inbox.controller.ts:59-63` | AAD v2 per baris untuk tulis. Fallback baca v1 tidak membuka penukaran (ciphertext v2 dari baris lain gagal di kedua AAD). Info: karena belum ada data v1, fallback bisa dihapus |
| SEC-G50-09, -12..-15 | Tidak diperiksa ulang | — | Status sama dengan review awal (-09 tetap blokir production tracking) |

### Verifikasi tambahan SEC-G50-01 (jalur lain)

- **`copy()`**: `copy_data` hanya membawa field `copy=True`. `outbox_ids`, `card_ids`, `message_ids`, `activity_ids`, dan
  semua field sistem ber-`copy=False` (default One2many/mail atau eksplisit). `copy(default={...})` lewat `create()` non-su
  sehingga key non-konten ditolak. Aman.
- **`name_create`**: memanggil `create({'name': ...})`, jadi melewati allowlist. Aman.
- **onchange**: tidak menulis. Aman.
- **Membuat `efn.campaign.outbox` langsung**: ACL Marketing baca saja (`ir.model.access.csv:3`). Aman.
- **Membuat `loyalty.card` langsung**: modul tidak memberi ACL. Hak CE dari grup POS/Sales di luar kendali modul (lihat -03).
- **Command pada relasi konten** (`partner_ids`, `markdown_*`): dieksekusi sebagai user dengan ACL masing-masing, jadi tidak
  ada eskalasi.
- **Action server**: hanya bisa dibuat admin. Tidak ada `ir.actions.server` di modul.
- **`default_<field>` context**: dibuang untuk field non-konten (`efn_campaign.py:341-349`), diuji di `test_campaign.py:323-331`.

## Temuan baru

| ID | Sev | Lokasi | Ringkas | Blokir |
|---|---|---|---|---|
| SEC-G50-16 | **Medium** (ketersediaan, fail-closed) | `segment-worker/efn_segment/contract.py:36, 83-101`; `api-gateway/src/campaign/segment-ingest.service.ts:11, 42`; `docs/04_SERVICE_CONTRACTS.md:4019` | Body ingest anggota segmen worker tanpa `company_code`, padahal BFF mewajibkannya → semua chunk 400 → segmen `failed`, tidak ada usulan ke Odoo | **Merge** (fungsional) |
| SEC-G50-17 | Low (kepatuhan) | `api-gateway/src/behavior/purchase-events.service.ts:76-90`; teks consent `superapp-web/lib/tracking.ts:18-21` | Riwayat pembelian online dimasukkan ke profil perilaku atas consent `tracking`, padahal teks consent hanya menyebut produk dilihat/wishlist/keranjang/pencarian | Production (tracking) |
| SEC-G50-18 | Low (kepatuhan) | `docs/compliance/PDP_DATA_INVENTORY.md:476-479, 612-618` | Inventaris PDP belum memuat `member_email`, `email_outbound`, `wa_unofficial_recipient/outbound`, `purchase_emitted`, dan retensi DLQ baru (baris 618 masih "Rencana") | Production (email/WA/tracking) |
| SEC-G50-19 | Low (PLAUSIBLE) | `efn_campaign.py:351-367` | `ir.default` pribadi (bukan context) dapat mengisi field sistem saat `create` non-su | — |
| SEC-G50-20 | Info | `.github/workflows/ci.yml:72`; `odoo-campaign.ts:103-110` | Perubahan fixture Odoo saja tidak menjalankan Jest BFF di PR; parser tidak memeriksa `schema_version` | — |

### SEC-G50-16 — Medium — Kontrak ingest segmen worker ↔ BFF tidak cocok setelah perbaikan SEC-G50-08 (memblokir merge)

**Lokasi:**
- BFF `segment-ingest.service.ts:11` (`KEYS` memuat `company_code`) dan `:42` (`if (!isCompanyCode(o.company_code)) return bad()`).
- Worker `contract.py:36` (`MEMBERS_KEYS = {"schema_version", "run_id", "segment_code", "subject_keys", "chunk_no"}`),
  `:83-96` (`members_payloads` tanpa `company_code`), `:99-101` (`set(payload) != MEMBERS_KEYS` → `ContractError`).
- Kontrak `docs/04_SERVICE_CONTRACTS.md:4019` masih menulis body tanpa `company_code`.

**Skenario.**
1. Worker menjalankan run harian PT SMD dan `bff.send_members(payload)` mengirim body tanpa `company_code`.
2. BFF menjawab `400 invalid_request`.
3. `runner.py:159-168` menandai segmen `failed` dan **tidak** memanggil `proposal_upsert`.
4. Tidak ada kampanye segmen di Odoo dan tidak ada promo terkirim.

Worker juga tidak bisa sekadar "menambah key", karena validator lokalnya menolak key tambahan. Jadi perubahan wajib
dilakukan di tiga tempat. Test kedua sisi tetap hijau karena masing-masing memakai fixture sendiri, pola yang sama dengan
SEC-G50-02 awal.

**Dampak.** Fitur kampanye segmen (inti G50) mati total. Tidak ada kebocoran data: perilakunya fail-closed.

**Rekomendasi.**
- Tambahkan `company_code` ke `MEMBERS_KEYS`, `members_payloads(company, ...)`, dan `validate_members`
  (`_COMPANY_RE`) di worker.
- Perbarui docs/04 §28.7c (body + respons `409 run_company_mismatch`).
- Buat fixture JSON bersama untuk body ingest, dibaca `test_contract.py` dan `segment-ingest.service.spec.ts`, seperti
  `campaign_payload_v1.json`.
- Tambahkan path `services/segment-worker/**` ke filter `api` CI, atau sebaliknya.

**Risiko:** R-B17 (ketersediaan kampanye), multi-company.

### SEC-G50-17 — Low — Event `purchase` di luar cakupan teks consent `tracking` (memblokir production tracking)

**Lokasi:** `purchase-events.service.ts:76-90` (bagian order lunas → event per produk ke profil subjek member, syarat consent
`tracking` akun) dan `:112` (membuat `behavior.subject_member`). Teks consent `superapp-web/lib/tracking.ts:18-21`:
"produk yang dilihat, wishlist, isi keranjang, dan kata pencarian ... memakai kode acak perangkat".

**Skenario.** Member memberi consent `tracking` berdasarkan teks itu. Job 5 menit kemudian memasukkan riwayat pembelian
online (produk, qty, harga) ke `event_raw`, lalu `fact_visitor_event`, segmen, dan penargetan promo. Kategori data ini dan
penautannya ke akun (bukan "kode acak perangkat") tidak disebut di teks.

**Dampak.** Persetujuan kurang spesifik untuk pemrosesan ini (UU PDP Pasal 20(2)a, 22). Teks masih DRAFT menunggu DPO, jadi
dampaknya saat ini nol (flag OFF).

**Rekomendasi.**
- Masukkan "riwayat pembelian online" dan penautan ke akun ke teks consent, lalu naikkan versi. Atau minta DPO menetapkan
  dasar hukum lain secara tertulis (mis. kepentingan sah untuk "viewed-not-bought").
- Catat di inventaris PDP (SEC-G50-18).

**Risiko:** R-L13, R-L01.

**Yang sudah benar pada kode baru ini:**
- Consent dicek di SQL saat diproses (`:85-86`). Member terhapus disaring (`:81`).
- Lewat outbox → antrean → writer, bukan tulis langsung.
- Idempoten (`purchase_emitted` + UUID v8 deterministik + dedup staging). Purge 30 hari (`behavior-jobs.service.ts:76`).
- Advisory lock satu replika.
- Tanpa PII di payload (subject HMAC, `product_id`, qty, harga).
- Race dengan erasure aman. `subject_member.member_id` ber-FK ke `platform.member`, sehingga insert setelah member dihapus
  gagal dan seluruh transaksi job rollback. Bila job lebih dulu, `received_at < erased_at`, sehingga writer menolak
  (`behavior-writer.consumer.ts:54-58`).

### SEC-G50-18 — Low — Inventaris PDP belum diperbarui untuk tabel G50-B dan event `purchase`

**Lokasi:** `docs/compliance/PDP_DATA_INVENTORY.md`. Baris G50 hanya mencakup `device_consent`, `device_link`, dan
`event_raw` (476-479, 612-618). Tidak ada `platform.member_email` (alamat terenkripsi), `email_outbound` (90 hari),
`wa_unofficial_recipient`/`wa_unofficial_outbound` (180 hari), maupun `behavior.purchase_emitted`. Baris 618 masih menulis
retensi pesan WA "≤ 24 jam ... Rencana", tanpa menyebut DLQ yang kini dibatasi policy broker.

**Dampak.** Rekomendasi SEC-G50-05 ("cantumkan retensi di inventaris PDP") belum selesai, sehingga DPO tidak punya
catatan lengkap untuk penilaian Q-SM18/19.

**Rekomendasi.** Tambahkan baris per tabel (tujuan, dasar hukum, retensi, enkripsi/pseudonim, penanganan erasure =
`ON DELETE CASCADE`). Perbarui baris 618 dengan policy `notify.wa_unofficial(.dlq)` 24 jam dan `behavior.event.dlq` 7 hari.

### SEC-G50-19 — Low (PLAUSIBLE) — Field sistem dapat terisi lewat `ir.default` pribadi saat create

**Lokasi:** `efn_campaign.py:351-367`. `_efn_clean_default_context` membuang `default_*` dari context, tetapi default
tersimpan di `ir.default` tetap dipakai `default_get`. Setelah create, hanya `state`, `maker_id`, dan `editor_ids` yang
ditimpa.

**Skenario (PLAUSIBLE: bergantung pada apakah ACL/rule `ir.default` CE 19 mengizinkan user internal membuat default
pribadi; tidak bisa dicek di repo).**
1. Marketer memanggil `ir.default.set('efn.campaign', 'approved_by_id', <uid approver>, user_id=True)`, atau field
   `origin`/`segment_summary`/`segment_member_count`.
2. Kampanye manual baru tampil "Disetujui oleh X" atau berasal dari "worker", dengan ringkasan segmen palsu, sebelum
   approval sungguhan.

`outbox_ids`/`card_ids` lewat default tetap dieksekusi sebagai user dan ditolak ACL. `state` ditimpa. `segment_run_id`
terkunci UNIQUE per PT dan difilter PT di dispatcher.

**Dampak.** Pemalsuan jejak tampilan dan informasi yang menyesatkan approver. Tidak ada voucher atau pengiriman tanpa
approval.

**Rekomendasi.** Pada create non-su, tulis ulang semua `SYSTEM_FIELDS` ke nilai awal lewat `_efn_sys_write` (bukan hanya
tiga field), atau saring `vals` hasil `default_get` untuk field non-konten.

### SEC-G50-20 — Info — Penjagaan kontrak di CI

- `ci.yml:72`: filter `api` hanya `services/api-gateway/**`. PR yang hanya mengubah
  `efn_campaign/tests/fixtures/campaign_payload_v1.json` (atau builder Odoo) tidak menjalankan
  `campaign-payload-fixture.spec.ts`. Drift baru tertangkap setelah push ke main.
  **Rekomendasi:** tambahkan path fixture dan `services/segment-worker/**` ke filter `api`.
- `odoo-campaign.ts:103-110`: parser tidak memeriksa `schema_version === 1`, sehingga payload v2 kelak diparse diam-diam
  dengan aturan v1. **Rekomendasi:** tolak `schema_version` selain 1.

## Pemeriksaan kode baru lain (tanpa temuan)

- **`POST /v1/events` tetap menolak `purchase` dan UUID non-v4.** `CLIENT_EVENT_TYPES` tanpa `purchase`
  (`behavior/dto/behavior.dto.ts:10-20`); `parseEvent` mewajibkan UUID v4 (`:108-109`). Tipe server dan UUID 1-8 hanya
  diterima `parseBehaviorMessage` (`behavior-message.ts:18, 65-66, 79-82`), yang hanya dikonsumsi dengan publisher
  `efn-bff` (`behavior-writer.consumer.ts:43-46`).
- **Writer menolak batch subjek ter-erase.** `erased_subject.erased_at >= received_at` → `stale` (ack, tidak ditulis)
  (`behavior-writer.consumer.ts:51-58`).
- **Inbox Super-App.**
  - Route GET memakai `requireMember(req, false)` → `isNotCrossSite` (`lib/orders-route.ts:63-69`). Ini cukup untuk GET
    tanpa efek samping.
  - Respons `noStoreJson` (`no-store`), allowlist `parseInbox` (`lib/inbox.ts:25-46`), dan BFF juga `Cache-Control:
    no-store` (`campaign-inbox.controller.ts:85-89`).
  - Tidak ada `console.*` di route, `lib/inbox.ts`, maupun `InboxView.tsx`. `lib/bff.ts` hanya me-log method/path/status.
  - `localStorage` hanya menyimpan timestamp "terakhir dilihat". Kode dirender sebagai teks React (escaped).
  - BFF hanya mendekripsi kode `anonymous_code` milik member sendiri atau `public_code`. Kegagalan dekripsi di-log dengan id
    inbox, bukan kode.
- **Segment-worker: metrik dan ringkasan tanpa PII.**
  - Metrik hanya hitungan dan parameter (`contract.py:133-142`).
  - Ringkasan berisi id produk dan jumlah subjek ≥ `k_min`, dengan pola `\d{6,}` dibuang (`:162-180`).
  - `_scan_no_identity` menolak string hex64 (`:183-194`).
  - Log run hanya kode segmen, status, dan hitungan (`runner.py:172-184`).

## Status blokir

- **Merge:** SEC-G50-16 (fungsional, kontrak worker ↔ BFF). SEC-G50-01 dan -02 sudah tidak memblokir.
- **Production (flag tetap OFF sampai beres):**
  - SEC-G50-09 dan -17 sebelum tracking dinyalakan.
  - SEC-G50-18 sebelum email/WA/tracking dinyalakan.
  - Persetujuan tertulis yang sudah digate deploy-validate (DPO, Legal, Superadmin).
  - Residual SEC-G50-03 (SoD peran Marketing ≠ POS/Sales user) dicatat di runbook akses.
- **Tidak memblokir:** SEC-G50-19 (PLAUSIBLE) dan SEC-G50-20. Disarankan dikerjakan bersama perbaikan SEC-G50-16.
