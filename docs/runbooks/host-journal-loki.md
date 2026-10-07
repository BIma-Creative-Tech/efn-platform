# Runbook: journald host → Loki (efn-deploy, backup)

Gelombang 12 (sisa G11.8). Service `promtail-journal` (hanya `docker-compose.prod.yml`, profil `observability`,
konfigurasi `infra/observability/promtail-journal.yml`) membaca `/var/log/journal` host **read-only** dan mengirim ke
Loki (mTLS, sertifikat promtail) hanya:
- `SYSLOG_IDENTIFIER=efn-deploy` (wrapper deploy root, `logger -t efn-deploy`) → `service="host-efn-deploy"`;
- unit `efn-backup.service` / `efn-restore-test.service` → `service="host-efn-backup"` / `host-efn-restore-test`.

Label `job="host-journal"`, retensi 400 hari (`loki.yml` `retention_stream`). Log host lain (sshd, kernel) tidak
dikirim (minimisasi data). Nomor HP dan token Bearer diredaksi seperti promtail container.

## Prasyarat host
- journald persisten: `mkdir -p /var/log/journal && systemd-tmpfiles --create --prefix /var/log/journal &&
  systemctl restart systemd-journald` (atau `Storage=persistent` di `/etc/systemd/journald.conf`).
- `/etc/machine-id` ada.

## Verifikasi setelah deploy
```bash
logger -t efn-deploy "uji promtail-journal $(date -u +%FT%TZ)"
# Grafana Explore (Loki): {job="host-journal", service="host-efn-deploy"} |= "uji promtail-journal"
```
Setelah restart container, promtail membaca ulang maksimum 12 jam (`max_age`; posisi di tmpfs).

## Kueri berguna
- Deploy ditolak: `{service="host-efn-deploy"} |~ "ditolak|GAGAL|downgrade|signature"`.
- Backup gagal sebelum metrik ditulis: `{service="host-efn-backup", level=~"err|crit"}`.

Status: belum diuji di host nyata (dev Docker Desktop tanpa journald); konfigurasi lolos `promtail -check-syntax`.
