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
SECRET=/opt/efn/secrets/odoo_api_key_online_rtl.txt

cat > /usr/local/bin/docker <<'EOF'
#!/usr/bin/env bash
# docker palsu (lihat header test)
FK=/tmp/fk
cmd="$1"; shift
if [[ "$cmd" == compose ]]; then
  args=(); while (($#)); do
    case "$1" in --project-name|--project-directory|-f) shift 2 ;; *) args+=("$1"); shift ;; esac
  done
  set -- "${args[@]}"
  sub="$1"; shift
  case "$sub" in
    config)
      jq -nc --arg f /opt/efn/secrets/odoo_api_key_online_rtl.txt '{secrets:{odoo_api_key_online_rtl:{file:$f}},
        services:{"api-gateway":{secrets:[{source:"odoo_api_key_online_rtl"},{source:"efn_ca_crt"}]},
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
    up) printf '%s\n' "$*" >> $FK/up.log; cp /opt/efn/secrets/odoo_api_key_online_rtl.txt $FK/mounted ;;
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
  db=$FK/keys.db; touch $db
  case "${E[EFN_OP]}" in
    create)
      [[ -e $FK/fail-create ]] && { echo "EFN_ROT ERR ValidationError"; exit 0; }
      id=$(( $(wc -l < $db) + 1 )); key=$(od -An -tx1 -N20 /dev/urandom | tr -d ' \n')
      exp=$(( $(date +%s) + ${E[EFN_KEY_DAYS]} * 86400 ))
      echo "$id $exp ${E[EFN_KEY_NAME]} $key 0" >> $db; echo "$key" >> /tmp/allkeys
      [[ -e $FK/fail-next-new ]] && { echo "$key" >> $FK/bad; rm -f $FK/fail-next-new; }
      echo "EFN_ROT NEW $id $exp $key" ;;
    revoke)
      while read -r id exp name key rv; do
        if [[ "$rv" == 0 ]] && { [[ -n "${E[EFN_REVOKE_ID]:-}" && "$id" == "${E[EFN_REVOKE_ID]}" ]] ||
                                 [[ -z "${E[EFN_REVOKE_ID]:-}" && "$id" != "${E[EFN_KEEP_ID]}" ]]; }; then
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
if [[ "${E[EFN_KEY_STDIN]:-}" == 1 ]]; then k=$(cat); else k=$(cat $FK/mounted 2> /dev/null); fi
st=401
if [[ "$k" =~ ^[0-9a-f]{40}$ ]] && grep -q " $k 0$" $FK/keys.db 2> /dev/null && ! grep -qx "$k" $FK/bad 2> /dev/null; then st=200; fi
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
fresh() { rm -rf /tmp/fk /var/lib/efn/online-key /var/lib/efn/node-exporter/*; mkdir -p /tmp/fk; : > /tmp/fk/keys.db
  printf 'disabled' > "$SECRET"; chmod 0444 "$SECRET"; : > /tmp/journal.log; conf OVERLAP_SECONDS=0; }
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

# ── 12. Penjaga independen: nilai key TIDAK PERNAH muncul di journald / keluaran ─────────────────────────────
leak=0
while read -r key; do
  grep -qF "$key" /tmp/journal.all && { echo "bocor: journal"; leak=1; }
  grep -qF "$key" <<< "$ALL_OUT" && { echo "bocor: keluaran"; leak=1; }
  grep -rqF "$key" /var/lib/efn/node-exporter /etc/efn && { echo "bocor: file"; leak=1; }
done < /tmp/allkeys
if (($(wc -l < /tmp/allkeys) >= 5 && leak == 0)); then ok "42 tidak ada nilai key ($(wc -l < /tmp/allkeys) key dibuat) di journald/stdout/stderr/metrik"; else bad "42 KEBOCORAN key"; fi

echo "RINGKASAN efn-rotate-online-key: PASS=$PASS FAIL=$FAIL"
[[ $FAIL -eq 0 ]]
