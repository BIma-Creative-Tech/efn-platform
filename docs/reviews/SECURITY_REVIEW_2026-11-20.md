# Security & Compliance Review — G50 (kampanye supermarket)

Tanggal: 2026-11-20 · Reviewer: efn-security-reviewer (read-only) · Worktree: `E:\Projects\efn-platform-g50`, branch
`feat/wave-50-supermarket-campaign`. Acuan: CLAUDE.md, docs/04 §4/§7/§28, ADR-044/047/048, OWASP ASVS 4.0.3 / API Top 10.

## Ringkasan eksekutif

1. **Satu celah High yang memblokir merge**: user Marketing biasa (bukan approver) bisa melewati approval 4-eyes. Caranya
   menulis `outbox_ids` / `card_ids` lewat RPC bersama satu field konten. `write()` menjalankan seluruh `vals` sebagai
   `sudo`, sehingga voucher bisa dibuat dan kampanye tak tersetujui bisa masuk antrean ke BFF.
2. **Kontrak payload Odoo ke BFF tidak cocok** (tanggal, `message`, `voucher`, `voucher_program_ref`). Akibatnya semua
   kampanye sah ditolak BFF dan cap anggaran tidak bisa ditegakkan. Ironisnya, payload buatan penyerang (SEC-G50-01) justru
   cocok dengan parser BFF. Memblokir merge (fungsional).
3. Temuan Medium yang memblokir production (flag tetap OFF): kode voucher terbaca Marketing lewat `loyalty.card`, nomor HP
   tertahan tanpa batas waktu di DLQ WA, dan DSAR export belum mencakup tabel email/WA baru.
4. Kontrol dasar sudah kuat: isolasi WA (network, secret, rootfs read-only, AES-GCM, gate production), mTLS dan allowlist CN
   di gateway/listener segmen, pseudonim HMAC dengan dua pepper, redaksi query, k-anonim, dan erasure ClickHouse yang
   terverifikasi.

Batasan: review statis saja. Tidak ada `git diff`, test, atau `docker compose config` yang dijalankan. Diff dengan
`origin/main` tidak dibandingkan per baris; cakupan mengikuti daftar file dari brief. CSP/`next.config.mjs` tidak
diperiksa ulang (tidak terlihat ada perubahan G50).

## Tabel temuan

