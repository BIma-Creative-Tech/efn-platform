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
bundle="" ident="" last="" wsha="" wrepo=""
while (($#)); do
  case "$1" in
    --bundle) bundle="$2"; shift 2 ;;
    --certificate-identity) ident="$2"; shift 2 ;;
    --certificate-github-workflow-sha) wsha="$2"; shift 2 ;;
    --certificate-github-workflow-repository) wrepo="$2"; shift 2 ;;
    --certificate-identity-regexp|--certificate-oidc-issuer) shift 2 ;;
    *) last="$1"; shift ;;
  esac
done
# SEC-91: image "ditandatangani" oleh build commit /etc/fake-cosign/sha di repo /etc/fake-cosign/repo; flag wajib ada.
case "$cmd" in
  verify-blob) [[ "$(cat "$bundle")" == "VALID:$(sha256sum "$last" | cut -d' ' -f1)" ]] ;;
  verify) grep -qxF "$last" /etc/fake-cosign/signed && [[ "$ident" == "$(cat /etc/fake-cosign/identity)" ]] \
            && [[ -n "$wsha" && "$wsha" == "$(cat /etc/fake-cosign/sha)" ]] \
            && [[ -n "$wrepo" && "$wrepo" == "$(cat /etc/fake-cosign/repo)" ]] \
            && [[ -d "${DOCKER_CONFIG:-/nonexistent}" ]] ;;
  *) exit 1 ;;
esac
EOF
chmod 0755 /usr/local/bin/cosign
echo "$ID_OK" > /etc/fake-cosign/identity
GIT_OK=$(printf 'a%.0s' {1..40})
echo "$GIT_OK" > /etc/fake-cosign/sha
echo "$REPO" > /etc/fake-cosign/repo
# SEC-92: persetujuan rilis production out-of-band (Superadmin) untuk versi yang dipakai kasus uji
mkdir -p /etc/efn
approve() { printf '# disetujui Superadmin (uji)\n'; for v in "$@"; do printf '%s %s\n' "$v" "$GIT_OK"; done; }
approve 1.2.0 1.1.0 1.3.0 > /etc/efn/approved-release; chmod 0644 /etc/efn/approved-release

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
{ env | grep -E '^(IMAGE_|APP_ENV=|EFN_RELEASE_|WHATSAPP_OTP_TEMPLATE=|EFN_WG_|EFN_PUBLIC_BIND=|COMPOSE_PROFILES=|TRAEFIK_DOMAIN=|PAYMENT_PROVIDER=|JWT_SECRET=|EFN_ALLOW_FEATURE_ON=|LOYALTY_EXPIRY_ENABLED=|FNB_QR_ENABLED=|LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT=|ETL_ENABLE_KDS=|BOOKING_ENABLED=|ONLINE_SLOTS_ENABLED=)' | sort; } > /tmp/deploy-ran
# SEC-120: kunci host bersama dipegang efn-deploy (fd 8 diwarisi) selama deploy.sh berjalan
if flock -n /run/efn/deploy.lock true; then echo LOCK=free; else echo LOCK=held; fi >> /tmp/deploy-ran
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
if grep -qx 'LOCK=held' /tmp/deploy-ran 2> /dev/null; then ok "02b kunci host /run/efn/deploy.lock dipegang selama deploy.sh (SEC-120)"; else bad "02b kunci host"; fi
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
# W-11: API key Odoo kanal online dikelola DI HOST (secrets.list host:*) -> tidak boleh datang dari stdin CI
# G20: kanal default GL (secrets.list host:disabled) - nama ada di secrets.list tetapi host:* -> tetap ditolak
neg_env "110 ODOO_API_KEY_ONLINE_GL lewat stdin ditolak (W-11, host:*)" "variabel tidak diizinkan: ODOO_API_KEY_ONLINE_GL" \
  "ODOO_API_KEY_ONLINE_GL=0123456789abcdef0123456789abcdef01234567"  # gitleaks:allow (kunci uji palsu)
# SEC-120 (L-25h): rotasi API key memegang kunci host -> efn-deploy menunggu lalu gagal, /opt/efn tidak disentuh
install -d -m 0700 /run/efn; exec 6> /run/efn/deploy.lock; flock -n 6
SEQ=$((SEQ + 1)); make_release "N$SEQ" "$SEQ" 1.3.0; make_env "N$SEQ"; EFN_DEPLOY_LOCK_WAIT=2 run "N$SEQ"
expect_fail "110b kunci host dipegang rotasi -> deploy ditolak setelah menunggu (SEC-120)" "dipegang proses lain"
exec 6>&-
# G21 AC-13: kunci PII Odoo produksi = host:disabled [SA] (dibuat Superadmin di host) -> TIDAK pernah dari stdin CI
neg_env "110c ODOO_PII_KEY_V1 lewat stdin ditolak (AC-13, host:*)" "variabel tidak diizinkan: ODOO_PII_KEY_V1" \
  "ODOO_PII_KEY_V1=$(openssl rand -base64 32)"
neg_env "110d ODOO_PII_BIDX_KEY lewat stdin ditolak (AC-13, host:*)" "variabel tidak diizinkan: ODOO_PII_BIDX_KEY" \
  "ODOO_PII_BIDX_KEY=$(openssl rand -base64 32)"
neg_env "110e ODOO_PII_NAME_KEY lewat stdin ditolak (AC-13, host:*)" "variabel tidak diizinkan: ODOO_PII_NAME_KEY" \
  "ODOO_PII_NAME_KEY=$(openssl rand -base64 32)"
# G21 §13.1 no. 3: loyalty_policy_signing_key = secret GitHub Environment biasa (random) -> diterima dari stdin
SEQ=$((SEQ + 1)); make_release "N$SEQ" "$SEQ" 1.3.0; make_env "N$SEQ" "LOYALTY_POLICY_SIGNING_KEY=$(openssl rand -base64 36)"
run "N$SEQ"; expect_ok "110f LOYALTY_POLICY_SIGNING_KEY lewat stdin diterima (secret GitHub Environment)"
# Saklar PII ada di config manifest bertanda tangan (bukan stdin); production ENABLED tanpa REQUIRED=true ditolak
neg_env "110g EFN_ODOO_PII_ENABLED lewat stdin ditolak (SEC-50)" "variabel tidak diizinkan: EFN_ODOO_PII_ENABLED" \
  "EFN_ODOO_PII_ENABLED=true"
neg_man "110h production config EFN_ODOO_PII_ENABLED=true tanpa REQUIRED ditolak (AC-13)" "config manifest rilis tidak valid" \
  '.config.EFN_ODOO_PII_ENABLED = "true"'
neg_man "110i production config EFN_ODOO_PII_ENABLED=true + REQUIRED=false ditolak (AC-13)" "config manifest rilis tidak valid" \
  '.config.EFN_ODOO_PII_ENABLED = "true" | .config.EFN_ODOO_PII_REQUIRED = "false"'
