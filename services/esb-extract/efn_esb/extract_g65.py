"""Extractor G65 (docs/04 §27.13.1, ADR-042 §0b, L-250/L-254): histori pembelian + saldo stok awal. GET saja; I/O
hanya lewat `get` (diinjeksi di test).

- `purchase_line` (entitas HARIAN, opt-in `ESB_ENTITIES` / `backfill --entity purchase_line`, default mati): satu slice
  = brand x hari PO. Recap `/report/purchase/purchase-recapitulation/purchase` (limit 1000) + header
  `/purchase/purchase-order` (limit 100 [B]) hari yang sama, join `purchaseNum`. Filter cabang recap TIDAK dipakai
  (`branchID[]` diabaikan server, `branchID=` -> 0 baris; probe 24 Nov 2026).
- `stock_opening` (SNAPSHOT per cabang, `snapshot --entity stock_opening --date <cutover>`): `/report/stock-movement`
  start=end=D per `branchCode`, `unitToShow=Base Unit` (satuan dasar produk; G65b), limit 100 (maks; 200 -> HTTP
  400). Simpan hanya `Beginning` dengan qty != 0.
PDP (L-110): whitelist kunci SEBELUM raw/mart (`normalize.PURCHASE_*_FIELDS`, `STOCK_MOVEMENT_FIELDS`); `raw_rows()`
menjalankan `redact()` lagi sebagai lapisan kedua.
"""

from datetime import datetime
from typing import Any

from efn_esb.extract import BrandRuntime, CountingGetter, Extractor, RawDoc, SliceResult
from efn_esb.normalize import PURCHASE_HEADER_FIELDS, PURCHASE_RECAP_FIELDS, STOCK_MOVEMENT_FIELDS, text, whitelist
from efn_esb.paging import Getter, fetch_all
from efn_esb.planning import SliceTask
from efn_esb.transform_g65 import (
    check_branch,
    check_opening_present,
    check_purchase_day,
    is_opening_row,
    opening_rows,
    purchase_lookups,
    transform_purchase_lines,
    transform_stock_opening,
)

PURCHASE_RECAP_PATH = "/report/purchase/purchase-recapitulation/purchase"
PURCHASE_ORDER_PATH = "/purchase/purchase-order"
STOCK_MOVEMENT_PATH = "/report/stock-movement"
PURCHASE_RECAP_LIMIT = 1000  # probe 24 Nov 2026: 1000 diterima
PURCHASE_ORDER_LIMIT = 100  # batas maks header PO belum diuji [B]
STOCK_MOVEMENT_LIMIT = 100  # maks (200/500 -> HTTP 400, probe 24 Nov 2026)
# G65b (gladi 24 Nov 2026): default endpoint = satuan campur per baris (BOX/PACK/EA...). `unitToShow=Base Unit`
# (persis, dengan spasi; "Stock Unit"/"baseUnit" -> HTTP 400) = semua baris dalam satuan dasar produk ESB, total
# amountBalance identik. Wajib: export memakai satuan baris sebagai satuan produk bila dim_esb_product.uom kosong.
STOCK_MOVEMENT_UNIT = "Base Unit"
# Entitas `snapshot` G65 (`purchase_line` = entitas harian, terdaftar di config.DAILY_ENTITIES).
G65_SNAPSHOT_ENTITIES = ("stock_opening",)


def branch_code_for(rt: BrandRuntime, branch_id: int) -> str:
    """Kode cabang dari dim_esb_branch slice `branch` (kolom 2 = branch_id, 3 = branch_code). Tanpa kode -> error."""
    for row in rt.branch_rows:
        if int(row[2]) == branch_id:
            code = text(row[3], 64)
            if code:
                return code
            break
    raise ValueError(f"cabang {branch_id} tanpa branch_code di dim_esb_branch")


def branch_is_active(rt: BrandRuntime, branch_id: int) -> bool:
    """Flag aktif dim_esb_branch (kolom 7). Cabang tak dikenal dianggap aktif (pemeriksaan paling ketat)."""
    for row in rt.branch_rows:
        if int(row[2]) == branch_id:
            return int(row[7]) == 1
    return True


def extract_purchase_line(rt: BrandRuntime, task: SliceTask, version: datetime, get: Getter) -> SliceResult:
    cfg, day = rt.cfg, task.day.isoformat()
    window = {"dateFrom": day, "dateTo": day}
    recap = [
        whitelist(row, PURCHASE_RECAP_FIELDS)
        for row in fetch_all(get, PURCHASE_RECAP_PATH, window, limit=PURCHASE_RECAP_LIMIT, max_pages=rt.max_pages)
    ]
    headers = [
        whitelist(row, PURCHASE_HEADER_FIELDS)
        for row in fetch_all(get, PURCHASE_ORDER_PATH, window, limit=PURCHASE_ORDER_LIMIT, max_pages=rt.max_pages)
    ]
    check_purchase_day(recap, task.day, "purchase-recapitulation")  # SEC-G65-01
    check_purchase_day(headers, task.day, "purchase-order")
    branches = [(int(row[2]), str(row[4])) for row in rt.branch_rows]
    with rt.lock:
        suppliers = list(rt.suppliers)
    lookups = purchase_lookups(branches, suppliers)
    facts = transform_purchase_lines(recap, headers, cfg.key, cfg.company_for, lookups, task.day, version)
    result = SliceResult(task, version, tables={"fact_esb_purchase_line": facts})
    result.raw = [
        RawDoc("report/purchase-recapitulation", day, task.day, recap),
        RawDoc("purchase/purchase-order", day, task.day, headers),
    ]
    return result


def extract_stock_opening(rt: BrandRuntime, task: SliceTask, version: datetime, get: Getter) -> SliceResult:
    cfg, day, branch_id = rt.cfg, task.day.isoformat(), task.branch_id
    code = branch_code_for(rt, branch_id)
    params = {"startPeriod": day, "endPeriod": day, "branchCode": code, "unitToShow": STOCK_MOVEMENT_UNIT}
    pages = CountingGetter(get)
    rows = [
        whitelist(row, STOCK_MOVEMENT_FIELDS)
        for row in fetch_all(pages, STOCK_MOVEMENT_PATH, params, limit=STOCK_MOVEMENT_LIMIT, max_pages=rt.max_pages)
    ]
    check_branch(rows, branch_id, code)
    check_opening_present(rows, branch_id, branch_is_active(rt, branch_id))  # SEC-G65-02
    kept = opening_rows(rows)
    facts = transform_stock_opening(kept, cfg.key, cfg.company_for(branch_id), branch_id, code, task.day, version)
    summary: dict[str, Any] = {
        "pages": pages.calls,
        "rows_total": len(rows),
        "rows_beginning": sum(1 for row in rows if is_opening_row(row)),
        "rows_kept": len(kept),
    }
    result = SliceResult(task, version, tables={"fact_esb_stock_opening": facts})
    result.raw.append(
        RawDoc("report/stock-movement", f"{day}|{branch_id}", task.day, {"rows": kept, "summary": summary})
    )
    return result


G65_EXTRACTORS: dict[str, Extractor] = {
    "purchase_line": extract_purchase_line,
    "stock_opening": extract_stock_opening,
}
