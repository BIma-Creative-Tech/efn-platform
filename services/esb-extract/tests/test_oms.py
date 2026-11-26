"""G69 ESB OMS (docs/04 §33): allowlist klien, Basic auth tidak bocor, paginasi, PDP, transform, slice, config, DDL.

Fixture SINTETIS saja (tanpa angka penjualan, nama cabang, atau username nyata).
"""

import base64
import json
import logging
import re
import threading
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from test_insight_access import ACCESS, ESB_POLICY_SH, INIT_SH, insight_views, output_columns

from efn_esb.config import (
    BrandConfig,
    EsbSettings,
    OmsSettings,
    check_oms_prod_lock,
    parse_hours,
    validate_oms_base_url,
)
from efn_esb.http import (
    CallBudgetExceeded,
    EsbAuthError,
    EsbError,
    EsbHttpError,
    HttpResponse,
    MethodNotAllowed,
    TokenBucket,
    check_request,
)
from efn_esb.oms_http import (
    OMS_PATHS,
    P_BRANCH,
    P_BRANCH_SUMMARY,
    P_PAYMENT_METHOD,
    P_SALES_HEAD,
    P_VISIT_PURPOSE,
    OmsClient,
    check_oms_request,
)
from efn_esb.oms_runner import (
    E_DAY,
    E_MASTER,
    E_SALES,
    OmsContext,
    OmsPageLimit,
    OmsParseError,
    extract_sales_slice,
    fetch_sales_head,
    oms_cycle,
    plan_days,
    pull_branches,
    pull_day,
)
from efn_esb.oms_transform import (
    OMS_TABLES,
    BomHeaderRow,
    BomLine,
    SliceKey,
    build_menu_fin,
    parse_day,
    prepare_bill,
    strict_int,
    transform_bills,
    transform_branch_daily,
    transform_payment_methods,
)
from efn_esb.records import raw_rows

ROOT = Path(__file__).resolve().parents[3]
CH = ROOT / "infra" / "clickhouse"
DDL26 = (CH / "init" / "26-g69-esb-oms.sql").read_text(encoding="utf-8")
ESB_ACCESS = (CH / "access" / "esb-access.sql").read_text(encoding="utf-8")

USER = "user-sintetis"
PASSWORD = "sintetis-Rahasia-123"  # gitleaks:allow (fixture palsu, sama dengan test_http.py)
TOKEN = base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
DAY = date(2026, 1, 5)
VERSION = datetime(2026, 1, 6, 0, 0, 0, 123000, tzinfo=UTC)
DROP_KEYS = (
    "memberID", "memberCode", "memberName", "cardNumber", "accountName", "verificationCode", "voucherCode", "notes",
    "cancelNotes", "additionalInfo", "createdBy", "editedBy", "selfOrderID", "salesInfo", "tableName",
    # SEC-OMS-02: kunci baru sintetis yang harus tertangkap substring/whitelist.
    "maskedCardNumber", "approvalCode", "customerPhone", "traceNumber", "edcRefNo",
)  # fmt: skip
CFG = BrandConfig(key="pb", user=USER, password=PASSWORD, company_code="PB")


# ------------------------------------------------------------------ fixture sintetis
def menu(menu_id: str, name: str, qty: str = "1", **extra: Any) -> dict[str, Any]:
    return {
        "menuID": menu_id,
        "menuCode": f"MN{menu_id}",
        "menuName": name,
        "qty": qty,
        "price": "5000.00",
        "originalPrice": "5000.00",
        "total": str(Decimal(qty) * 5000),
        "discountValue": "0",
        "vatValue": "0",
        "statusID": "1",
        "statusName": "Active",
        "salesType": "Sales",
        "cancelNotes": "SENTINEL-CANCEL",
        "notes": "SENTINEL-NOTE",
        "createdBy": "SENTINEL-KASIR",
        **extra,
    }


def bill(num: str, grand: str = "10000.00", status: str = "Paid", member: bool = True) -> dict[str, Any]:
    return {
        "salesNum": num,
        "billNum": f"B{num}",
        "parentLinkSalesNum": "",
        "salesDate": DAY.isoformat(),
        "salesDateIn": "2026-01-05 10:00:00",
        "salesDateOut": "2026-01-05 10:30:00",
        "branchCode": "BR01",
        "branchName": "Cabang Sintetis",
        "memberID": "77" if member else "0",
        "memberCode": "SENTINEL-MBR" if member else "",
        "memberName": "SENTINEL-NAMA" if member else "",
        "tableID": "1",
        "tableName": "SENTINEL-MEJA",
        "customerPhone": "SENTINEL-PHONE",
        "visitPurposeID": "2",
        "visitPurposeName": "Dine In",
        "paxTotal": "2",
        "subtotal": grand,
        "discountTotal": "0.00",
        "menuDiscountTotal": "0",
        "promotionDiscount": "0",
        "otherTaxTotal": "0",
        "vatTotal": "0",
        "grandTotal": grand,
        "voucherTotal": "0",
        "roundingTotal": "0",
        "paymentTotal": grand,
        "billingPrintCount": "1",
        "paymentPrintCount": "1",
        "additionalInfo": "SENTINEL-INFO",
        "promotionID": "0",
        "promotionName": "",
        "statusID": "1",
        "statusName": status,
        "createdBy": "SENTINEL-KASIR",
        "editedBy": "SENTINEL-EDITOR",
        "editedDate": "2026-01-05 10:31:00",
        "salesPayments": [
            {
                "accountName": "SENTINEL-ACC",
                "bankName": "Bank Sintetis",
                "cardNumber": "SENTINEL-CARD",
                "fullPaymentAmount": grand,
                "notes": "SENTINEL-PAYNOTE",
                "paymentAmount": grand,
                "paymentMethodID": "3",
                "paymentMethodName": "Kartu",
                "paymentMethodTypeID": "2",
                "paymentMethodTypeName": "EDC",
                "salesPaymentBackendID": "1",
                "salesPaymentPosID": "1",
                "selfOrderID": "SENTINEL-SELF",
                "verificationCode": "SENTINEL-VER",
                "voucherCode": "SENTINEL-VOUCHER",
                "maskedCardNumber": "SENTINEL-MASKED",
                "approvalCode": "SENTINEL-APPROVAL",
                "traceNumber": "SENTINEL-TRACE",
                "edcRefNo": "SENTINEL-EDC",
                "nested": {"anything": "SENTINEL-NESTED"},
            }
        ],
        "salesMenus": [
            menu("10", "Menu Sintetis A", "2", extras=[{"menuID": "11", "menuName": "Topping", "notes": "SENTINEL-X"}]),
        ],
        "salesInfo": [{"infoKey": "SENTINEL-SALESINFO"}],
    }


