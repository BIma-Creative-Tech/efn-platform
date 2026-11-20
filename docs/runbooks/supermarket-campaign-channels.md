# Runbook: kanal kampanye supermarket (G50)

**Cakupan:** tracking perilaku visitor, wishlist/notify-me/price alert, kampanye tertarget (Odoo `efn_campaign` + BFF +
`segment-worker`), kanal email (Mailpit di dev), WhatsApp tidak resmi (`wa-unofficial`).
**ADR:** ADR-044 (WA tidak resmi), ADR-047 (tracking/segmen/kampanye), ADR-048 (email), semuanya **Proposed**.
**Kontrak:** `docs/04_SERVICE_CONTRACTS.md` §28.6–§28.10. **Risiko:** R-L13 (profiling/PDP), R-B16 (ban WA), R-B17
(spam/anggaran), R-S21 (supply chain Baileys).

> Semua flag default `false` di semua environment. Production: tracking, kampanye, dan WA tidak resmi **OFF** sampai
> persetujuan di §7. Dokumen ini untuk dev dan staging lokal (K25-06).

## 1. Peta komponen dan flag

| Flag | Service | Overlay / file | Prasyarat minimum di dev |
|---|---|---|---|
| `BEHAVIOR_TRACKING_ENABLED` | api-gateway | base | `CONSENT_TRACKING_VERSION` + `_TEXT_SHA256`, migrasi `20-platform-g50a.sql` |
| `WISHLIST_ENABLED`, `RESTOCK_ALERT_ENABLED`, `PRICE_ALERT_ENABLED` | api-gateway | base | kanal online aktif (`ONLINE_CHANNELS`); SMD belum punya kanal online |
| `PROMO_CAMPAIGN_ENABLED` | api-gateway + Odoo `efn_campaign` | base | modul `efn_campaign` terpasang, user teknis `efn_campaign_smd`, key di `secrets/odoo_api_key_campaign_smd.txt` |
| `EMAIL_ENABLED` | api-gateway | base + Mailpit (dev) | migrasi `21-platform-g50b.sql`, email member terverifikasi (alur isi email belum dibangun) |
| `ETL_ENABLE_BEHAVIOR` | etl | base | grant `services/etl/sql/platform-behavior-grants.sql` (**terblokir**, lihat §8) |
| `SEGMENT_ENABLED` | segment-worker | `docker-compose.segment.yml` | `PROMO_CAMPAIGN_ENABLED=true`, user teknis `efn_segment_smd`, mart perilaku terisi |
| `WA_UNOFFICIAL_ENABLED` | api-gateway + wa-unofficial | `docker-compose.wa-unofficial.yml` (profil `wa-unofficial`) | secret WA nyata (`APP_ENV=dev`), pairing nomor uji (§5) |

Queue RabbitMQ (quorum + `.dlq`, `x-delivery-limit` 10) dideklarasikan broker di semua environment
(`infra/rabbitmq/rabbitmq-entrypoint.sh`): `behavior.event`, `promo.campaign.requested`, `notify.restock`,
`notify.price_alert`, `notify.email`, `notify.wa_unofficial`, `notify.wa_unofficial.result`. Broker yang sudah berjalan
sebelum G50 harus di-recreate agar definitions dimuat ulang:

```bash
cd infra/compose
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --force-recreate rabbitmq
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec rabbitmq rabbitmqctl list_queues name arguments | grep -E 'behavior|campaign|notify\.'
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec rabbitmq rabbitmqctl list_policies
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec rabbitmq rabbitmqctl list_queues name policy effective_policy_definition | grep -E 'wa_unofficial|behavior'
```

### 1.1 Retensi antrean berisi data pribadi (SEC-G50-04)
RabbitMQ **menghapus properti `expiration`** saat pesan di-dead-letter, sehingga tanpa batas di sisi broker nomor HP
(`to_e164`) di `notify.wa_unofficial.dlq` akan tersimpan selamanya. Policy broker (definitions, dipasang ulang setiap
start rabbitmq):

