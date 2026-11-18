"""Importer migrasi ESB -> Odoo, dijalankan DI DALAM `odoo shell` (G43 W2b; ADR-042; docs/04 §27.2/§27.5). JANGAN
dijalankan langsung: pakai `scripts/migration/run-import.sh` (container Odoo SEKALI JALAN `docker compose run --rm
--no-deps`, bukan service odoo yang melayani pengguna; SEC-200).

Argumen = env `EFN_MIG_ARGV` (JSON). Path relatif terhadap `EFN_MIG_EXCHANGE_DIR`; path absolut wajib DI DALAM
direktori itu (selain itu exit 2):
    {"bundle": "<run>.efnmig",                 # wajib (kecuali mode totals): bundel master / segmen histori
     "validation": "validation_<run>.json",    # wajib & BERTANDA TANGAN (SEC-201) kecuali dry-run dev longgar
     "dry_run": true,                          # default true; run nyata = false
     "companies": ["PB"],                      # opsional: target (default = company brand: pb->PB, bacha->BCF,BCR …)
     "steps": ["master"],                      # opsional: subset langkah §27.2 (default = semua berkas di bundel)
     "confirm_brand": "pb",                    # wajib bila APP_ENV production ATAU staging dan dry_run=false
     "allow_older": false,                     # SEC-198: izinkan bundel lebih tua dari run sukses terakhir (dicatat)
     "enable_opening_balances": false,         # stock/opening: STUB, selalu exit 2 (data E6+ belum diverifikasi)
     "allow_missing_validation": false}
Mode totals (tanpa bundel, tanpa tulis; untuk `reconcile --final`):
    {"steps": ["totals"], "companies": ["PB"], "months": ["2026-09", "2026-10"], "brand": "pb" (opsional)}
    -> `<run_id>.totals.result.efnmig` (kind=result, import_report.mode = "totals").

Env overlay `infra/compose/docker-compose.migration.yml`: `EFN_MIG_BUNDLE_KEY_FILE` (secret `migration_bundle_key`),
`EFN_MIG_EXCHANGE_DIR` (=/mnt/efn-mig), `EFN_MIG_CODE_DIR` (=/opt/efn-migration; `efn_mig` resmi bila ada, selain itu
salinan terkendali di modul), `EFN_MIG_USER_LOGIN` (default `efn_migration`), `APP_ENV`, `EFN_MIG_SALDO_BLOCKED`
(default `IMD,CUP`), `EFN_MIG_MAX_BUNDLE_BYTES` (default 1 GiB; bundel lebih besar ditolak SEBELUM dibaca).

Keluaran: `<run_id>.result.efnmig` (run nyata) / `<run_id>.dryrun.result.efnmig` (dry-run) di direktori pertukaran,
ditulis O_CREAT|O_EXCL|O_NOFOLLOW mode 0600 lalu rename atomik (SEC-202), + satu baris `EFN_RESULT <json ringkas, tanpa
data bisnis/pribadi>` di stdout. Exit: 0 OK · 1 ada baris/irisan gagal · 2 argumen/konfigurasi · 3 integritas ·
4 gerbang. Dry-run = savepoint di-rollback + `cr.rollback()` (tanpa commit); run nyata = commit per entitas/irisan.
"""

import json
import os
import re
import secrets
import sys
from pathlib import Path

_CODE = re.compile(r"^[A-Z0-9]{2,8}$")
_STEP = re.compile(r"^[a-z_-]{2,32}$")
_BRAND = re.compile(r"^[a-z0-9]{2,16}$")
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_DEFAULT_MAX_BUNDLE = 1024 * 1024 * 1024
_MAX_VALIDATION = 64 * 1024 * 1024


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


