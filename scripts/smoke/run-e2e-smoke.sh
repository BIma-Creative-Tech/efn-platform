#!/usr/bin/env bash
# Smoke E2E stack dev EFN dengan TLS/mTLS aktif (gelombang 8, docs/testing/E2E_SMOKE_REPORT.md).
#
# Prasyarat: stack dev jalan (profil core + data + ai), modul efn_* terpasang di DB efn_odoo
# (docs/runbooks/dev-stack-boot.md). HANYA untuk dev: memanipulasi `dsar_request.scheduled_at` lewat superuser
# PostgreSQL (socket lokal container) untuk melewati masa tenggang, membaca OTP dari log console BFF
# (APP_ENV=dev), dan memanggil endpoint simulate payment (404 di produksi).
#
# Tidak ada secret di skrip: password dibaca DI DALAM container dari /run/secrets. Nomor HP = nomor fiktif acak.
# Idempoten: setiap run memakai nomor, order, dan foto baru; data Odoo smoke (config/produk/metode) dipakai ulang.
#
# Pemakaian (Git Bash/Linux):  bash scripts/smoke/run-e2e-smoke.sh [a b c d e f g l o v]   (default: a b c d f e g l o;
#   v = jalur VLM nyata, opsional, butuh model OLLAMA_VISION_MODEL di Ollama; o = penjualan online G17, terakhir
#   karena me-recreate api-gateway untuk uji kill switch)
# Keluaran: baris `SMOKE_RESULT {...}` + ringkasan PASS/FAIL; exit 1 bila ada FAIL.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SMOKE_DIR="${ROOT}/scripts/smoke"
# Docker Desktop (Windows/Git Bash) tidak selalu ada di PATH; override dengan env DOCKER_BIN.
DOCKER_BIN="${DOCKER_BIN:-${LOCALAPPDATA:-/nonexistent}/Programs/DockerDesktop/resources/bin}"
command -v cygpath > /dev/null && DOCKER_BIN="$(cygpath -u "$DOCKER_BIN")"
[[ -d "$DOCKER_BIN" ]] && PATH="$PATH:$DOCKER_BIN"
command -v docker > /dev/null || { echo "docker dibutuhkan" >&2; exit 2; }
command -v openssl > /dev/null || { echo "openssl dibutuhkan" >&2; exit 2; }

P="efn"
BFF="${P}-api-gateway-1"; ODOO="${P}-odoo-1"; PG="${P}-postgres-1"; CH="${P}-clickhouse-1"
SCENARIOS=("$@"); [[ ${#SCENARIOS[@]} -eq 0 ]] && SCENARIOS=(a b c d f e g l p o)

WORK="$(mktemp -d)"
RESULTS="${WORK}/results.txt"
: > "$RESULTS"
cp "${ROOT}/secrets/dev_edge_crt.txt" "${WORK}/edge_ca.crt"
cp "${SMOKE_DIR}/bff_smoke.py" "${SMOKE_DIR}/odoo_rpc_smoke.py" "${SMOKE_DIR}/obs_smoke.py" "${SMOKE_DIR}/online_smoke.py" "${WORK}/"
# G18: rekonsiliasi harian Poin EFN dijalankan di container ETL (stdin), bukan disalin ke WORK
WORK_W="$WORK"; command -v cygpath > /dev/null && WORK_W="$(cygpath -w "$WORK")"
SECRETS_W="${ROOT}/secrets"; command -v cygpath > /dev/null && SECRETS_W="$(cygpath -w "$SECRETS_W")"
PY_IMAGE="python:3.12-bookworm@sha256:e91fec3d1ac69f04e4eddcd29c327e630ce34658cf31075bfa7e8b0e052bafea"

docker logs -f --since 1s "$BFF" > "${WORK}/bff.log" 2>&1 &
LOGPID=$!
trap 'kill $LOGPID 2>/dev/null || true' EXIT

TRAEFIK_IP="$(docker inspect "${P}-traefik-1" --format '{{(index .NetworkSettings.Networks "efn_edge").IPAddress}}')"
rand8() { printf '%08d' $(( ( $(od -An -N4 -tu4 /dev/urandom | tr -d ' ') ) % 100000000 )); }
newuuid() { local h; h="$(openssl rand -hex 16 | tr -d '\r\n')"; echo "${h:0:8}-${h:8:4}-4${h:13:3}-a${h:17:3}-${h:20:12}"; }
PHONE="62812$(rand8)"          # akun Super-App (dipakai juga oleh partner Odoo E untuk DSAR lintas store)
MASKED="${PHONE:0:5}****${PHONE: -3}"

record() { echo "SMOKE_RESULT $1" | tee -a "$RESULTS"; }
check() { # nama ok(0/1) detail
  local ok=false; [[ "$2" == 1 ]] && ok=true
  record "{\"check\": \"$1\", \"ok\": $ok, \"detail\": \"${3//\"/\'}\"}"
}

py() { # subperintah bff_smoke.py
  MSYS_NO_PATHCONV=1 docker run --rm --network efn_edge \
    --add-host "api.efn.local:${TRAEFIK_IP}" --add-host "app.efn.local:${TRAEFIK_IP}" \
    -v "${WORK_W}:/work" -v efn-pip-cache:/root/.cache/pip \
    -e SMOKE_PHONE="$PHONE" -e SMOKE_CONSENT_VERSION="location@2026-10-06" \
    python:3.12-bookworm@sha256:e91fec3d1ac69f04e4eddcd29c327e630ce34658cf31075bfa7e8b0e052bafea sh -c "pip install -q pillow==11.3.0 > /dev/null 2>&1; python /work/bff_smoke.py $1" \
    2>&1 | grep '^SMOKE_RESULT' | tee -a "$RESULTS" || true
}

odoo() { # -e K=V ... ; mencetak satu baris SMOKE_RESULT
  # G13: traceback psycopg2 (`psycopg2.errors.UndefinedColumn: ...`) tidak memuat "Error" -> dulu grep kosong +
  # pipefail menghentikan seluruh run tanpa ringkasan. Sekarang baris terakhir yang relevan atau penanda gagal.
  local o
  o="$(MSYS_NO_PATHCONV=1 docker exec -i "$@" "$ODOO" odoo shell -d efn_odoo --no-http --log-level=warn \
    < "${SMOKE_DIR}/odoo_pos_smoke.py" 2>&1 | grep -E '^SMOKE_RESULT|Error|errors\.' | tail -1 || true)"
  if [[ -n "$o" ]]; then echo "$o"; else echo 'SMOKE_RESULT {"ok": false, "error": "odoo_shell_no_result"}'; fi
}

obs() { # G13c: query Loki / Alertmanager nyata (mTLS, sertifikat klien Grafana read-only) -> satu baris SMOKE_RESULT
  MSYS_NO_PATHCONV=1 docker run --rm --network efn_internal -v "${WORK_W}:/work:ro" \
    -v "${SECRETS_W}/efn_ca_crt.txt:/s/efn_ca_crt.txt:ro" -v "${SECRETS_W}/tls_grafana_crt.txt:/s/tls_grafana_crt.txt:ro" \
    -v "${SECRETS_W}/tls_grafana_key.txt:/s/tls_grafana_key.txt:ro" "$PY_IMAGE" python /work/obs_smoke.py "$@" 2>&1 \
    | grep '^SMOKE_RESULT' | tail -1 || true
}

MIGRATION_ON=0
docker inspect "$BFF" --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -qx 'MIGRATION_ENABLED=true' && MIGRATION_ON=1
migrate() { # G13c: <members|opening-balance> <json> -> "<body>|<http>" ; kunci migrasi dibaca DI DALAM container
  MSYS_NO_PATHCONV=1 docker run --rm --network efn_internal -v "${SECRETS_W}/migration_api_key.txt:/s/k:ro" \
    -v "${SECRETS_W}/efn_ca_crt.txt:/s/ca:ro" --entrypoint /bin/sh \
    curlimages/curl:8.11.1@sha256:c1fe1679c34d9784c1b0d1e5f62ac0a79fca01fb6377cdd33e90473c6f9f9a69 -c \
    'printf "X-EFN-Migration-Key: %s\n" "$(cat /s/k)" > /tmp/h && curl -sS -m 10 --cacert /s/ca -H @/tmp/h \
       -H "Content-Type: application/json" -d "$2" -w "|%{http_code}" "https://api-gateway:3000/internal/v1/migration/$1"' \
    sh "$1" "$2" 2>&1 | tr -d '\r\n' || true
}

psql_platform() { MSYS_NO_PATHCONV=1 docker exec -i "$PG" psql -U postgres -d efn_platform -At "$@"; }
chq() {
  MSYS_NO_PATHCONV=1 docker exec -i "$CH" sh -c \
    'clickhouse-client --secure --host clickhouse --port 9440 --user "${CLICKHOUSE_USER:-efn}" --password "$(cat /run/secrets/clickhouse_password)" --multiquery'
}

otp_code() { # masked -> kode terakhir di log BFF (console sender dev)
  for _ in $(seq 1 20); do
    local c; c="$(grep -F "OTP untuk $1:" "${WORK}/bff.log" | tail -1 | grep -oE ': [0-9]{6}' | tr -dc '0-9' || true)"
    [[ -n "$c" ]] && { echo "$c"; return 0; }
    sleep 1
  done
  echo "000000"
}

field() { sed -nE "s/.*\"$1\": \"?([^\",}]*)\"?.*/\1/p" <<< "$2" | head -1; }
# Detail check tanpa isi `message` Odoo (teks bebas, bisa memuat PII). `sed -E` + fungsi -> satu tempat.
nomsg() { sed -E 's/"message": "[^"]*"/"message": "…"/' <<< "$1"; }

pos_otp() { # uuid partner amount -> verify
  local r m code
  r="$(odoo -e SMOKE_STEP=otp_request -e SMOKE_ORDER_UUID="$1" -e SMOKE_PARTNER="$2" -e SMOKE_AMOUNT="$3")"
  m="$(field member_masked "$r")"
  code="$(otp_code "$m")"
  r="$(odoo -e SMOKE_STEP=otp_verify -e SMOKE_ORDER_UUID="$1" -e SMOKE_CODE="$code")"
  check "pos.otp_verify[$2]" "$([[ "$(field ok "$r")" == true ]] && echo 1 || echo 0)" "$r"
}

# ---------------------------------------------------------------------------------------------------------------
scenario_a() {
  echo "== a. Login OTP Super-App ($MASKED)"
  py login
  py login_web
  py negatives
}

scenario_b() {
  echo "== b. Consent + Price Hunter"
  py consent
  py report
  py report_dup
  local raw rows
  raw="$(MSYS_NO_PATHCONV=1 docker exec "$PG" pg_dump -U postgres -d efn_platform --data-only --schema=platform | grep -c "${PHONE:4}" || true)"
  check "b.db_no_raw_phone" "$([[ "$raw" == 0 ]] && echo 1 || echo 0)" "hits=$raw (pg_dump skema platform)"
  rows="$(psql_platform -c "select count(*) from platform.price_hunter_report where length(user_hash)=64 and get_byte(user_enc,0)=1 and length(user_enc)=42 and created_at > now() - interval '15 minutes'")"
  check "b.report_user_hash_enc" "$([[ "$rows" -ge 1 ]] && echo 1 || echo 0)" "rows=$rows (user_hash 64 hex, user_enc v1 AES-GCM)"
  local sse
  sse="$(cd "$ROOT" && MSYS_NO_PATHCONV=1 docker compose -f infra/compose/docker-compose.yml -f infra/compose/docker-compose.dev.yml \
    --env-file infra/compose/.env --profile core run --rm --no-deps --entrypoint /bin/sh minio-init -c \
    'mkdir -p /tmp/.mc/certs/CAs && cp /run/secrets/efn_ca_crt /tmp/.mc/certs/CAs/ && mc alias set efn https://minio:9000 "$(cat /run/secrets/minio_root_user)" "$(cat /run/secrets/minio_root_password)" >/dev/null && O=$(mc ls --recursive efn/market-scout/reports/ | tail -1 | awk "{print \$NF}") && mc stat "efn/market-scout/reports/$O" | grep -i "^Encryption"' 2>/dev/null | tail -1)"
  check "b.minio_sse" "$([[ "$sse" == *SSE-S3* ]] && echo 1 || echo 0)" "$sse"
  local notif
  notif="$(grep -F "notify.push price_hunter_valid" "${WORK}/bff.log" | grep -cF "${MASKED}: sent" || true)"
  check "b.notification_recorded" "$([[ "$notif" -ge 1 ]] && echo 1 || echo 0)" "notify.push price_hunter_valid count=$notif"
  # Gelombang 10 (docs/04 §8.12/§8.14): foto masuk lewat filter s3-gateway; access key lama bernama tetap ditolak,
  # access key acak lolos autentikasi (AccessDenied = policy least-privilege, bukan InvalidAccessKeyId).
  # `docker logs --since` tidak andal untuk log HAProxy ini (Docker Desktop membaca file log lama, gelombang 10):
  # pakai --tail lalu cari id report dari run ini (PUT oleh BFF + GET oleh ocr-worker).
  local gw repid
  repid="$(sed -nE 's/.*"report_id": "([0-9a-f-]{36})".*/\1/p' "${WORK}/state.json" 2>/dev/null || true)"
  gw="$(docker logs --tail 400 "${P}-s3-gateway-1" 2>&1 | grep -F "${repid:-none}" | grep -oE ' (PUT|GET) [^ ]+ 200 ' | awk '{print $1}' | sort -u | tr '\n' ' ' || true)"
  check "b.upload_via_s3_gateway" "$([[ -n "$repid" && "$gw" == *GET* && "$gw" == *PUT* ]] && echo 1 || echo 0)" "report=$repid metode 200 di log s3-gateway: $gw"
  local sw keys
  sw="$(cd "$ROOT/secrets" && pwd)"; command -v cygpath > /dev/null && sw="$(cygpath -w "$sw")"
  keys="$(MSYS_NO_PATHCONV=1 docker run --rm --network efn_data -v "${sw}:/s:ro" --entrypoint /bin/sh "${P}/minio:dev" -c '
    export MC_CONFIG_DIR=/tmp/.mc; mkdir -p /tmp/.mc/certs/CAs && cp /s/efn_ca_crt.txt /tmp/.mc/certs/CAs/ca.crt
    t() { mc alias set --api S3v4 k https://s3-gateway:9000 "$1" "$(cat "/s/$2")" > /dev/null 2>&1
          r="$(mc ls k/market-scout/reports/ 2>&1 | tail -1)"
          case "$r" in *"does not exist"*) echo -n "$3=invalid ";; *"Access Denied"*) echo -n "$3=authn ";; *) echo -n "$3=other ";; esac; }
    t efn-bff minio_bff_secret.txt old_bff; t efn-ocr minio_ocr_secret.txt old_ocr
    t pgbackup minio_backup_password.txt old_backup
    t "$(cat /s/minio_bff_access_key.txt)" minio_bff_secret.txt new_bff
    t "$(cat /s/minio_ocr_access_key.txt)" minio_ocr_secret.txt new_ocr' 2>&1 | tail -1)"
  check "b.old_access_keys_rejected" "$([[ "$keys" == *"old_bff=invalid old_ocr=invalid old_backup=invalid"* ]] && echo 1 || echo 0)" "$keys"
  check "b.random_access_keys_authenticate" "$([[ "$keys" == *"new_bff=authn new_ocr=authn"* ]] && echo 1 || echo 0)" "$keys"
}

