#!/usr/bin/env bash
# PKI internal EFN 3 tingkat (docs/04 §7.1, §8.5, §8.6; ADR-022 Keputusan 1 / W1; SEC-23).
#
#   root (offline, 10 th, pathlen:1)  ->  intermediate per lingkungan (2 th, pathlen:0, nameConstraints)  ->  leaf (90 hari)
#
# Subperintah:
#   gen-pki.sh root                    Buat ROOT CA di mesin Superadmin (offline). Kunci dienkripsi passphrase.
#                                      Menolak berjalan di server (/opt/efn ada). `make pki-root`.
#   gen-pki.sh intermediate <env>      Terbitkan intermediate untuk env (staging|production) dengan kunci root.
#                                      Keluaran: ${PKI_OUT_DIR}/<env>/{intermediate.crt,intermediate.key,efn_ca_crt.txt}
#                                      -> diunggah Superadmin ke GitHub Environment <env>. `make pki-intermediate ENV=..`
#   gen-pki.sh [leaf] [svc ...]        (default) Terbitkan/perbarui sertifikat LEAF dari intermediate di ${CA_DIR}.
#                                      Dipakai dev/CI (`make secrets`) dan scripts/deploy.sh di host (kunci intermediate
#                                      hanya ada di tmpfs selama deploy, lalu dihancurkan).
#
# Keluaran leaf (nama = nama Docker secret di scripts/secrets.list, jenis `pki`):
#   ${SECRETS_DIR}/efn_ca_crt.txt                      BUNDLE intermediate + root (+ EFN_PKI_EXTRA_TRUST saat rotasi)
#   ${SECRETS_DIR}/tls_<service>_crt.txt / _key.txt     per service ('-' -> '_')
#   ${SECRETS_DIR}/.pki-renewed                         daftar secret leaf yang baru diterbitkan pada run ini
#                                                      (dibaca deploy.sh untuk me-recreate container pemakainya)
#
# Dev/CI: bila ${CA_DIR} kosong DAN APP_ENV bukan staging/production, root + intermediate DEV dibuat otomatis
# di ${CA_DIR} (tanpa passphrase, hanya untuk laptop/CI). Staging/production TIDAK PERNAH membuat CA otomatis.
#
# Idempoten: leaf diterbitkan ulang hanya bila belum ada, sisa masa berlaku < RENEW_DAYS, tidak diterbitkan
# intermediate saat ini, kunci tidak cocok, atau profil EKU berubah. FORCE=1 menerbitkan ulang semua leaf.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS_DIR="${SECRETS_DIR:-${ROOT_DIR}/secrets}"
CA_DIR="${CA_DIR:-${SECRETS_DIR}/pki-ca}"
PKI_ROOT_DIR="${PKI_ROOT_DIR:-${HOME:-/root}/efn-pki-root}"
PKI_OUT_DIR="${PKI_OUT_DIR:-${PKI_ROOT_DIR}/out}"
ROOT_DAYS="${ROOT_DAYS:-3650}"
INTERMEDIATE_DAYS="${INTERMEDIATE_DAYS:-730}"
LEAF_DAYS="${LEAF_DAYS:-90}"
RENEW_DAYS="${RENEW_DAYS:-30}"
FORCE="${FORCE:-0}"
APP_ENV="${APP_ENV:-dev}"

# Service yang mendapat sertifikat. Format: <nama-service-compose>|<profil>[|<alias DNS tambahan>,...]
#   server : EKU serverAuth saja (tidak bisa dipakai sebagai sertifikat klien mTLS)
#   both   : serverAuth + clientAuth (service yang juga menjadi KLIEN mTLS, docs/04 §8.5)
#   client : clientAuth saja (tidak punya listener jaringan)
# Klien mTLS = Traefik, Prometheus, Grafana, Promtail, ocr-worker (ke Ollama), blackbox-exporter (probe), cAdvisor,
# Loki (ruler -> Alertmanager), etl (-> loki-tls) dan metabase-provision (-> metabase-tls) (G13).
# Sumber kebenaran SAN + nameConstraints intermediate. Tambah service baru = baris di sini + 2 baris `pki` di
# scripts/secrets.list + TERBITKAN ULANG INTERMEDIATE (nameConstraints memuat daftar ini; lihat runbook internal-pki).
PKI_SERVICES=(
  "postgres|server"
  "redis|server"
  "rabbitmq|server"
  "minio|server"
  "s3-gateway|server"   # filter L7 HAProxy di depan object storage (ADR-030 Keputusan 2, gelombang 9)
  "clickhouse|server"
  "api-gateway|server"
  "odoo|server"
  "superapp-web|server"
  "ollama|server"
  "metabase|server"
  "traefik|both"
  "prometheus|both"
  "alertmanager|server"
  "grafana|both"
  "loki|both"           # klien mTLS ke Alertmanager (ruler alert audit sink, gelombang 11)
  "promtail|client"
  "etl|client"                 # G13: klien mTLS loki-tls (verifikasi HMAC sink audit, SEC-51)
  "metabase-provision|client"  # G13: one-shot infra/metabase/provision.py -> metabase-tls
  "uptime-kuma|server"
  "node-exporter|server"
  "postgres-exporter|server"
  "redis-exporter|server"
  "blackbox-exporter|both"
  "ocr-worker|both"
  "cadvisor|both"
  "docker-socket-proxy|server"
)

