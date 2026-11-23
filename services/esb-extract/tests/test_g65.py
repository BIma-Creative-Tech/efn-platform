"""G65 (docs/04 §27.13.1-27.13.3): `purchase_line` + `stock_opening` dengan ESB & ClickHouse palsu, plus DDL/grant
statis. Semua fixture SINTETIS (nama, kode, angka karangan; tidak ada data klien)."""

import argparse
import json
import re
import threading
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from test_runner import CFG, Client, FakeEsb, FakeStore, V, base_routes

import efn_esb.__main__ as cli
from efn_esb.config import DAILY_ENTITIES, DEFAULT_ENTITIES, BrandConfig, EsbSettings
from efn_esb.extract import BrandRuntime, extractor_for, run_slice
from efn_esb.extract_g65 import (
    G65_EXTRACTORS,
    PURCHASE_ORDER_PATH,
    PURCHASE_RECAP_PATH,
    STOCK_MOVEMENT_PATH,
    branch_code_for,
)
from efn_esb.normalize import name_norm
from efn_esb.planning import WIB, SliceTask, snapshot_tasks, wib_today
from efn_esb.records import RAW_COLUMNS, raw_rows
from efn_esb.runner import ALL_TABLES, run_backfill, run_snapshot
from efn_esb.transform_g65 import (
    FACT_PURCHASE_LINE_COLUMNS,
    FACT_STOCK_OPENING_COLUMNS,
    G65_TABLES,
    MAP_HEADER,
    MAP_NAME,
    MAP_NONE,
    check_branch,
    opening_rows,
    purchase_lookups,
    transform_purchase_lines,
    transform_stock_opening,
    unique_map,
)

ROOT = Path(__file__).resolve().parents[3]
CH = ROOT / "infra" / "clickhouse"
DDL = (CH / "init" / "23-g65-esb-purchase-stock.sql").read_text(encoding="utf-8")
DDL12 = (CH / "init" / "12-g41-esb.sql").read_text(encoding="utf-8")
ESB_ACCESS = (CH / "access" / "esb-access.sql").read_text(encoding="utf-8")
MIG_ACCESS = (CH / "access" / "mig-access.sql").read_text(encoding="utf-8")

DAY = date(2026, 10, 15)
CUTOVER = date(2026, 11, 25)
P = {name: i for i, name in enumerate(FACT_PURCHASE_LINE_COLUMNS)}
S = {name: i for i, name in enumerate(FACT_STOCK_OPENING_COLUMNS)}
BACHA = BrandConfig("bacha", "user-sintetis", "pw-sintetis", "BCF", frozenset(), frozenset(), {6: "BCR"})
PII = ("catatan-sintetis", "pembuat.sintetis", "surel@contoh.invalid", "CC-Sintetis", "Proyek-Sintetis",
       "info-tambahan-sintetis", "pencetak.sintetis")  # fmt: skip


def runtime(cfg: BrandConfig = CFG) -> BrandRuntime:
    rt = BrandRuntime(cfg)
    rt.branch_rows = [
        [cfg.key, cfg.company_for(4), 4, "CKX", "Central Kitchen Sintetis", 1, 0, 1, V],
        [cfg.key, cfg.company_for(6), 6, "T06", "Toko Enam", 0, 1, 1, V],
        [cfg.key, cfg.company_for(7), 7, "T07", "Toko Tujuh", 0, 1, 1, V],
        [cfg.key, cfg.company_for(8), 8, "T08", "Toko  Kembar", 0, 1, 1, V],
        [cfg.key, cfg.company_for(9), 9, "", "toko kembar", 0, 1, 0, V],
    ]
    rt.suppliers = [
        (501, "SUP01", "PT Pemasok Satu"),
        (502, "SUP02", "CV Pemasok Dua"),
        (0, "SUP03", "PT Ganda"),
        (0, "SUP04", "pt  ganda"),
    ]
    return rt


def lookups(rt: BrandRuntime | None = None) -> Any:
    rt = rt or runtime()
    return purchase_lookups([(int(r[2]), str(r[4])) for r in rt.branch_rows], rt.suppliers)


