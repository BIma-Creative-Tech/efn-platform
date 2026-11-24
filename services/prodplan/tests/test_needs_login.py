"""Outbox/job memakai token pemicu (docs/04 §30.17.9), resume-posting, pembatasan cabang + cache per aktor (§30.17.6).

Memakai FakeStore/FakeEsb/Env dari test_outbox.py (aktor 1 = pemicu; `logged_in` = aktor yang punya sesi hidup).
"""

from datetime import date, timedelta  # noqa: I001 - modul tetangga test_* (bukan first-party), urutan blok sengaja
from types import SimpleNamespace

import pytest

from efn_prodplan.actor import Actor
from efn_prodplan.esb import EsbError, EsbTokenRejected, HttpResponse
from efn_prodplan.jobs import BomCatalog, JobRunner, cache_scope, job_error_code, source_branches, source_locations
from efn_prodplan.service import Service, delete_branches, esb_api_error, pr_branches
from efn_prodplan.workflow import (
    ApiError,
    check_sod,
    plan_resume,
    posting_state,
    require_branches,
    require_doc_access,
    resume_status,
)
from test_jobs import FakeReader
from test_outbox import T0, Env, FakeEsb, row

ALL_DOCS = frozenset({"simple_transfer", "goods_transfer_request", "simple_manufacturing"})


def actor(sub=1, branches=(4,), insert=ALL_DOCS, delete=ALL_DOCS, roles=("pp_planner", "pp_warehouse")):
    return Actor(
        sub=sub,
        name="Uji",
        roles=frozenset(roles),
        brand="pb",
        branch_ids=frozenset(branches),
        doc_insert=frozenset(insert),
        doc_delete=frozenset(delete),
    )


def code(fn, *args, **kw):
    with pytest.raises(ApiError) as exc:
        fn(*args, **kw)
    return exc.value.status, exc.value.code


def resume(env, oid, by=1):
    r = env.store.rows[oid]
    r.update(status=resume_status(r), run_as_sub=by, next_attempt_at=env.now)
    if r.get("last_error_code") == "needs_login":
        r["last_error_code"] = None


# ---------------------------------------------------------------- worker tanpa token
def test_pending_without_token_goes_needs_login_without_esb_call():
    env = Env([row(1)], logged_in=())
    assert env.worker.run_once()
    r = env.store.rows["o1"]
    assert r["status"] == "needs_login" and r["last_error_code"] == "needs_login" and r["attempts"] == 0
    assert env.esb.posts == [] and env.esb.gets == []
    assert env.client_calls == [(1, "pb", 120)]  # POST butuh sisa token >= 120 dtk
    assert posting_state([r]) == "needs_attention"
    assert not env.worker.run_once()  # needs_login bukan kerja worker


def test_token_owner_is_run_as_then_creator():
    env = Env([row(1, created_by_sub=5, run_as_sub=None)], logged_in=(5,))
    env.esb.post_outcomes = ["ST1"]
    env.worker.run_once()
    assert env.client_calls[-1][0] == 5 and env.status("o1") == "sent"
    env = Env([row(1, created_by_sub=5, run_as_sub=9)], logged_in=(5,))
    env.worker.run_once()
    assert env.client_calls[-1][0] == 9 and env.status("o1") == "needs_login" and env.esb.posts == []


def test_read_actions_ask_30_seconds_and_keep_status_without_token():
    env = Env([row(1, "sent", esb_doc_number="ST1")], logged_in=())
    env.worker.run_once()
    r = env.store.rows["o1"]
    assert env.client_calls == [(1, "pb", 30)]
    assert r["status"] == "sent" and r["last_error_code"] == "needs_login"
    assert r["next_attempt_at"] == T0 + timedelta(seconds=60)
    # aktor login lagi -> lanjut otomatis (GET saja)
    env.logged_in.add(1)
    env.esb.docs["ST1"] = {"additionalInfo": r["marker"], "statusID": 1}
    env.advance(61)
    env.worker.run_once()
    assert env.status("o1") == "confirmed" and env.esb.posts == []


def test_401_on_post_then_resume_reconciles_without_second_post():
    env = Env([row(1)])
    env.esb.post_outcomes = [EsbTokenRejected("401"), "ST-ganda"]
    env.worker.run_once()
    r = env.store.rows["o1"]
    assert r["status"] == "needs_login" and r["attempts"] == 1
    resume(env, "o1")
    assert env.status("o1") == "unknown"  # pernah dikirim -> rekonsiliasi dulu
    env.esb.listing = [{"simpleTransferNum": "ST77", "additionalInfo": r["marker"]}]
    env.worker.run_once()
    assert env.status("o1") == "sent" and env.store.rows["o1"]["esb_doc_number"] == "ST77"
    assert len(env.esb.posts) == 1


def test_delete_pending_without_token_and_resume_back_to_delete_pending():
    env = Env([row(1, "delete_pending", esb_doc_number="ST1", attempts=1)], logged_in=())
    env.worker.run_once()
    assert env.status("o1") == "needs_login" and env.esb.deletes == []
    resume(env, "o1")
    assert env.status("o1") == "delete_pending"