scenario_v() { # opsional (tidak di default): jalur VLM nyata, butuh model OLLAMA_VISION_MODEL di Ollama
  echo "== v. Price Hunter jalur VLM (Ollama)"
  # Akun skenario a sudah dihapus bila e jalan lebih dulu (urutan "a b c d f e g v"): login nomor fiktif baru.
  if [[ "${ERASED:-0}" == 1 ]]; then PHONE="62812$(rand8)"; MASKED="${PHONE:0:5}****${PHONE: -3}"; py login
  elif [[ ! -s "${WORK}/state.json" ]]; then py login; fi
  py consent
  py report_vlm
}

scenario_c() {
  echo "== c. POS OTP gate (Odoo -> BFF HTTPS)"
  rp() { echo "0812$(rand8)"; }
  odoo -e SMOKE_STEP=setup -e SMOKE_PHONE_A="$(rp)" -e SMOKE_PHONE_B="$(rp)" -e SMOKE_PHONE_C="$(rp)" \
    -e SMOKE_PHONE_D="$(rp)" -e SMOKE_PHONE_E="0${PHONE:2}" | tee -a "$RESULTS"
  local u r
  u="$(newuuid)"; pos_otp "$u" A 45000
  r="$(odoo -e SMOKE_STEP=sync -e SMOKE_ORDER_UUID="$u" -e SMOKE_PARTNER=A)"
  check "c.consume_consumed" "$([[ "$(field gate_state "$r")" == consumed && "$(field challenge_state "$r")" == consumed ]] && echo 1 || echo 0)" "$r"
  r="$(odoo -e SMOKE_STEP=sync -e SMOKE_ORDER_UUID="$u" -e SMOKE_PARTNER=A)"
  check "c.resync_idempotent" "$([[ "$(field gate_state "$r")" == consumed ]] && echo 1 || echo 0)" "$r"
  u="$(newuuid)"; pos_otp "$u" C 45000
  r="$(odoo -e SMOKE_STEP=sync -e SMOKE_ORDER_UUID="$u" -e SMOKE_PARTNER=B)"
  check "c.member_mismatch_review" "$([[ "$(field gate_state "$r")" == review && "$(field gate_note "$r")" == *member_mismatch* ]] && echo 1 || echo 0)" "$r"
  u="$(newuuid)"; pos_otp "$u" D 45000
  r="$(odoo -e SMOKE_STEP=sync -e SMOKE_ORDER_UUID="$u" -e SMOKE_PARTNER=D -e SMOKE_QTY=3)"
  check "c.amount_mismatch_review" "$([[ "$(field gate_state "$r")" == review && "$(field gate_note "$r")" == *amount_mismatch* ]] && echo 1 || echo 0)" "$r"
  r="$(odoo -e SMOKE_STEP=sync -e SMOKE_ORDER_UUID="$(newuuid)" -e SMOKE_PARTNER=A)"
  check "c.paid_reward_without_otp_rejected" "$([[ "$(field ok "$r")" == false && "$(field error "$r")" == UserError ]] && echo 1 || echo 0)" "$r"
  r="$(odoo -e SMOKE_STEP=sync -e SMOKE_ORDER_UUID="$(newuuid)" -e SMOKE_PARTNER=A -e SMOKE_REWARD=0)"
  check "c.regular_order_ok" "$([[ "$(field state "$r")" == paid ]] && echo 1 || echo 0)" "$r"
  # Gelombang 10 (ADR-031): efn_audit mencatat harga produk & HP partner (ter-mask)
  r="$(odoo -e SMOKE_STEP=audit -e SMOKE_NEW_PHONE="0813$(rand8)")"
  check "c.audit_price_logged" "$([[ "$(field price_logged "$r")" == true ]] && echo 1 || echo 0)" "$r"
  check "c.audit_phone_masked" "$([[ "$(field phone_logged "$r")" == true && "$(field phone_masked "$r")" == true && "$(field phone_raw_hidden "$r")" == true ]] && echo 1 || echo 0)" "$r"
  check "c.audit_partner_e_logged" "$([[ "$(field partner_e_logs "$r")" -ge 1 ]] && echo 1 || echo 0)" "logs=$(field partner_e_logs "$r")"
  # R2 (docs/04 §8.13): saldo POS via POST body dengan service key Odoo; GET lama 404
  local pts
  r="$(odoo -e SMOKE_STEP=balance)"
  pts="$(sed -nE 's/.*"points_after": ([0-9]+).*/\1/p' "${WORK}/state.json" 2>/dev/null || true)"
  check "c.pos_balance_post_200" "$([[ "$(field http "$r")" == 200 && -n "$(field points_balance "$r")" && ( -z "$pts" || "$(field points_balance "$r")" == "$pts" ) ]] && echo 1 || echo 0)" "$r (points_after Super-App=${pts:-?})"
  check "c.pos_balance_old_get_404" "$([[ "$(field old_get_http "$r")" == 404 ]] && echo 1 || echo 0)" "old_get_http=$(field old_get_http "$r")"
  check "c.pos_balance_invalid_400" "$([[ "$(field invalid_http "$r")" == 400 ]] && echo 1 || echo 0)" "invalid_http=$(field invalid_http "$r")"
}

scenario_d() {
  echo "== d. QRIS POS mock"
  local u p r iid tx
  u="$(newuuid)"; p="$(newuuid)"
  r="$(odoo -e SMOKE_STEP=gw_create -e SMOKE_ORDER_UUID="$u" -e SMOKE_PAYMENT_UUID="$p" -e SMOKE_AMOUNT=50000)"
  iid="$(field intent_id "$r")"
  check "d.create_pending" "$([[ "$(field status "$r")" == pending ]] && echo 1 || echo 0)" "$r"
  r="$(odoo -e SMOKE_STEP=sync -e SMOKE_ORDER_UUID="$u" -e SMOKE_REWARD=0 -e "SMOKE_PAYMENT={\"method\":\"qris\",\"transaction_id\":\"forged-$(newuuid)\",\"uuid\":\"$p\"}")"
  check "d.forged_tx_rejected" "$([[ "$r" == *intent_unknown* ]] && echo 1 || echo 0)" "$r"
  r="$(odoo -e SMOKE_STEP=gw_simulate -e SMOKE_INTENT_ID="$iid")"
  check "d.simulate_paid" "$([[ "$(field status "$r")" == paid ]] && echo 1 || echo 0)" "$r"
  r="$(odoo -e SMOKE_STEP=gw_status -e SMOKE_INTENT_ID="$iid")"
  tx="$(field transaction_id "$r")"
  check "d.status_paid" "$([[ "$(field status "$r")" == paid && -n "$tx" ]] && echo 1 || echo 0)" "$r"
  r="$(odoo -e SMOKE_STEP=sync -e SMOKE_ORDER_UUID="$u" -e SMOKE_REWARD=0 -e SMOKE_QTY=2 -e "SMOKE_PAYMENT={\"method\":\"qris\",\"transaction_id\":\"$tx\",\"uuid\":\"$p\"}")"
  check "d.amount_mismatch_rejected" "$([[ "$r" == *amount_mismatch* ]] && echo 1 || echo 0)" "$r"
  r="$(odoo -e SMOKE_STEP=sync -e SMOKE_ORDER_UUID="$u" -e SMOKE_REWARD=0 -e "SMOKE_PAYMENT={\"method\":\"qris\",\"transaction_id\":\"$tx\",\"uuid\":\"$p\"}")"
  check "d.sync_paid_ok" "$([[ "$(field state "$r")" == paid ]] && echo 1 || echo 0)" "$r"
  r="$(odoo -e SMOKE_STEP=sync -e SMOKE_ORDER_UUID="$(newuuid)" -e SMOKE_REWARD=0 -e "SMOKE_PAYMENT={\"method\":\"qris\",\"transaction_id\":\"$tx\",\"uuid\":\"$(newuuid)\"}")"
  check "d.intent_reuse_rejected" "$([[ "$r" == *order_mismatch* || "$r" == *intent_reused* ]] && echo 1 || echo 0)" "$r"
}

scenario_f() {
  echo "== f. ETL -> ClickHouse (tunggu 1 siklus ETL dev 120 s)"
  local mh n1 n2 n3 mb g
  # R5 (gelombang 9): grant baca level-kolom efn_etl di DB Odoo (sama dengan langkah deploy.sh; idempoten, sebagai
  # pemilik tabel efn_odoo lewat socket lokal). Job sales_fact_pos aktif di dev (ETL_ENABLE_ODOO=true).
  g="$(if MSYS_NO_PATHCONV=1 docker exec -i "$PG" psql -X -q -v ON_ERROR_STOP=1 -U efn_odoo -d efn_odoo -f - \
        < "${ROOT}/services/etl/sql/odoo-readonly-grants.sql" 2>&1; then echo GRANT_OK; fi)"
  check "f.odoo_etl_grants" "$([[ "$g" == *GRANT_OK* ]] && echo 1 || echo 0)" "$(tail -c 160 <<< "$g")"
  mh="$(psql_platform -c "select user_hash from platform.price_hunter_report where status='valid' order by created_at desc limit 1")"
  for _ in $(seq 1 30); do
    n1="$(echo "select count() from efn_dwh.competitor_prices final where user_hash='$mh'" | chq)"
    n2="$(echo "select count() from efn_dwh.loyalty_events final where member_hash='$mh'" | chq)"
    n3="$(echo "select count() from efn_dwh.fact_sales_line final where source='pos' and _loaded_at > now() - interval 1 day" | chq)"
    [[ "$n1" -ge 1 && "$n2" -ge 1 && "$n3" -ge 1 ]] && break
    sleep 10
  done
  check "f.ch_competitor_prices" "$([[ "$n1" -ge 1 ]] && echo 1 || echo 0)" "rows=$n1 untuk user_hash report valid terakhir"
  check "f.ch_loyalty_events" "$([[ "$n2" -ge 1 ]] && echo 1 || echo 0)" "rows=$n2"
  # Order POS smoke (skenario c/d) -> fact_sales_line (R5). customer_id = id partner saja (tanpa PII).
  check "f.ch_fact_sales_line" "$([[ "$n3" -ge 1 ]] && echo 1 || echo 0)" "rows=$n3 (pos, dimuat 24 jam terakhir)"
  mb="$(docker inspect "${P}-metabase-1" --format '{{.State.Health.Status}}')/$(docker inspect "${P}-metabase-tls-1" --format '{{.State.Health.Status}}')"
  check "f.metabase_healthy" "$([[ "$mb" == healthy/healthy ]] && echo 1 || echo 0)" "metabase/metabase-tls=$mb"
  # SEC-86: wrapper kunci enkripsi at rest benar-benar dipakai container yang berjalan
  mbep="$(docker inspect "${P}-metabase-1" --format '{{json .Config.Entrypoint}}')"
  check "f.metabase_entrypoint" "$([[ "$mbep" == '["/usr/local/bin/efn-metabase-entrypoint"]' ]] && echo 1 || echo 0)" "entrypoint=$mbep"
  echo "$mh" > "${WORK}/member_hash"
}