def recap(num: str, code: str, qty: Any, price: Any, total: Any, **extra: Any) -> dict[str, Any]:
    row = {
        "purchaseNum": num, "purchaseDate": "2026-10-15T00:00:00", "requiredDate": "2026-10-17",
        "branchName": "Toko Tujuh", "supplierName": "PT Pemasok Satu", "productCode": code,
        "productName": f"Bahan {code}", "categoryName": "Bahan", "subCategoryName": "Tepung", "uomName": "KG",
        "qty": qty, "receiptQty": qty, "pricelistPrice": price, "price": price, "discount": 0, "vatValue": 0,
        "total": total, "statusName": "Finished",
        # Wajib dibuang (L-110 / §27.13.1):
        "notes": PII[0], "createdBy": PII[1], "emailSupplier": PII[2], "costCenterName": PII[3],
        "projectName": PII[4], "additionalInfo": PII[5],
    }  # fmt: skip
    row.update(extra)
    return row


def header(num: str = "PO/1", branch: int = 7, supplier: int = 501, **extra: Any) -> dict[str, Any]:
    row = {
        "purchaseNum": num, "purchaseDate": "2026-10-15", "branchID": branch, "branchName": "Toko Tujuh",
        "supplierID": supplier, "supplierName": "PT Pemasok Satu", "currencyID": 1, "currencySign": "IDR",
        "purchaseTotal": 30000,
        "statusID": 6, "statusName": "Finished", "createdBy": PII[1], "emailSupplier": PII[2], "printedBy": PII[6],
    }  # fmt: skip
    row.update(extra)
    return row


# ================================================================== murni: nama & pemetaan
def test_name_norm_and_unique_map() -> None:
    assert name_norm("  toko　  tujuh ") == "TOKO TUJUH"  # NFKC: spasi ideografis -> spasi biasa
    assert name_norm("Yoghurt Mart") == "YOGHURT MART"  # aturan ejaan produk TIDAK berlaku untuk nama
    assert unique_map([("A", 1), ("B", 2), ("B", 3), ("", 4), ("C", 0), ("A", 1)]) == {"A": 1}


def test_purchase_header_join_line_no_stable_and_decimal() -> None:
    rows = [recap("PO/1", "RM2", 1, "12500", 12500), recap("PO/1", "RM1", 2, 12.5, 25.0)]
    out = transform_purchase_lines(rows, [header()], "pb", CFG.company_for, lookups(), DAY, V)
    again = transform_purchase_lines(list(reversed(rows)), [header()], "pb", CFG.company_for, lookups(), DAY, V)
    assert out == again  # urutan respons tidak mengubah line_no
    assert [(r[P["line_no"]], r[P["product_code"]]) for r in out] == [(1, "RM1"), (2, "RM2")]
    first = dict(zip(FACT_PURCHASE_LINE_COLUMNS, out[0], strict=True))
    assert first["branch_id"] == 7 and first["supplier_id"] == 501 and first["supplier_code"] == "SUP01"
    assert first["map_source"] == MAP_HEADER and first["company_code"] == "PB"
    assert first["status_id"] == 6 and first["status_name"] == "Finished" and first["currency"] == "IDR"
    assert first["purchase_date"] == DAY and first["required_date"] == date(2026, 10, 17)
    assert first["qty"] == Decimal("2.000") and str(first["price"]) == "12.5000" and str(first["total"]) == "25.00"
    assert str(out[1][P["price"]]) == "12500.0000" and isinstance(out[1][P["qty"]], Decimal)
    assert all(len(r) == len(FACT_PURCHASE_LINE_COLUMNS) for r in out) and out[0][-1] == V


