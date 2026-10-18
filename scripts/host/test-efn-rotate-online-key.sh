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
# G19 (kasus 50-84): kanal berhifen (RTL-GL -> secret/user/state rtl_gl), validasi kode kanal (traversal/injeksi),
# --all (CHANNELS / fallback ONLINE_CHANNELS .env, kegagalan per kanal + metrik, satu overlap, kunci host, idempoten),
# --no-wait, --bootstrap (overlay sementara), user teknis bersama ditolak; blok Python `odoo shell` dijalankan nyata
# terhadap env Odoo palsu (precheck user_shared / user_not_in_channel / user_missing / user_privileged).
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
# G19: overlay kanal brand untuk --bootstrap (RTL-BACHA sengaja TIDAK ada -> kasus overlay hilang)
echo "services: {}" > /opt/efn/compose/docker-compose.online-fnb-bacha.yml
SECRET=/opt/efn/secrets/odoo_api_key_online_rtl.txt
SEC_GL=/opt/efn/secrets/odoo_api_key_online_rtl_gl.txt
SEC_FB=/opt/efn/secrets/odoo_api_key_online_fnb_bacha.txt

# Docker palsu G19: secret RTL + RTL-GL di-mount api-gateway (deploy dengan ONLINE_CHANNELS=RTL,RTL-GL); FNB-BACHA hanya
# bila overlay docker-compose.online-fnb-bacha.yml ikut `-f` (--bootstrap); FNB-PB tidak pernah (kanal belum di-deploy).
# Key per user teknis: efn_online_rtl -> /tmp/fk/keys.db (kasus lama), lainnya -> /tmp/fk/keys.<login>.db.
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
  fb=0; for f in "${files[@]}"; do [[ "$f" == */docker-compose.online-fnb-bacha.yml ]] && fb=1; done
  case "$sub" in
    config)
      jq -nc --argjson fb "$fb" '{secrets:({odoo_api_key_online_rtl:{file:"/opt/efn/secrets/odoo_api_key_online_rtl.txt"},
                   odoo_api_key_online_rtl_gl:{file:"/opt/efn/secrets/odoo_api_key_online_rtl_gl.txt"}}
                 + (if $fb == 1 then {odoo_api_key_online_fnb_bacha:{file:"/opt/efn/secrets/odoo_api_key_online_fnb_bacha.txt"}} else {} end)),
        services:{"api-gateway":{secrets:([{source:"odoo_api_key_online_rtl"},{source:"odoo_api_key_online_rtl_gl"},{source:"efn_ca_crt"}]
                    + (if $fb == 1 then [{source:"odoo_api_key_online_fnb_bacha"}] else [] end))},
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
        for s in /opt/efn/secrets/odoo_api_key_online_*.txt; do cp "$s" "$FK/mounted.$(basename "$s" .txt)"; done ;;
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
  db=$FK/keys.db; [[ "$login" == efn_online_rtl ]] || db=$FK/keys.$login.db
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
# BFF: node -e <js> -> status gateway
lc=$(tr 'A-Z-' 'a-z_' <<< "${E[EFN_CH]:-RTL}")
if [[ "${E[EFN_KEY_STDIN]:-}" == 1 ]]; then k=$(cat); else k=$(cat "$FK/mounted.odoo_api_key_online_$lc" 2> /dev/null); fi
st=401
if [[ "$k" =~ ^[0-9a-f]{40}$ ]] && cat $FK/keys*.db 2> /dev/null | grep -q " $k 0$" && ! grep -qx "$k" $FK/bad 2> /dev/null; then st=200; fi
echo "EFN_VERIFY $st deny=0"
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
rot() { run "$W" --channel=RTL "$@"; }
fresh() { rm -rf /tmp/fk /var/lib/efn/online-key /var/lib/efn/node-exporter/* /opt/efn/compose/.env; mkdir -p /tmp/fk; : > /tmp/fk/keys.db
  for s in "$SECRET" "$SEC_GL" "$SEC_FB"; do printf 'disabled' > "$s"; chmod 0444 "$s"; done
  : > /tmp/journal.log; conf OVERLAP_SECONDS=0; }
active() { awk '$5 == 0' /tmp/fk/keys.db | wc -l; }
key_of() { awk -v i="$1" '$1 == i { print $4 }' /tmp/fk/keys.db; }
metric() { sed -nE "s/^efn_odoo_api_key_$1\{channel=\"RTL\"\} ([0-9]+)$/\1/p" /var/lib/efn/node-exporter/efn_odoo_api_key.prom; }
ALL_OUT=""

# ── 1. Pemasangan pertama (placeholder, tanpa state) ──────────────────────────────────────────────────────────
fresh; rot
if [[ $RC -eq 0 && "$(cat "$SECRET")" == "$(key_of 1)" && "$(active)" == 1 ]]; then ok "01 bootstrap: key baru id 1 terpasang"; else bad "01 bootstrap"; fi
if [[ "$(stat -c '%a %U %G' "$SECRET")" == "444 root efn-superadmin" ]]; then ok "02 file secret 0444 root:efn-superadmin (pola deploy.sh)"; else bad "02 mode $(stat -c '%a %U %G' "$SECRET")"; fi
if grep -q 'api-gateway' /tmp/fk/up.log && grep -q 'bff-tls' /tmp/fk/up.log && ! grep -q 'superapp-web\|odoo' /tmp/fk/up.log && grep -q -- '--force-recreate' /tmp/fk/up.log; then
  ok "03 recreate hanya pemakai secret + sidecar network_mode"; else OUT=$(cat /tmp/fk/up.log); bad "03 recreate"; fi
if grep -q "VERIFIKASI OK kanal=RTL container=bbbbbbbbbbbb" /tmp/journal.log && grep -q "VERIFIKASI OK kanal=RTL container=cccccccccccc" /tmp/journal.log; then ok "04 verifikasi json2 di KEDUA replika BFF"; else bad "04 verifikasi replika"; fi
if [[ "$(metric rotation_last_run_ok)" == 1 && -n "$(metric expiry_timestamp_seconds)" && "$(metric active_keys)" == 1 ]] \
   && grep -q '^# TYPE efn_odoo_api_key_expiry_timestamp_seconds gauge' /var/lib/efn/node-exporter/efn_odoo_api_key.prom; then
  ok "05 metrik textfile (expiry, active_keys, last_run_ok)"; else OUT=$(cat /var/lib/efn/node-exporter/efn_odoo_api_key.prom); bad "05 metrik"; fi
exp1=$(metric expiry_timestamp_seconds); now=$(date +%s)
if (( exp1 - now > 29 * 86400 && exp1 - now <= 30 * 86400 )); then ok "06 masa berlaku default 30 hari"; else bad "06 masa berlaku"; fi
if grep -q 'KEY BARU kanal=RTL id=1 nama=efn-online-RTL-' /tmp/journal.log && grep -q 'SELESAI kanal=RTL aktor=root mode=rotate rc=0' /tmp/journal.log; then
  ok "07 journald: id+nama+hasil"; else bad "07 journald"; fi

# ── 2. Run harian berikutnya: tidak perlu ─────────────────────────────────────────────────────────────────────
: > /tmp/journal.log; rot
if [[ $RC -eq 0 && "$(wc -l < /tmp/fk/keys.db)" == 1 ]] && grep -q 'TIDAK PERLU kanal=RTL id=1' /tmp/journal.log; then ok "08 sisa > 10 hari -> tidak dirotasi"; else bad "08 tidak perlu"; fi

# ── 3. --force, overlap 0: key lama dicabut + dibuktikan ditolak ──────────────────────────────────────────────
: > /tmp/journal.log; OLD=$(key_of 1); rot --force
if [[ $RC -eq 0 && "$(cat "$SECRET")" == "$(key_of 2)" && "$(active)" == 1 ]] && awk '$1==1 {exit !($5==1)}' /tmp/fk/keys.db; then
  ok "09 --force: key 2 terpasang, key 1 dicabut"; else bad "09 force"; fi
if grep -q 'KEY LAMA DITOLAK kanal=RTL status=401' /tmp/journal.log && [[ ! -e /var/lib/efn/online-key/rtl.prev-key ]]; then
  ok "10 penolakan key lama terbukti (401) + salinan key lama dihancurkan"; else bad "10 bukti cabut"; fi
if [[ "$(metric active_keys)" == 1 ]]; then ok "11 active_keys kembali 1"; else bad "11 active_keys"; fi

# ── 4. Key baru ditolak gateway -> rollback ke key lama, key baru dicabut ────────────────────────────────────
: > /tmp/journal.log; OLD=$(cat "$SECRET"); touch /tmp/fk/fail-next-new; rot --force
if [[ $RC -eq 1 && "$(cat "$SECRET")" == "$OLD" ]] && grep -q 'ROLLBACK OK' /tmp/journal.log && awk '$1==3 {exit !($5==1)}' /tmp/fk/keys.db \
   && [[ "$(active)" == 1 && "$(metric rotation_last_run_ok)" == 0 ]]; then
  ok "12 verifikasi gagal -> rollback, key baru dicabut, metrik last_run_ok 0"; else bad "12 rollback"; fi
if [[ "$(stat -c '%a %G' "$SECRET")" == "444 efn-superadmin" && ! -e /var/lib/efn/online-key/rtl.rollback-key ]]; then
  ok "13 file secret pulih dengan mode sama; salinan rollback dihancurkan"; else bad "13 pasca rollback"; fi
: > /tmp/journal.log; rot
if [[ $RC -eq 0 ]] && grep -q 'TIDAK PERLU kanal=RTL id=2' /tmp/journal.log; then ok "14 state tetap menunjuk key lama (id 2) setelah rollback"; else bad "14 state"; fi

# ── 5. BFF tidak sehat setelah recreate dan tetap tidak sehat -> ROLLBACK GAGAL (rc 3) ─────────────────────────
: > /tmp/journal.log; conf OVERLAP_SECONDS=0 HEALTH_TIMEOUT_SECONDS=30; touch /tmp/fk/unhealthy; rot --force; rm -f /tmp/fk/unhealthy
if [[ $RC -eq 3 ]] && grep -q 'ROLLBACK GAGAL kanal=RTL' /tmp/journal.log && grep -q 'DICABUT kanal=RTL id=4' /tmp/journal.log; then
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
echo "2 $(( $(date +%s) + 86400 * 30 )) efn-online-RTL-manual $(od -An -tx1 -N20 /dev/urandom | tr -d ' \n') 0" >> /tmp/fk/keys.db
printf 'CURRENT_ID=1\nPENDING_AFTER=%s\n' "$(( $(date +%s) - 10 ))" > /var/lib/efn/online-key/rtl.state
: > /tmp/journal.log; rot
if [[ $RC -eq 0 && "$(active)" == 1 ]] && awk '$1==2 {exit !($5==1)}' /tmp/fk/keys.db && grep -q 'cabut tertunda' /tmp/journal.log; then
  ok "18 cabut tertunda setelah overlap diselesaikan run harian"; else bad "18 tertunda"; fi
printf 'CURRENT_ID=1\nPENDING_AFTER=%s\n' "$(( $(date +%s) + 3000 ))" > /var/lib/efn/online-key/rtl.state
echo "3 $(( $(date +%s) + 86400 * 30 )) efn-online-RTL-x $(od -An -tx1 -N20 /dev/urandom | tr -d ' \n') 0" >> /tmp/fk/keys.db
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
run su -s /bin/bash ops1 -c "$W --channel=RTL";                                         expect_deny "23 non-root ditolak" "harus root"
run env SUDO_USER=ops1 SUDO_UID="$(id -u ops1)" "$W" --channel=RTL;                     expect_deny "24 sudo bukan efn-superadmin ditolak" "bukan anggota efn-superadmin"
run env SUDO_USER=sa1 SUDO_UID="$(id -u ops1)" "$W" --channel=RTL;                      expect_deny "25 SUDO_UID tidak konsisten ditolak" "tidak konsisten"
run env SUDO_USER=sa1 SUDO_UID="$(id -u sa1)" "$W" --channel=RTL --status
if [[ $RC -eq 0 ]] && grep -q 'aktor=sa1' /tmp/journal.log; then ok "26 Superadmin lewat sudo diterima (aktor dicatat)"; else bad "26 sudo sa1"; fi
: > /tmp/journal.log
rot --channel=FNB;                       expect_deny "27 --channel ganda ditolak" "ganda"
run "$W" --channel=rtl;                  expect_deny "28 kanal huruf kecil ditolak" "channel"
run "$W" --channel='RTL;id';             expect_deny "29 kanal berkarakter aneh ditolak" "channel"
rot --revoke-all;                        expect_deny "30 flag tak dikenal ditolak" "tidak dikenal"
rot --overlap-seconds=99999;             expect_deny "31 overlap > 86400 ditolak" "overlap"
rot --status --force;                    expect_deny "32 --status + --force ditolak" "tidak bisa digabung"
run "$W";                                expect_deny "33 tanpa --channel ditolak" "channel wajib"
run "$W" --channel=FNB;                  expect_deny "34 kanal tanpa secret compose ditolak" "tidak didefinisikan compose"
conf OVERLAP_SECONDS=0 'KEY_DAYS=30;id'; rot;  expect_deny "35 nilai konfigurasi aneh ditolak" "baris tidak valid"
conf EVIL=1; rot;                        expect_deny "36 kunci konfigurasi tak dikenal ditolak" "tidak dikenal"
conf KEY_DAYS=10 ROTATE_BEFORE_DAYS=10; rot; expect_deny "37 ROTATE_BEFORE_DAYS >= KEY_DAYS ditolak" "ROTATE_BEFORE_DAYS"
conf COMPOSE_FILES=../../etc/passwd; rot;    expect_deny "38 COMPOSE_FILES di luar pola ditolak" "file compose tidak valid"
conf OVERLAP_SECONDS=0; chmod 0664 /etc/efn/online-key-rotation.conf; rot; expect_deny "39 konfigurasi dapat ditulis grup ditolak" "tidak dapat ditulis"
conf OVERLAP_SECONDS=0
echo user_missing > /tmp/fk/odoo-err; : > /tmp/journal.log; rot; rm -f /tmp/fk/odoo-err
if [[ $RC -eq 1 ]] && grep -q 'odoo list: user_missing' /tmp/journal.log && ! grep -q '0123456789abcdef0123' /tmp/journal.log \
   && grep -q '\[REDACTED\]' /tmp/journal.log; then ok "40 user teknis tidak ada -> rc 1; stderr Odoo disaring (hex panjang di-redaksi)"; else bad "40 user missing"; fi
exec 8> /var/lib/efn/online-key/.lock.rtl; flock -n 8; rot; exec 8>&-
if [[ $RC -eq 2 ]] && grep -q 'sedang berjalan' /tmp/journal.log; then ok "41 run paralel kanal sama ditolak (flock)"; else bad "41 flock"; fi

# ── 11b. SEC-120 (L-25h): key yatim dicabut per nama; kunci host bersama deploy ──────────────────────────────
fresh; rot; BEFORE=$(cat "$SECRET")
: > /tmp/journal.log; touch /tmp/fk/garbled-new; rot --force
if [[ $RC -eq 1 && "$(cat "$SECRET")" == "$BEFORE" && "$(active)" == 1 ]] && awk '$1==2 {exit !($5==1)}' /tmp/fk/keys.db \
   && grep -q 'DICABUT kanal=RTL nama=efn-online-RTL-.*keluaran tidak dikenali' /tmp/journal.log; then
  ok "43 keluaran NEW rusak -> key run ini dicabut per nama, secret tidak disentuh (SEC-120)"; else bad "43 key yatim parse"; fi
: > /tmp/journal.log; touch /tmp/fk/err-after-commit; rot --force
if [[ $RC -eq 1 && "$(cat "$SECRET")" == "$BEFORE" && "$(active)" == 1 ]] && awk '$1==3 {exit !($5==1)}' /tmp/fk/keys.db \
   && grep -q 'DICABUT kanal=RTL nama=efn-online-RTL-.*create gagal' /tmp/journal.log; then
  ok "44 galat setelah commit -> key run ini dicabut per nama (SEC-120)"; else bad "44 key yatim err"; fi
install -d -m 0700 /run/efn; exec 6> /run/efn/deploy.lock; flock -n 6
conf OVERLAP_SECONDS=0 HOST_LOCK_WAIT_SECONDS=10; N0=$(wc -l < /tmp/fk/keys.db); : > /tmp/journal.log; rot --force
if [[ $RC -eq 1 && "$(wc -l < /tmp/fk/keys.db)" == "$N0" ]] && grep -q 'kunci host /run/efn/deploy.lock dipegang deploy' /tmp/journal.log \
   && [[ "$(metric rotation_last_run_ok)" == 0 ]]; then
  ok "45 deploy memegang kunci host -> rotasi menunggu lalu gagal TANPA membuat key (SEC-120)"; else bad "45 kunci host"; fi
echo "9 $(( $(date +%s) + 86400 * 30 )) efn-online-RTL-x $(od -An -tx1 -N20 /dev/urandom | tr -d ' \n') 0" >> /tmp/fk/keys.db
sed -i 's/^PENDING_AFTER=.*/PENDING_AFTER=1/' /var/lib/efn/online-key/rtl.state
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

# ── 11c. G19 (docs/04 §11.3): kanal berhifen, --all, --no-wait, --bootstrap, user teknis bersama ─────────────────
GLDB=/tmp/fk/keys.efn_online_rtl_gl.db
FBDB=/tmp/fk/keys.efn_online_fnb_bacha.db
active_in() { awk '$5 == 0' "$1" 2> /dev/null | wc -l; }
key_in() { awk -v i="$2" '$1 == i { print $4 }' "$1"; }
metric_ch() { sed -nE "s/^efn_odoo_api_key_$1\{channel=\"$2\"\} ([0-9]+)$/\1/p" /var/lib/efn/node-exporter/efn_odoo_api_key.prom; }
pending_of() { sed -nE 's/^PENDING_AFTER=([0-9]+)$/\1/p' "/var/lib/efn/online-key/$1.state"; }

fresh; run "$W" --channel=RTL-GL
if [[ $RC -eq 0 && "$(cat "$SEC_GL")" == "$(key_in "$GLDB" 1)" && "$(active_in "$GLDB")" == 1 && "$(cat "$SECRET")" == disabled ]] \
   && grep -q 'KEY BARU kanal=RTL-GL id=1 nama=efn-online-RTL-GL-' /tmp/journal.log && grep -qx 'efn_online_rtl_gl RTL-GL' /tmp/fk/odoo-ch.log; then
  ok "50 kanal berhifen RTL-GL -> secret odoo_api_key_online_rtl_gl + user efn_online_rtl_gl; kanal RTL tidak disentuh"
else bad "50 kanal berhifen"; fi
if [[ -f /var/lib/efn/online-key/rtl_gl.state && "$(metric_ch active_keys RTL-GL)" == 1 && "$(metric_ch rotation_last_run_ok RTL-GL)" == 1 ]]; then
  ok "51 state & metrik per kanal (rtl_gl.state, label channel=\"RTL-GL\")"; else bad "51 state per kanal"; fi

: > /tmp/journal.log
run "$W" --channel=RTL_GL;               expect_deny "52 kode kanal ber-underscore ditolak" "channel"
run "$W" --channel=RTL/../GL;            expect_deny "53 kode kanal path traversal ditolak" "channel"
# shellcheck disable=SC2016  # literal $(id) disengaja (uji injeksi)
run "$W" '--channel=RTL-GL$(id)';        expect_deny "54 kode kanal berisi substitusi shell ditolak" "channel"
run "$W" --channel=RTL-ABCDEFGHIJKLM;    expect_deny "55 kode kanal > 16 karakter ditolak" "channel"
run "$W" --all;                          expect_deny "56 --all tanpa CHANNELS dan tanpa .env ditolak" "daftar kanal kosong"
run "$W" --all --channel=RTL;            expect_deny "57 --all + --channel ditolak" "hanya boleh digabung"
run "$W" --all --force;                  expect_deny "58 --all + --force ditolak" "hanya boleh digabung"
conf OVERLAP_SECONDS=0 CHANNELS=RTL,../../etc; rot; expect_deny "59 CHANNELS berisi path ditolak" "CHANNELS"
conf OVERLAP_SECONDS=0 CHANNELS=RTL,RTL;       rot; expect_deny "60 CHANNELS ganda ditolak" "CHANNELS"
conf OVERLAP_SECONDS=0 CHANNELS=rtl;           rot; expect_deny "61 CHANNELS huruf kecil ditolak" "CHANNELS"

# --all: RTL & RTL-GL (di-mount) + FNB-PB (belum di-deploy -> anak gagal sebelum menulis metrik)
fresh; conf OVERLAP_SECONDS=2 CHANNELS=RTL,RTL-GL,FNB-PB; run "$W" --all
if [[ $RC -eq 1 && "$(cat "$SECRET")" == "$(key_of 1)" && "$(cat "$SEC_GL")" == "$(key_in "$GLDB" 1)" ]] \
   && grep -q 'HASIL --all kanal=RTL argumen=--no-wait rc=0' /tmp/journal.log \
   && grep -q 'HASIL --all kanal=RTL-GL argumen=--no-wait rc=0' /tmp/journal.log \
   && grep -q 'HASIL --all kanal=FNB-PB argumen=--no-wait rc=2' /tmp/journal.log; then
  ok "62 --all: RTL + RTL-GL dipasang; FNB-PB gagal TANPA menghentikan kanal lain; rc 1"; else bad "62 --all"; fi
if [[ "$(metric_ch rotation_last_run_ok FNB-PB)" == 0 && "$(metric_ch rotation_last_run_ok RTL)" == 1 \
      && "$(metric_ch rotation_last_run_ok RTL-GL)" == 1 ]]; then
  ok "63 metrik per kanal: FNB-PB last_run_ok 0 walau anak gagal sebelum menulis metrik (alert per kanal)"
else OUT=$(cat /var/lib/efn/node-exporter/efn_odoo_api_key.prom); bad "63 metrik per kanal"; fi
awk '{ $2 = '"$(( $(date +%s) + 5 * 86400 ))"'; print }' "$GLDB" > /tmp/fk/k && mv /tmp/fk/k "$GLDB"
: > /tmp/journal.log; run "$W" --all
if [[ $RC -eq 1 && "$(cat "$SEC_GL")" == "$(key_in "$GLDB" 2)" && "$(active_in "$GLDB")" == 1 && "$(cat "$SECRET")" == "$(key_of 1)" ]] \
   && awk '$1==1 {exit !($5==1)}' "$GLDB" && grep -q 'TIDAK PERLU kanal=RTL id=1' /tmp/journal.log \
   && grep -q -- '--all menunggu overlap' /tmp/journal.log && grep -q 'KEY LAMA DITOLAK kanal=RTL-GL status=401' /tmp/journal.log \
   && [[ "$(pending_of rtl_gl)" == 0 ]]; then
  ok "64 --all: hanya kanal yang perlu dirotasi; key lama RTL-GL dicabut setelah SATU overlap (401 terbukti)"
else bad "64 --all overlap"; fi
N_RTL=$(wc -l < /tmp/fk/keys.db); N_GL=$(wc -l < "$GLDB"); : > /tmp/journal.log; run "$W" --all
if [[ $RC -eq 1 && "$(wc -l < /tmp/fk/keys.db)" == "$N_RTL" && "$(wc -l < "$GLDB")" == "$N_GL" ]] && ! grep -q 'menunggu overlap' /tmp/journal.log; then
  ok "65 --all idempoten (run ulang: tidak ada key baru, tidak menunggu)"; else bad "65 idempoten"; fi
conf OVERLAP_SECONDS=2 CHANNELS=RTL,RTL-GL; : > /tmp/journal.log; run "$W" --all
if [[ $RC -eq 0 ]] && grep -q 'SELESAI --all kanal=RTL RTL-GL .* rc=0' /tmp/journal.log && ! grep -q 'kanal=FNB-PB' /tmp/journal.log; then
  ok "66 kanal aktif = konfigurasi CHANNELS (FNB-PB dikeluarkan -> tidak dijalankan, rc 0)"; else bad "66 CHANNELS"; fi
install -d -m 0700 /run/efn; exec 6> /run/efn/deploy.lock; flock -n 6
awk '{ $2 = '"$(( $(date +%s) + 5 * 86400 ))"'; print }' "$GLDB" > /tmp/fk/k && mv /tmp/fk/k "$GLDB"
conf OVERLAP_SECONDS=2 CHANNELS=RTL,RTL-GL HOST_LOCK_WAIT_SECONDS=10; N_GL=$(wc -l < "$GLDB"); : > /tmp/journal.log; run "$W" --all
if [[ $RC -eq 1 && "$(wc -l < "$GLDB")" == "$N_GL" && "$(metric_ch rotation_last_run_ok RTL-GL)" == 0 && "$(metric_ch rotation_last_run_ok RTL)" == 1 ]] \
   && grep -q 'dipegang deploy' /tmp/journal.log; then
  ok "67 --all memakai kunci host bersama deploy per kanal (RTL-GL menunggu lalu gagal tanpa key baru; RTL tetap OK)"
else bad "67 --all kunci host"; fi
exec 6>&-
run "$W" --all --status
if [[ $RC -eq 0 ]] && grep -q '== kanal RTL ==' <<< "$OUT" && grep -q '== kanal RTL-GL ==' <<< "$OUT" && ! grep -qE '[0-9a-f]{40}' <<< "$OUT"; then
  ok "68 --all --status: semua kanal, tanpa nilai key"; else bad "68 --all --status"; fi
# Fallback daftar kanal = ONLINE_CHANNELS hasil deploy (.env 0640 root)
conf OVERLAP_SECONDS=0; printf 'APP_ENV=production\nONLINE_CHANNELS=RTL-GL\n' > /opt/efn/compose/.env; chmod 0640 /opt/efn/compose/.env
: > /tmp/journal.log; run "$W" --all --status
if [[ $RC -eq 0 ]] && grep -q 'sumber=/opt/efn/compose/.env ONLINE_CHANNELS' /tmp/journal.log && grep -q '== kanal RTL-GL ==' <<< "$OUT" \
   && ! grep -q '== kanal RTL ==' <<< "$OUT"; then
  ok "69 CHANNELS kosong -> kanal dari ONLINE_CHANNELS .env deploy"; else bad "69 fallback .env"; fi
chmod 0662 /opt/efn/compose/.env; run "$W" --all --status; expect_deny "70 .env dapat ditulis lainnya ditolak" "tidak dapat ditulis"
printf 'ONLINE_CHANNELS=RTL,../x\n' > /opt/efn/compose/.env; chmod 0640 /opt/efn/compose/.env
run "$W" --all --status;                 expect_deny "71 ONLINE_CHANNELS .env tidak valid ditolak (tanpa fallback diam-diam)" "ONLINE_CHANNELS tidak valid"
rm -f /opt/efn/compose/.env

# --no-wait (dipakai --all): rotasi tanpa sleep overlap, key lama tetap sah sampai cabut tertunda
conf OVERLAP_SECONDS=3600; : > /tmp/journal.log; rot --force --no-wait
if [[ $RC -eq 0 && "$(active)" == 2 && "$(pending_of rtl)" -gt "$(date +%s)" ]] && grep -q -- '(--no-wait)' /tmp/journal.log; then
  ok "72 --no-wait: tanpa menunggu overlap, cabut tertunda tercatat di state"; else bad "72 --no-wait"; fi
conf OVERLAP_SECONDS=0
rot --status --no-wait;                  expect_deny "73 --status + --no-wait ditolak" "tidak bisa digabung"

# --bootstrap: key pertama kanal yang belum ada di ONLINE_CHANNELS (overlay ikut sementara)
run "$W" --channel=FNB-BACHA;            expect_deny "74 kanal belum di-deploy tanpa --bootstrap ditolak" "tidak didefinisikan compose"
: > /tmp/fk/files.log; run "$W" --channel=FNB-BACHA --bootstrap
if [[ $RC -eq 0 && "$(cat "$SEC_FB")" == "$(key_in "$FBDB" 1)" ]] && grep -q 'docker-compose.online-fnb-bacha.yml' /tmp/fk/files.log \
   && grep -q 'VERIFIKASI OK kanal=FNB-BACHA' /tmp/journal.log; then
  ok "75 --bootstrap: overlay kanal ikut di-mount sementara, key pertama terpasang & terverifikasi"; else bad "75 bootstrap"; fi
: > /tmp/journal.log
run "$W" --channel=FNB-BACHA --bootstrap; expect_deny "76 --bootstrap saat secret bukan placeholder ditolak" "hanya untuk key pertama"
run "$W" --channel=RTL-BACHA --bootstrap; expect_deny "77 --bootstrap tanpa file overlay kanal ditolak" "overlay"
# User teknis bersama beberapa kanal (data demo) -> Odoo shell menolak sebelum key dibuat (fail-closed)
echo user_shared > /tmp/fk/odoo-err.efn_online_rtl_gl; N_GL=$(wc -l < "$GLDB"); : > /tmp/journal.log; run "$W" --channel=RTL-GL --force
rm -f /tmp/fk/odoo-err.efn_online_rtl_gl
if [[ $RC -eq 1 && "$(wc -l < "$GLDB")" == "$N_GL" ]] && grep -q 'odoo list: user_shared' /tmp/journal.log; then
  ok "78 user_shared dari Odoo -> rc 1, tidak ada key dibuat"; else bad "78 user_shared"; fi

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
with open(sys.argv[1], encoding="utf-8") as fh:
    code = compile(fh.read(), "efn-rotate-online-key:PY", "exec")
exec(code, {"env": Env(), "__name__": "efn_rot"})
EOF
PYOUT=""
pyrun() { set +e; PYOUT=$(env -i PATH=/usr/local/bin:/usr/bin:/bin EFN_OP=list EFN_LOGIN="$1" EFN_CH="$2" FAKE_SPEC="$3" \
            python3 -I /tmp/efn-fake-odoo.py "$PYF" 2>&1); set -e; }
pyok() { if grep -qx "$2" <<< "$PYOUT" && { [[ "$2" == 'EFN_ROT OK' ]] || ! grep -qx 'EFN_ROT OK' <<< "$PYOUT"; }; then ok "$1"
         else OUT="$PYOUT"; bad "$1"; fi; }
G='"groups":["efn_online_order.group_online_channel"]'
pyrun efn_online_rtl_gl RTL-GL '{"users":{"efn_online_rtl_gl":{"id":7,"active":true,'"$G"'}},
  "channels":[{"code":"RTL-GL","users":["efn_online_rtl_gl"]},{"code":"RTL","users":[]}],
  "keys":[{"id":3,"user":"efn_online_rtl_gl","name":"efn-online-RTL-GL-x"}]}'
pyok "79 python: user khusus satu kanal -> daftar key + OK" 'EFN_ROT OK'
if grep -qx 'EFN_ROT KEY 3 0 efn-online-RTL-GL-x' <<< "$PYOUT"; then ok "80 python: baris KEY (id exp nama) sesuai format parser"; else OUT="$PYOUT"; bad "80 python KEY"; fi
pyrun efn_online_rtl RTL '{"users":{"efn_online_rtl":{"id":5,"active":true,'"$G"'}},
  "channels":[{"code":"RTL","users":["efn_online_rtl"]},{"code":"RTL-GL","users":["efn_online_rtl"]}]}'
pyok "81 python: user teknis bersama RTL + RTL-GL (pola data demo) -> ERR user_shared" 'EFN_ROT ERR user_shared'
pyrun efn_online_rtl RTL-GL '{"users":{"efn_online_rtl":{"id":5,"active":true,'"$G"'}},"channels":[{"code":"RTL","users":["efn_online_rtl"]}]}'
pyok "82 python: user tidak terhubung ke kanal -> ERR user_not_in_channel" 'EFN_ROT ERR user_not_in_channel'
pyrun efn_online_x RTL '{"users":{}}'
pyok "83 python: user tidak ada -> ERR user_missing" 'EFN_ROT ERR user_missing'
pyrun efn_online_rtl RTL '{"users":{"efn_online_rtl":{"id":5,"active":true,"groups":["efn_online_order.group_online_channel","base.group_system"]}},
  "channels":[{"code":"RTL","users":["efn_online_rtl"]}]}'
pyok "84 python: user teknis ber-hak admin -> ERR user_privileged" 'EFN_ROT ERR user_privileged'
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
