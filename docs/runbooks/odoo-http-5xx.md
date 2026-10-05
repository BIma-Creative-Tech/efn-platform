# Runbook: Odoo HTTP 5xx tinggi

Alert: `EfnOdooHttp5xxHigh` — rasio 5xx router `odoo@docker`/`odoo-ws@docker` di Traefik > 5% selama 5 menit (min. 0.2 req/s).

## Dampak
Back-office dan sinkronisasi POS online gagal sebagian. POS tetap bisa jual offline; order tersinkron setelah pulih.

## Diagnosis
1. Bedakan **502/504** (Odoo tidak menjawab / timeout) vs **500** (error aplikasi):
   Grafana Explore: `sum by (code) (rate(traefik_service_requests_total{service=~"odoo.*"}[5m]))`.
2. Log Odoo (Loki): `{service="odoo"} |= "ERROR"` atau di host `$DC logs --tail=300 odoo | grep -E 'ERROR|CRITICAL|Traceback'`.
3. Pola umum:
   | Gejala | Penyebab | Tindakan |
   |---|---|---|
   | 502 + container restart | OOM / `limit_memory_hard` worker | cek `docker stats`, naikkan limit memori atau kurangi `ODOO_WORKERS` |
   | 504 | `limit_time_real` tercapai, query lambat, lock | `SELECT pid, now()-query_start, state, query FROM pg_stat_activity WHERE datname='efn_odoo' ORDER BY 2 DESC;` |
   | 500 setelah deploy | bug addon / migrasi modul belum dijalankan | rollback image tag atau jalankan upgrade modul (`odoo -u <modul> --stop-after-init`) di jendela maintenance |
   | 500 di `/v1/pos/*` callout | BFF tidak bisa dijangkau / API key salah | cek `EFN_BFF_API_KEY_FILE` dan [secret-rotation.md](secret-rotation.md) |
   | `too many connections` | koneksi PG habis | `SHOW max_connections;` vs worker × db_maxconn; pertimbangkan PgBouncer (R-T11) |

## Pemulihan
- Restart Odoo: `$DC restart odoo` (sesi POS online tersambung ulang otomatis).
- Rollback: workflow *Deploy* `workflow_dispatch` ke tag sebelumnya (migrasi DB harus backward-compatible satu versi).
- Kunci DB berkepanjangan: `SELECT pg_terminate_backend(<pid>);` hanya untuk query yang jelas macet dan setelah koordinasi.

## Pencegahan
Test addon di CI (`odoo-test`), monitor latensi p95 di dashboard, review query lambat (`log_min_duration_statement=500`).
