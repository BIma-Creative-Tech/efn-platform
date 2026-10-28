# Metabase OSS (BI internal, ADR-003) — G13 efn-data-ai-engineer; G16 (9 Okt 2026) efn-devsecops-engineer.
# G16: v0.52.4 (Jan 2025) gagal gate Trivy image (93 CVE HIGH/CRITICAL: Alpine + jar). Dinaikkan ke jalur LTS
# v0.58.35 (Alpine 3.24, JRE 21). Sejak v0.54 driver ClickHouse sudah BAWAAN Metabase (namespace metabase/clickhouse
# di metabase.jar, diperiksa 9 Okt 2026) -> jar driver eksternal 1.52.0 dihapus (driver ganda berbenturan).
# PERHATIAN: migrasi DB aplikasi Metabase 0.52 -> 0.58 berjalan otomatis saat start dan TIDAK bisa dibalik;
# backup DB `metabase` sebelum menjalankan image ini di lingkungan yang datanya perlu dipertahankan.
# Sisa 6 CVE HIGH di metabase.jar (dependency upstream, belum ada rilis perbaikan) -> .trivyignore.yaml ber-tanggal.
# Pengecualian checkov terdokumentasi (tinjau ulang saat bump Metabase):
# checkov:skip=CKV_DOCKER_3: run_metabase.sh image resmi mulai sebagai root lalu `su metabase` (MUID/MGID, uid 2000) setelah chown data; USER di sini mematahkan langkah itu.
FROM metabase/metabase:v0.58.35@sha256:a84b5dd5db15edac289af7204ff897e8b2dd441f579db445e0c7579402a5d77d

# G18 (PR #60, Trivy): perbaikan keamanan Alpine yang terbit setelah digest upstream dipin - libexpat 2.9.0-r0
# (CVE-2026-77214); G24 (PR #68, Trivy): brotli-libs 1.2.1-r0 (CVE-2026-111497); G26 (PR #71, Trivy): curl/libcurl
# 8.23.0-r0 (CVE-2026-92392). Hanya paket ber-CVE yang dinaikkan
# (tidak `apk upgrade` penuh: JRE/libc tetap dari upstream yang diuji Metabase). Bump tag Metabase berikutnya: hapus
# paket dari baris ini bila base sudah memuat versi perbaikan (v0.58.38 sudah memuat curl 8.23.0).
RUN apk add --no-cache --upgrade libexpat brotli-libs curl libcurl

# G16b (efn-devsecops-engineer): enkripsi at rest detail koneksi di DB aplikasi `metabase`. Wrapper membaca Docker
# secret metabase_encryption_key -> MB_ENCRYPTION_SECRET_KEY (fail-closed) lalu exec /app/run_metabase.sh resmi
# (perilaku root -> `su metabase` dipertahankan). Lihat efn-entrypoint.sh dan README "Enkripsi at rest".
COPY --chmod=0755 efn-entrypoint.sh /usr/local/bin/efn-metabase-entrypoint
# Sengaja tanpa USER: run_metabase.sh resmi start sebagai root lalu `su metabase` (uid 2000); compose membatasi
# cap_drop ALL + CHOWN/SETUID/SETGID + no-new-privileges (SEC-85, .trivyignore.yaml DS-0002).
# nosemgrep: dockerfile.security.missing-user-entrypoint.missing-user-entrypoint
ENTRYPOINT ["/usr/local/bin/efn-metabase-entrypoint"]
