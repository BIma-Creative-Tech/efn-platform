"""G68 (docs/04 §32.1-§32.4): profit_loss, item_journal, transfer_request, delivery_ref (+ retransform) dengan ESB &
ClickHouse palsu; planning, konfigurasi, CLI. Semua fixture SINTETIS (nama, kode, angka karangan; tidak ada data klien).
DDL/view/akses statis: tests/test_g68_ddl.py; perilaku view di ClickHouse 24.8: tests/clickhouse/g68_views_harness.sh.
(Semula test_g67.py; dinomori ulang G68 saat merge #165 "Margin bahan" = G67.)
"""

import argparse
import json
import os
import threading
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from test_runner import CFG, Client, FakeEsb, FakeStore, V, base_routes

import efn_esb.__main__ as cli
from efn_esb.config import (
    BACKFILL_ENTITIES,
    DAILY_ENTITIES,
    DEFAULT_ENTITIES,
    ENTITIES,
    BrandConfig,
    EsbSettings,
    parse_entities,
    parse_pl_refresh_months,
)
from efn_esb.extract import BrandRuntime, extractor_for, run_slice
from efn_esb.extract_g68 import G68_EXTRACTORS, GTR_PATH, ITEM_JOURNAL_PATH, PL_PATH
from efn_esb.http import check_request
from efn_esb.planning import (
    WIB,
    SliceTask,
    pl_backfill_tasks,
    pl_month,
    pl_refresh_years,
    pl_run_tasks,
    wib_today,
)
from efn_esb.records import RAW_COLUMNS, raw_rows
from efn_esb.runner import ALL_TABLES, execute, run_backfill, run_cycle, run_retransform, write_slice
from efn_esb.transform_g68 import (
    FACT_DELIVERY_REF_LINE_COLUMNS,
    FACT_ITEM_JOURNAL_LINE_COLUMNS,
    FACT_PROFIT_LOSS_COLUMNS,
    FACT_TRANSFER_REQUEST_LINE_COLUMNS,
    G68_TABLES,
    RawMissing,
    account_key,
    delivery_details_from_raw,
    gtr_product_pairs,
    journal_line_keys,
    line_value,
    parse_period_label,
    pl_detail_values,
    transform_delivery_ref,
    transform_item_journal,
    transform_profit_loss,
    transform_transfer_requests,
)

DAY = date(2026, 10, 15)
V2 = datetime(2026, 11, 18, 3, 0, 0, 2000, tzinfo=UTC)
V3 = datetime(2026, 11, 19, 3, 0, 0, 3000, tzinfo=UTC)
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")  # fmt: skip
PII = ("pembuat.sintetis", "penyetuju.sintetis", "info-tambahan-sintetis", "pp:rencana-sintetis", "aset-sintetis",
       "pencetak.sintetis")  # fmt: skip
BACHA = BrandConfig("bacha", "user-sintetis", "pw-sintetis", "BCF", frozenset(), frozenset(), {6: "BCR"})
PL = {name: i for i, name in enumerate(FACT_PROFIT_LOSS_COLUMNS)}
IJ = {name: i for i, name in enumerate(FACT_ITEM_JOURNAL_LINE_COLUMNS)}
TR = {name: i for i, name in enumerate(FACT_TRANSFER_REQUEST_LINE_COLUMNS)}
DR = {name: i for i, name in enumerate(FACT_DELIVERY_REF_LINE_COLUMNS)}


def runtime(cfg: BrandConfig = CFG) -> BrandRuntime:
    rt = BrandRuntime(cfg)
    rt.branch_rows = [
        [cfg.key, cfg.company_for(4), 4, "CK1", "CK Sintetis", 1, 0, 1, V],
        [cfg.key, cfg.company_for(7), 7, "T07", "Toko Tujuh", 0, 1, 1, V],
        [cfg.key, cfg.company_for(8), 8, "HO", "Head Office Sintetis", 0, 0, 1, V],
        [cfg.key, cfg.company_for(9), 9, "T09", "Toko Tutup", 0, 1, 0, V],
    ]
    return rt


# ================================================================== profit_loss: fixture
def pl_detail(branch: int | None, year: int, months: dict[int, Any], current: Any, area_id: int | None = None) -> Any:
    prefix = "" if branch is None else f"{branch}-"
    detail: dict[str, Any] = {"periods": [{"key": f"{prefix}{MONTHS[m - 1]} {year}", "value": v} for m, v in
                                          months.items()]}  # fmt: skip
    if current is not None:
        detail["area"] = {"id": branch if area_id is None else area_id, "key": "Toko Sintetis", "value": current}
    return detail


def acct(account: str, name: str, details: list[Any]) -> dict[str, Any]:
    return {"account": account, "description": name, "details": details, "createdBy": PII[0]}


def pl_payload(branch: int = 12, year: int = 2026) -> dict[str, Any]:
    """Bulan M = 3 (Maret): Januari-Februari di periods, Maret di area.value."""
    return {
        "sales": [
            acct("4  1 10 10", "Penjualan Roti", [pl_detail(branch, year, {1: 1000.5, 2: "2000"}, 3000)]),
            acct("4 1 10 10", "Penjualan Roti", [pl_detail(branch, year, {1: 1, 2: 1}, 1)]),  # akun ganda -> dijumlah
        ],
        "cogs": [acct("5 1", "HPP Bahan", [pl_detail(branch, year, {1: -400, 2: -500}, -600)])],
        "summaries": {
            "netSales": {
                "description": "Penjualan Bersih",
                "total": 5700,
                "allTotal": 1,
                "details": [pl_detail(branch, year, {1: 900, 2: 1900}, 2900)],
            },  # fmt: skip
            "grossProfit": {"description": "Laba Kotor", "details": [], "total": 2300},  # hanya total -> bulan M
            "netProfitLoss": {"description": "Laba Bersih", "total": None},  # tanpa angka -> tidak ada baris
        },
        "dynamicHeaders": {"details": [], "totalInCurrentPeriod": f"{branch}-March {year}"},
        "printedBy": PII[5],
        "approvedByName": PII[1],
    }


