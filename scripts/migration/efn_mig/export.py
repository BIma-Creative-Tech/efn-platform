"""Orkestrasi `export` (docs/04 §27.2/§27.3/§27.3a). Sumber diinjeksi (`Source`) -> bisa diuji tanpa ClickHouse.

- Master (`--step branch..cost` / `master`) dan cutover tunggal (`stock`, `cutover-param`): satu bundel
  `<run_id>.efnmig`, JSONL tanpa kompresi.
- Histori (`--step legacy_* / history`): satu bundel per company x bulan (dipecah per cabang bila > segment_max_rows),
  `<parent_run_id>_<CODE>_<YYYYMM>[_pN].efnmig`, JSONL gzip. Baris ditulis streaming ke `workdir` (tmpfs).
- G65 (docs/04 §27.13.4, ADR-042 §0b): `supplierinfo`, `cost` berurutan sumber, `stock` (quant.jsonl),
  `legacy_purchase_line` (selalu part 1). `opening` tetap NOT_BUILT (Q-ESB21).
"""

import gzip
import logging
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from efn_mig import mapping as m
from efn_mig.bundle import (
    Keys,
    canonical_json,
    file_entry,
    jsonl_bytes,
    open_secure_write,
    sha256_hex,
    write_bundle,
)
from efn_mig.segments import history_months, history_ranges, month_label, plan_parts, segment_filename
from efn_mig.source import Source

log = logging.getLogger("efn_mig.export")

MASTER_STEPS = ("branch", "uom", "category", "product", "menu", "partner", "bom", "supplierinfo", "cost")
HISTORY_STEPS = ("legacy_flow", "legacy_sales_daily", "legacy_sales_line", "legacy_purchase_line")
CUTOVER_STEPS = ("stock", "opening", "cutover-param")
# Langkah yang masuk SATU bundel (`<run_id>.efnmig`): master + cutover tunggal (§27.3a).
SINGLE_STEPS = (*MASTER_STEPS, "stock", "cutover-param")
HISTORY_FILE = {
    "legacy_flow": "legacy_flow_daily",
    "legacy_sales_daily": "legacy_sales_daily",
    "legacy_sales_line": "legacy_sales_line",
    "legacy_purchase_line": "legacy_purchase_line",
}
STEP_ENTITIES = {
    "branch": ("branch",),
    "uom": ("uom",),
    "category": ("category", "pos_category"),
    "product": ("product",),
    "menu": ("menu",),
    "partner": ("supplier", "customer", "payment_term"),
    "bom": ("bom",),
    "supplierinfo": ("supplierinfo",),  # G65: dari fact_esb_purchase_line (§27.13.4)
    "cost": ("cost",),
    "stock": ("quant",),  # G65: dari fact_esb_stock_opening
}
MAP_FILES = ("branch_map.csv", "uom_map.csv", "coa_map.csv", "payment_map.csv", "location_map.csv")
NOT_BUILT = frozenset({"opening"})  # neraca pembukaan / AR-AP: Q-ESB21 (G65 §27.13.4)
# Berkas histori yang menentukan pembagian part per gudang; pembelian selalu di part 1 (§27.13.4).
PURCHASE_FILE = "legacy_purchase_line"
WIB = timezone(timedelta(hours=7))


class StockGateError(ValueError):
    """Cabang non-skip tanpa checkpoint `stock_opening` pada cutover (exit 1; bundel tetap ditulis untuk diperiksa,
    validator menolak)."""


class GateError(RuntimeError):
    """Gerbang ditolak (exit 4)."""


class StepError(ValueError):
    """Argumen langkah tidak valid / belum dibangun (exit 2)."""


def expand_steps(raw: Sequence[str]) -> list[str]:
    out: list[str] = []
    for step in raw:
        if step == "master":
            out += MASTER_STEPS
        elif step == "history":
            out += HISTORY_STEPS
        elif step == "cutover":
            out += [*HISTORY_STEPS, "stock", "cutover-param"]  # opening: NOT_BUILT (Q-ESB21)
        elif step in MASTER_STEPS or step in HISTORY_STEPS or step in CUTOVER_STEPS:
            out.append(step)
        else:
            raise StepError(f"langkah tidak dikenal: {step}")
    steps = list(dict.fromkeys(out))
    pending = [s for s in steps if s in NOT_BUILT]
    if pending:
        raise StepError(f"langkah {', '.join(pending)} belum dibangun (neraca pembukaan/AR-AP ditunda, Q-ESB21)")
    return steps


