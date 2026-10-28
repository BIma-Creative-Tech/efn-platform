#!/usr/bin/env bash
# Uji regresi scripts/host/efn-rotate-online-key (W-11, R-T15) dengan docker/Odoo/BFF PALSU, di dalam CONTAINER sekali
# pakai (pola test-efn-online-cs.sh). Menulis /usr/local/{sbin,bin}, /opt/efn, /etc/efn, /var/lib/efn -> MENOLAK jalan
# di luar container.
#
#   docker run --rm -v "$PWD:/src:ro" python:3.12-bookworm@sha256:e91fec3d1ac69f04e4eddcd29c327e630ce34658cf31075bfa7e8b0e052bafea \
#     bash /src/scripts/host/test-efn-rotate-online-key.sh
# Keluaran: PASS/FAIL per kasus; exit 1 bila ada FAIL.
# Docker palsu: `compose config/ps/up` (2 replika BFF + 1 sidecar network_mode), `exec <odoo> odoo shell` = penyimpanan
# key di /tmp/fk/keys.db (id exp nama key dicabut), `exec <bff> node` = gateway: 200 bila key yang TERPASANG saat
# recreate terakhir ada di db, belum dicabut, dan tidak ditandai rusak; selain itu 401.
# G19 (kasus 50-84): kanal overlay (PB -> secret/user/state pb), validasi kode kanal (traversal/injeksi), --all
# (CHANNELS / fallback ONLINE_CHANNELS .env, kegagalan per kanal + metrik, satu overlap, kunci host, idempoten),
# --no-wait, --bootstrap (overlay sementara), user teknis bersama ditolak; blok Python `odoo shell` dijalankan nyata
# terhadap env Odoo palsu (precheck user_shared / user_not_in_channel / user_missing / user_privileged).
# G24 (docs/04 §16.1, kasus 77b-77g): WTZ di allowlist (overlay online-wtz), QR-WTZ/BOOKING-WTZ ditolak (L-92).
# G20 (docs/04 §12.1, kasus 55b-55e, 61b, 71b): kode kanal = kode PT (GL base, PB/BCR/BCF overlay); kode lama RTL/FNB
# dan kanal G19 berhifen (RTL-GL, FNB-BACHA, ...) DITOLAK allowlist ALLOWED_CHANNELS. Pemetaan kasus lama -> baru:
# RTL -> GL (base), RTL-GL -> PB (overlay ter-deploy), FNB-BACHA -> BCF (--bootstrap), FNB-PB/RTL-BACHA -> BCR (belum
# di-deploy, overlay tidak ada di fixture).
# G22 (docs/04 §14.A.5, kasus 90-110): kunci user teknis QR meja QR-PB/QR-BCF (secret odoo_api_key_qr_<pt>, user
# efn_qr_<pt>, overlay docker-compose.online-qr-<pt>.yml, nama key efn-qr-<PT>-*, verifikasi path #15 efn_qr_status dengan
# 422 = key sah HANYA untuk QR), --all dari FNB_QR_ENABLED/FNB_QR_COMPANIES .env, precheck Python QR (company, grup POS,
# user bersama kanal online, modul belum terpasang).
# G23 (docs/04 §15.3, kasus 111-122): kunci user teknis reservasi BOOKING-PB/BOOKING-BCF (secret odoo_api_key_booking_<pt>,
# user efn_booking_<pt>, overlay docker-compose.online-booking-<pt>.yml, nama key efn-booking-<PT>-*, verifikasi path #19
# efn_booking_status dengan 403 Odoo = key sah HANYA untuk booking; 403 gateway (deny=1) = gagal), --all dari
# BOOKING_ENABLED/BOOKING_COMPANIES .env, precheck Python booking.
# G27 (docs/04 §18.2, kasus 127-136): kunci user teknis promo PROMO-PB (secret odoo_api_key_promo_pb, user efn_promo_pb,
# overlay docker-compose.online-promo-pb.yml, nama key efn-promo-PB-*, verifikasi path #22 efn_promo_feed 200 saja), --all
# dari PROMO_ENABLED/PROMO_COMPANIES .env, precheck Python promo.
# Yang TIDAK diuji di sini: Odoo/gateway/BFF sungguhan (uji nyata di stack dev: runbook odoo-online-api-key.md §2),
# systemd, journald asli.
set -euo pipefail
[[ -f /.dockerenv || -n "${EFN_TEST_IN_CONTAINER:-}" ]] || { echo "hanya di dalam container sekali pakai" >&2; exit 2; }
[[ "$EUID" -eq 0 ]] || { echo "harus root (di container)" >&2; exit 2; }
SRC="${SRC:-/src}"
export DEBIAN_FRONTEND=noninteractive
command -v jq > /dev/null || { apt-get update -qq && apt-get install -y -qq jq > /dev/null; }
W=/usr/local/sbin/efn-rotate-online-key
install -m 0755 "$SRC/scripts/host/efn-rotate-online-key" "$W"

getent group efn-superadmin > /dev/null || groupadd efn-superadmin
id sa1 > /dev/null 2>&1 || useradd -m -G efn-superadmin sa1
id ops1 > /dev/null 2>&1 || useradd -m ops1
install -d -m 0755 /opt/efn/compose /etc/efn /var/lib/efn/node-exporter
install -d -o root -g efn-superadmin -m 0750 /opt/efn/secrets
printf 'docker-compose.yml\ndocker-compose.prod.yml\ndocker-compose.host.yml\n' > /opt/efn/compose/.efn-compose-files
for f in docker-compose.yml docker-compose.prod.yml docker-compose.host.yml; do echo "name: efn" > "/opt/efn/compose/$f"; done
# Overlay kanal untuk --bootstrap: BCF ada; BCR sengaja TIDAK ada -> kasus overlay hilang
echo "services: {}" > /opt/efn/compose/docker-compose.online-bcf.yml
# G22: overlay QR meja PB ada (--bootstrap QR-PB); QR-BCF sengaja TIDAK ada -> kasus overlay hilang
echo "services: {}" > /opt/efn/compose/docker-compose.online-qr-pb.yml
# G23: overlay reservasi PB ada (--bootstrap BOOKING-PB); BOOKING-BCF sengaja TIDAK ada -> kasus overlay hilang
echo "services: {}" > /opt/efn/compose/docker-compose.online-booking-pb.yml
# G27: overlay promo PB ada (--bootstrap PROMO-PB)
echo "services: {}" > /opt/efn/compose/docker-compose.online-promo-pb.yml
SECRET=/opt/efn/secrets/odoo_api_key_online_gl.txt
SEC_PB=/opt/efn/secrets/odoo_api_key_online_pb.txt
SEC_BCF=/opt/efn/secrets/odoo_api_key_online_bcf.txt
SEC_QRPB=/opt/efn/secrets/odoo_api_key_qr_pb.txt
SEC_BKPB=/opt/efn/secrets/odoo_api_key_booking_pb.txt
SEC_PRPB=/opt/efn/secrets/odoo_api_key_promo_pb.txt

# Docker palsu: secret GL (base) + PB (overlay; deploy dengan ONLINE_CHANNELS=GL,PB) di-mount api-gateway; BCF hanya
# bila overlay docker-compose.online-bcf.yml ikut `-f` (--bootstrap); BCR tidak pernah (kanal belum di-deploy).
# Key per user teknis: efn_online_gl -> /tmp/fk/keys.db (kasus lama), lainnya -> /tmp/fk/keys.<login>.db.
cat > /usr/local/bin/docker <<'EOF'
#!/usr/bin/env bash
# docker palsu (lihat header test)
FK=/tmp/fk
cmd="$1"; shift
if [[ "$cmd" == compose ]]; then
  args=(); files=(); while (($#)); do
    case "$1" in --project-name|--project-directory) shift 2 ;; -f) files+=("$2"); shift 2 ;; *) args+=("$1"); shift ;; esac
  done
  set -- "${args[@]}"
  sub="$1"; shift
  fb=0; qp=0; bp=0; pp=0
  for f in "${files[@]}"; do
    [[ "$f" == */docker-compose.online-bcf.yml ]] && fb=1
    # G23: overlay reservasi PB -> secret odoo_api_key_booking_pb di-mount api-gateway
    [[ "$f" == */docker-compose.online-booking-pb.yml ]] && bp=1
    # G27: overlay promo PB -> secret odoo_api_key_promo_pb di-mount api-gateway
    [[ "$f" == */docker-compose.online-promo-pb.yml ]] && pp=1
    # G22: overlay QR meja PB (docker-compose.online-qr-pb.yml) -> secret odoo_api_key_qr_pb di-mount api-gateway
    [[ "$f" == */docker-compose.online-qr-pb.yml ]] && qp=1
  done
  case "$sub" in
    config)
      jq -nc --argjson fb "$fb" --argjson qp "$qp" --argjson bp "$bp" --argjson pp "$pp" '{secrets:({odoo_api_key_online_gl:{file:"/opt/efn/secrets/odoo_api_key_online_gl.txt"},
                   odoo_api_key_online_pb:{file:"/opt/efn/secrets/odoo_api_key_online_pb.txt"}}
                 + (if $fb == 1 then {odoo_api_key_online_bcf:{file:"/opt/efn/secrets/odoo_api_key_online_bcf.txt"}} else {} end)
                 + (if $qp == 1 then {odoo_api_key_qr_pb:{file:"/opt/efn/secrets/odoo_api_key_qr_pb.txt"}} else {} end)
                 + (if $bp == 1 then {odoo_api_key_booking_pb:{file:"/opt/efn/secrets/odoo_api_key_booking_pb.txt"}} else {} end)
                 + (if $pp == 1 then {odoo_api_key_promo_pb:{file:"/opt/efn/secrets/odoo_api_key_promo_pb.txt"}} else {} end)),
        services:{"api-gateway":{secrets:([{source:"odoo_api_key_online_gl"},{source:"odoo_api_key_online_pb"},{source:"efn_ca_crt"}]
                    + (if $fb == 1 then [{source:"odoo_api_key_online_bcf"}] else [] end)
                    + (if $qp == 1 then [{source:"odoo_api_key_qr_pb"}] else [] end)
                    + (if $bp == 1 then [{source:"odoo_api_key_booking_pb"}] else [] end)
                    + (if $pp == 1 then [{source:"odoo_api_key_promo_pb"}] else [] end))},
                  "bff-tls":{network_mode:"service:api-gateway"}, odoo:{secrets:[{source:"odoo_online_event_hmac"}]},
                  "superapp-web":{}}}' ;;
    ps)
      if [[ "$1" == -q ]]; then
        shift 3   # -q --status running
        for s in "$@"; do
          case "$s" in odoo) echo aaaaaaaaaaaa ;; api-gateway) echo bbbbbbbbbbbb; echo cccccccccccc ;; esac
        done
      else
        shift 2   # --format json
        h=healthy; [[ -e $FK/unhealthy ]] && h=unhealthy
        for s in "$@"; do printf '{"Service":"%s","State":"running","Health":"%s"}\n' "$s" "$h"; done
      fi ;;
    up) printf '%s\n' "$*" >> $FK/up.log; printf '%s\n' "${files[@]}" >> $FK/files.log
        for s in /opt/efn/secrets/odoo_api_key_online_*.txt /opt/efn/secrets/odoo_api_key_qr_*.txt /opt/efn/secrets/odoo_api_key_booking_*.txt /opt/efn/secrets/odoo_api_key_promo_*.txt; do
          [[ -f "$s" ]] && cp "$s" "$FK/mounted.$(basename "$s" .txt)"
        done ;;
    *) exit 1 ;;
  esac
  exit 0