def pl_response(branch: int | None, year: int, month: int) -> dict[str, Any]:
    """Respons sintetis berbentuk probe untuk bulan M = `month` (brand: dua area cabang)."""
    before = {m: m * 10 for m in range(1, month)}
    if branch is None:
        details = [pl_detail(7, year, before, 100, 7), pl_detail(12, year, before, 200, 12)]
    else:
        details = [pl_detail(branch, year, before, 100)]
    return {
        "sales": [acct("4 1", "Penjualan", details)],
        "summaries": {"netSales": {"description": "Penjualan Bersih", "details": details, "total": 1}},
        "createdBy": PII[0],
        "notes": PII[2],
    }


def pl_route(_path: str, params: dict[str, Any]) -> Any:
    return pl_response(params.get("branchIDs"), params["year"], params["month"])


# ================================================================== profit_loss: murni
def test_parse_period_label_and_account_key() -> None:
    assert parse_period_label("12-January 2026", 12, 2026) == date(2026, 1, 1)
    assert parse_period_label(" 12 - december 2026 ", 12, 2026) == date(2026, 12, 1)
    assert parse_period_label("March 2026", None, 2026) == date(2026, 3, 1)  # brand: prefix opsional
    assert parse_period_label("7-March 2026", None, 2026) == date(2026, 3, 1)
    for label, branch in (
        ("12-Januari 2026", 12),  # bukan bahasa Inggris
        ("13-January 2026", 12),  # prefix cabang lain
        ("January 2026", 12),  # scope cabang tanpa prefix
        ("12-January 2025", 12),  # tahun lain
        ("12-2026-01", 12),
        ("", 12),
        (None, None),
    ):
        with pytest.raises(ValueError):
            parse_period_label(label, branch, 2026)
    assert account_key("4 1 10 10") == "4.1.10.10" and account_key("") == ""


def test_transform_profit_loss_rows_dup_summary_and_decimal() -> None:
    out = transform_profit_loss(pl_payload(), "pb", "PB", 12, 2026, 3, V)
    assert all(len(r) == len(FACT_PROFIT_LOSS_COLUMNS) for r in out) and all(r[-1] == V for r in out)
    rows = {(r[PL["line_type"]], r[PL["section"]], r[PL["account_no"]], r[PL["period_month"]]): r for r in out}
    assert len(out) == 10
    sales = [rows[("account", "sales", "4 1 10 10", date(2026, m, 1))] for m in (1, 2, 3)]
    assert [r[PL["amount"]] for r in sales] == [Decimal("1001.50"), Decimal("2001.00"), Decimal("3001.00")]
    assert {r[PL["source_lines"]] for r in sales} == {2}  # dup_account
    assert sales[0][PL["account_key"]] == "4.1.10.10" and sales[0][PL["account_name"]] == "Penjualan Roti"
    assert str(sales[0][PL["amount"]]) == "1001.50" and isinstance(sales[0][PL["amount"]], Decimal)
    cogs = rows[("account", "cogs", "5 1", date(2026, 3, 1))]
    assert cogs[PL["amount"]] == Decimal("-600.00") and cogs[PL["source_lines"]] == 1  # tanda apa adanya
    net = [rows[("summary", "netSales", "", date(2026, m, 1))][PL["amount"]] for m in (1, 2, 3)]
    assert net == [Decimal("900.00"), Decimal("1900.00"), Decimal("2900.00")]
    gross = [k for k in rows if k[1] == "grossProfit"]
    assert gross == [("summary", "grossProfit", "", date(2026, 3, 1))]
    assert rows[gross[0]][PL["amount"]] == Decimal("2300.00")
    assert not [k for k in rows if k[1] == "netProfitLoss"]
    first = dict(zip(FACT_PROFIT_LOSS_COLUMNS, out[0], strict=True))
    assert first["scope"] == "branch" and first["branch_id"] == 12 and first["slice_year"] == 2026
    assert first["company_code"] == "PB" and first["brand"] == "pb"


def test_transform_profit_loss_brand_scope_sums_areas() -> None:
    payload = pl_response(None, 2026, 3)
    out = transform_profit_loss(payload, "pb", "PB", 0, 2026, 3, V)
    acct_rows = {r[PL["period_month"]]: r for r in out if r[PL["line_type"]] == "account"}
    assert acct_rows[date(2026, 1, 1)][PL["amount"]] == Decimal("20.00")  # 10 (cabang 7) + 10 (cabang 12)
    assert acct_rows[date(2026, 3, 1)][PL["amount"]] == Decimal("300.00")  # area.value 100 + 200
    assert {r[PL["scope"]] for r in out} == {"brand"} and {r[PL["branch_id"]] for r in out} == {0}


@pytest.mark.parametrize(
    "details",
    [
        [pl_detail(12, 2026, {1: 1}, 5, area_id=13)],  # area.id cabang lain
        [pl_detail(12, 2026, {1: 1}, None)],  # area (bulan M) tidak ada pada scope cabang
        [pl_detail(12, 2026, {4: 1}, 5)],  # periods bulan setelah M
        [pl_detail(12, 2026, {3: 4}, 5)],  # periods bulan M != area.value
        [
            {
                "periods": [{"key": "12-January 2026", "value": 1}, {"key": "12-January 2026", "value": 2}],
                "area": {"id": 12, "value": 1},
            }
        ],  # label ganda  # fmt: skip
        [pl_detail(12, 2026, {1: "satu"}, 5)],  # angka rusak
        "bukan-list",
        ["bukan-objek"],
    ],
)
def test_pl_detail_values_fail_closed(details: Any) -> None:
    with pytest.raises(ValueError):
        pl_detail_values(details, 12, 2026, 3)


def test_pl_detail_values_accepts_equal_month_m_in_periods() -> None:
    assert pl_detail_values([pl_detail(12, 2026, {3: 5}, 5)], 12, 2026, 3) == {date(2026, 3, 1): Decimal("5.00")}
    assert pl_detail_values(None, 12, 2026, 3) == {}


