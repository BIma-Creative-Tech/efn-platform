"""Perbaikan G66 putaran 2: SEC-225/226/227/230/231, QA-A..E, nama ESB, /v1/boms, kode galat job."""

from decimal import Decimal as D  # noqa: I001 - `test_outbox`/`test_calc` = modul tetangga, urutan blok sengaja
from pathlib import Path
from types import SimpleNamespace

import pytest

from efn_prodplan.actor import Actor
from efn_prodplan.calc import Bom, BomLine, Demand, compute_plan
from efn_prodplan.esb import EsbError, classify_call
from efn_prodplan.jobs import (
    BomHeader,
    JobAbort,
    job_error_code,
    parse_bom_detail,
    save_result,
    signature_from_result,
    signature_rows,
    validate_search,
)
from efn_prodplan.names import NameCache
from efn_prodplan.outbox import find_by_marker
from efn_prodplan.service import Service
from efn_prodplan.store import outbox_update_sql
from efn_prodplan.workflow import JOB_ERROR_CODES, MESSAGES, ApiError, check_sod, posting_state
from test_calc import milk_plan
from test_outbox import SM_NUM, T0, Env, FakeEsb, row


# ---------------------------------------------------------------- SEC-225: paginasi rekonsiliasi
class PagedEsb(FakeEsb):
    def __init__(self, rows):
        super().__init__()
        self.all_rows = rows

    def get(self, path, params=None):
        p = dict(params or {})
        self.gets.append((path, p))
        page, limit = p["page"], p["limit"]
        chunk = self.all_rows[(page - 1) * limit : page * limit]
        return {"page": page, "limit": limit, "count": len(self.all_rows), "data": chunk}


def _sm_rows(n):
    return [{"simpleManufacturingNum": f"SM20261124{i:04d} - 1"} for i in range(n)]


def test_production_marker_found_on_page_two():
    env = Env([row(1, "unknown", kind="simple_manufacturing", sent_at=T0)])
    esb = PagedEsb(_sm_rows(150))
    for r in esb.all_rows:
        esb.docs[r["simpleManufacturingNum"]] = {"simpleManufacturingDetails": [{"notes": "pp:ffffffffffff"}]}
    target = esb.all_rows[120]["simpleManufacturingNum"]
    esb.docs[target] = {"simpleManufacturingDetails": [{"additionalInfo": "pp:000000000001"}], "statusID": 3}
    env.esb = esb
    env.advance(400)  # lewat grace: dulu = failed palsu karena hanya halaman 1 dibaca
    env.worker.run_once()
    r = env.store.rows["o1"]
    assert r["status"] == "sent" and r["esb_doc_number"] == target and r["esb_status_id"] == 3
    pages = [p["page"] for _, p in esb.gets]
    assert pages == [1, 2] and all(p["limit"] == 100 and p["branchID"] == 4 for _, p in esb.gets)


def test_page_limit_or_esb_error_keeps_unknown_never_failed():
    esb = PagedEsb(_sm_rows(10))
    for r in esb.all_rows:
        esb.docs[r["simpleManufacturingNum"]] = {"simpleManufacturingDetails": [{"notes": "pp:ffffffffffff"}]}
    res = find_by_marker(esb, row(1, kind="simple_manufacturing"), set(), limit=2, max_pages=2)
    assert res.number is None and res.complete is False
    res = find_by_marker(esb, row(1, kind="simple_manufacturing"), set(), limit=100)
    assert res.number is None and res.complete is True

    class Down(FakeEsb):
        def get(self, path, params=None):
            raise EsbError("down")

    env = Env([row(1, "unknown", sent_at=T0)])
    env.esb = Down()
    env.advance(400)
    env.worker.run_once()
    r = env.store.rows["o1"]
    assert r["status"] == "unknown" and r["last_error_code"] == "reconcile_incomplete"
    assert posting_state([r]) == "needs_attention"
    assert env.esb.posts == []


