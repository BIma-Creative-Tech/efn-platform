"""Extractor entitas migrasi G43 (docs/04 §27.11, ADR-042). GET saja; I/O hanya lewat `get` (diinjeksi di test).

Bukan bagian loop `run`: dijalankan sekali per kebutuhan lewat `python -m efn_esb snapshot --brand B --entity E`
(lihat `runner.run_snapshot`). Satu slice = (entitas, '*', hari) -> checkpoint seperti entitas lain.

- P1 (`P1_ENTITIES`, sebelum dry-run master): `bom_detail` (E1), `menu_price` (E2), `customer` (E4), `location` (E5).
  Bentuk respons dari spec publik (openapi.json); BELUM diuji GET nyata (lead yang menguji).
- P2 (`P2_ENTITIES`, sebelum cutover): `stock_opname` (E6), `ap_open` (E7), `ar_open` (E8), `coa` (E9). Default MATI:
  CLI menolak tanpa `--allow-unverified` (parameter & arti kolom belum diverifikasi). E10 (TB/GL) tidak dibangun:
  endpoint di spec = POST (dilarang, GET-only) -> ekspor Finance (Q-ESB21).
Redaksi (L-110): customer/AR/AP/COA = whitelist SEBELUM disimpan; dokumen lain = `redact()`.
"""

from collections.abc import Mapping
from datetime import timedelta
from typing import Any
from urllib.parse import quote

from efn_esb.extract import MASTER_LIMIT, BrandRuntime, Extractor, RawDoc, SliceResult, _list
from efn_esb.normalize import AP_FIELDS, AR_FIELDS, COA_FIELDS, flag, redact, redact_customer, text, to_int, whitelist
from efn_esb.paging import Getter, fetch_all
from efn_esb.planning import SliceTask
from efn_esb.transform_mig import (
    transform_ap_open,
    transform_ar_open,
    transform_bom,
    transform_coa,
    transform_customers,
    transform_locations,
    transform_menu_prices,
    transform_stock_opname,
)

P1_ENTITIES = ("bom_detail", "menu_price", "customer", "location")
P2_ENTITIES = ("stock_opname", "ap_open", "ar_open", "coa")
SNAPSHOT_ENTITIES = P1_ENTITIES + P2_ENTITIES
AR_TYPE_INVOICE = 2  # `/customer-receivable?type=2` = per faktur (spec)


def _all_branches(rt: BrandRuntime) -> list[int]:
    """Semua cabang brand (toko, CK, gudang, head office) dari `dim_esb_branch` slice `branch`."""
    return sorted({int(row[2]) for row in rt.branch_rows if int(row[2]) > 0})


# ================================================================== P1
def extract_bom_detail(rt: BrandRuntime, task: SliceTask, version: Any, get: Getter) -> SliceResult:
    """E1: `/product/bom` (daftar) -> `/product/bom/{bomID}` untuk setiap BOM AKTIF (satu GET per BOM)."""
    cfg = rt.cfg
    listed = _list(get, "/product/bom", {}, rt, limit=MASTER_LIMIT)
    ids = sorted(
        {to_int(r.get("bomID")) for r in listed if to_int(r.get("bomID")) > 0 and flag(r.get("flagActive"), 1) == 1}
    )
    result = SliceResult(task, version)
    key = task.day.isoformat()
    result.raw.append(RawDoc("product/bom", key, task.day, listed))
    headers: list[list[Any]] = []
    lines: list[list[Any]] = []
    for bom_id in ids:
        detail = get(f"/product/bom/{bom_id}", {})
        detail = redact(detail) if isinstance(detail, Mapping) else {}
        result.raw.append(RawDoc("product/bom/{bomID}", str(bom_id), task.day, detail))
        header, bom_lines = transform_bom(detail, cfg.key, cfg.company_code, version)
        if header is not None:
            headers.append(header)
            lines.extend(bom_lines)
    result.tables = {"dim_esb_bom": headers, "dim_esb_bom_line": lines}
    return result


def extract_menu_price(rt: BrandRuntime, task: SliceTask, version: Any, get: Getter) -> SliceResult:
    """E2: `/menu/browse-for-product` -> harga + bomName per menu."""
    cfg = rt.cfg
    rows = _list(get, "/menu/browse-for-product", {}, rt, limit=MASTER_LIMIT)
    result = SliceResult(task, version)
    result.tables = {"dim_esb_menu_price": transform_menu_prices(rows, cfg.key, cfg.company_code, version)}
    result.raw.append(RawDoc("menu/browse-for-product", task.day.isoformat(), task.day, rows))
    return result


def extract_customer(rt: BrandRuntime, task: SliceTask, version: Any, get: Getter) -> SliceResult:
    """E4: `/customer` -> whitelist `CUSTOMER_FIELDS` SEBELUM disimpan (alamat/PIC/telepon dibuang)."""
    cfg = rt.cfg
    rows = [redact_customer(r) for r in fetch_all(get, "/customer", {}, limit=MASTER_LIMIT, max_pages=rt.max_pages)]
    result = SliceResult(task, version)
    result.tables = {"dim_esb_customer": transform_customers(rows, cfg.key, cfg.company_code, version)}
    # raw_rows() menerapkan redact() lagi: kunci ber-"customer" ikut terbuang dari raw (mart tetap memuat kode/nama).
    result.raw.append(RawDoc("customer", task.day.isoformat(), task.day, rows))
    return result