fi
[[ "$cmd" == exec ]] || exit 1
declare -A E=(); stdin_on=0
while [[ "$1" == -* ]]; do
  case "$1" in -i) stdin_on=1; shift ;; -e) E["${2%%=*}"]="${2#*=}"; shift 2 ;; *) exit 1 ;; esac
done
cid="$1"; shift
echo "$cid $*" | cut -c1-40 >> $FK/exec.log
if [[ "$cid" == aaaaaaaaaaaa ]]; then           # odoo shell
  cat > /dev/null
  [[ -e $FK/odoo-err ]] && { echo "EFN_ROT ERR $(cat $FK/odoo-err)"; echo "Traceback: kunci 0123456789abcdef0123456789abcdef01234567" >&2; exit 0; }
  login="${E[EFN_LOGIN]}"; echo "$login ${E[EFN_CH]:-}" >> $FK/odoo-ch.log
  [[ -e $FK/odoo-err.$login ]] && { echo "EFN_ROT ERR $(cat "$FK/odoo-err.$login")"; exit 0; }
  db=$FK/keys.db; [[ "$login" == efn_online_gl ]] || db=$FK/keys.$login.db
  touch $db
  case "${E[EFN_OP]}" in
    create)
      [[ -e $FK/fail-create ]] && { echo "EFN_ROT ERR ValidationError"; exit 0; }
      id=$(( $(wc -l < $db) + 1 )); key=$(od -An -tx1 -N20 /dev/urandom | tr -d ' \n')
      exp=$(( $(date +%s) + ${E[EFN_KEY_DAYS]} * 86400 ))
      echo "$id $exp ${E[EFN_KEY_NAME]} $key 0" >> $db; echo "$key" >> /tmp/allkeys
      [[ -e $FK/fail-next-new ]] && { echo "$key" >> $FK/bad; rm -f $FK/fail-next-new; }
      # SEC-120: key sudah di-commit, tetapi keluaran rusak / galat setelah commit
      [[ -e $FK/garbled-new ]] && { echo "EFN_ROT NEW $id $exp ${key:0:39}"; rm -f $FK/garbled-new; echo "EFN_ROT OK"; exit 0; }
      [[ -e $FK/err-after-commit ]] && { echo "EFN_ROT ERR OperationalError"; rm -f $FK/err-after-commit; exit 0; }
      echo "EFN_ROT NEW $id $exp $key" ;;
    revoke)
      while read -r id exp name key rv; do
        if [[ "$rv" == 0 ]] && { [[ -n "${E[EFN_REVOKE_NAME]:-}" && "$name" == "${E[EFN_REVOKE_NAME]}" && "$id" != "${E[EFN_KEEP_ID]:-0}" ]] ||
                                 [[ -z "${E[EFN_REVOKE_NAME]:-}" && -n "${E[EFN_REVOKE_ID]:-}" && "$id" == "${E[EFN_REVOKE_ID]}" ]] ||
                                 [[ -z "${E[EFN_REVOKE_NAME]:-}${E[EFN_REVOKE_ID]:-}" && "$id" != "${E[EFN_KEEP_ID]}" ]]; }; then
          echo "EFN_ROT REVOKED $id $name"; [[ -e $FK/revoke-ineffective ]] || rv=1
        fi
        echo "$id $exp $name $key $rv"
      done < $db > $db.new
      grep '^EFN_ROT' $db.new; grep -v '^EFN_ROT' $db.new > $db; rm -f $db.new ;;
  esac
  while read -r id exp name key rv; do
    [[ "$rv" == 0 ]] && echo "EFN_ROT KEY $id $exp $name"
  done < $db
  echo "EFN_ROT OK"; exit 0
fi
# BFF: node -e <js> -> status gateway. G22: nama secret & path RPC dari env skrip (EFN_SECRET, EFN_RPC_PATH);
# key sah -> 200, kecuali path QR efn_qr_status (UserError config_id 0 -> 422) atau penanda force-422.
lc=$(tr 'A-Z-' 'a-z_' <<< "${E[EFN_CH]:-GL}")
sec="${E[EFN_SECRET]:-odoo_api_key_online_$lc}"
echo "${E[EFN_CH]:-} ${E[EFN_RPC_PATH]:-} ${E[EFN_KEY_ENV]:-}" >> $FK/rpc.log
if [[ "${E[EFN_KEY_STDIN]:-}" == 1 ]]; then k=$(cat); else k=$(cat "$FK/mounted.$sec" 2> /dev/null); fi
st=401; deny=0
if [[ "$k" =~ ^[0-9a-f]{40}$ ]] && cat $FK/keys*.db 2> /dev/null | grep -q " $k 0$" && ! grep -qx "$k" $FK/bad 2> /dev/null; then
  st=200
  if [[ "${E[EFN_RPC_PATH]:-}" == */efn_qr_status || -e $FK/force-422 ]]; then st=422; fi
  # G23: outlet VERIFY-KEY tidak dikenal -> AccessError Odoo 403 (deny=0); penanda gateway-deny -> 403 dari gateway
  if [[ "${E[EFN_RPC_PATH]:-}" == */efn_booking_status || -e $FK/force-403 ]]; then st=403; fi
  if [[ -e $FK/gateway-deny ]]; then st=403; deny=1; fi
fi
echo "EFN_VERIFY $st deny=$deny"
EOF
cat > /usr/local/bin/logger <<'EOF'
#!/usr/bin/env bash
shift 3 2> /dev/null || true
printf '%s\n' "$*" >> /tmp/journal.log
printf '%s\n' "$*" >> /tmp/journal.all
EOF
chmod 0755 /usr/local/bin/docker /usr/local/bin/logger
conf() { printf '%s\n' VERIFY_ATTEMPTS=1 "$@" > /etc/efn/online-key-rotation.conf; chmod 0644 /etc/efn/online-key-rotation.conf; }
# journald palsu dapat ditulis user non-root (kasus 23); journal.all & allkeys = penjaga kebocoran lintas kasus
: > /tmp/journal.log; : > /tmp/journal.all; : > /tmp/allkeys; chmod 0666 /tmp/journal.log /tmp/journal.all