| ID | Sev | Lokasi | Ringkas | Blokir |
|---|---|---|---|---|
| SEC-G50-01 | **High** | `efn_campaign/models/efn_campaign.py:317-337` | `write()` sudo seluruh `vals`, sehingga `outbox_ids`/`card_ids` (Command.create) lolos tanpa ACL/approval | **Merge** |
| SEC-G50-02 | **High** (integritas/ketersediaan) | `efn_campaign.py:562-594` vs `api-gateway/src/campaign/odoo-campaign.ts:98-168` | Bentuk payload outbox ≠ parser BFF; `voucher_value_idr` tidak pernah dikirim | **Merge** |
| SEC-G50-03 | Medium | `efn_campaign/security/ir.model.access.csv:7` | Marketing baca `loyalty.card.code` (kode bearer) lewat RPC; bertentangan dengan `payload` group_system | Production |
| SEC-G50-04 | Medium | `infra/rabbitmq/rabbitmq-entrypoint.sh:136`, `wa-unofficial/src/processor.ts:107-114` | `notify.wa_unofficial.dlq` menyimpan `to_e164` tanpa TTL (expiration dicabut saat dead-letter) | Production (WA) |
| SEC-G50-05 | Medium | `api-gateway/src/privacy/dsar.service.ts:210-213` | DSAR export tanpa `member_email`, `email_outbound`, `wa_unofficial_outbound/recipient` | Production |
| SEC-G50-06 | Low | `infra/compose/docker-compose.wa-unofficial.yml:104,188,191` | Network `wa-admin` dua arah: wa-unofficial yang dikompromi bisa menjangkau Traefik & Prometheus | Production (WA) |
| SEC-G50-07 | Low | `behavior/behavior-privacy.ts:46-75`, `behavior-writer.consumer.ts:51-58` | Setelah erasure, device tertaut tetap dilacak dengan `device_key` yang sama tanpa consent ulang | Production (tracking) |
| SEC-G50-08 | Low | `infra/postgres/init/20-platform-g50a.sql:151-168`, `campaign-dispatcher.consumer.ts:120-131` | Anggota segmen tidak terikat PT (`run_id` + `segment_code` saja) | — (defense in depth) |
| SEC-G50-09 | Low (PLAUSIBLE) | `superapp-web/lib/tracking.ts:12-23`, `behavior.service.ts:64-69` | Teks consent yang tampil = konstanta klien; bukti `text_sha256` = env BFF; hanya versi yang dicocokkan | Production (tracking) |
| SEC-G50-10 | Low | `scripts/lib/deploy-validate.sh:72-74,714-718` | `EMAIL_ENABLED=true` di production tanpa izin Superadmin/ref DPA (Q-SM19, SMTP pihak ketiga) | Production (email) |
| SEC-G50-11 | Low | `api-gateway/src/common/pii/pii-columns.ts:46-47` | AAD `email_enc`/`code_enc` tidak terikat baris (pola SEC-102 tidak diikuti) | — |
| SEC-G50-12 | Info | `wa-unofficial/src/admin-server.ts:94-108` | Limiter gagal-auth global (10/menit) sehingga operator bisa dikunci (DoS) | — |
| SEC-G50-13 | Info | `wa-unofficial/package-lock.json:4028` | `libsignal` = git dep (dipin commit, tanpa `integrity`), lisensi GPL-3.0 | — |
| SEC-G50-14 | Info | `notification/email/README.md:62`, `wa-unofficial-result.consumer.ts:98-106` | Consent marketing per purpose, bukan per kanal; STOP WA mencabut semua kanal promo | Keputusan DPO |
| SEC-G50-15 | Info | `privacy/consent.service.ts:105-106`, `behavior/subject-key.ts:14` | Re-grant `tracking` menautkan ulang data lama (subject_key deterministik) | Keputusan DPO |

## Detail

### SEC-G50-01 — High — SoD approval dilewati lewat `write()` sudo (memblokir merge)
**Lokasi:** `services/odoo/addons/efn_campaign/models/efn_campaign.py:317-337`.
`write()` memeriksa `SYSTEM_FIELDS`. Bila ada field `CONTENT_FIELDS`, kode menjalankan `check_access("write")` lalu
`super(EfnCampaign, self.sudo()).write(vals)` untuk **seluruh** `vals`. `outbox_ids` (One2many ke `efn.campaign.outbox`) dan
`card_ids` (One2many ke `loyalty.card`) tidak ada di kedua daftar. Atribut `readonly=True` hanya berlaku di UI dan tidak
ditegakkan ORM.

**Eksploit.** Pelaku: user `group_campaign_user` (bukan approver). Kampanye berstatus draft/proposed miliknya. Panggilan
`/json/2/efn.campaign/write`:
```
ids=[X], vals={"name":"x", "outbox_ids":[[0,0,{"company_id":C,"kind":"segment","ref_model":"efn.campaign","ref_id":X,
  "approval_rev":99,"idempotency_key":"campaign:SMD:X:99","payload":{...bentuk parser BFF, channels:["inbox","push","email","wa_unofficial"],
  voucher_mode:"public_code", public_code:"...", message:{...}}}]],
  "card_ids":[[0,0,{"program_id":P,"points":1000000,"code":"BEBAS-001"}]]}
```
**Dampak.**
- Baris outbox `queued` dibuat tanpa approval. BFF mengklaimnya dan menerbitkan `promo.campaign.requested`, lalu promo
  terkirim ke seluruh anggota segmen/wishlist dengan teks bebas (lewat push, email, dan WA).
- `loyalty.card` dibuat dengan poin arbitrer, yaitu voucher bernilai uang tanpa 4-eyes (fraud). Ini melanggar L-156 dan
  ADR-047 §3.4.
