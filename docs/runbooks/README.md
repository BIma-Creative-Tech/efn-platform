# Runbook Operasional EFN

Indeks runbook untuk alert Prometheus (`infra/observability/alert-rules.yml`) dan ruler Loki (`infra/observability/loki-rules/`) — anotasi `runbook_url` — serta prosedur operasional. Diperbarui gelombang 12 (7 Okt 2026). Audiens: on-call IT EFN / vendor ops.

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
| EfnWgCertProbeFailed (sertifikat wg-https dari service `acme-dns`, SEC-37) | warning | [acme-dns-token.md](acme-dns-token.md#5-operasi-service-acme-dns) |
| EfnAltchaMaxLevelSpike (ALTCHA adaptif level 3, SEC-34) | warning | [otp-failure-rate.md](otp-failure-rate.md#altcha-level-3) |
| EfnOnlineOrderStuck, EfnOnlineAmountMismatch, EfnOnlineDlqNotEmpty, EfnOnlineCircuitOpen, EfnOnlineCatalogStale, EfnOnlineRefundPending (metrik diusulkan) (penjualan online, G17) | critical / critical / warning / warning / warning / warning | [online-orders.md](online-orders.md) |
| EfnOnlineOdooAuthFailure, EfnOnlineApiKeyExpiringSoon, EfnOnlineApiKeyExpiryCritical, EfnOnlineApiKeyExpiryUnknown, EfnOnlineApiKeyRotationFailed, EfnOnlineApiKeyRotationNotRunning, EfnOnlineApiKeyExtraKeys (API key Odoo kanal online, R-T15, rotasi otomatis W-11) | critical / warning / critical / warning / warning / warning / warning | [odoo-online-api-key.md](odoo-online-api-key.md) |
| EfnDsarErasureForcedOpenOrders (erasure dipaksa setelah 14 hari, W-18) | warning | [online-orders.md](online-orders.md#erasure-paksa) |
| EfnMetabaseHousekeepingStale (akun Metabase tidak aktif 90 hari, W-19) | warning | [retention.md](retention.md#metabase-housekeeping) |

### Alert dari log (ruler Loki, `infra/observability/loki-rules/`)

| Alert | Severity | Runbook |
|---|---|---|
| EfnAuditCriticalAccessChange (G13), EfnAuditControlChange, EfnAuditUnexpectedPurge, EfnAuditRetentionPurgeBurst, EfnAuditSinkUnsigned (G13), EfnAuditSinkPiiPattern (G15b, deteksi PII lolos mask) (sink audit Odoo v2) | critical / warning / critical / warning / warning / warning | [audit-sink-alerts.md](audit-sink-alerts.md) |
| EfnKdsTicketFailed, EfnKdsTicketFailedSpike, EfnKdsAlertFormatUnrecognized (tiket Kitchen Display gagal, G15b SEC-76) | warning / critical / warning | [kds-ticket-failed.md](kds-ticket-failed.md) |
| EfnAuditReconAnomaly, EfnAuditCopyFailing (salinan audit tamper-evident ETL) | critical / warning | [etl-audit-copy.md](etl-audit-copy.md) |
| EfnEtlCycleMissing, EfnEtlSalesFactStale, EfnEtlStockSnapshotStale, EfnAuditCopyMissing, EfnAuditReconMissing (dead-man heartbeat ETL, G13) | warning / warning / warning / warning / critical | [etl-audit-copy.md](etl-audit-copy.md#dead-man-etl-gelombang-13-sec-52-butir-1) |
| EfnS3GatewayDeniedBurst (burst 403 filter object storage) | warning | [s3-gateway-denied.md](s3-gateway-denied.md) |
| EfnOnlineReconDeadman, EfnOnlineAnomalyCritical, EfnOnlineAnomalyWarning (penjualan online, G17) | warning / critical / warning | [online-orders.md](online-orders.md) |
| EfnOdooRpcGatewayDenied (filter BFF → Odoo `/json/2` menolak request, G17) | warning | [online-orders.md](online-orders.md#efnodoorpcgatewaydenied) |
| EfnOnlineCsDeniedRepeated, EfnOnlineCsDualRole, EfnOnlineCsRefundVolumeHigh (wrapper CLI CS `efn-online-cs`, journald host, SEC-107/108) | warning / warning / warning | [online-orders.md](online-orders.md#cli-cs) |
| EfnOnlineApiKeyRollbackFailed, EfnOnlineApiKeyOldKeyStillAccepted (timer `efn-rotate-online-key`, journald host, W-11) | critical / warning | [odoo-online-api-key.md](odoo-online-api-key.md#rollback-gagal) |
| EfnLoyaltyAnomalyCritical, EfnLoyaltyAnomalyWarning, EfnLoyaltyReconMissing, EfnLoyaltyReconFailing (Poin EFN, G13c SEC-61); EfnLoyaltyReconStale (Prometheus) | critical / warning / warning / warning / warning | [loyalty-anomaly.md](loyalty-anomaly.md) |

Alert keamanan yang tidak terjelaskan → pintu masuk insiden: [security-incident.md](security-incident.md).

## Prosedur

| Prosedur | Runbook |
|---|---|
| Backup, restore test, pemulihan bencana (DR), migrasi volume PostgreSQL | [backup-restore.md](backup-restore.md) |
| Rotasi secret (semua secret di `scripts/secrets.list`) | [secret-rotation.md](secret-rotation.md) |
| PKI internal 3 tingkat & mTLS: rotasi leaf (otomatis)/intermediate/root, migrasi | [internal-pki.md](internal-pki.md) |
| Kustodi kunci Superadmin, root CA offline, break-glass, audit akses secret; paket segel brankas Direksi, log brankas, uji pemulihan disaksikan, Superadmin tidak tersedia (W-28, R-B11) | [key-custody.md](key-custody.md#brankas-direksi) |
| Rotasi kunci enkripsi PII (key ring, re-encrypt) | [pii-key-rotation.md](pii-key-rotation.md) |
| Hub WireGuard + firewall nftables, onboarding outlet (OpenWrt / klien POS) & staf, VLAN Wi-Fi tamu | [wireguard-outlet.md](wireguard-outlet.md) |
| Firewall origin (443 hanya Cloudflare + WireGuard) | [origin-firewall.md](origin-firewall.md) |
| Enkripsi disk host (LUKS2) | [luks-disk.md](luks-disk.md) |
| Kegagalan pelindungan data pribadi 3×24 jam (UU PDP) | [breach-response-72h.md](breach-response-72h.md) |
| Redaksi PII/rahasia di log (Promtail), uji regresi, deteksi & hapus baris bocor di Loki | [log-redaction.md](log-redaction.md) |
| Insiden keamanan (deteksi, severity, containment, forensik) — pintu masuk | [security-incident.md](security-incident.md) |
| Token DNS ACME, service `acme-dns` (sertifikat wg-https), uji staging LE | [acme-dns-token.md](acme-dns-token.md) |
| Ubah grup/hak user Odoo (UI/RPC, bukan `odoo shell`); traceback JSON-RPC (sisa SEC-22) | [odoo-access-changes.md](odoo-access-changes.md) |
| journald host (efn-deploy, backup) → Loki | [host-journal-loki.md](host-journal-loki.md) |
| Operasi pesanan online (kill switch `ONLINE_ENABLED`, order macet, DLQ saga, refund CLI L-3, rotasi HMAC event) | [online-orders.md](online-orders.md) |
| CLI CS online lewat wrapper `efn-online-cs`: pasang sudoers, grup `efn-cs` / `efn-cs-approver`, alasan via stdin | [online-orders.md](online-orders.md#cli-cs) |
| API key Odoo kanal online: pemasangan timer rotasi otomatis 30 hari `efn-rotate-online-key`, rotasi darurat, metrik (R-T15, W-11) | [odoo-online-api-key.md](odoo-online-api-key.md) |
| Retensi data W-19 (env default, deploy pertama G18 / stop ETL, timer housekeeping Metabase) | [retention.md](retention.md) |
| Deploy gagal: modul Odoo wajib (`efn_pos_refund_origin`) belum `installed` (SEC-129) | [deploy-odoo-modules.md](deploy-odoo-modules.md) |
| Boot stack dev di laptop (Docker Desktop/WSL2), upgrade modul Odoo dev | [dev-stack-boot.md](dev-stack-boot.md) |
| CI GitHub di repo privat paket gratis: kuota artifact/menit, code scanning, suppression scanner, Dependabot | [ci-github-free-plan.md](ci-github-free-plan.md) |

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
| odoo-rpc-gateway (G17) | tanpa pengecualian (non-root, `read_only`, `cap_drop: [ALL]`, 64M, hanya network `internal`) | filter L7 BFF → Odoo `/json/2`, `infra/odoo-rpc-gateway/README.md` |
| sidecar `*-tls` (ghostunnel) | `network_mode: service:<app>` | TLS untuk app tanpa TLS native; app listen 127.0.0.1 |
| `ocr-worker-metrics-tls` + `ocr-worker-metrics-bridge` | dua ghostunnel (server mTLS `--allow-cn=prometheus` → loopback → client TLS) | worker HTTPS hanya di 127.0.0.1:9101; prometheus-client tidak bisa membatasi CN (E12 ditutup, gel. 12) |
| `acme-dns-init` | root dengan CAP_CHOWN+FOWNER, `network_mode: none`, one-shot | chown volume `acme-dns-*` ke 65532:0 + titik mount `base/`, `dev/` (SEC-37) |
| `acme-dns` | satu-satunya pemegang token DNS; tanpa port, hanya network `egress`, non-root 65532 | ACME DNS-01 terpisah dari Traefik (SEC-37) |
| `promtail-journal` (prod) | mount `/var/log/journal` + `/etc/machine-id` read-only, root tanpa capability | journald host (efn-deploy, backup) → Loki |

Di prod hanya Traefik yang mem-publish port (80/443); dicek otomatis di CI job `infra`.
