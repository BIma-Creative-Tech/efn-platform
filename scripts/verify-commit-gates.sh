#!/usr/bin/env bash
# Gerbang rilis (SEC-80, r9): pastikan workflow CI & Security (dan opsional Build & Push) untuk COMMIT YANG SAMA sudah
# selesai `success` sebelum image di-push/ditandatangani (build-push.yml job `gate`) atau dideploy (deploy.yml job
# `resolve`). Pengganti branch protection yang tidak tersedia di repo privat paket gratis.
#
# Mengapa Actions API (bukan check-runs per nama): nama check-run tidak unik antar-workflow; di sini run dipilih per
# FILE workflow (.github/workflows/<file>) + head_sha + event=push, lalu job yang diwajibkan dicek di run itu.
#
# Pemakaian (env GH_TOKEN + GITHUB_REPOSITORY; izin token: actions: read):
#   verify-commit-gates.sh --sha <40 hex> [--timeout-min 60] [--absent-min 10] [--interval 30] \
#     --require 'ci.yml@main=CI gate' \
#     --require 'security.yml@main=Secret scan (gitleaks)|SAST (semgrep)' \
#     --require 'build-push.yml@*'
#   verify-commit-gates.sh --sha <sha> --preset release      # = CI gate + 4 job Security di bawah (satu sumber)
#   Spesifikasi: <file workflow>@<branch|*>[=<job>|<job>...]. Tanpa daftar job: hanya kesimpulan run.
#   Nama job = `name:` di workflow (ci.yml `CI gate` mencakup npm audit/pip-audit/test; security.yml 4 scanner).
#   Mengganti `name:` job tersebut WAJIB diikuti perubahan PRESET_RELEASE di bawah (gate gagal-tertutup: "absent").
#   Run yang dipilih = run TERBARU (created_at) untuk file + head_sha + event=push (+ branch bila bukan *).
# Gagal (exit 1): run tidak ditemukan setelah --absent-min menit, run selesai selain success, job wajib tidak ada /
# bukan success, atau batas --timeout-min habis saat masih berjalan. Tidak ada mode lunak.
set -euo pipefail

SHA="" TIMEOUT_MIN=60 ABSENT_MIN=10 INTERVAL=30
REQS=()
PRESET_RELEASE=(
  'ci.yml@main=CI gate'
  'security.yml@main=Secret scan (gitleaks)|SAST (semgrep)|Trivy filesystem (vuln, secret, misconfig)|IaC (checkov: Dockerfile, GitHub Actions)'
)
while [[ $# -gt 0 ]]; do
  case "$1" in
    --sha) SHA="$2"; shift 2 ;;
    --timeout-min) TIMEOUT_MIN="$2"; shift 2 ;;
    --absent-min) ABSENT_MIN="$2"; shift 2 ;;
    --interval) INTERVAL="$2"; shift 2 ;;
    --require) REQS+=("$2"); shift 2 ;;
    --preset)
      [[ "$2" == release ]] || { echo "::error::preset tidak dikenal: $2" >&2; exit 2; }
      REQS+=("${PRESET_RELEASE[@]}"); shift 2 ;;
    *) echo "::error::argumen tidak dikenal: $1" >&2; exit 2 ;;
  esac
done