- Variasi lain: `message_ids` [0,0,…] memalsukan chatter atas nama sudo.

**Rekomendasi.**
- Untuk non-su, tolak semua key di luar `CONTENT_FIELDS` ∪ allowlist mail (`message_follower_ids`, `activity_ids` hanya
  lewat API mail). Lebih aman lagi: denylist eksplisit `outbox_ids`, `card_ids`, `message_ids`.
- Tulis `vals` konten sebagai **user** (`super().write(vals)`). `editor_ids` saja yang lewat `_efn_sys_write`.
- Tambah test: marketer `write({'name':..,'outbox_ids':[(0,0,..)]})` harus `AccessError`; idem `card_ids`.
- Tambah entri `write` efn.campaign di `efn_core/tests/test_rpc_surface.py` bila daftar itu mencakup override CRUD.

**Risiko:** R-B17, R-S04 (privilege escalation), L-156.

### SEC-G50-02 — High (integritas/ketersediaan, bukan celah eksploitasi) — Kontrak outbox Odoo ↔ BFF tidak cocok
**Lokasi:** `efn_campaign.py:546-594` (`_efn_generate_vouchers`, `_efn_outbox_payload`) vs
`api-gateway/src/campaign/odoo-campaign.ts:98-168` dan fixture `campaign/testing/campaign-fixtures.ts:11-19`.

| Odoo mengirim | BFF mengharapkan |
|---|---|
| `valid_from/valid_to` `YYYY-MM-DD` | ISO datetime `...Z` (`ISO_RE`), `valid_to > valid_from` (Odoo mengizinkan sama) |
| `title`, `body` top-level | `message: {title, body}` |
| `voucher: {mode, codes / code+quota / cards[{partner_id, code}]}` | `voucher_codes[]` / `public_code` |
| `voucher_program_ref` integer | string `REF_RE` |
| (tidak ada) | `voucher_value_idr` (cap anggaran) |

**Dampak.**
- Setiap baris ditolak `parseClaim` dan tidak di-ack. Setelah 5 percobaan baris menjadi `failed`, dan kode voucher terkirim
  ulang 5 kali tanpa guna.
- Cap anggaran (`CAMPAIGN_BUDGET_CAP_ENABLED`) tidak pernah bisa ditegakkan karena `voucher_value_idr` selalu null.
- Payload penyerang dari SEC-G50-01 adalah satu-satunya bentuk yang diterima.
- Test di kedua sisi hijau karena masing-masing memakai fixture sendiri (tidak ada test kontrak bersama).

**Rekomendasi.**
- Tetapkan satu skema di docs/04 §28.7. Tabel §28.7 saat ini juga belum memuat `voucher_mode` / `voucher` / `message`.
- Buat fixture JSON bersama yang divalidasi di pytest Odoo dan Jest BFF.
- Putuskan nasib `member_bound`: BFF membuang `cards[]` tanpa pemetaan `partner_id` ke member.

**Risiko:** R-B17, L-155/L-157.

### SEC-G50-03 — Medium — Kode voucher terbaca seluruh Marketing (memblokir production)
**Lokasi:** `ir.model.access.csv:7` (read `loyalty.card` untuk `group_campaign_user`), `loyalty_card.py:9-11`.
`efn.campaign.outbox.payload` sengaja dibatasi `groups="base.group_system"` (`efn_campaign_outbox.py:47`) karena kode
bersifat bearer. Kode yang sama tetap terbaca lewat `loyalty.card`.

**Eksploit.** Marketer menjalankan `search_read('loyalty.card', [('efn_campaign_id','=',X)], ['code'])` dan mendapat semua
kode anonim (maks 5000 per kampanye). Kode itu bisa ditebus sendiri sebelum ditugaskan BFF ke member. Mode `public_code`
juga terbaca dari `loyalty.rule.code` (csv:5); itu wajar untuk kode publik.

**Rekomendasi.**
- Hapus ACL csv:7. `card_count` sudah dihitung sudo, jadi form tidak butuh ACL ini.
- Bila Marketing perlu melihat daftar kartu, buat field ringkas tanpa `code`. Opsi lain: override `_read`/`fields_get` agar
  `code` tidak terbaca untuk kartu ber-`efn_campaign_id`.
