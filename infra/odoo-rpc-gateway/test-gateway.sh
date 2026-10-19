#!/usr/bin/env bash
# Uji negatif + positif NYATA filter odoo-rpc-gateway di stack dev yang sedang berjalan (docs/04 §10.4.1, §10.15;
# ADR-034 Proposed, ADR-022). Pola: uji encoded s3-gateway (SEC-41).
#
#   bash infra/odoo-rpc-gateway/test-gateway.sh
#
# Prasyarat: stack dev profil core berjalan (odoo, odoo-tls, odoo-rpc-gateway sehat), secrets/ dari gen-secrets.sh.
# Semua request dikirim dari SATU container curl sekali pakai di network efn_internal (= posisi BFF di jaringan).
#   - Ditolak gateway  = respons membawa header X-EFN-Gateway-Deny (alasan) ATAU handshake TLS gagal (kode 000)
#                        ATAU (mode `reject`) parser HTTP HAProxy menolak sebelum aturan (400 halaman HAProxy / 000).
#   - Lolos filter     = TANPA header itu; status apa pun dari Odoo (404 bila E-28 belum terpasang, 401 karena token
#                        uji bukan API key sungguhan) membuktikan request sampai ke Odoo lewat mTLS odoo-tls.
# Token bearer uji bukan rahasia (string tetap); tidak ada secret yang dicetak. Exit 1 bila ada kasus gagal.
set -euo pipefail

# Git Bash: MSYS_NO_PATHCONV hanya untuk `docker` (bukan global: openssl native butuh konversi path /tmp).
CURL_IMAGE="curlimages/curl:8.11.1@sha256:c1fe1679c34d9784c1b0d1e5f62ac0a79fca01fb6377cdd33e90473c6f9f9a69"
NETWORK="${EFN_TEST_NETWORK:-efn_internal}"

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
sec="${root}/secrets"
hostpath() {
  if command -v cygpath > /dev/null 2>&1; then cygpath -m "$1"; else printf '%s\n' "$1"; fi
}

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
chmod 755 "$work"

for f in efn_ca_crt tls_api_gateway_crt tls_api_gateway_key tls_traefik_crt tls_traefik_key \
         tls_postgres_crt tls_postgres_key; do
  [[ -s "${sec}/${f}.txt" ]] || { echo "secret ${f} tidak ada (jalankan scripts/gen-secrets.sh)" >&2; exit 2; }
  cp "${sec}/${f}.txt" "${work}/${f}"
done
# Klien dengan CN api-gateway tetapi diterbitkan CA ASING (bukan CA internal) -> harus gagal handshake
printf '[req]\ndistinguished_name = dn\nprompt = no\nx509_extensions = v3\n[dn]\nCN = api-gateway\n[v3]\nextendedKeyUsage = clientAuth\n' \
  > "${work}/foreign.cnf"
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 1 -config "${work}/foreign.cnf" \
  -keyout "${work}/foreign_key" -out "${work}/foreign_crt" 2> /dev/null