def test_401_on_delete_goes_needs_login():
    env = Env([row(1, "delete_pending", esb_doc_number="ST1")])
    env.esb.docs = {"ST1": {"additionalInfo": "pp:000000000001"}}
    env.esb.delete_outcomes = [EsbTokenRejected("401")]
    env.worker.run_once()
    assert env.status("o1") == "needs_login" and env.store.rows["o1"]["esb_doc_number"] == "ST1"


def test_restart_with_empty_sessions_is_safe():
    env = Env([row(1, "sending", attempts=1, sent_at=T0)], logged_in=())
    env.worker.start()
    assert env.status("o1") == "unknown"
    env.worker.run_once()
    r = env.store.rows["o1"]
    assert r["status"] == "unknown" and r["last_error_code"] == "needs_login"
    assert env.esb.posts == [] and env.esb.gets == []


def test_reconcile_token_rejected_marks_waiting():
    class Rejecting(FakeEsb):
        def get(self, path, params=None):
            raise EsbTokenRejected("401")

    env = Env([row(1, "unknown", sent_at=T0)])
    env.esb = Rejecting()
    env.worker.run_once()
    r = env.store.rows["o1"]
    assert r["status"] == "unknown" and r["last_error_code"] == "needs_login"


# ---------------------------------------------------------------- resume-posting (murni)
def _rows(*specs):
    return [{"id": f"o{i}", "doc_kind": "simple_transfer", "created_by_sub": 1, **s} for i, s in enumerate(specs, 1)]


def test_resume_nothing_to_resume():
    rows = _rows({"status": "confirmed"}, {"status": "pending", "attempts": 0})
    assert code(plan_resume, rows, actor(), {}) == (409, "nothing_to_resume")


def test_resume_transitions_and_run_as():
    rows = _rows(
        {"status": "needs_login", "attempts": 0, "esb_doc_number": None},
        {"status": "needs_login", "attempts": 1, "esb_doc_number": None},
        {"status": "needs_login", "attempts": 1, "esb_doc_number": "ST1"},
        {"status": "confirmed"},
    )
    out = plan_resume(rows, actor(), {r["id"]: [4] for r in rows})
    assert out == [("o1", "pending"), ("o2", "unknown"), ("o3", "delete_pending")]


def test_resume_by_other_actor_when_row_may_exist_is_rejected():
    rows = _rows({"status": "unknown", "attempts": 1}, {"status": "needs_login", "attempts": 0})
    assert code(plan_resume, rows, actor(sub=2), {}) == (403, "resume_requires_original_user")
    rows = _rows({"status": "needs_login", "attempts": 1, "esb_doc_number": None})
    assert code(plan_resume, rows, actor(sub=2), {"o1": [4]}) == (403, "resume_requires_original_user")
    # baris belum pernah dikirim boleh dilanjutkan aktor lain (dengan cabang + izin)
    rows = _rows({"status": "needs_login", "attempts": 0})
    assert plan_resume(rows, actor(sub=2), {"o1": [4]}) == [("o1", "pending")]


def test_resume_checks_branches_and_doc_access():
    rows = _rows({"status": "needs_login", "attempts": 0})
    assert code(plan_resume, rows, actor(branches=(13,)), {"o1": [4, 13]}) == (403, "branch_forbidden")
    with pytest.raises(ApiError) as exc:
        plan_resume(rows, actor(insert=()), {"o1": [4]})
    assert exc.value.code == "esb_access_missing" and exc.value.details == {"doc_kinds": ["simple_transfer"]}
    rows = _rows({"status": "needs_login", "attempts": 1, "esb_doc_number": "ST1"})
    assert code(plan_resume, rows, actor(delete=()), {"o1": [4]}) == (403, "esb_access_missing")


# ---------------------------------------------------------------- cabang + akses dokumen
def test_require_branches_and_doc_access():
    require_branches(actor(branches=(4, 13)), [4, 13])
    assert code(require_branches, actor(branches=(4,)), [4, 13]) == (403, "branch_forbidden")
    require_doc_access(actor(), insert_kinds={"simple_transfer"}, delete_kinds={"simple_manufacturing"})
    result = code(require_doc_access, actor(delete=()), delete_kinds={"simple_manufacturing"})
    assert result == (403, "esb_access_missing")


def test_plan_of_other_branch_or_brand_is_404():
    svc = Service(SimpleNamespace(esb_env="staging"), None, None, lambda: T0)  # type: ignore[arg-type]
    plan = {"esb_env": "staging", "esb_brand": "pb", "branch_id": 4}
    assert svc._visible(actor(branches=(4,)), plan) is plan
    assert code(svc._visible, actor(branches=(13,)), plan) == (404, "not_found")
    assert code(svc._visible, actor(), {**plan, "esb_brand": "wtz"}) == (404, "not_found")
    assert code(svc._visible, actor(), {**plan, "esb_env": "production"}) == (404, "not_found")
    assert code(svc._visible, actor(), None) == (404, "not_found")


