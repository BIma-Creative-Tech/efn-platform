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
    # tr -d '\r\n': openssl Windows (Git Bash) menulis CRLF -> tanpa '\r' secret berisi CR (gelombang 7)
    random)    openssl rand -base64 36 | tr -d '\r\n' ;;
    hex32)     openssl rand -hex 32 | tr -d '\r\n' ;;
    # access key ID object storage (docs/04 §8.12): 20 hex huruf besar, bukan nama service yang mudah ditebak
    akid)      openssl rand -hex 10 | tr -d '\r\n' | tr 'a-f' 'A-F' ;;
    b64key32)  openssl rand -base64 32 | tr -d '\r\n' ;;
    minio_kms) printf 'efn-sse-key:%s' "$(openssl rand -base64 32 | tr -d '\r\n')" ;;
    literal:*) printf '%s' "${kind#literal:}" ;;
    host:*)    printf '%s' "${kind#host:}" ;;   # W-11: nilai awal; staging/prod dikelola di host
    *) echo "generator tidak dikenal: $kind" >&2; return 1 ;;
  esac
}

while read -r name kind _; do
  [[ -z "${name:-}" || "$name" == \#* ]] && continue
  [[ "$kind" == "pki" || "$kind" == derived:* ]] && continue
  # G21 AC-13: kunci PII Odoo di secrets.list berjenis host:disabled (produksi = dibuat Superadmin di host). Dev/CI butuh
  # kunci NYATA (32 byte acak) -> dibuat di bagian "PII Odoo" di bawah, bukan placeholder `disabled`.
  [[ "$name" == odoo_pii_* ]] && continue
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

# PII Odoo (G19 E-38 PoC -> G20 E-38a, docs/04 §12.5, ADR-036 Accepted §0): kunci AES-256-GCM key ring v1, kunci blind
# index, dan (G20, ADR-036 §0.5) kunci token nama `odoo_pii_name_key`; masing-masing 32 byte acak base64, HANYA di-mount
# ke container odoo oleh docker-compose.dev.yml. G21 (AC-13): ketiganya kini di scripts/secrets.list sebagai
# `host:disabled [SA]` (produksi dibuat Superadmin di host, overlay docker-compose.pii.yml) - nilai dev tetap dibuat di
# sini. Idempoten: file yang ada TIDAK ditimpa (menimpa key ring = PII dev terenkripsi tidak terbaca lagi; menimpa
# bidx/name = pencarian gagal sampai indeks/token dihitung ulang). Pengecualian: file berisi placeholder `disabled`
# (bukan kunci; tidak ada data yang terenkripsi dengannya) diganti kunci acak.
for name in odoo_pii_key_v1 odoo_pii_bidx_key odoo_pii_name_key; do
  f="${OUT_DIR}/${name}.txt"
  if [[ -s "$f" ]] && ! grep -qx 'disabled' "$f"; then
    chmod 644 "$f"
    echo "ada    : $f"
    continue
  fi
  ( umask 022; gen_value b64key32 > "$f" )
  chmod 644 "$f"
  echo "dibuat : $f (PII Odoo, dev/test saja)"
done

# PKI internal (docs/04 §7.1): enkripsi in transit wajib juga di dev/CI (dev = prod, tanpa fallback plaintext).
# Dev/CI: root + intermediate DEV dibuat otomatis di secrets/pki-ca (3 tingkat seperti produksi, ADR-022 W1).
SECRETS_DIR="$OUT_DIR" bash "${ROOT_DIR}/scripts/gen-pki.sh" leaf
rm -f "${OUT_DIR}/.pki-renewed"

# Sertifikat EDGE DEV (hanya docker-compose.dev.yml): Traefik sniStrict menolak handshake bila tidak ada sertifikat
# yang cocok dengan SNI, dan ACME tidak bisa menerbitkan untuk *.efn.local -> tanpa ini https://app.efn.local gagal.
# Self-signed TERPISAH dari CA internal (nameConstraints intermediate tidak mengizinkan efn.local, dan memang tidak
# boleh). Tidak ada di secrets.list (bukan secret produksi). Masa berlaku 90 hari; hapus file untuk membuat ulang.
EDGE_CRT="${OUT_DIR}/dev_edge_crt.txt"
EDGE_KEY="${OUT_DIR}/dev_edge_key.txt"
if [[ ! -s "$EDGE_CRT" || ! -s "$EDGE_KEY" ]] || ! openssl x509 -in "$EDGE_CRT" -noout -checkend 86400 > /dev/null 2>&1; then
  edge_cnf="$(mktemp)"
  cat > "$edge_cnf" <<'EOF'
[req]
distinguished_name = dn
prompt = no
x509_extensions = v3
[dn]
CN = efn.local (dev edge, self-signed)
[v3]
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature
extendedKeyUsage = serverAuth
subjectAltName = DNS:efn.local, DNS:*.efn.local
EOF
  ( umask 022
    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 90 -sha256 \
      -config "$edge_cnf" -keyout "$EDGE_KEY" -out "$EDGE_CRT" 2> /dev/null )
  rm -f "$edge_cnf"
  chmod 644 "$EDGE_CRT" "$EDGE_KEY"
  echo "dibuat : $EDGE_CRT (edge dev self-signed)"
fi

# Sanity: semua secret jenis pki di secrets.list harus sudah ada (gen-pki.sh dan secrets.list harus sinkron).
missing=0
while read -r name kind _; do
  [[ -z "${name:-}" || "$name" == \#* || "$kind" != "pki" ]] && continue
  [[ -s "${OUT_DIR}/${name}.txt" ]] || { echo "PKI hilang: ${name} (tambahkan service ke PKI_SERVICES di gen-pki.sh)" >&2; missing=1; }
done < "$LIST"
exit "$missing"
