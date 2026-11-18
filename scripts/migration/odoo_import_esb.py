"""Importer migrasi ESB -> Odoo, dijalankan DI DALAM `odoo shell` (G43 W2b; ADR-042; docs/04 §27.2/§27.5). JANGAN
dijalankan langsung: pakai `scripts/migration/run-import.sh` (atau perintah runbook `migrasi-esb-odoo.md` §4.2).

    docker compose ... -f docker-compose.migration.yml exec -T -e EFN_MIG_ARGV='<json>' odoo \
        odoo shell -d "$ODOO_DB" --no-http --log-level=warn < scripts/migration/odoo_import_esb.py

Argumen = env `EFN_MIG_ARGV` (JSON). Path relatif terhadap `EFN_MIG_EXCHANGE_DIR`; path absolut wajib DI DALAM
direktori itu (selain itu exit 2):
    {"bundle": "<run>.efnmig",                 # wajib: bundel master atau segmen histori (kind=bundle)
     "validation": "validation_<run>.json",    # wajib kecuali dry-run non-production + "allow_missing_validation"
     "dry_run": true,                          # default true; run nyata = false
     "companies": ["PB"],                      # opsional: target (default = company brand: pb->PB, bacha->BCF,BCR …)
     "steps": ["master"],                      # opsional: subset langkah §27.2 (default = semua berkas di bundel)
     "confirm_brand": "pb",                    # wajib bila APP_ENV production dan dry_run=false
     "enable_opening_balances": false,         # stock/opening: STUB, selalu exit 2 (data E6+ belum diverifikasi)
     "allow_missing_validation": false}
Env overlay `infra/compose/docker-compose.migration.yml`: `EFN_MIG_BUNDLE_KEY_FILE` (secret `migration_bundle_key`),
`EFN_MIG_EXCHANGE_DIR` (=/mnt/efn-mig; baca bundel & validasi, tulis hasil 0600), `EFN_MIG_CODE_DIR`
(=/opt/efn-migration; `efn_mig` resmi dari repo, dipakai bila ada — selain itu salinan terkendali di modul),
`EFN_MIG_USER_LOGIN`
(default `efn_migration`), `APP_ENV`, `EFN_MIG_SALDO_BLOCKED` (default `IMD,CUP`).

Keluaran: `<run_id>.result.efnmig` (run nyata) / `<run_id>.dryrun.result.efnmig` (dry-run) di direktori pertukaran +
satu baris `EFN_RESULT <json ringkas, tanpa data bisnis/pribadi>` di stdout. Exit: 0 OK · 1 ada baris/irisan gagal ·
2 argumen/konfigurasi · 3 integritas · 4 gerbang. Dry-run = savepoint di-rollback + `cr.rollback()` (tanpa commit);
run nyata = commit per entitas/irisan oleh importer.
"""

import json
import os
import re
import sys
from pathlib import Path

_CODE = re.compile(r"^[A-Z0-9]{2,8}$")
_STEP = re.compile(r"^[a-z_-]{2,32}$")


class _ArgError(Exception):
    pass


def _efn_mig_bundle():
    """`efn_mig.bundle` resmi dari EFN_MIG_CODE_DIR bila ada; fallback salinan terkendali modul (isi identik)."""
    code_dir = os.environ.get("EFN_MIG_CODE_DIR")
    if code_dir and Path(code_dir, "efn_mig", "bundle.py").is_file():
        if code_dir not in sys.path:
            sys.path.insert(0, code_dir)
        from efn_mig import bundle as official  # noqa: PLC0415

        return official
    from odoo.addons.efn_legacy_history.esb_import.efn_mig import bundle as vendored  # noqa: PLC0415

    return vendored


def _inside(base: Path, raw, what: str, required: bool = True) -> Path | None:
    if raw in (None, ""):
        if required:
            raise _ArgError(f"argumen {what} wajib")
        return None
    if not isinstance(raw, str) or "\x00" in raw:
        raise _ArgError(f"argumen {what} tidak valid")
    path = (base / raw).resolve() if not os.path.isabs(raw) else Path(raw).resolve()
    if base not in path.parents:
        raise _ArgError(f"{what} wajib berada di dalam EFN_MIG_EXCHANGE_DIR")
    return path