@dataclass(frozen=True)
class RunParams:
    brand: str
    cutover: date | None
    legacy_days: int
    sales_line_months: int
    git_sha: str = "unknown"
    segment_max_rows: int = 500_000
    blocked: frozenset[str] = frozenset({"IMD", "CUP"})
    options: m.ExportOptions = field(default_factory=m.ExportOptions)  # default ketat (fail closed)
    purchase_statuses: tuple[str, ...] = m.PURCHASE_PRICE_STATUSES  # --purchase-statuses (Q-ESB28)
    purchase_discount_mode: str = "nominal"  # --purchase-discount-mode (Q-G65-2)

    def __post_init__(self) -> None:
        if self.purchase_discount_mode not in m.DISCOUNT_MODES:
            raise StepError(f"--purchase-discount-mode wajib salah satu {', '.join(m.DISCOUNT_MODES)}")
        if not self.purchase_statuses or not all(s.strip() for s in self.purchase_statuses):
            raise StepError("--purchase-statuses tidak boleh kosong")


@dataclass
class Maps:
    branch: dict[int, m.BranchMap]
    uom: dict[str, m.UomMap]
    raw: dict[str, bytes] = field(default_factory=dict)  # nama berkas -> byte (ikut bundel, hash di manifest)
    location: dict[tuple[int, str], str] = field(default_factory=dict)  # G65 location_map.csv (opsional)

    @classmethod
    def from_bytes(cls, raw: dict[str, bytes], brand: str) -> "Maps":
        for required in ("branch_map.csv", "uom_map.csv"):
            if required not in raw:
                raise m.MapError(f"{required} wajib ada di --maps")
        location = raw.get("location_map.csv")
        return cls(
            branch=m.parse_branch_map(raw["branch_map.csv"].decode("utf-8-sig"), brand),
            uom=m.parse_uom_map(raw["uom_map.csv"].decode("utf-8-sig")),
            raw=raw,
            location=m.parse_location_map(location.decode("utf-8-sig"), brand) if location is not None else {},
        )

    @classmethod
    def load(cls, directory: Path, brand: str) -> "Maps":
        raw = {name: (directory / name).read_bytes() for name in MAP_FILES if (directory / name).is_file()}
        return cls.from_bytes(raw, brand)

    def entries(self) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
        entries: list[dict[str, Any]] = []
        members: dict[str, bytes] = {}
        for name, data in sorted(self.raw.items()):
            lines = [ln for ln in data.decode("utf-8-sig").splitlines() if ln.strip()]
            entries.append({"name": f"maps/{name}", "sha256": sha256_hex(data), "rows": max(0, len(lines) - 1)})
            members[f"maps/{name}"] = data
        return entries, members


@dataclass
class MasterData:
    """Dimensi brand yang dibutuhkan master DAN histori (kunci menu/produk/cabang)."""

    companies: list[str]
    branches: list[dict[str, Any]]
    products: list[dict[str, Any]]
    menus: list[dict[str, Any]]
    prices: list[dict[str, Any]]
    archive: list[dict[str, Any]]
    suppliers: list[dict[str, Any]]
    customers: list[dict[str, Any]]
    bom_headers: list[dict[str, Any]]
    bom_lines: list[dict[str, Any]]
    ratios: dict[str, float]
    snapshot: dict[str, str]
    # L-166: menu histori yang terjual pada/ setelah tanggal ini = aktif (None = semua menu histori arsip).
    recent_since: date | None = None

    @classmethod
    def load(
        cls, src: Source, brand: str, archive_from: date, archive_to: date, recent_since: date | None = None
    ) -> "MasterData":
        return cls(
            companies=src.companies(brand),
            branches=src.dim("dim_esb_branch", brand),
            products=src.dim("dim_esb_product", brand),
            menus=src.dim("dim_esb_menu", brand),
            prices=src.dim("dim_esb_menu_price", brand),
            archive=src.archive_menus(brand, archive_from, archive_to),
            suppliers=src.dim("dim_esb_supplier", brand),
            customers=src.dim("dim_esb_customer", brand),
            bom_headers=src.dim("dim_esb_bom", brand),
            bom_lines=src.dim("dim_esb_bom_line", brand),
            ratios=src.bom_ratios(brand),
            snapshot=src.snapshot(brand),
            recent_since=recent_since,
        )

    @property
    def product_codes(self) -> set[str]:
        return {str(p["product_code"]) for p in self.products}

    @property
    def supplier_codes(self) -> frozenset[str]:
        return frozenset(str(s["supplier_code"]) for s in self.suppliers if s.get("supplier_code"))


