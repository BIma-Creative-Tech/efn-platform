# Runbook: salinan audit tamper-evident (ETL) — EfnAuditReconAnomaly, EfnAuditCopyFailing

Sumber alert: ruler Loki `infra/observability/loki-rules/efn-ops-logs.yml` (gelombang 12). Komponen:
`services/etl/efn_etl/audit_job.py` menyalin `efn_audit_log`/`efn_audit_log_line` Odoo ke ClickHouse (append-only,
rantai hash per company) dan merekonsiliasi harian (`EFN_AUDIT_RECON {json}`; level WARNING hanya bila anomali).
Detail desain: `services/etl/README.md`. Insiden: [security-incident.md](security-incident.md).

Urutan deploy wajib: upgrade `efn_audit` (>= 19.0.1.2.0, kolom `origin_user_id`) → deploy (grant
`services/etl/sql/odoo-readonly-grants.sql`; `deploy.sh` berhenti dengan pesan jelas bila kolom belum ada) → job
`ETL_ENABLE_GL` / `ETL_AUDIT_COPY_ENABLED` aktif otomatis (kecuali di-set `false` di GitHub Environment vars).

## EfnAuditReconAnomaly (critical)
Log audit Odoo dan salinannya berbeda: log hilang/berubah di Odoo, celah `chain_seq`, atau rantai hash putus.
1. Ambil payload: Loki `{service="etl"} |= "EFN_AUDIT_RECON" |= " WARNING "` (JSON tanpa nilai/PII).
2. Bandingkan dengan sink audit (`{stream="efn_audit_sink"}`, [audit-sink-alerts.md](audit-sink-alerts.md)): purge
   `retensi:`/`erasure:` yang sah menjelaskan log hilang **hanya** bila id-nya cocok.
3. `seq_gap` tanpa purge: cek apakah ada dua instance ETL (`$DC ps etl`) — dua instance membuat rantai bercabang.
4. Tidak terjelaskan → insiden menurut [security-incident.md](security-incident.md): bekukan akses admin Odoo, simpan
   salinan ClickHouse (`audit_log_copy`) sebagai bukti. Jangan mengubah tabel salinan (user ETL memang tanpa
   ALTER/DELETE).

## EfnAuditCopyFailing (warning)
`job=audit_copy gagal` ≥ 2 kali/jam. Log Odoo tetap aman, tetapi jejak eksternal tertunda.
1. `$DC logs --tail=200 etl | grep -A20 'job=audit_copy gagal'`.
2. `permission denied for table efn_audit_log` / kolom tidak ada → modul belum di-upgrade atau grant belum
   diterapkan: upgrade `efn_audit`, ulangi deploy (grant idempoten).
3. ClickHouse menolak (TLS, kuota, disk) → [service-down.md](service-down.md), [disk-usage.md](disk-usage.md).
4. Setelah pulih job melanjutkan dari id terakhir (keyset); tidak perlu backfill manual.

## Dead-man ETL (gelombang 13, SEC-52 butir 1)
Sumber: heartbeat data-AI `EFN_ETL_HEARTBEAT job=<job> status=<ok|error|partial> ...` (logger `efn_etl.heartbeat`),
dicocokkan berjangkar di `loki-rules/efn-ops-logs.yml` grup `efn-etl-heartbeat` (`absent_over_time` status=ok).
Riwayat juga di ClickHouse `efn_dwh.etl_job_run` / view `v_etl_freshness` (kartu Metabase).

### EfnEtlCycleMissing
Tidak ada `job=cycle status=ok` 45 menit (juga `EfnEtlSalesFactStale` 45 menit, `EfnEtlStockSnapshotStale` 3 jam).
`docker compose ps etl`, `docker compose logs --tail=200 etl` (cari `status=error`, traceback, koneksi PG/CH/RabbitMQ).
Kredensial/sertifikat kedaluwarsa → [tls-cert-expiry.md](tls-cert-expiry.md). `sales_fact_pos` absen tetapi `cycle` ok
→ periksa `ETL_ENABLE_ODOO` dan grant `odoo-readonly-grants.sql` (deploy.sh).

### EfnAuditCopyMissing
`job=audit_copy status=ok` absen 1 jam: salinan tamper-evident berhenti. Production menolak
`ETL_AUDIT_COPY_ENABLED=false` kecuali `EFN_ALLOW_CONTROL_OFF` di `/etc/efn/deploy.conf` (SEC-50) — bila flag itu
ada, pastikan ada change request. Selain itu ikuti langkah `EfnAuditCopyFailing`.

### EfnAuditReconMissing
`job=audit_recon status=ok` absen 26 jam (critical): rekonsiliasi harian tidak berjalan → tamper tidak terdeteksi.
Jalankan ulang ETL; bila recon gagal terus, eskalasi ke data-AI dan perlakukan jendela tanpa recon sebagai celah
kontrol di register insiden.

## Jangkar eksternal 400 hari (SEC-52 butir 2)
Baris `EFN_AUDIT_CHAIN_HEAD` dan `EFN_AUDIT_RECON` dari logger `efn_etl.audit_job` diberi label promtail
`stream="efn_audit_anchor"` (`audit_event=chain_head|recon`) dan disimpan 400 hari (`loki.yml retention_stream`).
Kepala rantai TIDAK dikenai redaksi nomor HP (hash hex dapat memuat pola `08…`; isi dibatasi regex angka+hex).
Investigasi: bandingkan kepala rantai hari X di Loki (`{stream="efn_audit_anchor", audit_event="chain_head"}`) dengan
`row_hash` baris `chain_seq` yang sama di ClickHouse; berbeda = rantai ditulis ulang. Perbandingan otomatis = backlog
data-AI (recon membaca Loki lewat mTLS CN `etl`, disiapkan G13).
