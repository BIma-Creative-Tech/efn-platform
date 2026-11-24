"""Worker job `pr_import` dan `calculate` (docs/04 §30.5, §30.8). Parser respons ESB di sini MURNI (diuji tanpa I/O).

`calculate`: segarkan BOM + stok dari ESB (GET saja), hitung `calc.compute_plan`, simpan snapshot stok + hasil dalam
satu transaksi bila rencana masih `draft` dan `row_version` tidak berubah sejak job diminta.

§30.17.9: job memakai token sesi hidup milik `job.requested_by_sub` (tanpa sesi / token ditolak -> `failed`
`needs_login`; GET saja, aman diulang setelah login). Sumber transfer = semua lokasi cabang sesi PEMICU (maks 20 cabang,
kecuali lokasi produksi); cache katalog BOM dikunci per aktor (`cache_scope`).
"""

import logging
import threading
import time
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from efn_prodplan import metrics
from efn_prodplan.calc import (
    Bom,
    BomLine,
    CalcError,
    Demand,
    PlanResult,
    compute_plan,
    q4,
    short_materials,
    to_decimal,
)
from efn_prodplan.esb import (
    CallBudgetExceeded,
    EsbAuthError,
    EsbError,
    EsbTokenRejected,
    encode_doc_number,
    page_rows,
)
from efn_prodplan.logs import log_event
from efn_prodplan.names import parse_names
from efn_prodplan.workflow import JOB_ERROR_CODES

BOM_FETCH_MAX = 400
PR_NUMBER_KEYS = ("purchaseRequestNum", "purchaseRequestNumber", "prNum")
SOURCE_BRANCHES_MAX = 20


def cache_scope(actor_id: int, brand: str) -> str:
    """Kunci cache per aktor (§30.17.6, R-T24): data ESB tidak dibagi antar-pengguna lewat cache."""
    return f"{int(actor_id)}:{brand}"


def source_branches(branch_ids: Iterable[int], prod_branch: int) -> tuple[list[int], list[int]]:
    """Cabang sumber transfer dari sesi pemicu: cabang produksi dulu, lalu naik; maks 20 -> (dipakai, diabaikan)."""
    ordered = sorted({int(b) for b in branch_ids})
    if prod_branch in ordered:
        ordered.remove(prod_branch)
        ordered.insert(0, prod_branch)
    return ordered[:SOURCE_BRANCHES_MAX], ordered[SOURCE_BRANCHES_MAX:]


def source_locations(locations: Mapping[int, Iterable[int]], prod_branch: int, prod_loc: int) -> list[tuple[int, int]]:
    """Semua lokasi cabang sumber kecuali lokasi produksi rencana (urutan sumber final tetap `calc.order_sources`)."""
    return [
        (br, loc)
        for br in locations
        for loc in sorted({int(x) for x in locations[br]})
        if (br, loc) != (prod_branch, prod_loc)
    ]


