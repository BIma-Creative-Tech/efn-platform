#!/usr/bin/env bash
# Uji regresi scripts/host/efn-deploy (SEC-36/SEC-42/SEC-50) + scripts/lib/deploy-validate.sh dengan cosign PALSU, di dalam CONTAINER sekali pakai.
# Menulis /etc/efn, /opt/efn, /var/lib/efn, /usr/local/bin/cosign -> MENOLAK jalan di luar container.
#
# Pemakaian (dari root repo; Git Bash: MSYS_NO_PATHCONV=1 + "$(pwd -W)"):
#   docker run --rm -v "$PWD:/src:ro" python:3.12-bookworm@sha256:e91fec3d1ac69f04e4eddcd29c327e630ce34658cf31075bfa7e8b0e052bafea \
#     bash /src/scripts/host/test-efn-deploy.sh
# Keluaran: PASS/FAIL per kasus; exit 1 bila ada FAIL.
#
# Cosign palsu: `verify-blob` lolos hanya bila isi bundle == "VALID:<sha256 blob>" (meniru tanda tangan terikat
# isi); `verify` lolos hanya bila image ada di daftar image "bertanda tangan" DAN --certificate-identity sama dengan
# identitas yang diharapkan. Yang TIDAK diuji di sini: kriptografi cosign/Rekor/Fulcio asli (diuji vendor).
set -euo pipefail
[[ -f /.dockerenv || -n "${EFN_TEST_IN_CONTAINER:-}" ]] || { echo "hanya di dalam container sekali pakai" >&2; exit 2; }
[[ "$EUID" -eq 0 ]] || { echo "harus root (di container)" >&2; exit 2; }
SRC="${SRC:-/src}"
export DEBIAN_FRONTEND=noninteractive
if ! command -v jq > /dev/null || ! command -v rsync > /dev/null; then apt-get update -qq && apt-get install -y -qq jq rsync bsdutils > /dev/null; fi

install -m 0755 "$SRC/scripts/host/efn-deploy" /usr/local/sbin/efn-deploy
T=$(mktemp -d); cd "$T"
ORG=efn-test; REPO="$ORG/efn-platform"; PREFIX="ghcr.io/${ORG}/"
ID_OK="https://github.com/${REPO}/.github/workflows/build-push.yml@refs/tags/v1.2.0"

# ── cosign palsu ──────────────────────────────────────────────────────────────────────────────────────────────
mkdir -p /etc/fake-cosign
cat > /usr/local/bin/cosign <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
cmd="$1"; shift
bundle="" ident="" last=""
while (($#)); do
  case "$1" in
    --bundle) bundle="$2"; shift 2 ;;
    --certificate-identity) ident="$2"; shift 2 ;;
    --certificate-identity-regexp|--certificate-oidc-issuer) shift 2 ;;
    *) last="$1"; shift ;;
  esac
done
case "$cmd" in
  verify-blob) [[ "$(cat "$bundle")" == "VALID:$(sha256sum "$last" | cut -d' ' -f1)" ]] ;;
  verify) grep -qxF "$last" /etc/fake-cosign/signed && [[ "$ident" == "$(cat /etc/fake-cosign/identity)" ]] \
            && [[ -d "${DOCKER_CONFIG:-/nonexistent}" ]] ;;
  *) exit 1 ;;
esac
EOF
chmod 0755 /usr/local/bin/cosign
echo "$ID_OK" > /etc/fake-cosign/identity

# ── PKI uji: root sah, root lama (rotasi), root penyerang, intermediate ───────────────────────────────────────
mkca() { # nama issuer_key issuer_crt
  local n="$1"
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 30 -subj "/CN=$n" \
    -addext basicConstraints=critical,CA:TRUE -addext keyUsage=critical,keyCertSign,cRLSign \
    -keyout "$n.key" -out "$n.crt" 2> /dev/null
}
mkint() { # nama root
  openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -subj "/CN=$1" -keyout "$1.key" -out "$1.csr" 2> /dev/null
  printf 'basicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign,cRLSign\n' > ext.cnf
  openssl x509 -req -in "$1.csr" -CA "$2.crt" -CAkey "$2.key" -CAcreateserial -days 30 -extfile ext.cnf -out "$1.crt" 2> /dev/null
}
fp() { openssl x509 -in "$1" -noout -fingerprint -sha256 | cut -d= -f2 | tr -d ':' | tr 'A-F' 'a-f'; }
mkca root; mkca oldroot; mkca evilroot
mkint int root; mkint evilint evilroot
ROOT_FP=$(fp root.crt); OLD_FP=$(fp oldroot.crt)

