"""Handler API prodplan (docs/04 §30.8). Aturan murni ada di `workflow`; modul ini = validasi body + SQL transaksi.

Setiap mutasi rencana: `SELECT ... FOR UPDATE` baris `plan` + cek `row_version` -> 409 `conflict_version` (§30.6).

§30.17 (amandemen A1 ADR-051): pemanggil = sesi ESB (`Actor` dari `session.Session`). Rencana hanya terlihat bila
`esb_env` = environment proses, `esb_brand` = brand sesi, dan `branch_id` ∈ cabang sesi (di luar -> 404). Semua GET ESB
sinkron memakai token pemanggil; cache BOM/nama lokasi dikunci per aktor (`cache_scope`).
"""

import re
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from efn_prodplan.actor import Actor
from efn_prodplan.calc import q4
from efn_prodplan.config import Settings
from efn_prodplan.esb import EsbError, EsbTokenRejected
from efn_prodplan.jobs import BomCatalog, BomHeader, cache_scope, source_branches, validate_search
from efn_prodplan.names import NameCache
from efn_prodplan.outbox import RESENDABLE_ERRORS, find_by_marker
from efn_prodplan.payloads import (
    InvalidQuantity,
    doc_status_id,
    goods_transfer_request_body,
    manufacturing_body,
    new_marker,
    simple_transfer_body,
)
from efn_prodplan.store import Database, Tx, audit, jsonb
from efn_prodplan.workflow import (
    ANY_ROLE,
    CONTENT_ACTIONS,
    LIMITATIONS,
    PLANNER,
    WAREHOUSE,
    ApiError,
    check_date_window,
    check_sod,
    check_stock_age,
    check_version,
    plan_no,
    plan_resume,
    posting_state,
    preview_digest,
    require_branches,
    require_brand,
    require_doc_access,
    require_role,
    require_status,
    run_as,
    transition,
)

MAX_DEMAND_LINES = 500
_PRODUCT_CODE_RE = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
PLAN_COLUMNS = (
    "id, plan_no, company_code, esb_brand, esb_env, branch_id, production_location_id, production_date, status,"
    " calc_status, calculated_at, stock_snapshot_id, notes, created_by_sub, created_by_name, created_at,"
    " submitted_by_sub, submitted_by_name, submitted_at, approved_by_sub, approved_by_name, approved_at,"
    " posted_by_sub, posted_by_name, posted_at, cancelled_by_sub, cancelled_at, cancel_reason, row_version, updated_at"
)


# ====================================================================== validasi body
def _bad(field: str) -> ApiError:
    return ApiError(400, "invalid_request", {"field": field})


def req_int(
    body: Mapping[str, Any], name: str, low: int = 1, high: int = 2_147_483_647, *, optional: bool = False
) -> Any:
    value = body.get(name)
    if value is None and optional:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise _bad(name)
    return value


def req_str(body: Mapping[str, Any], name: str, max_len: int, *, optional: bool = False, min_len: int = 1) -> Any:
    value = body.get(name)
    if value is None and optional:
        return None
    if not isinstance(value, str) or not min_len <= len(value.strip()) <= max_len:
        raise _bad(name)
    return value.strip()


def req_decimal(body: Mapping[str, Any], name: str, *, high: Decimal = Decimal("1000000000")) -> Decimal:
    value = body.get(name)
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise _bad(name)
    try:
        dec = Decimal(str(value))
    except InvalidOperation:
        raise _bad(name) from None
    if not dec.is_finite() or dec <= 0 or dec > high:
        raise _bad(name)
    return q4(dec)


def req_date(value: Any, name: str) -> date:
    if not isinstance(value, str):
        raise _bad(name)
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise _bad(name) from None


def req_bool(body: Mapping[str, Any], name: str, default: bool | None = None) -> bool:
    value = body.get(name, default)
    if not isinstance(value, bool):
        raise _bad(name)
    return value


def req_int_list(body: Mapping[str, Any], name: str, max_items: int) -> list[int]:
    value = body.get(name) or []
    if not isinstance(value, list) or len(value) > max_items:
        raise _bad(name)
    out: list[int] = []
    for item in value:
        if not isinstance(item, int) or isinstance(item, bool) or not 1 <= item <= 2_147_483_647:
            raise _bad(name)
        out.append(item)
    return sorted(set(out))


def parse_uuid(value: str) -> str:
    if not _UUID_RE.fullmatch(value):
        raise ApiError(404, "not_found")
    return value


def pr_branches(actor: Actor, given: list[int]) -> list[int]:
    """SEC-235: `branch_ids` import PR wajib subset cabang sesi (403 `branch_forbidden`); kosong -> cabang sesi dengan
    batas sama seperti sumber transfer (maks 20), bukan "semua yang terlihat token ESB"."""
    if given:
        require_branches(actor, given)
        return given
    return source_branches(actor.branch_ids, 0)[0]


def delete_branches(rows: list[dict[str, Any]], plan_branch: int) -> set[int]:
    """Cabang dokumen outbox: transfer = asal + tujuan (`plan_transfer`), produksi = cabang rencana."""
    out: set[int] = set()
    for r in rows:
        pair = [b for b in (r.get("origin_branch_id"), r.get("dest_branch_id")) if b is not None]
        out.update(int(b) for b in (pair or [plan_branch]))
    return out


def esb_api_error(exc: EsbError) -> ApiError:
    """GET ESB sinkron gagal: token pemanggil ditolak (sesi sudah dibuang) -> 401 `session_expired`; lainnya -> 502."""
    if isinstance(exc, EsbTokenRejected):
        return ApiError(401, "session_expired")
    return ApiError(502, "esb_unavailable")