class JobAbort(Exception):
    """Job dihentikan dengan kode galat tertentu (mis. rencana bukan draft lagi, versi berubah)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# ====================================================================== parser murni
def _int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return 0


def _text(value: Any, limit: int) -> str:
    return str(value).strip()[:limit] if value is not None else ""


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return False


@dataclass(frozen=True)
class BomHeader:
    bom_id: int
    product_detail_id: int
    product_code: str
    product_name: str
    uom_name: str
    bom_name: str = ""
    bom_code: str = ""


def _assembly_active(row: Mapping[str, Any]) -> bool:
    """Filter ganda: baris dengan `bomTypeID`/`flagActive` eksplisit selain 1 dibuang (query sudah memfilter)."""
    if "bomTypeID" in row and row.get("bomTypeID") is not None and _int(row.get("bomTypeID")) != 1:
        return False
    return not ("flagActive" in row and row.get("flagActive") is not None and _int(row.get("flagActive")) != 1)


def parse_bom_list(rows: Iterable[Mapping[str, Any]]) -> dict[int, dict[str, str]]:
    """`GET /product/bom?bomTypeID=1&flagActive=1` -> bom_id -> {bom_name, bom_code}.

    Uji staging: daftar TIDAK memuat productDetailID -> produk hasil diambil dari header `/product/bom/{bomID}`.
    """
    out: dict[int, dict[str, str]] = {}
    for row in rows:
        bom_id = _int(row.get("bomID"))
        if bom_id <= 0 or not _assembly_active(row):
            continue
        out[bom_id] = {"bom_name": _text(row.get("bomName"), 200), "bom_code": _text(row.get("bomCode"), 40)}
    return out


def parse_bom_header(doc: Any, listed: Mapping[str, str] | None = None) -> BomHeader | None:
    """`GET /product/bom/{bomID}` -> header (None bila bukan Assembly aktif / tanpa produk hasil)."""
    if not isinstance(doc, Mapping) or not _assembly_active(doc):
        return None
    bom_id = _int(doc.get("bomID"))
    detail_id = _int(doc.get("productDetailID"))
    if bom_id <= 0 or detail_id <= 0:
        return None
    listed = listed or {}
    return BomHeader(
        bom_id=bom_id,
        product_detail_id=detail_id,
        product_code=_text(doc.get("productCode"), 40),
        product_name=_text(doc.get("productName") or doc.get("bomName"), 200),
        uom_name=_text(doc.get("uomName"), 20),
        bom_name=_text(doc.get("bomName"), 200) or listed.get("bom_name", ""),
        bom_code=_text(doc.get("bomCode"), 40) or listed.get("bom_code", ""),
    )


def index_by_product(headers: Mapping[int, BomHeader]) -> dict[int, int]:
    """productDetailID -> bom_id Assembly terkecil (deterministik bila satu produk punya beberapa BOM)."""
    by_product: dict[int, int] = {}
    for bom_id in sorted(headers):
        by_product.setdefault(headers[bom_id].product_detail_id, bom_id)
    return by_product


SEARCH_MAX_LEN = 64


def validate_search(q: Any) -> str:
    """Teks pencarian BOM: 2..64 karakter tanpa karakter kontrol (dikirim sebagai parameter query ter-encode; tidak
    pernah dilog). Selaras dengan insight-web (`prodplan-actions.ts`)."""
    if not isinstance(q, str):
        raise ValueError("q")
    text = q.strip()
    if not 2 <= len(text) <= SEARCH_MAX_LEN or any(unicodedata.category(ch).startswith("C") for ch in text):
        raise ValueError("q")
    return text


class BomCatalog:
    """Daftar + header BOM Assembly per `scope` (= `cache_scope(actor_id, brand)`, §30.17.6: tidak ada data ESB yang
    dibagi antar-pengguna), cache memori TTL pendek (thread-safe). Semua GET lewat klien bertoken pemanggil (token
    bucket brand + anggaran panggilan job tetap berlaku). Parameter `brand` di bawah = scope."""

    def __init__(self, ttl_seconds: float = 900.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._lists: dict[str, tuple[float, dict[int, dict[str, str]]]] = {}
        self._headers: dict[tuple[str, int], tuple[float, BomHeader | None]] = {}
        self._lock = threading.Lock()

    def listing(self, brand: str, client: Any) -> dict[int, dict[str, str]]:
        now = self._clock()
        with self._lock:
            cached = self._lists.get(brand)
            if cached and now - cached[0] < self._ttl:
                return cached[1]
        rows = client.get_all("/product/bom", {"bomTypeID": 1, "flagActive": 1}, limit=200, max_pages=50)
        listed = parse_bom_list(rows)
        with self._lock:
            self._lists[brand] = (now, listed)
        return listed

    def header(self, brand: str, client: Any, bom_id: int, listed: Mapping[str, str] | None = None) -> BomHeader | None:
        now = self._clock()
        key = (brand, int(bom_id))
        with self._lock:
            cached = self._headers.get(key)
            if cached and now - cached[0] < self._ttl:
                return cached[1]
        header = parse_bom_header(client.get(f"/product/bom/{int(bom_id)}"), listed)
        if header is not None and header.bom_id != int(bom_id):
            header = None
        with self._lock:
            self._headers[key] = (now, header)
            if len(self._headers) > 20_000:
                self._headers = {k: v for k, v in self._headers.items() if now - v[0] < self._ttl}
        return header

    def index(self, brand: str, client: Any) -> tuple[dict[int, int], dict[int, BomHeader]]:
        """Semua BOM Assembly aktif -> (produk -> bom_id, bom_id -> header).

        Satu GET header per BOM yang belum di cache (beban ESB R-O10: dibatasi token bucket + PRODPLAN_JOB_MAX_CALLS).
        """
        listed = self.listing(brand, client)
        headers: dict[int, BomHeader] = {}
        for bom_id in sorted(listed):
            header = self.header(brand, client, bom_id, listed[bom_id])
            if header is not None:
                headers[bom_id] = header
        return index_by_product(headers), headers

    def search(self, brand: str, client: Any, q: str, limit: int) -> list[dict[str, Any]]:
        rows = client.get(
            "/product/bom", {"bomTypeID": 1, "flagActive": 1, "bomName": q, "page": 1, "limit": max(limit, 1)}
        )
        listed = parse_bom_list(page_rows(rows))
        items: list[dict[str, Any]] = []
        for bom_id in sorted(listed)[:limit]:
            header = self.header(brand, client, bom_id, listed[bom_id])
            if header is None:
                continue
            items.append(
                {
                    "bom_id": header.bom_id,
                    "bom_name": header.bom_name,
                    "bom_code": header.bom_code,
                    "product_detail_id": header.product_detail_id,
                    "product_code": header.product_code,
                    "product_name": header.product_name,
                    "uom_name": header.uom_name,
                }
            )
        return items


@dataclass
class BomDetail:
    bom: Bom
    stock: dict[int, Decimal] = field(default_factory=dict)  # productDetailID -> stockQty di lokasi diminta
    skipped_substitutes: list[str] = field(default_factory=list)
    invalid_qty: list[str] = field(default_factory=list)  # bahan ber-qty ≤ 0 / tak terbaca (SEC-231)


def parse_bom_detail(header: BomHeader, rows: Iterable[Mapping[str, Any]]) -> BomDetail:
    """`GET /product/bom/detail/{bomID}?branchID=&locationID=` -> BOM + stok lokasi.

    Baris `subtitution` bernilai benar (1/true) dianggap bahan PENGGANTI dan tidak ikut kebutuhan (belum diverifikasi).
    """
    lines: list[BomLine] = []
    stock: dict[int, Decimal] = {}
    skipped: list[str] = []
    invalid: list[str] = []
    for row in rows:
        detail_id = _int(row.get("productDetailID"))
        if detail_id <= 0:
            continue
        code = _text(row.get("productCode"), 40)
        raw_stock = row.get("stockQty")
        if raw_stock is not None:
            try:
                stock[detail_id] = to_decimal(raw_stock)
            except (ValueError, ArithmeticError):
                pass
        if _truthy(row.get("subtitution")):
            skipped.append(code)
            continue
        try:
            qty: Decimal | None = to_decimal(row.get("qty"))
        except (ValueError, ArithmeticError):
            qty = None
        if qty is None or not qty.is_finite() or qty <= 0:
            invalid.append(code or str(detail_id))  # SEC-231: dilewati + peringatan, bukan dikirim ke ESB
            continue
        yield_raw = row.get("yieldPercent")
        try:
            yield_pct = to_decimal(yield_raw) if yield_raw not in (None, "") else None
        except (ValueError, ArithmeticError):
            yield_pct = None
        lines.append(
            BomLine(
                product_detail_id=detail_id,
                product_code=code,
                product_name=_text(row.get("productName"), 200),
                uom_name=_text(row.get("uomName"), 20),
                qty=qty,
                yield_percent=yield_pct,
            )
        )
    bom = Bom(
        bom_id=header.bom_id,
        product_detail_id=header.product_detail_id,
        product_code=header.product_code,
        product_name=header.product_name,
        uom_name=header.uom_name,
        lines=tuple(lines),
    )
    return BomDetail(bom=bom, stock=stock, skipped_substitutes=skipped, invalid_qty=invalid)


def parse_master_uom(rows: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """`GET /product` -> productCode -> satuan dasar (`uomName`/`baseUomName`)."""
    out: dict[str, str] = {}
    for row in rows:
        code = _text(row.get("productCode"), 40)
        uom = _text(row.get("uomName") or row.get("baseUomName"), 20)
        if code and uom:
            out[code] = uom
    return out


def pr_number(row: Mapping[str, Any]) -> str | None:
    for key in PR_NUMBER_KEYS:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:40]
    return None


@dataclass
class PrLine:
    product_detail_id: int
    product_code: str
    product_name: str
    uom_name: str
    qty: Decimal
    pr_nums: list[str]


def aggregate_pr(details: Sequence[tuple[str, Mapping[str, Any]]]) -> tuple[dict[int, PrLine], list[dict[str, Any]]]:
    """Gabungkan `purchaseRequestDetails` semua PR per productDetailID. Baris tanpa productDetailID -> unknown."""
    agg: dict[int, PrLine] = {}
    unknown: list[dict[str, Any]] = []
    for num, detail in details:
        rows = detail.get("purchaseRequestDetails") if isinstance(detail, Mapping) else None
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, Mapping):
                continue
            try:
                qty = to_decimal(row.get("qty"))
            except (ValueError, ArithmeticError):
                continue
            if qty <= 0:
                continue
            detail_id = _int(row.get("productDetailID"))
            code = _text(row.get("productCode"), 40)
            if detail_id <= 0:
                unknown.append(
                    {
                        "pr_num": num,
                        "product_code": code or "?",
                        "product_name": _text(row.get("productName"), 200),
                        "qty": q4(qty),
                        "reason": "unknown_product",
                    }
                )
                continue
            line = agg.get(detail_id)
            if line is None:
                agg[detail_id] = PrLine(
                    detail_id, code, _text(row.get("productName"), 200), _text(row.get("uomName"), 20), qty, [num]
                )
            else:
                line.qty += qty
                if num not in line.pr_nums:
                    line.pr_nums.append(num)
    return agg, unknown


def map_pr_demand(
    agg: Mapping[int, PrLine], bom_for_product: Mapping[int, int], headers: Mapping[int, BomHeader]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Baris PR -> permintaan (punya BOM Assembly, satuan sama) atau `unmapped` (§30.5 langkah 1)."""
    demand: list[dict[str, Any]] = []
    unmapped: list[dict[str, Any]] = []
    for detail_id in sorted(agg):
        line = agg[detail_id]
        bom_id = bom_for_product.get(detail_id)
        base = {"product_code": line.product_code, "product_name": line.product_name, "qty": q4(line.qty)}
        if bom_id is None:
            unmapped.extend({**base, "pr_num": n, "reason": "no_assembly_bom"} for n in line.pr_nums[:1])
            continue
        header = headers[bom_id]
        if header.uom_name and line.uom_name and header.uom_name.strip().upper() != line.uom_name.strip().upper():
            unmapped.extend({**base, "pr_num": n, "reason": "uom_mismatch"} for n in line.pr_nums[:1])
            continue
        demand.append(
            {
                "product_detail_id": detail_id,
                "product_code": line.product_code or header.product_code,
                "product_name": line.product_name or header.product_name,
                "bom_id": bom_id,
                "uom_name": header.uom_name or line.uom_name,
                "qty": q4(line.qty),
                "pr_nums": sorted(line.pr_nums),
            }
        )
    return demand, unmapped


