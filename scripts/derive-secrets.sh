#!/usr/bin/env bash
# Secret TURUNAN (jenis `derived:<nama>` di scripts/secrets.list) - dihitung dari secret lain, TIDAK disimpan di
# GitHub Environment. Dipanggil scripts/gen-secrets.sh (dev/CI) dan scripts/deploy.sh (host) setelah secret dasar
# ditulis. Idempoten: file ditulis ulang hanya bila nilainya berubah (mis. setelah rotasi secret dasar).
#
#   member_hash_pepper     = hex(HKDF-SHA256(ikm = otp_pepper, salt = "", info = "efn:member_hash:v1", L = 32))
#                            docs/04 §1 + §8.4: pepper `member_hash` yang SAMA dengan BFF (app-config.ts
#                            `memberHashPepper`) dan ETL. Dipakai Odoo (EFN_MEMBER_HASH_PEPPER_FILE).
#                            Test vector (app-config.spec.ts): otp_pepper "test-otp-pepper-v1" ->
#                            46df3d2fee0e91a91083827a5f303586e9c6b5c54afe096896bac9feb724061f
#   minio_prometheus_token = JWT HS512 {sub/accessKey: efn-prometheus, iss: prometheus, exp: 2100-01-01} ditandatangani
#                            secret key user MinIO `efn-prometheus` (= `mc admin prometheus generate`), dipakai
#                            Prometheus sebagai bearer token metrik MinIO (MINIO_PROMETHEUS_AUTH_TYPE=jwt).
#
# Kerahasiaan input: bila python3 tersedia, kunci dikirim lewat stdin (tidak pernah di argv/`ps`). Fallback openssl
# (`openssl kdf`/`openssl dgst -mac`) memerlukan kunci di argv selama beberapa milidetik -> di host produksi pasang
# /proc dengan hidepid=2 (docs/runbooks/key-custody.md). Setiap jalur diuji dengan test vector sebelum dipakai.
#
# Pemakaian: derive-secrets.sh <secrets_dir>        (mode file mengikuti SECRET_FILE_MODE, default 0644)
#            derive-secrets.sh --self-test          (hanya uji test vector; dipakai CI)
set -euo pipefail

VECTOR_IKM="test-otp-pepper-v1"
VECTOR_OUT="46df3d2fee0e91a91083827a5f303586e9c6b5c54afe096896bac9feb724061f"
MINIO_PROM_USER="${MINIO_PROMETHEUS_USER:-efn-prometheus}"
JWT_EXP=4102444800   # 2100-01-01T00:00:00Z (setara token `mc admin prometheus generate`, rotasi = ganti secret dasar)

die() { echo "derive-secrets: $*" >&2; exit 1; }

# EFN_DERIVE_FORCE_OPENSSL=1 memaksa jalur openssl (dipakai CI untuk menguji fallback)
have_python() { [[ "${EFN_DERIVE_FORCE_OPENSSL:-0}" != "1" ]] && command -v python3 > /dev/null 2>&1; }

# HKDF-SHA256 dengan salt kosong, keluaran hex 32 byte. IKM dibaca dari stdin (byte apa adanya).
hkdf_hex() {
  local info="$1"
  if have_python; then
    python3 -c '
import hashlib, hmac, sys
ikm = sys.stdin.buffer.read()
info = sys.argv[1].encode()
prk = hmac.new(b"\x00" * 32, ikm, hashlib.sha256).digest()      # salt kosong = HashLen nol (RFC 5869 2.2)
okm = hmac.new(prk, info + b"\x01", hashlib.sha256).digest()      # L = 32 = satu blok
sys.stdout.write(okm.hex())
' "$info"
  else
    local hex
    hex=$(od -An -v -tx1 | tr -d ' \n')
    [[ -n "$hex" ]] || die "IKM kosong"
    # OpenSSL >= 3.0. Salt tidak diberikan = salt kosong (RFC 5869). Keluaran default hex ber-titik-dua.
    openssl kdf -keylen 32 -kdfopt digest:SHA256 -kdfopt "hexkey:${hex}" -kdfopt "info:${info}" HKDF \
      | tr -d ':\n[:space:]' | tr 'A-F' 'a-f'
  fi
}