def key(version: datetime = VERSION) -> SliceKey:
    return SliceKey("pb", "PB", 7, "BR01", DAY, version)


def all_keys(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        out = {str(k) for k in value}
        for v in value.values():
            out |= all_keys(v)
        return out
    if isinstance(value, list):
        out = set()
        for item in value:
            out |= all_keys(item)
        return out
    return set()


# ------------------------------------------------------------------ fake transport / API / store
class FakeTransport:
    def __init__(self, responses: list[HttpResponse | Exception]) -> None:
        self.responses = list(responses)
        self.sent: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def send(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float
    ) -> HttpResponse:
        self.sent.append((method, url, dict(headers), body))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class NoBucket(TokenBucket):
    def __init__(self) -> None:
        super().__init__(1000.0)

    def acquire(self) -> None:
        return None


def ok(data: Any) -> HttpResponse:
    return HttpResponse(200, json.dumps(data).encode(), {})


def client(transport: FakeTransport, base: str = "https://esbcore.co.id", **kwargs: Any) -> OmsClient:
    return OmsClient(base, USER, PASSWORD, transport, NoBucket(), sleep=lambda _s: None, jitter=lambda: 0.0, **kwargs)


class FakeApi:
    """API OMS palsu: summary 1 cabang (2 struk), sales-head 2 struk lalu halaman berulang."""

    def __init__(self, bills: list[dict[str, Any]] | None = None) -> None:
        self.bills = bills if bills is not None else [bill("S1"), bill("S2", "15000.00")]
        self.log: list[tuple[str, dict[str, Any], int | None]] = []
        self._calls = 0

    @property
    def calls(self) -> int:
        return self._calls

    def reset_budget(self, max_calls: int | None) -> None:
        self._calls = 0

    def post(self, path: str, body: Mapping[str, Any] | None = None, page: int | None = None) -> Any:
        self._calls += 1
        self.log.append((path, dict(body or {}), page))
        if path == P_VISIT_PURPOSE:
            return {"data": [{"visitPurposeID": "1", "visitPurposeName": "Dine In", "flagDineIn": "1"}]}
        if path == P_BRANCH:
            return [{"branchCode": "BR02"}, {"branchCode": "BR01"}, {"branchCode": ""}]
        if path == P_PAYMENT_METHOD:
            assert body == {"filterBranchCode": "BR01"}  # API menolak body kosong; kode cabang terkecil
            return [
                {
                    "paymentMethodTypeID": "1",
                    "paymentMethodTypeName": "Tunai",
                    "paymentMethods": [{"paymentMethodID": "1", "paymentMethodName": "Cash"}],
                }
            ]
        if path == P_BRANCH_SUMMARY:
            day = date.fromisoformat((body or {})["filterSalesDateFrom"]).strftime("%d-%m-%Y")  # format nyata
            return [{"salesDate": day, "branchCode": "BR01", "billTotal": "2", "paxTotal": "4", "subtotal": "25000.00",
                     "discountTotal": "0", "scTotal": "0", "taxTotal": "0", "grandTotal": "25000.00"}]  # fmt: skip
        if path == P_SALES_HEAD:
            return {"data": self.bills}  # < 20 -> halaman terakhir
        raise AssertionError(path)


class FakeStore:
    mart_db = "efn_dwh"
    raw_db = "efn_esb"

    def __init__(self, checkpoints: dict[tuple[str, str, date], datetime] | None = None) -> None:
        self.ops: list[tuple[str, str, list[list[Any]]]] = []
        self._checkpoints = checkpoints or {}

    def insert(self, table: str, columns: Sequence[str], rows: list[list[Any]]) -> None:
        assert tuple(columns) == OMS_TABLES[table]
        self.ops.append(("mart", table, rows))

    def insert_control(self, table: str, columns: Sequence[str], rows: list[list[Any]]) -> None:
        self.ops.append(("control", table, rows))

    def checkpoints(self, brand: str, since: date) -> dict[tuple[str, str, date], datetime]:
        return dict(self._checkpoints)

    def query_rows(self, sql: str, parameters: dict[str, Any] | None = None) -> list[Sequence[Any]]:
        if "dim_esb_branch" in sql:
            return [("BR01", 7)]
        return []

    def written_checkpoints(self) -> list[list[Any]]:
        return [row for kind, table, rows in self.ops if table == "extract_checkpoint" for row in rows]


# ================================================================== allowlist (§33.2, §33.8.1)
@pytest.mark.parametrize("path", sorted(OMS_PATHS))
def test_five_post_paths_allowed_with_basic_auth_and_explicit_user_agent(path: str) -> None:
    transport = FakeTransport([ok({"data": []})])
    api = client(transport)
    body = {"filterSalesDateFrom": "2026-01-05", "filterSalesDateTo": "2026-01-05"}
    if path == P_SALES_HEAD:
        api.post(path, {**body, "filterBranchCode": "BR01"}, page=1)
    elif path == P_BRANCH_SUMMARY:
        api.post(path, body)
    else:
        api.post(path, {})
    method, url, headers, _ = transport.sent[0]
    assert method == "POST"
    assert url == "https://esbcore.co.id" + path + ("?page=1" if path == P_SALES_HEAD else "")
    assert headers["Authorization"] == f"Basic {TOKEN}"
    assert headers["User-Agent"].startswith("efn-esb-extract/") and "urllib" not in headers["User-Agent"].lower()
    assert headers["Content-Type"] == "application/json"


@pytest.mark.parametrize("method", ["GET", "PUT", "PATCH", "DELETE", "post"])
def test_non_post_rejected(method: str) -> None:
    with pytest.raises(MethodNotAllowed):
        check_oms_request(method, P_BRANCH, {}, {})


@pytest.mark.parametrize(
    "path",
    [
        "/external/general/push/sales",
        "/external/general/sales-data",
        "/external/general/create-sales",
        "/external/general/update-menu",
        "/external/general/../general/get-branch",
        "/external/general/get-branch/",
        "/external/general/%67et-branch",
        "/external/general/get-branch?x=1",
        "/auth/login",
        "",
    ],
)
def test_other_paths_rejected_before_network(path: str) -> None:
    transport = FakeTransport([])
    with pytest.raises(MethodNotAllowed):
        client(transport).post(path, {})
    assert transport.sent == []


@pytest.mark.parametrize(
    ("path", "query"),
    [
        (P_SALES_HEAD, {"page": 0}),
        (P_SALES_HEAD, {"page": -1}),
        (P_SALES_HEAD, {"page": "1"}),
        (P_SALES_HEAD, {"page": True}),
        (P_SALES_HEAD, {"page": 10_001}),
        (P_SALES_HEAD, {"limit": 100}),
        (P_BRANCH, {"page": 1}),
    ],
)
def test_query_outside_allowlist_rejected(path: str, query: dict[str, Any]) -> None:
    body = {"filterSalesDateFrom": "2026-01-05"} if path == P_SALES_HEAD else {}
    with pytest.raises(MethodNotAllowed):
        check_oms_request("POST", path, query, body)


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (P_BRANCH, {"filterBranchCode": "BR01"}),
        (P_BRANCH_SUMMARY, {"filterBranchCode": "BR01"}),
        (P_BRANCH_SUMMARY, {"filterSalesDateFrom": "05-01-2026"}),
        (P_SALES_HEAD, {"filterBranchCode": "BR 01; DROP"}),
        (P_SALES_HEAD, {"filterSalesDateFrom": 20260105}),
        (P_SALES_HEAD, ["filterSalesDateFrom"]),
        (P_SALES_HEAD, {"filterSalesDateFrom": "2026-01-05", "filterSalesDateTo": "2026-01-05"}),  # tanpa cabang
    ],
)
def test_body_outside_allowlist_rejected(path: str, body: Any) -> None:
    query = {"page": 1} if path == P_SALES_HEAD else {}
    with pytest.raises(MethodNotAllowed):
        check_oms_request("POST", path, query, body)