def test_transform_profit_loss_rejects_bad_sections() -> None:
    with pytest.raises(ValueError):
        transform_profit_loss({"sales": {"account": "4"}}, "pb", "PB", 12, 2026, 3, V)
    with pytest.raises(ValueError):
        transform_profit_loss({"summaries": ["x"]}, "pb", "PB", 12, 2026, 3, V)
    with pytest.raises(ValueError):
        transform_profit_loss({}, "pb", "PB", 12, 2026, 13, V)
    assert transform_profit_loss({}, "pb", "PB", 12, 2026, 3, V) == []


# ================================================================== profit_loss: extractor
def test_extract_profit_loss_branch_params_whitelist_and_raw() -> None:
    esb = FakeEsb({PL_PATH: pl_route})
    result = run_slice(runtime(), SliceTask(date(2026, 1, 1), "profit_loss", "12"), V, esb.get)
    # V = 17 Nov 2026 WIB -> tahun berjalan: month = 11. branchIDs = integer tunggal, tanpa `branchIDs[]`.
    assert esb.calls == [(PL_PATH, {"year": 2026, "month": 11, "branchIDs": 12})]
    facts = result.tables["fact_esb_profit_loss"]
    assert {r[PL["period_month"]] for r in facts} == {date(2026, m, 1) for m in range(1, 12)}
    assert {r[PL["company_code"]] for r in facts} == {"PB"} and result.calls == 1
    raw = raw_rows("pb", result.raw, V)[0]
    assert raw[RAW_COLUMNS.index("endpoint")] == "report/profit-loss/month-to-month"
    assert raw[RAW_COLUMNS.index("doc_key")] == "2026|12" and raw[RAW_COLUMNS.index("doc_date")] == date(2026, 1, 1)
    payload = raw[RAW_COLUMNS.index("payload")] + json.dumps(facts, default=str)
    for secret in PII:
        assert secret not in payload, secret
    assert "createdBy" not in result.raw[0].payload["sales"][0] and "notes" not in result.raw[0].payload


def test_extract_profit_loss_brand_scope_and_past_year() -> None:
    esb = FakeEsb({PL_PATH: pl_route})
    # Positif: PB satu PT -> total brand ber-PT default.
    brand = run_slice(runtime(), SliceTask(date(2026, 1, 1), "profit_loss", "*"), V, esb.get)
    assert esb.calls[0] == (PL_PATH, {"year": 2026, "month": 11})  # total brand: tanpa branchIDs
    facts = brand.tables["fact_esb_profit_loss"]
    assert {r[PL["scope"]] for r in facts} == {"brand"} and {r[PL["company_code"]] for r in facts} == {"PB"}
    assert brand.raw[0].doc_key == "2026|*" and not CFG.multi_company
    # Negatif SEC-G68-01: Bacha multi-PT (BCF + override 6=BCR) -> total brand company_code '' (row policy membuang).
    multi = run_slice(runtime(BACHA), SliceTask(date(2026, 1, 1), "profit_loss", "*"), V, esb.get)
    assert BACHA.multi_company and {r[PL["company_code"]] for r in multi.tables["fact_esb_profit_loss"]} == {""}
    same_pt = BrandConfig("bacha", "u", "p", "BCF", frozenset(), frozenset(), {6: "BCF"})
    assert not same_pt.multi_company  # override ke PT yang sama bukan multi-PT
    past = run_slice(runtime(BACHA), SliceTask(date(2025, 1, 1), "profit_loss", "6"), V, esb.get)
    assert esb.calls[2] == (PL_PATH, {"year": 2025, "month": 12, "branchIDs": 6})  # tahun lampau: month 12
    assert {r[PL["company_code"]] for r in past.tables["fact_esb_profit_loss"]} == {"BCR"}  # override cabang


def test_extract_profit_loss_rejects_non_january_slice_and_non_object() -> None:
    with pytest.raises(ValueError):
        run_slice(runtime(), SliceTask(date(2026, 2, 1), "profit_loss", "12"), V, FakeEsb({PL_PATH: pl_route}).get)
    with pytest.raises(ValueError):
        run_slice(runtime(), SliceTask(date(2026, 1, 1), "profit_loss", "12"), V, FakeEsb({PL_PATH: [1, 2]}).get)


# ================================================================== item_journal
def ij_list(num: str, day: str = "2026-10-15T00:00:00", **extra: Any) -> dict[str, Any]:
    row = {
        "itemJournalNum": num, "itemJournalDate": day, "branchID": 7, "branchName": "Toko Tujuh", "locationID": 3,
        "locationName": "Gudang Toko", "statusID": 3, "statusName": "Authorized",
        "additionalInfo": PII[2], "createdBy": PII[0],
    }  # fmt: skip
    row.update(extra)
    return row


def ij_line(line_id: Any, purpose: str, pid: int, qty: Any, hpp: Any) -> dict[str, Any]:
    return {"ID": line_id, "purposeID": 1, "purposeName": purpose, "productDetailID": pid * 10, "productID": pid,
            "productName": f"Bahan {pid}", "uomName": "KG", "qty": qty, "hpp": hpp, "notes": PII[2]}  # fmt: skip


def ij_detail(num: str, lines: list[Any], **extra: Any) -> dict[str, Any]:
    row = {
        **ij_list(num), "requestTemplateName": "T" * 200, "itemJournalDetails": lines,
        "creatorFullName": PII[0], "authorizerName": PII[1], "editor": PII[0],
    }  # fmt: skip
    row.update(extra)
    return row


def test_journal_line_keys_and_value() -> None:
    lines = [ij_line(11, "WASTE", 2, -1, 10), ij_line(12, "WASTE", 1, -2, 10)]
    assert [(d, n) for d, n, _ in journal_line_keys(lines)] == [(11, 0), (12, 0)]
    dup = [ij_line(5, "WASTE", 2, -1, 10), ij_line(5, "UNIFORM", 1, -2, 10), ij_line(None, "WASTE", 1, -1, 10)]
    keys = [(d, n, line["productID"]) for d, n, line in journal_line_keys(dup)]
    again = [(d, n, line["productID"]) for d, n, line in journal_line_keys(list(reversed(dup)))]
    assert keys == again and [k[:2] for k in keys] == [(0, 1), (0, 2), (0, 3)]  # seluruh dokumen fallback, stabil
    assert line_value(Decimal("-2.500"), Decimal("1234.5678")) == Decimal("-3086.42")  # ROUND_HALF_UP
    assert line_value(Decimal("0.005"), Decimal("1.0000")) == Decimal("0.01")


