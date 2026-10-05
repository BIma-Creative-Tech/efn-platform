#!/usr/bin/env bash
# Mendaftarkan peer WireGuard outlet/staf ke hub (docs/runbooks/wireguard-outlet.md, ADR-023, SEC-01/21/28).
# Dijalankan ops (staf IT) DI HOST hub sebagai root (butuh wireguard-tools).
#
# Mode DISARANKAN (ADR-023 butir 5): kunci privat dibuat DI PERANGKAT (router OpenWrt/MikroTik, aplikasi WireGuard
# Windows/Android/Linux); hanya PUBLIC KEY yang dikirim ke ops:
#   gen-peer.sh --pubkey <PUBKEY> <outlet|staff> <nama-peer> <ip-peer/32> <endpoint-host:port>
#   contoh : gen-peer.sh --pubkey 'AbC...=' outlet FNB-JKT-001 10.88.1.1/32 vpn.efn.example:51820
# Mode darurat (perangkat tidak bisa membuat kunci): tanpa --pubkey -> kunci dibuat di host, file .conf berisi
# kunci privat di OUT_DIR (0700, BUKAN repo); kirim lewat kanal terenkripsi lalu `shred -u`.
#
# Alokasi (harus sama dengan EFN_WG_OUTLET_CIDR/EFN_WG_STAFF_CIDR):
#   outlet : 10.88.1.0 - 10.88.255.254 (10.88.0.0/24 = hub, tidak dialokasikan)
#   staff  : 10.89.0.0/16 (10.89.0.0/24 = staf IT, boleh SSH ke hub)
set -euo pipefail

PUBKEY=""
if [[ "${1:-}" == "--pubkey" ]]; then
  PUBKEY="${2:?--pubkey membutuhkan kunci publik}"
  shift 2
fi
ROLE="${1:?peran: outlet|staff}"
NAME="${2:?nama peer (mis. FNB-JKT-001)}"
PEER_IP="${3:?IP peer, mis. 10.88.1.1/32}"
ENDPOINT="${4:?endpoint hub host:port}"
WG_IF="${WG_IF:-wg0}"
HUB_IP="${HUB_IP:-10.88.0.1}"
SERVER_PUBKEY_FILE="${SERVER_PUBKEY_FILE:-/etc/wireguard/server.pub}"
OUT_DIR="${OUT_DIR:-/root/wg-peers}"
OCTET='(25[0-4]|2[0-4][0-9]|1[0-9]{2}|[1-9][0-9]|[1-9])'

[[ "$NAME" =~ ^[A-Za-z0-9_-]{3,32}$ ]] || { echo "nama peer tidak valid" >&2; exit 1; }
[[ "$ENDPOINT" =~ ^[A-Za-z0-9.-]+:[0-9]{2,5}$ ]] || { echo "endpoint harus host:port" >&2; exit 1; }
case "$ROLE" in
  outlet) [[ "$PEER_IP" =~ ^10\.88\.${OCTET}\.${OCTET}/32$ ]] \
            || { echo "IP outlet harus /32 di 10.88.1.0-10.88.255.254" >&2; exit 1; } ;;
  staff)  [[ "$PEER_IP" =~ ^10\.89\.([0-9]|${OCTET}|255)\.${OCTET}/32$ ]] \
            || { echo "IP staf harus /32 di 10.89.0.0/16" >&2; exit 1; } ;;
  *) echo "peran harus outlet|staff" >&2; exit 1 ;;
esac
if [[ -n "$PUBKEY" ]]; then
  [[ "$PUBKEY" =~ ^[A-Za-z0-9+/]{42}[AEIMQUYcgkosw048]=$ ]] || { echo "public key WireGuard tidak valid" >&2; exit 1; }
fi
command -v wg > /dev/null || { echo "wireguard-tools (wg) dibutuhkan" >&2; exit 1; }
[[ -r "$SERVER_PUBKEY_FILE" ]] || { echo "kunci publik hub tidak ada: $SERVER_PUBKEY_FILE" >&2; exit 1; }
if wg show "$WG_IF" allowed-ips 2> /dev/null | grep -qw "${PEER_IP%/32}/32"; then
  echo "IP ${PEER_IP} sudah dipakai peer lain di ${WG_IF}" >&2; exit 1
fi

umask 077
mkdir -p "$OUT_DIR"
psk="$(wg genpsk)"
server_pub="$(< "$SERVER_PUBKEY_FILE")"
if [[ -n "$PUBKEY" ]]; then
  pub="$PUBKEY"
  priv_line="PrivateKey = <DIBUAT_DI_PERANGKAT - jangan diisi ops>"
else
  priv="$(wg genkey)"
  pub="$(printf '%s' "$priv" | wg pubkey)"
  priv_line="PrivateKey = ${priv}"
  unset priv
fi

# Konfigurasi klien. Mode --pubkey: berisi PSK (rahasia bersama) tetapi TANPA kunci privat perangkat.
cat > "${OUT_DIR}/${NAME}.conf" <<EOF
# WireGuard peer ${NAME} (${ROLE}) - dibuat $(date -Is). RAHASIA: berisi PresharedKey.
[Interface]
${priv_line}
Address = ${PEER_IP}
DNS = ${HUB_IP}

[Peer]
PublicKey = ${server_pub}
PresharedKey = ${psk}
Endpoint = ${ENDPOINT}
AllowedIPs = ${HUB_IP}/32
PersistentKeepalive = 25
EOF
unset priv_line

# Blok untuk hub (tanpa kunci privat peer)
cat > "${OUT_DIR}/${NAME}.server-peer.conf" <<EOF
[Peer]
# ${NAME} (${ROLE}), dibuat $(date +%F), kunci $( [[ -n "$PUBKEY" ]] && echo "dibuat di perangkat" || echo "dibuat di hub (darurat)")
PublicKey = ${pub}
PresharedKey = ${psk}
AllowedIPs = ${PEER_IP}
EOF
unset psk

echo "Peer ${NAME} (${ROLE}) disiapkan di ${OUT_DIR}/ (mode 0600)."
echo "Pasang di hub tanpa restart:"
echo "  wg set ${WG_IF} peer ${pub} preshared-key <(sed -n 's/^PresharedKey = //p' ${OUT_DIR}/${NAME}.server-peer.conf) allowed-ips ${PEER_IP}"
echo "  cat ${OUT_DIR}/${NAME}.server-peer.conf >> /etc/wireguard/${WG_IF}.conf"
echo "Kirim ${NAME}.conf (berisi PSK) lewat kanal terenkripsi, lalu: shred -u ${OUT_DIR}/${NAME}.conf"
echo "Catat di register peer: ${NAME}, ${ROLE}, ${PEER_IP}, ${pub}, $(date +%F)"
