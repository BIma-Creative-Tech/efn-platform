#!/usr/bin/env bash
# Uji regresi scripts/host/efn-online-cs (SEC-98 / L-16d, SEC-107/SEC-108 / L-19c/e, L-3) dengan docker & logger PALSU,
# di dalam CONTAINER sekali pakai (pola test-efn-deploy.sh). Menulis /usr/local/{sbin,bin}, membuat user/grup ->
# MENOLAK jalan di luar container.
#
#   docker run --rm -v "$PWD:/src:ro" python:3.12-bookworm@sha256:e91fec3d1ac69f04e4eddcd29c327e630ce34658cf31075bfa7e8b0e052bafea \
#     bash /src/scripts/host/test-efn-online-cs.sh
# Keluaran: PASS/FAIL per kasus; exit 1 bila ada FAIL. Yang TIDAK diuji: sudoers asli (sudo tidak dipasang; SUDO_USER/
# SUDO_UID disimulasikan seperti yang disetel sudo) dan CLI BFF sungguhan (diuji Jest BFF).
set -euo pipefail
[[ -f /.dockerenv || -n "${EFN_TEST_IN_CONTAINER:-}" ]] || { echo "hanya di dalam container sekali pakai" >&2; exit 2; }
[[ "$EUID" -eq 0 ]] || { echo "harus root (di container)" >&2; exit 2; }
SRC="${SRC:-/src}"
W=/usr/local/sbin/efn-online-cs
install -m 0755 "$SRC/scripts/host/efn-online-cs" "$W"

# docker palsu: `ps` -> id container (kosong bila /tmp/fake-no-container), `exec` -> argv ke /tmp/exec.log, stdin ke
# /tmp/exec.stdin
cat > /usr/local/bin/docker <<'EOF'
#!/usr/bin/env bash
case "$1" in
  ps) [[ -e /tmp/fake-no-container ]] || echo 0123456789ab ;;
  exec) shift; printf '%s\n' "$@" > /tmp/exec.log; cat > /tmp/exec.stdin; echo 'online-cli: {"status":"ok"}'
        exit "$(cat /tmp/fake-rc 2> /dev/null || echo 0)" ;;
  *) exit 1 ;;
esac
EOF
# logger palsu (journald): baris ke /tmp/journal.log
cat > /usr/local/bin/logger <<'EOF'
#!/usr/bin/env bash
shift 3 2> /dev/null || true
printf '%s\n' "$*" >> /tmp/journal.log
EOF
chmod 0755 /usr/local/bin/docker /usr/local/bin/logger
for g in efn-cs efn-cs-approver; do getent group "$g" > /dev/null || groupadd "$g"; done
id cs1 > /dev/null 2>&1 || useradd -m -G efn-cs cs1
id cs2 > /dev/null 2>&1 || useradd -m cs2
id ap1 > /dev/null 2>&1 || useradd -m -G efn-cs-approver ap1
id dual > /dev/null 2>&1 || useradd -m -G efn-cs,efn-cs-approver dual
UID1=$(id -u cs1); UID2=$(id -u cs2); UIDA=$(id -u ap1); UIDD=$(id -u dual)
P=0b6f3c1e-4b6a-4e8e-9d2a-1f0c2b3a4d5e
R=5d2e7a10-3c4b-4d5e-8f90-a1b2c3d4e5f6

PASS=0; FAIL=0; RC=0; OUT=""
ok()  { echo "PASS $1"; PASS=$((PASS + 1)); }
bad() { echo "FAIL $1 :: rc=$RC :: $(tail -n2 <<< "$OUT" | tr '\n' ' ')"; FAIL=$((FAIL + 1)); }
# IN = isi stdin (alasan); default alasan sah
run() { rm -f /tmp/exec.log /tmp/exec.stdin; set +e; OUT=$(printf '%s\n' "${IN-tiket CS-123}" | "$@" 2>&1); RC=$?; set -e; }
as_user() { local u="$1" id="$2"; shift 2
  run env -i PATH=/usr/bin:/bin SUDO_USER="$u" SUDO_UID="$id" EFN_CS_OPERATOR=evil EFN_CS_HOST=evilhost EFN_CS_ROLE=approver "$W" "$@"; }
as_cs1() { as_user cs1 "$UID1" "$@"; }
expect_ok()   { if [[ $RC -eq 0 && -s /tmp/exec.log ]]; then ok "$1"; else bad "$1"; fi; }
expect_deny() { if [[ $RC -ne 0 && ! -e /tmp/exec.log ]] && grep -q -- "$2" <<< "$OUT"; then ok "$1"; else bad "$1"; fi; }