SEQ=$((SEQ + 1)); make_release "N$SEQ" "$SEQ" 1.3.0 '.config.EFN_ODOO_PII_ENABLED = "true" | .config.EFN_ODOO_PII_REQUIRED = "true"'
make_env "N$SEQ"; run "N$SEQ"; expect_ok "110j production config PII ENABLED + REQUIRED=true diterima (AC-13)"
# ── G22 (docs/04 §14): secret baru, D-22-18, L-73 (fitur menunggu persetujuan klien), QR meja ───────────────────────
# fnb_table_token_key / odoo_fnb_event_hmac = secret GitHub Environment biasa (random) -> diterima dari stdin
SEQ=$((SEQ + 1)); make_release "N$SEQ" "$SEQ" 1.3.0
make_env "N$SEQ" "FNB_TABLE_TOKEN_KEY=$(openssl rand -base64 36)" "ODOO_FNB_EVENT_HMAC=$(openssl rand -base64 36)"
run "N$SEQ"; expect_ok "170 FNB_TABLE_TOKEN_KEY + ODOO_FNB_EVENT_HMAC lewat stdin diterima (secret GitHub Environment)"
# API key QR per PT = host:* (dibuat Odoo, efn-rotate-online-key --channel=QR-<PT>) -> TIDAK pernah dari stdin CI
neg_env "171 ODOO_API_KEY_QR_PB lewat stdin ditolak (host:*)" "variabel tidak diizinkan: ODOO_API_KEY_QR_PB" \
  "ODOO_API_KEY_QR_PB=0123456789abcdef0123456789abcdef01234567"  # gitleaks:allow (kunci uji palsu)
neg_env "172 EFN_ALLOW_FEATURE_ON lewat stdin ditolak (dipin deploy.conf)" "variabel tidak diizinkan: EFN_ALLOW_FEATURE_ON" \
  "EFN_ALLOW_FEATURE_ON=LOYALTY_EXPIRY_ENABLED"
neg_env "173 LOYALTY_EXPIRY_ENABLED lewat stdin ditolak (config manifest)" "variabel tidak diizinkan: LOYALTY_EXPIRY_ENABLED" \
  "LOYALTY_EXPIRY_ENABLED=true"
# D-22-18: LOYALTY_POINT_EXPIRY_DAYS bukan kunci config rilis (dihapus) -> manifest ditolak skema
neg_man "174 config manifest LOYALTY_POINT_EXPIRY_DAYS (dihapus D-22-18) ditolak" "skema" '.config.LOYALTY_POINT_EXPIRY_DAYS = "365"'
# L-73: job hangus OFF di production tanpa izin Superadmin di deploy.conf (manifest bertanda tangan saja tidak cukup)
neg_man "175 production LOYALTY_EXPIRY_ENABLED=true tanpa EFN_ALLOW_FEATURE_ON ditolak (L-73)" "config manifest rilis tidak valid" \
  '.config.LOYALTY_EXPIRY_ENABLED = "true" | .config.LOYALTY_ACTIVITY_EPOCH = "2026-11-01"'
write_conf "" "EFN_ALLOW_FEATURE_ON='LOYALTY_EXPIRY_ENABLED'"
SEQ=$((SEQ + 1))
make_release "N$SEQ" "$SEQ" 1.3.0 '.config.LOYALTY_EXPIRY_ENABLED = "true" | .config.LOYALTY_ACTIVITY_EPOCH = "2026-11-01"'
make_env "N$SEQ"; run "N$SEQ"
expect_ok "176 production LOYALTY_EXPIRY_ENABLED=true dengan EFN_ALLOW_FEATURE_ON di deploy.conf diterima (L-73)"
if grep -qxF 'EFN_ALLOW_FEATURE_ON=LOYALTY_EXPIRY_ENABLED' /tmp/deploy-ran 2> /dev/null && grep -qxF 'LOYALTY_EXPIRY_ENABLED=true' /tmp/deploy-ran; then
  ok "177 izin fitur (deploy.conf) + config manifest diteruskan ke deploy.sh"; else bad "177 izin fitur ke deploy.sh"; fi
SEQ=$((SEQ + 1))
make_release "N$SEQ" "$SEQ" 1.3.0 '.config.LOYALTY_EXPIRY_ENABLED = "true" | .config.LOYALTY_ACTIVITY_EPOCH = "2026-11-01" | .config.LOYALTY_INACTIVE_ANONYMIZE = "true"'
make_env "N$SEQ"; run "N$SEQ"
expect_fail "178 izin LOYALTY_EXPIRY_ENABLED tidak membuka LOYALTY_INACTIVE_ANONYMIZE (L-73)" "config manifest rilis tidak valid"
write_conf
neg_conf "179 EFN_ALLOW_FEATURE_ON nama tak dikenal (FNB_QR_ENABLED) ditolak" "topologi" "EFN_ALLOW_FEATURE_ON='FNB_QR_ENABLED'"
# SEC-171 (r15): filter consent notice hangus - production `false` hanya dengan izin deploy.conf (gerbang sama L-73)
neg_man "179b production LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT=false tanpa EFN_ALLOW_FEATURE_ON ditolak (SEC-171)" \
  "config manifest rilis tidak valid" '.config.LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT = "false"'
write_conf "" "EFN_ALLOW_FEATURE_ON='LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT'"
SEQ=$((SEQ + 1))
make_release "N$SEQ" "$SEQ" 1.3.0 '.config.LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT = "false"'
make_env "N$SEQ"; run "N$SEQ"
expect_ok "179c production LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT=false dengan EFN_ALLOW_FEATURE_ON di deploy.conf diterima"
if grep -qxF 'LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT=false' /tmp/deploy-ran 2> /dev/null; then
  ok "179d nilai consent dari manifest diteruskan ke deploy.sh"; else bad "179d consent ke deploy.sh"; fi
write_conf
# QA G22 P2-3: job ETL opsional = kunci config manifest (bukan stdin), diteruskan ke deploy.sh
SEQ=$((SEQ + 1))
make_release "N$SEQ" "$SEQ" 1.3.0 '.config.ETL_ENABLE_KDS = "true" | .config.ETL_KDS_ONLINE_ENABLED = "true"'
make_env "N$SEQ"; run "N$SEQ"
expect_ok "179e production ETL_ENABLE_KDS + ETL_KDS_ONLINE_ENABLED di manifest diterima (ETL_ENABLE_ODOO kosong = setelah grant)"
if grep -qxF 'ETL_ENABLE_KDS=true' /tmp/deploy-ran 2> /dev/null; then
  ok "179f ETL_ENABLE_KDS dari manifest diteruskan ke deploy.sh"; else bad "179f ETL_ENABLE_KDS ke deploy.sh"; fi
neg_env "179g ETL_ENABLE_KDS lewat stdin ditolak (config manifest)" "variabel tidak diizinkan: ETL_ENABLE_KDS" "ETL_ENABLE_KDS=true"
neg_man "179h manifest ETL_ENABLE_KDS=true + ETL_ENABLE_ODOO=false ditolak" "config manifest rilis tidak valid" \
  '.config.ETL_ENABLE_KDS = "true" | .config.ETL_ENABLE_ODOO = "false"'
# QR meja (§14.A.10 API8): aktif tanpa URL tenant https ditolak di manifest
neg_man "180 production FNB_QR_ENABLED=true tanpa FNB_QR_BASE_URL_* ditolak" "config manifest rilis tidak valid" \
  '.config.FNB_QR_ENABLED = "true"'