b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }

# HMAC-SHA512 (kunci dari file $1) atas stdin, keluaran base64url.
hmac512_b64url() {
  local keyfile="$1"
  if have_python; then
    python3 -c '
import base64, hashlib, hmac, sys
key = open(sys.argv[1], "rb").read()
msg = sys.stdin.buffer.read()
sig = hmac.new(key, msg, hashlib.sha512).digest()
sys.stdout.write(base64.urlsafe_b64encode(sig).decode().rstrip("="))
' "$keyfile"
  else
    local hex
    hex=$(od -An -v -tx1 < "$keyfile" | tr -d ' \n')
    openssl dgst -sha512 -mac HMAC -macopt "hexkey:${hex}" -binary | b64url
  fi
}

self_test() {
  local got
  got=$(printf '%s' "$VECTOR_IKM" | hkdf_hex "efn:member_hash:v1")
  [[ "$got" == "$VECTOR_OUT" ]] || die "test vector HKDF GAGAL (dapat ${got:-<kosong>}, harap ${VECTOR_OUT})"
  echo "derive-secrets: test vector member_hash_pepper OK ($(have_python && echo python3 || echo openssl))"
}

write_if_changed() {
  # $1 = file tujuan, stdin = nilai
  local dst="$1" tmp
  tmp=$(mktemp "$(dirname "$dst")/.derive.XXXXXX")
  cat > "$tmp"
  [[ -s "$tmp" ]] || { rm -f "$tmp"; die "nilai turunan kosong untuk $(basename "$dst")"; }
  if cmp -s "$tmp" "$dst" 2> /dev/null; then
    rm -f "$tmp"; echo "ada    : $(basename "$dst" .txt)"
  else
    chmod "${SECRET_FILE_MODE:-0644}" "$tmp"
    mv -f "$tmp" "$dst"; echo "dibuat : $(basename "$dst" .txt) (turunan)"
  fi
}

derive_all() {
  local dir="$1" src
  [[ -d "$dir" ]] || die "direktori secret tidak ada: $dir"
  self_test

  src="${dir}/otp_pepper.txt"
  [[ -s "$src" ]] || die "otp_pepper.txt tidak ada (dibutuhkan member_hash_pepper)"
  # BFF membaca file secret tanpa newline penutup -> buang newline di akhir agar IKM identik
  printf '%s' "$(< "$src")" | hkdf_hex "efn:member_hash:v1" | write_if_changed "${dir}/member_hash_pepper.txt"

  src="${dir}/minio_prometheus_secret.txt"
  [[ -s "$src" ]] || die "minio_prometheus_secret.txt tidak ada (dibutuhkan minio_prometheus_token)"
  local header payload sig keyfile
  header=$(printf '%s' '{"alg":"HS512","typ":"JWT"}' | b64url)
  payload=$(printf '{"exp":%s,"sub":"%s","accessKey":"%s","iss":"prometheus"}' "$JWT_EXP" "$MINIO_PROM_USER" "$MINIO_PROM_USER" | b64url)
  keyfile=$(mktemp)
  printf '%s' "$(< "$src")" > "$keyfile"
  sig=$(printf '%s.%s' "$header" "$payload" | hmac512_b64url "$keyfile")
  rm -f "$keyfile"
  printf '%s.%s.%s' "$header" "$payload" "$sig" | write_if_changed "${dir}/minio_prometheus_token.txt"
}

case "${1:-}" in
  --self-test) self_test ;;
  "") die "pemakaian: $0 <secrets_dir> | --self-test" ;;
  *) derive_all "$1" ;;
esac