def today_wib(now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(WIB).date()


@dataclass
class G65Inputs:
    """Masukan G65 untuk bundel tunggal (§27.13.4): baris PO jendela supplierinfo + saldo awal pada cutover."""

    ref: date  # acuan jendela supplierinfo [ref − 12 bulan, ref): cutover, selain itu hari ini WIB
    as_of: date  # cost.as_of = cutover atau tanggal export
    purchases: list[dict[str, Any]] = field(default_factory=list)
    opening: list[dict[str, Any]] = field(default_factory=list)
    purchase_meta: dict[str, Any] | None = None
    opening_meta: dict[str, Any] | None = None

    @classmethod
    def empty(cls, cutover: date | None, now: datetime | None = None) -> "G65Inputs":
        anchor = cutover or today_wib(now)
        return cls(ref=anchor, as_of=anchor)

    @classmethod
    def load(
        cls, src: Source, brand: str, steps: Sequence[str], cutover: date | None, now: datetime | None = None
    ) -> "G65Inputs":
        out = cls.empty(cutover, now)
        if "stock" in steps and cutover is None:
            raise StepError("langkah stock wajib --cutover-date (§27.13.4)")
        if {"supplierinfo", "cost", "stock"} & set(steps):
            out.purchases = src.purchase_lines(brand, m.year_before(out.ref), out.ref)
            out.purchase_meta = src.purchase_snapshot(brand)
        if cutover is not None and {"cost", "stock"} & set(steps):
            out.opening = src.stock_opening(brand, cutover)
            out.opening_meta = src.stock_opening_meta(brand, cutover)
        return out


@dataclass
class G65Result:
    """Catatan export G65 untuk manifest."""

    purchase: m.PurchaseSkips = field(default_factory=m.PurchaseSkips)
    stock: m.StockSkips = field(default_factory=m.StockSkips)
    cost_mixed_uom: set[str] = field(default_factory=set)
    # (company, produk, sumber, satuan): sumber HPP dengan satuan tanpa faktor ke UoM produk -> sumber berikutnya
    cost_unconvertible: set[tuple[str, str, str, str]] = field(default_factory=set)
    stock_without_cost: list[str] = field(default_factory=list)  # "<company>|<product_code>"


def master_records(
    data: MasterData,
    brand: str,
    maps: Maps,
    steps: Sequence[str],
    options: m.ExportOptions | None = None,
    skips: m.BomSkips | None = None,
    g65: G65Inputs | None = None,
    params: RunParams | None = None,
    g65_result: G65Result | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], m.BranchIndex]:
    """Baris JSONL per entitas untuk langkah master yang diminta (semua company brand; produk/menu kembar per PT).
    `options` = pengecualian eksplisit (default ketat); yang dibuang dicatat di `skips`. G65 (`supplierinfo`, `cost`,
    `stock`) memakai `g65` (kosong bila None) dan mencatat yang dilewati di `g65_result`."""
    opts = options or m.ExportOptions()
    run = params or RunParams(brand=brand, cutover=None, legacy_days=1, sales_line_months=0)
    inputs = g65 or G65Inputs.empty(run.cutover)
    result = g65_result if g65_result is not None else G65Result()
    branch_records, index = m.build_branches(brand, data.branches, maps.branch)
    uom_by_code = m.product_uoms(data.products, data.bom_headers, data.bom_lines)
    codes = data.product_codes
    out: dict[str, list[dict[str, Any]]] = {e: [] for s in steps for e in STEP_ENTITIES.get(s, ())}
    latest = m.latest_purchases(
        inputs.purchases, run.purchase_statuses, m.year_before(inputs.ref), inputs.ref, result.purchase
    )
    if "quant" in out:
        if run.cutover is None:
            raise StepError("langkah stock wajib --cutover-date (§27.13.4)")
        out["quant"] = m.build_quants(
            brand, inputs.opening, index, maps.branch, data.products, uom_by_code, maps.uom, maps.location,
            run.cutover, result.stock,
        )  # fmt: skip
        result.stock_without_cost = _stock_without_cost(brand, data, inputs, latest, out["quant"], maps)
    if "branch" in out:
        out["branch"] = branch_records
    for company in data.companies:
        menus, menu_index = m.build_menus(brand, company, data.menus, data.prices, data.archive, data.recent_since)
        if "uom" in out:
            uoms = list(uom_by_code.values()) + [str(ln.get("uom") or "") for ln in data.bom_lines]
            out["uom"] += m.build_uoms(brand, company, uoms, maps.uom)
        if "category" in out:
            out["category"] += m.build_categories(brand, company, data.products)
            out["pos_category"] += m.build_pos_categories(brand, company, [*data.menus, *data.archive])
        if "product" in out:
            out["product"] += m.build_products(
                brand, company, data.products, uom_by_code, maps.uom, default_uom=opts.default_uom
            )
        if "menu" in out:
            out["menu"] += menus
        if "supplier" in out:
            partners = m.build_partners(brand, company, data.suppliers, data.customers)
            for entity in ("supplier", "customer", "payment_term"):
                out[entity] += partners[entity]
        if "bom" in out:
            out["bom"] += m.build_boms(
                brand, company, data.bom_headers, data.bom_lines, codes, menu_index, data.ratios, maps.uom,
                options=opts, skips=skips,
            )  # fmt: skip
        if "supplierinfo" in out:
            out["supplierinfo"] += m.build_supplierinfos(
                brand, company, latest, set(data.supplier_codes), codes, maps.uom, run.purchase_discount_mode,
                result.purchase, uom_by_code,
            )  # fmt: skip
        if "cost" in out:
            out["cost"] += company_costs(
                brand, company, data, inputs, latest, maps, result.cost_mixed_uom, result.cost_unconvertible
            )
    return out, index


