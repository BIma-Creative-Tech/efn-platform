# Runbook: tiga environment lokal (K25-06) — `efn-prod`, `efn-stg`, `efn-dev`

Status 20 Nov 2026: hanya **prod lokal** (`efn-prod`, gladi produksi) yang dinyalakan. `efn-stg` dan `efn-dev` akan
menjadi **cermin prod** (overlay yang sama, identitas stack berbeda). Prod lokal = gladi, **bukan go-live**: LUKS,
backup off-site, domain publik, WireGuard, dan WhatsApp produksi tetap butuh server nyata (CLAUDE.md K25-06). Tidak ada
deploy ke VPS; `scripts/deploy.sh` (server) tetap hanya mendukung stack default `efn` (ditolak bila `EFN_STACK`/
`EFN_SUBNET_BASE` lain, `efn_validate_stack_identity server`).

Terkait: ADR-004, `docs/02` §4, [dev-stack-boot.md](dev-stack-boot.md), [internal-pki.md](internal-pki.md),
[forecast-worker.md](forecast-worker.md), [backup-restore.md](backup-restore.md).

## 1. Identitas stack: dua variabel

| Variabel | Default | Mengatur | Catatan |
|---|---|---|---|
| `EFN_STACK` | `efn` | nama project compose (`name:`), nama network `<stack>_<network>`, label `traefik.docker.network`, constraint discovery Traefik + filter Promtail (label `com.docker.compose.project`), tag image lokal default `ghcr.io/<stack>/<svc>:dev` (overlay dev: `<stack>/<svc>:dev`), container `<stack>-<svc>-1`, volume `<stack>_<volume>` | pola `^[a-z][a-z0-9-]*[a-z0-9]$`, 2..32, **tanpa `_`** |
| `EFN_SUBNET_BASE` | `172.30` | dua oktet pertama subnet semua network: `<base>.<n>.0/24` | privat RFC1918, bukan `10.88`/`10.89` (WireGuard) |

Peta oktet ketiga (tetap): 0 `edge`, 1 `internal`, 2 `data`, 3 `egress`, 4 `socket-proxy`, 5 `cache`, 6 `objstore`,
7 `esb`, 8 `egress-esb`, 9 `insight-ingress`, 10 `insight-data`, 11 `forecast-ch`, 12 `forecast-rpc`, 13 `rmq`,
14 `wa-egress` (proxy egress IP statis `.14.10`), 15 `wa-internet`, 16 `wa-admin`, 17 `segment-ch`, 18 `segment-rpc`,
19 `segment-bff`, 20 `mail` (dev), 21 `wa-ingress` (relay admin WA IP statis `.21.10`).

Turunan otomatis (tidak perlu diisi):

- `TRUSTED_PROXIES` BFF = `<base>.1.0/24` (bila tidak diset di `.env`; bila diset, wajib memuat subnet itu — dicek).
- HOST user ClickHouse (SEC-215): `CH_FORECAST_READER_HOST_CIDR` = `<base>.11.0/24`, `CH_SEGMENT_READER_HOST_CIDR` =
  `<base>.17.0/24` (bila overlay ter-render, wajib sama dengan ipam `forecast-ch`/`segment-ch` — dicek). Hapus nilai lama
  `CH_FORECAST_READER_HOST_CIDR=172.30.11.0/24` dari `.env` stack ber-base lain.
- WA tidak resmi: IP statis relay `<base>.21.10` (env `WA_RELAY_BIND_IP` untuk `admin-relay.cfg`, tanpa env = gagal start)
  dan proxy egress `<base>.14.10` (`extra_hosts`); Prometheus men-scrape alias `wa-admin-relay-ingress`.
- `pg_hba.conf` memakai `samenet` (postgres hanya di network `data`, dicek CI) — tidak ada CIDR literal.
- Prometheus men-scrape `api-gateway-metrics` / `ocr-worker-metrics` (alias ber-scope network `internal`) dengan
  relabel keep oktet ketiga `1`; tidak ada nama network di `prometheus.yml`.
