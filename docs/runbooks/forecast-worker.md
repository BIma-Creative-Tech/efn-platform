# Runbook: forecast-worker (G44, forecast statistik Supermarket)

Dasar: `docs/04` §28.2 (transport & 5 metode RPC), §28.10 (flag), ADR-046 (Proposed), K25-02 (stack data terpisah),
K25-06 (profil env kecil). Komponen: `services/forecast-worker` (efn-data-ai-engineer), overlay
`infra/compose/docker-compose.forecast.yml`, filter `infra/odoo-rpc-gateway` (path #25–#29). Audiens: on-call IT EFN +
Superadmin.

> **Ringkasan manajemen.** Setiap malam (default 23:30 WIB) sebuah layanan terpisah menghitung perkiraan penjualan per
> produk per toko dan mengirim hasilnya ke Odoo. Kepala toko dan buyer tetap menyetujui setiap usulan. Bila layanan ini
> mati atau gagal, toko tetap berjualan dan reorder rule terakhir tetap berlaku; yang hilang hanya usulan baru hari itu.
> Layanan default **mati** di semua lingkungan sampai backtest lulus dan user teknis per PT dibuat.

## Fakta teknis

| Hal | Nilai |
|---|---|
| Service compose | `forecast-worker` (profil `data`, overlay `docker-compose.forecast.yml`) |
| Perintah | `python -m efn_forecast run --company ${FORECAST_COMPANIES:-SMD}` (G46d: daftar PT bergiliran, mis. `SMD,PB`; loop internal, jadwal `FORECAST_DAILY_CRON`, zona `FORECAST_TZ`) |
| Kill switch | `FORECAST_ENABLED` (default `false`; `false` = proses hidup tetapi idle) |
| Baca | ClickHouse `clickhouse:8443` HTTPS, user `efn_forecast_reader` (role `forecast_reader`, `SELECT efn_forecast.*` saja; SEC-215: login hanya dari `CH_FORECAST_READER_HOST_CIDR` = subnet `forecast-ch` 172.30.11.0/24; database `efn_forecast` hanya boleh berisi 6 view allowlist, objek lain → clickhouse-init mencabut grant dan gagal) |
| Image | `IMAGE_FORECAST_WORKER` (staging/prod: digest bertanda tangan dari manifest rilis); dependency dari `requirements.lock` ber-hash (SEC-214, [python-dependency-lock.md](python-dependency-lock.md)) |
| Alert | Loki `EfnForecastRunFailed`, `EfnForecastOdooAuthFailure`, `EfnForecastRunStale` (`loki-rules/efn-forecast.yml`); Prometheus `EfnForecastApiKey*` (`alert-rules.yml` grup `efn-forecast-key`) |
| Tulis | Odoo hanya lewat `https://odoo-rpc-gateway:8443/json/2/...` (mTLS CN `forecast-worker`) |
| Path RPC | #25 `efn.forecast.calendar/efn_forecast_calendar_upsert`, #26 `efn.forecast.run/efn_forecast_run_open`, #27 `efn.forecast.daily/efn_forecast_ingest`, #28 `efn.demand.forecast.line/efn_minmax_ingest`, #29 `efn.forecast.run/efn_forecast_run_close` |
| User teknis Odoo | `efn_forecast_<pt>` per PT (`efn_forecast_smd`, `efn_forecast_pb`) = grup `efn_demand_forecast.group_forecast_bot`, **tepat satu company** |
| Secret | `odoo_api_key_forecast_smd`, `odoo_api_key_forecast_pb` (`host:disabled`; hanya PT di `FORECAST_COMPANIES` yang wajib nyata), `tls_forecast_worker_crt/_key` (`pki`, profil client), `clickhouse_password_forecast` (`random`, juga di clickhouse-init), `efn_ca_crt` |
| Network | `forecast-ch` (clickhouse ↔ worker), `forecast-rpc` (odoo-rpc-gateway ↔ worker). Tanpa `data`/`internal`/`egress` |
| Hardening | `user 65534`, `read_only`, `tmpfs /tmp 256m`, `cap_drop ALL`, `no-new-privileges`, memori `FORECAST_MEMORY_LIMIT` (default 1536M), CPU `FORECAST_CPUS` (default 2) |
| Gateway | backend terpisah `odoo_forecast` `maxconn 1`, antrean maks. 4 × 10 s → `503 X-EFN-Gateway-Error: unavailable`; body ≤ 512 KiB |

## 1. Menyalakan di dev / staging lokal {#nyalakan}

Prasyarat: stack profil `core` + `data` berjalan; modul Odoo G44 (`efn_demand_forecast` dengan model kalender/run/daily
+ `RPC_ALLOWLIST` memuat 5 metode di atas) terpasang; view `efn_forecast.*` ada (clickhouse-init sukses).

1. Secret (dev/CI dibuat otomatis, idempoten):

   ```bash
   bash scripts/gen-secrets.sh            # clickhouse_password_forecast, placeholder odoo_api_key_forecast_smd, PKI
   ```

   Menambah `forecast-worker` ke `PKI_SERVICES` mengubah nameConstraints: di dev `gen-pki.sh` membuat ulang CA dev dan
   **semua** leaf, jadi recreate seluruh stack: `docker compose ... up -d --force-recreate`.
2. Buat user teknis + API key di Odoo (§3) dan tulis ke `secrets/odoo_api_key_forecast_smd.txt` (tanpa newline).
3. `.env` (lokal, gitignored):

   ```dotenv
   APP_ENV=dev                 # staging: APP_ENV=staging
   FORECAST_ENABLED=true
   FORECAST_MEMORY_LIMIT=768M  # K25-06: data demo kecil; staging boleh 1024M
   FORECAST_CPUS=1
   # uji cepat: jadwal beberapa menit ke depan, mis. FORECAST_DAILY_CRON="*/30 * * * *"
   ```

4. Naikkan (gateway ikut di-recreate karena network `forecast-rpc` ditambahkan):

   ```bash
   cd infra/compose
   docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.forecast.yml \
     --profile core --profile data up -d odoo-rpc-gateway forecast-worker
   docker compose ... logs -f forecast-worker
   ```

5. Verifikasi jalur gateway (opsional, uji nyata 27 kasus forecast): `bash infra/odoo-rpc-gateway/test-gateway.sh`.

Staging: sama dengan dev, file `docker-compose.prod.yml` sebagai pengganti `docker-compose.dev.yml`, secret & CA milik
staging (K25-06: tanpa berbagi secret antar-env). Production: lihat §8 (belum di rantai rilis).

## 2. Mematikan {#matikan}

`FORECAST_ENABLED=false` lalu `up -d forecast-worker` (proses idle), atau `docker compose ... stop forecast-worker`.
Dampak: tidak ada run baru → Odoo tidak membuat usulan baru; reorder rule dan proyeksi run aktif terakhir tetap dipakai
(§28.2, L-147). Sakelar per gudang `efn_forecast_engine=v1` di Odoo mengembalikan gudang ke forecast v1.

## 3. User teknis & API key Odoo `efn_forecast_<pt>` {#api-key}

Dilakukan Superadmin di Odoo (belum ada modul setup / rotasi otomatis):

1. *Settings → Users → New*: login `efn_forecast_smd`, **Internal User**, company yang diizinkan **hanya** PT SMD
   (company default SMD), grup **hanya** `Demand Forecast / Forecast bot` (`group_forecast_bot`). Tanpa grup
   Inventory/POS/admin. Odoo menolak `company_code` ≠ company user (`403 company_mismatch`).
2. API key dibuat dari sesi user itu sendiri (UI *Account Security*): set password acak sekali pakai, login di jendela privat, *Preferences → Account Security → New API Key*, nama
   `efn-forecast-SMD-<YYYYMMDD>`, durasi ≤ batas grup (ditetapkan modul; usul 35 hari seperti user teknis lain).
   Hapus password (set acak baru, jangan disimpan).
3. Tulis key ke secret **tanpa menampilkannya**:

   ```bash
   # host staging/prod lokal (root):
   install -m 0640 -o root -g efn-superadmin /dev/null /opt/efn/secrets/odoo_api_key_forecast_smd.txt
   read -rs KEY && printf '%s' "$KEY" > /opt/efn/secrets/odoo_api_key_forecast_smd.txt && unset KEY
   # dev: secrets/odoo_api_key_forecast_smd.txt dengan cara yang sama
   docker compose ... up -d --force-recreate forecast-worker
   ```

PT lain (G46d, saat ini PB): langkah sama dengan login `efn_forecast_pb`, company hanya PT PB, nama key
`efn-forecast-PB-<YYYYMMDD>`, secret `odoo_api_key_forecast_pb.txt`; lalu `.env` `FORECAST_COMPANIES=SMD,PB` dan
`FORECAST_DC_CODES=SMDDC,P004` (P004 = Central Kitchen PB). PB juga butuh `ETL_ENABLE_FORECAST_LEGACY=true` (histori
ESB, `docs/04` §28.2a). PT dijalankan berurutan pada jadwal yang sama; batch satu PT gagal tidak menghentikan PT lain.

4. Uji: log worker memuat `calendar_upsert ok` / `run_open ok` (tanpa nilai key). 401 = key salah/kedaluwarsa,
   403 `company_mismatch` = company user salah, 403 dengan header `X-EFN-Gateway-Deny` = gateway (§5).

## 4. Rotasi {#rotasi}

| Rahasia | Cara | Frekuensi |
|---|---|---|
| `odoo_api_key_forecast_<pt>` (smd, pb) | Buat key baru (§3 langkah 2–3) → recreate worker → setelah run berikut `ok`, hapus key lama di Odoo (*Account Security*). Dugaan bocor: hapus key lama **dulu**, lalu pasang baru. | Sebelum kedaluwarsa (≤ 35 hari) — manual. Pengingat otomatis: alert `EfnForecastApiKeyExpiringSoon` (sisa < 7 hari, §4a). `efn-rotate-online-key` belum mendukung `FORECAST-*`. |
| `tls_forecast_worker_crt/_key` | Otomatis: `gen-pki.sh` menerbitkan ulang leaf bila < 30 hari (deploy.sh / `make secrets`). Manual: `bash scripts/gen-pki.sh forecast-worker` lalu recreate worker. | 90 hari (leaf) |
| `clickhouse_password_forecast` | Tulis nilai baru (`openssl rand -base64 36`), jalankan ulang `clickhouse-init` (ALTER USER), recreate worker. Staging/prod: GitHub Environment `CLICKHOUSE_PASSWORD_FORECAST`. | Sesuai `secret-rotation.md` |

### 4a. Pengingat kedaluwarsa API key (SEC-215) {#pengingat-key}

Key bot forecast tidak dirotasi otomatis. Skrip host `efn-forecast-key-status` (hanya membaca `res.users.apikeys`
lewat `odoo shell`: id + tanggal kedaluwarsa, **tanpa** nilai key) menulis metrik textfile node-exporter
`efn_forecast_api_key_*` setiap hari 07:30 WIB. Deret per PT hanya ditulis bila `.env` deploy memuat
`FORECAST_ENABLED=true`, sehingga env yang mematikan forecast tidak mendapat alert.
PT yang diperiksa = `FORECAST_COMPANIES` di `.env` (kosong = SMD) yang dikenal skrip (G46d: SMD, PB). **Pasang ulang skrip
ini setiap kali PT forecast bertambah** (mis. PB, G46d); versi lama yang menemui PT tak dikenal menandai run gagal
(`EfnForecastApiKeyCheckFailed`), bukan diam-diam melewatinya.

Pasang sekali per host staging/prod (Superadmin, root):

```bash
install -o root -g root -m 0755 scripts/host/efn-forecast-key-status /usr/local/sbin/efn-forecast-key-status
sha256sum /usr/local/sbin/efn-forecast-key-status         # cocokkan dengan sha256 file di PR yang di-review
install -o root -g root -m 0644 scripts/host/systemd/efn-forecast-key-status.{service,timer} /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now efn-forecast-key-status.timer
systemctl start efn-forecast-key-status.service && journalctl -t efn-forecast-key-status -n 20
cat /var/lib/efn/node-exporter/efn_forecast_api_key.prom
```

| Alert | Arti | Tindakan |
|---|---|---|
| `EfnForecastApiKeyExpiringSoon` | Key aktif terbaru PT itu habis < 7 hari | Rotasi (tabel §4), lalu `systemctl start efn-forecast-key-status.service` agar alert padam |
| `EfnForecastApiKeyMissing` | Forecast aktif tetapi user tanpa key aktif | Buat key (§3); sampai itu run berikutnya 401 |
| `EfnForecastApiKeyNoExpiry` | User `efn_forecast_<pt>` punya key **tanpa** tanggal kedaluwarsa (metrik `efn_forecast_api_key_no_expiry_keys`, SEC-223) | §4b |
| `EfnForecastApiKeyCheckFailed` | Skrip gagal membaca (container `odoo` mati, user `efn_forecast_<pt>` hilang/nonaktif) | `journalctl -t efn-forecast-key-status`; perbaiki user/Odoo |
| `EfnForecastApiKeyCheckNotRunning` | Timer tidak jalan > 2 hari | `systemctl status efn-forecast-key-status.timer` |

Dev lokal tidak memasang skrip ini (tanpa systemd/node-exporter textfile).

### 4b. Key tanpa tanggal kedaluwarsa (SEC-223) {#key-tanpa-kedaluwarsa}

Alert `EfnForecastApiKeyNoExpiry` (warning, `alert-rules.yml` grup `efn-forecast-key`): `efn-forecast-key-status`
menemukan ≥ 1 API key user `efn_forecast_<pt>` dengan `expiration_date` kosong. Key seperti itu tidak pernah memicu
`EfnForecastApiKeyExpiringSoon`, jadi bila bocor berlaku tanpa batas waktu (R-T15). Kebijakan: key bot forecast
berdurasi ≤ 35 hari (§3 langkah 2). Penyebab umum: key dibuat oleh admin dengan hak "tanpa batas" atau lewat shell.

1. Buat key baru **berdurasi** (§3 langkah 2–3), pasang ke secret `odoo_api_key_forecast_<pt>`, recreate worker.
2. Tunggu satu run `ok` (atau uji manual §5 langkah 7).
3. Hapus key tanpa kedaluwarsa di Odoo (*Settings → Users → efn_forecast_<pt> → Account Security*). Bila tidak jelas
   siapa yang membuatnya, cek `efn_audit`/chatter user dan perlakukan sebagai dugaan pelanggaran (`security-incident.md`).
4. `systemctl start efn-forecast-key-status.service` → metrik kembali 0, alert padam.

## 5. Troubleshooting: run gagal {#run-gagal}

Gejala: di Odoo *Demand Forecast → Runs* status `failed`, atau run `open` > 6 jam (cron Odoo menandai `failed`), atau
tidak ada run baru pagi ini. Usulan hari itu tidak dibuat (bukan insiden transaksi).

1. **Proses hidup?** `docker compose ... ps forecast-worker` (healthcheck = PID 1 hidup). Restart loop → `logs` (biasanya
   konfigurasi: secret `disabled`, env tidak valid → worker menolak start).
2. **Flag**: `FORECAST_ENABLED` harus `true`; jadwal `FORECAST_DAILY_CRON`/`FORECAST_TZ` benar.
3. **Gateway menolak** (respons membawa `X-EFN-Gateway-Deny`, log gateway `why=`):

   | `why=` | Arti / tindakan |
   |---|---|
   | `cn` | Sertifikat klien bukan CN `forecast-worker` / bukan CA internal → `gen-pki.sh forecast-worker`, recreate |
   | `path` | Metode di luar 5 path (versi worker ≠ kontrak) atau rilis gateway lama → samakan dengan §28.2 |
   | `too_large` | Body > 512 KiB → turunkan `FORECAST_CHUNK_SIZE` (≤ 500 baris) |
   | `header`/`ctype`/`auth` | Bug klien worker (header di luar allowlist, bearer tidak valid) |
   | `busy` (503 `X-EFN-Gateway-Error`) | Antrean forecast penuh (>4 menunggu) → worker harus berurutan + backoff |

   Handshake gagal (tanpa respons HTTP): CA (`efn_ca_crt`) beda env, atau worker tidak di network `forecast-rpc`
   (overlay tidak ikut di `-f`).
4. **Odoo menolak**: `401` key; `403 company_mismatch`; `400 {"error": "<code>"}` validasi chunk (lihat log worker
   `rejected[{index, code}]`, mis. `warehouse_code` bukan milik company). `5xx` → `odoo-http-5xx.md`.
5. **ClickHouse**: `Authentication failed` → password/clickhouse-init, **atau** worker login dari IP di luar
   `CH_FORECAST_READER_HOST_CIDR` (SEC-215: worker harus di network `forecast-ch`, nilai env = subnet ipam
   `forecast-ch`; cek `docker inspect <worker> --format '{{json .NetworkSettings.Networks}}'`). `ACCESS_DENIED` → role
   `forecast_reader` belum terpasang atau grant dicabut pagar SEC-215 (`docker compose ... logs clickhouse-init`, cari
   `forecast` / `di luar allowlist`: objek baru di `efn_forecast` harus ditambahkan ke allowlist clickhouse-init.sh,
   forecast-access.sql dan test data-AI dalam satu PR; atau `definisi view efn_forecast menyimpang` (SEC-223): setiap
   view wajib `DEFINER = efn_forecast_definer SQL SECURITY DEFINER AS SELECT <kolom polos> FROM efn_dwh.v_fc_<nama>`,
   tanpa fungsi/JOIN/subquery/UNION; transformasi diletakkan di view `efn_dwh.v_fc_*`. Cek dengan
   `SELECT name, create_table_query FROM system.tables WHERE database = 'efn_forecast'`). Data kosong → ETL 15 menit belum mengisi mart
   (`ETL_ENABLE_ODOO`), lihat log `etl`.
6. **OOM / lambat**: `docker inspect forecast-worker --format '{{.State.OOMKilled}}'` = true → naikkan
   `FORECAST_MEMORY_LIMIT` (maks. wajar 4G; di atas itu tinjau ulang ADR-046 §7). Durasi > 2 jam = pemicu tinjau ulang.
7. Setelah perbaikan: jalankan ulang manual (run idempoten per `run_id`; chunk ganda → `duplicate_chunk: true`):
   `docker compose ... exec forecast-worker python -m efn_forecast run --company SMD --once` (opsi `--once` milik
   worker; cek `--help` versi terpasang).

### Alert run (SEC-215, ruler Loki `infra/observability/loki-rules/efn-forecast.yml`)

Worker tidak punya endpoint metrik; alert dibuat dari baris log ringkasan run (`EFN_FORECAST_RUN {...}`, logger
`efn_forecast.runner`) dan galat start batch (`ERROR efn_forecast batch <kind> tidak dapat dimulai`). Semua severity
**warning** (bukan insiden transaksi).

| Alert | Kondisi | Tindakan |
|---|---|---|
| `EfnForecastRunFailed` | Run `failed` atau batch tidak dapat dimulai dalam 2 jam terakhir (label `forecast_kind`) | Langkah 1–7 di atas; kode galat ada di field `error` log (mis. `odoo_transport:http_503`, `no_data`, `ClickHouseError`) |
| `EfnForecastOdooAuthFailure` | Run gagal dengan `error` `odoo_auth*` / `odoo_company_mismatch*` | Key kedaluwarsa/dicabut atau company user salah → §3/§4 |

Kueri Grafana (Explore → Loki) untuk melihat riwayat run:
`{service="forecast-worker"} |= "EFN_FORECAST_RUN"`.

## 5a. Troubleshooting: tidak ada run sukses (basi) {#run-basi}

`EfnForecastRunStale`: tidak ada `EFN_FORECAST_RUN` harian `status":"ok"` > 26 jam (tahan 30 menit), **dan** worker
terbukti aktif (`FORECAST_ENABLED=true`) dalam 4 hari terakhir. Penyebab umum:

1. Worker crash loop / OOM (`docker compose ... ps forecast-worker`, `docker inspect ... OOMKilled`) → §5 langkah 1 & 6.
2. Batch hang (> 26 jam tanpa ringkasan): `docker compose ... logs --since 30h forecast-worker | tail -n 50`; restart
   worker; run open > 6 jam ditutup `failed` oleh cron Odoo.
3. Jadwal salah: `FORECAST_DAILY_CRON` / `FORECAST_TZ` (log `batch berikutnya <waktu>` menunjukkan jadwal berikutnya).
4. Forecast **sengaja** dimatikan setelah aktif: alert ini bisa menyala sampai 4 hari (jendela bukti aktif). Silence
   di Alertmanager dengan alasan "forecast dimatikan", sampai 4 hari.

Batas: worker yang mati/hang > 4 hari berhenti memicu alert ini; tertangkap `EfnServiceDown` (container) dan
pemeriksaan pagi planner di menu *Demand Forecast → Runs*.

## 6. Sizing worker Odoo {#sizing}

Backend gateway `odoo_forecast` memakai **maks. 1 koneksi** ke Odoo, terpisah dari `ODOO_RPC_MAXCONN` (kanal online).
Selama batch, `ODOO_WORKERS` harus ≥ POS + `ODOO_RPC_MAXCONN` + 1 (prod overlay saat ini 6 = POS 4 + online 2 → batch
memakai worker POS bila toko masih buka pukul 23:30). Supermarket yang buka sampai larut: geser `FORECAST_DAILY_CRON`
atau naikkan `ODOO_WORKERS` (lihat `online-orders.md#sizing-worker`).

## 7. Antar-mesin (K25-02) — belum dibangun {#antar-mesin}

Overlay ini mengasumsikan satu host. Bila `efn-data` dipindah ke mesin lain: gateway perlu listener di antarmuka
WireGuard (bukan port publik) dengan allowlist IP mesin data, worker memakai `extra_hosts: odoo-rpc-gateway:<IP WG>`
(nama tetap = SAN sertifikat, verify-full), dan ClickHouse lokal di mesin data. Bila ClickHouse tetap di mesin inti,
`CH_FORECAST_READER_HOST_CIDR` diganti subnet/IP WireGuard mesin data (SEC-215; check-encryption memaksa sama dengan
`forecast-ch` hanya untuk render satu host). Prefix wajib /24../32 (SEC-223; disarankan `/32` = IP WG mesin data):
/16../23 ditolak `deploy-validate.sh`, `check-encryption.sh`, dan `clickhouse-init.sh` karena 172.30.0.0/16 mencakup
semua network EFN. Desain + uji = tindak lanjut devsecops.

## 8. Production / staging lewat rilis bertanda tangan {#produksi}

Sejak 19 Nov 2026 forecast-worker ikut rantai rilis: `deploy.yml` memverifikasi cosign image `forecast-worker` dan
menulis `IMAGE_FORECAST_WORKER` ke manifest; `efn-deploy` memverifikasi ulang di host; `deploy.sh` menulis `.env` dan
**menyertakan overlay `docker-compose.forecast.yml` hanya bila `FORECAST_ENABLED=true`**. Variabel (GitHub Environment
`vars`, masuk manifest `config` bertanda tangan; kosong = default compose): `FORECAST_ENABLED`, `FORECAST_DAILY_CRON`,
`FORECAST_TZ`, `FORECAST_HORIZON_DAYS`, `FORECAST_CHUNK_SIZE`, `FORECAST_ODOO_RPC_TIMEOUT_SECONDS`,
`FORECAST_MEMORY_LIMIT`, `FORECAST_CPUS`, `CH_FORECAST_READER_HOST_CIDR` (rentang divalidasi `deploy-validate.sh`).

Gerbang saat `FORECAST_ENABLED=true`:

- Production: `FORECAST_ENABLED` wajib tercantum di `EFN_ALLOW_FEATURE_ON` (`/etc/efn/deploy.conf`, Superadmin) setelah
  backtest G44 lulus (ADR-046) dan user teknis dibuat. Manifest saja ditolak.
- `COMPOSE_PROFILES` memuat `data`; setiap PT di `EFN_FORECAST_COMPANY_CODES` (saat ini `SMD`) punya
  `/opt/efn/secrets/odoo_api_key_forecast_<pt>.txt` nyata (bukan `disabled`), dibuat §3. Tanpa itu deploy berhenti.
- `FORECAST_ENABLED=false` (default): overlay tidak dipasang, container dihapus `--remove-orphans`, key tidak di-mount.

Sebelum deploy staging/prod pertama yang memuat G44:

- Superadmin **memasang ulang wrapper** `/usr/local/sbin/efn-deploy` dari rilis ini (IMAGE_VARS bertambah 1 → manifest
  lama/baru tidak kompatibel; key-custody.md §4) dan memasang `efn-forecast-key-status` (§4a).
- Superadmin **menerbitkan ulang intermediate** (nameConstraints memuat `forecast-worker`), lihat
  [internal-pki.md](internal-pki.md#service-baru) — tanpa ini `deploy.sh` berhenti di langkah PKI.
- GitHub Environment `CLICKHOUSE_PASSWORD_FORECAST` diisi (clickhouse-init me-mount-nya di semua env).
- Migrasi `19-platform-g44.sql` (CHECK kode PT + `SMD`) wajib ada di bundel (deploy.sh menolak bila hilang).
- `services/forecast-worker/requirements.lock` sudah dibuat (SEC-214); tanpa itu build image gagal dan deploy.yml tidak
  menemukan image bertanda tangan untuk tag rilis.

Belum: rotasi otomatis key (`efn-rotate-online-key --channel=FORECAST-SMD`).
