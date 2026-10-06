#!/usr/bin/env bash
# Gerbang "tanpa modul pihak ketiga" (CLAUDE.md, commit 244de70): setiap `depends` di
# services/odoo/addons/efn_*/__manifest__.py harus modul Odoo CE 19 bawaan atau modul `efn_*` di repo.
# OCA/Cybrosys/vendor lain hanya referensi, BUKAN dependensi.
#
# Pemakaian: check-odoo-deps.sh <file daftar modul CE (satu nama per baris)>
#   Daftar CE diambil dari image Odoo, mis.:
#   docker run --rm --entrypoint ls efn-odoo:ci /usr/lib/python3/dist-packages/odoo/addons > /tmp/ce-modules.txt
# Exit 1 bila ada dependensi di luar CE/efn_* (dicetak per modul).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CE_LIST="${1:?pemakaian: $0 <daftar-modul-ce.txt>}"
[[ -s "$CE_LIST" ]] || { echo "check-odoo-deps: daftar modul CE kosong: $CE_LIST" >&2; exit 2; }
command -v python3 > /dev/null || { echo "check-odoo-deps: python3 dibutuhkan" >&2; exit 2; }

python3 - "$ROOT_DIR/services/odoo/addons" "$CE_LIST" <<'PY'
import ast, pathlib, sys
addons = pathlib.Path(sys.argv[1])
ce = {l.strip() for l in open(sys.argv[2], encoding="utf-8") if l.strip()}
ce.add("base")
own = {p.parent.name for p in addons.glob("*/__manifest__.py")}
bad = 0
for man in sorted(addons.glob("efn_*/__manifest__.py")):
    data = ast.literal_eval(man.read_text(encoding="utf-8"))
    for dep in data.get("depends", []):
        if dep in ce or (dep.startswith("efn_") and dep in own):
            continue
        print(f"::error::{man.parent.name} depends '{dep}' (bukan Odoo CE 19 / efn_*)")
        bad += 1
print(f"check-odoo-deps: {len(own)} modul diperiksa, {bad} dependensi terlarang")
sys.exit(1 if bad else 0)
PY