def test_transform_item_journal_rows() -> None:
    docs = [
        ij_detail("IJ/2", [ij_line(21, "WASTE", 5, "-2.5", "1234.5678")]),
        ij_detail("", [ij_line(1, "X", 1, 1, 1)]),  # tanpa nomor -> dibuang
        ij_detail("IJ/2", [ij_line(99, "DUPLIKAT", 9, 1, 1)]),  # nomor ganda -> yang pertama
    ]
    out = transform_item_journal(docs, "bacha", BACHA.company_for, DAY, V)
    assert len(out) == 1 and len(out[0]) == len(FACT_ITEM_JOURNAL_LINE_COLUMNS)
    row = dict(zip(FACT_ITEM_JOURNAL_LINE_COLUMNS, out[0], strict=True))
    assert row["journal_num"] == "IJ/2" and row["detail_id"] == 21 and row["line_no"] == 0
    assert row["journal_date"] == DAY and row["branch_id"] == 7 and row["company_code"] == "BCF"
    assert row["qty"] == Decimal("-2.500") and row["hpp"] == Decimal("1234.5678")
    assert row["value"] == Decimal("-3086.42") and row["purpose_name"] == "WASTE"
    assert row["status_id"] == 3 and row["status_name"] == "Authorized" and len(row["template_name"]) == 128
    assert row["product_id"] == 5 and row["product_detail_id"] == 50 and row["uom"] == "KG"
    with pytest.raises(ValueError):
        transform_item_journal([ij_detail("IJ/3", [ij_line(1, "WASTE", 1, "dua", 1)])], "pb", CFG.company_for, DAY, V)


def test_extract_item_journal_params_detail_once_and_pdp() -> None:
    detail = ij_detail("IJ/001", [ij_line(1, "WASTE", 1, -1, 1000)])
    esb = FakeEsb({
        ITEM_JOURNAL_PATH: {"count": 2, "data": [ij_list("IJ/001"), ij_list("IJ/001")]},
        f"{ITEM_JOURNAL_PATH}/*": detail,
    })  # fmt: skip
    result = run_slice(runtime(), SliceTask(DAY, "item_journal", "*"), V, esb.get)
    assert esb.calls == [
        (ITEM_JOURNAL_PATH, {"dateFrom": "2026-10-15", "dateTo": "2026-10-15", "page": 1, "limit": 100}),
        (f"{ITEM_JOURNAL_PATH}/IJ%2F001", {}),
    ]
    raws = raw_rows("pb", result.raw, V)
    assert [(r[RAW_COLUMNS.index("endpoint")], r[RAW_COLUMNS.index("doc_key")]) for r in raws] == [
        ("inventory/item-journal", "2026-10-15"),
        ("inventory/item-journal/{num}", "IJ/001"),
    ]
    payload = json.dumps([r[RAW_COLUMNS.index("payload")] for r in raws]) + json.dumps(
        result.tables["fact_esb_item_journal_line"], default=str
    )
    for secret in PII:
        assert secret not in payload, secret
    assert "IJ/001" in payload and result.calls == 2
    assert result.tables["fact_esb_item_journal_line"][0][IJ["value"]] == Decimal("-1000.00")


def test_item_journal_date_or_number_mismatch_writes_nothing() -> None:
    for routes in (
        {ITEM_JOURNAL_PATH: {"count": 1, "data": [ij_list("IJ/1", "2026-10-14")]}},
        {ITEM_JOURNAL_PATH: {"count": 1, "data": [ij_list("IJ/1", "")]}},
        {
            ITEM_JOURNAL_PATH: {"count": 1, "data": [ij_list("IJ/1")]},
            f"{ITEM_JOURNAL_PATH}/*": ij_detail("IJ/999", []),
        },  # fmt: skip
    ):
        store = FakeStore()
        outcome = execute([SliceTask(DAY, "item_journal", "*")], runtime(), store, FakeEsb(routes).get, 1,
                          threading.Event(), clock=lambda: V)  # fmt: skip
        assert outcome.errors == 1 and store.writes == []  # tanpa raw, fakta, maupun checkpoint


# ================================================================== transfer_request
def gtr_list(num: str, **extra: Any) -> dict[str, Any]:
    row = {
        "transferNum": num, "transferDate": "2026-10-15", "originBranch": "CK Sintetis",
        "destinationBranch": "Toko Enam", "statusID": 3, "statusName": "Authorized", "finishTransferRequest": True,
        "purchaseRequestNum": "PR/1", "createdBy": PII[0],
    }  # fmt: skip
    row.update(extra)
    return row


def gtr_line(code: str, pid: int, uom: str, req: Any, qty: Any, name: str | None = None) -> dict[str, Any]:
    return {"productDetailID": pid * 10, "productCode": code, "productName": name or f"Bahan {pid}",
            "productID": pid, "uomName": uom, "availableQty": 1, "stockQty": 2, "requestQty": req, "pendingQty": 0,
            "qty": qty}  # fmt: skip


def gtr_detail(num: str, lines: list[Any], origin: int = 4, dest: int = 6, **extra: Any) -> dict[str, Any]:
    row = {
        "transferNum": num, "transferDate": "2026-10-15", "originBranchID": origin, "destinationBranchID": dest,
        "originLocationID": 1, "destinationLocationID": 2, "categoryTypeName": "Bahan", "statusID": 3,
        "statusName": "Authorized", "bypassGoodsDelivery": 0, "transferDetails": lines,
        "additionalInfo": PII[3], "creatorName": PII[0], "authorizerFullName": PII[1],
        "transferDetailAssets": [{"assetName": PII[4]}],
    }  # fmt: skip
    row.update(extra)
    return row


