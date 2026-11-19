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
| Perintah | `python -m efn_forecast run --company SMD` (loop internal, jadwal `FORECAST_DAILY_CRON`, zona `FORECAST_TZ`) |
| Kill switch | `FORECAST_ENABLED` (default `false`; `false` = proses hidup tetapi idle) |
| Baca | ClickHouse `clickhouse:8443` HTTPS, user `efn_forecast_reader` (role `forecast_reader`, `SELECT efn_forecast.*` saja) |
| Tulis | Odoo hanya lewat `https://odoo-rpc-gateway:8443/json/2/...` (mTLS CN `forecast-worker`) |
| Path RPC | #25 `efn.forecast.calendar/efn_forecast_calendar_upsert`, #26 `efn.forecast.run/efn_forecast_run_open`, #27 `efn.forecast.daily/efn_forecast_ingest`, #28 `efn.demand.forecast.line/efn_minmax_ingest`, #29 `efn.forecast.run/efn_forecast_run_close` |
| User teknis Odoo | `efn_forecast_smd` = grup `efn_demand_forecast.group_forecast_bot`, **tepat satu company** (SMD) |
| Secret | `odoo_api_key_forecast_smd` (`host:disabled`), `tls_forecast_worker_crt/_key` (`pki`, profil client), `clickhouse_password_forecast` (`random`, juga di clickhouse-init), `efn_ca_crt` |
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

## 3. User teknis & API key Odoo `efn_forecast_smd` {#api-key}

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

4. Uji: log worker memuat `calendar_upsert ok` / `run_open ok` (tanpa nilai key). 401 = key salah/kedaluwarsa,
   403 `company_mismatch` = company user salah, 403 dengan header `X-EFN-Gateway-Deny` = gateway (§5).

## 4. Rotasi {#rotasi}

| Rahasia | Cara | Frekuensi |
|---|---|---|
| `odoo_api_key_forecast_smd` | Buat key baru (§3 langkah 2–3) → recreate worker → setelah run berikut `ok`, hapus key lama di Odoo (*Account Security*). Dugaan bocor: hapus key lama **dulu**, lalu pasang baru. | Sebelum kedaluwarsa (≤ 35 hari) — manual, pasang pengingat. `efn-rotate-online-key` belum mendukung `FORECAST-*` (§8). |
| `tls_forecast_worker_crt/_key` | Otomatis: `gen-pki.sh` menerbitkan ulang leaf bila < 30 hari (deploy.sh / `make secrets`). Manual: `bash scripts/gen-pki.sh forecast-worker` lalu recreate worker. | 90 hari (leaf) |
| `clickhouse_password_forecast` | Tulis nilai baru (`openssl rand -base64 36`), jalankan ulang `clickhouse-init` (ALTER USER), recreate worker. Staging/prod: GitHub Environment `CLICKHOUSE_PASSWORD_FORECAST`. | Sesuai `secret-rotation.md` |

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
5. **ClickHouse**: `Authentication failed` → password/clickhouse-init; `ACCESS_DENIED` → role `forecast_reader` belum
   terpasang (`docker compose ... logs clickhouse-init`, cari `forecast`). Data kosong → ETL 15 menit belum mengisi mart
   (`ETL_ENABLE_ODOO`), lihat log `etl`.
6. **OOM / lambat**: `docker inspect forecast-worker --format '{{.State.OOMKilled}}'` = true → naikkan
   `FORECAST_MEMORY_LIMIT` (maks. wajar 4G; di atas itu tinjau ulang ADR-046 §7). Durasi > 2 jam = pemicu tinjau ulang.
7. Setelah perbaikan: jalankan ulang manual (run idempoten per `run_id`; chunk ganda → `duplicate_chunk: true`):
   `docker compose ... exec forecast-worker python -m efn_forecast run --company SMD --once` (opsi `--once` milik
   worker; cek `--help` versi terpasang).

Alert Prometheus `EfnForecastRunFailed` (ADR-046 §3.1) **belum ada**: worker tidak punya endpoint metrik dan status run
ada di Odoo. Sementara: pemeriksaan pagi oleh planner di menu Runs.

## 6. Sizing worker Odoo {#sizing}

Backend gateway `odoo_forecast` memakai **maks. 1 koneksi** ke Odoo, terpisah dari `ODOO_RPC_MAXCONN` (kanal online).
Selama batch, `ODOO_WORKERS` harus ≥ POS + `ODOO_RPC_MAXCONN` + 1 (prod overlay saat ini 6 = POS 4 + online 2 → batch
memakai worker POS bila toko masih buka pukul 23:30). Supermarket yang buka sampai larut: geser `FORECAST_DAILY_CRON`
atau naikkan `ODOO_WORKERS` (lihat `online-orders.md#sizing-worker`).

## 7. Antar-mesin (K25-02) — belum dibangun {#antar-mesin}

Overlay ini mengasumsikan satu host. Bila `efn-data` dipindah ke mesin lain: gateway perlu listener di antarmuka
WireGuard (bukan port publik) dengan allowlist IP mesin data, worker memakai `extra_hosts: odoo-rpc-gateway:<IP WG>`
(nama tetap = SAN sertifikat, verify-full), dan ClickHouse lokal di mesin data. Desain + uji = tindak lanjut devsecops.

## 8. Production — belum di rantai rilis {#produksi}

Belum: `IMAGE_FORECAST_WORKER` di `deploy.yml`/`efn-deploy`/`deploy.sh`, `FORECAST_*` di `EFN_RELEASE_CONFIG_VARS`
(deploy-validate), deploy.sh menyertakan overlay bila `FORECAST_ENABLED=true`, gerbang `EFN_ALLOW_FEATURE_ON`, dan
`efn-rotate-online-key --channel=FORECAST-SMD`. Sebelum deploy staging/prod pertama yang memuat G44:

- Superadmin **menerbitkan ulang intermediate** (nameConstraints memuat `forecast-worker`), lihat
  [internal-pki.md](internal-pki.md#service-baru) — tanpa ini `deploy.sh` berhenti di langkah PKI.
- GitHub Environment `CLICKHOUSE_PASSWORD_FORECAST` diisi (clickhouse-init me-mount-nya di semua env).
- Migrasi `19-platform-g44.sql` (CHECK kode PT + `SMD`) wajib ada di bundel (deploy.sh menolak bila hilang).