def job_error_code(exc: BaseException) -> str:
    """Kode galat job; selalu anggota `workflow.JOB_ERROR_CODES` (kode tak dikenal -> `internal`)."""
    if isinstance(exc, CalcError | JobAbort):
        return exc.code if exc.code in JOB_ERROR_CODES else "internal"
    if isinstance(exc, CallBudgetExceeded):
        return "call_budget_exceeded"
    if isinstance(exc, EsbTokenRejected):
        return "needs_login"  # §30.17.9: token pemicu ditolak di tengah job (sesi sudah dibuang)
    if isinstance(exc, EsbAuthError):
        return "esb_auth_failed"
    if isinstance(exc, EsbError):
        return "esb_unavailable"
    if isinstance(exc, ValueError | KeyError | TypeError | ArithmeticError):
        return "parse_error"
    return "internal"


# ====================================================================== pengambilan data ESB
class EsbReader(Protocol):
    calls: int

    def get(self, path: str, params: Mapping[str, Any] | None = None) -> Any: ...

    def get_all(
        self, path: str, params: Mapping[str, Any] | None = None, *, limit: int = 200, max_pages: int = 100
    ) -> list[dict[str, Any]]: ...


def fetch_boms(
    client: EsbReader,
    roots: Iterable[int],
    bom_for_product: Mapping[int, int],
    headers: Mapping[int, BomHeader],
    branch_id: int,
    location_id: int,
) -> tuple[dict[int, Bom], dict[int, Decimal], list[str], list[str]]:
    """BOM detail rekursif (BFS) di lokasi produksi -> (boms, stok lokasi, kode substitusi yang dilewati,
    "BOM:bahan" ber-qty tidak valid yang dilewati)."""
    boms: dict[int, Bom] = {}
    stock: dict[int, Decimal] = {}
    skipped: list[str] = []
    invalid: list[str] = []
    queue = sorted(set(roots))
    while queue:
        bom_id = queue.pop(0)
        if bom_id in boms:
            continue
        header = headers.get(bom_id)
        if header is None:
            raise CalcError("bom_not_assembly", [str(bom_id)])
        if len(boms) >= BOM_FETCH_MAX:
            raise CalcError("bom_too_deep", [header.product_code])
        rows = client.get(f"/product/bom/detail/{bom_id}", {"branchID": branch_id, "locationID": location_id})
        detail = parse_bom_detail(header, _rows(rows))
        boms[bom_id] = detail.bom
        stock.update(detail.stock)
        skipped.extend(detail.skipped_substitutes)
        invalid.extend(f"{header.product_code}:{code}" for code in detail.invalid_qty)
        for ln in detail.bom.lines:
            child = bom_for_product.get(ln.product_detail_id)
            if child is not None and child not in boms and child not in queue:
                queue.append(child)
    return boms, stock, sorted(set(skipped)), sorted(set(invalid))