def to_json(value: Any) -> Any:
    """Nilai baris PG -> JSON (Decimal -> string 4 desimal, UUID/datetime -> string)."""
    if isinstance(value, dict):
        return {k: to_json(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [to_json(v) for v in value]
    if isinstance(value, Decimal):
        return str(q4(value))
    if isinstance(value, datetime | date):
        return value.isoformat()
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return str(value)


# ====================================================================== pratinjau posting (murni)
def build_preview(
    phase: str,
    plan: Mapping[str, Any],
    transfers: list[dict[str, Any]],
    transfer_lines: Mapping[Any, list[dict[str, Any]]],
    production_lines: list[dict[str, Any]],
) -> dict[str, Any]:
    """Dokumen yang AKAN dibuat + dampak stok + digest (§30.8). Angka = string 4 desimal (kanonik)."""
    docs: list[dict[str, Any]] = []
    impact: dict[tuple[int, str], list[Decimal]] = {}

    def add(loc: int, code: str, out_qty: Decimal, in_qty: Decimal) -> None:
        entry = impact.setdefault((loc, code), [Decimal(0), Decimal(0)])
        entry[0] += out_qty
        entry[1] += in_qty

    if phase == "transfers":
        for t in sorted(transfers, key=lambda x: x["id"]):
            if t["status"] != "approved":
                continue
            lines = sorted(transfer_lines.get(t["id"], []), key=lambda x: x["product_detail_id"])
            docs.append(
                {
                    "doc_kind": t["doc_type"],
                    "transfer_id": int(t["id"]),
                    "origin": {"branch_id": t["origin_branch_id"], "location_id": t["origin_location_id"]},
                    "dest": {"branch_id": t["dest_branch_id"], "location_id": t["dest_location_id"]},
                    "lines": [
                        {
                            "product_detail_id": ln["product_detail_id"],
                            "product_code": ln["product_code"],
                            "uom_name": ln["uom_name"],
                            "qty": str(q4(Decimal(ln["qty"]))),
                        }
                        for ln in lines
                    ],
                }
            )
            for ln in lines:
                qty = Decimal(ln["qty"])
                add(t["origin_location_id"], ln["product_code"], qty, Decimal(0))
                add(t["dest_location_id"], ln["product_code"], Decimal(0), qty)
    elif phase == "production":
        loc = int(plan["production_location_id"])
        for pl in sorted(production_lines, key=lambda x: (-x["level"], x["bom_id"])):
            materials = sorted(pl["materials"], key=lambda m: m["product_detail_id"])
            docs.append(
                {
                    "doc_kind": "simple_manufacturing",
                    "line_id": int(pl["id"]),
                    "level": pl["level"],
                    "bom_id": int(pl["bom_id"]),
                    "product_detail_id": int(pl["product_detail_id"]),
                    "product_code": pl["product_code"],
                    "qty": str(q4(Decimal(pl["qty"]))),
                    "origin": {"branch_id": plan["branch_id"], "location_id": loc},
                    "dest": {"branch_id": plan["branch_id"], "location_id": loc},
                    "lines": [
                        {
                            "product_detail_id": m["product_detail_id"],
                            "product_code": m["product_code"],
                            "qty": str(q4(Decimal(str(m["total_qty"])))),
                        }
                        for m in materials
                    ],
                }
            )
            for m in materials:
                add(loc, m["product_code"], Decimal(str(m["total_qty"])), Decimal(0))
            add(loc, pl["product_code"], Decimal(0), Decimal(pl["qty"]))
    else:
        raise _bad("phase")
    stock_impact = [
        {"location_id": loc, "product_code": code, "qty_out": str(q4(v[0])), "qty_in": str(q4(v[1]))}
        for (loc, code), v in sorted(impact.items())
    ]
    return {"phase": phase, "docs": docs, "stock_impact": stock_impact, "digest": preview_digest(docs)}


# ====================================================================== service
class Service:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        read_clients: Callable[[Actor], Any] | None,
        clock: Callable[[], datetime],
        catalog: BomCatalog | None = None,
        names: NameCache | None = None,
    ) -> None:
        self.settings = settings
        self.db = db
        self._clients = read_clients or (lambda _actor: None)
        self._clock = clock
        self._catalog = catalog or BomCatalog()
        self._names = names or NameCache()

    def _client(self, actor: Actor) -> Any:
        """Klien ESB bertoken sesi pemanggil; sesi hilang -> 401 `session_expired`."""
        client = self._clients(actor)
        if client is None:
            raise ApiError(401, "session_expired")
        return client

    # ------------------------------------------------------------------ nama (tampilan; gagal -> None)
    @staticmethod
    def _branch_name(actor: Actor, branch_id: Any) -> str | None:
        """Nama cabang dari `/branch/list/user` saat login (tanpa GET baru); cabang di luar sesi -> None."""
        try:
            return actor.branch_names.get(int(branch_id)) or None
        except (TypeError, ValueError):
            return None

    def _location_name(self, actor: Actor, branch_id: Any, location_id: Any) -> str | None:
        scope = cache_scope(actor.sub, actor.brand)
        return self._names.location_name(scope, self._clients(actor), branch_id, location_id)

    def _name_plan(self, actor: Actor, item: dict[str, Any]) -> None:
        item["branch_name"] = self._branch_name(actor, item.get("branch_id"))
        item["production_location_name"] = self._location_name(
            actor, item.get("branch_id"), item.get("production_location_id")
        )

    def _name_place(self, actor: Actor, place: dict[str, Any]) -> dict[str, Any]:
        out = dict(place)
        out["branch_name"] = self._branch_name(actor, place.get("branch_id"))
        out["location_name"] = self._location_name(actor, place.get("branch_id"), place.get("location_id"))
        return out

    @staticmethod
    def _contributors(tx: Tx, plan_id: Any) -> set[int]:
        """SEC-227: semua aktor yang pernah mengubah isi rencana (audit `CONTENT_ACTIONS`), tanpa DDL baru."""
        rows = tx.all(
            "SELECT DISTINCT actor_sub FROM prodplan.audit_event WHERE plan_id = %s AND action = ANY(%s)"
            " AND actor_sub IS NOT NULL",
            (plan_id, list(CONTENT_ACTIONS)),
        )
        return {int(r["actor_sub"]) for r in rows}

    # ------------------------------------------------------------------ util
    def _brand_ok(self, actor: Actor, brand: str) -> None:
        require_brand(actor, brand, [b.key for b in self.settings.brands])

    def _plan_for_update(self, tx: Tx, actor: Actor, plan_id: str) -> dict[str, Any]:
        sql = f"SELECT {PLAN_COLUMNS} FROM prodplan.plan WHERE id = %s FOR UPDATE"  # noqa: S608
        return self._visible(actor, tx.one(sql, (parse_uuid(plan_id),)))

    def _plan(self, tx: Tx, actor: Actor, plan_id: str) -> dict[str, Any]:
        sql = f"SELECT {PLAN_COLUMNS} FROM prodplan.plan WHERE id = %s"  # noqa: S608
        return self._visible(actor, tx.one(sql, (parse_uuid(plan_id),)))

    def _visible(self, actor: Actor, plan: dict[str, Any] | None) -> dict[str, Any]:
        """§30.17.6: env proses + brand sesi + cabang sesi; selain itu 404 (tidak membocorkan keberadaan)."""
        if (
            plan is None
            or plan["esb_env"] != self.settings.esb_env
            or plan["esb_brand"] != actor.brand
            or int(plan["branch_id"]) not in actor.branch_ids
        ):
            raise ApiError(404, "not_found")
        return plan

    @staticmethod
    def _bump(tx: Tx, plan_id: Any, extra_sql: str = "", params: tuple[Any, ...] = ()) -> int:
        row = tx.one(
            f"UPDATE prodplan.plan SET row_version = row_version + 1, updated_at = now(){extra_sql}"  # noqa: S608
            " WHERE id = %s RETURNING row_version",
            (*params, plan_id),
        )
        return int(row["row_version"]) if row else 0

    def _audit(self, tx: Tx, plan: Mapping[str, Any], actor: Actor, action: str, rid: str, **detail: Any) -> None:
        audit(tx, plan["company_code"], plan.get("id"), actor.sub, actor.name, action, to_json(detail), rid)

    def _bom_header(self, actor: Actor, bom_id: int) -> BomHeader | None:
        try:
            return self._catalog.header(cache_scope(actor.sub, actor.brand), self._client(actor), bom_id)
        except EsbError as exc:
            raise esb_api_error(exc) from None

    def search_boms(self, actor: Actor, query: Mapping[str, str]) -> dict[str, Any]:
        """`GET /v1/boms?brand=&q=&limit=` (keputusan lead G66; kontrak §30.8 diperbarui lead). `q` tidak dilog."""
        require_role(actor, ANY_ROLE)
        brand = query.get("brand", "")
        self._brand_ok(actor, brand)
        try:
            q = validate_search(query.get("q"))
        except ValueError:
            raise ApiError(422, "bad_input", {"field": "q"}) from None
        try:
            limit = int(query.get("limit") or 20)
        except ValueError:
            raise ApiError(422, "bad_input", {"field": "limit"}) from None
        if not 1 <= limit <= 50:
            raise ApiError(422, "bad_input", {"field": "limit"})
        try:
            items = self._catalog.search(cache_scope(actor.sub, brand), self._client(actor), q, limit)
        except EsbError as exc:
            raise esb_api_error(exc) from None
        return {"items": items}

    def _plan_view(self, tx: Tx, plan_id: Any) -> dict[str, Any]:
        plan = tx.one(f"SELECT {PLAN_COLUMNS} FROM prodplan.plan WHERE id = %s", (plan_id,))  # noqa: S608
        assert plan is not None
        return to_json(plan)

    # ------------------------------------------------------------------ meta & pack rules
    def meta(self, actor: Actor | None) -> dict[str, Any]:
        """§30.17.3: tanpa sesi -> `{esb_env}` saja (banner halaman masuk); dengan sesi -> brand sesi, `ck` disaring
        cabang sesi, tanpa `source_locations`."""
        if actor is None:
            return {"esb_env": self.settings.esb_env}
        require_role(actor, ANY_ROLE)
        brands = []
        for b in self.settings.brands:
            if b.key == actor.brand:
                ck = [
                    {
                        "branch_id": br,
                        "production_location_id": loc,
                        "branch_name": self._branch_name(actor, br),
                        "production_location_name": self._location_name(actor, br, loc),
                    }
                    for br in sorted(b.ck)
                    if br in actor.branch_ids
                    for loc in b.ck[br]
                ]
                brands.append({"esb_brand": b.key, "company_code": b.company_code, "ck": ck})
        return {
            "esb_env": self.settings.esb_env,
            "write_enabled": self.settings.write_enabled,
            "mfg_mode": self.settings.mfg_mode,
            "brands": brands,
        }

    def list_pack_rules(self, actor: Actor, query: Mapping[str, str]) -> dict[str, Any]:
        require_role(actor, ANY_ROLE)
        brand = query.get("brand", "")
        self._brand_ok(actor, brand)
        with self.db.tx() as tx:
            rows = tx.all(
                "SELECT id, company_code, esb_brand, product_code, product_detail_id, base_uom, pack_qty,"
                " opened_shelf_life_hours, notes, active, created_by_name, created_at, updated_at, row_version"
                " FROM prodplan.pack_rule WHERE esb_brand = %s ORDER BY product_code",
                (brand,),
            )
        return {"items": to_json(rows)}

    def put_pack_rule(self, actor: Actor, brand: str, product_code: str, body: Mapping[str, Any], rid: str) -> Any:
        require_role(actor, {PLANNER})
        self._brand_ok(actor, brand)
        if not _PRODUCT_CODE_RE.fullmatch(product_code):
            raise _bad("product_code")
        base_uom = req_str(body, "base_uom", 20)
        pack_qty = req_decimal(body, "pack_qty")
        shelf = req_int(body, "opened_shelf_life_hours", 1, 720, optional=True)
        notes = req_str(body, "notes", 500, optional=True, min_len=0)
        active = req_bool(body, "active")
        company = self.settings.brand(brand).company_code  # type: ignore[union-attr]
        with self.db.tx() as tx:
            current = tx.one(
                "SELECT id, row_version FROM prodplan.pack_rule WHERE esb_brand = %s AND product_code = %s FOR UPDATE",
                (brand, product_code),
            )
            if current is None:
                row = tx.one(
                    "INSERT INTO prodplan.pack_rule (company_code, esb_brand, product_code, base_uom, pack_qty,"
                    " opened_shelf_life_hours, notes, active, created_by_sub, created_by_name, updated_by_sub)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                    (
                        company,
                        brand,
                        product_code,
                        base_uom,
                        pack_qty,
                        shelf,
                        notes,
                        active,
                        actor.sub,
                        actor.name,
                        actor.sub,
                    ),
                )
            else:
                check_version(int(current["row_version"]), body.get("row_version"))
                row = tx.one(
                    "UPDATE prodplan.pack_rule SET base_uom = %s, pack_qty = %s, opened_shelf_life_hours = %s,"
                    " notes = %s, active = %s, updated_by_sub = %s, updated_at = now(), row_version = row_version + 1"
                    " WHERE id = %s RETURNING *",
                    (base_uom, pack_qty, shelf, notes, active, actor.sub, current["id"]),
                )
            audit(
                tx,
                company,
                None,
                actor.sub,
                actor.name,
                "pack_rule_saved",
                {"product_code": product_code, "pack_qty": str(pack_qty), "active": active},
                rid,
            )
        return to_json(row)

    # ------------------------------------------------------------------ rencana
    def list_plans(self, actor: Actor, query: Mapping[str, str]) -> dict[str, Any]:
        require_role(actor, ANY_ROLE)
        brand = query.get("brand", "") or actor.brand  # tanpa filter brand (insight-web) = brand sesi
        self._brand_ok(actor, brand)
        where = ["esb_env = %s", "esb_brand = %s", "branch_id = ANY(%s)"]  # §30.17.6: hanya cabang sesi
        params: list[Any] = [self.settings.esb_env, brand, sorted(actor.branch_ids)]
        if query.get("from"):
            where.append("production_date >= %s")
            params.append(req_date(query["from"], "from"))
        if query.get("to"):
            where.append("production_date <= %s")
            params.append(req_date(query["to"], "to"))
        if query.get("status"):
            if query["status"] not in ("draft", "submitted", "approved", "posted", "cancelled"):
                raise _bad("status")
            where.append("status = %s")
            params.append(query["status"])
        try:
            limit = int(query.get("limit") or 50)
            offset = int(query.get("offset") or 0)
        except ValueError:
            raise _bad("limit") from None
        if not 1 <= limit <= 200 or not 0 <= offset <= 100_000:
            raise _bad("limit")
        clause = " AND ".join(where)  # potongan SQL tetap, nilai lewat parameter
        with self.db.tx() as tx:
            count = tx.one(f"SELECT count(*) AS n FROM prodplan.plan WHERE {clause}", params)  # noqa: S608
            items = tx.all(
                f"SELECT {PLAN_COLUMNS} FROM prodplan.plan WHERE {clause}"  # noqa: S608
                " ORDER BY production_date DESC, plan_no DESC LIMIT %s OFFSET %s",
                [*params, limit, offset],
            )
        out_items = to_json(items)
        for item in out_items:
            self._name_plan(actor, item)
        return {"items": out_items, "count": int((count or {}).get("n") or 0)}

    def create_plan(self, actor: Actor, body: Mapping[str, Any], rid: str) -> Any:
        require_role(actor, {PLANNER})
        brand = req_str(body, "brand", 16)
        self._brand_ok(actor, brand)
        branch_id = req_int(body, "branch_id")
        loc_id = req_int(body, "production_location_id")
        prod_date = req_date(body.get("production_date"), "production_date")
        notes = req_str(body, "notes", 1000, optional=True, min_len=0)
        cfg = self.settings.brand(brand)
        if cfg is None or not cfg.is_ck(branch_id, loc_id) or branch_id not in actor.branch_ids:
            raise ApiError(422, "invalid_branch")  # §30.17.6: branch_ids sesi ∩ PRODPLAN_CK_BRANCHES
        today = self._clock().date()
        if not today - timedelta(days=30) <= prod_date <= today + timedelta(days=60):
            raise _bad("production_date")
        with self.db.tx() as tx:
            key = f"{self.settings.esb_env}:{cfg.company_code}:{prod_date.isoformat()}"
            tx.one("SELECT pg_advisory_xact_lock(hashtext(%s)) AS ok", (key,))
            n = tx.one(
                "SELECT count(*) AS n FROM prodplan.plan WHERE esb_env = %s AND company_code = %s"
                " AND production_date = %s",
                (self.settings.esb_env, cfg.company_code, prod_date),
            )
            number = plan_no(cfg.company_code, prod_date, int((n or {}).get("n") or 0) + 1)
            row = tx.one(
                "INSERT INTO prodplan.plan (plan_no, company_code, esb_brand, esb_env, branch_id,"  # noqa: S608
                " production_location_id, production_date, notes, created_by_sub, created_by_name)"
                f" VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING {PLAN_COLUMNS}",
                (
                    number,
                    cfg.company_code,
                    brand,
                    self.settings.esb_env,
                    branch_id,
                    loc_id,
                    prod_date,
                    notes,
                    actor.sub,
                    actor.name,
                ),
            )
            assert row is not None
            self._audit(tx, row, actor, "plan_created", rid, plan_no=number)
        out = to_json(row)
        self._name_plan(actor, out)
        return out

    def get_plan(self, actor: Actor, plan_id: str) -> dict[str, Any]:
        require_role(actor, ANY_ROLE)
        with self.db.tx() as tx:
            plan = self._plan(tx, actor, plan_id)
            pid = plan["id"]
            out = to_json(plan)
            out["demand"] = to_json(
                tx.all(
                    "SELECT id, product_detail_id, product_code, product_name, bom_id, uom_name, qty, source, pr_nums,"
                    " suggestion_id FROM prodplan.plan_demand WHERE plan_id = %s ORDER BY product_code, source",
                    (pid,),
                )
            )
            out["unmapped"] = to_json(
                tx.all(
                    "SELECT pr_num, product_code, product_name, qty, reason FROM prodplan.plan_unmapped"
                    " WHERE plan_id = %s ORDER BY product_code",
                    (pid,),
                )
            )
            out["production_lines"] = to_json(
                tx.all(
                    "SELECT id, level, bom_id, product_detail_id, product_code, product_name, uom_name, qty, is_demand,"
                    " materials FROM prodplan.plan_production_line WHERE plan_id = %s ORDER BY level DESC, bom_id",
                    (pid,),
                )
            )
            out["requirements"] = to_json(
                tx.all(
                    "SELECT product_detail_id, product_code, product_name, uom_name, need_qty, pack_qty, packs,"
                    " leftover_qty, need_eff_qty, stock_prod_qty, transfer_qty, shortage_qty, flags"
                    " FROM prodplan.plan_requirement WHERE plan_id = %s ORDER BY product_code",
                    (pid,),
                )
            )
            out["suggestions"] = to_json(
                tx.all(
                    "SELECT id, material_detail_id, bom_id, product_detail_id, extra_units, uses_qty, leftover_qty,"
                    " status, decided_by_sub, decided_at FROM prodplan.plan_suggestion WHERE plan_id = %s ORDER BY id",
                    (pid,),
                )
            )
            # Nama produk untuk UI (usulan batch + baris transfer): dari hasil hitung yang sama, tanpa query baru.
            lookup = {
                int(r["product_detail_id"]): (r["product_code"], r["product_name"])
                for r in [*out["production_lines"], *out["requirements"]]
            }
            for s in out["suggestions"]:
                s["material_code"], s["material_name"] = lookup.get(int(s["material_detail_id"]), (None, None))
                s["product_code"], s["product_name"] = lookup.get(int(s["product_detail_id"]), (None, None))
            transfers = tx.all(
                "SELECT id, doc_type, origin_branch_id, origin_location_id, dest_branch_id, dest_location_id, status,"
                " decided_by_sub, decided_by_name, decided_at, reject_reason FROM prodplan.plan_transfer"
                " WHERE plan_id = %s ORDER BY id",
                (pid,),
            )
            lines = tx.all(
                "SELECT l.transfer_id, l.product_detail_id, l.product_code, l.uom_name, l.qty"
                " FROM prodplan.plan_transfer_line l JOIN prodplan.plan_transfer t ON t.id = l.transfer_id"
                " WHERE t.plan_id = %s ORDER BY l.transfer_id, l.product_detail_id",
                (pid,),
            )
            for ln in lines:
                ln["product_name"] = lookup.get(int(ln["product_detail_id"]), (None, None))[1]
            for t in transfers:
                t["lines"] = [ln for ln in lines if ln["transfer_id"] == t["id"]]
            out["transfers"] = to_json(transfers)
            out["outbox"] = to_json(
                tx.all(
                    "SELECT id, seq, phase, doc_kind, status, attempts, esb_doc_number, esb_status_id, last_error_code,"
                    " sent_at, confirmed_at, deleted_at FROM prodplan.outbox WHERE plan_id = %s ORDER BY seq",
                    (pid,),
                )
            )
            snap = tx.one("SELECT fetched_at FROM prodplan.stock_snapshot WHERE id = %s", (plan["stock_snapshot_id"],))
            out["stock_fetched_at"] = to_json(snap["fetched_at"]) if snap else None
        out["limitations"] = list(LIMITATIONS)
        out["posting_state"] = posting_state(out["outbox"])
        # Nama dari ESB (cache) di luar transaksi DB; gagal -> null.
        self._name_plan(actor, out)
        for t in out["transfers"]:
            t["origin_branch_name"] = self._branch_name(actor, t["origin_branch_id"])
            t["origin_location_name"] = self._location_name(actor, t["origin_branch_id"], t["origin_location_id"])
            t["dest_branch_name"] = self._branch_name(actor, t["dest_branch_id"])
            t["dest_location_name"] = self._location_name(actor, t["dest_branch_id"], t["dest_location_id"])
        return out

    def put_demand(self, actor: Actor, plan_id: str, body: Mapping[str, Any], rid: str) -> Any:
        require_role(actor, {PLANNER})
        lines = body.get("lines")
        if not isinstance(lines, list) or len(lines) > MAX_DEMAND_LINES:
            raise _bad("lines")
        parsed: list[dict[str, Any]] = []
        seen: set[tuple[int, str]] = set()
        for idx, line in enumerate(lines):
            if not isinstance(line, Mapping):
                raise _bad(f"lines[{idx}]")
            source = line.get("source")
            if source not in ("manual", "pr"):
                raise _bad(f"lines[{idx}].source")
            pr_nums = line.get("pr_nums") or []
            if (
                not isinstance(pr_nums, list)
                or len(pr_nums) > 50
                or not all(isinstance(n, str) and 1 <= len(n) <= 40 for n in pr_nums)
            ):
                raise _bad(f"lines[{idx}].pr_nums")
            item = {
                "product_detail_id": req_int(line, "product_detail_id"),
                "bom_id": req_int(line, "bom_id"),
                "qty": req_decimal(line, "qty"),
                "source": source,
                "pr_nums": sorted(set(pr_nums)),
            }
            key = (item["product_detail_id"], source)
            if key in seen:
                raise _bad(f"lines[{idx}].product_detail_id")
            seen.add(key)
            parsed.append(item)
        with self.db.tx() as tx:
            plan = self._plan_for_update(tx, actor, plan_id)
            require_status(plan["status"], {"draft"})
            check_version(int(plan["row_version"]), body.get("row_version"))
        for idx, item in enumerate(parsed):  # GET ESB (header BOM, di-cache) di luar transaksi
            header = self._bom_header(actor, item["bom_id"])
            if header is None or header.product_detail_id != item["product_detail_id"]:
                raise ApiError(422, "bom_not_assembly", {"field": f"lines[{idx}].bom_id"})
            item["header"] = header
        with self.db.tx() as tx:
            plan = self._plan_for_update(tx, actor, plan_id)
            require_status(plan["status"], {"draft"})
            check_version(int(plan["row_version"]), body.get("row_version"))
            tx.exec("DELETE FROM prodplan.plan_demand WHERE plan_id = %s AND source <> 'suggestion'", (plan["id"],))
            for item in parsed:
                h: BomHeader = item["header"]
                tx.exec(
                    "INSERT INTO prodplan.plan_demand (plan_id, product_detail_id, product_code, product_name, bom_id,"
                    " uom_name, qty, source, pr_nums, created_by_sub) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        plan["id"],
                        item["product_detail_id"],
                        h.product_code or "?",
                        h.product_name or "?",
                        item["bom_id"],
                        h.uom_name or "?",
                        item["qty"],
                        item["source"],
                        item["pr_nums"],
                        actor.sub,
                    ),
                )
            self._bump(tx, plan["id"], ", calc_status = 'stale'")
            self._audit(tx, plan, actor, "demand_saved", rid, lines=len(parsed))
            return self._plan_view(tx, plan["id"])

    def _enqueue(
        self, actor: Actor, plan_id: str, kind: str, params: dict[str, Any], rid: str, row_version: Any = None
    ) -> dict[str, Any]:
        from psycopg import errors

        try:
            with self.db.tx() as tx:
                plan = self._plan_for_update(tx, actor, plan_id)
                if kind == "calculate" and plan["status"] == "approved":
                    # QA-A: segarkan stok rencana `approved` (P atau W); tidak boleh bila sudah ada outbox.
                    require_role(actor, ANY_ROLE)
                    if tx.one("SELECT 1 AS x FROM prodplan.outbox WHERE plan_id = %s LIMIT 1", (plan["id"],)):
                        raise ApiError(409, "invalid_state")
                    params = {**params, "refresh": True}
                else:
                    require_role(actor, {PLANNER})
                    require_status(plan["status"], {"draft"})
                if row_version is not None or kind == "calculate":
                    check_version(int(plan["row_version"]), row_version)
                job = tx.one(
                    "INSERT INTO prodplan.job (company_code, plan_id, kind, params, requested_by_sub)"
                    " VALUES (%s,%s,%s,%s,%s) RETURNING id",
                    (plan["company_code"], plan["id"], kind, jsonb(params), actor.sub),
                )
                assert job is not None
                self._audit(tx, plan, actor, f"job_{kind}", rid, job_id=str(job["id"]))
        except errors.UniqueViolation:
            raise ApiError(409, "job_running") from None
        return {"job_id": str(job["id"])}

    def import_pr(self, actor: Actor, plan_id: str, body: Mapping[str, Any], rid: str) -> Any:
        require_role(actor, {PLANNER})
        start = req_date(body.get("required_date_from"), "required_date_from")
        end = req_date(body.get("required_date_to"), "required_date_to")
        if end < start or (end - start).days > 31:
            raise _bad("required_date_to")
        params = {
            "required_date_from": start.isoformat(),
            "required_date_to": end.isoformat(),
            "branch_ids": pr_branches(actor, req_int_list(body, "branch_ids", 50)),
            "status_ids": req_int_list(body, "status_ids", 10) if "status_ids" in body else [3, 30],
        }
        return self._enqueue(actor, plan_id, "pr_import", params, rid)

    def calculate(self, actor: Actor, plan_id: str, body: Mapping[str, Any], rid: str) -> Any:
        """`draft` (P): hitung penuh. `approved` (P atau W): mode refresh (QA-A), lihat `_save_calculation`."""
        require_role(actor, ANY_ROLE)
        version = body.get("row_version")
        if not isinstance(version, int) or isinstance(version, bool):
            raise _bad("row_version")
        return self._enqueue(actor, plan_id, "calculate", {"row_version": version}, rid, version)

    def get_job(self, actor: Actor, job_id: str) -> dict[str, Any]:
        require_role(actor, ANY_ROLE)
        with self.db.tx() as tx:
            job = tx.one(
                "SELECT id, plan_id, kind, status, error_code, result FROM prodplan.job WHERE id = %s",
                (parse_uuid(job_id),),
            )
            if job is None:
                raise ApiError(404, "not_found")
            self._plan(tx, actor, str(job["plan_id"]))
        out = {"status": job["status"], "kind": job["kind"]}
        if job["error_code"]:
            out["error_code"] = job["error_code"]
        if job["result"] is not None:
            out["result"] = to_json(job["result"])
        return out

    def decide_suggestion(
        self, actor: Actor, plan_id: str, sid: str, decision: str, body: Mapping[str, Any], rid: str
    ) -> Any:
        require_role(actor, {PLANNER})
        if not sid.isdigit():
            raise ApiError(404, "not_found")
        with self.db.tx() as tx:
            plan = self._plan_for_update(tx, actor, plan_id)
            require_status(plan["status"], {"draft"})
            check_version(int(plan["row_version"]), body.get("row_version"))
            sug = tx.one(
                "SELECT * FROM prodplan.plan_suggestion WHERE id = %s AND plan_id = %s FOR UPDATE",
                (int(sid), plan["id"]),
            )
            if sug is None:
                raise ApiError(404, "not_found")
            if decision == "reject" and sug["status"] == "accepted":
                # QA-D: batalkan usulan yang sudah diterima -> kurangi/hapus baris permintaan `suggestion` terkait.
                self._undo_accepted_suggestion(tx, plan["id"], sug)
                tx.exec(
                    "UPDATE prodplan.plan_suggestion SET status = 'rejected', decided_by_sub = %s, decided_at = now()"
                    " WHERE id = %s",
                    (actor.sub, sug["id"]),
                )
                self._bump(tx, plan["id"], ", calc_status = 'stale'")
                self._audit(tx, plan, actor, "suggestion_reject", rid, suggestion_id=int(sug["id"]), undo=True)
                return self._plan_view(tx, plan["id"])
            require_status(sug["status"], {"open"})
            if decision == "reject":
                tx.exec(
                    "UPDATE prodplan.plan_suggestion SET status = 'rejected', decided_by_sub = %s, decided_at = now()"
                    " WHERE id = %s",
                    (actor.sub, sug["id"]),
                )
                self._bump(tx, plan["id"])
            else:
                if plan["calc_status"] != "fresh":
                    raise ApiError(409, "stale_calculation")  # usulan basi tidak boleh diterima (QA-D)
                line = tx.one(
                    "SELECT product_code, product_name, uom_name FROM prodplan.plan_production_line"
                    " WHERE plan_id = %s AND bom_id = %s",
                    (plan["id"], sug["bom_id"]),
                )
                if line is None:
                    raise ApiError(409, "stale_calculation")
                tx.exec(
                    "UPDATE prodplan.plan_suggestion SET status = 'accepted', decided_by_sub = %s, decided_at = now()"
                    " WHERE id = %s",
                    (actor.sub, sug["id"]),
                )
                # Usulan = alternatif: usulan terbuka lain untuk bahan yang sama -> superseded (§30.5 langkah 4).
                tx.exec(
                    "UPDATE prodplan.plan_suggestion SET status = 'superseded', decided_by_sub = %s, decided_at = now()"
                    " WHERE plan_id = %s AND material_detail_id = %s AND status = 'open' AND id <> %s",
                    (actor.sub, plan["id"], sug["material_detail_id"], sug["id"]),
                )
                tx.exec(
                    "INSERT INTO prodplan.plan_demand (plan_id, product_detail_id, product_code, product_name, bom_id,"
                    " uom_name, qty, source, suggestion_id, created_by_sub)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,'suggestion',%s,%s)"
                    " ON CONFLICT (plan_id, product_detail_id, source)"
                    " DO UPDATE SET qty = prodplan.plan_demand.qty + EXCLUDED.qty,"
                    " suggestion_id = EXCLUDED.suggestion_id",
                    (
                        plan["id"],
                        sug["product_detail_id"],
                        line["product_code"],
                        line["product_name"],
                        sug["bom_id"],
                        line["uom_name"],
                        Decimal(sug["extra_units"]),
                        sug["id"],
                        actor.sub,
                    ),
                )
                self._bump(tx, plan["id"], ", calc_status = 'stale'")
            self._audit(tx, plan, actor, f"suggestion_{decision}", rid, suggestion_id=int(sug["id"]))
            return self._plan_view(tx, plan["id"])

    @staticmethod
    def _undo_accepted_suggestion(tx: Tx, plan_id: Any, sug: Mapping[str, Any]) -> None:
        demand = tx.one(
            "SELECT id, qty FROM prodplan.plan_demand WHERE plan_id = %s AND product_detail_id = %s"
            " AND source = 'suggestion' FOR UPDATE",
            (plan_id, sug["product_detail_id"]),
        )
        if demand is None:
            return
        remaining = Decimal(demand["qty"]) - Decimal(sug["extra_units"])
        if remaining <= 0:
            tx.exec("DELETE FROM prodplan.plan_demand WHERE id = %s", (demand["id"],))
        else:
            tx.exec("UPDATE prodplan.plan_demand SET qty = %s WHERE id = %s", (q4(remaining), demand["id"]))

    def submit(self, actor: Actor, plan_id: str, body: Mapping[str, Any], rid: str) -> Any:
        require_role(actor, {PLANNER})
        with self.db.tx() as tx:
            plan = self._plan_for_update(tx, actor, plan_id)
            target = transition("submit", plan["status"])
            check_version(int(plan["row_version"]), body.get("row_version"))
            demand = tx.one("SELECT count(*) AS n FROM prodplan.plan_demand WHERE plan_id = %s", (plan["id"],))
            if not demand or int(demand["n"]) == 0:
                raise ApiError(409, "empty_plan")  # QA-E: tanpa permintaan -> pesan yang tepat, bukan "hitung ulang"
            if plan["calc_status"] != "fresh":
                raise ApiError(409, "stale_calculation")
            lines = tx.one("SELECT count(*) AS n FROM prodplan.plan_production_line WHERE plan_id = %s", (plan["id"],))
            if not lines or int(lines["n"]) == 0:
                raise ApiError(409, "empty_plan")  # QA-E
            self._check_snapshot_age(tx, plan)
            self._bump(
                tx,
                plan["id"],
                ", status = %s, submitted_by_sub = %s, submitted_by_name = %s, submitted_at = now()",
                (target, actor.sub, actor.name),
            )
            self._audit(tx, plan, actor, "plan_submitted", rid)
            return self._plan_view(tx, plan["id"])

    def _check_snapshot_age(self, tx: Tx, plan: Mapping[str, Any]) -> None:
        snap = None
        if plan["stock_snapshot_id"] is not None:
            snap = tx.one("SELECT fetched_at FROM prodplan.stock_snapshot WHERE id = %s", (plan["stock_snapshot_id"],))
        check_stock_age(snap["fetched_at"] if snap else None, self._clock(), self.settings.stock_max_age_minutes)

    def decide_transfer(
        self, actor: Actor, plan_id: str, tid: str, decision: str, body: Mapping[str, Any], rid: str
    ) -> Any:
        require_role(actor, {WAREHOUSE})
        if not tid.isdigit():
            raise ApiError(404, "not_found")
        reason = req_str(body, "reason", 500, optional=decision == "approve")
        with self.db.tx() as tx:
            plan = self._plan_for_update(tx, actor, plan_id)
            require_status(plan["status"], {"submitted"})
            check_version(int(plan["row_version"]), body.get("row_version"))
            check_sod(actor.sub, plan["created_by_sub"], plan["submitted_by_sub"], self._contributors(tx, plan["id"]))
            row = tx.one(
                "SELECT id, origin_branch_id, dest_branch_id FROM prodplan.plan_transfer"
                " WHERE id = %s AND plan_id = %s FOR UPDATE",
                (int(tid), plan["id"]),
            )
            if row is None:
                raise ApiError(404, "not_found")
            require_branches(actor, (row["origin_branch_id"], row["dest_branch_id"]))  # §30.17.6: asal DAN tujuan
            tx.exec(
                "UPDATE prodplan.plan_transfer SET status = %s, decided_by_sub = %s, decided_by_name = %s,"
                " decided_at = now(), reject_reason = %s WHERE id = %s",
                (
                    "approved" if decision == "approve" else "rejected",
                    actor.sub,
                    actor.name,
                    reason if decision == "reject" else None,
                    row["id"],
                ),
            )
            self._bump(tx, plan["id"])
            self._audit(tx, plan, actor, f"transfer_{decision}", rid, transfer_id=int(row["id"]), reason=reason)
            return self._plan_view(tx, plan["id"])

    def approve(self, actor: Actor, plan_id: str, body: Mapping[str, Any], rid: str) -> Any:
        require_role(actor, {WAREHOUSE})
        with self.db.tx() as tx:
            plan = self._plan_for_update(tx, actor, plan_id)
            target = transition("approve", plan["status"])
            check_version(int(plan["row_version"]), body.get("row_version"))
            require_branches(actor, (plan["branch_id"],))
            check_sod(actor.sub, plan["created_by_sub"], plan["submitted_by_sub"], self._contributors(tx, plan["id"]))
            pending = tx.one(
                "SELECT count(*) AS n FROM prodplan.plan_transfer WHERE plan_id = %s AND status = 'proposed'",
                (plan["id"],),
            )
            if pending and int(pending["n"]) > 0:
                raise ApiError(409, "transfers_undecided")
            self._bump(
                tx,
                plan["id"],
                ", status = %s, approved_by_sub = %s, approved_by_name = %s, approved_at = now()",
                (target, actor.sub, actor.name),
            )
            self._audit(tx, plan, actor, "plan_approved", rid)
            return self._plan_view(tx, plan["id"])

    def return_to_draft(self, actor: Actor, plan_id: str, body: Mapping[str, Any], rid: str) -> Any:
        require_role(actor, ANY_ROLE)
        reason = req_str(body, "reason", 500)
        with self.db.tx() as tx:
            plan = self._plan_for_update(tx, actor, plan_id)
            target = transition("return_to_draft", plan["status"])
            check_version(int(plan["row_version"]), body.get("row_version"))
            tx.exec(
                "UPDATE prodplan.plan_transfer SET status = 'proposed', decided_by_sub = NULL, decided_by_name = NULL,"
                " decided_at = NULL, reject_reason = NULL WHERE plan_id = %s",
                (plan["id"],),
            )
            self._bump(
                tx,
                plan["id"],
                ", status = %s, submitted_by_sub = NULL, submitted_by_name = NULL, submitted_at = NULL",
                (target,),
            )
            self._audit(tx, plan, actor, "plan_returned", rid, reason=reason)
            return self._plan_view(tx, plan["id"])

    # ------------------------------------------------------------------ posting
    def _preview(self, tx: Tx, plan: Mapping[str, Any], phase: str) -> dict[str, Any]:
        transfers = tx.all("SELECT * FROM prodplan.plan_transfer WHERE plan_id = %s", (plan["id"],))
        lines = tx.all(
            "SELECT l.* FROM prodplan.plan_transfer_line l JOIN prodplan.plan_transfer t ON t.id = l.transfer_id"
            " WHERE t.plan_id = %s",
            (plan["id"],),
        )
        by_transfer: dict[Any, list[dict[str, Any]]] = {}
        for ln in lines:
            by_transfer.setdefault(ln["transfer_id"], []).append(ln)
        production = tx.all("SELECT * FROM prodplan.plan_production_line WHERE plan_id = %s", (plan["id"],))
        return build_preview(phase, plan, transfers, by_transfer, production)

    def post_preview(self, actor: Actor, plan_id: str, query: Mapping[str, str]) -> dict[str, Any]:
        require_role(actor, ANY_ROLE)
        phase = query.get("phase", "")
        if phase not in ("transfers", "production"):
            raise _bad("phase")
        with self.db.tx() as tx:
            plan = self._plan(tx, actor, plan_id)
            require_status(plan["status"], {"approved", "posted"})
            preview = self._preview(tx, plan, phase)
        # Nama ditambahkan SETELAH digest dihitung (digest tidak bergantung pada nama/ketersediaan ESB).
        preview["docs"] = [
            {**d, "origin": self._name_place(actor, d["origin"]), "dest": self._name_place(actor, d["dest"])}
            for d in preview["docs"]
        ]
        return preview

    def _transfers_authorized(self, actor: Actor, plan_id: str) -> None:
        """Fase produksi: semua outbox transfer `confirmed` + GET ulang simple-transfer statusID=3 (L-256: GTR tak)."""
        with self.db.tx() as tx:
            plan = self._plan(tx, actor, plan_id)
            approved = tx.one(
                "SELECT count(*) AS n FROM prodplan.plan_transfer WHERE plan_id = %s AND status = 'approved'",
                (plan["id"],),
            )
            rows = tx.all(
                "SELECT doc_kind, status, esb_doc_number FROM prodplan.outbox WHERE plan_id = %s"
                " AND phase = 'transfers' ORDER BY seq",
                (plan["id"],),
            )
        n_approved = int((approved or {}).get("n") or 0)
        if n_approved == 0:
            return
        if len(rows) < n_approved or any(r["status"] != "confirmed" for r in rows):
            raise ApiError(
                409,
                "transfers_not_authorized",
                {"doc_numbers": [r["esb_doc_number"] for r in rows if r["esb_doc_number"]]},
            )
        client = self._client(actor)
        not_ok: list[str] = []
        for r in rows:
            if r["doc_kind"] != "simple_transfer":
                continue
            try:
                doc = client.get_doc("simple_transfer", r["esb_doc_number"])
            except EsbError as exc:
                raise esb_api_error(exc) from None
            if doc_status_id(doc) != 3:
                not_ok.append(r["esb_doc_number"])
        if not_ok:
            raise ApiError(409, "transfers_not_authorized", {"doc_numbers": not_ok})

    def post(self, actor: Actor, plan_id: str, body: Mapping[str, Any], rid: str) -> Any:
        require_role(actor, ANY_ROLE)
        phase = body.get("phase")
        if phase not in ("transfers", "production"):
            raise _bad("phase")
        digest = req_str(body, "digest", 64)
        accept_shortage = req_bool(body, "accept_shortage", False)
        if not self.settings.write_enabled:
            raise ApiError(409, "write_disabled")
        if phase == "production":
            self._transfers_authorized(actor, plan_id)  # GET ESB di luar transaksi
        with self.db.tx() as tx:
            plan = self._plan_for_update(tx, actor, plan_id)
            require_status(plan["status"], {"approved"})
            check_version(int(plan["row_version"]), body.get("row_version"))
            check_date_window(plan["production_date"], self._clock(), self.settings.date_window)
            self._check_snapshot_age(tx, plan)
            preview = self._preview(tx, plan, phase)
            if preview["digest"] != digest:
                raise ApiError(409, "digest_mismatch")
            # §30.17.6: pemicu memegang semua cabang asal/tujuan dokumen fase ini + `doc_insert` semua doc_kind-nya.
            require_branches(actor, {d[k]["branch_id"] for d in preview["docs"] for k in ("origin", "dest")})
            require_doc_access(actor, insert_kinds={d["doc_kind"] for d in preview["docs"]})
            existing = tx.all("SELECT seq, phase FROM prodplan.outbox WHERE plan_id = %s", (plan["id"],))
            if any(r["phase"] == phase for r in existing):
                raise ApiError(409, "invalid_state")
            if len(existing) + len(preview["docs"]) > self.settings.post_max_docs:
                raise ApiError(409, "too_many_docs")
            if phase == "production":
                short = tx.one(
                    "SELECT count(*) AS n FROM prodplan.plan_requirement WHERE plan_id = %s AND shortage_qty > 0",
                    (plan["id"],),
                )
                if short and int(short["n"]) > 0 and not accept_shortage:
                    raise ApiError(409, "shortage_open")
            if not preview["docs"]:
                raise ApiError(409, "invalid_state")
            seq = max((int(r["seq"]) for r in existing), default=0)
            outbox_ids: list[str] = []
            for doc in preview["docs"]:
                seq += 1
                marker = new_marker()
                try:
                    body_esb = self._esb_body(plan, doc, marker)
                except InvalidQuantity:
                    raise ApiError(422, "invalid_quantity") from None  # SEC-231: tidak pernah qty <= 0 ke ESB
                row = tx.one(
                    "INSERT INTO prodplan.outbox (company_code, esb_brand, esb_env, plan_id, seq, phase, doc_kind,"
                    " source_transfer_id, source_line_id, marker, request_body, created_by_sub, run_as_sub)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (
                        plan["company_code"],
                        plan["esb_brand"],
                        plan["esb_env"],
                        plan["id"],
                        seq,
                        phase,
                        doc["doc_kind"],
                        doc.get("transfer_id"),
                        doc.get("line_id"),
                        marker,
                        jsonb(body_esb),
                        actor.sub,
                        actor.sub,  # run_as_sub = pemicu (§30.17.9): worker memakai token sesi aktor ini
                    ),
                )
                assert row is not None
                outbox_ids.append(str(row["id"]))
            if phase == "production":
                target = transition("post_production", plan["status"])
                self._bump(
                    tx,
                    plan["id"],
                    ", status = %s, posted_by_sub = %s, posted_by_name = %s, posted_at = now()",
                    (target, actor.sub, actor.name),
                )
            else:
                self._bump(tx, plan["id"])
            self._audit(
                tx,
                plan,
                actor,
                f"post_{phase}",
                rid,
                digest=digest,
                docs=len(outbox_ids),
                accept_shortage=accept_shortage,
            )
        return {"outbox_ids": outbox_ids}

    def _esb_body(self, plan: Mapping[str, Any], doc: Mapping[str, Any], marker: str) -> dict[str, Any]:
        day = plan["production_date"]
        lines = [{"product_detail_id": ln["product_detail_id"], "qty": ln["qty"]} for ln in doc["lines"]]
        if doc["doc_kind"] == "simple_transfer":
            return simple_transfer_body(doc["origin"]["location_id"], doc["dest"]["location_id"], day, marker, lines)
        if doc["doc_kind"] == "goods_transfer_request":
            return goods_transfer_request_body(
                doc["origin"]["branch_id"], doc["dest"]["branch_id"], doc["origin"]["location_id"], day, marker, lines
            )
        line = {
            "bom_id": doc["bom_id"],
            "product_detail_id": doc["product_detail_id"],
            "qty": doc["qty"],
            "materials": [{"product_detail_id": m["product_detail_id"], "total_qty": m["qty"]} for m in doc["lines"]],
        }
        return manufacturing_body(
            self.settings.mfg_mode, plan["branch_id"], plan["production_location_id"], day, marker, line
        )

    def cancel(self, actor: Actor, plan_id: str, body: Mapping[str, Any], rid: str) -> Any:
        require_role(actor, {PLANNER})
        reason = req_str(body, "reason", 500)
        with self.db.tx() as tx:
            plan = self._plan_for_update(tx, actor, plan_id)
            target = transition("cancel", plan["status"])
            check_version(int(plan["row_version"]), body.get("row_version"))
            # SEC-230: kunci SEMUA baris outbox rencana sebelum menilai status. Claim worker memakai FOR UPDATE SKIP
            # LOCKED + syarat rencana belum `cancelled`, jadi tidak ada baris yang lolos ke `sending` di sela-sela.
            rows = tx.all(
                "SELECT o.id, o.status, o.doc_kind, o.attempts, o.esb_doc_number, t.origin_branch_id, t.dest_branch_id"
                " FROM prodplan.outbox o LEFT JOIN prodplan.plan_transfer t ON t.id = o.source_transfer_id"
                " WHERE o.plan_id = %s ORDER BY o.seq FOR UPDATE OF o",
                (plan["id"],),
            )
            if any(r["status"] in ("sending", "sent", "unknown", "delete_pending", "deleting") for r in rows):
                raise ApiError(409, "invalid_state")
            # §30.17.9: `needs_login` yang mungkin sudah tercipta di ESB (pernah dikirim / punya nomor) = tidak pasti.
            uncertain = [r for r in rows if r["status"] == "needs_login" and (r["attempts"] or r["esb_doc_number"])]
            if uncertain:
                raise ApiError(409, "invalid_state")
            # §30.17.6 + SEC-241: pembatal memegang cabang asal/tujuan + `doc_delete` dokumen ESB yang akan dihapus.
            confirmed = [r for r in rows if r["status"] == "confirmed"]
            require_branches(actor, delete_branches(confirmed, plan["branch_id"]))
            require_doc_access(actor, delete_kinds={r["doc_kind"] for r in confirmed})
            cancelled_pending = tx.exec(
                "UPDATE prodplan.outbox SET status = 'failed', last_error_code = 'cancelled', updated_at = now()"
                " WHERE plan_id = %s AND status IN ('pending','needs_login')",
                (plan["id"],),
            )
            delete_pending = tx.exec(
                "UPDATE prodplan.outbox SET status = 'delete_pending', run_as_sub = %s, next_attempt_at = now(),"
                " updated_at = now() WHERE plan_id = %s AND status = 'confirmed'",
                (actor.sub, plan["id"]),
            )
            self._bump(
                tx,
                plan["id"],
                ", status = %s, cancelled_by_sub = %s, cancelled_at = now(), cancel_reason = %s",
                (target, actor.sub, reason),
            )
            self._audit(
                tx,
                plan,
                actor,
                "plan_cancelled",
                rid,
                reason=reason,
                from_status=plan["status"],
                esb_deletes_queued=delete_pending,
                pending_cancelled=cancelled_pending,
            )
            return self._plan_view(tx, plan["id"])

    @staticmethod
    def resendable(row: Mapping[str, Any], plan_status: str) -> bool:
        """SEC-226 + QA-B: hanya `failed` TANPA nomor dokumen, kode galat yang aman diulang, rencana tidak batal."""
        return (
            plan_status != "cancelled"
            and row.get("status") == "failed"
            and not row.get("esb_doc_number")
            and row.get("last_error_code") in RESENDABLE_ERRORS
        )

    def _load_outbox(self, tx: Tx, actor: Actor, outbox_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        row = tx.one(
            "SELECT o.id, o.plan_id, o.doc_kind, o.marker, o.status, o.last_error_code, o.esb_doc_number, o.esb_env,"
            " o.created_by_sub, o.run_as_sub,"
            " p.production_date, p.branch_id FROM prodplan.outbox o JOIN prodplan.plan p ON p.id = o.plan_id"
            " WHERE o.id = %s FOR UPDATE OF o",
            (parse_uuid(outbox_id),),
        )
        if row is None:
            raise ApiError(404, "not_found")
        plan = self._plan_for_update(tx, actor, str(row["plan_id"]))
        return row, plan

    def resend(self, actor: Actor, outbox_id: str, rid: str) -> Any:
        """Kirim ulang manusia. SELALU rekonsiliasi GET marker dulu (QA-C): dokumen ternyata ada -> nomor diadopsi
        (`sent`, dikonfirmasi worker), bukan POST baru; daftar ESB tak terbaca tuntas -> 409 `reconcile_incomplete`."""
        require_role(actor, {PLANNER})
        if not self.settings.write_enabled:
            raise ApiError(409, "write_disabled")
        with self.db.tx() as tx:
            row, plan = self._load_outbox(tx, actor, outbox_id)
            if run_as(row) != actor.sub:
                raise ApiError(403, "resume_requires_original_user")  # §30.17.9: hanya aktor `run_as` baris itu
            if not self.resendable(row, plan["status"]):
                raise ApiError(409, "invalid_state")
            known = {
                r["esb_doc_number"]
                for r in tx.all(
                    "SELECT esb_doc_number FROM prodplan.outbox WHERE esb_env = %s AND doc_kind = %s"
                    " AND esb_doc_number IS NOT NULL",
                    (row["esb_env"], row["doc_kind"]),
                )
            }
        try:
            found = find_by_marker(self._client(actor), row, known)  # GET ESB di luar transaksi, token pemanggil
        except EsbError as exc:
            raise esb_api_error(exc) from None
        with self.db.tx() as tx:
            row, plan = self._load_outbox(tx, actor, outbox_id)
            if not self.resendable(row, plan["status"]):
                raise ApiError(409, "invalid_state")  # berubah selama rekonsiliasi
            if found.number is not None:
                tx.exec(
                    "UPDATE prodplan.outbox SET status = 'sent', esb_doc_number = COALESCE(esb_doc_number, %s),"
                    " esb_status_id = %s, last_error_code = NULL, next_attempt_at = now(), updated_at = now()"
                    " WHERE id = %s",
                    (found.number, found.status_id, row["id"]),
                )
                self._audit(
                    tx, plan, actor, "outbox_adopted", rid, outbox_id=str(row["id"]), esb_doc_number=found.number
                )
                return {"outbox_id": str(row["id"]), "status": "sent", "esb_doc_number": found.number}
            if not found.complete:
                raise ApiError(409, "reconcile_incomplete")
            tx.exec(
                "UPDATE prodplan.outbox SET status = 'pending', last_error_code = NULL, next_attempt_at = now(),"
                " updated_at = now() WHERE id = %s",
                (row["id"],),
            )
            self._audit(tx, plan, actor, "outbox_resend", rid, outbox_id=str(row["id"]))
        return {"outbox_id": str(row["id"]), "status": "pending"}

    def resume_posting(self, actor: Actor, plan_id: str, body: Mapping[str, Any], rid: str) -> Any:
        """§30.17.9 `POST /v1/plans/{id}/resume-posting` (R): baris `needs_login` dilanjutkan dengan token pemanggil.
        Baris yang mungkin sudah tercipta di ESB oleh aktor lain -> 403 `resume_requires_original_user`; baris tanpa
        nomor yang pernah dikirim -> `unknown` (rekonsiliasi marker dulu, TIDAK POST ulang)."""
        require_role(actor, ANY_ROLE)
        with self.db.tx() as tx:
            plan = self._plan_for_update(tx, actor, plan_id)
            check_version(int(plan["row_version"]), body.get("row_version"))
            rows = tx.all(
                "SELECT o.id, o.doc_kind, o.status, o.attempts, o.esb_doc_number, o.last_error_code, o.created_by_sub,"
                " o.run_as_sub, t.origin_branch_id, t.dest_branch_id FROM prodplan.outbox o"
                " LEFT JOIN prodplan.plan_transfer t ON t.id = o.source_transfer_id"
                " WHERE o.plan_id = %s ORDER BY o.seq FOR UPDATE OF o",
                (plan["id"],),
            )
            row_branches: dict[Any, list[int]] = {}
            for r in rows:  # transfer: cabang asal + tujuan; produksi: cabang rencana
                pair = [b for b in (r["origin_branch_id"], r["dest_branch_id"]) if b is not None]
                row_branches[r["id"]] = pair or [plan["branch_id"]]
            targets = plan_resume(rows, actor, row_branches)
            for outbox_id, status in targets:
                tx.exec(
                    "UPDATE prodplan.outbox SET run_as_sub = %s, status = %s, next_attempt_at = now(),"
                    " last_error_code = CASE WHEN last_error_code = 'needs_login' THEN NULL ELSE last_error_code END,"
                    " updated_at = now() WHERE id = %s",
                    (actor.sub, status, outbox_id),
                )
            self._bump(tx, plan["id"])
            self._audit(tx, plan, actor, "posting_resumed", rid, rows=len(targets))
        return {"outbox_ids": [str(oid) for oid, _ in targets]}