def test_sales_head_requires_page() -> None:
    body = {"filterSalesDateFrom": "2026-01-05", "filterSalesDateTo": "2026-01-05", "filterBranchCode": "BR01"}
    check_oms_request("POST", P_SALES_HEAD, {"page": 1}, body)
    with pytest.raises(MethodNotAllowed):
        check_oms_request("POST", P_SALES_HEAD, {}, body)


@pytest.mark.parametrize(
    "base",
    [
        "https://evil.example",
        "https://esbcore.co.id.evil.example",
        "https://services.esb.co.id",
        "http://esbcore.co.id",
        "https://esbcore.co.id:8443",
        "https://u:p@esbcore.co.id",
        "https://esbcore.co.id/external",
        "https://esbcore.co.id?x=1",
    ],
)
def test_other_hosts_rejected(base: str) -> None:
    with pytest.raises(MethodNotAllowed):
        client(FakeTransport([]), base=base)


def test_redirect_not_followed() -> None:
    transport = FakeTransport([HttpResponse(302, b"", {"Location": "https://evil.example/"})])
    with pytest.raises(EsbHttpError):
        client(transport).post(P_BRANCH, {})
    assert len(transport.sent) == 1


def test_core_client_still_get_only() -> None:
    for path in OMS_PATHS:
        with pytest.raises(MethodNotAllowed):
            check_request("POST", path)


def test_budget_and_retry() -> None:
    transport = FakeTransport([HttpResponse(503, b"", {}), ok([])])
    api = client(transport, max_calls=2)
    assert api.post(P_BRANCH, {}) == []
    with pytest.raises(CallBudgetExceeded):
        api.post(P_BRANCH, {})