# ── jalur sah ────────────────────────────────────────────────────────────────────────────────────────────────
: > /tmp/journal.log
# shellcheck disable=SC2016  # sengaja literal: metakarakter shell tidak boleh dieksekusi wrapper
EVIL='pelanggan batal $(id); rm -rf / `id`'
IN="$EVIL" as_cs1 online-refund --part=$P --full --kind=customer_cs --restock
expect_ok "01 online-refund --full sah diteruskan"
if grep -qx "EFN_CS_OPERATOR=cs1" /tmp/exec.log && grep -qx "EFN_CS_ROLE=cs" /tmp/exec.log && ! grep -q evil /tmp/exec.log; then
  ok "02 EFN_CS_OPERATOR/HOST/ROLE dari sudo+grup, env pemanggil (ROLE=approver) ditimpa"; else bad "02 aktor/peran"; fi
if grep -qx 'dist/cli/online-refund.js' /tmp/exec.log && ! grep -q -- '--reason' /tmp/exec.log && [[ "$(cat /tmp/exec.stdin)" == "$EVIL" ]]; then
  ok "03 alasan lewat stdin literal (tidak di argv, metakarakter tidak dieksekusi)"; else bad "03 stdin"; fi
if grep -q "aktor=cs1 peran=cs .*cmd=online-refund part=$P full" /tmp/journal.log && ! grep -q 'pelanggan' /tmp/journal.log; then
  ok "04 journald: aktor+peran+cmd+id, TANPA alasan"; else OUT=$(cat /tmp/journal.log); bad "04 journald"; fi
as_cs1 online-refund --part=$P --lines=101:1,202:3
expect_ok "05 online-refund --lines sah"
as_user ap1 "$UIDA" online-refund-approve --refund=$R
expect_ok "06 online-refund-approve oleh approver sah"
if grep -qx "EFN_CS_ROLE=approver" /tmp/exec.log && grep -qx "EFN_CS_OPERATOR=ap1" /tmp/exec.log; then ok "07 peran approver diteruskan"; else bad "07 peran approver"; fi
as_cs1 online-refund-settle --refund=$R --provider-ref=TRF/2026-10/001
expect_ok "08 online-refund-settle sah"
if ! grep -q 'TRF/2026' /tmp/journal.log; then ok "09 provider-ref tidak dicatat journald"; else bad "09 provider-ref"; fi
as_cs1 online-resolve --part=$P --action=sync
expect_ok "10 online-resolve sah"
echo 1 > /tmp/fake-rc; as_user ap1 "$UIDA" online-resolve --part=$P --action=cancel; rm -f /tmp/fake-rc
if [[ $RC -eq 1 ]] && grep -q 'rc=1' /tmp/journal.log; then ok "11 kode keluar CLI diteruskan + dicatat (approver cancel)"; else bad "11 rc"; fi
as_cs1 online-resolve --part=$P --action=cancel
expect_deny "11b online-resolve cancel oleh peran cs ditolak (L-20)" "online-resolve --action=cancel hanya"
IN=$'tiket CS-9\r' as_cs1 online-resolve --part=$P --action=sync
if [[ $RC -eq 0 && "$(cat /tmp/exec.stdin)" == "tiket CS-9" ]]; then ok "12 CRLF di akhir alasan dibuang"; else bad "12 CRLF"; fi

# ── identitas / peran (SEC-107) ─────────────────────────────────────────────────────────────────────────────
run su -s /bin/bash cs1 -c "$W online-resolve --part=$P --action=sync"
expect_deny "13 tanpa root (bukan lewat sudo) ditolak" "lewat sudo"
run env -i PATH=/usr/bin:/bin "$W" online-resolve --part=$P --action=sync
expect_deny "14 root tanpa SUDO_USER ditolak" "SUDO_USER"
run env -i PATH=/usr/bin:/bin SUDO_USER=root SUDO_UID=0 "$W" online-resolve --part=$P --action=sync
expect_deny "15 SUDO_USER=root ditolak" "root langsung"
run env -i PATH=/usr/bin:/bin SUDO_USER=cs1 SUDO_UID="$UID2" "$W" online-resolve --part=$P --action=sync
expect_deny "16 SUDO_USER/SUDO_UID tidak konsisten ditolak" "tidak konsisten"
as_user cs2 "$UID2" online-resolve --part=$P --action=sync
expect_deny "17 bukan anggota grup CS ditolak" "bukan anggota"
run env -i PATH=/usr/bin:/bin SUDO_USER='cs1;id' SUDO_UID="$UID1" "$W" online-resolve --part=$P --action=sync
expect_deny "18 SUDO_USER berkarakter aneh ditolak" "SUDO_USER"
as_cs1 online-refund-approve --refund=$R
expect_deny "19 online-refund-approve oleh peran cs ditolak (SEC-107)" "hanya untuk grup efn-cs-approver"
as_user dual "$UIDD" online-refund-approve --refund=$R
expect_deny "20 anggota efn-cs DAN efn-cs-approver ditolak (SEC-107)" "satu orang satu peran"
as_user dual "$UIDD" online-resolve --part=$P --action=sync
expect_deny "21 dua peran ditolak juga untuk subcommand lain" "satu orang satu peran"