def company_costs(
    brand: str,
    company: str,
    data: MasterData,
    inputs: G65Inputs,
    latest: dict[m.PurchaseKey, tuple[m.PurchaseRank, Any]],
    maps: Maps,
    mixed_uom: set[str] | None = None,
    unconvertible: set[tuple[str, str, str, str]] | None = None,
) -> list[dict[str, Any]]:
    """`cost.jsonl` satu company: stock_opening -> bom_last_hpp -> purchase_last (L-252), semua dalam satuan produk.
    HPP `stock_opening` hanya dari baris yang juga dimuat langkah `stock` (cabang non-skip, lokasi `stock`; QA P3)."""
    _, index = m.build_branches(brand, data.branches, maps.branch)
    opening = [
        r for r in inputs.opening
        if str(r.get("company_code")) == company and m.opening_row_loaded(brand, r, index, maps.branch, maps.location)
    ]  # fmt: skip
    product_uoms = m.product_uoms(data.products, data.bom_headers, data.bom_lines)
    return m.build_costs(
        brand, company, data.bom_lines, data.product_codes, opening, m.purchase_last_prices(latest, company),
        inputs.as_of, mixed_uom, product_uoms, maps.uom, unconvertible,
    )  # fmt: skip


def _stock_without_cost(
    brand: str,
    data: MasterData,
    inputs: G65Inputs,
    latest: dict[m.PurchaseKey, tuple[m.PurchaseRank, Any]],
    quants: list[dict[str, Any]],
    maps: Maps,
) -> list[str]:
    """Produk ber-quant tanpa sumber HPP mana pun (validator: error; §27.13.4 butir 4)."""
    missing: set[str] = set()
    for company in sorted({str(q["company_code"]) for q in quants}):
        costs = company_costs(brand, company, data, inputs, latest, maps)
        priced = {str(c["data"]["product_code"]) for c in costs}
        missing |= {
            f"{company}|{q['data']['product_code']}"
            for q in quants
            if q["company_code"] == company and q["data"]["product_code"] not in priced
        }
    return sorted(missing)


