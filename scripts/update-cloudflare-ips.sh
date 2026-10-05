#!/usr/bin/env bash
# Sinkronisasi rentang IP Cloudflare (SEC-03, docs/04 §7.5).
#   scripts/update-cloudflare-ips.sh           unduh daftar terbaru -> infra/traefik/cloudflare-ips.txt, lalu
#                                              bangkitkan ulang blok `# BEGIN/END cloudflare-ips` di traefik.yml
#   scripts/update-cloudflare-ips.sh --check   (CI) gagal bila blok traefik.yml != cloudflare-ips.txt (tanpa jaringan)
#   scripts/update-cloudflare-ips.sh --render  hanya bangkitkan ulang blok dari file txt (tanpa jaringan)
# Setelah update: commit kedua file, deploy (Traefik + TRUSTED_PROXIES BFF), perbarui firewall origin
# (docs/runbooks/origin-firewall.md).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TXT="${ROOT_DIR}/infra/traefik/cloudflare-ips.txt"
YML="${ROOT_DIR}/infra/traefik/traefik.yml"
MODE="${1:-update}"

cidrs() { grep -Ev '^[[:space:]]*(#|$)' "$TXT"; }

render_block() {
  local tmp
  tmp="$(mktemp)"
  awk -v list="$(cidrs | tr '\n' ' ')" '
    /# BEGIN cloudflare-ips/ { print; indent = substr($0, 1, index($0, "#") - 1);
                               n = split(list, a, " "); for (i = 1; i <= n; i++) print indent "- " a[i];
                               skip = 1; next }
    /# END cloudflare-ips/   { skip = 0 }
    !skip { print }
  ' "$YML" > "$tmp"
  printf '%s' "$tmp"
}

case "$MODE" in
  --check)
    tmp="$(render_block)"
    if ! diff -u "$YML" "$tmp"; then
      rm -f "$tmp"
      echo "::error::traefik.yml trustedIPs tidak sinkron dengan cloudflare-ips.txt (jalankan scripts/update-cloudflare-ips.sh --render)" >&2
      exit 1
    fi
    rm -f "$tmp"
    echo "cloudflare-ips: sinkron ($(cidrs | wc -l | tr -d ' ') rentang)"
    ;;
  --render)
    tmp="$(render_block)"; mv -f "$tmp" "$YML"; echo "traefik.yml diperbarui dari $TXT"
    ;;
  update)
    command -v curl > /dev/null || { echo "curl dibutuhkan" >&2; exit 1; }
    v4="$(curl -fsS --max-time 20 https://www.cloudflare.com/ips-v4)"
    v6="$(curl -fsS --max-time 20 https://www.cloudflare.com/ips-v6)"
    # Validasi kasar: hanya CIDR; daftar kosong = gagal (jangan pernah mengosongkan trustedIPs diam-diam)
    all="$(printf '%s\n%s\n' "$v4" "$v6" | grep -E '^[0-9a-fA-F:.]+/[0-9]{1,3}$' || true)"
    [[ $(wc -l <<< "$all") -ge 10 ]] || { echo "daftar Cloudflare tidak valid/terlalu pendek" >&2; exit 1; }
    {
      head -n 3 "$TXT" | grep '^#'
      echo "# Terakhir diperiksa: $(date +%Y-%m-%d)"
      echo "$all"
    } > "${TXT}.new"
    mv -f "${TXT}.new" "$TXT"
    tmp="$(render_block)"; mv -f "$tmp" "$YML"
    echo "cloudflare-ips.txt + traefik.yml diperbarui ($(cidrs | wc -l | tr -d ' ') rentang). Review diff lalu commit."
    ;;
  *) echo "pemakaian: $0 [--check|--render]" >&2; exit 2 ;;
esac
