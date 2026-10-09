#!/bin/sh
# Entrypoint wrapper Metabase EFN (G16b, efn-devsecops-engineer) - enkripsi at rest DB aplikasi Metabase.
# Metabase hanya membaca MB_ENCRYPTION_SECRET_KEY dari env (tidak ada *_FILE untuk variabel ini di run_metabase.sh
# resmi). Wrapper ini membaca Docker secret /run/secrets/metabase_encryption_key ke env tersebut lalu `exec` start
# script RESMI image (/app/run_metabase.sh) tanpa mengubah perilakunya: tetap mulai sebagai root, membuat user
# metabase (MUID/MGID 2000) dan menjalankan JVM lewat `su metabase`.
#
# FAIL-CLOSED (CLAUDE.md "enkripsi at rest", ADR-022): tanpa kunci yang valid container BERHENTI (exit 1), di semua
# lingkungan termasuk dev (scripts/gen-secrets.sh membuat kuncinya). Tidak ada fallback ke penyimpanan plaintext:
# Metabase tanpa kunci akan menyimpan detail koneksi (password ClickHouse) plaintext di DB `metabase`, dan Metabase
# yang SUDAH terenkripsi tanpa kunci tidak bisa membaca detail koneksinya sendiri.
# Nilai kunci tidak pernah dicetak. Kustodi: Superadmin (docs/runbooks/key-custody.md).
set -eu

KEY_FILE=/run/secrets/metabase_encryption_key
MIN_LEN=32

die() {
  echo "efn-metabase-entrypoint: $*" >&2
  exit 1
}

# Kunci lewat env (compose environment / docker run -e) terlihat di `docker inspect` -> ditolak.
if [ -n "${MB_ENCRYPTION_SECRET_KEY:-}" ]; then
  die "MB_ENCRYPTION_SECRET_KEY tidak boleh diset lewat env; gunakan Docker secret metabase_encryption_key"
fi
[ -f "$KEY_FILE" ] || die "secret $KEY_FILE tidak ada (mount secret metabase_encryption_key; dev: bash scripts/gen-secrets.sh)"
[ -r "$KEY_FILE" ] || die "secret $KEY_FILE tidak bisa dibaca"

# $(cat) membuang newline di akhir; CR/spasi di tengah/akhir (file dari Windows) ditolak, bukan dipotong diam-diam.
key="$(cat "$KEY_FILE")"
case "$key" in
  *[[:space:]]*) die "secret $KEY_FILE berisi spasi/CR (buat ulang tanpa CRLF)" ;;
esac
[ "${#key}" -ge "$MIN_LEN" ] || die "secret $KEY_FILE kosong/terlalu pendek (minimal $MIN_LEN karakter)"

MB_ENCRYPTION_SECRET_KEY="$key"
export MB_ENCRYPTION_SECRET_KEY
unset key

exec /app/run_metabase.sh "$@"