- `traefik.yml` dan `promtail.yml` adalah **template** (`@EFN_STACK@`); `infra/stack/render-config.sh` merendernya ke
  tmpfs `/tmp` container saat start. Tanpa ini Traefik/Promtail stack A ikut menemukan container stack B (dan project
  Docker lain) lewat `docker-socket-proxy`, yang melihat semua container host.

Aturan: **set `EFN_STACK` di `.env`, jangan pakai `docker compose -p`**. `-p` yang berbeda dari `EFN_STACK` membuat
Traefik/Promtail tidak menemukan apa pun (constraint project) dan network bentrok dengan stack lain (compose menolak
network milik project lain). `scripts/check-encryption.sh` menolak render dengan nama project != `EFN_STACK`.

Yang sengaja **tidak** diparameterisasi (lihat §8): path secret `../../secrets/*.txt` (pakai satu worktree per env),
port host overlay dev, SAN sertifikat edge `*.efn.local`, `NODE_EXPORTER_TEXTFILE_DIR` (isi per env di `.env`).

## 2. Satu worktree per environment

Secret, CA internal, `.env`, dan state build terpisah per env dengan cara paling sederhana: worktree git sendiri.
`scripts/gen-secrets.sh` membuat root + intermediate + leaf **per worktree** (`secrets/pki-ca/`), jadi CA prod lokal
tidak dipakai env lain dan sebaliknya (tanpa berbagi secret, K25-06).

```bash
# dari clone utama (Git Bash). Jangan set MSYS_NO_PATHCONV untuk git/openssl.
git worktree add ../efn-prod main          # gladi prod lokal (branch/tag yang diuji)
cd ../efn-prod
bash scripts/gen-secrets.sh                # secret + PKI + sertifikat edge self-signed *.efn.local (90 hari)
cp infra/compose/.env.example infra/compose/.env
```

Lalu edit `infra/compose/.env` (§3). Folder `secrets/` dan `.env` di-gitignore; jangan menyalin isinya antar worktree.
Hapus env = `docker compose ... down -v` di worktree itu, lalu `git worktree remove`.

## 3. `.env` prod lokal (`efn-prod`)

Mulai dari `.env.example`, ubah/tambahkan baris berikut (nilai non-rahasia saja):

```dotenv
EFN_STACK=efn-prod
EFN_SUBNET_BASE=10.250              # rentang 172.16-31 dipakai project lain di host pengembang
APP_ENV=production                  # aturan prod berlaku (gladi)
NODE_ENV=production
TRAEFIK_DOMAIN=efn.local            # SAN sertifikat edge lokal = efn.local + *.efn.local (§8)
PAYMENT_PROVIDER=disabled           # prod menolak mock; vendor belum dipilih (ADR-016)
EFN_PUBLIC_BIND=127.0.0.1           # 80/443 hanya loopback laptop (bukan LAN)
EFN_WG_BIND=127.0.0.1               # wg-https (erp./bi./ops./status./insight.) di 127.0.0.1:9443
EFN_WG_HTTPS_PORT=9443
EFN_INTERNAL_EXTRA_CIDR=10.250.0.0/24   # subnet edge = sumber koneksi port publish (lihat §4)
NODE_EXPORTER_TEXTFILE_DIR=/var/lib/efn-prod/node-exporter
COMPOSE_PROFILES=core,data,observability,ops   # tanpa ai di 12 GB (§7)
FORECAST_ENABLED=false              # default SEMUA env (§5.4)
```

Hapus/biarkan terkomentar: `IMAGE_*` (termasuk `IMAGE_FORECAST_WORKER`, `IMAGE_SEGMENT_WORKER`, `IMAGE_WA_UNOFFICIAL`;
default compose sudah per stack), `TRUSTED_PROXIES`, `CH_FORECAST_READER_HOST_CIDR`, `CH_SEGMENT_READER_HOST_CIDR`
(turunan subnet). Baris
`IMAGE_*=ghcr.io/efn/...:dev` peninggalan `.env` lama membuat stack berbagi image dengan `efn` → ditolak check-encryption
(aturan h).

Hosts Windows (`C:\Windows\System32\drivers\etc\hosts`, admin): `127.0.0.1 efn.local app.efn.local api.efn.local
mart.efn.local kitchen.efn.local erp.efn.local bi.efn.local ops.efn.local status.efn.local insight.efn.local`.

