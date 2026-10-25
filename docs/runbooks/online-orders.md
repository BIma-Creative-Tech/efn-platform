# Runbook: operasi pesanan online (Fase 3, pickup)

Dasar: docs/04 §10 (v5.0) — saga §10.6, queue §10.9, observability §10.11, keputusan lead L-3 (refund CS lewat CLI
BFF, teraudit). ADR-034/ADR-019 (Proposed). Audiens: on-call IT EFN, supervisor toko, Finance (refund).

> **Ringkasan manajemen.** Pelanggan membayar sekali di Super-App lalu mengambil barang di toko dengan kode ambil.
> Bila ada yang macet (pembayaran tanpa konfirmasi, selisih nominal, refund gagal), alert muncul dan runbook ini
> memandu langkahnya. POS di toko tidak terdampak oleh gangguan online. Tombol darurat: `ONLINE_ENABLED=false`.

## Peta komponen

```
Super-App ─► BFF (api-gateway) ─mTLS CN api-gateway─► odoo-rpc-gateway (10 path /json/2) ─mTLS─► odoo-tls ─► Odoo
                │  outbox → RabbitMQ order.paid / order.unpaid_closed / order.compensate / order.refund.requested
                └◄─ event Odoo (service key + HMAC odoo_online_event_hmac, replay 300 s, seq) ◄── Odoo outbox
```

- Log yang boleh dilihat: `order_id`/`part_id`, `channel_code`, state, kode error. **Dilarang** di log/tiket/chat:
  kode ambil, `qr_string`, `quote_hash`, API key/`Authorization`, JWT, nomor HP (Promtail meredaksi sebagai jaring
  pengaman: `[PICKUP_REDACTED]`, `[REDACTED]`).
- Kill switch: `ONLINE_ENABLED=false` (cart/quote/order → `503 online_unavailable`; katalog terpisah
  `ONLINE_CATALOG_ENABLED`). Ubah di `.env` host lalu `$DC up -d api-gateway`. Order yang sudah dibayar tetap
  diproses consumer/sweeper (idempoten).

## Alert

### EfnOnlineOrderStuck (critical) {#efnonlineorderstuck}
`efn_online_stuck_parts{state}` > 0 selama 15 m. Ambang: `placing` > 2 m, `paying` > 10 m, `awaiting_payment` >
`expires_at` + 5 m, refund belum final > 24 j.
1. Identifikasi bagian: log BFF `$DC logs --since 1h api-gateway | grep -E 'Online|EFN_ONLINE'` (id saja).
2. Odoo/gateway sehat? `$DC ps odoo odoo-tls odoo-rpc-gateway`; circuit (`EfnOnlineCircuitOpen`); DLQ
   (`EfnOnlineDlqNotEmpty`); consumer: `docker exec <rabbitmq> rabbitmqctl list_queues name messages consumers | grep order`.
3. Rekonsiliasi pull (`efn_online_status`) memulihkan event yang hilang otomatis; bila dead-man
   `EfnOnlineReconDeadman` juga menyala, perbaiki BFF dulu.
4. Masih macet → CLI BFF `online-resolve` (L-3; lihat README `services/api-gateway/src/cli`), selalu dengan alasan
   tertulis; jangan mengubah tabel `platform.online_*` langsung.

### EfnOnlineAmountMismatch (critical) {#efnonlineamountmismatch}
Selisih nominal di `stage` checkout/webhook/portion/odoo. Bagian otomatis `review`. **Jangan** konfirmasi/refund manual
sebelum Finance membandingkan: payment intent (provider), `amount_payable` SO Odoo (`sale_order_name`), porsi BFF.
Indikasi manipulasi (webhook palsu, harga diubah) → [security-incident.md](security-incident.md).

### EfnOnlineDlqNotEmpty (warning) {#efnonlinedlqnotempty}
Pesan saga gagal 11 kali (`x-delivery-limit` 10, diverifikasi RabbitMQ 3.13.7: delivery ke-11 di-dead-letter dengan
`x-first-death-reason: delivery_limit`). Prosedur umum inspeksi/replay: [rabbitmq-dlq.md](rabbitmq-dlq.md). Khusus
online: consumer idempoten (`processed_message`), jadi replay aman setelah penyebab diperbaiki (Odoo down, key
kedaluwarsa, bug). Pesan tanpa PII (id + `idempotency_key`).