def test_listed_number_with_unreadable_detail_is_incomplete():
    esb = FakeEsb()
    esb.listing = [{"simpleManufacturingNum": SM_NUM}]
    res = find_by_marker(esb, row(1, kind="simple_manufacturing"), set())
    assert res.number is None and res.complete is False


def test_transfer_listing_without_additional_info_checks_detail():
    esb = FakeEsb()
    esb.listing = [{"simpleTransferNum": "ST5"}]
    esb.docs["ST5"] = {"additionalInfo": "pp:000000000001", "statusID": 1}
    res = find_by_marker(esb, row(1), set())
    assert (res.number, res.status_id, res.complete) == ("ST5", 1, True)
    assert esb.gets[0][1]["additionalInfo"] == "pp:000000000001"


def test_reconcile_with_existing_number_goes_back_to_sent():
    env = Env([row(1, "unknown", esb_doc_number="ST1", sent_at=T0)])
    env.advance(400)
    env.worker.run_once()
    assert env.status("o1") == "sent" and env.esb.gets == []


# ---------------------------------------------------------------- SEC-226: store tidak menimpa nomor
def test_outbox_update_sql_never_overwrites_number_or_downgrades():
    sql, params = outbox_update_sql("o1", {"status": "failed", "esb_doc_number": "X", "bogus": 1})
    assert "esb_doc_number = COALESCE(esb_doc_number, %(esb_doc_number)s)" in sql
    assert "WHEN esb_doc_number IS NOT NULL AND %(status)s IN ('failed','pending','unknown') THEN status" in sql
    assert "bogus" not in sql and params["id"] == "o1"
    assert outbox_update_sql("o1", {"bogus": 1}) is None


def test_resendable_rules():
    ok = {"status": "failed", "esb_doc_number": None, "last_error_code": "esb_rejected"}
    assert Service.resendable(ok, "posted")
    for code in ("not_sent", "client_error", "not_found_after_timeout"):
        assert Service.resendable({**ok, "last_error_code": code}, "approved")
    assert not Service.resendable({**ok, "esb_doc_number": "ST1"}, "posted")
    assert not Service.resendable({**ok, "last_error_code": "cancelled"}, "posted")
    assert not Service.resendable({**ok, "last_error_code": "marker_mismatch"}, "posted")
    assert not Service.resendable({**ok, "status": "sent"}, "posted")
    assert not Service.resendable(ok, "cancelled")


# ---------------------------------------------------------------- QA-B: posting_state
@pytest.mark.parametrize(
    ("rows", "state"),
    [
        ([], "none"),
        ([{"status": "failed", "last_error_code": "cancelled"}], "none"),
        ([{"status": "confirmed"}, {"status": "pending"}], "in_progress"),
        ([{"status": "confirmed"}, {"status": "failed", "last_error_code": "esb_rejected"}], "needs_attention"),
        ([{"status": "sent", "last_error_code": "doc_not_found"}], "needs_attention"),
        ([{"status": "deleted"}, {"status": "delete_failed"}], "needs_attention"),
        ([{"status": "confirmed"}, {"status": "confirmed"}], "complete"),
        ([{"status": "deleted"}, {"status": "failed", "last_error_code": "cancelled"}], "complete"),
    ],
)
def test_posting_state(rows, state):
    assert posting_state(rows) == state


# ---------------------------------------------------------------- SEC-227: SoD penulis isi rencana
def test_sod_rejects_content_contributors():
    check_sod(9, 1, 2, {3, 4})
    with pytest.raises(ApiError) as exc:
        check_sod(4, 1, 2, {3, 4})
    assert (exc.value.status, exc.value.code) == (403, "sod_violation")
    with pytest.raises(ApiError):
        check_sod(1, 1, None)


