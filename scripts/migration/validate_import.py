"""Validator pra-impor migrasi (docs/04 §27.7 v19.1; perluasan docs/08 §6.4 G13-7).

  python validate_import.py --source esb-bundle <bundel.efnmig> [--out DIR]

Membuka bundel (header -> dekripsi -> HMAC -> sha256/rows; gagal = exit 3) lalu menjalankan aturan §27.7. Keluaran
`validation_<run_id>.json` (+ `.md`) berisi `manifest_sha256`, `errors`, `warnings`, temuan (kode/entitas/kunci, tanpa
data pribadi). Exit 0 bila errors = 0, 1 bila ada error, 2 konfigurasi, 3 integritas. Importer Odoo mewajibkan laporan
dengan `manifest_sha256` sama dan `errors = 0` (gerbang §27.5).
Mode `--source templates` (CSV `docs/templates/migrasi/`, aturan barcode/HP/pajak docs/08 §6.4) BELUM dibangun.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from efn_mig.bundle import IntegrityError, read_bundle
from efn_mig.settings import ConfigError, MigSettings, bundle_keys
from efn_mig.validate import validate_bundle


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="validate_import.py", description="Validator bundel migrasi ESB")
    parser.add_argument("--source", required=True, choices=("esb-bundle", "templates"))
    parser.add_argument("path", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if args.source == "templates":
        print("mode templates (docs/08 §6.4) belum dibangun", file=sys.stderr)
        return 2
    try:
        keys = bundle_keys()
        out_dir = args.out or MigSettings.from_env().out_dir
        report = validate_bundle(read_bundle(args.path, keys, expect_kind="bundle"))
    except IntegrityError as exc:
        print(f"integritas: {exc}", file=sys.stderr)
        return 3
    except ConfigError as exc:
        print(f"konfigurasi: {exc}", file=sys.stderr)
        return 2
    stem = f"validation_{report.run_id}"
    (out_dir / f"{stem}.json").write_text(json.dumps(report.to_json(), ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / f"{stem}.md").write_text(report.to_markdown(), encoding="utf-8")
    print(f"{stem}: errors={report.errors} warnings={report.warnings}")
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
