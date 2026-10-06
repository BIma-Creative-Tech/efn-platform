# Runbook: alert sink audit Odoo (`EfnAudit*`)

Gelombang 11, SEC-43 (review keamanan r4), risiko R-S10/R-B06. Sumber: Odoo `efn_audit` menulis baris
`EFN_AUDIT_SINK {json}` (logger `odoo.addons.efn_audit.sink`, **setelah commit**, tanpa nilai lama/baru dan tanpa
PII) untuk perubahan model kontrol audit (aturan audit, `ir.config_parameter` `efn.audit.*`, server action, cron,
modul, otomasi) dan setiap operasi purge log audit.

Jalur: stdout Odoo → Promtail (`infra/observability/promtail.yml`, hanya container `service=odoo`, label
`stream="efn_audit_sink"`, `audit_op`) → Loki (mTLS) → ruler Loki (`infra/observability/loki-rules/efn-audit-sink.yml`)
→ Alertmanager (mTLS, SAN `loki` di `web-config-alertmanager.yml`). Retensi stream ini **400 hari**
(`retention_stream` di `loki.yml`); stream lain 30 hari. Salinan ini berada di luar kendali admin Odoo: menghapus
log di Odoo tidak menghapus jejaknya di Loki.

Query dasar (Grafana → Explore → Loki):

```logql
{stream="efn_audit_sink"} | regexp `EFN_AUDIT_SINK (?P<payload>[{].*[}])` | line_format "{{.payload}}" | json
```

Field: `log_id`, `operation` (create/write/unlink/purge), `model`, `res_id`, `uid`, `sudo`, `fields` (nama field
yang berubah), `note` (purge: `retensi: N log` atau `erasure: ...`).

## EfnAuditControlChange
Severity **warning**. Model kontrol audit berubah dalam 5 menit terakhir.
1. Lihat `model`, `fields`, `uid`, `sudo` di query dasar. Buka log lengkap di Odoo: Pengaturan → Audit → Log
   (`log_id`).
2. Cocokkan dengan change request / jadwal deploy (upgrade modul lewat pipeline juga memicu `ir.module.module`).
3. Tanpa CR, atau `efn.audit.retention_days` / `retention_days` aturan diturunkan, atau server action berisi kode
   baru: perlakukan sebagai insiden ([breach-response-72h.md](breach-response-72h.md)), kunci akun `uid`, simpan
   bukti dari Loki (ekspor query), kembalikan konfigurasi.

## EfnAuditUnexpectedPurge
Severity **critical**. Ada log `purge` dengan `note` yang bukan `retensi:`/`erasure:`. Jalur sah hanya GC retensi
terjadwal dan erasure UU PDP; purge lain berarti kode kustom/server action memanggil method purge.
1. Ambil `uid`, `model`, `note`, waktu dari Loki. Cari server action / cron yang berubah di jendela yang sama
   (alert `EfnAuditControlChange`).
2. Insiden keamanan: nonaktifkan server action/cron terkait, cabut hak admin `uid`, pulihkan log dari backup
   PostgreSQL (WAL-G PITR ke container sementara, `scripts/restore-test.sh`) bila perlu sebagai bukti.

## EfnAuditRetentionPurgeBurst
Severity **warning**. > 5 purge retensi dalam 1 jam (normal: sekali per jalan autovacuum harian).
Periksa perubahan `efn.audit.retention_days` / aturan audit (alert `EfnAuditControlChange` sebelumnya) dan jumlah
log yang dihapus (`note`).

## Diagnosis jalur
- Tidak ada data di `{stream="efn_audit_sink"}`: cek `docker logs efn-odoo-1 | grep EFN_AUDIT_SINK` (modul
  `efn_audit` versi gelombang 11 terpasang?), lalu `docker logs efn-promtail-1`.
- Ruler tidak mengirim: `docker logs efn-loki-1 | grep -i ruler` (error TLS = sertifikat loki belum ber-EKU
  clientAuth → `bash scripts/gen-secrets.sh` di dev / deploy di staging-prod, lalu recreate `loki`).

## Batas (jujur)
- Admin sistem Odoo yang juga punya akses host/Docker dapat mematikan Promtail; itu terlihat sebagai target
  hilang di Prometheus/Uptime Kuma, bukan dicegah.
- Baris sink dapat dipalsukan oleh kode yang berjalan di proses Odoo (menambah alert palsu), tetapi tidak dapat
  menghapus baris yang sudah terkirim ke Loki.
- Loki bukan penyimpanan WORM; salinan append-only jangka panjang (ClickHouse tanpa DELETE / object lock) masih
  backlog SEC-43 butir 4.
