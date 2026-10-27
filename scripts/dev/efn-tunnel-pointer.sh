#!/usr/bin/env bash
# Penjaga alamat tunnel dev untuk APK uji (lokal saja; bukan untuk staging/produksi).
#
# Masalah: quick tunnel Cloudflare (`efn-tunnel`) mendapat alamat *.trycloudflare.com BARU setiap kali container
# start (laptop nyala, Docker Desktop restart), dan alamat lama bisa MATI tanpa container berhenti bila koneksi ke edge
# Cloudflare putus lama (laptop tidur, Wi-Fi putus; log cloudflared: "dial tcp ...:7844: i/o timeout"). APK uji membaca
# alamat BFF dari file pointer (gist publik `efn-api.json`, isi `{"api_base_url":"https://<x>.trycloudflare.com"}`)
# saat dibuka dan setiap kali permintaan gagal karena jaringan.
#
# Skrip ini berjalan terus di laptop (startup Windows, lihat install-tunnel-pointer.sh). Setiap INTERVAL detik:
#   1. Docker belum jalan -> nyalakan Docker Desktop (paling sering 1x per 5 menit).
#   2. Container tunnel mati -> docker start.
#   3. `/health` lewat tunnel != 200 selama RESTART_AFTER putaran berturut-turut -> docker restart tunnel (alamat baru).
#   4. Tunnel sehat -> pastikan gist berisi alamat itu (PATCH bila berbeda).
# Tidak ada secret yang ditulis: gist hanya memuat alamat tunnel (publik), autentikasi memakai `gh` yang sudah login.
#
# Pemakaian:
#   bash scripts/dev/efn-tunnel-pointer.sh            # loop (dipanggil startup Windows)
#   bash scripts/dev/efn-tunnel-pointer.sh --once     # satu putaran sekarang; kode keluar 0 = pointer sehat
#   bash scripts/dev/efn-tunnel-pointer.sh --status   # ringkasan status + 5 baris log terakhir
set -u

GIST_ID="${EFN_TUNNEL_GIST_ID:-573f07cbf9b051029976e907f3e274a2}"
GIST_FILE="efn-api.json"
CONTAINER="${EFN_TUNNEL_CONTAINER:-efn-tunnel}"
INTERVAL="${EFN_TUNNEL_INTERVAL:-30}"
RESTART_AFTER="${EFN_TUNNEL_RESTART_AFTER:-4}"          # 4 x 30 dtk = ±2 menit tidak sehat -> restart tunnel
DOCKER_DESKTOP="${EFN_DOCKER_DESKTOP:-${LOCALAPPDATA:-}/Programs/DockerDesktop/Docker Desktop.exe}"
STATE_DIR="${LOCALAPPDATA:-$HOME}/efn"
LOG="$STATE_DIR/tunnel-pointer.log"
BEAT="$STATE_DIR/tunnel-pointer.beat"                    # detak per putaran = penanda instans hidup (lintas sesi bash)
STATUS="$STATE_DIR/tunnel-pointer.status"
mkdir -p "$STATE_DIR"

log() {
  # log berputar sederhana: > 512 KB -> simpan 200 baris terakhir
  if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 524288 ]; then tail -200 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"; fi
  printf '%s %s\n' "$(date '+%F %T')" "$*" >> "$LOG"
}

set_state() { # $1 = kunci status, $2 = pesan; log hanya bila status berubah
  [ "${LAST_STATE:-}" = "$1" ] || log "$2"
  LAST_STATE="$1"
  printf '%s %s\n' "$(date '+%F %T')" "$2" > "$STATUS"
}

current_tunnel() {
  docker logs "$CONTAINER" 2>&1 | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1
}

gist_value() {
  gh api "gists/$GIST_ID" --jq ".files[\"$GIST_FILE\"].content" 2>/dev/null | tr -d '\r\n'
}