SEQ=$((SEQ + 1))
make_release "N$SEQ" "$SEQ" 1.3.0 '.config.FNB_QR_ENABLED = "true" | .config.FNB_QR_COMPANIES = "PB" | .config.FNB_QR_BASE_URL_PB = "https://pb.efn.example"'
make_env "N$SEQ"; run "N$SEQ"
expect_ok "181 production FNB_QR_ENABLED=true + PB + URL https diterima (cek API key QR = deploy.sh di host)"
# G23 (docs/04 §15): reservasi & slot pre-order = kunci config manifest; validasi daftar PT dan rentang angka
SEQ=$((SEQ + 1))
make_release "N$SEQ" "$SEQ" 1.3.0 '.config.BOOKING_ENABLED = "true" | .config.BOOKING_COMPANIES = "PB" | .config.ONLINE_SLOTS_ENABLED = "true"'
make_env "N$SEQ"; run "N$SEQ"
expect_ok "182 production BOOKING_ENABLED=true + PB + ONLINE_SLOTS_ENABLED=true diterima (cek API key booking = deploy.sh di host)"
if grep -qxF 'BOOKING_ENABLED=true' /tmp/deploy-ran 2> /dev/null && grep -qxF 'ONLINE_SLOTS_ENABLED=true' /tmp/deploy-ran 2> /dev/null; then
  ok "183 BOOKING_ENABLED/ONLINE_SLOTS_ENABLED dari manifest diteruskan ke deploy.sh"; else bad "183 BOOKING ke deploy.sh"; fi
neg_man "184 BOOKING_COMPANIES bukan PT F&B (GL) ditolak" "config manifest rilis tidak valid" '.config.BOOKING_COMPANIES = "PB,GL"'
neg_man "185 BOOKING_COMPANIES ganda ditolak" "config manifest rilis tidak valid" '.config.BOOKING_COMPANIES = "PB,PB"'
neg_man "186 BOOKING_ENABLED bukan true|false ditolak" "config manifest rilis tidak valid" '.config.BOOKING_ENABLED = "yes"'
neg_man "187 BOOKING_MAX_ACTIVE_PER_MEMBER di luar 1..10 ditolak" "config manifest rilis tidak valid" '.config.BOOKING_MAX_ACTIVE_PER_MEMBER = "50"'
neg_man "188 BOOKING_MEMBER_LINK_DAYS > BOOKING_RETENTION_DAYS ditolak" "config manifest rilis tidak valid" \
  '.config.BOOKING_MEMBER_LINK_DAYS = "300" | .config.BOOKING_RETENTION_DAYS = "200"'
neg_env "189 BOOKING_ENABLED lewat stdin ditolak (config manifest)" "variabel tidak diizinkan: BOOKING_ENABLED" "BOOKING_ENABLED=true"
neg_man "40 config manifest multi-baris ditolak" "skema" '.config.RABBITMQ_STRICT_PERMISSIONS = "true\nIMAGE_ODOO=evil"'
neg_man "41 kunci config manifest tidak dikenal ditolak" "skema" '.config.IMAGE_ODOO = "evil.example/odoo"'
neg_man "101 saklar dev ODOO_ONLINE_MOCK di config manifest ditolak (G17)" "skema" '.config.ODOO_ONLINE_MOCK = "true"'
neg_man "101b saklar dev ETL_AUDIT_EPOCH_AUTO di config manifest ditolak (G18)" "skema" '.config.ETL_AUDIT_EPOCH_AUTO = "true"'
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

# ── SEC-91 / SEC-92 (r9 re-review G16c): image terikat SHA commit; persetujuan rilis production out-of-band ──────
# (a) image bertanda tangan sah tetapi dari build commit lain (manifest menunjuk commit c..c yang disetujui)
GIT_C=$(printf 'c%.0s' {1..40})
{ approve 1.2.0 1.1.0 1.3.0; echo "1.3.0 ${GIT_C}"; } > /etc/efn/approved-release
SEQ=$((SEQ + 1)); make_release "N$SEQ" "$SEQ" 1.3.0 ".git_sha = \"${GIT_C}\""; make_env "N$SEQ"; run "N$SEQ"
expect_fail "80 image dari build commit lain (sha manifest != sertifikat) ditolak" "cosign verify GAGAL untuk IMAGE_POSTGRES"
approve 1.2.0 1.1.0 1.3.0 > /etc/efn/approved-release
# (b) repo di sertifikat berbeda (fork/repo lain dengan nama workflow sama)
echo "evil/efn-platform" > /etc/fake-cosign/repo
SEQ=$((SEQ + 1)); make_release "N$SEQ" "$SEQ" 1.3.0; make_env "N$SEQ"; run "N$SEQ"
expect_fail "81 image dari repo lain ditolak" "cosign verify GAGAL"
echo "$REPO" > /etc/fake-cosign/repo
# (c) tanpa file persetujuan -> production ditolak
mv /etc/efn/approved-release /etc/efn/approved-release.bak
SEQ=$((SEQ + 1)); make_release "N$SEQ" "$SEQ" 1.3.0; make_env "N$SEQ"; run "N$SEQ"
expect_fail "82 production tanpa /etc/efn/approved-release ditolak" "persetujuan rilis production tidak ada"
mv /etc/efn/approved-release.bak /etc/efn/approved-release
# (d) versi disetujui tetapi SHA commit lain (tag v* dari commit di luar main)
SEQ=$((SEQ + 1)); make_release "N$SEQ" "$SEQ" 1.3.0 ".git_sha = \"${GIT_C}\""; make_env "N$SEQ"; run "N$SEQ"
expect_fail "83 SHA commit tidak disetujui ditolak" "persetujuan rilis production tidak cocok"
# (e) versi tidak disetujui (SHA sama)
echo "https://github.com/${REPO}/.github/workflows/build-push.yml@refs/tags/v1.4.0" > /etc/fake-cosign/identity
SEQ=$((SEQ + 1)); make_release "N$SEQ" "$SEQ" 1.4.0; make_env "N$SEQ"; run "N$SEQ"
expect_fail "84 versi tidak disetujui ditolak" "persetujuan rilis production tidak cocok"
# (f) file persetujuan bisa ditulis group/other -> ditolak
{ approve 1.2.0 1.1.0 1.3.0 1.4.0; } > /etc/efn/approved-release; chmod 0666 /etc/efn/approved-release
SEQ=$((SEQ + 1)); make_release "N$SEQ" "$SEQ" 1.4.0; make_env "N$SEQ"; run "N$SEQ"
expect_fail "85 approved-release world-writable ditolak" "tidak bisa ditulis group/other"
# (g) setelah disetujui (0644 root) -> diterima
chmod 0644 /etc/efn/approved-release
SEQ=$((SEQ + 1)); make_release "N$SEQ" "$SEQ" 1.4.0; make_env "N$SEQ"; run "N$SEQ"
expect_ok "86 rilis 1.4.0 disetujui (versi + SHA) diterima"
# (h) staging tidak butuh persetujuan (stage: main sha-*), tetap terikat SHA commit
write_conf "" ; sed -i 's/^EFN_APP_ENV=production$/EFN_APP_ENV=staging/' /etc/efn/deploy.conf
rm -f /etc/efn/approved-release
echo "https://github.com/${REPO}/.github/workflows/build-push.yml@refs/heads/main" > /etc/fake-cosign/identity
SEQ=$((SEQ + 1))
make_release "N$SEQ" "$SEQ" 1.4.0 '.app_env = "staging" | .version = "sha-aaaaaaa" | .signer_ref = "refs/heads/main" | .config.PAYMENT_PROVIDER = "mock"'
make_env "N$SEQ"; run "N$SEQ"
expect_ok "87 staging (main) tanpa approved-release diterima"
echo "$ID_OK" > /etc/fake-cosign/identity
write_conf; approve 1.2.0 1.1.0 1.3.0 1.4.0 > /etc/efn/approved-release; chmod 0644 /etc/efn/approved-release

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