def base_manifest(
    run_id: str,
    params: RunParams,
    companies: Sequence[str],
    steps: Sequence[str],
    snapshot: dict[str, str],
    now: datetime,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "brand": params.brand,
        "company_codes": sorted(companies),
        "steps": list(steps),
        "cutover_date": params.cutover.isoformat() if params.cutover else None,
        "legacy_days": params.legacy_days,
        "sales_line_months": params.sales_line_months,
        "parent_run_id": None,
        "segment": None,
        "created_at": now.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "source": {"snapshot": dict(snapshot)},
        "tool": {"git_sha": params.git_sha, "schema": 1},
        "export_options": params.options.to_manifest(),
    }


def export_master(
    data: MasterData,
    params: RunParams,
    steps: Sequence[str],
    maps: Maps,
    keys: Keys,
    out_dir: Path,
    now: datetime | None = None,
    run_id: str | None = None,
    g65: G65Inputs | None = None,
) -> Path:
    """Bundel tunggal (master dan/atau `stock` + `cutover-param`). `stock`: company di `params.blocked` -> exit 4;
    cabang non-skip tanpa checkpoint `stock_opening` pada cutover -> bundel tetap ditulis (daftar di manifest) lalu
    `StockGateError` (exit 1)."""
    run_id = run_id or str(uuid.uuid4())
    stamp = now or datetime.now(UTC)
    master_steps = [s for s in steps if s in SINGLE_STEPS]
    if "stock" in master_steps:
        if params.cutover is None:
            raise StepError("langkah stock wajib --cutover-date (§27.13.4)")
        refused = sorted(set(data.companies) & params.blocked)
        if refused:
            raise GateError(f"stok awal ditolak untuk company {', '.join(refused)} (EFN_MIG_SALDO_BLOCKED, Q-ESB5)")
    inputs = g65 or G65Inputs.empty(params.cutover, stamp)
    skips = m.BomSkips()
    result = G65Result()
    records, index = master_records(
        data, params.brand, maps, master_steps, params.options, skips, inputs, params, result
    )
    manifest = base_manifest(run_id, params, data.companies, master_steps, data.snapshot, stamp)
    files: list[dict[str, Any]] = []
    members: dict[str, bytes] = {}
    for entity, rows in records.items():
        name = m.ENTITY_FILES[entity]
        blob, count = jsonl_bytes(rows)
        files.append(file_entry(name, entity, blob, count))
        members[name] = blob
    map_entries, map_members = maps.entries()
    members.update(map_members)
    skipped = skips.to_manifest()
    missing: list[int] = []
    if {"supplierinfo", "cost", "stock"} & set(master_steps):
        skipped.update(result.purchase.to_manifest())
        manifest["purchase_statuses"] = list(params.purchase_statuses)
        manifest["purchase_discount_mode"] = params.purchase_discount_mode
        manifest["source"]["snapshot"]["fact_esb_purchase_line"] = inputs.purchase_meta
    if "cost" in master_steps:
        manifest["cost_as_of"] = inputs.as_of.isoformat()
        manifest["cost_mixed_uom"] = sorted(result.cost_mixed_uom)
        items = [
            {"company_code": c, "product_code": p, "source": s, "uom": u}
            for c, p, s, u in sorted(result.cost_unconvertible)
        ]
        skipped["cost_uom_unconvertible"] = {"count": len(items), "items": items[:1000]}
    if "stock" in master_steps:
        skipped.update(result.stock.to_manifest())
        meta = inputs.opening_meta or {"opening_date": None, "max_version": "", "branches_checkpointed": []}
        manifest["source"]["snapshot"]["fact_esb_stock_opening"] = meta
        missing = missing_opening_branches(maps.branch, data.branches, meta.get("branches_checkpointed") or [])
        manifest["stock_opening_missing_branches"] = missing
        manifest["stock_without_cost"] = result.stock_without_cost
        manifest["stock_by_branch"] = result.stock.branch_stats()
    manifest.update(files=files, maps=map_entries, unmapped_branches=index.unmapped, skipped=skipped)
    path = out_dir / f"{run_id}.efnmig"
    write_bundle(path, manifest, members, keys, "bundle")
    if missing:
        raise StockGateError(
            f"{len(missing)} cabang tanpa checkpoint stock_opening pada {params.cutover} (bundel {path.name} ditolak "
            "validator; jalankan snapshot --entity stock_opening untuk cabang itu)"
        )
    return path


