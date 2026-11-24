"""Extractor + orkestrasi dengan ESB & ClickHouse palsu: parameter GET, redaksi raw (L-110), urutan tulis (checkpoint
terakhir), error/budget/auth/jendela, backfill resumable. Fixture sintetis."""

import argparse
import json
import logging
import threading
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest

import efn_esb.__main__ as cli
from efn_esb.config import BrandConfig, EsbSettings
from efn_esb.extract import BrandRuntime, extract_payment, extract_production, run_slice
from efn_esb.http import CallBudgetExceeded, EsbAuthError
from efn_esb.planning import WIB, SliceTask, wib_today
from efn_esb.records import RAW_COLUMNS, RUN_COLUMNS, raw_rows, version_now
from efn_esb.runner import (
    AUTH_SUSPEND_AFTER,
    AuthBreaker,
    execute,
    guarded_cycle,
    run_backfill,
    run_cycle,
    write_slice,
)
from efn_esb.transform import BomRatio

DAY = date(2026, 11, 16)
V = datetime(2026, 11, 17, 3, 0, 0, 1000, tzinfo=UTC)
CFG = BrandConfig("pb", "user-sintetis", "pw-sintetis", "PB", frozenset({4}), frozenset(), {})


class FakeEsb:
    """Respons per path; mencatat setiap (path, params)."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append((path, dict(params or {})))
        route = self.routes.get(path)
        if route is None:
            for prefix, value in self.routes.items():
                if prefix.endswith("/*") and path.startswith(prefix[:-1]):
                    route = value
                    break
        if isinstance(route, Exception):
            raise route
        if callable(route):
            return route(path, params or {})
        return route if route is not None else {"count": 0, "data": []}


class FakeStore:
    def __init__(self, checkpoints: dict[tuple[str, str, date], datetime] | None = None) -> None:
        self.writes: list[tuple[str, str, list[list[Any]]]] = []
        self._checkpoints = checkpoints or {}
        self.aliases: dict[str, str] = {"FIN ROTI": "FIN001"}
        self.ratios: dict[int, BomRatio] = {}
        self.suppliers: list[tuple[int, str, str]] = []
        self.product_ids: dict[int, str] = {}
        # G67 retransform: {(endpoint, doc_key): (doc_date, payload)} = isi raw_doc (versi terakhir).
        self.raw: dict[tuple[str, str], tuple[date, Any]] = {}

    # Sama dengan ClickHouseStore: baris kosong tidak ditulis.
    def insert(self, table: str, columns: Sequence[str], rows: list[list[Any]]) -> None:
        if rows:
            assert all(len(row) == len(columns) for row in rows), table
            self.writes.append(("mart", table, rows))

    def insert_control(self, table: str, columns: Sequence[str], rows: list[list[Any]]) -> None:
        if rows:
            assert all(len(row) == len(columns) for row in rows), table
            self.writes.append(("control", table, rows))

    def checkpoints(self, brand: str, since: date) -> dict[tuple[str, str, date], datetime]:
        return dict(self._checkpoints)

    def load_aliases(self, brand: str) -> dict[str, str]:
        return dict(self.aliases)

    def load_bom_ratios(self, brand: str) -> dict[int, BomRatio]:
        return dict(self.ratios)

    def load_suppliers(self, brand: str) -> list[tuple[int, str, str]]:
        return list(self.suppliers)

    def load_product_ids(self, brand: str) -> dict[int, str]:
        return dict(self.product_ids)

    def load_raw(self, brand: str, endpoint: str, doc_keys: Sequence[str]) -> dict[str, tuple[date, Any]]:
        return {key: self.raw[(endpoint, key)] for key in doc_keys if (endpoint, key) in self.raw}

    def tables(self) -> list[str]:
        return [table for _, table, _ in self.writes]


def runtime() -> BrandRuntime:
    rt = BrandRuntime(CFG)
    rt.branch_rows = [["pb", "PB", 4, "CK1", "CK", 1, 0, 1, V], ["pb", "PB", 7, "T07", "Toko 7", 0, 1, 1, V]]
    rt.aliases = {"FIN ROTI": "FIN001"}
    return rt


def test_sales_params_fixed_limit_and_raw_redacted() -> None:
    row = {"salesNum": "S1", "salesType": "Sales", "menuCode": "M1", "menuName": "Roti", "qty": 1, "price": 5000,
           "total": 5000, "discountTotal": 0, "cogsTotal": 2000, "rowType": "ALACARTE",
           "cashierName": "Kasir Sintetis", "customerPhone": "080000000000"}  # fmt: skip
    esb = FakeEsb({"/report/sales-menu-cogs": {"count": 1, "data": [row]}})
    result = run_slice(runtime(), SliceTask(DAY, "sales", "7"), V, esb.get)
    path, params = esb.calls[0]
    assert params == {"salesDate": "2026-11-16", "branchID": 7, "salesType": "Sales", "page": 1}
    assert result.calls == 1 and result.rows == 1
    raw = raw_rows("pb", result.raw, V)
    payload = raw[0][RAW_COLUMNS.index("payload")]
    assert "Kasir Sintetis" not in payload and "080000000000" not in payload and '"salesNum":"S1"' in payload
    assert raw[0][RAW_COLUMNS.index("doc_key")] == "2026-11-16|7"


def test_fin_sold_detail_params() -> None:
    detail = {"count": 1, "data": [{"productName": "FIN Roti", "qtyTotal": 2}]}
    esb = FakeEsb({"/report/sales-menu-cogs-detail": detail})
    result = run_slice(runtime(), SliceTask(DAY, "fin_sold", "7"), V, esb.get)
    assert esb.calls[0][1] == {
        "salesDate": "2026-11-16",
        "branchID": 7,
        "mode": "recap",
        "productName": "FIN",
        "page": 1,
        "limit": 20,
    }
    assert result.tables["fact_esb_fin_sold_daily"][0][4] == "FIN001"


def test_payment_single_call_all_branches() -> None:
    summary = [{"branchCode": "T07", "payments": [{"paymentMethodTypeName": "Cash", "paymentMethodName": "Tunai",
                                                   "paymentCount": 1, "paymentAmount": 1000}]}]  # fmt: skip
    esb = FakeEsb({"/report/sales-payment-summary": summary})
    result = extract_payment(runtime(), SliceTask(DAY, "payment", "*"), V, esb.get)
    assert len(esb.calls) == 1
    assert result.tables["fact_esb_payment_daily"][0][2] == 7  # branch_id dari branchCode


def test_production_fetches_detail_once_per_unknown_bom() -> None:
    sm_rows = [
        {"simpleManufacturingNum": f"SM{i}", "branchID": 4, "bomID": bom, "bomCode": f"B{bom}", "bomName": "FIN Donat",
         "manufacturingQty": 1, "statusID": 3, "createdBy": "Operator Sintetis"}
        for i, bom in enumerate([10, 10, 11])
    ]  # fmt: skip
    detail = {"manufacturingQty": 1, "simpleManufacturingResults": [
        {"productCode": "FIN010", "productName": "FIN Donat", "qty": 12, "uomName": "EA"}]}  # fmt: skip
    esb = FakeEsb(
        {
            "/production/simple-manufacturing": {"count": 3, "data": sm_rows},
            "/production/simple-manufacturing/*": detail,
            "/production/production-result": {"count": 0, "data": []},
        }
    )
    rt = runtime()
    rt.ratios = {11: BomRatio("FIN011", "FIN Roti", 6.0, "EA", "old")}
    result = extract_production(rt, SliceTask(DAY, "production", "*"), V, esb.get)
    detail_calls = [p for p, _ in esb.calls if p.startswith("/production/simple-manufacturing/")]
    assert detail_calls == ["/production/simple-manufacturing/SM0"]
    assert esb.calls[0][1]["statusID"] == 3 and esb.calls[0][1]["dateFrom"] == "2026-11-16"
    assert 10 in rt.ratios and result.bom_ratios[10].ratio == 12.0
    qty = {r[6]: r[8] for r in result.tables["fact_esb_production"]}
    assert str(qty["FIN010"]) == "12.000" and str(qty["FIN011"]) == "6.000"
    assert all("Operator Sintetis" not in json.dumps(doc.payload) for doc in result.raw)


def test_write_slice_order_checkpoint_last() -> None:
    esb = FakeEsb({"/report/sales-menu-cogs": {"count": 0, "data": []}})
    result = run_slice(runtime(), SliceTask(DAY, "sales", "7"), V, esb.get)
    store = FakeStore()
    write_slice(store, "pb", result, V)
    assert store.tables() == ["raw_doc", "extract_checkpoint"]  # tabel mart kosong tidak ditulis
    checkpoint = store.writes[-1][2][0]
    assert checkpoint == ["pb", "sales", "7", DAY, "done", 0, 1, V]


def test_execute_error_slice_has_no_checkpoint_and_others_continue() -> None:
    def sales(path: str, params: dict[str, Any]) -> Any:
        if params["branchID"] == 7:
            raise ValueError("rusak")
        return {"count": 0, "data": []}

    esb = FakeEsb({"/report/sales-menu-cogs": sales})
    store = FakeStore()
    tasks = [SliceTask(DAY, "sales", "7"), SliceTask(DAY, "sales", "9")]
    outcome = execute(tasks, runtime(), store, esb.get, 2, threading.Event(), clock=lambda: V)
    assert outcome.errors == 1 and outcome.slices == 1 and outcome.status() == "partial"
    checkpoints = [rows[0] for _, table, rows in store.writes if table == "extract_checkpoint"]
    assert [c[2] for c in checkpoints] == ["9"]


@pytest.mark.parametrize(("error", "status"), [(CallBudgetExceeded("x"), "limit"), (EsbAuthError("x"), "auth_failed")])
def test_execute_stops_on_budget_or_auth(error: Exception, status: str) -> None:
    esb = FakeEsb({"/report/sales-menu-cogs": error})
    tasks = [SliceTask(DAY, "sales", str(b)) for b in range(1, 50)]
    outcome = execute(tasks, runtime(), FakeStore(), esb.get, 1, threading.Event(), clock=lambda: V)
    assert outcome.status() == status
    assert len(esb.calls) <= 3  # berhenti menjadwalkan, tidak menghabiskan 49 slice


def test_execute_gate_closed_stops_scheduling() -> None:
    esb = FakeEsb({})
    outcome = execute([SliceTask(DAY, "sales", "7")], runtime(), FakeStore(), esb.get, 1, threading.Event(),
                      gate=lambda: False)  # fmt: skip
    assert outcome.status() == "window_closed" and esb.calls == []


class Client:
    """Klien palsu ber-budget untuk run_cycle/run_backfill."""

    def __init__(self, esb: FakeEsb) -> None:
        self.esb = esb
        self.budget: int | None = None

    @property
    def calls(self) -> int:
        return len(self.esb.calls)

    def reset_budget(self, max_calls: int | None) -> None:
        self.budget = max_calls

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self.esb.get(path, params)


def base_routes() -> dict[str, Any]:
    return {
        "/branch/list/user": {"dataCount": 2, "data": [
            {"branchID": 4, "branchCode": "CK1", "branchName": "Central Kitchen", "flagActive": 1},
            {"branchID": 7, "branchCode": "T07", "branchName": "Toko Tujuh", "flagActive": 1}]},
    }  # fmt: skip


def test_backfill_outside_window_makes_no_calls() -> None:
    settings = EsbSettings(enabled=True, brands=(CFG,), backfill_window=(time(22), time(5)))
    esb = FakeEsb(base_routes())
    store = FakeStore()
    run = run_backfill(
        settings, BrandRuntime(CFG), Client(esb), store, DAY, DAY, ["sales"], threading.Event(),
        now_fn=lambda: datetime(2026, 11, 17, 12, 0, tzinfo=WIB),
    )  # fmt: skip
    assert run.status == "window_closed" and esb.calls == []
    assert store.tables() == ["extract_run"]


def test_backfill_resumes_and_sets_budget() -> None:
    settings = EsbSettings(enabled=True, brands=(CFG,), backfill_max_calls=1234, entities=("sales",))
    esb = FakeEsb(base_routes())
    done = {("sales", "7", date(2026, 11, 15)): V, ("master", "*", date(2026, 11, 17)): V}
    store = FakeStore(done)
    client = Client(esb)
    run = run_backfill(
        settings, BrandRuntime(CFG), client, store, date(2026, 11, 15), DAY, ["sales"], threading.Event(),
        ignore_window=True, now_fn=lambda: datetime(2026, 11, 17, 1, 0, tzinfo=WIB),
    )  # fmt: skip
    assert client.budget == 1234 and run.status == "ok"
    sales_calls = [p for p, _ in esb.calls if p == "/report/sales-menu-cogs"]
    assert sales_calls == ["/report/sales-menu-cogs"]  # hanya 16 Nov toko 7 (15 Nov sudah done; CK bukan toko)
    assert [p for p, _ in esb.calls if p == "/product"] == []  # master sudah done hari ini


def test_run_cycle_incremental_and_master() -> None:
    settings = EsbSettings(enabled=True, brands=(CFG,), entities=("branch", "master", "sales"), gap_fill_days=1,
                           correction_days=(1,))  # fmt: skip
    esb = FakeEsb(base_routes())
    store = FakeStore()
    run = run_cycle(settings, BrandRuntime(CFG), Client(esb), store, datetime(2026, 11, 17, 4, 0, tzinfo=WIB),
                    threading.Event())  # fmt: skip
    paths = [p for p, _ in esb.calls]
    assert paths[0] == "/branch/list/user"
    assert {"/product", "/product/bom", "/menu/browse", "/supplier"} <= set(paths)
    assert paths.count("/report/sales-menu-cogs") == 1
    assert run.status == "ok" and store.tables()[-1] == "extract_run"


def test_run_cycle_auth_failure_marks_brand() -> None:
    settings = EsbSettings(enabled=True, brands=(CFG,))
    esb = FakeEsb({"/branch/list/user": EsbAuthError("ditolak")})
    run = run_cycle(settings, BrandRuntime(CFG), Client(esb), FakeStore(), datetime(2026, 11, 17, 4, tzinfo=WIB),
                    threading.Event())  # fmt: skip
    assert run.status == "auth_failed"


class FailingStore(FakeStore):
    """Insert tabel tertentu gagal (ClickHouse putus / ditolak di tengah slice)."""

    def __init__(self, fail_table: str) -> None:
        super().__init__()
        self.fail_table = fail_table

    def insert(self, table: str, columns: Sequence[str], rows: list[list[Any]]) -> None:
        if table == self.fail_table and rows:
            raise RuntimeError("insert gagal")
        super().insert(table, columns, rows)


SALES_ROW = {"salesNum": "S1", "salesType": "Sales", "menuCode": "M1", "menuName": "Roti", "qty": 1, "price": 5000,
             "total": 5000, "discountTotal": 0, "cogsTotal": 2000, "rowType": "ALACARTE"}  # fmt: skip


def test_checkpoint_not_written_when_mart_insert_fails() -> None:
    esb = FakeEsb({"/report/sales-menu-cogs": {"count": 1, "data": [SALES_ROW]}})
    store = FailingStore("fact_esb_sales_line")
    with pytest.raises(RuntimeError):
        execute([SliceTask(DAY, "sales", "7")], runtime(), store, esb.get, 1, threading.Event(), clock=lambda: V)
    # raw_doc sudah tertulis (urutan), tetapi checkpoint TIDAK: slice dianggap belum selesai dan ditarik ulang.
    assert "extract_checkpoint" not in store.tables()


def test_run_cycle_store_failure_marks_run_error_and_records_it() -> None:
    settings = EsbSettings(enabled=True, brands=(CFG,), entities=("branch",), gap_fill_days=1, correction_days=(1,))
    store = FailingStore("dim_esb_branch")
    run = run_cycle(settings, BrandRuntime(CFG), Client(FakeEsb(base_routes())), store,
                    datetime(2026, 11, 17, 4, tzinfo=WIB), threading.Event())  # fmt: skip
    assert run.status == "error"
    assert "extract_checkpoint" not in store.tables() and store.tables()[-1] == "extract_run"


def test_budget_exhausted_mid_run_keeps_finished_slices_only() -> None:
    def sales(path: str, params: dict[str, Any]) -> Any:
        if params["branchID"] == 7:
            return {"count": 0, "data": []}
        raise CallBudgetExceeded("habis")

    esb = FakeEsb({"/report/sales-menu-cogs": sales})
    store = FakeStore()
    tasks = [SliceTask(DAY, "sales", str(b)) for b in (7, 9, 11)]
    outcome = execute(tasks, runtime(), store, esb.get, 1, threading.Event(), clock=lambda: V)
    assert outcome.budget and outcome.status() == "limit" and outcome.errors == 0 and outcome.slices == 1
    assert [rows[0][2] for _, table, rows in store.writes if table == "extract_checkpoint"] == ["7"]


def test_budget_exhausted_inside_slice_writes_nothing() -> None:
    sm_rows = [{"simpleManufacturingNum": "SM0", "branchID": 4, "bomID": 10, "bomCode": "B10", "bomName": "FIN Donat",
                "manufacturingQty": 1, "statusID": 3}]  # fmt: skip
    esb = FakeEsb(
        {
            "/production/simple-manufacturing": {"count": 1, "data": sm_rows},
            "/production/simple-manufacturing/*": CallBudgetExceeded("habis"),
        }
    )
    store = FakeStore()
    outcome = execute([SliceTask(DAY, "production", "*")], runtime(), store, esb.get, 1, threading.Event(),
                      clock=lambda: V)  # fmt: skip
    assert outcome.budget and store.writes == []  # tidak ada raw, mart, maupun checkpoint parsial


def test_company_override_per_branch_for_bacha() -> None:
    bacha = BrandConfig("bacha", "user-sintetis", "pw-sintetis", "BCF", frozenset(), frozenset(), {12: "BCR"})
    esb = FakeEsb({"/report/sales-menu-cogs": {"count": 1, "data": [SALES_ROW]}})
    retail = run_slice(BrandRuntime(bacha), SliceTask(DAY, "sales", "12"), V, esb.get)
    cafe = run_slice(BrandRuntime(bacha), SliceTask(DAY, "sales", "15"), V, esb.get)
    assert retail.tables["fact_esb_sales_line"][0][1] == "BCR"
    assert cafe.tables["fact_esb_sales_line"][0][1] == "BCF"


def test_sales_slice_supplements_menu_master_with_category_only() -> None:
    bacha = BrandConfig("bacha", "user-sintetis", "pw-sintetis", "BCF", frozenset(), frozenset(), {12: "BCR"})
    with_cat = {**SALES_ROW, "salesNum": "S2", "menuCode": "TB9001", "menuName": "Teh Sintetis",
                "menuCategory": "Minuman", "menuCategoryDetail": "Teh"}  # fmt: skip
    esb = FakeEsb({"/report/sales-menu-cogs": {"count": 2, "data": [SALES_ROW, with_cat]}})
    result = run_slice(BrandRuntime(bacha), SliceTask(DAY, "sales", "12"), V, esb.get)
    # SALES_ROW tanpa menuCategory -> tidak masuk dim; PT dim = PT default brand (bukan override cabang BCR);
    # versi = awal hari penjualan (kalah dari run master berikutnya), bukan versi slice V.
    assert result.tables["dim_esb_menu"] == [
        ["bacha", "BCF", "TB9001", "Teh Sintetis", "Minuman", "Teh", 0, datetime(2026, 11, 16, tzinfo=UTC)]
    ]
    assert len(result.tables["fact_esb_sales_line"]) == 2 and result.rows == 3
    store = FakeStore()
    write_slice(store, "bacha", result, V)
    assert store.tables() == ["raw_doc", "fact_esb_sales_line", "dim_esb_menu", "extract_checkpoint"]


def test_backfill_window_closing_mid_run_across_midnight_stops_scheduling() -> None:
    stamps = iter(
        [
            datetime(2026, 11, 17, 23, 58, tzinfo=WIB),  # gerbang awal
            datetime(2026, 11, 17, 23, 59, tzinfo=WIB),  # hari WIB (today)
            datetime(2026, 11, 18, 0, 10, tzinfo=WIB),  # melewati tengah malam, masih di jendela
            datetime(2026, 11, 18, 0, 20, tzinfo=WIB),
        ]
    )

    def now_fn() -> datetime:
        return next(stamps, datetime(2026, 11, 18, 5, 1, tzinfo=WIB))  # jendela tertutup

    settings = EsbSettings(enabled=True, brands=(CFG,), backfill_window=(time(22), time(5)), workers=1)
    esb = FakeEsb(base_routes())
    store = FakeStore({("master", "*", date(2026, 11, 17)): V})
    run = run_backfill(
        settings, BrandRuntime(CFG), Client(esb), store, date(2026, 11, 12), date(2026, 11, 14), ["sales"],
        threading.Event(), now_fn=now_fn,
    )  # fmt: skip
    days = [params["salesDate"] for path, params in esb.calls if path == "/report/sales-menu-cogs"]
    assert run.status == "window_closed"
    assert days == ["2026-11-14", "2026-11-13"]  # hari terbaru dulu; 12 Nov menunggu malam berikutnya


# ---------------------------------------------------------------- SEC-186: circuit breaker auth_failed

NOW = datetime(2026, 11, 17, 4, tzinfo=WIB)
SMALL = EsbSettings(enabled=True, brands=(CFG,), entities=("branch",), gap_fill_days=1, correction_days=(1,))


def run_statuses(store: FakeStore) -> list[str]:
    return [rows[0][RUN_COLUMNS.index("status")] for _, table, rows in store.writes if table == "extract_run"]


def test_auth_breaker_pure() -> None:
    breaker = AuthBreaker()
    assert breaker.threshold == AUTH_SUSPEND_AFTER == 2
    assert breaker.record("pb", "auth_failed") is False and not breaker.suspended("pb")
    assert breaker.record("pb", "error") is False and breaker.failures("pb") == 1  # error tidak mereset
    assert breaker.record("pb", "auth_failed") is True and breaker.suspended("pb")
    assert breaker.record("pb", "auth_failed") is False  # True hanya sekali
    assert breaker.record("pb", "ok") is False and breaker.suspended("pb")  # tidak pulih tanpa restart
    assert not breaker.suspended("wtz")
    with pytest.raises(ValueError):
        AuthBreaker(0)


@pytest.mark.parametrize("status", ["ok", "partial", "limit"])
def test_auth_breaker_resets_on_login_success(status: str) -> None:
    breaker = AuthBreaker()
    breaker.record("pb", "auth_failed")
    breaker.record("pb", status)
    assert breaker.failures("pb") == 0
    assert breaker.record("pb", "auth_failed") is False and not breaker.suspended("pb")


def test_guarded_cycle_suspends_after_two_auth_failures(caplog: pytest.LogCaptureFixture) -> None:
    esb = FakeEsb({"/branch/list/user": EsbAuthError("ditolak")})
    store = FakeStore()
    rt, client, breaker = BrandRuntime(CFG), Client(esb), AuthBreaker()
    with caplog.at_level(logging.INFO, logger="efn_esb"):
        runs = [guarded_cycle(SMALL, rt, client, store, breaker, NOW, threading.Event()) for _ in range(2)]
        calls_before = len(esb.calls)
        runs += [guarded_cycle(SMALL, rt, client, store, breaker, NOW, threading.Event()) for _ in range(3)]
    assert [r.status for r in runs] == ["auth_failed", "auth_failed"] + ["auth_suspended"] * 3
    assert len(esb.calls) == calls_before  # tidak ada login/GET lagi setelah ditangguhkan
    assert run_statuses(store) == ["auth_failed", "auth_failed"] + ["auth_suspended"] * 3
    suspended_warnings = [
        r for r in caplog.records if r.levelno == logging.WARNING and "auth_suspended" in r.getMessage()
    ]
    assert len(suspended_warnings) == 1  # WARNING satu kali
    text = caplog.text
    assert "pw-sintetis" not in text and "user-sintetis" not in text
    assert "esb_core_password_pb" in suspended_warnings[0].getMessage()


def test_guarded_cycle_success_between_failures_resets() -> None:
    routes: dict[str, Any] = {"/branch/list/user": EsbAuthError("ditolak")}
    esb = FakeEsb(routes)
    store = FakeStore()
    rt, client, breaker = BrandRuntime(CFG), Client(esb), AuthBreaker()
    first = guarded_cycle(SMALL, rt, client, store, breaker, NOW, threading.Event())
    routes.update(base_routes())
    second = guarded_cycle(SMALL, rt, client, store, breaker, NOW, threading.Event())
    routes["/branch/list/user"] = EsbAuthError("ditolak")
    third = guarded_cycle(SMALL, rt, client, store, breaker, NOW, threading.Event())
    assert (first.status, second.status, third.status) == ("auth_failed", "ok", "auth_failed")
    assert not breaker.suspended("pb") and breaker.failures("pb") == 1


def test_suspended_store_failure_does_not_crash() -> None:
    class BrokenStore(FakeStore):
        def insert_control(self, table: str, columns: Sequence[str], rows: list[list[Any]]) -> None:
            raise RuntimeError("clickhouse mati")

    breaker = AuthBreaker(threshold=1)
    breaker.record("pb", "auth_failed")
    esb = FakeEsb(base_routes())
    run = guarded_cycle(SMALL, BrandRuntime(CFG), Client(esb), BrokenStore(), breaker, NOW, threading.Event())
    assert run.status == "auth_suspended" and esb.calls == []


# ---------------------------------------------------------------- SEC-185: --ignore-window di production


def backfill_args(days: int, ignore_window: bool = True) -> argparse.Namespace:
    end = wib_today(datetime.now(UTC)) - timedelta(days=2)
    return argparse.Namespace(
        brand="pb", start=end - timedelta(days=days - 1), end=end, entity=None, force=False, ignore_window=ignore_window
    )


class StoreReached(Exception):
    """Guard lolos: _cmd_backfill sampai ke pembuatan store (I/O)."""


@pytest.fixture
def no_io(monkeypatch: pytest.MonkeyPatch) -> None:
    def _store(_env: Any) -> Any:
        raise StoreReached

    monkeypatch.setattr(cli, "_store", _store)


@pytest.mark.parametrize(("allowed", "days"), [(False, 1), (False, 30), (True, 2), (True, 730)])
def test_cli_ignore_window_rejected_in_production(
    no_io: None, capsys: pytest.CaptureFixture[str], allowed: bool, days: int
) -> None:
    settings = EsbSettings(enabled=True, brands=(CFG,), production=True, allow_ignore_window=allowed)
    assert cli._cmd_backfill(settings, backfill_args(days), threading.Event()) == 2
    err = capsys.readouterr().err
    assert "backfill ditolak" in err and "--ignore-window" in err and "pw-sintetis" not in err


@pytest.mark.parametrize(
    ("production", "allowed", "days", "ignore_window"),
    [(True, True, 1, True), (False, False, 730, True), (True, False, 730, False)],
)
def test_cli_ignore_window_allowed_paths_reach_io(
    no_io: None, production: bool, allowed: bool, days: int, ignore_window: bool
) -> None:
    settings = EsbSettings(enabled=True, brands=(CFG,), production=production, allow_ignore_window=allowed)
    with pytest.raises(StoreReached):
        cli._cmd_backfill(settings, backfill_args(days, ignore_window), threading.Event())


def test_version_now_millisecond_precision() -> None:
    value = version_now(datetime(2026, 11, 17, 1, 2, 3, 456789, tzinfo=UTC))
    assert value.microsecond == 456000 and value.tzinfo == UTC