# ── alasan via stdin (SEC-108) ──────────────────────────────────────────────────────────────────────────────
as_cs1 online-resolve --part=$P --action=sync --reason=abc
expect_deny "22 --reason di argv ditolak (SEC-108)" "tidak boleh di argv"
IN="" as_cs1 online-resolve --part=$P --action=sync
expect_deny "23 alasan kosong di stdin ditolak" "alasan wajib"
IN="ab" as_cs1 online-resolve --part=$P --action=sync
expect_deny "24 alasan < 3 ditolak" "alasan wajib"
IN=$'tiket\tCS' as_cs1 online-resolve --part=$P --action=sync
expect_deny "25 karakter kontrol (tab) di alasan ditolak" "kontrol"
IN="$(head -c 900 /dev/zero | tr '\0' 'a')" as_cs1 online-resolve --part=$P --action=sync
expect_deny "26 alasan > 800 byte ditolak" "alasan wajib"
IN=$'baris1\nIMAGE=x' as_cs1 online-resolve --part=$P --action=sync
if [[ $RC -eq 0 && "$(cat /tmp/exec.stdin)" == "baris1" ]]; then ok "27 hanya baris pertama stdin dipakai sebagai alasan"; else bad "27 multi-baris"; fi

# ── allowlist subcommand / flag / nilai ─────────────────────────────────────────────────────────────────────
as_cs1 pii-reencrypt;                                                    expect_deny "28 subcommand lain (pii-reencrypt) ditolak" "tidak diizinkan"
as_cs1 ../online-refund --part=$P --full;                                expect_deny "29 subcommand path traversal ditolak" "tidak diizinkan"
as_cs1 online-refund --part=$P --full --actor=boss;                      expect_deny "30 --actor (SEC-98) ditolak" "tidak diizinkan"
as_cs1 online-refund --part=$P --part=$R --full;                         expect_deny "31 flag ganda ditolak" "ganda"
as_cs1 online-refund --part=12345 --full;                                expect_deny "32 --part bukan UUID ditolak" "UUID"
as_cs1 online-refund --part=$P --full --lines=1:1;                       expect_deny "33 --full + --lines ditolak" "salah satu"
as_cs1 online-refund --part=$P;                                          expect_deny "34 tanpa --full/--lines ditolak" "salah satu"
as_cs1 online-refund --part=$P --full=yes;                               expect_deny "35 --full=nilai ditolak" "tanpa nilai"
as_cs1 online-refund --part=$P --lines='1:1;rm';                         expect_deny "36 --lines format salah ditolak" "lines"
as_cs1 online-refund --part=$P --full --kind=gratis;                     expect_deny "37 --kind di luar enum ditolak" "kind"
as_cs1 online-refund --part=$P --full "--kind=$(printf 'a\nb')";         expect_deny "38 karakter kontrol di argv ditolak" "kontrol"
as_cs1 online-refund --part=$P;                                          expect_deny "39 --full/--lines wajib" "salah satu"
as_cs1 online-refund-settle --refund=$R --provider-ref='a b';            expect_deny "40 provider-ref berspasi ditolak" "provider-ref"
as_cs1 online-resolve --part=$P --action=delete;                         expect_deny "41 --action di luar enum ditolak" "action"
as_cs1 online-resolve --part=$P --action=sync extra;                     expect_deny "42 argumen posisi bebas ditolak" "nama=nilai"
as_user ap1 "$UIDA" online-refund-approve --refund=$R -e;                expect_deny "43 opsi pendek (-e) ditolak" "nama=nilai"
touch /tmp/fake-no-container
as_cs1 online-resolve --part=$P --action=sync;                           expect_deny "44 container api-gateway tidak berjalan ditolak" "tidak berjalan"
rm -f /tmp/fake-no-container
if grep -q 'DITOLAK' /tmp/journal.log && ! grep -q 'tiket CS' /tmp/journal.log; then
  ok "45 penolakan tercatat journald, alasan tidak pernah di journald"; else bad "45 penolakan journald"; fi

echo "RINGKASAN efn-online-cs: PASS=$PASS FAIL=$FAIL"
[[ $FAIL -eq 0 ]]