def test_purchase_name_fallback_ambiguous_and_unknown_supplier_id() -> None:
    rows = [
        recap("PO/2", "RM1", 1, 1, 1, branchName=" toko   tujuh ", supplierName="cv pemasok dua"),
        recap("PO/3", "RM1", 1, 1, 1, branchName="Toko Kembar", supplierName="PT Ganda"),  # keduanya ambigu
        recap("PO/4", "RM1", 1, 1, 1),  # header ada, supplierID tak dikenal -> kode lewat nama
        recap("PO/5", "RM1", 1, 1, 1, branchName="Cabang Tak Dikenal"),  # header tanpa branchID -> nama gagal
    ]
    headers = [header("PO/4", supplier=999), header("PO/5", branch=0)]
    out = {r[P["purchase_num"]]: r for r in transform_purchase_lines(rows, headers, "pb", CFG.company_for,
                                                                       lookups(), DAY, V)}  # fmt: skip
    pick = ("branch_id", "supplier_id", "supplier_code", "map_source", "company_code", "status_id", "currency")
    view = {num: tuple(row[P[c]] for c in pick) for num, row in out.items()}
    assert view["PO/2"] == (7, 502, "SUP02", MAP_NAME, "PB", 0, "")
    assert view["PO/3"] == (0, 0, "", MAP_NONE, "PB", 0, "")  # baris TETAP disimpan
    assert view["PO/4"] == (7, 999, "SUP01", MAP_NAME, "PB", 6, "IDR")
    assert view["PO/5"] == (0, 501, "SUP01", MAP_NONE, "PB", 6, "IDR")


def test_purchase_company_from_header_branch_override() -> None:
    out = transform_purchase_lines([recap("PO/9", "RM1", 1, 1, 1)], [header("PO/9", branch=6)], "bacha",
                                   BACHA.company_for, lookups(runtime(BACHA)), DAY, V)  # fmt: skip
    assert out[0][P["company_code"]] == "BCR" and out[0][P["branch_id"]] == 6


def test_purchase_drops_rows_without_number_and_rejects_garbage_numbers() -> None:
    assert transform_purchase_lines([recap("", "RM1", 1, 1, 1)], [], "pb", CFG.company_for, lookups(), DAY, V) == []
    with pytest.raises(ValueError):
        transform_purchase_lines([recap("PO/1", "RM1", "dua", 1, 1)], [], "pb", CFG.company_for, lookups(), DAY, V)


def test_extract_purchase_line_params_and_pdp() -> None:
    esb = FakeEsb({
        PURCHASE_RECAP_PATH: {"page": 1, "limit": 1000, "count": 1, "data": [recap("PO/1", "RM1", 2, 100, 200)]},
        PURCHASE_ORDER_PATH: {"count": 1, "data": [header()]},
    })  # fmt: skip
    result = run_slice(runtime(), SliceTask(DAY, "purchase_line", "*"), V, esb.get)
    window = {"dateFrom": "2026-10-15", "dateTo": "2026-10-15", "page": 1}
    assert esb.calls == [
        (PURCHASE_RECAP_PATH, {**window, "limit": 1000}),
        (PURCHASE_ORDER_PATH, {**window, "limit": 100}),
    ]
    assert "branchID" not in esb.calls[0][1] and "branchID[]" not in esb.calls[0][1]  # filter cabang recap tak andal
    raws = raw_rows("pb", result.raw, V)
    assert [(r[RAW_COLUMNS.index("endpoint")], r[RAW_COLUMNS.index("doc_key")]) for r in raws] == [
        ("report/purchase-recapitulation", "2026-10-15"),
        ("purchase/purchase-order", "2026-10-15"),
    ]
    payload = json.dumps([r[RAW_COLUMNS.index("payload")] for r in raws]) + json.dumps(
        result.tables["fact_esb_purchase_line"], default=str
    )
    for secret in PII:
        assert secret not in payload, secret
    assert "PO/1" in payload and "PT Pemasok Satu" in payload
    assert result.tables["fact_esb_purchase_line"][0][P["map_source"]] == MAP_HEADER and result.calls == 2


def test_purchase_line_is_daily_opt_in_entity() -> None:
    assert "purchase_line" in DAILY_ENTITIES and "purchase_line" not in DEFAULT_ENTITIES
    assert extractor_for("purchase_line") is G65_EXTRACTORS["purchase_line"]
    assert extractor_for("stock_opening") is G65_EXTRACTORS["stock_opening"]
    assert set(G65_TABLES) <= set(ALL_TABLES)


