#!/bin/sh
# One-shot sebelum acme-dns (SEC-37): volume bernama dibuat Docker milik root:root 0755, sedangkan acme-dns berjalan
# sebagai 65532:0. Root di sini hanya punya CAP_CHOWN + CAP_FOWNER (tanpa DAC_OVERRIDE). Idempoten (direktori puncak).
#   /data  (acme-dns-data)  : 0700 - kunci akun ACME + sertifikat asli lego, HANYA acme-dns
#   /certs (acme-dns-certs) : 0750 grup 0 - di-mount Traefik read-only SEBAGAI /etc/traefik/dynamic (direktori yang
#            di-watch provider file; fsnotify tidak rekursif, jadi acme-dns.yml harus di puncak). Subdirektori
#            base/ dan dev/ = titik mount bind konfigurasi statis (infra/traefik/dynamic, infra/traefik/dev); Docker
#            tidak bisa membuat titik mount di dalam volume read-only, jadi dibuat di sini.
set -eu
chown 0:0 /certs
mkdir -p /certs/base /certs/dev
chown 65532:0 /certs /data
chmod 0750 /certs
chmod 0700 /data
echo "acme-dns-init: /data $(stat -c '%u:%g %a' /data), /certs $(stat -c '%u:%g %a' /certs)"