mkdir -p /etc/efn
write_conf() { # [EXTRA_FP] [baris tambahan topologi...]
  local extra_fp="${1:-}"; shift || true
  cat > /etc/efn/deploy.conf <<EOF
COSIGN_BIN=/usr/local/bin/cosign
EFN_SIGNER_IDENTITY_REGEXP='^https://github\.com/${ORG}/efn-platform/\.github/workflows/deploy\.yml@refs/tags/v[0-9]+\.[0-9]+\.[0-9]+\$'
EFN_SIGNER_ISSUER=https://token.actions.githubusercontent.com
EFN_APP_ENV=production
EFN_GITHUB_REPO=${REPO}
EFN_IMAGE_PREFIX=${PREFIX}
EFN_PKI_ROOT_SHA256=${ROOT_FP}
EFN_PKI_EXTRA_TRUST_SHA256='${extra_fp}'
TRAEFIK_DOMAIN=efn.example
EFN_PUBLIC_BIND=203.0.113.10
EOF
  local l
  for l in "$@"; do printf '%s\n' "$l" >> /etc/efn/deploy.conf; done
  chmod 0644 /etc/efn/deploy.conf
}
write_conf

SVCS=(postgres odoo api-gateway superapp-web ai-ocr-worker etl minio metabase)
dg() { printf '%s' "$1" | sha256sum | cut -d' ' -f1; }
: > /etc/fake-cosign/signed
for s in "${SVCS[@]}"; do echo "${PREFIX}${s}@sha256:$(dg "$s-1")" >> /etc/fake-cosign/signed; done