def extract_location(rt: BrandRuntime, task: SliceTask, version: Any, get: Getter) -> SliceResult:
    """E5: `/location?branchID=` untuk setiap cabang (respons tanpa branchID -> satu panggilan per cabang)."""
    cfg = rt.cfg
    result = SliceResult(task, version)
    out: list[list[Any]] = []
    for branch_id in _all_branches(rt):
        rows = _list(get, "/location", {"branchID": branch_id}, rt, limit=MASTER_LIMIT)
        result.raw.append(RawDoc("location", f"{task.day.isoformat()}|{branch_id}", task.day, rows))
        out.extend(transform_locations(rows, cfg.key, cfg.company_for(branch_id), branch_id, version))
    result.tables = {"dim_esb_location": out}
    return result


# ================================================================== P2 (belum diverifikasi; default mati di CLI)
def extract_coa(rt: BrandRuntime, task: SliceTask, version: Any, get: Getter) -> SliceResult:
    """E9: `/accounting/coa` (tidak berhalaman menurut spec)."""
    cfg = rt.cfg
    rows = [whitelist(r, COA_FIELDS) for r in fetch_all(get, "/accounting/coa", {}, limit=MASTER_LIMIT)]
    result = SliceResult(task, version)
    result.tables = {"dim_esb_coa": transform_coa(rows, cfg.key, cfg.company_code, version)}
    result.raw.append(RawDoc("accounting/coa", task.day.isoformat(), task.day, rows))
    return result


def extract_stock_opname(rt: BrandRuntime, task: SliceTask, version: Any, get: Getter) -> SliceResult:
    """E6: daftar opname [hari - window, hari] -> detail per nomor. Nama petugas/lampiran dibuang (redact)."""
    cfg = rt.cfg
    start = task.day - timedelta(days=max(0, rt.opname_window_days))
    window = {"dateFrom": start.isoformat(), "dateTo": task.day.isoformat()}
    listed = _list(get, "/inventory/stock-opname", window, rt, limit=MASTER_LIMIT)
    result = SliceResult(task, version)
    result.raw.append(RawDoc("inventory/stock-opname", task.day.isoformat(), task.day, listed))
    details: list[dict[str, Any]] = []
    for row in listed:
        num = text(row.get("stockOpnameNum"), 64)
        if not num:
            continue
        detail = get(f"/inventory/stock-opname/{quote(num, safe='')}", {})
        detail = redact(detail) if isinstance(detail, Mapping) else {}
        result.raw.append(RawDoc("inventory/stock-opname/{num}", num, task.day, detail))
        details.append({**row, **detail})
    facts = transform_stock_opname(details, cfg.key, cfg.company_for, task.day, version)
    result.tables = {"fact_esb_stock_opname": facts}
    return result


def extract_ap_open(rt: BrandRuntime, task: SliceTask, version: Any, get: Getter) -> SliceResult:
    """E7: `/supplier-payable/invoice-aging?branchID=&payableDate=` per cabang (respons tanpa cabang)."""
    cfg, day = rt.cfg, task.day.isoformat()
    result = SliceResult(task, version)
    out: list[list[Any]] = []
    for branch_id in _all_branches(rt):
        params = {"branchID": branch_id, "payableDate": day}
        listed = fetch_all(get, "/supplier-payable/invoice-aging", params, limit=MASTER_LIMIT, max_pages=rt.max_pages)
        rows = [whitelist(r, AP_FIELDS) for r in listed]
        result.raw.append(RawDoc("supplier-payable/invoice-aging", f"{day}|{branch_id}", task.day, rows))
        out.extend(transform_ap_open(rows, cfg.key, cfg.company_for(branch_id), branch_id, task.day, version))
    result.tables = {"fact_esb_ap_open": out}
    return result


def extract_ar_open(rt: BrandRuntime, task: SliceTask, version: Any, get: Getter) -> SliceResult:
    """E8: `/customer-receivable?type=2&branchID=&receivableDate=` per cabang; kode pelanggan saja."""
    cfg, day = rt.cfg, task.day.isoformat()
    result = SliceResult(task, version)
    out: list[list[Any]] = []
    for branch_id in _all_branches(rt):
        params = {"type": AR_TYPE_INVOICE, "branchID": branch_id, "receivableDate": day}
        listed = fetch_all(get, "/customer-receivable", params, limit=MASTER_LIMIT, max_pages=rt.max_pages)
        rows = [whitelist(r, AR_FIELDS) for r in listed]
        result.raw.append(RawDoc("customer-receivable", f"{day}|{branch_id}", task.day, rows))
        out.extend(transform_ar_open(rows, cfg.key, cfg.company_for(branch_id), branch_id, task.day, version))
    result.tables = {"fact_esb_ar_open": out}
    return result


MIG_EXTRACTORS: dict[str, Extractor] = {
    "bom_detail": extract_bom_detail,
    "menu_price": extract_menu_price,
    "customer": extract_customer,
    "location": extract_location,
    "stock_opname": extract_stock_opname,
    "ap_open": extract_ap_open,
    "ar_open": extract_ar_open,
    "coa": extract_coa,
}