## 4. Sertifikat edge lokal tanpa ACME + allowlist

Host tanpa domain publik tidak bisa mendapat sertifikat ACME (Let's Encrypt menolak `.local`). Overlay
`infra/compose/docker-compose.local-edge.yml` me-mount sertifikat self-signed `secrets/dev_edge_*.txt` (dibuat
`gen-secrets.sh`) ke Traefik — sama dengan dev, tanpa mengubah aturan prod lain. Router `websecure` masih
mereferensikan resolver `letsencrypt`: Traefik mencatat kegagalan ACME lalu melayani sertifikat edge (perilaku dev).
File ini **tidak pernah** disertakan `deploy.sh`.

`internal-only`/`internal-ops` (erp., bi., ops., status., insight.) mengizinkan subnet WireGuard +
`EFN_INTERNAL_EXTRA_CIDR`. Di laptop koneksi lewat port publish datang dari gateway bridge network `edge`
(`<base>.0.1`), jadi isi subnet `edge` (`10.250.0.0/24`; isinya hanya Traefik + gateway). Verifikasi: access log
Traefik (`docker logs efn-prod-traefik-1 | grep erp.efn.local`) field `ClientHost`; 403 = CIDR belum cocok. Dev overlay
tetap default `172.16.0.0/12` dan bisa ditimpa `.env` yang sama.

## 5. Menyalakan `efn-prod` (urutan seed)

```bash
cd ../efn-prod
DC="docker compose -f infra/compose/docker-compose.yml -f infra/compose/docker-compose.prod.yml \
  -f infra/compose/docker-compose.local-edge.yml --env-file infra/compose/.env"
$DC config -q && bash scripts/check-encryption.sh      # render prod-stack/dev-stack (+ esb/forecast/segment/wa-unofficial)
$DC --profile core --profile data build                 # image ghcr.io/efn-prod/<svc>:dev, tidak menimpa stack lain
```

### 5.1 Data layer dan role

```bash
$DC --profile core up -d postgres
# volume baru: 00-roles.sh + migrasi >= 04 jalan otomatis lewat initdb. Volume lama (idempoten, pola deploy.sh):
MSYS_NO_PATHCONV=1 $DC exec -T postgres bash /docker-entrypoint-initdb.d/00-roles.sh
for f in infra/postgres/init/[0-9][0-9]-*.sql; do n=$(basename "$f"); [ "${n%%-*}" -ge 04 ] 2>/dev/null || continue
  MSYS_NO_PATHCONV=1 $DC exec -T postgres psql -X -q -v ON_ERROR_STOP=1 -U postgres -d postgres -f "/docker-entrypoint-initdb.d/$n"; done
```

### 5.2 Odoo (DB, modul) — selalu bersama sidecar

```bash
$DC --profile core run --rm --no-deps odoo odoo -d efn_odoo -i base --stop-after-init --without-demo=all
$DC --profile core run --rm --no-deps odoo odoo -d efn_odoo --stop-after-init --without-demo=all -i <daftar modul efn_*>
$DC --profile core up -d odoo odoo-tls odoo-ws-tls odoo-rpc-gateway
```

Pelajaran gladi 20 Nov 2026:

- **`odoo-tls` wajib untuk jalur BFF → Odoo.** `odoo-rpc-gateway` hanya berbicara mTLS ke `odoo-tls:8443`
  (CN `odoo-rpc-gateway`); `up -d odoo` / `--no-deps` tanpa sidecar = BFF 503 dari gateway walau Odoo sehat. Sertakan
  `odoo odoo-tls odoo-ws-tls` di setiap perintah `up`/`stop`/`--force-recreate`.
- **Sidecar `*-tls` di-recreate bersama induknya.** Sidecar memakai `network_mode: service:<app>`; recreate/restart
  induk saja membuat namespace sidecar basi (sidecar `unhealthy`, koneksi putus). Pasangan: `odoo`+`odoo-tls`+
  `odoo-ws-tls`, `superapp-web`+`superapp-web-tls`, `metabase`+`metabase-tls`, `insight-web`+`insight-web-tls`,
  `loki`+`loki-tls`, `traefik`+`traefik-metrics-tls`, `rabbitmq`+`rabbitmq-metrics-tls`, `cadvisor`+`cadvisor-tls`,
  `redis-exporter`+`redis-exporter-tls`, `ocr-worker`+`ocr-worker-metrics-tls`+`ocr-worker-metrics-bridge`,
  `docker-socket-proxy`+`docker-socket-proxy-tls`.
- Modul wajib (`EFN_REQUIRED_ODOO_MODULES`, `scripts/lib/deploy-validate.sh`) harus `installed` sebelum grant ETL.

### 5.3 Core, data, observability

```bash
$DC --profile core up -d                    # minio-init (exit 0), s3-gateway, BFF, web, traefik
$DC --profile core --profile data up -d     # clickhouse-init (exit 0) -> metabase -> metabase-provision (exit 0)
# grant ETL setelah modul Odoo terpasang (pola deploy.sh langkah "Grant ETL"):
MSYS_NO_PATHCONV=1 $DC exec -T postgres psql -X -q -v ON_ERROR_STOP=1 -U efn_odoo -d efn_odoo -f - < services/etl/sql/odoo-readonly-grants.sql
# lalu ETL_ENABLE_ODOO/ETL_ENABLE_GL/ETL_AUDIT_COPY_ENABLED=true di .env + `$DC up -d etl`
$DC --profile core --profile data --profile observability --profile ops up -d
```

Urutan seed ringkas: secret/PKI → postgres (+role, migrasi) → Odoo DB + modul (+sidecar) → core (minio-init) →
data (clickhouse-init, metabase-provision) → grant ETL + aktifkan job ETL → API key kanal/user teknis Odoo bila fitur
dinyalakan (runbook `odoo-*-api-key.md`) → observability/ops.

### 5.4 Forecast (opsional)

`FORECAST_ENABLED=false` adalah default semua env (§28.10). Overlay `docker-compose.forecast.yml` boleh ikut sejak awal
(worker idle). Nyalakan hanya setelah user teknis `efn_forecast_smd` + API key dibuat dan backtest lulus
([forecast-worker.md](forecast-worker.md)); worker berat (AutoARIMA) menambah beban CPU/RAM (§7).

### 5.5 Verifikasi

| Cek | Perintah | Harapan |
|---|---|---|
| Nama network/subnet | `docker network inspect efn-prod_internal -f '{{(index .IPAM.Config 0).Subnet}}'` | `10.250.1.0/24` |
| Traefik hanya stack ini | `docker logs efn-prod-traefik-1 2>&1 \| grep -i 'could not find network\|constraint'` | kosong |
| API | `curl --cacert secrets/dev_edge_crt.txt --resolve api.efn.local:443:127.0.0.1 https://api.efn.local/health` | 200 |
| Odoo (wg-https) | `curl --cacert secrets/dev_edge_crt.txt --resolve erp.efn.local:9443:127.0.0.1 https://erp.efn.local:9443/web/login` | 200 (403 = §4) |
| Prometheus | Grafana → Explore → `up{job=~"api-gateway\|ocr-worker"}` | 1 per replika, alamat `10.250.1.x` |
| Promtail | Loki: `count by (container) (count_over_time({service=~".+"}[5m]))` | hanya `efn-prod-*` |
| pg_hba | `$DC exec postgres psql -U postgres -c "select type,address from pg_hba_file_rules where address is not null or type like 'host%'"` | `samenet` |

## 6. Membuat `efn-stg` / `efn-dev` sebagai cermin prod

Sama dengan §2–§5 di worktree baru, dengan identitas, subnet, port, dan dir textfile sendiri:

| Env | `EFN_STACK` | `EFN_SUBNET_BASE` | `APP_ENV` | `EFN_HTTP_PORT`/`EFN_HTTPS_PORT` | `EFN_WG_HTTPS_PORT` | Overlay |
|---|---|---|---|---|---|---|
| prod lokal | `efn-prod` | `10.250` | `production` | 80/443 | 9443 | prod + local-edge |
| staging | `efn-stg` | `10.251` | `staging` | 8080/8443 bila hidup bersamaan | 9444 | prod + local-edge |
| dev (cermin) | `efn-dev` | `10.252` | `production` atau `staging` | 8081/8444 bila hidup bersamaan | 9445 | prod + local-edge |
| dev (hot reload) | `efn-dev` | `10.252` | `dev` | 80/443 | 9443 | dev (port host tetap: satu per host) |

- Port non-443 hanya untuk menghidupkan dua stack bersamaan; URL absolut aplikasi (cookie, origin SSO Insight,
  `EFN_INSIGHT_URL`) mengasumsikan 443/9443 → uji fungsional penuh lakukan satu stack di 443, stack lain `stop`.
- `staging`: `deploy-validate` mengizinkan `PAYMENT_PROVIDER=mock`; replika/limit tetap dari `docker-compose.prod.yml`
  sampai profil ramping K25-06 dibuat (§7).
- Seed data demo kecil untuk dev/stg (skrip `scripts/dev/seed_demo_*.py`), bukan salinan data prod.
- Dua stack bersamaan wajib `EFN_SUBNET_BASE` berbeda (subnet duplikat ditolak Docker) dan `EFN_STACK` berbeda.

## 7. Anggaran memori (host 32 GB, Docker Desktop 12 GB)

Batas memori compose adalah **plafon**, bukan reservasi. Jumlah plafon render prod (base + `docker-compose.prod.yml`):

| Profil | Plafon (± GB) | Komponen besar |
|---|---|---|
| core | 23 | postgres 8G (shared_buffers 2 GB), odoo 6G (6 worker), api-gateway 2×1G, rabbitmq 2G, minio 2G |
| data | 21,6 | clickhouse 16G, metabase 3G, etl 2G |
| observability | 7,4 | prometheus 4G, loki 2G |
| ops | 0,9 | exporter, cadvisor, alertmanager |
| ai | 32,7 | ollama 24G (GPU), ocr-worker 2×4G — **jangan di 12 GB** |

Total core+data+observability+ops ≈ 53 GB plafon di VM 12 GB: aman hanya selama pemakaian nyata < 12 GB. Risiko
utama: ClickHouse menyesuaikan cache dengan plafon cgroup (16G) dan Odoo 6 worker. Langkah:

1. Ukur dulu (K25-06: target diukur, bukan janji): `docker stats --no-stream --format '{{.Name}} {{.MemUsage}}' | grep efn-prod`
   setelah 30 menit beban gladi; catat di laporan gladi.
2. Satu stack penuh pada satu waktu; stack lain `$DC stop` (volume tetap).
3. Bila pemakaian > ±10 GB: matikan profil `ai` (default di §3), `FORECAST_ENABLED=false`, lalu buat profil ramping
   (overlay `deploy.resources.limits` + `ODOO_WORKERS` + replika 1) sebagai tugas terpisah — belum ada di repo.
   Odoo: worker > `ODOO_RPC_MAXCONN` + kebutuhan POS ([online-orders.md](online-orders.md#sizing-worker)).

## 8. Batasan yang diketahui

- User ClickHouse lain (`efn_etl`, `efn_text2sql`, `efn_metabase`, `efn_esb`, `efn_insight`) tetap `HOST ANY` (isolasi
  lewat network Docker); hanya `efn_forecast_reader` dan `efn_segment_reader` dibatasi `HOST IP` (§1).
- Secret: path tetap `../../secrets/` (satu worktree per env). `SECRETS_DIR` per env di compose belum dibuat.
- Sertifikat edge lokal hanya `efn.local`/`*.efn.local`; domain per env (mis. `stg.efn.local`) butuh perluasan SAN di
  `gen-secrets.sh` (belum).
- `NODE_EXPORTER_TEXTFILE_DIR` default sama untuk semua env → isi per env (§3) agar metrik backup tidak tercampur.
- cAdvisor tetap melihat semua container host (metrik saja, tidak ada alert per container).
- Migrasi dari worktree gladi yang ditambal `sed`: `git checkout -- infra/` (buang tambalan), set `EFN_STACK`/
  `EFN_SUBNET_BASE` di `.env`, hapus `IMAGE_*`/`TRUSTED_PROXIES` lama, `build`, lalu `$DC up -d` (network bernama sama
  dipakai ulang; traefik, promtail, prometheus, api-gateway, ocr-worker ter-recreate — sertakan sidecar-nya, §5.2).