# ── Pembuat rilis ─────────────────────────────────────────────────────────────────────────────────────────────
# make_release <dir> <seq> <version> [jq-filter-modifikasi-manifest] [extra_fps_json]
make_release() {
  local d="$1" seq="$2" ver="$3" mod="${4:-.}" extra="${5:-[]}"
  rm -rf "$d"; mkdir -p "$d/r/infra/compose" "$d/r/scripts/lib"
  echo "marker-$seq" > "$d/r/infra/compose/marker.txt"
  cp "$SRC/scripts/secrets.list" "$d/r/scripts/secrets.list"
  cp "$SRC/scripts/lib/deploy-validate.sh" "$d/r/scripts/lib/deploy-validate.sh"
  cat > "$d/r/scripts/deploy.sh" <<'EOF'
#!/usr/bin/env bash
{ env | grep -E '^(IMAGE_|APP_ENV=|EFN_RELEASE_|WHATSAPP_OTP_TEMPLATE=|EFN_WG_|EFN_PUBLIC_BIND=|COMPOSE_PROFILES=|TRAEFIK_DOMAIN=|PAYMENT_PROVIDER=|JWT_SECRET=)' | sort; } > /tmp/deploy-ran
EOF
  local imgs
  imgs=$(for s in "${SVCS[@]}"; do v="IMAGE_$(echo "$s" | tr 'a-z-' 'A-Z_')"; printf '%s\t%s\n' "$v" "${PREFIX}${s}@sha256:$(dg "$s-1")"; done \
    | jq -R 'split("\t") | {(.[0]): .[1]}' | jq -s add)
  jq -n --argjson seq "$seq" --arg ver "$ver" --arg root "$ROOT_FP" --argjson imgs "$imgs" --argjson extra "$extra" \
    '{schema:2, app_env:"production", version:$ver, seq:$seq, git_sha:("a"*40), signer_ref:("refs/tags/v"+$ver),
      images:$imgs, pki:{root_sha256:$root, extra_trust_sha256:$extra},
      config:{ACME_EMAIL:"ops@efn.example", PAYMENT_PROVIDER:"disabled", WHATSAPP_OTP_TEMPLATE:"efn_otp_v2",
              RABBITMQ_STRICT_PERMISSIONS:"true", ETL_AUDIT_COPY_ENABLED:""}}' | jq "$mod" > "$d/r/efn-release.json"
  tar -C "$d/r" --owner=0 --group=0 -czf "$d/release.tar.gz" infra scripts efn-release.json
  echo "VALID:$(sha256sum "$d/release.tar.gz" | cut -d' ' -f1)" > "$d/release.sigstore.json"
}
b64() { printf '%s' "$1" | base64 -w0; }
# make_env <dir> [extra baris NAME=nilai mentah...]
make_env() {
  local d="$1"; shift
  {
    for kv in "GHCR_USER=ci" "GHCR_TOKEN=tok-123" "JWT_SECRET=nilai \$(id) dengan spasi"; do
      echo "${kv%%=*}=$(b64 "${kv#*=}")"
    done
    echo "EFN_PKI_ROOT_CRT=$(b64 "$(cat "${ROOT_CRT_FILE:-root.crt}")")"
    echo "EFN_PKI_INTERMEDIATE_CRT=$(b64 "$(cat "${INT_CRT_FILE:-int.crt}")")"
    [[ -n "${EXTRA_FILE:-}" ]] && echo "EFN_PKI_EXTRA_TRUST=$(b64 "$(cat "$EXTRA_FILE")")"
    for kv in "$@"; do echo "${kv%%=*}=$(b64 "${kv#*=}")"; done
  } > "$d/env.b64"
  [[ -n "${ENV_OVERRIDE:-}" ]] && printf '%s\n' "$ENV_OVERRIDE" >> "$d/env.b64"
  return 0
}
run() { # dir -> keluaran gabungan; rc di $RC
  rm -f /tmp/deploy-ran
  set +e
  OUT=$(tar -C "$1" -cf - release.tar.gz release.sigstore.json env.b64 | /usr/local/sbin/efn-deploy 2>&1); RC=$?
  set -e
}
PASS=0; FAIL=0
ok()  { echo "PASS $1"; PASS=$((PASS + 1)); }
bad() { echo "FAIL $1 :: rc=$RC :: $(tail -n2 <<< "$OUT" | tr '\n' ' ')"; FAIL=$((FAIL + 1)); }
expect_ok()   { if [[ $RC -eq 0 && -f /tmp/deploy-ran ]]; then ok "$1"; else bad "$1"; fi; }
expect_fail() { # nama pola
  if [[ $RC -ne 0 && ! -f /tmp/deploy-ran && "$OUT" == *"$2"* ]]; then ok "$1"; else bad "$1 (harap: $2)"; fi
}
marker() { cat /opt/efn/compose/marker.txt 2> /dev/null || echo none; }

# 1. Rilis sah seq 10 v1.2.0 -> deploy.sh jalan, IMAGE_* dari manifest, state tercatat
make_release A 10 1.2.0; make_env A; run A; expect_ok "01 rilis sah diterima"
if grep -q "IMAGE_ODOO=${PREFIX}odoo@sha256:$(dg odoo-1)" /tmp/deploy-ran 2> /dev/null; then ok "02 IMAGE_ODOO dari manifest"; else bad "02 IMAGE_ODOO dari manifest"; fi
if grep -q '^seq=10$' /var/lib/efn/last-release && [[ "$(stat -c %a /var/lib/efn/last-release)" == 600 ]]; then
  ok "03 state last-release seq=10 (0600)"; else bad "03 state last-release"; fi
# 2. Rilis yang sama di-deploy ulang (re-run job) -> diterima
run A; expect_ok "04 deploy ulang rilis identik diterima"
# 3. Replay rilis lama bertanda tangan (seq lebih kecil)
make_release OLD 9 1.1.0; make_env OLD; run OLD; expect_fail "05 replay seq lama ditolak" "anti-downgrade"
if [[ "$(marker)" == marker-10 ]]; then ok "06 /opt/efn tidak berubah setelah penolakan"; else bad "06 /opt/efn berubah ($(marker))"; fi
# 4. seq sama, isi lain
make_release SAME 10 1.2.0 '.git_sha = ("b"*40)'; make_env SAME; run SAME; expect_fail "07 seq sama isi beda ditolak" "anti-downgrade"
# 5. Downgrade versi dengan seq baru (production) -> ditolak; dengan izin sekali pakai Superadmin -> diterima
echo "${PREFIX}odoo@sha256:$(dg odoo-1)" >> /etc/fake-cosign/signed
echo "https://github.com/${REPO}/.github/workflows/build-push.yml@refs/tags/v1.1.0" > /etc/fake-cosign/identity
make_release DOWN 11 1.1.0; make_env DOWN; run DOWN; expect_fail "08 downgrade versi (seq baru) ditolak" "versi 1.1.0 < terakhir 1.2.0"
sha256sum DOWN/release.tar.gz | cut -d' ' -f1 > /etc/efn/allow-downgrade; chmod 0600 /etc/efn/allow-downgrade
run DOWN; expect_ok "09 downgrade dengan /etc/efn/allow-downgrade (sha cocok) diterima"
if [[ ! -e /etc/efn/allow-downgrade ]]; then ok "10 izin downgrade dihapus setelah dipakai"; else bad "10 izin downgrade masih ada"; fi
echo "$ID_OK" > /etc/fake-cosign/identity
# 6. IMAGE_* lewat env (penyerang dengan kunci SSH) -> ditolak
make_release B 20 1.2.0; make_env B "IMAGE_ODOO=evil.example/odoo@sha256:$(dg evil)"; run B
expect_fail "11 IMAGE_ODOO lewat env ditolak" "variabel tidak diizinkan: IMAGE_ODOO"
# 7. Manifest image di luar prefix registry / tanpa tanda tangan image
make_release C 21 1.2.0 ".images.IMAGE_ODOO = \"evil.example/odoo@sha256:$(dg evil)\""; make_env C; run C
expect_fail "12 image registry asing di manifest ditolak" "IMAGE_ODOO di manifest tidak sesuai"
make_release D 22 1.2.0 ".images.IMAGE_ETL = \"${PREFIX}etl@sha256:$(dg unsigned)\""; make_env D; run D
expect_fail "13 image tanpa tanda tangan cosign ditolak" "cosign verify GAGAL untuk IMAGE_ETL"
make_release D2 22 1.2.0 '.images.IMAGE_ETL = .images.IMAGE_ODOO'; make_env D2; run D2
expect_fail "14 image svc lain (odoo sebagai etl) ditolak" "IMAGE_ETL di manifest tidak sesuai"
# 8. Trust anchor PKI
make_release E 23 1.2.0; ROOT_CRT_FILE=evilroot.crt INT_CRT_FILE=evilint.crt make_env E; run E
expect_fail "15 root CA penyerang ditolak" "sidik jari dipin"
make_release E2 23 1.2.0; INT_CRT_FILE=evilint.crt make_env E2; run E2
expect_fail "16 intermediate dari root lain ditolak" "tidak diterbitkan root CA yang dipin"
cat root.crt evilroot.crt > two.crt
make_release E3 23 1.2.0; ROOT_CRT_FILE=two.crt make_env E3; run E3
expect_fail "17 EFN_PKI_ROOT_CRT berisi 2 sertifikat ditolak" "tepat 1 sertifikat"
make_release E4 23 1.2.0 ".pki.root_sha256 = \"$(fp evilroot.crt)\""; make_env E4; run E4
expect_fail "18 root di manifest != pin ditolak" "root CA di manifest rilis"
make_release F 24 1.2.0 '.' "[\"$(fp evilroot.crt)\"]"; EXTRA_FILE=evilroot.crt make_env F; run F
expect_fail "19 EXTRA_TRUST CA penyerang (tidak dipin) ditolak" "tidak dipin"
write_conf "$OLD_FP"
make_release G 25 1.2.0; EXTRA_FILE=oldroot.crt make_env G; run G
expect_fail "20 EXTRA_TRUST dipin tapi tidak ada di manifest ditolak" "tidak ada di manifest"
make_release H 25 1.2.0 '.' "[\"$OLD_FP\"]"; EXTRA_FILE=oldroot.crt make_env H; run H
expect_ok "21 EXTRA_TRUST root lama (dipin + manifest) diterima"
write_conf
# 9. Lingkungan
make_release I 30 1.2.0 '.app_env = "staging"'; make_env I; run I; expect_fail "22 manifest staging di host production ditolak" "host ini production"
make_release J 31 1.2.0; ENV_OVERRIDE="APP_ENV=$(b64 production)" make_env J; run J; expect_fail "23 APP_ENV lewat stdin ditolak (SEC-50)" "variabel tidak diizinkan: APP_ENV"
make_release K 32 1.3.0 '.signer_ref = "refs/heads/main"'; make_env K; run K; expect_fail "24 production dari main ditolak" "production hanya menerima"
# 10. Tanda tangan / struktur
make_release L 33 1.2.0; make_env L; echo "VALID:00" > L/release.sigstore.json; run L; expect_fail "25 tanda tangan tidak valid ditolak" "tanda tangan rilis TIDAK valid"
make_release M 34 1.2.0; make_env M; tar -C M/r --owner=0 --group=0 -czf M/release.tar.gz infra scripts
echo "VALID:$(sha256sum M/release.tar.gz | cut -d' ' -f1)" > M/release.sigstore.json; run M
expect_fail "26 rilis tanpa manifest ditolak" "manifest efn-release.json tidak ada"
make_release N 35 1.2.0 'del(.images.IMAGE_MINIO)'; make_env N; run N; expect_fail "27 manifest kurang image ditolak" "skema"
make_release O 36 1.2.0; make_env O "PATH=/tmp"; run O; expect_fail "28 PATH lewat env ditolak" "variabel tidak diizinkan: PATH"
if [[ "$(marker)" == marker-10 || "$(marker)" == marker-11 || "$(marker)" == marker-25 ]]; then ok "29 /opt/efn hanya berubah oleh rilis yang diterima ($(marker))"; else bad "29 marker $(marker)"; fi
# 11. Rilis baru sah setelah semua penolakan
make_release P 40 1.3.0; echo "https://github.com/${REPO}/.github/workflows/build-push.yml@refs/tags/v1.3.0" > /etc/fake-cosign/identity
make_env P; run P; expect_ok "30 rilis baru v1.3.0 seq 40 diterima"
if grep -q '^EFN_RELEASE_VERSION=1.3.0$' /tmp/deploy-ran 2> /dev/null; then ok "31 EFN_RELEASE_VERSION diteruskan ke deploy.sh"; else bad "31 EFN_RELEASE_VERSION"; fi
# shellcheck disable=SC2016 # literal $(id) disengaja: nilai tidak boleh dieksekusi
if grep -qxF 'JWT_SECRET=nilai $(id) dengan spasi' /tmp/deploy-ran 2> /dev/null; then ok "32 nilai secret ber-spasi dan substitusi perintah utuh (tidak dieksekusi)"; else bad "32 nilai env berubah"; fi

# ── SEC-50 (gelombang 13): topologi dipin di deploy.conf, config di manifest bertanda tangan ───────────────────
if grep -qxF 'WHATSAPP_OTP_TEMPLATE=efn_otp_v2' /tmp/deploy-ran && grep -qxF 'EFN_WG_BIND=10.88.0.1' /tmp/deploy-ran \
   && grep -qxF 'EFN_PUBLIC_BIND=203.0.113.10' /tmp/deploy-ran && grep -qxF 'APP_ENV=production' /tmp/deploy-ran \
   && grep -qxF 'COMPOSE_PROFILES=core,data,ai,observability,ops' /tmp/deploy-ran; then
  ok "33 config manifest + topologi deploy.conf (default) diteruskan ke deploy.sh"; else bad "33 config/topologi ke deploy.sh"; fi
SEQ=50
neg_env() { # nama pola NAME=nilai...
  local name="$1" pat="$2"; shift 2
  SEQ=$((SEQ + 1)); make_release "N$SEQ" "$SEQ" 1.3.0; make_env "N$SEQ" "$@"; run "N$SEQ"; expect_fail "$name" "$pat"
}
neg_man() { # nama pola jq-filter
  SEQ=$((SEQ + 1)); make_release "N$SEQ" "$SEQ" 1.3.0 "$3"; make_env "N$SEQ"; run "N$SEQ"; expect_fail "$1" "$2"
}
neg_conf() { # nama pola baris-conf...
  local name="$1" pat="$2"; shift 2
  write_conf "" "$@"
  SEQ=$((SEQ + 1)); make_release "N$SEQ" "$SEQ" 1.3.0; make_env "N$SEQ"; run "N$SEQ"; expect_fail "$name" "$pat"
  write_conf
}
# (a) stdin tidak bisa membawa topologi / konfigurasi
neg_env "34 EFN_WG_BIND lewat stdin ditolak" "variabel tidak diizinkan: EFN_WG_BIND" "EFN_WG_BIND=203.0.113.10"
neg_env "35 EFN_WG_STAFF_CIDR lewat stdin ditolak" "variabel tidak diizinkan: EFN_WG_STAFF_CIDR" "EFN_WG_STAFF_CIDR=0.0.0.0/0"
neg_env "36 ETL_AUDIT_COPY_ENABLED lewat stdin ditolak" "variabel tidak diizinkan: ETL_AUDIT_COPY_ENABLED" "ETL_AUDIT_COPY_ENABLED=false"
neg_env "37 PAYMENT_PROVIDER lewat stdin ditolak" "variabel tidak diizinkan: PAYMENT_PROVIDER" "PAYMENT_PROVIDER=mock"
# (b) nilai multi-baris / karakter kontrol
neg_env "38 secret multi-baris (injeksi .env) ditolak" "karakter kontrol" \
  "JWT_SECRET=$(printf 'abc\nIMAGE_ODOO=evil.example/odoo@sha256:%s' "$(dg evil)")"
neg_env "39 secret dengan CR/ESC ditolak" "karakter kontrol" "JWT_SECRET=$(printf 'abc\r\033[2Jx')"
neg_man "40 config manifest multi-baris ditolak" "skema" '.config.RABBITMQ_STRICT_PERMISSIONS = "true\nIMAGE_ODOO=evil"'
neg_man "41 kunci config manifest tidak dikenal ditolak" "skema" '.config.IMAGE_ODOO = "evil.example/odoo"'
neg_man "42 manifest schema 1 (tanpa config) ditolak" "skema" '.schema = 1 | del(.config)'
neg_man "43 config bukan string ditolak" "skema" '.config.ETL_ENABLE_GL = true'
# shellcheck disable=SC2016 # literal $(id) disengaja
neg_man "44 WHATSAPP_OTP_TEMPLATE berisi \$(id) ditolak" "config manifest rilis tidak valid" '.config.WHATSAPP_OTP_TEMPLATE = "x $(id)"'
neg_man "45 ACME_SERVER selain Let's Encrypt ditolak" "config manifest rilis tidak valid" '.config.ACME_SERVER = "https://evil.example/dir"'
# (c) kontrol keamanan production
neg_man "46 production ETL_AUDIT_COPY_ENABLED=false ditolak" "config manifest rilis tidak valid" '.config.ETL_AUDIT_COPY_ENABLED = "false"'
neg_man "47 production RABBITMQ_STRICT_PERMISSIONS=false ditolak" "config manifest rilis tidak valid" '.config.RABBITMQ_STRICT_PERMISSIONS = "false"'
neg_man "48 production PAYMENT_PROVIDER=mock ditolak" "config manifest rilis tidak valid" '.config.PAYMENT_PROVIDER = "mock"'
# (a) topologi deploy.conf
neg_conf "49 EFN_WG_BIND = IP publik ditolak" "topologi" "EFN_WG_BIND=203.0.113.11"
neg_conf "50 EFN_WG_BIND = 0.0.0.0 ditolak" "topologi" "EFN_WG_BIND=0.0.0.0"
neg_conf "51 EFN_WG_STAFF_CIDR=0.0.0.0/0 ditolak" "topologi" "EFN_WG_STAFF_CIDR=0.0.0.0/0"
neg_conf "52 EFN_WG_OUTLET_CIDR=10.0.0.0/8 (terlalu lebar) ditolak" "topologi" "EFN_WG_OUTLET_CIDR=10.0.0.0/8"
neg_conf "53 CIDR di luar subnet WG ditolak" "topologi" "EFN_WG_STAFF_CIDR=192.168.1.0/24"
neg_conf "54 COMPOSE_PROFILES tanpa observability (production) ditolak" "topologi" "COMPOSE_PROFILES=core,data,ai"
neg_conf "55 EFN_PUBLIC_BIND di subnet WG ditolak" "topologi" "EFN_PUBLIC_BIND=10.88.0.5"
neg_conf "56 EFN_ALLOW_CONTROL_OFF nama tak dikenal ditolak" "topologi" "EFN_ALLOW_CONTROL_OFF='PAYMENT_PROVIDER'"
write_conf; printf 'EFN_WG_BIND=10.88.0.1\r\n' >> /etc/efn/deploy.conf
SEQ=$((SEQ + 1)); make_release "N$SEQ" "$SEQ" 1.3.0; make_env "N$SEQ"; run "N$SEQ"; expect_fail "57 karakter kontrol di deploy.conf ditolak" "karakter kontrol"
write_conf
# Env pemanggil (mis. sudo tanpa env_reset) tidak bisa menimpa pin topologi
SEQ=$((SEQ + 1)); make_release "N$SEQ" "$SEQ" 1.3.0; make_env "N$SEQ"
rm -f /tmp/deploy-ran; set +e
OUT=$(tar -C "N$SEQ" -cf - release.tar.gz release.sigstore.json env.b64 | EFN_WG_BIND=203.0.113.11 EFN_WG_STAFF_CIDR=0.0.0.0/0 /usr/local/sbin/efn-deploy 2>&1); RC=$?
set -e
if [[ $RC -eq 0 ]] && grep -qxF 'EFN_WG_BIND=10.88.0.1' /tmp/deploy-ran && grep -qxF 'EFN_WG_STAFF_CIDR=10.89.0.0/16' /tmp/deploy-ran; then
  ok "58 env pemanggil tidak menimpa topologi deploy.conf"; else bad "58 env pemanggil menimpa topologi"; fi
LAST_OK=$SEQ
if [[ "$(marker)" == "marker-$LAST_OK" ]]; then ok "59 /opt/efn hanya berubah oleh rilis yang diterima"; else bad "59 marker $(marker)"; fi
# Izin darurat Superadmin: ETL_AUDIT_COPY_ENABLED=false hanya bila dicantumkan di deploy.conf
write_conf "" "EFN_ALLOW_CONTROL_OFF='ETL_AUDIT_COPY_ENABLED'" "EFN_WG_BIND=10.89.0.1" "EFN_WG_OUTLET_CIDR=10.88.0.0/16" \
  "EFN_WG_STAFF_CIDR=10.89.0.0/24" "COMPOSE_PROFILES=core,data,observability"
SEQ=$((SEQ + 1)); make_release "N$SEQ" "$SEQ" 1.3.0 '.config.ETL_AUDIT_COPY_ENABLED = "false"'; make_env "N$SEQ"; run "N$SEQ"
expect_ok "60 ETL_AUDIT_COPY_ENABLED=false dengan EFN_ALLOW_CONTROL_OFF di deploy.conf diterima"
if grep -qxF 'EFN_WG_BIND=10.89.0.1' /tmp/deploy-ran 2> /dev/null && grep -qxF 'EFN_WG_STAFF_CIDR=10.89.0.0/24' /tmp/deploy-ran; then
  ok "61 topologi kustom deploy.conf diteruskan"; else bad "61 topologi kustom"; fi
SEQ=$((SEQ + 1)); make_release "N$SEQ" "$SEQ" 1.3.0 '.config.RABBITMQ_STRICT_PERMISSIONS = "false"'; make_env "N$SEQ"; run "N$SEQ"
expect_fail "62 izin satu kontrol tidak membuka kontrol lain" "config manifest rilis tidak valid"
write_conf

# ── Unit: scripts/lib/deploy-validate.sh (dipakai deploy.sh juga) ──────────────────────────────────────────────
# shellcheck source=scripts/lib/deploy-validate.sh
source "$SRC/scripts/lib/deploy-validate.sh"
u() { # nama harapan(0|1) perintah...
  local name="$1" want="$2"; shift 2
  local got=0; "$@" 2> /dev/null || got=1
  RC=$got OUT=""
  if [[ "$got" == "$want" ]]; then ok "$name"; else bad "$name"; fi
}
u "63 efn_is_cidr menolak bit host (10.88.0.1/16)" 1 efn_is_cidr 10.88.0.1/16
u "64 efn_is_ipv4 menolak oktet > 255 / nol di depan" 1 efn_is_ipv4 10.88.0.256
u "65 efn_is_ipv4 menolak nol di depan" 1 efn_is_ipv4 10.088.0.1
u "66 efn_cidr_within 10.88.4.0/24 di 10.88.0.0/15" 0 efn_cidr_within 10.88.4.0/24 10.88.0.0/15
DOT=$(mktemp -d)
u "67 .env: nilai multi-baris ditolak" 1 efn_write_dotenv "$DOT/.env" "A=1" "B=$(printf 'x
IMAGE_ODOO=evil')"
u "68 .env: kunci ganda ditolak" 1 efn_write_dotenv "$DOT/.env" "A=1" "A=2"
u "69 .env: nilai sah ditulis" 0 efn_write_dotenv "$DOT/.env" "A=1" "B=x y \$(id)"
if [[ "$(cat "$DOT/.env")" == $'A=1
B=x y $(id)' && "$(stat -c %a "$DOT/.env")" == 640 ]]; then ok "70 .env isi & mode 0640"; else bad "70 .env isi"; fi
printf "EFN_WG_BIND=10.88.0.1
EFN_WG_STAFF_CIDR='10.89.0.0/16'
" > "$DOT/conf"; chmod 0644 "$DOT/conf"
u "71 pin deploy.conf: env berbeda (jalur manual) ditolak" 1 env EFN_WG_BIND=203.0.113.11 bash -c "source '$SRC/scripts/lib/deploy-validate.sh'; efn_load_pinned_conf '$DOT/conf'"
u "72 pin deploy.conf: env sama diterima" 0 env EFN_WG_BIND=10.88.0.1 bash -c "source '$SRC/scripts/lib/deploy-validate.sh'; efn_load_pinned_conf '$DOT/conf' && [[ \$EFN_WG_STAFF_CIDR == 10.89.0.0/16 ]]"
chmod 0666 "$DOT/conf"
u "73 pin deploy.conf world-writable ditolak" 1 bash -c "source '$SRC/scripts/lib/deploy-validate.sh'; efn_load_pinned_conf '$DOT/conf'"
rm -rf "$DOT"
u "74 staging boleh ETL_AUDIT_COPY_ENABLED=false" 0 env ACME_EMAIL=a@b.co PAYMENT_PROVIDER=mock ETL_AUDIT_COPY_ENABLED=false \
  bash -c "source '$SRC/scripts/lib/deploy-validate.sh'; efn_validate_config staging"
# G14: cutoff rekonsiliasi loyalty (manifest config) - UTC kanonik, tidak di masa depan
for c in "75|0|2026-10-07T07:09:00Z" "76|1|2026-10-07 07:09:00" "77|1|2026-10-07T07:09:00+07:00" "78|1|2099-01-01T00:00:00Z" \
  "79|1|2026-13-01T00:00:00Z"; do
  IFS='|' read -r n want val <<< "$c"
  u "$n ETL_LOYALTY_RECON_NOT_BEFORE=$val" "$want" env ACME_EMAIL=a@b.co PAYMENT_PROVIDER=disabled \
    ETL_LOYALTY_RECON_NOT_BEFORE="$val" bash -c "source '$SRC/scripts/lib/deploy-validate.sh'; efn_validate_config production"
done

echo "RINGKASAN efn-deploy: PASS=$PASS FAIL=$FAIL"
[[ $FAIL -eq 0 ]]