# ================================================================== Basic auth tidak bocor (§33.8.7)
def test_credentials_never_in_repr_exception_or_log(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    api = client(FakeTransport([HttpResponse(401, b"denied", {})]))
    assert PASSWORD not in repr(api) and TOKEN not in repr(api) and USER not in repr(api)
    with pytest.raises(EsbAuthError) as auth:
        api.post(P_BRANCH, {})
    failing = client(FakeTransport([OSError(f"boom {TOKEN}")] * 6), max_retries=2)
    with pytest.raises(EsbError) as net:
        failing.post(P_BRANCH, {})
    for text in (str(auth.value), str(net.value)):
        assert PASSWORD not in text and TOKEN not in text
    # Siklus penuh dengan 401: log/heartbeat tanpa kredensial.
    store = FakeStore()
    settings = EsbSettings(enabled=True, brands=(CFG,), gap_fill_days=1)
    api2 = client(FakeTransport([HttpResponse(401, b"", {})]))
    run = oms_cycle(settings, OmsSettings(enabled=True), CFG, api2, store, datetime(2026, 1, 6, 5, tzinfo=UTC),
                    threading.Event())  # fmt: skip
    assert run.status == "auth_failed"
    assert PASSWORD not in caplog.text and TOKEN not in caplog.text and USER not in caplog.text


# ================================================================== paginasi (§33.4, §33.8.2)
def _pager(pages: list[list[dict[str, Any]]]) -> tuple[Any, list[int]]:
    seen: list[int] = []

    def post(path: str, body: Mapping[str, Any] | None = None, page: int | None = None) -> Any:
        assert path == P_SALES_HEAD and body is not None and body["filterBranchCode"] == "BR01"
        assert page is not None
        seen.append(page)
        return {"data": pages[min(page, len(pages)) - 1]}  # sesudah halaman terakhir: halaman terakhir diulang

    return post, seen


def test_last_page_exactly_20_then_repeated_page_stops_without_duplicates() -> None:
    page1 = [bill(f"A{i:02d}") for i in range(20)]
    page2 = [bill(f"B{i:02d}") for i in range(20)]
    post, seen = _pager([page1, page2])
    rows, calls = fetch_sales_head(post, DAY, "BR01", max_pages=50)
    assert len(rows) == 40 and len({r["salesNum"] for r in rows}) == 40
    assert seen == [1, 2, 3] and calls == 3


def test_short_page_stops() -> None:
    post, seen = _pager([[bill(f"A{i:02d}") for i in range(20)], [bill("B1")]])
    rows, _ = fetch_sales_head(post, DAY, "BR01", max_pages=50)
    assert len(rows) == 21 and seen == [1, 2]


def test_empty_page_stops() -> None:
    post, seen = _pager([[]])
    assert fetch_sales_head(post, DAY, "BR01", max_pages=5) == ([], 1)


def test_hard_page_limit_marks_slice_failed_without_checkpoint() -> None:
    def endless(path: str, body: Mapping[str, Any] | None = None, page: int | None = None) -> Any:
        return {"data": [bill(f"P{page}-{i:02d}") for i in range(20)]}

    with pytest.raises(OmsPageLimit):
        fetch_sales_head(endless, DAY, "BR01", max_pages=3)

    class Api(FakeApi):
        def post(self, path: str, body: Mapping[str, Any] | None = None, page: int | None = None) -> Any:
            return endless(path, body, page)

    store = FakeStore()
    oms = OmsSettings(enabled=True, max_pages=3)
    ctx = OmsContext(EsbSettings(enabled=True, brands=(CFG,)), oms, CFG, Api(), store, threading.Event())
    outcome = pull_branches(ctx, DAY, ["BR01"], lambda _c: False)
    assert outcome.errors == 1 and outcome.slices == 0 and outcome.status() == "partial"
    assert store.ops == []  # tidak ada raw/mart/checkpoint untuk slice terpotong


# ================================================================== PDP (§33.6, §33.8.4)
def test_pii_and_pci_keys_removed_from_raw_and_mart() -> None:
    raw = [bill("S1"), bill("S2", member=False)]
    raw[0]["salesMenus"].append(
        menu("20", "Paket Sintetis", packages=[menu("21", "Isi Paket", extras=[menu("22", "Extra Isi")])])
    )

    def post(path: str, body: Mapping[str, Any] | None = None, page: int | None = None) -> Any:
        return {"data": raw}

    result = extract_sales_slice(post, CFG, "BR01", 7, DAY, VERSION, 10)
    payload_rows = raw_rows("pb", result.raw, VERSION)
    keys = all_keys(json.loads(payload_rows[0][4]))
    lowered = {k.lower() for k in keys}
    for name in DROP_KEYS:
        assert name.lower() not in lowered, name
    assert "SENTINEL" not in payload_rows[0][4]
    for table, rows in result.tables.items():
        for column in OMS_TABLES[table]:
            assert column.replace("_", "") not in {k.lower() for k in DROP_KEYS}, (table, column)
        assert "SENTINEL" not in repr(rows), table
    bills = result.tables["fact_esb_oms_bill"]
    has_member = {row[5]: row[OMS_TABLES["fact_esb_oms_bill"].index("has_member")] for row in bills}
    assert has_member == {"S1": 1, "S2": 0}


def test_payment_whitelist_and_substring_denylist() -> None:
    raw = {
        "salesPayments": [
            {"paymentMethodID": "1", "paymentAmount": "5", "cardNo": "x", "edcRefNo": "y", "extra": {"a": 1}},
            "bukan-objek",
        ],
        "salesMenus": [
            {
                "menuName": "A",
                "approvalCode": "x",
                "customerEmail": "x",
                "loyaltyAccount": "x",
                "packages": [{"menuName": "B", "maskedCardNumber": "x", "verificationRef": "x"}],
            }
        ],  # fmt: skip
        "MemberTier": "x",
        "driverPhoneNo": "x",
    }
    stripped = prepare_bill(raw).payload
    assert stripped["salesPayments"] == [{"paymentMethodID": "1", "paymentAmount": "5"}]
    assert stripped["salesMenus"] == [{"menuName": "A", "packages": [{"menuName": "B"}]}]
    assert set(stripped) == {"salesPayments", "salesMenus"}


def test_has_member_semantics() -> None:
    assert prepare_bill({"memberID": "0", "memberCode": "", "memberName": None}).has_member == 0
    assert prepare_bill({"memberCode": "X"}).has_member == 1
    assert prepare_bill({}).has_member == 0


# ================================================================== transform (§33.2 angka string)
def test_string_numbers_to_decimal_and_int() -> None:
    out = transform_bills([prepare_bill(bill("S1", "12500.50"))], key())
    row = dict(zip(OMS_TABLES["fact_esb_oms_bill"], out.tables["fact_esb_oms_bill"][0], strict=True))
    assert row["grand_total"] == Decimal("12500.50") and row["pax"] == 2 and row["visit_purpose_id"] == 2
    assert row["sales_ts_out"].isoformat() == "2026-01-05T10:30:00+07:00"
    assert row["is_final"] == 1 and row["_version"] == VERSION
    assert "table_name" not in row  # SEC-OMS-01
    daily = transform_branch_daily(
        [
            {
                "salesDate": "2026-01-05",
                "branchCode": "BR01",
                "paxTotal": "4",
                "billTotal": "2.00",
                "subtotal": "1.5",
                "discountTotal": "0",
                "scTotal": "0",
                "taxTotal": "0",
                "grandTotal": "25000.00",
            }
        ],  # fmt: skip
        "pb",
        lambda _b: "PB",
        {"BR01": 7},
        VERSION,
    )
    assert daily[DAY][0][2] == 7 and daily[DAY][0][5:8] == [4, 2, Decimal("1.50")]


@pytest.mark.parametrize("bad", ["12,500", "Rp 1", "abc", "1.5.0"])
def test_bad_number_fails_slice(bad: str) -> None:
    with pytest.raises(ValueError):
        transform_bills([prepare_bill(bill("S1", bad))], key())


def test_strict_int() -> None:
    assert strict_int("12") == 12 and strict_int("12.00") == 12 and strict_int(None) == 0
    with pytest.raises(ValueError):
        strict_int("1.5")


def test_void_and_open_bills_not_final() -> None:
    out = transform_bills([prepare_bill(bill("S1", status="Void")), prepare_bill(bill("S2", status="Open"))], key())
    idx = OMS_TABLES["fact_esb_oms_bill"].index("is_final")
    assert [r[idx] for r in out.tables["fact_esb_oms_bill"]] == [0, 0]


# ================================================================== flatten paket/extra
def _lines(menus: list[Any]) -> tuple[list[dict[str, Any]], int]:
    raw = bill("S1")
    raw["salesMenus"] = menus
    out = transform_bills([prepare_bill(raw)], key())
    cols = OMS_TABLES["fact_esb_oms_bill_line"]
    return [dict(zip(cols, r, strict=True)) for r in out.tables["fact_esb_oms_bill_line"]], out.errors


def test_packages_and_extras_flattened_depth_first() -> None:
    lines, errors = _lines(
        [menu("20", "Paket", packages=[menu("21", "Isi 1"), menu("22", "Isi 2", extras=[{"menuName": "Extra"}])])]
    )
    assert errors == 0
    shape = [(r["line_no"], r["parent_line_no"], r["line_kind"], r["has_packages"]) for r in lines]
    assert shape == [(1, 0, "menu", 1), (2, 1, "package", 0), (3, 1, "package", 0), (4, 3, "extra", 0)]
    assert lines[0]["menu_key"] == "PAKET"


def test_unknown_child_shapes_counted_not_fatal() -> None:
    lines, errors = _lines([menu("20", "Menu", packages={"x": 1}, extras=["teks", {"foo": 1}]), "bukan-objek"])
    assert len(lines) == 1 and errors == 4


def test_line_no_stable_across_input_order() -> None:
    menus = [menu("30", "C"), menu("10", "A"), menu("20", "B", qty="3")]
    first, _ = _lines(list(menus))
    second, _ = _lines(list(reversed(menus)))
    assert [(r["line_no"], r["menu_id"]) for r in first] == [(r["line_no"], r["menu_id"]) for r in second]


def test_cancelled_line_flag() -> None:
    lines, _ = _lines([menu("10", "A", statusName="Cancel")])
    assert lines[0]["is_cancelled"] == 1


def test_payment_methods_both_shapes() -> None:
    as_list = [{"paymentMethodTypeID": "1", "paymentMethodTypeName": "Tunai",
                "paymentMethods": [{"paymentMethodID": "5", "paymentMethodName": "Cash"}]}]  # fmt: skip
    as_dict = {"data": {"Tunai": as_list[0]}}
    for shape in (as_list, as_dict):
        rows = transform_payment_methods(shape, "pb", "PB", VERSION)
        assert rows == [["pb", "PB", 1, "Tunai", 5, "Cash", VERSION]]


# ================================================================== slice & versi (§33.4, §33.8.3)
def test_slice_write_order_version_and_reconciliation() -> None:
    store = FakeStore()
    settings = EsbSettings(enabled=True, brands=(CFG,), gap_fill_days=1)
    api = FakeApi()
    now = datetime(2026, 1, 6, 5, tzinfo=UTC)  # 12:00 WIB, sesudah jam siap
    run = oms_cycle(settings, OmsSettings(enabled=True), CFG, api, store, now, threading.Event())
    assert run.status == "ok", run
    cps = store.written_checkpoints()
    keys = {(r[1], r[2], r[3]) for r in cps}
    assert (E_SALES, "BR01", date(2026, 1, 6)) in keys  # hari berjalan
    assert (E_DAY, "*", date(2026, 1, 5)) in keys and (E_DAY, "*", date(2025, 12, 30)) in keys  # H-1, H-7
    assert (E_DAY, "*", date(2026, 1, 6)) not in keys  # hari berjalan tidak pernah final
    assert (E_MASTER, "*", date(2026, 1, 6)) in keys
    # Urutan per slice oms_sales: raw_doc -> mart -> checkpoint; _version mart = finished_at checkpoint.
    ops = store.ops
    for i, (_kind, table, rows) in enumerate(ops):
        if table == "extract_checkpoint" and rows[0][1] == E_SALES:
            assert [t for _, t, _ in ops[i - 4 : i]] == [
                "raw_doc", "fact_esb_oms_bill", "fact_esb_oms_bill_line", "fact_esb_oms_bill_payment",
            ]  # fmt: skip
            for _, _, mart_rows in ops[i - 3 : i]:
                assert all(r[-1] == rows[0][7] for r in mart_rows)
    # Rekonsiliasi fixture: Σ grand_total & jumlah struk = sales-branch-summary.
    bills = [r for k, t, rs in ops if t == "fact_esb_oms_bill" for r in rs if r[4] == date(2026, 1, 6)]
    gt = OMS_TABLES["fact_esb_oms_bill"].index("grand_total")
    daily = [r for k, t, rs in ops if t == "fact_esb_oms_branch_daily" for r in rs if r[4] == date(2026, 1, 6)]
    assert len(bills) == daily[0][6] == 2 and sum(r[gt] for r in bills) == daily[0][11]


def test_final_branch_skipped_when_already_final_today() -> None:
    ready = datetime(2026, 1, 5, 21, tzinfo=UTC)  # 04:00 WIB 6 Jan, sesudah jam siap 03:00
    store = FakeStore({(E_SALES, "BR01", date(2026, 1, 5)): ready})
    api = FakeApi()
    settings = EsbSettings(enabled=True, brands=(CFG,), gap_fill_days=1, correction_days=(1,))
    oms = OmsSettings(enabled=True, correction_days=(1,), intraday_hours=(7, 8))
    oms_cycle(settings, oms, CFG, api, store, datetime(2026, 1, 6, 5, tzinfo=UTC), threading.Event())
    assert [p for p, _b, _pg in api.log if p == P_SALES_HEAD] == []  # sudah final -> tidak ditarik ulang
    assert (E_DAY, "*", date(2026, 1, 5)) in {(r[1], r[2], r[3]) for r in store.written_checkpoints()}


def test_plan_days_intraday_and_ready_hour() -> None:
    settings = EsbSettings(enabled=True, gap_fill_days=1)
    oms = OmsSettings(enabled=True)
    early = plan_days(datetime(2026, 1, 5, 19, tzinfo=UTC), settings, oms, {})  # 02:00 WIB
    assert early == []  # di luar intraday, sebelum jam siap
    noon = plan_days(datetime(2026, 1, 6, 5, tzinfo=UTC), settings, oms, {})  # 12:00 WIB
    assert noon == [(date(2026, 1, 6), False), (date(2026, 1, 5), True), (date(2025, 12, 30), True)]


# ================================================================== pemetaan menu -> FIN
def test_build_menu_fin_keys_and_no_fin_marker() -> None:
    headers = [
        BomHeaderRow(1, "MN10", "Menu Sintetis A", "Menu", "", "", True),
        BomHeaderRow(2, "", "Minuman Sintetis", "Menu", "", "", True),
        BomHeaderRow(3, "MN99", "Menu Nonaktif", "Menu", "", "", False),
        BomHeaderRow(4, "WIP1", "Adonan", "Production", "WIP1", "Adonan", True),
    ]
    lines = [
        BomLine(1, "FIN001", "FIN Roti Sintetis", Decimal("1"), "EA"),
        BomLine(1, "PAM01", "Kemasan", Decimal("1"), "PCS"),
        BomLine(2, "RMT01", "Bahan", Decimal("0.2"), "KG"),
    ]
    rows = build_menu_fin(headers, lines, [("MN77", "Minuman Sintetis")], frozenset(), "pb", "PB", VERSION)
    got = {(r[2], r[3], r[4], r[6]) for r in rows}
    assert ("code", "MN10", "bom_code", "FIN001") in got
    assert ("code", "MN77", "menu_price", "") in got  # BOM cocok tanpa FIN
    assert ("name", "MENU SINTETIS A", "name", "FIN001") in got
    assert not any(r[3] in ("MN99", "MENU NONAKTIF", "ADONAN") for r in rows)


# ================================================================== config (§33.3, §33.8.6)
def test_config_defaults_safe() -> None:
    oms = OmsSettings.from_env({})
    assert not oms.enabled and oms.brands == ("pb",) and oms.base_url == "https://esbcore.co.id"
    assert oms.interval_seconds == 900 and oms.intraday_hours == (7, 23) and oms.correction_days == (1, 7)
    assert oms.max_calls_per_run == 5000 and oms.backfill_max_calls == 60_000 and oms.max_pages == 500
    assert OmsSettings.from_env({"ESB_MAX_PAGES": "40"}).max_pages == 40  # ikut ESB_MAX_PAGES
    assert OmsSettings.from_env({"ESB_MAX_PAGES": "40", "ESB_OMS_MAX_PAGES": "9"}).max_pages == 9


def test_config_enabled_requires_core_and_subset() -> None:
    good = {"ESB_OMS_ENABLED": "true", "ESB_ENABLED": "true", "ESB_BRANDS": "pb,wtz", "APP_ENV": "dev"}
    assert OmsSettings.from_env(good).enabled
    with pytest.raises(RuntimeError, match="ESB_ENABLED"):
        OmsSettings.from_env({"ESB_OMS_ENABLED": "true", "ESB_BRANDS": "pb"})
    with pytest.raises(RuntimeError, match="ESB_BRANDS"):
        OmsSettings.from_env({**good, "ESB_BRANDS": "wtz"})
    with pytest.raises(RuntimeError):
        OmsSettings.from_env({"ESB_OMS_BRANDS": "xyz"})


@pytest.mark.parametrize("app_env", [None, "", "production", "staging", "prod"])
def test_production_locked_in_code(app_env: str | None) -> None:
    """SEC-OMS-04: APP_ENV kosong/tidak dikenal = production (fail closed) -> OMS aktif ditolak."""
    env = {"ESB_OMS_ENABLED": "true", "ESB_ENABLED": "true", "ESB_BRANDS": "pb"}
    if app_env is not None:
        env["APP_ENV"] = app_env
    with pytest.raises(RuntimeError, match="ESB_OMS_ALLOW_PRODUCTION"):
        OmsSettings.from_env(env)
    for not_true in ("false", "", "1", "yes", "TRUEX"):
        with pytest.raises(RuntimeError, match="ESB_OMS_ALLOW_PRODUCTION"):
            OmsSettings.from_env({**env, "ESB_OMS_ALLOW_PRODUCTION": not_true})
    assert not OmsSettings.from_env({**env, "ESB_OMS_ENABLED": "false"}).enabled  # mati di production = boleh
    assert OmsSettings.from_env({**env, "ESB_OMS_ALLOW_PRODUCTION": "true"}).enabled  # efn-prod lokal pra-go-live


def test_production_lock_flag() -> None:
    check_oms_prod_lock(True, False, False)  # non-produksi
    check_oms_prod_lock(False, True, False)  # mati
    check_oms_prod_lock(True, True, True)  # flag eksplisit
    with pytest.raises(RuntimeError):
        check_oms_prod_lock(True, True, False)


@pytest.mark.parametrize(
    "url",
    [
        "http://esbcore.co.id",
        "https://evil.example",
        "https://esbcore.co.id:444",
        "https://u:p@esbcore.co.id",
        "https://esbcore.co.id/external/general",
        "https://esbcore.co.id/?q=1",
    ],
)
def test_config_base_url_rejected(url: str) -> None:
    with pytest.raises(RuntimeError):
        validate_oms_base_url(url)
    assert validate_oms_base_url("https://esbcore.co.id:443/") == "https://esbcore.co.id"


@pytest.mark.parametrize("text", ["7-23x", "23-07", "07-25", "07", ""])
def test_config_hours_rejected(text: str) -> None:
    with pytest.raises(RuntimeError):
        parse_hours(text)


def test_kill_switch_no_thread_no_calls() -> None:
    from efn_esb.__main__ import _start_oms

    settings = EsbSettings(enabled=True, brands=(CFG,))
    assert _start_oms(settings, OmsSettings(enabled=False), threading.Event()) is None


# ================================================================== DDL & akses (§33.5, §33.8.8)
OMS_VIEW_COLUMNS = {
    "v_esb_oms_hourly": (
        "sales_date brand company_code branch_id branch_code branch_name hour visit_purpose_name bills pax "
        "gross_sales net_sales items"
    ).split(),
    "v_esb_oms_menu_hourly": (
        "sales_date brand company_code branch_id branch_code hour line_kind menu_code menu_name qty gross_sales "
        "discount net_sales"
    ).split(),
    "v_esb_oms_fin_hourly": (
        "sales_date brand company_code branch_id branch_code hour product_key product_name uom qty_fin"
    ).split(),
    "v_esb_oms_fin_mapping_quality": (
        "sales_date brand company_code branch_id branch_code menu_qty fin_mapped_qty no_fin_qty unmapped_qty "
        "no_code_qty unmapped_pct qty_fin core_fin_sold_qty"
    ).split(),
    "v_esb_oms_recon_daily": (
        "sales_date brand company_code branch_id branch_code branch_name bills_oms grand_total_oms bills_summary "
        "grand_total_summary core_payment_amount diff_bills_pct diff_summary_pct diff_core_pct"
    ).split(),
    "v_esb_oms_freshness": ("brand company_code branch_id branch_code max_sales_ts_out_today last_finished_at").split(),
}


def _table_body(name: str) -> str:
    return DDL26.split(f"CREATE TABLE IF NOT EXISTS efn_dwh.{name}\n", 1)[1].split(";", 1)[0]


def test_ddl_tables_match_transform_columns() -> None:
    created = set(re.findall(r"CREATE TABLE IF NOT EXISTS efn_dwh\.(\w+)", DDL26))
    assert created == set(OMS_TABLES)
    for table, columns in OMS_TABLES.items():
        body = _table_body(table)
        head = body.split("ENGINE", 1)[0]
        assert re.findall(r"^\s{4}(\w+)\s", head, re.MULTILINE) == [*columns, "_loaded_at"], table
        assert "storage_policy = 'encrypted'" in body, table
        if table.startswith("fact_"):
            assert "ReplacingMergeTree(_version)" in body and "PARTITION BY toYYYYMM(sales_date)" in body
            assert "ORDER BY (company_code, brand, branch_code, sales_date" in body
    assert "ENGINE = MergeTree" in _table_body("dim_esb_oms_menu_fin")
    code = re.sub(r"--[^\n]*", "", DDL26)
    assert not re.search(r"\b(DROP|TRUNCATE|ALTER|INSERT|EXCHANGE|RENAME|OPTIMIZE)\b", code)
    assert "SELECT *" not in code
    assert "raw_doc" not in code and "member_" not in code


def test_views_present_columns_and_latest_slice() -> None:
    views = set(re.findall(r"CREATE OR REPLACE VIEW efn_dwh\.(\w+) AS", DDL26))
    assert set(OMS_VIEW_COLUMNS) | {"v_esb_oms_bill", "v_esb_oms_line", "v_esb_oms_menu_fin",
                                    "v_esb_oms_fin_line"} == views  # fmt: skip
    for name, columns in OMS_VIEW_COLUMNS.items():
        assert output_columns(name) == columns, name
    assert output_columns("v_esb_oms_bill")[:6] == [
        "sales_date", "brand", "company_code", "branch_id", "branch_code", "branch_name",
    ]  # fmt: skip
    assert DDL26.count("WHERE entity = 'oms_sales')") >= 2  # struk + baris: hanya versi ber-checkpoint
    assert "WHERE b.is_final = 1" in DDL26 and "l.is_cancelled = 0" in DDL26
    # Resep G43 (memuat HPP) TIDAK dibaca view: pemetaan lewat tabel turunan dim_esb_oms_menu_fin.
    assert "dim_esb_bom" not in re.sub(r"--[^\n]*", "", DDL26)


def test_writer_grants_select_insert_only() -> None:
    code = re.sub(r"--[^\n]*", "", ESB_ACCESS)
    for table in OMS_TABLES:
        lines = [line for line in code.splitlines() if f"efn_dwh.{table} " in line]
        assert lines == [f"GRANT SELECT, INSERT ON efn_dwh.{table} TO esb_writer;"], table
    for path in sorted((CH / "access").glob("*.sql")):
        if path.name in ("esb-access.sql", "insight-access.sql"):
            continue
        assert "_esb_oms_" not in path.read_text(encoding="utf-8"), path.name  # metabase/text2sql/forecast/mig


def test_insight_wrappers_and_row_policy_classes() -> None:
    views = insight_views()
    block = ESB_POLICY_SH.split('ESB_POLICY_VIEWS="\n', 1)[1].split('\n"\n', 1)[0]
    policy = dict(line.split() for line in block.splitlines() if line.strip())
    exposed = set(OMS_VIEW_COLUMNS) | {"v_esb_oms_bill"}
    for name in exposed:
        cols, source = views[name]
        assert source == f"efn_dwh.{name}"
        assert cols == output_columns(name), name
        assert policy[name] == ("PT" if name == "v_esb_oms_recon_daily" else "BR"), name
        if policy[name] == "BR":
            assert {"brand", "branch_id", "company_code"} <= set(cols)
    assert "\nESB_POLICY_EXPECTED=32\n" in INIT_SH  # 25 setelah G68 (#167) + 7 view G69 OMS
    # Pembantu tidak di-wrap; raw_doc tidak pernah di-grant.
    for helper in ("v_esb_oms_line", "v_esb_oms_menu_fin", "v_esb_oms_fin_line"):
        assert f"efn_insight.{helper} " not in ACCESS
    assert "efn_esb.raw_doc" not in re.sub(r"--[^\n]*", "", ACCESS)
    # Tabel pembayaran OMS: hanya definer (penutupan rujukan view cadangan pembayaran init/27), tidak pernah dibungkus.
    pay_grants = re.findall(r"GRANT SELECT ON efn_dwh\.fact_esb_oms_bill_payment TO (\w+);", ACCESS)
    assert pay_grants == ["efn_insight_definer"]
    assert "FROM efn_dwh.fact_esb_oms_bill_payment" not in ACCESS


# ---------------------------------------------------------------- perbaikan data nyata 25 Nov 2026
def test_parse_day_accepts_summary_and_iso_formats() -> None:
    assert parse_day("24-11-2026") == date(2026, 11, 24)  # sales-branch-summary
    assert parse_day("2026-11-24") == date(2026, 11, 24)  # sales-head
    assert parse_day("2026-11-24T08:00:00+07:00") == date(2026, 11, 24)
    for bad in ("", None, "24/11/2026", "31-02-2026", "x"):
        assert parse_day(bad) is None


def test_branch_daily_from_real_summary_format() -> None:
    rows = [{"salesDate": "24-11-2026", "branchCode": "BR01", "paxTotal": "4", "billTotal": "2",
             "subtotal": "25000.0000", "discountTotal": "0", "scTotal": "0", "taxTotal": "0",
             "grandTotal": "25000.0000"}]  # fmt: skip
    by_day = transform_branch_daily(rows, "pb", lambda _b: "PB", {"BR01": 7}, VERSION)
    assert list(by_day) == [date(2026, 11, 24)] and len(by_day[date(2026, 11, 24)]) == 1


def test_unreadable_summary_never_marks_day_final() -> None:
    """Regresi efn-prod 25 Nov 2026: tanggal tak terbaca -> dulu 0 baris + hari ditandai final (data hilang)."""

    class Api(FakeApi):
        def post(self, path: str, body: Mapping[str, Any] | None = None, page: int | None = None) -> Any:
            if path == P_BRANCH_SUMMARY:
                return [{"salesDate": "24/11/2026", "branchCode": "BR01", "billTotal": "1"}]
            return super().post(path, body, page)

    store = FakeStore()
    ctx = OmsContext(EsbSettings(enabled=True, brands=(CFG,)), OmsSettings(enabled=True), CFG, Api([]), store,
                     threading.Event())  # fmt: skip
    with pytest.raises(OmsParseError):
        pull_day(ctx, DAY, True, datetime(2026, 1, 6, 4, 0, tzinfo=UTC))
    assert not [r for r in store.written_checkpoints() if E_DAY in r]