| Policy | Queue | `message-ttl` | `max-length` / `overflow` |
|---|---|---|---|
| `efn-wa-unofficial-dlq-retention` | `notify.wa_unofficial.dlq` | 24 jam | 1000 / `drop-head` (dibuang, tanpa DLX) |
| `efn-wa-unofficial-retention` | `notify.wa_unofficial` | 24 jam (backstop `expiration` per pesan dari BFF) | 5000 / `drop-head` (ke DLQ lalu TTL di atas) |
| `efn-behavior-dlq-retention` | `behavior.event.dlq` (subject_key pseudonim) | 7 hari (= retensi staging `behavior.event_raw`) | 100000 / `drop-head` |

Konsekuensi operasional:
- Replay `notify.wa_unofficial.dlq` hanya mungkin dalam 24 jam, dan umumnya **tidak dilakukan** (promo kedaluwarsa).
  Replay `behavior.event.dlq` dalam 7 hari.
- Erasure DSAR tidak perlu menyisir DLQ: pesan hilang paling lambat 24 jam / 7 hari (sebut di inventaris PDP).
- Jangan menambah policy operator yang polanya cocok dengan queue di atas (RabbitMQ hanya menerapkan SATU policy per
  queue, yang prioritasnya tertinggi; policy lain bisa menghapus TTL tanpa terlihat).

## 2. Persiapan dev (sekali)

```bash
# Secret baru (idempoten). APP_ENV=dev membuat nilai NYATA untuk secret WA tidak resmi (selain itu `disabled`).
APP_ENV=dev bash scripts/gen-secrets.sh
# Leaf baru: segment-worker, wa-unofficial, wa-operator (klien operator), mailpit (dev). CA dev dibuat ulang otomatis
# bila intermediate lama belum mencakup nama baru -> recreate semua container ber-TLS:
docker compose -f infra/compose/docker-compose.yml -f infra/compose/docker-compose.dev.yml \
  --profile core --profile data up -d --force-recreate
```

Migrasi PostgreSQL platform `20-platform-g50a.sql` lalu `21-platform-g50b.sql` (urutan wajib) dijalankan otomatis oleh
initdb volume baru atau `scripts/deploy.sh`; di volume dev lama jalankan manual:

```bash
for f in 20-platform-g50a.sql 21-platform-g50b.sql; do
  docker compose -f infra/compose/docker-compose.yml -f infra/compose/docker-compose.dev.yml exec -T postgres \
    psql -X -q -v ON_ERROR_STOP=1 -U postgres -d postgres -f "/docker-entrypoint-initdb.d/$f"
done
```

ClickHouse: user `efn_segment_reader` dibuat `clickhouse-init` (secret `clickhouse_password_segment`). Jalankan ulang:
`docker compose ... --profile data up -d clickhouse-init` lalu pastikan exit 0.

## 3. Menyalakan per flag (dev)

Semua flag di `infra/compose/.env` lalu `docker compose ... up -d api-gateway` (recreate). Matikan = set `false` lagi.

### 3.1 Tracking perilaku
1. Hitung SHA-256 UTF-8 **body** teks consent DRAFT di `services/superapp-web/lib/tracking.ts` (`TRACKING_TEXT`) persis
   seperti dirender (laporan frontend G50 §4). Isi `CONSENT_TRACKING_VERSION=tracking@2026-11-20` dan
   `CONSENT_TRACKING_TEXT_SHA256=<hex 64>` di `.env`. Tanpa keduanya `GET /v1/config` mengembalikan
   `features.tracking=false` walau flag aktif.
2. `BEHAVIOR_TRACKING_ENABLED=true`.
3. Verifikasi: `curl --cacert secrets/efn_ca_crt.txt https://localhost:3000/v1/config` -> `features.tracking: true`.
   Event masuk `behavior.event` lalu tabel partisi `behavior.event_raw`.

### 3.2 Wishlist, notify-me, price alert
`WISHLIST_ENABLED=true`, `RESTOCK_ALERT_ENABLED=true`, `PRICE_ALERT_ENABLED=true`. Hanya untuk kanal di
`ONLINE_CHANNELS`. Notifikasi lewat push (Expo) + email/WA bila kanal aktif dan member punya consent `marketing`.

### 3.3 Kampanye (Odoo -> BFF)
1. Odoo dev: install/upgrade `efn_campaign`; parameter sistem `efn_campaign.enabled=true` (kill switch modul).
2. Buat user teknis `efn_campaign_smd` (Internal User, grup `group_campaign_bot` saja, **tepat satu** company SMD), buat
   API key (Preferensi > Keamanan Akun), simpan ke `secrets/odoo_api_key_campaign_smd.txt` (tanpa newline). Jangan
   pernah menempelkan key di tiket/chat.