# G17 (docs/04 §10.13): konfigurasi penjualan online di manifest rilis
vc() { # nama harapan app_env VAR=nilai...
  local n="$1" w="$2" e="$3"; shift 3
  u "$n" "$w" env ACME_EMAIL=a@b.co PAYMENT_PROVIDER="${PP:-disabled}" "$@" \
    bash -c "source '$SRC/scripts/lib/deploy-validate.sh'; efn_validate_config $e"
}
vc "88 production ONLINE_ENABLED=true dengan PAYMENT_PROVIDER=disabled ditolak" 1 production ONLINE_ENABLED=true
PP=xendit vc "89 production ONLINE_ENABLED=true dengan provider nyata diterima" 0 production ONLINE_ENABLED=true ONLINE_CHANNELS=GL
vc "89b L-55 production online counter + provider disabled diterima" 0 production ONLINE_ENABLED=true ONLINE_CHANNELS=GL ONLINE_PAYMENT_METHODS=counter
vc "89c L-55 production online qris,counter tanpa vendor ditolak" 1 production ONLINE_ENABLED=true ONLINE_CHANNELS=GL ONLINE_PAYMENT_METHODS=qris,counter
PP=xendit vc "89d L-55 production online qris dengan vendor nyata diterima" 0 production ONLINE_ENABLED=true ONLINE_CHANNELS=GL ONLINE_PAYMENT_METHODS=qris
vc "89e L-55 ONLINE_PAYMENT_METHODS tidak dikenal ditolak" 1 staging ONLINE_PAYMENT_METHODS=card
vc "89f L-55 ONLINE_PAYMENT_METHODS duplikat ditolak" 1 staging ONLINE_PAYMENT_METHODS=counter,counter
vc "90 ONLINE_ENABLED bukan boolean ditolak" 1 staging ONLINE_ENABLED=yes
vc "91 ONLINE_CHANNELS huruf kecil ditolak" 1 staging ONLINE_CHANNELS=gl
vc "92 ONLINE_CHANNELS GL,PB,BCR,BCF diterima (G20 kode kanal = kode PT)" 0 staging ONLINE_CHANNELS=GL,PB,BCR,BCF
# G20 (docs/04 §12.1): kode lama & kanal G19 berhifen dihapus -> allowlist EFN_ONLINE_CHANNEL_CODES
vc "92b ONLINE_CHANNELS=RTL (kode lama) ditolak" 1 staging ONLINE_CHANNELS=RTL
vc "92c ONLINE_CHANNELS=GL,FNB-PB (kanal G19) ditolak" 1 production ONLINE_CHANNELS=GL,FNB-PB
vc "92d ONLINE_CHANNELS kanal tak dikenal (FNB-BAKSO) ditolak" 1 staging ONLINE_CHANNELS=GL,FNB-BAKSO
vc "92e ONLINE_CHANNELS ganda ditolak" 1 staging ONLINE_CHANNELS=PB,PB
vc "92f ONLINE_CHANNELS=BCF saja diterima" 0 production ONLINE_CHANNELS=BCF
# G24 (docs/04 §16.1, L-91..L-93): WTZ = kanal ke-5 (overlay online-wtz); bukan PT QR meja/reservasi (L-92)
vc "92k ONLINE_CHANNELS=GL,PB,BCR,BCF,WTZ diterima (G24)" 0 production ONLINE_CHANNELS=GL,PB,BCR,BCF,WTZ
vc "92l ONLINE_CHANNELS=WTZ saja diterima" 0 staging ONLINE_CHANNELS=WTZ
vc "92m ONLINE_CHANNELS=wtz (huruf kecil) ditolak" 1 staging ONLINE_CHANNELS=wtz
vc "92n FNB_QR_COMPANIES=PB,WTZ ditolak (L-92)" 1 staging FNB_QR_COMPANIES=PB,WTZ
vc "92o BOOKING_COMPANIES=WTZ ditolak (L-92)" 1 staging BOOKING_COMPANIES=WTZ
u "92p efn_validate_online_channels WTZ" 0 efn_validate_online_channels WTZ
u "92q efn_validate_fnb_qr_companies WTZ ditolak" 1 efn_validate_fnb_qr_companies WTZ
u "92r efn_validate_booking_companies WTZ ditolak" 1 efn_validate_booking_companies WTZ
# G20 W-30 (docs/04 §12.2): batas order bayar di kasir per member
vc "92g ONLINE_MAX_OPEN_COUNTER_ORDERS_PER_MEMBER=1 diterima" 0 production ONLINE_MAX_OPEN_COUNTER_ORDERS_PER_MEMBER=1
vc "92h ONLINE_MAX_OPEN_COUNTER_ORDERS_PER_MEMBER=0 ditolak" 1 production ONLINE_MAX_OPEN_COUNTER_ORDERS_PER_MEMBER=0
vc "92i ONLINE_MAX_OPEN_COUNTER_ORDERS_PER_MEMBER=11 ditolak (plafon 10 = BFF)" 1 staging ONLINE_MAX_OPEN_COUNTER_ORDERS_PER_MEMBER=11 \
  ONLINE_MAX_OPEN_ORDERS_PER_MEMBER=20
vc "92j counter 3 > batas order terbuka default 2 ditolak" 1 staging ONLINE_MAX_OPEN_COUNTER_ORDERS_PER_MEMBER=3
vc "92k counter 3 <= batas order terbuka 4 diterima" 0 staging ONLINE_MAX_OPEN_COUNTER_ORDERS_PER_MEMBER=3 \
  ONLINE_MAX_OPEN_ORDERS_PER_MEMBER=4
vc "93 ONLINE_FULFILMENTS di luar allowlist ditolak" 1 staging ONLINE_FULFILMENTS=pickup,shipping
vc "94 ONLINE_PAYMENT_TIMEOUT_SECONDS < 60 ditolak" 1 staging ONLINE_PAYMENT_TIMEOUT_SECONDS=30
vc "95 ONLINE_PAYMENT_TIMEOUT_SECONDS nol di depan ditolak" 1 staging ONLINE_PAYMENT_TIMEOUT_SECONDS=0900
vc "96 angka online dalam rentang diterima" 0 staging ONLINE_PAYMENT_TIMEOUT_SECONDS=900 ONLINE_QUOTE_TTL_SECONDS=600 \
  ONLINE_MAX_OPEN_ORDERS_PER_MEMBER=3 ODOO_RPC_TIMEOUT_MS=8000 ONLINE_EVENT_RETENTION_DAYS=180
