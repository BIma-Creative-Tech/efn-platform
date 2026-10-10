#!/usr/bin/env bash
# Uji regresi scripts/host/efn-online-cs (SEC-98 / L-16d, L-3) dengan docker & logger PALSU, di dalam CONTAINER sekali
# pakai (pola test-efn-deploy.sh). Menulis /usr/local/{sbin,bin}, membuat user/grup -> MENOLAK jalan di luar container.
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

# docker palsu: `ps` -> id container (kosong bila /tmp/fake-no-container), `exec` -> argv ke /tmp/exec.log
cat > /usr/local/bin/docker <<'EOF'
#!/usr/bin/env bash
case "$1" in
  ps) [[ -e /tmp/fake-no-container ]] || echo 0123456789ab ;;
  exec) shift; printf '%s\n' "$@" > /tmp/exec.log; echo 'online-cli: {"status":"ok"}'; exit "$(cat /tmp/fake-rc 2> /dev/null || echo 0)" ;;
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
getent group efn-cs > /dev/null || groupadd efn-cs
id cs1 > /dev/null 2>&1 || useradd -m -G efn-cs cs1
id cs2 > /dev/null 2>&1 || useradd -m cs2
UID1=$(id -u cs1); UID2=$(id -u cs2)
P=0b6f3c1e-4b6a-4e8e-9d2a-1f0c2b3a4d5e
R=5d2e7a10-3c4b-4d5e-8f90-a1b2c3d4e5f6

PASS=0; FAIL=0; RC=0; OUT=""
ok()  { echo "PASS $1"; PASS=$((PASS + 1)); }
bad() { echo "FAIL $1 :: rc=$RC :: $(tail -n2 <<< "$OUT" | tr '\n' ' ')"; FAIL=$((FAIL + 1)); }
run() { rm -f /tmp/exec.log; set +e; OUT=$("$@" 2>&1); RC=$?; set -e; }
as_cs1() { run env -i PATH=/usr/bin:/bin SUDO_USER=cs1 SUDO_UID="$UID1" EFN_CS_OPERATOR=evil EFN_CS_HOST=evilhost "$W" "$@"; }
expect_ok()   { if [[ $RC -eq 0 && -s /tmp/exec.log ]]; then ok "$1"; else bad "$1"; fi; }
expect_deny() { if [[ $RC -ne 0 && ! -e /tmp/exec.log ]] && grep -q -- "$2" <<< "$OUT"; then ok "$1"; else bad "$1"; fi; }

# ── jalur sah ────────────────────────────────────────────────────────────────────────────────────────────────
: > /tmp/journal.log
# shellcheck disable=SC2016  # sengaja literal: metakarakter shell tidak boleh dieksekusi wrapper
EVIL='pelanggan batal $(id); rm -rf /'
as_cs1 online-refund --part=$P --full --reason="$EVIL" --kind=customer_cs --restock
expect_ok "01 online-refund --full sah diteruskan"
if grep -qx "EFN_CS_OPERATOR=cs1" /tmp/exec.log && ! grep -q evil /tmp/exec.log; then ok "02 EFN_CS_OPERATOR/HOST dari sudo, env pemanggil ditimpa"; else bad "02 aktor"; fi
if grep -qx 'dist/cli/online-refund.js' /tmp/exec.log && grep -qxF -- "--reason=$EVIL" /tmp/exec.log; then
  ok "03 argv literal ke docker exec (tanpa shell; metakarakter tidak dieksekusi)"; else bad "03 argv"; fi
if grep -q "aktor=cs1 .*cmd=online-refund part=$P full" /tmp/journal.log && ! grep -q 'pelanggan' /tmp/journal.log; then
  ok "04 journald: aktor+cmd+id, TANPA alasan"; else OUT=$(cat /tmp/journal.log); bad "04 journald"; fi
as_cs1 online-refund --part=$P --lines=101:1,202:3 --reason=rusak
expect_ok "05 online-refund --lines sah"
as_cs1 online-refund-approve --refund=$R --reason='tiket CS-12'
expect_ok "06 online-refund-approve sah"
as_cs1 online-refund-settle --refund=$R --provider-ref=TRF/2026-10/001 --reason='transfer manual'
expect_ok "07 online-refund-settle sah"
if ! grep -q 'TRF/2026' /tmp/journal.log; then ok "08 provider-ref tidak dicatat journald"; else bad "08 provider-ref"; fi
as_cs1 online-resolve --part=$P --action=sync --reason='event hilang'
expect_ok "09 online-resolve sah"
echo 1 > /tmp/fake-rc; as_cs1 online-resolve --part=$P --action=cancel --reason='ops batal'; rm -f /tmp/fake-rc
if [[ $RC -eq 1 ]] && grep -q 'rc=1' /tmp/journal.log; then ok "10 kode keluar CLI diteruskan + dicatat"; else bad "10 rc"; fi

