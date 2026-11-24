"""Validator pra-impor migrasi (docs/04 §27.7 v19.1; perluasan docs/08 §6.4 G13-7).

  python validate_import.py --source esb-bundle <bundel.efnmig> [<bundel2.efnmig> ...] [--out DIR]

Membuka setiap bundel (batas ukuran SEC-200 -> header -> dekripsi -> HMAC -> sha256/rows; gagal = exit 3) lalu
menjalankan aturan §27.7. Segmen histori yang dipecah (parts > 1) WAJIB divalidasi bersama semua part-nya: gudang
tujuan tiap part harus saling lepas (SEC-197; tumpang tindih = exit 3, part tidak lengkap = error PARTS_INCOMPLETE).
Keluaran per bundel `validation_<run_id>.json` (+ `.md`) BERTANDA TANGAN (SEC-201: HMAC kunci HKDF
`efn:mig-esb:val:v1`, mengikat run_id + `bundle_sha256` + `manifest_sha256`); importer memverifikasi dengan
`efn_mig.bundle.verify_validation`. Berkas ditulis O_EXCL|O_NOFOLLOW + rename atomik (SEC-202).
Exit 0 bila semua errors = 0, 1 bila ada error, 2 konfigurasi, 3 integritas.
Mode `--source templates` (CSV `docs/templates/migrasi/`, aturan barcode/HP/pajak docs/08 §6.4) BELUM dibangun.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from efn_mig.bundle import IntegrityError, read_bundle, write_secure
from efn_mig.settings import ConfigError, MigSettings, bundle_keys
from efn_mig.validate import check_segment_set, validate_bundle


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="validate_import.py", description="Validator bundel migrasi ESB")
    parser.add_argument("--source", required=True, choices=("esb-bundle", "templates"))
    parser.add_argument("paths", type=Path, nargs="+")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if args.source == "templates":
        print("mode templates (docs/08 §6.4) belum dibangun", file=sys.stderr)
        return 2
    try:
        keys = bundle_keys()
        settings = MigSettings.from_env()
        out_dir = args.out or settings.out_dir
        bundles = [read_bundle(path, keys, expect_kind="bundle") for path in args.paths]
        manifests = [b.manifest for b in bundles]
        check_segment_set(manifests)  # tumpang tindih gudang antar-part -> IntegrityError
        # G65 Q-ESB25: lokasi stok berpola damage/expired tanpa location_map = error di production.
        reports = [validate_bundle(b, manifests, production=settings.production) for b in bundles]
    except IntegrityError as exc:
        print(f"integritas: {exc}", file=sys.stderr)
        return 3
    except (ConfigError, ValueError) as exc:
        print(f"konfigurasi: {exc}", file=sys.stderr)
        return 2
    failed = 0
    for report in reports:
        stem = f"validation_{report.run_id}"
        signed = report.to_signed_json(keys)
        write_secure(out_dir / f"{stem}.json", json.dumps(signed, ensure_ascii=False, indent=1).encode("utf-8"))
        write_secure(out_dir / f"{stem}.md", report.to_markdown().encode("utf-8"))
        print(f"{stem}: errors={report.errors} warnings={report.warnings}")
        failed += 1 if report.errors else 0
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