def missing_opening_branches(
    bmap: dict[int, m.BranchMap], branches: Iterable[dict[str, Any]], checkpointed: Iterable[int]
) -> list[int]:
    """Gerbang `stock` (§27.13.4): cabang brand ini yang di branch_map bukan `skip` tetapi tanpa checkpoint."""
    known = {int(b["branch_id"]) for b in branches}
    done = {int(b) for b in checkpointed}
    return sorted(b for b, entry in bmap.items() if entry.type != "skip" and b in known and b not in done)


@dataclass
class SegmentResult:
    path: Path
    company: str
    month: str
    part: int
    parts: int
    rows: dict[str, int]
    skipped: dict[str, int]


def export_history(
    src: Source,
    data: MasterData,
    params: RunParams,
    steps: Sequence[str],
    maps: Maps,
    keys: Keys,
    out_dir: Path,
    workdir: Path,
    months: Iterable[str] | None = None,
    now: datetime | None = None,
    parent_run_id: str | None = None,
    run_id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
) -> tuple[str, list[SegmentResult]]:
    """Satu bundel per company x bulan (x part). Company di `params.blocked` (EFN_MIG_SALDO_BLOCKED) -> exit 4."""
    wanted = [HISTORY_FILE[s] for s in steps if s in HISTORY_FILE]
    if not wanted:
        return parent_run_id or "", []
    if params.cutover is None:
        raise StepError("langkah histori wajib --cutover-date (§27.5 gerbang 4)")
    refused = sorted(set(data.companies) & params.blocked)
    if refused:
        raise GateError(f"histori ditolak untuk company {', '.join(refused)} (EFN_MIG_SALDO_BLOCKED, Q-ESB5)")
    ranges = history_ranges(params.cutover, params.legacy_days, params.sales_line_months)
    month_list = history_months(ranges, months)
    parent = parent_run_id or str(uuid.uuid4())
    _, index = m.build_branches(params.brand, data.branches, maps.branch)
    uom_by_code = m.product_uoms(data.products, data.bom_headers, data.bom_lines)
    ea = {code: maps.uom[u].ea_factor for code, u in uom_by_code.items() if u in maps.uom}
    map_entries, map_members = maps.entries()
    history_steps = [s for s in steps if s in HISTORY_FILE]
    results: list[SegmentResult] = []
    stamp = now or datetime.now(UTC)
    for company in data.companies:
        _, menu_index = m.build_menus(params.brand, company, data.menus, data.prices, data.archive, data.recent_since)
        ctx = m.HistoryContext(params.brand, company, index, data.product_codes, ea, menu_index, data.supplier_codes)
        company_whs = index.warehouses_of(company)
        if not company_whs:
            log.warning("company %s tanpa gudang tujuan di branch_map: histori dilewati", company)
            continue
        branches_of_wh = index.branches_by_warehouse(index.wh_key)
        for month in month_list:
            all_windows = {f: w for f, w in ranges.for_month(month).items() if f in wanted and w is not None}
            # Pembelian: irisan company × bulan (gudang nullable) -> tidak ikut pembagian part, seluruhnya di part 1.
            purchase_window = all_windows.pop(PURCHASE_FILE, None)
            windows = all_windows
            per_branch: dict[int, int] = {}
            for file, (start, end) in windows.items():
                for branch_id, count in src.history_counts(params.brand, company, file, start, end).items():
                    per_branch[branch_id] = per_branch.get(branch_id, 0) + count
            # SEC-197: part per GUDANG TUJUAN (cabang lokasi ikut gudang induk); gudang tanpa baris tetap terdaftar.
            per_wh = dict.fromkeys(company_whs, 0)
            stray: list[int] = []  # cabang tanpa gudang tujuan PT ini -> baris dihitung tak terpetakan (part 1)
            for branch_id, count in per_branch.items():
                wh = index.wh_key.get(branch_id)
                if wh in per_wh:
                    per_wh[wh] += count
                else:
                    stray.append(branch_id)
            parts = plan_warehouse_parts(per_wh, params.segment_max_rows)
            for part_no, targets in enumerate(parts, start=1):
                part_branches = {b for wh in targets for b in branches_of_wh.get(wh, [])}
                if part_no == 1:
                    part_branches |= set(stray)
                branch_ids = sorted(part_branches)
                run_id = run_id_factory()
                manifest = base_manifest(run_id, params, [company], history_steps, data.snapshot, stamp)
                files: list[dict[str, Any]] = []
                members = dict(map_members)
                rows: dict[str, int] = {}
                skipped: dict[str, int] = {}
                for file, (start, end) in windows.items():
                    name = m.ENTITY_FILES[file]
                    builder = m.HISTORY_BUILDERS[file]
                    source_rows = src.history_rows(params.brand, company, file, start, end, branch_ids)
                    tmp = workdir / f"{run_id}_{name}"
                    built = m.aggregate_rows(file, (builder(ctx, r) for r in source_rows))
                    exported, dropped = write_gz(tmp, built)
                    blob = tmp.read_bytes()
                    tmp.unlink()
                    entry = file_entry(name, file, blob, exported)
                    entry["range"] = [start.isoformat(), end.isoformat()]
                    files.append(entry)
                    members[name] = blob
                    rows[file], skipped[file] = exported, dropped
                stats: dict[str, dict[str, int]] = {
                    f: {"rows_exported": rows[f], "rows_skipped_unmapped": skipped[f]} for f in rows
                }
                if purchase_window is not None and part_no == 1:
                    start, end = purchase_window
                    name = m.ENTITY_FILES[PURCHASE_FILE]
                    tmp = workdir / f"{run_id}_{name}"
                    source_rows = src.purchase_history_rows(params.brand, company, start, end)
                    exported, dropped = write_gz(tmp, (m.purchase_line_row(ctx, r) for r in source_rows))
                    blob = tmp.read_bytes()
                    tmp.unlink()
                    entry = file_entry(name, PURCHASE_FILE, blob, exported)
                    entry["range"] = [start.isoformat(), end.isoformat()]
                    files.append(entry)
                    members[name] = blob
                    rows[PURCHASE_FILE], skipped[PURCHASE_FILE] = exported, dropped
                    # Satu-satunya baris yang tidak diekspor = mata uang ≠ IDR (§27.13.4).
                    stats[PURCHASE_FILE] = {"rows_exported": exported, "rows_skipped_unmapped": 0,
                                            "rows_skipped_currency": dropped}  # fmt: skip
                    manifest["skipped"] = {"currency": {"count": dropped}}
                segment = {"company_code": company, "month": month_label(month), "part": part_no, "parts": len(parts)}
                manifest.update(
                    parent_run_id=parent,
                    segment=segment,
                    branch_ids=branch_ids,
                    target_warehouses=targets,
                    company_warehouses=company_whs,
                    files=files,
                    maps=map_entries,
                    stats=stats,
                )
                path = out_dir / segment_filename(parent, company, month, part_no, len(parts))
                write_bundle(path, manifest, members, keys, "bundle")
                results.append(SegmentResult(path, company, month_label(month), part_no, len(parts), rows, skipped))
                log.info("segmen %s rows=%s skipped=%s", path.name, rows, skipped)
    return parent, results


def write_gz(path: Path, rows: Iterable[dict[str, Any] | None]) -> tuple[int, int]:
    """Tulis baris (None = tak terpetakan, dihitung) ke gzip deterministik secara streaming."""
    exported = dropped = 0
    with (
        open_secure_write(path) as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=6) as gz,
    ):
        for row in rows:
            if row is None:
                dropped += 1
                continue
            gz.write(canonical_json(row))
            gz.write(b"\n")
            exported += 1
    return exported, dropped


def plan_warehouse_parts(rows_by_wh: dict[str, int], max_rows: int) -> list[list[str]]:
    """SEC-197: gudang tujuan -> part. Satu gudang selalu di tepat satu part (tidak terbelah). Gudang berbaris dibagi
    greedy (urut xmlid) dengan total <= max_rows (kecuali satu gudang sendiri > batas); gudang tanpa baris masuk part 1
    (agar importer membersihkan irisan lama). Tanpa gudang -> []."""
    if not rows_by_wh:
        return []
    parts = plan_parts({wh: n for wh, n in rows_by_wh.items() if n > 0}, max_rows) or [[]]
    parts[0] = sorted(parts[0] + [wh for wh, n in rows_by_wh.items() if n <= 0])
    return parts