def _read_limited(path: Path, limit: int, what: str) -> bytes:
    """Baca berkas reguler (tanpa ikut symlink) dengan batas ukuran DIPERIKSA SEBELUM membaca (SEC-200)."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise _ArgError(f"{what} tidak bisa dibuka ({type(exc).__name__})") from None
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not (info.st_mode & 0o170000 == 0o100000):
            raise _ArgError(f"{what} bukan berkas reguler")
        if info.st_size > limit:
            raise _ArgError(f"{what} {info.st_size} byte melebihi batas {limit} (EFN_MIG_MAX_BUNDLE_BYTES)")
        return handle.read(limit + 1)


def _write_exclusive(out: Path, data: bytes) -> None:
    """SEC-202: berkas sementara unik O_CREAT|O_EXCL|O_NOFOLLOW 0600 -> fsync -> rename atomik (symlink tujuan diganti,
    tidak diikuti)."""
    tmp = out.with_name(f".{out.name}.{secrets.token_hex(8)}.part")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, out)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _max_bundle_bytes() -> int:
    raw = os.environ.get("EFN_MIG_MAX_BUNDLE_BYTES") or str(_DEFAULT_MAX_BUNDLE)
    if not raw.isdigit() or int(raw) < 1:
        raise _ArgError("EFN_MIG_MAX_BUNDLE_BYTES wajib bilangan bulat > 0")
    return int(raw)


def _string_list(args, key, pattern, what):
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(v, str) and pattern.fullmatch(v) for v in value):
        raise _ArgError(f"{key} wajib daftar {what}")
    return value


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
        companies = _string_list(args, "companies", _CODE, 'kode PT (mis. ["PB"])')
        steps = _string_list(args, "steps", _STEP, "nama langkah") or []
        key_file = Path(os.environ.get("EFN_MIG_BUNDLE_KEY_FILE") or "/run/secrets/migration_bundle_key")
        if not key_file.is_file():
            raise _ArgError("kunci bundel (EFN_MIG_BUNDLE_KEY_FILE) tidak ada")
        secret = key_file.read_text(encoding="ascii")
        official = _efn_mig_bundle()
        integrity = (vendored.IntegrityError, official.IntegrityError)
        keys = official.derive_keys(secret)

        if steps == ["totals"]:  # mode totals: tanpa bundel, tanpa tulis DB
            months = _string_list(args, "months", _MONTH, "bulan YYYY-MM")
            brand = args.get("brand")
            if brand is not None and not (isinstance(brand, str) and _BRAND.fullmatch(brand)):
                raise _ArgError("brand tidak valid")
            if not companies or not months:
                raise _ArgError("mode totals wajib companies dan months")
            brand = brand or imp.brand_for_companies(companies)
            result = imp.run_totals(env, brand, companies, months)
            env.cr.rollback()
            out = exchange / f"{result.report['run_id']}.totals.result.efnmig"
            _write_exclusive(out, imp.build_totals_result(result, keys))
            summary.update(exit_code=imp.EXIT_OK, mode="totals", run_id=result.report["run_id"], brand=brand,
                           result_file=out.name, months=months, companies=companies)  # fmt: skip
            return summary

        bundle_path = _inside(exchange, args.get("bundle"), "bundle")
        validation_path = _inside(exchange, args.get("validation"), "validation", required=False)
        dry_run = args.get("dry_run", True)
        allow_older = args.get("allow_older", False)
        if not isinstance(dry_run, bool) or not isinstance(allow_older, bool):
            raise _ArgError("dry_run / allow_older wajib boolean")
        blob = _read_limited(bundle_path, _max_bundle_bytes(), "bundel")
        bundle = official.open_bundle(blob, keys, expect_kind="bundle")
        del blob
        validation = None
        if validation_path is not None:
            validation = json.loads(_read_limited(validation_path, _MAX_VALIDATION, "validasi").decode("utf-8"))
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
            validation_key=imp.derive_validation_key(secret),
            allow_older=allow_older,
        )
        del secret
        summary.update(run_id=bundle.manifest["run_id"], brand=brand, dry_run=dry_run)
        result = imp.run_import(env, bundle, opts, commit=None if dry_run else env.cr.commit)
        if dry_run:
            env.cr.rollback()
        else:
            env.cr.commit()
        out = exchange / imp.result_filename(bundle, dry_run)
        _write_exclusive(out, imp.build_result(bundle, result, keys))
        summary.update(
            exit_code=result.exit_code,
            result_file=out.name,
            entities=result.report["entities"],
            slices=len(result.report["slices"]),
            slices_failed=sum(1 for s in result.report["slices"] if "error" in s),
            table_bytes=result.report["table_bytes"],
            skipped_by_param=result.report["skipped_by_param"],
            allow_older_used=bool(result.report.get("allow_older_used")),
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