def _rows(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, list):
        return [r for r in result if isinstance(r, dict)]
    if isinstance(result, Mapping):
        data = result.get("data") or result.get("bomDetails") or []
        return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []
    return []


def boms_covering(materials: Iterable[int], boms: Mapping[int, Bom]) -> list[int]:
    """BOM minimum (serakah, bom_id naik) yang memuat semua bahan -> satu GET detail per BOM per lokasi sumber."""
    remaining = set(materials)
    chosen: list[int] = []
    for bom_id in sorted(boms):
        contains = {ln.product_detail_id for ln in boms[bom_id].lines} & remaining
        if contains:
            chosen.append(bom_id)
            remaining -= contains
        if not remaining:
            break
    return chosen


def result_to_json(result: PlanResult) -> dict[str, Any]:
    return {
        "production_lines": len(result.production_lines),
        "requirements": len(result.requirements),
        "suggestions": len(result.suggestions),
        "transfers": len(result.transfers),
        "shortages": sum(1 for r in result.requirements if r.shortage_qty > 0),
        "warnings": {k: list(v) for k, v in result.warnings.items()},
    }


# ====================================================================== runner (PostgreSQL + ESB)
class JobRunner:
    def __init__(
        self,
        db: Any,
        job_store: Any,
        settings: Any,
        clients: Callable[[int, str], tuple[Any, Any] | None],
        clock: Callable[[], datetime],
        catalog: BomCatalog | None = None,
    ) -> None:
        """`clients(actor_id, brand)` -> (sesi, klien ESB beranggaran `PRODPLAN_JOB_MAX_CALLS`) milik pemicu, atau
        None bila aktor itu tidak punya sesi hidup (job -> `needs_login`)."""
        self._db = db
        self._jobs = job_store
        self._settings = settings
        self._clients = clients
        self._clock = clock
        self._catalog = catalog or BomCatalog()

    def start(self) -> None:
        recovered = self._jobs.recover_running()
        if recovered:
            log_event("job_recovered_running", logging.WARNING, count=recovered)

    def run_once(self) -> bool:
        job = self._jobs.claim_job()
        if job is None:
            return False
        started = time.monotonic()
        kind = job["kind"]
        client = None
        try:
            plan = self._load_plan(job["plan_id"])
            owned = self._clients(int(job["requested_by_sub"]), plan["esb_brand"])
            if owned is None:
                raise JobAbort("needs_login")  # tanpa panggilan ESB
            session, client = owned
            scope = cache_scope(session.actor_id, plan["esb_brand"])
            if kind == "pr_import":
                result = self._pr_import(job, plan, client, scope)
            else:
                result = self._calculate(job, plan, client, scope, session.branch_ids)
            self._jobs.finish_job(job["id"], "done", result, None, client.calls)
            status = "done"
        except Exception as exc:  # noqa: BLE001 - job gagal tercatat, worker tetap hidup
            code = job_error_code(exc)
            detail: dict[str, Any] | None = None
            if isinstance(exc, CalcError) and exc.path:
                detail = {"path": exc.path[:12]}
            self._jobs.finish_job(job["id"], "failed", detail, code[:40], client.calls if client else 0)
            if kind == "calculate":
                self._mark_calc_failed(job["plan_id"])
            log_event(
                "job_failed",
                logging.WARNING,
                job_id=str(job["id"]),
                kind=kind,
                error_code=code,
                error_class=type(exc).__name__,
            )
            status = "failed"
        metrics.JOBS.inc(kind, status)
        metrics.JOB_SECONDS.observe(time.monotonic() - started, kind)
        return True

    # ------------------------------------------------------------------ DB
    def _load_plan(self, plan_id: Any) -> dict[str, Any]:
        with self._db.tx() as tx:
            plan = tx.one(
                "SELECT id, company_code, esb_brand, esb_env, branch_id, production_location_id, production_date,"
                " status, row_version FROM prodplan.plan WHERE id = %s",
                (plan_id,),
            )
        if plan is None:
            raise JobAbort("not_found")
        return plan

    def _mark_calc_failed(self, plan_id: Any) -> None:
        with self._db.tx() as tx:
            tx.exec(
                "UPDATE prodplan.plan SET calc_status = 'failed', updated_at = now()"
                " WHERE id = %s AND status = 'draft'",
                (plan_id,),
            )

    # ------------------------------------------------------------------ pr_import
    def _pr_import(self, job: Mapping[str, Any], plan: Mapping[str, Any], client: Any, scope: str) -> dict[str, Any]:
        params = job["params"] or {}
        query: dict[str, Any] = {
            "requiredDateFrom": params["required_date_from"],
            "requiredDateTo": params["required_date_to"],
        }
        branch_ids = params.get("branch_ids") or []
        if branch_ids:
            query["branchIDs"] = ",".join(str(int(b)) for b in branch_ids)
        nums: list[str] = []
        for status_id in params.get("status_ids") or [None]:
            q = dict(query)
            if status_id is not None:
                q["statusID"] = int(status_id)
            for row in client.get_all("/purchase/purchase-request", q, limit=100, max_pages=50):
                num = pr_number(row)
                if num and num not in nums:
                    nums.append(num)
        details: list[tuple[str, Mapping[str, Any]]] = []
        for num in nums:
            detail = client.get(f"/purchase/purchase-request/{encode_doc_number(num)}")
            if isinstance(detail, Mapping):
                details.append((num, detail))
        bom_for_product, headers = self._catalog.index(scope, client)
        agg, unknown = aggregate_pr(details)
        demand, unmapped = map_pr_demand(agg, bom_for_product, headers)
        unmapped.extend(unknown)
        with self._db.tx() as tx:
            locked = tx.one("SELECT status FROM prodplan.plan WHERE id = %s FOR UPDATE", (plan["id"],))
            if locked is None or locked["status"] != "draft":
                raise JobAbort("invalid_state")
            tx.exec("DELETE FROM prodplan.plan_demand WHERE plan_id = %s AND source = 'pr'", (plan["id"],))
            tx.exec("DELETE FROM prodplan.plan_unmapped WHERE plan_id = %s", (plan["id"],))
            for d in demand:
                tx.exec(
                    "INSERT INTO prodplan.plan_demand (plan_id, product_detail_id, product_code, product_name, bom_id,"
                    " uom_name, qty, source, pr_nums, created_by_sub) VALUES (%s,%s,%s,%s,%s,%s,%s,'pr',%s,%s)",
                    (
                        plan["id"],
                        d["product_detail_id"],
                        d["product_code"] or "?",
                        d["product_name"] or "?",
                        d["bom_id"],
                        d["uom_name"] or "?",
                        d["qty"],
                        d["pr_nums"],
                        job["requested_by_sub"],
                    ),
                )
            for u in unmapped:
                tx.exec(
                    "INSERT INTO prodplan.plan_unmapped (plan_id, pr_num, product_code, product_name, qty, reason)"
                    " VALUES (%s,%s,%s,%s,%s,%s)",
                    (plan["id"], u["pr_num"], u["product_code"] or "?", u["product_name"], u["qty"], u["reason"]),
                )
            tx.exec(
                "UPDATE prodplan.plan SET calc_status = 'stale', row_version = row_version + 1, updated_at = now()"
                " WHERE id = %s",
                (plan["id"],),
            )
        return {"prs": len(nums), "demand_lines": len(demand), "unmapped": len(unmapped)}

    # ------------------------------------------------------------------ calculate
    def _source_locations(
        self, client: Any, branch_ids: Iterable[int], prod_branch: int, prod_loc: int
    ) -> tuple[list[tuple[int, int]], list[int]]:
        """§30.17.6: lokasi semua cabang sesi pemicu (GET `/location/branch/{id}`, maks 20 cabang)."""
        used, dropped = source_branches(branch_ids, prod_branch)
        locations: dict[int, list[int]] = {}
        for b in used:
            locations[b] = list(parse_names(client.get(f"/location/branch/{b}"), "locationID", "locationName"))
        return source_locations(locations, prod_branch, prod_loc), dropped

    def _calculate(
        self, job: Mapping[str, Any], plan: Mapping[str, Any], client: Any, scope: str, branch_ids: Iterable[int]
    ) -> dict[str, Any]:
        settings = self._settings
        brand = settings.brand(plan["esb_brand"])
        if brand is None:
            raise JobAbort("forbidden")
        with self._db.tx() as tx:
            demand_rows = tx.all(
                "SELECT product_detail_id, bom_id, qty FROM prodplan.plan_demand WHERE plan_id = %s", (plan["id"],)
            )
            pack_rows = tx.all(
                "SELECT product_code, pack_qty FROM prodplan.pack_rule WHERE esb_brand = %s AND active",
                (plan["esb_brand"],),
            )
        demands = [Demand(int(r["product_detail_id"]), int(r["bom_id"]), Decimal(r["qty"])) for r in demand_rows]
        pack_rules = {r["product_code"]: Decimal(r["pack_qty"]) for r in pack_rows}
        branch_id, loc_id = int(plan["branch_id"]), int(plan["production_location_id"])

        bom_for_product, headers = self._catalog.index(scope, client)
        master_uom = parse_master_uom(client.get_all("/product", {}, limit=500, max_pages=40))
        boms, prod_stock, skipped, invalid_qty = fetch_boms(
            client, [d.bom_id for d in demands], bom_for_product, headers, branch_id, loc_id
        )
        stock: dict[tuple[int, int, int], Decimal] = {(branch_id, loc_id, k): v for k, v in prod_stock.items()}
        sources: list[tuple[int, int]] = []  # diisi hanya bila ada kekurangan (hemat GET lokasi)
        dropped: list[int] = []

        def compute() -> PlanResult:
            return compute_plan(
                demands,
                boms,
                bom_for_product,
                master_uom=master_uom,
                pack_rules=pack_rules,
                stock=stock,
                prod_branch=branch_id,
                prod_loc=loc_id,
                sources=sources,
                apply_yield=settings.apply_yield,
            )

        result = compute()
        short = short_materials(result)
        if short:
            sources, dropped = self._source_locations(client, branch_ids, branch_id, loc_id)
        if short and sources:
            covering = boms_covering(short, boms)
            for src_branch, src_loc in sources:
                for bom_id in covering:
                    rows = client.get(f"/product/bom/detail/{bom_id}", {"branchID": src_branch, "locationID": src_loc})
                    detail = parse_bom_detail(headers[bom_id], _rows(rows))
                    for detail_id, qty in detail.stock.items():
                        stock[(src_branch, src_loc, detail_id)] = qty
            result = compute()
        saved = self._save_calculation(job, plan, result, stock, client.calls)
        out = result_to_json(result)
        out.update(saved)  # plan_status, changed (QA-A)
        if skipped:
            out["warnings"]["skipped_substitutes"] = skipped
        if invalid_qty:
            merged = set(out["warnings"].get("invalid_bom_qty", [])) | set(invalid_qty)
            out["warnings"]["invalid_bom_qty"] = sorted(merged)
        if dropped:
            out["warnings"]["source_branches_truncated"] = dropped  # cabang sesi ke-21 dst. diabaikan
        return out

    def _save_calculation(
        self,
        job: Mapping[str, Any],
        plan: Mapping[str, Any],
        result: PlanResult,
        stock: Mapping[tuple[int, int, int], Decimal],
        calls: int,
    ) -> dict[str, Any]:
        """Simpan hasil. Mode refresh (rencana `approved`, QA-A): hasil material identik -> tetap `approved`, hanya
        snapshot stok + kebutuhan diperbarui; berbeda -> kembali `draft`, keputusan transfer direset, audit."""
        from efn_prodplan.store import audit

        plan_id = plan["id"]
        params = job["params"] or {}
        expected_version = params.get("row_version")
        refresh = bool(params.get("refresh"))
        with self._db.tx() as tx:
            locked = tx.one("SELECT status, row_version FROM prodplan.plan WHERE id = %s FOR UPDATE", (plan_id,))
            if locked is None or locked["status"] != ("approved" if refresh else "draft"):
                raise JobAbort("invalid_state")
            if expected_version is not None and locked["row_version"] != expected_version:
                raise JobAbort("conflict_version")
            if refresh and tx.one("SELECT 1 AS x FROM prodplan.outbox WHERE plan_id = %s LIMIT 1", (plan_id,)):
                raise JobAbort("invalid_state")  # sudah ada dokumen ESB untuk rencana ini
            changed = True
            if refresh:
                changed = signature_from_db(tx, plan_id) != signature_from_result(result)
            snap = tx.one(
                "INSERT INTO prodplan.stock_snapshot (company_code, esb_brand, esb_env, esb_calls)"
                " VALUES (%s,%s,%s,%s) RETURNING id",
                (plan["company_code"], plan["esb_brand"], plan["esb_env"], calls),
            )
            assert snap is not None
            for (br, loc, detail_id), qty in sorted(stock.items()):
                tx.exec(
                    "INSERT INTO prodplan.stock_snapshot_line (snapshot_id, branch_id, location_id, product_detail_id,"
                    " stock_qty, source) VALUES (%s,%s,%s,%s,%s,'bom_detail')",
                    (snap["id"], br, loc, detail_id, q4(qty)),
                )
            if refresh and not changed:
                tx.exec("DELETE FROM prodplan.plan_requirement WHERE plan_id = %s", (plan_id,))
                save_result(tx, plan_id, result, parts=("requirements",))
                tx.exec(
                    "UPDATE prodplan.plan SET calculated_at = now(), stock_snapshot_id = %s,"
                    " row_version = row_version + 1, updated_at = now() WHERE id = %s",
                    (snap["id"], plan_id),
                )
                audit(tx, plan["company_code"], plan_id, job["requested_by_sub"], None, "plan_refreshed", {})
                return {"plan_status": "approved", "changed": False}
            # QA-D: usulan yang pernah ditolak tidak diusulkan ulang bila identik (bahan, bom, extra_units).
            rejected = {
                (int(r["material_detail_id"]), int(r["bom_id"]), int(r["extra_units"]))
                for r in tx.all(
                    "SELECT material_detail_id, bom_id, extra_units FROM prodplan.plan_suggestion"
                    " WHERE plan_id = %s AND status = 'rejected'",
                    (plan_id,),
                )
            }
            for table in ("plan_production_line", "plan_requirement", "plan_transfer"):
                tx.exec(f"DELETE FROM prodplan.{table} WHERE plan_id = %s", (plan_id,))  # noqa: S608 - nama tetap
            tx.exec(
                "DELETE FROM prodplan.plan_suggestion WHERE plan_id = %s AND status IN ('open','superseded')",
                (plan_id,),
            )
            save_result(tx, plan_id, result, skip_suggestions=rejected)
            if refresh:
                tx.exec(
                    "UPDATE prodplan.plan SET status = 'draft', calc_status = 'fresh', calculated_at = now(),"
                    " stock_snapshot_id = %s, submitted_by_sub = NULL, submitted_by_name = NULL, submitted_at = NULL,"
                    " approved_by_sub = NULL, approved_by_name = NULL, approved_at = NULL,"
                    " row_version = row_version + 1, updated_at = now() WHERE id = %s",
                    (snap["id"], plan_id),
                )
                audit(tx, plan["company_code"], plan_id, job["requested_by_sub"], None, "plan_refresh_changed", {})
            else:
                tx.exec(
                    "UPDATE prodplan.plan SET calc_status = 'fresh', calculated_at = now(), stock_snapshot_id = %s,"
                    " row_version = row_version + 1, updated_at = now() WHERE id = %s",
                    (snap["id"], plan_id),
                )
        return {"plan_status": "draft", "changed": True}


