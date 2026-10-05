# Runbook: Disk hampir penuh

Alert: `EfnDiskUsageHigh` (> 85% selama 15 menit, warning), `EfnDiskUsageCritical` (> 95% selama 5 menit, critical).

## Dampak
PostgreSQL berhenti menerima tulis saat disk penuh (POS online, BFF, Odoo gagal). RabbitMQ memblokir publisher saat disk alarm. Prometheus/Loki berhenti ingest.

## Diagnosis
```bash
df -h
sudo du -xh --max-depth=1 /var/lib/docker | sort -h | tail
docker system df -v | head -50
docker exec efn-postgres-1 du -sh /var/lib/postgresql/data/pgdata/pg_wal   # WAL menumpuk = archive gagal
```
| Penyumbang | Tindakan |
|---|---|
| `pg_wal` besar | archive WAL gagal → [backup-failed.md](backup-failed.md). Jangan hapus file WAL manual. |
| Volume MinIO (`efn_minio-data`) | cek retensi WAL-G (`RETAIN_FULL`), dump logis lama, foto `market-scout` (lifecycle) |
| Image lama | `docker image prune -af --filter until=168h` |
| Log container | limit 5×20 MB per container sudah dipasang; cek container di luar compose |
| Prometheus/Loki | retensi 30 hari (prod Prometheus juga `retention.size=40GB`); turunkan bila perlu |
| ClickHouse | partisi lama / TTL tabel (data turunan, bisa dibangun ulang) |
| Ollama models | `docker exec efn-ollama-1 ollama rm <model-lama>` |

## Pemulihan
Bersihkan penyumbang yang aman (image, log, model), lalu rencanakan perluasan disk bila tren naik (Grafana panel *Disk terpakai*). Target: < 70% setelah tindakan.
