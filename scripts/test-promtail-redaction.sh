#!/usr/bin/env bash
# Uji regresi redaksi PII/rahasia di pipeline Promtail (G15; SEC-72, SEC-73, SEC-78; R-L01, UU PDP).
#
#   scripts/test-promtail-redaction.sh            jalankan uji (CI: job `promtail-redaction` di ci.yml)
#   scripts/test-promtail-redaction.sh --update   tulis ulang expected.txt dari keluaran saat ini
#                                                 (WAJIB ditinjau manual di diff PR sebelum commit)
#
# Cara kerja (tanpa Loki, tanpa jaringan selain pull image):
#   1. yq menyalin `pipeline_stages` dari infra/observability/promtail.yml (scrape docker) dan promtail-journal.yml
#      (journal host) ke kerangka infra/observability/tests/promtail/harness.yml -> yang diuji = konfigurasi
#      yang dideploy, bukan salinan pola.
#   2. Promtail image produksi (digest sama dengan compose) dijalankan `-dry-run` atas fixture
#      (tests/promtail/fixtures/*.txt, label statis meniru relabel docker_sd). fixtures/generic.txt juga
#      dilewatkan ke pipeline journal -> kedua file wajib berperilaku sama.
#   3. Keluaran dinormalisasi (timestamp dibuang, diurutkan) lalu dibandingkan dengan expected.txt.
#   4. Penjaga independen: tidak boleh ada string `SENTINEL` (nilai rahasia fixture) di keluaran, dan jumlah
#      entri harus lengkap (baris yang dibuang/tertelan pipeline = gagal).
# Semua data fixture fiktif (nomor HP rekaan, token bernama SENTINEL*); tidak ada secret nyata.
set -euo pipefail

# Git Bash (Windows): cegah konversi path MSYS pada argumen `docker -v` (skrip ini hanya memanggil docker).
export MSYS_NO_PATHCONV=1

PROMTAIL_IMAGE="grafana/promtail:3.2.1@sha256:bf617e9d67e80247a59f717f9c1ad388d7d32dc0a1d29abd5799516d15e0a9b5"
YQ_IMAGE="mikefarah/yq:4.44.3@sha256:5b3d851bf28b04ef902fee86305f4dd7e063919c1cafedab2042adb16221c025"
WAIT_SECONDS="${PROMTAIL_TEST_WAIT:-60}"

update=0
case "${1:-}" in
  --update) update=1 ;;
  "") ;;
  *) echo "pemakaian: $0 [--update]" >&2; exit 2 ;;
esac

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
obs="$root/infra/observability"
tdir="$obs/tests/promtail"

# Path host untuk `docker -v` (Git Bash: C:/...; Linux: apa adanya).
hostpath() {
  if command -v cygpath > /dev/null 2>&1; then cygpath -m "$1"; else printf '%s\n' "$1"; fi
}

work="$(mktemp -d)"
cname="efn-promtail-redaction-test-$$"
cleanup() {
  docker rm -f "$cname" > /dev/null 2>&1 || true
  rm -rf "$work"
}
trap cleanup EXIT

mkdir -p "$work/journal"
cp "$tdir/fixtures/generic.txt" "$work/journal/generic.txt"
chmod -R a+rX "$work"

# 1. Konfigurasi uji = kerangka + pipeline_stages produksi
docker run --rm -v "$(hostpath "$obs"):/obs:ro" "$YQ_IMAGE" eval \
  '.scrape_configs[0].pipeline_stages = load("/obs/promtail.yml").scrape_configs[0].pipeline_stages
   | .scrape_configs[1].pipeline_stages = load("/obs/promtail-journal.yml").scrape_configs[0].pipeline_stages' \
  /obs/tests/promtail/harness.yml > "$work/promtail-test.yml"
for job in docker host-journal; do
  n="$(docker run --rm -v "$(hostpath "$work"):/w:ro" "$YQ_IMAGE" eval \
    ".scrape_configs[] | select(.job_name == \"$job\") | .pipeline_stages | length" /w/promtail-test.yml)"
  if [ "${n:-0}" -lt 1 ]; then
    echo "::error::pipeline_stages job $job kosong (struktur promtail*.yml berubah?)" >&2
    exit 1
  fi