PASS=0; FAIL=0; RC=0; OUT=""
ok()  { echo "PASS $1"; PASS=$((PASS + 1)); }
bad() { echo "FAIL $1 :: rc=$RC :: $(tail -n3 <<< "$OUT" | tr '\n' ' ') :: $(tail -n4 /tmp/journal.log 2> /dev/null | tr '\n' '|')"; FAIL=$((FAIL + 1)); }
run() { set +e; OUT=$(env -i PATH=/usr/bin:/bin "$@" 2>&1); RC=$?; set -e; ALL_OUT+="$OUT"$'\n'; }
rot() { run "$W" --channel=GL "$@"; }
fresh() { rm -rf /tmp/fk /var/lib/efn/online-key /var/lib/efn/node-exporter/* /opt/efn/compose/.env; mkdir -p /tmp/fk; : > /tmp/fk/keys.db
  for s in "$SECRET" "$SEC_PB" "$SEC_BCF" "$SEC_QRPB" "$SEC_BKPB" "$SEC_PRPB"; do printf 'disabled' > "$s"; chmod 0444 "$s"; done
  : > /tmp/journal.log; conf OVERLAP_SECONDS=0; }
active() { awk '$5 == 0' /tmp/fk/keys.db | wc -l; }
key_of() { awk -v i="$1" '$1 == i { print $4 }' /tmp/fk/keys.db; }
metric() { sed -nE "s/^efn_odoo_api_key_$1\{channel=\"GL\"\} ([0-9]+)$/\1/p" /var/lib/efn/node-exporter/efn_odoo_api_key.prom; }
ALL_OUT=""

# ── 1. Pemasangan pertama (placeholder, tanpa state) ──────────────────────────────────────────────────────────
fresh; rot
if [[ $RC -eq 0 && "$(cat "$SECRET")" == "$(key_of 1)" && "$(active)" == 1 ]]; then ok "01 bootstrap: key baru id 1 terpasang"; else bad "01 bootstrap"; fi
if [[ "$(stat -c '%a %U %G' "$SECRET")" == "444 root efn-superadmin" ]]; then ok "02 file secret 0444 root:efn-superadmin (pola deploy.sh)"; else bad "02 mode $(stat -c '%a %U %G' "$SECRET")"; fi
if grep -q 'api-gateway' /tmp/fk/up.log && grep -q 'bff-tls' /tmp/fk/up.log && ! grep -q 'superapp-web\|odoo' /tmp/fk/up.log && grep -q -- '--force-recreate' /tmp/fk/up.log; then
  ok "03 recreate hanya pemakai secret + sidecar network_mode"; else OUT=$(cat /tmp/fk/up.log); bad "03 recreate"; fi
if grep -q "VERIFIKASI OK kanal=GL container=bbbbbbbbbbbb" /tmp/journal.log && grep -q "VERIFIKASI OK kanal=GL container=cccccccccccc" /tmp/journal.log; then ok "04 verifikasi json2 di KEDUA replika BFF"; else bad "04 verifikasi replika"; fi
if [[ "$(metric rotation_last_run_ok)" == 1 && -n "$(metric expiry_timestamp_seconds)" && "$(metric active_keys)" == 1 ]] \
   && grep -q '^# TYPE efn_odoo_api_key_expiry_timestamp_seconds gauge' /var/lib/efn/node-exporter/efn_odoo_api_key.prom; then
  ok "05 metrik textfile (expiry, active_keys, last_run_ok)"; else OUT=$(cat /var/lib/efn/node-exporter/efn_odoo_api_key.prom); bad "05 metrik"; fi
exp1=$(metric expiry_timestamp_seconds); now=$(date +%s)
if (( exp1 - now > 29 * 86400 && exp1 - now <= 30 * 86400 )); then ok "06 masa berlaku default 30 hari"; else bad "06 masa berlaku"; fi
if grep -q 'KEY BARU kanal=GL id=1 nama=efn-online-GL-' /tmp/journal.log && grep -q 'SELESAI kanal=GL aktor=root mode=rotate rc=0' /tmp/journal.log \
   && grep -qx 'efn_online_gl GL' /tmp/fk/odoo-ch.log; then
  ok "07 journald: id+nama+hasil; user teknis efn_online_gl (G20)"; else bad "07 journald"; fi

# ── 2. Run harian berikutnya: tidak perlu ─────────────────────────────────────────────────────────────────────
: > /tmp/journal.log; rot
if [[ $RC -eq 0 && "$(wc -l < /tmp/fk/keys.db)" == 1 ]] && grep -q 'TIDAK PERLU kanal=GL id=1' /tmp/journal.log; then ok "08 sisa > 10 hari -> tidak dirotasi"; else bad "08 tidak perlu"; fi

# ── 3. --force, overlap 0: key lama dicabut + dibuktikan ditolak ──────────────────────────────────────────────
: > /tmp/journal.log; OLD=$(key_of 1); rot --force
if [[ $RC -eq 0 && "$(cat "$SECRET")" == "$(key_of 2)" && "$(active)" == 1 ]] && awk '$1==1 {exit !($5==1)}' /tmp/fk/keys.db; then
  ok "09 --force: key 2 terpasang, key 1 dicabut"; else bad "09 force"; fi
if grep -q 'KEY LAMA DITOLAK kanal=GL status=401' /tmp/journal.log && [[ ! -e /var/lib/efn/online-key/gl.prev-key ]]; then
  ok "10 penolakan key lama terbukti (401) + salinan key lama dihancurkan"; else bad "10 bukti cabut"; fi
if [[ "$(metric active_keys)" == 1 ]]; then ok "11 active_keys kembali 1"; else bad "11 active_keys"; fi

# ── 4. Key baru ditolak gateway -> rollback ke key lama, key baru dicabut ────────────────────────────────────
: > /tmp/journal.log; OLD=$(cat "$SECRET"); touch /tmp/fk/fail-next-new; rot --force
if [[ $RC -eq 1 && "$(cat "$SECRET")" == "$OLD" ]] && grep -q 'ROLLBACK OK' /tmp/journal.log && awk '$1==3 {exit !($5==1)}' /tmp/fk/keys.db \
   && [[ "$(active)" == 1 && "$(metric rotation_last_run_ok)" == 0 ]]; then
  ok "12 verifikasi gagal -> rollback, key baru dicabut, metrik last_run_ok 0"; else bad "12 rollback"; fi
if [[ "$(stat -c '%a %G' "$SECRET")" == "444 efn-superadmin" && ! -e /var/lib/efn/online-key/gl.rollback-key ]]; then
  ok "13 file secret pulih dengan mode sama; salinan rollback dihancurkan"; else bad "13 pasca rollback"; fi
: > /tmp/journal.log; rot
if [[ $RC -eq 0 ]] && grep -q 'TIDAK PERLU kanal=GL id=2' /tmp/journal.log; then ok "14 state tetap menunjuk key lama (id 2) setelah rollback"; else bad "14 state"; fi

# ── 5. BFF tidak sehat setelah recreate dan tetap tidak sehat -> ROLLBACK GAGAL (rc 3) ─────────────────────────
: > /tmp/journal.log; conf OVERLAP_SECONDS=0 HEALTH_TIMEOUT_SECONDS=30; touch /tmp/fk/unhealthy; rot --force; rm -f /tmp/fk/unhealthy
if [[ $RC -eq 3 ]] && grep -q 'ROLLBACK GAGAL kanal=GL' /tmp/journal.log && grep -q 'DICABUT kanal=GL id=4' /tmp/journal.log; then
  ok "15 tidak sehat -> rc 3 ROLLBACK GAGAL, key baru tetap dicabut"; else bad "15 rollback gagal"; fi
conf OVERLAP_SECONDS=0

# ── 6. Odoo menolak pembuatan (KEY_DAYS > api_key_duration) -> rc 1, secret tidak berubah ─────────────────────
: > /tmp/journal.log; BEFORE=$(cat "$SECRET"); touch /tmp/fk/fail-create; rot --force; rm -f /tmp/fk/fail-create
if [[ $RC -eq 1 && "$(cat "$SECRET")" == "$BEFORE" ]] && grep -q 'GAGAL: key baru tidak dibuat' /tmp/journal.log; then
  ok "16 pembuatan key gagal -> secret tidak disentuh"; else bad "16 create gagal"; fi

# ── 7. Pencabutan tidak efektif (key lama masih diterima) -> GAGAL ────────────────────────────────────────────
: > /tmp/journal.log; touch /tmp/fk/revoke-ineffective; rot --force; rm -f /tmp/fk/revoke-ineffective
if [[ $RC -eq 1 ]] && grep -q 'GAGAL: key lama masih dijawab status=200' /tmp/journal.log; then ok "17 key lama masih diterima setelah dicabut -> GAGAL"; else bad "17 cabut tidak efektif"; fi

# ── 8. Cabut tertunda (proses mati saat overlap) diselesaikan run berikutnya ─────────────────────────────────
fresh; rot; conf OVERLAP_SECONDS=3600
echo "2 $(( $(date +%s) + 86400 * 30 )) efn-online-GL-manual $(od -An -tx1 -N20 /dev/urandom | tr -d ' \n') 0" >> /tmp/fk/keys.db
printf 'CURRENT_ID=1\nPENDING_AFTER=%s\n' "$(( $(date +%s) - 10 ))" > /var/lib/efn/online-key/gl.state
: > /tmp/journal.log; rot
if [[ $RC -eq 0 && "$(active)" == 1 ]] && awk '$1==2 {exit !($5==1)}' /tmp/fk/keys.db && grep -q 'cabut tertunda' /tmp/journal.log; then
  ok "18 cabut tertunda setelah overlap diselesaikan run harian"; else bad "18 tertunda"; fi
printf 'CURRENT_ID=1\nPENDING_AFTER=%s\n' "$(( $(date +%s) + 3000 ))" > /var/lib/efn/online-key/gl.state
echo "3 $(( $(date +%s) + 86400 * 30 )) efn-online-GL-x $(od -An -tx1 -N20 /dev/urandom | tr -d ' \n') 0" >> /tmp/fk/keys.db
: > /tmp/journal.log; rot
if [[ $RC -eq 0 && "$(active)" == 2 && "$(metric active_keys)" == 2 ]]; then ok "19 overlap belum lewat -> key lama belum dicabut (active_keys 2)"; else bad "19 overlap"; fi
conf OVERLAP_SECONDS=0

# ── 9. Sisa masa berlaku < ROTATE_BEFORE_DAYS -> rotasi; key tanpa kedaluwarsa -> rotasi ──────────────────────
fresh; rot; awk '{ $2 = '"$(( $(date +%s) + 5 * 86400 ))"'; print }' /tmp/fk/keys.db > /tmp/fk/k && mv /tmp/fk/k /tmp/fk/keys.db
: > /tmp/journal.log; rot
if [[ $RC -eq 0 && "$(cat "$SECRET")" == "$(key_of 2)" ]] && grep -qE 'alasan=sisa [45] hari' /tmp/journal.log; then ok "20 sisa 5 hari -> dirotasi"; else bad "20 sisa"; fi
awk '$1==2 { $2 = 0 } { print }' /tmp/fk/keys.db > /tmp/fk/k && mv /tmp/fk/k /tmp/fk/keys.db
: > /tmp/journal.log; rot
if [[ $RC -eq 0 ]] && grep -q 'tanpa masa berlaku' /tmp/journal.log; then ok "21 key tanpa kedaluwarsa -> dirotasi"; else bad "21 tanpa exp"; fi

# ── 10. --status: daftar tanpa nilai key ─────────────────────────────────────────────────────────────────────
rot --status
if [[ $RC -eq 0 ]] && grep -q 'terpasang (state)' <<< "$OUT" && ! grep -qE '[0-9a-f]{40}' <<< "$OUT"; then ok "22 --status tanpa nilai key"; else bad "22 status"; fi

# ── 11. Penolakan identitas / argumen / konfigurasi ──────────────────────────────────────────────────────────
expect_deny() { if [[ $RC -eq 2 ]] && grep -q -- "$2" /tmp/journal.log; then ok "$1"; else bad "$1"; fi; : > /tmp/journal.log; }
: > /tmp/journal.log
run su -s /bin/bash ops1 -c "$W --channel=GL";                                          expect_deny "23 non-root ditolak" "harus root"
run env SUDO_USER=ops1 SUDO_UID="$(id -u ops1)" "$W" --channel=GL;                      expect_deny "24 sudo bukan efn-superadmin ditolak" "bukan anggota efn-superadmin"
run env SUDO_USER=sa1 SUDO_UID="$(id -u ops1)" "$W" --channel=GL;                       expect_deny "25 SUDO_UID tidak konsisten ditolak" "tidak konsisten"
run env SUDO_USER=sa1 SUDO_UID="$(id -u sa1)" "$W" --channel=GL --status
if [[ $RC -eq 0 ]] && grep -q 'aktor=sa1' /tmp/journal.log; then ok "26 Superadmin lewat sudo diterima (aktor dicatat)"; else bad "26 sudo sa1"; fi
: > /tmp/journal.log
rot --channel=PB;                        expect_deny "27 --channel ganda ditolak" "ganda"
run "$W" --channel=gl;                   expect_deny "28 kanal huruf kecil ditolak" "channel"
run "$W" --channel='GL;id';              expect_deny "29 kanal berkarakter aneh ditolak" "channel"
rot --revoke-all;                        expect_deny "30 flag tak dikenal ditolak" "tidak dikenal"
rot --overlap-seconds=99999;             expect_deny "31 overlap > 86400 ditolak" "overlap"
rot --status --force;                    expect_deny "32 --status + --force ditolak" "tidak bisa digabung"
run "$W";                                expect_deny "33 tanpa --channel ditolak" "channel wajib"
run "$W" --channel=BCR;                  expect_deny "34 kanal tanpa secret compose ditolak" "tidak didefinisikan compose"
conf OVERLAP_SECONDS=0 'KEY_DAYS=30;id'; rot;  expect_deny "35 nilai konfigurasi aneh ditolak" "baris tidak valid"
conf EVIL=1; rot;                        expect_deny "36 kunci konfigurasi tak dikenal ditolak" "tidak dikenal"
conf KEY_DAYS=10 ROTATE_BEFORE_DAYS=10; rot; expect_deny "37 ROTATE_BEFORE_DAYS >= KEY_DAYS ditolak" "ROTATE_BEFORE_DAYS"
conf COMPOSE_FILES=../../etc/passwd; rot;    expect_deny "38 COMPOSE_FILES di luar pola ditolak" "file compose tidak valid"
conf OVERLAP_SECONDS=0; chmod 0664 /etc/efn/online-key-rotation.conf; rot; expect_deny "39 konfigurasi dapat ditulis grup ditolak" "tidak dapat ditulis"
conf OVERLAP_SECONDS=0
echo user_missing > /tmp/fk/odoo-err; : > /tmp/journal.log; rot; rm -f /tmp/fk/odoo-err
if [[ $RC -eq 1 ]] && grep -q 'odoo list: user_missing' /tmp/journal.log && ! grep -q '0123456789abcdef0123' /tmp/journal.log \
   && grep -q '\[REDACTED\]' /tmp/journal.log; then ok "40 user teknis tidak ada -> rc 1; stderr Odoo disaring (hex panjang di-redaksi)"; else bad "40 user missing"; fi
exec 8> /var/lib/efn/online-key/.lock.gl; flock -n 8; rot; exec 8>&-
if [[ $RC -eq 2 ]] && grep -q 'sedang berjalan' /tmp/journal.log; then ok "41 run paralel kanal sama ditolak (flock)"; else bad "41 flock"; fi

# ── 11b. SEC-120 (L-25h): key yatim dicabut per nama; kunci host bersama deploy ──────────────────────────────
fresh; rot; BEFORE=$(cat "$SECRET")
: > /tmp/journal.log; touch /tmp/fk/garbled-new; rot --force
if [[ $RC -eq 1 && "$(cat "$SECRET")" == "$BEFORE" && "$(active)" == 1 ]] && awk '$1==2 {exit !($5==1)}' /tmp/fk/keys.db \
   && grep -q 'DICABUT kanal=GL nama=efn-online-GL-.*keluaran tidak dikenali' /tmp/journal.log; then
  ok "43 keluaran NEW rusak -> key run ini dicabut per nama, secret tidak disentuh (SEC-120)"; else bad "43 key yatim parse"; fi
: > /tmp/journal.log; touch /tmp/fk/err-after-commit; rot --force
if [[ $RC -eq 1 && "$(cat "$SECRET")" == "$BEFORE" && "$(active)" == 1 ]] && awk '$1==3 {exit !($5==1)}' /tmp/fk/keys.db \
   && grep -q 'DICABUT kanal=GL nama=efn-online-GL-.*create gagal' /tmp/journal.log; then
  ok "44 galat setelah commit -> key run ini dicabut per nama (SEC-120)"; else bad "44 key yatim err"; fi
install -d -m 0700 /run/efn; exec 6> /run/efn/deploy.lock; flock -n 6
conf OVERLAP_SECONDS=0 HOST_LOCK_WAIT_SECONDS=10; N0=$(wc -l < /tmp/fk/keys.db); : > /tmp/journal.log; rot --force
if [[ $RC -eq 1 && "$(wc -l < /tmp/fk/keys.db)" == "$N0" ]] && grep -q 'kunci host /run/efn/deploy.lock dipegang deploy' /tmp/journal.log \
   && [[ "$(metric rotation_last_run_ok)" == 0 ]]; then
  ok "45 deploy memegang kunci host -> rotasi menunggu lalu gagal TANPA membuat key (SEC-120)"; else bad "45 kunci host"; fi
echo "9 $(( $(date +%s) + 86400 * 30 )) efn-online-GL-x $(od -An -tx1 -N20 /dev/urandom | tr -d ' \n') 0" >> /tmp/fk/keys.db
sed -i 's/^PENDING_AFTER=.*/PENDING_AFTER=1/' /var/lib/efn/online-key/gl.state
: > /tmp/journal.log; rot
if [[ $RC -eq 1 ]] && awk '$1==9 {exit !($5==0)}' /tmp/fk/keys.db && grep -q 'dipegang deploy' /tmp/journal.log; then
  ok "46 cabut tertunda juga menunggu kunci host (tidak mencabut saat deploy)"; else bad "46 tertunda + kunci"; fi
exec 6>&-
: > /tmp/journal.log; rot
if [[ $RC -eq 0 ]] && awk '$1==9 {exit !($5==1)}' /tmp/fk/keys.db; then ok "47 kunci dilepas -> cabut tertunda selesai"; else bad "47 setelah kunci"; fi
: > /tmp/journal.log; rot --force
if [[ $RC -eq 0 ]] && [[ ! -e /proc/$$/fd/6 ]] && flock -n /run/efn/deploy.lock true; then
  ok "48 rotasi sukses melepas kunci host (deploy berikutnya tidak tertahan)"; else bad "48 kunci tidak dilepas"; fi
conf OVERLAP_SECONDS=0

# ── 11c. G19/G20: kanal overlay, allowlist kode, --all, --no-wait, --bootstrap, user teknis bersama ───────────────
PBDB=/tmp/fk/keys.efn_online_pb.db
BCFDB=/tmp/fk/keys.efn_online_bcf.db
active_in() { awk '$5 == 0' "$1" 2> /dev/null | wc -l; }
key_in() { awk -v i="$2" '$1 == i { print $4 }' "$1"; }
metric_ch() { sed -nE "s/^efn_odoo_api_key_$1\{channel=\"$2\"\} ([0-9]+)$/\1/p" /var/lib/efn/node-exporter/efn_odoo_api_key.prom; }
pending_of() { sed -nE 's/^PENDING_AFTER=([0-9]+)$/\1/p' "/var/lib/efn/online-key/$1.state"; }

fresh; run "$W" --channel=PB
if [[ $RC -eq 0 && "$(cat "$SEC_PB")" == "$(key_in "$PBDB" 1)" && "$(active_in "$PBDB")" == 1 && "$(cat "$SECRET")" == disabled ]] \
   && grep -q 'KEY BARU kanal=PB id=1 nama=efn-online-PB-' /tmp/journal.log && grep -qx 'efn_online_pb PB' /tmp/fk/odoo-ch.log; then
  ok "50 kanal overlay PB -> secret odoo_api_key_online_pb + user efn_online_pb; kanal GL tidak disentuh"
else bad "50 kanal overlay"; fi
if [[ -f /var/lib/efn/online-key/pb.state && "$(metric_ch active_keys PB)" == 1 && "$(metric_ch rotation_last_run_ok PB)" == 1 ]]; then
  ok "51 state & metrik per kanal (pb.state, label channel=\"PB\")"; else bad "51 state per kanal"; fi

: > /tmp/journal.log
run "$W" --channel=P_B;                  expect_deny "52 kode kanal ber-underscore ditolak" "channel"
run "$W" --channel=PB/../GL;             expect_deny "53 kode kanal path traversal ditolak" "channel"
# shellcheck disable=SC2016  # literal $(id) disengaja (uji injeksi)
run "$W" '--channel=PB$(id)';            expect_deny "54 kode kanal berisi substitusi shell ditolak" "channel"
run "$W" --channel=GL-ABCDEFGHIJKLMN;    expect_deny "55 kode kanal > 16 karakter ditolak" "channel"
# G20 (docs/04 §12.1): kode lama & kanal G19 berhifen tidak lagi dikelola (allowlist)
run "$W" --channel=RTL;                  expect_deny "55b kode lama RTL ditolak (G20)" "salah satu: GL PB BCR BCF"
run "$W" --channel=FNB;                  expect_deny "55c kode lama FNB ditolak (G20)" "salah satu: GL PB BCR BCF"
run "$W" --channel=RTL-GL;               expect_deny "55d kanal G19 RTL-GL ditolak (G20)" "salah satu: GL PB BCR BCF"
run "$W" --channel=FNB-BACHA --bootstrap; expect_deny "55e kanal G19 FNB-BACHA --bootstrap ditolak sebelum menyentuh compose" "salah satu"
run "$W" --all;                          expect_deny "56 --all tanpa CHANNELS dan tanpa .env ditolak" "daftar kanal kosong"
run "$W" --all --channel=GL;             expect_deny "57 --all + --channel ditolak" "hanya boleh digabung"
run "$W" --all --force;                  expect_deny "58 --all + --force ditolak" "hanya boleh digabung"
conf OVERLAP_SECONDS=0 CHANNELS=GL,../../etc; rot; expect_deny "59 CHANNELS berisi path ditolak" "CHANNELS"
conf OVERLAP_SECONDS=0 CHANNELS=GL,GL;        rot; expect_deny "60 CHANNELS ganda ditolak" "CHANNELS"
conf OVERLAP_SECONDS=0 CHANNELS=gl;           rot; expect_deny "61 CHANNELS huruf kecil ditolak" "CHANNELS"
conf OVERLAP_SECONDS=0 CHANNELS=GL,RTL-GL;    rot; expect_deny "61b CHANNELS memuat kanal G19 ditolak (G20)" "CHANNELS"

# --all: GL & PB (di-mount) + BCR (belum di-deploy -> anak gagal sebelum menulis metrik)
fresh; conf OVERLAP_SECONDS=2 CHANNELS=GL,PB,BCR; run "$W" --all
if [[ $RC -eq 1 && "$(cat "$SECRET")" == "$(key_of 1)" && "$(cat "$SEC_PB")" == "$(key_in "$PBDB" 1)" ]] \
   && grep -q 'HASIL --all kanal=GL argumen=--no-wait rc=0' /tmp/journal.log \
   && grep -q 'HASIL --all kanal=PB argumen=--no-wait rc=0' /tmp/journal.log \
   && grep -q 'HASIL --all kanal=BCR argumen=--no-wait rc=2' /tmp/journal.log; then
  ok "62 --all: GL + PB dipasang; BCR gagal TANPA menghentikan kanal lain; rc 1"; else bad "62 --all"; fi
if [[ "$(metric_ch rotation_last_run_ok BCR)" == 0 && "$(metric_ch rotation_last_run_ok GL)" == 1 \
      && "$(metric_ch rotation_last_run_ok PB)" == 1 ]]; then
  ok "63 metrik per kanal: BCR last_run_ok 0 walau anak gagal sebelum menulis metrik (alert per kanal)"
else OUT=$(cat /var/lib/efn/node-exporter/efn_odoo_api_key.prom); bad "63 metrik per kanal"; fi
awk '{ $2 = '"$(( $(date +%s) + 5 * 86400 ))"'; print }' "$PBDB" > /tmp/fk/k && mv /tmp/fk/k "$PBDB"
: > /tmp/journal.log; run "$W" --all
if [[ $RC -eq 1 && "$(cat "$SEC_PB")" == "$(key_in "$PBDB" 2)" && "$(active_in "$PBDB")" == 1 && "$(cat "$SECRET")" == "$(key_of 1)" ]] \
   && awk '$1==1 {exit !($5==1)}' "$PBDB" && grep -q 'TIDAK PERLU kanal=GL id=1' /tmp/journal.log \
   && grep -q -- '--all menunggu overlap' /tmp/journal.log && grep -q 'KEY LAMA DITOLAK kanal=PB status=401' /tmp/journal.log \
   && [[ "$(pending_of pb)" == 0 ]]; then
  ok "64 --all: hanya kanal yang perlu dirotasi; key lama PB dicabut setelah SATU overlap (401 terbukti)"
else bad "64 --all overlap"; fi
N_GL=$(wc -l < /tmp/fk/keys.db); N_PB=$(wc -l < "$PBDB"); : > /tmp/journal.log; run "$W" --all
if [[ $RC -eq 1 && "$(wc -l < /tmp/fk/keys.db)" == "$N_GL" && "$(wc -l < "$PBDB")" == "$N_PB" ]] && ! grep -q 'menunggu overlap' /tmp/journal.log; then
  ok "65 --all idempoten (run ulang: tidak ada key baru, tidak menunggu)"; else bad "65 idempoten"; fi
conf OVERLAP_SECONDS=2 CHANNELS=GL,PB; : > /tmp/journal.log; run "$W" --all
if [[ $RC -eq 0 ]] && grep -q 'SELESAI --all kanal=GL PB .* rc=0' /tmp/journal.log && ! grep -q 'kanal=BCR' /tmp/journal.log; then
  ok "66 kanal aktif = konfigurasi CHANNELS (BCR dikeluarkan -> tidak dijalankan, rc 0)"; else bad "66 CHANNELS"; fi
install -d -m 0700 /run/efn; exec 6> /run/efn/deploy.lock; flock -n 6
awk '{ $2 = '"$(( $(date +%s) + 5 * 86400 ))"'; print }' "$PBDB" > /tmp/fk/k && mv /tmp/fk/k "$PBDB"
conf OVERLAP_SECONDS=2 CHANNELS=GL,PB HOST_LOCK_WAIT_SECONDS=10; N_PB=$(wc -l < "$PBDB"); : > /tmp/journal.log; run "$W" --all
if [[ $RC -eq 1 && "$(wc -l < "$PBDB")" == "$N_PB" && "$(metric_ch rotation_last_run_ok PB)" == 0 && "$(metric_ch rotation_last_run_ok GL)" == 1 ]] \
   && grep -q 'dipegang deploy' /tmp/journal.log; then
  ok "67 --all memakai kunci host bersama deploy per kanal (PB menunggu lalu gagal tanpa key baru; GL tetap OK)"
else bad "67 --all kunci host"; fi
exec 6>&-
run "$W" --all --status
if [[ $RC -eq 0 ]] && grep -q '== kanal GL ==' <<< "$OUT" && grep -q '== kanal PB ==' <<< "$OUT" && ! grep -qE '[0-9a-f]{40}' <<< "$OUT"; then
  ok "68 --all --status: semua kanal, tanpa nilai key"; else bad "68 --all --status"; fi
# Fallback daftar kanal = ONLINE_CHANNELS hasil deploy (.env 0640 root)
conf OVERLAP_SECONDS=0; printf 'APP_ENV=production\nONLINE_CHANNELS=PB\n' > /opt/efn/compose/.env; chmod 0640 /opt/efn/compose/.env
: > /tmp/journal.log; run "$W" --all --status
if [[ $RC -eq 0 ]] && grep -q 'sumber=/opt/efn/compose/.env ONLINE_CHANNELS' /tmp/journal.log && grep -q '== kanal PB ==' <<< "$OUT" \
   && ! grep -q '== kanal GL ==' <<< "$OUT"; then
  ok "69 CHANNELS kosong -> kanal dari ONLINE_CHANNELS .env deploy"; else bad "69 fallback .env"; fi
chmod 0662 /opt/efn/compose/.env; run "$W" --all --status; expect_deny "70 .env dapat ditulis lainnya ditolak" "tidak dapat ditulis"
printf 'ONLINE_CHANNELS=GL,../x\n' > /opt/efn/compose/.env; chmod 0640 /opt/efn/compose/.env
run "$W" --all --status;                 expect_deny "71 ONLINE_CHANNELS .env tidak valid ditolak (tanpa fallback diam-diam)" "ONLINE_CHANNELS tidak valid"
printf 'ONLINE_CHANNELS=RTL\n' > /opt/efn/compose/.env; chmod 0640 /opt/efn/compose/.env
run "$W" --all --status;                 expect_deny "71b ONLINE_CHANNELS .env kode lama RTL ditolak (G20, tanpa fallback)" "ONLINE_CHANNELS tidak valid"
printf 'APP_ENV=production\n' > /opt/efn/compose/.env; chmod 0640 /opt/efn/compose/.env
: > /tmp/journal.log; run "$W" --all --status
if [[ $RC -eq 0 ]] && grep -q '== kanal GL ==' <<< "$OUT" && ! grep -q '== kanal PB ==' <<< "$OUT"; then
  ok "71c .env tanpa ONLINE_CHANNELS -> default GL (sama dengan deploy.sh)"; else bad "71c default GL"; fi
rm -f /opt/efn/compose/.env

# --no-wait (dipakai --all): rotasi tanpa sleep overlap, key lama tetap sah sampai cabut tertunda
conf OVERLAP_SECONDS=3600; : > /tmp/journal.log; rot --force --no-wait
if [[ $RC -eq 0 && "$(active)" == 2 && "$(pending_of gl)" -gt "$(date +%s)" ]] && grep -q -- '(--no-wait)' /tmp/journal.log; then
  ok "72 --no-wait: tanpa menunggu overlap, cabut tertunda tercatat di state"; else bad "72 --no-wait"; fi
conf OVERLAP_SECONDS=0
rot --status --no-wait;                  expect_deny "73 --status + --no-wait ditolak" "tidak bisa digabung"

# --bootstrap: key pertama kanal yang belum ada di ONLINE_CHANNELS (overlay ikut sementara)
run "$W" --channel=BCF;                  expect_deny "74 kanal belum di-deploy tanpa --bootstrap ditolak" "tidak didefinisikan compose"
: > /tmp/fk/files.log; run "$W" --channel=BCF --bootstrap
if [[ $RC -eq 0 && "$(cat "$SEC_BCF")" == "$(key_in "$BCFDB" 1)" ]] && grep -q 'docker-compose.online-bcf.yml' /tmp/fk/files.log \
   && grep -q 'VERIFIKASI OK kanal=BCF' /tmp/journal.log; then
  ok "75 --bootstrap: overlay kanal ikut di-mount sementara, key pertama terpasang & terverifikasi"; else bad "75 bootstrap"; fi
: > /tmp/journal.log
run "$W" --channel=BCF --bootstrap;      expect_deny "76 --bootstrap saat secret bukan placeholder ditolak" "hanya untuk key pertama"
run "$W" --channel=BCR --bootstrap;      expect_deny "77 --bootstrap tanpa file overlay kanal ditolak" "overlay"
# G24 (docs/04 §16.1, L-91..L-93): WTZ = kode allowlist (kanal online); belum di-deploy (ship OFF) & overlay test tidak ada
run "$W" --channel=WTZ;                  expect_deny "77b WTZ di allowlist tetapi belum di-deploy tanpa --bootstrap ditolak" "tidak didefinisikan compose"
run "$W" --channel=WTZ --bootstrap;      expect_deny "77c --bootstrap WTZ tanpa file overlay ditolak" "overlay"
run "$W" --channel=QR-WTZ;               expect_deny "77d QR-WTZ ditolak allowlist (L-92: WTZ tanpa QR meja)" "salah satu"
run "$W" --channel=BOOKING-WTZ;          expect_deny "77e BOOKING-WTZ ditolak allowlist (L-92: WTZ tanpa reservasi)" "salah satu"
conf OVERLAP_SECONDS=0 CHANNELS=GL,QR-WTZ; rot; expect_deny "77f CHANNELS memuat QR-WTZ ditolak (allowlist)" "CHANNELS"
conf OVERLAP_SECONDS=0 CHANNELS=GL,WTZ; rot --status
if [[ $RC -eq 0 ]]; then ok "77g CHANNELS=GL,WTZ diterima konfigurasi (WTZ kode allowlist G24)"; else bad "77g CHANNELS GL,WTZ"; fi
: > /tmp/journal.log; conf OVERLAP_SECONDS=0
# User teknis bersama beberapa kanal (data demo) -> Odoo shell menolak sebelum key dibuat (fail-closed)
echo user_shared > /tmp/fk/odoo-err.efn_online_pb; N_PB=$(wc -l < "$PBDB"); : > /tmp/journal.log; run "$W" --channel=PB --force
rm -f /tmp/fk/odoo-err.efn_online_pb
if [[ $RC -eq 1 && "$(wc -l < "$PBDB")" == "$N_PB" ]] && grep -q 'odoo list: user_shared' /tmp/journal.log; then
  ok "78 user_shared dari Odoo -> rc 1, tidak ada key dibuat"; else bad "78 user_shared"; fi

# ── 11d. G22 (docs/04 §14.A.5, L-72 D-22-7): API key user teknis QR meja per PT (QR-PB / QR-BCF) ─────────────────
QRDB=/tmp/fk/keys.efn_qr_pb.db
QR_FILES=docker-compose.yml,docker-compose.prod.yml,docker-compose.host.yml,docker-compose.online-qr-pb.yml
: > /tmp/journal.log
run "$W" --channel=QR-PB;                expect_deny "90 QR-PB belum di-deploy (overlay tidak di-mount) tanpa --bootstrap ditolak" "tidak didefinisikan compose"
run "$W" --channel=QR-GL;                expect_deny "91 kode QR untuk PT non-F&B (QR-GL) ditolak allowlist" "salah satu"
run "$W" --channel=QR-BCF --bootstrap;   expect_deny "92 --bootstrap QR-BCF tanpa file overlay ditolak" "overlay"
: > /tmp/fk/files.log; : > /tmp/fk/rpc.log; run "$W" --channel=QR-PB --bootstrap
if [[ $RC -eq 0 && "$(cat "$SEC_QRPB")" == "$(key_in "$QRDB" 1)" && "$(active_in "$QRDB")" == 1 ]] \
   && grep -q 'docker-compose.online-qr-pb.yml' /tmp/fk/files.log && grep -qx 'efn_qr_pb QR-PB' /tmp/fk/odoo-ch.log \
   && grep -q 'KEY BARU kanal=QR-PB id=1 nama=efn-qr-PB-' /tmp/journal.log \
   && grep -q 'VERIFIKASI OK kanal=QR-PB container=bbbbbbbbbbbb status=422' /tmp/journal.log; then
  ok "93 --bootstrap QR-PB: secret odoo_api_key_qr_pb + user efn_qr_pb, nama efn-qr-PB-*, verifikasi 422 = key sah"
else bad "93 bootstrap QR-PB"; fi
if grep -qx 'QR-PB /json/2/efn.pos.qr.order/efn_qr_status ODOO_API_KEY_QR_PB_FILE' /tmp/fk/rpc.log \
   && ! grep -q 'QR-PB /json/2/efn.online' /tmp/fk/rpc.log; then
  ok "94 verifikasi QR memakai path gateway #15 efn_qr_status + env ODOO_API_KEY_QR_PB_FILE (bukan path online)"
else OUT=$(cat /tmp/fk/rpc.log); bad "94 path verifikasi QR"; fi
if [[ -f /var/lib/efn/online-key/qr_pb.state && "$(metric_ch active_keys QR-PB)" == 1 && "$(metric_ch rotation_last_run_ok QR-PB)" == 1 ]]; then
  ok "95 state & metrik per kunci QR (qr_pb.state, channel=\"QR-PB\" -> alert EfnOnlineApiKey* per label)"; else bad "95 metrik QR"; fi
GL_BEFORE=$(cat "$SECRET"); PB_BEFORE=$(cat "$SEC_PB")
conf OVERLAP_SECONDS=0 "COMPOSE_FILES=${QR_FILES}"; : > /tmp/journal.log; run "$W" --channel=QR-PB --force
if [[ $RC -eq 0 && "$(cat "$SEC_QRPB")" == "$(key_in "$QRDB" 2)" && "$(active_in "$QRDB")" == 1 ]] && awk '$1==1 {exit !($5==1)}' "$QRDB" \
   && grep -q 'KEY LAMA DITOLAK kanal=QR-PB status=401' /tmp/journal.log \
   && [[ "$(cat "$SECRET")" == "$GL_BEFORE" && "$(cat "$SEC_PB")" == "$PB_BEFORE" ]]; then
  ok "96 rotasi QR-PB (overlay ter-deploy): key 2 terpasang, key 1 dicabut (401 terbukti), kanal GL tidak disentuh"
else bad "96 rotasi QR-PB"; fi
# 422 hanya diterima untuk QR: kanal online yang dijawab 422 = gagal -> rollback (bukan diam-diam lolos)
conf OVERLAP_SECONDS=0; rot; BEFORE=$(cat "$SECRET"); touch /tmp/fk/force-422; : > /tmp/journal.log; rot --force; rm -f /tmp/fk/force-422
if [[ $RC -eq 1 && "$(cat "$SECRET")" == "$BEFORE" ]] && grep -q 'VERIFIKASI GAGAL kanal=GL .*status=422' /tmp/journal.log; then
  ok "97 kanal online: status 422 BUKAN bukti key sah -> rollback (status_ok khusus QR)"; else bad "97 422 online"; fi
# --all: QR-PB ikut dari .env deploy (FNB_QR_ENABLED=true + FNB_QR_COMPANIES)
conf OVERLAP_SECONDS=0 "COMPOSE_FILES=${QR_FILES}"
printf 'APP_ENV=production\nONLINE_CHANNELS=GL\nFNB_QR_ENABLED=true\nFNB_QR_COMPANIES=PB\n' > /opt/efn/compose/.env; chmod 0640 /opt/efn/compose/.env
: > /tmp/journal.log; run "$W" --all --status
if [[ $RC -eq 0 ]] && grep -q '== kanal GL ==' <<< "$OUT" && grep -q '== kanal QR-PB ==' <<< "$OUT" \
   && grep -q 'sumber=/opt/efn/compose/.env ONLINE_CHANNELS + FNB_QR_COMPANIES' /tmp/journal.log && ! grep -qE '[0-9a-f]{40}' <<< "$OUT"; then
  ok "98 --all: FNB_QR_ENABLED=true di .env -> QR-PB ikut (tanpa nilai key)"; else bad "98 --all QR"; fi
printf 'APP_ENV=production\nONLINE_CHANNELS=GL\nFNB_QR_ENABLED=false\nFNB_QR_COMPANIES=PB\n' > /opt/efn/compose/.env; chmod 0640 /opt/efn/compose/.env
: > /tmp/journal.log; run "$W" --all --status
if [[ $RC -eq 0 ]] && ! grep -q '== kanal QR-PB ==' <<< "$OUT"; then ok "99 --all: FNB_QR_ENABLED=false -> QR tidak dirotasi"; else bad "99 QR mati"; fi
printf 'FNB_QR_ENABLED=true\nFNB_QR_COMPANIES=GL\n' > /opt/efn/compose/.env; chmod 0640 /opt/efn/compose/.env
run "$W" --all --status;                 expect_deny "100 FNB_QR_COMPANIES .env bukan PT F&B ditolak" "FNB_QR_COMPANIES tidak valid"
printf 'ONLINE_CHANNELS=GL,QR-PB\n' > /opt/efn/compose/.env; chmod 0640 /opt/efn/compose/.env
run "$W" --all --status;                 expect_deny "101 QR-* di ONLINE_CHANNELS ditolak" "ONLINE_CHANNELS tidak valid"
rm -f /opt/efn/compose/.env
conf OVERLAP_SECONDS=0

# ── 11e. G23 (docs/04 §15.3, L-85): API key user teknis reservasi meja per PT (BOOKING-PB / BOOKING-BCF) ────────────
BKDB=/tmp/fk/keys.efn_booking_pb.db
BK_FILES=docker-compose.yml,docker-compose.prod.yml,docker-compose.host.yml,docker-compose.online-booking-pb.yml
printf 'disabled' > "$SEC_BKPB"; chmod 0444 "$SEC_BKPB"
: > /tmp/journal.log
run "$W" --channel=BOOKING-PB;           expect_deny "111 BOOKING-PB belum di-deploy tanpa --bootstrap ditolak" "tidak didefinisikan compose"
run "$W" --channel=BOOKING-GL;           expect_deny "112 kode BOOKING untuk PT non-F&B (BOOKING-GL) ditolak allowlist" "salah satu"
run "$W" --channel=BOOKING-BCF --bootstrap; expect_deny "113 --bootstrap BOOKING-BCF tanpa file overlay ditolak" "overlay"
: > /tmp/fk/files.log; : > /tmp/fk/rpc.log; run "$W" --channel=BOOKING-PB --bootstrap
if [[ $RC -eq 0 && "$(cat "$SEC_BKPB")" == "$(key_in "$BKDB" 1)" && "$(active_in "$BKDB")" == 1 ]] \
   && grep -q 'docker-compose.online-booking-pb.yml' /tmp/fk/files.log && grep -qx 'efn_booking_pb BOOKING-PB' /tmp/fk/odoo-ch.log \
   && grep -q 'KEY BARU kanal=BOOKING-PB id=1 nama=efn-booking-PB-' /tmp/journal.log \
   && grep -q 'VERIFIKASI OK kanal=BOOKING-PB container=bbbbbbbbbbbb status=403' /tmp/journal.log; then
  ok "114 --bootstrap BOOKING-PB: secret odoo_api_key_booking_pb + user efn_booking_pb, verifikasi 403 Odoo = key sah"
else bad "114 bootstrap BOOKING-PB"; fi
if grep -qx 'BOOKING-PB /json/2/efn.table.booking/efn_booking_status ODOO_API_KEY_BOOKING_PB_FILE' /tmp/fk/rpc.log; then
  ok "115 verifikasi booking memakai path gateway #19 efn_booking_status + env ODOO_API_KEY_BOOKING_PB_FILE"
else OUT=$(cat /tmp/fk/rpc.log); bad "115 path verifikasi booking"; fi
if [[ -f /var/lib/efn/online-key/booking_pb.state && "$(metric_ch active_keys BOOKING-PB)" == 1 ]]; then
  ok "116 state & metrik per kunci booking (booking_pb.state, channel=\"BOOKING-PB\")"; else bad "116 metrik booking"; fi
conf OVERLAP_SECONDS=0 "COMPOSE_FILES=${BK_FILES}"; : > /tmp/journal.log; run "$W" --channel=BOOKING-PB --force
if [[ $RC -eq 0 && "$(cat "$SEC_BKPB")" == "$(key_in "$BKDB" 2)" && "$(active_in "$BKDB")" == 1 ]] \
   && grep -q 'KEY LAMA DITOLAK kanal=BOOKING-PB status=401' /tmp/journal.log; then
  ok "117 rotasi BOOKING-PB: key 2 terpasang, key 1 dicabut (401 terbukti)"
else bad "117 rotasi BOOKING-PB"; fi
# 403 dari GATEWAY (X-EFN-Gateway-Deny) bukan bukti key sah -> rollback
BEFORE=$(cat "$SEC_BKPB"); touch /tmp/fk/gateway-deny; : > /tmp/journal.log; run "$W" --channel=BOOKING-PB --force; rm -f /tmp/fk/gateway-deny
if [[ $RC -eq 1 && "$(cat "$SEC_BKPB")" == "$BEFORE" ]] && grep -q 'VERIFIKASI GAGAL kanal=BOOKING-PB .*status=000' /tmp/journal.log; then
  ok "118 booking: 403 gateway-deny BUKAN key sah -> rollback"; else bad "118 403 gateway booking"; fi
# 403 hanya diterima untuk booking: kanal online yang dijawab 403 = gagal -> rollback
conf OVERLAP_SECONDS=0; rot; BEFORE=$(cat "$SECRET"); touch /tmp/fk/force-403; : > /tmp/journal.log; rot --force; rm -f /tmp/fk/force-403
if [[ $RC -eq 1 && "$(cat "$SECRET")" == "$BEFORE" ]] && grep -q 'VERIFIKASI GAGAL kanal=GL .*status=403' /tmp/journal.log; then
  ok "119 kanal online: status 403 BUKAN bukti key sah -> rollback (status_ok khusus booking)"; else bad "119 403 online"; fi
conf OVERLAP_SECONDS=0 "COMPOSE_FILES=${BK_FILES}"
printf 'APP_ENV=production\nONLINE_CHANNELS=GL\nBOOKING_ENABLED=true\nBOOKING_COMPANIES=PB\n' > /opt/efn/compose/.env; chmod 0640 /opt/efn/compose/.env
: > /tmp/journal.log; run "$W" --all --status
if [[ $RC -eq 0 ]] && grep -q '== kanal BOOKING-PB ==' <<< "$OUT" \
   && grep -q 'sumber=/opt/efn/compose/.env ONLINE_CHANNELS + BOOKING_COMPANIES' /tmp/journal.log && ! grep -qE '[0-9a-f]{40}' <<< "$OUT"; then
  ok "120 --all: BOOKING_ENABLED=true di .env -> BOOKING-PB ikut (tanpa nilai key)"; else bad "120 --all booking"; fi
printf 'BOOKING_ENABLED=true\nBOOKING_COMPANIES=GL\n' > /opt/efn/compose/.env; chmod 0640 /opt/efn/compose/.env
run "$W" --all --status;                 expect_deny "121 BOOKING_COMPANIES .env bukan PT F&B ditolak" "BOOKING_COMPANIES tidak valid"
printf 'ONLINE_CHANNELS=GL,BOOKING-PB\n' > /opt/efn/compose/.env; chmod 0640 /opt/efn/compose/.env
run "$W" --all --status;                 expect_deny "122 BOOKING-* di ONLINE_CHANNELS ditolak" "ONLINE_CHANNELS tidak valid"
rm -f /opt/efn/compose/.env
conf OVERLAP_SECONDS=0

# ── 11f. G27 (docs/04 §18.2): API key user teknis promo Super-App per PT (PROMO-PB) ──────────────────────────────────
PRDB=/tmp/fk/keys.efn_promo_pb.db
PR_FILES=docker-compose.yml,docker-compose.prod.yml,docker-compose.host.yml,docker-compose.online-promo-pb.yml
printf 'disabled' > "$SEC_PRPB"; chmod 0444 "$SEC_PRPB"
: > /tmp/journal.log
run "$W" --channel=PROMO-PB;             expect_deny "127 PROMO-PB belum di-deploy tanpa --bootstrap ditolak" "tidak didefinisikan compose"
run "$W" --channel=PROMO-GL;             expect_deny "128 PROMO-GL (PT tanpa overlay promo) ditolak allowlist" "salah satu"
: > /tmp/fk/files.log; : > /tmp/fk/rpc.log; run "$W" --channel=PROMO-PB --bootstrap
if [[ $RC -eq 0 && "$(cat "$SEC_PRPB")" == "$(key_in "$PRDB" 1)" && "$(active_in "$PRDB")" == 1 ]] \
   && grep -q 'docker-compose.online-promo-pb.yml' /tmp/fk/files.log && grep -qx 'efn_promo_pb PROMO-PB' /tmp/fk/odoo-ch.log \
   && grep -q 'KEY BARU kanal=PROMO-PB id=1 nama=efn-promo-PB-' /tmp/journal.log \
   && grep -q 'VERIFIKASI OK kanal=PROMO-PB container=bbbbbbbbbbbb status=200' /tmp/journal.log; then
  ok "129 --bootstrap PROMO-PB: secret odoo_api_key_promo_pb + user efn_promo_pb, verifikasi 200"
else bad "129 bootstrap PROMO-PB"; fi
if grep -qx 'PROMO-PB /json/2/efn.promo.banner/efn_promo_feed ODOO_API_KEY_PROMO_PB_FILE' /tmp/fk/rpc.log; then
  ok "130 verifikasi promo memakai path gateway #22 efn_promo_feed + env ODOO_API_KEY_PROMO_PB_FILE"
else OUT=$(cat /tmp/fk/rpc.log); bad "130 path verifikasi promo"; fi
conf OVERLAP_SECONDS=0 "COMPOSE_FILES=${PR_FILES}"; : > /tmp/journal.log; run "$W" --channel=PROMO-PB --force
if [[ $RC -eq 0 && "$(cat "$SEC_PRPB")" == "$(key_in "$PRDB" 2)" && "$(active_in "$PRDB")" == 1 ]] \
   && grep -q 'KEY LAMA DITOLAK kanal=PROMO-PB status=401' /tmp/journal.log; then
  ok "131 rotasi PROMO-PB: key 2 terpasang, key 1 dicabut (401 terbukti)"
else bad "131 rotasi PROMO-PB"; fi
# 403 (Odoo maupun gateway) TIDAK pernah bukti key sah untuk promo -> rollback
BEFORE=$(cat "$SEC_PRPB"); touch /tmp/fk/force-403; : > /tmp/journal.log; run "$W" --channel=PROMO-PB --force; rm -f /tmp/fk/force-403
if [[ $RC -eq 1 && "$(cat "$SEC_PRPB")" == "$BEFORE" ]] && grep -q 'VERIFIKASI GAGAL kanal=PROMO-PB .*status=403' /tmp/journal.log; then
  ok "132 promo: status 403 BUKAN key sah -> rollback"; else bad "132 403 promo"; fi
printf 'APP_ENV=production\nONLINE_CHANNELS=GL\nPROMO_ENABLED=true\nPROMO_COMPANIES=PB\n' > /opt/efn/compose/.env; chmod 0640 /opt/efn/compose/.env
: > /tmp/journal.log; run "$W" --all --status
if [[ $RC -eq 0 ]] && grep -q '== kanal PROMO-PB ==' <<< "$OUT" \
   && grep -q 'sumber=/opt/efn/compose/.env ONLINE_CHANNELS + PROMO_COMPANIES' /tmp/journal.log && ! grep -qE '[0-9a-f]{40}' <<< "$OUT"; then
  ok "133 --all: PROMO_ENABLED=true di .env -> PROMO-PB ikut (tanpa nilai key)"; else bad "133 --all promo"; fi
printf 'PROMO_ENABLED=true\nPROMO_COMPANIES=GL\n' > /opt/efn/compose/.env; chmod 0640 /opt/efn/compose/.env
run "$W" --all --status;                 expect_deny "134 PROMO_COMPANIES .env di luar PB ditolak" "PROMO_COMPANIES tidak valid"
printf 'ONLINE_CHANNELS=GL,PROMO-PB\n' > /opt/efn/compose/.env; chmod 0640 /opt/efn/compose/.env
run "$W" --all --status;                 expect_deny "135 PROMO-* di ONLINE_CHANNELS ditolak" "ONLINE_CHANNELS tidak valid"
rm -f /opt/efn/compose/.env
conf OVERLAP_SECONDS=0

# Logika Python odoo shell (precheck G19) dijalankan NYATA terhadap env Odoo palsu (bukan hanya grep)
PYF=/tmp/efn-rot-py.py
sed -n "/^read -r -d '' PY << 'PYEOF'/,/^PYEOF\$/p" "$W" | sed '1d;$d' > "$PYF"
cat > /tmp/efn-fake-odoo.py <<'EOF'
import json, os, sys
SPEC = json.loads(os.environ["FAKE_SPEC"])
USERS = SPEC.get("users", {})
class O:
    def __init__(self, **kw):
        self.__dict__.update(kw)
class User(O):
    def __init__(self, **kw):
        super().__init__(**kw)
        # G22: company_id {"efn_brand_code": ".."} -> objek; company_ids = daftar id (len dipakai precheck QR)
        if isinstance(getattr(self, "company_id", None), dict):
            self.company_id = O(**self.company_id)
    def has_group(self, g):
        return g in self.groups
class Rec:
    def __init__(self, items):
        self._items = list(items)
    def __bool__(self):
        return bool(self._items)
    def __iter__(self):
        return iter(self._items)
    @property
    def ids(self):
        return [i.id for i in self._items]
    def mapped(self, f):
        return [getattr(i, f) for i in self._items]
    def __getattr__(self, name):
        items = self.__dict__.get("_items", [])
        if len(items) != 1:
            raise AttributeError(name)
        return getattr(items[0], name)
def uid(login):
    return USERS[login]["id"]
class Model:
    def __init__(self, name):
        self.name = name
    def sudo(self):
        return self
    def with_context(self, *a, **k):
        return self
    def search(self, dom, limit=None, order=None):
        if self.name == "res.users":
            u = USERS.get(dom[0][2])
            return Rec([User(login=dom[0][2], **u)] if u else [])
        if self.name == "efn.online.channel":
            want = set(dom[0][2])
            return Rec([O(code=c["code"]) for c in SPEC.get("channels", []) if want & {uid(x) for x in c["users"]}])
        if self.name == "res.users.apikeys":
            return Rec([O(id=k["id"], expiration_date=None, name=k["name"])
                        for k in SPEC.get("keys", []) if uid(k["user"]) == dom[0][2]])
        raise KeyError(self.name)
class Cr:
    def commit(self):
        pass
    def rollback(self):
        pass
class Env(dict):
    cr = Cr()
    def __missing__(self, key):
        return Model(key)
    # G22: env.ref(xmlid, raise_if_not_found=False) & `model in env` (modul terpasang) untuk precheck QR
    def ref(self, xmlid, raise_if_not_found=True):
        return xmlid not in SPEC.get("missing_xmlids", [])
    def __contains__(self, name):
        return name not in SPEC.get("missing_models", [])
with open(sys.argv[1], encoding="utf-8") as fh:
    code = compile(fh.read(), "efn-rotate-online-key:PY", "exec")
exec(code, {"env": Env(), "__name__": "efn_rot"})
EOF
PYOUT=""
pyrun() { set +e; PYOUT=$(env -i PATH=/usr/local/bin:/usr/bin:/bin EFN_OP=list EFN_LOGIN="$1" EFN_CH="$2" FAKE_SPEC="$3" \
            EFN_KIND="${4:-online}" EFN_PT="${5:-}" python3 -I /tmp/efn-fake-odoo.py "$PYF" 2>&1); set -e; }
pyok() { if grep -qx "$2" <<< "$PYOUT" && { [[ "$2" == 'EFN_ROT OK' ]] || ! grep -qx 'EFN_ROT OK' <<< "$PYOUT"; }; then ok "$1"
         else OUT="$PYOUT"; bad "$1"; fi; }
G='"groups":["efn_online_order.group_online_channel"]'
pyrun efn_online_pb PB '{"users":{"efn_online_pb":{"id":7,"active":true,'"$G"'}},
  "channels":[{"code":"PB","users":["efn_online_pb"]},{"code":"GL","users":[]}],
  "keys":[{"id":3,"user":"efn_online_pb","name":"efn-online-PB-x"}]}'
