# s3-gateway — filter L7 di depan object storage

- **Tujuan:** satu-satunya jalur klien aplikasi (BFF, ocr-worker, WAL-G Postgres, Prometheus) ke object storage
  (SILO, service `minio`). Menolak operasi berbahaya yang tidak pernah dibutuhkan aplikasi, terlepas dari vendor
  storage: bypass/ubah retensi & object lock, header replikasi internal, Snowball, S3 Select, payload
  `STREAMING-UNSIGNED-PAYLOAD-TRAILER`, kredensial query-string untuk tulis, admin/KMS/peer API, STS.
  Daftar lengkap + alasan: komentar di `haproxy.cfg`.
- **ADR:** ADR-030 Keputusan 2 (status **Proposed**; diterapkan lead gelombang 9 karena CVE High aktif). ADR-022
  (enkripsi: TLS masuk sertifikat `s3-gateway`, TLS keluar ke `minio:9000` dengan `verify required` + CA internal).
- **Risiko dimitigasi:** R-S02 (CVE-2026-41145/40344/34204/39414, SN-2026-015), R-T08 (backup `pg-backup` tidak bisa
  dibuka kuncinya lewat jalur aplikasi), R-S06.
- **Komponen:** HAProxy LTS `3.2.25-alpine` (dipin digest), non-root, read-only, tanpa capability; konfigurasi
  `haproxy.cfg`, `entrypoint.sh` (gabung crt+key ke tmpfs). Storage hanya di network `objstore`.
- **Verifikasi:** `scripts/smoke/run-e2e-smoke.sh g` (cek `g.s3gw_*`), CI `haproxy -c` + `check-encryption.sh`.
- **Belum / batas:**
  - Filter tidak bisa membedakan principal (semua klien sama); admin hanya dari `minio-init` (network `objstore`).
  - SN-2026-015 juga bisa dieksploitasi oleh kredensial yang langsung ke storage (hanya `minio-init`/root) — itu
    di luar jangkauan filter; mode COMPLIANCE menunggu Q-OS3.
  - Tidak ada metrik HAProxy ke Prometheus (log akses ke Loki via Promtail sudah ada). Kandidat: exporter
    bawaan HAProxy di loopback + sidecar mTLS.
  - Belum diuji dengan beban (base backup besar); timeout klien/server 300 s.