# ---------------------------------------------------------------- QA-A: sidik hasil material (refresh)
def _rows_from(result):
    production = [
        {
            "bom_id": pl.bom_id,
            "level": pl.level,
            "qty": str(pl.qty),
            "materials": [
                {"product_detail_id": m.product_detail_id, "total_qty": str(m.total_qty)} for m in pl.materials
            ],
        }
        for pl in result.production_lines
    ]
    transfers = [
        {
            "id": i,
            "doc_type": t.doc_type,
            "origin_branch_id": t.origin_branch_id,
            "origin_location_id": t.origin_location_id,
            "dest_branch_id": t.dest_branch_id,
            "dest_location_id": t.dest_location_id,
        }
        for i, t in enumerate(result.transfers)
    ]
    lines = [
        {"transfer_id": i, "product_detail_id": tl.product_detail_id, "qty": D(tl.qty)}
        for i, t in enumerate(result.transfers)
        for tl in t.lines
    ]
    reqs = [{"product_detail_id": r.product_detail_id, "shortage_qty": r.shortage_qty} for r in result.requirements]
    return production, transfers, lines, reqs


def test_signature_identical_when_material_result_unchanged():
    result = milk_plan()
    production, transfers, lines, reqs = _rows_from(result)
    assert signature_rows(production, transfers, lines, reqs) == signature_from_result(result)
    lines[0]["qty"] = D("999")
    assert signature_rows(production, transfers, lines, reqs) != signature_from_result(result)
    production2, transfers2, lines2, reqs2 = _rows_from(result)
    reqs2[0]["shortage_qty"] = D("1")
    assert signature_rows(production2, transfers2, lines2, reqs2) != signature_from_result(result)


# ---------------------------------------------------------------- QA-D: usulan ditolak tidak muncul lagi
class RecTx:
    def __init__(self):
        self.calls = []

    def exec(self, sql, params=None):
        self.calls.append(sql)
        return 1

    def one(self, sql, params=None):
        self.calls.append(sql)
        return {"id": 1}


def test_save_result_skips_identical_rejected_suggestions_and_parts():
    pytest.importorskip("psycopg")  # jsonb() memakai psycopg.types.json
    result = milk_plan()
    tx = RecTx()
    save_result(tx, "p1", result, skip_suggestions={(21, 101, 1)})
    assert sum("plan_suggestion" in s for s in tx.calls) == 1  # hanya usulan FIN-B
    tx = RecTx()
    save_result(tx, "p1", result, parts=("requirements",))
    assert tx.calls and all("plan_requirement" in s for s in tx.calls)


# ---------------------------------------------------------------- SEC-231: qty BOM <= 0
def test_bom_detail_and_calc_skip_non_positive_quantities():
    rows = [
        {"productDetailID": 21, "productCode": "OK", "uomName": "ML", "qty": 5},
        {"productDetailID": 22, "productCode": "ZERO", "uomName": "ML", "qty": 0},
        {"productDetailID": 23, "productCode": "NEG", "uomName": "ML", "qty": -2},
        {"productDetailID": 24, "productCode": "TXT", "uomName": "ML", "qty": "abc"},
        {"productDetailID": 25, "productCode": "NAN", "uomName": "ML", "qty": "NaN"},
    ]
    detail = parse_bom_detail(BomHeader(1, 100, "FIN", "Fin", "PCS"), rows)
    assert [ln.product_code for ln in detail.bom.lines] == ["OK"]
    assert detail.invalid_qty == ["ZERO", "NEG", "TXT", "NAN"]
    bom = Bom(1, 100, "FIN", "Fin", "PCS", (BomLine(21, "OK", "x", "ML", D(5)), BomLine(22, "NEG", "x", "ML", D(-1))))
    result = compute_plan(
        [Demand(100, 1, D(2))],
        {1: bom},
        {100: 1},
        master_uom={},
        pack_rules={},
        stock={},
        prod_branch=4,
        prod_loc=7,
        sources=[],
    )
    assert [r.product_code for r in result.requirements] == ["OK"]
    assert all(m.total_qty > 0 for pl in result.production_lines for m in pl.materials)
    assert result.warnings["invalid_bom_qty"] == ("FIN:NEG",)


# ---------------------------------------------------------------- QA-E + kode galat
def test_new_error_codes_have_messages():
    for code in ("empty_plan", "bad_input", "invalid_quantity", "reconcile_incomplete"):
        assert code in MESSAGES


