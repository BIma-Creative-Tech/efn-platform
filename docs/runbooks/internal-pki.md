# Runbook: PKI internal & TLS/mTLS antar-service

Alert: `EfnInternalTlsHandshakeFailed` (critical), `EfnInternalCertExpiringSoon` (< 21 hari, warning), `EfnInternalCertExpiryCritical` (< 7 hari, critical).
Kontrak: `docs/04_SERVICE_CONTRACTS.md` §7.1, §8.5 (mTLS), §8.6 (kustodi); ADR-022 Keputusan 1 (W1 selesai). Alat: `scripts/gen-pki.sh`, `scripts/deploy.sh`, `scripts/check-encryption.sh`. Kustodi kunci: [key-custody.md](key-custody.md).

## Ringkasan desain (gelombang 5)

| Tingkat | Nilai |
|---|---|
| Root CA | ECDSA P-256, 10 tahun, `pathlen:1`, kunci terenkripsi passphrase, **offline** di mesin Superadmin (`make pki-root`). Tidak pernah di server/CI |
| Intermediate per lingkungan | `staging`, `production`; 2 tahun, `pathlen:0`, **`nameConstraints`** = nama service compose (+ `efn-<svc>-1`) + `.efn.internal` + `localhost` + IP 127.0.0.1. Diterbitkan Superadmin (`make pki-intermediate ENV=..`), kunci di GitHub Environment `EFN_PKI_INTERMEDIATE_KEY` → tmpfs host selama deploy saja |
| Leaf | ECDSA P-256, **90 hari**, diterbitkan **di host oleh `deploy.sh`** bila belum ada / sisa < 30 hari / issuer berubah / profil EKU berubah. Container pemakainya otomatis di-recreate |
| Profil EKU | `server` (serverAuth saja) untuk server murni; `both` untuk service yang juga klien mTLS (traefik, prometheus, grafana, blackbox-exporter, ocr-worker, cadvisor); `client` untuk promtail. Sertifikat server murni **tidak bisa** dipakai sebagai klien mTLS |
| Trust (`efn_ca_crt`) | **Bundle** intermediate + root (+ `EFN_PKI_EXTRA_TRUST` selama rotasi) |
| Dev/CI | `make secrets` membuat root + intermediate **DEV** otomatis di `secrets/pki-ca/` (3 tingkat, sama dengan produksi) |

Efek `nameConstraints` (SEC-23): kunci intermediate yang bocor tidak bisa memalsukan domain publik (mis. `graph.facebook.com`) — CI membuktikannya dengan sertifikat palsu yang harus gagal diverifikasi. BFF juga tidak lagi memakai `NODE_EXTRA_CA_CERTS` (CA internal tidak dipercaya untuk egress publik).

### Matriks mTLS (docs/04 §8.5)

| Server | Mekanisme | Klien sah |
|---|---|---|
| ghostunnel `odoo-tls`, `odoo-ws-tls`, `superapp-web-tls`, `metabase-tls` | `--cacert` + `--allow-cn` | `traefik` (+ `blackbox-exporter` untuk probe) |
| ghostunnel `ollama-tls` | idem | `ocr-worker` (+ `blackbox-exporter`) |
| ghostunnel `cadvisor-tls` | idem | `prometheus` |
| ghostunnel `docker-socket-proxy-tls` | idem | `traefik`, `promtail`, `cadvisor` |
| Prometheus, Alertmanager | `web-config-ui.yml` RequireAndVerifyClientCert + `client_allowed_sans` | `prometheus`, `grafana` |
| node/postgres/blackbox exporter | `web-config.yml` idem | `prometheus` |
| Loki | `http_tls_config` RequireAndVerifyClientCert | klien ber-EKU clientAuth (promtail, grafana, prometheus, …) — tanpa allowlist SAN (E12) |
| redis_exporter, RabbitMQ :15691, Traefik :8082 | verifikasi sertifikat klien CA internal | klien ber-EKU clientAuth (E12) |
| MinIO metrik | bearer JWT user `efn-prometheus` (`admin:Prometheus`) | Prometheus |
| ClickHouse `/metrics`, BFF `/metrics`, ocr-worker `/metrics` | lihat E11/E12 ADR-022 | Prometheus |

Hop dengan kredensial aplikasi (PostgreSQL, Redis, RabbitMQ AMQP, MinIO S3, ClickHouse SQL, BFF API) tetap server-TLS + password per service (mTLS opsional fase 2).

## Diagnosis