# Service TANPA SAN IP 127.0.0.1 (gelombang 7). Erlang/OTP public_key (RabbitMQ 3.13/OTP 26, juga 4.1/OTP 27)
# crash (function_clause di pubkey_cert:match_name iPAddress) saat memvalidasi sertifikat KLIEN yang punya SAN IP
# terhadap nameConstraints IP intermediate -> handshake mTLS ke rabbitmq:15691 gagal ("internal error").
# Prometheus = satu-satunya klien mTLS ke server Erlang; ia tidak butuh SAN IP (scrape diri via DNS localhost).
NO_IP_SAN_SERVICES=" prometheus "

die() { echo "gen-pki: $*" >&2; exit 1; }
command -v openssl > /dev/null || die "openssl tidak ditemukan"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# ── nameConstraints: nama service compose (+ container efn-<svc>-1, alias) + .efn.internal + localhost ─────────
name_constraints() {
  local entry svc rest aliases a out=""
  for entry in "${PKI_SERVICES[@]}"; do
    svc="${entry%%|*}"; rest="${entry#*|}"; aliases=""
    [[ "$rest" == *"|"* ]] && aliases="${rest#*|}"
    out+="permitted;DNS:${svc},permitted;DNS:efn-${svc}-1,"
    if [[ -n "$aliases" ]]; then
      IFS=',' read -r -a extra <<< "$aliases"
      for a in "${extra[@]}"; do out+="permitted;DNS:${a},"; done
    fi
  done
  out+="permitted;DNS:.efn.internal,permitted;DNS:localhost,permitted;IP:127.0.0.1/255.255.255.255"
  printf '%s' "$out"
}

# ── root ────────────────────────────────────────────────────────────────────────────────────────────────────────
make_root() {
  # $1 = dir, $2 = "encrypt" | "plain"
  local dir="$1" mode="$2" cn="EFN Root CA"
  [[ "$mode" == "plain" ]] && cn="EFN DEV Root CA (bukan produksi)"
  mkdir -p "$dir"; chmod 700 "$dir"
  [[ -s "${dir}/root.key" ]] && die "root CA sudah ada di ${dir} (tidak ditimpa)"
  cat > "${WORK}/root.cnf" <<EOF
[req]
distinguished_name = dn
prompt = no
[dn]
O = EFN
CN = ${cn}
[v3_root]
basicConstraints = critical,CA:TRUE,pathlen:1
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash
EOF
  if [[ "$mode" == "encrypt" ]]; then
    echo "Masukkan passphrase kunci ROOT (min 20 karakter; simpan di brankas, prosedur docs/runbooks/key-custody.md):"
    ( umask 077; openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -aes-256-cbc -out "${dir}/root.key" )
  else
    ( umask 077; openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "${dir}/root.key" )
  fi
  ( umask 022; openssl req -x509 -new -key "${dir}/root.key" -sha256 -days "$ROOT_DAYS" \
      -config "${WORK}/root.cnf" -extensions v3_root -out "${dir}/root.crt" )
  chmod 600 "${dir}/root.key"; chmod 644 "${dir}/root.crt"
  echo "dibuat : root CA ${dir}/root.crt (kunci ${dir}/root.key, ${ROOT_DAYS} hari)"
  openssl x509 -in "${dir}/root.crt" -noout -fingerprint -sha256
}

