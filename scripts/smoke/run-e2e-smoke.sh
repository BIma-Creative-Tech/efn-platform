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
# Pemakaian (Git Bash/Linux):  bash scripts/smoke/run-e2e-smoke.sh [a b c d e f g l v]   (default: a b c d f e g l;
#   v = jalur VLM nyata, opsional, butuh model OLLAMA_VISION_MODEL di Ollama)
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
SCENARIOS=("$@"); [[ ${#SCENARIOS[@]} -eq 0 ]] && SCENARIOS=(a b c d f e g l)

WORK="$(mktemp -d)"
RESULTS="${WORK}/results.txt"
: > "$RESULTS"
cp "${ROOT}/secrets/dev_edge_crt.txt" "${WORK}/edge_ca.crt"
cp "${SMOKE_DIR}/bff_smoke.py" "${WORK}/"
WORK_W="$WORK"; command -v cygpath > /dev/null && WORK_W="$(cygpath -w "$WORK")"

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
  g="$(MSYS_NO_PATHCONV=1 docker exec -i "$PG" psql -X -q -v ON_ERROR_STOP=1 -U efn_odoo -d efn_odoo -f - \
        < "${ROOT}/services/etl/sql/odoo-readonly-grants.sql" 2>&1 && echo GRANT_OK || true)"
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
  out="$(MSYS_NO_PATHCONV=1 docker run --rm --network efn_data postgres:16.6-bookworm@sha256:557fea37a744d5f4c8faab304b0a90858b53ab119735a88c131fd19dab802f36 psql "host=postgres dbname=efn_platform user=efn_bff sslmode=disable connect_timeout=5" -c 'select 1' 2>&1 || true)"
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
  check "l.config_points_product" "$([[ "$(field ok "$r")" == true && "$(field points_product "$r")" -gt 0 ]] && echo 1 || echo 0)" "$r"
  ledger() { psql_platform -c "select coalesce(sum(delta_points),0) from platform.loyalty_ledger where source='$1' and source_ref='$2'${3:+ and $3}"; }
  resv() { psql_platform -c "select status||'/'||points||'/'||value_idr from platform.loyalty_reservation where order_ref='$1' order by created_at desc limit 1"; }
  bal() { field points_balance "$(odoo -e SMOKE_STEP=loy_balance -e SMOKE_PARTNER="${1:-L}")"; }
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
  check "l.redeem_order_synced" "$([[ "$(field state "$r")" == paid && "$(field points_lines "$r")" == 1 && "$(field total "$r")" == 194000* && "$r" == *redeem_commit* ]] && echo 1 || echo 0)" "$r"
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
  check "l.draft_keeps_points_line" "$([[ "$(field state "$r")" == draft && "$(field points_lines "$r")" == 1 ]] && echo 1 || echo 0)" "$r"
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
  check "l.refund_synced" "$([[ "$(field ok "$r")" == true && "$(field is_refund "$r")" == true && "$(field preview_value "$r")" == 3000 && "$(field reverse_ratio "$r")" == 0.5 && "$(field reverse_eligible "$r")" == 97000 ]] && echo 1 || echo 0)" "$r"
  r="$(odoo -e SMOKE_STEP=loy_outbox -e SMOKE_ORDER_UUID="$ur")"
  check "l.refund_reverse_done" "$([[ "$(field reverse_state "$r")" == "done" && "$(field reverse_delta "$r")" == 20 ]] && echo 1 || echo 0)" "$r"
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

  # 9. PDP: tidak ada HP mentah di tabel loyalty BFF/Odoo maupun log BFF/Odoo
  hs=0
  for n in "$pl" "$pm" "$pn"; do
    m="${n:4}"
    hs=$(( hs + $(MSYS_NO_PATHCONV=1 docker exec "$PG" sh -c "pg_dump -U postgres -d efn_platform --data-only -t 'platform.loyalty_*'; pg_dump -U postgres -d efn_odoo --data-only -t 'efn_pos_loyalty_*' -t efn_pos_otp_challenge" | grep -c "$m" || true) ))
    hs=$(( hs + $(grep -c "$m" "${WORK}/bff.log" || true) + $(docker logs --since 30m "$ODOO" 2>&1 | grep -c "$m" || true) ))
  done
  check "l.no_raw_phone_tables_logs" "$([[ "$hs" == 0 ]] && echo 1 || echo 0)" "hits=$hs (pg_dump platform.loyalty_* + efn_pos_loyalty_* + efn_pos_otp_challenge, log BFF & Odoo)"

  # 10. ETL -> ClickHouse: baris poin (line_kind=points) order u2 dan refund ur masuk fact_sales_line
  local o2 orr
  o2="$(MSYS_NO_PATHCONV=1 docker exec "$PG" psql -U postgres -d efn_odoo -At -c "select id from pos_order where uuid='$u2'")"
  orr="$(MSYS_NO_PATHCONV=1 docker exec "$PG" psql -U postgres -d efn_odoo -At -c "select id from pos_order where uuid='$ur'")"
  for _ in $(seq 1 30); do
    r="$(echo "select countIf(order_id=$o2 and line_kind='points') || '/' || toString(toInt64(sumIf(subtotal, order_id=$o2 and line_kind='points'))) || '/' || countIf(order_id=$orr and is_refund=1) from efn_dwh.fact_sales_line final where order_id in ($o2,$orr)" | chq)"
    [[ "$r" == 1/* && "${r##*/}" -ge 1 ]] && break
    sleep 10
  done
  check "l.etl_points_and_refund_lines" "$([[ "$r" == 1/-6000/* && "${r##*/}" -ge 1 ]] && echo 1 || echo 0)" "points_lines/points_subtotal/refund_lines=$r"
  echo "$u2 $ur $o2 $orr" > "${WORK}/loyalty_orders"
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