3. `.env`: `PROMO_CAMPAIGN_ENABLED=true`, `CAMPAIGN_COMPANIES=SMD`. Recreate api-gateway.
4. BFF mengklaim outbox lewat `odoo-rpc-gateway` path #30 (`efn_campaign_claim`) dan #31 (`efn_campaign_ack`); listener
   mTLS segmen `:9466` terbuka (log `Endpoint segmen HTTPS mTLS di :9466`).
5. Gagal `why=path` / `why=cn` di log gateway = path/CN salah; `X-EFN-Gateway-Deny` di respons.

### 3.4 Email (Mailpit)
1. Mailpit ikut profil `core` di dev (`docker-compose.dev.yml`), SMTP implicit TLS `mailpit:1025` dengan sertifikat CA
   dev, tanpa relai ke internet. UI: `https://localhost:8025` (percayai `secrets/efn_ca_crt.txt`).
2. `.env`: `EMAIL_ENABLED=true` (host/port/mode/from/URL unsubscribe sudah diisi default dev). Recreate api-gateway.
3. Selama alur isi/verifikasi email member belum dibangun, notifier mengembalikan `skipped_no_email` (bukan error).
4. Production (SEC-G50-10): `EMAIL_ENABLED=true` ditolak `deploy-validate` kecuali config rilis memuat
   `EMAIL_DPA_APPROVAL_REF` (nomor dokumen DPA penyedia + persetujuan DPO) **dan** Superadmin mencantumkan
   `EMAIL_ENABLED` di `EFN_ALLOW_FEATURE_ON` (`/etc/efn/deploy.conf`). Dev/staging tidak terkena gerbang ini.

### 3.5 ETL perilaku -> ClickHouse
`ETL_ENABLE_BEHAVIOR=true` **belum boleh** dinyalakan: skema PG `behavior.*` dari BFF dan asumsi ETL belum cocok (§8).

### 3.6 segment-worker
Lihat §4.

### 3.7 WA tidak resmi
Lihat §5.

## 4. segment-worker (overlay `docker-compose.segment.yml`)

1. Odoo: user teknis **terpisah** `efn_segment_smd` (grup `group_campaign_bot`, satu company SMD) + API key ->
   `secrets/odoo_api_key_segment_smd.txt`. Keputusan devsecops G50: tidak memakai key BFF (kredensial per service,
   docs/04 §7.3); gateway membatasi CN `segment-worker` ke path #32 `efn_campaign_proposal_upsert` saja.
2. `.env`: `SEGMENT_ENABLED=true` (butuh `PROMO_CAMPAIGN_ENABLED=true`, listener BFF :9466).
3. Jalankan:
   ```bash
   cd infra/compose
   F="-f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.segment.yml"
   docker compose $F --profile core --profile data up -d segment-worker
   # Uji tanpa BFF/Odoo (hitung + validasi saja):
   docker compose $F --profile core --profile data run --rm segment-worker python -m efn_segment run --dry-run
   # Sekali jalan semua PT:
   docker compose $F --profile core --profile data run --rm segment-worker python -m efn_segment run --once
   ```
4. Pantau log `EFN_SEGMENT_RUN` (status `failed`/`partial`). Belum ada metrik Prometheus.

Jaringan: `segment-ch` (ClickHouse), `segment-rpc` (odoo-rpc-gateway), `segment-bff` (api-gateway); tanpa egress.
User ClickHouse `efn_segment_reader` hanya bisa login dari subnet `segment-ch` (`HOST IP`, pola SEC-215 forecast):
`CH_SEGMENT_READER_HOST_CIDR` di `clickhouse-init`, default `<EFN_SUBNET_BASE>.17.0/24` (= ipam `segment-ch`, dicek
check-encryption; divalidasi `private_cidr_ok` /24../32). Volume ClickHouse lama: user dipersempit otomatis saat
`clickhouse-init` berikutnya (`ALTER USER ... HOST IP`). Login segment-worker ditolak (`AUTHENTICATION_FAILED`) = CIDR
tidak sama dengan subnet segment-ch (mis. nilai lama di `.env`) atau worker di mesin lain (K25-02: isi subnet WireGuard).