# ── identitas / hak ─────────────────────────────────────────────────────────────────────────────────────────
run su -s /bin/bash cs1 -c "$W online-resolve --part=$P --action=sync --reason=abc"
expect_deny "11 tanpa root (bukan lewat sudo) ditolak" "lewat sudo"
run env -i PATH=/usr/bin:/bin "$W" online-resolve --part=$P --action=sync --reason=abc
expect_deny "12 root tanpa SUDO_USER ditolak" "SUDO_USER"
run env -i PATH=/usr/bin:/bin SUDO_USER=root SUDO_UID=0 "$W" online-resolve --part=$P --action=sync --reason=abc
expect_deny "13 SUDO_USER=root ditolak" "root langsung"
run env -i PATH=/usr/bin:/bin SUDO_USER=cs1 SUDO_UID="$UID2" "$W" online-resolve --part=$P --action=sync --reason=abc
expect_deny "14 SUDO_USER/SUDO_UID tidak konsisten ditolak" "tidak konsisten"
run env -i PATH=/usr/bin:/bin SUDO_USER=cs2 SUDO_UID="$UID2" "$W" online-resolve --part=$P --action=sync --reason=abc
expect_deny "15 bukan anggota grup efn-cs ditolak" "bukan anggota"
run env -i PATH=/usr/bin:/bin SUDO_USER='cs1;id' SUDO_UID="$UID1" "$W" online-resolve --part=$P --action=sync --reason=abc
expect_deny "16 SUDO_USER berkarakter aneh ditolak" "SUDO_USER"

# ── allowlist subcommand / flag / nilai ─────────────────────────────────────────────────────────────────────
as_cs1 pii-reencrypt;                                                    expect_deny "17 subcommand lain (pii-reencrypt) ditolak" "tidak diizinkan"
as_cs1 ../online-refund --part=$P --full --reason=abc;                   expect_deny "18 subcommand path traversal ditolak" "tidak diizinkan"
as_cs1 online-refund --part=$P --full --reason=abc --actor=boss;         expect_deny "19 --actor (SEC-98) ditolak" "tidak diizinkan"
as_cs1 online-refund --part=$P --part=$R --full --reason=abc;            expect_deny "20 flag ganda ditolak" "ganda"
as_cs1 online-refund --part=12345 --full --reason=abc;                   expect_deny "21 --part bukan UUID ditolak" "UUID"
as_cs1 online-refund --part=$P --full --lines=1:1 --reason=abc;          expect_deny "22 --full + --lines ditolak" "salah satu"
as_cs1 online-refund --part=$P --reason=abc;                             expect_deny "23 tanpa --full/--lines ditolak" "salah satu"
as_cs1 online-refund --part=$P --full=yes --reason=abc;                  expect_deny "24 --full=nilai ditolak" "tanpa nilai"
as_cs1 online-refund --part=$P --lines='1:1;rm' --reason=abc;            expect_deny "25 --lines format salah ditolak" "lines"
as_cs1 online-refund --part=$P --full --reason=abc --kind=gratis;        expect_deny "26 --kind di luar enum ditolak" "kind"
as_cs1 online-refund --part=$P --full --reason="$(printf 'baris1\nIMAGE=x')"; expect_deny "27 karakter kontrol (newline) ditolak" "kontrol"
as_cs1 online-refund --part=$P --full --reason=ab;                       expect_deny "28 alasan < 3 ditolak" "reason"
as_cs1 online-refund --part=$P --full;                                   expect_deny "29 tanpa --reason ditolak" "wajib"
as_cs1 online-refund-settle --refund=$R --provider-ref='a b' --reason=abc; expect_deny "30 provider-ref berspasi ditolak" "provider-ref"
as_cs1 online-resolve --part=$P --action=delete --reason=abc;            expect_deny "31 --action di luar enum ditolak" "action"
as_cs1 online-resolve --part=$P --action=sync --reason=abc extra;        expect_deny "32 argumen posisi bebas ditolak" "--nama=nilai"
as_cs1 online-refund-approve --refund=$R --reason=abc -e;                expect_deny "33 opsi pendek (-e) ditolak" "--nama=nilai"
touch /tmp/fake-no-container
as_cs1 online-resolve --part=$P --action=sync --reason=abc;              expect_deny "34 container api-gateway tidak berjalan ditolak" "tidak berjalan"
rm -f /tmp/fake-no-container
if grep -q 'DITOLAK' /tmp/journal.log; then ok "35 penolakan tercatat journald"; else bad "35 penolakan journald"; fi

echo "RINGKASAN efn-online-cs: PASS=$PASS FAIL=$FAIL"
[[ $FAIL -eq 0 ]]