def _q(value: Any) -> str:
    return str(q4(Decimal(str(value))))


def signature_from_result(result: PlanResult) -> tuple[Any, ...]:
    """Sidik hasil material (QA-A): baris produksi (+ bahan), usulan transfer + qty, himpunan bahan yang kurang."""
    prod = sorted(
        (pl.bom_id, pl.level, _q(pl.qty), tuple(sorted((m.product_detail_id, _q(m.total_qty)) for m in pl.materials)))
        for pl in result.production_lines
    )
    transfers = sorted(
        (
            t.doc_type,
            t.origin_branch_id,
            t.origin_location_id,
            t.dest_branch_id,
            t.dest_location_id,
            tuple(sorted((tl.product_detail_id, _q(tl.qty)) for tl in t.lines)),
        )
        for t in result.transfers
    )
    short = sorted(r.product_detail_id for r in result.requirements if r.shortage_qty > 0)
    return tuple(prod), tuple(transfers), tuple(short)


def signature_rows(
    production: Iterable[Mapping[str, Any]],
    transfers: Iterable[Mapping[str, Any]],
    transfer_lines: Iterable[Mapping[str, Any]],
    requirements: Iterable[Mapping[str, Any]],
) -> tuple[Any, ...]:
    """Sidik yang sama dari baris PostgreSQL yang tersimpan (murni)."""
    prod = sorted(
        (
            int(pl["bom_id"]),
            int(pl["level"]),
            _q(pl["qty"]),
            tuple(sorted((int(m["product_detail_id"]), _q(m["total_qty"])) for m in pl["materials"] or [])),
        )
        for pl in production
    )
    lines_by: dict[Any, list[tuple[int, str]]] = {}
    for ln in transfer_lines:
        lines_by.setdefault(ln["transfer_id"], []).append((int(ln["product_detail_id"]), _q(ln["qty"])))
    trans = sorted(
        (
            t["doc_type"],
            int(t["origin_branch_id"]),
            int(t["origin_location_id"]),
            int(t["dest_branch_id"]),
            int(t["dest_location_id"]),
            tuple(sorted(lines_by.get(t["id"], []))),
        )
        for t in transfers
    )
    short = sorted(int(r["product_detail_id"]) for r in requirements if Decimal(str(r["shortage_qty"])) > 0)
    return tuple(prod), tuple(trans), tuple(short)