def _backfill(store: FakeStore, esb: FakeEsb, rt: BrandRuntime) -> Any:
    settings = EsbSettings(enabled=True, brands=(CFG,), workers=1)
    return run_backfill(
        settings, rt, Client(esb), store, DAY, DAY, ["purchase_line"], threading.Event(), ignore_window=True,
        now_fn=lambda: datetime(2026, 10, 16, 1, 0, tzinfo=WIB),
    )  # fmt: skip


def test_backfill_purchase_line_forces_master_once_when_supplier_ids_missing() -> None:
    routes = {
        **base_routes(),
        "/supplier": {"count": 1, "data": [{"supplierID": 501, "supplierCode": "SUP01",
                                            "supplierName": "PT Pemasok Satu", "phone": "080000000000"}]},
        PURCHASE_RECAP_PATH: {"count": 1, "data": [recap("PO/1", "RM1", 1, 10, 10)]},
        PURCHASE_ORDER_PATH: {"count": 1, "data": [header()]},
    }  # fmt: skip
    master_done = {("master", "*", date(2026, 10, 16)): V}
    rt = BrandRuntime(CFG)
    store = FakeStore(dict(master_done))  # dim_esb_supplier kosong / supplier_id belum terisi
    run = _backfill(store, FakeEsb(routes), rt)
    assert run.status == "ok" and rt.master_forced_for_ids
    facts = [rows for _, table, rows in store.writes if table == "fact_esb_purchase_line"][0]
    assert (facts[0][P["supplier_code"]], facts[0][P["map_source"]]) == ("SUP01", MAP_HEADER)
    supplier_dim = [rows for _, table, rows in store.writes if table == "dim_esb_supplier"][0]
    assert supplier_dim[0][-2] == 501 and "080000000000" not in json.dumps(supplier_dim, default=str)
    # Proses yang sama: master tidak dipaksa lagi walau store masih tanpa supplier_id.
    esb2 = FakeEsb(routes)
    _backfill(FakeStore(dict(master_done)), esb2, rt)
    assert [p for p, _ in esb2.calls if p == "/supplier"] == []


# ================================================================== stock_opening
def movement(
    branch: int | None, ttype: str, code: str, qty: Any, vpu: Any = 0, amount: Any = 0, **extra: Any
) -> dict[str, Any]:
    row = {
        "branchID": branch, "branchCode": "T07", "branchName": "Toko Tujuh", "location": "Gudang Utama",
        "documentDate": "2026-11-25", "transactionType": ttype, "documentCode": "Opening", "referenceNumber": "",
        "productID": 1, "productDetailID": 11, "productCode": code, "productName": f"Bahan {code}", "UOM": "KG",
        "valuePerUnit": vpu, "qtyIn": 0, "amountIn": 0, "qtyOut": 0, "amountOut": 0, "qtyBalance": qty,
        "amountBalance": amount, "createdBy": PII[1],
    }  # fmt: skip
    row.update(extra)
    return row


def test_opening_rows_filter_and_transform_decimal() -> None:
    rows = [
        movement(7, "Beginning", "RM1", 10, 1250.5, 12505),
        movement(7, "Beginning", "RM2", 0, 100, 0),  # saldo nol -> dibuang
        movement(7, "Transfer In", "RM3", 5, 10, 50),  # mutasi hari D -> dibuang
        movement(7, "Beginning", "RM4", -2, 1000, -2000),  # negatif tetap disimpan (W-B melaporkan, L-257)
        movement(7, "Beginning", "RM5", 3, "100", 300, productDetailID=55),
        movement(7, "Beginning", "RM5", 4, 999, 400, productDetailID=55),  # kunci ganda -> dijumlah
        movement(7, "Beginning", "RM6", 1, 1, 1, location="Gudang Damage"),
    ]
    kept = opening_rows(rows)
    assert [r["productCode"] for r in kept] == ["RM1", "RM4", "RM5", "RM5", "RM6"]
    out = transform_stock_opening(kept, "pb", "PB", 7, "T07", CUTOVER, V)
    by_code = {(r[S["product_code"]], r[S["location_name"]]): r for r in out}
    rm1 = by_code[("RM1", "Gudang Utama")]
    assert rm1[S["value_per_unit"]] == Decimal("1250.5000") and str(rm1[S["qty_balance"]]) == "10.000"
    assert str(rm1[S["amount_balance"]]) == "12505.00" and rm1[S["opening_date"]] == CUTOVER
    rm5 = by_code[("RM5", "Gudang Utama")]
    assert (rm5[S["qty_balance"]], rm5[S["amount_balance"]], rm5[S["value_per_unit"]]) == (
        Decimal("7.000"), Decimal("700.00"), Decimal("100.0000"))  # fmt: skip
    assert by_code[("RM4", "Gudang Utama")][S["qty_balance"]] == Decimal("-2.000")
    assert ("RM6", "Gudang Damage") in by_code  # lokasi apa adanya; pemetaan lokasi = W-B (L-257)
    assert all(len(r) == len(FACT_STOCK_OPENING_COLUMNS) for r in out)
    with pytest.raises(ValueError):
        transform_stock_opening([movement(7, "Beginning", "RMX", "x")], "pb", "PB", 7, "T07", CUTOVER, V)