## 5. WA tidak resmi (overlay `docker-compose.wa-unofficial.yml`) - NON-PRODUCTION

> Penggunaan Baileys melanggar ToS WhatsApp secara sadar (ADR-044 §4.2). Nomor **terpisah** dari WABA resmi (Q-SM15).
> Hanya nomor uji di dev/staging. Pairing di production DILARANG sampai §7 lengkap.

### 5.1 Menyalakan
1. Secret nyata: `APP_ENV=dev bash scripts/gen-secrets.sh` (file berisi `disabled` diganti nilai acak). Tanpa ini user
   broker `efn-wa-unofficial` tidak dibuat dan service gagal konek.
2. Recreate broker agar user dibuat: `docker compose $F up -d --force-recreate rabbitmq`
   (dengan `F="-f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.wa-unofficial.yml"`).
3. Idle dulu (flag `false`) untuk memeriksa jalur:
   ```bash
   docker compose $F --profile core --profile observability --profile wa-unofficial up -d wa-egress-proxy wa-unofficial wa-admin-relay
   docker compose $F ps wa-unofficial wa-egress-proxy wa-admin-relay     # healthy
   ```
4. `.env`: `WA_UNOFFICIAL_ENABLED=true` lalu recreate `wa-unofficial` **dan** `api-gateway` (BFF memeriksa flag yang sama).

Egress: `wa-egress-proxy` (HAProxy, `infra/wa-egress/haproxy.cfg`) meneruskan TLS apa adanya hanya untuk SNI yang
diizinkan; `wa-unofficial` menjangkau `web.whatsapp.com` lewat `extra_hosts` ke IP proxy. **Daftar host BELUM
DIVERIFIKASI**: bila pairing/kirim gagal, lihat log proxy (`sni=... ts=...`): SNI ditolak = host belum ada di allowlist.
Tambah host hanya lewat perubahan devsecops (ACL + backend + `extra_hosts`) dengan catatan di ADR-044.

Jalur masuk admin & metrik (SEC-G50-06): Traefik dan Prometheus -> `wa-admin-relay` (HAProxy TCP, TLS passthrough,
`infra/wa-egress/admin-relay.cfg`, network `wa-ingress`, listen hanya `<EFN_SUBNET_BASE>.21.10:8443`, default
`172.30.21.10`; IP dari env `WA_RELAY_BIND_IP` compose, tanpa env relay gagal start) -> `wa-unofficial:8443`
(network `wa-admin`). wa-unofficial tidak berbagi network dengan Traefik/Prometheus dan relay tidak listen di sisi
`wa-admin`, jadi wa-unofficial yang dikompromi tidak bisa membuka koneksi ke Traefik, Prometheus, maupun relay. TLS
tetap diverifikasi end-to-end terhadap sertifikat `wa-unofficial`. Bila rute admin/scrape gagal: cek
`docker compose $F logs wa-admin-relay` (`ts=` terminasi) dan pastikan relay healthy.

### 5.2 Pairing (operator)
Endpoint admin hanya lewat Traefik `wg-https` host `wa-admin.<domain>` + IP subnet staf (`internal-ops`) + sertifikat
klien CA internal (TLS option `efn-mtls`, leaf `wa-operator`) + bearer `wa_unofficial_admin_token`. Dev:
`https://wa-admin.efn.local:9443` (tambahkan `127.0.0.1 wa-admin.efn.local` ke hosts).

```bash
umask 077; d="$(mktemp -d)"
printf 'Authorization: Bearer %s\n' "$(cat secrets/wa_unofficial_admin_token.txt)" > "$d/h"   # token tidak di argv
C=(--cacert secrets/dev_edge_crt.txt --cert secrets/tls_wa_operator_crt.txt --key secrets/tls_wa_operator_key.txt
   -H @"$d/h" -H 'Content-Type: application/json')
curl -fsS "${C[@]}" https://wa-admin.efn.local:9443/v1/admin/session
# Pairing kode (nomor uji 62...); atau {"method":"qr"} -> payload QR mentah, render LOKAL (mis. qrencode -t ansiutf8),
# jangan unggah ke layanan QR online.
curl -fsS "${C[@]}" -X POST -d '{"method":"code","phone":"62XXXXXXXXXX"}' https://wa-admin.efn.local:9443/v1/admin/session/pair
# Logout + hapus material sesi:
curl -fsS "${C[@]}" -X POST https://wa-admin.efn.local:9443/v1/admin/session/logout
rm -rf "$d"
```

