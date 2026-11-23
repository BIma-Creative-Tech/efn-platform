"""Mesin migrasi ESB -> Odoo sisi data (G43, docs/04 §27.2, ADR-042 Accepted 18 Nov 2026).

Jalan di container sekali jalan `esb-migrate` (image esb-extract, network `esb` saja, tmpfs /work):
  python esb_to_odoo.py export --brand pb --step master --maps /maps --out /out [--cutover-date D]
  python esb_to_odoo.py export --brand pb --step history --cutover-date D --maps /maps --out /out [--months 2025-01,...]
  python esb_to_odoo.py export --brand pb --step stock --cutover-date D --maps /maps --out /out   (G65, §27.13.4)
  python esb_to_odoo.py writeback --result /out/<run_id>.result.efnmig
  python esb_to_odoo.py reconcile --bundle <f> [--bundle <f> ...] --result <f> [...] --out /out [--with-source]
  python esb_to_odoo.py reconcile --final --parent-run-id X --bundle <semua segmen> --result <hasil totals> --out /out
Impor ke Odoo = `odoo_import_esb.py` (odoo shell, efn-odoo-engineer). Format bundel = README.md (kontrak bersama).
Exit: 0 OK, 1 error data, 2 argumen/konfigurasi, 3 integritas, 4 gerbang ditolak. Tidak mencetak secret/data pribadi.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from efn_mig.bundle import Bundle, IntegrityError, read_bundle, write_secure
from efn_mig.export import (
    SINGLE_STEPS,
    G65Inputs,
    GateError,
    Maps,
    MasterData,
    RunParams,
    StepError,
    expand_steps,
    export_history,
    export_master,
)
from efn_mig.mapping import DISCOUNT_MODES, PURCHASE_PRICE_STATUSES, ExportOptions, MapError
from efn_mig.reconcile import reconcile, reconcile_final
from efn_mig.results import DryRunResult, map_rows
from efn_mig.segments import MAX_LEGACY_DAYS, MAX_SALES_LINE_MONTHS, SALES_DAILY_MONTHS, add_months, month_start
from efn_mig.settings import ClickHouseSettings, ConfigError, MigSettings, bundle_keys

if TYPE_CHECKING:
    from efn_mig.source import ClickHouseSource

log = logging.getLogger("efn_mig")
BRANDS = ("pb", "wtz", "bacha", "imd", "cup")
EXIT_OK, EXIT_DATA, EXIT_CONFIG, EXIT_INTEGRITY, EXIT_GATE = 0, 1, 2, 3, 4


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="esb_to_odoo.py", description="Migrasi ESB -> Odoo (sisi data)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    exp = sub.add_parser("export", help="ClickHouse -> bundel terenkripsi")
    exp.add_argument("--brand", required=True, choices=BRANDS)
    exp.add_argument("--step", action="append", required=True, help="langkah atau grup master/history/cutover")
    exp.add_argument("--cutover-date", type=date.fromisoformat)
    exp.add_argument("--legacy-days", type=int)
    exp.add_argument("--sales-line-months", type=int)
    exp.add_argument("--months", help="YYYY-MM[,YYYY-MM...] (langkah histori)")
    exp.add_argument("--maps", required=True, type=Path)
    # Pengecualian EKSPLISIT (default mati = ketat); dicatat di manifest `export_options` + laporan validator.
    exp.add_argument("--default-uom", help="xmlid UoM untuk produk tanpa satuan (setelah fallback BoM)")
    exp.add_argument("--skip-unlinked-menu-boms", action="store_true", help="BoM tipe Menu tanpa tautan tidak diekspor")
    exp.add_argument("--allow-default-ratio", action="store_true", help="BoM FIN tanpa bom_ratio -> product_qty 1")
    exp.add_argument("--drop-zero-qty-components", action="store_true", help="buang komponen BoM qty <= 0")
    exp.add_argument(
        "--recent-menu-days",
        type=int,
        default=30,
        help="L-166: menu histori yang terjual dalam N hari sebelum cutover = aktif (0 = semua arsip; 0..365)",
    )
    # G65 (docs/04 §27.13.4): nilai efektif dicatat di manifest.
    exp.add_argument(
        "--purchase-statuses",
        default=",".join(PURCHASE_PRICE_STATUSES),
        help="status PO harga beli sah, dipisah koma (Q-ESB28; default Authorized,Receiving,Finished,Closed)",
    )
    exp.add_argument(
        "--purchase-discount-mode",
        choices=DISCOUNT_MODES,
        default="nominal",
        help="arti discount ESB untuk supplierinfo (Q-G65-2)",
    )
    exp.add_argument("--out", type=Path)
    wb = sub.add_parser("writeback", help="hasil impor -> efn_dwh.map_esb_odoo")
    wb.add_argument("--result", required=True, type=Path)
    rc = sub.add_parser("reconcile", help="laporan rekonsiliasi 7 butir")
    rc.add_argument("--bundle", action="append", required=True, type=Path)
    rc.add_argument("--result", action="append", required=True, type=Path)
    rc.add_argument("--out", type=Path)
    rc.add_argument("--with-source", action="store_true", help="butir 6/7 dari ClickHouse (v_esb_*)")
    rc.add_argument("--final", action="store_true", help="rekonsiliasi final lintas segmen vs hasil importer totals")
    rc.add_argument("--parent-run-id", help="wajib dengan --final")
    return parser.parse_args(argv)


def _source() -> ClickHouseSource:
    from efn_mig.source import ClickHouseSource

    return ClickHouseSource.connect(ClickHouseSettings.from_env())


def build_params(args: argparse.Namespace, settings: MigSettings) -> RunParams:
    legacy_days = args.legacy_days if args.legacy_days is not None else settings.legacy_days
    line_months = args.sales_line_months if args.sales_line_months is not None else settings.sales_line_months
    if not 1 <= legacy_days <= MAX_LEGACY_DAYS:
        raise ConfigError(f"--legacy-days di luar 1..{MAX_LEGACY_DAYS}")
    if not 0 <= line_months <= MAX_SALES_LINE_MONTHS:
        raise ConfigError(f"--sales-line-months di luar 0..{MAX_SALES_LINE_MONTHS}")
    if args.cutover_date and args.cutover_date > datetime.now(UTC).date() + timedelta(days=400):
        raise ConfigError("--cutover-date lebih dari 400 hari ke depan")
    return RunParams(
        brand=args.brand,
        cutover=args.cutover_date,
        legacy_days=legacy_days,
        sales_line_months=line_months,
        git_sha=settings.git_sha,
        segment_max_rows=settings.segment_max_rows,
        blocked=settings.saldo_blocked,
        options=ExportOptions(
            default_uom=args.default_uom,
            skip_unlinked_menu_boms=args.skip_unlinked_menu_boms,
            allow_default_ratio=args.allow_default_ratio,
            drop_zero_qty_components=args.drop_zero_qty_components,
        ),
        purchase_statuses=tuple(s.strip() for s in args.purchase_statuses.split(",") if s.strip()),
        purchase_discount_mode=args.purchase_discount_mode,
    )


def cmd_export(args: argparse.Namespace, settings: MigSettings) -> int:
    steps = expand_steps(args.step)
    params = build_params(args, settings)
    if "cutover-param" in steps and not args.cutover_date:
        raise StepError("cutover-param wajib --cutover-date")
    keys = bundle_keys()
    maps = Maps.load(args.maps, args.brand)
    out_dir = args.out or settings.out_dir
    anchor = args.cutover_date or datetime.now(UTC).date()
    src = _source()
    archive_from = add_months(month_start(anchor), -SALES_DAILY_MONTHS)
    if not 0 <= args.recent_menu_days <= 365:
        raise StepError("--recent-menu-days di luar rentang 0..365")
    recent_since = anchor - timedelta(days=args.recent_menu_days) if args.recent_menu_days else None
    data = MasterData.load(src, args.brand, archive_from, anchor, recent_since)
    if any(s in SINGLE_STEPS for s in steps):
        g65 = G65Inputs.load(src, args.brand, steps, args.cutover_date)
        path = export_master(data, params, steps, maps, keys, out_dir, g65=g65)
        print(f"export master: {path.name}")
    months = [x.strip() for x in args.months.split(",") if x.strip()] if args.months else None
    parent, segments = export_history(src, data, params, steps, maps, keys, out_dir, settings.workdir, months)
    for seg in segments:
        print(f"export histori: {seg.path.name} rows={seg.rows} tak_terpetakan={seg.skipped}")
    if segments:
        print(f"parent_run_id={parent} segmen={len(segments)}")
    return EXIT_OK


def cmd_writeback(args: argparse.Namespace) -> int:
    keys = bundle_keys()
    result = read_bundle(args.result, keys, expect_kind="result")
    rows = map_rows(result, datetime.now(UTC))
    _source().insert_map(rows)
    print(f"writeback run_id={result.manifest['run_id']} baris={len(rows)}")
    return EXIT_OK


def cmd_reconcile_final(args: argparse.Namespace, settings: MigSettings) -> int:
    """`reconcile --final --parent-run-id X --bundle <semua segmen> --result <hasil importer mode totals>`."""
    if not args.parent_run_id:
        raise ConfigError("--final wajib --parent-run-id")
    keys = bundle_keys()
    sources = [read_bundle(path, keys, "bundle") for path in args.bundle]
    totals = [read_bundle(path, keys, "result") for path in args.result]
    report = reconcile_final(args.parent_run_id, sources, totals)
    out_dir = args.out or settings.out_dir
    stem = f"reconcile_final_{report.parent_run_id}"
    write_secure(out_dir / f"{stem}.md", report.to_markdown().encode("utf-8"))
    write_secure(out_dir / f"{stem}.csv", report.to_csv().encode("utf-8"))
    print(f"reconcile final {stem}: pemblokir={report.blocking}")
    return EXIT_DATA if report.blocking else EXIT_OK


def cmd_reconcile(args: argparse.Namespace, settings: MigSettings) -> int:
    if args.final:
        return cmd_reconcile_final(args, settings)
    keys = bundle_keys()
    bundles: dict[str, Bundle] = {}
    for path in args.bundle:
        bundle = read_bundle(path, keys, "bundle")
        bundles[str(bundle.manifest["run_id"])] = bundle
    results: dict[str, Bundle] = {}
    for path in args.result:
        result = read_bundle(path, keys, "result")
        results[str(result.manifest["run_id"])] = result
    if set(bundles) != set(results):
        raise ValueError(f"bundel tanpa hasil / hasil tanpa bundel: {len(set(bundles) ^ set(results))} run_id")
    parents = {str(b.manifest.get("parent_run_id") or b.manifest["run_id"]) for b in bundles.values()}
    if len(parents) != 1:
        raise ValueError("reconcile satu ekspor (satu parent_run_id / satu master) per laporan")
    pairs = [(bundles[k], results[k]) for k in sorted(bundles)]
    sales_payments = quality = None
    if args.with_source:
        src = _source()
        first = pairs[0][0].manifest
        brand = str(first["brand"])
        cutover = date.fromisoformat(first["cutover_date"]) if first.get("cutover_date") else datetime.now(UTC).date()
        start = add_months(month_start(cutover), -SALES_DAILY_MONTHS)
        sales_payments = src.monthly_sales_payments(brand, start, cutover)
        quality = src.data_quality(brand)
    report = reconcile(pairs, sales_payments, quality)
    out_dir = args.out or settings.out_dir
    stem = f"reconcile_{report.parent_run_id}"
    write_secure(out_dir / f"{stem}.md", report.to_markdown().encode("utf-8"))
    write_secure(out_dir / f"{stem}.csv", report.to_csv().encode("utf-8"))
    print(f"reconcile {stem}: pemblokir={report.blocking}")
    return EXIT_DATA if report.blocking else EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("clickhouse_connect").setLevel(logging.WARNING)
    args = parse_args(argv)
    try:
        settings = MigSettings.from_env()
        if args.cmd == "export":
            return cmd_export(args, settings)
        if args.cmd == "writeback":
            return cmd_writeback(args)
        return cmd_reconcile(args, settings)
    except IntegrityError as exc:
        print(f"integritas: {exc}", file=sys.stderr)
        return EXIT_INTEGRITY
    except (GateError, DryRunResult) as exc:
        print(f"gerbang ditolak: {exc}", file=sys.stderr)
        return EXIT_GATE
    except (ConfigError, StepError, MapError) as exc:
        print(f"konfigurasi: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except ValueError as exc:
        print(f"data: {exc}", file=sys.stderr)
        return EXIT_DATA


if __name__ == "__main__":
    sys.exit(main())