def test_transform_transfer_requests_direction_merge_and_resolution() -> None:
    lines = [
        gtr_line("P1", 1, "KG", 2, 2),
        gtr_line("P1", 1, "KG", "3.5", 1),  # baris ganda -> dijumlah
        gtr_line("P2", 2, "PCS", 1, 1),
        gtr_line("", 77, "KG", 4, 4),  # dim_esb_product_id
        gtr_line("", 88, "KG", 5, 5, name="Gula  pasir"),  # alias nama
        gtr_line("", 99, "LTR", 6, 6, name="Tak Dikenal"),  # tetap disimpan, kode ''
    ]
    doc = {**gtr_list("TF/1"), **gtr_detail("TF/1", lines)}
    out = transform_transfer_requests([doc], "bacha", BACHA.company_for, {77: "RM77"}, {"GULA PASIR": "RM88"}, DAY, V)
    assert all(len(r) == len(FACT_TRANSFER_REQUEST_LINE_COLUMNS) for r in out)
    rows = {(r[TR["product_code"]], r[TR["uom"]]): dict(zip(FACT_TRANSFER_REQUEST_LINE_COLUMNS, r, strict=True))
            for r in out}  # fmt: skip
    assert set(rows) == {("P1", "KG"), ("P2", "PCS"), ("RM77", "KG"), ("RM88", "KG"), ("", "LTR")}
    p1 = rows[("P1", "KG")]
    assert (p1["request_qty"], p1["qty"], p1["stock_qty"]) == (Decimal("5.500"), Decimal("3.000"), Decimal("4.000"))
    # Arah [B]: origin = pemasok (CK), destination = peminta (outlet); PT = PT peminta (cabang 6 -> BCR, L-276).
    assert (p1["requester_branch_id"], p1["supplier_branch_id"], p1["company_code"]) == (6, 4, "BCR")
    assert p1["finished"] == 1 and p1["bypass_gd"] == 0 and p1["purchase_request_num"] == "PR/1"
    assert p1["status_id"] == 3 and p1["category_type"] == "Bahan" and p1["transfer_date"] == DAY


def test_gtr_product_pairs_drop_ambiguous_and_empty() -> None:
    docs = [{"transferDetails": [gtr_line("P1", 1, "KG", 1, 1), gtr_line("P1X", 1, "KG", 1, 1),
                                 gtr_line("P2", 2, "KG", 1, 1), gtr_line("", 3, "KG", 1, 1),
                                 gtr_line("P4", 0, "KG", 1, 1), "bukan-objek"]}]  # fmt: skip
    assert gtr_product_pairs(docs) == {2: "P2"}


def test_extract_transfer_request_dim_product_id_and_pdp() -> None:
    detail = gtr_detail("TF/9", [gtr_line("P1", 1, "KG", 2, 2), gtr_line("", 1, "KG", 1, 1)], dest=7)
    esb = FakeEsb({GTR_PATH: {"count": 1, "data": [gtr_list("TF/9")]}, f"{GTR_PATH}/*": detail})
    rt = runtime()
    result = run_slice(rt, SliceTask(DAY, "transfer_request", "*"), V, esb.get)
    assert esb.calls[1] == (f"{GTR_PATH}/TF%2F9", {})
    facts = result.tables["fact_esb_transfer_request_line"]
    assert [(r[TR["product_code"]], r[TR["request_qty"]]) for r in facts] == [("P1", Decimal("3.000"))]  # kode dari
    # pasangan slice yang sama (productID 1 -> P1), lalu dijumlah dengan baris P1.
    assert result.tables["dim_esb_product_id"] == [["pb", "PB", 1, "P1", "gtr", V]]
    payload = json.dumps([r[RAW_COLUMNS.index("payload")] for r in raw_rows("pb", result.raw, V)]) + json.dumps(
        facts, default=str
    )
    for secret in PII:
        assert secret not in payload, secret
    assert "transferDetailAssets" not in payload and "TF/9" in payload


def test_transfer_request_date_mismatch_writes_nothing() -> None:
    routes = {GTR_PATH: {"count": 1, "data": [gtr_list("TF/1", transferDate="2026-10-16")]}}
    store = FakeStore()
    outcome = execute([SliceTask(DAY, "transfer_request", "*")], runtime(), store, FakeEsb(routes).get, 1,
                      threading.Event(), clock=lambda: V)  # fmt: skip
    assert outcome.errors == 1 and store.writes == []


# ================================================================== delivery_ref
def gd_list(num: str, status: int = 3) -> dict[str, Any]:
    return {"goodsDeliveryNum": num, "statusID": status, "originBranchID": 4, "destinationBranchID": 7,
            "createdBy": PII[0]}  # fmt: skip


def gd_line(code: str, uom: str, sent: Any, received: Any, **extra: Any) -> dict[str, Any]:
    return {"productCode": code, "productName": f"Produk {code}", "uomName": uom, "deliveryQty": sent,
            "receiptQty": received, **extra}  # fmt: skip


def gd_detail(num: str, ref: str, lines: list[Any]) -> dict[str, Any]:
    return {"goodsDeliveryNum": num, "originBranchID": 4, "destinationBranchID": 7, "referenceNumber": ref,
            "transType": "Transfer Request", "refDate": "2026-10-14", "transferDate": "2026-10-14",
            "goodsDeliveryDetails": lines, "driverName": PII[0]}  # fmt: skip


GD_DOCS = [
    {**gd_list("GD/1"), **gd_detail("GD/1", "TF/1", [
        gd_line("P1", "EA", 5, 4), gd_line("P1", "EA", 2, 2), gd_line("P2", "EA", 1, None), gd_line("", "EA", 9, 9),
        gd_line("P3", "EA", 3, 3, referenceNumber="TF/9"),
    ])},
    {**gd_list("GD/2", status=33), **gd_detail("GD/2", "TF/2", [gd_line("P1", "EA", 1, 1)])},
    {**gd_detail("", "TF/3", [gd_line("P1", "EA", 1, 1)])},
]  # fmt: skip