vc "97 ODOO_RPC_TIMEOUT_MS > 30000 (timeout gateway) ditolak" 1 staging ODOO_RPC_TIMEOUT_MS=60000
vc "98 production ODOO_ONLINE_MOCK=true ditolak (L-7)" 1 production ODOO_ONLINE_MOCK=true
vc "99 staging EFN_PAYMENT_SIMULATOR=true ditolak (hanya dev)" 1 staging EFN_PAYMENT_SIMULATOR=true
vc "100 default online (semua kosong) diterima" 0 production
vc "102 ODOO_RPC_MAXCONN > 16 ditolak (SEC-96)" 1 staging ODOO_RPC_MAXCONN=32
vc "103 ODOO_RPC_MAXCONN=4 diterima (SEC-96)" 0 staging ODOO_RPC_MAXCONN=4
# Sizing pilot opsi B (10 Okt 2026): pool BFF publik + saga <= maxconn gateway (default efektif prod 1 + 1 <= 2)
vc "104 default pool prod (1+1 <= maxconn 2) diterima" 0 production
vc "105 pool publik 2 + saga default 1 > maxconn default 2 ditolak" 1 production ODOO_RPC_PUBLIC_MAX_CONCURRENCY=2
vc "106 pool 2+2 > maxconn 2 ditolak" 1 staging ODOO_RPC_MAXCONN=2 ODOO_RPC_PUBLIC_MAX_CONCURRENCY=2 ODOO_RPC_SAGA_MAX_CONCURRENCY=2
vc "107 pool 2+2 = maxconn 4 diterima" 0 staging ODOO_RPC_MAXCONN=4 ODOO_RPC_PUBLIC_MAX_CONCURRENCY=2 ODOO_RPC_SAGA_MAX_CONCURRENCY=2
vc "108 maxconn 1 dengan pool default 1+1 ditolak" 1 production ODOO_RPC_MAXCONN=1
vc "109 pool 0 di luar rentang ditolak" 1 staging ODOO_RPC_SAGA_MAX_CONCURRENCY=0
# W-18/W-19 (keputusan 10 Okt 2026): rentang env retensi baru
vc "111 ERASURE_SUPPRESSION_DAYS=0 diterima (tidak disimpan)" 0 production ERASURE_SUPPRESSION_DAYS=0
vc "112 ERASURE_SUPPRESSION_DAYS=365 diterima" 0 production ERASURE_SUPPRESSION_DAYS=365
vc "113 ERASURE_SUPPRESSION_DAYS=731 ditolak" 1 production ERASURE_SUPPRESSION_DAYS=731
vc "114 ERASURE_SUPPRESSION_DAYS=007 ditolak" 1 production ERASURE_SUPPRESSION_DAYS=007
vc "115 ONLINE_ADMIN_AUDIT_RETENTION_DAYS=3650 diterima" 0 production ONLINE_ADMIN_AUDIT_RETENTION_DAYS=3650
vc "116 ONLINE_ADMIN_AUDIT_RETENTION_DAYS=3651 ditolak" 1 production ONLINE_ADMIN_AUDIT_RETENTION_DAYS=3651
vc "117 DSAR_OPEN_ORDER_MAX_DEFER_DAYS=15 ditolak (plafon 14)" 1 production DSAR_OPEN_ORDER_MAX_DEFER_DAYS=15
vc "118 MEMBER_MIN_AGE=16 ditolak" 1 production MEMBER_MIN_AGE=16
vc "119 PRICE_HUNTER_GEO_RETENTION_DAYS=90 diterima" 0 production PRICE_HUNTER_GEO_RETENTION_DAYS=90
vc "120 CH 10 th / pseudonim 5 / geo 90 / Metabase 3650 diterima" 0 production CH_MART_RETENTION_YEARS=10 \
  CH_PSEUDONYM_RETENTION_YEARS=5 CH_GEO_RETENTION_DAYS=90 MB_AUDIT_MAX_RETENTION_DAYS=3650
vc "121 pseudonim > baris mart ditolak" 1 production CH_MART_RETENTION_YEARS=5 CH_PSEUDONYM_RETENTION_YEARS=6
vc "122 pseudonim 11 > default mart 10 ditolak" 1 production CH_PSEUDONYM_RETENTION_YEARS=11
vc "123 CH_MART_RETENTION_YEARS=31 ditolak" 1 production CH_MART_RETENTION_YEARS=31
vc "124 MB_AUDIT_MAX_RETENTION_DAYS=20 ditolak (min 30)" 1 production MB_AUDIT_MAX_RETENTION_DAYS=20
vc "125 production ETL_LOYALTY_POINTS_MODE=product ditolak (SEC-114)" 1 production ETL_LOYALTY_POINTS_MODE=product
vc "126 staging ETL_LOYALTY_POINTS_MODE=product diterima (data historis)" 0 staging ETL_LOYALTY_POINTS_MODE=product
vc "127 production ETL_LOYALTY_POINTS_MODE=payment diterima" 0 production ETL_LOYALTY_POINTS_MODE=payment
vc "128 LOYALTY_RESTORE_TOLERANCE_PCT=0 diterima" 0 production LOYALTY_RESTORE_TOLERANCE_PCT=0
vc "129 LOYALTY_RESTORE_TOLERANCE_PCT=51 ditolak" 1 production LOYALTY_RESTORE_TOLERANCE_PCT=51
vc "130 LOYALTY_COMMITTED_RESERVATION_RETENTION_DAYS=3649 ditolak (min 10 th)" 1 production LOYALTY_COMMITTED_RESERVATION_RETENTION_DAYS=3649
vc "131 LOYALTY_COMMITTED_RESERVATION_RETENTION_DAYS=3650 diterima" 0 production LOYALTY_COMMITTED_RESERVATION_RETENTION_DAYS=3650
vc "131b production ETL_AUDIT_EPOCH_AUTO=true ditolak (dev saja, G18)" 1 production ETL_AUDIT_EPOCH_AUTO=true
vc "131c staging ETL_AUDIT_EPOCH_AUTO=true ditolak (dev saja, G18)" 1 staging ETL_AUDIT_EPOCH_AUTO=true
vc "131d ETL_AUDIT_EPOCH_ANCHORS=20 diterima" 0 production ETL_AUDIT_EPOCH_ANCHORS=20
vc "131e ETL_AUDIT_EPOCH_ANCHORS=1001 ditolak" 1 production ETL_AUDIT_EPOCH_ANCHORS=1001
# SEC-129 (r11): modul Odoo wajib (efn_pos_refund_origin) harus `installed`
mods() { printf '%b' "$1" | efn_odoo_modules_missing > /dev/null; }
u "132 modul wajib installed -> lulus (SEC-129)" 0 mods 'efn_pos_refund_origin|installed\n'
u "133 modul wajib uninstalled -> deploy gagal" 1 mods 'efn_pos_refund_origin|uninstalled\n'
u "134 modul wajib 'to upgrade' -> deploy gagal" 1 mods 'efn_pos_refund_origin|to upgrade\n'
u "135 modul wajib tidak ada di ir_module_module -> deploy gagal" 1 mods 'efn_core|installed\n'
u "136 keluaran psql kosong -> deploy gagal (fail-closed)" 1 mods ''
u "137 baris CRLF/spasi tetap dikenali" 0 mods 'efn_pos_refund_origin|installed\r\n'
if [[ "$(printf 'efn_pos_refund_origin|to install\n' | efn_odoo_modules_missing)" == "efn_pos_refund_origin=to install" ]]; then
  ok "138 pesan menyebut modul + state"; else bad "138 pesan modul"; fi