- Verifikasi apakah ACL CE `loyalty` sendiri sudah memberi read `loyalty.card` ke `base.group_user` (PLAUSIBLE, tidak bisa
  dicek di repo). Bila ya, perlu record rule/field guard khusus kartu kampanye.

**Risiko:** R-B17 (fraud promo), R-S04.

### SEC-G50-04 — Medium — Nomor HP tertahan tanpa batas di `notify.wa_unofficial.dlq` (memblokir production WA)
**Lokasi:** `infra/rabbitmq/rabbitmq-entrypoint.sh:95-104,136` (DLQ quorum tanpa `x-message-ttl` / policy),
`wa-unofficial/src/processor.ts:107-114` (session `disconnected`/`banned` → DLQ), notifier BFF
`wa-unofficial.notifier.ts:131-141` (`to_e164` + per-message `expiration` ≤ 24 jam).

RabbitMQ **menghapus properti `expiration`** saat pesan di-dead-letter. Pesan kedaluwarsa (service idle/terputus) dan pesan
yang di-nack jatuh ke DLQ dan bertahan selamanya, lengkap dengan nomor HP plaintext di volume broker.

**Dampak.**
- Retensi PII tak terbatas, bertentangan dengan UU PDP Pasal 16(2) (penyimpanan terbatas) dan klaim "retensi ≤ 24 jam" ADR-044.
- Erasure DSAR tidak menjangkau DLQ.
- Siapa pun yang memegang akses admin RabbitMQ dapat membaca daftar nomor.

**Rekomendasi.**
- Policy broker `^notify\.wa_unofficial\.dlq$` dengan `message-ttl` ≤ 24-48 jam dan `max-length`.
- Opsi lain: DLX ke tujuan yang membuang pesan (`dead-letter` tanpa DLQ) untuk alasan `expired`.
- Dokumentasikan di runbook bahwa replay DLQ hanya dilakukan dalam jendela itu.
- Pertimbangkan juga `behavior.event.dlq` (subject_key, pseudonim): TTL ≤ 7 hari agar selaras retensi staging.

**Risiko:** R-L01, R-S21, R-B16.

### SEC-G50-05 — Medium — DSAR export belum mencakup tabel G50-B (memblokir production)
**Lokasi:** `privacy/dsar.service.ts:210-213, 273-281` (hanya wishlist/behavior/campaign);
`infra/postgres/init/21-platform-g50b.sql:35-90`.

`member_email` (alamat terenkripsi), `email_outbound` (jejak kirim 90 hari), `wa_unofficial_outbound`, dan
`wa_unofficial_recipient` (pseudonim nomor, 180 hari) adalah data pribadi tetapi tidak diekspor. README email baris 57 masih
menyebut hal ini sebagai "belum". Bagian `pii-columns` di README itu sudah usang karena kolom tersebut kini terdaftar.

Erasure sudah aman lewat `ON DELETE CASCADE` saat `DELETE platform.member` (`erasure.service.ts:376`).

**Rekomendasi.**
- Tambah `exportEmailMember` / `exportWaMember`: alamat email didekripsi untuk subjek sendiri, status, waktu antre/kirim,
  kategori.
- Jangan ekspor `to_hash` mentah. Tampilkan saja "nomor HP Anda dipakai untuk WA promo pada …".
- Cantumkan retensi di inventaris PDP.

**Risiko:** R-L01, UU PDP Pasal 7 dan 32.

### SEC-G50-06 — Low — Network `wa-admin` tidak satu arah
**Lokasi:** `docker-compose.wa-unofficial.yml:18-21,104,188,191,217-220`.
Komentar compose menulis "tanpa akses balik", tetapi network bridge Docker bersifat dua arah. Proses wa-unofficial (kode
pihak ketiga Baileys, permukaan RCE terbesar di G50) dapat membuka koneksi ke semua listener Traefik (`web`, `websecure`,
`wg-https`) dan Prometheus `:9090` lewat 172.30.16.0/24.

