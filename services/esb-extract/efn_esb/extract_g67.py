"""Extractor G67 (docs/04 §31.1, ADR-040 amandemen G67, L-260..L-268): laba rugi per cabang, jurnal item (waste &
pemakaian internal), goods transfer request (fill rate). GET saja; I/O hanya lewat `get` (diinjeksi di test).

- `profit_loss`: satu slice = cabang x TAHUN (`SliceTask.day` = 1 Januari, scope `<branch_id>` atau '*' = total brand).
  `/report/profit-loss/month-to-month?year=Y&month=M[&branchIDs=<id>]` (`branchIDs[]=` diabaikan server -> tidak
  pernah dipakai). M = bulan ini (WIB) untuk tahun berjalan, 12 untuk tahun lampau.
- `item_journal`: list `/inventory/item-journal` (dateFrom=dateTo=D) + detail per `itemJournalNum`.
- `transfer_request`: list `/inventory/goods-transfer-request` (dateFrom=dateTo=D) + detail per `transferNum`; pasangan
  (productID, productCode) ikut ditulis ke `dim_esb_product_id` (source 'gtr') dalam slice yang sama.
- `delivery_ref`: TIDAK punya extractor sendiri -- ditulis `extract.extract_delivery` (detail yang sama, tanpa GET
  tambahan) dan mode `retransform` (`runner.run_retransform`, dari raw_doc).
PDP (L-110): whitelist per endpoint SEBELUM raw/mart (`normalize.PL_FIELDS`, `ITEM_JOURNAL_*`, `GTR_*`); `raw_rows()`
menjalankan `redact()` lagi sebagai lapisan kedua (akibatnya `description` = nama akun P&L tidak ada di raw_doc; mart
tetap memuatnya).
"""

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any
from urllib.parse import quote

from efn_esb.extract import MASTER_LIMIT, BrandRuntime, Extractor, RawDoc, SliceResult
from efn_esb.normalize import (
    GTR_HEADER_FIELDS,
    GTR_LINE_FIELDS,
    GTR_LIST_FIELDS,
    ITEM_JOURNAL_HEADER_FIELDS,
    ITEM_JOURNAL_LINE_FIELDS,
    ITEM_JOURNAL_LIST_FIELDS,
    PL_FIELDS,
    text,
    whitelist,
    whitelist_doc,
    whitelist_tree,
)
from efn_esb.paging import Getter, fetch_all
from efn_esb.planning import BRAND_SCOPE, SliceTask, pl_month, wib_today
from efn_esb.transform_g67 import (
    SOURCE_GTR,
    check_detail_num,
    check_doc_day,
    gtr_product_pairs,
    product_id_rows,
    transform_item_journal,
    transform_profit_loss,
    transform_transfer_requests,
)

PL_PATH = "/report/profit-loss/month-to-month"
ITEM_JOURNAL_PATH = "/inventory/item-journal"
GTR_PATH = "/inventory/goods-transfer-request"
ITEM_JOURNAL_LINES = "itemJournalDetails"
GTR_LINES = "transferDetails"


def extract_profit_loss(rt: BrandRuntime, task: SliceTask, version: datetime, get: Getter) -> SliceResult:
    cfg, year = rt.cfg, task.day.year
    if task.day != date(year, 1, 1):
        raise ValueError("slice profit_loss wajib bertanggal 1 Januari")
    month = pl_month(year, wib_today(version))
    params: dict[str, Any] = {"year": year, "month": month}
    branch_id = task.branch_id
    if task.scope != BRAND_SCOPE:
        params["branchIDs"] = branch_id  # integer tunggal; `branchIDs[]=` diabaikan server (probe 25 Nov 2026)
    response = get(PL_PATH, params)
    if not isinstance(response, Mapping):
        raise ValueError("respons profit-loss bukan objek")
    payload = whitelist_tree(response, PL_FIELDS)
    # SEC-G67-01: total brand milik brand multi-PT (mis. Bacha BCF + override BCR) memuat agregat semua PT ->
    # company_code '' (row policy efn_insight membuangnya; tetap ada untuk admin/quality). Satu PT (PB) -> PT default.
    if branch_id > 0:
        company = cfg.company_for(branch_id)
    else:
        company = "" if cfg.multi_company else cfg.company_code
    facts = transform_profit_loss(payload, cfg.key, company, branch_id, year, month, version)
    result = SliceResult(task, version, tables={"fact_esb_profit_loss": facts})
    result.raw.append(RawDoc("report/profit-loss/month-to-month", f"{year}|{task.scope}", task.day, payload))
    return result