Kode/QR pairing tidak pernah di-log; jangan tempel di chat.

#### Operator terkunci (429) - SEC-G50-12
Limiter gagal-autentikasi di `wa-unofficial` (`src/admin-server.ts`) bersifat **global per proses**, bukan per IP/operator:
10 request dengan token salah dalam satu menit dari siapa pun yang lolos mTLS + `internal-ops` membuat SEMUA request admin
(termasuk operator dengan token benar) dijawab `429` sampai jendela berakhir. Risiko diterima (Info) karena di depannya
ada mTLS + IP staf; perubahan limiter = kode service (bukan devsecops).
Pemulihan:
1. Tunggu >= 60 detik tanpa request lalu coba lagi (jendela per menit).
2. Bila 429 terus berulang, ada klien lain yang terus gagal: cek log Traefik untuk router `wa-admin` (IP sumber WireGuard)
   dan log `wa-unofficial` (pesan gagal auth, tanpa token). Hentikan/putus peer itu (WireGuard), atau cabut sertifikat
   klien `wa-operator` yang bocor (terbitkan ulang: `bash scripts/gen-pki.sh wa-operator`).
3. Darurat: `docker compose $F restart wa-unofficial` (penghitung di memori ikut ter-reset; sesi WA terenkripsi di volume
   tetap ada, tidak perlu pairing ulang).
4. Curiga token admin bocor -> ganti `secrets/wa_unofficial_admin_token.txt` (>= 32 karakter acak) lalu recreate
   `wa-unofficial`.

### 5.3 Mematikan darurat
`WA_UNOFFICIAL_ENABLED=false` di `.env` lalu recreate `wa-unofficial` + `api-gateway` (BFF berhenti publish; pesan di
antrean kedaluwarsa <= 24 jam -> `notify.wa_unofficial.dlq`, tidak diputar ulang). Hapus sesi: endpoint logout, atau
`docker compose $F down wa-unofficial && docker volume rm ${EFN_STACK:-efn}_wa-unofficial-state` (K25-06: volume diberi
prefix nama stack, mis. `efn-prod_wa-unofficial-state`).

Stack lain (K25-06, [multi-env-local.md](multi-env-local.md)): nama network `<EFN_STACK>_wa-*`/`<EFN_STACK>_rmq`,
subnet `<EFN_SUBNET_BASE>.13-16` dan `.21`, IP statis proxy egress `<base>.14.10` (tujuan `extra_hosts`) dan relay
`<base>.21.10`, scrape Prometheus lewat alias `wa-admin-relay-ingress` (hanya di `wa-ingress`). Semua turunan otomatis;
check-encryption memeriksanya di render `prod-stack`/`dev-stack`.

### EfnWaUnofficialDown
Kanal aktif tetapi sesi terputus > 15 menit. Dampak: promo/notify-me WA masuk DLQ; push/email tetap jalan; transaksi
tidak terdampak.
1. `GET /v1/admin/session` -> `detail` (`logged_out`, `connecting`, ...).
2. Log `wa-unofficial` dan `wa-egress-proxy` (SNI ditolak? DNS gagal? `ts=` terminasi).
3. `logged_out` = sesi dicabut dari ponsel -> pairing ulang (§5.2, non-production saja).
4. Jangan putar ulang `notify.wa_unofficial.dlq` (promo kedaluwarsa); policy broker membuang isinya setelah 24 jam
   (§1.1). Catat jumlahnya bila perlu, jangan ekspor isi (berisi nomor HP).

### EfnWaUnofficialBanned
Nomor diblokir WhatsApp (R-B16). Segera `WA_UNOFFICIAL_ENABLED=false` (§5.3), catat waktu + volume kirim 24 jam
terakhir (`efn_wa_unofficial_sent_today`), laporkan ke pemilik risiko. **Jangan** pairing nomor pengganti tanpa keputusan
tertulis pemilik proyek (ADR-044). Kanal resmi tidak terdampak (nomor terpisah).