def test_transform_delivery_ref_separate_sent_received_and_reference() -> None:
    out = transform_delivery_ref(GD_DOCS, "bacha", BACHA.company_for, DAY, V)
    assert all(len(r) == len(FACT_DELIVERY_REF_LINE_COLUMNS) for r in out)
    rows = {(r[DR["reference_num"]], r[DR["product_code"]]): r for r in out}
    assert set(rows) == {("TF/1", "P1"), ("TF/1", "P2"), ("TF/9", "P3")}  # status 33 / tanpa nomor / tanpa kode dibuang
    p1 = rows[("TF/1", "P1")]
    assert (p1[DR["qty_sent"]], p1[DR["qty_received"]]) == (Decimal("7.000"), Decimal("6.000"))
    assert rows[("TF/1", "P2")][DR["qty_received"]] == Decimal("0.000")  # belum diterima
    assert p1[DR["trans_type"]] == "Transfer Request" and p1[DR["ref_date"]] == date(2026, 10, 14)
    assert (p1[DR["origin_branch_id"]], p1[DR["dest_branch_id"]], p1[DR["company_code"]]) == (4, 7, "BCF")
    assert p1[DR["delivery_date"]] == DAY and p1[DR["doc_num"]] == "GD/1"


def delivery_esb() -> FakeEsb:
    details = {f"/inventory/goods-delivery/{d['goodsDeliveryNum'].replace('/', '%2F')}": d for d in GD_DOCS[:2]}
    listed = [gd_list("GD/1"), gd_list("GD/2", status=33)]
    return FakeEsb({"/inventory/goods-delivery": {"count": 2, "data": listed}, **details})


def test_extract_delivery_writes_delivery_ref_only_when_enabled() -> None:
    rt = runtime()
    plain = run_slice(rt, SliceTask(DAY, "delivery", "*"), V, delivery_esb().get)
    assert "fact_esb_delivery_ref_line" not in plain.tables and plain.derived == []  # default G41 tidak berubah
    rt.delivery_ref = True
    esb = delivery_esb()
    result = run_slice(rt, SliceTask(DAY, "delivery", "*"), V, esb.get)
    assert len(esb.calls) == 2  # tanpa GET tambahan (list + 1 detail; status 33 dilewati)
    refs = result.tables["fact_esb_delivery_ref_line"]
    assert {(r[DR["reference_num"]], r[DR["product_code"]]) for r in refs} == {("TF/1", "P1"), ("TF/1", "P2"),
                                                                               ("TF/9", "P3")}  # fmt: skip
    assert result.derived == [(SliceTask(DAY, "delivery_ref", "*"), 3)]
    store = FakeStore()
    write_slice(store, "pb", result, V)
    checkpoints = [rows for _, table, rows in store.writes if table == "extract_checkpoint"]
    assert store.tables()[-2:] == ["extract_checkpoint", "extract_checkpoint"]  # delivery lalu delivery_ref (terakhir)
    assert checkpoints[0][0][1] == "delivery" and checkpoints[1] == [["pb", "delivery_ref", "*", DAY, "done", 3, 0, V]]


def test_retransform_equals_extract_and_is_idempotent() -> None:
    rt = runtime()
    rt.delivery_ref = True
    extracted = run_slice(rt, SliceTask(DAY, "delivery", "*"), V, delivery_esb().get)
    store = FakeStore()
    for row in raw_rows("pb", extracted.raw, V):  # isi raw_doc persis seperti yang ditulis slice delivery G41
        store.raw[(row[1], row[2])] = (row[3], json.loads(row[4]))
    expected = [r[:-1] for r in extracted.tables["fact_esb_delivery_ref_line"]]
    for version in (V2, V3):  # dua kali: hasil sama, hanya versi slice berubah (RMT + checkpoint -> idempoten)
        store.writes.clear()
        run, missing = run_retransform(BrandRuntime(CFG), store, DAY, DAY, threading.Event(), clock=lambda v=version: v)
        assert (run.status, run.mode, run.calls, run.slices, missing) == ("ok", "retransform", 0, 1, [])
        facts = [rows for _, table, rows in store.writes if table == "fact_esb_delivery_ref_line"][0]
        assert [r[:-1] for r in facts] == expected and {r[-1] for r in facts} == {version}
        assert store.tables() == ["fact_esb_delivery_ref_line", "extract_checkpoint", "extract_run"]  # tanpa raw baru
        checkpoint = [rows for _, table, rows in store.writes if table == "extract_checkpoint"][0][0]
        assert checkpoint == ["pb", "delivery_ref", "*", DAY, "done", 3, 0, version]


def test_retransform_raw_missing_writes_nothing_for_that_day() -> None:
    day2 = DAY + timedelta(days=1)
    listed = [gd_list("GD/1"), gd_list("GD/X", status=33)]
    store = FakeStore()
    store.raw[("inventory/goods-delivery", DAY.isoformat())] = (DAY, listed)
    detail = gd_detail("GD/1", "TF/1", [gd_line("P1", "EA", 1, 1)])
    store.raw[("inventory/goods-delivery/{num}", "GD/1")] = (DAY, detail)
    store.raw[("inventory/goods-delivery", day2.isoformat())] = (day2, [gd_list("GD/1"), gd_list("GD/2")])
    run, missing = run_retransform(BrandRuntime(CFG), store, DAY, day2, threading.Event(), clock=lambda: V2)
    # day2: GD/1 detail ber-doc_date DAY (nomor dipakai ulang) + GD/2 hilang -> raw_missing.
    assert missing == [day2] and run.status == "partial" and run.slices == 1 and run.errors == 1
    days = [rows[0][3] for _, table, rows in store.writes if table == "extract_checkpoint"]
    assert days == [DAY]
    with pytest.raises(RawMissing) as exc:
        delivery_details_from_raw(day2, [gd_list("GD/1"), gd_list("GD/2")], {"GD/1": (DAY, {})})
    assert sorted(exc.value.missing) == ["GD/1", "GD/2"]
    with pytest.raises(RawMissing):
        delivery_details_from_raw(DAY, None, {})  # list raw tidak ada
    assert delivery_details_from_raw(DAY, [gd_list("GD/9", status=33)], {}) == []  # hanya status 33 -> kosong sah


