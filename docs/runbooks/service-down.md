# Runbook: Service down

Alert: `EfnServiceDown`, `EfnHealthProbeFailed`, `EfnPublicEndpointDown`, `EfnPostgresDown`, `EfnRedisDown`.

## Dampak
| Komponen | Dampak bisnis |
|---|---|
| postgres | **Semua** PT berhenti (Odoo satu DB multi-company, BFF). Prioritas tertinggi. |
| odoo | POS online tidak bisa sinkron order; back-office berhenti. POS offline tetap jual (fitur OTP/poin/MGM nonaktif). |
| api-gateway | Super-App, login OTP, Price Hunter, OTP gate POS (promo ber-OTP tidak bisa dipakai). |
| redis | OTP & discount token tidak bisa diverifikasi; rate limit BFF tidak berfungsi. |
| superapp-web | Super-App web tidak bisa diakses. |
| ocr-worker, ollama, etl, clickhouse, metabase | Tidak ada dampak transaksi (AI/analitik di luar jalur transaksi). |
| exporter (node, cadvisor, postgres/redis-exporter, blackbox) | Hanya visibilitas monitoring berkurang. |

## Diagnosis
1. Grafana → **EFN - Platform Overview**: panel *Target scrape down* / *Health probe gagal*.
2. Di host:
   ```bash
   $DC ps                                  # cari Exit / unhealthy / restarting
   $DC logs --tail=200 <service>
   docker inspect --format '{{json .State.Health}}' efn-<service>-1 | jq
   docker stats --no-stream                # OOM? mendekati limit memori
   dmesg -T | grep -i -E 'oom|killed'      # OOM killer
   ```
3. Penyebab umum:
   - **OOM** (limit di `deploy.resources.limits`): naikkan limit di `docker-compose.prod.yml` lewat PR, sementara restart.
   - **Secret tidak terbaca** (log "Permission denied /run/secrets/..."): file di `/opt/efn/secrets` harus mode 0644, direktori 0700. Jalankan ulang deploy.
   - **Dependensi**: api-gateway menunggu postgres/redis/rabbitmq sehat dan `minio-init` selesai sukses (`$DC logs minio-init`).
   - **Disk penuh**: lihat [disk-usage.md](disk-usage.md).
   - **Exporter belum dijalankan**: profile `ops` tidak aktif (`COMPOSE_PROFILES` harus memuat `ops`).
   - `EfnPublicEndpointDown` saja (probe internal hijau): masalah DNS/Cloudflare/Traefik/sertifikat → `$DC logs traefik`, [tls-cert-expiry.md](tls-cert-expiry.md).

## Pemulihan
```bash
$DC up -d <service>                 # recreate dengan konfigurasi terkini
$DC restart <service>               # bila hanya hang
```
- PostgreSQL tidak bisa start karena korupsi/volume rusak → **jangan** hapus volume; eskalasi dan ikuti [backup-restore.md](backup-restore.md) §4 (DR).
- Image rusak setelah deploy → rollback: GitHub Actions *Deploy* `workflow_dispatch` dengan `image_tag` sebelumnya.

## Setelah insiden
Catat timeline, akar masalah, dan tindakan pencegahan di issue `incident`. Bila alert berisik (flapping) usulkan perubahan `for:` lewat PR, bukan silence permanen.
