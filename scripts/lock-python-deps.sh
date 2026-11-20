#!/usr/bin/env bash
# lock-python-deps.sh - buat ulang services/<svc>/requirements.lock (pin penuh + hash sha256, termasuk dependency
# transitif) dari services/<svc>/requirements.txt dengan pip-compile (pip-tools) di dalam container sekali pakai.
# SEC-214 (G44 security review): supply chain forecast-worker. Runbook: docs/runbooks/python-dependency-lock.md.
#
# Pemakaian (root repo; Docker wajib; butuh akses PyPI):
#   bash scripts/lock-python-deps.sh forecast-worker            # pertahankan pin lock yang ada bila masih memenuhi
#   bash scripts/lock-python-deps.sh forecast-worker --upgrade  # resolve ulang semua dependency transitif
#
# Kenapa container: hash/marker harus di-resolve untuk platform image (linux, Python 3.12), bukan host Windows/Git Bash.
# Image = baris FROM Dockerfile service (tag@digest, SEC-47) -> resolusi sama persis dengan build image.
# Idempoten: tanpa --upgrade dan tanpa perubahan requirements.txt, keluaran identik dengan lock yang ada.
# Keluaran ditulis atomik (file sementara -> mv) hanya bila berisi hash; lock lama tidak tersentuh bila gagal.
set -euo pipefail

# pip-tools dipin (SEC-88). Versi dengan dukungan pip >= 25.1; ganti hanya lewat PR (runbook §Versi pip-tools).
PIP_TOOLS_VERSION="7.5.1"

die() { echo "lock-python-deps: $*" >&2; exit 1; }

svc="${1:-}"
[[ "$svc" =~ ^[a-z0-9][a-z0-9-]{0,40}$ ]] || die "pemakaian: $0 <service> [--upgrade] (service = nama direktori di services/)"
shift
upgrade=0
for a in "$@"; do
  case "$a" in
    --upgrade) upgrade=1 ;;
    *) die "argumen tidak dikenal: $a" ;;
  esac
done

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dir="$root/services/$svc"
[[ -f "$dir/requirements.txt" && -f "$dir/Dockerfile" ]] || die "services/$svc/{requirements.txt,Dockerfile} tidak ada"
command -v docker > /dev/null || die "docker tidak ada di PATH"

base="$(sed -nE 's/^FROM[[:space:]]+([^[:space:]]+).*/\1/p' "$dir/Dockerfile" | head -n1)"
[[ "$base" =~ ^[a-z0-9./_-]+:[A-Za-z0-9._-]+@sha256:[a-f0-9]{64}$ ]] \
  || die "baris FROM services/$svc/Dockerfile harus tag@sha256 (SEC-47), dapat: '${base}'"

# Lock lama dipakai sebagai titik awal (pip-compile mempertahankan pin yang masih memenuhi) kecuali placeholder/--upgrade.
reuse=0
if [[ -s "$dir/requirements.lock" ]] && ! grep -q 'EFN_LOCK_PLACEHOLDER' "$dir/requirements.lock" && (( upgrade == 0 )); then
  reuse=1
fi

# Git Bash (Windows): path host untuk -v harus format Windows dan tanpa konversi MSYS (CLAUDE.md, dev-stack-boot.md).
if src_host="$(cd "$dir" && pwd -W 2> /dev/null)"; then :; else src_host="$dir"; fi

tmp="$(mktemp "$dir/.requirements.lock.XXXXXX")"
trap 'rm -f "$tmp"' EXIT

# Skrip di dalam container: tidak ada nilai host yang disisipkan ke string (parameter lewat env).
# shellcheck disable=SC2016  # $VAR dievaluasi shell di dalam container, bukan di host
inner='set -eu
pip install --quiet --no-cache-dir --disable-pip-version-check "pip-tools==${EFN_PIP_TOOLS_VERSION}" >&2
mkdir -p /work && cd /work
cp /src/requirements.txt requirements.txt
if [ "$EFN_REUSE" = 1 ]; then cp /src/requirements.lock requirements.lock; fi
upg=""
if [ "$EFN_UPGRADE" = 1 ]; then upg="--upgrade"; fi
CUSTOM_COMPILE_COMMAND="bash scripts/lock-python-deps.sh ${EFN_SVC}" \
  pip-compile --quiet --generate-hashes --allow-unsafe --strip-extras --no-emit-index-url \
    --resolver=backtracking $upg --output-file=requirements.lock requirements.txt >&2
cat requirements.lock'

echo "lock-python-deps: ${svc} (image ${base%%@*}, pip-tools ${PIP_TOOLS_VERSION}, reuse=${reuse}, upgrade=${upgrade})" >&2
MSYS_NO_PATHCONV=1 docker run --rm \
  -e "EFN_PIP_TOOLS_VERSION=${PIP_TOOLS_VERSION}" -e "EFN_REUSE=${reuse}" -e "EFN_UPGRADE=${upgrade}" -e "EFN_SVC=${svc}" \
  -v "${src_host}:/src:ro" "$base" sh -c "$inner" > "$tmp"

grep -q -- '--hash=sha256:' "$tmp" || die "keluaran pip-compile tanpa hash (lock lama tidak diubah)"
grep -q 'EFN_LOCK_PLACEHOLDER' "$tmp" && die "keluaran pip-compile masih placeholder (lock lama tidak diubah)"
# Normalisasi akhir baris (Docker Desktop di Windows bisa menambah CR lewat terminal; di sini stdout file, tetap aman).
tr -d '\r' < "$tmp" > "$tmp.n" && mv -f "$tmp.n" "$tmp"
chmod 0644 "$tmp"
mv -f "$tmp" "$dir/requirements.lock"
trap - EXIT
echo "lock-python-deps: services/${svc}/requirements.lock diperbarui ($(grep -cE '^[A-Za-z0-9._-]+==' "$dir/requirements.lock") paket)" >&2
echo "lock-python-deps: lanjutkan dengan: bash scripts/check-py-lock.sh ${svc}" >&2