pyok "79 python: user khusus satu kanal -> daftar key + OK" 'EFN_ROT OK'
if grep -qx 'EFN_ROT KEY 3 0 efn-online-PB-x' <<< "$PYOUT"; then ok "80 python: baris KEY (id exp nama) sesuai format parser"; else OUT="$PYOUT"; bad "80 python KEY"; fi
pyrun efn_online_bcr BCR '{"users":{"efn_online_bcr":{"id":5,"active":true,'"$G"'}},
  "channels":[{"code":"BCR","users":["efn_online_bcr"]},{"code":"BCF","users":["efn_online_bcr"]}]}'
pyok "81 python: user teknis bersama BCR + BCF (pola data demo) -> ERR user_shared" 'EFN_ROT ERR user_shared'
pyrun efn_online_gl PB '{"users":{"efn_online_gl":{"id":5,"active":true,'"$G"'}},"channels":[{"code":"GL","users":["efn_online_gl"]}]}'
pyok "82 python: user tidak terhubung ke kanal -> ERR user_not_in_channel" 'EFN_ROT ERR user_not_in_channel'
pyrun efn_online_x GL '{"users":{}}'
pyok "83 python: user tidak ada -> ERR user_missing" 'EFN_ROT ERR user_missing'
pyrun efn_online_gl GL '{"users":{"efn_online_gl":{"id":5,"active":true,"groups":["efn_online_order.group_online_channel","base.group_system"]}},
  "channels":[{"code":"GL","users":["efn_online_gl"]}]}'