# ── intermediate ────────────────────────────────────────────────────────────────────────────────────────────────
make_intermediate() {
  # $1 = env, $2 = root dir, $3 = out dir
  local env="$1" rdir="$2" odir="$3"
  [[ -s "${rdir}/root.key" && -s "${rdir}/root.crt" ]] || die "root CA tidak ada di ${rdir} (jalankan: make pki-root)"
  mkdir -p "$odir"; chmod 700 "$odir"
  cat > "${WORK}/int.cnf" <<EOF
[req]
distinguished_name = dn
prompt = no
[dn]
O = EFN
CN = EFN Intermediate CA ${env} $(date +%Y%m)
[v3_int]
basicConstraints = critical,CA:TRUE,pathlen:0
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always
nameConstraints = critical,$(name_constraints)
EOF
  ( umask 077; openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "${odir}/intermediate.key" )
  openssl req -new -key "${odir}/intermediate.key" -config "${WORK}/int.cnf" -out "${WORK}/int.csr"
  ( umask 022; openssl x509 -req -in "${WORK}/int.csr" -CA "${rdir}/root.crt" -CAkey "${rdir}/root.key" \
      -set_serial "0x$(openssl rand -hex 16 | tr -d '\r')" -days "$INTERMEDIATE_DAYS" -sha256 \
      -extfile "${WORK}/int.cnf" -extensions v3_int -out "${odir}/intermediate.crt" )
  cat "${odir}/intermediate.crt" "${rdir}/root.crt" > "${odir}/efn_ca_crt.txt"
  cp "${rdir}/root.crt" "${odir}/root.crt"
  chmod 600 "${odir}/intermediate.key"; chmod 644 "${odir}/intermediate.crt" "${odir}/efn_ca_crt.txt" "${odir}/root.crt"
  openssl verify -CAfile "${rdir}/root.crt" "${odir}/intermediate.crt" > /dev/null
  echo "dibuat : intermediate ${env} -> ${odir} (${INTERMEDIATE_DAYS} hari, pathlen:0, nameConstraints)"
}

# ── leaf ────────────────────────────────────────────────────────────────────────────────────────────────────────
INT_KEY="${CA_DIR}/intermediate.key"
INT_CRT="${CA_DIR}/intermediate.crt"
ROOT_CRT="${CA_DIR}/root.crt"
BUNDLE="${SECRETS_DIR}/efn_ca_crt.txt"

eku_for() {
  case "$1" in
    server) echo "serverAuth" ;;
    both)   echo "serverAuth,clientAuth" ;;
    client) echo "clientAuth" ;;
    *) die "profil tidak dikenal: $1" ;;
  esac
}

eku_text() {
  # Teks EKU seperti dicetak openssl, untuk membandingkan profil sertifikat lama
  case "$1" in
    server) echo "TLS Web Server Authentication" ;;
    both)   echo "TLS Web Server Authentication, TLS Web Client Authentication" ;;
    client) echo "TLS Web Client Authentication" ;;
  esac
}

leaf_ok() {
  local crt="$1" key="$2" profile="$3" a b eku
  [[ -s "$crt" && -s "$key" ]] || return 1
  openssl x509 -in "$crt" -noout -checkend $((RENEW_DAYS * 86400)) > /dev/null 2>&1 || return 1
  # Harus diterbitkan intermediate SAAT INI (bukan CA lama yang mungkin masih ada di bundle transisi)
  openssl verify -partial_chain -CAfile "$INT_CRT" "$crt" > /dev/null 2>&1 || return 1
  eku=$(openssl x509 -in "$crt" -noout -ext extendedKeyUsage 2> /dev/null | tail -n1 | sed 's/^[[:space:]]*//')
  [[ "$eku" == "$(eku_text "$profile")" ]] || return 1
  a=$(openssl x509 -in "$crt" -noout -pubkey | openssl sha256)
  b=$(openssl pkey -in "$key" -pubout | openssl sha256)
  [[ "$a" == "$b" ]]
}