def signature_from_db(tx: Any, plan_id: Any) -> tuple[Any, ...]:
    production = tx.all(
        "SELECT bom_id, level, qty, materials FROM prodplan.plan_production_line WHERE plan_id = %s", (plan_id,)
    )
    transfers = tx.all(
        "SELECT id, doc_type, origin_branch_id, origin_location_id, dest_branch_id, dest_location_id"
        " FROM prodplan.plan_transfer WHERE plan_id = %s",
        (plan_id,),
    )
    lines = tx.all(
        "SELECT l.transfer_id, l.product_detail_id, l.qty FROM prodplan.plan_transfer_line l"
        " JOIN prodplan.plan_transfer t ON t.id = l.transfer_id WHERE t.plan_id = %s",
        (plan_id,),
    )
    reqs = tx.all(
        "SELECT product_detail_id, shortage_qty FROM prodplan.plan_requirement WHERE plan_id = %s", (plan_id,)
    )
    return signature_rows(production, transfers, lines, reqs)


def save_result(
    tx: Any,
    plan_id: Any,
    result: PlanResult,
    *,
    parts: tuple[str, ...] = ("production_lines", "requirements", "suggestions", "transfers"),
    skip_suggestions: set[tuple[int, int, int]] | None = None,
) -> None:
    from efn_prodplan.store import jsonb

    production_lines = result.production_lines if "production_lines" in parts else ()
    requirements = result.requirements if "requirements" in parts else ()
    skip = skip_suggestions or set()
    suggestions = (
        [s for s in result.suggestions if (s.material_detail_id, s.bom_id, s.extra_units) not in skip]
        if "suggestions" in parts
        else []
    )
    transfers = result.transfers if "transfers" in parts else ()
    for ln in production_lines:
        tx.exec(
            "INSERT INTO prodplan.plan_production_line (plan_id, level, bom_id, product_detail_id, product_code,"
            " product_name, uom_name, qty, is_demand, materials) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                plan_id,
                ln.level,
                ln.bom_id,
                ln.product_detail_id,
                ln.product_code or "?",
                ln.product_name or "?",
                ln.uom_name or "?",
                ln.qty,
                ln.is_demand,
                jsonb(
                    [
                        {
                            "product_detail_id": m.product_detail_id,
                            "product_code": m.product_code,
                            "qty_per_unit": str(m.qty_per_unit),
                            "total_qty": str(m.total_qty),
                        }
                        for m in ln.materials
                    ]
                ),
            ),
        )
    for r in requirements:
        tx.exec(
            "INSERT INTO prodplan.plan_requirement (plan_id, product_detail_id, product_code, product_name, uom_name,"
            " need_qty, pack_qty, packs, leftover_qty, need_eff_qty, stock_prod_qty, transfer_qty, shortage_qty, flags)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                plan_id,
                r.product_detail_id,
                r.product_code or "?",
                r.product_name or "?",
                r.uom_name or "?",
                r.need_qty,
                r.pack_qty,
                r.packs,
                r.leftover_qty,
                r.need_eff_qty,
                r.stock_prod_qty,
                r.transfer_qty,
                r.shortage_qty,
                list(r.flags),
            ),
        )
    for s in suggestions:
        tx.exec(
            "INSERT INTO prodplan.plan_suggestion (plan_id, material_detail_id, bom_id, product_detail_id,"
            " extra_units, uses_qty, leftover_qty) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (plan_id, s.material_detail_id, s.bom_id, s.product_detail_id, s.extra_units, s.uses_qty, s.leftover_qty),
        )
    for t in transfers:
        row = tx.one(
            "INSERT INTO prodplan.plan_transfer (plan_id, doc_type, origin_branch_id, origin_location_id,"
            " dest_branch_id, dest_location_id) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
            (plan_id, t.doc_type, t.origin_branch_id, t.origin_location_id, t.dest_branch_id, t.dest_location_id),
        )
        for tl in t.lines:
            tx.exec(
                "INSERT INTO prodplan.plan_transfer_line (transfer_id, product_detail_id, product_code, uom_name, qty)"
                " VALUES (%s,%s,%s,%s,%s)",
                (row["id"], tl.product_detail_id, tl.product_code or "?", tl.uom_name or "?", tl.qty),
            )


def run_loop(runner: JobRunner, stop: threading.Event, poll_seconds: float = 2.0) -> None:
    runner.start()
    while not stop.is_set():
        try:
            busy = runner.run_once()
        except Exception as exc:  # noqa: BLE001
            log_event("job_loop_error", logging.ERROR, error_class=type(exc).__name__)
            busy = False
        if not busy:
            stop.wait(poll_seconds)