def _main(env):
    from odoo.addons.efn_legacy_history.esb_import import importer as imp  # noqa: PLC0415
    from odoo.addons.efn_legacy_history.esb_import.efn_mig import bundle as vendored  # noqa: PLC0415

    summary = {"exit_code": imp.EXIT_CONFIG}
    integrity = (vendored.IntegrityError,)
    try:
        args = json.loads(os.environ.get("EFN_MIG_ARGV") or "{}")
        if not isinstance(args, dict):
            raise _ArgError("EFN_MIG_ARGV wajib objek JSON")
        exchange = Path(os.environ.get("EFN_MIG_EXCHANGE_DIR") or "/mnt/efn-mig").resolve()
        if not exchange.is_dir() or not os.access(exchange, os.R_OK | os.W_OK):
            raise _ArgError("EFN_MIG_EXCHANGE_DIR tidak ada / tidak bisa dibaca-tulis (overlay migration terpasang?)")
        bundle_path = _inside(exchange, args.get("bundle"), "bundle")
        validation_path = _inside(exchange, args.get("validation"), "validation", required=False)
        dry_run = args.get("dry_run", True)
        if not isinstance(dry_run, bool):
            raise _ArgError("dry_run wajib boolean")
        companies = args.get("companies")
        if companies is not None and (
            not isinstance(companies, list) or not all(isinstance(c, str) and _CODE.fullmatch(c) for c in companies)
        ):
            raise _ArgError('companies wajib daftar kode PT (mis. ["PB"])')
        steps = args.get("steps") or []
        if not isinstance(steps, list) or not all(isinstance(s, str) and _STEP.fullmatch(s) for s in steps):
            raise _ArgError("steps wajib daftar nama langkah")
        key_file = Path(os.environ.get("EFN_MIG_BUNDLE_KEY_FILE") or "/run/secrets/migration_bundle_key")
        if not key_file.is_file():
            raise _ArgError("kunci bundel (EFN_MIG_BUNDLE_KEY_FILE) tidak ada")
        official = _efn_mig_bundle()
        integrity = (vendored.IntegrityError, official.IntegrityError)
        keys = official.derive_keys(key_file.read_text(encoding="ascii"))
        bundle = official.open_bundle(bundle_path.read_bytes(), keys, expect_kind="bundle")
        validation = None
        if validation_path is not None:
            validation = json.loads(validation_path.read_text(encoding="utf-8"))
            if not isinstance(validation, dict):
                raise _ArgError("berkas validasi bukan objek JSON")
        brand = str(bundle.manifest["brand"])
        targets = frozenset(companies) if companies is not None else imp.default_targets(brand)
        blocked = os.environ.get("EFN_MIG_SALDO_BLOCKED", "IMD,CUP")
        opts = imp.Options(
            companies=targets,
            dry_run=dry_run,
            steps=tuple(steps),
            confirm_brand=args.get("confirm_brand"),
            app_env=os.environ.get("APP_ENV", ""),
            saldo_blocked=frozenset(c.strip() for c in blocked.split(",") if c.strip()),
            enable_opening_balances=bool(args.get("enable_opening_balances")),
            migration_login=os.environ.get("EFN_MIG_USER_LOGIN") or "efn_migration",
            validation=validation,
            allow_missing_validation=bool(args.get("allow_missing_validation")),
        )
        summary.update(run_id=bundle.manifest["run_id"], brand=brand, dry_run=dry_run)
        result = imp.run_import(env, bundle, opts, commit=None if dry_run else env.cr.commit)
        if dry_run:
            env.cr.rollback()
        else:
            env.cr.commit()
        out = exchange / imp.result_filename(bundle, dry_run)
        tmp = out.with_name(out.name + ".part")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(imp.build_result(bundle, result, keys))
        os.chmod(tmp, 0o600)
        tmp.replace(out)
        summary.update(
            exit_code=result.exit_code,
            result_file=out.name,
            entities=result.report["entities"],
            slices=len(result.report["slices"]),
            slices_failed=sum(1 for s in result.report["slices"] if "error" in s),
            table_bytes=result.report["table_bytes"],
            skipped_by_param=result.report["skipped_by_param"],
            notes=result.report["notes"][:20],
            errors=result.report["errors"][:20],
        )
    except _ArgError as exc:
        env.cr.rollback()
        summary.update(exit_code=imp.EXIT_CONFIG, error=str(exc))
    except imp.ConfigError as exc:
        env.cr.rollback()
        summary.update(exit_code=imp.EXIT_CONFIG, error=str(exc))
    except imp.GateError as exc:
        env.cr.rollback()
        summary.update(exit_code=imp.EXIT_GATE, error=f"gerbang ditolak: {exc}")
    except (ValueError, OSError) as exc:  # BundleKeyError (ValueError), JSON rusak, I/O
        env.cr.rollback()
        if isinstance(exc, integrity):
            summary.update(exit_code=imp.EXIT_INTEGRITY, error=f"integritas: {exc}")
        else:
            summary.update(exit_code=imp.EXIT_CONFIG, error=f"{type(exc).__name__}: {str(exc)[:200]}")
    except Exception as exc:  # noqa: BLE001
        env.cr.rollback()
        if isinstance(exc, integrity):
            summary.update(exit_code=imp.EXIT_INTEGRITY, error=f"integritas: {exc}")
        else:
            raise
    return summary


_summary = _main(env)  # noqa: F821  (variabel global odoo shell)
print("EFN_RESULT " + json.dumps(_summary, ensure_ascii=False, sort_keys=True, default=str), flush=True)
if _summary.get("exit_code"):
    sys.exit(int(_summary["exit_code"]))