**Dampak.** Lateral movement terbatas. Router Traefik yang memakai `internal-ops` tetap menolak (subnet bukan WireGuard
staf), tetapi API Prometheus (query metrik semua service, `/api/v1/admin` bila diaktifkan) terekspos.

**Rekomendasi.**
- Pakai network `wa-admin` hanya untuk Traefik. Scrape Prometheus lewat Traefik, atau beri wa-unofficial network scrape
  terpisah yang hanya berisi Prometheus dengan web config TLS + auth.
- Perbaiki klaim di komentar dan `check-encryption.sh`.

**Risiko:** R-S21.

### SEC-G50-07 — Low — Pelacakan device berlanjut setelah erasure akun
**Lokasi:** `behavior/behavior-privacy.ts:46-75` (bukti `device_consent` dipertahankan, `device_key` masuk `erased_subject`),
`behavior/behavior-writer.consumer.ts:51-58` (event dengan `received_at > erased_at` diterima kembali),
`behavior.service.ts:162-178` (gate tanpa Bearer = consent device terbaru).

Setelah erasure, perangkat yang dulu tertaut masih punya `device_consent.granted=true`. Event berikutnya ditulis ulang
dengan `device_key` yang sama yang baru saja dihapus dari ClickHouse. Komentar writer menulis "consent baru", tetapi tidak
ada consent baru yang diminta.

**Dampak.** Pemrosesan berlanjut atas pseudonim subjek yang meminta penghapusan. Datanya tidak dapat ditautkan ke member
lagi, sehingga dampak rendah. Namun ini bertentangan dengan maksud erasure ADR-047 §3.3 dan klaim komentar.

**Rekomendasi.** Dalam transaksi erasure, sisipkan `device_consent (granted=false, version, UNKNOWN_TEXT_SHA256)` untuk
setiap `device_key` tertaut. Klien menerima 403 `tracking_consent_required`, menghentikan pengiriman, lalu banner meminta
pilihan ulang.

**Risiko:** R-L13.

### SEC-G50-08 — Low — Anggota segmen tidak terikat PT
**Lokasi:** `20-platform-g50a.sql:151-168` (`segment_chunk`/`segment_member` tanpa `company_code`),
`campaign/segment-ingest.service.ts:28-71`, `campaign-dispatcher.consumer.ts:120-131`.

Target kampanye PT X hanya ditentukan oleh `segment_run_id` + `segment_code` dari payload Odoo. Saat ini aman karena
`run_id` dibuat per PT oleh worker (`segment-worker/efn_segment/runner.py:104`) dan field segmen di Odoo hanya ditulis bot.
Namun satu sertifikat `segment-worker` dan satu user ClickHouse tanpa row policy (`segment-access.sql:10-11`) melayani semua
PT. Bug atau penyalahgunaan worker dapat mendorong subjek PT lain ke `run_id` PT X.

**Rekomendasi.**
- Tambah `company_code` di body ingest dan kolom `segment_chunk`/`segment_member`.
- Dispatcher memfilter `company_code = p.company_code`.
- Kelak beri row policy per PT bila lebih dari satu PT supermarket aktif.

**Risiko:** R-L13, multi-company.

### SEC-G50-09 — Low (PLAUSIBLE) — Integritas bukti consent tracking
**Lokasi:** `superapp-web/lib/tracking.ts:12-23` (judul/teks/versi hard-coded klien), `behavior.service.ts:64-69` (BFF hanya
mencocokkan `version`, lalu mencatat `textSha256` dari env `CONSENT_TRACKING_TEXT_SHA256`).

Bila teks klien diubah tanpa mengganti versi, atau env BFF memakai hash teks lain, `device_consent.text_sha256` tidak
membuktikan teks yang benar-benar dilihat pengguna (UU PDP Pasal 22: bukti persetujuan).

**Rekomendasi.** Klien mengambil teks aktif dari BFF (`GET /v1/config` → `tracking_consent: {version, text}`) dan
menampilkannya apa adanya. Alternatif: test CI yang menghitung SHA-256 `TRACKING_TEXT.body` dan membandingkannya dengan
nilai env per environment.