def test_check_branch_rejects_rows_of_other_branch() -> None:
    check_branch([movement(7, "Beginning", "RM1", 1)], 7)
    check_branch([], 7)
    for bad in (movement(8, "Beginning", "RM1", 1), movement(None, "Beginning", "RM1", 1)):
        with pytest.raises(ValueError):
            check_branch([movement(7, "Beginning", "RM0", 1), bad], 7)


def test_extract_stock_opening_params_paging_summary_and_pdp() -> None:
    rows = [movement(7, "Beginning", "RM1", 10, 1250.5, 12505), movement(7, "Transfer In", "RM3", 5)]
    rows += [movement(7, "Beginning", f"Z{i:03d}", 0) for i in range(148)]

    def page(_path: str, params: dict[str, Any]) -> Any:
        start = (params["page"] - 1) * params["limit"]
        return {"count": len(rows), "data": rows[start : start + params["limit"]]}

    esb = FakeEsb({STOCK_MOVEMENT_PATH: page})
    result = run_slice(runtime(), SliceTask(CUTOVER, "stock_opening", "7"), V, esb.get)
    assert esb.calls[0] == (STOCK_MOVEMENT_PATH, {"startPeriod": "2026-11-25", "endPeriod": "2026-11-25",
                                                  "branchCode": "T07", "page": 1, "limit": 100})  # fmt: skip
    assert len(esb.calls) == 2 and result.calls == 2
    facts = result.tables["fact_esb_stock_opening"]
    assert len(facts) == 1 and facts[0][S["branch_code"]] == "T07" and facts[0][S["company_code"]] == "PB"
    raw = raw_rows("pb", result.raw, V)[0]
    assert raw[RAW_COLUMNS.index("doc_key")] == "2026-11-25|7"
    payload = json.loads(raw[RAW_COLUMNS.index("payload")])
    assert payload["summary"] == {"pages": 2, "rows_total": 150, "rows_beginning": 149, "rows_kept": 1}
    assert len(payload["rows"]) == 1 and PII[1] not in raw[RAW_COLUMNS.index("payload")]


def test_extract_stock_opening_fails_without_branch_code_or_on_foreign_rows() -> None:
    rt = runtime()
    with pytest.raises(ValueError):
        branch_code_for(rt, 9)  # cabang tanpa kode
    with pytest.raises(ValueError):
        branch_code_for(rt, 404)  # cabang tak dikenal
    esb = FakeEsb({STOCK_MOVEMENT_PATH: {"count": 1, "data": [movement(8, "Beginning", "RM1", 1)]}})
    with pytest.raises(ValueError):
        run_slice(rt, SliceTask(CUTOVER, "stock_opening", "7"), V, esb.get)


def test_snapshot_tasks_per_branch_for_stock_opening() -> None:
    assert snapshot_tasks(["menu_price", "stock_opening"], CUTOVER, [7, 4, 0, 4]) == [
        SliceTask(CUTOVER, "menu_price", "*"),
        SliceTask(CUTOVER, "stock_opening", "4"),
        SliceTask(CUTOVER, "stock_opening", "7"),
    ]


