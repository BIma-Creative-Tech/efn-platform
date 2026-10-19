#!/usr/bin/env bash
# Uji beban kecil batas koneksi odoo-rpc-gateway -> Odoo (SEC-96 / L-16b, docs/04 §10.17). Stack dev harus berjalan.
#
#   bash infra/odoo-rpc-gateway/test-gateway-load.sh [jumlah_request] [paralel]     (default 200 / 150)
#
# Satu container curl (network efn_internal, sertifikat klien api-gateway) mengirim N POST paralel ke path sah.
# Harapan:
#   - sebagian request DILAYANI Odoo (401 karena token uji bukan API key = sampai ke Odoo),
#   - `--http1.1` + `Connection: close` wajib: Odoo (werkzeug) menjawab `HTTP/1.0`, lalu curl (mode paralel) menurunkan request berikutnya ke
#     HTTP/1.0 yang ditolak gateway (403 target). BFF (undici) selalu HTTP/1.1.
#   - kelebihan di atas ODOO_RPC_MAXCONN + antrean 16 / timeout queue 2 s dijawab gateway 503 dengan header
#     X-EFN-Gateway-Error: unavailable (bukan diteruskan ke worker Odoo),
#   - log gateway memperlihatkan waktu antre (%Tw) > 0 pada sebagian request dilayani,
#   - tidak ada status lain (000 = koneksi gagal) dan gateway tetap healthy setelahnya.
# Tidak ada secret yang dicetak; token uji string tetap. Exit 1 bila harapan tidak terpenuhi.
set -euo pipefail

CURL_IMAGE="curlimages/curl:8.11.1@sha256:c1fe1679c34d9784c1b0d1e5f62ac0a79fca01fb6377cdd33e90473c6f9f9a69"
N="${1:-200}"
PAR="${2:-150}"
[[ "$N" =~ ^[1-9][0-9]{0,3}$ && "$PAR" =~ ^[1-9][0-9]{0,2}$ ]] || { echo "pemakaian: $0 [N<=9999] [paralel<=999]" >&2; exit 2; }

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
hostpath() {
  if command -v cygpath > /dev/null 2>&1; then cygpath -m "$1"; else printf '%s\n' "$1"; fi
}
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
chmod 755 "$work"
for f in efn_ca_crt tls_api_gateway_crt tls_api_gateway_key; do
  cp "${root}/secrets/${f}.txt" "${work}/${f}"
done
chmod 644 "${work}"/*

# N transfer ke path yang sama lewat file config curl (url + output per transfer)
for _ in $(seq 1 "$N"); do
  printf 'url = "https://odoo-rpc-gateway:8443/json/2/efn.online.order/efn_online_status"\noutput = "/dev/null"\n'
done > "${work}/urls.cfg"
chmod 644 "${work}/urls.cfg"
since="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
sleep 1
MSYS_NO_PATHCONV=1 docker run --rm --network efn_internal -v "$(hostpath "$work"):/t:ro" "$CURL_IMAGE" \
  -sS --http1.1 -H 'Connection: close' --parallel --parallel-immediate --parallel-max "$PAR" -m 30 \
  --cacert /t/efn_ca_crt --cert /t/tls_api_gateway_crt --key /t/tls_api_gateway_key \
  -X POST -H 'Content-Type: application/json' -H 'Authorization: bearer g17-load-test-token-not-a-secret' \
  --data '{"channel_code":"GL","platform_order_refs":[]}' \
  -w '%{http_code} %header{x-efn-gateway-error}\n' -K /t/urls.cfg \
  > "${work}/codes.txt" 2> "${work}/err.txt" || true

echo "Distribusi status (kode header-gateway):"
sort "${work}/codes.txt" | uniq -c | sed 's/^/  /'
served=$(grep -c '^401 $' "${work}/codes.txt" || true)
busy=$(grep -c '^503 unavailable$' "${work}/codes.txt" || true)
other=$(( $(wc -l < "${work}/codes.txt") - served - busy ))
lines=$(docker logs --since "$since" efn-odoo-rpc-gateway-1 2>&1 | grep -E '/efn_online_status ' || true)
queued=$(awk 'NF > 2 && $(NF-1) ~ /^[0-9]+$/ && $(NF-1) > 0' <<< "$lines" | grep -c . || true)
maxq=$(awk 'NF > 2 && $(NF-1) ~ /^[0-9]+$/ {if ($(NF-1) > m) m = $(NF-1)} END {print m + 0}' <<< "$lines")
echo "dilayani Odoo=${served} ditolak-antrean(503 gateway)=${busy} lain=${other} | log: antre>0 ms=${queued}, antre maks=${maxq} ms"
health="$(docker inspect -f '{{.State.Health.Status}}' efn-odoo-rpc-gateway-1)"
echo "health gateway setelah uji: ${health}"
if (( served > 0 && busy > 0 && other == 0 )) && [[ "$health" == healthy ]]; then
  echo "OK: batas koneksi + antrean + 503 bekerja"
else
  echo "GAGAL: harapan tidak terpenuhi (lihat distribusi di atas)" >&2
  head -5 "${work}/err.txt" >&2
  exit 1
fi