scenario_e() {
  echo "== e. DSAR hapus akun lintas store"
  ERASED=1   # akun $PHONE dihapus di skenario ini; skenario sesudahnya (v) wajib login akun baru
  # OTP login + login_web + reauth = 3 permintaan / 15 menit untuk nomor yang sama (batas rate limit per member)
  py delete
  local rid mh st r
  rid="$(psql_platform -c "select id from platform.dsar_request where type='delete' and status='scheduled' order by requested_at desc limit 1")"
  mh="$(psql_platform -c "select member_hash from platform.dsar_request where id='$rid'")"
  r="$(odoo -e SMOKE_STEP=partner -e SMOKE_MEMBER_HASH="$mh")"
  check "e.odoo_partner_hash_matches_bff" "$([[ "$(field by_hash "$r")" -ge 1 ]] && echo 1 || echo 0)" "$r"
  # G13c SEC-65: migrasi K-7 untuk subjek yang sedang DSAR delete -> 409 member_deleting (member & saldo pembuka).
  # Hanya bila BFF dev dijalankan dengan MIGRATION_ENABLED=true (endpoint 404 bila tidak; runbook E2E_SMOKE_REPORT).
  if [[ "$MIGRATION_ON" == 1 ]]; then
    r="$(migrate members "{\"source_ref\":\"$(newuuid)\",\"member_ref\":\"$PHONE\"}")"
    r="$r ; $(migrate opening-balance "{\"source_ref\":\"$(newuuid)\",\"member_ref\":\"$PHONE\",\"points\":10,\"company_code\":\"FNB\"}")"
    check "e.migration_member_deleting_409" "$([[ "$r" == *member_deleting*"|409 ; "*member_deleting*"|409" ]] && echo 1 || echo 0)" "$r"
  else
    echo "   (cek migrasi SEC-65 dilewati: MIGRATION_ENABLED != true di $BFF)"
  fi
  # Dev: lewati masa tenggang (DSAR_DELETE_GRACE_DAYS) dengan memajukan scheduled_at
  psql_platform -c "update platform.dsar_request set scheduled_at = now() - interval '1 second' where id='$rid'" > /dev/null
  for _ in $(seq 1 15); do
    st="$(psql_platform -c "select status||' '||coalesce(array_to_string(stores_pending, ','),'') from platform.dsar_request where id='$rid'")"
    [[ "$st" == processing* || "$st" == completed* ]] && break
    sleep 6
  done
  check "e.platform_erasure_processing" "$([[ "$st" == processing* ]] && echo 1 || echo 0)" "$st"
  local left
  left="$(psql_platform -c "select (select count(*) from platform.member where member_hash='$mh' and deleted_at is null) || '/' || (select count(*) from platform.price_hunter_report where user_hash='$mh') || '/' || (select count(*) from platform.loyalty_ledger where member_ref_hash='$mh')")"
  check "e.platform_rows_tombstoned" "$([[ "$left" == 0/0/0 ]] && echo 1 || echo 0)" "member_aktif/report/ledger dengan hash lama = $left"
  # SEC-31 (docs/04 §8.13): tombstone = 32 byte acak, BUKAN sha256("erased:"||request_id) (desain lama) dan bukan
  # member_hash; tidak tersimpan di dsar_request; satu tombstone per subjek (report & ledger sama).
  local repid tomb derived tinfo
  repid="$(sed -nE 's/.*"report_id": "([0-9a-f-]{36})".*/\1/p' "${WORK}/state.json" 2>/dev/null || true)"
  tomb="$(psql_platform -c "select user_hash from platform.price_hunter_report where id='$repid'")"
  derived="$(printf 'erased:%s' "$rid" | openssl dgst -sha256 -r | cut -c1-64)"
  tinfo="$(psql_platform -c "select (select count(*) from platform.loyalty_ledger where member_ref_hash='$tomb') || '/' || (select count(*) from platform.dsar_request where member_hash='$tomb')")"
  check "e.tombstone_random_unlinkable" "$([[ ${#tomb} == 64 && "$tomb" != "$derived" && "$tomb" != "$mh" && "${tinfo#*/}" == 0 && "${tinfo%/*}" -ge 1 ]] && echo 1 || echo 0)" "len=${#tomb} beda_turunan_request_id=$([[ "$tomb" != "$derived" ]] && echo ya || echo TIDAK) ledger_same_tombstone/dsar_rows=$tinfo"
  r="$(odoo -e SMOKE_STEP=erasure_cron)"
  r="$(odoo -e SMOKE_STEP=partner -e SMOKE_MEMBER_HASH="$mh")"
  check "e.odoo_partner_anonymized" "$([[ "$(field by_hash "$r")" == 0 ]] && echo 1 || echo 0)" "$r"
  for _ in $(seq 1 30); do
    st="$(psql_platform -c "select status||' '||coalesce(array_to_string(stores_pending, ','),'') from platform.dsar_request where id='$rid'")"
    [[ "$st" == completed* ]] && break
    sleep 10
  done
  check "e.dsar_completed" "$([[ "$st" == completed* ]] && echo 1 || echo 0)" "$st"
  local mhn
  mhn="$(psql_platform -c "select count(*) filter (where member_hash is null) || '/' || count(*) from platform.dsar_request where id='$rid'")"
  check "e.dsar_member_hash_nulled" "$([[ "$mhn" == 1/1 ]] && echo 1 || echo 0)" "member_hash NULL/baris=$mhn"
  if [[ "$MIGRATION_ON" == 1 ]]; then
    # Setelah erasure selesai: subjek ada di platform.erasure_suppression -> 409 member_erased, tidak hidup kembali
    r="$(migrate members "{\"source_ref\":\"$(newuuid)\",\"member_ref\":\"$PHONE\"}")"
    local alive
    alive="$(psql_platform -c "select count(*) from platform.member where member_hash='$mh' and deleted_at is null")"
    check "e.migration_member_erased_409" "$([[ "$r" == *member_erased*"|409" && "$alive" == 0 ]] && echo 1 || echo 0)" "$r member_aktif=$alive"
    r="$(grep -cF 'EFN_LOYALTY_ANOMALY {"kind":"migration_erased_member"' "${WORK}/bff.log" || true)"
    check "e.migration_anomaly_logged" "$([[ "$r" -ge 3 ]] && echo 1 || echo 0)" "baris migration_erased_member=$r (2 deleting + 1 erased)"
  fi
  # efn_audit (ADR-031): log res.partner subjek terhapus, ref lintas model kosong, log purge tanpa id
  r="$(odoo -e SMOKE_STEP=audit_partner)"
  check "e.odoo_audit_logs_purged" "$([[ "$(field partner_logs "$r")" == 0 && "$(field ref_lines "$r")" == 0 && "$(field purge_note "$r")" == erasure* && "$(field purge_res_id "$r")" == null ]] && echo 1 || echo 0)" "$r"
  check "e.odoo_pending_markers_cleared" "$([[ "$(field pending_markers "$r")" == 0 ]] && echo 1 || echo 0)" "pending=$(field pending_markers "$r")"
  local chn
  chn="$( (echo "select count() from efn_dwh.competitor_prices where user_hash='$mh'"; ) | chq)/$(echo "select count() from efn_dwh.loyalty_events where member_hash='$mh'" | chq)"
  check "e.clickhouse_rows_deleted" "$([[ "$chn" == 0/0 ]] && echo 1 || echo 0)" "competitor_prices/loyalty_events=$chn"
  py requests
}

scenario_g() {
  echo "== g. Negatif enkripsi (regresi)"
  local out
  out="$(MSYS_NO_PATHCONV=1 docker run --rm --network efn_data postgres:16.15-bookworm@sha256:0ea6700a3b4f0ae6ce746519073558aed4d88a79d8d07622a9a644946c7319c4 psql "host=postgres dbname=efn_platform user=efn_bff sslmode=disable connect_timeout=5" -c 'select 1' 2>&1 || true)"
  check "g.pg_plaintext_rejected" "$([[ "$out" == *"no encryption"* || "$out" == *"pg_hba"* ]] && echo 1 || echo 0)" "$(head -c 160 <<< "$out")"
  out="$(MSYS_NO_PATHCONV=1 docker run --rm --network efn_internal curlimages/curl:8.11.1@sha256:c1fe1679c34d9784c1b0d1e5f62ac0a79fca01fb6377cdd33e90473c6f9f9a69 -sS -m 5 -o /dev/null -w '%{http_code}' https://odoo:8443/web/login --cacert /dev/null -k 2>&1 || true)"
  check "g.odoo_tls_without_client_cert_rejected" "$([[ "$out" != 200 ]] && echo 1 || echo 0)" "$(head -c 160 <<< "$out")"
  # ADR-030 (gelombang 9): storage tidak terjangkau dari network klien; semua lewat filter L7 s3-gateway
  out="$(MSYS_NO_PATHCONV=1 docker run --rm --network efn_data curlimages/curl:8.11.1@sha256:c1fe1679c34d9784c1b0d1e5f62ac0a79fca01fb6377cdd33e90473c6f9f9a69 -sS -m 5 -o /dev/null -w '%{http_code}' https://minio:9000/minio/health/live 2>&1 || true)"
  # Lolos bila TIDAK ada respons HTTP sama sekali (nama tak ter-resolve / timeout / koneksi gagal)
  check "g.storage_not_reachable_from_data" "$([[ ! "$out" =~ ^[0-9]{3}$ || "$out" == 000 ]] && echo 1 || echo 0)" "$(head -c 160 <<< "$out")"
  out="$(MSYS_NO_PATHCONV=1 docker run --rm --network efn_data curlimages/curl:8.11.1@sha256:c1fe1679c34d9784c1b0d1e5f62ac0a79fca01fb6377cdd33e90473c6f9f9a69 -sS -m 5 -o /dev/null -w '%{http_code}' http://s3-gateway:9000/minio/health/live 2>&1 || true)"
  check "g.s3gw_http_rejected" "$([[ "$out" != 200 ]] && echo 1 || echo 0)" "$(head -c 160 <<< "$out")"
  cp "${ROOT}/secrets/efn_ca_crt.txt" "${WORK}/efn_ca.crt"
  s3gw() { # metode path [header...] -> "kode|potongan body"
    local m="$1" p="$2"; shift 2
    local h=(); for x in "$@"; do h+=(-H "$x"); done
    MSYS_NO_PATHCONV=1 docker run --rm --network efn_data -v "${WORK_W}:/work" curlimages/curl:8.11.1@sha256:c1fe1679c34d9784c1b0d1e5f62ac0a79fca01fb6377cdd33e90473c6f9f9a69 -sS --path-as-is -m 10 \
      --cacert /work/efn_ca.crt -X "$m" "${h[@]}" -w '|%{http_code}' "https://s3-gateway:9000${p}" 2>&1 | tr -d '\r\n' || true
  }
  out="$(s3gw GET /minio/health/live)"
  check "g.s3gw_tls_health_ok" "$([[ "$out" == *"|200" ]] && echo 1 || echo 0)" "$(tail -c 120 <<< "$out")"
  local denied='forbidden by administrative rules'
  out="$(s3gw PUT '/pg-backup/efn/x' 'x-amz-bypass-governance-retention: true')"
  check "g.s3gw_bypass_governance_blocked" "$([[ "$out" == *"$denied"* && "$out" == *"|403" ]] && echo 1 || echo 0)" "$(tail -c 120 <<< "$out")"
  out="$(s3gw PUT '/pg-backup/efn/x?retention')"
  check "g.s3gw_put_retention_blocked" "$([[ "$out" == *"$denied"* ]] && echo 1 || echo 0)" "$(tail -c 120 <<< "$out")"
  out="$(s3gw PUT '/pg-backup/efn/x' 'x-amz-object-lock-mode: COMPLIANCE')"
  check "g.s3gw_object_lock_header_blocked" "$([[ "$out" == *"$denied"* ]] && echo 1 || echo 0)" "$(tail -c 120 <<< "$out")"
  out="$(s3gw PUT '/market-scout/reports/x' 'x-amz-content-sha256: STREAMING-UNSIGNED-PAYLOAD-TRAILER')"
  check "g.s3gw_unsigned_trailer_blocked" "$([[ "$out" == *"$denied"* ]] && echo 1 || echo 0)" "$(tail -c 120 <<< "$out")"
  out="$(s3gw PUT '/market-scout/reports/x' 'X-Minio-Replication-Server-Side-Encryption-Iv: AAAA')"
  check "g.s3gw_replication_header_blocked" "$([[ "$out" == *"$denied"* ]] && echo 1 || echo 0)" "$(tail -c 120 <<< "$out")"
  out="$(s3gw POST '/market-scout/reports/x?select&select-type=2')"
  check "g.s3gw_select_blocked" "$([[ "$out" == *"$denied"* ]] && echo 1 || echo 0)" "$(tail -c 120 <<< "$out")"
  out="$(s3gw GET '/minio/admin/v3/info')"
  check "g.s3gw_admin_api_blocked" "$([[ "$out" == *"$denied"* ]] && echo 1 || echo 0)" "$(tail -c 120 <<< "$out")"
  # SEC-41 (gelombang 11): varian encoded / ';' / huruf besar / path non-kanonik harus ditolak FILTER (403 HAProxy),
  # bukan diteruskan ke storage (yang men-decode nama kunci query dan memisah pada ';').
  local v bad=""
  for v in 'PUT /pg-backup/efn/x?rete%6Etion' 'PUT /pg-backup/efn/x?%72etention=' 'PUT /pg-backup/efn/x?x=1;retention' \
           'PUT /pg-backup/efn/x?RETENTION' 'PUT /pg-backup/efn/x?Retention=' 'PUT /pg-backup/efn/x?legal-ho%6Cd' \
           'PUT /pg-backup/efn/x?a=1&legal-hold;b' 'PUT /pg-backup/efn/x?%2572etention' 'DELETE /pg-backup?LIFECYCLE' \
           'POST /market-scout/reports/x?%73elect&select-type=2' 'GET /market-scout/reports/x?SELECT' \
           'POST /market-scout/reports/x?x;select' 'PUT /market-scout/reports/x?x-amz-cr%65dential=a' \
           'PUT /market-scout/reports/x?X-AMZ-CREDENTIAL=a' 'PUT /market-scout/reports/x?X-Amz-Signature=0' \
           'POST /?%61ction=AssumeRole' 'GET /?ACTION=AssumeRole' 'POST //' 'GET /%6Dinio/admin/v3/info' \
           'GET /MINIO/admin/v3/info' 'GET /minio/health/../admin/v3/info' 'GET //minio/admin/v3/info'; do
    out="$(s3gw "${v%% *}" "${v#* }")"
    [[ "$out" == *"$denied"* && "$out" == *"|403" ]] || bad+="${v} => $(tail -c 40 <<< "$out"); "
  done
  out="$(s3gw PUT '/market-scout/reports/x' 'x-amz-content-sha256: UNSIGNED-PAYLOAD' 'X-Amz-Content-Sha256: STREAMING-UNSIGNED-PAYLOAD-TRAILER')"
  [[ "$out" == *"$denied"* ]] || bad+="dup-sha256 => $(tail -c 40 <<< "$out"); "
  out="$(s3gw PUT '/pg-backup/efn/x' 'X-AMZ-Bypass-Governance-Retention: true')"
  [[ "$out" == *"$denied"* ]] || bad+="bypass-hdr-case => $(tail -c 40 <<< "$out"); "
  check "g.s3gw_encoded_variants_blocked" "$([[ -z "$bad" ]] && echo 1 || echo 0)" "${bad:-22 varian query/path + 2 varian header -> 403 filter}"
  # Kontrol positif: query sah dengan nilai ber-%-encoding (ListObjectsV2 prefix) tetap diteruskan ke storage
  out="$(s3gw GET '/market-scout?list-type=2&prefix=reports%2F&delimiter=%2F')"
  check "g.s3gw_encoded_value_get_forwarded" "$([[ "$out" == *AccessDenied* && "$out" != *"$denied"* ]] && echo 1 || echo 0)" "$(tail -c 120 <<< "$out")"
  # Kontrol positif: request S3 biasa tanpa kredensial diteruskan ke storage (ditolak storage, bukan filter)
  out="$(s3gw GET '/market-scout/reports/x')"
  check "g.s3gw_forwards_normal_s3" "$([[ "$out" == *AccessDenied* && "$out" != *"$denied"* ]] && echo 1 || echo 0)" "$(tail -c 120 <<< "$out")"
  out="$(MSYS_NO_PATHCONV=1 docker run --rm --network efn_data curlimages/curl:8.11.1@sha256:c1fe1679c34d9784c1b0d1e5f62ac0a79fca01fb6377cdd33e90473c6f9f9a69 -sS -m 5 telnet://rabbitmq:5672 2>&1 || true)"
  check "g.amqp_plain_port_closed" "$([[ "$out" == *"Could not resolve"* || "$out" == *"refused"* || "$out" == *"Failed to connect"* ]] && echo 1 || echo 0)" "$(head -c 160 <<< "$out")"
  out="$(MSYS_NO_PATHCONV=1 docker exec "$PG" psql -U postgres -At -c "select usename||':'||ssl||':'||coalesce(version,'') from pg_stat_ssl join pg_stat_activity using(pid) where usename in ('efn_bff','efn_odoo','efn_ocr','efn_etl') group by 1 order by 1" | tr '\n' ' ')"
  check "g.pg_clients_tls" "$([[ "$out" != *":f:"* && "$out" == *efn_ocr* && "$out" == *efn_bff* ]] && echo 1 || echo 0)" "$out"
  out="$(MSYS_NO_PATHCONV=1 docker exec "${P}-rabbitmq-1" rabbitmqctl list_connections user ssl ssl_protocol -q --no-table-headers 2>/dev/null | sort -u | tr '\t\n' ': ')"
  check "g.amqp_clients_tls" "$([[ "$out" != *":false"* && "$out" == *efn-ocr* ]] && echo 1 || echo 0)" "$out"
  out="$(MSYS_NO_PATHCONV=1 docker run --rm --network efn_edge --add-host "api.efn.local:${TRAEFIK_IP}" -v "${WORK_W}:/work" curlimages/curl:8.11.1@sha256:c1fe1679c34d9784c1b0d1e5f62ac0a79fca01fb6377cdd33e90473c6f9f9a69 -sS -o /dev/null -w '%{http_code}' --cacert /work/edge_ca.crt -X POST -H 'Content-Type: application/json' -H 'X-EFN-Signature: t=1,v1=00' -d '{}' https://api.efn.local/v1/webhooks/payments/mock 2>&1 || true)"
  check "g.webhook_bad_signature_rejected" "$([[ "$out" == 401 || "$out" == 400 || "$out" == 403 ]] && echo 1 || echo 0)" "http=$out"
}

scenario_l() { # Gelombang 13: Poin EFN di POS (docs/04 §9) — Odoo efn_pos_loyalty_ledger -> BFF nyata
  echo "== l. Poin EFN POS: earn, saldo, redeem OTP, release, cancel, lintas-purpose, refund, outbox saat BFF mati"
  local r u1 u2 u3 u4 u5 u6 u7 ur m pl pm pn n hs
  pl="0812$(rand8)"; pm="0812$(rand8)"; pn="0812$(rand8)"
  # State Odoo (config/sesi/produk smoke) dari skenario c; container Odoo baru -> setup ulang (tanpa partner A-E)
  MSYS_NO_PATHCONV=1 docker exec "$ODOO" test -s /tmp/efn_smoke_state.json || odoo -e SMOKE_STEP=setup | tee -a "$RESULTS"
  for k in L M N; do
    case $k in L) n="$pl";; M) n="$pm";; N) n="$pn";; esac
    odoo -e SMOKE_STEP=loy_partner -e SMOKE_KEY=$k -e SMOKE_PHONE="$n" > /dev/null
  done
  r="$(odoo -e SMOKE_STEP=loy_config)"
  # G18 E-30: redeem = metode bayar Poin EFN (non-tunai, tanpa terminal) tertaut ke POS; produk poin lama diarsipkan
  check "l.config_points_method" "$([[ "$(field ok "$r")" == true && "$(field points_pm "$r")" -gt 0 && "$(field points_pm_cash "$r")" == false && "$(field legacy_product_active "$r")" == false ]] && echo 1 || echo 0)" "$r"

  # 0. G13c SEC-58: user internal biasa & kasir (login nyata password + TOTP lewat Traefik wg-https) memanggil
  #    efn.bff.client._efn_post/_efn_get dan nama lama post/get lewat /web/dataset/call_kw -> semua DITOLAK; kontrol
  #    positif (res.partner.search_count) membuktikan sesi valid. Kredensial: file 0600 di container Odoo -> stdin.
  local rpc who
  r="$(odoo -e SMOKE_STEP=rpc_users)"
  rpc="$(MSYS_NO_PATHCONV=1 docker exec "$ODOO" sh -c 'cat /tmp/efn_smoke_rpc.json; rm -f /tmp/efn_smoke_rpc.json' \
    | MSYS_NO_PATHCONV=1 docker run --rm -i --network efn_edge --add-host "erp.efn.local:${TRAEFIK_IP}" \
        -v "${WORK_W}:/work:ro" "$PY_IMAGE" python /work/odoo_rpc_smoke.py 2>&1 | grep '^SMOKE_RESULT' || true)"
  for who in internal cashier; do
    r="$(grep -F "\"user\": \"smoke_rpc_${who}\"" <<< "$rpc" | head -1)"
    check "l.sec58_rpc_${who}_bff_client_rejected" "$([[ "$r" == *'"logged_in": true'* && "$r" == *'"all_rejected": true'* ]] && echo 1 || echo 0)" "${r:-tidak ada hasil}"
  done

  ledger() { psql_platform -c "select coalesce(sum(delta_points),0) from platform.loyalty_ledger where source='$1' and source_ref='$2'${3:+ and $3}"; }
  resv() { psql_platform -c "select status||'/'||points||'/'||value_idr from platform.loyalty_reservation where order_ref='$1' order by created_at desc limit 1"; }
  bal() { field points_balance "$(odoo -e SMOKE_STEP=loy_balance -e SMOKE_PARTNER=L)"; }  # saldo partner uji L
  redeem_otp() { # uuid partner amount -> 1 bila OTP pos_redeem terverifikasi
    local rr mm cc
    rr="$(odoo -e SMOKE_STEP=loy_otp_request -e SMOKE_ORDER_UUID="$1" -e SMOKE_PARTNER="$2" -e SMOKE_AMOUNT="$3")"
    mm="$(field member_masked "$rr")"; cc="$(otp_code "$mm")"
    rr="$(odoo -e SMOKE_STEP=loy_otp_verify -e SMOKE_ORDER_UUID="$1" -e SMOKE_CODE="$cc")"
    [[ "$(field ok "$rr")" == true ]] && echo 1 || echo 0
  }

  # 1. Earn: order ber-member 20 x Rp50.000 = Rp1.000.000 -> 100 poin (1 poin / Rp10.000)
  u1="$(newuuid)"
  r="$(odoo -e SMOKE_STEP=loy_sync -e SMOKE_ORDER_UUID="$u1" -e SMOKE_PARTNER=L -e SMOKE_QTY=20)"
  check "l.earn_order_synced" "$([[ "$(field state "$r")" == paid && "$r" == *'"earn"'* ]] && echo 1 || echo 0)" "$r"
  r="$(odoo -e SMOKE_STEP=loy_outbox -e SMOKE_ORDER_UUID="$u1")"
  check "l.earn_outbox_done" "$([[ "$(field earn_state "$r")" == "done" && "$(field points_earned "$r")" == 100 && "$(field loyalty_state "$r")" == "done" ]] && echo 1 || echo 0)" "$r"
  r="$(psql_platform -c "select points_earned||'/'||eligible_amount::bigint from platform.loyalty_pos_earn where order_ref='$u1'")/$(ledger pos_earn "$u1")"
  check "l.earn_bff_ledger" "$([[ "$r" == 100/1000000/100 ]] && echo 1 || echo 0)" "pos_earn points/eligible/ledger=$r"

  # 2. Cek saldo dari POS (RPC efn_loyalty_balance -> POST /v1/pos/loyalty/balance)
  r="$(odoo -e SMOKE_STEP=loy_balance -e SMOKE_PARTNER=L)"
  check "l.pos_balance" "$([[ "$(field ok "$r")" == true && "$(field points_balance "$r")" == 100 && "$(field point_value_idr "$r")" == 100 && "$(field member_masked "$r")" == *'****'* ]] && echo 1 || echo 0)" "$r"

  # 3. Redeem: OTP pos_redeem -> reserve 60 poin (Rp6.000) -> baris Poin EFN -> sync -> commit
  u2="$(newuuid)"
  check "l.redeem_otp_verified" "$(redeem_otp "$u2" L 200000)" "OTP pos_redeem order $u2"
  r="$(odoo -e SMOKE_STEP=loy_reserve -e SMOKE_ORDER_UUID="$u2" -e SMOKE_PARTNER=L -e SMOKE_POINTS=60 -e SMOKE_AMOUNT=200000)"
  check "l.redeem_reserve" "$([[ "$(field ok "$r")" == true && "$(field value_idr "$r")" == 6000 && "$(resv "$u2")" == reserved/60/6000 ]] && echo 1 || echo 0)" "$r bff=$(resv "$u2")"
  r="$(odoo -e SMOKE_STEP=loy_sync -e SMOKE_ORDER_UUID="$u2" -e SMOKE_PARTNER=L -e SMOKE_QTY=4)"
  # E-30: total order tetap 200.000 (DPP penuh); Poin EFN = baris pembayaran Rp6.000, sisa tunai
  check "l.redeem_order_synced" "$([[ "$(field state "$r")" == paid && "$(field points_payments "$r")" == 1 && "$(field points_paid "$r")" == 6000* && "$(field total "$r")" == 200000* && "$r" == *redeem_commit* ]] && echo 1 || echo 0)" "$r"
  r="$(odoo -e SMOKE_STEP=loy_outbox -e SMOKE_ORDER_UUID="$u2")"
  check "l.redeem_commit_done" "$([[ "$(field redeem_commit_state "$r")" == "done" && "$(field redeem_commit_delta "$r")" == -60 && "$(field reservation_state "$r")" == committed && "$(field earn_state "$r")" == "done" && "$(field points_earned "$r")" == 19 ]] && echo 1 || echo 0)" "$r (earn 19 = Rp194.000 tanpa porsi poin)"
  r="$(resv "$u2")/$(ledger pos_redeem "$u2")/$(ledger pos_earn "$u2")"
  check "l.redeem_bff_ledger" "$([[ "$r" == committed/60/6000/-60/19 ]] && echo 1 || echo 0)" "reservasi/ledger redeem/earn=$r"
  r="$(bal)"
  check "l.balance_after_redeem" "$([[ "$r" == 59 ]] && echo 1 || echo 0)" "saldo=$r (100 - 60 + 19)"

  # 4. Batal di dialog kasir -> RPC release (K-5)
  u5="$(newuuid)"
  check "l.release_otp_verified" "$(redeem_otp "$u5" L 100000)" "OTP pos_redeem order $u5"
  r="$(odoo -e SMOKE_STEP=loy_reserve -e SMOKE_ORDER_UUID="$u5" -e SMOKE_PARTNER=L -e SMOKE_POINTS=50 -e SMOKE_AMOUNT=100000)"
  r="$r | $(odoo -e SMOKE_STEP=loy_release -e SMOKE_ORDER_UUID="$u5")"
  check "l.cancel_release" "$([[ "$r" == *'"reservation_state": "released"'* && "$(resv "$u5")" == released/50/5000 ]] && echo 1 || echo 0)" "$r bff=$(resv "$u5")"
  # 5. Order draft berpoin dibatalkan (action_pos_order_cancel) -> outbox redeem_release
  u6="$(newuuid)"
  check "l.cancel_draft_otp_verified" "$(redeem_otp "$u6" L 100000)" "OTP pos_redeem order $u6"
  r="$(odoo -e SMOKE_STEP=loy_reserve -e SMOKE_ORDER_UUID="$u6" -e SMOKE_PARTNER=L -e SMOKE_POINTS=50 -e SMOKE_AMOUNT=100000)"
  r="$(odoo -e SMOKE_STEP=loy_sync -e SMOKE_ORDER_UUID="$u6" -e SMOKE_PARTNER=L -e SMOKE_QTY=2 -e SMOKE_DRAFT=1)"
  check "l.draft_keeps_points_payment" "$([[ "$(field state "$r")" == draft && "$(field points_payments "$r")" == 1 ]] && echo 1 || echo 0)" "$r"
  r="$(odoo -e SMOKE_STEP=loy_cancel -e SMOKE_ORDER_UUID="$u6")"
  check "l.cancel_draft_release" "$([[ "$(field order_state "$r")" == cancel && "$(field reservation_state "$r")" == released && "$(field release_state "$r")" == "done" && "$(resv "$u6")" == released/50/5000 ]] && echo 1 || echo 0)" "$r bff=$(resv "$u6")"
  r="$(bal)"
  check "l.balance_unchanged_after_release" "$([[ "$r" == 59 ]] && echo 1 || echo 0)" "saldo=$r"

  # 6. Lintas purpose: OTP promo tidak bisa untuk redeem, OTP redeem tidak bisa untuk promo (Odoo DAN BFF)
  u3="$(newuuid)"; pos_otp "$u3" M 50000
  r="$(odoo -e SMOKE_STEP=loy_reserve -e SMOKE_ORDER_UUID="$u3" -e SMOKE_PARTNER=M -e SMOKE_POINTS=50 -e SMOKE_AMOUNT=50000)"
  check "l.promo_otp_reserve_rejected_odoo" "$([[ "$(field ok "$r")" == false && "$(field error_code "$r")" == otp_required ]] && echo 1 || echo 0)" "$r"
  r="$(odoo -e SMOKE_STEP=loy_token_probe -e SMOKE_ORDER_UUID="$u3" -e SMOKE_PURPOSE=pos_promo -e SMOKE_PARTNER=M -e SMOKE_AMOUNT=50000)"
  check "l.promo_token_reserve_rejected_bff" "$([[ "$(field http "$r")" == 409 && "$(field code "$r")" == token_invalid ]] && echo 1 || echo 0)" "$r"
  u4="$(newuuid)"
  check "l.redeem_otp_for_promo_verified" "$(redeem_otp "$u4" N 45000)" "OTP pos_redeem order $u4"
  r="$(odoo -e SMOKE_STEP=sync -e SMOKE_ORDER_UUID="$u4" -e SMOKE_PARTNER=N)"
  check "l.redeem_otp_promo_rejected_odoo" "$([[ "$(field ok "$r")" == false && "$(field error "$r")" == UserError ]] && echo 1 || echo 0)" "$r"
  r="$(odoo -e SMOKE_STEP=loy_token_probe -e SMOKE_ORDER_UUID="$u4" -e SMOKE_PURPOSE=pos_redeem -e SMOKE_PARTNER=N -e SMOKE_AMOUNT=45000)"
  check "l.redeem_token_consume_rejected_bff" "$([[ "$(field http "$r")" == 409 ]] && echo 1 || echo 0)" "$r"

  # 7. Refund 2 dari 4 unit order u2 -> rasio 0,5: restore floor(60x0,5)=30, clawback ceil(19x0,5)=10
  ur="$(newuuid)"
  r="$(odoo -e SMOKE_STEP=loy_refund -e SMOKE_ORDER_UUID="$ur" -e SMOKE_ORIG_UUID="$u2" -e SMOKE_QTY=2)"
  check "l.refund_synced" "$([[ "$(field ok "$r")" == true && "$(field is_refund "$r")" == true && "$(field preview_value "$r")" == 3000 && "$(field reverse_ratio "$r")" == 0.5 && "$(field reverse_eligible "$r")" == 97000 && "$(field points_paid "$r")" == -3000* && "$(field points_restored_value "$r")" == 3000 ]] && echo 1 || echo 0)" "$r"
  r="$(odoo -e SMOKE_STEP=loy_outbox -e SMOKE_ORDER_UUID="$ur")"
  check "l.refund_reverse_done" "$([[ "$(field reverse_state "$r")" == "done" && "$(field reverse_delta "$r")" == 20 && "$(field reverse_points_restored_value "$r")" == 3000 ]] && echo 1 || echo 0)" "$r"
  r="$(psql_platform -c "select points_restored_value || '/' || points_restored from platform.loyalty_pos_reverse where refund_order_ref='$ur'")"
  check "l.bff_points_restored_value" "$([[ "$r" == 3000/30 ]] && echo 1 || echo 0)" "loyalty_pos_reverse value/poin=$r (K-18-5)"
  r="$(ledger pos_reverse "$u2" "delta_points > 0")/$(ledger pos_reverse "$u2" "delta_points < 0")/$(bal)"
  check "l.refund_bff_proportional" "$([[ "$r" == 30/-10/79 ]] && echo 1 || echo 0)" "restore/clawback/saldo=$r"

  # 8. BFF mati saat sync: order tetap paid, earn tertunda di outbox, terkirim setelah BFF hidup
  u7="$(newuuid)"
  docker stop "$BFF" > /dev/null
  r="$(odoo -e SMOKE_STEP=loy_sync -e SMOKE_ORDER_UUID="$u7" -e SMOKE_PARTNER=L -e SMOKE_QTY=2)"
  check "l.bff_down_sync_ok" "$([[ "$(field state "$r")" == paid && "$r" == *'"earn"'* ]] && echo 1 || echo 0)" "$r"
  r="$(odoo -e SMOKE_STEP=loy_outbox -e SMOKE_ORDER_UUID="$u7")"
  check "l.bff_down_earn_pending" "$([[ "$(field earn_state "$r")" == pending && -n "$(field earn_error "$r")" && "$(field loyalty_state "$r")" == pending ]] && echo 1 || echo 0)" "$r"
  docker start "$BFF" > /dev/null
  for _ in $(seq 1 36); do [[ "$(docker inspect "$BFF" --format '{{.State.Health.Status}}')" == healthy ]] && break; sleep 5; done
  docker logs -f --since 1s "$BFF" >> "${WORK}/bff.log" 2>&1 &
  LOGPID="$LOGPID $!"
  for _ in $(seq 1 40); do
    r="$(odoo -e SMOKE_STEP=loy_outbox -e SMOKE_ORDER_UUID="$u7")"
    [[ "$(field earn_state "$r")" == "done" ]] && break
    sleep 15
  done
  check "l.bff_up_earn_delivered" "$([[ "$(field earn_state "$r")" == "done" && "$(field points_earned "$r")" == 10 && "$(ledger pos_earn "$u7")" == 10 ]] && echo 1 || echo 0)" "$r ledger=$(ledger pos_earn "$u7")"
  r="$(bal)"
  check "l.balance_final" "$([[ "$r" == 89 ]] && echo 1 || echo 0)" "saldo=$r (79 + 10)"

  # 8b. G13c SEC-59: retur tanpa struk (qty < 0 tanpa order asal) untuk member L -> review, tanpa earn/reverse
  local u9 u8 po
  u9="$(newuuid)"
  r="$(odoo -e SMOKE_STEP=loy_refund_noreceipt -e SMOKE_ORDER_UUID="$u9" -e SMOKE_PARTNER=L -e SMOKE_QTY=1)"
  check "l.refund_noreceipt_review" "$([[ "$(field ok "$r")" == true && "$(field state "$r")" =~ ^(paid|done)$ && "$(field loyalty_state "$r")" == review && "$(field outbox "$r")" == none ]] && echo 1 || echo 0)" "$r"
  r="$(psql_platform -c "select (select count(*) from platform.loyalty_ledger where source_ref='$u9') || '/' || (select count(*) from platform.loyalty_pos_earn where order_ref='$u9')")/$(bal)"
  check "l.refund_noreceipt_no_bff_effect" "$([[ "$r" == 0/0/89 ]] && echo 1 || echo 0)" "ledger/earn/saldo=$r"

  # 8c. G13c SEC-61: earn melebihi batas harian member (2000 poin; order 401 x Rp50.000 = 2005 poin, < plafon order
  #     5000) -> 409 earn_limit_exceeded final, order review, tanpa catatan earn; baris EFN_LOYALTY_ANOMALY sampai Loki
  #     (stream efn_loyalty, label kind) dan alert EfnLoyaltyAnomalyWarning sampai Alertmanager.
  po="0812$(rand8)"
  odoo -e SMOKE_STEP=loy_partner -e SMOKE_KEY=O -e SMOKE_PHONE="$po" > /dev/null
  u8="$(newuuid)"
  r="$(odoo -e SMOKE_STEP=loy_sync -e SMOKE_ORDER_UUID="$u8" -e SMOKE_PARTNER=O -e SMOKE_QTY=401)"
  r="$(odoo -e SMOKE_STEP=loy_outbox -e SMOKE_ORDER_UUID="$u8")"
  check "l.earn_daily_limit_rejected" "$([[ "$(field earn_state "$r")" == failed && "$(field earn_error "$r")" == *earn_limit_exceeded* && "$(field loyalty_state "$r")" == review ]] && echo 1 || echo 0)" "$r"
  r="$(psql_platform -c "select (select count(*) from platform.loyalty_pos_earn where order_ref='$u8') || '/' || (select count(*) from platform.loyalty_ledger where source_ref='$u8')")"
  check "l.earn_daily_limit_no_bff_earn" "$([[ "$r" == 0/0 ]] && echo 1 || echo 0)" "earn/ledger=$r"
  r="$(grep -F 'EFN_LOYALTY_ANOMALY {"kind":"earn_limit_member_day","severity":"warning"' "${WORK}/bff.log" | grep -F "$u8" | tail -1 || true)"
  check "l.anomaly_log_line" "$([[ "$r" == *'WARN [PosLoyaltyService] EFN_LOYALTY_ANOMALY'* && "$r" != *$'\e'* && "$r" != *"${po:4}"* ]] && echo 1 || echo 0)" "$(tail -c 220 <<< "$r")"
  for _ in $(seq 1 12); do
    r="$(obs loki "{stream=\"efn_loyalty\", loyalty_event=\"anomaly\", kind=\"earn_limit_member_day\", severity=\"warning\"} |= \"${u8:0:8}\"" 30)"
    [[ "$(field lines "$r")" -ge 1 ]] && break
    sleep 10
  done
  check "l.anomaly_in_loki" "$([[ "$(field lines "$r")" -ge 1 ]] && echo 1 || echo 0)" "$(head -c 260 <<< "$r")"
  for _ in $(seq 1 24); do
    r="$(obs alert EfnLoyaltyAnomalyWarning kind=earn_limit_member_day)"
    [[ "$r" == *'"state": "active"'* ]] && break
    sleep 10
  done
  check "l.anomaly_alert_in_alertmanager" "$([[ "$r" == *'"state": "active"'* && "$r" == *'"severity": "warning"'* ]] && echo 1 || echo 0)" "$(head -c 260 <<< "$r")"
  r="$(obs loki '{stream="efn_loyalty", loyalty_event="recon", recon_status="ok"}' 30)"
  check "l.recon_heartbeat_in_loki" "$([[ "$(field lines "$r")" -ge 1 ]] && echo 1 || echo 0)" "$(head -c 200 <<< "$r") (dev: LOYALTY_RECON_INTERVAL_MS=300000)"

  # 9. PDP: tidak ada HP mentah di tabel loyalty BFF/Odoo maupun log BFF/Odoo
  hs=0
  for n in "$pl" "$pm" "$pn" "$po"; do
    m="${n:4}"
    hs=$(( hs + $(MSYS_NO_PATHCONV=1 docker exec "$PG" sh -c "pg_dump -U postgres -d efn_platform --data-only -t 'platform.loyalty_*'; pg_dump -U postgres -d efn_odoo --data-only -t 'efn_pos_loyalty_*' -t efn_pos_otp_challenge" | grep -c "$m" || true) ))
    hs=$(( hs + $(grep -c "$m" "${WORK}/bff.log" || true) + $(docker logs --since 30m "$ODOO" 2>&1 | grep -c "$m" || true) ))
  done
  check "l.no_raw_phone_tables_logs" "$([[ "$hs" == 0 ]] && echo 1 || echo 0)" "hits=$hs (pg_dump platform.loyalty_* + efn_pos_loyalty_* + efn_pos_otp_challenge, log BFF & Odoo)"

  # 10. ETL -> ClickHouse: baris poin (line_kind=points) order u2 dan refund ur masuk fact_sales_line
  local o2 orr
  o2="$(MSYS_NO_PATHCONV=1 docker exec "$PG" psql -U postgres -d efn_odoo -At -c "select id from pos_order where uuid='$u2'")"
  orr="$(MSYS_NO_PATHCONV=1 docker exec "$PG" psql -U postgres -d efn_odoo -At -c "select id from pos_order where uuid='$ur'")"
  # G18 E-30: tidak ada baris produk poin; penjualan u2 di fact_sales_line = harga penuh (200.000, tanpa line_kind
  # points), Poin EFN = fact_pos_payment is_loyalty_points (+6.000 order u2, -3.000 refund ur), refund masuk fact_sales_line
  for _ in $(seq 1 30); do
    r="$(echo "select countIf(order_id=$o2 and line_kind='points') || '/' || toString(toInt64(sumIf(subtotal, order_id=$o2))) || '/' || countIf(order_id=$orr and is_refund=1) from efn_dwh.fact_sales_line final where order_id in ($o2,$orr)" | chq)"
    r="$r/$(echo "select toString(toInt64(sumIf(amount, order_id=$o2 and is_loyalty_points=1))) || '/' || toString(toInt64(sumIf(amount, order_id=$orr and is_loyalty_points=1))) from efn_dwh.fact_pos_payment final where order_id in ($o2,$orr)" | chq)"
    [[ "$r" == 0/200000/*/6000/-3000 ]] && break
    sleep 10
  done
  check "l.etl_points_payment_and_refund_lines" "$([[ "$r" =~ ^0/200000/[1-9][0-9]*/6000/-3000$ ]] && echo 1 || echo 0)" "points_lines/subtotal_u2/refund_lines/poin_bayar_u2/poin_refund=$r (produk tanpa pajak: subtotal = incl)"
  echo "$u2 $ur $o2 $orr" > "${WORK}/loyalty_orders"
  echo "$u2,$ur,$u5,$u6" > "${WORK}/points_refs"   # order berpoin run ini (rekonsiliasi harian di tahap p)
}