def _details(
    rt: BrandRuntime,
    task: SliceTask,
    get: Getter,
    path: str,
    list_fields: tuple[str, ...],
    num_key: str,
    date_key: str,
    header_fields: tuple[str, ...],
    line_key: str,
    line_fields: tuple[str, ...],
    result: SliceResult,
) -> list[dict[str, Any]]:
    """List hari D (ter-whitelist, tanggal dicek) + satu GET detail per nomor unik -> dokumen gabungan (list + detail).
    Raw: list (`doc_key=D`) dan setiap detail (`doc_key=<nomor>`, `doc_date=D`)."""
    day = task.day.isoformat()
    endpoint = path.lstrip("/")
    listed = [
        whitelist(row, list_fields)
        for row in fetch_all(get, path, {"dateFrom": day, "dateTo": day}, limit=MASTER_LIMIT, max_pages=rt.max_pages)
    ]
    check_doc_day(listed, task.day, date_key, endpoint)
    result.raw.append(RawDoc(endpoint, day, task.day, listed))
    docs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in listed:
        num = text(row.get(num_key), 64)
        if not num or num in seen:
            continue
        seen.add(num)
        response = get(f"{path}/{quote(num, safe='')}", {})
        detail = whitelist_doc(response if isinstance(response, Mapping) else {}, header_fields, line_key, line_fields)
        check_detail_num(detail, num_key, num)
        result.raw.append(RawDoc(f"{endpoint}/{{num}}", num, task.day, detail))
        docs.append({**row, **detail})
    return docs


def extract_item_journal(rt: BrandRuntime, task: SliceTask, version: datetime, get: Getter) -> SliceResult:
    cfg = rt.cfg
    result = SliceResult(task, version)
    docs = _details(
        rt, task, get, ITEM_JOURNAL_PATH, ITEM_JOURNAL_LIST_FIELDS, "itemJournalNum", "itemJournalDate",
        ITEM_JOURNAL_HEADER_FIELDS, ITEM_JOURNAL_LINES, ITEM_JOURNAL_LINE_FIELDS, result,
    )  # fmt: skip
    result.tables = {
        "fact_esb_item_journal_line": transform_item_journal(docs, cfg.key, cfg.company_for, task.day, version)
    }
    return result


def extract_transfer_request(rt: BrandRuntime, task: SliceTask, version: datetime, get: Getter) -> SliceResult:
    cfg = rt.cfg
    result = SliceResult(task, version)
    docs = _details(
        rt, task, get, GTR_PATH, GTR_LIST_FIELDS, "transferNum", "transferDate", GTR_HEADER_FIELDS, GTR_LINES,
        GTR_LINE_FIELDS, result,
    )  # fmt: skip
    pairs = gtr_product_pairs(docs)
    with rt.lock:
        product_ids = {**rt.product_ids, **pairs}
    facts = transform_transfer_requests(
        docs, cfg.key, cfg.company_for, product_ids, rt.alias_snapshot(), task.day, version
    )
    result.tables = {
        "fact_esb_transfer_request_line": facts,
        "dim_esb_product_id": product_id_rows(pairs, cfg.key, cfg.company_code, SOURCE_GTR, version),
    }
    return result


G67_EXTRACTORS: dict[str, Extractor] = {
    "profit_loss": extract_profit_loss,
    "item_journal": extract_item_journal,
    "transfer_request": extract_transfer_request,
}
