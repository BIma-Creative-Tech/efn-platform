# Laporan Agent: efn-devsecops-engineer

## G62 infra login-gateway (23 Nov 2026, worktree `efn-platform-odoo`, branch `feat/odoo-completion`, belum di-commit) — ADR-050

- **Compose:** `login-gateway` (profil `core`, image `${IMAGE_LOGIN_GATEWAY:-ghcr.io/${EFN_STACK}/login-gateway:dev}`, target
  `runtime`, 127.0.0.1:3005, read_only, tmpfs `/tmp` + `/app/.next/cache`, cap_drop ALL, 256M, healthcheck `/healthz`,
  TANPA secret) + `login-gateway-tls` (ghostunnel :3443 → :3005, CN traefik/blackbox-exporter, status 6077,
  `depends_on ... restart: true`). Network baru `login-ingress` (oktet 22): traefik + blackbox-exporter + login-gateway.
  Router `login` wg-https, `internal-only` + `secure-headers-insight` + `ratelimit-login` (baru), serversTransport
  `efn-login-gateway`. `ACME_DOMAINS` + `login.` (base + overlay wa-unofficial). Dev overlay: URL :9443; prod: replika 1.
- **Keputusan:** profil `core` (pintu masuk ERP tidak bergantung stack data); `internal-only` (kasir outlet juga masuk ERP);
  URL tujuan bisa ditimpa `.env` (`${LOGIN_GATEWAY_*_URL:-...}`) untuk gladi lokal non-443 karena
  `docker-compose.prodlocal-urls.yml` tidak ada di git.
- **PKI/secret:** `gen-pki.sh` `login-gateway|server`, `secrets.list` + compose `tls_login_gateway_crt/_key`.
- **Observability:** job `probe-login`, alert `EfnLoginGatewayDown` (warning, 15m) → runbook `login-gateway.md#5`.
- **Gerbang/CI:** `check-encryption.sh` §6b4b + `login-ingress` di regex nama network; `build-push.yml` (scan-pr + build,
  target runtime); rantai rilis `IMAGE_LOGIN_GATEWAY` (deploy.yml, `efn-deploy`, `deploy.sh`, `test-efn-deploy.sh` SVCS);
  dependabot docker + npm. `ci.yml` (job node) sudah diisi frontend.
- **Dokumen:** runbook `docs/runbooks/login-gateway.md`, README runbook, `internal-pki.md` (G62), `docs/04` §29, `.env.example`.
- **Belum terverifikasi:** tidak menjalankan `docker compose config`, `check-encryption.sh`, promtool, shellcheck, maupun
  `test-efn-deploy.sh` (aturan sesi: tanpa docker). Nested default `${A:-https://erp.${B:-x}/...}` diasumsikan didukung
  compose v2 (pola sama dengan `image:` yang sudah ada). `depends_on.restart` butuh Compose ≥ 2.17. Dockerfile
  `services/login-gateway` belum ada saat ditulis (frontend paralel): harus target `runtime`, USER non-root, node tersedia
  untuk healthcheck. Staging/prod: terbitkan ulang intermediate + pasang ulang wrapper `efn-deploy` (12 image) sebelum
  rilis; stack lokal yang sedang jalan akan mendapat CA dev baru saat `gen-secrets.sh` → `up -d --force-recreate`.

## Kanal online SMD + tenant web `supermarket` (23 Nov 2026, worktree `efn-platform-smweb`, branch `feat/supermarket-web`, belum di-commit)

Dasar: keputusan user 23 Nov 2026 — PT Supermarket Demo `SMD` mendapat kanal online sendiri (kode kanal = kode PT,
pola G20 L-48), pickup di toko + pre-order, tanpa QR meja/reservasi. Pola persis kanal WTZ (G24).

**Perubahan**
- `infra/compose/docker-compose.online-smd.yml` (baru): `api-gateway` env `ODOO_API_KEY_ONLINE_SMD_FILE` + secret
  `odoo_api_key_online_smd`. Hanya disertakan deploy.sh bila `SMD` ada di `ONLINE_CHANNELS`.
- `infra/compose/docker-compose.yml`: secret top-level `odoo_api_key_online_smd`; router Traefik `web` + `Host(supermarket.<domain>)`
  (sertifikat edge dev/local-edge `*.efn.local` sudah mencakup; prod ACME per host lewat certresolver yang sama).
- `scripts/secrets.list`: `odoo_api_key_online_smd host:disabled` (gen-secrets.sh membaca daftar ini → placeholder `disabled`).
- `scripts/lib/deploy-validate.sh`: `EFN_ONLINE_CHANNEL_CODES=(GL PB BCR BCF WTZ SMD)`. Default `ONLINE_CHANNELS` tetap
  `GL` (compose, deploy.sh, deploy.yml) → kanal SMD mati sampai dinyalakan eksplisit. SMD tetap di luar daftar QR/booking/promo.
- `scripts/host/efn-rotate-online-key`: `ALLOWED_CHANNELS` + `SMD`; batas `parse_channels` dinaikkan 8 → 16 kode.
  **Bug laten yang ditemukan:** `--all` menggabungkan kanal online + QR-*/BOOKING-*/PROMO-*; dengan semua fitur aktif
  sudah 10 kode sebelum SMD (11 sesudahnya) → regex `{0,7}` menolak konfigurasi sah. Keunikan + allowlist tetap membatasi.
- `scripts/check-encryption.sh`: regex least privilege `odoo_api_key_online_(gl|pb|bcr|bcf|wtz|smd)`; render
  `prod-online` otomatis memuat overlay baru lewat glob `docker-compose.online-*.yml`.
- CI `.github/workflows/ci.yml` (job infra): glob overlay sudah mencakup `online-smd` → hanya komentar. `deploy.yml`: komentar.
- Alert: `alert-rules.yml` generik per label `channel` (tidak ada daftar kanal di ekspresi) → hanya komentar.
- Test: `test-efn-deploy.sh` 92w dibalik (SMD kini diterima) + 92w2–92w9 (daftar 6 kanal, SMD saja, huruf kecil
  ditolak, QR/booking SMD ditolak, default tetap GL); `test-efn-rotate-online-key.sh` 77h–77n (SMD belum di-deploy,
  `--bootstrap` tanpa overlay, QR-/BOOKING-/PROMO-SMD ditolak, CHANNELS GL,SMD, daftar 11 kode diterima).
- Docs: `docs/runbooks/odoo-online-api-key.md` (tabel kanal, §1b paragraf SMD, batas 16), `docs/runbooks/README.md`
  (baris indeks), `README.md` (hosts: + `wetzels.efn.local supermarket.efn.local`), komentar unit systemd, docker-compose.dev.yml.
- PostgreSQL: tidak perlu migrasi baru — `19-platform-g44.sql` sudah menambah `SMD` ke CHECK `online_order_part`,
  `payment_intent(_portion)`, ledger loyalty (deploy.sh mewajibkan 19).

**Temuan BFF (tidak diubah):** `onlineApiKeyEnvName`/`onlineApiKeySecretName` (`app-config.ts`) generik per kode
kanal → `SMD` otomatis memakai `ODOO_API_KEY_ONLINE_SMD_FILE`/`odoo_api_key_online_smd`; `onlineChannels` tidak punya
allowlist. Perlu ditinjau agent BFF: `odoo-rpc/odoo-online.mock.ts` (dev mock) belum punya entri `SMD`
(company/brand, payment modes, `slotChannels` untuk pre-order) dan `online-config.spec.ts` belum punya assert SMD.

**Belum terverifikasi:** `docker compose config`, shellcheck, dan kedua skrip test belum dijalankan (tanpa Bash di sesi
ini) — perintah diserahkan ke koordinator.

## K25-06 identitas stack (20 Nov 2026, worktree efn-platform-k2506, branch feat/k25-06-stack-name, belum di-commit) — compose jalan dengan nama project & subnet apa pun

Pemicu: gladi prod lokal `efn-prod` (lead menambal `sed`): label/konfigurasi memakai nama network `efn_*`, subnet tetap
`172.30.x`, tag image `:dev` dipakai bersama semua env. Tanpa shell: belum ada `docker compose config` yang dijalankan.

### Keputusan
1. **Satu variabel `EFN_STACK`** (default `efn`): `name: ${EFN_STACK:-efn}` (project), `networks.<n>.name:
   ${EFN_STACK:-efn}_<n>` (base, esb, forecast), semua label `traefik.docker.network`, tag image default
   `ghcr.io/${EFN_STACK:-efn}/<svc>:dev` (dev overlay `${EFN_STACK:-efn}/<svc>:dev`). Default me-render string identik.
   Produksi server tidak berubah: `IMAGE_*` digest dari deploy.sh menimpa default; deploy.sh kini menolak
   `EFN_STACK`/`EFN_SUBNET_BASE` non-default (`efn_validate_stack_identity server`, karena TRUSTED_PROXIES dipin 172.30).
2. **`EFN_SUBNET_BASE`** (default `172.30`): IPAM `"${EFN_SUBNET_BASE:-172.30}.<n>.0/24"` (dikutip: `{`/`}` di flow
   mapping). `TRUSTED_PROXIES` default `${TRUSTED_PROXIES:-${EFN_SUBNET_BASE:-172.30}.1.0/24}`; baris di `.env.example`
   dikomentari. `EFN_INTERNAL_EXTRA_CIDR` dev overlay jadi `${EFN_INTERNAL_EXTRA_CIDR:-172.16.0.0/12}`.
3. **pg_hba → `samenet`** (postgres hanya di network `data`; CI menolak network tambahan). Cakupan = data subnet +
   loopback container postgres sendiri (hanya role aplikasi, bukan superuser). Tanpa entrypoint/template baru.
4. **Traefik + Promtail: render template saat start** (`infra/stack/render-config.sh`, sh, tmpfs `/tmp`): konfigurasi
   statis Traefik tidak bisa membaca env bila memakai file, dan Promtail `-config.expand-env` mengubah backslash regex
   redaksi. Ditemukan masalah tambahan yang tidak disebut di brief: `docker-socket-proxy` melihat SEMUA container host,
   sehingga Traefik stack A memuat router stack B / project lain dan Promtail menelan log stack lain (prod → dev Loki).
   Diperbaiki dengan `providers.docker.constraints: Label(com.docker.compose.project, @EFN_STACK@)` dan filter docker_sd
   `label=com.docker.compose.project=@EFN_STACK@`. `providers.docker.network: "@EFN_STACK@_internal"` dipertahankan
   sebagai fallback; setiap router tetap wajib label eksplisit (dicek).
5. **Prometheus**: dns_sd ke alias ber-scope network `api-gateway-metrics` / `ocr-worker-metrics` (hanya di
   `networks.internal.aliases` service pemilik; juga di overlay llm untuk ocr-worker). Relabel keep dipertahankan,
   diubah ke `\d{1,3}\.\d{1,3}\.1\.\d{1,3}:<port>` (oktet ketiga 1 = network internal untuk base apa pun).
   `docker_sd_configs` lewat socket proxy ditolak: menambah akses API Docker ke Prometheus (least privilege).
6. Port host Traefik `${EFN_HTTP_PORT:-80}` / `${EFN_HTTPS_PORT:-443}` agar stack kedua bisa hidup bersamaan.
7. Overlay baru `docker-compose.local-edge.yml` (sertifikat edge self-signed untuk gladi tanpa domain publik; tidak
   dipakai deploy.sh).

### File
- `infra/compose/docker-compose.yml`, `docker-compose.dev.yml`, `docker-compose.esb.yml`, `docker-compose.forecast.yml`,
  `docker-compose.llm.yml`, `docker-compose.local-edge.yml` (baru), `.env.example`
- `infra/stack/render-config.sh` (baru), `infra/traefik/traefik.yml`, `infra/observability/promtail.yml`,
  `infra/observability/prometheus.yml`, `infra/postgres/pg_hba.conf`
- `scripts/check-encryption.sh` (aturan 10 `check_stack_identity` + `check_stack_files`; render `prod-stack`
  EFN_STACK=efn-prod/10.250 dan `dev-stack` efn-dev/10.252; `kind` agar render paksaan memakai aturan prod/dev penuh),
  `scripts/lib/deploy-validate.sh` (`efn_validate_stack_identity`), `scripts/deploy.sh` (panggilan mode server),
  `scripts/restore-test.sh` (`COMPOSE_PROJECT` default `EFN_STACK`), `scripts/smoke/run-e2e-smoke.sh`,
  `infra/odoo-rpc-gateway/test-gateway.sh`, `test-gateway-load.sh` (network/container dari `EFN_STACK`)
- `.github/workflows/ci.yml` (shellcheck `infra/stack/*.sh`)
- `docs/runbooks/multi-env-local.md` (baru), `docs/runbooks/README.md`

### Perbedaan `docker compose config` stack `efn` (yang diharapkan)
Identik kecuali: traefik & promtail (+`entrypoint` render-config, `command` menunjuk `/tmp/...`, +env `EFN_STACK`,
+volume skrip; healthcheck traefik membaca `/tmp/traefik.yml`); api-gateway & ocr-worker (+`networks.internal.aliases`).
Container itu ter-recreate sekali.

### Belum terverifikasi
- Interpolasi `name:` top-level dan nested default `${A:-${B:-x}}` di Compose v2 CI (dokumentasi: didukung).
- Traefik image alpine punya `/bin/sh`+`sed`; Promtail image debian (`bash` ada di healthcheck). Constraint & filter
  docker_sd belum diuji runtime. Sumber IP koneksi port publish Docker Desktop (asumsi gateway edge `<base>.0.1`).
- `CH_FORECAST_READER_HOST_CIDR` tidak ada di main (mungkin cabang lain) — tidak diubah.
- `tests/load/run.sh` masih memakai `efn_*` / `efn-*-1` (uji beban dev, stack default).
- `CLAUDE.md` tidak diubah (aturan K25-06 sudah mencakup; runbook menjadi rujukan teknis).

### Lanjutan 21 Nov 2026 (merge origin/main G45/G50 ke PR #121, belum di-commit)
- Konflik diselesaikan: laporan ini (kedua bagian), `.env.example`, `docker-compose.yml` (komentar oktet + api-gateway:
  secret G50 dipertahankan + networks map ber-alias), `check-encryption.sh` (`check_stack_files` + blok G50/SEC-215/SEC-223).
- K25-06 diterapkan ke tambahan main: overlay `docker-compose.segment.yml` dan `docker-compose.wa-unofficial.yml`,
  network `mail` (dev) dan `wa-ingress`: nama `${EFN_STACK:-efn}_<n>`, subnet `"${EFN_SUBNET_BASE:-172.30}.<n>.0/24"`,
  label `traefik.docker.network` berparameter, IP statis relay `.21.10` / proxy egress `.14.10` + `extra_hosts` dari base.
  `admin-relay.cfg` bind `"${WA_RELAY_BIND_IP}:8443"` (env HAProxy, dari compose) dengan pagar `.if !defined` + `.alert`
  (tanpa env = gagal start, bukan bind semua interface); CI `haproxy -c` memberi env + uji negatif tanpa env.
- Prometheus job wa-unofficial: alias `wa-admin-relay-ingress` (hanya di wa-ingress), keep `\d+\.\d+\.21\.10:8443`.
- SEC-215: `CH_FORECAST_READER_HOST_CIDR` default compose `<base>.11.0/24` (baris `.env.example` dikomentari); BARU
  `CH_SEGMENT_READER_HOST_CIDR` default `<base>.17.0/24`, `efn_segment_reader` kini `HOST IP` (sebelumnya HOST ANY),
  divalidasi `private_cidr_ok`; check-encryption: CIDR /24../32, sama dengan ipam segment-ch, grep create_user.
- `IMAGE_FORECAST_WORKER`/`IMAGE_SEGMENT_WORKER`/`IMAGE_WA_UNOFFICIAL` dikomentari; default `ghcr.io/${EFN_STACK:-efn}/...`.
- api-gateway di overlay segment dan dev memakai bentuk map + alias (aman terhadap merge list).
- `prod-stack`/`dev-stack` kini memuat overlay segment + wa-unofficial (`--profile wa-unofficial`); kill switch
  esb/forecast/segment/wa ikut dicek di `prod-stack`. Alias check digeneralisasi (svc:alias:network).
- Runbook: `multi-env-local.md` (peta oktet 13-21, turunan SEC-215/WA), `supermarket-campaign-channels.md`.
- Belum pasti: semantik `.alert` HAProxy 3.2 (diharapkan fatal, diuji CI negatif); bentuk JSON `extra_hosts` di Compose
  CI (jq menerima list `host:ip`/`host=ip` dan map); user `efn_segment_reader` di volume lama dipersempit saat
  clickhouse-init berikutnya (segment-worker di mesin lain perlu CIDR WireGuard).

## G50 perbaikan security review (20 Nov 2026, `docs/reviews/SECURITY_REVIEW_2026-11-20.md`)

Tanpa shell; belum dijalankan.
- **SEC-G50-04:** policy broker di definitions (`infra/rabbitmq/rabbitmq-entrypoint.sh`): `notify.wa_unofficial.dlq`
  TTL 24 jam + maks 1000 drop-head; `notify.wa_unofficial` TTL 24 jam + maks 5000 drop-head; `behavior.event.dlq` TTL
  7 hari + maks 100000. Policy (bukan argumen quorum) agar bisa diterapkan ke queue yang sudah ada.
- **SEC-G50-06:** service baru `wa-admin-relay` (HAProxy TCP passthrough, `infra/wa-egress/admin-relay.cfg`, bind hanya
  `172.30.21.10:8443`). Network `wa-admin` = relay + wa-unofficial; `wa-ingress` (baru, 172.30.21.0/24) = traefik +
  prometheus + relay. Label Traefik pindah ke relay; job Prometheus dns_sd `wa-admin-relay.efn_wa-ingress`. TLS tetap
  end-to-end (serverName/server_name `wa-unofficial`). check-encryption + CI `haproxy -c` diperbarui.
- **SEC-G50-10:** `EMAIL_ENABLED` masuk `EFN_GATED_FEATURES`; production wajib `EMAIL_DPA_APPROVAL_REF` (release var,
  deploy.sh, deploy.yml, .env.example). check-encryption menolak ref persetujuan berisi nilai di `.env.example`/compose.
- **SEC-G50-12:** runbook §5.2 "Operator terkunci (429)".

## G50 (20 Nov 2026, worktree `efn-platform-g50`, branch `feat/wave-50-supermarket-campaign`, belum di-commit) — wiring kampanye supermarket

Ditulis tanpa shell: `docker compose config`, shellcheck, hadolint, yamllint, promtool, `haproxy -c`, check-encryption
**belum dijalankan** (lead menjalankan). ADR-044/047/048 Proposed; semua flag default `false`.

### File
- Baru: `infra/compose/docker-compose.wa-unofficial.yml` (wa-unofficial + wa-egress-proxy, profil `wa-unofficial`),
  `infra/compose/docker-compose.segment.yml` (segment-worker, profil `data`), `infra/wa-egress/haproxy.cfg` (TLS
  passthrough allowlist SNI), `docs/runbooks/supermarket-campaign-channels.md`.
- Diubah: `infra/rabbitmq/rabbitmq-entrypoint.sh` (7 queue + DLQ, izin efn-bff, user opsional efn-wa-unofficial),
  `infra/clickhouse/clickhouse-init.sh` (efn_segment_reader + role/profil), `infra/odoo-rpc-gateway/haproxy.cfg` + README
  (path #30-#32, CN ketiga), `infra/compose/docker-compose.yml` (secret, env BFF/ETL G50), `docker-compose.dev.yml`
  (Mailpit TLS, env email/consent dev, network `mail`), `.env.example`, `infra/observability/prometheus.yml` (job
  wa-unofficial dns_sd), `alert-rules.yml` (EfnWaUnofficialDown/Banned, DLQ WA dikecualikan dari alert generik),
  `infra/traefik/dynamic/internal-tls.yml` (serversTransport wa-unofficial), `scripts/secrets.list`, `gen-secrets.sh`,
  `gen-pki.sh` (segment-worker, wa-unofficial, wa-operator, mailpit; DEV_ONLY/NONPROD_ONLY), `scripts/lib/deploy-validate.sh`
  (`efn_validate_g50_config`, EFN_GATED_FEATURES, release vars), `scripts/deploy.sh` (DOTENV G50),
  `scripts/check-encryption.sh` (blok 6b6, render dev/prod-segment + dev/prod-wa-unofficial, ACL CN ketiga),
  `.github/workflows/ci.yml`, `build-push.yml`, `deploy.yml` (CFG_* G50), `.github/dependabot.yml`,
  `services/wa-unofficial/Dockerfile` (direktif `# syntax` dipindah ke baris 1), `docs/runbooks/README.md`.

### Keputusan
1. **User Odoo terpisah untuk segment-worker** (`efn_segment_<pt>`, secret `odoo_api_key_segment_smd`); BFF tetap
   `efn_campaign_<pt>`. Kode worker tidak berubah: env `ODOO_API_KEY_CAMPAIGN_SMD_FILE` menunjuk file secret segmen.
2. **Egress WA = TLS passthrough HAProxy + allowlist SNI** (bukan proxy HTTP CONNECT): tanpa hop plaintext dan tanpa
   pengecualian check-encryption; `WA_UNOFFICIAL_PROXY_URL` tidak diset (ditolak check-encryption). Host:
   `web.whatsapp.com` saja, **BELUM DIVERIFIKASI**; media tidak diizinkan.
3. **Network wa-unofficial = rmq + wa-egress + wa-admin.** wa-admin (internal) wajib agar Traefik/Prometheus bisa masuk;
   menyimpang dari brief "rmq + wa-egress saja", dicatat di overlay.
4. **Secret WA `host:disabled`** (production OFF); dev nyata hanya `APP_ENV=dev bash scripts/gen-secrets.sh` (pola
   SEC-199). User broker efn-wa-unofficial hanya dibuat bila secret bukan placeholder; secret hash key BFF hanya lewat
   overlay (tanpa overlay = kanal BFF mati `hash_key_missing`).
5. **mTLS operator**: TLS option `efn-mtls` (CA internal) + leaf `wa-operator` (non-production). Traefik tidak bisa
   memfilter CN -> lapis IP staf + token admin; usulan CA operator terpisah (backlog).
6. **Gate production**: ref (`BEHAVIOR_TRACKING_DPO_APPROVAL_REF`, `PROMO_CAMPAIGN_APPROVAL_REF`,
   `WA_UNOFFICIAL_RISK_ACCEPTANCE_REF`) **dan** izin Superadmin `EFN_ALLOW_FEATURE_ON`. ETL_ENABLE_BEHAVIOR /
   SEGMENT_ENABLED ditolak di production selama fitur sumber mati.
7. Semua queue G50 `x-delivery-limit 10` (pola G17/G31). segment-worker berbagi backend gateway `odoo_forecast`.
8. Scrape wa-unofficial lewat dns_sd: tanpa overlay = 0 target (tidak ada EfnServiceDown palsu).

### Blocker untuk lead (bukan file devsecops)
- **Skema `behavior.*` BFF vs ETL tidak cocok**: `20-platform-g50a.sql` tanpa `company_code`/`ingested_at` di
  `event_raw` dan tabel erasure `erased_subject`; ETL mengharapkan `company_code`, `ingested_at`, `subject_erasure(id,
  subject_key, request_id, created_at)`. Pemilik tabel = `efn_platform_owner` (NOLOGIN), bukan `efn_bff` seperti header
  `platform-behavior-grants.sql`. Grant ETL sengaja TIDAK dipasang di deploy.sh.
- `test-efn-deploy.sh` belum punya kasus G50 (fungsi baru + gated features); `test-gateway.sh` belum punya kasus #30-#32.

### Belum terverifikasi
Lihat laporan balik ke lead (render compose, sintaks HAProxy/promtool, digest Mailpit + `readyz` dengan UI TLS, nested
merge `networks`/`secrets` override, sysctl bind :443 non-root, Baileys/egress nyata, Trivy image wa-unofficial).

## G43 fase 2 W3 (18 Nov 2026, branch feat/wave-43-esb-migration, belum di-commit) — secret migrasi, image/service `esb-migrate`, CI, check-encryption, runbook

Kontrak: kontrak kerja G43 fase 2 (W3), docs/04 §27.2/§27.10, ADR-042 (Accepted), laporan W1 "Please forward → W3".
Tidak ada yang dijalankan (sesi tanpa shell): compose config, check-encryption, shellcheck, hadolint, checkov, pytest,
build image = tugas lead.

### File
| File | Perubahan |
|---|---|
| `scripts/secrets.list` | + `clickhouse_password_mig host:disabled`, `migration_bundle_key host:disabled [SA]` (komentar mount + siklus jendela) |
| `scripts/gen-secrets.sh` | dev/CI membuat nilai NYATA untuk dua secret itu (`random` / `hex32`), pola `odoo_pii_*`; file `disabled` diganti, nilai lain tidak ditimpa |
| `infra/compose/docker-compose.yml` | top-level secret `clickhouse_password_mig`, `migration_bundle_key`; `clickhouse_password_mig` di-mount ke `clickhouse-init`; komentar header overlay |
| `scripts/migration/Dockerfile` (baru), `scripts/migration/.dockerignore` (baru) | image `esb-migrate`: `python:3.12-slim@sha256:05cd…` (= etl/esb-extract), apt/zlib1g `--only-upgrade` + `checkov:skip=CKV_DOCKER_9` + `hadolint ignore=DL3008` (pola G41), cek import `AESGCM`+`clickhouse_connect` saat build, COPY eksplisit (tanpa tests), `USER 65534:65534`, `CMD --help` |
| `infra/compose/docker-compose.esb.yml` | service `esb-migrate` profil `migrate`; default `ESB_CK_BRANCHES=pb:4\|38,wtz:5\|6,imd:2,cup:4\|6`; dokumentasi override `bacha:6=BCR` (cabang 10 = Q-ESB10) |
| `infra/compose/docker-compose.migration.yml` (baru) | sisi Odoo jendela migrasi: `migration_bundle_key`, `${MIG_EXCHANGE_DIR}`→`/mnt/efn-mig`, `scripts/migration`→`/opt/efn-migration:ro`, env `EFN_MIG_BUNDLE_KEY_FILE/EXCHANGE_DIR/CODE_DIR/USER_LOGIN` |
| `infra/compose/.env.example` | `IMAGE_ESB_MIGRATE`, `ESB_CK_BRANCHES` baru + catatan Bacha, blok G43 (`MIG_EXCHANGE_DIR`, `MIG_MAPS_DIR`, `EFN_MIG_UID/GID`, `EFN_MIG_*`) |
| `scripts/check-encryption.sh` | aturan G43 (6b3a) + render `prod-esb-migrate` dan `prod-migration`; network `esb` mengizinkan `esb-migrate` |
| `.github/workflows/ci.yml` | filter `esb-migrate` + `python` mencakup `scripts/migration/**`; job `python` (ruff/compileall/pip-audit) mencakup `scripts/migration`; matriks `pytest` + `esb-migrate` (kolom `dir`); compose config dev/prod + `docker-compose.migration.yml --profile migrate`; diff secret menyertakan overlay migration + profil migrate |
| `.github/workflows/build-push.yml` | `esb-migrate` (context `scripts/migration`) di `scan-pr` dan `build` (scan Trivy, SBOM, cosign); path PR `scripts/migration/**` |
| `.github/dependabot.yml` | `/scripts/migration` di ekosistem `docker` dan `pip` |
| `scripts/migration/ruff.toml` | `extend-per-file-ignores` `odoo_import_esb.py` = F821 (global `env` dari `odoo shell`) |
| `Makefile` | `validate` render migrate, `test-py` + scripts/migration, `lint` hadolint Dockerfile baru |
| `docs/runbooks/migrasi-esb-odoo.md` (baru), `docs/runbooks/README.md` | runbook + indeks + baris tabel hardening |

### Keputusan
1. **Secret berjenis `host:disabled`, bukan `random`/`hex32` di secrets.list** (menyimpang dari brief, alasan):
   `clickhouse-init.sh` W1 sengaja menghapus `efn_mig` bila secret placeholder → desainnya "aktif hanya selama jendela".
   Dengan `random`, staging/prod deploy butuh GitHub Environment `CLICKHOUSE_PASSWORD_MIG`/`MIGRATION_BUNDLE_KEY` (deploy
   gagal bila kosong) dan user `efn_mig` + kunci bundel hidup permanen. `host:*` = tidak lewat GitHub/stdin efn-deploy,
   deploy.sh mengisi `disabled` bila belum ada, operator mengisi di host saat jendela dibuka dan mengembalikan `disabled`
   saat ditutup (= rotasi/crypto-erase PDP Q-ESB24). Dev/CI tetap nilai nyata (`random`, `hex32` = 64 hex untuk
   `bytes.fromhex`) lewat gen-secrets.sh. Bila lead tetap ingin `random`/`hex32`: ganti 2 baris + tambah 2 GitHub
   Environment secret, hapus loop khusus di gen-secrets.sh.
2. **Lokasi image = `scripts/migration/Dockerfile`** (context = kode yang dikemas, matriks build-push `context`), bukan
   `services/esb-migrate` (folder berisi Dockerfile saja tidak bisa menjangkau kode di luar context). §27.2 menyebut
   "image esb-extract + mount kode": diganti image sendiri karena `cryptography` tidak ada di esb-extract dan image
   mount-kode tidak tertandatangani. **Untuk W4**: perbarui §27.2 (image, overlay, memori).
3. **Service di `docker-compose.esb.yml` profil `migrate`** (sesuai brief), sisi Odoo di **overlay terpisah**
   `docker-compose.migration.yml` (sesuai §27.2 "hanya selama jendela"): bila kunci bundel ditaruh di esb.yml, Odoo akan
   memegangnya permanen setiap kali overlay ESB aktif. check-encryption menolak `migration_bundle_key` di odoo tanpa
   overlay migration.
4. **uid/gid `esb-migrate` = user `odoo`** (`EFN_MIG_UID/GID`, default 65534): bundel ditulis 0600 (`bundle.write_bundle`)
   sehingga importer di container Odoo hanya bisa membaca bila uid sama; hasil impor 0600 milik odoo dibaca balik
   `writeback`. Alternatif (chmod 0640 + grup bersama) butuh ubah kode W1 — tidak dipilih.
5. **Memori 1536M (bukan 1 GB §27.2)**: tmpfs `/work` 768m dihitung ke cgroup memori; + bundel dienkripsi di memori.
   tmpfs `/work` `mode=1777` (+ `/tmp` 64m), `TMPDIR=/work`, `read_only`, `cap_drop ALL`, `no-new-privileges`,
   `restart: "no"`, `healthcheck: disable` (sekali jalan; dicatat di README runbook tabel hardening).
6. Bind mount `create_host_path: false` untuk `MIG_EXCHANGE_DIR`/`MIG_MAPS_DIR`: direktori hilang = gagal, bukan folder
   root otomatis. Default path `/var/lib/efn/migration/*` (bukan `:?`) agar `docker compose config` di CI tetap lolos.
7. `depends_on: clickhouse (service_healthy)` → perintah `run` wajib `--profile data --profile migrate` (runbook §1).
8. `cryptography==46.0.3` (pin W1) dipertahankan; ikut gate `pip-audit --strict` di job `python`. **Belum diverifikasi**
   ketersediaan di PyPI/advisori per Nov 2026 (tanpa akses) — lead: lihat hasil pip-audit; bila ada advisori naikkan ke
   patch 46.x terbaru (API `AESGCM`/`HKDF` stabil).

### Untuk agent lain (diteruskan lead)
- **W2 (odoo-engineer)**: importer di container Odoo memakai env dari overlay migration: kunci
  `EFN_MIG_BUNDLE_KEY_FILE=/run/secrets/migration_bundle_key`, berkas di `EFN_MIG_EXCHANGE_DIR=/mnt/efn-mig` (baca bundel
  + `validation_*.json`, tulis `<run_id>.result.efnmig`), kode `efn_mig` di `EFN_MIG_CODE_DIR=/opt/efn-migration`
  (`sys.path.insert`). Runbook §4.2 mengasumsikan `EFN_MIG_ARGV` JSON `{"bundle","validation","dry_run"}` — sesuaikan
  runbook bila header importer berbeda. `scripts/migration/run-import.sh` (§27.2) belum ada (tidak saya buat agar
  tidak bentrok); job CI `python` kini menjalankan ruff atas `odoo_import_esb.py` (F821 sudah dikecualikan).
- **W1 (data-ai)**: `scripts/migration` kini di-gate ruff `check` + `format --check` dan pip-audit; laporan W1 menyebut
  ruff belum dijalankan → kemungkinan perlu `ruff format .`.
- **W4 (architect)**: §27.2 (image sendiri `scripts/migration/Dockerfile`, service di esb.yml profil `migrate`, overlay
  migration untuk Odoo, memori 1536M, uid odoo, secret `host:disabled`); ADR-042 bagian keamanan bundel: siklus hidup secret (buka/tutup jendela) + PDP inventory
  entri bundel menyebut penutupan jendela = `disabled` (runbook §7).

### Belum terverifikasi
- `docker compose config` semua render baru (khususnya bind `create_host_path: false` dengan path yang tidak ada di
  runner CI — diharapkan tidak dicek saat `config`), check-encryption render `prod-esb-migrate`/`prod-migration`,
  shellcheck gen-secrets/check-encryption, hadolint + checkov Dockerfile baru, build image (wheel `cryptography` /
  `clickhouse-connect` untuk py3.12), Trivy image.
- Opsi tmpfs `mode=1777` di short syntax compose; uid user `odoo` image 19.0 (runbook memakai `id -u`, tidak menebak).
- Runbook §3–§5 belum pernah dijalankan; nama tabel Postgres `efn_legacy_*` diasumsikan dari nama model W2.

## G43-VPS (18 Nov 2026, branch feat/wave-43-esb-migration, belum di-commit) — runbook VPS Bima Creative (ADR-043)

Paket kerja: runbook operator dua bagian untuk VPS milik Bima Creative. ADR-043 ditulis efn-architect paralel; saat
laporan ini ditulis file ADR-043 **belum ada** di `docs/decisions/` (runbook merujuknya sebagai Proposed). Tidak ada
VPS/docker yang dijalankan; semua perintah dan skrip inline diperiksa manual.

### File
- **Baru** `docs/runbooks/vps-bima.md` — A (dev: akun `efnadmin`/`efndev`, sshd `00-efn-hardening.conf`, UFW +
  WireGuard hub `10.90.0.1/24`, tabel nft `efn_vps` (peer WG hanya ke host + drop DNAT dari antarmuka publik),
  unattended-upgrades tanpa reboot otomatis, chrony, fail2ban/sshguard, swap, Docker resmi rootful+grup docker atau
  rootless, plafon memori `efndev.slice` 8 GiB, clone + PAT fine-grained/deploy key, secret DEV + cek "tanpa kredensial
  klien", tunnel SSH, Claude Code di tmux, validasi CI lokal, backup repo). B (go/no-go 12 butir, host terpisah atau
  varian satu host bersyarat, LUKS di block volume atau file, data-root Docker di LUKS + `containerd-snapshotter:false`,
  image cosign, secret/CA dibuat di host B, nft `efn_esb` egress `172.30.8.0/24` → IP `services.esb.co.id:443` + DNS
  dengan timer refresh, UFW outgoing host, compose `-p efn-esb` hanya `clickhouse clickhouse-init esb-extract`, backfill
  per esb-extract.md §5, heartbeat systemd, backup logis `FORMAT Native` + `age` (dua penerima) + rclone, restore test
  ke container sementara `--network none`, akun per orang + auditd + sudo log, crypto-erase + berita acara).
- **Ubah** `docs/runbooks/README.md` — entri prosedur.
- Tidak ada perubahan kode/compose/skrip repo.

### Temuan yang memengaruhi desain
1. **Traefik base mem-publish `${EFN_PUBLIC_BIND:-0.0.0.0}:80/443`** dan port publish Docker melewati UFW. Di VPS,
   `.env` wajib `EFN_PUBLIC_BIND=127.0.0.1` + penjaga nft DNAT (runbook A3/A8/B7). Tanpa itu stack dev di VPS terbuka
   ke internet.
2. **`--profile data` saja tidak lolos validasi compose** (metabase/etl `depends_on: postgres` profil core). Host B
   memakai `--profile core --profile data` tetapi hanya menyalakan service eksplisit; risiko operator `up -d` tanpa nama
   service = seluruh core+data menyala. Runbook memberi larangan tertulis; usulan overlay di bawah menghilangkannya.
3. **Batas memori profil `core` dev ±11,2 GiB** (> target K25-06 8 GB). Ditegakkan sementara lewat cgroup slice host.
4. **Tidak ada backup ClickHouse di repo** (WAL-G hanya PostgreSQL). `BACKUP TO Disk/File` butuh config server baru →
   dipakai ekspor logis; skrip inline di runbook (bukan di `scripts/`).
5. Di A, grup `docker` = root host dan Claude Code berjalan sebagai `efndev` → alasan utama B di host terpisah.
6. Memori Claude Code laptop memuat detail akun ESB → runbook melarang menyalinnya ke VPS A.

### Usulan (belum dibuat; butuh keputusan lead)
- `infra/compose/docker-compose.vps-esb.yml` (atau profil compose `esb` baru): clickhouse + clickhouse-init + esb-extract
  tanpa ketergantungan core, sehingga host B cukup `--profile esb` dan tidak bisa menyalakan Odoo/BFF/Traefik tak sengaja.
- Overlay dev ramping K25-06 (`docker-compose.dev-slim.yml`): odoo 1,5G, postgres 1G, api-gateway 1G, superapp-web 768M,
  rabbitmq/minio 512M — lalu ukur.
- Pindahkan skrip inline B7/B10/B11 ke `scripts/host/` (`efn-esb-egress-refresh`, `efn-esb-heartbeat`,
  `efn-esb-backup`, `efn-esb-restore-test`) agar ikut shellcheck CI dan test.
- Konfigurasi `backups` ClickHouse (`config.d/backups.xml`, disk terenkripsi) sebagai pengganti ekspor logis bila
  volume besar.

### Belum terverifikasi
- Seluruh runbook di VPS nyata; skrip inline tanpa shellcheck/eksekusi.
- Rootless Docker dengan stack EFN (IP sumber untuk middleware Traefik, `ulimits` ClickHouse, profil ops).
- Default `containerd-snapshotter` pada versi Docker yang akan terpasang; jalur DNS embedded Docker (perlu tidaknya set
  `dns_v4`); hak `SYSTEM STOP MERGES` untuk user admin `efn`; perilaku `SHOW CREATE TABLE` + kebijakan `encrypted` saat
  restore ke container uji.

## G42 (18 Nov 2026, branch feat/wave-42-insight-web, belum di-commit) — infra dashboard Insight + SSO Odoo

Kontrak: docs/04 §26 (terutama §26.10–§26.13), ADR-041 (Proposed). Kode `services/insight-web` (frontend) dan
`efn_insight` (Odoo) dikerjakan paralel; saat laporan ini ditulis `services/insight-web/` **belum ada** di repo.
Tidak ada docker lokal yang dijalankan oleh agent ini: semua YAML/skrip diperiksa manual; lead menjalankan
`docker compose config`, check-encryption, shellcheck, yamllint, hadolint.

### G42.1 Keputusan jaringan

| Network (baru, `internal: true`) | Anggota | Alasan |
|---|---|---|
| `insight-ingress` 172.30.9.0/24 | traefik, insight-web, blackbox-exporter | Ingress ke sidecar `insight-web-tls :3443` + probe health. insight-web **tidak** di `internal` (di sana ada RabbitMQ, sidecar Odoo, BFF) |
| `insight-data` 172.30.10.0/24 | insight-web, clickhouse | satu-satunya jalur data (HTTPS 8443, user `efn_insight`). insight-web **tidak** di `data` (PostgreSQL, RabbitMQ, s3-gateway, Loki) |

Traefik dan ClickHouse sengaja tidak berbagi network (edge internet-facing tidak menjangkau gudang data). Tanpa
`egress`. Odoo tidak punya jalur ke insight-web (SSO lewat browser). Pola mengikuti `esb` (G41) tetapi di base,
karena insight-web masuk rantai rilis bertanda tangan (lihat G42.3). Semua dicek `check-encryption.sh`.

### G42.2 File

| File | Perubahan |
|---|---|
| `scripts/secrets.list` | `insight_sso_key`, `insight_session_key`, `insight_scope_key` (hex32 = 64 hex), `clickhouse_password_insight` (random), `tls_insight_web_{crt,key}` (pki) |
| `scripts/gen-pki.sh` | `insight-web|server` (nameConstraints intermediate berubah) |
| `infra/compose/docker-compose.yml` | secret top-level; network `insight-ingress`/`insight-data`; traefik + blackbox ikut `insight-ingress`; clickhouse ikut `insight-data`; clickhouse-init me-mount `clickhouse_password_insight` + `insight_scope_key`; odoo: `INSIGHT_ENABLED`, `EFN_INSIGHT_URL=https://insight.${TRAEFIK_DOMAIN}`, `EFN_INSIGHT_SSO_KEY_FILE` + secret `insight_sso_key`, router `odoo-insight-open` (`/efn/insight/open`, no-referrer); service `insight-web` (profil `data`, build target `runtime`, `read_only`, tmpfs `/tmp` + `/app/.next/cache`, `cap_drop ALL`, no-new-privileges, 512M, env §26.10, healthcheck loopback `:3002/api/health`, router `insight.` wg-https) + sidecar `insight-web-tls` (`--allow-cn=traefik`, `--allow-cn=blackbox-exporter`); `ACME_DOMAINS` + `insight.` |
| `infra/compose/docker-compose.dev.yml` | insight-web `image efn/insight-web:dev`, `APP_ENV dev`, `EFN_INSIGHT_ODOO_URL=https://erp.efn.local:9443`; odoo `EFN_INSIGHT_URL=https://insight.efn.local:9443` (origin C1 harus memuat port dev) |
| `infra/compose/docker-compose.prod.yml` | insight-web replicas 1 (L-124), 1 CPU / 512M, `APP_ENV` |
| `infra/compose/.env.example` | `IMAGE_INSIGHT_WEB`, `INSIGHT_ENABLED=false` |
| `infra/traefik/dynamic/internal-tls.yml` | serversTransport `efn-insight-web` (serverName insight-web, sertifikat klien traefik) |
| `infra/traefik/dynamic/middlewares.yml` | `secure-headers-insight` (= secure-headers + `Referrer-Policy: no-referrer`), `ratelimit-insight` |
| `infra/observability/prometheus.yml` | job `probe-insight` (https_internal ke `https://insight-web:3443/api/health`) |
| `infra/observability/alert-rules.yml` | `EfnInsightWebDown` (warning, 15m), `EfnHostClockNotSynced` (warning, 30m; C6) |
| `infra/observability/promtail.yml`, `promtail-journal.yml`, `tests/promtail/fixtures/generic.txt`, `tests/promtail/expected.txt` | redaksi `eit1.`/`eis1.` → `[INSIGHT_TOKEN_REDACTED]` (lapis kedua) + fixture regresi |
| `scripts/check-encryption.sh` | aturan G42 (lihat G42.4) |
| `.github/workflows/ci.yml` | filter `insight`; matriks node + `insight-web` (lint, test, build, npm audit); langkah per-entri hanya jalan bila service-nya berubah (`env.CHANGED`, pola pytest). `efn_insight` sudah ada di `EFN_TEST_MODULES` (oleh agent lain) |
| `.github/workflows/build-push.yml` | `insight-web` di scan-pr + build, `target: runtime` |
| `.github/dependabot.yml` | npm `/services/insight-web`; docker `/services/insight-web` (+ `/services/text2sql` yang sebelumnya terlewat) |
| `.github/workflows/deploy.yml`, `scripts/deploy.sh`, `scripts/host/efn-deploy`, `scripts/host/test-efn-deploy.sh`, `scripts/lib/deploy-validate.sh` | `IMAGE_INSIGHT_WEB` di rantai rilis bertanda tangan; `INSIGHT_ENABLED` config rilis (boolean) + `EFN_GATED_FEATURES` (production true hanya dengan `EFN_ALLOW_FEATURE_ON`, L-125); 5 kasus uji 273a–e |
| `docs/runbooks/insight-web.md` (baru), `docs/runbooks/README.md` | runbook aktivasi/rotasi/NTP/troubleshooting/Metabase Holding; indeks + tabel hardening |
| `docs/runbooks/wireguard-outlet.md`, `infra/wireguard/{wg0-server,peer-staff}.conf.example` | split DNS + akses staf: `insight.` |

### G42.3 Penyimpangan dari paket kerja (dengan alasan)

1. **Middleware `secure-headers-insight` menggantikan `secure-headers`** pada router `insight.` (dan router baru
   `odoo-insight-open`): middleware headers Traefik menimpa `Referrer-Policy` upstream menjadi
   `strict-origin-when-cross-origin`, sedangkan §26.2/§26.6 mewajibkan `no-referrer`. Selain itu sama persis.
   Ditambah `ratelimit-insight` (20/s, burst 60). `internal-ops` tetap (Q-INS4).
2. **Wiring rilis dikerjakan penuh** (bukan dicatat sebagai sisa seperti G41): polanya sama dengan text2sql G38, dan
   karena service ada di base profil `data` (production wajib `core,data,observability`), tanpa wiring deploy
   production akan menarik `ghcr.io/efn/insight-web:dev` dan gagal.
3. **Alert baru** `EfnInsightWebDown` (warning, bukan critical karena bukan jalur transaksi) dan
   `EfnHostClockNotSynced` (C6, usulan > 10 dtk) — keduanya dengan runbook.
4. `user:` tidak di-set di compose (mengikuti superapp-web): non-root bergantung pada `USER` di Dockerfile insight-web
   (gate Trivy/hadolint). Mohon frontend memakai pola superapp-web (`USER app`).

### G42.4 Aturan check-encryption baru

- `insight_sso_key` ⊆ {odoo, insight-web}; `insight_session_key` ⊆ {insight-web}; `insight_scope_key`,
  `clickhouse_password_insight` ⊆ {clickhouse-init, insight-web}; `tls_insight_web_key` ⊆ {insight-web-tls}.
- Wajib ter-mount + env cocok: odoo `EFN_INSIGHT_SSO_KEY_FILE`; insight-web `INSIGHT_SSO_KEY_FILE`,
  `INSIGHT_SESSION_KEY_FILE`, `INSIGHT_SCOPE_KEY_FILE`, `CH_PASSWORD_FILE`; clickhouse-init kedua secret insight.
- Network `insight-ingress` ⊆ {traefik, insight-web, blackbox-exporter}; `insight-data` ⊆ {clickhouse, insight-web};
  insight-web hanya kedua network itu, `read_only`, `cap_drop ALL`; sidecar `--allow-cn` ⊆ {traefik, blackbox-exporter}.
- `CH_URL`, `EFN_INSIGHT_ODOO_URL`, `EFN_INSIGHT_URL` diawali `https://`; `INSIGHT_ENABLED` boolean di semua render;
  render `prod`: `INSIGHT_ENABLED == "false"` untuk odoo dan insight-web, dan insight-web wajib ter-render.

### G42.5 Langkah operasional sebelum deploy staging/production pertama (juga di runbook §2)

1. GitHub Environment: `INSIGHT_SSO_KEY`, `INSIGHT_SESSION_KEY`, `INSIGHT_SCOPE_KEY`, `CLICKHOUSE_PASSWORD_INSIGHT`
   (`openssl rand -hex 32`, terpisah). Tanpa ini deploy berhenti / clickhouse-init gagal.
2. **Terbitkan ulang intermediate PKI** (nameConstraints + `insight-web`), unggah `EFN_PKI_INTERMEDIATE_*`.
3. **Pasang ulang `/usr/local/sbin/efn-deploy`** dari rilis ini (manifest kini 10 image; wrapper lama menolak skema).

### G42.6 Belum terverifikasi / sisa

- Belum dijalankan: `docker compose config` (dev, prod, + overlay ESB), `check-encryption.sh`, shellcheck, yamllint,
  promtool, `test-promtail-redaction.sh` (fixture baru: keluaran diprediksi manual), `test-efn-deploy.sh` (kasus 273a–e).
- `services/insight-web` belum ada: CI node `npm ci` dan build-push akan gagal sampai Dockerfile (target `runtime`) +
  `package-lock.json` tersedia. Asumsi kontrak: standalone, `PORT`/`HOSTNAME` dari env, `/api/health` tanpa ClickHouse,
  `USER` non-root, tulisan hanya ke `/tmp` dan `/app/.next/cache`.
- Dev overlay memakai image produksi (tanpa stage `dev`/hot reload); bila frontend menambah stage `dev`, overlay bisa
  diubah ke pola superapp-web.
- Staging dengan `EFN_WG_HTTPS_PORT` ≠ 443: `EFN_INSIGHT_URL`/`EFN_INSIGHT_ODOO_URL` base tidak memuat port → origin C1
  dan form-action tidak cocok. Production (443) aman; staging perlu port 443 atau override.
- K25-02 lintas mesin (stack data di host lain): network Docker `insight-ingress` tidak menjangkau Traefik di host
  inti → butuh desain edge di mesin data (belum).
- K25-06: profil `data` bertambah ±576 MB (insight-web 512M + sidecar 64M).
- Tidak ada cek otomatis bahwa ketiga kunci HMAC insight saling berbeda (usulan: insight-web menolak start bila
  `sso == session == scope` sebagian sama).
- Nama project compose tetap `efn` (label `traefik.docker.network: efn_insight-ingress`), sama keterbatasannya dengan
  `efn_internal` untuk project `efn-dev`/`efn-stg` (K25-06).

## G41 (17 Nov 2026, branch feat/wave-41-esb-datamart, belum di-commit) — infra ekstraksi ESB Core → ClickHouse

Kontrak: G41_CONTRACT (efn-lead), docs/04 §25, ADR-040 (Proposed). Kode service = efn-data-ai-engineer (paralel; saat laporan ini ditulis baru `efn_esb/__init__.py` + `tls.py` yang ada).

### G41.1 File

| File | Perubahan |
|---|---|
| `infra/compose/docker-compose.esb.yml` (baru) | Overlay profil `data`: service `esb-extract` (non-root 65534, `read_only`, tmpfs `/tmp`, `cap_drop: [ALL]`, `no-new-privileges`, 1 CPU / 512M, healthcheck proses, `depends_on clickhouse-init: service_completed_successfully`), env non-rahasia `ESB_*` + `CH_*` (user `efn_esb`), 5 secret password brand + `clickhouse_password_esb` + `efn_ca_crt`; network baru `esb` (internal, 172.30.7.0/24, hanya clickhouse ↔ esb-extract) + `egress`; `clickhouse` ditambah ke network `esb`. |
| `infra/compose/docker-compose.yml` | Top-level secret `clickhouse_password_esb`; di-mount ke `clickhouse-init` (pembuatan user `efn_esb`); komentar header overlay. Tidak ada service baru di base. |
| `infra/compose/.env.example` | `IMAGE_ESB_EXTRACT`, blok `ESB_ENABLED=false`, `ESB_BRANDS=pb`, `ESB_RATE_PER_SEC`, `ESB_WORKERS`, `ESB_MAX_CALLS_PER_RUN`, `ESB_BACKFILL_WINDOW`, `ESB_INTERVAL_SECONDS`, `ESB_CK_BRANCHES`, `ESB_BRANCH_COMPANY_OVERRIDES` (tanpa nilai rahasia). |
| `scripts/secrets.list` | `clickhouse_password_esb random`; `esb_core_password_{pb,wtz,bacha,imd,cup} host:disabled` (pola `ninerouter_api_key`: diisi operator, tidak di-generate, tidak di GitHub Environment). `gen-secrets.sh`/`derive-secrets.sh` tidak perlu diubah (membaca secrets.list). |
| `scripts/check-encryption.sh` | Aturan 6b3 (least privilege secret ESB/CH, network `esb` hanya clickhouse+esb-extract, esb-extract hanya `esb`+`egress`, non-root/read_only/cap_drop, `ESB_BASE_URL` wajib `https://services.esb.co.id/`, `ESB_PASSWORD_<BRAND>_FILE` ↔ secret ter-mount) + aturan 8 (render `prod-esb`: service ter-render, `ESB_ENABLED` default `false`) + render baru `prod-esb`. |
| `.github/workflows/ci.yml` | Filter `esb-extract` (+ masuk `python`), `pyservices` push, ruff/compile/pip-audit mencakup `services/esb-extract`, matriks `pytest` + `esb-extract`, langkah baru `docker compose config (dev + prod, overlay ESB)`, cek secrets == secrets.list menyertakan overlay esb. Pola permissions (`contents: read`), pin SHA, cache pip tidak berubah. |
| `.github/workflows/build-push.yml` | `esb-extract` di matriks `scan-pr` (build + Trivy gate HIGH/CRITICAL, PR read-only) dan `build` (push by digest → Trivy → SBOM → cosign). |
| `.github/dependabot.yml` | `/services/esb-extract` di ekosistem docker & pip. |
| `Makefile` | `test-py` + `validate` (render dev + overlay esb). |
| `docs/runbooks/esb-extract.md` (baru), `docs/runbooks/README.md` | Runbook (secret per brand + rename file lama, menyalakan, probe, backfill 24 bulan per brand di jendela malam + perkiraan durasi, memantau `extract_run`/`extract_checkpoint`/`v_esb_data_quality`, 429/401/403/400/5xx/TLS, rotasi, menghentikan, sisa risiko egress); baris Prosedur + baris baseline hardening. |

### G41.2 Keputusan

1. **Overlay `docker-compose.esb.yml`, bukan service di base.** Konsisten dengan pola fitur opsional berkredensial pihak ketiga + egress (`docker-compose.llm.yml` G37, `docker-compose.payment.yml`). Alasan penentu: image esb-extract belum ada di rantai rilis bertanda tangan (`IMAGE_*` di `deploy.yml` → manifest → `efn-deploy` `IMAGE_VARS` → `deploy.sh`, SEC-42). Bila service ada di base, deploy produksi profil `data` akan menarik `ghcr.io/efn/esb-extract:dev` dan gagal. Dengan overlay, deploy tanpa ESB tidak berubah sama sekali.
2. **`clickhouse_password_esb` di base** (di-mount `clickhouse-init`), agar `clickhouse-init.sh` bisa membuat user `efn_esb` tanpa syarat (pola `create_user`). Konsekuensi: staging/prod butuh GitHub Environment `CLICKHOUSE_PASSWORD_ESB` sebelum deploy berikutnya; tanpa itu `deploy.sh` berhenti `secret CLICKHOUSE_PASSWORD_ESB kosong` (fail-closed, sama seperti G38 `clickhouse_password_text2sql`).
3. **Jaringan: lebih sempit dari kontrak.** Kontrak menyebut `data`; esb-extract justru **tidak** di `data` tetapi di network baru `esb` (hanya ClickHouse) + `egress`. Service ini satu-satunya yang mem-parse JSON dari internet sambil punya egress; bila disusupi, ia tidak bisa menjangkau PostgreSQL/RabbitMQ/Loki di `data`. ClickHouse ikut `esb` (sertifikat SAN `clickhouse` tetap berlaku). Ditegakkan check-encryption.
4. **Egress allowlist.** Repo tidak punya egress proxy/firewall per host (9router G37 dan WhatsApp juga hanya network `egress` + allowlist di aplikasi). Diterapkan pola yang sama: `ESB_BASE_URL` tetap di overlay (bukan dari .env, dicek CI), allowlist host di klien `efn_esb.http` (tugas data-AI). Allowlist tingkat jaringan = **sisa risiko lintas service** (runbook §9).
5. **Password ESB `host:disabled`**, bukan `literal:`/`random`: kredensial pihak ketiga, tidak boleh lewat GitHub Environment/stdin efn-deploy (efn-deploy menolak `host:*` dari stdin secara generik).

### G41.3 Validasi — JUJUR

- **Tidak ada validasi yang dijalankan.** Sesi agent ini tidak punya tool shell (hanya baca/tulis file), jadi `docker compose ... config -q`, `scripts/check-encryption.sh`, `shellcheck`, `yamllint`, dan `gen-secrets.sh` dengan direktori secret dummy **belum dijalankan**. Folder `secrets/` asli tidak dibaca/diubah.
- Pemeriksaan manual: indentasi YAML overlay & workflow; setiap secret yang direferensikan service didefinisikan di top-level (base atau overlay); 5 password + `clickhouse_password_esb` ada di secrets.list sehingga diff "compose secrets == secrets.list" (dengan overlay esb) seharusnya sama; network `egress` didefinisikan di base, `esb` di overlay; subnet 172.30.7.0/24 tidak dipakai di repo; ekspresi jq dibaca ulang (presedensi `|`/`-`/`//`).
- Perintah yang harus dijalankan efn-lead/QA (Git Bash, dari root repo; secret dummy di luar `secrets/`):

```bash
export SECRETS_DIR="$TEMP/efn-g41-secrets"      # BUKAN secrets/ asli
SECRETS_DIR="$SECRETS_DIR" bash scripts/gen-secrets.sh
# compose merujuk ../../secrets -> render di salinan repo sementara, atau sementara isi secrets/ dari gen-secrets di CI
cd infra/compose && cp -n .env.example .env
for env in dev prod; do
  docker compose -f docker-compose.yml -f docker-compose.$env.yml -f docker-compose.esb.yml \
    --profile core --profile data --profile ai --profile observability --profile ops config -q && echo "$env+esb OK"
done
cd ../.. && bash scripts/check-encryption.sh && shellcheck -x scripts/check-encryption.sh
pipx run yamllint -c .yamllint.yml infra .github
```

  Catatan: path secret di compose tetap `../../secrets/*.txt`; `docker compose config -q` tidak memeriksa keberadaan file secret, tetapi `up` memeriksanya. CI job `infra` menjalankan `gen-secrets.sh` ke `secrets/` runner (sekali pakai) — itu tempat validasi otoritatif.
- Risiko CI: `docker compose config` dengan `build.context: ../../services/esb-extract` — bila direktori/Dockerfile belum ada saat CI, compose bisa gagal (perilaku compose-go untuk konteks yang hilang tidak saya verifikasi). Dockerfile esb-extract juga ikut gerbang hadolint, Trivy image, checkov.

### G41.4 Untuk efn-lead (rekonsiliasi dengan efn-data-ai-engineer)

Nama di overlay mengikuti kontrak; cocokkan dengan kode `efn_esb` saat selesai:

1. Env yang dipakai overlay: `ESB_ENABLED`, `ESB_BASE_URL` (`https://services.esb.co.id/core`), `ESB_BRANDS`, `ESB_USER_<BRAND>`, `ESB_PASSWORD_<BRAND>_FILE`, `ESB_RATE_PER_SEC`, `ESB_WORKERS`, `ESB_MAX_CALLS_PER_RUN` (default overlay **20000**, angka saya — samakan dengan default kode), `ESB_BACKFILL_WINDOW`, `ESB_INTERVAL_SECONDS`, `ESB_CK_BRANCHES` (`pb:4|38`), `ESB_BRANCH_COMPANY_OVERRIDES`, `CH_HOST/PORT/SECURE/CA_FILE/DATABASE(efn_dwh)/USER(efn_esb)/PASSWORD_FILE`, `APP_ENV`. Bila kode memakai nama lain (mis. URL hard-code tanpa `ESB_BASE_URL`), ubah overlay **dan** aturan check-encryption 6b3.
2. `command: ["python", "-m", "efn_esb", "run"]` eksplisit di overlay; runbook memakai `docker compose run esb-extract python -m efn_esb probe|backfill ...`. **Dockerfile tidak boleh memakai `ENTRYPOINT`** (CMD saja), kalau tidak perintah jadi ganda.
3. `user: "65534:65534"` di compose: dependency harus terpasang world-readable (pip ke site-packages sistem seperti etl), bukan venv di home 0700.
4. `ESB_ENABLED=false` → proses **tidur**, bukan exit 0 (`restart: unless-stopped` akan me-restart terus).
5. Brand di `ESB_BRANDS` dengan password `disabled`/kosong → gagal jelas (error konfigurasi), jangan mencoba login.
6. `clickhouse-init.sh` perlu `create_user efn_esb /run/secrets/clickhouse_password_esb` + `GRANT esb_writer` (secret sudah di-mount ke clickhouse-init).
7. Klien HTTP ESB: allowlist host `services.esb.co.id`, https, tanpa redirect lintas host, `trust_env=False` (abaikan `HTTP(S)_PROXY`), token/password tidak pernah di log/exception.

### G41.5 Belum / tindak lanjut

- **Wiring rilis** (gelombang berikut, perlu uji `test-efn-deploy.sh`): `IMAGE_ESB_EXTRACT` di `deploy.yml` (digest + manifest), `efn-deploy` `IMAGE_VARS`, `deploy.sh` (validasi image, `.env`, menyertakan overlay bila `ESB_ENABLED=true` dan password brand aktif bukan `disabled`), `ESB_*` di `EFN_RELEASE_CONFIG_VARS` + validasi rentang di `deploy-validate.sh`. Sampai itu ada, ESB hanya dijalankan manual di lingkungan lokal (K25-06).
- **Alert**: belum ada (service tanpa endpoint metrik). Usulan G42: `EfnEsbExtractStale` (warning, data penjualan > 26 jam per brand) dari `v_esb_data_quality` lewat exporter/textfile, + runbook link ke esb-extract.md §6.
- **Allowlist egress tingkat jaringan** (egress proxy / nftables per subnet `egress`) untuk semua service yang keluar (esb-extract, ocr-worker/text2sql via 9router, api-gateway WhatsApp, acme-dns) — belum ada.
- Validasi lokal di G41.3 belum dijalankan.

### G41.6 Perbaikan review security (18 Nov 2026, SECURITY_REVIEW_2026-11-18_g41)

| Temuan | Perbaikan | File |
|---|---|---|
| SEC-180 (P1) | `ESB_USER_<BRAND>: ${ESB_USER_<BRAND>:-}` (kosong default; brand aktif tanpa user = gagal start di kode); `.env.example` `ESB_USER_*=` kosong; runbook memakai placeholder `<user API brand PB>` dst. + langkah isi di `.env` lokal gitignored / config rilis; komentar overlay diperbaiki (akun saat ini **admin**, read-only = Q-ESB1). `check-encryption.sh` fungsi baru `check_esb_users` (jalan di mode `all` dan `files`): (a) teks `ESB_USER_*` di `infra/compose/docker-compose*.yml` wajib `${ESB_USER_X:-}`/kosong, di `.env.example` wajib kosong; (b) `git ls-files --cached --others --exclude-standard` tidak boleh memuat username lama (pola disusun dari potongan agar skrip tidak cocok dengan dirinya sendiri; kecuali laporan review G41 dan docs/04; `.env` lokal otomatis terkecuali karena di-gitignore). Render `prod-esb`: `ESB_USER_*` ter-render berisi nilai = error bila `CI` di-set (CI menyalin `.env.example`), info saja di lokal. | overlay, `.env.example`, runbook, check-encryption |
| SEC-183 | Network baru `egress-esb` (bridge non-internal, `enable_icc: "false"`, subnet **172.30.8.0/24** — base memakai 172.30.0–6, overlay `esb` 172.30.7); esb-extract = `esb` + `egress-esb`. Cek: esb-extract hanya `esb`+`egress-esb`, `egress-esb` hanya esb-extract, `enable_icc` wajib `"false"`. | overlay, check-encryption, runbook §0/§4/§9 |
| SEC-185/187 passthrough | `ESB_ALLOW_IGNORE_WINDOW` (default `false`, dicek di render `prod-esb` seperti `ESB_ENABLED`) dan `ESB_MAX_RESPONSE_BYTES` (default 33554432). `.env.example` kini memuat semua 11 env passthrough yang dilaporkan QA + 2 baru + 5 `ESB_USER_*`. | overlay, `.env.example`, check-encryption |
| SEC-185/186 runbook | Satu proses per brand (L-111): keluarkan brand dari `ESB_BRANDS`/hentikan loop saat backfill; `--ignore-window` production hanya `ESB_ALLOW_IGNORE_WINDOW=true` + ≤ 1 hari. `auth_suspended` setelah 2 siklus auth gagal → perbaiki password/username, `probe`, restart. 403 → slice `partial` tanpa checkpoint, dicoba ulang tiap siklus. §1a prasyarat aktivasi production: inventaris PDP + tanda tangan DPO (SEC-184), Q-ESB1/Q-ESB7/Q-ESB9, Metabase mart ESB hanya grup Holding sampai row policy G42 (SEC-182), redaksi raw_doc (SEC-181). | runbook |

Validasi: **tidak dijalankan** (sesi tanpa shell). Lead perlu menjalankan `docker compose ... -f docker-compose.esb.yml config -q`, `bash scripts/check-encryption.sh` (lokal: `.env` berisi username nyata hanya memicu info, bukan error), `shellcheck -x scripts/check-encryption.sh`. Dependensi kode: `ESB_ALLOW_IGNORE_WINDOW`, `ESB_MAX_RESPONSE_BYTES`, dan status `auth_suspended` diimplementasi efn-data-ai-engineer (paralel); saat sesi ini config.py belum membaca kedua env itu. Sisa username lama di repo: hanya `docs/04` Q-ESB2 (lead) dan laporan review — selama docs/04 belum diubah, cek (b) mengecualikannya.

## G22 (19 Okt 2026, branch chore/wave-22, HEAD 852b5fe) — secret QR meja, gateway path 13–15, redaksi `fts1.`, gerbang L-73, D-22-18

Kontrak: docs/04 §14.A.2/§14.A.5/§14.A.9/§14.A.10, §14.D.8–D.9, §14.E.2/E.4, §14.7 L-70/L-72 (D-22-18)/L-73/L-74.
Tidak ada commit (lead). Tidak ada tes yang saya jalankan (sesi tanpa shell) — semua **belum terverifikasi**; perintah
uji di bagian akhir.

> **Ringkasan manajemen.** Infrastruktur siap untuk pesan-dari-meja (QR) dan poin hangus: kunci rahasia baru dibuat
> terpisah per fungsi dan per PT, gerbang server ke Odoo hanya membuka tiga fungsi QR baru, "tiket" QR meja disensor dari
> semua log, dan fitur poin hangus/anonimisasi **tidak bisa** dinyalakan di production tanpa izin Superadmin di server
> (sesuai L-73, menunggu Legal/DPO/Meta).

### 1. Secret baru (§14.E.4)

| Secret | Jenis | Service | Env | Catatan |
|---|---|---|---|---|
| `fnb_table_token_key` | `random` (GitHub Env `FNB_TABLE_TOKEN_KEY`) | api-gateway | `FNB_TABLE_TOKEN_KEY_FILE` | IKM HKDF token `fts1.`; rotasi docs/runbooks/fnb-table-token-key.md |
| `odoo_fnb_event_hmac` | `random` (GitHub Env `ODOO_FNB_EVENT_HMAC`) | odoo + api-gateway | Odoo `EFN_FNB_EVENT_HMAC_FILE`, BFF `ODOO_FNB_EVENT_HMAC_FILE` | `X-EFN-Signature` POST /v1/pos/fnb/events |
| `odoo_api_key_qr_pb` | `host:disabled` | api-gateway (overlay `docker-compose.online-qr-pb.yml`) | `ODOO_API_KEY_QR_PB_FILE` | user `efn_qr_pb`; rotasi `efn-rotate-online-key --channel=QR-PB` |
| `odoo_api_key_qr_bcf` | `host:disabled` | api-gateway (overlay `docker-compose.online-qr-bcf.yml`) | `ODOO_API_KEY_QR_BCF_FILE` | user `efn_qr_bcf`; `--channel=QR-BCF` |

- `scripts/secrets.list` + top-level `secrets:` compose (sinkron; CI membandingkan). `gen-secrets.sh` tidak perlu
  diubah (generator `random`/`host:` generik). `deploy.yml` mengirim `random` otomatis dari `toJSON(secrets)` →
  **Superadmin wajib menambah `FNB_TABLE_TOKEN_KEY` dan `ODOO_FNB_EVENT_HMAC` ke GitHub Environment staging &
  production sebelum deploy G22** (deploy.sh berhenti "secret ... kosong").
- Overlay QR dinamai `docker-compose.online-qr-<pt>.yml` dengan sengaja: glob `docker-compose.online-*.yml` di CI
  (perbandingan secret) dan `check-encryption.sh` (render `prod-online`) otomatis ikut, dan `--bootstrap`
  efn-rotate-online-key memakai `docker-compose.online-${CH,,}.yml` tanpa kode khusus.
- `deploy.sh`: `FNB_QR_ENABLED=true` → setiap PT di `FNB_QR_COMPANIES` (default `PB,BCF`) wajib punya key QR nyata
  (bukan `disabled`) + overlay ada; overlay ditambahkan ke `FILES` (ikut `.efn-compose-files` untuk rotasi).
- `check-encryption.sh` §6/§6a2/§6a3/§7: secret QR hanya `pb|bcf` dan hanya `api-gateway`; `ODOO_API_KEY_QR_<PT>_FILE`
  ↔ secret ter-mount; `fnb_table_token_key` hanya & wajib di api-gateway; `odoo_fnb_event_hmac` hanya & wajib di
  odoo + api-gateway dengan env cocok; `LOYALTY_POINT_EXPIRY_DAYS` dilarang di env BFF semua render; render prod: kill
  switch `FNB_QR_ENABLED`, `FNB_QR_POINTS_ENABLED`, `ONLINE_LOYALTY_{REDEEM,EARN}_ENABLED`, `LOYALTY_EXPIRY_ENABLED`,
  `LOYALTY_INACTIVE_ANONYMIZE` = `false`.

### 2. Rotasi API key QR (perluasan `efn-rotate-online-key`, pola sama)

- `ALLOWED_CHANNELS` + `QR-PB QR-BCF`. Pemetaan: secret `odoo_api_key_qr_<pt>`, login `efn_qr_<pt>`, nama key
  `efn-qr-<PT>-<UTC>` (regex cabut-per-nama diperluas `efn-(online|qr)-...`), env `ODOO_API_KEY_QR_<PT>_FILE`, state
  `qr_<pt>.*`, metrik label `channel="QR-PB"` → semua alert `EfnOnlineApiKey*` yang ada langsung berlaku (tanpa rule
  baru = tidak berisik).
- Verifikasi di setiap replika BFF: path #15 `efn.pos.qr.order/efn_qr_status` body `{"config_id":0,"submission_refs":[]}`;
  **200 atau 422** = key sah (422 = `UserError efn_qr:*`, bearer + akses metode sudah lolos). 422 untuk kanal online
  tetap gagal (uji 97). Pembuktian key lama dicabut tetap 401.
- Precheck Odoo shell (Python) QR: modul/grup `efn_pos_bff_order.group_qr_bff` ada (`group_missing`), user anggota grup
  (`user_not_qr`), tanpa `base.group_system`/`base.group_erp_manager`/`point_of_sale.group_pos_user`
  (`user_privileged`, L-74), satu company dengan `efn_brand_code` = PT (`user_company_mismatch`), bukan user kanal online
  (`user_shared`).
- `--all`: kanal dari `CHANNELS` atau `.env` deploy (`ONLINE_CHANNELS` + `QR-<PT>` bila `FNB_QR_ENABLED=true`,
  `FNB_QR_COMPANIES` divalidasi `PB|BCF`); `QR-*` di `ONLINE_CHANNELS` ditolak. Lock per kanal, kunci host bersama
  deploy, alert per kanal — tidak berubah.
- JS verifikasi kini menerima nama env/secret/path/body dari skrip (nilai turunan allowlist, bukan input).
- Runbook: docs/runbooks/odoo-qr-api-key.md. **Skrip host wajib dipasang ulang** (`install ... /usr/local/sbin/efn-rotate-online-key`
  + sha256 di-review); versi lama menolak `QR-*`.

### 3. odoo-rpc-gateway path 13–15 (§14.A.5/§14.E.2)

- `haproxy.cfg`: `p_allowed` + `/json/2/efn.pos.qr.order/efn_qr_menu`, `/efn_qr_submit`, `/efn_qr_status` (15 path).
  Tidak ada header/aturan baru; `odoo-tls --allow-cn` tidak berubah.
- `test-gateway.sh`: +12 kasus (3 positif; negatif: model salah, sufiks `_v2`, huruf besar, `search_read`,
  `pos.order/create`, `pos.order/sync_from_ui`, `pos.config/efn_qr_session_open` (RPC POS bukan BFF), garis miring akhir,
  encoded `%61`) → 114 kasus. README diperbarui.
- **Ketidaksesuaian nama (untuk lead):** docs/04 §14.A.5 = model `efn.pos.qr.order` + `efn_qr_menu/submit/status`;
  docs/08 §13.1b (fit-gap) = `efn.pos.table.session.efn_qr_catalog`, `efn_qr_order_submit`, `efn_qr_order_status`; L-70
  mengganti nama modul ke `efn_pos_bff_order` tanpa menyebut model. Gateway mengikuti §14.A.5 sesuai instruksi. Bila
  efn-odoo-engineer memakai nama docs/08, tiga baris `p_allowed`, kasus uji gateway, dan path verifikasi rotasi
  (`RPC_PATH` di efn-rotate-online-key) harus diubah bersamaan dengan `RPC_ALLOWLIST` Odoo.

### 4. Redaksi Promtail `fts1.` + alert D.9

- `promtail.yml` & `promtail-journal.yml` tahap **(a0)** (pertama, sebelum (a)): `(fts1\.[A-Za-z0-9_.-]+)` →
  `[TABLE_TOKEN_REDACTED]`, tanpa batas kiri (bentuk URL-encoded `%3Dfts1.` juga kena). Fixture: api-gateway (token di
  teks + header), traefik (`?ts=`), generic (fragment `#t=`, juga lewat pipeline journal); expected.txt +7 entri.
- `traefik.yml`: header `X-Table-Session: drop` eksplisit (defaultMode sudah drop).
- Heartbeat W-17: promtail tahap (4) baru → `{stream="efn_loyalty", loyalty_event="expiry_run", expiry_status, expiry_phase}`.
  **Kontrak format untuk efn-bff-engineer:** logger `LoyaltyInactivity<Nama>`, level LOG/WARN/ERROR, baris
  `EFN_LOYALTY_EXPIRY_RUN {"status":"ok|dry_run|aborted|error","phase":"notice|expire|anonymize",...}` (kunci pertama
  `status`, kedua `phase`, sisanya jumlah tanpa data member). Fixture: dry_run, aborted, satu baris palsu (logger lain).
- `loki-rules/efn-loyalty.yml` grup `efn-loyalty-expiry`: `EfnLoyaltyExpiryAborted` (critical, aborted dalam 26 j),
  `EfnLoyaltyExpiryFailed` (warning, error dalam 26 j), `EfnLoyaltyExpiryHeartbeatMissing` (warning, dead-man 26 j,
  bersenjata hanya bila ada heartbeat 7 hari terakhir → production yang OFF tidak alarm). Runbook
  docs/runbooks/loyalty-inactivity-expiry.md. Metrik counter Prometheus (`efn_loyalty_expiry_total`) sengaja belum dipakai
  alert (deret hilang saat restart).

### 5. deploy-validate (A.10 API8, D-22-18, L-73) + env

- `EFN_RELEASE_CONFIG_VARS` + 28 variabel G22 (FNB_QR_*, FNB_*, ONLINE_LOYALTY_*, LOYALTY_EXPIRY_*, LOYALTY_ACTIVITY_EPOCH,
  LOYALTY_POINT_MAX_AGE_DAYS, LOYALTY_INACTIVE_ANONYMIZE*); `deploy.sh` menulis ke `.env` (kosong = default compose);
  `deploy.yml` `CFG_*`.
- `efn_validate_g22_config`: boolean, `FNB_CAPTCHA_MODE`, rentang (TTL 600..14400, baris 1..30, qty 1..20 = batas keras
  Odoo, retensi 7..730, link ≤ retensi, bulan 6..36, notice 7..60, jam 0..23, batch 1..5000, `MAX_PER_RUN` 1..999999,
  `MAX_AGE_DAYS` 0..3650, grace 0..90), `FNB_QR_COMPANIES` ⊆ {PB,BCF} unik, `FNB_QR_BASE_URL_<PT>` = `https://host[/path]`
  huruf kecil tanpa port/query/fragment/userinfo, `FNB_QR_ENABLED=true` → URL setiap PT wajib.
- **D-22-18:** `EFN_REMOVED_CONFIG_VARS=(LOYALTY_POINT_EXPIRY_DAYS)` → production ditolak (nilai apa pun, termasuk 0),
  staging peringatan; manifest yang memuatnya ditolak skema efn-deploy; check-encryption melarang di env BFF.
- **L-73:** `EFN_GATED_FEATURES=(LOYALTY_EXPIRY_ENABLED LOYALTY_INACTIVE_ANONYMIZE)`; production `true` hanya bila nama ada
  di **`EFN_ALLOW_FEATURE_ON`** — kunci baru `/etc/efn/deploy.conf` (dipin host, ditolak dari stdin, divalidasi
  topologi; satu izin tidak membuka yang lain). `LOYALTY_EXPIRY_ENABLED=true` wajib `LOYALTY_ACTIVITY_EPOCH`;
  anonimisasi tanpa job hangus ditolak. `efn-deploy` mengenal & mengekspor kunci baru → **wajib dipasang ulang** sebelum
  Superadmin menulis kunci itu (versi lama `die "kunci tidak dikenal"`).
- Compose base (`api-gateway`): semua env §14.A.9/§14.C.1/§14.D.8 dengan default kontrak; `LOYALTY_EXPIRY_DRY_RUN`
  default `true`; Odoo `EFN_FNB_EVENT_HMAC_FILE`. Dev: `FNB_QR_BASE_URL_{PB,BCF}=https://app.efn.local`. `.env.example`
  diperbarui (nilai non-rahasia; nilai kosong tanpa komentar inline karena parser .env compose memotong spasi depan).
- `FNB_RL_*`, `LOYALTY_POLICY_CEILING_INACTIVITY_MONTHS_MIN/_MAX`, `ONLINE_REDEEM_STEPUP_MIN_POINTS` **tidak** diekspos
  (default BFF; plafon kebijakan tidak boleh dilonggarkan lewat config rilis).

### 6. Untuk agent lain

- **efn-bff-engineer:** env & file secret persis tabel §1; key QR tidak ada (env tidak diset) = PT "QR tidak
  dikonfigurasi" (jangan gagal start); heartbeat `EFN_LOYALTY_EXPIRY_RUN` format §4; `LOYALTY_POINT_EXPIRY_DAYS` masih
  dibaca `app-config.ts:900` (default 365) — harus dihapus/ditolak sesuai D-22-18; `FNB_TABLE_TOKEN_KEY_PREV_FILE` belum
  di-wire compose.
- **efn-odoo-engineer (A):** grup `efn_pos_bff_order.group_qr_bff` wajib `api_key_duration` 35 hari (tanpa itu key user
  non-admin dibatasi 1 hari → rotasi gagal); `RPC_ALLOWLIST` + `test_rpc_surface.py` memuat 3 metode yang sama dengan
  gateway; `efn_qr_status(config_id=0, submission_refs=[])` harus menjawab 200 atau `UserError` (422), bukan 500.
  Odoo membaca `EFN_FNB_EVENT_HMAC_FILE`.
- **efn-data-ai-engineer:** promtail.yml & efn-loyalty.yml disentuh di tahap (a0)/(4) dan grup baru — rekonsiliasi bila
  ada perubahan paralel.
- **efn-security-reviewer:** gerbang L-73 di host (bukan manifest), redaksi `fts1.`, least privilege secret QR/HMAC,
  4 kasus 422 vs 401.

### 7. Belum terverifikasi / risiko

- Semua uji di bawah **belum dijalankan** (tidak ada shell di sesi ini); shellcheck v0.9.0 belum dijalankan — kode ditulis
  mengikuti pola yang sudah lolos (mis. `${PT,,}` bukan `tr 'A-Z'` untuk menghindari SC2018/2019).
- `expected.txt` promtail disusun manual (urutan `LC_ALL=C`); bila beda urutan/label, jalankan `--update` dan tinjau diff.
- Uji nyata gateway (114 kasus) & rotasi QR terhadap Odoo nyata menunggu modul `efn_pos_bff_order`.
- Nama model RPC QR (lihat §3).

### 8. Perintah uji (Git Bash, root repo)

```bash
PY=python:3.12-bookworm@sha256:e91fec3d1ac69f04e4eddcd29c327e630ce34658cf31075bfa7e8b0e052bafea
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/src:ro" "$PY" bash /src/scripts/host/test-efn-deploy.sh
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/src:ro" "$PY" bash /src/scripts/host/test-efn-rotate-online-key.sh
bash scripts/test-promtail-redaction.sh
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/mnt:ro" -w /mnt koalaman/shellcheck:v0.9.0 -x \
  scripts/*.sh scripts/lib/*.sh scripts/host/efn-deploy scripts/host/efn-online-cs scripts/host/efn-rotate-online-key \
  scripts/host/*.sh infra/odoo-rpc-gateway/test-gateway.sh infra/odoo-rpc-gateway/test-gateway-load.sh
bash scripts/gen-secrets.sh && cp -n infra/compose/.env.example infra/compose/.env
bash scripts/check-encryption.sh
(cd infra/compose && docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.payment.yml \
   -f docker-compose.pii.yml $(for f in docker-compose.online-*.yml; do printf -- '-f %s ' "$f"; done) \
   --profile core --profile data --profile ai --profile observability --profile ops config --format json \
   | jq -r '.secrets | keys[]' | sort | diff - <(awk '!/^[[:space:]]*#/ && NF {print $1}' ../../scripts/secrets.list | sort))
# haproxy -c gateway (seperti CI job infra) lalu, dengan stack dev berjalan:
bash infra/odoo-rpc-gateway/test-gateway.sh
```

### G22 r15 — QA G22 P2-3 (env ETL opsional) + SEC-171 (saklar consent notice hangus)

Tanpa shell: tidak ada tes yang saya jalankan. Semua di bawah **belum terverifikasi**; lead menjalankan
`test-efn-deploy.sh`, shellcheck v0.9.0, `check-encryption.sh`, `docker compose config` (perintah di atas).

> **Ringkasan manajemen.** Empat job analitik baru (wilayah pelanggan, KPI dapur, KDS kanal online, rekonsiliasi poin
> online) kini bisa dinyalakan lewat konfigurasi rilis resmi; default tetap mati. Saklar "WA pengingat poin hangus hanya
> ke member yang memberi consent" terpasang default **aktif**. Di production, saklar itu hanya bisa dilepas oleh
> Superadmin di server setelah keputusan Legal, memakai gerbang yang sama dengan fitur poin hangus.

**Perubahan**

| Area | Isi |
|---|---|
| compose `etl` | `ETL_PARTNER_REGION_ENABLED`, `ETL_LOYALTY_RECON_ONLINE_ENABLED`, `ETL_ENABLE_KDS`, `ETL_KDS_ONLINE_ENABLED` = `${X:-false}` (nama persis `services/etl/efn_etl/config.py:259-275`) |
| compose `api-gateway` | `LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT: ${…:-true}` |
| dev overlay | Hanya komentar: flag ETL baru **tidak** dipaksa `true` (butuh `efn_region`/`efn_pos_kds`/E-29a + grant G21/G22 di DB dev); nyalakan lewat `.env` |
| `.env.example` | 5 variabel + prasyarat |
| `deploy-validate.sh` | 5 nama masuk `EFN_RELEASE_CONFIG_VARS`; boolean `true|false`; prasyarat ETL cermin `config.py` (KDS/REGION/RECON_ONLINE + `ETL_ENABLE_ODOO=false` eksplisit ditolak; kosong diterima = aktif setelah grant; KDS_ONLINE butuh KDS; RECON_ONLINE butuh mode `payment`); `LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT` masuk `EFN_GATED_FEATURES` + daftar baru `EFN_GATED_FEATURES_OFF` (nilai berisiko = `false`) |
| `deploy.sh` | 5 kunci ke `.env` (default `false` / `true`); peringatan bila job ETL opsional `true` sementara `ETL_ENABLE_ODOO` efektif belum `true` (etl restart sampai grant sukses, lalu `up -d etl` yang sudah ada) |
| `deploy.yml` | `CFG_*` untuk 5 variabel (default `'false'` / `'true'`) |
| `efn-deploy` | Komentar header: nama izin baru di `EFN_ALLOW_FEATURE_ON` |
| `check-encryption.sh` | Render prod: `LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT == "true"` (wajib ada), 4 flag ETL `== "false"` (wajib ada) |
| `test-efn-deploy.sh` | 179b–179h (jalur penuh efn-deploy: manifest, izin deploy.conf, penerusan ke deploy.sh, stdin ditolak) + 251–269 (unit `efn_validate_config`) |
| runbook | `loyalty-inactivity-expiry.md`: baris kontrol SEC-171 dilengkapi status infra + langkah 4a melepas filter consent |

**Keputusan.** Gerbang `false` memakai `EFN_ALLOW_FEATURE_ON` (bukan `EFN_ALLOW_CONTROL_OFF`): keputusannya jenis yang
sama dengan L-73 (persetujuan Legal/Meta, bukan izin darurat), dan izin satu nama tidak membuka nama lain (tes 256/257).
Superadmin menulis `EFN_ALLOW_FEATURE_ON='LOYALTY_EXPIRY_ENABLED LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT'`; arti nama
kedua = "boleh `false`". Bila lead/security reviewer lebih suka semantik "kontrol off", cukup pindahkan nama ke
`EFN_SECURITY_CONTROLS` dan hapus dari `EFN_GATED_FEATURES*` (tes 251/252/179b/179c ikut diganti).

**Belum / risiko**
- BFF belum membaca env ini saat saya memeriksa (`app-config.ts` tanpa `LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT`); env yang
  belum dibaca tidak berbahaya. Parser BFF harus menerima `true|false` saja (sama dengan deploy-validate).
- ETL dengan flag opsional `true` dan `ETL_ENABLE_ODOO` belum `true` (deploy pertama sebelum grant) restart terus sampai
  grant sukses. Tidak ada `--wait` di deploy.sh sehingga deploy tidak gagal, tetapi alert container restart bisa muncul
  sebentar. Grant G21/G22 (view region/KDS/online) dan modul Odoo prasyarat **tidak** dicek deploy.sh (job ETL hanya
  mencatat gagal query bila view belum ada); urutan aktivasi ada di `services/etl/README.md`.
- `check-encryption.sh` sekarang mewajibkan kelima env ada di render prod; `.env` lokal developer yang menyalakan
  KDS/region akan membuat cek prod lokal gagal (sama dengan kill switch G22 lain). CI memakai `.env.example`.

## G21 (19 Okt 2026, branch chore/wave-21, HEAD 71aef24) — AC-4 CI PII, AC-13 kunci PII produksi, kunci kebijakan poin, path gateway ke-12

Kontrak: docs/04 §13.1 no. 3 (L-61), §13.2 (AC-4, AC-13, L-59), §13.5 (path ke-12, L-63), §13.6 baris devsecops;
ADR-036 §0, ADR-022. Tidak di-commit. **Tidak ada perintah yang dijalankan** oleh agent ini (sesi tanpa shell/docker);
semua uji di bawah wajib dijalankan lead. YAML/shell diperiksa manual (indentasi, anchor, referensi secret).

### File diubah / baru
- `.github/workflows/ci.yml` — `EFN_TEST_MODULES`/`EFN_INSTALL_ONLY_MODULES` dipindah ke `env:` tingkat workflow (sumber
  tunggal untuk dua job; isi tidak berubah). **Job baru `odoo-test-pii`** (AC-4): path-filter `odoo`, `permissions:
  contents: read`, postgres service + build image (pola `odoo-test`), modul = `efn_pii efn_pii_pos efn_pii_hr
  efn_pii_account` (wajib ada, gagal bila hilang) + semua `efn_pii_*` lain yang ditemukan (`efn_pii_base_vat`, nanti
  `efn_pii_l10n_id`) + `EFN_TEST_MODULES`, lalu `efn_setup_security` tanpa test di DB yang sama. 3 kunci
  `openssl rand -base64 32` di `$RUNNER_TEMP/efn-pii-keys` (tidak di repo/log/artefak/`GITHUB_ENV`; dicek berbeda;
  dihapus `if: always()`), mount `:ro` ke `/run/efn-pii-test` + `-e EFN_ODOO_PII_KEY_DIR`. Pemeriksaan log: ERROR/CRITICAL
  = gagal; test `odoo.addons.efn_pii.tests.*` wajib muncul (anti-skip); nilai kunci di log = gagal (`grep -qF -f`, tanpa
  mencetak). Masuk `ci-gate.needs`. Langkah infra "secret == secrets.list" kini menyertakan `docker-compose.pii.yml`.
- `infra/compose/docker-compose.pii.yml` (**baru**, AC-13) — hanya service `odoo`: env `EFN_ODOO_PII_KEY_V1_FILE`,
  `EFN_ODOO_PII_ACTIVE_KEY_VERSION=1`, `EFN_ODOO_PII_BIDX_KEY_FILE`, `EFN_ODOO_PII_NAME_KEY_FILE`,
  `EFN_ODOO_PII_REQUIRED=${EFN_ODOO_PII_REQUIRED:-true}` + secrets `odoo_pii_{key_v1,bidx_key,name_key}`.
- `infra/compose/docker-compose.yml` — top-level `loyalty_policy_signing_key` + `odoo_pii_*` (definisi; mount PII hanya
  overlay/dev). odoo: `EFN_LOYALTY_POLICY_SIGNING_KEY_FILE` + mount; api-gateway: `LOYALTY_POLICY_SIGNING_KEY_FILE` + mount.
- `infra/compose/docker-compose.dev.yml` — definisi top-level `odoo_pii_*` duplikat dihapus (kini di base); mount dev tetap.
- `infra/compose/.env.example` — `EFN_ODOO_PII_ENABLED=false`, `EFN_ODOO_PII_REQUIRED=` (non-rahasia).
- `scripts/secrets.list` — `loyalty_policy_signing_key random`; `odoo_pii_key_v1`, `odoo_pii_bidx_key`,
  `odoo_pii_name_key` = `host:disabled [SA]`.
- `scripts/gen-secrets.sh` — loop utama melewati `odoo_pii_*` (dev tetap 32 byte acak di bagian PII; file berisi
  placeholder `disabled` diganti kunci acak, file kunci nyata tidak pernah ditimpa).
- `scripts/lib/deploy-validate.sh` — `EFN_ODOO_PII_ENABLED`/`EFN_ODOO_PII_REQUIRED` di `EFN_RELEASE_CONFIG_VARS`
  (boolean); production + ENABLED=true tanpa `REQUIRED=true` ditolak (tidak bisa dibuka `EFN_ALLOW_CONTROL_OFF`);
  fungsi baru `efn_validate_pii_keys <dir>` (file biasa, bukan symlink, 32 byte base64 kanonik, bukan `disabled`, ketiganya
  berbeda; pesan tanpa nilai) dan `efn_pii_module_state_ok <enabled> <state>`.
- `scripts/deploy.sh` — ENABLED=true → `efn_validate_pii_keys` (langkah 1) + overlay `docker-compose.pii.yml`; `.env`
  memuat `EFN_ODOO_PII_ENABLED/REQUIRED`; setelah migrasi & **sebelum `up`**: `efn_pii` berstatus
  installed/to upgrade/to remove di `efn_odoo` tanpa ENABLED=true → gagal (Odoo lama tidak di-recreate tanpa kunci).
- `.github/workflows/deploy.yml` — `CFG_EFN_ODOO_PII_ENABLED` (default `false`), `CFG_EFN_ODOO_PII_REQUIRED`.
- `scripts/check-encryption.sh` — `loyalty_policy_signing_key` hanya odoo + api-gateway **dan** wajib ter-mount di
  keduanya dengan env yang cocok (§6a); render `prod`/`prod-online` tidak boleh me-mount `odoo_pii_*` ke odoo; render
  baru `prod-pii` (base + prod + pii): ketiga kunci + env cocok + `EFN_ODOO_PII_REQUIRED == "true"`.
- `scripts/host/test-efn-deploy.sh` — kasus baru 110c–110j (e2e efn-deploy) dan 139–165 (unit), lihat di bawah.
- `infra/odoo-rpc-gateway/{haproxy.cfg,test-gateway.sh,README.md}` — path ke-12
  `/json/2/efn.online.order/efn_online_set_invoice_party` (pola L-60); uji: +1 positif, +4 negatif (model salah, sufiks
  `_v2`, `res.partner/create`, `res.partner/search_read`) → 102 kasus.
- Runbook: `docs/runbooks/key-custody.md` (§1 baris kunci PII Odoo, **§4e baru** `{#pii-odoo}`: pembuatan di host,
  salinan tersegel brankas Direksi sebelum aktivasi, gerbang otomatis, verifikasi, larangan; §8.1 isi paket; §8.5 butir 5),
  `docs/runbooks/pii-key-rotation.md` (status G21, anchor `{#kunci-pii-odoo}`, rotasi produksi bidx/name & key ring
  v1→v2 di host, kunci hilang), `docs/runbooks/secret-rotation.md` (baris `loyalty_policy_signing_key`, `odoo_pii_*`),
  `docs/runbooks/ci-github-free-plan.md` (perkiraan menit job baru, belum diukur).

### Kasus uji baru `test-efn-deploy.sh`
| # | Kasus | Harapan |
|---|---|---|
| 110c–110e | `ODOO_PII_KEY_V1` / `_BIDX_KEY` / `_NAME_KEY` lewat stdin efn-deploy | ditolak (host:*) |
| 110f | `LOYALTY_POLICY_SIGNING_KEY` lewat stdin | diterima |
| 110g | `EFN_ODOO_PII_ENABLED` lewat stdin | ditolak (SEC-50) |
| 110h / 110i | manifest production ENABLED=true tanpa REQUIRED / REQUIRED=false | ditolak |
| 110j | manifest production ENABLED=true + REQUIRED=true | diterima |
| 139–146 | `efn_validate_config` kombinasi saklar PII (prod/staging, non-boolean, default mati) | sesuai tabel AC-13 |
| 147–158 | `efn_validate_pii_keys`: sah, newline akhir, `disabled`, hilang, kosong, duplikat, 16 byte, hex, non-kanonik, multi-baris, symlink, pesan tanpa nilai | 0 hanya 147/148 |
| 159–165 | `efn_pii_module_state_ok`: installed/to upgrade/to remove tanpa ENABLED gagal; uninstalled/kosong lulus; CR | sesuai |

### Kontrak untuk agent lain (rekonsiliasi lead)
- **Nama env `loyalty_policy_signing_key`** (dipilih di sini; kontrak §13.1 hanya menyebut nama secret): Odoo
  `EFN_LOYALTY_POLICY_SIGNING_KEY_FILE`, BFF `LOYALTY_POLICY_SIGNING_KEY_FILE` (pola `*_HMAC_FILE` G17). efn-odoo-engineer A
  dan efn-bff-engineer harus membaca nama ini; `check-encryption.sh` §6a gagal bila env/mount tidak cocok.
- `LOYALTY_POLICY_CEILING_*` (plafon BFF §13.1 no. 4) **belum** ditambahkan ke compose/deploy-validate: nama & rentang
  milik BFF. Bila BFF butuh env baru, kirim daftar → devsecops menambah ke compose + `EFN_RELEASE_CONFIG_VARS` + `deploy.yml`.
- efn-odoo-engineer C: `efn_online_set_invoice_party` wajib ada di `RPC_ALLOWLIST`/`test_rpc_surface` Odoo; gateway sudah.
- GitHub Environment `staging`/`production`: tambah secret **`LOYALTY_POLICY_SIGNING_KEY`** (`openssl rand -base64 36`)
  sebelum deploy G21 (tanpa itu `deploy.sh` berhenti "secret ... kosong"). Kunci PII Odoo **tidak** ditambahkan ke GitHub.

### Perintah uji persis (Git Bash, root repo; `MSYS_NO_PATHCONV=1` hanya untuk `docker run`)
```bash
# 1) shellcheck (pola CI)
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/mnt" -w /mnt koalaman/shellcheck:stable -x \
  scripts/*.sh scripts/lib/*.sh scripts/host/efn-deploy scripts/host/*.sh infra/odoo-rpc-gateway/test-gateway.sh
# 2) regresi efn-deploy + deploy-validate (kasus baru 110c-110j, 139-165)
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/src:ro" \
  python:3.12-bookworm@sha256:e91fec3d1ac69f04e4eddcd29c327e630ce34658cf31075bfa7e8b0e052bafea \
  bash /src/scripts/host/test-efn-deploy.sh
# 3) secret dev baru + lint enkripsi (butuh docker compose + jq di host; render dev, prod, prod-online, prod-pii)
bash scripts/gen-secrets.sh            # membuat secrets/loyalty_policy_signing_key.txt; kunci PII dev tidak ditimpa
[ -f infra/compose/.env ] || cp infra/compose/.env.example infra/compose/.env
bash scripts/check-encryption.sh
# 4) compose secrets == secrets.list (langkah CI infra)
cd infra/compose && overlays=(); for f in docker-compose.online-*.yml; do overlays+=(-f "$f"); done
diff <(docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.payment.yml -f docker-compose.pii.yml \
  "${overlays[@]}" --profile core --profile data --profile ai --profile observability --profile ops config --format json \
  | jq -r '.secrets | keys[]' | sort) <(awk '!/^[[:space:]]*#/ && NF {print $1}' ../../scripts/secrets.list | sort) && cd ../..
# 5) yamllint
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/src:ro" -w /src \
  python:3.12-bookworm@sha256:e91fec3d1ac69f04e4eddcd29c327e630ce34658cf31075bfa7e8b0e052bafea \
  sh -c 'pip install -q yamllint && yamllint -c .yamllint.yml infra .github'
# 6) haproxy -c gateway (pola CI) lalu uji nyata path ke-12 di stack dev
cat secrets/tls_odoo_rpc_gateway_crt.txt secrets/tls_odoo_rpc_gateway_key.txt > /tmp/odoo-rpc-gateway.pem && chmod 644 /tmp/odoo-rpc-gateway.pem
MSYS_NO_PATHCONV=1 docker run --rm -e ODOO_DB=efn_odoo -e ODOO_RPC_MAXCONN=4 \
  -v "$(pwd -W)/infra/odoo-rpc-gateway/haproxy.cfg:/usr/local/etc/haproxy/haproxy.cfg:ro" \
  -v "$(cygpath -m /tmp/odoo-rpc-gateway.pem):/tmp/haproxy/odoo-rpc-gateway.pem:ro" \
  -v "$(pwd -W)/secrets/efn_ca_crt.txt:/run/secrets/efn_ca_crt:ro" \
  haproxy:3.2.25-alpine@sha256:5d97434a423c2533cfeb42874d45cf6d69a840b60334582dfbfd5008e94c80be haproxy -c -f /usr/local/etc/haproxy/haproxy.cfg
(cd infra/compose && docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile core up -d --force-recreate odoo-rpc-gateway odoo api-gateway)
bash infra/odoo-rpc-gateway/test-gateway.sh     # harap TOTAL_FAIL=0 (102 kasus)
# 7) replikasi lokal job odoo-test-pii (image efn-odoo:ci + postgres sementara; ±10-15 menit)
docker build -t efn-odoo:ci services/odoo
docker run -d --name efn-pg-pii -e POSTGRES_USER=odoo -e POSTGRES_PASSWORD=odoo-ci-only -p 55432:5432 postgres:16.15-bookworm
K="$(mktemp -d)"; for k in odoo_pii_key_v1 odoo_pii_bidx_key odoo_pii_name_key; do openssl rand -base64 32 | tr -d '\r\n' > "$K/$k"; done
MSYS_NO_PATHCONV=1 docker run --rm --network host --entrypoint odoo -e EFN_ODOO_PII_KEY_DIR=/run/efn-pii-test \
  -v "$(cygpath -m "$K"):/run/efn-pii-test:ro" efn-odoo:ci --db_host=127.0.0.1 --db_port=55432 --db_user=odoo \
  --db_password=odoo-ci-only --addons-path=/mnt/extra-addons,/usr/lib/python3/dist-packages/odoo/addons -d efn_test_pii \
  -i "efn_pii,efn_pii_pos,efn_pii_hr,efn_pii_account,efn_pii_base_vat,efn_core,efn_audit,efn_pos_otp_gate,efn_kitting,efn_pos_payment_gateway,efn_pos_loyalty_ledger,efn_privacy,efn_pos_kds,efn_setup_company,efn_setup_pos,efn_setup_mrp,efn_setup_audit,efn_online_order,efn_setup_online,efn_online_pos_sale,efn_pos_refund_origin,efn_online_pos_counter" \
  --test-tags efn --stop-after-init --log-level=test --workers=0 --max-cron-threads=0 2>&1 | tee odoo-test-pii.log
grep -E '^[0-9-]+ [0-9:,]+ [0-9]+ (ERROR|CRITICAL) ' odoo-test-pii.log; grep -c 'odoo\.addons\.efn_pii\.tests\.' odoo-test-pii.log
docker rm -f efn-pg-pii; rm -rf "$K"
```
Catatan: `--network host` di Docker Desktop (WSL2) hanya bekerja bila fitur host networking aktif; bila tidak, ganti
dengan network bersama (`docker network create`) + `--db_host=efn-pg-pii`.

### Belum terverifikasi / risiko (jujur)
- Tidak satu pun perintah di atas dijalankan. Paling berisiko: (a) `odoo-test-pii` bisa **merah** karena modul lain
  (POS/online/loyalty) membuat partner perorangan dan belum pernah diuji dengan `efn_pii` aktif — itu memang tujuan AC-4;
  temuan = pekerjaan efn-odoo-engineer B, bukan bug job; (b) test `efn_pii` yang sengaja menguji mode terkunci bisa
  mencetak CRITICAL ke log bila tidak memakai `assertLogs`/`mute_logger` → job gagal di pemeriksaan log (perlu dilihat di
  run pertama; jangan longgarkan grep, perbaiki test); (c) pola grep `odoo.addons.efn_pii.tests.` mengasumsikan format
  logger test Odoo 19 standar.
- Menit CI: job baru ±7–9 menit per run Odoo (perkiraan, belum diukur).
- Staging yang sudah memasang `efn_pii` (uji manual) akan **gagal deploy** sampai kunci staging dibuat + ENABLED=true
  (fail-closed, disengaja). Production belum punya DB → tidak terdampak.
- `efn_validate_pii_keys` tidak menilai entropi (kunci `AAAA…A=` lolos format); kunci wajib dari `openssl rand` (runbook).
- Rotasi `loyalty_policy_signing_key` tanpa jeda butuh BFF menerima 2 kunci (belum dikontrakkan; saat ini jendela
  401 ditutup retry outbox Odoo).
- Restore DR ke host baru membutuhkan kunci PII dari paket brankas (key-custody §8.6); `restore-test.sh` (dump DB) tidak
  terpengaruh karena ciphertext dipulihkan apa adanya — tetapi uji pemulihan 2×/tahun kini wajib mencocokkan hash
  `odoo_pii_*` (key-custody §8.5 butir 5).

## G20 (18 Okt 2026, branch chore/wave-20) — kanal = kode PT (GL/PB/BCR/BCF), bayar di kasir, kunci token nama PII

Kontrak: docs/04 §12.1 (L-48), §12.2 no. 2, §12.5, §12.6 baris devsecops; ADR-036 §0.5. Tidak di-commit. **Tidak ada
perintah yang dijalankan** oleh agent ini (sesi tanpa shell); semua uji di bawah wajib dijalankan lead.

### File diubah / baru
- `scripts/secrets.list` — `odoo_api_key_online_{gl,pb,bcr,bcf}` (`host:disabled`) menggantikan `_rtl`, `_fnb`,
  `_rtl_gl`, `_rtl_bacha`, `_fnb_pb`, `_fnb_bacha`.
- `infra/compose/docker-compose.yml` — secret top-level 4 kanal baru; BFF: `ODOO_API_KEY_ONLINE_GL_FILE` + mount
  `odoo_api_key_online_gl` (kanal default di base), `ONLINE_CHANNELS` default `GL`, **baru**
  `ONLINE_MAX_OPEN_COUNTER_ORDERS_PER_MEMBER` default `1`.
- **Baru** `infra/compose/docker-compose.online-{pb,bcr,bcf}.yml` (mount secret + env `_FILE` hanya api-gateway).
- **Harus dihapus lead** (`git rm`; agent tanpa shell tidak bisa menghapus file): `infra/compose/docker-compose.online-{fnb,rtl-gl,rtl-bacha,fnb-pb,fnb-bacha}.yml`.
  Isinya sudah dinetralkan menjadi `services: {}` + komentar tombstone sehingga render compose/CI tetap valid bila lupa.
- `infra/compose/docker-compose.dev.yml` — secret dev `odoo_pii_name_key` (top-level dev), mount HANYA `odoo`, env
  `EFN_ODOO_PII_NAME_KEY_FILE=/run/secrets/odoo_pii_name_key`; komentar ADR-036 Accepted; komentar key dev GL.
- `infra/compose/.env.example` — `ONLINE_CHANNELS=GL` + penjelasan kode G20; `ONLINE_MAX_OPEN_COUNTER_ORDERS_PER_MEMBER=1`.
- `scripts/gen-secrets.sh` — `odoo_pii_name_key` di loop yang sama dengan `odoo_pii_key_v1`/`odoo_pii_bidx_key`
  (b64key32, idempoten, tidak pernah menimpa, di luar secrets.list).
- `scripts/check-encryption.sh` — §6: secret `odoo_api_key_online_*` ter-render wajib `gl|pb|bcr|bcf`; setiap env BFF
  `ODOO_API_KEY_ONLINE_<KODE>_FILE` wajib = `/run/secrets/odoo_api_key_online_<kode>` yang di-mount ke BFF; nama
  tetap yang diperiksa `odoo_api_key_online_gl`. **§6b baru** (render dev): odoo wajib me-mount ketiga kunci PII
  (`odoo_pii_key_v1`, `odoo_pii_bidx_key`, `odoo_pii_name_key`) dengan env `*_FILE` yang cocok. Least privilege
  `odoo_pii_*` → hanya odoo (sudah generik sejak G19).
- `scripts/lib/deploy-validate.sh` — `EFN_ONLINE_CHANNEL_CODES=(GL PB BCR BCF)`, `EFN_ONLINE_BASE_CHANNEL=GL`, fungsi
  `efn_validate_online_channels` (allowlist + unik; kode lama/kanal G19 ditolak dengan pesan jelas), dipanggil
  `efn_validate_config`; `ONLINE_MAX_OPEN_COUNTER_ORDERS_PER_MEMBER` masuk `EFN_RELEASE_CONFIG_VARS`, rentang 1..10
  (= BFF `app-config.ts`), wajib ≤ `ONLINE_MAX_OPEN_ORDERS_PER_MEMBER` (BFF menghitungnya juga dalam batas total).
- `scripts/deploy.sh` — default kanal `GL`; overlay untuk setiap kanal ≠ GL; `efn_validate_online_channels` sebelum
  overlay; cek API key nyata per kanal (nama huruf kecil); `.env` + `ONLINE_MAX_OPEN_COUNTER_ORDERS_PER_MEMBER`;
  migrasi wajib bertambah `09-platform-g20.sql` (file milik efn-bff-engineer, sudah ada di working tree).
- `.github/workflows/deploy.yml` — `CFG_ONLINE_CHANNELS` default `GL`; `CFG_ONLINE_MAX_OPEN_COUNTER_ORDERS_PER_MEMBER`.
- `.github/workflows/ci.yml` — komentar saja (diff secrets.list↔compose sudah generik atas `docker-compose.online-*.yml`).
- `scripts/host/efn-rotate-online-key` — `ALLOWED_CHANNELS=(GL PB BCR BCF)`, `BASE_CHANNEL=GL`; `--channel`, `CHANNELS`
  conf, dan fallback `ONLINE_CHANNELS` `.env` divalidasi allowlist; fallback default `GL`; `--bootstrap` overlay untuk
  semua kanal ≠ GL; LOGIN/secret = `efn_online_<kode>` / `odoo_api_key_online_<kode>`; header & contoh diperbarui.
  **Skrip host berubah → pasang ulang di host + cocokkan sha256 (runbook §1).**
- `scripts/host/systemd/efn-rotate-online-key@.service` — komentar instance `@GL`.
- `scripts/host/test-efn-rotate-online-key.sh` — semua kasus lama dipertahankan dengan pemetaan RTL→GL, RTL-GL→PB,
  FNB-BACHA→BCF, FNB-PB/RTL-BACHA→BCR; kasus baru 55b–55e (RTL/FNB/RTL-GL/FNB-BACHA ditolak), 61b (CHANNELS kanal
  G19), 71b (`.env` RTL ditolak tanpa fallback), 71c (`.env` tanpa ONLINE_CHANNELS → GL). Total 90 kasus.
- `scripts/host/test-efn-deploy.sh` — 89/91/92 ke kode G20; 110 → `ODOO_API_KEY_ONLINE_GL` (benar-benar `host:*`);
  baru 92b–92k (kode lama/G19/tak dikenal/ganda ditolak, BCF diterima, rentang & relasi batas counter).
- `scripts/smoke/run-e2e-smoke.sh` — key `secrets/odoo_api_key_online_gl.txt`, `channel_code` `GL` (10 path gateway),
  katalog `channel_code='GL'`, saldo pembuka migrasi `company_code` `PB`.
- `scripts/smoke/online_smoke.py` — `CHANNEL=GL`, `COMPANY=GL`, `OTHER_COMPANY=PB` (uji `company_mismatch`).
- `scripts/smoke/README.md`, `infra/odoo-rpc-gateway/test-gateway{,-load}.sh` (payload `GL`, kosmetik),
  `infra/observability/alert-rules.yml` (komentar label channel).
- Runbook: `docs/runbooks/odoo-online-api-key.md` (tabel kanal G20, §1b `#kanal-baru` untuk PB/BCR/BCF, galat allowlist,
  **§8 baru `#migrasi-g20`** cutover dev/host), **baru** `docs/runbooks/dev-reset.md` (dirujuk docs/04 §12.1 dan
  09-platform-g20.sql tetapi belum ada), `docs/runbooks/online-orders.md` (kanal PT + batas bayar di kasir),
  `docs/runbooks/pii-key-rotation.md` (bagian kunci PII Odoo: tiga kunci, rotasi bidx/name hanya bila bocor, AC-8).

### Cara menguji (perintah persis, Git Bash dari root repo)
```bash
git rm infra/compose/docker-compose.online-fnb.yml infra/compose/docker-compose.online-rtl-gl.yml \
  infra/compose/docker-compose.online-rtl-bacha.yml infra/compose/docker-compose.online-fnb-pb.yml \
  infra/compose/docker-compose.online-fnb-bacha.yml
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/src:ro" python:3.12-bookworm@sha256:e91fec3d1ac69f04e4eddcd29c327e630ce34658cf31075bfa7e8b0e052bafea bash /src/scripts/host/test-efn-rotate-online-key.sh
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/src:ro" python:3.12-bookworm@sha256:e91fec3d1ac69f04e4eddcd29c327e630ce34658cf31075bfa7e8b0e052bafea bash /src/scripts/host/test-efn-deploy.sh
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/mnt:ro" koalaman/shellcheck:stable -x /mnt/scripts/host/efn-rotate-online-key /mnt/scripts/host/test-efn-rotate-online-key.sh /mnt/scripts/host/test-efn-deploy.sh /mnt/scripts/deploy.sh /mnt/scripts/lib/deploy-validate.sh /mnt/scripts/gen-secrets.sh /mnt/scripts/check-encryption.sh /mnt/scripts/smoke/run-e2e-smoke.sh /mnt/infra/odoo-rpc-gateway/test-gateway.sh /mnt/infra/odoo-rpc-gateway/test-gateway-load.sh
bash scripts/gen-secrets.sh            # membuat secrets/odoo_api_key_online_{gl,pb,bcr,bcf}.txt + secrets/odoo_pii_name_key.txt
cd infra/compose && cp -n .env.example .env
docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile core --profile data --profile ai --profile observability --profile ops config -q
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile core --profile data --profile ai --profile observability --profile ops config -q
docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.payment.yml $(for f in docker-compose.online-*.yml; do printf -- '-f %s ' "$f"; done) --profile core --profile data --profile ai --profile observability --profile ops config --format json | jq -r '.secrets|keys[]' | sort | diff - <(awk '!/^[[:space:]]*#/ && NF {print $1}' ../../scripts/secrets.list | sort)
cd ../.. && bash scripts/check-encryption.sh   # butuh docker + jq
bash scripts/smoke/run-e2e-smoke.sh o          # setelah dev-reset + odoo_online_smoke.py G20 (temuan 2)
```
Catatan: `infra/compose/.env` lokal yang sudah ada tidak memuat `ONLINE_CHANNELS` (dicek) → default GL berlaku.

### Temuan untuk lead (penting)
1. **Kebijakan deploy vs bayar di kasir (keputusan dibutuhkan).** `deploy-validate` masih menolak production
   `ONLINE_ENABLED=true` tanpa `PAYMENT_PROVIDER=xendit|midtrans` (kasus uji 88). §12.2 menyatakan pilot tanpa vendor =
   `counter` saja → aturan ini akan **memblokir pilot** di production. Tidak saya ubah (gerbang keamanan; butuh
   keputusan lead/security reviewer). Opsi: izinkan `PAYMENT_PROVIDER=disabled` bila config rilis baru mis.
   `ONLINE_PAYMENT_METHODS=counter` (BFF menolak `qris` bila provider disabled) — perlu kontrak.
2. **`odoo_online_smoke.py` & `odoo_pos_smoke.py` (milik builder Odoo)** masih `efn_online_rtl`/kanal `RTL`/company
   `FNB`. Smoke shell & `online_smoke.py` sudah GL/PB → tahap `o` gagal sampai builder Odoo A mengubah file itu
   (kanal `GL`, user `efn_online_gl`, `efn_brand_code` `PB`).
3. **Env nama kunci token nama** `EFN_ODOO_PII_NAME_KEY_FILE` belum ada di `efn_pii` (builder B, grep kosong). Default
   `EFN_ODOO_PII_KEY_DIR=/run/secrets` + nama file `odoo_pii_name_key` juga cocok bila builder B memakai pola direktori.
   Mohon disamakan; bila nama env berbeda, ubah satu baris di `docker-compose.dev.yml` dan `check-encryption.sh` §6b.
4. `09-platform-g20.sql` (BFF) menyebut "File 02..08 sudah memuat daftar kode baru", padahal 02/03/05/07/08 di working
   tree masih `('EFN','RTL','FNB','FARM')` (saat saya baca). Aman secara fungsi (09 menulis ulang constraint), tetapi
   komentar/isi harus konsisten — konfirmasi ke efn-bff-engineer.
5. Allowlist kanal kini di tiga tempat (deploy-validate, skrip rotasi host, regex check-encryption) — sengaja: skrip
   host berdiri sendiri (dipasang manual + sha256). PT baru = ubah ketiganya (runbook §1b).

### Yang belum / tidak terverifikasi
- Tidak satu pun perintah dijalankan; YAML/shell diperiksa manual: setiap secret di overlay baru didefinisikan top-level
  base; `odoo_pii_name_key` didefinisikan top-level dev dan hanya di-mount odoo; jq baru di check-encryption memakai
  `capture(...) | .k` dan `//` dengan precedence yang benar.
- `services: {}` sebagai isi override yang dinetralkan belum diuji `docker compose config` (diyakini valid; tetap
  sebaiknya di-`git rm`).
- Cutover host (§8 runbook) belum dijalankan (staging belum ada). Uji nyata kanal PB/BCR/BCF menunggu data Odoo G20.
- `efn_pii` belum memakai kunci token nama (builder B); check-encryption §6b hanya memastikan kunci ter-mount.

---

## G19 (18 Okt 2026, branch chore/wave-19) — secret kanal berhifen, rotasi multi-kanal, env BFF, PoC PII Odoo

Kontrak: docs/04 §11.1, §11.2 no. 6, §11.3 no. 3, §11.5. Tidak di-commit. **Tidak ada yang dijalankan** oleh agent ini
(tanpa shell); semua di bawah wajib dijalankan lead.

### File diubah / baru
- `infra/compose/docker-compose.yml` — secret top-level `odoo_api_key_online_{rtl_gl,rtl_bacha,fnb_pb,fnb_bacha}`; BFF
  `ONLINE_PAYMENT_TIMEOUT_SECONDS` default 3600, `ONLINE_MAX_OPEN_ORDERS_PER_MEMBER` 2, `ONLINE_RL_PROMO_MEMBER_LIMIT=10`,
  `_MEMBER_WINDOW_SECONDS=900`, `ONLINE_RL_PROMO_IP_LIMIT=30`, `_IP_WINDOW_SECONDS=900` (nama = `ONLINE_RL_<NAMA>_LIMIT|WINDOW_SECONDS` app-config BFF).
- **Baru** `infra/compose/docker-compose.online-{rtl-gl,rtl-bacha,fnb-pb,fnb-bacha}.yml` — mount secret + `ODOO_API_KEY_ONLINE_<KANAL>_FILE` hanya ke api-gateway.
- `infra/compose/docker-compose.dev.yml` — PoC E-38: secret dev `odoo_pii_key_v1`, `odoo_pii_bidx_key` (top-level dev saja), mount HANYA `odoo`, env `EFN_ODOO_PII_KEY_V1_FILE`, `EFN_ODOO_PII_ACTIVE_KEY_VERSION=1`, `EFN_ODOO_PII_BIDX_KEY_FILE`.
- `infra/compose/.env.example` — 3600 / 2 / 4 env promo RL, catatan kanal berhifen + urutan aktivasi.
- `infra/observability/alert-rules.yml` — komentar/deskripsi alert rotasi untuk `--all` (tanpa alert baru).
- `scripts/secrets.list` — 4 baris `host:disabled` kanal brand.
- `scripts/gen-secrets.sh` — bagian PoC PII Odoo (b64key32, idempoten, di LUAR secrets.list → tidak diwajibkan deploy/production; pola `dev_edge_*`).
- `scripts/host/efn-rotate-online-key` — `--all` (CHANNELS di conf, fallback `ONLINE_CHANNELS` dari `/opt/efn/compose/.env` milik root), anak per kanal berurutan, satu overlap + cabut tertunda, `mark_failed` per kanal (metrik `last_run_ok=0` walau anak gagal sebelum menulis metrik), `--no-wait`, `--bootstrap` (overlay kanal ikut sementara, hanya bila secret masih `disabled`), validasi CHANNELS (regex kontrak, unik, maks 8), precheck Python `user_not_in_channel` / `user_shared`, `EFN_CH` ke odoo shell, `render_prom` dipisah.
- **Baru** `scripts/host/systemd/efn-rotate-online-key.{service,timer}` (`--all`, TimeoutStartSec 6h). Unit `@` lama dipertahankan.
- `scripts/host/test-efn-rotate-online-key.sh` — kasus 1–49 lama dipertahankan (docker palsu diperluas, semantik sama); kasus baru 50–84 termasuk blok Python odoo shell dieksekusi nyata terhadap env Odoo palsu.
- **Di luar daftar cakupan (perlu agar konsisten; mohon lead tinjau/revert bila tidak setuju):**
  `scripts/deploy.sh` (overlay per kanal `docker-compose.online-<kanal>.yml` untuk setiap kanal ≠ RTL di ONLINE_CHANNELS, gagal bila overlay tidak ada — sebelumnya hanya `FNB`),
  `scripts/check-encryption.sh` (least privilege generik: semua `odoo_api_key_online_*` hanya api-gateway, `odoo_pii_*` hanya odoo; render tambahan `prod-online` dengan semua overlay),
  `.github/workflows/ci.yml` (diff secrets.list↔compose menyertakan semua `docker-compose.online-*.yml` — TANPA ini CI infra gagal karena secret baru tidak ter-render).
- Runbook: `docs/runbooks/odoo-online-api-key.md` (tabel pemetaan kanal, §1 timer `--all`, §1b `#kanal-baru`, galat baru, batas), `docs/runbooks/online-orders.md` (`#aktivasi-reservasi-1-jam`: Odoo 70 dulu, baru BFF 3600; mundur kebalikannya).

### Cara menguji (perintah persis, Git Bash dari root repo)
```bash
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/src:ro" python:3.12-bookworm@sha256:e91fec3d1ac69f04e4eddcd29c327e630ce34658cf31075bfa7e8b0e052bafea bash /src/scripts/host/test-efn-rotate-online-key.sh
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/src:ro" python:3.12-bookworm@sha256:e91fec3d1ac69f04e4eddcd29c327e630ce34658cf31075bfa7e8b0e052bafea bash /src/scripts/host/test-efn-deploy.sh
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/mnt:ro" koalaman/shellcheck:stable -x /mnt/scripts/host/efn-rotate-online-key /mnt/scripts/host/test-efn-rotate-online-key.sh /mnt/scripts/deploy.sh /mnt/scripts/gen-secrets.sh /mnt/scripts/check-encryption.sh
bash scripts/gen-secrets.sh            # membuat secrets/odoo_api_key_online_*_*.txt (disabled) + odoo_pii_*.txt
cd infra/compose && cp -n .env.example .env
docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile core --profile data --profile ai --profile observability --profile ops config -q
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile core --profile data --profile ai --profile observability --profile ops config -q
docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.payment.yml $(for f in docker-compose.online-*.yml; do printf -- '-f %s ' "$f"; done) --profile core --profile data --profile ai --profile observability --profile ops config --format json | jq -r '.secrets|keys[]' | sort | diff - <(awk '!/^[[:space:]]*#/ && NF {print $1}' ../../scripts/secrets.list | sort)
cd ../.. && bash scripts/check-encryption.sh   # butuh docker + jq (di CI job infra)
```
(Shellcheck juga dijalankan CI job infra. `.env` lokal yang sudah ada TIDAK diubah: dev lama masih 900/3 sampai disalin ulang.)

### Temuan untuk lead (penting)
1. **Data demo `efn_setup_online` G19 menghubungkan `RTL-GL` dan `RTL-BACHA` ke user `efn_online_rtl`** (user bersama). Dengan secret per kanal (§11.3) ini membuat rotasi satu kanal mencabut key kanal lain, dan key satu brand bisa dipakai brand lain. Skrip kini menolak (`user_shared`, fail-closed). Rekomendasi ke efn-odoo-engineer (A)/functional: user teknis khusus per kanal (`efn_online_rtl_gl`, …) — produksi wajib (runbook §1b).
2. Default compose BFF sekarang 3600: lingkungan yang Odoo kanalnya belum 70 harus set `ONLINE_PAYMENT_TIMEOUT_SECONDS=900` eksplisit di config rilis sebelum deploy G19 (pengaman BFF menolak checkout, bukan korupsi data).
3. Env promo RL ditambahkan ke compose walau RL online lain tidak terdaftar di sana (default sama dengan kontrak/BFF; deploy.sh tidak meneruskannya → ubah lewat PR).

### Yang belum / tidak terverifikasi
- Tidak satu pun perintah di atas dijalankan oleh agent ini; YAML & shell diperiksa manual (indentasi, referensi secret: setiap secret overlay didefinisikan top-level base; secret PoC didefinisikan top-level dev).
- `--all`/`--bootstrap`/`user_shared` belum diuji terhadap Odoo/BFF/systemd sungguhan. Kode HTTP json2 untuk `channel_inactive` (diduga 422) belum diverifikasi.
- PoC PII: secret tidak ada di staging/production, tidak di `deploy-validate`, tidak di key-custody (sengaja, §11.5). Nama env mengikuti ADR-036 §5; perlu dicocokkan dengan modul `efn_pii` builder B.
- Belum ada gerbang deploy otomatis yang membandingkan `payment_timeout_minutes` Odoo dengan BFF (prosedural, runbook).
- `.github/workflows/deploy.yml` tidak diubah (secret `host:*` memang tidak dikirim; `CFG_ONLINE_CHANNELS` sudah menerima kode berhifen lewat deploy-validate).

---

Tanggal: 2026-10-05. Cakupan: secrets kontrak docs/04 §2, hardening compose, observability + alerting, backup & DR (R-T08), CI, runbook. **Tidak ada yang dijalankan** (mesin penulis tanpa Docker/Git/Python); semua validasi terjadi di CI job `infra` dan `odoo-test`. Lihat bagian "Belum terverifikasi".

## 1. Tabel secret → service → env var (untuk QA)

Sumber tunggal: `scripts/secrets.list`. File host: `/opt/efn/secrets/<secret>.txt` (dev: `./secrets/<secret>.txt`), di container: `/run/secrets/<secret>`. GitHub Environment secret = nama UPPERCASE.

| Secret (`/run/secrets/…`) | Service | Env var yang menunjuk file | Nilai dev (`gen-secrets.sh`) |
|---|---|---|---|
| `pg_password` | postgres | `POSTGRES_PASSWORD_FILE` | random |
| | odoo | `PASSWORD_FILE` | |
| | api-gateway | `PG_PASSWORD_FILE` | |
| | ocr-worker | `PG_PASSWORD_FILE` (**baru**) | |
| | etl | `PG_PASSWORD_FILE` | |
| | metabase | (entrypoint wrapper → `MB_DB_PASS`) | |
| | postgres-exporter | `DATA_SOURCE_PASS_FILE` | |
| `odoo_admin_password` | odoo | `ODOO_ADMIN_PASSWORD_FILE` | random |
| `jwt_secret` | api-gateway | `JWT_SECRET_FILE` | random |
| `rabbitmq_password` | rabbitmq | `RABBITMQ_DEFAULT_PASS_FILE` | random |
| | api-gateway | `RABBITMQ_PASSWORD_FILE` | |
| | ocr-worker | `RABBITMQ_PASSWORD_FILE` | |
| `minio_root_password` | minio | `MINIO_ROOT_PASSWORD_FILE` | random |
| | minio-init | (dibaca langsung `/run/secrets/minio_root_password`) | |
| | api-gateway | `MINIO_ROOT_PASSWORD_FILE` (**baru**) | |
| | ocr-worker | `MINIO_ROOT_PASSWORD_FILE` | |
| `clickhouse_password` | clickhouse | (entrypoint wrapper → `CLICKHOUSE_PASSWORD`) | random |
| | etl | `CH_PASSWORD_FILE` | |
| `otp_pepper` (**baru**) | api-gateway | `OTP_PEPPER_FILE` | random |
| `discount_token_secret` (**baru**) | api-gateway | `DISCOUNT_TOKEN_SECRET_FILE` | random |
| `odoo_bff_api_key` (**baru**) | api-gateway | `ODOO_BFF_API_KEY_FILE` | hex 32 byte |
| | odoo | `EFN_BFF_API_KEY_FILE` | |
| `whatsapp_token` (**baru**) | api-gateway | `WHATSAPP_TOKEN_FILE` | literal `disabled` |
| `webhook_payment_secret` (**baru**) | api-gateway | `WEBHOOK_PAYMENT_SECRET_FILE` | random |
| `minio_backup_password` (**baru**, R-T08) | postgres (efn-walg) | `MINIO_BACKUP_PASSWORD_FILE` | random |
| | minio-init | (dibaca langsung) | |
| `walg_libsodium_key` (**baru**, R-T08) | postgres (efn-walg) | `WALG_LIBSODIUM_KEY_FILE` | 32 byte base64 |
| `grafana_admin_password` (**baru**; sebelumnya Grafana memakai ulang `odoo_admin_password`) | grafana | `GF_SECURITY_ADMIN_PASSWORD__FILE` | random |
| `alertmanager_webhook_url` (**baru**) | alertmanager | `url_file` di `alertmanager.yml` | literal `http://127.0.0.1:9/disabled` |

Env non-rahasia baru:

| Service | Env | Nilai |
|---|---|---|
| api-gateway | `WHATSAPP_ENABLED` | `${WHATSAPP_ENABLED:-false}` (dev override paksa `"false"`) |
| | `WHATSAPP_PHONE_NUMBER_ID` | `${WHATSAPP_PHONE_NUMBER_ID:-}` |
| | `WHATSAPP_OTP_TEMPLATE` | `${WHATSAPP_OTP_TEMPLATE:-efn_otp}` |
| | `MINIO_ENDPOINT` / `MINIO_USE_SSL` / `MINIO_ROOT_USER` | `minio:9000` / `false` / `efnadmin` |
| | `MINIO_BUCKET_MARKET_SCOUT` | `market-scout` |
| | `REDIS_URL` | `redis://redis:6379` |
| | `AMQP_URL` (host, tanpa password) | `amqp://efn@rabbitmq:5672` (`RABBITMQ_URL` dipertahankan untuk kompatibilitas) |
| | `PG_HOST` / `PG_PORT` / `PG_DB` / `PG_USER` | `postgres` / `5432` / `efn_platform` / `efn` (+ `DATABASE_URL` tetap) |
| ocr-worker | `PG_HOST` / `PG_PORT` / `PG_DB` / `PG_USER` | sama seperti di atas |
| | `METRICS_PORT` | `9100` |
| | `MINIO_BUCKET_MARKET_SCOUT` | `market-scout` |
| superapp-web | `BFF_INTERNAL_URL` | `http://api-gateway:3000` (`API_INTERNAL_URL` & `NEXT_PUBLIC_API_URL` dihapus) |
| | `COOKIE_INSECURE` | `"true"` **hanya** di `docker-compose.dev.yml` |
| postgres | `WALG_S3_PREFIX`, `WALG_S3_ENDPOINT`, `MINIO_BACKUP_USER` | `s3://pg-backup/efn`, `http://minio:9000`, `pgbackup` |

## 2. File yang diubah / dibuat

| File | Isi |
|---|---|
| `scripts/secrets.list` (baru) | Daftar tunggal 15 secret + generator dev |
| `scripts/gen-secrets.sh` | Membaca `secrets.list`; dir 0700, file 0644 (lihat temuan #1); idempoten |
| `scripts/deploy.sh` | Loop dari `secrets.list`, host override secret digenerate, `.env` + WhatsApp/WAL-G, `config -q` sebelum up, `up --no-build`, cek `minio-init` exit 0, domain target probe |
| `scripts/backup.sh` | WAL-G `backup-push` + `delete retain FULL 14` + metrik textfile; dump logis age opsional |
| `scripts/restore-test.sh` (baru) | Restore ke container+volume sementara, tunggu promote, verifikasi DB/tabel/row count, exit non-zero, metrik |
| `scripts/fetch-oca.sh` | + `report_xlsx` (OCA/reporting-engine), `date_range` (OCA/server-ux); gagal bila modul hilang |
| `infra/compose/docker-compose.yml` | 15 secret, mount per service, env baru, hardening, network `egress` & `socket-proxy`, `docker-socket-proxy`, `minio-init`, image postgres+WAL-G, profile `ops` (alertmanager, node-exporter, cadvisor, postgres-exporter, redis-exporter, blackbox-exporter), host `mart.`/`kitchen.` |
| `infra/compose/docker-compose.dev.yml` | Port dev bind `127.0.0.1`, mount `lib/`, `components/`, `middleware.ts`, `COOKIE_INSECURE=true`, command postgres dengan archive |
| `infra/compose/docker-compose.prod.yml` | `archive_command` WAL-G (sebelumnya merujuk pgBackRest yang tidak terpasang), limit tambahan; tanpa `ports` |
| `infra/compose/.env.example` | WhatsApp, WAL-G, textfile dir |
| `infra/backup/postgres/{Dockerfile,efn-walg.sh,efn-restore.sh}` (baru) | postgres:16.6-bookworm + WAL-G v3.0.9, wrapper & entrypoint restore |
| `infra/backup/systemd/efn-{backup,restore-test}.{service,timer}` (baru) | Jadwal harian 01:30 WIB & bulanan |
| `infra/minio/minio-init.sh` (baru) | Bucket `market-scout`, `pg-backup`, user `pgbackup` berkebijakan minimum |
| `infra/clickhouse/config.d/prometheus.xml` (baru) | Endpoint metrik :9363 (sebelumnya di-scrape tapi tidak aktif) |
| `infra/traefik/traefik.yml`, `infra/observability/promtail.yml` | Docker provider lewat `tcp://docker-socket-proxy:2375` |
| `infra/observability/prometheus.yml` | rules, alertmanager, scrape exporter, `ocr-worker:9100` (dns_sd), rabbitmq `/metrics/per-object`, minio, loki, probe internal & HTTPS |
| `infra/observability/{alert-rules,alertmanager,blackbox,postgres-exporter-queries}.yml`, `targets/blackbox-https.yml` (baru) | 22 alert + routing + probe + metrik archiver |
| `infra/observability/grafana/provisioning/datasources/datasources.yml` | uid tetap + datasource Alertmanager |
| `infra/observability/grafana/provisioning/dashboards/{dashboards.yml,json/efn-platform-overview.json}` (baru) | Dashboard "EFN - Platform Overview" (14 panel) |
| `.github/workflows/ci.yml` | `permissions: {}` + per job, pytest matrix (ai-ocr-worker/etl/text2sql), job `odoo-test`, secrets dummy via `gen-secrets.sh`, cek port prod, cek secret compose = `secrets.list`, promtool, amtool, shellcheck, komentar pin SHA |
| `.github/workflows/build-push.yml` | Matrix `context`, image `postgres` (WAL-G) ikut scan/SBOM/sign |
| `.github/workflows/deploy.yml` | Permission minimal (hapus `id-token`), rsync diperbaiki (temuan #2), secret lewat stdin, 9 secret baru, vars WhatsApp, cosign verify `postgres` |
| `.github/dependabot.yml` | docker `infra/backup/postgres`, pip `services/text2sql` |
| `Makefile` | profile `ops`, `validate` membuat secrets, `test-py`, `backup`, `restore-test`, shellcheck |
| `README.md` (hanya baris hosts, atas permintaan paket kerja) | + `mart.efn.local kitchen.efn.local` |
| `docs/runbooks/*.md` (baru, 11 file) | Indeks + runbook tiap alert + backup/restore/DR + rotasi secret |

## 3. Keputusan

- **WAL-G, bukan pgBackRest**: repo S3 pgBackRest hanya HTTPS; MinIO internal HTTP. WAL-G: endpoint HTTP, enkripsi libsodium, zstd, PITR. Dapat dibalik (hanya image `infra/backup/postgres`). ADR-001 menyebut pgBackRest sebagai contoh — architect perlu mencatat perubahan ini.
- **RPO 15 menit** = `archive_timeout=900` + alert `EfnWalArchiveFailing`. **RTO 4 jam** diukur oleh `efn_restore_test_duration_seconds` (timeout restore 3 jam).
- **Jadwal = systemd timer di host**, bukan GitHub Actions `schedule` + SSH: backup tidak bergantung pada GitHub/VPN runner, tidak membuka SSH ke runner publik.
- **docker-socket-proxy**: Traefik & Promtail tidak lagi me-mount `docker.sock` langsung.
- **Network `egress`** (non-internal) untuk api-gateway (WhatsApp Cloud API), alertmanager, blackbox, ollama (pull model), uptime-kuma. Sebelumnya api-gateway hanya di network `internal: true` → WhatsApp tidak akan pernah terjangkau.

## 4. Temuan bug pada scaffold sebelumnya (sudah diperbaiki)

1. Secret ditulis mode 0600 oleh `deploy.sh`/`gen-secrets.sh`; compose non-swarm me-bind-mount file apa adanya → user non-root (odoo, app, worker) tidak bisa membaca. Sekarang dir 0700 + file 0644.
2. `deploy.yml`: `rsync --delete infra/ scripts/ host:/opt/efn/` akan **menghapus `/opt/efn/secrets`** dan menaruh skrip di `/opt/efn/` (bukan `/opt/efn/scripts/` yang dipanggil). Diperbaiki dengan dua rsync + exclude.
3. Secret dikirim sebagai argumen `ssh` (terlihat di `ps` host). Sekarang lewat stdin `bash -s`.
4. `archive_command=pgbackrest …` di prod padahal image postgres alpine tidak memuat pgbackrest → archive selalu gagal.
5. Grafana memakai `odoo_admin_password` sebagai password admin (berbagi secret lintas sistem).
6. ClickHouse di-scrape di :9363 tanpa konfigurasi endpoint Prometheus.

## 5. Item untuk agent lain / QA

- **BFF (efn-bff-engineer)**:
  - Ekspos metrik `/metrics` (prom-client): `efn_otp_request_total{purpose,result}`, `efn_otp_verify_total{purpose,result}`, `efn_whatsapp_send_total{result}` — alert OTP sudah ditulis dan bertanda `metric_status: proposed`. Jangan ada nomor HP di label.
  - **Percayai `X-Forwarded-For` hanya dari jaringan internal** (Traefik di network `efn_internal`); Fastify `trustProxy` harus dibatasi ke subnet internal/Traefik, bukan `true`, agar rate limit per IP tidak bisa dipalsukan. QA: uji kirim header `X-Forwarded-For` palsu langsung ke container.
  - Baca semua secret via `*_FILE` sesuai tabel §1; `AMQP_URL` tidak berisi password.
  - Usulan: dukung 2 key (current+previous) untuk `odoo_bff_api_key` dan `webhook_payment_secret` agar rotasi tanpa jeda.
  - Konfirmasi apakah `otp_pepper` juga dipakai untuk `member_hash` (docs/04 §1); bila ya, pisahkan sebelum rotasi pertama.
- **Data/AI (efn-data-ai-engineer)**: ocr-worker wajib mengekspos `/metrics` di `:9100` (healthcheck compose & scrape bergantung padanya); worker dapat `PG_*` + `PG_PASSWORD_FILE`. ETL: usulan metrik `efn_etl_last_success_timestamp_seconds`. Test di `services/<svc>/tests` (pytest), dependensi test di `requirements-dev.txt` (opsional).
- **Odoo (efn-odoo-engineer)**: baca `EFN_BFF_API_KEY_FILE`; test diberi tag `efn` (`@tagged('efn', 'post_install', '-at_install')`); CI menginstal `efn_core,efn_pos_otp_gate,efn_kitting` (yang ada) di DB `efn_test`.
- **Frontend**: CSP dikirim Next.js; middleware Traefik `secure-headers@file` tidak menyetel CSP (hanya HSTS, frame deny, nosniff, referrer, permissions-policy) → tidak konflik. `NEXT_PUBLIC_API_URL` dihapus dari compose runtime; `next.config.mjs` masih merujuknya di CSP `connect-src` (nilai kosong → aman, tetapi bersihkan).
- **Architect**: perbarui docs/03 §3.2 (daftar GitHub Environment secrets + vars `WHATSAPP_ENABLED`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_OTP_TEMPLATE`, `COMPOSE_PROFILES` default kini memuat `ops`) dan catat WAL-G di ADR terkait R-T08.
- **QA**: cocokkan tabel §1 dengan `docker compose config`; CI job `infra` otomatis gagal bila secret compose ≠ `secrets.list` atau ada port publish selain Traefik di prod.

## 6. Belum terverifikasi / risiko

- Tidak ada `docker compose config`, promtool, amtool, shellcheck, hadolint, yamllint yang dijalankan lokal — pertama kali dijalankan di CI.
- Tag image belum dicek keberadaannya: `minio/mc:RELEASE.2024-11-17T19-35-25Z`, `tecnativa/docker-socket-proxy:0.3.0`, `oliver006/redis_exporter:v1.66.0-alpine`, `gcr.io/cadvisor/cadvisor:v0.49.1`, `quay.io/prometheuscommunity/postgres-exporter:v0.16.0`, `prom/blackbox-exporter:v0.25.0`, `prom/alertmanager:v0.27.0`, `prom/node-exporter:v1.8.2`. Nama aset WAL-G (`wal-g-pg-22.04-amd64` + `.sha256`, v3.0.9) dicek di halaman rilis; `WALG_SHA256` belum di-pin (verifikasi hanya terhadap file .sha256 rilis).
- Asumsi perilaku image: entrypoint postgres resmi berjalan sebagai `USER postgres`; `RABBITMQ_DEFAULT_PASS_FILE` masih didukung image 3.13; plugin `rabbitmq_prometheus` aktif bawaan; `WALG_LIBSODIUM_KEY_PATH` + `TRANSFORM=base64`; Alertmanager `url_file` (≥ 0.26); amtool tidak memeriksa keberadaan `url_file`; Loki 3.2 tanpa shell (healthcheck dinonaktifkan).
- `cap_add` untuk redis/rabbitmq ditebak dari perilaku entrypoint; bila container gagal start, longgarkan per service dan catat.
- Pindah ke postgres **bookworm** mengubah uid (70→999) dan collation (musl→glibc): volume `pg-data` lama harus di-dump/restore (runbook backup-restore §5). Dev: `make clean`.
- Trivy image gate bisa memblokir image `postgres` bila binary WAL-G membawa CVE stdlib Go HIGH; perlu bump/justifikasi.
- Repo backup MinIO berada di host yang sama → **belum DR**; off-site wajib sebelum go-live.
- Job `odoo-test` bergantung pada pola log `ERROR/CRITICAL` dan exit code Odoo; `--test-tags efn` hanya menjalankan test bertag `efn`.
- node-exporter berjalan di network compose (bukan host network) → metrik jaringan host tidak akurat; disk/CPU/memori akurat via `--path.rootfs`.

---

# Gelombang 3 — Enkripsi di setiap transaksi data (2026-10-05)

Instruksi user: **enkripsi di setiap transaksi data**, tanpa pengecualian diam-diam. Kontrak: docs/04 §7 (v2), ADR-022. **Tidak ada yang dijalankan** (tanpa Docker/Git/openssl lokal); validasi pertama di CI job `infra`. Bagian §1 di atas (tabel secret gelombang 1) **usang** — tabel G3.1 di bawah yang berlaku.

## G3.1 Tabel secret → service → env (final)

File host `/opt/efn/secrets/<secret>.txt` (dev `./secrets/`), di container `/run/secrets/<secret>`; GitHub Environment = UPPERCASE. Generator dev di `scripts/secrets.list`; jenis `pki` oleh `scripts/gen-pki.sh`.

| Secret | Service | Env / cara pakai |
|---|---|---|
| `pg_password` | postgres | `POSTGRES_PASSWORD_FILE` (superuser `postgres`, hanya socket lokal) |
| `pg_password_odoo` | postgres (00-roles.sh), odoo | odoo `PASSWORD_FILE`, `USER=efn_odoo` |
| `pg_password_bff` | postgres, api-gateway | `PG_PASSWORD_FILE`, `PG_USER=efn_bff` |
| `pg_password_ocr` | postgres, ocr-worker | `PG_PASSWORD_FILE`, `PG_USER=efn_ocr` |
| `pg_password_etl` | postgres, etl | `PG_PASSWORD_FILE` + `ODOO_PG_PASSWORD_FILE`, user `efn_etl` |
| `pg_password_metabase` | postgres, metabase | wrapper → `MB_DB_PASS`, `MB_DB_USER=efn_metabase` |
| `pg_password_exporter` | postgres, postgres-exporter | `DATA_SOURCE_PASS_FILE`, `DATA_SOURCE_USER=efn_exporter` |
| `odoo_admin_password` | odoo | `ODOO_ADMIN_PASSWORD_FILE` (48 karakter) |
| `odoo_bff_api_key` | odoo, api-gateway | `EFN_BFF_API_KEY_FILE` / `ODOO_BFF_API_KEY_FILE` |
| `rabbitmq_password` | rabbitmq | user admin `efn-admin` (definitions, ops saja) |
| `rabbitmq_password_bff` | rabbitmq, api-gateway | `RABBITMQ_PASSWORD_FILE`, `AMQP_URL=amqps://efn-bff@rabbitmq:5671` |
| `rabbitmq_password_ocr` | rabbitmq, ocr-worker | `RABBITMQ_PASSWORD_FILE`, `AMQP_URL=amqps://efn-ocr@rabbitmq:5671` |
| `redis_password_bff` | redis (ACL), api-gateway | `REDIS_PASSWORD_FILE`, `REDIS_USERNAME=efn-bff` |
| `redis_password_exporter` | redis (ACL + healthcheck), redis-exporter | wrapper → `REDIS_PASSWORD`, `REDIS_USER=efn-exporter` |
| `minio_root_password` | minio, minio-init | `MINIO_ROOT_PASSWORD_FILE` (root hanya bootstrap) |
| `minio_backup_password` | postgres (efn-walg), minio-init | `MINIO_BACKUP_PASSWORD_FILE` (user `pgbackup`) |
| `minio_bff_secret` | minio-init, api-gateway | `MINIO_SECRET_KEY_FILE`, `MINIO_ACCESS_KEY=efn-bff` |
| `minio_ocr_secret` | minio-init, ocr-worker | `MINIO_SECRET_KEY_FILE`, `MINIO_ACCESS_KEY=efn-ocr` |
| `minio_kms_key` | minio | `MINIO_KMS_SECRET_KEY_FILE` (format `efn-sse-key:<b64 32 byte>`, SSE-S3) |
| `clickhouse_password` | clickhouse, clickhouse-init | wrapper → `CLICKHOUSE_PASSWORD` (admin `efn`) |
| `clickhouse_password_etl` | clickhouse-init, etl | `CH_PASSWORD_FILE`, `CH_USER=efn_etl` |
| `clickhouse_password_text2sql` | clickhouse-init | (user `efn_text2sql`; service text2sql belum ada) |
| `clickhouse_password_metabase` | clickhouse-init, metabase | di-mount untuk ops; diisi di UI koneksi Metabase→ClickHouse |
| `clickhouse_disk_key` | clickhouse | wrapper → `CLICKHOUSE_DISK_KEY_HEX` (encrypted-storage.xml data-ai) |
| `jwt_secret`, `otp_pepper`, `discount_token_secret`, `webhook_payment_secret`, `whatsapp_token` | api-gateway | `*_FILE` masing-masing (**`otp_pepper` dicabut dari etl**, SEC-16) |
| `pii_encryption_key` | api-gateway saja | `PII_ENCRYPTION_KEY_FILE`, `PII_ACTIVE_KEY_VERSION=1` |
| `walg_libsodium_key` | postgres (efn-walg), restore-test | `WALG_LIBSODIUM_KEY_FILE` |
| `grafana_admin_password` | grafana | `GF_SECURITY_ADMIN_PASSWORD__FILE` |
| `alertmanager_webhook_url` | alertmanager | `url_file` (deploy.sh menolak selain `https://`) |
| `efn_ca_crt` | semua klien TLS | `PG_SSLROOTCERT`, `PGSSLROOTCERT`, `EFN_CA_FILE`, `REDIS_TLS_CA_FILE`, `AMQP_TLS_CA_FILE`, `MINIO_CA_FILE`, `CH_CA_FILE`, `OLLAMA_CA_FILE`, `NODE_EXTRA_CA_CERTS`, `HEALTHCHECK_CA_FILE`, `METRICS_TLS_CA_FILE`, `WALG_S3_CA_CERT_FILE`, `SSL_CERT_FILE` (minio), file config (Traefik rootCAs, Prometheus/blackbox/promtail `ca_file`, Grafana `$__file`, ghostunnel `--cacert`) |
| `tls_postgres_crt/key` | postgres | `ssl_cert_file`; kunci disalin 0600 ke `/var/run/postgresql/efn-tls/server.key` |
| `tls_redis_*` | redis | `--tls-cert-file/--tls-key-file` |
| `tls_rabbitmq_*` | rabbitmq | `ssl_options.*`, `management.ssl.*`, `prometheus.ssl.*` |
| `tls_minio_*` | minio | disalin ke `/tmp/certs/{public.crt,private.key}` |
| `tls_clickhouse_*` | clickhouse | `config.d/tls.xml` openSSL.server |
| `tls_api_gateway_*` | api-gateway | `TLS_CERT_FILE`, `TLS_KEY_FILE` |
| `tls_odoo_*` | odoo-tls, odoo-ws-tls (ghostunnel) | `--cert/--key` |
| `tls_superapp_web_*`, `tls_metabase_*`, `tls_ollama_*`, `tls_cadvisor_*`, `tls_docker_socket_proxy_*` | sidecar ghostunnel masing-masing (cadvisor juga sertifikat klien mTLS ke docker-socket-proxy) | `--cert/--key` |
| `tls_traefik_*` | traefik | `tls.certificates` (metrics :8082) + klien mTLS `providers.docker.tls` |
| `tls_promtail_*` | promtail | klien mTLS docker_sd |
| `tls_prometheus_*`, `tls_alertmanager_*`, `tls_node_exporter_*`, `tls_postgres_exporter_*`, `tls_blackbox_exporter_*` | masing-masing | di-mount sebagai `tls_server_crt/key` → `web-config.yml` |
| `tls_redis_exporter_*` | redis-exporter | `REDIS_EXPORTER_TLS_SERVER_CERT_FILE/KEY_FILE` |
| `tls_grafana_*` | grafana | `GF_SERVER_CERT_FILE/KEY` |
| `tls_loki_*` | loki | `server.http_tls_config` |
| `tls_uptime_kuma_*` | uptime-kuma | `UPTIME_KUMA_SSL_CERT/KEY` |
| `tls_ocr_worker_*` | ocr-worker | `METRICS_TLS_CERT_FILE/KEY_FILE` |

Total 82 secret (33 + 49 PKI) — di bawah batas 100 secret per GitHub Environment (ditambah `DEPLOY_*`). `deploy.yml` kini membaca nilai lewat `toJSON(secrets)` sesuai `secrets.list` (tidak perlu edit workflow saat menambah secret).

## G3.2 Hop TLS yang diterapkan (docs/04 §7.1)

| Hop | Server | Klien / verifikasi |
|---|---|---|
| * → PostgreSQL 5432 | `ssl=on`, TLSv1.2+, `hba_file` = `infra/postgres/pg_hba.conf` (hanya `hostssl` + scram per DB/role; `hostnossl` & superuser TCP ditolak) | verify-full: BFF, ocr, etl (platform+Odoo), Odoo (render_odoo_conf), Metabase (JDBC), postgres-exporter |
| BFF/exporter → Redis 6379 | `port 0`, `tls-port 6379`, ACL (`default` off, `efn-bff` prefix `otp:* dtok:* rl:* ms:*`, `efn-exporter`), `noeviction` (SEC-05), network `cache` khusus | `rediss://` + CA |
| → RabbitMQ | amqps 5671 saja, mgmt https 15671, metrik https 15691, distribusi Erlang/epmd loopback | `amqps://` + CA |
| → MinIO 9000 | HTTPS (`--certs-dir`), SSE-S3 | BFF/ocr (`MINIO_USE_SSL=true` + CA), WAL-G (`WALG_S3_CA_CERT_FILE`), mc (`certs/CAs`) |
| → ClickHouse | https 8443 + native TLS 9440; 8123/9000/9004/9005/9009 dihapus; metrik via http_handlers di 8443 | ETL (HTTPS+CA), clickhouse-init (native TLS strict) |
| Traefik → upstream | semua `scheme=https` + `serversTransport` per service (`rootCAs` + `serverName`) | dicek CI (`check-encryption.sh`) |
| → Odoo / Next.js / Metabase / Ollama / cAdvisor / docker-socket-proxy | ghostunnel `v1.11.3-alpine` (`network_mode: service:<app>`), app listen 127.0.0.1 | Traefik, Prometheus, ocr-worker (Ollama), Traefik/Promtail/cAdvisor (mTLS CN allowlist ke socket proxy) |
| Next.js → BFF, Odoo → BFF | BFF HTTPS native :3000 | `NODE_EXTRA_CA_CERTS` / `EFN_CA_FILE` |
| Prometheus → semua target | exporter-toolkit `web-config.yml`, redis_exporter TLS flags, rabbitmq 15691, traefik metrics router TLS | `scheme: https` + `ca_file` (+ `server_name` untuk dns_sd) |
| Promtail → Loki, Grafana → Prometheus/Loki/Alertmanager, Prometheus → Alertmanager | TLS | CA internal |
| Antar-node / outlet | WireGuard (lapisan kedua) | — |

## G3.3 Pengecualian enkripsi yang tersisa (untuk architect → ADR-022)

| # | Pengecualian | Alasan | Kontrol kompensasi |
|---|---|---|---|
| E1 | Plaintext **loopback** di dalam network namespace app ber-sidecar (Odoo :8069/:8072, Next.js :3001, Metabase :3000, Ollama :11435, cAdvisor :8080, docker-socket-proxy :2375) | App tanpa TLS native; sesuai §7.1 (sidecar) | Bind 127.0.0.1 (tidak terjangkau container lain); ghostunnel TLS1.2+ |
| E2 | Healthcheck Docker HTTP loopback (odoo, superapp-web, metabase, cadvisor, docker-socket-proxy, status ghostunnel `http://127.0.0.1:606x`) | Probe liveness di namespace sendiri, bukan transaksi data | Loopback saja; lint mengizinkan hanya 127.0.0.1/localhost |
| E3 | Healthcheck ClickHouse `wget --no-check-certificate https://127.0.0.1:8443/ping` | busybox wget tanpa opsi CA | Loopback; verifikasi penuh oleh clickhouse-init + `probe-internal-tls` |
| E4 | PostgreSQL socket unix `local all all trust` di container postgres | Entrypoint init, WAL-G backup-push, ops `docker exec` | Bukan TCP; socket tidak dibagikan; superuser ditolak via TCP |
| E5 | Distribusi Erlang RabbitMQ (25672) + epmd plaintext | CLI `rabbitmqctl/diagnostics` | Bind 127.0.0.1 (`inet_dist_use_interface`, `ERL_EPMD_ADDRESS`) |
| E6 | Loki gRPC/memberlist, Promtail HTTP/gRPC | Komponen internal single binary | Bind 127.0.0.1 |
| E7 | CI job `odoo-test`: PostgreSQL service tanpa TLS | DB efemeral CI, data sintetis | Hanya di runner CI, tidak ada data nyata |
| E8 | Dev: sertifikat publik = default self-signed Traefik | Laptop tanpa domain publik | Dev only; hop internal dev tetap TLS PKI |
| E9 | Cloudflare edge → origin | Wajib mode **Full (strict)** di Cloudflare (sertifikat LE origin diverifikasi) | Dicatat untuk ops; tidak bisa dicek dari repo |
| E10 | Healthcheck Docker dinonaktifkan untuk prometheus, alertmanager, grafana, loki, exporter | Endpoint hanya TLS, image tanpa klien ber-CA (bukan pengecualian enkripsi, tetapi trade-off observability) | `up`/`probe-internal-tls` + Uptime Kuma |

Bukan pengecualian tetapi sisa pekerjaan (ADR-022/023, belum dalam jangkauan gelombang ini):
- **Rantai PKI**: root offline → intermediate per lingkungan → leaf 90 hari, `efn_ca_crt` = bundle root+intermediate. Saat ini satu tingkat (CA → leaf 365 hari); `gen-pki.sh` + runbook rotasi CA (bundle transisi) sudah mendukung bundle.
- **ADR-023 entrypoint `wg-https` + ACME DNS-01** untuk `erp.`/`ops.`/`bi.`/`status.`: belum; saat ini host internal memakai router `websecure` + `internal-only` + sertifikat HTTP-01 (butuh port 80 publik). Butuh secret token DNS Cloudflare.
- `superapp-web` prod `replicas: 1` (sidecar tidak bisa menempel ke >1 replika). Opsi: TLS native di Next.js custom server (frontend) atau pasangan service ganda.

## G3.4 Status SEC yang ditangani

| ID | Status | Implementasi |
|---|---|---|
| SEC-01 | **Selesai (infra)** | Router `odoo`, `odoo-ws`, `odoo-login` = `internal-only` + `secure-headers`; `odoo-login` 10/menit/peer; `odoo-rpc-block` (`/xmlrpc`, `/jsonrpc`) → `deny-all` 403; `internal-only` tanpa `ipStrategy` (RemoteAddr, benar untuk WireGuard langsung); template WG + `gen-peer.sh` + runbook onboarding. 2FA = efn-odoo-functional |
| SEC-02 | **Selesai (infra)** | Role PG per service (`00-roles.sh`, password dari secret, dijalankan juga tiap deploy untuk rotasi), user MinIO `efn-bff`/`efn-ocr` + policy, user RabbitMQ per service tanpa tag administrator, ACL Redis, user ClickHouse per service; superuser/root hanya bootstrap |
| SEC-03 | **Selesai (infra)**, butuh verifikasi staging | `forwardedHeaders.trustedIPs` = Cloudflare (dari `cloudflare-ips.txt`, CI cek sinkron) + 10.0.0.0/8; `TRUSTED_PROXIES` = `172.30.1.0/24` (subnet `internal` di-pin) + Cloudflare (deploy.sh); runbook firewall origin |
| SEC-04 (lapisan 2) | **Selesai (infra)** | `clickhouse-init` menerapkan `access/*.sql` + mengikat `efn_text2sql` ke `text2sql_reader` setiap up/deploy; deploy gagal bila init gagal |
| SEC-05 | **Selesai** | ACL, TLS, `noeviction`, network `cache` |
| SEC-09 | **Selesai** | cAdvisor tanpa docker.sock/rootfs: docker-socket-proxy via mTLS, `cgroup: host`, mount `/sys/fs/cgroup` + `/var/lib/docker/image` ro, `cap_drop: ALL`, listen loopback + sidecar TLS |
| SEC-10 | **Selesai** | Semua `uses:` dipin SHA + komentar versi (ci, build-push, deploy, security, codeql, dast); `webfactory/ssh-agent` dihapus; identitas cosign = `build-push.yml@refs/heads/main` (staging) atau `@refs/tags/vX.Y.Z` (prod; prod menolak image non-rilis); deploy by digest (`IMAGE_<SVC>=…@sha256:`) hasil `cosign verify --output json`; input lewat `env` + regex. **trivy-action 0.29.0 dipindah ke 0.35.0** (tag lama di-force-push pada insiden CVE-2026-33634, Maret 2026) + binary Trivy `v0.69.3` |
| SEC-16 | **Selesai (compose)** | `otp_pepper` dicabut dari etl |
| SEC-18 | **Selesai** | `log_parameter_max_length=0`, `log_parameter_max_length_on_error=0` di base/dev/prod |
| SEC-11 (sebagian) | Lifecycle MinIO `market-scout/reports/` 365 hari; runbook breach 72 jam | DSAR/consent = BFF |

## G3.5 File utama gelombang 3

Baru: `scripts/gen-pki.sh`, `scripts/check-encryption.sh`, `scripts/update-cloudflare-ips.sh`, `infra/postgres/init/00-roles.sh`, `infra/postgres/pg_hba.conf`, `infra/redis/redis-entrypoint.sh`, `infra/rabbitmq/{rabbitmq.conf,rabbitmq-entrypoint.sh}`, `infra/clickhouse/{clickhouse-init.sh,client-config.xml}`, `infra/traefik/{cloudflare-ips.txt,dynamic/internal-tls.yml}`, `infra/observability/web-config.yml`, `infra/wireguard/{wg0-server.conf.example,peer-outlet.conf.example,gen-peer.sh}`, runbook `internal-pki.md`, `pii-key-rotation.md`, `wireguard-outlet.md`, `origin-firewall.md`, `luks-disk.md`, `breach-response-72h.md`.
Diubah: `infra/compose/docker-compose{,.dev,.prod}.yml`, `.env.example`, `scripts/{secrets.list,gen-secrets.sh,deploy.sh,restore-test.sh}`, `infra/minio/minio-init.sh`, `infra/clickhouse/config.d/{tls.xml,prometheus.xml}` (tls.xml versi data-ai ditimpa — pemilik devsecops), `infra/traefik/{traefik.yml,dynamic/middlewares.yml}`, `infra/observability/{prometheus,blackbox,loki,promtail,alert-rules,alertmanager}.yml`, Grafana datasources, `infra/backup/postgres/{efn-walg.sh,Dockerfile}` (komentar), semua workflow, `Makefile` (`pki`, `pki-rotate`, `lint-enc`), README (bagian menjalankan), runbook README/secret-rotation/rabbitmq-*/backup-failed.
`infra/clickhouse/config.d/zz-no-plaintext-native.xml` (data-ai) **tidak di-mount**: tidak diperlukan karena `tls.xml` menghapus port 9000 dan init berjalan lewat `clickhouse-init` (TLS 9440), bukan initdb.d bawaan.

## G3.6 Koordinasi

- **BFF**: Redis prefix yang diizinkan ACL `otp:* dtok:* rl:* ms:*` + `INFO`, `CLIENT SETNAME/GETNAME/ID`; prefix baru = ubah `infra/redis/redis-entrypoint.sh`. `00-roles.sh` tidak menulis grant tabel; `ALTER DATABASE metabase OWNER TO efn_metabase` idempoten dengan 01-*.sql. Key ring PII v2: runbook `pii-key-rotation.md` mengasumsikan `PII_ENCRYPTION_KEY_V<n>_FILE`.
- **data-AI**: RabbitMQ `efn-ocr` diberi configure+read pada 3 queue + 3 DLQ (worker men-declare non-pasif; deklarasi ber-DLX butuh read). Write ke default exchange (`amq.default`) **tidak bisa dibatasi per routing key** — kontrol nyata = properti AMQP `user_id` (divalidasi broker) + validasi semantik BFF (SEC-08). Pengetatan: worker passive-declare queue tujuan → read worker cukup `ocr.requested(.dlq)`. ClickHouse metrik pindah ke `https://clickhouse:8443/metrics` (handler `prometheus`). `clickhouse-client` config di-mount sebagai `/etc/clickhouse-client/config.xml` (menggantikan default image, bukan `config.d/`).
- **Odoo**: env sesuai permintaan (`USER=efn_odoo`, `PASSWORD_FILE`, `PGSSLROOTCERT`, `EFN_CA_FILE`, `EFN_BFF_URL=https://api-gateway:3000`, `ODOO_HTTP_INTERFACE=127.0.0.1`, `APP_ENV`); `efn_odoo` boleh connect DB `postgres` (pg_hba). CI build Odoo memakai `DOCKER_BUILDKIT=1`.
- **Frontend**: `HOSTNAME=127.0.0.1`, `PORT=3001`, `BFF_INTERNAL_URL=https://api-gateway:3000`, `NODE_EXTRA_CA_CERTS`; dev memakai PKI (`make secrets`), tanpa `EFN_ALLOW_PLAINTEXT`.
- **Architect**: catat G3.3 di ADR-022; WAL-G kini ke MinIO HTTPS (alasan awal "MinIO HTTP" di ADR-001 tidak berlaku lagi, WAL-G tetap dipilih); subnet Docker di-pin `172.30.0.0/16` (/24 per network).

## G3.7 Belum terverifikasi / risiko CI (cek pertama saat push)

1. **`docker compose config`**: secret long syntax `target: tls_server_crt` (non-swarm), `cgroup: host`, `network_mode: service:` + `depends_on`, `healthcheck.disable`, entrypoint `$$@` di postgres.
2. **`check-encryption.sh`**: regex bash bisa false positive/negative pada nilai compose ter-render; parsing `jq` label Traefik. Bila gagal, lihat pesan `::error::` per service.
3. **promtool**: `check config` memeriksa keberadaan file TLS (CI me-mount CA & sertifikat dummy); `promtool check web-config` (asumsi tersedia di 2.55).
4. **shellcheck** skrip baru (`gen-pki.sh`, `00-roles.sh` dengan `return` top-level untuk mode source, skrip POSIX sh redis/rabbitmq/clickhouse/minio).
5. **yamllint**: template Go di `middlewares.yml` (string ber-kutip), baris panjang (warning).
6. Runtime (PLAUSIBLE, belum diuji): ClickHouse `http_handlers` tipe `prometheus` (bila ditolak → server gagal start; fallback: metrik 9363 loopback + ghostunnel); `BIND_CONFIG` di `tecnativa/docker-socket-proxy:0.3.0` (bila tidak didukung, haproxy tetap :2375 di network `socket-proxy` — hanya Traefik/Promtail/cAdvisor); `rabbitmq.conf` tanpa listener TCP mgmt/prometheus default saat hanya `*.ssl.*` diset; definitions RabbitMQ dengan `password_hash` buatan openssl; cAdvisor flag `--docker-tls*` dan operasi tanpa `/rootfs`; Traefik template `env` di file provider; `MINIO_KMS_SECRET_KEY_FILE` pada volume lama; Loki `memberlist.bind_addr` loopback; Grafana `$__file{}` di `secureJsonData`; clickhouse-client `<secure>/<port>` di config.
7. Pin SHA diambil dari GitHub API (`git/matching-refs`, tag annotated di-dereference: codeql-action v3.38.2, sbom-action v0.24.3, zaproxy v0.14.0). Versi yang dipilih = minor terbaru di mayor yang sama; Dependabot akan mengusulkan mayor baru.
8. `semgrep/semgrep:latest` (container, bukan `uses:`) belum dipin digest — TODO.
9. Migrasi volume lama (PG superuser `efn`, RabbitMQ nodename, MinIO KMS, ClickHouse disk terenkripsi, object lock `pg-backup`) — langkah di `docs/runbooks/internal-pki.md`. Dev: `make clean`.

---

# Gelombang 5 — mTLS, PKI 3 tingkat, wg-https, kustodi kunci Superadmin (2026-10-06)

Kontrak: docs/04 §8 (v3: §8.1 ALTCHA, §8.3 payment, §8.4 privasi/retensi, §8.5 mTLS, §8.6 kustodi). Keputusan user: kunci hanya Superadmin; mTLS sesuai rekomendasi (hop tanpa kredensial aplikasi); payment gateway belum ditentukan; outlet memakai opsi open source. **Tidak ada yang dijalankan** (tanpa Docker/Git/openssl lokal) — validasi pertama di CI job `infra`.

## G5.1 File utama

| File | Perubahan |
|---|---|
| `scripts/gen-pki.sh` | **Ditulis ulang**: subperintah `root` (offline, passphrase, menolak di server), `intermediate <env>` (pathlen:0, **nameConstraints** = nama service + `efn-<svc>-1` + `.efn.internal` + localhost/127.0.0.1), `leaf` (90 hari, renew < 30 hari, profil EKU `server`/`both`/`client`, verifikasi rantai+nameConstraints, daftar `.pki-renewed`). Dev/CI: root+intermediate DEV otomatis |
| `scripts/derive-secrets.sh` (baru) | `member_hash_pepper` = HKDF-SHA256(otp_pepper, salt "", info `efn:member_hash:v1`) hex 64 + **self-test vector** `test-otp-pepper-v1` → `46df3d2fee0e91a91083827a5f303586e9c6b5c54afe096896bac9feb724061f`; `minio_prometheus_token` (JWT HS512). python3 (stdin, tanpa argv) atau fallback `openssl kdf` (argv → hidepid) — keduanya diuji di CI |
| `scripts/deploy.sh` | Root wajib; `/opt/efn/secrets` root:efn-superadmin 0750, file 0444 (lihat G5.5); kunci intermediate hanya di `/dev/shm` lalu `shred`; leaf diterbitkan di host + **recreate otomatis** service pemakai (+ sidecar); metrik expiry bundle (intermediate/root) + leaf; validasi `PAYMENT_PROVIDER` (prod tolak mock), `EFN_PUBLIC_BIND`/`EFN_WG_BIND` (IP WG harus ada), `ACME_EMAIL` (SEC-30), token DNS bukan placeholder; `docker-compose.payment.yml` bila provider ≠ mock; grant ETL Odoo (`/opt/efn/etl-sql/odoo-readonly-grants.sql`, sebagai `efn_odoo` via socket, ON_ERROR_STOP, dilewati bila `pos_order` belum ada); `PKI_SOURCE=host` dihapus |
| `scripts/secrets.list`, `gen-secrets.sh` | Jenis baru `derived:*`; 7 secret baru; penanda `[SA]` = kustodi Superadmin |
| `scripts/check-encryption.sh` | Tolak ghostunnel `--disable-authentication`, wajib `--cacert` + `--allow-cn`; web-config/Loki wajib `RequireAndVerifyClientCert` |
| `infra/compose/docker-compose.yml` | Semua sidecar mTLS; Traefik port publik di `EFN_PUBLIC_BIND`, `wg-https` di `EFN_WG_BIND:443→8443`; router erp./bi./ops./status. hanya `wg-https` + `letsencrypt-dns`; blok `/json/2` (SEC-22); router `api-webhook-payments` (POST, rate limit); BFF tanpa `NODE_EXTRA_CA_CERTS` (SEC-23), `ALTCHA_HMAC_KEY_FILE`, `PAYMENT_PROVIDER`; Odoo `EFN_MEMBER_HASH_PEPPER_FILE`; ETL AMQP `efn-etl`; ocr-worker `OLLAMA_CLIENT_CERT/KEY_FILE`, `METRICS_TLS_CLIENT_CA_FILE`; MinIO metrik `jwt`; lifecycle foto 90 hari; `CH_MART_RETENTION_YEARS` ke clickhouse-init |
| `infra/compose/docker-compose.payment.yml` (baru), `.prod.yml`, `.dev.yml`, `.env.example` | Mount kunci payment hanya bila provider ≠ mock; prod `PAYMENT_PROVIDER:?` wajib; dev `mock`, wg-https di `127.0.0.1:9443` |
| `infra/traefik/traefik.yml`, `dynamic/middlewares.yml`, `dynamic/internal-tls.yml` | Entrypoint `wg-https`, resolver `letsencrypt-dns` (DNS-01, provider via deploy); XFF hanya Cloudflare (10/8 dihapus, SEC-21); `internal-only` = outlet+staf, `internal-ops` = staf saja; `ratelimit-webhook`; serversTransports membawa sertifikat klien `traefik`; metrics :8082 mTLS (`efn-mtls`) |
| `infra/observability/*` | `prometheus.yml` (cert klien semua job, bearer MinIO, relabel QA U8), `web-config.yml` (exporter: SAN prometheus), `web-config-ui.yml` (baru: prometheus+grafana), Loki/Promtail/Grafana/blackbox mTLS, alert expiry internal < 21 hari, probe `erp.` publik dihapus |
| `infra/rabbitmq/*` | Queue + DLQ dideklarasikan broker (definitions); user `efn-etl` (write `amq.default`, configure/read kosong); write BFF/OCR = `amq.default` saja; `RABBITMQ_STRICT_PERMISSIONS=true` mencabut configure setelah klien passive-declare; metrik 15691 `verify_peer` |
| `infra/postgres/init/00-roles.sh`, `pg_hba.conf` | SEC-26: password lewat stdin (`\set pw`), bukan argv; pg_hba ADDRESS = `172.30.2.0/24` |
| `infra/minio/minio-init.sh` | Lifecycle `reports/` default **90 hari** (env); user `efn-prometheus` (`admin:Prometheus`, non-fatal) |
| `infra/wireguard/*` | Hub `10.88.0.1` (outlet 10.88/16, staf 10.89/16), `efn-wg.nft` (SEC-28), `gen-peer.sh --pubkey` (kunci di perangkat), contoh klien POS Windows/Android/Linux + laptop staf, **OpenWrt** `network.example` + `firewall.example` (VLAN POS vs Wi-Fi tamu) |
| `infra/backup/systemd/efn-restore-test.service` | User root (dir secret 0750) |
| `.github/workflows/deploy.yml`, `ci.yml`, `Makefile` | `sudo -n bash -s`; secret `EFN_PKI_*`; skip `pki`/`derived`; vars baru; rsync `services/etl/sql`; CI: bundle 2 cert, leaf ≤ 90 hari, EKU, **nameConstraints menolak leaf palsu**, test vector HKDF (python & openssl), promtool 2 web-config; `make pki-root`, `make pki-intermediate ENV=` |
| Runbook | **baru** `key-custody.md`; ditulis ulang `internal-pki.md`, `wireguard-outlet.md`; `origin-firewall.md`, `README.md` (runbooks), README (bagian menjalankan) |

## G5.2 Tabel secret → service → env (final, menggantikan G3.1)

File host `/opt/efn/secrets/<secret>.txt` (root:efn-superadmin, 0444 dalam dir 0750), container `/run/secrets/<secret>`. Sumber: **GH** = GitHub Environment (UPPERCASE), **HOST-PKI** = diterbitkan deploy.sh dari intermediate, **DERIVED** = derive-secrets.sh. `[SA]` = kustodi Superadmin.

| Secret | Sumber | Service → env |
|---|---|---|
| `pg_password` | GH | postgres `POSTGRES_PASSWORD_FILE` (superuser, socket saja) |
| `pg_password_{odoo,bff,ocr,etl,metabase,exporter}` | GH | postgres (00-roles.sh) + odoo `PASSWORD_FILE` / api-gateway, ocr-worker, etl `PG_PASSWORD_FILE` (+etl `ODOO_PG_PASSWORD_FILE`) / metabase wrapper `MB_DB_PASS` / postgres-exporter `DATA_SOURCE_PASS_FILE` |
| `odoo_admin_password` | GH | odoo `ODOO_ADMIN_PASSWORD_FILE` |
| `odoo_bff_api_key` | GH | odoo `EFN_BFF_API_KEY_FILE`, api-gateway `ODOO_BFF_API_KEY_FILE` |
| `rabbitmq_password` | GH | rabbitmq (admin `efn-admin`) |
| `rabbitmq_password_bff` / `_ocr` / **`_etl`** | GH | rabbitmq + api-gateway / ocr-worker / **etl** `RABBITMQ_PASSWORD_FILE` (`AMQP_URL=amqps://efn-{bff,ocr,etl}@rabbitmq:5671`, `AMQP_TLS_CA_FILE`) |
| `redis_password_bff` / `_exporter` | GH | redis ACL + api-gateway `REDIS_PASSWORD_FILE` / redis-exporter wrapper |
| `minio_root_password`, `minio_backup_password`, `minio_bff_secret`, `minio_ocr_secret` | GH | minio/minio-init; postgres `MINIO_BACKUP_PASSWORD_FILE`; api-gateway / ocr-worker `MINIO_SECRET_KEY_FILE` |
| **`minio_prometheus_secret`** | GH | minio-init (user `efn-prometheus`) |
| **`minio_prometheus_token`** | DERIVED | prometheus `authorization.credentials_file` |
| `minio_kms_key` [SA] | GH | minio `MINIO_KMS_SECRET_KEY_FILE` |
| `clickhouse_password`, `_etl`, `_text2sql`, `_metabase` | GH | clickhouse/clickhouse-init; etl `CH_PASSWORD_FILE`; metabase (UI) |
| `clickhouse_disk_key` [SA] | GH | clickhouse wrapper `CLICKHOUSE_DISK_KEY_HEX` |
| `jwt_secret`, `discount_token_secret`, `webhook_payment_secret`, `whatsapp_token` | GH | api-gateway `*_FILE` |
| `otp_pepper` [SA] | GH | api-gateway `OTP_PEPPER_FILE` |
| **`member_hash_pepper`** [SA] | DERIVED (HKDF dari `otp_pepper`) | **odoo `EFN_MEMBER_HASH_PEPPER_FILE`** |
| `pii_encryption_key` [SA] | GH | api-gateway `PII_ENCRYPTION_KEY_FILE` |
| **`altcha_hmac_key`** | GH | api-gateway `ALTCHA_HMAC_KEY_FILE` |
| **`payment_provider_api_key`** | GH (dev `disabled`) | api-gateway `PAYMENT_PROVIDER_API_KEY_FILE` **hanya bila `PAYMENT_PROVIDER` ≠ mock** (`docker-compose.payment.yml`) |
| **`acme_dns_api_token`** | GH (dev `disabled`) | traefik `${ACME_DNS_TOKEN_ENV}_FILE` (default `CF_DNS_API_TOKEN_FILE`) |
| `walg_libsodium_key` [SA] | GH | postgres `WALG_LIBSODIUM_KEY_FILE`, restore-test |
| `grafana_admin_password`, `alertmanager_webhook_url` | GH | grafana / alertmanager |
| `efn_ca_crt` (bundle intermediate+root) | HOST-PKI | semua klien TLS + server mTLS (`--cacert`, `client_ca_file`, `caFiles`) |
| `tls_<svc>_crt/key` (24 service) | HOST-PKI | server TLS masing-masing; **klien mTLS** (EKU both/client): traefik (serversTransports, docker provider), prometheus (`tls_server_*` di semua scrape), grafana (datasource `tlsClientCert`), promtail (Loki + docker_sd), ocr-worker (`OLLAMA_CLIENT_CERT_FILE/KEY_FILE`), blackbox-exporter (modul probe), cadvisor (socket-proxy) |
| — (bukan Docker secret) | GH | `EFN_PKI_INTERMEDIATE_KEY` [SA], `EFN_PKI_INTERMEDIATE_CRT`, `EFN_PKI_ROOT_CRT`, `EFN_PKI_EXTRA_TRUST` (rotasi), `DEPLOY_*` |

Env non-rahasia baru: api-gateway `PAYMENT_PROVIDER` (dev/staging `mock`; prod wajib eksplisit, deploy menolak mock); traefik `EFN_WG_OUTLET_CIDR`, `EFN_WG_STAFF_CIDR`; GitHub vars `PAYMENT_PROVIDER`, `EFN_PUBLIC_BIND`, `EFN_WG_*`, `ACME_DNS_PROVIDER`, `ACME_DNS_TOKEN_ENV`, `RABBITMQ_STRICT_PERMISSIONS`, `MARKET_SCOUT_PHOTO_RETENTION_DAYS` (90), `CH_MART_RETENTION_YEARS` (5). Jumlah GitHub Environment secret turun dari ~82 ke ~40 (leaf tidak lagi di GitHub).

## G5.3 Pengecualian enkripsi/mTLS tersisa (perbarui tabel ADR-022 — architect)

| # | Status | Pengecualian | Kontrol kompensasi |
|---|---|---|---|
| E1 | Tetap (diperketat) | Loopback app↔sidecar (Odoo, Next.js, Metabase, Ollama, cAdvisor, socket-proxy) | Sisi jaringan sidecar kini **mTLS + CN allowlist** |
| E2–E9 | Tetap | (sama seperti G3.3) | — ; E9 + catatan host internal tidak lewat Cloudflare |
| E10 | Tetap | Healthcheck Docker dinonaktifkan untuk endpoint TLS/mTLS | `up`, probe, Uptime Kuma |
| **E11** | Baru | ClickHouse `/metrics` (8443) server-TLS saja, tanpa sertifikat klien: verifikasi klien Poco berlaku global untuk port (ETL/Metabase berpassword tanpa cert) | Network `data` saja, hanya metrik server agregat tanpa PII; rencana: `predefined_query_handler` FORMAT Prometheus + user baca-saja (fase 2) |
| **E12** | Baru (parsial) | mTLS **tanpa allowlist SAN/CN** (klien = sertifikat CA internal ber-EKU clientAuth apa pun): Loki, redis_exporter, RabbitMQ 15691, Traefik :8082, ocr-worker `/metrics` (prometheus-client), BFF `/metrics` (belum memverifikasi klien — kode BFF) | EKU clientAuth hanya diterbitkan ke 7 klien (traefik, prometheus, grafana, promtail, ocr-worker, blackbox, cadvisor); server murni EKU serverAuth saja (dicek CI) |
| **E13** | Baru | `blackbox-exporter` diizinkan CN di sidecar Odoo/Next.js/Metabase/Ollama (probe kesehatan/handshake) — melebihi daftar §8.5 | Blackbox hanya dapat dipicu Prometheus (web-config SAN `prometheus`), target statis; hapus `--allow-cn=blackbox-exporter` bila lead menolak (probe sidecar akan gagal → hapus juga target probe terkait) |

Dihapus: `--disable-authentication` (SEC-20), XFF dari 10/8 (SEC-21), MinIO metrik `public` (SEC-30), `NODE_EXTRA_CA_CERTS` BFF (SEC-23).

## G5.4 Status SEC (infra)

| ID | Status | Catatan |
|---|---|---|
| SEC-20 | **Fixed (infra)**, sisa E11/E12 | Butuh kode: data-AI `vlm.py` membawa `OLLAMA_CLIENT_CERT/KEY_FILE` + `METRICS_TLS_CLIENT_CA_FILE`; BFF `/metrics` verifikasi klien |
| SEC-21 | **Fixed** | `internal-only` = 10.88/16 + 10.89/16, `internal-ops` = staf; entrypoint `wg-https` di IP WG; XFF hanya CF; VLAN tamu di runbook + contoh OpenWrt |
| SEC-22 | **Fixed** | Sidecar Odoo mTLS CN traefik(+blackbox); `/json/2` diblok |
| SEC-23 | **Fixed** | 3 tingkat, nameConstraints, leaf 90 hari, `PKI_SOURCE=host` dihapus, `NODE_EXTRA_CA_CERTS` BFF dihapus |
| SEC-25 | **Partial** | Topologi di broker, write = `amq.default` saja, `efn-etl` minimal; configure/read ekstra tetap sampai BFF & worker passive-declare → lalu `RABBITMQ_STRICT_PERMISSIONS=true` |
| SEC-26 | **Fixed** | stdin `\set pw` |
| SEC-28 | **Fixed (PLAUSIBLE)** | `efn-wg.nft` + catatan ip_forward; `gen-peer.sh --pubkey` |
| SEC-29 | Bukan infra | BFF: baca kunci PII sebagai Buffer, jangan masuk `process.env` |
| SEC-30 | **Partial** | Fixed: MinIO metrik JWT, `ACME_EMAIL` divalidasi, pg_hba subnet, proteksi branch Environment didokumentasikan (key-custody §3). Belum: `semgrep:latest` (digest belum diketahui tanpa akses registry), `toJSON(secrets)` tetap (diterima), passlib Odoo (odoo-engineer) |
| QA U1 | **Fixed (infra)** | deploy menjalankan SQL data-AI sebagai `efn_odoo` |
| QA U3/U7/U8 | Fixed / Partial / Fixed | U3 = SEC-20; U7 = SEC-25; U8 relabel subnet `internal` |

## G5.5 Keputusan & penyimpangan dari paket kerja

1. **File secret 0444 (bukan 0440)** dalam dir `root:efn-superadmin 0750`: compose non-swarm bind-mount file apa adanya; uid container beragam dan gosu/su-exec (postgres image resmi, redis, rabbitmq, clickhouse) membuang supplementary group → `group_add` + 0440 tidak andal tanpa uji runtime. Batas akses host = izin traverse direktori. `SECRET_FILE_MODE=0440` dapat diuji di staging. Detail: key-custody.md §Mekanisme uid.
2. **deploy.sh berjalan sebagai root** (`sudo -n bash -s`); user deploy (grup docker) sudah setara root — dicatat jujur di runbook.
3. **Alokasi WG = ADR-023** (outlet 10.88/16, staf 10.89/16), bukan 10.66/16 (template repo memakai 10.88).
4. **Sertifikat klien = sertifikat service yang sama dengan EKU server+client** untuk 6 service (promtail: client saja), bukan pasangan file baru — server murni kini serverAuth-only sehingga mTLS "CA + EKU" bermakna.
5. Leaf tidak lagi di GitHub; diterbitkan di host tiap deploy. Konsekuensi: rotasi leaf butuh deploy (alert < 21 hari); menambah service = intermediate baru (nameConstraints).
6. Dev override Traefik: `--api.insecure`/dashboard :8080 dihapus (diabaikan Traefik karena `--configFile` dipakai; port 8080 tidak pernah berfungsi).

## G5.6 Koordinasi

- **data-AI**: `vlm.py` → `requests(..., cert=(OLLAMA_CLIENT_CERT_FILE, OLLAMA_CLIENT_KEY_FILE))`; `/metrics` wajib `METRICS_TLS_CLIENT_CA_FILE`; ETL publish `privacy.erasure.completed` tanpa declare (efn-etl configure/read kosong; queue dibuat broker); passive declare di worker → strict mode. `services/etl/sql/odoo-readonly-grants.sql` harus idempoten & dapat dijalankan sebagai `efn_odoo`. Blok TTL `CH_MART_RETENTION_YEARS` di `clickhouse-init.sh` — env sudah diteruskan, file tidak saya ubah.
- **BFF**: `ALTCHA_HMAC_KEY_FILE`, `PAYMENT_PROVIDER`, `PAYMENT_PROVIDER_API_KEY_FILE` (opsional bila mock); jangan andalkan `NODE_EXTRA_CA_CERTS`; consumer `privacy.erasure.completed` + allowlist `user_id=efn-etl`; `assertQueue` → `checkQueue` agar strict mode bisa aktif; `/metrics` verifikasi klien (`requestCert`, CA internal, CN `prometheus`).
- **Odoo engineer**: baca `EFN_MEMBER_HASH_PEPPER_FILE` (hex 64, sama dengan BFF `memberHashPepper`; test vector di atas).
- **Architect/lead**: perbarui ADR-022 (W1 & W2 selesai, E11–E13), ADR-023 (alokasi 10.88/10.89 final, OpenWrt), docs/03 (GitHub Environment secrets/vars baru, `sudo` deploy), docs/04 §8.6 (0750/0444 vs "0700").

## G5.7 Risiko CI / runtime (cek pertama saat push)

1. **CI `infra`**: `openssl req -newkey ec -pkeyopt` & `-addext` (OpenSSL 3 di runner ✓), `-ext nameConstraints` output; `openssl kdf` di runner (OpenSSL 3.0 Ubuntu 22.04/24.04); promtool `credentials_file` + `check web-config` dengan `client_allowed_sans`; jq baru di `check-encryption.sh`; shellcheck `gen-pki.sh`/`derive-secrets.sh`/`gen-peer.sh`/`deploy.sh`/`00-roles.sh` (printf `\\\\set`); yamllint `prometheus.yml` (regex relabel) & baris panjang compose.
2. **`docker compose config` prod**: `PAYMENT_PROVIDER:?` (`.env.example` mengisi mock), env list `${ACME_DNS_TOKEN_ENV:-CF_DNS_API_TOKEN}_FILE=...`, port `${EFN_PUBLIC_BIND}`.
3. **Runtime PLAUSIBLE**: Traefik `serversTransports.certificates` + TLS option per Host pada entrypoint metrics; lego `CF_DNS_API_TOKEN_FILE`; RabbitMQ `prometheus.ssl.verify/fail_if_no_peer_cert` + definitions `queues` quorum (argumen harus identik dengan klien → kalau beda: PRECONDITION_FAILED); MinIO JWT metrik (`admin:Prometheus` untuk user non-root); `REDIS_EXPORTER_TLS_SERVER_CA_CERT_FILE`; Loki `client_auth_type`; Grafana `tlsClientCert` via `$__file{}`; nftables `ct original proto-dst` dan interaksi dengan iptables-nft Docker; Docker bind `10.88.0.1:443` (wg0 harus naik dulu); DNAT mempertahankan IP sumber peer (bila `userland-proxy` ikut campur, allowlist melihat IP gateway → 403 → set `"userland-proxy": false`).
4. **Migrasi**: deploy pertama gelombang 5 menerbitkan ulang SEMUA leaf + recreate semua container; Superadmin harus mengunggah `EFN_PKI_*` dan vars `PAYMENT_PROVIDER`, `EFN_PUBLIC_BIND`, secret `ACME_DNS_API_TOKEN`, `ALTCHA_HMAC_KEY`, `RABBITMQ_PASSWORD_ETL`, `MINIO_PROMETHEUS_SECRET`, `PAYMENT_PROVIDER_API_KEY` (`disabled` bila mock) **sebelum** deploy; host butuh grup `efn-superadmin`, sudoers deploy, wg0 + drop-in docker.


---

# Gelombang 7 — runtime (6 Okt 2026, Docker Desktop 29.8.1 / WSL2)

Pertama kali stack dijalankan nyata. Semua hasil di bawah **terverifikasi di runtime**, kecuali yang ditandai *belum*.

## G7.1 Langkah CI infra (direplikasi persis di container)

| Langkah | Status | Catatan |
|---|---|---|
| yamllint `infra .github` | **Lolos** (hanya warning line-length) | 2 syntax error diperbaiki: `name:` berisi `: ` tanpa kutip (`security.yml:94`, `ci.yml:226`) |
| actionlint (rhysd/actionlint:latest) | **Lolos** | — |
| shellcheck (persis ci.yml) | **Lolos** | SC2034 `deploy.sh` (`for _`), SC2115 `fetch-oca.sh` (`${TARGET:?}`) |
| secrets compose == secrets.list | **Lolos setelah perbaikan** | Bug nyata: `compose config` membuang secret top-level yang tak dipakai service, sehingga `payment_provider_api_key` (hanya di-mount `docker-compose.payment.yml`) hilang. Langkah CI kini ikut `-f docker-compose.payment.yml`. Ratusan baris `-` di harness lead berasal dari escaping awk di harness itu, bukan bug repo. |
| prod hanya publish Traefik | Lolos | — |
| promtool (prometheus.yml, web-config*.yml) | Lolos | Diulang setelah perubahan prometheus.yml |
| amtool | Lolos | — |
| hadolint `--failure-threshold warning` | **Lolos** | DL3003 di `infra/backup/postgres/Dockerfile` (hapus `cd /tmp`, path absolut). `infra/minio/Dockerfile` baru juga lolos. |
| derive self-test, check-encryption, cloudflare, grafana JSON | Lolos | — |
| `docker compose config -q` dev & prod (semua profil) | Lolos | — |

## G7.2 Status service

| Profil | Service | Status | Catatan |
|---|---|---|---|
| core | postgres, redis, rabbitmq, minio, minio-init (exit 0), docker-socket-proxy(+tls), traefik, odoo(+tls, +ws-tls), api-gateway, superapp-web(+tls) | **healthy** | lihat perbaikan G7.3 |
| data | clickhouse, clickhouse-init (exit 0), etl, metabase(+tls) | **healthy** | tabel `efn_dwh` ber-`storage_policy=encrypted` |
| observability/ops | prometheus, loki, promtail, grafana, uptime-kuma, alertmanager, node-exporter, cadvisor(+tls), postgres-/redis-/blackbox-exporter | **Up**; yang punya healthcheck = healthy | 33/33 target Prometheus `up`; Loki menerima log 29 service |
| ai | ollama, ocr-worker | *belum di-boot* | butuh pull model; alert `EfnOcrNoConsumer` dan probe `ollama:11434` wajar gagal |

Alert yang aktif di dev dan memang diharapkan: `EfnPublicEndpointDown` (blackbox memakai CA publik sementara dev memakai sertifikat self-signed), `EfnBackupMetricMissing`, `EfnOcrNoConsumer`, probe ollama.

## G7.3 Perbaikan (bug nyata yang ditemukan runtime)

1. **Image MinIO hilang dari registry**: `minio/minio` & `minio/mc` tidak ada lagi di Docker Hub, dan quay.io meminta auth. Solusi: `infra/minio/Dockerfile` membangun **dari source tag upstream** (minio `RELEASE.2025-10-15T17-29-55Z`, yang memuat fix CVE-2025-62506; mc `RELEASE.2025-08-13T08-35-41Z`), non-root uid 1000, satu image untuk `minio` dan `minio-init`. Image ditambahkan ke `build-push.yml` (scan/SBOM/sign), `deploy.yml` (cosign verify digest), `deploy.sh` (`IMAGE_MINIO` wajib digest), `.env.example`. **Keputusan untuk lead/architect**: upstream community edition kini source-only dan dalam mode maintenance. Pilihannya: build sendiri (cara sekarang), fork komunitas (pgsty/minio), atau S3 lain (Garage/SeaweedFS). Pertimbangkan ADR.
2. **CRLF di secret** (openssl Windows): `tr -d '\n'` menyisakan CR, sehingga postgres 00-roles menolak password dan init gagal di tengah jalan (volume tercatat sudah terinisialisasi, role tidak ada). Redis menolak password. Kini `gen-secrets.sh` memakai `tr -d '\r\n'` dan `gen-pki.sh` membuang CR dari serial. Secret turunan dihitung ulang.
3. **Redis**: (a) `chmod` setelah `chown` ditolak (tanpa CAP_FOWNER), urutan dibalik. (b) **Crash-loop pada setiap restart dengan data**: entrypoint resmi menjalankan `find . ! -user redis -exec chown` sebagai root tanpa DAC_OVERRIDE (`appendonlydir: Permission denied`). Wrapper kini langsung `setpriv --reuid redis` dan tidak memakai entrypoint resmi. Sudah diuji dengan 2x restart. (c) ACL exporter `+latency|histogram`.
4. **MinIO metrik**: policy `admin:Prometheus` dengan `Resource arn:minio:admin:::*` ditolak ("invalid resource"), sehingga user efn-prometheus gagal dibuat. Resource dihapus; target `minio` kini up.
5. **Traefik v3.2 tidak bisa bicara dengan Docker Engine 29** (API minimum 1.44, provider docker "Error response from daemon"). Dinaikkan ke `traefik:v3.7.13`. `aliasHeadersStrategy: delete` ditambahkan di semua entrypoint (peringatan keamanan v3.7: spoof header alias seperti `X_Forwarded_For`).
6. **TLS publik dev gagal total**: `sniStrict: true` + tidak ada sertifikat untuk `*.efn.local` (ACME tidak bisa), sehingga handshake ditolak ("unrecognized name"). Perbaikan: sertifikat edge **dev-only** self-signed (`gen-secrets.sh`, `secrets/dev_edge_*.txt`, terpisah dari CA internal) + `infra/traefik/dev/edge-dev.yml`, di-mount oleh dev override. Mount dynamic base dipindah ke `/etc/traefik/dynamic/base`; provider file membaca subdirektori secara rekursif. Prod tidak berubah perilakunya.
7. **RabbitMQ :15692 plaintext terbuka di network** (listener TCP default plugin prometheus, tidak bisa dimatikan lewat conf). Kini diikat ke `127.0.0.1`.
8. **mTLS Prometheus → RabbitMQ :15691 gagal** ("internal error"). Erlang/OTP `public_key` crash (`function_clause` di `pubkey_cert:match_name(iPAddress, ...)`) saat sertifikat klien punya SAN IP dan intermediate punya nameConstraints IP. Direproduksi di OTP RabbitMQ 3.13 **dan** 4.1. Perbaikan: `gen-pki.sh` `NO_IP_SAN_SERVICES=" prometheus "` (leaf prometheus tanpa SAN IP, terbit ulang otomatis). Perhatian: klien mTLS baru ke server Erlang harus masuk daftar ini.
9. **Alertmanager**: `--web.external-url=.../alertmanager` memindahkan semua route, sehingga scrape **dan pengiriman alert** Prometheus mendapat 404. Perbaikan: `path_prefix: /alertmanager` + `metrics_path`.
10. **Target api-gateway hilang**: DNS Docker mengembalikan IP network `data` (172.30.2.x), lalu relabel `172.30.1.x` membuangnya. Kini `api-gateway.efn_internal` / `ocr-worker.efn_internal`.
11. **Promtail**: `tcp://` menyebabkan klien docker memakai HTTP polos ke port mTLS (koneksi putus/EOF). Diganti `https://`. Discovery & push Loki jalan. Sisa: peringatan berkala `could not transfer logs ... request canceled` (stream log terputus timeout lalu tersambung ulang). Log tetap masuk, tapi perlu dipantau.
12. **ClickHouse**: disk `encrypted` di atas `default` ditolak oleh 24.8 ("wrapped disk must have been announced earlier"). Kini memakai disk lokal eksplisit `disk_local` (`/var/lib/clickhouse/disks/local/`). Volume prod lama (bila ada) perlu migrasi; untuk dev volume baru.
13. **BFF dev OOM** (`nest start --watch`, heap ~250 MB): dev override `NODE_OPTIONS=--max-old-space-size=1024`, limit 1536M.
14. **node-exporter** di Docker Desktop: `rslave` ditolak. Dev override `/:/host:ro`. Prod tetap rslave.

## G7.4 Bukti enkripsi / kontrol akses (runtime)

- Traefik publik: TLSv1.3 (`TLS_AES_128_GCM_SHA256`). `https://api.efn.local/health` 200, `app.efn.local` 200, HTTP 80 → 301 https. HSTS/CSP terpasang. `/metrics` & `/v1/pos/*` di api publik 404. `erp.` di 443 publik 404. Di wg-https :9443: `/web/login` 200 (setelah init DB), `/web/database/manager` 404, `/jsonrpc` & `/xmlrpc` 403.
- PostgreSQL: `sslmode=disable` → `pg_hba rejects ... no encryption`. Superuser via TCP+TLS ditolak. `pg_stat_ssl`: efn_bff, efn_odoo, efn_metabase semuanya `ssl=t TLSv1.3`.
- Redis: plaintext → connection reset. TLS + ACL efn-bff → PONG. User default tanpa auth ditolak. BFF tersambung sebagai `efn-bff`.
- RabbitMQ: hanya 5671/15671/15691 di network (5672, 15672, 25672, 4369 tertutup, dan 15692 kini loopback). Handshake AMQP plaintext dibalas alert TLS. `list_connections`: efn-bff `ssl=true tlsv1.3`. BFF reconnect otomatis setelah broker restart.
- MinIO: HTTP → 400. HTTPS verify CA → 200. BFF→MinIO TLS terverifikasi (`authorized=true`, TLSv1.3, CN minio). Policy efn-bff: Put `reports/*` OK, Get & Put di luar prefix ditolak. Objek SSE-S3. WAL-G archive ke MinIO berjalan (`pg_stat_archiver` archived 3; 4 gagal awal sebelum MinIO siap).
- mTLS sidecar: odoo-tls tanpa sertifikat klien / CN api-gateway → ditolak; CN traefik → 200. BFF :9464 tanpa sertifikat → ditolak. docker-socket-proxy tanpa sertifikat → `certificate required`. Port plaintext 8069/3001 tidak terjangkau dari network.
- ClickHouse: hanya 8443/9440 (TLS) yang listen.

## G7.5 File yang diubah

`.github/workflows/{ci,security,build-push,deploy}.yml`; `scripts/{deploy,fetch-oca,gen-secrets,gen-pki}.sh`; `infra/compose/{docker-compose.yml,docker-compose.dev.yml,.env.example}` (+ `.env` lokal, di-ignore); `infra/minio/Dockerfile` (baru), `infra/minio/minio-init.sh`; `infra/redis/redis-entrypoint.sh`; `infra/rabbitmq/rabbitmq.conf`; `infra/traefik/traefik.yml`, `infra/traefik/dev/edge-dev.yml` (baru); `infra/observability/{prometheus,promtail}.yml`; `infra/clickhouse/config.d/encrypted-storage.xml`; `infra/backup/postgres/Dockerfile`; `docs/runbooks/dev-stack-boot.md` (baru). Tidak ada perubahan di `services/**`.

## G7.6 Sisa / butuh agent lain

- **Lead/architect**: keputusan sumber image MinIO (G7.3 no. 1, perlu ADR); Traefik naik minor v3.2 → v3.7 (cek catatan migrasi v3.3–v3.7 terhadap middleware yang ada; peringatan `encodedCharacters` belum ditindaklanjuti); update docs/04 §7 (subjek tanpa SAN IP untuk klien Erlang).
- **efn-bff-engineer**: image dev (`target: dev`) berjalan sebagai **root** (stage dev tanpa `USER`); prod runtime non-root. Peringatan `EBADENGINE` (paket butuh Node ≥ 22.13, image 22.12): pertimbangkan bump `node:22.x-alpine`.
- **efn-odoo-engineer/functional**: DB `efn_odoo` hanya diinit `base` (runbook §3). Pemasangan modul efn_* belum diuji di stack ini.
- **efn-data-ai-engineer**: profil `ai` belum di-boot.
- *Belum*: restore-test.sh, backup base (`efn-walg backup-push`), deploy prod overlay, workflow GitHub di runner nyata.

Volume dev `efn_*` **dipertahankan** setelah `down` (pg-data sudah berisi `efn_odoo` ter-init base, plus MinIO, ClickHouse, dan lain-lain), sehingga run berikutnya tidak perlu init ulang. Reset: `down -v`.

---

# Gelombang 9 — SILO, filter L7, access key acak, SEC-35/36/37/30, R1/R5/R7 (6 Okt 2026)

Runtime: Docker Desktop 29.8.1 (WSL2), host 16 CPU/31 GB yang **sangat padat** (CPU 100 %, RAM bebas 0,2–7 GB karena
proses lain di host). Docker Desktop sempat mati sekali (pipe hilang, pull Ollama `unexpected EOF`) dan saya nyalakan
ulang; hasil di bawah diambil setelah restart. ADR-030 masih **Proposed**; lead memutuskan menerapkan rekomendasi jangka
pendek. Semua perubahan reversible (G9.5).

## G9.1 Status per butir

| # | Butir | Status | Bukti (runtime kecuali disebut) |
|---|---|---|---|
| 1 | Object storage → **SILO** dari source | **Selesai** | `infra/minio/Dockerfile`: `pgsty/silo` + `pgsty/mc` tag `RELEASE.2026-09-16T00-00-00Z` (rilis terbaru per GitHub API), commit SHA diverifikasi saat build (`2a4d514…`, `e952aa7…`), `golang:1.27.1-alpine@sha256:cf6fca…` (= `go.mod` 1.27.1, digest yang sama dipakai SILO), runtime `alpine:3.24`, non-root 1000, symlink `minio`/`mcli`. `silo version RELEASE.2026-09-16T15-50-09Z (go1.27.1)`. **Upgrade in-place volume dev lama** (data MinIO 2025-10-15): boot healthy tanpa reset; 16 objek lama `pg-backup/efn/` terbaca; versioning enabled; `Object locking 'GOVERNANCE' 15DAYS`; `sse-s3` auto-encrypt kedua bucket; objek baru `Encryption: SSE-S3`; ILM `reports/` 90 hari + noncurrent 35 hari; `minio-init` idempoten. Metrik JWT lewat filter: 219 baris `minio_*` dengan token, 403 tanpa token |
| 2 | Access key acak (§8.12) | **Selesai** | Secret `minio_{bff,ocr,backup,prometheus}_access_key` (generator `akid` = 20 hex huruf besar, 80 bit): `secrets.list`, `gen-secrets.sh`, compose (top-level + mount per service). BFF & worker `MINIO_ACCESS_KEY_FILE`; WAL-G `MINIO_BACKUP_ACCESS_KEY_FILE` (`efn-walg.sh`, `restore-test.sh`, runbook backup-restore); token JWT Prometheus `sub/accessKey` dari file (`derive-secrets.sh`, diverifikasi). `minio-init` membuat user dari access key acak dan **menghapus user lama** `pgbackup/efn-bff/efn-ocr/efn-prometheus` (log "dihapus: 4"); keluaran `mc admin user` dibuang (access key tidak masuk log). Uji: kredensial lama `efn-bff` ditolak; bff PUT `reports/` OK, PUT di luar prefix & GET ditolak; ocr GET OK, PUT ditolak. Worker: `efn_ocr/config.py` (`_minio_access_key`: file > literal hanya APP_ENV dev/development/local/test dan bukan production, regex + min 16 di luar dev) + test (fixture `MINIO_ACCESS_KEY_FILE`, 4 test baru). pytest **168 lulus**, ruff 0.16.10 bersih |
| 3 | Filter L7 | **Selesai** | Service `s3-gateway` = HAProxy 3.2.25 LTS (digest dipin), `infra/s3-gateway/{haproxy.cfg,entrypoint.sh,README.md}`. TLS masuk sertifikat `s3-gateway` (gen-pki), re-encrypt `ssl verify required ca-file … verifyhost minio`. Storage dipindah ke network baru `objstore` (172.30.6.0/24, internal): hanya `s3-gateway` + `minio-init`. Klien: BFF/worker `MINIO_ENDPOINT=s3-gateway`, WAL-G `https://s3-gateway:9000`, Prometheus job `minio` + probe TLS. Header trailer dicek di sumber dulu: minio-js 8.x `UNSIGNED-PAYLOAD`/sha256, minio-py 7.2.12 `UNSIGNED-PAYLOAD`, WAL-G v3.0.9 = aws-sdk-go **v1** (tanpa trailer) → aman diblok; dibuktikan runtime (upload BFF, GET worker, WAL-G push sha256 tetap 200 lewat filter). Smoke `g.s3gw_*` (10 cek) lulus: bypass-governance, `PUT ?retention`, `x-amz-object-lock-*`, trailer, `X-Minio-Replication-*`, `?select`, `/minio/admin/` → 403 halaman HAProxy; HTTP polos ditolak; storage tak ter-resolve dari `data`; kontrol positif: S3 biasa diteruskan (AccessDenied dari storage). `mc retention clear` oleh user backup → 403 filter |
| 4 | R1 Ollama | lihat G9.3 | `ollama/ollama:0.35.1@sha256:292ee7…` (stabil terbaru 29 Sep 2026; 0.40 masih rc), `hostname: ollama` tetap; container + sidecar mTLS + ocr-worker healthy. `ollama pull qwen2.5vl:3b`: manifest **diterima** (0.5.4 = 412) |
| 5 | R5 ETL Odoo | **Selesai** | `ETL_ENABLE_ODOO` (base `false`, dev `true`, `.env.example`, var deploy). Sebelum grant: `job=sales_fact_pos gagal … permission denied for table pos_order_line` (job lain tetap jalan). Smoke `f` menerapkan `odoo-readonly-grants.sql` (sama dengan deploy.sh: `psql -U efn_odoo` via socket) → `f.odoo_etl_grants` OK, **`f.ch_fact_sales_line rows=36`** dari order POS smoke. `deploy.sh`: setelah grant sukses dan var tidak di-set, `.env` → `ETL_ENABLE_ODOO=true` + `up -d etl` (nilai dipertahankan antar-deploy) |
| 6 | R7 nightly | **Selesai, belum pernah jalan di GitHub** | `.github/workflows/e2e-nightly.yml`: `schedule` 02:00 WIB + `workflow_dispatch` (input skenario divalidasi regex), `permissions: contents: read`, action dipin SHA, secret dummy `gen-secrets.sh`, build → core → install modul Odoo → data+ai (Ollama tanpa model) → `run-e2e-smoke.sh` (`SMOKE_OUT_DIR` baru) → artefak (hasil, `compose ps`, tail log; data fiktif) → `down -v`. Runner `vars.E2E_RUNNER` (default ubuntu-latest; stack ±12–16 GB → larger/self-hosted untuk repo privat) |
| 7a | SEC-35 | **Selesai** | Loki → `127.0.0.1:3101` + sidecar `loki-tls` (`--allow-cn` promtail, grafana, prometheus); redis_exporter → `127.0.0.1:9122` + `redis-exporter-tls` (prometheus); RabbitMQ: listener TLS 15691 plugin dicabut, `rabbitmq-metrics-tls` :15691 → 127.0.0.1:15692 (prometheus); Traefik entrypoint `metrics` → `127.0.0.1:8084` + `traefik-metrics-tls` :8082 (prometheus). Uji: sertifikat prometheus → 200 di keempatnya, grafana → Loki 200; sertifikat **ocr-worker → `alert bad certificate`** di keempatnya. Target `loki`, `redis`, `rabbitmq`, `traefik`, `minio`, `api-gateway`, `ocr-worker`, `clickhouse` = up. `loki:3100` dikeluarkan dari probe-internal-tls (blackbox tidak boleh membaca log). check-encryption: Loki wajib loopback |
| 7b | SEC-36 | **Kode + runbook selesai; host belum dipasang** | `scripts/host/efn-deploy` (wrapper root, dipasang manual Superadmin) + `deploy.yml`: bundel `infra/ scripts/ services/etl/sql` deterministik ditandatangani **cosign keyless** (`id-token: write` hanya job deploy), dikirim bersama env base64 lewat satu stdin tar ke forced command `restrict,command="sudo -n /usr/local/sbin/efn-deploy"`. Wrapper: batas 64 MB, tepat 3 file biasa, `cosign verify-blob` dengan identitas/issuer dari `/etc/efn/deploy.conf` (root), tolak symlink & path di luar `infra|scripts|services/etl/sql`, ekstrak ke `/opt/efn/releases/<sha256>` milik root, sync ke `/opt/efn`, env hanya allowlist (nama tetap + `secrets.list` rilis terverifikasi), `exec /bin/bash deploy.sh` salinan root. Uji container (cosign palsu): tanda tangan invalid, `PATH`, `LD_PRELOAD`, `$(id)`, entri tar ekstra, path traversal, stdin kosong → ditolak; kasus sah → deploy.sh jalan sebagai root, nilai ber-spasi/`$` utuh, file konfigurasi 0644 root. Runbook `key-custody.md §4a`: sudoers tanpa argumen, `deploy` keluar dari grup docker, cosign v2.6.1 + sha256, auditd |
| 7c | SEC-37 | **Prosedur + desain** | `docs/runbooks/acme-dns-token.md`: delegasi `_acme-challenge` CNAME ke zona khusus, token `DNS:Edit`+`Zone:Read` satu zona + IP filtering + TTL, uji negatif ke zona utama, opsi wildcard `*.int` untuk CT. Pemisahan ACME dari Traefik (container lego di `egress`) didesain, **belum** diterapkan |
| 7d | SEC-30 semgrep | **Selesai** | `semgrep/semgrep:1.179.0@sha256:93963d…` (digest index, `imagetools inspect`) |
| 8 | Validasi | **Lulus** | G9.2 |

Pesan lead di tengah tugas: `api-gateway` memakai `MINIO_ACCESS_KEY_FILE=/run/secrets/minio_bff_access_key` + secret di-mount,
literal dihapus (dev juga). `04-platform-v3-6.sql` dijalankan manual di volume dev (2×, idempoten) sebelum BFF start;
`deploy.sh` kini menjalankan semua `infra/postgres/init/NN-*.sql` dengan NN ≥ 04 sebagai superuser (socket lokal) setelah
`00-roles.sh` dan sebelum `up` penuh (kontrak: file ≥ 04 wajib idempoten + transaksional); runbook dev-stack-boot §2b.

## G9.2 Validasi

| Langkah CI `infra` (direplikasi di container Linux) | Hasil |
|---|---|
| yamllint `infra .github` / actionlint | 0 error / lulus (termasuk e2e-nightly.yml, deploy.yml baru) |
| shellcheck (daftar CI + `scripts/host/efn-deploy`, `scripts/smoke/*.sh`, `infra/s3-gateway/*.sh`) | bersih |
| hadolint v2.12.0 (= hadolint-action v3.1.0) | lulus (`hadolint:latest` memunculkan DL3062/DL3025 baru; CI memakai versi action) |
| `haproxy -c` (langkah CI baru) | valid |
| promtool (prometheus.yml, 25 rules) / amtool | lulus |
| compose config dev & prod, secrets == secrets.list, prod hanya publish Traefik | OK |
| check-encryption (+ cek baru: Loki loopback, s3-gateway TLS/verify) | OK |
| derive self-test (python3 & openssl), cloudflare sinkron | OK |
| PKI | `gen-pki.sh` mendeteksi intermediate dev yang tidak mencakup `s3-gateway`, mengarsipkan CA lama, membuat ulang root+intermediate dev + semua leaf; run kedua idempoten |

Runtime: core + data + ai healthy; observability/ops dinyalakan sebagian (RAM host). **Smoke E2E penuh (a b c d f e g):
PASS=88 FAIL=0** (gelombang 8: 72). Cek baru: `g.s3gw_*`, `g.storage_not_reachable_from_data`, `f.odoo_etl_grants`,
`f.ch_fact_sales_line`. Smoke sempat gagal di skenario c karena kolom `res_partner.efn_erased` (perubahan odoo-engineer
paralel) belum di-upgrade di DB dev → saya jalankan `-u efn_privacy,efn_pos_otp_gate` (dev saja).

## G9.3 R1 — VLM

- **Terverifikasi:** `ollama/ollama:0.35.1` (digest dipin) jalan di belakang sidecar mTLS, `hostname: ollama` tetap,
  healthcheck & ocr-worker healthy, probe/target tidak regresi; `ollama pull qwen2.5vl:3b` **menerima manifest**
  (0.5.4 menjawab 412) dan mengunduh blob 3,2 GB sampai 100 %.
- **Belum terverifikasi:** model tidak pernah selesai terdaftar — Docker Desktop mati di akhir unduhan pertama; unduhan
  ulang (mulai dari 0) turun ke ±12 KB/s (estimasi 61 jam) sehingga dihentikan. Jadi smoke `report_vlm` dengan model
  nyata **belum dijalankan**; jalur tanpa model (`flagged/vlm_unavailable`) tetap lulus di smoke b.
- Ulangi di jaringan normal: `docker exec efn-ollama-1 ollama pull qwen2.5vl:3b`, set `OLLAMA_VISION_MODEL=qwen2.5vl:3b`
  di `.env`, `up -d ocr-worker`, lalu jalankan subperintah `report_vlm` (runbook dev-stack-boot §3b). Default compose
  masih `qwen2.5vl:7b` (keputusan data-ai/architect, ADR-008).

## G9.3b Kesiapan penghapusan OCA (instruksi user, commit 244de70) — DISIAPKAN, BELUM DIEKSEKUSI

Menunggu kabar lead bahwa modul audit pengganti `efn_setup_audit` sudah ada (addons tidak saya sentuh).
- **Siap:** `scripts/check-odoo-deps.sh <daftar-CE>` — gagal bila `depends` efn_* di luar Odoo CE 19 / `efn_*`.
  Diuji: daftar CE dari `efn-odoo:ci` (692 modul) → saat ini **1 pelanggaran: `efn_setup_audit` → `auditlog`** (benar),
  shellcheck bersih. Belum dipasang di CI (akan memerahkan CI sampai audit diganti).
- **Inventaris yang akan dihapus/diubah:** `scripts/fetch-oca.sh`; `ci.yml` (path-filter `scripts/fetch-oca.sh` baris 40,
  langkah "Fetch OCA addons" + pengecualian auditlog + `--addons-path …/mnt/oca-addons` di job odoo-test); `build-push.yml`
  langkah "Fetch OCA addons"; `services/odoo/Dockerfile` (`COPY oca /mnt/oca-addons`, mkdir/chown) dan
  `services/odoo/odoo.conf.tpl` `addons_path` (di luar batas file saya → minta izin lead/odoo-engineer); `.gitignore`
  baris 22–24 + `services/odoo/oca/README.md`. Tidak ada rujukan di `deploy.sh`, compose, `check-encryption.sh`,
  runbook, maupun Makefile (tidak ada Makefile di repo). Lalu job CI baru: ekstrak daftar CE dari image odoo-test,
  jalankan `check-odoo-deps.sh`.

## G9.4 File yang diubah / dibuat

Baru: `infra/s3-gateway/{haproxy.cfg,entrypoint.sh,README.md}`, `scripts/host/efn-deploy`, `scripts/check-odoo-deps.sh`, `.github/workflows/e2e-nightly.yml`,
`docs/runbooks/acme-dns-token.md`.
Diubah: `infra/minio/{Dockerfile,minio-init.sh}`, `infra/compose/{docker-compose.yml,docker-compose.dev.yml,.env.example}`,
`infra/backup/postgres/efn-walg.sh`, `infra/observability/{loki.yml,prometheus.yml}`, `infra/rabbitmq/rabbitmq.conf`,
`infra/traefik/{traefik.yml,dynamic/internal-tls.yml}`, `scripts/{secrets.list,gen-secrets.sh,gen-pki.sh,derive-secrets.sh,
deploy.sh,restore-test.sh,check-encryption.sh}`, `scripts/smoke/run-e2e-smoke.sh`, `.github/workflows/{ci.yml,deploy.yml,
security.yml}`, `docs/runbooks/{dev-stack-boot,internal-pki,key-custody,backup-restore}.md`,
**di luar infra (dicatat):** `services/ai-ocr-worker/efn_ocr/config.py`, `services/ai-ocr-worker/tests/test_config.py`.
Lokal (tidak di-commit): `secrets/*` baru, CA dev lama di `secrets/pki-ca.old-*`, `.env` (hapus `MINIO_BACKUP_USER`,
tambah `ETL_ENABLE_ODOO`), image rollback `efn/minio:legacy-2025-10-15`.

## G9.5 Rollback (reversible, ADR-030 Proposed)

- Storage: ganti ARG/repo di `infra/minio/Dockerfile` kembali (atau tag `efn/minio:legacy-2025-10-15` di dev). Format
  `.minio.sys` tidak diubah oleh boot SILO (diuji satu arah saja: lama → SILO; arah balik **belum diuji**).
- Filter: kembalikan `MINIO_ENDPOINT`/`WALG_S3_ENDPOINT`/target Prometheus ke `minio` dan tambahkan `internal, data`
  ke network `minio`. Access key acak tetap dipakai (tidak bergantung filter).

## G9.6 Sisa / butuh pihak lain

- **Lead/architect:** ADR-030 masih Proposed (Q-OS1..5); docs/04 §7.3 (endpoint storage = `s3-gateway`, network `objstore`)
  dan ADR-022 (hop baru s3-gateway; loopback E1 untuk Loki/redis_exporter/RabbitMQ metrics/Traefik metrics menggantikan E12)
  perlu diperbarui architect. Staging/produksi: **intermediate PKI harus diterbitkan ulang** (service baru `s3-gateway`
  di nameConstraints; deploy.sh gagal jelas bila belum) + 4 secret `MINIO_*_ACCESS_KEY` baru di GitHub Environment.
- **Superadmin (SEC-36):** pasang `efn-deploy`, `/etc/efn/deploy.conf`, cosign, sudoers, authorized_keys forced command,
  keluarkan `deploy` dari grup docker — **deploy.yml baru tidak akan jalan sebelum ini**. Belum diuji end-to-end dengan
  cosign/Rekor asli (hanya cosign palsu di container).
- **SEC-37:** delegasi DNS & token terbatas = tugas Superadmin; komponen ACME terpisah belum dibangun.
- **SN-2026-015** masih berlaku untuk kredensial yang langsung ke storage (root via `minio-init`); filter menutup jalur
  aplikasi. COMPLIANCE menunggu Q-OS3.
- **Filter:** log HAProxy ke stdout kehilangan baris saat host sangat lambat (permintaan smoke tidak semua tercatat);
  HAProxy sempat mencetak peringatan watchdog `stuck` lalu pulih (CPU host 100 %). Belum ada metrik HAProxy. Belum diuji
  base backup besar (`backup-push`) dan `restore-test.sh` lewat filter.
- **BFF/QA:** `mc rm` dengan user bff gagal karena mc melakukan stat (GetObject) dulu; BFF memakai `removeObject` langsung
  (tidak terdampak), tetapi QA sebaiknya menguji erasure foto DSAR lewat filter (smoke e lulus).
- **R7:** workflow belum pernah jalan di GitHub; ukuran runner perlu diputuskan (biaya larger runner).
- **docs/testing/E2E_SMOKE_REPORT.md** (milik QA) perlu diperbarui dengan hasil 88/88 dan R1/R5/R7.


# Gelombang 11 — SEC-41, SEC-42, SEC-47, SEC-48, sink audit Odoo ke Loki (6 Okt 2026)

Basis: `a810b55`, review keamanan r4 (`docs/reviews/SECURITY_REVIEW_2026-10-06-r4.md`). Semua diuji di stack dev
nyata (Docker Desktop/WSL2, profil core+data+ai+observability+ops) kecuali yang ditandai.

## G11.1 Status

| SEC | Status | Bukti |
|---|---|---|
| SEC-41 (s3-gateway encoding) | **Fixed + terverifikasi runtime** | Sebelum perbaikan bypass **terkonfirmasi** (bukan lagi PLAUSIBLE): `PUT /pg-backup/efn/x?rete%6Etion` → SILO `MissingContentMD5` (= handler PutObjectRetention), `PUT ?X-Amz-Cr%65dential=..` → `MissingFields` (jalur presigned), `?x=1;retention` juga lolos filter. Sesudah: 22 varian query/path + 2 varian header → 403 filter (`g.s3gw_encoded_variants_blocked`), ListObjectsV2 dengan nilai ber-`%` tetap diteruskan (`g.s3gw_encoded_value_get_forwarded`), WAL-G `backup-push` + `wal-push` (archived naik, failed 0), BFF PUT + ocr GET (smoke `b`) lolos |
| SEC-42 (efn-deploy) | **Fixed (kode + uji container); host belum dipasang** | manifest bertanda tangan + `cosign verify` image di host + pin root CA + anti-replay/downgrade; `scripts/host/test-efn-deploy.sh` 32/32 PASS (cosign palsu); uji mutasi (cek pin root dihapus) → FAIL terdeteksi |
| SEC-47 (supply chain) | **Fixed** | semua `FROM` (7 Dockerfile) + 22 `image:` compose + image CI/smoke dipin `tag@sha256`; Dependabot `docker` (+ `/infra/minio` baru) dan `docker-compose` memperbarui. Build ulang semua image lokal sukses; `odoo:19.0@sha256:dd9013…` = base image dev yang sedang berjalan (layer pip CACHED) |
| SEC-48 (root MinIO) | **Fixed + terverifikasi** | secret `minio_root_user` (`akid`, 20 hex) → `MINIO_ROOT_USER_FILE`; `efnadmin` dihapus dari compose/.env.example/deploy.sh. Volume lama: minio healthy, minio-init exit 0, 4 user IAM utuh, objek pg-backup terbaca, `efnadmin` ditolak |
| Tambahan lead: sink audit | **Selesai, diuji dengan baris sintetis** | Promtail → `stream="efn_audit_sink"`; ruler Loki → Alertmanager (mTLS): `EfnAuditUnexpectedPurge` (critical) dan `EfnAuditControlChange` (warning) diterima; purge `retensi:` tidak memicu alert. Retensi stream 400 hari (`retention_stream`) |

## G11.2 SEC-41 — aturan (`infra/s3-gateway/haproxy.cfg`)
1. Tolak `;` literal di query (semua method) dan `%` di **nama** kunci query (semua method); tolak `%` di mana pun
   di query untuk PUT/POST/DELETE (nilai tulis sah hanya uploadId/partNumber/versionId URL-safe). Double-encoding
   (`%2572…`) karenanya ditolak.
2. Daftar blok (`retention|legal-hold|object-lock|versioning|lifecycle|policy|encryption|replication|notification|acl`,
   `select`, `action`, `x-amz-credential|signature|algorithm`) dicocokkan pada `query,url_dec`, `-i`, pemisah `[&;]`.
3. Path: `path,url_dec` + `-i` untuk `/minio/*` (sebelumnya `/%6Dinio/admin` diteruskan ke storage); segmen
   `/./`, `/../`, `//` ditolak; `p_root` = `^/+$`.
4. Header: `x-amz-content-sha256` ganda ditolak (Go `Header.Get` membaca kemunculan pertama). Body form
   `application/x-www-form-urlencoded` berisi `X-Amz-Credential`/`retention` **diuji tidak** dirutekan SILO sebagai
   presigned/retensi (AccessDenied biasa), jadi tidak diblok.
5. Path-style vs virtual-host: `MINIO_DOMAIN` tidak diset → Host `pg-backup.s3-gateway` tetap path-style (diuji).
   Komentar di cfg: bila MINIO_DOMAIN diaktifkan, `p_root` wajib ditinjau.
6. `log-format` + `%Ta`. `s3gw()` di smoke kini `--path-as-is` (tanpa itu curl menormalkan `..`).

## G11.3 SEC-42 — desain
- `deploy.yml`: langkah bundel menulis `efn-release.json` (schema 1: `app_env`, `version`, `seq` = `run_number`,
  `git_sha` (= `workflow_run.head_sha` bila ada), `signer_ref`, 7 `images`, `pki.root_sha256`,
  `pki.extra_trust_sha256[]`) **ke dalam** `release.tar.gz` sebelum `cosign sign-blob`; `gzip -n` (deterministik).
  `IMAGE_*` tidak lagi dikirim lewat env.
- `efn-deploy`: kunci conf baru wajib `EFN_APP_ENV`, `EFN_GITHUB_REPO`, `EFN_IMAGE_PREFIX`, `EFN_PKI_ROOT_SHA256`
  (+ opsional `EFN_PKI_EXTRA_TRUST_SHA256`). Urutan: verify-blob → ekstrak ke `releases/<sha>.tmp` → manifest
  (skema, env, versi/ref, anti-downgrade terhadap `/var/lib/efn/last-release`) → env allowlist (tanpa `IMAGE_*`;
  `APP_ENV` = conf) → trust anchor (root tepat 1 sertifikat = pin = manifest; intermediate `openssl verify
  -x509_strict` ke root dipin + CA:TRUE; setiap EXTRA_TRUST dipin **dan** ada di manifest) → `cosign verify` tiap
  image di host (identitas tepat `build-push.yml@<signer_ref>`; kredensial pull di tmpfs `DOCKER_CONFIG`, bukan
  argv) → baru sinkron `/opt/efn`, tulis state, exec `deploy.sh`. Downgrade sah: `/etc/efn/allow-downgrade` berisi
  sha256 arsip (root 0600), sekali pakai.
- `deploy.sh`: lapis kedua — root CA wajib cocok `EFN_PKI_ROOT_SHA256` (dari wrapper atau dibaca deploy.conf saat manual).
- Runbook: `key-custody.md` §4a (conf baru) + §4b baru (tabel cek, rollback, rotasi root, risiko residu secret env),
  `internal-pki.md` rotasi root (pin sebelum deploy transisi).
- CI `infra`: langkah `efn-deploy regression (cosign palsu)` + shellcheck `scripts/host/*.sh`.

## G11.4 Sink audit Odoo (permintaan lead)
- `promtail.yml`: `match {service="odoo"} |= "odoo.addons.efn_audit.sink: EFN_AUDIT_SINK "` → regex + json →
  label `stream=efn_audit_sink`, `audit_op`. Baris yang memuat teks `EFN_AUDIT_SINK` tanpa prefiks logger (mis. URL
  di access log) **tidak** masuk stream (diuji).
- `loki.yml`: `retention_stream` `{stream="efn_audit_sink"}` 9600h (400 hari > minimum 365 hari log kontrol Odoo);
  stream lain 30 hari. Ruler lokal (`infra/observability/loki-rules/efn-audit-sink.yml`, mount read-only),
  `enable_api: false`, Alertmanager `https://alertmanager:9093/alertmanager` dengan mTLS (sertifikat loki).
- PKI: `loki|both` (EKU clientAuth; nama sama → **tidak** perlu intermediate baru, leaf terbit ulang otomatis).
  Alertmanager kini memakai `web-config-alertmanager.yml` (allowlist prometheus, grafana, **loki**);
  `web-config-ui.yml` (Prometheus) tidak berubah → sertifikat loki tidak bisa meng-query Prometheus.
- Alert: `EfnAuditControlChange` (warning, operation ≠ purge), `EfnAuditUnexpectedPurge` (critical, note bukan
  `retensi:`/`retention`/`erasure:`), `EfnAuditRetentionPurgeBurst` (warning, > 5 purge retensi/jam). Runbook
  `docs/runbooks/audit-sink-alerts.md`. Odoo menulis `retensi:` (bukan `retention`) — regex menerima keduanya.
- Diuji dengan container sementara berlabel `service=odoo` yang mencetak baris write param, purge `x 12`, purge
  `retensi: 3 log`: series Loki `audit_op=write|purge`, Alertmanager menerima tepat 2 alert yang benar.
  **Belum** diuji dengan Odoo nyata (modul `efn_audit` gelombang 11 belum di-upgrade di container berjalan; tidak
  saya restart karena Odoo engineer bekerja paralel).

## G11.5 Validasi

| Cek | Hasil |
|---|---|
| shellcheck (daftar CI + `scripts/host/*.sh`) | bersih |
| actionlint 1.7.12 | bersih |
| yamllint `infra .github` | 0 error |
| hadolint v2.12.0 (7 Dockerfile) | lulus (info DL3059 saja) |
| promtool config (25 rule) + 3 web-config, amtool | SUCCESS |
| compose config dev & prod (semua profil), secrets compose == secrets.list, port publish prod = traefik | OK |
| check-encryption.sh (di container docker:27-cli + jq; host tanpa jq) | OK |
| haproxy -c (image CI dipin) | rc 0 |
| update-cloudflare-ips --check | sinkron |
| `test-efn-deploy.sh` | 32/32 |
| Prometheus targets | 33/33 up |
| smoke E2E penuh | lihat G11.6 |

## G11.6 Smoke E2E
Setelah semua perubahan diterapkan (stack di-recreate dengan image ber-digest, root MinIO acak, Loki ruler):
- `g` (negatif enkripsi + s3-gateway): **19/19** (termasuk 24 varian encoded dan kontrol positif), dua kali.
- `a`, `b` (login OTP, consent, Price Hunter upload lewat s3-gateway, access key lama ditolak, SSE-S3 dengan root
  acak): semua lulus; `d` (QRIS) 7/7; `f` (ETL → ClickHouse, Metabase) 5/5; `c` 6 cek POS OTP gate lulus.
- **Terhenti (bukan regresi gelombang ini):** `c.audit_*` dan `e` bagian Odoo (`odoo_partner_*`, `audit_partner`)
  gagal karena kode `efn_audit` 19.0.1.1.0 milik Odoo engineer (pekerjaan paralel, kolom baru `prev_company_id`)
  sudah ter-mount tetapi DB masih 19.0.1.0.0 (belum `-u efn_audit`): `odoo shell` → `UndefinedColumn
  prev_company_id`. Bagian platform `e` (DSAR completed, `member_hash` NULL) lulus. Jalankan ulang `c e` setelah
  upgrade modul. `e.odoo_partner_hash_matches_bff` juga butuh partner E dari skenario `c` di run yang sama.


## G11.7 File
`infra/s3-gateway/{haproxy.cfg,README.md}`, `scripts/smoke/run-e2e-smoke.sh`, `scripts/host/efn-deploy`,
`scripts/host/test-efn-deploy.sh` (baru), `scripts/deploy.sh`, `scripts/secrets.list`, `scripts/gen-pki.sh`,
`.github/workflows/{deploy.yml,ci.yml}`, `.github/dependabot.yml`,
`services/{odoo,api-gateway,superapp-web,ai-ocr-worker,etl}/Dockerfile`, `infra/minio/{Dockerfile,minio-init.sh}`,
`infra/backup/postgres/Dockerfile`, `infra/compose/{docker-compose.yml,.env.example}`,
`infra/observability/{loki.yml,promtail.yml,web-config-ui.yml,web-config-alertmanager.yml (baru),loki-rules/efn-audit-sink.yml (baru)}`,
`docs/runbooks/{key-custody.md,internal-pki.md,secret-rotation.md,dev-stack-boot.md,audit-sink-alerts.md (baru)}`.

## G11.8 Sisa / butuh pihak lain
- **Host staging/prod (Superadmin):** pasang ulang `/usr/local/sbin/efn-deploy` (sha256 dari PR), tambah kunci baru
  di `/etc/efn/deploy.conf`; GitHub Environment butuh secret baru `MINIO_ROOT_USER` (deploy.sh menolak bila kosong).
  Rilis pertama: `last-release` belum ada → seq apa pun diterima.
- cosign/Rekor/Fulcio asli belum diuji end-to-end (hanya cosign palsu); `cosign verify` image privat GHCR dengan
  `DOCKER_CONFIG` tmpfs belum dicoba di host nyata.
- Secret dari env tetap tidak bertanda tangan (risiko residu, key-custody §4b); log `efn-deploy` (journald host)
  belum dikirim ke Loki — Promtail hanya membaca log container.
- Alert sink perlu diuji dengan Odoo nyata setelah upgrade `efn_audit` (lead/QA). `docs/04` §8.5 (matriks mTLS)
  perlu baris loki → alertmanager (dokumen architect).
- Makefile `lint` memakai `hadolint/hadolint` tanpa tag (di luar batas file).
- `infra/compose/.env` lokal developer lain mungkin masih memuat `MINIO_ROOT_USER=efnadmin` (diabaikan).


# Gelombang 12 — SEC-37 ACME terpisah, E12 ocr-worker, alert log, ops kecil (7 Okt 2026)

Basis: `59d37a5`. Diuji di stack dev nyata (Docker Desktop/WSL2) kecuali yang ditandai. Tidak ada commit.
Recreate di stack dev (UTC): 03:31 `acme-dns-init`, `acme-dns`, `traefik`, `superapp-web` (+ sidecar
`traefik-metrics-tls`, `superapp-web-tls`); 03:33 `odoo`, `metabase`, `grafana`, `uptime-kuma` (+ `odoo-tls`,
`odoo-ws-tls`, `metabase-tls`) agar label router baru berlaku; 03:37 `ocr-worker` (rebuild image dev) + 2 sidecar baru;
03:46 `etl`, restart `prometheus`, `blackbox-exporter`, `loki` (03:54 recreate `loki-tls` — restart loki memutus
namespace sidecar ±8 menit, promtail sempat 500 `empty ring`/connection refused).

## G12.1 Status

| Item | Status | Bukti |
|---|---|---|
| SEC-37 ACME terpisah dari Traefik | **Fixed (kode + uji Pebble); LE/Cloudflare nyata belum** | `scripts/test-acme-dns.sh` 10/10 PASS (8 run berurutan lulus; 1 run sebelumnya gagal tanpa detail tercatat → batas tunggu hot-reload dinaikkan 30→60 s); CI: Traefik tanpa token, `acme-dns` tanpa port + hanya `egress` |
| E12 ocr-worker `/metrics` | **Ditutup** | dari network `:9100`: CN prometheus 200, grafana/ocr-worker/tanpa sertifikat ditolak (000), `:9101` tidak terjangkau; Prometheus target `ocr-worker` up |
| SEC-22 sisa (`data.debug`) | **Didokumentasikan, tidak bisa ditutup di infra** | Odoo 19 `serialize_exception` selalu mengisi `debug`, tanpa opsi konfigurasi; usulan override `efn_*` (Odoo engineer) di `odoo-access-changes.md` §2 |
| Alert log (lead) | **Selesai + diuji** | `EfnAuditReconAnomaly`, `EfnAuditCopyFailing`, `EfnS3GatewayDeniedBurst` diterima Alertmanager (container sintetis berlabel `service=etl` / `service=s3-gateway`); `EfnAltchaMaxLevelSpike` (Prometheus, promtool OK, belum firing nyata) |
| ETL GL/audit copy env | **Compose + deploy.sh siap; job belum jalan di dev** | env `true` di container etl, tetapi image `efn/etl:dev` belum di-rebuild dengan kode data-AI (job GL/audit tidak muncul di log). Rebuild setelah `-u efn_audit` 19.0.1.2.0 + grant |
| Ops kecil | Selesai | lihat G12.4 |

## G12.2 SEC-37 — desain
- `acme-dns-init` (one-shot, root CAP_CHOWN+FOWNER, `network_mode: none`) → `acme-dns` (`goacme/lego:v5.5.2@sha256:1944e8…`,
  `infra/acme/acme-dns.sh`, user 65532:0, read-only, cap_drop ALL, `init: true`, hanya `egress`, secret
  `acme_dns_api_token` via `<ACME_DNS_TOKEN_ENV>_FILE`). `lego run` tiap 12 jam (gagal: 1 jam), satu sertifikat SAN
  erp./bi./ops./status., EC256, `--force-cert-domains`. Mode `disabled` (dev/CI) = idle sehat.
- Volume `acme-dns-certs` di-mount Traefik **read-only sebagai `/etc/traefik/dynamic`**; `base/` (dan `dev/`) bind
  mount di dalamnya. Ditemukan saat uji: watch provider file Traefik **tidak rekursif** — layout awal (volume di
  subdirektori `acme/`) gagal hot-reload. Nama file berversi (`efn-wg-<sha16>.crt`) karena Traefik tidak membaca ulang
  isi file berpath sama. Kunci 0640 grup 0 (Traefik root tanpa CAP_DAC_*).
- Traefik: resolver `letsencrypt-dns` dihapus, 7 router wg-https `tls.certresolver` → `tls: "true"`; secret dan env
  token dihapus. `deploy.sh`: `ACME_SERVER` (hanya URL LE prod/staging), `.env` + `ACME_DNS_PROVIDER`, sed provider di
  traefik.yml dihapus; `efn-deploy` allowlist + `deploy.yml` var `ACME_SERVER`.
- Monitoring: blackbox module `tls_wg_public` + job `probe-wg-tls` (`traefik:8443`, SNI `erp.<domain>`, CA publik;
  deploy.sh mengganti domain) → `EfnCertExpiringSoon/Critical` kini juga untuk wg-https + alert baru
  `EfnWgCertProbeFailed` (penerbitan pertama gagal). Dev: probe gagal (self-signed), sama seperti probe-https.
- Fixture uji di `scripts/acme-test/` (bukan `infra/`: check-encryption menolak URL http challtestsrv; tidak di-mount compose).

## G12.3 E12 — ocr-worker
Worker tetap HTTPS+mTLS (kode data-AI menolak plaintext di produksi) tetapi `METRICS_BIND_ADDRESS=127.0.0.1`,
`METRICS_PORT=9101`. ghostunnel tidak bisa meneruskan ke backend TLS, jadi dua sidecar di namespace worker:
`ocr-worker-metrics-tls` (server :9100, `--allow-cn=prometheus`) → 127.0.0.1:9102 → `ocr-worker-metrics-bridge`
(client, `--verify-cn=ocr-worker`, sertifikat klien ocr-worker) → 127.0.0.1:9101. `check-encryption.sh` kini juga
memeriksa ghostunnel mode client (`--cacert`, `--verify-cn`, listen loopback, tanpa `--disable-authentication`/
`--unsafe-listen`). Alternatif lebih sederhana (satu sidecar) butuh worker mengizinkan plaintext loopback di produksi
— keputusan data-AI/lead. ADR-022 E12 & docs/04 §8.5 perlu diperbarui (architect).

## G12.4 Ops kecil
- `superapp-web` dev: diukur 330 MiB setelah start → 477 → 487 MiB/512 (95 %) setelah 6–9 route `next dev`
  dikompilasi; limit dev 1024M (base/prod tetap 512M).
- `Makefile lint`: hadolint `v2.12.0@sha256:30a8fd…`, shellcheck `v0.11.0@sha256:61862e…`, `--config .hadolint.yaml`,
  daftar file = CI (+ infra/minio, scripts/host, acme).
- Runbook `odoo-access-changes.md`: ubah grup lewat UI/RPC, bukan `odoo shell`; §2 traceback JSON-RPC.
- journald host → Loki: service `promtail-journal` (prod override saja, `infra/observability/promtail-journal.yml`,
  `/var/log/journal` + `/etc/machine-id` ro, hanya `efn-deploy`, `efn-backup`, `efn-restore-test`, label `service=host-*`,
  retensi 400 hari). `promtail -check-syntax` OK; **belum diuji di host Linux nyata**.
- ETL: `ETL_ENABLE_GL`, `ETL_AUDIT_COPY_ENABLED` (base default false, dev true); `deploy.sh` pola R5 (eksplisit >
  .env lama > false; aktif otomatis setelah grant sukses) + pra-cek kolom `efn_audit_log.origin_user_id` dengan pesan
  jelas sebelum grant. `efn-deploy` allowlist + `deploy.yml` vars.
- Loki: `loki-rules/efn-ops-logs.yml` (3 alert); `alert-rules.yml` + `EfnAltchaMaxLevelSpike`, `EfnWgCertProbeFailed`.
- Runbook baru: `etl-audit-copy.md`, `s3-gateway-denied.md`, `host-journal-loki.md`, `odoo-access-changes.md`;
  diperbarui: `acme-dns-token.md` (§3–§5 baru), `tls-cert-expiry.md`, `otp-failure-rate.md` (ALTCHA), `README.md`
  (indeks: semua 25 runbook + alert Loki, tabel pengecualian hardening).

## G12.5 Validasi

| Cek | Hasil |
|---|---|
| shellcheck (daftar CI + acme) | bersih |
| actionlint 1.7.12 | bersih |
| yamllint `infra .github` | 0 error (1 warning lama line-length baris 416) |
| hadolint v2.12.0 (7 Dockerfile) | lulus |
| promtool config (27 rule) + 3 web-config; amtool | SUCCESS |
| compose config dev & prod (semua profil); secrets compose == secrets.list; port publish prod = traefik | OK |
| check-encryption.sh (container docker:27-cli) | OK |
| haproxy -c | rc 0 |
| update-cloudflare-ips --check, derive-secrets --self-test | OK |
| test-efn-deploy.sh | 32/32 |
| test-acme-dns.sh (baru, CI) | 10/10 |
| Prometheus targets | 34 up, 0 down |
| Smoke E2E `a b g` | **57/57** (`a g` saja: 39/40 — `g.pg_clients_tls` butuh koneksi efn_ocr yang hanya ada setelah `b`; bukan regresi) |

## G12.6 Sisa / butuh pihak lain
- **Superadmin**: delegasi zona + token terbatas (runbook §1), uji perdana dengan `ACME_SERVER` staging LE (§4).
  Belum ada uji dengan Let's Encrypt/Cloudflare nyata.
- **Odoo engineer**: override `data.debug` untuk non-`group_system` (SEC-22 sisa).
- **Lead/Odoo/data-AI**: urutan dev: `-u efn_audit` 19.0.1.2.0 → grant `odoo-readonly-grants.sql` → rebuild/recreate
  `etl` (job GL + audit copy). Sebelum grant, `EfnAuditCopyFailing` akan firing di dev.
- **Architect**: ADR-022 E12 → ditutup (pola 2 sidecar); docs/04 §8.5 baris ocr-worker; ADR-023 catatan acme-dns.
- Git: file `infra/acme/*.sh` dipanggil lewat `/bin/sh <file>` (tidak butuh bit exec); hook uji disalin + chmod di
  volume uji.
- `promtail-journal` belum diuji di host nyata; `EfnAltchaMaxLevelSpike` belum pernah firing nyata.


# Gelombang 13 — SEC-50/51/52/53/54, integrasi G13 (Odoo/data-AI/BFF), load test Fase 1 (7 Okt 2026)

Basis: `67039db` (+ commit G13 agent lain s.d. `67f4462`). Diuji di stack dev nyata. Tidak ada commit.

## G13.1 Status
| Item | Status | Bukti |
|---|---|---|
| SEC-50 env deploy | **Fixed (kode + uji)** | `test-efn-deploy.sh` **74/74** (42 kasus baru) |
| SEC-51 selector sink | **Fixed (infra)** | regex berjangkar format v2 + label `audit_op`,`critical`; uji container sintetis: 2 baris sah masuk, 5 palsu (substring di pesan login, logger `sinkx`, level WARNING, v1, tanpa `log_id`) ditolak; alert Critical/Control/Unsigned diterima Alertmanager, purge palsu TIDAK memicu alert |
| SEC-52 butir 1 dead-man | **Fixed** | 5 rule heartbeat ETL (`efn-etl-heartbeat`), `absent_over_time` dibuktikan (jendela 5 s → 1, 1 h → kosong) |
| SEC-52 butir 2 jangkar 400 hari | **Fixed** | `stream="efn_audit_anchor"` (chain_head, recon) retensi 9600h; kepala rantai dikecualikan dari redaksi HP (terbukti: hash memuat `0812345678` utuh); perbandingan otomatis = data-AI |
| SEC-53 acme-dns | **Fixed (kode + Pebble)** | service `acme-publish` (network none); `test-acme-dns.sh` 16/16 (PEM berisi router YAML ditolak, kunci multi-blok ditolak, `base/` tak bisa diubah) |
| SEC-54 CGNAT | **Mitigasi infra + dokumentasi** | `OTP_RL_SOURCE_LIMIT` 20→60; `::ffff:` sudah dinormalkan BFF (`common/net/client-ip.ts`); level berbasis rasio gagal = backlog BFF |
| Load test | **Selesai** | `tests/load/` k6; 5 menit, 200 771 req, error 0 % (di luar 404 transisi Traefik) |

## G13.2 SEC-50 — desain
- `scripts/lib/deploy-validate.sh` (baru): satu sumber aturan, di-source `efn-deploy` (dari rilis TERVERIFIKASI, sebelum
  `/opt/efn` ditulis) dan `deploy.sh` (lapis 2 + jalur manual).
- Topologi (`TRAEFIK_DOMAIN`, `EFN_PUBLIC_BIND`, `EFN_WG_*`, `COMPOSE_PROFILES`, `EFN_ALLOW_CONTROL_OFF`) dipin di
  `/etc/efn/deploy.conf`; env pemanggil dibuang. Konfigurasi aplikasi → manifest `config` (schema 2, ditandatangani;
  kunci allowlist, string tanpa karakter kontrol). Stdin hanya GHCR + `EFN_PKI_*` + secret; nama lain → ditolak dengan
  pesan SEC-50. `APP_ENV` = `EFN_APP_ENV`.
- Validasi: WG bind di 10.88/16|10.89/16 (+ `deploy.sh`: harus ada di `wg0`); CIDR kanonik dalam 10.88.0.0/15, prefix
  ≥ /16; public bind ≠ 0.0.0.0/loopback/subnet WG; regex per variabel; production menolak `RABBITMQ_STRICT_PERMISSIONS=
  false`/`ETL_AUDIT_COPY_ENABLED=false` (kecuali `EFN_ALLOW_CONTROL_OFF`), profil wajib `core,data,observability`.
  `.env` ditulis lewat `efn_write_dotenv` (kunci unik, tanpa kontrol, jumlah baris diverifikasi).
- `deploy.yml`: `CFG_*` → `config` (jq `$ENV`, cek kontrol di CI); step deploy hanya mengirim secret.
- sha256 `scripts/host/efn-deploy` saat laporan ini: `9c2bb1b0074408d1f094094d6d05f720c5e80c908e9b3221a218ad930f0ea273` (cocokkan ulang di PR).

## G13.3 Integrasi permintaan lead (Odoo / data-AI / BFF)
- Secret baru `efn_audit_sink_key` [SA] (odoo + etl, `EFN_AUDIT_SINK_KEY_FILE`), `metabase_admin_password`,
  `migration_api_key` (api-gateway, `MIGRATION_API_KEY_FILE`; `MIGRATION_ENABLED` default false, prod via manifest
  `config`, peringatan di deploy.sh bila true). `/internal/*` tetap dikecualikan router Traefik (dev: 404, diuji).
- PKI klien baru `etl` (→ loki-tls `--allow-cn=etl`, loki ikut network `data`; diuji: dengan sertifikat 200, tanpa
  sertifikat ditolak) dan `metabase-provision` (→ metabase-tls). **Staging/prod: terbitkan ulang intermediate.**
- Metabase: `build: ../metabase`, image `IMAGE_METABASE` (image ke-8: build-push matrix, deploy.yml digest+manifest,
  efn-deploy `IMAGE_VARS`, deploy.sh). One-shot `metabase-provision` (python ber-digest, `-I -B`, user 65534,
  read-only, network internal, mTLS) jalan otomatis begitu metabase(+tls) sehat; deploy.sh memeriksa exit 0. Dev: exit 0
  (14 kartu `completed`). Sidecar `metabase-tls` ikut di-recreate pada `up --force-recreate` (network_mode service).
- Heartbeat ETL: rule `efn-etl-heartbeat` (cycle/sales_fact_pos 45m, audit_copy 1h, stock_snapshot 3h, audit_recon 26h
  critical); rule G13 awal berbasis `EFN_AUDIT_CHAIN_HEAD` diganti agar tidak dobel.
- deploy.sh menerapkan `05-platform-loyalty-pos.sql` lewat pola ≥ 04 yang ada (sebelum `up`). Dev volume lama belum
  punya tabelnya → `earn` 500 di load test pertama; diterapkan manual 06:37 UTC (idempoten).
- CI: langkah `pytest + ruff infra/metabase` (23 passed lokal); hadolint-action (2.12) diganti hadolint v2.14.0
  ber-digest (2.12 tidak mengenal `ADD --checksum`); DL3062 minio di-ignore dengan alasan; shellcheck `-x` +
  `scripts/lib`, `tests/load`; cek EKU klien `etl`, `metabase_provision`.

## G13.4 Load test (detail: `tests/load/README.md`)
p95: login otp/request 195 ms, POS otp/request 158 ms, balance 118 ms, earn 170 ms, scout upload 413 ms; p99 ≤ 518 ms;
±669 req/s. Bottleneck: api-gateway CPU ±128 % (event loop, mode dev), postgres 75 % rata-rata, rabbitmq puncak
315 %, promtail puncak 200 % (log per request). Temuan: (1) Traefik merutekan container baru 20–25 s setelah recreate
(404 transisi) — relevan untuk deploy rolling; (2) load test menghabiskan budget OTP global per jam → `run.sh` kini
membersihkan `rl:otp:budget:*` (dev). Belum: image produksi, verify OTP sukses, redeem.

## G13.5 Validasi
| Cek | Hasil |
|---|---|
| shellcheck v0.11.0 (`-x`, daftar CI + lib + tests/load + acme sh) | bersih |
| hadolint v2.14.0 (8 Dockerfile) | lulus (info DL3059) |
| actionlint 1.7.12 | bersih |
| yamllint | 0 error (5 warning line-length regex) |
| promtool rules (27) / promtail -check-syntax / loki -verify-config | OK |
| compose config dev & prod semua profil; secrets compose == secrets.list; publish prod = traefik | OK |
| check-encryption.sh (docker:27-cli) | OK |
| test-efn-deploy.sh | 74/74 |
| test-acme-dns.sh | 16/16 (2 run berurutan) |
| pytest infra/metabase | 23 passed |
| Prometheus targets | 34/34 up |
| Smoke E2E `a b g` | 57/57 |
| Ruler Loki | semua rule G13 dievaluasi tanpa error |

## G13.6 Restart stack dev (UTC)
05:37–05:42 promtail crash loop (selector match pakai backtick → promtail tidak menerima; diperbaiki, log container
periode itu mungkin tertunda); 05:57 recreate `acme-dns-init`, `acme-dns`, `acme-publish` (baru), `traefik`,
`traefik-metrics-tls`; 06:25 `gen-secrets.sh` → **CA dev dibuat ulang** (service PKI baru) + semua leaf; 06:26–06:28
`up -d --force-recreate` SEMUA profil; 06:33–06:47 api-gateway di-recreate beberapa kali oleh load test; 06:37 migrasi
05 di PG dev; 06:57 hapus `rl:otp:budget:*` di Redis dev; ±06:50 recreate `acme-publish`. Container uji sintetis
`g13-fake-*` (label service=odoo/etl) menulis baris palsu ke Loki dev (log_id 99000x/99010x, company 990/991) — abaikan
saat investigasi dev; memicu EfnAuditControlChange/CriticalAccessChange/SinkUnsigned di Alertmanager dev.

## G13.7 Sisa / butuh pihak lain
- **Superadmin (host):** pasang ulang `efn-deploy`; tambah kunci topologi ke `deploy.conf` (key-custody §4c); secret
  GitHub baru `EFN_AUDIT_SINK_KEY`, `METABASE_ADMIN_PASSWORD`, `MIGRATION_API_KEY`; vars `METABASE_ADMIN_EMAIL`,
  `MIGRATION_ENABLED`; terbitkan ulang intermediate (etl, metabase-provision). Rilis schema 1 ditolak.
- **BFF:** level ALTCHA dari rasio kegagalan + kuota IP CGNAT (SEC-54 butir 2–3).
- **data-AI:** pakai `LOKI_URL`/`LOKI_CLIENT_*` (CN etl) untuk verifikasi `mac` sink v2 + perbandingan kepala rantai antar
  hari (SEC-52 butir 2–3). `sales_fact_pos` alert akan firing bila `ETL_ENABLE_ODOO=false` di production.
- **Odoo:** baris sink v1 (efn_audit < 19.0.1.3.0) tidak lagi masuk stream audit — pastikan modul ter-upgrade sebelum
  deploy G13.
- Belum diuji: host Linux nyata (wg0, cosign asli), LE/Cloudflare nyata, load test image produksi.

# Gelombang 13c — Penutupan infra loyalty (SEC-61/65), 2026-10-07

Dijalankan bersama peran QA (satu agent). Tanpa commit. Detail runtime: `docs/testing/E2E_SMOKE_REPORT.md` §G13c.

## G13c.1 Perubahan
- `scripts/deploy.sh`: migrasi `>= 04` sudah otomatis lewat glob (06 ikut); ditambah **cek wajib** `04/05/06-*.sql` ada
  di bundel sebelum loop (bundel tidak lengkap = deploy gagal, bukan dilewati diam-diam).
- `infra/compose/docker-compose.yml`: api-gateway `NO_COLOR: "1"` (log NestJS tanpa ANSI). Dev override:
  `LOYALTY_RECON_INTERVAL_MS: "300000"` (rekonsiliasi 5 menit agar teramati smoke).
- `infra/observability/promtail.yml` tahap 4: regex berjangkar penuh (`[Nest] <pid>  - <waktu> WARN|LOG|ERROR
  [PosLoyaltyService|LoyaltyReconService|MigrationService] EFN_LOYALTY_* {json ber-skema}`, hanya `service=api-gateway`)
  → `stream="efn_loyalty"`, label `loyalty_event` (anomaly|recon|migration), `kind`, `severity`, `recon_status`.
- `infra/observability/loki.yml`: retensi stream `efn_loyalty` 400 hari (bukti fraud/rekonsiliasi Finance).
- `infra/observability/loki-rules/efn-loyalty.yml` (baru): `EfnLoyaltyAnomalyCritical` (critical → page, per kind,
  15 m), `EfnLoyaltyAnomalyWarning` (warning, ringkasan per kind 1 j, satu notifikasi lewat group_by alertname),
  dead-man `EfnLoyaltyReconMissing` (`absent_over_time` heartbeat ok 2 j, for 15 m), `EfnLoyaltyReconFailing`.
- `infra/observability/alert-rules.yml` grup `efn-loyalty`: `EfnLoyaltyReconStale` dari gauge
  `efn_loyalty_recon_last_success_timestamp_seconds` (`max_over_time[26h] > 0` agar restart/deploy tidak memicu alert
  palsu; max antar replika). `alertmanager.yml`: inhibit Loki-dead-man → gauge-dead-man.
- Runbook baru `docs/runbooks/loyalty-anomaly.md` (+ indeks README); `dev-stack-boot.md` §2d migrasi dev G13c.

## G13c.2 Validasi
- promtool `check config` (28 rule) + `promtool test rules` untuk `EfnLoyaltyReconStale` (2 kasus: stale 2h20m → alert;
  gauge 0 tiga jam → tidak ada alert); amtool `check-config` (5 inhibit); `docker compose config -q` dev & prod;
  yamllint (`.yamllint.yml`, hanya warning panjang baris lama/baru); actionlint 0 temuan; shellcheck `deploy.sh`,
  `run-e2e-smoke.sh` bersih; ruff 0.16.10 check + format `scripts/smoke/` bersih.
- Runtime dev: ruler Loki mengevaluasi 4 rule baru; anomali nyata → Loki → `EfnLoyaltyAnomalyWarning` active di
  Alertmanager (±1 menit); heartbeat recon di Loki dan gauge Prometheus.

## G13c.3 Restart stack dev (UTC)
08:02 SQL 06; 08:03 upgrade Odoo (odoo berhenti ±4 menit); 08:07 recreate api-gateway/odoo/promtail/loki/alertmanager/
prometheus; 08:08–08:10 promtail restart loop (selector salah, diperbaiki); 08:14 api-gateway `MIGRATION_ENABLED=true`;
08:15–08:31 smoke (api-gateway mati ±1 menit di skenario `l`); ±08:33 api-gateway kembali `MIGRATION_ENABLED=false`.

## G13c.4 Sisa
- `EfnLoyaltyAnomalyCritical` belum dipicu nyata; dead-man Loki belum ditunggu 2 jam.
- Log ruler Loki `status=500` pada semua rule (lama juga) meskipun query 200 dan alert terkirim — selidiki (bukan blocker).
- Regex promtail bergantung format waktu locale default Node (`MM/DD/YYYY, h:mm:ss AM`); perubahan format = dead-man
  menyala (didokumentasikan di runbook). Opsi lebih kuat: logger JSON di BFF (backlog BFF).
- CI: `EFN_TOUR_MIN_TESTS` 9 → 15 (usul odoo-engineer) dan `docs/04` §6 `efn.bff.client.get` → `_efn_get` belum diubah
  (di luar batas file).

---

# Gelombang 14 (8 Okt 2026) — loyalty recon ETL, bug redaksi Promtail, cutoff & urutan grant G14

## G14.1 Perubahan
- `infra/observability/promtail.yml`
  - Tahap sink audit (2) dipindah **sebelum** redaksi; redaksi kini `stream!~"efn_audit_anchor|efn_audit_sink"`
    (sink tidak memuat PII by design; redaksi merusak MAC → `sink_mac_invalid` palsu).
  - **Bug PII baru ditemukan saat uji**: stage `replace` Promtail hanya mengganti *capture group*. Ekspresi lama
    `(\+?62|0)8[1-9][0-9]{7,11}` hanya mengganti awalan `0`/`62` → sisa nomor HP **masuk Loki**
    (`[PHONE_REDACTED]81298765432`); ekspresi Bearer tanpa grup **tidak meredaksi apa pun**. Diperbaiki:
    `((?:\+?62|0)8[1-9][0-9]{7,11})` dan `(?i)bearer\s+([a-z0-9\-_\.~+/]+=*)`. Sama di `promtail-journal.yml`.
  - Tahap 5 baru: `EFN_LOYALTY_RECON_ETL` dari `service=etl`, regex berjangkar
    `<ts> ((INFO|WARNING) efn_etl.loyalty_recon|ERROR efn_etl) EFN_LOYALTY_RECON_ETL {"status":"(ok|error)"...}`
    → `stream=efn_loyalty`, `loyalty_event=etl_recon`, label `recon_status`. Catatan: baris error dicetak logger
    `efn_etl` (`__main__`), bukan `efn_etl.loyalty_recon` — regex mengikuti kode.
- `infra/observability/loki-rules/efn-loyalty.yml` grup `efn-loyalty-etl-recon` (5m): `EfnLoyaltyEtlReconAnomaly`
  (critical, `last_over_time(...unwrap anomalies [26h]) > 0`), `EfnLoyaltyEtlReconOpenItems` (warning, open_items),
  dead-man `EfnLoyaltyEtlReconMissing` (`absent_over_time` status ok 26h, for 15m). Angka diambil dengan `| regexp`
  (baris diawali timestamp → `| json` gagal JSONParserErr; regexp juga tidak membuat label kinds_*).
- `infra/compose/docker-compose.dev.yml`: `ETL_LOYALTY_RECON_NOT_BEFORE=2026-10-07T07:09:00Z`.
  `infra/compose/docker-compose.yml` (etl): `ETL_LOYALTY_RECON_NOT_BEFORE: ${ETL_LOYALTY_RECON_NOT_BEFORE:-}`.
- SEC-50: `scripts/lib/deploy-validate.sh` — var masuk `EFN_RELEASE_CONFIG_VARS` (manifest bertanda tangan) dan
  `EFN_SECURITY_CONTROLS`; validasi UTC kanonik `YYYY-MM-DDTHH:MM:SSZ`, masa depan ditolak (cutoff di masa depan =
  rekonsiliasi buta). `scripts/deploy.sh`: nilai manifest → else `.env` lama (pola R5); production menolak
  **memajukan** cutoff tanpa `EFN_ALLOW_CONTROL_OFF` berisi `ETL_LOYALTY_RECON_NOT_BEFORE`; ditulis ke `.env`.
- `scripts/deploy.sh`: sebelum file grant, cek 5 kolom G14 (`pos_order.uuid/efn_loyalty_state`,
  `pos_config.efn_loyalty_earn_enabled`, `efn_pos_loyalty_outbox.original_order_id`,
  `efn_pos_loyalty_reservation.order_uuid`) → `die` jelas "efn_pos_loyalty_ledger belum terpasang (n/5)".
- `scripts/host/test-efn-deploy.sh`: uji 75–79 (format sah, spasi, offset +07:00, masa depan, bulan 13).
- Runbook: `loyalty-anomaly.md` §Rekonsiliasi harian ETL (3 alert + Batas diperbarui); `audit-sink-alerts.md`
  catatan G14 (sink sebelum redaksi; `sink_mac_invalid` lama pada baris berpola nomor = mungkin artefak).

## G14.2 Validasi
| Cek | Hasil |
|---|---|
| promtail `-check-syntax` (promtail.yml, promtail-journal.yml) | OK |
| Promtail **dry-run** terisolasi (docker_sd filter label `efn_test=g14`, tanpa push ke Loki) | sink → `efn_audit_sink` utuh; baris biasa: `+62…`, `62857…`, `0812…` → `[PHONE_REDACTED]`, `62812****789` tetap, Bearer → `Bearer [REDACTED]`; ETL ok/warning/error → `etl_recon` + `recon_status`; logger `efn_etl.other` & sisipan teks ditolak |
| MAC sink setelah pipeline (`efn_etl.sink.parse_sink_line`, image `efn/etl:dev`, kunci dev) | `valid=True`; varian ter-redaksi `valid=False` |
| promtool check rules (28) / amtool check-config | SUCCESS |
| yamllint `infra .github` | 0 error |
| shellcheck v0.11.0 (`scripts/*.sh scripts/lib/*.sh` + host) | bersih |
| `test-efn-deploy.sh` (python:3.12 container) | PASS 79 / FAIL 0 |
| compose config dev & prod (semua profil) | OK; var muncul di etl |
| `scripts/check-encryption.sh` (container docker-cli) | OK |
| Cek kolom G14 terhadap DB dev | 5 (terpasang); kolom fiktif → 0 |
| Runtime dev: baris sintetis `anomalies:2, open_items:1` (container `service=etl`) | `EfnLoyaltyEtlReconAnomaly` (critical) + `OpenItems` diterima Alertmanager ±5 menit |
| Run ETL nyata sekali (`ETL_RUN_ONCE`, NOT_BEFORE aktif) | `anomalies 0, open_items 3` (2 order review + 1 outbox failed, sama dengan data-AI); query nilai terakhir = 0 / 3 |

## G14.3 Restart stack dev (UTC)
16:16 restart `promtail` (config baru); 16:26 recreate `etl` (env NOT_BEFORE); 16:26 & 16:34 run ETL sekali
(`compose run`, container sementara). Container uji `efn-g14-*` sudah dihapus.
**Jejak sintetis di Loki dev**: 1 baris `etl_recon` palsu (container `efn-g14-synth1`, kind `g14_synthetic_test`)
tetap di stream 400 hari (tidak bisa dihapus tanpa delete API); sudah disusul baris nyata sehingga alert anomali
padam pada evaluasi 16:40 (terverifikasi di Alertmanager). `EfnLoyaltyEtlReconOpenItems` (3) tetap menyala = kondisi dev nyata.
Sink audit TIDAK diinjeksi ke Loki (uji dry-run) → tidak ada `sink_*` anomali baru.

## G14.4 Sisa / butuh pihak lain
- **Lead/.github**: `deploy.yml` belum mengirim `CFG_ETL_LOYALTY_RECON_NOT_BEFORE: ${{ vars.ETL_LOYALTY_RECON_NOT_BEFORE }}`
  (di luar batas file). Tanpa itu production memakai nilai `.env` lama / kosong (semua order dalam lookback).
- **PDP/keamanan (baru)**: bug redaksi berarti log Loki ≤ 30 hari (dan journal host) sebelum 8 Okt 16:16 UTC dapat
  memuat nomor HP hampir utuh dan token Bearer. Dev: 34 baris pola `[PHONE_REDACTED]\d{6}` dalam 30 hari. Bila
  staging/production sudah menerima log: nilai sebagai insiden PDP (breach-response-72h.md), pertimbangkan delete
  API Loki / tunggu retensi 30 hari. Stream 400 hari (`efn_loyalty`, `efn_audit_*`) berisi payload ber-skema tanpa HP.
- Dead-man ETL belum ditunggu 26 jam nyata (hanya query `absent_over_time` diverifikasi). Setelah deploy pertama G14
  dead-man bisa menyala sekali (runbook §EfnLoyaltyEtlReconMissing butir 4).
- Data-AI: regexp rule bergantung urutan kunci `status, anomalies, open_items` di `summary_payload` (kontrak; ubah
  bersama). Odoo: `note` sink jangan pernah memuat PII (kini tidak diredaksi).

---

# Gelombang 15 (9 Okt 2026): redaksi log, uji regresi Promtail, token di query string (SEC-72, SEC-73, SEC-78 infra)

## G15.1 Temuan pelacakan sumber (SEC-72a, Loki dev, 30 hari)
Query `sum by (service, container) (count_over_time({container=~".+"} |~ "\[PHONE_REDACTED\]\d{6}" [30d]))`.
Setiap kemunculan diklasifikasi menurut karakter sebelum `[PHONE_REDACTED]`: alfanumerik berarti potongan hex/ID.

| Sumber | Kemunculan | Sifat |
|---|---|---|
| `postgres` (dev `log_statement=all`) | ±300 | **Nomor HP fixture nyata-format.** INSERT `res_partner.phone` / `pos_order.mobile` "SMOKE Member A–N" (fiktif). Odoo menginterpolasi parameter di klien |
| `efn-g14-odoo`, `efn-g14-promtail` | 39 | Baris uji sintetis G14 (fiktif) |
| ocr-worker, api-gateway, s3-gateway, cadvisor, docker-socket-proxy, promtail, loki, etl, container test Odoo | ±200 | **False positive** pola lama: UUID report, ID container, ID thread, durasi, `row_hash` |
| traefik | 0 | Tidak ada HP. Juga tidak ada `token=`/`password=`/`code=`/`signature=`/`key=`/`secret=` di traefik/odoo/BFF/s3-gateway |

Probe R2 `member_ref=` tidak ditemukan lagi di Loki. Odoo werkzeug sudah `WARNING` (`odoo.conf.tpl:26`), sehingga baris
request + query tidak dicatat.

## G15.2 Perubahan
- `infra/observability/promtail.yml` + `promtail-journal.yml` (salinan persis): redaksi baru, urutan (a)–(d):
  - **(a)** parameter rahasia query/logfmt:
    - nama: `token`, `access_token`, `refresh_token`, `id_token`, `auth_token`, `password`, `passwd`, `pwd`,
      `csrf_token`, `secret`, `client_secret`, `api_key`, `apikey`, `signature`, `x-amz-*`, `otp`, `otp_code`;
    - prefiks: non-word, `\u0026` (Traefik JSON **meng-escape `&`**, diverifikasi dengan Traefik v3.7.13 nyata), `%3F`, `%26`;
    - `=` boleh `%3D`.
  - **(a2)** `code`, `key`, `sig`, `auth`, `session_id`, `sid`, hanya sebagai parameter query.
  - **(b)** kunci JSON/dict: `"password": "…"`.
  - **(c)** `Bearer` dan `Basic` (base64 wajib memuat digit/`+`/`/`/`=`).
  - **(d)** HP berpemisah spasi/titik/strip, `(+62)`, `%2B62`:
    - struktur `8[1-9]x(x)[sep]xxx(x)[sep]xxx(xx)`;
    - batas kiri & kanan non-alfanumerik, sehingga UUID/hash/ID tidak lagi terganti dan report_id bisa dikorelasikan lagi;
    - stage dijalankan **2×** karena batas kanan mengonsumsi satu karakter (nomor berurutan).
- `infra/traefik/traefik.yml`: `accessLog.fields.headers.defaultMode: drop` dibuat eksplisit. Komentar mencatat bahwa
  RequestPath memuat query dan redaksinya dilakukan di Promtail. Static config diuji boot dengan `traefik:v3.7.13`.
- **Perbaikan di sumber (SEC-72):** `docker-compose.dev.yml` postgres `log_statement=${PG_LOG_STATEMENT:-ddl}` (sebelumnya
  `all`), `.env.example` `PG_LOG_STATEMENT=ddl`. **Belum berlaku di stack dev yang berjalan**, karena postgres tidak
  di-recreate (dipakai agent lain). Berlaku saat recreate berikutnya.
- **Uji regresi CI baru:**
  - `scripts/test-promtail-redaction.sh`:
    - yq menyalin `pipeline_stages` produksi ke `infra/observability/tests/promtail/harness.yml`;
    - `promtail -dry-run` (image digest compose) atas `fixtures/{odoo,etl,api-gateway,traefik,generic}.txt`, 41 baris
      fiktif; `generic.txt` juga lewat pipeline journal;
    - keluaran dinormalisasi, lalu `diff` dengan `expected.txt` (62 entri).
  - Penjaga independen: tidak ada `SENTINEL*`, keluaran docker == journal, jumlah entri lengkap. `--update` untuk regenerasi.
  - Fixture mencakup:
    - HP format `08…`, `+62…`, `62…`, `+62 812 3456 …`, `62-812-…`, `0812-3456-…`, `0812 3456 …`, `(+62) …`,
      `0812.3456.…`, `%2B62…`, JSON, SQL, nomor berurutan;
    - Bearer, Basic, `access_token=`, `token=`, `password=`, `code=`, `X-Amz-Signature`, `%3Ftoken%3D`, JSON secret;
    - harus utuh: sink audit, jangkar, recon, loyalty, mask `62812****789`, UUID/ID/durasi, prosa.
- `.github/workflows/ci.yml`: job baru `promtail-redaction` (`-check-syntax` kedua config + skrip uji, `contents: read`,
  path-filter `infra`), ditambahkan ke `ci-gate.needs`. Baris modul Odoo tidak disentuh.
- Runbook baru `docs/runbooks/log-redaction.md`:
  - pola dan batasnya;
  - prosedur ubah pola + uji;
  - query deteksi staging/prod;
  - prosedur delete API Loki;
  - status near-miss dev.
- Runbook lain:
  - `security-incident.md:107` dikoreksi: Traefik mencatat query string, nilai rahasia diredaksi, s3-gateway tanpa query;
  - `dev-stack-boot.md` (`PG_LOG_STATEMENT`);
  - `host-journal-loki.md`;
  - `README.md` (indeks).

## G15.3 Validasi
| Cek | Hasil |
|---|---|
| `promtail -check-syntax` (promtail.yml, promtail-journal.yml; 3.2.1) | Valid |
| `scripts/test-promtail-redaction.sh` | OK, 62 entri. **Uji negatif:** pola HP lama dipasang → gagal (diff UUID/ID/thread + journal ≠ docker) |
| Runtime dev | promtail di-recreate 17:46 UTC. Hasil di Loki (dev): |
| — container sintetis `efn-g15-synth` (31 baris fixture) | 0 `SENTINEL`, semua HP/token teredaksi |
| — probe nyata ke Traefik dev `?member_ref=%2B62…&token=SENTINEL…` | `member_ref=%2B[PHONE_REDACTED]\u0026token=[REDACTED]` |
| shellcheck v0.11.0 (`-x`) | bersih |
| actionlint 1.7.7 `ci.yml` | bersih |
| yamllint 1.35.1 `infra .github` | 0 error (warning line-length lama) |
| gitleaks v8.21.2 (`infra/observability`, skrip) | no leaks |
| `docker compose config` dev & prod (semua profil) | OK (dev: `log_statement=ddl`) |
| `scripts/check-encryption.sh` (container docker-cli) | OK |

## G15.4 Sisa / butuh pihak lain
- **Hapus baris lama Loki dev (SEC-72c):** permintaan delete API **ditolak permission classifier di sesi agent**.
  Perintah siap pakai ada di `log-redaction.md` §4 (selector `stream=""`, end 2026-10-08T17:46Z). Pilihannya: operator
  menjalankan perintah itu, atau membiarkan retensi habis (±7 Nov 2026).
- Recreate promtail membaca ulang log container (posisi di tmpfs):
  - entri yang lebih tua ±3 jam ditolak Loki (`too far behind`);
  - entri ±3 jam terakhir terkirim ulang dengan redaksi baru, sehingga muncul duplikat berbeda isi (dev saja).
- Postgres dev perlu `up -d --force-recreate postgres` agar `log_statement=ddl` aktif (koordinasi lead).
- Log mentah container di host (`json-file`) tetap tidak teredaksi. Itu batas Promtail, bukan regresi.
- SEC-78 sisi Odoo (mask `note` purge sebelum MAC) belum. Rule deteksi `{stream="efn_audit_sink"} |~ HP` → warning
  belum dibuat (kandidat G16).
- Sebelum staging pertama menerima log: jalankan query §3 `log-redaction.md` di staging.

# Gelombang 15b (9 Okt 2026): alert KDS, detektor PII sink audit, rate limit self-order (SEC-75/76/78 sisi infra)

Masukan: laporan efn-odoo-engineer G15, bagian "Untuk devsecops". Tidak ada commit.

## G15b.1 Perubahan
- **Alert `EFN_KDS_TICKET_FAILED` (SEC-76).**
  - Ditambahkan tahap **(2b)** di `infra/observability/promtail.yml`. Tahap ini berjalan **sebelum** redaksi dan hanya
    untuk `service=odoo`.
  - Regex berjangkar penuh pada format log Odoo: logger `odoo.addons.efn_pos_kds.alert`, level ERROR, kunci JSON
    `sort_keys` persis.
  - Skema ketat: hanya angka/`null`/boolean, `mode` ∈ {kds, kds_printer, printer}, dan `error` berupa identifier
    Python ≤ 80 karakter.
  - Baris yang cocok masuk `stream="efn_kds"` (`kds_event="ticket_failed"`, label `kds_only`, retensi default 30
    hari).
  - Redaksi **tetap** diterapkan sesudahnya. `promtail-journal.yml` tidak berubah (tahap ini tidak relevan untuk
    journal host).
  - Rule baru `loki-rules/efn-kds.yml`:
    - `EfnKdsTicketFailed`: warning, ≥ 1 kejadian dalam 5 menit, `by (company_id, config_id, kds_only)`;
    - `EfnKdsTicketFailedSpike`: critical, ≥ 10 kejadian dalam 15 menit per company (PLACEHOLDER);
    - `EfnKdsAlertFormatUnrecognized`: warning, untuk baris dari logger alert yang tidak masuk stream (format berubah
      atau teks bebas di `error`). Penjaga ini dipasang supaya alert KDS tidak buta diam-diam.
  - Rule ini menggantikan usulan `|= "EFN_KDS_TICKET_FAILED"` yang tidak berjangkar. Dengan usulan itu, teks tersebut
    di catatan dapur atau pesan login sudah memicu alert.
- **Detektor PII sink audit (SEC-78).** Rule `EfnAuditSinkPiiPattern` ditambahkan di
  `loki-rules/efn-audit-sink.yml` (warning, `team: security`, `by (log_id, model)`, jendela 15 menit).
  - Pipeline:
    1. ambil payload;
    2. buang `"mac": "<hex>"`, karena hex 64 dapat memuat deret mirip HP/NIK;
    3. `| json log_id, model`;
    4. cocokkan HP Indonesia (pola = redaksi tahap (d)), email, 15–16 digit, dan NPWP berformat.
  - Hasil yang diharapkan selalu 0.
- **Rate limit self-order (Traefik).** File baru `infra/traefik/dynamic/odoo-self-order.yml` berisi:
  - router `odoo-pos-self@file`, **hanya** di `wg-https`, prioritas 220;
  - middleware `internal-only` + `secure-headers` + `ratelimit-odoo-pos-self` (5 req/s, burst 40, per RemoteAddr =
    per outlet; PLACEHOLDER);
  - service `odoo@docker` (https + serversTransport CA internal, tidak ada jalur plaintext baru).
  - Path yang ditangkap: `/pos-self/`, `/pos-self-order/`, `/pos_self_order/`, `/kiosk/`. Ini = seluruh 15 route
    `auth="public"` `pos_self_order` CE 19, diverifikasi dari kode di image `efn/odoo:dev`.
  - Komentar file dan runbook mencatat bahwa mode `mobile` dikunci di Odoo (`efn_setup_pos`, parameter
    `efn.pos.self_order_mobile_allowed`, SEC-75). Bila mode itu dibuka, jalurnya lewat BFF/ADR, bukan entrypoint
    publik.
  - Label compose tidak diubah (`infra/compose` di luar batas file). Router file-provider merujuk service docker.
- **Fixture** `tests/promtail/fixtures/odoo.txt` (+5 baris fiktif) dan `expected.txt` (62 → 67 entri):
  - 2 baris KDS valid → `efn_kds`;
  - teks `EFN_KDS_TICKET_FAILED` di catatan dapur → bukan `efn_kds`, HP teredaksi;
  - baris logger alert dengan `error` berisi nama + HP → bukan `efn_kds`, HP teredaksi;
  - baris sink audit berisi HP + email → tetap utuh di `efn_audit_sink`. Ini dokumentasi bahwa sink sengaja tidak
    diredaksi dan bahwa detektor yang menangkapnya.
- **Runbook:**
  - baru: `kds-ticket-failed.md`;
  - `audit-sink-alerts.md` §EfnAuditSinkPiiPattern;
  - `wireguard-outlet.md` §Self-order kiosk + baris troubleshooting 429;
  - `log-redaction.md` (catatan G15b);
  - `README.md` (indeks).

## G15b.2 Validasi
| Cek | Hasil |
|---|---|
| `promtail -check-syntax` (promtail.yml, promtail-journal.yml; 3.2.1) | Valid |
| `scripts/test-promtail-redaction.sh` | OK, 67 entri (docker + journal, 0 SENTINEL). Run pertama sebelum `--update` gagal tepat pada 5 baris baru (diff ditinjau) |
| LogQL di Loki 3.2.1 sekali pakai (baris fiktif, push API) | KDS: warning (company 1/config 3 = 11; company 2/config null = 1), spike company 1 = 11, format = 1 (baris palsu di logger lain tidak terhitung). PII: log_id 2–6 terdeteksi (HP berpemisah, `+62 …`, email, NIK 16, NPWP berformat); log_id 1/7/8 tidak terdeteksi (ter-mask, `mac` berisi deret angka, tanggal, ID kecil, `mac: null`) |
| Ruler Loki dev | rule baru termuat otomatis tanpa restart Loki, tanpa error/warn ruler |
| E2E dev (container sintetis `efn-g15b-synth`, `service=odoo`, company/config 999) | promtail → `efn_kds` → ruler → Alertmanager (mTLS): **EfnKdsTicketFailed, EfnKdsTicketFailedSpike, EfnKdsAlertFormatUnrecognized aktif**. Container dihapus; alert resolve sendiri setelah jendela 5/15 menit |
| `EfnAuditSinkPiiPattern` pada stream sink dev nyata | 0 (jendela 15 menit dan varian 7 hari: 16 baris sink, 0 temuan) |
| Traefik dev (v3.7.13, restart) | 150 POST paralel `/pos-self/ping` → 85×200 + 65×429; `/web/health` (router `odoo`) 150×200; RouterName `odoo-pos-self@file` untuk `/pos-self/1`, `/pos-self-order/get-slots`, `/pos_self_order/kiosk/…`, `/kiosk/payment/…`; `/pos-selfish` dan `/pos/ui` → `odoo@docker` |
| promtool check rules `alert-rules.yml` | SUCCESS, 28 (tidak berubah) |
| yamllint 1.35.1 (loki-rules, promtail, tests, traefik) | 0 error, hanya warning line-length (regex) |
| `scripts/check-encryption.sh` (docker:27-cli) | OK |

## G15b.3 Restart stack dev (UTC, 8 Okt)
- 18:41 dan 18:44: `traefik` (provider file di bind mount Windows tidak hot-reload). Routing kembali dalam ±20 detik.
- 18:45: `promtail`. Baca ulang log: entri lebih tua dari ±1 jam ditolak Loki (`too far behind`), dan sebagian kecil
  entri terkirim ulang (duplikat, dev saja).
- Loki dan Alertmanager tidak di-restart.

## G15b.4 Sisa / butuh pihak lain
- **README `efn_pos_kds` (Odoo engineer):** masih menyebut rule `|= "EFN_KDS_TICKET_FAILED"`. Perlu diganti dengan
  rujukan ke `loki-rules/efn-kds.yml` dan stream `efn_kds`.
- **Kontrak format log:** bila payload `EFN_KDS_TICKET_FAILED` berubah (kunci, separator `json.dumps`, mode stasiun
  baru), regex `promtail.yml` (2b) dan fixture wajib diubah bersamaan. Penjaga `EfnKdsAlertFormatUnrecognized` akan
  menyala.
- **Ambang dan routing:** ambang spike (10/15 menit), rate limit kiosk (5/s, burst 40), dan label `team: ops-outlet`
  masih PLACEHOLDER. Kalibrasi saat pilot KDS. Alertmanager masih punya satu penerima generik.
- **Label compose:** `traefik.http.routers.odoo.*` tidak disentuh. Router file-provider bergantung pada nama service
  docker `odoo`. Bila nama itu diganti di compose, router ini error (terlihat di log Traefik) dan `/pos-self` jatuh ke
  router `odoo` (tetap `internal-only`, limit 50/s).
- **Belum diuji:**
  - kiosk nyata di bawah beban (ambang);
  - path ter-encode (`/pos%2Dself/`). Traefik v3 menolak atau menormalkan sebagian; kasus yang lolos hanya mendapat
    limit `ratelimit-odoo`, bukan bypass allowlist.
- Delete API Loki untuk baris lama (G15.4) masih menunggu operator.

---

# Gelombang 16 — CI GitHub (repo privat, paket gratis) — 9 Okt 2026

Input: run pertama di `bctech-adm/efn-platform` (CI 37857432863, Security 37857433065, Build 37857432866, CodeQL
37857432889). Penyebab gagal: kuota artifact 500 MB, code scanning tidak tersedia, temuan scanner, gate Trivy image,
shellcheck, Dependabot 33 PR. Runbook baru: `docs/runbooks/ci-github-free-plan.md` (+ indeks README).

## G16.1 Perubahan

| Area | File | Isi |
|---|---|---|
| Artifact | `ci.yml`, `e2e-nightly.yml` | upload log hanya `if: failure()`, `continue-on-error: true`, retensi 2-3 hari, `if-no-files-found: ignore` -> kuota habis tidak menggagalkan test |
| Artifact | `build-push.yml` | SBOM Syft tidak lagi artifact (`upload-artifact: false`); di-`cosign attest --type spdxjson` (keyless) ke registry |
| Artifact | `security.yml` | gitleaks `GITLEAKS_ENABLE_UPLOAD_ARTIFACT=false` (temuan tetap redacted di log + summary) |
| Code scanning | `codeql.yml` | analisis hanya bila repo publik / `vars.CODE_SCANNING_ENABLED=true`; selain itu job notice. SAST tetap semgrep |
| Code scanning | `security.yml`, `build-push.yml` | semgrep `--json-output` + `--sarif-output`, ringkasan `file:baris/rule` (tanpa potongan kode) ke summary; Trivy fs/image: tabel gate -> log + summary; SARIF + upload hanya bila code scanning ada. Gate (exit 1) tidak berubah |
| Checkov | `security.yml` | SHA `bridgecrewio/checkov-action@2fb0e6d…` tidak bisa di-resolve lagi -> image `bridgecrew/checkov:3.3.21@sha256:9aefe565…` via `docker run` (tanpa action pihak ketiga) |
| Menit | `security.yml`, `codeql.yml`, `build-push.yml` | `concurrency` cancel-in-progress; izin per job (`security-events` hanya di job SARIF) |
| Menit | `e2e-nightly.yml`, `dast.yml` | jadwal E2E hanya bila `vars.E2E_NIGHTLY_ENABLED=true` (dispatch manual tetap); DAST hanya bila `vars.STAGING_DOMAIN` ada |
| Dependabot | `dependabot.yml` | grup minor+patch per ekosistem/aplikasi, grup security update, `directories` (docker 8 dir, pip 4 dir), limit 1-2, cooldown 7/14 hari, jadwal Senin WIB; tambah `/infra/metabase` |
| Shellcheck | `tests/load/run.sh`, `scripts/smoke/run-e2e-smoke.sh`, `infra/acme/acme-{dns,publish}.sh` | SC2317/SC2329 (trap), SC2015 -> `if`, SC2120 `bal()` tanpa argumen. Bersih di shellcheck 0.9.0 (versi runner) dan 0.11.0 |
| Base image | Dockerfile | node 22.12 (Des 2024) -> `22.23-alpine@0a7108bf…`; python `3.12-slim@05cda977…`; postgres 16.6 -> `16.15-bookworm@0ea6700a…` (+ service ci.yml, smoke, komentar compose); golang 1.27.1 -> `1.27.2-alpine@85dc1069…` (builder minio); metabase 0.52.4 -> `v0.58.35@a84b5dd5…` (LTS) |
| Hardening | `services/{api-gateway,superapp-web}/Dockerfile` | stage dev/build `USER node` (semgrep missing-user); runtime: hapus npm/npx/corepack/yarn (10 CVE HIGH di npm bawaan, tidak dipakai runtime) |
| Hardening | `infra/backup/postgres/Dockerfile` | hapus `gosu` (Go lama, 22 CVE; image selalu USER postgres) + kunci snakeoil ssl-cert (temuan secret Trivy) |
| Metabase | `infra/metabase/Dockerfile`, README | v0.58 membawa driver ClickHouse bawaan -> ADD jar 1.52.0 dihapus |
| Suppression | `.trivyignore.yaml` (baru), `nosemgrep`, `checkov:skip` | semua dengan alasan + `expired_at` (G16.3) |

## G16.2 Validasi lokal (container)

| Alat | Hasil |
|---|---|
| actionlint 1.7.7 | 0 temuan (termasuk info SC2153 lama di deploy.yml, diperbaiki) |
| yamllint 1.37.1 (`.github`, `infra`, `.trivyignore.yaml`) | 0 error (hanya warning line-length lama) |
| shellcheck 0.9.0 + 0.11.0, perintah persis CI | bersih |
| hadolint 2.14.0 semua Dockerfile | lulus (2 info DL3059 lama) |
| semgrep 1.179.0 (konfigurasi CI) | 3 temuan -> 0 (2 Dockerfile diperbaiki, 1 FP Odoo di-`nosemgrep`); flag `--json-output/--sarif-output` diverifikasi |
| checkov 3.3.21 | 4 FAILED -> 0 (3 skip beralasan) |
| Trivy 0.69.3 fs (vuln+secret+misconfig) | 0 dengan `.trivyignore.yaml` (lockfile/requirements di working tree saat ini, yang sedang diubah agent lain) |
| Trivy image: base node setelah strip npm, python slim baru, alpine 3.24, postgres hasil build, metabase hasil build, `odoo:19.0` base | semua 0 HIGH/CRITICAL fixed (postgres & metabase dengan pengecualian G16.3) |

## G16.3 Pengecualian (alasan + tanggal tinjau) — perlu persetujuan lead

- `.trivyignore.yaml`:
  - WAL-G v3.0.9 (`usr/local/bin/wal-g`): 10 CVE (stdlib Go 1.25.11, grpc 1.83.0) s.d. **2026-11-09**. Rilis terbaru
    upstream; klien CLI tanpa listener. Alternatif tanpa ignore: bangun WAL-G dari source dengan Go 1.27.2 (multi-stage,
    cgo libsodium), belum dikerjakan karena jalur backup R-T08 butuh uji restore penuh.
  - CVE-2021-38599 wal-g: FP (pseudo-version `v0.0.0-…+dirty`), s.d. 2027-01-07.
  - Metabase v0.58.35 `app/metabase.jar`: 6 CVE dependency Java upstream (lz4-java, httpcore5, httpcore5-h2,
    mssql-jdbc, zookeeper x2) s.d. **2026-11-09**.
  - Misconfig DS-0002 `infra/metabase/Dockerfile` (root di Dockerfile; `run_metabase.sh` turun ke `su metabase`,
    diverifikasi di image) s.d. 2027-01-07.
- `checkov:skip`: CKV_DOCKER_3 metabase (alasan sama); CKV_GHA_7 di `deploy.yml` & `e2e-nightly.yml` (input dispatch
  tidak memengaruhi build).
- `nosemgrep`: `services/odoo/addons/efn_pos_kds/models/efn_kds_station.py:262`, FP logger-credential-disclosure (yang
  di-log id stasiun/alasan/uid, bukan token). Satu-satunya sentuhan file aplikasi (komentar saja).

## G16.4 Temuan untuk agent lain (tidak diubah)

- `npm audit` api-gateway & superapp-web, `pip-audit` (job Python): dependency aplikasi, sedang dikerjakan agent lain.
- Gate Trivy image tetap gagal bila dependency aplikasi di image rentan: `node_modules` (api-gateway, superapp-web), pip
  `services/{etl,ai-ocr-worker}/requirements.txt`, `services/odoo/requirements.txt` (base `odoo:19.0` sendiri 0 temuan).
- `infra/metabase/provision.py` (efn-data-ai-engineer): verifikasi ulang terhadap Metabase 0.58 + driver ClickHouse bawaan
  (kunci `details` koneksi: `ssl`, `sslmode=strict`, `sslrootcert`).
- Stack dev yang sedang jalan belum di-rebuild dengan base image/Dockerfile baru.

## G16.5 Belum terverifikasi / sisa

- Belum ada run GitHub dengan perubahan ini (tidak di-push). Perlu dilihat di run pertama: ekspresi
  `github.event.repository.visibility`, `skip-setup-trivy` pada step SARIF (hanya jalan bila code scanning aktif), input
  `upload-artifact: false` sbom-action v0.24.3, `cosign attest`.
- Image minio (SILO + mc dari source, Go 1.27.2) tidak dibangun/di-scan lokal; temuan modul Go upstream SILO/mc bisa
  tetap muncul -> lihat summary run Build.
- Image odoo/etl/ai-ocr-worker/api-gateway/superapp-web lengkap tidak dibangun lokal (dependency sedang diubah agent lain).
- Dev stage `USER node`: `start:dev`/`next dev` dengan bind mount read-only belum diuji ulang di stack dev.
- `dependabot.yml` (`cooldown`, `directories`, `applies-to`) belum divalidasi oleh GitHub; error konfigurasi tampil di
  Insights > Dependency graph > Dependabot.
- DAST: `zaproxy/action-baseline` tetap mengunggah artifact `zap_scan` (kuota) bila staging diaktifkan.

## G16.6 Butuh keputusan / paket berbayar

1. Code scanning repo privat = GitHub Advanced Security (berbayar) atau repo publik, lalu set
   `CODE_SCANNING_ENABLED=true`. Tanpa itu CodeQL tidak berjalan (SAST hanya semgrep).
2. Kuota menit 2.000/bulan: E2E nightly penuh butuh runner self-hosted (`E2E_RUNNER`) atau spending limit.
3. Admin repo (sekali, setelah merge): hapus artifact lama dan tutup 33 PR Dependabot lama (perintah di runbook §3, §6);
   pastikan Dependency graph aktif (dependency-review).
4. Persetujuan risk acceptance G16.3 (WAL-G, Metabase) atau perintah membangun WAL-G dari source.
5. Upgrade Metabase 0.52 -> 0.58 (migrasi DB aplikasi tidak bisa dibalik): setujui sebelum dipakai di lingkungan berdata.

# Gelombang 16b — Enkripsi at rest DB aplikasi Metabase — 9 Okt 2026

Temuan (pelanggaran prinsip enkripsi at rest): Metabase v0.58.35 dev berjalan tanpa `MB_ENCRYPTION_SECRET_KEY` →
`metabase_database.details` (termasuk password ClickHouse `efn_metabase`, read-only) plaintext di DB `metabase` dan di
dump backup pra-upgrade.

## G16b.1 Perubahan

| File | Perubahan |
|---|---|
| `scripts/secrets.list` | Secret baru `metabase_encryption_key b64key32 [SA]` (32 byte acak base64). `gen-secrets.sh`, `deploy.sh`, `deploy.yml` membaca daftar ini otomatis → GitHub Environment `METABASE_ENCRYPTION_KEY` (staging/prod) **wajib diisi Superadmin sebelum deploy berikutnya** (deploy.sh `die` bila kosong). |
| `infra/compose/docker-compose.yml` | Secret top-level + mount **hanya** ke `metabase`. Override `entrypoint` shell inline dihapus → ENTRYPOINT image; `MB_DB_PASS_FILE` (didukung `run_metabase.sh` resmi, `file_env`) menggantikan `export MB_DB_PASS=$(cat …)`. |
| `infra/metabase/efn-entrypoint.sh` (baru) | POSIX sh: secret → `MB_ENCRYPTION_SECRET_KEY` → `exec /app/run_metabase.sh "$@"` (perilaku resmi root → `su metabase` uid 2000 dipertahankan; diverifikasi `docker top`: java uid 2000). **Fail-closed di semua lingkungan** (lebih ketat dari brief "selain dev-eksplisit": dev juga punya kunci dari gen-secrets, dan instance yang sudah terenkripsi memang tidak bisa jalan tanpa kunci): file tidak ada/tidak terbaca/kosong/< 32 karakter/berisi CR-spasi, atau kunci diberikan via env → exit 1. Nilai tidak pernah dicetak. |
| `infra/metabase/Dockerfile` | `COPY --chmod=0755 efn-entrypoint.sh` + `ENTRYPOINT ["/usr/local/bin/efn-metabase-entrypoint"]`. |
| `scripts/check-encryption.sh` | Cek baru (compose dev+prod): metabase wajib mount `metabase_encryption_key`, tanpa `MB_ENCRYPTION_SECRET_KEY` di environment, tanpa override `entrypoint`. |
| `.github/workflows/ci.yml` | shellcheck `-s sh` mencakup `infra/metabase/*.sh`. |
| `infra/metabase/README.md` | Baris `efn-entrypoint.sh`; bagian *Enkripsi at rest* (prosedur rotate one-off, verifikasi tanpa mencetak nilai, rotasi berikutnya); backup DB `metabase` kini **pg_dump \| age** (tidak ada dump plaintext di disk); jalur rollback v0.52.4 setelah dump dihapus; item *Belum* diperbarui. |
| `docs/runbooks/key-custody.md` | Baris kustodi `metabase_encryption_key` (Superadmin, keputusan klien 6 Okt). |
| `docs/runbooks/secret-rotation.md` | Baris rotasi `metabase_encryption_key` (rotate-encryption-key, bukan sekadar ganti nilai); `clickhouse_password_metabase` kini lewat `metabase-provision`, bukan UI. |
| `docs/runbooks/dev-stack-boot.md` | §2e migrasi stack dev lama (sekali). |

## G16b.2 Enkripsi data yang sudah ada (stack dev, nyata)

1. `bash scripts/gen-secrets.sh` → `secrets/metabase_encryption_key.txt` (44 karakter, tanpa CR).
2. `build metabase` → `stop metabase-tls metabase` → one-off resmi `java -jar metabase.jar rotate-encryption-key <kunci dari file>`
   (tanpa kunci lama = instance plaintext): log `Encryption key rotation OK.`, exit 0. Log tidak memuat kunci maupun
   password ClickHouse (grep -F -f file secret = 0).
3. `up -d --no-deps --force-recreate metabase metabase-tls` → healthy, restart 0, log `Database encrypted and
   MB_ENCRYPTION_SECRET_KEY correctly configured`.
4. DB (tanpa mencetak nilai): sebelum = `EFN DWH (ClickHouse)` `details LIKE '%password%'` **t**, JSON **t**; sesudah =
   **f / f** (Sample Database juga f/f). `strpos(details|setting.value, <password CH dari stdin>)` = **0** baris.
5. `metabase-provision` dua kali: exit 0 / exit 0, **33/33 kartu `completed`** tiap run (idempoten; PUT koneksi tetap
   tersimpan terenkripsi, dicek ulang f/f).
6. Fail-closed diuji pada image baru: tanpa secret, kunci via env, kunci 5 karakter, kunci ber-CRLF, file kosong →
   semuanya exit 1 dengan pesan jelas.
7. Dump plaintext `scratchpad/metabase-backup/metabase-pre-v0.58.dump` (514 KB) di-`shred -u` + direktori dihapus setelah
   langkah 1–6 lolos (catatan jujur: shred di NTFS/SSD tidak menjamin penghapusan fisik). Tag sementara
   `efn/metabase:pre-g16b-encrypt` dihapus; `efn/metabase:rollback-v0.52.4` dipertahankan (README: rollback = image
   itu + wrapper + DB kosong + provision ulang).

## G16b.3 Validasi lokal (container)

shellcheck v0.10.0 (`-s sh` wrapper; `-x` check-encryption/gen-secrets) bersih · hadolint v2.14.0 (digest CI) bersih ·
checkov 3.3.21 (digest & argumen = security.yml) exit 0, 0 FAILED · `docker compose config -q` dev & prod dengan
`.env.example` OK · secrets top-level == `scripts/secrets.list` (104) · `check-encryption.sh` OK (dijalankan di
container docker:27-cli + jq; host tanpa jq) + uji negatif jq rule baru (tanpa secret / env / entrypoint → ketiganya
FAIL) · pytest `infra/metabase` 23 passed + ruff check/format bersih.

## G16b.4 Rekomendasi & sisa

- **Rotasi password `clickhouse_password_metabase`** (ClickHouse read-only `efn_metabase`): sempat plaintext di DB dev dan
  dump dev (kini dihapus). Dev: opsional (hapus file → gen-secrets → clickhouse-init → metabase-provision). Staging/prod:
  tidak terdampak — belum pernah deploy, kunci aktif sejak start pertama.
- **Superadmin**: isi GitHub Environment `METABASE_ENCRYPTION_KEY` (`openssl rand -base64 32`) di staging & production
  sebelum deploy berikutnya + salinan offline (key-custody §1).
- Ditemukan di luar lingkup: scratchpad sesi masih berisi dump Odoo plaintext `efn_odoo_pre_g10.dump`,
  `efn_odoo_pre_g13c.dump` (data dev; usul lead: hapus bila tidak dibutuhkan untuk rollback).
- Belum: rotasi kunci kedua (old→new) belum diuji; uji di staging saat ada. Build image di CI (build-push) belum dijalankan
  untuk perubahan ini (hanya build lokal). `metabase-provision` tidak memeriksa status enkripsi (cek DB manual di README).

---

# Gelombang 16c (9 Okt 2026): perbaikan Security Review r9 (SEC-80..88)

Branch `chore/wave-16b-followups`, belum di-commit. Status per temuan + bukti: `docs/reviews/SECURITY_REVIEW_2026-10-09_r9.md`
§*Status perbaikan G16c* (8 Fixed, 1 Partial).

## G16c.1 Perubahan

| File | Isi |
|---|---|
| `scripts/verify-commit-gates.sh` (baru) | Gerbang rilis SEC-80: Actions API (`actions: read`), run per file workflow + `head_sha` + event push, job wajib `success`; `--preset release` = `ci.yml` `CI gate` + 4 job `security.yml`; polling, timeout, run absen = gagal. |
| `.github/workflows/build-push.yml` | Job `scan-pr` (PR, `contents: read`, cache baca saja) · job `gate` (non-PR, `needs` build) · job `build`: push sekali → Trivy BY DIGEST → Syft → cosign sign/attest (SEC-81/83); `DOCKER_BUILD_RECORD_UPLOAD=false`. |
| `.github/workflows/deploy.yml` | `resolve`: filter `workflow_run` event push + repo sendiri + main (SEC-82), SHA penuh, gerbang rilis + Build & Push success, output `git_sha`; `cosign verify --certificate-github-workflow-sha/-repository`; manifest `git_sha` = SHA yang dideploy. |
| `.github/workflows/ci.yml` | `persist-credentials: false` (kecuali `changes`, alasan dikomentari), `npm ci` + cache npm, pin `pip-audit==2.10.1`/`pylint-odoo==10.0.11`, `CI gate` `needs: changes` (lubang: paths-filter gagal → gate lolos). |
| `.github/workflows/security.yml` | Komentar header akurat (blokir rilis, bukan merge); label checkov "Dockerfile, GitHub Actions". |
| `infra/compose/docker-compose.yml` | `metabase`: `cap_drop: [ALL]` + `cap_add: [CHOWN, SETUID, SETGID]`, tanpa `clickhouse_password_metabase` (SEC-85). |
| `.trivyignore.yaml` | Statement DS-0002 faktual (perlu persetujuan ulang pemilik proyek). |
| `scripts/check-encryption.sh`, `scripts/deploy.sh`, `scripts/smoke/run-e2e-smoke.sh` | SEC-86: `entrypoint == null`, tolak image `metabase/metabase`; cek runtime `.Config.Entrypoint`. |
| `infra/metabase/README.md`, `docs/runbooks/secret-rotation.md` | SEC-87: prosedur rotasi jujur (argv terlihat `ps` host; mitigasi). |
| `docs/runbooks/ci-github-free-plan.md`, `docs/03_DEVSECOPS_PIPELINE.md` | Gerbang rilis, fakta Rekor (SEC-84), alur PR, pin digest checkov lokal. |

## G16c.2 Verifikasi nyata

- Gerbang rilis terhadap API GitHub nyata (read-only): ef346a0 → exit 0; 7c33351 (CI failure) → exit 1; SHA tanpa run →
  exit 1; nama job salah → `absent` exit 1.
- Rekor (publik, read-only): entri 3155542984 (`dsse`) = hash + sertifikat saja, `attestation: null`; 3155540831
  (`hashedrekord`). Sertifikat memuat repo, workflow@ref, SHA commit.
- Log run 62b1bf1: trivy-action 0.35.0 "Using YAML ignorefile" + "Some vulnerabilities have been ignored/suppressed".
- Metabase dev: recreate + restart dengan cap baru → healthy, `CapBnd=0xc1`, "correctly configured", `metabase-provision`
  exit 0; sidecar `metabase-tls` di-recreate (healthy).
- `docker compose config --format json`: key `entrypoint` selalu ada; `[]`/`''` → `[]`, absen/`null` → `null`.
  check-encryption (docker:27-cli + jq): asli OK, `entrypoint: []` FAIL, `image: metabase/metabase` FAIL.
- Java `@argfile` di image Metabase TIDAK menyembunyikan argumen (`/proc/self/cmdline` terekspansi).
- actionlint 1.7.7 bersih · shellcheck (scripts, lib, host, smoke, metabase) bersih · yamllint 0 error ·
  checkov 3.3.21 (digest + argumen security.yml) 0 FAILED · `docker compose config -q` dev/prod OK ·
  secrets compose == `secrets.list`.

## G16c.3 Belum terverifikasi / sisa

- Workflow baru belum pernah berjalan di GitHub (butuh push/merge ke `main`): job `gate`, Trivy remote by digest
  (auth lewat `docker login`), `cosign verify --certificate-github-workflow-sha`, `head_branch` untuk run tag `v*`.
- Rollback ke rilis sebelum G16c lewat `workflow_dispatch` akan ditolak gerbang (nama job checkov lama → `absent`). Belum
  ada rilis, jadi tidak berdampak sekarang.
- Prosedur rotasi kunci Metabase (lama → baru) belum diuji end-to-end: rotasi kunci dev ditolak permission sesi.
- Host `efn-deploy` belum mengikat `cosign verify` ke `git_sha` manifest (hanya `deploy.yml`). Usul gelombang berikut,
  dengan kasus uji `test-efn-deploy.sh`.
- Merge/push dengan check merah tetap mungkin secara teknis (paket gratis). Opsi: hook `pre-push` lokal atau org berbayar
  + ruleset.

## G16c.4 Lanjutan: re-review G16c (SEC-89..94), di atas commit `4996aa8`, belum di-commit

| ID | Perubahan | Bukti |
|---|---|---|
| SEC-89 | CI: semua job pada push ke `main` (filter path hanya PR, tanpa checkout di `changes`); gitleaks riwayat penuh pada push/jadwal (image v8.30.1 ber-digest); gate 75 mnt; runbook: aturan tag + estimasi menit | gitleaks lokal 67 commit bersih; actionlint/checkov bersih |
| SEC-90 | push by digest tanpa tag → scan → sign → `imagetools create` tag + verifikasi digest; `sha-*` hanya dari main | uji registry lokal: digest index tetap sama setelah tag |
| SEC-91 | `efn-deploy` `cosign verify` terikat `git_sha` manifest + repo | test-efn-deploy 87/87 PASS (80, 81 baru) |
| SEC-92 | `efn-deploy` production wajib `/etc/efn/approved-release` (`<X.Y.Z> <git_sha>`); key-custody §4d | kasus 82-87 PASS; API environments: production tanpa required reviewers |
| SEC-93 | concurrency Security per event | actionlint |
| SEC-94 | deploy memilih run build-push per ref (`main`/`vX.Y.Z`); izin `build` tanpa security-events/attestations | API nyata `@main` lolos, `@v1.0.0` gagal |
| (saran SEC-85) | check-encryption: cap metabase dikunci | negatif SYS_ADMIN → FAIL |

Validasi: actionlint 1.7.7, shellcheck (scripts/lib/host/smoke/metabase), yamllint 0 error, checkov 3.3.21 0 FAILED
(github_actions 658 passed), `docker compose config -q` dev/prod, check-encryption (asli OK + 3 negatif FAIL),
test-efn-deploy 87/87.

Belum / perlu tindakan:
- Job Odoo test/tour belum pernah selesai di GitHub; run CI penuh pertama di `main` bisa merah → gerbang rilis tertahan
  sampai hijau. Estimasi biaya ±135-195 menit runner per merge ke `main` (kuota 2.000/bulan ≈ 10-14 merge).
- Superadmin: pasang ulang `efn-deploy` di host (sha256 dari PR), isi `/etc/efn/approved-release` sebelum rilis
  production; admin repo: Environment `staging` → deployment branch `main` saja (saat ini tanpa aturan).
- `head_branch` = nama tag untuk run push tag (SEC-94) dan seluruh alur build/tag/deploy baru belum terbukti di run nyata.

---

# Gelombang 17 — infra penjualan online Fase 3 (docs/04 §10, ADR-034 Proposed), 9 Okt 2026, belum di-commit

> **Ringkasan manajemen.** Jalur aman Super-App → Odoo untuk pesanan online sudah dibangun dan diuji nyata: BFF hanya
> bisa memanggil 10 fungsi pesanan online di Odoo lewat penjaga khusus (`odoo-rpc-gateway`) dengan sertifikat di kedua
> sisi; 97 uji penyimpangan/serangan berperilaku sesuai harapan (ditolak), 10 fungsi sah lolos. Antrean pesanan, kunci
> rahasia baru, alert, dan runbook (rotasi kunci API, operasi pesanan) siap. Online tetap **mati** di produksi
> (`ONLINE_ENABLED=false`).

## G17.1 File (batas §10.15)
- **Baru:** `infra/odoo-rpc-gateway/{haproxy.cfg,entrypoint.sh,test-gateway.sh,README.md}`,
  `infra/compose/docker-compose.online-fnb.yml` (mount key FNB hanya saat kanal FNB aktif, pola payment overlay),
  `infra/observability/loki-rules/efn-online.yml`, `docs/runbooks/online-orders.md`, `docs/runbooks/odoo-online-api-key.md`.
- **Ubah:** `infra/compose/docker-compose.yml` (service `odoo-rpc-gateway`; `odoo-tls --allow-cn=odoo-rpc-gateway`;
  env BFF §10.13 + secret; Odoo `EFN_ONLINE_EVENT_HMAC_FILE`; `MINIO_BUCKET_CATALOG`; superapp-web
  `EFN_PAYMENT_SIMULATOR: "false"` bernilai tetap), `docker-compose.dev.yml` (superapp-web `EFN_PAYMENT_SIMULATOR: "true"`),
  `infra/rabbitmq/rabbitmq-entrypoint.sh`, `infra/redis/redis-entrypoint.sh` (`~cart:* ~oq:* ~ocat:*`),
  `infra/minio/minio-init.sh` (bucket `catalog` SSE-S3 + policy BFF), `infra/observability/{alert-rules.yml,
  alertmanager.yml, promtail.yml, promtail-journal.yml, tests/promtail/*}`, `scripts/{secrets.list, gen-pki.sh,
  check-encryption.sh}`, `.github/workflows/ci.yml`.

## G17.2 Keputusan teknis
- **PKI:** `odoo-rpc-gateway|both` (server untuk BFF, klien ke odoo-tls); `api-gateway` server → **both** (sertifikat
  BFF dipakai sebagai klien CN `api-gateway`; tidak perlu secret `tls_api_gateway_client_*`). Konsekuensi staging/prod:
  **intermediate wajib diterbitkan ulang** (nameConstraints belum memuat `odoo-rpc-gateway`; gen-pki.sh menolak di
  staging/prod sampai Superadmin `make pki-intermediate`, runbook internal-pki.md#service-baru). Dev: CA dev dibuat ulang.
- **Secret baru:** `odoo_api_key_online_rtl`, `odoo_api_key_online_fnb` (`literal:disabled`; key dibuat DI Odoo lewat
  runbook), `odoo_online_event_hmac` (`random`; mount odoo + api-gateway saja, dicek check-encryption). **Kunci
  enkripsi kode ambil:** kontrak §10.3.6 = AES-256-GCM §7.2 = key ring `pii_encryption_key` (AAD
  `efn:online_order_part.pickup_code_enc:v1`) → tidak ada secret baru.
- **Gateway:** pola s3-gateway; path dicocokkan MENTAH (tanpa decode) dengan 10 string; allowlist header (PCRE2);
  `X-Odoo-Database` dipaksa; penolakan membawa `X-EFN-Gateway-Deny: <alasan>` + log `why=`.
- **RabbitMQ:** 4 queue §10.9 quorum + DLQ + `x-delivery-limit: 10`; `efn-bff` read 4 queue (strict), write tetap
  `amq.default`. Argumen queue lama tidak diubah.
- **Alert:** grup Prometheus `efn-online` (OrderStuck, AmountMismatch, DlqNotEmpty, OdooAuthFailure, CircuitOpen,
  CatalogStale, RefundPending [proposed], ApiKeyExpiringSoon/Critical/Unknown — R-T15 lewat metrik textfile
  `efn_odoo_api_key_expiry_timestamp_seconds{channel}` yang ditulis runbook); `EfnDeadLetterNotEmpty` mengecualikan
  `order.*`; Loki: `EfnOnlineReconDeadman` (20 m; hanya bila heartbeat ada dalam 24 j), `EfnOnlineAnomaly*`,
  `EfnOdooRpcGatewayDenied`. Promtail tahap 6 (`stream="efn_online"`) + redaksi kode ambil `ABCD-EFGH`
  (`[PICKUP_REDACTED]`) dan kunci `pickup_code|qr_string|quote_hash` (query/JSON). Idempotency-Key tidak diredaksi
  (bukan rahasia; deterministik dari id yang boleh di-log).
- **Tambahan lead:** `EFN_PAYMENT_SIMULATOR` true hanya dev; base bernilai tetap `"false"` (bukan `${..}`, tidak bisa
  diubah lewat .env, tidak ada di allowlist deploy-validate); `check-encryption.sh` menolak render prod dengan nilai
  selain false dan menolak `ODOO_ONLINE_MOCK` di api-gateway prod.

## G17.3 Verifikasi nyata (stack dev, Docker Desktop 29.8.1)
- CA dev dibuat ulang (service PKI baru) → seluruh stack `up --force-recreate`: semua container healthy; `minio-init`
  membuat bucket `catalog` (SSE-S3).
- `test-gateway.sh`: **97/97 PASS** (diulang setelah Docker Desktop restart): tanpa sertifikat / CA asing (CN
  api-gateway) / sertifikat EKU server saja / HTTP polos / BFF langsung ke `odoo:8443` → handshake gagal; CN traefik →
  403 `cn`; 6 method → 405; 23 path lain (search_read, write, action_efn_ready, handover wizard, call_kw, jsonrpc,
  xmlrpc, trailing slash, huruf besar, `//`, dot segment, `;`, backslash) + 14 varian encoded (`%61`, `%2F`, `%2e%2e`,
  `%252e`, `%00`, `%c0%ae`, `%3F`, `%23`, …) → 403; query/fragment/absolute-form/HTTP 1.0 → ditolak; kasus header
  (method override ×3, X-Original-URL, X-Rewrite-URL, X-Openerp-Session-Id, Upgrade h2c/websocket, TE, Expect,
  Proxy-Authorization, header asing, Host salah ×2, Content-Type salah/ganda, Authorization hilang/ganda/Basic/pendek)
  → ditolak; body 300 KB → 413; 10 path sah + Cookie/X-Forwarded-*/X-Odoo-Database klien (dibuang) → sampai Odoo
  (401 dari Odoo karena token uji). Health check backend = mTLS CN odoo-rpc-gateway ke odoo-tls.
- **Bug ditemukan uji & diperbaiki:** regex `^bearer [..]` di HAProxy (spasi = pemisah argumen → pola OR) meloloskan
  `Authorization: Basic …`; kini `\x20`. Upgrade tanpa `Connection: upgrade` dibuang parser H1 HAProxy sebelum aturan
  (diverifikasi dengan HAProxy echo terpisah).
- Loki: log gateway tanpa token (query `bearer|token uji` = 0); query `EfnOdooRpcGatewayDenied` mengelompokkan alasan
  dengan benar; ruler mengevaluasi 4 rule baru. Prometheus memuat 10 rule `EfnOnline*`.
- RabbitMQ 3.13.7: 8 queue dengan argumen benar; uji nyata publish + 11× reject-requeue → pesan pindah ke
  `order.compensate.dlq` dengan `x-first-death-reason: delivery_limit` (butir §10.14 "dukungan x-delivery-limit" =
  **terverifikasi**; batas 10 = maksimal 11 kali pengiriman). Izin `efn-bff` sesuai.
- Lint: shellcheck (bash + sh) OK, actionlint 1.7.7 OK, yamllint OK (warning panjang baris pola lama), hadolint OK,
  checkov 3.3.21 (argumen security.yml) 0 FAILED, promtool 38 rule OK, amtool OK, `haproxy -c` OK,
  `docker compose config -q` dev/prod OK, secrets compose (+payment +online-fnb) == secrets.list, prod hanya publish
  traefik, `check-encryption.sh` OK (jq dalam container; host tanpa jq), `test-promtail-redaction.sh` 79 entri OK.

## G17.4 Belum / untuk lead
- **Staging/prod:** terbitkan ulang intermediate PKI sebelum deploy G17; isi GitHub Environment
  `ODOO_API_KEY_ONLINE_RTL`, `ODOO_API_KEY_ONLINE_FNB` (boleh `disabled`), `ODOO_ONLINE_EVENT_HMAC`.
- ~~Config rilis `ONLINE_*`, `.env.example`, indeks runbook~~ → selesai di G17.5.
- BFF: heartbeat `EFN_ONLINE_RECON {"status":"ok|error",…}` (konteks logger `Online*`, level LOG/ERROR) belum ada di
  kode BFF — tanpa itu `EfnOnlineReconDeadman` tidak aktif (aman, tetapi buta). Gauge refund terbuka per status
  diusulkan. Placeholder secret `disabled` ditolak BFF (terlalu pendek) saat kanal aktif — dev memakai
  `ODOO_ONLINE_MOCK` atau key nyata.
- Uji gateway belum di CI (butuh stack + Odoo); CI hanya `haproxy -c`. Respons 200 dari E-28 terpasang belum diuji.
- `EfnOdooRpcGatewayDenied` menyala di dev setelah `test-gateway.sh` (sengaja).
- Odoo test CI: `efn_online_order efn_setup_online` ditambahkan ke `EFN_TEST_MODULES` (dilewati bila belum ada).

## G17.5 Lanjutan (batas file diperluas lead): config rilis online
- `scripts/lib/deploy-validate.sh`: 12 variabel (`ONLINE_ENABLED`, `ONLINE_CATALOG_ENABLED`, `ONLINE_CHANNELS`,
  `ONLINE_FULFILMENTS`, 7 angka `ONLINE_*`, `ODOO_RPC_TIMEOUT_MS`) masuk `EFN_RELEASE_CONFIG_VARS`. Validasi: boolean
  `true|false`; kanal `^[A-Z][A-Z0-9-]{1,15}` (≤ 8, koma); fulfilment allowlist `pickup|delivery`; angka bulat tanpa
  nol depan dengan rentang (timeout bayar & TTL quote 60..3600, order terbuka 1..20, sinkron katalog 60..86400,
  kode ambil 1..30 hari, retensi 1..3650 hari, `ODOO_RPC_TIMEOUT_MS` 1000..30000 = di bawah timeout gateway 30 s).
  Production `ONLINE_ENABLED=true` hanya dengan `PAYMENT_PROVIDER` xendit|midtrans. `EFN_DEV_ONLY_SWITCHES`
  (`ODOO_ONLINE_MOCK`, `EFN_PAYMENT_SIMULATOR`) ditolak bila bukan false di staging/production (jalur manual), dan
  sebagai kunci manifest tetap ditolak skema (kunci tidak dikenal).
- `scripts/deploy.sh` (dibutuhkan agar config sampai ke compose): baris `.env` `ONLINE_*` (default mati, angka kosong =
  default compose); `ONLINE_ENABLED=true` → setiap kanal wajib punya key non-placeholder; kanal `FNB` → overlay
  `docker-compose.online-fnb.yml` (kanal brand `FNB-<X>` butuh overlay baru — dicatat di komentar).
- `.github/workflows/deploy.yml`: `CFG_ONLINE_*` + `CFG_ODOO_RPC_TIMEOUT_MS` dari GitHub variables (default
  `false`/`RTL`/`pickup`, angka kosong).
- `infra/compose/.env.example`: blok G17 dengan komentar rentang (diverifikasi `docker compose --env-file .env.example
  config`: komentar inline tidak ikut nilai).
- `docs/runbooks/README.md`: 4 baris alert (Prometheus + Loki), 2 prosedur, baseline hardening `odoo-rpc-gateway`.
  `online-orders.md`: kill switch staging/prod via config rilis + syarat menyalakan di production.
- `scripts/host/test-efn-deploy.sh`: +14 kasus (88–101: online prod tanpa provider nyata ditolak, provider nyata
  diterima, boolean/kanal/fulfilment/angka/rentang tidak valid ditolak, default diterima, mock/simulator ditolak,
  kunci manifest `ODOO_ONLINE_MOCK` ditolak). **PASS=101 FAIL=0** (container python:3.12-bookworm seperti CI).
- Ulang validasi: shellcheck (seluruh `scripts/*.sh scripts/lib scripts/host` + infra) OK (SC2015 baru di deploy.sh
  diperbaiki), actionlint OK, yamllint 0 error, checkov 0 FAILED, compose config dev/prod OK, `check-encryption.sh` OK.
- Tambahan lead #2: `07-platform-online.sql` masuk daftar migrasi WAJIB `deploy.sh` (bundel tanpa 07 = deploy gagal);
  dev `api-gateway` `ONLINE_ENABLED=true` + `ONLINE_CATALOG_ENABLED=true` (render prod tetap false; `ODOO_ONLINE_MOCK`
  absen di dev dan prod). Validasi: compose config dev/prod OK, check-encryption OK, shellcheck OK, efn-deploy 101/101.
  Container `api-gateway` dev BELUM di-recreate: perlu key RTL nyata di `secrets/odoo_api_key_online_rtl.txt` (runbook
  odoo-online-api-key §1, setelah E-28 + user teknis `efn_online_rtl` ada) dan migrasi 07 di volume dev lama.

## G17.11 Sizing pilot opsi B (keputusan pemilik proyek 10 Okt 2026)
- `docker-compose.prod.yml`: gateway `ODOO_RPC_MAXCONN ${..:-2}`; api-gateway `ODOO_RPC_PUBLIC_MAX_CONCURRENCY ${..:-1}`,
  `ODOO_RPC_SAGA_MAX_CONCURRENCY ${..:-1}`. Base compose: kedua pool `${..:-2}` (dev 2 + 2 = maxconn 4). Render tanpa
  .env: dev 4/2/2, prod 2/1/1.
- `deploy-validate.sh`: kedua pool masuk `EFN_RELEASE_CONFIG_VARS` (rentang 1..16) + aturan PUBLIC + SAGA ≤ MAXCONN
  (default efektif prod 2/1/1); `deploy.sh` `.env`; `deploy.yml` `CFG_ODOO_RPC_{PUBLIC,SAGA}_MAX_CONCURRENCY`;
  `.env.example`; runbook `online-orders.md#sizing-worker` (keputusan opsi B ditandai, tabel skenario + pool).
- Uji: efn-deploy **109/109** (+6 kasus 104–109), compose config dev/prod, check-encryption, shellcheck 0.9.0, actionlint OK.
- Catatan: prod 2 replika BFF → permintaan paralel maks. 2 × (1+1) = 4 ke gateway; Odoo tetap maks. 2 koneksi online,
  sisanya antre/503 di gateway (aturan per replika sesuai instruksi lead; pertimbangkan replika BFF 1 atau aturan
  replika × pool ≤ maxconn bila antre sering).

## G17.10 Perbaikan CI PR #36 (HEAD a522da6), 10 Okt 2026
- **ruff 0.16.10:** `services/odoo/addons/efn_setup_company/tests/__init__.py` (I001, import digabung; perilaku sama).
- **shellcheck 0.9.0 (versi CI):** `scripts/smoke/run-e2e-smoke.sh` SC2001 ×2 → helper `nomsg()` (`sed -E`, perilaku
  sama: isi `message` Odoo disamarkan di detail check). CI kini juga shellcheck `test-gateway-load.sh`.
- **gitleaks 8.24.3:** 1 FP `services/superapp-web/tests/online-routes.test.ts:270` (header Idempotency-Key
  buatan klien, fixture Vitest) → `.gitleaksignore` + alasan. Bukan secret. Re-scan rentang PR: no leaks.
- **semgrep 1.179.0:** 1 FP `efn_online_event_outbox.py:222` (log nama env var HMAC, bukan nilai) → `nosemgrep` +
  alasan (pola sama dengan efn_kds_station.py). Re-scan penuh `--error`: rc 0.
- **Trivy 0.69.3 image postgres:** CVE baru HIGH di binary rilis WAL-G v3.0.9 (stdlib Go 1.25.11 CVE-2026-78667/
  78669/97031, x/net 0.58.0 CVE-2026-78669) → **diperbaiki**: WAL-G dibangun dari source tag v3.0.9 (commit
  3e49318 diverifikasi) dengan Go 1.27.2 + x/net 0.60.0 + grpc 1.83.2, tag `libsodium`. Trivy bersih (hanya FP lama
  CVE-2021-38599 yang sudah disetujui); **10 pengecualian WAL-G G16 dihapus** dari `.trivyignore.yaml`. Uji fungsional:
  `wal-g --version` v3.0.9, `efn-walg backup-list` + `wal-fetch` (dekripsi libsodium) terhadap backup dev OK.
- **Trivy image minio:** x/net 0.59.0 (CVE-2026-78669) di `silo` & `mc` → `go get golang.org/x/net@v0.60.0` sebelum build;
  Trivy bersih; versi biner tetap (RELEASE.2026-09-16, commit sama).
- hadolint, checkov 0 FAILED, actionlint OK. Tidak ada pengecualian baru. Image dev `efn/postgres:dev` & `efn/minio:dev`
  di-tag ulang dari build baru; container dev belum di-recreate.

## G17.9 Re-review G17: SEC-107 (L-19c) + SEC-108 (L-19e) di wrapper
- **Peran:** grup host `efn-cs-approver` terpisah dari `efn-cs`; wrapper menetapkan `EFN_CS_ROLE=cs|approver` dari
  keanggotaan grup (env pemanggil ditimpa); `online-refund-approve` hanya peran approver; akun di kedua grup ditolak
  untuk semua subcommand ("satu orang satu peran").
- **Alasan via stdin:** `--reason` di argv ditolak; wrapper membaca satu baris stdin (prompt bila TTY; CR akhir
  dibuang; tanpa karakter kontrol; 3..800 byte) dan meneruskannya lewat `docker exec -i` stdin tanpa flag `--reason`
  (**kontrak untuk BFF: CLI membaca alasan dari stdin bila `--reason` tidak diberikan**; BFF belum mengimplementasikan
  saat ini — perlu dicek di laporan BFF/integrasi). sudoers: `Cmnd_Alias`, `%efn-cs, %efn-cs-approver`,
  `env_reset, !setenv, !log_input, !log_output`.
- **Loki:** `promtail-journal.yml` mengirim `efn-online-cs` HANYA bila `_UID=0` (field journald tepercaya; user host
  lain tidak bisa memalsukan) → `service="host-efn-online-cs"`. Rule baru grup `efn-online-cs`:
  `EfnOnlineCsDeniedRepeated` (≥ 3 DITOLAK per aktor / 15 m), `EfnOnlineCsDualRole`, `EfnOnlineCsRefundVolumeHigh`
  (> 10/jam, PLACEHOLDER). Approve di luar jam kerja tidak dibuat (LogQL tanpa fungsi jam).
- **Uji:** `test-efn-online-cs.sh` 45/45 (peran, dua grup, approve oleh cs, alasan stdin literal/kosong/pendek/tab/
  > 800 byte/multi-baris, `--reason` argv ditolak, journald tanpa alasan); promtail redaksi 83 entri OK (fixture baris
  wrapper utuh di pipeline docker + journal), `-check-syntax` OK; query LogQL rule baru diparse Loki (status success)
  dan dievaluasi ruler. shellcheck, actionlint, yamllint 0 error, checkov 0 FAILED, compose dev/prod, check-encryption OK.
- Runbook `online-orders.md#cli-cs` (peran, larangan dua grup + cek `comm`, sudoers baru, alasan via stdin, alert),
  `host-journal-loki.md`, indeks `README.md`.

## G17.8 SEC-98 (L-16d): wrapper host `efn-online-cs`
- `scripts/host/efn-online-cs` (root, dipasang manual Superadmin seperti efn-deploy): wajib root lewat sudo,
  `SUDO_USER` valid (bukan root, konsisten dengan `SUDO_UID`), anggota grup `efn-cs`; `EFN_CS_OPERATOR`=SUDO_USER dan
  `EFN_CS_HOST`=hostname SELALU ditimpa; hanya `online-refund|online-refund-approve|online-refund-settle|online-resolve`
  + flag allowlist per subcommand, UUID/enum/pola/panjang divalidasi, tanpa karakter kontrol/flag ganda/argumen posisi;
  argv ke `docker exec <api-gateway project efn> node dist/cli/<cmd>.js` (tanpa shell); journald `efn-online-cs`
  MULAI/SELESAI/DITOLAK dengan aktor, host, cmd, id, rc — tanpa `--reason` dan `--provider-ref`; rc CLI diteruskan.
- `scripts/host/test-efn-online-cs.sh`: 35 kasus (docker/logger palsu, container sekali pakai) **PASS=35 FAIL=0**;
  CI job infra: langkah baru + shellcheck `scripts/host/efn-online-cs`. shellcheck OK.
- Runbook `online-orders.md#cli-cs`: pasang (install + sha256, grup `efn-cs`, sudoers `NOPASSWD` + `env_reset,
  !setenv, log_output`, `visudo -c`), pemakaian 4 subcommand, jejak journald, cabut akses.
- Catatan untuk BFF: pemisahan tugas approve ≠ pembuat refund belum ditegakkan kode (hanya tercatat) — kini prosedural.
- Belum: uji dengan sudo + docker asli di host staging.

## G17.7 Review r10 SEC-96 (L-16b): batas koneksi gateway → Odoo
- `haproxy.cfg`: `maxconn "${ODOO_RPC_MAXCONN}"` (dari 32 → default **4**; env tak diset = HAProxy gagal parse =
  fail-closed), `timeout queue 2s`, antrean frontend dibatasi 16 (`queue(odoo) ge 16` → `503` seketika, `why=busy`),
  `http-error 503` JSON + `X-EFN-Gateway-Error: unavailable` (dibedakan dari `X-EFN-Gateway-Deny`), log + `%Tw`.
- Konfigurasi: `entrypoint.sh` validasi 1..16 (default 4); compose `ODOO_RPC_MAXCONN: ${ODOO_RPC_MAXCONN:-4}`;
  deploy-validate (+ rentang 1..16), deploy.sh `.env`, deploy.yml `CFG_ODOO_RPC_MAXCONN`, `.env.example`, CI `haproxy -c`
  dengan env. Loki `EfnOdooRpcGatewayDenied` mengecualikan `why=busy`.
- Runbook `online-orders.md#sizing-worker`: aturan `ODOO_WORKERS ≥ POS + ODOO_RPC_MAXCONN`. **Temuan:** prod
  `ODOO_WORKERS=6` + maxconn 4 menyisakan 2 worker untuk POS → sebelum online prod: workers 9 (4 CPU) atau maxconn 2
  (keputusan lead/klien; compose prod tidak diubah).
- Verifikasi: `test-gateway.sh` 97/97; uji beban baru `test-gateway-load.sh 300 200` → 150 dilayani Odoo (antre maks.
  90–123 ms), 150 × `503 unavailable`, 0 status lain, gateway tetap healthy; efn-deploy 103/103 (+2 kasus maxconn);
  shellcheck, actionlint, yamllint (0 error), checkov 0 FAILED, compose dev/prod, check-encryption OK.
- Temuan sampingan: Odoo menjawab `HTTP/1.0`; curl paralel lalu mengirim HTTP/1.0 → ditolak gateway (`why=target`).
  BFF (undici) tidak terdampak; uji beban memakai `--http1.1` + `Connection: close`.

## G17.6 Temuan QA G17-QA-05 (watch BFF dev di Windows)
- **Penyebab `unhealthy` sebelum integrasi (dari log Loki):** setelah recreate 14:20 (9 Okt) `nest start --watch`
  mengompilasi `src/` saat paket BFF setengah diedit → `Found 4 errors` (MockGateway belum punya `refund`, TS2420/2741/
  2322) → app tidak start (tidak ada listener :3000) → healthcheck gagal. Perbaikan file di host tidak terdeteksi karena
  Docker Desktop (Windows) tidak meneruskan event inotify dari bind mount, jadi container tetap unhealthy sampai
  di-recreate 15:38.
- **Perbaikan (hanya `docker-compose.dev.yml`):** env resmi TypeScript `TSC_WATCHFILE=DynamicPriorityPolling` +
  `TSC_WATCHDIRECTORY=RecursiveDirectoryUsingDynamicPriorityPolling` di api-gateway dev (watcher `nest --watch` = API
  watch TypeScript → polling). Superapp-web dev belum punya pola polling (tidak ada yang bisa ditiru). Compose prod tidak
  diubah.
- **Verifikasi nyata:** recreate api-gateway → healthy ±55 s; tambah satu baris komentar di `src/main.ts` → `File change
  detected` ±1 s, app start ulang (`successfully started`, ±4 s); file dikembalikan persis (`git diff` bersih) → reload
  lagi; status akhir healthy. compose config dev OK, check-encryption OK.

# Gelombang 18 — W-11 rotasi API key, W-19 env retensi, W-28 kustodi brankas (10–12 Okt 2026, belum di-commit)

Sumber: `docs/10_RENCANA_GELOMBANG_18_PLUS.md` (W-11, W-19, W-28), keputusan 10 Okt (`docs/decisions/README.md` #33,
Q-E1a/b/c, bagian E), R-T15, R-B11; permintaan lead (env data-AI, BFF: migrasi 08, `online-erasure-pending`, alert forced).

## G18.1 W-11 — rotasi otomatis API key kanal online (ADR-024/034 Proposed, R-T15)

| File | Isi |
|---|---|
| `scripts/host/efn-rotate-online-key` (baru) | Skrip root fail-closed (pola efn-deploy/efn-online-cs): identitas root/systemd/sudo `efn-superadmin`; flag allowlist (`--channel --force --status --overlap-seconds`); konfigurasi `/etc/efn/online-key-rotation.conf` tidak di-source (allowlist + validasi, milik root, tidak group/world-writable); flock per kanal. Alur: list key (`odoo shell` exec di container odoo) → rotasi bila sisa < 10 h / state tak dikenal / tanpa kedaluwarsa / `--force` → key baru `with_user(efn_online_<kanal>)._generate(None, 'efn-online-<KANAL>-<UTC>', +30 h)` → tulis atomik ke file secret yang di-mount compose (path dari `compose config`, wajib di SECRETS_DIR; `root:efn-superadmin` + mode deploy.sh) → recreate pemakai secret + sidecar → tunggu sehat → verifikasi json2 `efn_online_catalog` limit 1 lewat odoo-rpc-gateway (mTLS) di **setiap** replika BFF → gagal: rollback + cabut key baru (rc 1; rc 3 bila rollback gagal) / sukses: overlap 1 jam → verifikasi ulang → cabut semua key lain → **bukti 401** key lama → hancurkan salinan. Cabut tertunda diselesaikan run berikutnya. Nilai key tidak pernah di stdout/stderr/journald/argv; stderr Odoo disaring. Metrik textfile `efn_odoo_api_key_*` (multi-kanal satu file). |
| `scripts/host/systemd/efn-rotate-online-key@.{service,timer}` (baru) | Template per kanal, harian 03:15 WIB, `Persistent`, hardening systemd ringan. |
| `scripts/host/test-efn-rotate-online-key.sh` (baru) + CI step | 42 kasus docker/Odoo/BFF palsu: bootstrap, tidak-perlu, `--force`, rollback, unhealthy → rc 3, create gagal, cabut tidak efektif, cabut tertunda/overlap, sisa < 10 h, key tanpa exp, `--status`, penolakan identitas/argumen/konfigurasi/flock, penjaga kebocoran key. |
| `scripts/secrets.list` + `gen-secrets.sh`, `deploy.sh`, `deploy.yml`, `efn-deploy` | Jenis secret baru **`host:<awal>`** untuk `odoo_api_key_online_{rtl,fnb}`: tidak dikirim deploy.yml, ditolak efn-deploy dari stdin (test 110), **tidak ditimpa** deploy.sh (file belum ada → placeholder). Tanpa ini deploy berikutnya menimpa key hasil rotasi dengan nilai GitHub lama (yang sudah dicabut) → outage. `deploy.sh` menulis `/opt/efn/compose/.efn-compose-files` (dipakai skrip host). |
| `alert-rules.yml` | ExpiringSoon 14 → **7 h** (rotasi normal di sisa 10 h); baru `EfnOnlineApiKeyRotationFailed`, `…RotationNotRunning`, `…ExtraKeys`. |
| `loki-rules/efn-online.yml`, `promtail-journal.yml` | Grup `efn-online-key-rotation`: `EfnOnlineApiKeyRollbackFailed` (critical), `…OldKeyStillAccepted`; journald `efn-rotate-online-key` hanya `_UID 0` → `service="host-efn-rotate-online-key"`. |
| `docs/runbooks/odoo-online-api-key.md` (ditulis ulang), `docs/04` baris identitas Odoo | Pemasangan, cara kerja, alert, rotasi gagal, ROLLBACK GAGAL, darurat. |

**Uji NYATA stack dev (10 Okt):** skrip dijalankan di container `docker:27-cli` (socket + repo di `/run/desktop/mnt/host/e/...`): `--status` → rotasi (key id 1 → id 2, 30 h, verifikasi 200 di BFF sebelum & sesudah, key 1 dicabut, **401 terbukti**) → run kedua "TIDAK PERLU" → metrik lengkap; `efn-api-gateway-1` sehat. `secrets/odoo_api_key_online_rtl.txt` dev kini berisi key id 2 (berlaku s.d. 9 Nov 2026).

**Selaras functional:** `api_key_duration` grup teknis 35 h (efn_setup_online) ≥ KEY_DAYS 30. ⚠ **Temuan:** Odoo memakai **maksimum** durasi dari *semua* grup user (`all_group_ids`); di DB dev grup "Role / User" juga ber-`api_key_duration` 90 → batas efektif user teknis tetap 90 h. Perlu dicek efn-odoo-functional (jangan beri durasi di grup umum).

**Deviasi dari brief:** file secret ditulis mode **0444** (bukan 0400) di direktori 0750 `root:efn-superadmin` — sama dengan deploy.sh; 0400 root membuat BFF (uid 1000) tidak bisa membaca bind mount (key-custody "Mekanisme uid"). Salinan kerja di `/var/lib/efn/online-key` = 0400 root.

## G18.2 W-19 — env retensi (+ data-AI, BFF)

compose / `.env.example` / `deploy-validate` (release config + rentang) / `deploy.yml` `CFG_*` / `deploy.sh` `.env`:
`ONLINE_ADMIN_AUDIT_RETENTION_DAYS` 3650 (30..3650), `ERASURE_SUPPRESSION_DAYS` 365 (0..730), `DSAR_OPEN_ORDER_MAX_DEFER_DAYS` 14 (1..14), `MEMBER_MIN_AGE` 17 (17..21), `PRICE_HUNTER_GEO_RETENTION_DAYS` 90, `CH_MART_RETENTION_YEARS` **10** (1..30), `CH_PSEUDONYM_RETENTION_YEARS` 5 (clickhouse-init + etl; ≤ mart), `CH_GEO_RETENTION_DAYS` 90 (clickhouse-init + etl), `MB_AUDIT_MAX_RETENTION_DAYS` 3650 (metabase, 30..3650). Batas bawah `ONLINE_ORDER_MEMBER_LINK_DAYS` 30 / `ONLINE_EVENT_RETENTION_DAYS` 7 diselaraskan dengan BFF. Test efn-deploy +15 kasus (110–124).

- **Deploy G18:** `deploy.sh` kini `stop etl` sebelum `up -d` (profil data) → ETL baru jalan setelah `clickhouse-init` exit 0 (depends_on) — mitigasi jendela migrasi `loyalty_events` (data-AI), otomatis setiap deploy.
- Migrasi `08-platform-g18-privacy.sql` masuk daftar **wajib** deploy.sh.
- Housekeeping Metabase: `scripts/metabase-housekeeping.sh` + `scripts/host/systemd/efn-metabase-housekeeping.{service,timer}` (harian 04:00 WIB, `metabase-provision <argumen compose> --housekeeping-only`, file compose deploy terakhir), metrik + alert `EfnMetabaseHousekeepingStale`, journald unit → Loki.
- Runbook baru `docs/runbooks/retention.md` (tabel env, deploy pertama G18, housekeeping).
- ⚠ Variabel GitHub Environment lama (`ONLINE_ADMIN_AUDIT_RETENTION_DAYS=400`, `CH_MART_RETENTION_YEARS=5` bila diset) **menimpa** default baru — Superadmin hapus sebelum deploy G18. `infra/compose/.env` dev lokal juga masih bernilai lama.

## G18.3 Permintaan BFF

- Wrapper `efn-online-cs`: subcommand **`online-erasure-pending`** (alias `erasure-pending`, nama kanonik mengikuti runbook BFF) → `node dist/cli/online-erasure-pending.js`; read-only, tanpa flag, tanpa alasan, **tanpa stdin** (`docker exec` tanpa `-i`, `< /dev/null`), peran cs & approver; pemisahan peran tetap. Test 54/54. ⚠ sha256 wrapper berubah → Superadmin pasang ulang. Runbook online-orders.md diperbarui.
- Alert `EfnDsarErasureForcedOpenOrders`: `increase(efn_dsar_erasure_deferral_total{event="forced"}[1h]) > 0` (warning, runbook online-orders.md#erasure-paksa).

## G18.4 W-28 kick-off — kustodi brankas Direksi (R-B11)

`docs/runbooks/key-custody.md` §8 (baru) + penyesuaian §1/§2/§5/§6/§7 ke keputusan 1 Superadmin: isi Paket A (arsip `age` terenkripsi passphrase P-A di 2 flashdisk: root CA dir, pii key ring, walg, minio KMS, clickhouse disk, metabase, otp_pepper, audit sink, LUKS passphrase + header backup, kode pemulihan GitHub, kunci SSH break-glass, kanari PII, register SHA-256), Amplop B terpisah (P-A + passphrase root; rekomendasi lokasi kedua), pengemasan & penyegelan bernomor + berita acara, log brankas + pemeriksaan segel kuartalan, pemicu pembaruan paket, **uji pemulihan disaksikan 2×/tahun** (restore nyata dengan kunci dari paket, oleh staf selain Superadmin), prosedur Superadmin tidak tersedia (pemicu 4 jam / 2 minggu / keluar), Shamir sebagai opsi masa depan (tidak diwajibkan). Index `docs/runbooks/README.md` diperbarui.

## G18.5 Validasi (lokal, container)

shellcheck v0.10.0 (semua skrip CI + baru) bersih · `docker compose config` dev & prod OK · `check-encryption.sh` OK · promtool `check rules` SUCCESS · yamllint OK (1 warning lama: baris pytest metabase milik data-AI) · actionlint rc 0 · checkov 0 failed · test-efn-deploy **124/124** · test-efn-online-cs **54/54** · test-efn-rotate-online-key **42/42** · test-promtail-redaction OK (83 entri) · uji nyata rotasi di dev (G18.1).

## G18.6 Belum / risiko

- Belum diuji di host Linux dengan systemd/journald/promtail-journal nyata (staging belum ada, ADR-004); timer housekeeping & `stop etl` di deploy.sh belum dijalankan nyata.
- Rollback rotasi hanya diuji dengan docker palsu; jalur sukses diuji nyata.
- Recreate BFF (2 replika prod) serentak → 503 beberapa detik saat rotasi (03:15 WIB).
- `efn-online-cs`, `efn-deploy` (allowlist `host:`), skrip baru: sha256 berubah → pemasangan ulang manual Superadmin.
- Container `efn-fg18-suite`/`efn-fg18-pg` (image `efn-odoo:ci`, dimulai ±10 menit sebelum laporan) **bukan** milik devsecops dan tampak dipakai agent lain → tidak dihapus. Container uji devsecops semuanya `--rm` (tidak ada sisa).
- W-28 lanjutan (DPIA, off-site backup, PoC SeaweedFS) → G20. Nama Superadmin/pemegang brankas/saksi belum ditetapkan (G-3).

## G18.7 Perbaikan review r11 (SEC-114, SEC-120; L-25b/h) + env BFF r11 (14 Okt 2026, belum di-commit)

- **SEC-114 (L-25b):** default `ETL_LOYALTY_POINTS_MODE` = **`payment`** di compose (etl), `.env.example`, `deploy.yml`
  (`CFG_*`), `deploy.sh`. `deploy-validate` menolak `product` di **production** (staging/dev boleh untuk data historis).
  Test efn-deploy 125–127. ⚠ Default di kode ETL (`services/etl/efn_etl/config.py:51`, `loyalty_recon.py:613`) masih
  `product` — milik data-AI (lead meneruskan). Selama compose selalu mengisi env, kode default tidak dipakai.
- **SEC-120 (L-25h):** kunci host bersama **`/run/efn/deploy.lock`**:
  - `efn-deploy` mengambil kunci (fd 8, tunggu ≤ 900 s, `EFN_DEPLOY_LOCK_WAIT` hanya untuk test; sudo env_reset) sebelum
    sinkron ke `/opt/efn`; fd diwarisi `deploy.sh` lewat exec. `deploy.sh` jalur manual membuka & menunggu kunci yang
    sama (jalur efn-deploy: `flock -n` pada deskripsi warisan).
  - `efn-rotate-online-key` memegang kunci selama precheck → create → pasang → recreate → verifikasi/rollback, dan
    selama pencabutan setelah overlap (juga cabut tertunda); **overlap 1 jam tanpa kunci**. Kunci tidak didapat dalam
    `HOST_LOCK_WAIT_SECONDS` (default 900, 10..3600) → gagal **sebelum** membuat key (metrik 0, alert, coba lagi besok).
  - Key yatim: create gagal/galat setelah commit atau baris `NEW` tidak dikenali → op Odoo `revoke` per **nama berversi**
    `efn-online-<KANAL>-<UTC>` (nama divalidasi regex di sisi Python; key terpasang `CUR_ID` dikecualikan).
  - Test: rotasi +6 (43–48: keluaran rusak, galat setelah commit, kunci dipegang deploy, cabut tertunda menunggu kunci,
    kunci dilepas setelah sukses); efn-deploy 02b (kunci dipegang selama deploy.sh) dan 110b (kunci dipegang rotasi →
    deploy ditolak setelah menunggu).
  - ⚠ sha256 `efn-deploy` dan `efn-rotate-online-key` berubah lagi → pasang ulang manual Superadmin.
- **Env BFF r11:** `LOYALTY_RESTORE_TOLERANCE_PCT` (10; deploy-validate 0..50, BFF menerima 0..100) dan
  `LOYALTY_COMMITTED_RESERVATION_RETENTION_DAYS` (3650; 3650..7300) di compose api-gateway, `.env.example`,
  `deploy-validate` (release config + rentang), `deploy.yml`, `deploy.sh`. Test efn-deploy 128–131. Runbook
  `retention.md` + `odoo-online-api-key.md` (koordinasi deploy) diperbarui.
- **Validasi:** shellcheck bersih · compose config dev/prod OK · check-encryption OK · actionlint rc 0 · yamllint OK
  (1 warning lama baris pytest metabase) · checkov 0 failed · test-efn-deploy **133/133** · test-efn-rotate-online-key
  **48/48** · test-efn-online-cs **54/54**.
- **Belum:** perilaku flock lintas efn-deploy → exec deploy.sh hanya diuji dengan stub deploy.sh (bukan deploy.sh asli)
  dan belum di host Linux/systemd nyata.

## G18.8 SEC-129 (r11): modul Odoo wajib dicek saat deploy (14 Okt 2026, belum di-commit)

- `deploy-validate.sh`: `EFN_REQUIRED_ODOO_MODULES=(efn_pos_refund_origin)` + `efn_odoo_modules_missing` (stdin `nama|state`, fail-closed: kosong/absent/state lain = gagal).
- `deploy.sh`: bila DB `efn_odoo` sudah ter-install (`pos_order` ada), sebelum grant ETL: `SELECT name || '|' || state FROM ir_module_module WHERE name IN (...)` lewat jalur DB deploy (user `efn_odoo`, socket container postgres) -> deploy gagal dengan pesan `<modul>=<state>` + runbook. Catatan: `deploy.sh` tidak meng-upgrade modul Odoo (install/upgrade = langkah manual runbook); cek dipasang di blok Odoo yang sudah ada.
- Runbook baru `docs/runbooks/deploy-odoo-modules.md` (+ index README). Test efn-deploy 132-138 -> **140/140**; shellcheck bersih. Query diuji nyata di DB dev (`efn_pos_refund_origin|installed` -> lulus).
- Belum: perintah install di runbook (`odoo ... -u base -i efn_pos_refund_origin --stop-after-init` lewat entrypoint EFN) belum dijalankan di staging.

## G18.9 G18-QA-02: reset/restore DB Odoo ikut mereset data turunan (15 Okt 2026, belum di-commit)

- `docs/runbooks/dev-stack-boot.md` §3e (baru, dev): stop etl+api-gateway -> BFF: hapus `online_catalog_*`, data uji online (urutan DELETE aman; **bukan** `TRUNCATE online_order CASCADE` karena FK melingkar ikut mengosongkan semua `payment_intent` - terverifikasi di DB dev), inbox `processed_message` consumer `online.order-events` -> ClickHouse: TRUNCATE mart sumber Odoo + snapshot/recon + salinan audit (dev) + `etl_watermark` -> Redis (`ocat:`/`oq:`/`cart:`) dikosongkan -> `ETL_LOYALTY_RECON_NOT_BEFORE` = waktu reset -> up. SQL BFF diuji dry-run (BEGIN/ROLLBACK) di dev; helper `chq` diuji.
- `docs/runbooks/backup-restore.md` §4 (DR produksi): catat T; sebelum etl/api-gateway/odoo naik: `ALTER TABLE ... DELETE WHERE <kolom versi> > T` untuk 11 tabel turunan + watermark semua job -> epoch (muat ulang penuh; "T - overlap" tidak cukup karena versi lama bisa sudah ter-merge); salinan audit & riwayat recon TIDAK diubah; Redis dikosongkan (volume AOF; tidak ada user FLUSHALL); DLQ/online-resolve sync; rekonsiliasi provider bayar; `ETL_LOYALTY_RECON_NOT_BEFORE` tidak dimajukan; ETL terakhir; satu siklus rekonsiliasi loyalty+audit ditinjau sebelum `ONLINE_ENABLED`. SQL ClickHouse diuji sintaksnya di dev dengan T masa depan (no-op).
- ⚠ **Celah untuk data-AI:** `audit_copy` melanjutkan dari `max(id)` tersalin -> setelah restore, log audit Odoo baru ber-id <= id lama tidak tersalin (perlu "epoch rantai"). Didokumentasikan sebagai celah terbuka di DR.
- Review perubahan QA `docker-compose.dev.yml` (`ETL_LOYALTY_RECON_NOT_BEFORE: ${...:-2026-10-07T07:09:00Z}`): OK - hanya overlay dev, override `.env` bekerja; catatan: nilai kosong di `.env` juga jatuh ke default (dev tidak bisa "tanpa batas"), dapat diterima.

## G18.10 Epoch rantai salinan audit (lanjutan data-AI a769202) (15 Okt 2026, belum di-commit)

- `promtail.yml`: regex jangkar `EFN_AUDIT_CHAIN_HEAD` menerima `,"epoch":N` opsional; tahap baru (3b) `EFN_AUDIT_EPOCH` berjangkar (logger efn_etl.audit_job, level INFO/WARNING/ERROR, event allowlist register|new_epoch|divergence_blocked) -> `stream=efn_audit_anchor, audit_event=epoch` (retensi 400 h). Journal tidak relevan (ETL = log container). Fixture +5 baris (epoch sah, epoch non-angka, new_epoch, divergence_blocked, logger palsu); expected 88 entri, ditinjau.
- `loki-rules/efn-ops-logs.yml` grup `efn-audit-epoch`: `EfnAuditEpochDivergenceBlocked` (critical, security) dan `EfnAuditEpochNew` (warning). LogQL diuji parse di Loki dev (query instan sukses).
- `backup-restore.md` §4 langkah 6c menggantikan catatan celah: grant `odoo-readonly-grants.sql` + penanda `efn_audit.chain_epoch=restore-<UTC>` lewat ORM (`odoo shell` container sekali pakai, sebelum odoo/etl naik); `etl-audit-copy.md` §Epoch + 2 runbook alert; index README.
- `ETL_AUDIT_EPOCH_AUTO`: dev overlay `"true"`; base/prod literal `"false"`; `EFN_DEV_ONLY_SWITCHES` (deploy-validate menolak di staging/production; kunci manifest ditolak efn-deploy); `check-encryption` menolak render prod bila true. `ETL_AUDIT_EPOCH_ANCHORS` (20, 1..1000) = config rilis (compose, .env.example, deploy-validate, deploy.yml, deploy.sh).
- Validasi: shellcheck bersih, promtail test OK (88), efn-deploy **145/145** (+101b, 131b-e), rotasi 48/48, online-cs 54/54, compose dev/prod OK, check-encryption OK, promtool SUCCESS, actionlint rc 0, yamllint OK, checkov 0 failed.

## G18.11 CI PR #60 setelah repo PUBLIK di organisasi BIma-Creative-Tech (18 Okt 2026, belum di-commit)

- **gitleaks:** `gitleaks-action` dihapus (organisasi wajib lisensi). Satu langkah CLI `zricethezav/gitleaks:v8.30.1@sha256:c00b6bd0…` untuk semua event: PR = `--log-opts <base>..<head>` (SHA dari payload lewat env, divalidasi 40 hex + `git cat-file`), push/jadwal/dispatch = riwayat penuh (SEC-89). Izin job turun ke `contents: read`. Diuji lokal: 40 commit rentang PR, "no leaks found".
- **Trivy image:** semua temuan punya fix (OS): ai-ocr-worker libcurl4t64 (CVE-2026-8286/8927/92392 -> 8.14.1-2+deb13u7) + libexpat1 (CVE-2026-66046/76956/76957/77214/93990 -> 2.9.0-1~deb13u1): base `python:3.12-slim` dinaikkan ke digest 6 Okt (`a6e34c59…`) + `apt-get upgrade -y` di layer apt; metabase libexpat (CVE-2026-77214 -> 2.9.0-r0): `apk add --no-cache --upgrade libexpat` (hanya paket itu). Tidak ada CVE pip -> requirements.txt tidak diubah; **tidak ada pengecualian baru** di `.trivyignore.yaml`. Verifikasi lokal: build `--no-cache` + Trivy 0.69.3 (flag CI: HIGH/CRITICAL, ignore-unfixed, os+library, ignorefile) = 0 temuan kedua image; pytest OCR di image baru lulus.
- **Publik:** `security-events: write` sudah ada di semgrep/trivy-fs/checkov/CodeQL; ditambahkan ke job `build` (build-push) bersama `attestations: write` (attest-build-provenance). **Temuan tambahan:** owner `BIma-Creative-Tech` huruf besar -> `IMAGE_REPO=ghcr.io/${{ github.repository_owner }}/…` tidak valid untuk push OCI; kini dihitung huruf kecil di langkah pertama job `build` (GITHUB_ENV), dipakai metadata/buildx/Trivy/Syft/cosign/attest; `IMAGE_NS` dihapus (deploy.yml sudah `${OWNER,,}`).
- **Dependency review:** butuh "Dependency graph" aktif = **tindakan admin org** (runbook §0). Runbook `ci-github-free-plan.md` §0 baru: tabel perubahan publik/organisasi, deploy.conf host wajib diperbarui ke identitas cosign repo baru (`EFN_SIGNER_IDENTITY_REGEXP`, `EFN_GITHUB_REPO`, `EFN_IMAGE_PREFIX` huruf kecil), GHCR tetap privat, larangan `pull_request_target`, approval run dari kontributor luar.
- Validasi: actionlint rc 0, yamllint OK (warning lama baris pytest metabase), checkov 0 failed, hadolint lulus (info DL3059 lama).
- Di luar cakupan (gagal juga di PR #60): job **Odoo addon tests** (ERROR di log install/test) - bukan perubahan devsecops.

## G44 forecast-worker + kode PT SMD (18 Nov 2026, worktree `efn-platform-g44`, branch `feat/wave-44-supermarket-forecast`, belum di-commit)

Dasar: docs/04 §28.2 / §28.10 / §12.1 (revisi 18 Nov), ADR-046 (Proposed), K25-02, K25-06. Tidak ada kode `services/*`
yang diubah (forecast-worker ditulis efn-data-ai-engineer paralel).

### Keputusan
- **Overlay `infra/compose/docker-compose.forecast.yml`** (pola `docker-compose.esb.yml` G41), profil `data`: image belum
  di rantai rilis bertanda tangan; tanpa overlay tidak ada service yang memegang API key Odoo forecast. Repo belum punya
  compose project `efn-data` terpisah (stack data = profil `data` di project `efn`), jadi overlay = cara paling dekat
  K25-02 tanpa mengunci desain antar-mesin.
- **Jadwal = loop internal** (`FORECAST_DAILY_CRON`, default `30 23 * * *` WIB, ADR-046 §3.1), konsisten dengan `etl` /
  `esb-extract`; tidak ada ofelia/cron container (butuh docker.sock).
- **Flag = `FORECAST_ENABLED`** (nama kontrak §28.10), bukan `FORECAST_WORKER_ENABLED` dari brief: kontrak menang.
- **Network**: dua network internal baru, `forecast-ch` (clickhouse ↔ worker, 172.30.11.0/24) dan `forecast-rpc`
  (odoo-rpc-gateway ↔ worker, 172.30.12.0/24) — gateway (jalur ke ERP) tidak berbagi network dengan ClickHouse.
- **Gateway**: allowlist **per CN** — `cn_bff` hanya 24 path lama, `cn_forecast` hanya path #25–#29 (§18.1 berakhir di
  #24); body path forecast ≤ 512 KiB (§28.2; default 256 KiB tetap); backend terpisah `odoo_forecast` `maxconn 1`
  (antrean 4 × 10 s → 503) agar batch tidak memakan slot `ODOO_RPC_MAXCONN` kanal online.
- **Hardening**: user 65534, `read_only`, tmpfs `/tmp` 256m (NUMBA_CACHE_DIR/HOME di tmpfs), `cap_drop ALL`,
  `no-new-privileges`, memori `FORECAST_MEMORY_LIMIT` default 1536M (ADR-046: 1–2 GB batch; dev boleh 768M), CPU 2,
  healthcheck PID 1 (pola etl).

### Perubahan
- `infra/compose/docker-compose.forecast.yml` (baru); `docker-compose.yml`: secret `clickhouse_password_forecast` +
  mount ke `clickhouse-init`, komentar subnet/overlay; `.env.example`: blok `FORECAST_*`, `IMAGE_FORECAST_WORKER`.
- `infra/odoo-rpc-gateway/haproxy.cfg` (cn_forecast, p_forecast, batas body, backend odoo_forecast), `test-gateway.sh`
  (+27 kasus), `README.md` (tabel path #25–#29).
- `infra/clickhouse/clickhouse-init.sh`: `efn_forecast_reader` kini **wajib** (data-AI sebelumnya membuatnya bersyarat
  menunggu mount secret; mount sudah ada di base) + binding role/profil `forecast_reader`/`forecast_readonly`
  (role dari `access/forecast-access.sql` milik data-AI).
- `scripts/secrets.list`: `clickhouse_password_forecast random`, `odoo_api_key_forecast_smd host:disabled`,
  `tls_forecast_worker_{crt,key} pki`. `scripts/gen-pki.sh`: `forecast-worker|client` (penerbitan leaf saja:
  `bash scripts/gen-pki.sh forecast-worker`). `gen-secrets.sh` tidak perlu diubah (digerakkan secrets.list).
- `scripts/check-encryption.sh`: §6b5 least privilege secret/network/hardening forecast-worker, env `*_FILE` cocok,
  `ODOO_RPC_URL` = gateway; render `dev-forecast` + `prod-forecast` (FORECAST_ENABLED default false); ACL CN haproxy
  hanya `cn_bff`/`cn_forecast` `-m str`.
- `.github/workflows/ci.yml`: path-filter `forecast-worker`, ruff/compileall/pip-audit/pytest matrix, PKI EKU klien,
  `docker compose config` overlay forecast (sendiri + bersama ESB, dev & prod), cek secrets.list ikut overlay forecast.
  `build-push.yml`: forecast-worker di `scan-pr` (Trivy gate HIGH/CRITICAL) dan `build` (push by digest, Trivy, SBOM,
  cosign). hadolint otomatis mencakup Dockerfile baru (find).
- Runbook baru `docs/runbooks/forecast-worker.md`; indeks `docs/runbooks/README.md`; `internal-pki.md` §G44.

### Tambahan koordinator: CHECK kode PT `SMD` di PostgreSQL platform
- `infra/postgres/init/19-platform-g44.sql` (18 dicadangkan G43): DROP/ADD NOT VALID/VALIDATE 12 CHECK
  (`loyalty_ledger`, `payment_intent`, `loyalty_reservation`, `loyalty_pos_earn`, `online_order_part`,
  `payment_intent_portion`, `loyalty_pos_reverse`, `loyalty_policy`, `mgm_policy`, `mgm_source`, `mgm_reward`,
  `ck_loyalty_ledger_issuer`) dengan daftar `EFN, GL, PB, BCR, BCF, WTZ, FARM, SMD`; idempoten, transaksional, pola 13.
  **Pagar merge**: bila constraint yang ada memuat kode di luar daftar (mis. IMD/CUP dari `18-platform-g43.sql`),
  migrasi `RAISE EXCEPTION` (tidak diam-diam menyempitkan). QR/booking (`PB`,`BCF`) tidak diubah.
- `scripts/deploy.sh`: `19-platform-g44.sql` masuk daftar migrasi WAJIB.
- `scripts/lib/deploy-validate.sh`: `EFN_COMPANY_CODES=(EFN GL PB BCR BCF WTZ FARM SMD)` (§12.1) +
  `efn_validate_company_code` + `efn_validate_company_lists` (daftar kanal/QR/booking/promo wajib subset; dipanggil
  `efn_validate_config`). SMD **tidak** ditambahkan ke kanal online/QR/booking/promo. `test-efn-deploy.sh` +6 kasus
  (92s–92x).
- **Saat G43 merge**: `code_list` di `19-platform-g44.sql` dan `EFN_COMPANY_CODES` WAJIB menjadi
  `'EFN','GL','PB','BCR','BCF','WTZ','IMD','CUP','FARM','SMD'` (pagar merge akan menggagalkan migrasi bila lupa).

### Belum terverifikasi / sisa
- Tidak ada docker/bash/shellcheck/haproxy yang bisa dijalankan dari sesi ini: `docker compose config`, `haproxy -c`,
  `check-encryption.sh`, shellcheck, yamllint, `test-gateway.sh`, `test-efn-deploy.sh`, dan migrasi 19 terhadap
  PostgreSQL nyata **belum dijalankan** — gerbang = CI job `infra` + uji manual di stack dev.
- Interface env worker (`FORECAST_ENABLED`, `FORECAST_DAILY_CRON`, `FORECAST_TZ`, `FORECAST_HORIZON_DAYS`,
  `FORECAST_CHUNK_SIZE`, `ODOO_RPC_URL`, `ODOO_RPC_CA_FILE`, `ODOO_RPC_CLIENT_CERT_FILE`, `ODOO_RPC_CLIENT_KEY_FILE`,
  `ODOO_RPC_TIMEOUT_SECONDS`, `ODOO_API_KEY_FORECAST_SMD_FILE`, `CH_*` dengan `CH_DATABASE=efn_forecast`) ditetapkan di
  sini; efn-data-ai-engineer harus menyamakan (worker juga wajib menolak placeholder `disabled`, retry 503, berurutan).
- CI job pytest/build-push gagal bila `services/forecast-worker` (Dockerfile, requirements.txt, tests/) belum ada saat PR.
- Staging/prod: intermediate PKI **wajib diterbitkan ulang** (nameConstraints) sebelum deploy G44 — tanpa itu deploy.sh
  berhenti untuk semua service; GitHub Environment `CLICKHOUSE_PASSWORD_FORECAST` wajib diisi.
- Belum: wiring rilis (IMAGE_FORECAST_WORKER di deploy.yml/efn-deploy/deploy.sh, FORECAST_* di
  EFN_RELEASE_CONFIG_VARS, overlay otomatis), rotasi otomatis key `FORECAST-SMD` di `efn-rotate-online-key`, alert
  `EfnForecastRunFailed` (worker tanpa metrik), jalur antar-mesin WireGuard (K25-02), `.github/dependabot.yml` dan
  `Makefile test-py` (di luar izin path).
- Odoo `RPC_ALLOWLIST` harus memuat 5 metode yang sama (efn-odoo-engineer); `ODOO_WORKERS` + 1 selama batch.

## G44 lanjutan: SEC-214, SEC-215, wiring rilis forecast (19 Nov 2026, worktree `efn-platform-g44`, branch `feat/wave-45-supermarket-expiry`, belum di-commit)

Dasar: `docs/reviews/SECURITY_REVIEW_2026-11-18_g44.md` (SEC-214 Low, SEC-215 Info) + sisa laporan G44 di atas. Tidak ada
kode Odoo/worker yang diubah (hanya `Dockerfile` + `requirements.lock` forecast-worker).

### SEC-214: lock dependency ber-hash
- Repo belum punya pola lock Python (semua service `pip install -r requirements.txt`) → pola baru, dipakai forecast-worker
  dulu: `requirements.txt` tetap input pin langsung (review + pip-audit), `requirements.lock` = keluaran
  `pip-compile --generate-hashes` (Python 3.12 linux, image = `FROM` Dockerfile).
- `services/forecast-worker/Dockerfile`: `pip install --require-hashes --no-deps -r requirements.lock` + `pip check`;
  lock placeholder / tanpa hash → build gagal dengan pesan jelas (fail closed).
- `services/forecast-worker/requirements.lock`: **placeholder** (`EFN_LOCK_PLACEHOLDER`), hash tidak dikarang.
- `scripts/lock-python-deps.sh <svc> [--upgrade]`: pip-tools 7.5.1 di container image base service, idempoten
  (mempertahankan pin lock lama), tulis atomik, menangani Git Bash (`MSYS_NO_PATHCONV`, `pwd -W`).
- `scripts/check-py-lock.sh`: bash + awk, tanpa jaringan: placeholder, header Python 3.12, setiap entri `==` + hash
  sha256 64 hex, tanpa opsi indeks/URL, himpunan dependency langsung (`via -r requirements.txt`) dan versi = pin
  requirements.txt.
- CI `ci.yml`: langkah check-py-lock di job `python`; pip-audit atas lock (`--require-hashes --disable-pip`, transitif
  ikut); job pytest memasang lock ber-hash + `pip check` untuk service ber-lock; cache pip ikut lock; path-filter memuat
  kedua skrip. Runbook baru `docs/runbooks/python-dependency-lock.md` (perintah persis, cek lisensi transitif
  `pip-licenses`, penanganan hash mismatch sebagai dugaan supply chain).

### SEC-215: hardening ClickHouse + alert
- `clickhouse-init.sh`: `create_user` menerima CIDR opsional → `efn_forecast_reader` dibuat/di-ALTER `HOST IP
  '<CH_FORECAST_READER_HOST_CIDR>'` (default 172.30.11.0/24 = ipam `forecast-ch`; validasi CIDR privat /16../32 di sh).
  User lain tetap `HOST ANY` (tidak diubah, di luar temuan).
- Grant wildcard `efn_forecast.*` **tidak** diganti grant per view: `services/forecast-worker/tests/test_access_sql.py`
  (milik data-AI, di luar izin path) mengunci baris `GRANT SELECT ON efn_forecast.* TO forecast_reader;`. Pengganti
  setara: pagar fail-closed di clickhouse-init — `efn_forecast` hanya boleh berisi 6 VIEW allowlist (= `VIEW_COLUMNS`);
  objek lain → `REVOKE SELECT ON efn_forecast.* FROM forecast_reader` + init gagal (pola pagar row policy insight).
- Compose base: env `CH_FORECAST_READER_HOST_CIDR` di `clickhouse-init`; `.env.example` + config rilis.
- Alert Loki baru `infra/observability/loki-rules/efn-forecast.yml` (sumber log `EFN_FORECAST_RUN`, konsisten dengan
  dead-man ETL berbasis log; worker tanpa metrik): `EfnForecastRunFailed` (failed / batch tak dapat dimulai, 2 jam),
  `EfnForecastOdooAuthFailure` (`odoo_auth*`/`odoo_company_mismatch*`), `EfnForecastRunStale` (tanpa run harian ok
  > 26 jam **dan** bukti worker aktif dalam 4 hari → tidak berisik di env yang mematikan forecast). Semua warning.
- Pengingat kedaluwarsa key 35 hari (pola metrik textfile `efn-rotate-online-key`): skrip host baru
  `scripts/host/efn-forecast-key-status` (root, hanya membaca id + kedaluwarsa `res.users.apikeys` via `odoo shell`,
  tanpa nilai key; deret per PT hanya bila `.env` `FORECAST_ENABLED=true`) + unit systemd harian 07:30 WIB; grup
  Prometheus `efn-forecast-key`: `EfnForecastApiKeyExpiringSoon` (< 7 hari), `EfnForecastApiKeyMissing`,
  `EfnForecastApiKeyCheckFailed`, `EfnForecastApiKeyCheckNotRunning`.

### Wiring rilis G44
- `IMAGE_FORECAST_WORKER` di `deploy.yml` (cosign verify + manifest), `scripts/host/efn-deploy` (`IMAGE_VARS`, 11 image),
  `scripts/deploy.sh` (cek digest + `.env`). Image selalu di manifest; compose memakainya hanya dengan overlay.
- `deploy-validate.sh`: `FORECAST_ENABLED FORECAST_DAILY_CRON FORECAST_TZ FORECAST_HORIZON_DAYS FORECAST_CHUNK_SIZE
  FORECAST_ODOO_RPC_TIMEOUT_SECONDS FORECAST_MEMORY_LIMIT FORECAST_CPUS CH_FORECAST_READER_HOST_CIDR` di
  `EFN_RELEASE_CONFIG_VARS` + `efn_validate_forecast_config` (regex/rentang = config.py worker & gateway);
  `FORECAST_ENABLED` masuk `EFN_GATED_FEATURES` (production true hanya dengan `EFN_ALLOW_FEATURE_ON`);
  `EFN_FORECAST_COMPANY_CODES=(SMD)` ikut `efn_validate_company_lists`.
- `deploy.sh`: overlay `docker-compose.forecast.yml` otomatis bila `FORECAST_ENABLED=true` (pola overlay QR/booking/
  promo; ESB sendiri belum di rantai rilis), syarat profil `data` + API key forecast nyata per PT; cron dikutip tunggal
  di `.env`. `deploy.yml`: `CFG_FORECAST_*` + `CFG_CH_FORECAST_READER_HOST_CIDR`.
- `check-encryption.sh`: `CH_FORECAST_READER_HOST_CIDR` wajib /16../32 dan = subnet ipam `forecast-ch` saat overlay
  ter-render; grep `create_user efn_forecast_reader ... "$FORECAST_CIDR"` + pagar REVOKE.
- `test-efn-deploy.sh`: `forecast-worker` di SVCS; kasus 274a–274h (manifest tanpa/salah image forecast, stdin ditolak,
  gerbang production, cron berspasi utuh sampai deploy.sh, CIDR 0.0.0.0/0), 275a–275z (unit validasi), 276 (daftar
  IMAGE_* identik di efn-deploy / deploy.sh / .env / deploy.yml).
- Runbook `forecast-worker.md` (§4a pengingat key, alert run, §5a run basi, §8 production), indeks runbook.

### Belum terverifikasi
- Tidak ada docker/bash/shellcheck/yamllint/promtool di sesi ini: `check-py-lock.sh`, `lock-python-deps.sh`,
  `test-efn-deploy.sh`, `check-encryption.sh`, shellcheck (termasuk `clickhouse-init.sh -s sh`), `docker compose config`,
  promtool atas grup `efn-forecast-key`, dan build image **belum dijalankan**.
- LogQL `sum(absent_over_time(...)) and sum(count_over_time(...)) > 0` dan `regexp` non-capturing belum diuji di Loki
  3.2 (CI tidak memvalidasi loki-rules selain yamllint).
- `HOST IP` ClickHouse 24.8 terhadap klien IPv4 lewat listener `::` (alamat IPv4-mapped) belum diuji runtime; bila
  worker ditolak login, periksa `system.query_log`/log server dan runbook §5 langkah 5.
- `efn-forecast-key-status`: model `res.users.apikeys` CE 19 (`expiration_date`) diasumsikan sama dengan yang dipakai
  `efn-rotate-online-key`; belum dijalankan terhadap Odoo nyata.
- pip-tools 7.5.1 + pip di image base belum dicoba (`lock-python-deps.sh` belum pernah jalan).

### Dampak & yang harus dilakukan
- Sampai lock dibuat: job CI `python` (juga PR Odoo-only, karena job itu berjalan untuk perubahan Odoo), pytest
  forecast-worker, build-push forecast-worker, dan deploy.yml (image forecast-worker wajib bertanda tangan) **gagal**.
- Wrapper `efn-deploy` di host staging/prod wajib dipasang ulang sebelum rilis berikutnya (jumlah image manifest 11).
- Rekomendasi ke efn-data-ai-engineer: ganti grant wildcard dengan 6 `GRANT SELECT ON efn_forecast.<view>` + ubah
  assertion `test_reader_role_only_reads_efn_forecast`; pagar clickhouse-init tetap dipertahankan. Catat hasil cek
  lisensi transitif di README forecast-worker.

## G45: SEC-223 (hardening forecast) + SEC-216 sisi observability (19 Nov 2026, worktree `efn-platform-g44`, branch `feat/wave-45-supermarket-expiry`, belum di-commit)

Acuan: `docs/reviews/SECURITY_REVIEW_2026-11-19_g45.md` SEC-216 (rekomendasi 3) dan SEC-223. Status dari lead sebelum
kerja ini: lock forecast-worker OK, test-efn-deploy 399/0, shellcheck bersih (perbaikan lead SC2034 `check-py-lock.sh`
dan disable SC2016 `check-encryption.sh` dipertahankan).

### SEC-223 (a): CIDR HOST user forecast minimal /24
- `infra/clickhouse/clickhouse-init.sh` `private_cidr_ok`: prefix 24..32 (sebelumnya 16..32). 172.30.0.0/16 mencakup
  semua network EFN.
- `scripts/lib/deploy-validate.sh`: `>= 24` + pesan /24../32.
- `scripts/check-encryption.sh`: regex env compose `/(2[4-9]|3[0-2])$`; cek statis baru bahwa clickhouse-init berisi
  `[ "$c_p" -lt 24 ]` (disable SC2016 per perintah, literal).
- `scripts/host/test-efn-deploy.sh`: 275ya–275ye (/16, /23, 10.88.0.0/20 ditolak; /32 WireGuard dan /28 diterima) +
  275yf (fungsi `private_cidr_ok` diekstrak dari clickhouse-init.sh dengan awk lalu dijalankan di `sh`: 3 nilai
  diterima, 6 ditolak termasuk injeksi `';DROP` dan tanpa prefix). Jumlah test naik 6 → harapan 405/0.
- Komentar `docker-compose.yml` (clickhouse-init env) dan runbook forecast-worker §7 diperbarui.

### SEC-223 (b): pagar definisi view `efn_forecast`
- clickhouse-init.sh, setelah pagar allowlist nama/engine: bila `system.columns` menunjukkan kolom
  `system.tables.create_table_query` ada (CH 24.8: ada), setiap view `efn_forecast` (setelah backtick dibuang dan
  whitespace dinormalkan) WAJIB cocok
  `DEFINER = efn_forecast_definer SQL SECURITY DEFINER AS SELECT [A-Za-z0-9_, ]+ FROM efn_dwh[.]v_fc_<nama tanpa v_> ?$`
  dan memuat tepat satu `SELECT ` dan satu ` FROM ` (menutup UNION/JOIN/subquery/table function/db lain). Menyimpang →
  `REVOKE SELECT ON efn_forecast.* FROM forecast_reader` + exit 1 (pola SEC-215). Kolom tidak ada → PERINGATAN dan
  pagar dilewati (sesuai instruksi "bila CH mengeksposnya"). Regex tanpa backslash (`[.]`, `[[:space:]]`) agar tidak
  bergantung escape string literal ClickHouse.
- check-encryption.sh: cek statis keberadaan pola regex + blok `if [ -n "$FC_BADDEF" ]`.
- Runbook forecast-worker §5 langkah 5: pesan "definisi view efn_forecast menyimpang" + query diagnosis.

### SEC-223 (c): key forecast tanpa kedaluwarsa → metrik + alert
- `scripts/host/efn-forecast-key-status`: metrik baru `efn_forecast_api_key_no_expiry_keys{company}`. Sekaligus
  diperbaiki: sampel kini dikelompokkan per family (sebelumnya sampel `expiry` dan `active` ditulis setelah kedua blok
  HELP/TYPE dan berselang-seling per PT → format teks Prometheus tidak rapi bila PT > 1).
- `infra/observability/alert-rules.yml` grup `efn-forecast-key`: `EfnForecastApiKeyNoExpiry`
  (`max by (company) (efn_forecast_api_key_no_expiry_keys) > 0`, warning, tanpa `for` karena metrik harian).
- Runbook forecast-worker §4b `#key-tanpa-kedaluwarsa` + baris tabel §4a; indeks runbook.
- Catatan: key tanpa kedaluwarsa tetap dihitung di `active_keys` (perilaku lama, agar `EfnForecastApiKeyMissing` tidak
  salah menyala); `expiry_timestamp` tetap 0 bila hanya ada key tanpa kedaluwarsa (tidak memicu ExpiringSoon).

### SEC-216: alert Loki tebakan PIN supervisor POS
- Format aktual Odoo (dibaca dari `efn_pos_refund_origin/models/return_pin.py` `log_pin_failure`, bukan dari brief):
  `EFN_POS_PIN_FAIL path=<return_approval|expiry_override> config=<id> cashier=<uid> approver=<uid>
  reason=<wrong_pin|locked|cashier_locked>`, level WARNING, logger modul pemanggil (`odoo.addons.efn_*...`). Brief lead
  menyebut `config_id`/`cashier_uid`/`approver_uid`; regex mengikuti kode (`config=`/`cashier=`/`approver=`).
- File baru `infra/observability/loki-rules/efn-pos-pin.yml` grup `efn-pos-pin`:
  - `EfnPosPinFailures` ≥ 5 / 15 menit per `config_id` → warning, team security;
  - `EfnPosPinBruteForce` ≥ 10 / 15 menit per `config_id` → critical;
  - `EfnPosPinLogFormatUnrecognized` (penjaga format, logger `odoo.addons.efn_*` + tag tetapi skema tidak cocok, 1 jam)
    → warning.
  Regex berjangkar penuh (timestamp, pid, WARNING, logger efn_*, skema key=value, path/reason allowlist); `config_id`
  diekstrak di query (bukan label promtail). Tanpa perubahan promtail (baris tanpa PII, retensi default).
- `alertmanager.yml`: inhibit `EfnPosPinBruteForce` → `EfnPosPinFailures` (`equal: [config_id]`).
- Runbook baru `docs/runbooks/pos-pin-bruteforce.md` (anchor per alert) + indeks.
- Saat file ini ditulis, jalur `expiry_override` di `efn_expiry_monitor/models/pos_config.py` masih memakai
  `_logger.warning("EFN expiry override: PIN salah ...")` lama (belum memanggil `log_pin_failure`) → kegagalan override
  kedaluwarsa belum terhitung alert sampai efn-odoo-engineer selesai. Baris lama itu tidak memuat tag, jadi tidak
  memicu penjaga format.

### Belum terverifikasi
- Tidak ada shell/docker di sesi ini: shellcheck, `test-efn-deploy.sh` (275ya–275yf), `check-encryption.sh`,
  `docker compose config`, promtool (`EfnForecastApiKeyNoExpiry`), dan amtool (inhibit) **belum dijalankan**.
- Pagar definisi view: bentuk `create_table_query` CH 24.8 untuk view `SQL SECURITY DEFINER` diasumsikan
  `CREATE VIEW db.v (<kolom>) DEFINER = efn_forecast_definer SQL SECURITY DEFINER AS SELECT a, b FROM efn_dwh.v_fc_x`
  (urutan sesuai format SHOW CREATE ClickHouse 24.x). Belum dijalankan terhadap ClickHouse nyata. Bila formatnya beda
  (mis. alias kolom ditulis ulang), init GAGAL tertutup dan grant forecast dicabut → jalankan
  `SELECT name, create_table_query FROM system.tables WHERE database='efn_forecast'` di stack dev sebelum merge dan
  sesuaikan regex. `countSubstrings`, `replaceRegexpAll` dengan `[[:space:]]` (RE2) diasumsikan tersedia di 24.8.
- LogQL `efn-pos-pin.yml` (`|~` + `!~` berantai, `| regexp` sesudah filter) belum diuji di ruler Loki; CI tidak
  memvalidasi loki-rules selain yamllint.
- Test 275yf memakai `sh` di container test (diasumsikan ada, image Debian).

### Residual (bukan cakupan)
- View `efn_dwh.v_fc_*` (INVOKER, dieksekusi dengan hak `efn_forecast_definer`) tidak dipagari definisinya; pengubahnya
  butuh hak DDL admin ClickHouse, dan hak definer sudah dibatasi ke 14 objek eksplisit (forecast-access.sql).
- Alert per `config`: tebakan yang berpindah register dalam satu outlet bisa di bawah ambang per config (lockout server
  per approver/kasir tetap berlaku); dicatat di runbook "Batas yang diketahui".