[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "::error::--sha harus 40 hex" >&2; exit 2; }
[[ "${GITHUB_REPOSITORY:-}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || { echo "::error::GITHUB_REPOSITORY tidak valid" >&2; exit 2; }
[[ ${#REQS[@]} -gt 0 ]] || { echo "::error::minimal satu --require" >&2; exit 2; }
for n in "$TIMEOUT_MIN" "$ABSENT_MIN" "$INTERVAL"; do [[ "$n" =~ ^[0-9]+$ ]] || { echo "::error::angka tidak valid: $n" >&2; exit 2; }; done
for c in gh jq; do command -v "$c" > /dev/null || { echo "::error::butuh $c" >&2; exit 2; }; done

start=$SECONDS
deadline=$((start + TIMEOUT_MIN * 60))
absent_deadline=$((start + ABSENT_MIN * 60))
summary="${GITHUB_STEP_SUMMARY:-/dev/null}"

# Cek satu spesifikasi. Return: 0 = lolos, 1 = gagal permanen, 3 = belum (tunggu).
check_req() {
  local spec="$1" wf branch jobs_spec run_json run_id status conclusion url jobs_json job jc q
  local -a want
  wf="${spec%%=*}"; jobs_spec=""
  [[ "$spec" == *=* ]] && jobs_spec="${spec#*=}"
  branch="${wf#*@}"; wf="${wf%%@*}"
  [[ "$wf" =~ ^[A-Za-z0-9_.-]+\.ya?ml$ ]] || { echo "::error::file workflow tidak valid: $wf"; return 1; }
  q="head_sha=${SHA}&event=push&per_page=20"
  [[ "$branch" != "*" && -n "$branch" ]] && q="${q}&branch=${branch}"
  run_json=$(gh api "repos/${GITHUB_REPOSITORY}/actions/workflows/${wf}/runs?${q}" \
    --jq '[.workflow_runs[] | select(.head_sha == "'"$SHA"'")] | sort_by(.created_at) | last // empty') || {
    echo "  ${wf}: API gagal (dicoba lagi)"; return 3; }
  if [[ -z "$run_json" ]]; then
    if (( SECONDS >= absent_deadline )); then
      echo "::error::${wf}: tidak ada run push untuk ${SHA:0:12} (branch ${branch}) setelah ${ABSENT_MIN} menit"; return 1
    fi
    echo "  ${wf}: run belum ada"; return 3
  fi
  run_id=$(jq -r '.id' <<< "$run_json"); status=$(jq -r '.status' <<< "$run_json")
  conclusion=$(jq -r '.conclusion // ""' <<< "$run_json"); url=$(jq -r '.html_url' <<< "$run_json")
  if [[ "$status" != "completed" ]]; then echo "  ${wf}: ${status} (${url})"; return 3; fi
  if [[ "$conclusion" != "success" ]]; then
    echo "::error::${wf}: run selesai '${conclusion}' untuk ${SHA:0:12} (${url})"
    echo "| \`${wf}\` | ${conclusion} | [run](${url}) |" >> "$summary"
    return 1
  fi
  if [[ -n "$jobs_spec" ]]; then
    jobs_json=$(gh api "repos/${GITHUB_REPOSITORY}/actions/runs/${run_id}/jobs?filter=latest&per_page=100") || {
      echo "  ${wf}: API jobs gagal (dicoba lagi)"; return 3; }
    IFS='|' read -r -a want <<< "$jobs_spec"
    for job in "${want[@]}"; do
      jc=$(jq -r --arg n "$job" '[.jobs[] | select(.name == $n) | .conclusion // "none"] | if length == 1 then .[0] elif length == 0 then "absent" else "duplicate" end' <<< "$jobs_json")
      if [[ "$jc" != "success" ]]; then
        echo "::error::${wf}: job '${job}' = ${jc} (wajib success) (${url})"
        echo "| \`${wf}\` / ${job} | ${jc} | [run](${url}) |" >> "$summary"
        return 1
      fi
    done
  fi
  echo "  ${wf}: success (${url})"
  echo "| \`${wf}\` ${jobs_spec:+/ ${jobs_spec//|/, }} | success | [run](${url}) |" >> "$summary"
  return 0
}

{ echo "### Gerbang rilis untuk \`${SHA}\` (SEC-80)"; echo; echo "| Workflow / job | Hasil | Run |"; echo "|---|---|---|"; } >> "$summary"
pending=("${REQS[@]}")
while :; do
  next=()
  echo "[$(( (SECONDS - start) / 60 )) mnt] memeriksa ${#pending[@]} workflow untuk ${SHA:0:12}"
  for spec in "${pending[@]}"; do
    rc=0; check_req "$spec" || rc=$?
    case "$rc" in
      0) ;;
      3) next+=("$spec") ;;
      *) echo "::error::gerbang rilis GAGAL: image tidak boleh di-push/ditandatangani/dideploy"; exit 1 ;;
    esac
  done
  [[ ${#next[@]} -eq 0 ]] && { echo "gerbang rilis: semua workflow wajib success"; exit 0; }
  if (( SECONDS + INTERVAL > deadline )); then
    echo "::error::batas waktu ${TIMEOUT_MIN} menit habis; belum selesai: ${next[*]}"
    exit 1
  fi
  pending=("${next[@]}")
  sleep "$INTERVAL"
done
