#!/usr/bin/env bash
# Membuat secrets untuk DEV LOKAL dan CI di ./secrets/<nama>.txt (di-ignore git).
# Staging/prod TIDAK memakai skrip ini: nilai berasal dari GitHub Environments -> scripts/deploy.sh.
# Daftar secret: scripts/secrets.list (sumber tunggal). Idempoten: file yang sudah ada tidak ditimpa.
# Secret jenis `pki` (CA + sertifikat TLS internal) dibuat oleh scripts/gen-pki.sh yang dipanggil di akhir.
#
# Izin file: direktori 0700 (hanya pemilik host), file 0644. Docker Compose (non-swarm) me-bind-mount file
# secret apa adanya ke /run/secrets, sehingga user non-root di dalam container (odoo, app, postgres, worker)
# harus bisa membacanya. Perlindungan dari user host lain diberikan oleh izin direktori 0700.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIST="${ROOT_DIR}/scripts/secrets.list"
OUT_DIR="${SECRETS_DIR:-${ROOT_DIR}/secrets}"

command -v openssl > /dev/null || { echo "openssl tidak ditemukan" >&2; exit 1; }

mkdir -p "$OUT_DIR"
chmod 700 "$OUT_DIR"

gen_value() {
  local kind="$1"
  case "$kind" in
    random)    openssl rand -base64 36 | tr -d '\n' ;;
    hex32)     openssl rand -hex 32 | tr -d '\n' ;;
    b64key32)  openssl rand -base64 32 | tr -d '\n' ;;
    minio_kms) printf 'efn-sse-key:%s' "$(openssl rand -base64 32 | tr -d '\n')" ;;
    literal:*) printf '%s' "${kind#literal:}" ;;
    *) echo "generator tidak dikenal: $kind" >&2; return 1 ;;
  esac
}

while read -r name kind _; do
  [[ -z "${name:-}" || "$name" == \#* ]] && continue
  [[ "$kind" == "pki" || "$kind" == derived:* ]] && continue
  f="${OUT_DIR}/${name}.txt"
  if [[ -s "$f" ]]; then
    chmod 644 "$f"
    echo "ada    : $f"
    continue
  fi
  ( umask 022; gen_value "$kind" > "$f" )
  chmod 644 "$f"
  echo "dibuat : $f"
done < "$LIST"

# Secret turunan (member_hash_pepper dari otp_pepper, token metrik MinIO) - scripts/derive-secrets.sh
bash "${ROOT_DIR}/scripts/derive-secrets.sh" "$OUT_DIR"

# PKI internal (docs/04 §7.1): enkripsi in transit wajib juga di dev/CI (dev = prod, tanpa fallback plaintext).
# Dev/CI: root + intermediate DEV dibuat otomatis di secrets/pki-ca (3 tingkat seperti produksi, ADR-022 W1).
SECRETS_DIR="$OUT_DIR" bash "${ROOT_DIR}/scripts/gen-pki.sh" leaf
rm -f "${OUT_DIR}/.pki-renewed"

# Sanity: semua secret jenis pki di secrets.list harus sudah ada (gen-pki.sh dan secrets.list harus sinkron).
missing=0
while read -r name kind _; do
  [[ -z "${name:-}" || "$name" == \#* || "$kind" != "pki" ]] && continue
  [[ -s "${OUT_DIR}/${name}.txt" ]] || { echo "PKI hilang: ${name} (tambahkan service ke PKI_SERVICES di gen-pki.sh)" >&2; missing=1; }
done < "$LIST"
exit "$missing"