issue_leaf() {
  local svc="$1" profile="$2" aliases="$3" name crt key i
  name="tls_${svc//-/_}"
  crt="${SECRETS_DIR}/${name}_crt.txt"
  key="${SECRETS_DIR}/${name}_key.txt"

  local want_ip=1 has_ip=0
  [[ "$NO_IP_SAN_SERVICES" == *" ${svc} "* ]] && want_ip=0
  [[ -s "$crt" ]] && openssl x509 -in "$crt" -noout -ext subjectAltName 2> /dev/null | grep -q 'IP Address' && has_ip=1
  if [[ "$FORCE" != "1" && "$want_ip" == "$has_ip" ]] && leaf_ok "$crt" "$key" "$profile"; then
    echo "ada    : ${name}"
    return 0
  fi
  [[ -s "$INT_KEY" ]] || { echo "GAGAL  : ${name} perlu diterbitkan tetapi kunci intermediate tidak ada di ${CA_DIR}" >&2; return 1; }

  {
    echo "[req]"
    echo "distinguished_name = dn"
    echo "prompt = no"
    echo "[dn]"
    echo "O = EFN"
    echo "CN = ${svc}"
    echo "[v3_leaf]"
    echo "basicConstraints = critical,CA:FALSE"
    echo "keyUsage = critical,digitalSignature,keyAgreement"
    echo "extendedKeyUsage = $(eku_for "$profile")"
    echo "subjectKeyIdentifier = hash"
    echo "authorityKeyIdentifier = keyid"
    echo "subjectAltName = @alt"
    echo "[alt]"
    echo "DNS.1 = ${svc}"
    echo "DNS.2 = localhost"
    echo "DNS.3 = efn-${svc}-1"
    i=4
    if [[ -n "$aliases" ]]; then
      IFS=',' read -r -a extra <<< "$aliases"
      for a in "${extra[@]}"; do echo "DNS.${i} = ${a}"; i=$((i + 1)); done
    fi
    if [[ "$want_ip" == "1" ]]; then echo "IP.1 = 127.0.0.1"; fi
  } > "${WORK}/${name}.cnf"

  # Kunci PKCS#8 ECDSA P-256 (didukung PostgreSQL, Redis, Erlang, Go, Node, Poco/ClickHouse)
  ( umask 022
    openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "${WORK}/${name}.key"
    openssl req -new -key "${WORK}/${name}.key" -config "${WORK}/${name}.cnf" -out "${WORK}/${name}.csr"
    openssl x509 -req -in "${WORK}/${name}.csr" -CA "$INT_CRT" -CAkey "$INT_KEY" \
      -set_serial "0x$(openssl rand -hex 16 | tr -d '\r')" -days "$LEAF_DAYS" -sha256 \
      -extfile "${WORK}/${name}.cnf" -extensions v3_leaf -out "${WORK}/${name}.crt" 2> /dev/null
  )
  # Rantai lengkap harus valid terhadap bundle (memeriksa nameConstraints intermediate juga)
  openssl verify -CAfile "$BUNDLE" "${WORK}/${name}.crt" > /dev/null \
    || { echo "GAGAL  : ${name} tidak lolos verifikasi rantai/nameConstraints" >&2; return 1; }
  # Izin file: lihat scripts/deploy.sh (host: dir 0750 root:efn-superadmin) / gen-secrets.sh (dev).
  # PostgreSQL mensyaratkan kunci 0600 -> compose menyalin kunci ke lokasi privat saat start.
  install -m "${SECRET_FILE_MODE:-0644}" "${WORK}/${name}.crt" "$crt"
  install -m "${SECRET_FILE_MODE:-0644}" "${WORK}/${name}.key" "$key"
  printf '%s\n%s\n' "${name}_crt" "${name}_key" >> "${SECRETS_DIR}/.pki-renewed"
  echo "dibuat : ${name} (${profile}; SAN ${svc}, localhost, efn-${svc}-1${aliases:+, ${aliases}}$([[ "$want_ip" == "1" ]] && echo ", 127.0.0.1"); ${LEAF_DAYS} hari)"
}

# Apakah nameConstraints intermediate saat ini memuat setiap service di PKI_SERVICES (DNS:<svc>)?
int_covers_services() {
  local nc entry svc
  nc=$(openssl x509 -in "$INT_CRT" -noout -ext nameConstraints 2> /dev/null | tr -d '\r') || return 1
  for entry in "${PKI_SERVICES[@]}"; do
    svc="${entry%%|*}"
    grep -Eq "DNS:${svc}([^-a-z0-9]|$)" <<< "$nc" || return 1
  done
}

