# Runbook: Backup / WAL archiving gagal

Alert: `EfnBackupFailed`, `EfnBackupStale` (> 26 jam tanpa base backup sukses), `EfnBackupMetricMissing`, `EfnWalArchiveFailing`, `EfnWalArchiveStale`.

Arsitektur singkat (detail: [backup-restore.md](backup-restore.md)): PostgreSQL meng-archive WAL ke MinIO bucket `pg-backup` lewat `efn-walg wal-push` (RPO ≤ 15 menit), base backup harian `scripts/backup.sh` via systemd timer `efn-backup.timer`, metrik ditulis ke `/var/lib/efn/node-exporter/efn_backup.prom`.

## Dampak
- `EfnWalArchiveFailing`: **RPO terlanggar** sejak kegagalan pertama; WAL menumpuk di `pg_wal` → disk bisa penuh → PostgreSQL berhenti. Critical.
- `EfnBackupFailed/Stale`: pemulihan butuh replay WAL lebih panjang (RTO naik); bila berlanjut, rantai backup putus.

## Diagnosis
```bash
systemctl status efn-backup.service; journalctl -u efn-backup.service -n 200
cat /var/lib/efn/node-exporter/efn_backup.prom
$DC logs --tail=200 postgres | grep -i -E 'archive|wal-g|efn-walg'
docker exec efn-postgres-1 psql -U postgres -d postgres -c "SELECT * FROM pg_stat_archiver;"
docker exec efn-postgres-1 du -sh /var/lib/postgresql/data/pgdata/pg_wal
docker exec efn-postgres-1 efn-walg backup-list
$DC logs minio-init
```
| Gejala di log | Penyebab | Tindakan |
|---|---|---|
| `efn-walg: secret tidak terbaca` | file secret hilang / izin | `/opt/efn/secrets/minio_backup_password.txt`, `walg_libsodium_key.txt` mode 0644; deploy ulang |
| `AccessDenied` / `InvalidAccessKeyId` | user `pgbackup` belum dibuat / password beda | `$DC up minio-init` (upsert user dari secret) |
| `NoSuchBucket` | bucket `pg-backup` belum ada | `$DC up minio-init` |
| `connection refused minio:9000` | MinIO mati | [service-down.md](service-down.md) |
| `no space left` di MinIO | disk MinIO penuh | [disk-usage.md](disk-usage.md); cek retensi `RETAIN_FULL` |
| `EfnBackupMetricMissing` | timer belum dipasang / direktori textfile salah | pasang unit systemd ([backup-restore.md](backup-restore.md) §2) |
| `EfnWalArchiveStale` tanpa failing | DB idle, atau `archive_mode=off` | `SHOW archive_mode;` harus `on` |

## Pemulihan
1. Perbaiki penyebab; archive_command di-retry otomatis oleh PostgreSQL — `failed_count` berhenti naik, `last_archived_time` maju.
2. Jalankan base backup manual setelah pulih (rantai baru):
   ```bash
   sudo systemctl start efn-backup.service && journalctl -u efn-backup.service -f
   ```
3. Bila `pg_wal` hampir memenuhi disk dan archive tidak bisa dipulihkan cepat: **jangan hapus file di pg_wal**. Tambah disk/volume, atau (keputusan eskalasi) set `archive_command` sementara ke `/bin/true` dengan menerima RPO hilang, lalu base backup penuh segera setelah pulih.