def test_job_error_codes_single_registry():
    assert job_error_code(JobAbort("conflict_version")) == "conflict_version"
    assert job_error_code(JobAbort("something_new")) == "internal"
    store_src = (Path(__file__).resolve().parent.parent / "efn_prodplan" / "store.py").read_text(encoding="utf-8")
    assert "'interrupted'" in store_src and "interrupted" in JOB_ERROR_CODES
    assert {"bom_cycle", "bom_missing", "bom_not_assembly", "esb_unavailable", "parse_error"} <= JOB_ERROR_CODES


# ---------------------------------------------------------------- /v1/boms
def _svc(client=None, names=None):
    settings = SimpleNamespace(brands=[SimpleNamespace(key="pb")], mfg_mode="actual")
    return Service(settings, None, lambda _actor: client, lambda: T0, names=names)  # type: ignore[arg-type]


ACTOR = Actor(
    sub=7,
    name="Uji",
    roles=frozenset({"pp_planner"}),
    brand="pb",
    branch_ids=frozenset({4, 13}),
    branch_names={4: "CK Rekaan", 13: "WH Prod"},
)


@pytest.mark.parametrize(
    "query",
    [{"brand": "pb", "q": "a"}, {"brand": "pb", "q": "x" * 65}, {"brand": "pb", "q": "roti", "limit": "99"}],
)
def test_bom_search_validation_is_422_bad_input(query):
    with pytest.raises(ApiError) as exc:
        _svc().search_boms(ACTOR, query)
    assert (exc.value.status, exc.value.code) == (422, "bad_input")


def test_validate_search_len_64_and_no_control_chars():
    assert validate_search("x" * 64) == "x" * 64
    assert validate_search("Roti <tawar>; 50%") == "Roti <tawar>; 50%"
    for bad in ("a", "", None, "x" * 65, "a\nb", "\x00ab"):
        with pytest.raises(ValueError):
            validate_search(bad)


# ---------------------------------------------------------------- nama ESB
class NamesClient:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    def get_all(self, path, params=None, *, limit=200, max_pages=100):
        self.calls += 1
        if self.fail:
            raise EsbError("down")
        assert path == "/branch/list/user"
        return [{"branchID": 4, "branchName": "CK Rekaan"}, {"branchID": "13", "branchName": "WH Prod"}]

    def get(self, path, params=None):
        self.calls += 1
        if self.fail:
            raise EsbError("down")
        assert path == "/location/branch/4"
        return [{"locationID": 7, "locationName": "CK - Kitchen"}, {"locationID": 8, "locationName": "CK - Gudang"}]


def test_names_cached_and_failures_are_none():
    now = [0.0]
    cache = NameCache(clock=lambda: now[0])
    client = NamesClient()
    assert cache.branch_name("pb", client, 13) == "WH Prod"
    assert cache.location_name("pb", client, 4, 7) == "CK - Kitchen"
    calls = client.calls
    assert cache.branch_name("pb", client, 4) == "CK Rekaan" and client.calls == calls
    down = NamesClient(fail=True)
    broken = NameCache(clock=lambda: now[0])
    assert broken.branch_name("pb", down, 4) is None and broken.location_name("pb", down, 4, 7) is None
    calls = down.calls
    assert broken.branch_name("pb", down, 4) is None and down.calls == calls  # cache negatif
    now[0] = 61
    broken.branch_name("pb", down, 4)
    assert down.calls == calls + 1


def test_service_names_places_and_allowlist():
    svc = _svc(NamesClient())
    place = svc._name_place(ACTOR, {"branch_id": 4, "location_id": 8})
    # nama cabang dari sesi (login), nama lokasi dari GET bertoken pemanggil
    assert place["branch_name"] == "CK Rekaan" and place["location_name"] == "CK - Gudang"
    assert place["branch_id"] == 4 and place["location_id"] == 8
    other = _svc()._name_place(ACTOR, {"branch_id": 99, "location_id": 8})
    assert other["branch_name"] is None and other["location_name"] is None
    assert classify_call("GET", "/branch/list/user", mfg_mode="actual", allow_write=False) == "/branch/list/user"