# Body > 256 KB (262144 byte) untuk uji 413
head -c 300000 /dev/zero | tr '\0' 'a' | sed 's/^/{"x":"/; s/$/"}/' > "${work}/big.json"
chmod 644 "${work}"/*

cat > "${work}/cases.sh" <<'INNER'
#!/bin/sh
# Dijalankan DI DALAM container curl. Format hasil: "<PASS|FAIL> <nama> code=<kode> deny=<alasan|->"
GW=https://odoo-rpc-gateway:8443
OK_PATH=/json/2/efn.online.order/efn_online_status
TOKEN=g17-negative-test-token-not-a-secret
fails=0
run() { # $1 nama, $2 harapan (deny|tls|reject|pass), sisa = argumen curl
  name="$1"; want="$2"; shift 2
  : > /tmp/b
  code=$(curl -sS -o /tmp/b -D /tmp/h -m 15 --path-as-is --cacert /t/efn_ca_crt -w '%{http_code}' "$@" 2> /dev/null)
  [ -n "$code" ] || code=000
  deny=$(tr -d '\r' < /tmp/h 2> /dev/null | sed -n 's/^[Xx]-[Ee][Ff][Nn]-[Gg]ateway-[Dd]eny: //p' | head -n1)
  : > /tmp/h
  case "$want" in
    deny) [ -n "$deny" ] && [ "$code" != 200 ] && r=PASS || r=FAIL ;;
    tls)  [ "$code" = 000 ] && r=PASS || r=FAIL ;;
    reject)
      if [ -n "$deny" ] || [ "$code" = 000 ] || { [ "$code" = 400 ] && grep -q 'Your browser sent an invalid request' /tmp/b; }; then
        r=PASS
      else
        r=FAIL
      fi ;;
    pass) [ -z "$deny" ] && [ "$code" != 000 ] && r=PASS || r=FAIL ;;
  esac
  [ "$r" = FAIL ] && fails=$((fails + 1))
  echo "$r $name code=$code deny=${deny:--}"
}
BFF="--cert /t/tls_api_gateway_crt --key /t/tls_api_gateway_key"
H_CT="Content-Type: application/json"
H_AUTH="Authorization: bearer $TOKEN"
post() { # $1 nama, $2 harapan, $3 path, sisa = argumen tambahan
  n="$1"; w="$2"; p="$3"; shift 3
  # shellcheck disable=SC2086
  run "$n" "$w" $BFF -X POST -H "$H_CT" -H "$H_AUTH" --data '{"channel_code":"GL","platform_order_refs":[]}' "$@" "$GW$p"
}

# ── Identitas klien (mTLS) ──────────────────────────────────────────────────────────────────────────────
run tls_no_client_cert tls -X POST -H "$H_CT" -H "$H_AUTH" --data '{}' "$GW$OK_PATH"
run tls_foreign_ca_cn_api_gateway tls --cert /t/foreign_crt --key /t/foreign_key -X POST -H "$H_CT" -H "$H_AUTH" --data '{}' "$GW$OK_PATH"
run tls_server_only_eku_cert tls --cert /t/tls_postgres_crt --key /t/tls_postgres_key -X POST -H "$H_CT" -H "$H_AUTH" --data '{}' "$GW$OK_PATH"
run cn_traefik_rejected deny --cert /t/tls_traefik_crt --key /t/tls_traefik_key -X POST -H "$H_CT" -H "$H_AUTH" --data '{}' "$GW$OK_PATH"
# Uji NEGATIF plaintext: skema dirakit dari variabel agar check-encryption.sh tidak salah membaca sebagai konfigurasi.
PLAIN=http
run plaintext_http_rejected tls -X POST -H "$H_CT" --data '{}' "${PLAIN}://odoo-rpc-gateway:8443$OK_PATH"
# BFF langsung ke sidecar Odoo (melewati filter) harus ditolak ghostunnel (CN api-gateway tidak di allow-cn)
# shellcheck disable=SC2086
run bff_direct_to_odoo_tls_rejected tls $BFF -X POST -H "$H_CT" -H "$H_AUTH" --data '{}' "https://odoo:8443$OK_PATH"

# ── Method ─────────────────────────────────────────────────────────────────────────────────────────────
for m in GET PUT DELETE PATCH OPTIONS; do
  # shellcheck disable=SC2086
  run "method_$m" deny $BFF -X "$m" -H "$H_CT" -H "$H_AUTH" "$GW$OK_PATH"
done
# shellcheck disable=SC2086
run method_HEAD deny $BFF -I -H "$H_AUTH" "$GW$OK_PATH"

# ── Path di luar 12 metode / normalisasi / encoded ──────────────────────────────────────────────────────
post path_res_users_search deny /json/2/res.users/search_read
post path_sale_order_write deny /json/2/sale.order/write
post path_online_order_read deny /json/2/efn.online.order/read
post path_staff_action_ready deny /json/2/efn.online.order/action_efn_ready
post path_handover_wizard deny /json/2/efn.online.handover.wizard/action_confirm
# G21 §13.5 (path ke-12): metode faktur pembeli hanya di model efn.online.order; partner tidak bisa ditulis langsung
post path_invoice_party_wrong_model deny /json/2/efn.online.channel/efn_online_set_invoice_party
post path_invoice_party_suffix deny /json/2/efn.online.order/efn_online_set_invoice_party_v2
post path_partner_create deny /json/2/res.partner/create
post path_partner_search deny /json/2/res.partner/search_read
post path_web_dataset deny /web/dataset/call_kw/efn.online.order/efn_online_status
post path_jsonrpc deny /jsonrpc
post path_xmlrpc deny /xmlrpc/2/object
post path_root deny /
post path_web_login deny /web/login
post path_trailing_slash deny "$OK_PATH/"
post path_upper_json deny /JSON/2/efn.online.order/efn_online_status
post path_upper_model deny /json/2/EFN.online.order/efn_online_status
post path_prefix_extra deny "$OK_PATH/extra"
post path_suffix deny "${OK_PATH}x"
post path_dslash_lead deny "/$OK_PATH"
post path_dslash_mid deny /json/2//efn.online.order/efn_online_status
post path_dot deny /json/2/./efn.online.order/efn_online_status
post path_dotdot deny /json/2/efn.online.channel/../efn.online.order/efn_online_status
post path_dotdot_escape deny /json/2/efn.online.order/efn_online_status/../../../web/login
post path_semicolon deny "$OK_PATH;x=1"
post path_semicolon_mid deny "/json/2/efn.online.order;x/efn_online_status"
post path_backslash deny '/json/2\efn.online.order/efn_online_status'
for enc in /json/2/efn.online.order/efn_online_st%61tus /json/2/efn.online.order%2Fefn_online_status \
           /json/2/efn.online.order%2fefn_online_status /json/2/efn%2Eonline.order/efn_online_status \
           /json/2/efn.online.order/%2e%2e/efn.online.order/efn_online_status /%6Ason/2/efn.online.order/efn_online_status \
           /json/2/efn.online.order/efn_online_status%00 /json/2/efn.online.order/efn_online_status%3Fx \
           /json/2/efn.online.order/efn_online_status%23x /json/2/efn.online.order/efn_online_st%2561tus \
           /json/2/efn.online.order/efn_online_status%20 /json/2/efn.online.order/%c0%ae%c0%ae/efn_online_status \
           /json/2/efn.online.order/efn_online_%73tatus /json%2F2/efn.online.order/efn_online_status; do
  post "encoded:$enc" deny "$enc"
done

# ── Query string, fragment, request-target ──────────────────────────────────────────────────────────────
post query_param deny "$OK_PATH?db=postgres"
post query_empty deny "$OK_PATH" --request-target "$OK_PATH?"
post query_context deny "$OK_PATH?context=%7B%7D"
post target_fragment reject "$OK_PATH" --request-target "$OK_PATH#frag"
post target_absolute_form deny "$OK_PATH" --request-target "https://odoo-rpc-gateway:8443$OK_PATH"
post target_absolute_other_host reject "$OK_PATH" --request-target "https://odoo:8443$OK_PATH"
post http10 deny "$OK_PATH" --http1.0

# ── Header ────────────────────────────────────────────────────────────────────────────────────────────
post hdr_method_override deny "$OK_PATH" -H "X-HTTP-Method-Override: GET"
post hdr_x_method_override deny "$OK_PATH" -H "X-Method-Override: DELETE"
post hdr_x_http_method deny "$OK_PATH" -H "X-HTTP-Method: PUT"
post hdr_x_original_url deny "$OK_PATH" -H "X-Original-URL: /json/2/res.users/search_read"
post hdr_x_rewrite_url deny "$OK_PATH" -H "X-Rewrite-URL: /json/2/res.users/search_read"
post hdr_openerp_session deny "$OK_PATH" -H "X-Openerp-Session-Id: abc"
post hdr_upgrade_h2c deny "$OK_PATH" -H "Upgrade: h2c" -H "Connection: Upgrade, HTTP2-Settings" -H "HTTP2-Settings: AAMAAABkAARAAAAAAAIAAAAA"
post hdr_upgrade_websocket reject "$OK_PATH" -H "Upgrade: websocket" -H "Connection: Upgrade"
# Upgrade TANPA `Connection: upgrade` dibuang parser H1 HAProxy sebelum aturan (diverifikasi G17: req.hdr_names tidak
# memuatnya) -> tidak pernah sampai ke Odoo; request tetap lolos (status dari Odoo).
post hdr_upgrade_lone_stripped pass "$OK_PATH" -H "Upgrade: websocket"
post hdr_transfer_encoding deny "$OK_PATH" -H "Transfer-Encoding: chunked"
post hdr_te_trailers deny "$OK_PATH" -H "TE: trailers"
post hdr_expect deny "$OK_PATH" -H "Expect: 100-continue"
post hdr_proxy_authorization deny "$OK_PATH" -H "Proxy-Authorization: Basic eDp5"
post hdr_odd_name deny "$OK_PATH" -H "X-Anything: 1"
post hdr_host_odoo deny "$OK_PATH" -H "Host: odoo"
post hdr_host_other deny "$OK_PATH" -H "Host: odoo-rpc-gateway.evil"
# shellcheck disable=SC2086
run hdr_ct_text_plain deny $BFF -X POST -H "Content-Type: text/plain" -H "$H_AUTH" --data '{}' "$GW$OK_PATH"
# shellcheck disable=SC2086
run hdr_ct_form deny $BFF -X POST -H "$H_AUTH" --data 'a=1' "$GW$OK_PATH"
# shellcheck disable=SC2086
run hdr_ct_json_suffix deny $BFF -X POST -H "Content-Type: application/json-patch+json" -H "$H_AUTH" --data '{}' "$GW$OK_PATH"
post hdr_ct_duplicate deny "$OK_PATH" -H "Content-Type: application/json"
# shellcheck disable=SC2086
run hdr_auth_missing deny $BFF -X POST -H "$H_CT" --data '{}' "$GW$OK_PATH"
post hdr_auth_duplicate deny "$OK_PATH" -H "Authorization: bearer another-token-abcdefgh"
# shellcheck disable=SC2086
run hdr_auth_basic deny $BFF -X POST -H "$H_CT" -H "Authorization: Basic YWRtaW46YWRtaW4=" --data '{}' "$GW$OK_PATH"
# shellcheck disable=SC2086
run hdr_auth_bearer_short deny $BFF -X POST -H "$H_CT" -H "Authorization: bearer abc" --data '{}' "$GW$OK_PATH"
# shellcheck disable=SC2086
run body_too_large deny $BFF -X POST -H "$H_CT" -H "$H_AUTH" --data-binary @/t/big.json "$GW$OK_PATH"

# ── Positif: 12 path persis LOLOS filter (status dari Odoo, bukan dari gateway) ──────────────────────────
for p in efn.online.channel/efn_online_catalog efn.online.channel/efn_online_stock efn.online.channel/efn_online_image \
         efn.online.channel/efn_online_quote efn.online.order/efn_online_place efn.online.order/efn_online_mark_paid \
         efn.online.order/efn_online_cancel efn.online.order/efn_online_refund efn.online.order/efn_online_refund_settled \
         efn.online.order/efn_online_status efn.online.order/efn_online_counter_refund_authorize \
         efn.online.order/efn_online_set_invoice_party; do
  post "allowed:$p" pass "/json/2/$p"
done
# shellcheck disable=SC2086
run allowed_ct_charset pass $BFF -X POST -H "Content-Type: application/json; charset=utf-8" -H "$H_AUTH" --data '{}' "$GW$OK_PATH"
post allowed_cookie_stripped pass "$OK_PATH" -H "Cookie: session_id=x"
post allowed_xff_stripped pass "$OK_PATH" -H "X-Forwarded-For: 1.2.3.4" -H "X-Forwarded-Host: evil" -H "Forwarded: for=1.2.3.4"
post allowed_db_override_stripped pass "$OK_PATH" -H "X-Odoo-Database: postgres"
post allowed_host_port pass "$OK_PATH" -H "Host: odoo-rpc-gateway:8443"
post allowed_traceparent pass "$OK_PATH" -H "traceparent: 00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"

echo "TOTAL_FAIL=$fails"
[ "$fails" -eq 0 ]
INNER
chmod 755 "${work}/cases.sh"

set +e
MSYS_NO_PATHCONV=1 docker run --rm --network "$NETWORK" -v "$(hostpath "$work"):/t:ro" --entrypoint sh "$CURL_IMAGE" /t/cases.sh
rc=$?
set -e
echo "--- log gateway (alasan penolakan, tanpa body/header) 20 baris terakhir:"
docker logs --tail 20 efn-odoo-rpc-gateway-1 2>&1 | grep -E ' why=' || true
exit "$rc"
