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

## Sizing worker Odoo: kanal online vs POS {#sizing-worker}

SEC-96 / L-16b (docs/04 §10.17): online tidak boleh menghabiskan worker HTTP Odoo yang melayani POS.

- **Batas:** `odoo-rpc-gateway` membuka maksimal `ODOO_RPC_MAXCONN` (default **4**, rentang 1..16; GitHub variable
  `ODOO_RPC_MAXCONN` → deploy-validate) koneksi paralel ke Odoo. Request ke-5 dan seterusnya mengantre di gateway (maks.
  16 menunggu, tiap request maks. 2 s); lebih dari itu langsung `503` + header `X-EFN-Gateway-Error: unavailable`.
  BFF juga membatasi konkurensi global panggilan Odoo (default 4, L-16b) dan me-retry 503 dengan backoff + circuit
  breaker. Uji: `bash infra/odoo-rpc-gateway/test-gateway-load.sh` (dev 10 Okt: 300 request / 200 paralel → 150
  dilayani Odoo dengan antre ≤ 90–123 ms, 150 ditolak 503 seketika, gateway tetap healthy).
- **Aturan:** `ODOO_WORKERS` ≥ worker yang dibutuhkan POS + `ODOO_RPC_MAXCONN` (+ cron berjalan di thread terpisah,
  `ODOO_MAX_CRON_THREADS`). Patokan Odoo: worker ≈ 2 × CPU + 1, ±150–300 MB RAM per worker (+ limit memori per worker).
- **Kebutuhan POS (perkiraan, ukur saat pilot):** 1 worker per ±6 sesi POS aktif yang sinkron bersamaan; minimal 4.
- **Konfigurasi saat ini:** prod `ODOO_WORKERS=6` (docker-compose.prod.yml, odoo 4 CPU / 6 GB). Dengan
  `ODOO_RPC_MAXCONN=4` hanya 2 worker tersisa untuk POS — **tidak cukup**. Sebelum `ONLINE_ENABLED=true` di production
  pilih salah satu: (a) naikkan `ODOO_WORKERS` ke 9 (= 2 × 4 CPU + 1 → POS 5 + online 4; RAM ±9 × 300 MB ≈ 2,7 GB +
  cache, masih di bawah limit 6 GB), atau (b) turunkan `ODOO_RPC_MAXCONN` ke 2 (POS 4 + online 2) selama pilot volume
  rendah. Keputusan kapasitas = lead/klien (catat di laporan pilot).

| Skenario | ODOO_WORKERS | POS | ODOO_RPC_MAXCONN (online) |
|---|---|---|---|
| Pilot, online volume rendah | 6 | 4 | 2 |
| Rekomendasi pilot (4 CPU) | 9 | 5 | 4 (default) |
| Puncak (8 CPU) | 17 | 13 | 4–8 |

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
  `ONLINE_CS_REFUND_APPROVAL_MIN_IDR`, default 0 = semua).
- **Satu orang = satu grup.** Akun yang berada di kedua grup ditolak wrapper untuk SEMUA subcommand (journald
  `DITOLAK … satu orang satu peran`, alert `EfnOnlineCsDualRole`). Cek berkala:
  `comm -12 <(getent group efn-cs | cut -d: -f4 | tr , '\n' | sort) <(getent group efn-cs-approver | cut -d: -f4 | tr , '\n' | sort)` harus kosong.

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
- Uji regresi: `scripts/host/test-efn-online-cs.sh` (CI job infra, 45 kasus, docker/logger palsu).

## Belum / batas
- Wrapper belum diuji dengan sudo + docker asli di host staging (uji CI memakai SUDO_USER tersimulasi + docker palsu).
- Gauge refund terbuka per status belum ada (alert refund memakai counter transisi 1 jam, `metric_status: proposed`).
- Dashboard Grafana online belum dibuat.
