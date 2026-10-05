# Runbook Operasional EFN

Indeks runbook untuk alert Prometheus (`infra/observability/alert-rules.yml`, anotasi `runbook_url`) dan prosedur operasional. Audiens: on-call IT EFN / vendor ops.

## Konvensi di host staging/prod

```bash
cd /opt/efn/compose
DC="docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.host.yml"
$DC ps                       # status + health
$DC logs --tail=200 <service>
```

Layout host: `/opt/efn/{compose,traefik,observability,backup,minio,clickhouse,postgres,wireguard}` (isi `infra/`), `/opt/efn/scripts`, `/opt/efn/etl-sql`, `/opt/efn/secrets` (`root:efn-superadmin 0750`, tidak pernah disinkronkan dari repo; [key-custody.md](key-custody.md)). Grafana: `https://ops.<domain>` (WireGuard subnet staf saja), dashboard **EFN - Platform Overview**.

## Alert → runbook

| Alert | Severity | Runbook |
|---|---|---|
| EfnServiceDown, EfnHealthProbeFailed, EfnPublicEndpointDown, EfnPostgresDown, EfnRedisDown | critical | [service-down.md](service-down.md) |
| EfnOdooHttp5xxHigh | critical | [odoo-http-5xx.md](odoo-http-5xx.md) |
| EfnOcrQueueBacklog, EfnOcrNoConsumer | warning | [rabbitmq-ocr-backlog.md](rabbitmq-ocr-backlog.md) |
| EfnDeadLetterNotEmpty | warning | [rabbitmq-dlq.md](rabbitmq-dlq.md) |
| EfnOtpVerifyFailureRateHigh, EfnOtpSendFailureRateHigh (metrik diusulkan) | warning / critical | [otp-failure-rate.md](otp-failure-rate.md) |
| EfnBackupFailed, EfnBackupStale, EfnBackupMetricMissing, EfnWalArchiveFailing, EfnWalArchiveStale | critical / warning | [backup-failed.md](backup-failed.md) |
| EfnRestoreTestFailed, EfnRestoreTestOverdue | critical / warning | [backup-restore.md](backup-restore.md) |
| EfnDiskUsageHigh, EfnDiskUsageCritical | warning / critical | [disk-usage.md](disk-usage.md) |
| EfnCertExpiringSoon, EfnCertExpiryCritical | warning / critical | [tls-cert-expiry.md](tls-cert-expiry.md) |
| EfnInternalTlsHandshakeFailed, EfnInternalCertExpiringSoon, EfnInternalCertExpiryCritical | critical / warning / critical | [internal-pki.md](internal-pki.md) |

## Prosedur

| Prosedur | Runbook |
|---|---|
| Backup, restore test, pemulihan bencana (DR), migrasi volume PostgreSQL | [backup-restore.md](backup-restore.md) |
| Rotasi secret (semua secret di `scripts/secrets.list`) | [secret-rotation.md](secret-rotation.md) |
| PKI internal 3 tingkat & mTLS: rotasi leaf (otomatis)/intermediate/root, migrasi | [internal-pki.md](internal-pki.md) |
| Kustodi kunci Superadmin, root CA offline, break-glass, audit akses secret | [key-custody.md](key-custody.md) |
| Rotasi kunci enkripsi PII (key ring, re-encrypt) | [pii-key-rotation.md](pii-key-rotation.md) |
| Hub WireGuard + firewall nftables, onboarding outlet (OpenWrt / klien POS) & staf, VLAN Wi-Fi tamu | [wireguard-outlet.md](wireguard-outlet.md) |
| Firewall origin (443 hanya Cloudflare + WireGuard) | [origin-firewall.md](origin-firewall.md) |
| Enkripsi disk host (LUKS2) | [luks-disk.md](luks-disk.md) |
| Kegagalan pelindungan data pribadi 3×24 jam (UU PDP) | [breach-response-72h.md](breach-response-72h.md) |

## Baseline hardening container (`infra/compose/docker-compose.yml`)

Setiap service: `restart`, logging dibatasi 5×20 MB, `no-new-privileges`, limit memori, healthcheck. `cap_drop: [ALL]` dan `read_only` + `tmpfs` dipasang bila image mendukung. Pengecualian yang disengaja:

| Service | Pengecualian | Alasan |
|---|---|---|
| postgres | tidak `read_only` | socket/lock di `/var/run/postgresql` |
| odoo | tidak `read_only` | `entrypoint-efn.sh` merender `/etc/odoo/odoo.conf` |
| rabbitmq, redis | `cap_add` CHOWN/SETUID/SETGID (+DAC_OVERRIDE/FOWNER untuk rabbitmq) | entrypoint resmi chown data lalu turun ke user non-root; wrapper menulis ACL/definitions dari secret |
| minio, ollama | tidak `read_only` | menulis config/runner di luar volume |
| clickhouse, metabase, uptime-kuma | tanpa `cap_drop` | entrypoint chown / butuh IPC_LOCK |
| cadvisor | root (tanpa capability) + `cgroup: host`, mount `/sys/fs/cgroup` & `/var/lib/docker/image` ro | statistik cgroup; tanpa docker.sock/rootfs (SEC-09) |
| loki, prometheus, alertmanager, grafana, exporter | healthcheck Docker dinonaktifkan | endpoint hanya TLS dan image tanpa klien TLS ber-CA; dipantau `up`/probe Prometheus + Uptime Kuma |
| docker-socket-proxy | satu-satunya yang me-mount `docker.sock` (ro), listen 127.0.0.1 | Traefik, Promtail, cAdvisor lewat sidecar ghostunnel mTLS :2376 (GET only) |
| sidecar `*-tls` (ghostunnel) | `network_mode: service:<app>` | TLS untuk app tanpa TLS native; app listen 127.0.0.1 |

Di prod hanya Traefik yang mem-publish port (80/443); dicek otomatis di CI job `infra`.