pyok "84 python: user teknis ber-hak admin -> ERR user_privileged" 'EFN_ROT ERR user_privileged'
# G22: precheck user teknis QR meja (grup efn_pos_bff_order.group_qr_bff, satu company = PT, tanpa POS/admin, bukan kanal)
QG='"groups":["efn_pos_bff_order.group_qr_bff"]'
QU='"active":true,"company_ids":[3],"company_id":{"efn_brand_code":"PB"}'
pyrun efn_qr_pb QR-PB '{"users":{"efn_qr_pb":{"id":9,'"$QU"','"$QG"'}},"channels":[{"code":"PB","users":[]}],
  "keys":[{"id":4,"user":"efn_qr_pb","name":"efn-qr-PB-x"}]}' qr PB
pyok "102 python QR: user teknis QR PB satu company -> daftar key + OK" 'EFN_ROT OK'
if grep -qx 'EFN_ROT KEY 4 0 efn-qr-PB-x' <<< "$PYOUT"; then ok "103 python QR: baris KEY sesuai format parser"; else OUT="$PYOUT"; bad "103 python QR KEY"; fi
pyrun efn_qr_pb QR-PB '{"users":{"efn_qr_pb":{"id":9,"active":true,"company_ids":[3,4],"company_id":{"efn_brand_code":"PB"},'"$QG"'}}}' qr PB
pyok "104 python QR: user teknis multi-company -> ERR user_company_mismatch" 'EFN_ROT ERR user_company_mismatch'
pyrun efn_qr_pb QR-BCF '{"users":{"efn_qr_pb":{"id":9,'"$QU"','"$QG"'}}}' qr BCF
pyok "105 python QR: company user (PB) != kode PT (BCF) -> ERR user_company_mismatch" 'EFN_ROT ERR user_company_mismatch'
pyrun efn_qr_pb QR-PB '{"users":{"efn_qr_pb":{"id":9,'"$QU"',"groups":["efn_pos_bff_order.group_qr_bff","point_of_sale.group_pos_user"]}}}' qr PB
pyok "106 python QR: user teknis ber-grup POS -> ERR user_privileged" 'EFN_ROT ERR user_privileged'
pyrun efn_qr_pb QR-PB '{"users":{"efn_qr_pb":{"id":9,'"$QU"','"$QG"'}},"channels":[{"code":"PB","users":["efn_qr_pb"]}]}' qr PB
pyok "107 python QR: user QR juga user kanal online -> ERR user_shared" 'EFN_ROT ERR user_shared'
pyrun efn_qr_pb QR-PB '{"users":{"efn_qr_pb":{"id":9,'"$QU"','"$QG"'}},"missing_xmlids":["efn_pos_bff_order.group_qr_bff"]}' qr PB
pyok "108 python QR: modul efn_pos_bff_order belum terpasang -> ERR group_missing" 'EFN_ROT ERR group_missing'
pyrun efn_qr_pb QR-PB '{"users":{"efn_qr_pb":{"id":9,'"$QU"',"groups":["efn_online_order.group_online_channel"]}}}' qr PB
pyok "109 python QR: user tanpa grup QR -> ERR user_not_qr" 'EFN_ROT ERR user_not_qr'
pyrun efn_qr_pb QR-PB '{"users":{"efn_qr_pb":{"id":9,'"$QU"','"$QG"'}},"missing_models":["efn.online.channel"]}' qr PB
pyok "110 python QR: tanpa modul kanal online -> cek user_shared dilewati, OK" 'EFN_ROT OK'
# G23: precheck user teknis reservasi (grup efn_table_booking.group_booking_bff, satu company = PT, tanpa POS/admin)
BG='"groups":["efn_table_booking.group_booking_bff"]'
pyrun efn_booking_bcf BOOKING-BCF '{"users":{"efn_booking_bcf":{"id":11,"active":true,"company_ids":[4],"company_id":{"efn_brand_code":"BCF"},'"$BG"'}},
  "keys":[{"id":6,"user":"efn_booking_bcf","name":"efn-booking-BCF-x"}]}' booking BCF