def test_run_snapshot_stock_opening_checkpoint_per_branch_and_failure_isolated() -> None:
    def page(_path: str, params: dict[str, Any]) -> Any:
        if params["branchCode"] == "CK1":
            raise ValueError("cabang gagal")
        return {"count": 1, "data": [movement(7, "Beginning", "RM1", 2, 10, 20)]}

    esb = FakeEsb({**base_routes(), STOCK_MOVEMENT_PATH: page})
    now = datetime(2026, 11, 25, 9, 0, tzinfo=WIB)
    store = FakeStore({("master", "*", now.date()): V})
    settings = EsbSettings(enabled=True, brands=(CFG,))
    run = run_snapshot(settings, BrandRuntime(CFG), Client(esb), store, ["stock_opening"], CUTOVER,
                       threading.Event(), now_fn=lambda: now)  # fmt: skip
    assert run.status == "partial" and run.errors == 1
    checkpoints = [rows[0] for _, table, rows in store.writes if table == "extract_checkpoint"]
    assert [(c[1], c[2], c[3]) for c in checkpoints if c[1] == "stock_opening"] == [("stock_opening", "7", CUTOVER)]
    assert [p["branchCode"] for path, p in esb.calls if path == STOCK_MOVEMENT_PATH] == ["CK1", "T07"]


# ================================================================== CLI
def test_snapshot_args_stock_opening_requires_date_but_not_unverified() -> None:
    with pytest.raises(ValueError, match="--date"):
        cli.validate_snapshot_args(["stock_opening"], False, 3)
    with pytest.raises(ValueError, match="--date"):
        cli.validate_snapshot_args(["location", "stock_opening"], True, 3, None)
    cli.validate_snapshot_args(["stock_opening"], False, 3, CUTOVER)
    cli.validate_snapshot_args(["location"], False, 3)  # entitas G43 tetap boleh tanpa --date


class StoreReached(Exception):
    """Validasi lolos: _cmd_snapshot sampai ke pembuatan store (I/O)."""


@pytest.mark.parametrize("day_offset", [None, 1])
def test_cli_snapshot_stock_opening_rejected_before_io(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], day_offset: int | None
) -> None:
    def _store(_env: Any) -> Any:
        raise StoreReached

    monkeypatch.setattr(cli, "_store", _store)
    today = wib_today(datetime.now(UTC))
    day = None if day_offset is None else today + timedelta(days=day_offset)
    args = argparse.Namespace(brand="pb", entity=["stock_opening"], day=day, allow_unverified=False, opname_window=3)
    assert cli._cmd_snapshot(EsbSettings(enabled=True, brands=(CFG,)), args, threading.Event()) == 2
    assert "snapshot ditolak" in capsys.readouterr().err
    args.day = today
    with pytest.raises(StoreReached):
        cli._cmd_snapshot(EsbSettings(enabled=True, brands=(CFG,)), args, threading.Event())


# ================================================================== DDL & akses (statis)
def block(qualified: str) -> str:
    return DDL.split(f"CREATE TABLE IF NOT EXISTS {qualified}\n", 1)[1].split(";", 1)[0]


def strip_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def test_ddl_columns_engine_key_partition() -> None:
    for table, columns in G65_TABLES.items():
        body = block(f"efn_dwh.{table}")
        cols = body.split("ENGINE", 1)[0]
        for column in columns:
            assert re.search(rf"^\s+{column}\s", cols, re.MULTILINE), (table, column)
        assert re.search(r"^\s+_loaded_at\s", cols, re.MULTILINE)
        assert "ENGINE = ReplacingMergeTree(_version)" in body and "TTL" not in body
    purchase = block("efn_dwh.fact_esb_purchase_line")
    assert "ORDER BY (company_code, brand, purchase_date, purchase_num, line_no)" in purchase
    assert "PARTITION BY toYYYYMM(purchase_date)" in purchase
    flat = re.sub(r"[ \t]+", " ", purchase)
    for decl in ("qty Decimal(18, 3)", "receipt_qty Decimal(18, 3)", "price Decimal(18, 4)", "total Decimal(18, 2)",
                 "required_date Nullable(Date)", "map_source LowCardinality(String)"):  # fmt: skip
        assert f" {decl}," in flat, decl
    opening = block("efn_dwh.fact_esb_stock_opening")
    assert (
        "ORDER BY (company_code, brand, opening_date, branch_id, location_name, product_code, product_detail_id, uom)"
    ) in opening
    assert "PARTITION BY toYYYYMM(opening_date)" in opening
    flat = re.sub(r"[ \t]+", " ", opening)
    for decl in ("value_per_unit Decimal(18, 4)", "qty_balance Decimal(18, 3)", "amount_balance Decimal(18, 2)"):
        assert f" {decl}," in flat, decl