# ================================================================== planning
def test_pl_refresh_years_and_month() -> None:
    assert pl_refresh_years(date(2026, 11, 25), 2) == [2026]
    assert pl_refresh_years(date(2026, 1, 5), 2) == [2025, 2026]
    assert pl_refresh_years(date(2026, 3, 1), 12) == [2025, 2026]
    assert pl_refresh_years(date(2026, 3, 1), 1) == [2026]
    assert pl_month(2026, date(2026, 11, 25)) == 11 and pl_month(2024, date(2026, 11, 25)) == 12
    with pytest.raises(ValueError):
        pl_month(2027, date(2026, 11, 25))


def test_pl_run_tasks_once_per_day_after_ready_hour() -> None:
    early = datetime(2026, 1, 5, 2, 0, tzinfo=WIB)
    assert pl_run_tasks([4, 7], early, {}, 2, 3) == []
    now = datetime(2026, 1, 5, 4, 0, tzinfo=WIB)
    tasks = pl_run_tasks([7, 4, 0], now, {}, 2, 3)
    assert [(t.day, t.scope) for t in tasks] == [
        (date(2026, 1, 1), "4"), (date(2026, 1, 1), "7"), (date(2026, 1, 1), "*"),
        (date(2025, 1, 1), "4"), (date(2025, 1, 1), "7"), (date(2025, 1, 1), "*"),
    ]  # fmt: skip
    after_ready = datetime(2026, 1, 5, 3, 30, tzinfo=WIB)
    before_ready = datetime(2026, 1, 4, 23, 0, tzinfo=WIB)
    done = {("profit_loss", "4", date(2026, 1, 1)): after_ready, ("profit_loss", "7", date(2026, 1, 1)): before_ready}
    assert [t.scope for t in pl_run_tasks([4, 7], now, done, 1, 3)] == ["7", "*"]


def test_pl_backfill_tasks_years_resumable() -> None:
    tasks = pl_backfill_tasks([7, 4], date(2024, 12, 1), date(2026, 11, 24), {})
    assert [(t.day.year, t.scope) for t in tasks] == [
        (2026, "4"), (2026, "7"), (2026, "*"), (2025, "4"), (2025, "7"), (2025, "*"),
        (2024, "4"), (2024, "7"), (2024, "*"),
    ]  # fmt: skip
    assert {t.entity for t in tasks} == {"profit_loss"} and all(t.day.month == 1 and t.day.day == 1 for t in tasks)
    done = {("profit_loss", "*", date(2025, 1, 1)): V}
    assert len(pl_backfill_tasks([7, 4], date(2024, 12, 1), date(2026, 11, 24), done)) == 8
    assert len(pl_backfill_tasks([7, 4], date(2024, 12, 1), date(2026, 11, 24), done, force=True)) == 9


# ================================================================== runner
def test_run_backfill_profit_loss_all_branch_types_plus_brand() -> None:
    routes = {**base_routes(), PL_PATH: pl_route}
    esb = FakeEsb(routes)
    store = FakeStore({("master", "*", date(2026, 11, 25)): V})
    run = run_backfill(
        EsbSettings(enabled=True, brands=(CFG,), workers=1), BrandRuntime(CFG), Client(esb), store, DAY, DAY,
        ["profit_loss"], threading.Event(), ignore_window=True,
        now_fn=lambda: datetime(2026, 11, 25, 1, 0, tzinfo=WIB),
    )  # fmt: skip
    assert run.status == "ok"
    pl_calls = [p for path, p in esb.calls if path == PL_PATH]
    assert sorted(str(p.get("branchIDs", "*")) for p in pl_calls) == ["*", "4", "7"]  # CK 4 ikut (semua tipe)
    assert all(p["year"] == 2026 for p in pl_calls)
    checkpoints = {(r[0][1], r[0][2], r[0][3]) for _, t, r in store.writes if t == "extract_checkpoint"}
    assert {("profit_loss", s, date(2026, 1, 1)) for s in ("4", "7", "*")} <= checkpoints


def test_run_cycle_profit_loss_active_branches_once() -> None:
    settings = EsbSettings(enabled=True, brands=(CFG,), entities=("branch", "profit_loss"), workers=1)
    now = datetime(2026, 11, 17, 4, 0, tzinfo=WIB)
    esb = FakeEsb({**base_routes(), PL_PATH: pl_route})
    run = run_cycle(settings, BrandRuntime(CFG), Client(esb), FakeStore(), now, threading.Event())
    assert run.status == "ok" and len([p for p, _ in esb.calls if p == PL_PATH]) == 3
    done = {("profit_loss", s, date(2026, 1, 1)): datetime(2026, 11, 16, 21, 0, tzinfo=UTC) for s in ("4", "7", "*")}
    done[("master", "*", now.date())] = V
    esb2 = FakeEsb({**base_routes(), PL_PATH: pl_route})
    run_cycle(settings, BrandRuntime(CFG), Client(esb2), FakeStore(done), now, threading.Event())
    assert [p for p, _ in esb2.calls if p == PL_PATH] == []  # sudah ditarik setelah 03:00 WIB hari ini


def test_run_cycle_transfer_request_loads_product_ids_and_delivery_ref_flag() -> None:
    settings = EsbSettings(enabled=True, brands=(CFG,), entities=("branch", "delivery", "delivery_ref",
                                                                  "transfer_request"), workers=1)  # fmt: skip
    store = FakeStore()
    store.product_ids = {77: "RM77"}
    rt = BrandRuntime(CFG)
    run_cycle(settings, rt, Client(FakeEsb(base_routes())), store, datetime(2026, 11, 17, 4, 0, tzinfo=WIB),
              threading.Event())  # fmt: skip
    assert rt.product_ids == {77: "RM77"} and rt.delivery_ref is True
    rt2 = BrandRuntime(CFG)
    only_ref = EsbSettings(enabled=True, brands=(CFG,), entities=("branch", "delivery_ref"), workers=1)
    run_cycle(only_ref, rt2, Client(FakeEsb(base_routes())), FakeStore(), datetime(2026, 11, 17, 4, 0, tzinfo=WIB),
              threading.Event())  # fmt: skip
    assert rt2.delivery_ref is False  # turunan: tanpa `delivery` tidak ada apa pun