healthy() { # dua percobaan: satu kegagalan sesaat tidak dihitung
  local _i
  for _i in 1 2; do
    [ "$(curl -s -m 15 -o /dev/null -w '%{http_code}' "$1/health")" = 200 ] && return 0
    sleep 3
  done
  return 1
}

start_docker_desktop() {
  local now; now=$(date +%s)
  [ $((now - ${LAST_DD_START:-0})) -ge 300 ] || return 0
  LAST_DD_START=$now
  if [ -f "$DOCKER_DESKTOP" ]; then
    log "menyalakan Docker Desktop"
    powershell.exe -NoProfile -Command "Start-Process -FilePath '$DOCKER_DESKTOP'" >/dev/null 2>&1 || log "gagal menyalakan Docker Desktop"
  fi
}

# Satu putaran. 0 = pointer menunjuk tunnel yang sehat; 1 = belum.
tick() {
  if ! docker info >/dev/null 2>&1; then
    set_state docker_down "Docker belum jalan; menunggu"
    start_docker_desktop
    return 1
  fi
  if [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" != true ]; then
    log "container $CONTAINER tidak jalan; docker start"
    docker start "$CONTAINER" >/dev/null 2>&1 || { log "docker start $CONTAINER gagal"; return 1; }
    FAILS=0
    sleep 10
  fi
  local url want have
  url="$(current_tunnel)"
  if [ -z "$url" ] || ! healthy "$url"; then
    FAILS=$(( ${FAILS:-0} + 1 ))
    set_state "unhealthy $url" "tunnel ${url:-<belum ada alamat>} belum sehat (/health != 200)"
    if [ "$FAILS" -ge "$RESTART_AFTER" ]; then
      log "tidak sehat $FAILS putaran berturut-turut; docker restart $CONTAINER (alamat baru)"
      docker restart "$CONTAINER" >/dev/null 2>&1 || log "docker restart $CONTAINER gagal"
      FAILS=0
      LAST_PUBLISHED=
      sleep 15
    fi
    return 1
  fi
  FAILS=0
  want="{\"api_base_url\":\"$url\"}"
  if [ "${LAST_PUBLISHED:-}" != "$want" ]; then
    have="$(gist_value)"
    if [ "$have" != "$want" ]; then
      if gh api -X PATCH "gists/$GIST_ID" -f "files[$GIST_FILE][content]=$want" >/dev/null 2>&1; then
        log "pointer diperbarui -> $url"
      else
        set_state gist_fail "gagal memperbarui gist (gh auth / jaringan)"
        return 1
      fi
    fi
    LAST_PUBLISHED="$want"
  fi
  set_state "ok $url" "sehat: $url"
  return 0
}

beat_age() { echo $(( $(date +%s) - $(date -r "$BEAT" +%s 2>/dev/null || echo 0) )); }

case "${1:-}" in
  --once)
    tick; rc=$?
    tail -3 "$LOG" 2>/dev/null
    echo "pointer: $(gist_value)"
    exit $rc ;;
  --status)
    echo "status : $(cat "$STATUS" 2>/dev/null || echo '-')"
    if [ -f "$BEAT" ]; then echo "detak  : $(beat_age) dtk lalu"; else echo "detak  : belum pernah (penjaga tidak jalan)"; fi
    echo "pointer: $(gist_value)"
    tail -5 "$LOG" 2>/dev/null
    exit 0 ;;
esac

# Satu instans saja. PID bash tidak terlihat lintas sesi Git Bash (versi lama memakai pidfile dan bisa dobel),
# jadi pakai detak file: detak segar (< 3 interval + 60 dtk) = instans lain masih hidup.
if [ -f "$BEAT" ] && [ "$(beat_age)" -lt $(( INTERVAL * 3 + 60 )) ]; then exit 0; fi
touch "$BEAT"
log "mulai (gist $GIST_ID, interval ${INTERVAL}s, restart tunnel setelah $RESTART_AFTER putaran gagal)"
while true; do
  touch "$BEAT"
  tick || true
  touch "$BEAT"
  sleep "$INTERVAL"
done