def test_ddl_idempotent_only_additive() -> None:
    code = strip_comments(DDL)
    assert set(re.findall(r"CREATE TABLE IF NOT EXISTS efn_dwh\.(\w+)", code)) == set(G65_TABLES)
    assert not re.search(r"\b(DROP|TRUNCATE|DELETE|INSERT|RENAME|MODIFY)\b", code)
    alters = re.findall(r"ALTER TABLE [^;]+;", code)
    assert alters == ["ALTER TABLE efn_dwh.dim_esb_supplier ADD COLUMN IF NOT EXISTS supplier_id UInt64 DEFAULT 0;"]
    assert re.findall(r"CREATE OR REPLACE VIEW (efn_dwh\.\w+) AS", code) == ["efn_dwh.v_esb_g65_quality"]
    # v_esb_data_quality (INVOKER, dibaca Metabase/Insight) tidak disentuh -> tidak butuh hak atas tabel G65.
    dq = DDL12.split("CREATE OR REPLACE VIEW efn_dwh.v_esb_data_quality AS", 1)[1].split(";", 1)[0]
    assert "purchase_line" not in dq and "stock_opening" not in dq


def test_quality_view_reads_finished_slices_only() -> None:
    view = strip_comments(DDL.split("CREATE OR REPLACE VIEW efn_dwh.v_esb_g65_quality AS", 1)[1].split(";", 1)[0])
    assert "'purchase_unmapped' AS check_name" in view and "'stock_opening_missing_branch'" in view
    assert "(brand, purchase_date, _version) IN" in view
    assert "WHERE entity = 'purchase_line' AND scope_key = '*'" in view
    assert "map_source != 'header'" in view and "today() - 30" in view


def _grants(text: str, role: str, privileges: str) -> set[str]:
    return set(re.findall(rf"^GRANT {privileges} ON (efn_\w+\.\w+) TO {role};$", text, re.MULTILINE))


def test_grants_only_writer_and_migration() -> None:
    tables = {f"efn_dwh.{t}" for t in G65_TABLES}
    assert tables <= _grants(ESB_ACCESS, "esb_writer", "SELECT, INSERT")
    mig_read = _grants(MIG_ACCESS, "mig_rw", "SELECT")
    assert tables | {"efn_dwh.v_esb_g65_quality"} <= mig_read
    assert not tables & _grants(MIG_ACCESS, "mig_rw", "SELECT, INSERT")  # mig_rw hanya membaca
    view = strip_comments(DDL.split("CREATE OR REPLACE VIEW efn_dwh.v_esb_g65_quality AS", 1)[1].split(";", 1)[0])
    assert set(re.findall(r"\b(efn_(?:dwh|esb)\.\w+)", view)) <= mig_read  # view INVOKER: tabel dasar ter-grant
    # L-258: tidak ke Metabase, Insight, forecast, segment, text2sql, ETL.
    for path in sorted((CH / "access").iterdir()):
        if path.name in ("esb-access.sql", "mig-access.sql"):
            continue
        text = path.read_text(encoding="utf-8")
        for name in (*G65_TABLES, "v_esb_g65_quality"):
            assert name not in text, (path.name, name)
    # metabase_reader di-grant di esb-access.sql (dilewati loop di atas) -> cek eksplisit.
    metabase = _grants(ESB_ACCESS, "metabase_reader", "SELECT")
    assert not metabase & (tables | {"efn_dwh.v_esb_g65_quality"})
    objects = "fact_esb_purchase_line|fact_esb_stock_opening|v_esb_g65_quality"
    assert not re.search(rf"ON efn_dwh\.({objects}) TO (?!esb_writer;|mig_rw;)", ESB_ACCESS + MIG_ACCESS)