### SEC-G50-10 — Low — Email tidak di-gate di production
**Lokasi:** `scripts/lib/deploy-validate.sh:72-74` (`EFN_GATED_FEATURES` tanpa `EMAIL_ENABLED`), `:714-718`.
README email baris 6 menyebut production menunggu Q-SM19 (penyedia SMTP, DPA, SPF/DKIM/DMARC), tetapi gate tidak memaksa
apa pun. Penyedia SMTP adalah pihak ketiga dan mungkin berada di luar negeri (UU PDP Pasal 56). Dampak saat ini kecil karena
alur pengisian alamat email belum ada, sehingga semua kirim berakhir `skipped_no_email`.

**Rekomendasi.** Masukkan `EMAIL_ENABLED` ke `EFN_GATED_FEATURES` dan wajibkan `EMAIL_DPA_REF` di production.

### SEC-G50-11 — Low — AAD kolom terenkripsi baru tidak terikat baris
**Lokasi:** `common/pii/pii-columns.ts:46-47`.
Penyerang dengan hak UPDATE DB bisa menukar `email_enc` antar-member (email promo atau tautan unsubscribe salah alamat) atau
menukar `code_enc` antar-voucher. SEC-102 sudah menetapkan `rowAad` untuk kode bearer.

**Rekomendasi.** `rowAad: pk => efn:member_email.email_enc:v2:<member_id>` dan idem `member_voucher`. Belum ada data,
jadi tidak butuh migrasi.

### SEC-G50-12 — Info — Lockout endpoint admin WA
**Lokasi:** `wa-unofficial/src/admin-server.ts:94-108`. Penghitung gagal bersifat global per proses, sehingga 10 request
salah per menit dari jalur Traefik mengunci operator. Risiko kecil karena ada mTLS + `internal-ops` di depannya. Bisa
diterima; catat di runbook.

### SEC-G50-13 — Info — Rantai pasok Baileys
**Lokasi:** `wa-unofficial/package-lock.json:4028`.
- `libsignal` di-resolve `git+ssh://…#bcea72df…`: dipin commit, tetapi tanpa hash `integrity`. npm tidak memverifikasi tarball.
- Lisensi GPL-3.0 (service internal, tidak didistribusikan, jadi kemungkinan aman; konfirmasi Legal).
- Sudah baik: Baileys exact `6.7.24`, `npm ci --ignore-scripts`, git hanya di stage build.
- Rekomendasi: vendor tarball `libsignal` ber-checksum, atau mirror internal, dan SBOM + scan di CI sebelum overlay masuk
  rantai rilis.

### SEC-G50-14 / SEC-G50-15 — Info — Keputusan DPO
- Consent `marketing` berlaku per purpose. Berhenti berlangganan email atau STOP di WA mencabut **semua** kanal promo. Ini
  aman bagi subjek, tetapi brief meminta "consent marketing per kanal". Perlu keputusan DPO/ADR-047.
- Mencabut `tracking` hanya memutus `subject_member`. Data lama di ClickHouse (≤ 90 hari) tetap ada, dan bila consent
  diberikan lagi, `subject_key` deterministik menautkannya kembali. Rekomendasi: saat withdraw, masukkan juga
  `subject_key` member ke `behavior.subject_erasure` (reason `tracking_withdrawn`) agar data hilang dari ClickHouse.
- Catatan fungsional (bukan keamanan): CHECK `event_raw.type` tidak memuat `purchase`, padahal ETL dan view segmen
  mengandalkannya (`20-platform-g50a.sql:93-95`).

## Kontrol yang sudah benar

- **Odoo**:
  - Bot `check_caller`: Internal User + grup bot saja, tepat satu company, context klien dibuang. `company_code` dicocokkan.
  - Klaim/ack memakai `FOR UPDATE SKIP LOCKED` per company. Upsert dengan advisory lock + UNIQUE.
  - Filter PII pada summary/metrics.
  - SoD pembuat/penyunting ≠ approver dicek server-side dan dikunci baris.
  - Mode voucher terkunci setelah approval. Record rule multi-company pada kedua model.
  - `payload` hanya terbaca `group_system`. Test RPC surface G50 ada.