pyok "123 python booking: user teknis reservasi BCF satu company -> daftar key + OK" 'EFN_ROT OK'
pyrun efn_booking_pb BOOKING-PB '{"users":{"efn_booking_pb":{"id":12,'"$QU"','"$QG"'}}}' booking PB
pyok "124 python booking: user QR (bukan grup booking) -> ERR user_not_booking" 'EFN_ROT ERR user_not_booking'
pyrun efn_booking_pb BOOKING-PB '{"users":{"efn_booking_pb":{"id":12,'"$QU"',"groups":["efn_table_booking.group_booking_bff","point_of_sale.group_pos_manager","point_of_sale.group_pos_user"]}}}' booking PB
pyok "125 python booking: user teknis ber-grup POS -> ERR user_privileged" 'EFN_ROT ERR user_privileged'
pyrun efn_booking_pb BOOKING-PB '{"users":{"efn_booking_pb":{"id":12,'"$QU"','"$BG"'}},"missing_xmlids":["efn_table_booking.group_booking_bff"]}' booking PB
pyok "126 python booking: modul efn_table_booking belum terpasang -> ERR group_missing" 'EFN_ROT ERR group_missing'
# G27: precheck user teknis promo (grup efn_promo.group_promo_bff, satu company = PT, tanpa POS/admin)
PG='"groups":["efn_promo.group_promo_bff"]'
pyrun efn_promo_pb PROMO-PB '{"users":{"efn_promo_pb":{"id":13,'"$QU"','"$PG"'}},"keys":[{"id":7,"user":"efn_promo_pb","name":"efn-promo-PB-x"}]}' promo PB
pyok "136 python promo: user teknis promo PB satu company -> daftar key + OK" 'EFN_ROT OK'
pyrun efn_promo_pb PROMO-PB '{"users":{"efn_promo_pb":{"id":13,'"$QU"','"$BG"'}}}' promo PB
pyok "137 python promo: user booking (bukan grup promo) -> ERR user_not_promo" 'EFN_ROT ERR user_not_promo'
pyrun efn_promo_pb PROMO-PB '{"users":{"efn_promo_pb":{"id":13,'"$QU"',"groups":["efn_promo.group_promo_bff","base.group_system"]}}}' promo PB
pyok "138 python promo: user teknis admin -> ERR user_privileged" 'EFN_ROT ERR user_privileged'
conf OVERLAP_SECONDS=0

# ── 12. Penjaga independen: nilai key TIDAK PERNAH muncul di journald / keluaran ─────────────────────────────
leak=0
while read -r key; do
  grep -qF "$key" /tmp/journal.all && { echo "bocor: journal"; leak=1; }
  grep -qF "$key" <<< "$ALL_OUT" && { echo "bocor: keluaran"; leak=1; }
  grep -rqF "$key" /var/lib/efn/node-exporter /etc/efn && { echo "bocor: file"; leak=1; }
done < /tmp/allkeys
if (($(wc -l < /tmp/allkeys) >= 5 && leak == 0)); then ok "49 tidak ada nilai key ($(wc -l < /tmp/allkeys) key dibuat) di journald/stdout/stderr/metrik"; else bad "49 KEBOCORAN key"; fi

echo "RINGKASAN efn-rotate-online-key: PASS=$PASS FAIL=$FAIL"
[[ $FAIL -eq 0 ]]