scenario_o() { # Gelombang 17: penjualan online Fase 3 (docs/04 §10) — Super-App/BFF -> odoo-rpc-gateway -> E-28 nyata
  echo "== o. Online: katalog -> cart -> quote -> checkout -> bayar mock -> confirmed -> ready -> ambil; negatif & PDP"
  local r part main_oid code pa pb pc pw ok n
  oodoo() { # -e K=V ... -> satu baris SMOKE_RESULT (langkah odoo_online_smoke.py)
    local o
    o="$(MSYS_NO_PATHCONV=1 docker exec -i "$@" "$ODOO" odoo shell -d efn_odoo --no-http --log-level=warn \
      < "${SMOKE_DIR}/odoo_online_smoke.py" 2>&1 | grep -E '^SMOKE_RESULT|Error|errors\.' | tail -1 || true)"
    if [[ -n "$o" ]]; then echo "$o"; else echo 'SMOKE_RESULT {"ok": false, "error": "odoo_shell_no_result"}'; fi
  }
  opy() { # <hp> <member> <subperintah> [arg...]  (klien publik via Traefik, state per member)
    local phone="$1" who="$2"; shift 2
    MSYS_NO_PATHCONV=1 docker run --rm --network efn_edge \
      --add-host "api.efn.local:${TRAEFIK_IP}" --add-host "app.efn.local:${TRAEFIK_IP}" \
      -v "${WORK_W}:/work" -e SMOKE_PHONE="$phone" -e SMOKE_STATE="/work/online_${who}.json" -e SMOKE_BFF_LOG=/work/bff.log \
      "$PY_IMAGE" python /work/online_smoke.py "$@" 2>&1 | grep '^SMOKE_RESULT' | tee -a "$RESULTS" || true
  }
  python_free_json() { grep -oE "\"$2\": \\{[^}]*\"$3\": \"[^\"]+\"" "${WORK}/online_$1.json" | sed -nE "s/.*\"$3\": \"([^\"]+)\"/\\1/p" | head -1; }
  part_of() { psql_platform -c "select id from platform.online_order_part where order_id='$1' limit 1"; }

  # 0. Konfigurasi kanal dev (idempoten) + API key nyata user teknis (runbook odoo-online-api-key §1)
  r="$(oodoo -e SMOKE_STEP=setup)"
  check "o.odoo_channel_setup" "$([[ "$(field ok "$r")" == true && "$(field tech_user_groups_ok "$r")" == true && "$(field staff_is_admin "$r")" == false ]] && echo 1 || echo 0)" "$r"
  n="$(tr -d '\r\n' < "${ROOT}/secrets/odoo_api_key_online_rtl.txt" | grep -cE '^[0-9a-f]{40}$' || true)"
  check "o.api_key_real_not_placeholder" "$([[ "$n" == 1 ]] && echo 1 || echo 0)" "secrets/odoo_api_key_online_rtl.txt 40 hex (nilai tidak dicetak)"

  # 0b. Gateway + key nyata: 10 path sah -> jawaban APLIKASI Odoo (200 / galat bisnis), bukan 401/403 atau deny filter.
  #     Key dibaca DI DALAM container curl (header file), tidak lewat argv/log.
  cp "${ROOT}/secrets/efn_ca_crt.txt" "${WORK}/efn_ca.crt"
  r="$(MSYS_NO_PATHCONV=1 docker run --rm --network efn_internal -v "${WORK_W}:/work:ro" \
      -v "${SECRETS_W}/odoo_api_key_online_rtl.txt:/s/k:ro" -v "${SECRETS_W}/tls_api_gateway_crt.txt:/s/c:ro" \
      -v "${SECRETS_W}/tls_api_gateway_key.txt:/s/ck:ro" --entrypoint /bin/sh \
      curlimages/curl:8.11.1@sha256:c1fe1679c34d9784c1b0d1e5f62ac0a79fca01fb6377cdd33e90473c6f9f9a69 -c '
    printf "Authorization: bearer %s\n" "$(tr -d "\r\n" < /s/k)" > /tmp/h
    U=00000000-0000-4000-8000-000000000001
    for pb in "efn.online.channel/efn_online_catalog|{\"channel_code\":\"RTL\",\"limit\":1}" \
      "efn.online.channel/efn_online_stock|{\"channel_code\":\"RTL\",\"outlet_code\":\"TOKO-01\",\"product_ids\":[1]}" \
      "efn.online.channel/efn_online_image|{\"channel_code\":\"RTL\",\"tmpl_id\":1,\"size\":512}" \
      "efn.online.channel/efn_online_quote|{\"channel_code\":\"RTL\",\"outlet_code\":\"TOKO-01\",\"fulfilment\":\"pickup\",\"lines\":[]}" \
      "efn.online.order/efn_online_place|{\"channel_code\":\"RTL\",\"order\":{}}" \
      "efn.online.order/efn_online_mark_paid|{\"channel_code\":\"RTL\",\"platform_order_ref\":\"$U\",\"payment\":{}}" \
      "efn.online.order/efn_online_cancel|{\"channel_code\":\"RTL\",\"platform_order_ref\":\"$U\",\"reason\":\"ops\",\"idempotency_key\":\"online:cancel:$U\"}" \
      "efn.online.order/efn_online_refund|{\"channel_code\":\"RTL\",\"platform_order_ref\":\"$U\",\"refund\":{}}" \
      "efn.online.order/efn_online_refund_settled|{\"channel_code\":\"RTL\",\"refund_ref\":\"$U\",\"settlement\":{}}" \
      "efn.online.order/efn_online_status|{\"channel_code\":\"RTL\",\"platform_order_refs\":[\"$U\"]}"; do
      p="${pb%%|*}"; d="${pb#*|}"
      c=$(curl -sS -o /tmp/b -D /tmp/hd -m 20 --cacert /work/efn_ca.crt --cert /s/c --key /s/ck -X POST \
        -H "Content-Type: application/json" -H @/tmp/h --data "$d" "https://odoo-rpc-gateway:8443/json/2/$p" -w "%{http_code}")
      deny=$(grep -ci "^x-efn-gateway-deny" /tmp/hd)
      app=$(grep -c "efn_online:" /tmp/b)
      echo "${p#*/}=$c/deny$deny/app$app"
    done; rm -f /tmp/h' 2>&1 | tr '\n' ' ')"
  ok=1; for x in $r; do [[ "$x" =~ =(200|4[0-9][0-9]|5[0-9][0-9])/deny0/app([0-9]+)$ ]] || ok=0
    [[ "$x" =~ =(401|403)/ ]] && ok=0; [[ "$x" =~ =200/ || "$x" =~ /app[1-9] ]] || ok=0; done
  check "o.gateway_10_paths_real_key_app_response" "$([[ "$ok" == 1 && $(wc -w <<< "$r") == 10 ]] && echo 1 || echo 0)" "$r"

  # Katalog BFF tersinkron dari E-28 (job tiap ONLINE_CATALOG_SYNC_SECONDS; setup baru -> tunggu siklus berikut)
  for _ in $(seq 1 40); do
    n="$(psql_platform -c "select count(*) from platform.online_catalog_item where channel_code='RTL' and deleted_at is null and data->>'name' like 'SMOKE %'")"
    [[ "$n" -ge 4 ]] && break; sleep 10
  done
  check "o.catalog_synced_from_odoo" "$([[ "$n" -ge 4 ]] && echo 1 || echo 0)" "item SMOKE di salinan katalog BFF=$n"

  pa="62812$(rand8)"; pb="62812$(rand8)"; pc="62812$(rand8)"; pw="62812$(rand8)"
  echo "$pa $pb $pc $pw" > "${WORK}/online_phones"
  opy "$pa" a login; opy "$pb" b login; opy "$pc" c login
  opy "$pa" a catalog

  # 1. Alur utama (member A) + idempotensi L-8 + accept_total salah
  opy "$pa" a quote rice:2,bag:1 main
  opy "$pa" a checkout main
  main_oid="$(python_free_json a main order_id)"; part="$(part_of "$main_oid")"
  opy "$pa" a pickup_unavailable main
  r="$(oodoo -e SMOKE_STEP=order -e SMOKE_PART="$part")"
  check "o.odoo_so_draft_with_code_hash" "$([[ "$(field state "$r")" == placed && "$(field so_state "$r")" == draft && "$(field pickup_hash_present "$r")" == true && "$(field checkout_ref "$r")" == "$main_oid" ]] && echo 1 || echo 0)" "$r"
  r="$(psql_platform -c "select count(*) || '/' || bool_and(pickup_code_enc is not null and get_byte(pickup_code_enc,0)=1) from platform.online_order_part where order_id='$main_oid'")"
  check "o.bff_pickup_code_encrypted" "$([[ "$r" == 1/true ]] && echo 1 || echo 0)" "bagian/pickup_code_enc AES-GCM v1=$r"
  opy "$pa" a pay main paid
  opy "$pa" a wait main fulfilling confirmed
  r="$(oodoo -e SMOKE_STEP=order -e SMOKE_PART="$part")"
  check "o.odoo_confirmed_invoiced" "$([[ "$(field state "$r")" == confirmed && "$(field so_state "$r")" == sale && "$r" == *'"invoices": ["posted"]'* ]] && echo 1 || echo 0)" "$r"
  opy "$pa" a pickup main
  opy "$pb" b anti_enum /work/online_a.json main
  # L-4: DSAR hapus akun ditolak selama ada bagian confirmed (akun tetap aktif)
  opy "$pa" a dsar_open
  r="$(oodoo -e SMOKE_STEP=ready -e SMOKE_PART="$part")"
  check "o.odoo_staff_ready_event" "$([[ "$(field ok "$r")" == true && "$(field state "$r")" == ready && "$r" == *'ready:done'* ]] && echo 1 || echo 0)" "$r"
  opy "$pa" a wait main fulfilling ready
  code="$(python_free_json a main pickup_code)"
  r="$(oodoo -e SMOKE_STEP=handover -e SMOKE_PART="$part" -e SMOKE_CODE="$code")"
  check "o.odoo_handover_code_ok" "$([[ "$(field "done" "$r")" == true && "$(field state "$r")" == picked_up && "$(field pickup_hash_present "$r")" == false && "$(field pickings_done "$r")" -ge 1 && "$r" == *'picked_up:done'* ]] && echo 1 || echo 0)" "$(nomsg "$r")"
  opy "$pa" a wait main completed handed_over
  r="$(psql_platform -c "select count(*) filter (where pickup_code_enc is null) || '/' || count(*) from platform.online_order_part where order_id='$main_oid'")"
  check "o.bff_pickup_code_purged_after_handover" "$([[ "$r" == 1/1 ]] && echo 1 || echo 0)" "pickup_code_enc NULL/bagian=$r"
  echo "$code" > "${WORK}/online_codes"

  # 1b. Refund parsial CS lewat CLI teraudit (L-3, L-19c): operator `cs` membuat -> `approval_required` (ambang dev 0)
  #     -> pembuat yang sama (walau peran approver) ditolak, peran `cs` lain ditolak, approver lain -> settled.
  #     Identitas/peran = env yang pada host diisi wrapper efn-online-cs (dev: disimulasikan, APP_ENV=dev).
  cs() { # <operator> <peran> <skrip cli> [arg...] -> keluaran + "exit=<rc>"
    local op="$1" role="$2" cli="$3"; shift 3
    MSYS_NO_PATHCONV=1 docker exec -e EFN_CS_OPERATOR="$op" -e EFN_CS_HOST=smoke -e EFN_CS_ROLE="$role" "$BFF" \
      node "dist/cli/${cli}.js" "$@" 2>&1 | tr '\n' ' '; echo "exit=${PIPESTATUS[0]}"
  }
  local bag rice before crid
  bag="$(sed -nE 's/.*"bag": ([0-9]+).*/\1/p' "${WORK}/online_catalog.json")"; rice="$(sed -nE 's/.*"rice": ([0-9]+).*/\1/p' "${WORK}/online_catalog.json")"
  r="$(cs smoke-cs-op cs online-refund --part="$part" --lines="${bag}:1" --reason="smoke refund parsial SMOKE-CS-002")"
  n="$(psql_platform -c "select id || '|' || status || '|' || coalesce(initiated_by,'-') || '|' || coalesce(amount::bigint::text,'-') from platform.online_refund where part_id='$part' and kind='lines' order by created_at desc limit 1")"
  crid="${n%%|*}"
  check "o.cli_cs_refund_needs_approval" "$([[ "$r" == *exit=0* && "$n" == *'|approval_required|smoke-cs-op/smoke|'* ]] && echo 1 || echo 0)" "refund=${n#*|} cli=$(tail -c 160 <<< "$r")"
  r="$(cs smoke-cs-op approver online-refund-approve --refund="$crid" --reason="smoke setujui sendiri")"
  n="$(psql_platform -c "select status from platform.online_refund where id='$crid'")"
  check "o.cli_approve_same_actor_rejected" "$([[ "$r" == *exit=1* && "$n" == approval_required ]] && echo 1 || echo 0)" "$(tail -c 160 <<< "$r") status=$n"
  r="$(cs smoke-cs-other cs online-refund-approve --refund="$crid" --reason="smoke peran cs")"
  n="$(psql_platform -c "select status from platform.online_refund where id='$crid'")"
  check "o.cli_approve_role_cs_rejected" "$([[ "$r" == *exit=1* && "$n" == approval_required ]] && echo 1 || echo 0)" "$(tail -c 160 <<< "$r") status=$n"
  r="$(cs smoke-cs-sup approver online-refund-approve --refund="$crid" --reason="smoke disetujui supervisor SMOKE-CS-002")"
  for _ in $(seq 1 30); do
    n="$(psql_platform -c "select status || '/' || amount::bigint from platform.online_refund where id='$crid'")"
    [[ "$n" == settled/* ]] && break; sleep 3
  done
  before="$(psql_platform -c "select refunded_amount::bigint || '/' || amount_payable::bigint from platform.online_order_part where id='$part'")"
  check "o.cli_partial_refund_settled" "$([[ "$r" == *exit=0* && "$n" == settled/* && "${n#*/}" -gt 0 && "${before%/*}" == "${n#*/}" ]] && echo 1 || echo 0)" "refund=$n refunded/porsi=$before cli=$(tail -c 120 <<< "$r")"
  r="$(cs smoke-cs-op cs online-refund --part="$part" --lines="${rice}:50" --reason="smoke refund melebihi porsi")"
  n="$(psql_platform -c "select refunded_amount::bigint || '/' || amount_payable::bigint from platform.online_order_part where id='$part'")"
  check "o.cli_refund_over_portion_rejected" "$([[ "$r" == *exit=1* && "$n" == "$before" ]] && echo 1 || echo 0)" "$(tail -c 160 <<< "$r") refunded/porsi=$n"
  r="$(psql_platform -c "select string_agg(action || ':' || outcome || ':' || actor, ',' order by id) from platform.online_admin_audit where refund_id='$crid'")"
  check "o.cli_audit_recorded" "$([[ "$r" == *'refund:'*'smoke-cs-op/smoke'* && "$r" == *'refund_approve:'*'smoke-cs-sup/smoke'* && "$r" == *rejected* ]] && echo 1 || echo 0)" "$r"
  # SEC-112: settle manual hanya peran approver yang bukan pembuat refund (dicek sebelum status refund)
  r="$(cs smoke-cs-other cs online-refund-settle --refund="$crid" --provider-ref=SMOKE-TRF-1 --reason="smoke settle peran cs")"
  r="$r | $(cs smoke-cs-op approver online-refund-settle --refund="$crid" --provider-ref=SMOKE-TRF-2 --reason="smoke settle pembuat")"
  n="$(psql_platform -c "select string_agg(outcome || ':' || actor, ',' order by id) from platform.online_admin_audit where refund_id='$crid' and action='refund_settle'")"
  check "o.cli_settle_role_and_creator_rejected" "$([[ "$r" == *exit=1*'|'*exit=1* && "$n" == *'rejected_role:smoke-cs-other/smoke'* && "$n" == *'rejected_same_actor:smoke-cs-op/smoke'* && "$(psql_platform -c "select status from platform.online_refund where id='$crid'")" == settled ]] && echo 1 || echo 0)" "audit=$n cli=$(tail -c 160 <<< "$r")"
  r="$(oodoo -e SMOKE_STEP=order -e SMOKE_PART="$part")"
  check "o.odoo_credit_note_partial" "$([[ "$(field credit_notes "$r")" -ge 1 && "$(field state "$r")" == picked_up ]] && echo 1 || echo 0)" "$r"

  # 2. Batas bayar (member B): kedaluwarsa dipercepat lewat DB dev -> sweeper -> SO batal; bayar terlambat -> refund
  local tmo tpart
  opy "$pb" b quote oil:1 tmo
  opy "$pb" b checkout_only tmo
  tmo="$(python_free_json b tmo order_id)"; tpart="$(part_of "$tmo")"
  psql_platform -c "update platform.online_order set expires_at = now() - interval '1 minute' where id='$tmo' and status='awaiting_payment'" > /dev/null
  opy "$pb" b wait tmo expired cancelled 150
  for _ in $(seq 1 20); do r="$(oodoo -e SMOKE_STEP=order -e SMOKE_PART="$tpart")"; [[ "$(field state "$r")" == cancelled ]] && break; sleep 5; done
  check "o.timeout_odoo_so_cancelled" "$([[ "$(field state "$r")" == cancelled && "$(field cancel_reason "$r")" == payment_expired && "$(field so_state "$r")" == cancel ]] && echo 1 || echo 0)" "$r"
  opy "$pb" b pay tmo paid
  for _ in $(seq 1 30); do
    n="$(psql_platform -c "select kind || '/' || status || '/' || amount::bigint from platform.online_refund where part_id='$tpart' order by created_at desc limit 1")"
    [[ "$n" == late_payment/gateway_done/* || "$n" == late_payment/settled/* ]] && break; sleep 3
  done
  r="$(psql_platform -c "select o.status || '/' || p.amount_payable::bigint from platform.online_order o join platform.online_order_part p on p.order_id=o.id where o.id='$tmo'")"
  check "o.late_payment_refunded" "$([[ ( "$n" == late_payment/gateway_done/* || "$n" == late_payment/settled/* ) && "$r" == expired/"${n##*/}" ]] && echo 1 || echo 0)" "refund=$n order/porsi=$r"

  # 3. Stok kurang setelah bayar (member C): stok dihabiskan antara checkout dan bayar -> stock_short -> batal + refund porsi
  local short spart
  oodoo -e SMOKE_STEP=stock -e SMOKE_PRODUCT=scarce -e SMOKE_QTY=50 > /dev/null
  opy "$pc" c quote scarce:5 short
  opy "$pc" c checkout_nokey short     # tanpa Idempotency-Key -> 400 (quote tidak terpakai)
  opy "$pc" c checkout_only short
  short="$(python_free_json c short order_id)"; spart="$(part_of "$short")"
  oodoo -e SMOKE_STEP=stock -e SMOKE_PRODUCT=scarce -e SMOKE_QTY=0 > /dev/null
  opy "$pc" c pay short paid
  opy "$pc" c wait short cancelled cancelled 150
  r="$(oodoo -e SMOKE_STEP=order -e SMOKE_PART="$spart")"
  n="$(psql_platform -c "select p.refunded_amount::bigint || '/' || p.amount_payable::bigint || '/' || coalesce((select kind || ':' || status from platform.online_refund where part_id=p.id order by created_at desc limit 1),'-') from platform.online_order_part p where p.id='$spart'")"
  check "o.stock_short_cancel_refund_portion" "$([[ "$(field state "$r")" == cancelled && "$(field cancel_reason "$r")" == stock_short && "${n%%/*}" -gt 0 && "$n" == "${n%%/*}/${n%%/*}/full:"* ]] && echo 1 || echo 0)" "odoo=$(field state "$r")/$(field cancel_reason "$r")/so=$(field so_state "$r") refunded/porsi/refund=$n"
  oodoo -e SMOKE_STEP=stock -e SMOKE_PRODUCT=scarce -e SMOKE_QTY=50 > /dev/null

  # 4. Kode ambil salah 5x (member C, order kedua) -> review (Odoo) -> event -> bagian review di BFF
  local wrong wpart
  opy "$pc" c quote oil:1 wrong
  opy "$pc" c checkout_only wrong
  wrong="$(python_free_json c wrong order_id)"; wpart="$(part_of "$wrong")"
  opy "$pc" c pay wrong paid
  opy "$pc" c wait wrong fulfilling confirmed
  for _ in 1 2 3 4 5; do r="$(oodoo -e SMOKE_STEP=handover -e SMOKE_PART="$wpart" -e SMOKE_CODE="AAAA-AAAA")"; done
  check "o.pickup_wrong_5x_review" "$([[ "$(field "done" "$r")" == false && "$(field state "$r")" == review && "$(field review_reason "$r")" == pickup_code_attempts && "$(field pickup_attempts "$r")" == 5 ]] && echo 1 || echo 0)" "$(nomsg "$r")"
  opy "$pc" c wait wrong review review
  opy "$pc" c pickup_in_review wrong   # L-14: kode ambil ditahan selama review

  # 4b. SEC-95 (L-16): picking di luar wizard ditolak; batal SO berbayar dari Odoo oleh manajer -> refund
  #     `approval_required` (pemicu = odoo-uid) -> CLI online-refund-approve: peran cs ditolak, approver -> settled
  #     (L-19c: pemisahan tugas Odoo-vs-host berbasis peran; uid Odoo tidak bisa dipetakan ke akun host).
  local opc opart rid
  opy "$pc" c quote oil:1 opc
  opy "$pc" c checkout_only opc
  opc="$(python_free_json c opc order_id)"; opart="$(part_of "$opc")"
  opy "$pc" c pay opc paid
  opy "$pc" c wait opc fulfilling confirmed
  r="$(oodoo -e SMOKE_STEP=picking_direct -e SMOKE_PART="$opart")"
  check "o.picking_outside_wizard_rejected" "$([[ "$r" == *'"attempts": ["UserError", "UserError"]'* && "$(field picking_state "$r")" != "done" && "$(field state "$r")" == confirmed ]] && echo 1 || echo 0)" "$r"
  r="$(oodoo -e SMOKE_STEP=fake_ctx -e SMOKE_PART="$opart")"
  check "o.fake_internal_context_rejected" "$([[ "$r" == *'"staff_cancel": "'*Error'"'* && "$r" == *'"sudo_write_state": "'*Error'"'* && "$(field state "$r")" == confirmed && "$(field so_state "$r")" == sale ]] && echo 1 || echo 0)" "$r"
  r="$(oodoo -e SMOKE_STEP=ops_cancel -e SMOKE_PART="$opart" -e SMOKE_NOTE=0)"
  check "o.ops_cancel_without_reason_rejected" "$([[ "$(field ok "$r")" == false && "$(field state "$r")" == confirmed ]] && echo 1 || echo 0)" "$r"
  r="$(oodoo -e SMOKE_STEP=ops_cancel -e SMOKE_PART="$opart")"
  check "o.ops_cancel_by_manager" "$([[ "$(field ok "$r")" == true && "$(field state "$r")" == cancelled && "$(field cancel_reason "$r")" == ops && "$(field refund_initiator "$r")" == odoo && "$(field credit_notes "$r")" -ge 1 && "$r" == *'cancelled:done'* ]] && echo 1 || echo 0)" "$r"
  for _ in $(seq 1 20); do
    n="$(psql_platform -c "select id || '|' || status || '|' || coalesce(initiated_by,'-') || '|' || amount::bigint from platform.online_refund where part_id='$opart' order by created_at desc limit 1")"
    [[ "$n" == *'|approval_required|'* ]] && break; sleep 3
  done
  rid="${n%%|*}"
  r="$(psql_platform -c "select status || '/' || refunded_amount::bigint from platform.online_order_part where id='$opart'")"
  check "o.ops_cancel_refund_awaits_approval" "$([[ "$n" =~ \|approval_required\|odoo-(uid:[0-9]+|login:smoke_online_mgr)\| && "$r" == cancelled/0 ]] && echo 1 || echo 0)" "refund=${n#*|} bagian=$r"
  r="$(cs smoke-cs-op cs online-refund-approve --refund="$rid" --reason="smoke peran cs SMOKE-CS-001")"
  n="$(psql_platform -c "select status from platform.online_refund where id='$rid'")"
  check "o.ops_refund_approve_role_cs_rejected" "$([[ "$r" == *exit=1* && "$n" == approval_required ]] && echo 1 || echo 0)" "$(tail -c 200 <<< "$r") status=$n"
  r="$(cs smoke-cs-sup approver online-refund-approve --refund="$rid" --reason="smoke disetujui supervisor SMOKE-CS-001")"
  for _ in $(seq 1 30); do
    n="$(psql_platform -c "select r.status || '/' || r.amount::bigint || '/' || p.refunded_amount::bigint || '/' || p.amount_payable::bigint from platform.online_refund r join platform.online_order_part p on p.id=r.part_id where r.id='$rid'")"
    [[ "$n" == settled/* ]] && break; sleep 3
  done
  check "o.ops_refund_approved_settled" "$([[ "$r" == *exit=0* && "$n" =~ ^settled/([0-9]+)/([0-9]+)/([0-9]+)$ && "${BASH_REMATCH[1]}" == "${BASH_REMATCH[3]}" && "${BASH_REMATCH[2]}" == "${BASH_REMATCH[3]}" ]] && echo 1 || echo 0)" "refund/refunded/porsi=$n"
  r="$(psql_platform -c "select string_agg(action || ':' || outcome || ':' || actor, ',' order by id) from platform.online_admin_audit where refund_id='$rid'")"
  check "o.ops_refund_approve_audit" "$([[ "$r" == *'refund_approve:rejected_role:smoke-cs-op/smoke'* && "$r" == *'refund_approve:odoo_done:smoke-cs-sup/smoke'* ]] && echo 1 || echo 0)" "$r"
  opy "$pc" c wait opc cancelled cancelled 60

  # 5. Event Odoo -> BFF: HMAC salah / tanpa tanda tangan / di luar window / body diubah -> 401; duplikat & seq lama
  MSYS_NO_PATHCONV=1 docker run --rm --network efn_internal -v "${WORK_W}:/work" \
    -v "${SECRETS_W}/efn_ca_crt.txt:/s/efn_ca_crt.txt:ro" -v "${SECRETS_W}/odoo_bff_api_key.txt:/s/odoo_bff_api_key.txt:ro" \
    -v "${SECRETS_W}/odoo_online_event_hmac.txt:/s/odoo_online_event_hmac.txt:ro" \
    "$PY_IMAGE" python /work/online_smoke.py events "$part" "$main_oid" 2>&1 | grep '^SMOKE_RESULT' | tee -a "$RESULTS" || true

  # 6. Kontrak FE <-> BFF lewat route handler Next.js (https://app.efn.local/api/*)
  opy "$pw" w web_flow
  sed -nE 's/.*"pickup_code": "([^"]+)".*/\1/p' "${WORK}/online_web.json" >> "${WORK}/online_codes" 2>/dev/null || true

  # 7. PDP / rahasia: kode ambil & HP tidak ada di log BFF, Odoo, gateway, Traefik, maupun Loki
  local hits=0 c ph
  while read -r c; do
    [[ -z "$c" ]] && continue
    for v in "$c" "${c/-/}"; do
      hits=$(( hits + $(grep -cF "$v" "${WORK}/bff.log" || true) ))
      hits=$(( hits + $(docker logs --since 30m "$ODOO" 2>&1 | grep -cF "$v" || true) ))
      hits=$(( hits + $(docker logs --since 30m "${P}-odoo-rpc-gateway-1" 2>&1 | grep -cF "$v" || true) ))
      hits=$(( hits + $(docker logs --since 30m "${P}-traefik-1" 2>&1 | grep -cF "$v" || true) ))
    done
  done < "${WORK}/online_codes"
  check "o.pickup_code_not_in_logs" "$([[ "$hits" == 0 && $(grep -c . "${WORK}/online_codes") -ge 1 ]] && echo 1 || echo 0)" "hits=$hits untuk $(grep -c . "${WORK}/online_codes") kode (log BFF, Odoo, odoo-rpc-gateway, Traefik)"
  hits=0
  for ph in $pa $pb $pc $pw; do
    hits=$(( hits + $(grep -cF "${ph:4}" "${WORK}/bff.log" || true) + $(docker logs --since 30m "$ODOO" 2>&1 | grep -cF "${ph:4}" || true) ))
  done
  check "o.phone_not_in_logs" "$([[ "$hits" == 0 ]] && echo 1 || echo 0)" "hits=$hits (8 digit akhir 4 HP fiktif; log BFF & Odoo)"
  r="$(MSYS_NO_PATHCONV=1 docker exec "$PG" sh -c "pg_dump -U postgres -d efn_odoo --data-only -t 'efn_online_*' -t sale_order" | grep -cE "$(paste -sd'|' "${WORK}/online_codes")" || true)"
  check "o.pickup_code_not_in_odoo_db" "$([[ "$r" == 0 ]] && echo 1 || echo 0)" "hits=$r (pg_dump efn_online_* + sale_order: hanya hash KDF)"
  for _ in $(seq 1 6); do
    n="$(obs loki '{service="api-gateway"}' 30)"
    [[ "$(field lines "$n")" -ge 1 ]] && break; sleep 10
  done
  r=0
  while read -r c; do [[ -z "$c" ]] && continue
    x="$(obs loki "{service=~\"api-gateway|odoo|odoo-rpc-gateway|traefik\"} |= \"${c}\"" 60)"; r=$(( r + $(field lines "$x") ))
    x="$(obs loki "{service=~\"api-gateway|odoo|odoo-rpc-gateway|traefik\"} |= \"${c/-/}\"" 60)"; r=$(( r + $(field lines "$x") ))
  done < "${WORK}/online_codes"
  for ph in $pa $pb $pc $pw; do x="$(obs loki "{service=~\"api-gateway|odoo\"} |= \"${ph:4}\"" 60)"; r=$(( r + $(field lines "$x") )); done
  check "o.loki_no_pickup_code_or_phone" "$([[ "$r" == 0 && "$(field lines "$n")" -ge 1 ]] && echo 1 || echo 0)" "hits=$r; kontrol positif log api-gateway di Loki=$(field lines "$n")"
  r="$(obs loki '{stream="efn_online", online_event="recon"}' 60)"
  check "o.recon_heartbeat_in_loki" "$([[ "$(field lines "$r")" -ge 1 ]] && echo 1 || echo 0)" "$(head -c 200 <<< "$r")"

  # 8. Kill switch: ONLINE_ENABLED=false (overlay sementara; dev compose menyalakan online secara literal) -> 503
  local ov ovw
  ov="${WORK}/killswitch.override.yml"
  # G18: overlay yang sama menyalakan MIGRATION_ENABLED sementara untuk uji source_ref opak (L-21c) di jendela ini
  printf 'services:\n  api-gateway:\n    environment:\n      ONLINE_ENABLED: "false"\n      MIGRATION_ENABLED: "true"\n' > "$ov"
  ovw="$ov"; command -v cygpath > /dev/null && ovw="$(cygpath -m "$ov")"
  kill_bff_recreate() { # [overlay]
    (cd "$ROOT" && docker compose -f infra/compose/docker-compose.yml -f infra/compose/docker-compose.dev.yml ${1:+-f "$1"} \
      --env-file infra/compose/.env --profile core up -d --no-deps --force-recreate api-gateway > /dev/null 2>&1)
    for _ in $(seq 1 60); do [[ "$(docker inspect "$BFF" --format '{{.State.Health.Status}}' 2>/dev/null)" == healthy ]] && break; sleep 5; done
    # Traefik (provider docker): selama transisi status health container (starting -> healthy) router api dilepas
    # ±30-40 s (404 tanpa origin), walau container sudah menjawab. Tunggu 5 jawaban 200 berturut-turut (jeda 3 s).
    local ok200=0
    for _ in $(seq 1 60); do
      if [[ "$(MSYS_NO_PATHCONV=1 docker run --rm --network efn_edge --add-host "api.efn.local:${TRAEFIK_IP}" -v "${WORK_W}:/work" \
        curlimages/curl:8.11.1@sha256:c1fe1679c34d9784c1b0d1e5f62ac0a79fca01fb6377cdd33e90473c6f9f9a69 -sS -o /dev/null \
        -w '%{http_code}' --cacert /work/edge_ca.crt https://api.efn.local/v1/auth/challenge 2>/dev/null)" == 200 ]]; then
        ok200=$((ok200 + 1)); [[ "$ok200" -ge 5 ]] && break
      else
        ok200=0
      fi
      sleep 3
    done
    docker logs -f --since 1s "$BFF" >> "${WORK}/bff.log" 2>&1 &
    LOGPID="$LOGPID $!"
  }
  kill_bff_recreate "$ovw"
  opy "$pa" a killswitch
  local sr bad=""
  for sr in legacy-001 "62812$(rand8)" 6ba7b810-9dad-11d1-80b4-00c04fd430c8 12345678901234567890123456789012; do
    r="$(migrate members "{\"source_ref\":\"$sr\",\"member_ref\":\"62812$(rand8)\"}")"
    [[ "$r" == *invalid_source_ref*"|400" ]] || bad+="$sr=>$(tail -c 60 <<< "$r"); "
  done
  check "o.g18_source_ref_not_opaque_400" "$([[ -z "$bad" ]] && echo 1 || echo 0)" "${bad:-4 varian (label, HP, UUID v1, hex angka) -> 400 invalid_source_ref}"
  r="$(migrate members "{\"source_ref\":\"$(newuuid)\",\"member_ref\":\"62812$(rand8)\"}")"
  check "o.g18_source_ref_opaque_ok" "$([[ "$r" =~ \|20[01]$ ]] && echo 1 || echo 0)" "$(tail -c 120 <<< "$r")"
  kill_bff_recreate
  r="$(MSYS_NO_PATHCONV=1 docker run --rm --network efn_edge --add-host "api.efn.local:${TRAEFIK_IP}" -v "${WORK_W}:/work" \
    curlimages/curl:8.11.1@sha256:c1fe1679c34d9784c1b0d1e5f62ac0a79fca01fb6377cdd33e90473c6f9f9a69 -sS -o /dev/null -w '%{http_code}' \
    --cacert /work/edge_ca.crt https://api.efn.local/v1/catalog/channels 2>&1 || true)"
  check "o.killswitch_restored" "$([[ "$r" == 200 && "$(docker inspect "$BFF" --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -cx 'ONLINE_ENABLED=true')" == 1 ]] && echo 1 || echo 0)" "katalog=$r setelah recreate tanpa overlay"
}

scenario_p() { # Gelombang 18 (POS): Rupiah Rp1, poin = alat bayar dgn pajak tetap, refund ke metode asal, integritas
  #                pembayaran (r11), daftar member 17+, probe RPC kasir, rekonsiliasi harian Poin EFN (K-18-6)
  echo "== p. G18 POS: Rp1, redeem = pembayaran (DPP tetap), refund metode asal, SEC-113/115/116/121, enroll 17+, recon"
  local r pp pq pr t1 t2 t3 t4 t5 t6 td tr u v n code
  MSYS_NO_PATHCONV=1 docker exec "$ODOO" test -s /tmp/efn_smoke_state.json || odoo -e SMOKE_STEP=setup | tee -a "$RESULTS"
  pp="0812$(rand8)"; pq="0812$(rand8)"; pr="0812$(rand8)"
  odoo -e SMOKE_STEP=loy_partner -e SMOKE_KEY=P -e SMOKE_PHONE="$pp" > /dev/null
  odoo -e SMOKE_STEP=loy_partner -e SMOKE_KEY=Q -e SMOKE_PHONE="$pq" > /dev/null
  odoo -e SMOKE_STEP=loy_partner -e SMOKE_KEY=R -e SMOKE_PHONE="$pr" > /dev/null
  redeem_p() { # uuid partner amount points -> reservasi (OTP pos_redeem dari log BFF dev)
    local rr cc
    rr="$(odoo -e SMOKE_STEP=loy_otp_request -e SMOKE_ORDER_UUID="$1" -e SMOKE_PARTNER="$2" -e SMOKE_AMOUNT="$3")"
    cc="$(otp_code "$(field member_masked "$rr")")"
    odoo -e SMOKE_STEP=loy_otp_verify -e SMOKE_ORDER_UUID="$1" -e SMOKE_CODE="$cc" > /dev/null
    odoo -e SMOKE_STEP=loy_reserve -e SMOKE_ORDER_UUID="$1" -e SMOKE_PARTNER="$2" -e SMOKE_POINTS="$4" -e SMOKE_AMOUNT="$3"
  }
  odoo -e SMOKE_STEP=loy_config > /dev/null

  # 1. W-03: order ber-PB1 (Rp12.345 x 3 + PB1 10 %) -> total & pajak Rupiah bulat (IDR rounding 1)
  t1="$(newuuid)"
  r="$(odoo -e SMOKE_STEP=g18_tax_order -e SMOKE_ORDER_UUID="$t1" -e SMOKE_QTY=3)"
  check "p.idr_total_rounded" "$([[ "$(field ok "$r")" == true && "$(field idr_rounding "$r")" == 1.0 && "$(field total_int "$r")" == true && "$(field state "$r")" =~ ^(paid|done)$ ]] && echo 1 || echo 0)" "$r"

  # 2. E-30: member P punya poin (earn 20 x Rp50.000 = 100) -> redeem 50 poin (Rp5.000) sebagai PEMBAYARAN order ber-PB1;
  #    DPP & PB1 tetap atas harga penuh, commit membawa payment_amount (BFF reservasi committed/50/5000)
  u="$(newuuid)"
  odoo -e SMOKE_STEP=loy_sync -e SMOKE_ORDER_UUID="$u" -e SMOKE_PARTNER=P -e SMOKE_QTY=20 > /dev/null
  r="$(odoo -e SMOKE_STEP=loy_outbox -e SMOKE_ORDER_UUID="$u")"
  check "p.earn_for_redeem" "$([[ "$(field earn_state "$r")" == "done" && "$(field points_earned "$r")" == 100 ]] && echo 1 || echo 0)" "$r"
  t2="$(newuuid)"
  r="$(redeem_p "$t2" P 40739 50)"
  check "p.redeem_reserved" "$([[ "$(field ok "$r")" == true && "$(field value_idr "$r")" == 5000 ]] && echo 1 || echo 0)" "$r"
  r="$(odoo -e SMOKE_STEP=g18_tax_redeem -e SMOKE_ORDER_UUID="$t2" -e SMOKE_PARTNER=P -e SMOKE_QTY=3)"
  check "p.redeem_payment_tax_unchanged" "$([[ "$(field ok "$r")" == true && "$(field tax_unchanged "$r")" == true && "$(field total_int "$r")" == true && "$(field points_paid "$r")" == 5000* && "$(field commit_state "$r")" == "done" ]] && echo 1 || echo 0)" "$r"
  r="$(psql_platform -c "select status||'/'||points||'/'||value_idr from platform.loyalty_reservation where order_ref='$t2' order by created_at desc limit 1")"
  check "p.redeem_bff_committed" "$([[ "$r" == committed/50/5000 ]] && echo 1 || echo 0)" "reservasi BFF=$r"

  # 3. E-31: refund order tunai t1 ke EDC ditolak; tunai melebihi yang dibayar ditolak; tunai sesuai -> OK
  r="$(odoo -e SMOKE_STEP=g18_refund_method -e SMOKE_ORDER_UUID="$(newuuid)" -e SMOKE_ORIG_UUID="$t1")"
  check "p.refund_other_method_rejected" "$([[ "$(field other_method "$r")" == UserError && "$(field other_method_msg "$r")" == *"metode bayar asal"* ]] && echo 1 || echo 0)" "$r"
  check "p.refund_over_paid_rejected" "$([[ "$(field over_paid "$r")" == UserError ]] && echo 1 || echo 0)" "over_paid=$(field over_paid "$r") $(field over_paid_msg "$r")"
  check "p.refund_same_method_ok" "$([[ "$(field same_method "$r")" =~ ^(paid|done)$ ]] && echo 1 || echo 0)" "same_method=$(field same_method "$r")"
  r="$(odoo -e SMOKE_STEP=g18_unlinked_noncash -e SMOKE_ORDER_UUID="$(newuuid)")"
  check "p.unlinked_return_noncash_rejected" "$([[ "$(field error "$r")" == UserError && "$(field created "$r")" == false && "$r" == *tunai* ]] && echo 1 || echo 0)" "$r"

  # 3b. L-26 (SEC-128): refund berpoin dgn nilai restore SALAH (penuh Rp5.000 untuk 1 dari 3 unit) -> Odoo hold
  #     (alokasi) -> supervisor setujui apa adanya -> BFF 409 restore_value_mismatch -> hold lagi -> supervisor
  #     "setujui nilai restore" -> terkirim dgn restore_approved (BFF tetap membatasi ke sisa redeem)
  tr="$(newuuid)"
  r="$(odoo -e SMOKE_STEP=g18_refund_points -e SMOKE_ORDER_UUID="$tr" -e SMOKE_ORIG_UUID="$t2" -e SMOKE_RESTORE=5000)"
  check "p.restore_wrong_value_held_by_odoo" "$([[ "$(field ok "$r")" == true && "$(field reverse_state "$r")" == hold && "$(field reverse_error "$r")" == refund_points_allocation_mismatch ]] && echo 1 || echo 0)" "$r"
  r="$(odoo -e SMOKE_STEP=g18_hold_decide -e SMOKE_ORDER_UUID="$tr" -e SMOKE_ACTION=hold_approve)"
  check "p.restore_bff_409_held" "$([[ "$(field ok "$r")" == true && "$(field reverse_state "$r")" == hold && "$(field reverse_error "$r")" == *restore_value_mismatch* ]] && echo 1 || echo 0)" "$r"
  r="$(odoo -e SMOKE_STEP=g18_hold_decide -e SMOKE_ORDER_UUID="$tr" -e SMOKE_ACTION=restore_approve)"
  check "p.restore_supervisor_approved" "$([[ "$(field ok "$r")" == true && "$(field reverse_state "$r")" == "done" && "$(field restore_approved "$r")" == true ]] && echo 1 || echo 0)" "$r"
  r="$(psql_platform -c "select points_restored_value || '/' || restore_approved from platform.loyalty_pos_reverse where refund_order_ref='$tr'")"
  check "p.restore_approved_recorded_bff" "$([[ "$r" == 5000/t* || "$r" == 5000/true ]] && echo 1 || echo 0)" "loyalty_pos_reverse value/restore_approved=$r"

  # 3c. L-26: barang kategori TANPA poin (excluded) dibayar sebagian poin -> refund sah lolos (rasio eligible 0)
  u="$(newuuid)"
  odoo -e SMOKE_STEP=loy_sync -e SMOKE_ORDER_UUID="$u" -e SMOKE_PARTNER=R -e SMOKE_QTY=20 > /dev/null
  odoo -e SMOKE_STEP=loy_outbox -e SMOKE_ORDER_UUID="$u" > /dev/null
  t4="$(newuuid)"
  redeem_p "$t4" R 100000 50 > /dev/null
  r="$(odoo -e SMOKE_STEP=loy_sync -e SMOKE_ORDER_UUID="$t4" -e SMOKE_PARTNER=R -e SMOKE_QTY=2 -e SMOKE_PRODUCT=excluded)"
  r="$r | $(odoo -e SMOKE_STEP=loy_outbox -e SMOKE_ORDER_UUID="$t4")"
  check "p.excluded_goods_redeem_committed" "$([[ "$r" == *'"redeem_commit_state": "done"'* ]] && echo 1 || echo 0)" "$r"
  t5="$(newuuid)"
  r="$(odoo -e SMOKE_STEP=loy_refund -e SMOKE_ORDER_UUID="$t5" -e SMOKE_ORIG_UUID="$t4" -e SMOKE_QTY=1)"
  r="$r | $(odoo -e SMOKE_STEP=loy_outbox -e SMOKE_ORDER_UUID="$t5")"
  check "p.excluded_goods_refund_restore_ok" "$([[ "$r" == *'"reverse_state": "done"'* && "$r" == *'"reverse_points_restored_value": 2500'* ]] && echo 1 || echo 0)" "$r"

  # 3d. SEC-125: draft dgn nominal Poin EFN != reservasi -> baris poin dibuang (reservasi lalu dilepas)
  td="$(newuuid)"
  redeem_p "$td" R 100000 20 > /dev/null
  r="$(odoo -e SMOKE_STEP=loy_sync -e SMOKE_ORDER_UUID="$td" -e SMOKE_PARTNER=R -e SMOKE_QTY=2 -e SMOKE_DRAFT=1 -e SMOKE_POINTS_VALUE=1500)"
  check "p.draft_wrong_points_value_dropped" "$([[ "$(field ok "$r")" == true && "$(field state "$r")" == draft && "$(field points_payments "$r")" == 0 ]] && echo 1 || echo 0)" "$r"
  odoo -e SMOKE_STEP=loy_release -e SMOKE_ORDER_UUID="$td" > /dev/null

  # 4. SEC-116: is_change palsu (EDC dobel + tunai -total) tersimpan False; refund order EDC ke tunai ber-is_change ditolak
  t3="$(newuuid)"
  odoo -e SMOKE_STEP=g18_tax_order -e SMOKE_ORDER_UUID="$t3" -e SMOKE_QTY=1 -e SMOKE_METHOD=edc > /dev/null
  r="$(odoo -e SMOKE_STEP=g18_is_change -e SMOKE_ORDER_UUID="$(newuuid)" -e SMOKE_ORIG_UUID="$t3")"
  check "p.fake_is_change_not_trusted" "$([[ "$r" == *'"stored_is_change": [false]'* && "$(field refund_error "$r")" == UserError ]] && echo 1 || echo 0)" "$r"

  # 5. E-34 / K-18-8: daftar member tanpa centang usia -> ditolak lokal (tanpa OTP); dengan centang -> OTP -> enrolled,
  #    BFF member.age_confirmed_source=pos, partner terkunci (SEC-115)
  v="$(newuuid)"
  n="$(grep -c 'OTP untuk' "${WORK}/bff.log" || true)"
  r="$(odoo -e SMOKE_STEP=g18_enroll -e SMOKE_ORDER_UUID="$v" -e SMOKE_PARTNER=Q -e SMOKE_AGE=0)"
  check "p.enroll_without_age_rejected" "$([[ "$(field ok "$r")" == false && "$(field error_code "$r")" == age_confirmation_required && "$(grep -c 'OTP untuk' "${WORK}/bff.log" || true)" == "$n" ]] && echo 1 || echo 0)" "$r"
  r="$(odoo -e SMOKE_STEP=g18_enroll -e SMOKE_ORDER_UUID="$v" -e SMOKE_PARTNER=Q -e SMOKE_AGE=1)"
  code="$(otp_code "$(field member_masked "$r")")"
  r="$(odoo -e SMOKE_STEP=g18_enroll -e SMOKE_ORDER_UUID="$v" -e SMOKE_PARTNER=Q -e SMOKE_AGE=1 -e SMOKE_CODE="$code")"
  check "p.enroll_with_age_ok" "$([[ "$(field ok "$r")" == true && "$(field challenge_state "$r")" == consumed && "$(field member_locked "$r")" == true ]] && echo 1 || echo 0)" "$r"
  r="$(psql_platform -c "select count(*) from platform.member where age_confirmed_source='pos' and age_confirmed_at > now() - interval '10 minutes'")"
  check "p.enroll_bff_age_source_pos" "$([[ "$r" -ge 1 ]] && echo 1 || echo 0)" "member age_confirmed_source=pos baru=$r"

  # 6. r11 SEC-113/115/121 lewat RPC web client NYATA sebagai kasir (password + TOTP, Traefik wg-https)
  odoo -e SMOKE_STEP=rpc_users > /dev/null
  t6="$(newuuid)"
  odoo -e SMOKE_STEP=loy_sync -e SMOKE_ORDER_UUID="$t6" -e SMOKE_PARTNER=Q -e SMOKE_QTY=1 -e SMOKE_DRAFT=1 > /dev/null
  r="$(odoo -e SMOKE_STEP=g18_rpc_targets -e SMOKE_ORIG_UUID="$t1" -e SMOKE_PARTNER=P -e SMOKE_DRAFT_UUID="$t6")"
  local before; before="$(field payment_amount "$r")"
  check "p.member_locked_marker" "$([[ "$(field member_locked "$r")" == true ]] && echo 1 || echo 0)" "$r"
  r="$(MSYS_NO_PATHCONV=1 docker exec "$ODOO" sh -c 'cat /tmp/efn_smoke_rpc.json; rm -f /tmp/efn_smoke_rpc.json' \
    | MSYS_NO_PATHCONV=1 docker run --rm -i --network efn_edge --add-host "erp.efn.local:${TRAEFIK_IP}" \
        -v "${WORK_W}:/work:ro" "$PY_IMAGE" python /work/odoo_rpc_smoke.py 2>&1 | grep '^SMOKE_RESULT' | grep -F '"user": "smoke_rpc_cashier"' | head -1 || true)"
  rj() { grep -oE "\"$1\": \\{\"rejected\": (true|false)" <<< "$r" | grep -oE '(true|false)$'; }
  check "p.rpc_cashier_payment_locked" "$([[ "$r" == *'"logged_in": true'* && "$(rj payment_write_amount)" == true && "$(rj payment_write_method)" == true && "$(rj payment_unlink)" == true ]] && echo 1 || echo 0)" "$(head -c 400 <<< "$r")"
  check "p.rpc_cashier_member_phone_locked" "$([[ "$(rj member_phone_write)" == true ]] && echo 1 || echo 0)" "$(grep -oE '"member_phone_write": \{[^}]*\}' <<< "$r")"
  check "p.rpc_cashier_create_individual_ok" "$([[ "$r" == *'"create_individual": {"created": true'* ]] && echo 1 || echo 0)" "$(grep -oE '"create_individual": \{[^}]*\}' <<< "$r")"
  check "p.rpc_cashier_create_company_rejected" "$([[ "$(rj create_company)" == true ]] && echo 1 || echo 0)" "$(grep -oE '"create_company": \{[^}]*\}' <<< "$r")"
  check "p.rpc_cashier_paid_transition_rejected" "$([[ "$(rj order_paid_action)" == true && "$(rj order_write_state_paid)" == true && "$(rj order_create_paid)" == true ]] && echo 1 || echo 0)" "$(grep -oE '"order_(paid_action|write_state_paid|create_paid)": \{[^}]*\}' <<< "$r" | tr '\n' ' ')"
  check "p.rpc_cashier_sync_foreign_payment_rejected" "$([[ "$(rj sync_foreign_payment_1)" == true && "$(rj sync_foreign_payment_2)" == true && "$(rj sync_foreign_payment_4)" == true ]] && echo 1 || echo 0)" "$(grep -oE '"sync_foreign_payment_[124]": \{[^}]*\}' <<< "$r" | tr '\n' ' ')"
  r="$(odoo -e SMOKE_STEP=g18_rpc_after -e SMOKE_ORIG_UUID="$t1" -e SMOKE_PARTNER=P -e SMOKE_DRAFT_UUID="$t6")"
  check "p.rpc_after_state_unchanged" "$([[ "$(field payment_amount "$r")" == "$before" && "$(field payments "$r")" == 1 && "$(field phone_unchanged "$r")" == true && "$(field draft_state "$r")" == draft && "$(field paid_orders_by_cashier "$r")" == 0 && "$r" == *'"is_company": false'* && "$r" != *'"is_company": true'* ]] && echo 1 || echo 0)" "$r"

  # 7. K-18-6: rekonsiliasi harian Poin EFN (mode payment) untuk HARI INI atas transaksi smoke l + p = 0 selisih
  # Rekonsiliasi per hari WIB: order run lain (mis. run terputus yang meninggalkan reverse `hold`) memang ikut
  # terhitung -> yang dinilai = order berpoin RUN INI (SMOKE_REFS) 0 selisih; total hari dicatat di detail.
  local refs; refs="$(cat "${WORK}/points_refs" 2>/dev/null || true)"
  refs="${refs:+$refs,}$t2,$tr,$t4,$t5,$td"
  r="$(MSYS_NO_PATHCONV=1 docker exec -i -e SMOKE_REFS="$refs" "${P}-etl-1" python - < "${SMOKE_DIR}/etl_points_recon_smoke.py" 2>&1 | grep '^SMOKE_RESULT' | tail -1 || true)"
  check "p.points_recon_daily_zero_mismatch" "$([[ "$(field mode "$r")" == payment && "$(field run_refs_checked "$r")" -ge 6 && "$(field run_mismatched "$r")" == 0 ]] && echo 1 || echo 0)" "$(head -c 700 <<< "$r")"
  check "p.points_restore_value_zero_mismatch" "$([[ "$(field today_restore_mismatched "$r")" == 0 && "$(field today_restored_idr "$r")" != 0 ]] && echo 1 || echo 0)" "restore_mismatched=$(field today_restore_mismatched "$r") restored_idr=$(field today_restored_idr "$r") (SEC-118)"
  # PDP: HP fiktif P/Q tidak ada di log BFF/Odoo
  n=0; for x in "$pp" "$pq" "$pr"; do n=$(( n + $(grep -c "${x:4}" "${WORK}/bff.log" || true) + $(docker logs --since 30m "$ODOO" 2>&1 | grep -c "${x:4}" || true) )); done
  check "p.no_raw_phone_logs" "$([[ "$n" == 0 ]] && echo 1 || echo 0)" "hits=$n"
}

for s in "${SCENARIOS[@]}"; do "scenario_${s}"; done

echo
# Hanya status level cek (body respons bisa memuat "ok": false, mis. OTP salah yang memang diharapkan)
CHECK_OK='"check": "[^"]*", "ok": true'; CHECK_FAIL='"check": "[^"]*", "ok": false'
pass="$(grep -cE "$CHECK_OK" "$RESULTS" || true)"; fail="$(grep -cE "$CHECK_FAIL" "$RESULTS" || true)"
echo "RINGKASAN: PASS=$pass FAIL=$fail (hasil lengkap: $RESULTS)"
grep -E "$CHECK_FAIL" "$RESULTS" || true
# CI (R7, .github/workflows/e2e-nightly.yml): salin hasil ke direktori artefak bila diminta
if [[ -n "${SMOKE_OUT_DIR:-}" ]]; then mkdir -p "$SMOKE_OUT_DIR" && cp "$RESULTS" "${SMOKE_OUT_DIR}/smoke-results.txt"; fi
[[ "$fail" == 0 ]]
