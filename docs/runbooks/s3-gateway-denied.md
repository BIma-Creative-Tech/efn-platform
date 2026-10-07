# Runbook: EfnS3GatewayDeniedBurst — burst 403 di filter object storage

Alert ruler Loki (`infra/observability/loki-rules/efn-ops-logs.yml`, gelombang 12): > 10 respons 403 dari satu IP
klien dalam 5 menit di `s3-gateway` (filter L7 HAProxy, ADR-030, SEC-41). Klien sah (BFF, ocr-worker, WAL-G,
Prometheus) hampir tidak pernah ditolak. Percobaan `?retention`, `?legal-hold`, `?policy`, presigned URL, varian
percent-encoded, atau `/minio/admin` dari dalam jaringan = indikasi container terkompromi.

1. Lihat permintaan. Format log: `<ip> <method> <path> <status> ...`; query string tidak di-log (`%HPO`).
   Loki: `{service="s3-gateway"} |~ "^\\S+ \\S+ \\S+ 403 "`.
2. Identifikasi container dari IP (network `data` 172.30.2.0/24):
   `docker network inspect efn_data --format '{{range .Containers}}{{.Name}} {{.IPv4Address}}{{"\n"}}{{end}}'`.
3. Penyebab sah yang diketahui: `mc` dengan user aplikasi (stat/HEAD dulu), alat admin dijalankan di network `data`,
   upgrade SDK yang mengubah bentuk request. Konfirmasi dengan pemilik service.
4. Tidak sah → [security-incident.md](security-incident.md) baris "s3-gateway": isolasi container
   (`docker network disconnect efn_data <container>`), rotasi access key service itu
   ([secret-rotation.md](secret-rotation.md)), periksa object lock bucket `pg-backup` masih aktif.

Catatan: 403 dari SILO sendiri (AccessDenied kebijakan IAM) juga ikut terhitung; ambang 10/5 menit + `for: 2m`
mencegah alert dari kesalahan tunggal.