cmd_leaf() {
  mkdir -p "$SECRETS_DIR"
  chmod 700 "$SECRETS_DIR" 2> /dev/null || true

  if [[ ! -s "$INT_CRT" ]]; then
    case "$APP_ENV" in
      production|prod|staging) die "intermediate tidak ada di ${CA_DIR}; staging/production tidak membuat CA otomatis" ;;
    esac
    echo "dev/CI : membuat root + intermediate DEV di ${CA_DIR} (bukan untuk staging/production)"
    make_root "${CA_DIR}/root" plain
    make_intermediate dev "${CA_DIR}/root" "$CA_DIR"
    FORCE=1   # CA baru -> semua leaf diterbitkan ulang
  elif ! int_covers_services; then
    # Service baru di PKI_SERVICES tidak tercakup nameConstraints intermediate -> leaf-nya pasti gagal verifikasi.
    case "$APP_ENV" in
      production|prod|staging)
        die "intermediate ${CA_DIR} tidak mencakup semua service di PKI_SERVICES (nameConstraints). Superadmin: terbitkan ulang intermediate (docs/runbooks/internal-pki.md#service-baru)" ;;
    esac
    # Dev/CI: CA dev lama diarsipkan, root+intermediate dev dibuat ulang, semua leaf diterbitkan ulang.
    # Container yang memakai secret PKI perlu di-recreate (`up -d --force-recreate`).
    local old
    old="${CA_DIR}.old-$(date +%Y%m%d%H%M%S)"
    echo "dev/CI : intermediate dev tidak mencakup service baru -> CA dev dibuat ulang (lama: ${old})"
    mv "$CA_DIR" "$old"
    make_root "${CA_DIR}/root" plain
    make_intermediate dev "${CA_DIR}/root" "$CA_DIR"
    FORCE=1
  fi
  [[ -s "$ROOT_CRT" ]] || die "${ROOT_CRT} tidak ada (bundle membutuhkan root)"

  # Bundle = intermediate + root (+ trust tambahan selama rotasi CA). Ditulis ulang setiap run (murah, deterministik).
  {
    cat "$INT_CRT" "$ROOT_CRT"
    if [[ -n "${EFN_PKI_EXTRA_TRUST:-}" ]]; then printf '%s\n' "$EFN_PKI_EXTRA_TRUST"; fi
  } > "${WORK}/bundle"
  openssl verify -CAfile "$ROOT_CRT" "$INT_CRT" > /dev/null || die "intermediate tidak diterbitkan root ${ROOT_CRT}"
  if ! cmp -s "${WORK}/bundle" "$BUNDLE" 2> /dev/null; then
    install -m "${SECRET_FILE_MODE:-0644}" "${WORK}/bundle" "$BUNDLE"
    echo "efn_ca_crt" >> "${SECRETS_DIR}/.pki-renewed"
    echo "dibuat : efn_ca_crt (bundle intermediate + root)"
  fi

  local -a targets=()
  local entry want svc rest profile aliases rc=0
  if [[ $# -gt 0 ]]; then
    for want in "$@"; do
      for entry in "${PKI_SERVICES[@]}"; do [[ "${entry%%|*}" == "$want" ]] && targets+=("$entry"); done
    done
    [[ ${#targets[@]} -gt 0 ]] || die "service tidak dikenal: $*"
  else
    targets=("${PKI_SERVICES[@]}")
  fi
  for entry in "${targets[@]}"; do
    svc="${entry%%|*}"; rest="${entry#*|}"; profile="${rest%%|*}"; aliases=""
    [[ "$rest" == *"|"* ]] && aliases="${rest#*|}"
    issue_leaf "$svc" "$profile" "$aliases" || rc=1
  done
  echo "intermediate kedaluwarsa: $(openssl x509 -in "$INT_CRT" -noout -enddate | cut -d= -f2)"
  return "$rc"
}

case "${1:-leaf}" in
  root)
    shift
    [[ -d /opt/efn/compose ]] && die "root CA TIDAK BOLEH dibuat di server (/opt/efn ada). Jalankan di mesin Superadmin offline."
    make_root "$PKI_ROOT_DIR" encrypt
    echo "Simpan ${PKI_ROOT_DIR} di media terenkripsi offline (2 salinan tersegel). Jangan pernah salin ke server/CI."
    ;;
  intermediate)
    shift
    env="${1:?pemakaian: gen-pki.sh intermediate <staging|production>}"
    [[ "$env" =~ ^(staging|production)$ ]] || die "env harus staging|production"
    [[ -d /opt/efn/compose ]] && die "intermediate diterbitkan di mesin Superadmin (root offline), bukan di server"
    make_intermediate "$env" "$PKI_ROOT_DIR" "${PKI_OUT_DIR}/${env}"
    cat <<EOF
Unggah ke GitHub Environment '${env}' (hanya Superadmin), lalu hancurkan salinan kunci intermediate:
  gh secret set EFN_PKI_INTERMEDIATE_KEY --env ${env} < ${PKI_OUT_DIR}/${env}/intermediate.key
  gh secret set EFN_PKI_INTERMEDIATE_CRT --env ${env} < ${PKI_OUT_DIR}/${env}/intermediate.crt
  gh secret set EFN_PKI_ROOT_CRT         --env ${env} < ${PKI_OUT_DIR}/${env}/root.crt
  shred -u ${PKI_OUT_DIR}/${env}/intermediate.key
Leaf diterbitkan otomatis oleh scripts/deploy.sh pada deploy berikutnya (90 hari, diperbarui bila < ${RENEW_DAYS} hari).
EOF
    ;;
  leaf)
    shift
    cmd_leaf "$@"
    ;;
  *)
    cmd_leaf "$@"
    ;;
esac