## 6. Verifikasi cepat

```bash
bash scripts/check-encryption.sh                 # termasuk render dev/prod-segment dan dev/prod-wa-unofficial
docker compose $F exec rabbitmq rabbitmqctl list_permissions | grep -E 'efn-bff|efn-wa-unofficial'
docker compose $F logs clickhouse-init | grep efn_segment_reader   # "user (... efn_segment_reader ...) dan grant diterapkan"
```

## 7. Persetujuan WAJIB sebelum production

| Flag | Persetujuan | Bukti di konfigurasi rilis |
|---|---|---|
| `BEHAVIOR_TRACKING_ENABLED` | DPO: dasar hukum, teks banner final, DPIA, retensi (Q-SM16/17/26); entri inventaris PDP | `BEHAVIOR_TRACKING_DPO_APPROVAL_REF` + `CONSENT_TRACKING_*` final + Superadmin `EFN_ALLOW_FEATURE_ON` |
| `PROMO_CAMPAIGN_ENABLED` (+ `SEGMENT_ENABLED`) | DPO (profiling), Marketing (approver 4-eyes), Finance (anggaran voucher) | `PROMO_CAMPAIGN_APPROVAL_REF` + `EFN_ALLOW_FEATURE_ON` |
| `WA_UNOFFICIAL_ENABLED` | Penerimaan risiko tertulis pemilik proyek + Legal (Q-SM14), DPO (Q-SM18), nomor terpisah (Q-SM15), PoC host egress | `WA_UNOFFICIAL_RISK_ACCEPTANCE_REF` + `EFN_ALLOW_FEATURE_ON` + secret host diisi manual |
| `EMAIL_ENABLED` | Penyedia SMTP + DPA + SPF/DKIM/DMARC (Q-SM19); transfer lintas negara bila penyedia di luar negeri (PDP Pasal 56) | `EMAIL_DPA_APPROVAL_REF` + `EFN_ALLOW_FEATURE_ON` (SEC-G50-10) + `EMAIL_SMTP_*`, secret `email_smtp_password` di host |
| `WISHLIST/RESTOCK/PRICE_ALERT_ENABLED` | Entri PDP §2.8.12 disetujui DPO | config rilis |
| `ETL_ENABLE_BEHAVIOR` | mengikuti tracking + rekonsiliasi skema §8 | config rilis |

`scripts/lib/deploy-validate.sh` (`efn_validate_g50_config`) menolak production tanpa ref; ref = nomor dokumen
(`^[A-Za-z0-9._/-]{3,64}$`), bukan isi dokumen; `check-encryption.sh` memastikan ref tidak pernah berisi nilai di
`.env.example`/compose. Staging/prod juga butuh GitHub Environment `CLICKHOUSE_PASSWORD_SEGMENT`,
`BEHAVIOR_SUBJECT_PEPPER`, `BEHAVIOR_DEVICE_PEPPER`, `EMAIL_UNSUBSCRIBE_KEY` (`openssl rand -base64 36`) sebelum deploy
pertama setelah G50, dan intermediate PKI diterbitkan ulang (nama leaf baru, `internal-pki.md#service-baru`).

## 8. Batasan / belum terverifikasi

- **ETL perilaku terblokir:** `20-platform-g50a.sql` membuat `behavior.event_raw` tanpa kolom `company_code` dan
  `ingested_at`, dan tabel erasure bernama `behavior.erased_subject`; ETL (`platform-behavior-grants.sql`,
  `efn_etl/behavior.py`) mengharapkan `company_code`, `ingested_at`, `behavior.subject_erasure`. Grant ETL sengaja TIDAK
  dipasang di `deploy.sh` (ON_ERROR_STOP akan menggagalkan deploy) sampai lead merekonsiliasi skema BFF vs ETL.
- Daftar host egress WhatsApp, perilaku Baileys nyata, digest image Mailpit dan perintah healthcheck `readyz` dengan UI
  TLS belum diverifikasi di mesin.
- Traefik tidak bisa membatasi CN klien: router admin menerima sertifikat apa pun dari CA internal; lapis lain = IP staf
  + token admin. Usulan lanjutan: CA klien operator terpisah.
- segment-worker / wa-unofficial di mesin lain (K25-02): jalur WireGuard belum dibangun.