### EfnOnlineOdooAuthFailure (critical)
→ [odoo-online-api-key.md](odoo-online-api-key.md#efnonlineodooauthfailure).

### EfnOnlineCircuitOpen (warning) {#efnonlinecircuitopen}
5 kegagalan transport beruntun ke Odoo per kanal → 30 s terbuka, quote/checkout 503. Cek berurutan:
`odoo-rpc-gateway` sehat (`wget -qO- http://127.0.0.1:8406/healthz` di container) → `odoo-tls` → Odoo
([odoo-http-5xx.md](odoo-http-5xx.md)). Sertifikat internal kedaluwarsa → [internal-pki.md](internal-pki.md).

### EfnOnlineCatalogStale (warning) {#efnonlinecatalogstale}
Sinkron katalog > 30 menit. Harga final tetap dihitung Odoo saat quote/place (pelanggan mendapat `409 price_changed`
bila berubah). Penyebab umum: circuit terbuka, key kedaluwarsa, bucket `catalog` (s3-gateway) menolak — cek
[s3-gateway-denied.md](s3-gateway-denied.md).

### EfnOnlineRefundPending (warning) {#efnonlinerefundpending}
Refund `manual_required|failed`. Pilot (provider `mock`, ADR-016/026): refund diselesaikan manual oleh Finance di luar
sistem lalu dicatat dengan `sudo efn-online-cs online-refund-settle` (aktor dari sudo, alasan, order, porsi teraudit —
L-3/SEC-98, [§CLI CS](#cli-cs)). Refund parsial CS: `sudo efn-online-cs online-refund` (nominal ≤ porsi bagian; BFF
menolak di atas porsi).

### EfnOnlineReconDeadman (warning, Loki) {#efnonlinerecondeadman}
Tidak ada `EFN_ONLINE_RECON {"status":"ok"}` 20 menit (hanya bila heartbeat pernah ada dalam 24 jam). Penyebab: BFF
mati/crash loop, job rekonsiliasi error, atau format log berubah (promtail.yml tahap 6). Bila online **sengaja**
dimatikan, silence alert ini 24 jam.

### EfnOnlineAnomalyCritical / Warning (Loki) {#efnonlineanomaly}
Baris `EFN_ONLINE_ANOMALY {"kind":…,"severity":…}` dari BFF (`OnlineAnomaly`). Kind & severity ditentukan BFF.
Telusuri id order/bagian di log BFF + Odoo; critical = perlakukan sebagai insiden sampai terbukti sebaliknya.

### EfnOdooRpcGatewayDenied (warning, Loki) {#efnodoorpcgatewaydenied}
Filter `odoo-rpc-gateway` menolak request (header respons `X-EFN-Gateway-Deny`, log `why=`):
`cn` (klien bukan CN api-gateway), `target`, `method`, `path_encoded`, `query`, `path` (di luar 10 metode §10.4.3),
`header` (header di luar allowlist), `host`, `ctype`, `auth`, `length`, `too_large` (> 256 KB).
BFF sah tidak pernah memicu ini. Setelah deploy BFF baru → bug klien (perbaiki BFF, bukan melonggarkan filter; perubahan
daftar path = perubahan kontrak §10.4.3 + `RPC_ALLOWLIST` Odoo). Tanpa deploy → container BFF diduga terkompromi →
[security-incident.md](security-incident.md). Uji ulang filter: `bash infra/odoo-rpc-gateway/test-gateway.sh` (dev/staging).

## Prosedur

- **Mematikan online darurat:** `ONLINE_ENABLED=false` + `up -d api-gateway`. Tidak perlu mematikan gateway/Odoo.
  Staging/prod: nilai berasal dari config rilis bertanda tangan (GitHub variable `ONLINE_ENABLED` → `CFG_ONLINE_ENABLED`
  di deploy.yml → deploy-validate → `.env`); jalankan workflow Deploy. Darurat tanpa rilis baru: Superadmin mengedit
  `/opt/efn/compose/.env` di host lalu `up -d api-gateway` (deploy berikutnya menimpa dengan config rilis).
- **Menyalakan di production:** butuh `PAYMENT_PROVIDER` xendit|midtrans (deploy-validate menolak `disabled`/`mock`)
  dan API key nyata untuk setiap kanal di `ONLINE_CHANNELS` (deploy.sh menolak placeholder `disabled`).
- **Rotasi HMAC event (`odoo_online_event_hmac`):** ganti nilai di GitHub Environment → Deploy me-recreate `odoo` +
  `api-gateway` bersamaan; event yang ditolak (`401 invalid_signature`) selama jendela recreate dikirim ulang outbox Odoo
  (retry 1 m → 1 j) — tidak ada event hilang. Prosedur umum: [secret-rotation.md](secret-rotation.md).
- **Bucket `catalog`:** dibuat `minio-init` (SSE-S3, privat, tanpa lifecycle; retensi = job BFF). Hanya user BFF
  (Get/Put/Delete objek + GetBucketLocation).
- **Mengaktifkan kanal PT** (G20 docs/04 §12.1: kode kanal = kode PT `GL`, `PB`, `BCR`, `BCF`; kode lama `RTL`/`FNB`
  dan kanal G19 berhifen ditolak deploy): key & user teknis per kanal
  → [odoo-online-api-key.md#kanal-baru](odoo-online-api-key.md#kanal-baru). Kanal masuk `ONLINE_CHANNELS` **terakhir**.
  Cutover dari kode lama: [odoo-online-api-key.md §8](odoo-online-api-key.md#migrasi-g20), dev: [dev-reset.md](dev-reset.md).
- **Bayar di kasir** (G20 W-30, docs/04 §12.2): batas order `counter` terbuka per member
  `ONLINE_MAX_OPEN_COUNTER_ORDERS_PER_MEMBER` (default **1**, rentang deploy 1..10 = BFF, wajib ≤
  `ONLINE_MAX_OPEN_ORDERS_PER_MEMBER`; GitHub variable dengan nama sama → config rilis). Pelanggaran → `429
  too_many_open_orders`.

## Aktivasi reservasi stok 1 jam (G19 W-02, docs/04 §11.1) {#aktivasi-reservasi-1-jam}

> **Ringkasan manajemen.** Sejak G19 pesanan online mengunci stok selama 1 jam menunggu pembayaran. Pengaturannya ada
> di dua tempat (Odoo dan Super-App) dan **harus diubah berurutan**: Odoo dulu, baru Super-App. Bila terbalik, checkout
> online ditolak sampai Odoo diperbaiki (aman, tidak ada uang/stok yang salah) — tetapi pelanggan tidak bisa memesan.

Aturan: `payment_timeout_minutes` kanal Odoo ≥ `ONLINE_PAYMENT_TIMEOUT_SECONDS`/60 + 10. Target: Odoo **70**, BFF **3600**.
Default compose BFF sejak G19 = **3600** dan `ONLINE_MAX_OPEN_ORDERS_PER_MEMBER` = **2** (L-32).

1. **Odoo dulu** (setiap kanal di `ONLINE_CHANNELS`): Penjualan Online → Kanal → `payment_timeout_minutes = 70`
   (`stock_hold_mode = confirm` untuk kanal ritel, data `efn_setup_online`). Cek read-only:
   `SELECT code, active, payment_timeout_minutes FROM efn_online_channel;` → semua kanal aktif ≥ 70.
2. **Baru BFF:** GitHub variable `ONLINE_PAYMENT_TIMEOUT_SECONDS` kosong (= default 3600) atau `3600`, dan
   `ONLINE_MAX_OPEN_ORDERS_PER_MEMBER` kosong/`2` → workflow Deploy (me-recreate `api-gateway`).
   **Lingkungan yang Odoo-nya belum 70:** set `ONLINE_PAYMENT_TIMEOUT_SECONDS=900` eksplisit di config rilis **sebelum**
   men-deploy G19, karena default compose sudah 3600.
3. Verifikasi: checkout uji → `expires_at` order ≈ +60 menit; SO Odoo tercadang (picking `assigned`). Pengaman BFF
   §10.3.5 no. 5 (`expires_at` Odoo ≥ order + 5 menit) menolak checkout bila langkah 1 terlewat — gejala: checkout
   gagal untuk semua pelanggan kanal itu setelah deploy → kembalikan BFF ke 900 atau selesaikan langkah 1.
4. **Mundur:** BFF dulu (900), baru Odoo (25). Order yang sudah dibuat tetap memakai batas lamanya.

Rate limit kode promo (G19 W-09, §11.2 no. 6): `ONLINE_RL_PROMO_MEMBER_LIMIT/WINDOW_SECONDS` (10 / 900 s) dan
`ONLINE_RL_PROMO_IP_LIMIT/WINDOW_SECONDS` (30 / 900 s) di compose base; tidak lewat config rilis (ubah = PR).

## Sizing worker Odoo: kanal online vs POS {#sizing-worker}

SEC-96 / L-16b (docs/04 §10.17): online tidak boleh menghabiskan worker HTTP Odoo yang melayani POS.

> **Keputusan pilot (pemilik proyek, 10 Okt 2026): opsi B.** Produksi pilot memakai `ODOO_WORKERS=6` = POS 4 +
> online 2: gateway `ODOO_RPC_MAXCONN=2`, pool json2 BFF `ODOO_RPC_PUBLIC_MAX_CONCURRENCY=1` +
> `ODOO_RPC_SAGA_MAX_CONCURRENCY=1` (default overlay `docker-compose.prod.yml`; dev/base tetap 4 dan 2 + 2).
> Ditinjau ulang bila gejala salah sizing di bawah muncul atau volume online naik (opsi A: worker 9, maxconn 4).

- **Batas:** `odoo-rpc-gateway` membuka maksimal `ODOO_RPC_MAXCONN` (prod **2**, dev/base 4, rentang 1..16; GitHub
  variable `ODOO_RPC_MAXCONN` → deploy-validate) koneksi paralel ke Odoo. Kelebihan mengantre di gateway (maks.
  16 menunggu, tiap request maks. 2 s); lebih dari itu langsung `503` + header `X-EFN-Gateway-Error: unavailable`.
  BFF membatasi konkurensi panggilan Odoo per replika dengan dua pool (SEC-109 / L-19f): publik (katalog/stok/quote)
  `ODOO_RPC_PUBLIC_MAX_CONCURRENCY` dan saga `ODOO_RPC_SAGA_MAX_CONCURRENCY`, lalu me-retry 503 dengan backoff +
  circuit breaker. **Aturan deploy:** PUBLIC + SAGA ≤ MAXCONN (deploy-validate menolak rilis yang melanggar).
  Catatan: prod menjalankan 2 replika BFF, jadi permintaan paralel ke gateway bisa 2 × (1 + 1) = 4 > 2 — kelebihan
  antre/503 di gateway (Odoo tetap maks. 2 koneksi online); pantau `%Tw`/`why=busy`. Uji: `bash infra/odoo-rpc-gateway/test-gateway-load.sh` (dev 10 Okt: 300 request / 200 paralel → 150
  dilayani Odoo dengan antre ≤ 90–123 ms, 150 ditolak 503 seketika, gateway tetap healthy).
- **Aturan:** `ODOO_WORKERS` ≥ worker yang dibutuhkan POS + `ODOO_RPC_MAXCONN` (+ cron berjalan di thread terpisah,
  `ODOO_MAX_CRON_THREADS`). Patokan Odoo: worker ≈ 2 × CPU + 1, ±150–300 MB RAM per worker (+ limit memori per worker).
- **Kebutuhan POS (perkiraan, ukur saat pilot):** 1 worker per ±6 sesi POS aktif yang sinkron bersamaan; minimal 4.
- **Konfigurasi prod:** `ODOO_WORKERS=6` (docker-compose.prod.yml, odoo 4 CPU / 6 GB) + opsi B di atas. Opsi lain
  yang dipertimbangkan: (A) `ODOO_WORKERS` 9 (= 2 × 4 CPU + 1 → POS 5 + online 4; RAM ±9 × 300 MB ≈ 2,7 GB + cache).
  Mengubah sizing = ubah `ODOO_WORKERS` + `ODOO_RPC_MAXCONN` + kedua pool bersamaan (GitHub variables → Deploy).

| Skenario | ODOO_WORKERS | POS | ODOO_RPC_MAXCONN (online) | Pool BFF publik + saga |
|---|---|---|---|---|
| **Pilot produksi — DIPILIH (opsi B, 10 Okt 2026)** | 6 | 4 | 2 | 1 + 1 |
| Opsi A (4 CPU, volume online naik) | 9 | 5 | 4 | 2 + 2 |
| Puncak (8 CPU) | 17 | 13 | 4–8 | ≤ maxconn |
| Dev / base compose | 2 (default Odoo dev) | — | 4 | 2 + 2 |

- **Gejala salah sizing:** antre gateway (`%Tw` di log `odoo-rpc-gateway`) sering > 1 s atau banyak `why=busy` →
  online kekurangan slot (naikkan worker + maxconn bersamaan); POS lambat saat promo online → kurangi `ODOO_RPC_MAXCONN`.
  Penolakan `why=busy` tidak memicu `EfnOdooRpcGatewayDenied` (kapasitas, bukan pelanggaran filter); pantau lewat
  `EfnOnlineCircuitOpen` dan latensi `efn_online_odoo_request_seconds`.

## CLI CS lewat wrapper host `efn-online-cs` {#cli-cs}

L-3 + SEC-98 (L-16d) + SEC-107/SEC-108 (L-19c/e): refund/resolve CS dijalankan **hanya** lewat wrapper root
`/usr/local/sbin/efn-online-cs` (sumber `scripts/host/efn-online-cs`). Wrapper menentukan dari sudo + grup host:
identitas (`EFN_CS_OPERATOR` = `SUDO_USER`), host (`EFN_CS_HOST`) dan **peran** (`EFN_CS_ROLE` = `cs` untuk grup
`efn-cs`, `approver` untuk grup `efn-cs-approver`) — tidak ada argumen `--actor`/`--role`, env pemanggil selalu
ditimpa — lalu meneruskan argumen tervalidasi ke `docker exec -i <api-gateway> node dist/cli/<subcommand>.js`.
**Alasan** dibaca dari stdin (prompt bila terminal) dan diteruskan ke CLI lewat stdin, tidak pernah lewat argv (argv
sudo tercatat di auth log). BFF menyimpan aktor `<user>/<host>` + peran di `platform.online_admin_audit`.

**Peran (SEC-107, pemisahan tugas):**
- `efn-cs` (operator CS): `online-refund`, `online-resolve --action=sync`.
- `efn-cs-approver` (supervisor CS/Finance): `online-refund-approve`, `online-refund-settle` (SEC-112: mencatat dana
  keluar), `online-resolve --action=cancel` (L-20: memicu refund) (+ subcommand lain bila perlu). BFF menolak
  persetujuan oleh pembuat refund yang sama; semua refund buatan CS menunggu persetujuan (L-19c,
  `ONLINE_CS_REFUND_APPROVAL_MIN_IDR`, default 0 = semua). **Approver tidak boleh memegang grup Odoo Sales Manager**
  ([SEC-111](#sec-111)).
- **Satu orang = satu grup.** Akun yang berada di kedua grup ditolak wrapper untuk SEMUA subcommand (journald
  `DITOLAK … satu orang satu peran`, alert `EfnOnlineCsDualRole`). Cek berkala:
  `comm -12 <(getent group efn-cs | cut -d: -f4 | tr , '\n' | sort) <(getent group efn-cs-approver | cut -d: -f4 | tr , '\n' | sort)` harus kosong.

### Pemisahan peran Odoo ↔ host (SEC-111, kontrol prosedural) {#sec-111}

**Mengapa.** Pembatalan SO online yang sudah dibayar dari Odoo hanya boleh dilakukan **Sales Manager**
(`sales_team.group_sale_manager`, SEC-95). Refund hasil pembatalan itu berstatus `approval_required` dan baru diteruskan
ke gateway setelah `online-refund-approve` oleh anggota `efn-cs-approver` di host. BFF menolak persetujuan bila pembuat =
penyetuju, tetapi pemicu dari Odoo tercatat sebagai `odoo-uid:<n>` dan **tidak bisa** dicocokkan otomatis dengan akun
host. Akibatnya satu orang yang memegang kedua peran bisa membatalkan pesanan dan menyetujui refund-nya sendiri
(empat mata gagal). Sampai ada peta identitas teknis (opsi SEC-111 a, ±1–2 hari BFF + Superadmin), kontrolnya adalah
**aturan + review akses** berikut. Risiko register: **R-S14** (docs/01 §4.4).

**Aturan (berlaku sejak `ONLINE_ENABLED=true` di lingkungan mana pun yang memakai data nyata):**
1. Orang yang memegang grup Odoo **Sales Manager** (langsung atau lewat grup yang meng-*imply*-nya) **tidak boleh**
   menjadi anggota grup host **`efn-cs-approver`**. Sebaliknya juga berlaku.
2. Operator `efn-cs` dan approver `efn-cs-approver` **bukan** anggota grup host `docker` (grup docker setara root dan bisa
   memalsukan `EFN_CS_ROLE`, catatan SEC-98).
3. Superadmin memelihara **register peran** (orang → login Odoo → akun host → grup) di sistem dokumen internal klien yang
   aksesnya terbatas, **bukan** di repo ini (memuat nama karyawan, UU PDP). Register ini yang menghubungkan login Odoo dan
   akun host milik orang yang sama; tanpa register, perbandingan otomatis tidak mungkin karena nama akun berbeda.
4. Pemberian peran baru (Sales Manager atau `efn-cs-approver`) wajib mengecek register lebih dulu. Perubahan grup Odoo
   tercatat otomatis di `efn_audit` (log kontrol akses, SEC-49); perubahan grup host tercatat di auditd/journald host.
5. Pengecualian (mis. outlet kecil dengan satu supervisor) **tidak diizinkan** lewat runbook: harus lewat keputusan
   tertulis pemilik proses Finance, dicatat di register, dan dikompensasi dengan rekonsiliasi refund mingguan oleh Finance.

**Review akses bulanan** (Superadmin + satu saksi dari Finance/Internal Audit; tanggal 1–5 setiap bulan, juga setelah
mutasi/keluarnya karyawan):

```bash
# 1) Anggota grup host (di SETIAP host yang menjalankan api-gateway; root/Superadmin)
install -d -m 0700 -o root -g root /root/efn-review
getent group efn-cs-approver | cut -d: -f4 | tr , '\n' | sort > /root/efn-review/$(date +%F)-host-approver.txt
getent group efn-cs          | cut -d: -f4 | tr , '\n' | sort > /root/efn-review/$(date +%F)-host-cs.txt
getent group docker          | cut -d: -f4 | tr , '\n' | sort > /root/efn-review/$(date +%F)-host-docker.txt
# satu orang satu peran di host + operator/approver bukan anggota docker → ketiga perintah harus tanpa output
comm -12 /root/efn-review/$(date +%F)-host-cs.txt /root/efn-review/$(date +%F)-host-approver.txt
comm -12 /root/efn-review/$(date +%F)-host-approver.txt /root/efn-review/$(date +%F)-host-docker.txt
comm -12 /root/efn-review/$(date +%F)-host-cs.txt /root/efn-review/$(date +%F)-host-docker.txt

# 2) User Odoo AKTIF yang efektif memegang Sales Manager (langsung atau lewat grup yang meng-imply-nya), read-only.
#    Superuser postgres hanya untuk operasi manusia Superadmin (aplikasi tidak memakainya, docs/04 §7.3).
docker exec -i efn-postgres-1 psql -X -q -v ON_ERROR_STOP=1 -U postgres -d efn_odoo -A -F ' ' <<'SQL' \
  > /root/efn-review/$(date +%F)-odoo-sale-manager.txt
SET default_transaction_read_only = on;
WITH RECURSIVE target AS (
  SELECT res_id AS gid FROM ir_model_data
   WHERE module = 'sales_team' AND name = 'group_sale_manager' AND model = 'res.groups'
), carriers(gid) AS (            -- grup yang (transitif) meng-imply Sales Manager, termasuk grup itu sendiri
  SELECT gid FROM target
  UNION
  SELECT r.gid FROM res_groups_implied_rel r JOIN carriers c ON r.hid = c.gid
)
SELECT DISTINCT u.id, u.login
  FROM res_users u
  JOIN res_groups_users_rel gu ON gu.uid = u.id
  JOIN carriers c ON c.gid = gu.gid
 WHERE u.active AND NOT u.share
 ORDER BY u.login;
SQL
chmod 0600 /root/efn-review/*
```

3) Cocokkan kedua daftar **lewat register peran** (langkah manual): untuk setiap login Odoo di daftar Sales Manager, cari
   orangnya di register; bila akun host orang itu ada di daftar `efn-cs-approver` → **pelanggaran**. Cabut salah satu
   peran hari itu juga (`gpasswd -d <akun> efn-cs-approver`, atau cabut grup Sales Manager lewat UI Odoo —
   [odoo-access-changes.md](odoo-access-changes.md), bukan `odoo shell`), lalu periksa refund `odoo-uid:<n>` milik user
   itu sejak review sebelumnya: `platform.online_refund.initiated_by` + `platform.online_admin_audit` (aksi
   `refund_approve`, aktor) — perlakukan sebagai insiden bila approve dilakukan oleh orang yang sama
   ([security-incident.md](security-incident.md)).
4) Cocokkan juga: setiap akun di daftar host dan setiap login Odoo masih karyawan aktif dengan peran yang sesuai (akun
   yatim → cabut).
5) **Catat hasil** di register peran: tanggal, pelaksana, saksi, jumlah akun per daftar, pelanggaran (ya/tidak) dan
   tindakannya. Berkas `/root/efn-review/*` memuat login karyawan: simpan di host (root 0600) **maks 13 bulan** (bukti
   review tahunan), jangan diunggah ke tiket/chat/repo.

Catatan:
- Nama tabel `res_groups_users_rel` dipakai `efn_audit` (terverifikasi di kode); `res_groups_implied_rel` (kolom `gid`
  → `hid`) mengikuti skema standar Odoo dan **belum diverifikasi** di DB 19.0 EFN — jalankan sekali di staging dan
  bandingkan dengan UI Odoo (Pengaturan → Pengguna, filter grup Penjualan / Administrator) sebelum dipakai sebagai bukti.
- Alternatif tanpa SQL: Odoo UI → Pengaturan → Pengguna & Perusahaan → Grup → "Sales / Administrator" → tab Pengguna
  (menampilkan anggota langsung; anggota lewat grup turunan tidak selalu tampil — karena itu SQL di atas lebih lengkap).
- Kontrol pelengkap yang sudah ada: alert `EfnOnlineCsDualRole` (satu akun host di dua grup), `EfnOnlineCsRefundVolumeHigh`,
  dan rekonsiliasi `provider-ref` refund dengan mutasi bank oleh Finance (SEC-112).

**Pasang (Superadmin, sekali per host; ulangi `install` + cek sha256 setiap wrapper berubah):**
```bash
install -o root -g root -m 0755 scripts/host/efn-online-cs /usr/local/sbin/efn-online-cs
sha256sum /usr/local/sbin/efn-online-cs          # cocokkan dengan sha256 di PR yang di-review
groupadd --system efn-cs; groupadd --system efn-cs-approver   # sekali
usermod -aG efn-cs <akun-cs>                     # operator CS: akun per orang, BUKAN anggota grup docker
usermod -aG efn-cs-approver <akun-supervisor>    # approver: orang LAIN dari operator (jangan di kedua grup)
cat > /etc/sudoers.d/efn-online-cs <<'EOF'
Cmnd_Alias EFN_ONLINE_CS = /usr/local/sbin/efn-online-cs
%efn-cs, %efn-cs-approver ALL=(root) NOPASSWD: EFN_ONLINE_CS
Defaults!EFN_ONLINE_CS env_reset, !setenv, !log_input, !log_output
EOF
chmod 0440 /etc/sudoers.d/efn-online-cs && visudo -cf /etc/sudoers.d/efn-online-cs
```
`!log_input`/`!log_output` (SEC-108): alasan diketik di stdin/terminal dan tidak boleh terekam I/O log sudo; jejak
audit resmi = journald wrapper (tanpa alasan) + `platform.online_admin_audit` (alasan, terenkripsi DB/backup).
Cabut akses: `gpasswd -d <akun> efn-cs` / `gpasswd -d <akun> efn-cs-approver`.

**Pakai (lewat SSH akun pribadi ke host; alasan diminta di prompt atau disalurkan lewat stdin):**
```bash
sudo efn-online-cs online-refund         --part=<part_id> --full [--restock]
sudo efn-online-cs online-refund         --part=<part_id> --lines=<product_id>:<qty>,... [--kind=customer_cs|ops_cancelled]
sudo efn-online-cs online-refund-settle  --refund=<refund_id> --provider-ref=<ref transfer>
sudo efn-online-cs online-resolve        --part=<part_id> --action=sync|cancel
sudo efn-online-cs online-refund-approve --refund=<refund_id>              # HANYA grup efn-cs-approver
printf '%s\n' 'tiket CS-123' | sudo efn-online-cs online-resolve --part=<part_id> --action=sync   # non-interaktif
```
- Alasan: satu baris, 3..200 karakter, berisi **nomor tiket/alasan singkat — tanpa nama/HP pelanggan**. `--reason=`
  di argv ditolak.
- Hanya 4 subcommand dan flag di atas; UUID/enum/pola divalidasi, karakter kontrol & flag ganda ditolak (exit 2).
  `--refund-id=<uuid>` pada `online-refund` = mengulang refund yang sama (idempoten).
- Output = JSON id/status/nominal dari CLI BFF; exit code CLI diteruskan.
- **Jejak:** journald tag `efn-online-cs` (`journalctl -t efn-online-cs`) → Loki lewat promtail-journal
  (`{service="host-efn-online-cs"}`, hanya entri `_UID=0` sehingga tidak bisa dipalsukan user host lain; redaksi
  standar ikut diterapkan): `MULAI/SELESAI aktor=<user> peran=<cs|approver> host=<host> cmd=<subcommand> part=<id>|refund=<id> … rc=<n>`
  dan `DITOLAK: …`. Tanpa alasan dan tanpa `--provider-ref`.
- **Alert (Loki, grup `efn-online-cs`):** `EfnOnlineCsDeniedRepeated` (≥ 3 penolakan per aktor / 15 m),
  `EfnOnlineCsDualRole` (akun di dua grup), `EfnOnlineCsRefundVolumeHigh` (> 10 refund/approve sukses per jam,
  ambang PLACEHOLDER). Alert "approve di luar jam kerja" belum: LogQL tidak punya fungsi jam; kandidat Grafana/ETL.
- Uji regresi: `scripts/host/test-efn-online-cs.sh` (CI job infra, 54 kasus termasuk `online-erasure-pending` G18, docker/logger palsu).

## Hapus akun dengan pesanan terbuka & erasure paksa (W-18, Q-P18, L-21a) {#erasure-paksa}

**Latar:** pelanggan yang meminta hapus akun saat masih punya bagian pesanan `paying|confirmed|ready|review` harus
mengonfirmasi (`?confirm_open_orders=true`). Erasure ditunda selama pesanan terbuka, **paling lama 14 hari sejak
permintaan** (`DSAR_OPEN_ORDER_MAX_DEFER_DAYS`, plafon 14). Setelah itu erasure **tetap jalan**: tautan
`online_order.member_id` dilepas. Order, bagian, dan refund tetap ada, tetapi tidak lagi bisa dikaitkan ke akun.
Konsekuensinya, pelanggan **kehilangan akses kode ambil di aplikasi**.

**Sinyal:**
- Hari 3: `EFN_ONLINE_ANOMALY kind=erasure_deferred_open_orders` warning.
- Hari 10: anomali yang sama, critical ([alert](#efnonlineanomaly)).
- Hari 14: `kind=erasure_forced_open_orders` critical, dengan `part_ids` (maks 20) + `parts_total`.
- Metrik: `efn_dsar_erasure_deferral_total{event=…}`.
- Sinyal tidak memuat HP, `member_id`, maupun `member_hash`.

**Langkah CS (peran `cs`, kecuali disebut lain):**
1. **Lihat daftar kerja** (read-only, tanpa alasan):
   `sudo efn-online-cs online-erasure-pending` (alias `erasure-pending`; peran `cs` dan `approver`; tanpa flag,
   tanpa alasan/stdin — wrapper G18). Wrapper host lama (sebelum dipasang ulang dari rilis G18) menolak subcommand
   ini → Superadmin memasang ulang `/usr/local/sbin/efn-online-cs` (cocokkan sha256 PR).
   Output JSON:
   - `deferred[]`: `request_id`, `requested_at`, `scheduled_at`, `erasure_deadline`, `escalation`, `part_ids`. Ini
     penghapusan yang masih tertunda; selesaikan sebelum `erasure_deadline`.
   - `unlinked_open_parts[]`: `part_id`, `order_id`, `status`, `company_code`, `outlet_code`, `member_unlinked_at`.
     Ini bagian terbuka yang sudah **tanpa tautan akun** (setelah erasure paksa).
   - Tidak ada HP, member, maupun kode ambil.
2. **Sebelum hari 14** (masih bertautan), selesaikan dengan prosedur biasa: pelanggan mengambil barang, atau batal
   + refund.
3. **Setelah erasure paksa:** untuk tiap `part_id`, putuskan salah satu jalur berikut.
   - **Barang belum diambil, pelanggan tidak bisa diverifikasi** → refund penuh:
     `sudo efn-online-cs online-refund --part=<part_id> --full [--restock]`
     (status `approval_required`; disetujui `online-refund-approve` oleh **approver lain**). Bila SO perlu dibatalkan:
     `online-resolve --part=<part_id> --action=cancel` (**hanya approver**, L-20).
   - **Status perlu disinkronkan dengan Odoo:** `online-resolve --part=<part_id> --action=sync`.
   - **Pelanggan datang ke toko:** serah terima di Odoo **hanya** setelah verifikasi lewat kanal lain sesuai SOP toko,
     misalnya nomor pesanan + bukti bayar dari bank/e-wallet pelanggan + identitas. Alasan dicatat di tiket.
     **Kode ambil tidak pernah ditampilkan ke CS** dan tidak dapat dipulihkan.
4. **Alasan CLI:** nomor tiket atau alasan singkat, **tanpa nama/HP pelanggan** (SOP Q-P19).
5. **Penutupan:** `unlinked_open_parts` kosong untuk bagian terkait. Rekonsiliasi refund vs mutasi bank tetap oleh
   Finance.

**Teks ke pelanggan** (respons 409, layar konfirmasi hapus akun, pernyataan usia 17+) masih **draf** dan **menunggu
persetujuan Legal (DPO sementara)**.

## Transfer bank + bukti (G26, docs/04 §17) {#transfer-bank}
**Aktivasi (per kanal, dev/staging dulu):**
1. Odoo: pasang modul `efn_online_transfer`.
2. Odoo: buat rekening bank milik partner PT, lalu jurnal Bank yang memakainya. **Finance (grup *Validate bank
   account* / admin) wajib menandai rekening itu terverifikasi** (Rekening Bank → *Send Money* / trusted). Rekening
   untrusted tidak ditampilkan ke pelanggan dan kanal menolak `bank_transfer`. Tanpa langkah ini, faktur ber-rekening
   untrusted ditolak saat diposting (CE anti-phishing), sehingga persetujuan Verifikator gagal.
3. Odoo: di **Kanal online**, isi *Jurnal transfer bank* dan tambahkan `bank_transfer` ke *Metode bayar*.
4. Odoo: beri grup **Verifikator Pembayaran** ke staf Finance/CS yang bukan kasir atau gudang (SoD `SOD-PAY-01/02`).
5. Postgres: jalankan migrasi `14-platform-g26.sql`. `scripts/deploy.sh` menjalankannya otomatis.
6. BFF: set `ONLINE_PAYMENT_METHODS` memuat `bank_transfer`. Pilot tanpa vendor gateway:
   `ONLINE_PAYMENT_METHODS=bank_transfer` + `PAYMENT_PROVIDER=disabled`.

**Operasi harian:** Verifikator membuka **Verifikasi Transfer → Bukti transfer** dan mencocokkan setiap bukti dengan
mutasi rekening PT:
- nominal harus sama persis;
- kode berita transfer sebaiknya tertulis di berita transfer;
- bukti bertanda *diduga duplikat* diperiksa lebih teliti.

Lalu Verifikator memilih **Setujui** (isi tanggal, nominal, dan referensi mutasi) atau **Tolak** (alasan dari daftar
tetap). Bukti harus diputuskan sebelum batas waktu pesanan; unggah ditutup 10 menit sebelum itu.

| Gejala | Tindakan |
|---|---|
| `late_transfer_payment` / `review transfer_paid_after_*` | Dana masuk setelah pesanan batal/kedaluwarsa. Finance mengembalikan dana (CS menghubungi pelanggan lewat kanal resmi). Lalu `efn-online-cs refund ...` → approver → `online-refund-settle --provider-ref manual:<ref mutasi>` |
| `transfer_proof_failed` (warning) berulang | Odoo/gateway tidak terjangkau saat unggah. Pelanggan mendapat 503 dan bisa mengunggah ulang. Cek `odoo-rpc-gateway` (path 21, batas body 2,5 MiB) |
| `transfer_proof_duplicate_suspect` | Informasi saja. Keputusan tetap di Verifikator |
| `refund_transfer_manual` | Refund pesanan transfer menunggu Finance mentransfer balik. Setelah itu jalankan settle manual (approver) |

## Belum / batas
- Belum ada gerbang otomatis di deploy yang membandingkan `payment_timeout_minutes` Odoo dengan
  `ONLINE_PAYMENT_TIMEOUT_SECONDS` (urutan aktivasi §[aktivasi](#aktivasi-reservasi-1-jam) masih prosedural; pengaman
  runtime = cek `expires_at` di BFF).
- Wrapper belum diuji dengan sudo + docker asli di host staging (uji CI memakai SUDO_USER tersimulasi + docker palsu).
- Gauge refund terbuka per status belum ada (alert refund memakai counter transisi 1 jam, `metric_status: proposed`).
- Dashboard Grafana online belum dibuat.