done

# Jumlah entri yang diharapkan = semua baris fixture + generic.txt sekali lagi (pipeline journal)
expected_n=$(( $(cat "$tdir"/fixtures/*.txt | wc -l) + $(wc -l < "$tdir/fixtures/generic.txt") ))

# 2. Promtail -dry-run (tidak push; entri dicetak ke stdout)
docker run -d --name "$cname" \
  -v "$(hostpath "$work"):/w:ro" \
  -v "$(hostpath "$tdir/fixtures"):/fixtures:ro" \
  -v "$(hostpath "$work/journal"):/journal:ro" \
  "$PROMTAIL_IMAGE" -config.file=/w/promtail-test.yml -dry-run -log.level=warn > /dev/null

# Baris entri dry-run: "<RFC3339 timestamp>\t{label=...}<\t?>baris". Promtail -dry-run menulis timestamp, tab, label,
# dan baris sebagai write terpisah dari beberapa goroutine: tab pemisah kadang berpindah ke entri lain (CI flaky
# 9 Okt 2026, 66/67 entri). Fixture tidak mengandung tab -> semua tab dibuang sebelum dihitung/dinormalisasi.
is_entry='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+(Z|[+-][0-9]{2}:?[0-9]{2})[{]'
got_n=0
for _ in $(seq 1 "$WAIT_SECONDS"); do
  got_n="$(docker logs "$cname" 2> /dev/null | tr -d '\t' | grep -cE "$is_entry" || true)"
  [ "$got_n" -ge "$expected_n" ] && break
  sleep 1
done
docker logs "$cname" 2> "$work/err.raw" | tr -d '\t' > "$work/out.raw"
docker rm -f "$cname" > /dev/null 2>&1 || true

# 3. Normalisasi: buang timestamp; "{labels}baris" -> "{labels} baris"; urutkan stabil
grep -E "$is_entry" "$work/out.raw" \
  | sed -E 's/^[0-9T:.Z+-]*([{][^}]*[}])/\1 /' \
  | LC_ALL=C sort > "$work/actual.txt"

if [ "$got_n" -ne "$expected_n" ]; then
  echo "::error::Promtail menghasilkan $got_n entri, diharapkan $expected_n (baris hilang/tertelan pipeline?)" >&2
  diff "$tdir/expected.txt" "$work/actual.txt" >&2 || true
  sed -n '1,40p' "$work/err.raw" >&2
  exit 1
fi

if [ "$update" -eq 1 ]; then
  cp "$work/actual.txt" "$tdir/expected.txt"
  echo "expected.txt diperbarui ($expected_n entri). Tinjau diff-nya sebelum commit."
  exit 0
fi

fail=0
# 4a. Penjaga independen: nilai rahasia fixture tidak boleh lolos
if grep -n 'SENTINEL' "$work/actual.txt" >&2; then
  echo "::error::nilai rahasia fixture (SENTINEL*) lolos redaksi" >&2
  fail=1
fi
# 4b. Pipeline journal == pipeline docker untuk generic.txt
diff <(grep '^{container="efn-fixture-1"' "$work/actual.txt" | sed -E 's/^[{][^}]*[}] //') \
     <(grep '^{filename="/journal/generic.txt"' "$work/actual.txt" | sed -E 's/^[{][^}]*[}] //') >&2 || {
  echo "::error::redaksi promtail-journal.yml berbeda dari promtail.yml (salin tahap (a)-(d) persis)" >&2
  fail=1
}
# 4c. Keluaran persis sama dengan expected.txt
if ! diff -u "$tdir/expected.txt" "$work/actual.txt" >&2; then
  echo "::error::keluaran redaksi Promtail berubah. Bila disengaja: scripts/test-promtail-redaction.sh --update + tinjau" >&2
  fail=1
fi

if [ "$fail" -ne 0 ]; then exit 1; fi
echo "OK: redaksi Promtail ($expected_n entri, docker + journal) sesuai expected.txt"