# ================================================================== konfigurasi & CLI
def test_entities_opt_in_and_registry() -> None:
    for name in ("profit_loss", "item_journal", "transfer_request", "delivery_ref"):
        assert name in ENTITIES and name not in DEFAULT_ENTITIES and name in BACKFILL_ENTITIES
    assert {"item_journal", "transfer_request"} <= set(DAILY_ENTITIES)
    assert "profit_loss" not in DAILY_ENTITIES and "delivery_ref" not in DAILY_ENTITIES
    assert parse_entities("delivery,delivery_ref,profit_loss") == ("delivery", "delivery_ref", "profit_loss")
    for name in G68_EXTRACTORS:
        assert extractor_for(name) is G68_EXTRACTORS[name]
    assert set(G68_TABLES) <= set(ALL_TABLES)
    with pytest.raises(KeyError):
        extractor_for("delivery_ref")  # turunan, tanpa extractor sendiri


def test_pl_refresh_months_lenient_default(tmp_path: Any) -> None:
    from test_config import env_with

    assert [parse_pl_refresh_months(v) for v in ("3", " 12 ", "1")] == [3, 12, 1]
    assert [parse_pl_refresh_months(v) for v in ("0", "13", "-1", "dua", "", None)] == [2] * 6
    assert EsbSettings.from_env(env_with(tmp_path)).pl_refresh_months == 2
    assert EsbSettings.from_env(env_with(tmp_path, ESB_PL_REFRESH_MONTHS="4")).pl_refresh_months == 4
    assert EsbSettings.from_env(env_with(tmp_path, ESB_PL_REFRESH_MONTHS="99")).pl_refresh_months == 2


def test_check_request_allows_new_get_paths() -> None:
    check_request("GET", PL_PATH)
    check_request("GET", f"{ITEM_JOURNAL_PATH}/IJ%2F001")
    check_request("GET", f"{GTR_PATH}/TF%2F2026%2F1")


def test_backfill_delivery_ref_requires_delivery() -> None:
    cli.validate_backfill_entities(["delivery", "delivery_ref", "profit_loss"])
    cli.validate_backfill_entities(["item_journal"])
    with pytest.raises(ValueError, match="retransform"):
        cli.validate_backfill_entities(["delivery_ref"])


class StoreReached(Exception):
    """Validasi lolos: perintah sampai ke pembuatan store (I/O)."""


@pytest.fixture
def no_io(monkeypatch: pytest.MonkeyPatch) -> None:
    def _store(_env: Any) -> Any:
        raise StoreReached

    monkeypatch.setattr(cli, "_store", _store)


def test_cli_retransform_validation_before_io(no_io: None, capsys: pytest.CaptureFixture[str]) -> None:
    settings = EsbSettings(enabled=True, brands=(CFG,))
    today = wib_today(datetime.now(UTC))
    bad = argparse.Namespace(brand="pb", entity="item_journal", start=DAY, end=DAY)
    assert cli._cmd_retransform(settings, bad, threading.Event()) == 2
    assert "tidak didukung" in capsys.readouterr().err
    future = argparse.Namespace(brand="pb", entity="delivery_ref", start=today, end=today)
    assert cli._cmd_retransform(settings, future, threading.Event()) == 2  # --to harus < hari ini
    ok = argparse.Namespace(brand="pb", entity="delivery_ref", start=DAY, end=DAY)
    with pytest.raises(StoreReached):
        cli._cmd_retransform(settings, ok, threading.Event())


def test_cli_backfill_delivery_ref_alone_rejected_before_io(no_io: None) -> None:
    args = argparse.Namespace(brand="pb", start=DAY, end=DAY, entity=["delivery_ref"], force=False,
                              ignore_window=False)  # fmt: skip
    assert cli._cmd_backfill(EsbSettings(enabled=True, brands=(CFG,)), args, threading.Event()) == 2
    args.entity = ["profit_loss", "item_journal", "transfer_request", "delivery", "delivery_ref"]
    with pytest.raises(StoreReached):
        cli._cmd_backfill(EsbSettings(enabled=True, brands=(CFG,)), args, threading.Event())


def test_cli_retransform_respects_kill_switch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key in list(os.environ):
        if key.startswith(("ESB_", "CH_")):
            monkeypatch.delenv(key, raising=False)
    argv = ["retransform", "--brand", "pb", "--entity", "delivery_ref", "--from", "2026-10-01", "--to", "2026-10-02"]
    assert cli.main(argv) == 0  # ESB_ENABLED=false -> tanpa konfigurasi, tanpa ClickHouse
    assert "ESB dinonaktifkan" in capsys.readouterr().out


def test_cli_parser_accepts_g68_commands() -> None:
    args = cli._parse_args(["retransform", "--brand", "pb", "--entity", "delivery_ref", "--from", "2024-12-01",
                            "--to", "2026-11-24"])  # fmt: skip
    assert (args.cmd, args.entity, args.start, args.end) == ("retransform", "delivery_ref", date(2024, 12, 1),
                                                            date(2026, 11, 24))  # fmt: skip
    args = cli._parse_args(["backfill", "--brand", "pb", "--from", "2024-12-01", "--to", "2026-11-24", "--entity",
                            "profit_loss", "--entity", "item_journal"])  # fmt: skip
    assert args.entity == ["profit_loss", "item_journal"]
    with pytest.raises(SystemExit):
        cli._parse_args(["backfill", "--brand", "pb", "--from", "2026-01-01", "--to", "2026-01-02", "--entity",
                         "stock_opening"])  # fmt: skip