```bash
cd /opt/efn/compose; DC="docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.host.yml"
# Masa berlaku semua sertifikat (root/superadmin)
sudo bash -c 'for f in /opt/efn/secrets/tls_*_crt.txt; do printf "%-45s %s\n" "$(basename "$f")" "$(openssl x509 -in "$f" -noout -enddate | cut -d= -f2)"; done'
# Rantai + nameConstraints
sudo openssl verify -CAfile /opt/efn/secrets/efn_ca_crt.txt /opt/efn/secrets/tls_odoo_crt.txt
# Log penolakan mTLS ghostunnel (CN tidak diizinkan / tanpa sertifikat klien)
$DC logs --tail=100 odoo-tls | grep -i -E 'unauthorized|bad certificate|handshake'
# Prometheus target down karena mTLS: Status > Targets di ops.<domain>, error "tls: certificate required"
```

Penyebab umum `EfnInternalTlsHandshakeFailed`: container belum di-recreate setelah leaf baru (deploy.sh melakukannya otomatis; cek log deploy "Recreate pemakai sertifikat baru"), service baru belum ada di `PKI_SERVICES` **atau** belum tercakup nameConstraints intermediate (terbitkan intermediate baru), klien lupa membawa sertifikat (EKU `server` saja), atau service mati.

## Rotasi leaf (rutin, otomatis)

Tidak ada langkah manual: setiap deploy menerbitkan ulang leaf yang sisa < 30 hari. Alert `EfnInternalCertExpiringSoon` (< 21 hari) = belum ada deploy sejak masuk jendela perpanjangan → jalankan workflow **Deploy** (`workflow_dispatch`, tag yang sedang berjalan). Paksa semua leaf (mis. dugaan kunci leaf bocor): hapus `tls_<svc>_crt.txt` terkait di host sebagai root, lalu deploy.

Dev: `make pki-rotate`.

## Rotasi intermediate (2 tahun, atau bila kunci intermediate bocor)

1. Superadmin: `make pki-intermediate ENV=production` (root offline), unggah 3 secret `EFN_PKI_*`.
2. Deploy → bundle berubah → semua leaf diterbitkan ulang (issuer berubah) → semua pemakai di-recreate. Root sama, jadi klien yang memegang bundle lama tetap memverifikasi leaf baru setelah recreate.
3. Kunci intermediate bocor: perlakukan sebagai insiden ([breach-response-72h.md](breach-response-72h.md)); langkah 1–2 segera; nameConstraints membatasi dampaknya ke nama internal.

## Rotasi root (10 tahun, atau bila root bocor)

1. `make pki-root` di direktori baru (`PKI_ROOT_DIR=~/efn-pki-root-2`), intermediate baru dari root baru.
2. Transisi: set `EFN_PKI_EXTRA_TRUST` = PEM root lama, unggah `EFN_PKI_*` baru, deploy (bundle = intermediate baru + root baru + root lama).
3. Setelah semua leaf baru (satu deploy), kosongkan `EFN_PKI_EXTRA_TRUST`, deploy. Musnahkan root lama sesuai [key-custody.md](key-custody.md).

## Migrasi dari PKI satu tingkat (gelombang 3) — sekali

1. Superadmin membuat root + intermediate (§key-custody 5) dan mengunggah `EFN_PKI_*` ke `staging` lalu `production`.
2. Deploy: leaf lama (diterbitkan CA lama, 365 hari, EKU server+client) gagal `leaf_ok` → semua leaf baru, semua container di-recreate (jendela maintenance ±5 menit).
3. Hapus GitHub secret lama `EFN_CA_CRT` + `TLS_*` dan variable `PKI_SOURCE`. Di host staging: `shred -u /opt/efn/pki-ca/*; rmdir /opt/efn/pki-ca` (deploy.sh memperingatkan selama direktori itu ada).
4. Dev: `make clean` tidak wajib; `make secrets` membuat CA dev baru otomatis bila `secrets/pki-ca/intermediate.crt` belum ada (hapus `secrets/` lama bila ingin bersih).

## Migrasi stack sebelum gelombang 3 (arsip)

| Komponen | Langkah |
|---|---|
| PostgreSQL | Superuser default `postgres`. Volume lama (superuser `efn`): set `vars.PG_SUPERUSER=efn` atau `CREATE ROLE postgres SUPERUSER LOGIN PASSWORD '<pg_password>'`. Role per service dibuat `deploy.sh` (00-roles.sh) |
| Redis | Recreate container (data cache) |
| RabbitMQ | Nodename `rabbit@localhost` → kuras antrean, `docker volume rm efn_rabbitmq-data` |
| MinIO | <a id="minio-object-lock"></a>**Object lock `pg-backup`** hanya saat bucket dibuat: buat `pg-backup-v2` (`mc mb --with-lock`), ubah `WALG_S3_PREFIX` + `MINIO_BUCKET_PG_BACKUP`, base backup baru, hapus bucket lama setelah retensi |
| ClickHouse | `docker volume rm efn_clickhouse-data` lalu `clickhouse-init` + ETL mengisi ulang |
| Metabase | Koneksi ClickHouse di UI: host `clickhouse`, port 8443, SSL on, user `efn_metabase`, sertifikat CA = isi `efn_ca_crt` (bundle) |
