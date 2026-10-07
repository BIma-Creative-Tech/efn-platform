#!/bin/sh
# One-shot sebelum acme-dns / acme-publish / Traefik (SEC-37, SEC-53 gelombang 13): volume bernama dibuat Docker milik
# root:root 0755. Root di sini hanya punya CAP_CHOWN + CAP_FOWNER (tanpa DAC_OVERRIDE). Idempoten.
#   /data (acme-dns-data)    65532:0 0700 - kunci akun ACME + sertifikat asli lego, HANYA acme-dns
#   /out  (acme-dns-out)     65532:0 0750 - PEM crt/key hasil acme-dns (ditulis acme-dns; dibaca acme-publish via grup 0)
#   /dyn  (traefik-dynamic)  0:0 1770     - direktori provider file Traefik (/etc/traefik/dynamic, read-only di Traefik).
#            Hanya acme-publish (65533:0, network none) yang me-mount-nya rw: menulis acme-dns.yml dari TEMPLATE TETAP.
#            Sticky bit + base/ dev/ milik root: acme-publish tidak bisa mengganti nama/menghapus titik mount
#            konfigurasi statis (infra/traefik/dynamic, infra/traefik/dev). acme-dns (ber-network) TIDAK me-mount /dyn
#            -> tidak bisa menulis router/middleware Traefik (SEC-53).
set -eu
chown 0:0 /dyn
mkdir -p /dyn/base /dyn/dev
chown 0:0 /dyn/base /dyn/dev
chmod 0755 /dyn/base /dyn/dev
chmod 1770 /dyn
chown 65532:0 /out /data
chmod 0750 /out
chmod 0700 /data
echo "acme-dns-init: /data $(stat -c '%u:%g %a' /data), /out $(stat -c '%u:%g %a' /out), /dyn $(stat -c '%u:%g %a' /dyn)"