- **Gateway RPC**: CN `segment-worker` hanya ke path #32, CN BFF hanya ke #30-#31. Path dicocokkan mentah, backend dan
  antrean terpisah.
- **BFF behavior**:
  - Consent akun dan consent device terpisah (Bearer tidak diturunkan menjadi anonim).
  - Device ID tidak disimpan mentah (HMAC). Dua pepper wajib berbeda.
  - Allowlist field, batas 50 event / 32 KB, redaksi query (NFKC, email, ≥ 6 digit), lalu redaksi ulang di ETL.
  - Rate limit per device dan per IP (IP di-hash, fail-closed saat Redis mati). Harga/stok diperkaya server.
- **Segmen**: listener mTLS terpisah (`requestCert` + `rejectUnauthorized`, CN allowlist, tanpa plaintext), body ≤ 512 KB
  streaming, idempoten per chunk (409 bila isi berbeda), subjek terhapus ditolak.
- **Kampanye**:
  - Kode voucher AES-256-GCM + HMAC, terdaftar di re-encrypt. Payload `platform.campaign` tanpa kode.
  - Inbox hanya menampilkan kode milik pemilik.
  - Publisher `user_id` diverifikasi di semua consumer. Delivery idempoten (PK kampanye × member).
- **Email**:
  - Alamat tidak lewat broker; log memakai alamat yang di-mask.
  - Token unsubscribe HMAC dengan domain separation, sekali pakai (jti dalam transaksi yang sama), di fragmen URL,
    dibandingkan `timingSafeEqual`, error tidak membedakan alasan.
  - HTML di-escape. TLS wajib.
- **WA unofficial**:
  - Idle bila flag ≠ `"true"`. Menolak start di production tanpa ref (juga di BFF + deploy-validate + izin Superadmin).
  - Secret hanya dari file, panjang minimum dicek. Sesi AES-256-GCM dengan AAD per berkas.
  - Non-root, `read_only`, `cap_drop ALL`, `no-new-privileges`. Network tanpa DB/Redis/Odoo/internet langsung.
  - Egress lewat passthrough SNI dengan satu backend tetap. Izin RabbitMQ minimal.
  - Allowlist kategori/template, verifikasi `to_hash` = HMAC(`to_e164`), at-most-once (flush sebelum kirim).
  - Kuota harian, per penerima, per menit, plus jitter. STOP memicu opt-out lokal dan pencabutan consent.
  - Log memakai nomor yang di-mask. `to_e164` hanya di `notify.wa_unofficial` (pengecualian ADR-044).
- **Data**:
  - ClickHouse `segment_reader` membaca view DEFINER per view, profil `readonly=2 CONST`, kuota.
  - ETL efn_etl hanya mendapat grant per kolom; tabel penaut identitas di-REVOKE.
  - TTL 90 hari (fakta) / 2 tahun (agregat), k-anonim istilah pencarian (`k_min ≥ 2` dipaksa).
  - Erasure ClickHouse diverifikasi count = 0 sebelum watermark maju, dan DSAR ditahan sampai selesai.
  - Fungsi SECURITY DEFINER dengan `SET search_path = pg_catalog, behavior` dan parameter dibatasi.
- **Superapp-web**:
  - Device ID hanya disimpan setelah BFF mencatat persetujuan.
  - Tombol Izinkan/Tolak setara (tanpa dark pattern). Route tulis memeriksa Origin.
  - Bearer hanya diteruskan dengan `?link=1`. Tanpa `console.*`.

## Status blokir

- **Merge**: SEC-G50-01, SEC-G50-02.
- **Production** (flag tetap OFF sampai beres): SEC-G50-03, -04, -05. Juga -06 (sebelum WA dinyalakan), -07 dan -09
  (sebelum tracking dinyalakan), dan -10 (sebelum email dinyalakan).
