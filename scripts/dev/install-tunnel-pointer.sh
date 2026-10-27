#!/usr/bin/env bash
# Pasang/lepas penjaga pointer tunnel (efn-tunnel-pointer.sh) agar otomatis jalan setiap login Windows,
# tersembunyi (tanpa jendela konsol). Lokasi: folder Startup user (tanpa hak admin, tanpa Task Scheduler).
#   bash scripts/dev/install-tunnel-pointer.sh            # pasang + jalankan sekarang
#   bash scripts/dev/install-tunnel-pointer.sh --remove   # lepas
set -eu
STARTUP="$APPDATA/Microsoft/Windows/Start Menu/Programs/Startup"
VBS="$STARTUP/efn-tunnel-pointer.vbs"
SCRIPT_WIN="$(cd "$(dirname "$0")" && pwd -W)/efn-tunnel-pointer.sh"
BASH_WIN='C:\Program Files\Git\bin\bash.exe'

if [ "${1:-}" = "--remove" ]; then
  rm -f "$VBS"
  pid="$(cat "${LOCALAPPDATA}/efn/tunnel-pointer.pid" 2>/dev/null || true)"
  [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  echo "dilepas"
  exit 0
fi

command -v gh >/dev/null || { echo "gh CLI belum terpasang"; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "gh belum login (gh auth login, scope gist)"; exit 1; }

# WScript.Shell.Run(..., 0 = tersembunyi, False = tidak menunggu)
printf '%s\r\n' \
  "' EFN: penjaga alamat tunnel dev untuk APK uji (scripts/dev/efn-tunnel-pointer.sh)" \
  "CreateObject(\"WScript.Shell\").Run \"\"\"$BASH_WIN\"\" -lc \"\"bash '${SCRIPT_WIN//\\//}'\"\"\", 0, False" \
  > "$VBS"
echo "terpasang: $VBS"
cscript.exe //nologo "$(cygpath -w "$VBS")" && echo "dijalankan"