# ── G21 AC-13 (docs/04 §13.2, ADR-036 §0): saklar PII Odoo produksi + validasi kunci host ──────────────────────────
vc "139 production EFN_ODOO_PII_ENABLED=true tanpa REQUIRED ditolak" 1 production EFN_ODOO_PII_ENABLED=true
vc "140 production PII ENABLED + REQUIRED=false ditolak" 1 production EFN_ODOO_PII_ENABLED=true EFN_ODOO_PII_REQUIRED=false
vc "141 production PII ENABLED + REQUIRED=true diterima" 0 production EFN_ODOO_PII_ENABLED=true EFN_ODOO_PII_REQUIRED=true
vc "142 staging PII ENABLED tanpa REQUIRED diterima (default overlay true)" 0 staging EFN_ODOO_PII_ENABLED=true
vc "143 EFN_ODOO_PII_ENABLED bukan boolean ditolak" 1 staging EFN_ODOO_PII_ENABLED=yes
vc "144 EFN_ODOO_PII_REQUIRED bukan boolean ditolak" 1 production EFN_ODOO_PII_ENABLED=true EFN_ODOO_PII_REQUIRED=1
vc "145 production PII mati (default) diterima (L-59)" 0 production
vc "146 production PII mati + REQUIRED=true diterima" 0 production EFN_ODOO_PII_REQUIRED=true
# SEC-148 (QA G21): mode migrasi efnpii1 tidak boleh menyala di production (jalur manual sudo -E)
vc "146a production EFN_ODOO_PII_ACCEPT_V1=true ditolak (SEC-148)" 1 production EFN_ODOO_PII_ACCEPT_V1=true
vc "146b production EFN_ODOO_PII_ACCEPT_V1=true + PII ENABLED/REQUIRED tetap ditolak" 1 production EFN_ODOO_PII_ENABLED=true EFN_ODOO_PII_REQUIRED=true EFN_ODOO_PII_ACCEPT_V1=true
vc "146c production EFN_ODOO_PII_ACCEPT_V1=false diterima" 0 production EFN_ODOO_PII_ACCEPT_V1=false
vc "146d staging EFN_ODOO_PII_ACCEPT_V1=true diterima (migrasi data PoC)" 0 staging EFN_ODOO_PII_ACCEPT_V1=true
vc "146e EFN_ODOO_PII_ACCEPT_V1 bukan boolean ditolak" 1 staging EFN_ODOO_PII_ACCEPT_V1=1
# Kunci host (nilai acak dibuat di sini; tidak pernah dicetak). mkkeys <dir> -> 3 kunci 32 byte berbeda (.txt)
PK=$(mktemp -d)
mkkeys() {
  rm -rf "$1"; mkdir -p "$1"
  local n
  for n in odoo_pii_key_v1 odoo_pii_bidx_key odoo_pii_name_key; do openssl rand -base64 32 | tr -d '\n' > "$1/$n.txt"; done
}
mkkeys "$PK/ok"
u "147 tiga kunci 32 byte berbeda diterima" 0 efn_validate_pii_keys "$PK/ok"
mkkeys "$PK/nl"; for f in "$PK"/nl/*.txt; do echo >> "$f"; done
u "148 newline penutup diterima" 0 efn_validate_pii_keys "$PK/nl"
mkkeys "$PK/ph"; printf 'disabled' > "$PK/ph/odoo_pii_name_key.txt"
u "149 placeholder 'disabled' ditolak" 1 efn_validate_pii_keys "$PK/ph"
mkkeys "$PK/miss"; rm -f "$PK/miss/odoo_pii_bidx_key.txt"
u "150 kunci hilang ditolak" 1 efn_validate_pii_keys "$PK/miss"
mkkeys "$PK/empty"; : > "$PK/empty/odoo_pii_key_v1.txt"
u "151 kunci kosong ditolak" 1 efn_validate_pii_keys "$PK/empty"
mkkeys "$PK/dup"; cp "$PK/dup/odoo_pii_key_v1.txt" "$PK/dup/odoo_pii_bidx_key.txt"
u "152 dua kunci identik ditolak (SEC-140 c)" 1 efn_validate_pii_keys "$PK/dup"
mkkeys "$PK/short"; openssl rand -base64 16 | tr -d '\n' > "$PK/short/odoo_pii_key_v1.txt"
u "153 kunci 16 byte ditolak" 1 efn_validate_pii_keys "$PK/short"
mkkeys "$PK/hex"; openssl rand -hex 32 | tr -d '\n' > "$PK/hex/odoo_pii_name_key.txt"
u "154 kunci hex (bukan base64 32 byte) ditolak" 1 efn_validate_pii_keys "$PK/hex"
mkkeys "$PK/noncanon"; printf '%s' "$(printf 'A%.0s' {1..42})B=" > "$PK/noncanon/odoo_pii_key_v1.txt"
u "155 base64 non-kanonik (bit sisa != 0) ditolak" 1 efn_validate_pii_keys "$PK/noncanon"
mkkeys "$PK/multi"; { openssl rand -base64 32; openssl rand -base64 32; } > "$PK/multi/odoo_pii_bidx_key.txt"
u "156 file multi-baris ditolak" 1 efn_validate_pii_keys "$PK/multi"
mkkeys "$PK/link"; mv "$PK/link/odoo_pii_key_v1.txt" "$PK/link/real.txt"; ln -s real.txt "$PK/link/odoo_pii_key_v1.txt"
u "157 kunci symlink ditolak" 1 efn_validate_pii_keys "$PK/link"
# Pesan galat tidak memuat nilai kunci
mkkeys "$PK/leak"; cp "$PK/leak/odoo_pii_key_v1.txt" "$PK/leak/odoo_pii_name_key.txt"
leak_msg=$(efn_validate_pii_keys "$PK/leak" 2>&1 || true)
if [[ -n "$leak_msg" && "$leak_msg" != *"$(cat "$PK/leak/odoo_pii_key_v1.txt")"* ]]; then
  ok "158 pesan galat kunci tanpa nilai kunci"; else RC=1 OUT="(pesan disembunyikan)"; bad "158 pesan galat kunci"; fi
rm -rf "$PK"
# Status modul efn_pii di DB vs saklar overlay (deploy.sh, sebelum `up`)
u "159 efn_pii installed tanpa ENABLED -> deploy gagal" 1 efn_pii_module_state_ok false installed
u "160 efn_pii installed + ENABLED -> lulus" 0 efn_pii_module_state_ok true installed
u "161 efn_pii 'to upgrade' tanpa ENABLED -> gagal" 1 efn_pii_module_state_ok false "to upgrade"
u "162 efn_pii 'to remove' tanpa ENABLED -> gagal" 1 efn_pii_module_state_ok false "to remove"
u "163 efn_pii uninstalled tanpa ENABLED -> lulus" 0 efn_pii_module_state_ok false uninstalled
u "164 efn_pii tidak dikenal DB (kosong) -> lulus" 0 efn_pii_module_state_ok false ""
u "165 status ber-CR (psql) tetap dikenali" 1 efn_pii_module_state_ok false $'installed\r'

# ── G22 (docs/04 §14): lib deploy-validate (efn_validate_g22_config lewat efn_validate_config) ───────────────────────
# D-22-18: env dihapus -> production ditolak, staging peringatan (rc 0)
vc "200 production LOYALTY_POINT_EXPIRY_DAYS=365 ditolak (D-22-18)" 1 production LOYALTY_POINT_EXPIRY_DAYS=365
vc "201 production LOYALTY_POINT_EXPIRY_DAYS=0 tetap ditolak (variabel dihapus, bukan nilainya)" 1 production LOYALTY_POINT_EXPIRY_DAYS=0
vc "202 staging LOYALTY_POINT_EXPIRY_DAYS diset -> peringatan saja" 0 staging LOYALTY_POINT_EXPIRY_DAYS=365
warn_msg=$(env ACME_EMAIL=a@b.co PAYMENT_PROVIDER=disabled LOYALTY_POINT_EXPIRY_DAYS=365 \
  bash -c "source '$SRC/scripts/lib/deploy-validate.sh'; efn_validate_config staging" 2>&1 || true)
if [[ "$warn_msg" == *"PERINGATAN LOYALTY_POINT_EXPIRY_DAYS"* ]]; then ok "203 staging: peringatan menyebut LOYALTY_POINT_EXPIRY_DAYS"
else RC=1 OUT="$warn_msg"; bad "203 peringatan D-22-18"; fi
# L-73: job hangus & anonimisasi OFF di production kecuali izin deploy.conf (EFN_ALLOW_FEATURE_ON)
vc "204 production LOYALTY_EXPIRY_ENABLED=true tanpa izin ditolak" 1 production LOYALTY_EXPIRY_ENABLED=true LOYALTY_ACTIVITY_EPOCH=2026-11-01
vc "205 production LOYALTY_EXPIRY_ENABLED=true + izin diterima" 0 production LOYALTY_EXPIRY_ENABLED=true \
  LOYALTY_ACTIVITY_EPOCH=2026-11-01 EFN_ALLOW_FEATURE_ON=LOYALTY_EXPIRY_ENABLED
vc "206 production anonimisasi dengan izin expiry saja ditolak" 1 production LOYALTY_EXPIRY_ENABLED=true \
  LOYALTY_ACTIVITY_EPOCH=2026-11-01 LOYALTY_INACTIVE_ANONYMIZE=true EFN_ALLOW_FEATURE_ON=LOYALTY_EXPIRY_ENABLED
vc "207 production hangus + anonimisasi dengan kedua izin diterima" 0 production LOYALTY_EXPIRY_ENABLED=true \
  LOYALTY_ACTIVITY_EPOCH=2026-11-01 LOYALTY_INACTIVE_ANONYMIZE=true \
  "EFN_ALLOW_FEATURE_ON=LOYALTY_EXPIRY_ENABLED LOYALTY_INACTIVE_ANONYMIZE"
vc "208 production LOYALTY_EXPIRY_ENABLED=false (default) diterima tanpa izin" 0 production LOYALTY_EXPIRY_ENABLED=false
vc "209 staging LOYALTY_EXPIRY_ENABLED=true tanpa izin diterima (gerbang L-73 hanya production)" 0 staging \
  LOYALTY_EXPIRY_ENABLED=true LOYALTY_ACTIVITY_EPOCH=2026-11-01
vc "210 LOYALTY_EXPIRY_ENABLED=true tanpa LOYALTY_ACTIVITY_EPOCH ditolak (D-22-15)" 1 staging LOYALTY_EXPIRY_ENABLED=true
# SEC-175: poin QR & redeem poin online butuh izin Superadmin di production
vc "210a production FNB_QR_POINTS_ENABLED=true tanpa izin ditolak (SEC-175)" 1 production FNB_QR_POINTS_ENABLED=true
vc "210b production FNB_QR_POINTS_ENABLED=true + izin diterima" 0 production FNB_QR_POINTS_ENABLED=true EFN_ALLOW_FEATURE_ON=FNB_QR_POINTS_ENABLED
vc "210c production ONLINE_LOYALTY_REDEEM_ENABLED=true tanpa izin ditolak (SEC-175)" 1 production ONLINE_LOYALTY_REDEEM_ENABLED=true
vc "210d staging FNB_QR_POINTS_ENABLED=true tanpa izin diterima" 0 staging FNB_QR_POINTS_ENABLED=true
vc "210e FNB_SESSION_RETENTION_DAYS=396 ditolak (SEC-176)" 1 staging FNB_SESSION_RETENTION_DAYS=396
vc "210f FNB_SESSION_RETENTION_DAYS=395 diterima" 0 staging FNB_SESSION_RETENTION_DAYS=395
vc "211 LOYALTY_INACTIVE_ANONYMIZE=true tanpa job hangus ditolak" 1 staging LOYALTY_INACTIVE_ANONYMIZE=true
vc "212 LOYALTY_ACTIVITY_EPOCH bulan 13 ditolak" 1 staging LOYALTY_ACTIVITY_EPOCH=2026-13-01
vc "213 LOYALTY_ACTIVITY_EPOCH format waktu ditolak" 1 staging LOYALTY_ACTIVITY_EPOCH=2026-11-01T00:00:00Z
vc "214 LOYALTY_EXPIRY_ENABLED bukan boolean ditolak" 1 staging LOYALTY_EXPIRY_ENABLED=1
vc "215 LOYALTY_INACTIVITY_EXPIRY_MONTHS=5 ditolak (6..36)" 1 staging LOYALTY_INACTIVITY_EXPIRY_MONTHS=5
vc "216 LOYALTY_INACTIVITY_EXPIRY_MONTHS=36 diterima" 0 staging LOYALTY_INACTIVITY_EXPIRY_MONTHS=36
vc "217 LOYALTY_EXPIRY_NOTICE_DAYS=6 ditolak (7..60)" 1 staging LOYALTY_EXPIRY_NOTICE_DAYS=6
vc "218 LOYALTY_EXPIRY_RUN_HOUR_WIB=0 diterima" 0 staging LOYALTY_EXPIRY_RUN_HOUR_WIB=0
vc "219 LOYALTY_EXPIRY_NOTICE_HOUR_WIB=24 ditolak" 1 staging LOYALTY_EXPIRY_NOTICE_HOUR_WIB=24
vc "220 LOYALTY_POINT_MAX_AGE_DAYS=0 diterima (default D-22-18)" 0 production LOYALTY_POINT_MAX_AGE_DAYS=0
vc "221 LOYALTY_POINT_MAX_AGE_DAYS=00 ditolak" 1 production LOYALTY_POINT_MAX_AGE_DAYS=00
vc "222 LOYALTY_EXPIRY_MAX_PER_RUN=1000000 ditolak (maks 999999)" 1 staging LOYALTY_EXPIRY_MAX_PER_RUN=1000000
vc "223 LOYALTY_EXPIRY_BATCH_SIZE=5001 ditolak" 1 staging LOYALTY_EXPIRY_BATCH_SIZE=5001
vc "224 LOYALTY_INACTIVE_ANONYMIZE_GRACE_DAYS=91 ditolak" 1 staging LOYALTY_INACTIVE_ANONYMIZE_GRACE_DAYS=91
vc "225 LOYALTY_EXPIRY_DRY_RUN=false diterima" 0 production LOYALTY_EXPIRY_DRY_RUN=false
# QR meja (§14.A.7/§14.A.9/§14.A.10)
vc "226 FNB_QR_ENABLED=true + URL https kedua PT diterima" 0 production FNB_QR_ENABLED=true \
  FNB_QR_BASE_URL_PB=https://pb.efn.example FNB_QR_BASE_URL_BCF=https://bacha.efn.example/fnb
vc "227 FNB_QR_ENABLED=true tanpa URL BCF (default PB,BCF) ditolak" 1 production FNB_QR_ENABLED=true \
  FNB_QR_BASE_URL_PB=https://pb.efn.example
vc "228 FNB_QR_ENABLED=true hanya PB + URL PB diterima" 0 production FNB_QR_ENABLED=true FNB_QR_COMPANIES=PB \
  FNB_QR_BASE_URL_PB=https://pb.efn.example
# Uji NEGATIF plaintext: skema dirakit dari variabel agar check-encryption.sh/grep URL tidak salah membaca
PLAIN=http
vc "229 FNB_QR_BASE_URL plaintext ditolak" 1 staging "FNB_QR_BASE_URL_PB=${PLAIN}://pb.efn.example"
vc "230 FNB_QR_BASE_URL dengan query ditolak" 1 staging "FNB_QR_BASE_URL_PB=https://pb.efn.example/?ts=1"
vc "231 FNB_QR_BASE_URL dengan fragment ditolak" 1 staging "FNB_QR_BASE_URL_PB=https://pb.efn.example/#t=x"
vc "232 FNB_QR_BASE_URL dengan port ditolak" 1 staging FNB_QR_BASE_URL_PB=https://pb.efn.example:8443
vc "233 FNB_QR_BASE_URL huruf besar ditolak" 1 staging FNB_QR_BASE_URL_PB=https://PB.efn.example
vc "234 FNB_QR_BASE_URL dengan userinfo ditolak" 1 staging FNB_QR_BASE_URL_PB=https://a@pb.efn.example
vc "235 FNB_QR_COMPANIES=GL (bukan F&B) ditolak" 1 staging FNB_QR_COMPANIES=GL
vc "236 FNB_QR_COMPANIES ganda ditolak" 1 staging FNB_QR_COMPANIES=PB,PB
vc "237 FNB_QR_COMPANIES huruf kecil ditolak" 1 staging FNB_QR_COMPANIES=pb
vc "238 FNB_QR_ENABLED=yes ditolak" 1 staging FNB_QR_ENABLED=yes
vc "239 FNB_TABLE_SESSION_TTL_SECONDS=14401 ditolak (maks 4 jam)" 1 staging FNB_TABLE_SESSION_TTL_SECONDS=14401
vc "240 FNB_TABLE_SESSION_TTL_SECONDS=600 diterima" 0 staging FNB_TABLE_SESSION_TTL_SECONDS=600
vc "241 FNB_MAX_LINES=31 ditolak (batas keras Odoo 30)" 1 staging FNB_MAX_LINES=31
vc "242 FNB_MAX_LINE_QTY=21 ditolak (batas keras Odoo 20)" 1 staging FNB_MAX_LINE_QTY=21
vc "243 FNB_CAPTCHA_MODE=first_submit diterima" 0 production FNB_CAPTCHA_MODE=first_submit
vc "244 FNB_CAPTCHA_MODE tak dikenal ditolak" 1 staging FNB_CAPTCHA_MODE=sometimes
vc "245 FNB_SESSION_MEMBER_LINK_DAYS > retensi sesi (default 180) ditolak" 1 staging FNB_SESSION_MEMBER_LINK_DAYS=200
vc "246 FNB_SESSION_RETENTION_DAYS=180 + link 30 diterima" 0 production FNB_SESSION_RETENTION_DAYS=180 FNB_SESSION_MEMBER_LINK_DAYS=30
vc "247 ONLINE_LOYALTY_REDEEM_ENABLED bukan boolean ditolak" 1 staging ONLINE_LOYALTY_REDEEM_ENABLED=on
vc "248 FNB_MENU_SYNC_SECONDS=59 ditolak" 1 staging FNB_MENU_SYNC_SECONDS=59
u "249 efn_validate_fnb_qr_companies PB,BCF" 0 efn_validate_fnb_qr_companies PB,BCF
u "250 efn_validate_fnb_qr_companies BCR (ritel) ditolak" 1 efn_validate_fnb_qr_companies BCR
# SEC-171 (r15): LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT - nilai berisiko `false`, gerbang EFN_ALLOW_FEATURE_ON (production)
vc "251 production NOTICE_REQUIRE_CONSENT=false tanpa izin ditolak" 1 production LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT=false
vc "252 production NOTICE_REQUIRE_CONSENT=false + izin diterima" 0 production LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT=false \
  EFN_ALLOW_FEATURE_ON=LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT
vc "253 production NOTICE_REQUIRE_CONSENT=true tanpa izin diterima" 0 production LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT=true
vc "254 staging NOTICE_REQUIRE_CONSENT=false tanpa izin diterima (gerbang hanya production)" 0 staging \
  LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT=false
vc "255 NOTICE_REQUIRE_CONSENT bukan boolean ditolak" 1 staging LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT=no
vc "256 izin LOYALTY_EXPIRY_ENABLED tidak membuka NOTICE_REQUIRE_CONSENT=false" 1 production \
  LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT=false EFN_ALLOW_FEATURE_ON=LOYALTY_EXPIRY_ENABLED
vc "257 izin NOTICE_REQUIRE_CONSENT tidak membuka LOYALTY_EXPIRY_ENABLED=true" 1 production LOYALTY_EXPIRY_ENABLED=true \
  LOYALTY_ACTIVITY_EPOCH=2026-11-01 EFN_ALLOW_FEATURE_ON=LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT
vc "258 hangus + consent off dengan kedua izin diterima" 0 production LOYALTY_EXPIRY_ENABLED=true \
  LOYALTY_ACTIVITY_EPOCH=2026-11-01 LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT=false \
  "EFN_ALLOW_FEATURE_ON=LOYALTY_EXPIRY_ENABLED LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT"
# QA G22 P2-3: job ETL opsional (boolean + prasyarat cermin services/etl/efn_etl/config.py)
vc "259 ETL_ENABLE_KDS=1 ditolak (boolean)" 1 staging ETL_ENABLE_KDS=1
vc "260 ETL_PARTNER_REGION_ENABLED=yes ditolak (boolean)" 1 staging ETL_PARTNER_REGION_ENABLED=yes
vc "261 production ETL_ENABLE_KDS=true + ETL_ENABLE_ODOO kosong diterima (aktif setelah grant)" 0 production ETL_ENABLE_KDS=true
vc "262 ETL_ENABLE_KDS=true + ETL_ENABLE_ODOO=false ditolak" 1 staging ETL_ENABLE_KDS=true ETL_ENABLE_ODOO=false
vc "263 ETL_KDS_ONLINE_ENABLED=true tanpa ETL_ENABLE_KDS ditolak" 1 staging ETL_KDS_ONLINE_ENABLED=true
vc "264 ETL_KDS_ONLINE_ENABLED=true + ETL_ENABLE_KDS=true diterima" 0 production ETL_ENABLE_KDS=true ETL_KDS_ONLINE_ENABLED=true
vc "265 ETL_PARTNER_REGION_ENABLED=true + ETL_ENABLE_ODOO=false ditolak" 1 staging ETL_PARTNER_REGION_ENABLED=true \
  ETL_ENABLE_ODOO=false
vc "266 ETL_LOYALTY_RECON_ONLINE_ENABLED=true + mode product ditolak" 1 staging ETL_LOYALTY_RECON_ONLINE_ENABLED=true \
  ETL_LOYALTY_POINTS_MODE=product
vc "267 ETL_LOYALTY_RECON_ONLINE_ENABLED=true + ETL_ENABLE_ODOO=false ditolak" 1 staging \
  ETL_LOYALTY_RECON_ONLINE_ENABLED=true ETL_ENABLE_ODOO=false
vc "268 production recon online + region + ETL_ENABLE_ODOO=true diterima" 0 production ETL_ENABLE_ODOO=true \
  ETL_LOYALTY_RECON_ONLINE_ENABLED=true ETL_PARTNER_REGION_ENABLED=true
vc "269 semua job ETL opsional false diterima" 0 production ETL_PARTNER_REGION_ENABLED=false \
  ETL_LOYALTY_RECON_ONLINE_ENABLED=false ETL_ENABLE_KDS=false ETL_KDS_ONLINE_ENABLED=false

echo "RINGKASAN efn-deploy: PASS=$PASS FAIL=$FAIL"
[[ $FAIL -eq 0 ]]