def test_sod_with_two_esb_actors():
    check_sod(1_000_002, 1_000_001, 1_000_001)  # akun ESB kedua boleh menyetujui
    assert code(check_sod, 1_000_001, 1_000_001, 1_000_001) == (403, "sod_violation")


def test_sources_only_from_trigger_branches_max_20():
    used, dropped = source_branches([13, 4, 99, 2], 4)
    assert used == [4, 2, 13, 99] and dropped == []
    used, dropped = source_branches(range(1, 31), 25)
    assert used[0] == 25 and len(used) == 20 and len(dropped) == 10
    locs = source_locations({4: [7, 8, 9], 13: [21]}, 4, 7)
    assert locs == [(4, 8), (4, 9), (13, 21)]  # lokasi produksi dikecualikan


def test_catalog_cache_does_not_leak_between_actors():
    def reader(name):
        return FakeReader(
            {
                "/product/bom": {"page": 1, "limit": 200, "count": 1, "data": [{"bomID": 101, "bomName": name}]},
                "/product/bom/101": {"bomID": 101, "productDetailID": 1001, "productCode": name, "uomName": "PCS"},
            }
        )

    catalog = BomCatalog()
    a, b = reader("BOM-A"), reader("BOM-B")
    _, headers_a = catalog.index(cache_scope(1_000_001, "pb"), a)
    _, headers_b = catalog.index(cache_scope(1_000_002, "pb"), b)
    assert headers_a[101].product_code == "BOM-A" and headers_b[101].product_code == "BOM-B"
    assert b.calls  # aktor kedua memanggil ESB dengan tokennya sendiri, bukan dari cache aktor pertama


# ---------------------------------------------------------------- job
class FakeTx:
    def __init__(self, plan):
        self.plan = plan
        self.execs = []

    def one(self, sql, params=None):
        return dict(self.plan)

    def exec(self, sql, params=None):
        self.execs.append(sql)
        return 1


class FakeDb:
    def __init__(self, plan):
        self.tx_obj = FakeTx(plan)

    def tx(self):
        db = self

        class Ctx:
            def __enter__(self):
                return db.tx_obj

            def __exit__(self, *exc):
                return False

        return Ctx()


class FakeJobs:
    def __init__(self, job):
        self.job = job
        self.finished = []

    def claim_job(self):
        job, self.job = self.job, None
        return job

    def finish_job(self, job_id, status, result, error_code, esb_calls):
        self.finished.append((status, error_code, esb_calls))


def test_job_without_token_fails_needs_login_without_esb():
    plan = {"id": "p1", "esb_brand": "pb", "esb_env": "staging", "branch_id": 4, "production_location_id": 7}
    plan.update(company_code="PB", production_date=date(2026, 11, 25), status="draft", row_version=1)
    asked = []
    jobs = FakeJobs({"id": "j1", "plan_id": "p1", "kind": "calculate", "params": {}, "requested_by_sub": 1_000_001})
    runner = JobRunner(FakeDb(plan), jobs, SimpleNamespace(), lambda a, b: asked.append((a, b)), lambda: T0)
    assert runner.run_once()
    assert jobs.finished == [("failed", "needs_login", 0)] and asked == [(1_000_001, "pb")]


def test_job_error_code_token_rejected_mid_job():
    assert job_error_code(EsbTokenRejected("401")) == "needs_login"


def test_import_pr_branches_limited_to_session():
    """SEC-235: cabang di luar sesi -> 403; kosong -> cabang sesi (maks 20), bukan tanpa filter."""
    assert pr_branches(actor(branches=(4, 13)), [13]) == [13]
    assert code(pr_branches, actor(branches=(4, 13)), [4, 99]) == (403, "branch_forbidden")
    assert pr_branches(actor(branches=(13, 4)), []) == [4, 13]
    assert len(pr_branches(actor(branches=range(1, 40)), [])) == 20


def test_cancel_requires_branches_of_documents_to_delete():
    """SEC-241: `cancel` menuntut cabang asal/tujuan transfer (produksi: cabang rencana) seperti post/resume."""
    rows = [{"origin_branch_id": 13, "dest_branch_id": 4}, {"origin_branch_id": None, "dest_branch_id": None}]
    assert delete_branches(rows, 4) == {4, 13}
    assert code(require_branches, actor(branches=(4,)), delete_branches(rows, 4)) == (403, "branch_forbidden")
    assert delete_branches([], 4) == set()


def test_http_response_repr_hides_body_and_headers():
    response = HttpResponse(200, b'{"result":{"accessToken":"tok-rahasia"}}', {"Set-Cookie": "x=rahasia"})
    assert "rahasia" not in repr(response) and "200" in repr(response)


def test_sync_get_token_rejected_is_session_expired():
    err = esb_api_error(EsbTokenRejected("x"))
    assert (err.status, err.code) == (401, "session_expired")
    assert esb_api_error(EsbError("x")).code == "esb_unavailable"
