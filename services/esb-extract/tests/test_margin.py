"""G67 "Margin bahan" (docs/04 §31): algoritme §31.2 dengan data SINTETIS (tanpa nama produk/supplier klien),
refresh lewat ClickHouse palsu (versi-insert, kunci, parameter), jadwal mingguan, CLI, konfigurasi, serta DDL / grant /
row policy (statis, tanpa ClickHouse)."""

import re
import threading
import uuid
from collections.abc import Sequence
from dataclasses import astuple
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

import efn_esb.__main__ as cli
from efn_esb import aggregates, margin, runner
from efn_esb.aggregates import REFRESH_COLUMNS, REFRESH_TABLE, RefreshLock
from efn_esb.config import BrandConfig, EsbSettings, MarginSettings, validate_margin
from efn_esb.margin import (
    GROUP_COLUMNS,
    MART_TABLES,
    MENU_COLUMNS,
    PRICE_COLUMNS,
    TEMPLATES,
    UNPRICED_GROUP,
    BomHeader,
    BomLine,
    PurchaseMonth,
    SalesMonth,
    add_months,
    build_ingredients,
    canonical_menu,
    clean_product_name,
    compute,
    dominant_uom,
    expand_menus,
    is_due,
    pick_company,
    refresh_margin,
    render,
    window_months,
)
from efn_esb.records import RunRecord
from efn_esb.runner import AuthBreaker, MarginSchedule

ROOT = Path(__file__).resolve().parents[3]
CH = ROOT / "infra" / "clickhouse"
DDL = (CH / "init" / "24-margin-bahan.sql").read_text(encoding="utf-8")
ESB_ACCESS = (CH / "access" / "esb-access.sql").read_text(encoding="utf-8")
INSIGHT_ACCESS = (CH / "access" / "insight-access.sql").read_text(encoding="utf-8")
POLICY_SH = (CH / "access" / "insight-esb-policy.sh").read_text(encoding="utf-8")
INIT_SH = (CH / "clickhouse-init.sh").read_text(encoding="utf-8")

D = Decimal
TODAY = date(2026, 4, 10)  # jendela 3 bulan penuh = Jan..Mar 2026; PO dimuat sejak Okt 2025 (bergulir 4 bulan)
JAN, FEB, MAR = date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)
WINDOW = [JAN, FEB, MAR]
V = datetime(2026, 4, 6, 19, 0, tzinfo=UTC)
MONDAY = datetime(2026, 11, 22, 19, 30, tzinfo=UTC)  # Senin 23 Nov 2026 02:30 WIB
RUN = uuid.UUID("00000000-0000-4000-8000-0000000000aa")
OTHER = uuid.UUID("00000000-0000-4000-8000-0000000000bb")
BRAND = BrandConfig("pb", "user-sintetis", "pw-sintetis")
SETTINGS = EsbSettings(enabled=True, brands=(BRAND,), entities=("sales", "purchase_line"))


def po(month: date, code: str, qty: str, value: str, uom: str = "KG", sub: str = "Sub A") -> PurchaseMonth:
    name = f"Bahan {code} ({code})"  # pola nama PO ESB: kode dalam kurung
    return PurchaseMonth(month, code, uom, D(qty), D(value), month + timedelta(days=9), name, sub, "")


PURCHASES = [
    po(date(2025, 11, 1), "BHN-A", "10", "1000"),
    po(JAN, "BHN-A", "10", "1200"),
    po(FEB, "BHN-A", "30", "3900"),
    po(FEB, "BHN-A", "1000", "50", uom="GR"),  # satuan minor (nilai kecil) -> diabaikan
    po(FEB, "BHN-C", "10", "500", sub="Sub B"),
    po(MAR, "BHN-C", "10", "700", sub="Sub B"),
    po(date(2025, 9, 1), "BHN-B", "5", "500"),  # di luar jendela bergulir mana pun -> bukan bahan ber-harga
]
HEADERS = [
    BomHeader(1, "Roti Bakar Sintetis", "Menu", "", "", True),
    BomHeader(2, "Adonan Sintetis", "Production", "WIP-ADONAN", "Adonan Sintetis", True),
    BomHeader(9, "Adonan Lama", "Production", "WIP-ADONAN", "Adonan Lama", False),  # nonaktif -> diabaikan
]
LINES = [
    BomLine(1, "WIP-ADONAN", 1.0, 600.0),
    BomLine(1, "BHN-C", 2.0, 50.0),
    BomLine(1, "KMS-1", 1.0, 300.0),
    BomLine(2, "BHN-A", 0.5, 100.0),
    BomLine(2, "BHN-D", 1.0, 150.0),
    BomLine(9, "BHN-X", 1.0, 999.0),
]
SALES = [
    SalesMonth(JAN, "Roti Bakar Sintetis", "Roti", D("10"), D("150000"), D("20000")),
    SalesMonth(FEB, "ROTI BAKAR SINTETIS T", "Roti", D("20"), D("300000"), D("40000")),
    SalesMonth(MAR, "KIOSK - Roti  Bakar Sintetis", "Roti", D("30"), D("450000"), D("60000")),
    SalesMonth(JAN, "Minuman Sintetis", "Minuman", D("5"), D("50000"), D("1000")),
    SalesMonth(date(2026, 4, 1), "Roti Bakar Sintetis", "Roti", D("99"), D("1"), D("1")),  # di luar jendela
]


# ------------------------------------------------------------------------------------------------ logika murni
def test_canonical_menu_strips_takeaway_and_kiosk() -> None:
    assert canonical_menu("  kiosk -  Roti  Bakar Sintetis T ") == "ROTI BAKAR SINTETIS"
    assert canonical_menu("Roti Bakar Sintetis T") == "ROTI BAKAR SINTETIS"
    assert canonical_menu("KIOSK - roti bakar sintetis") == "ROTI BAKAR SINTETIS"
    # Negatif: huruf T tanpa spasi, awalan di tengah, string kosong.
    assert canonical_menu("Teh Set") == "TEH SET"
    assert canonical_menu("Paket KIOSK - A") == "PAKET KIOSK - A"
    assert canonical_menu("") == ""


def test_clean_product_name_drops_code_in_parentheses() -> None:
    assert clean_product_name("Bahan  Sintetis (BHN-A)", "BHN-A") == "Bahan Sintetis"
    assert clean_product_name("(BHN-A) Bahan Sintetis", "BHN-A") == "Bahan Sintetis"
    assert clean_product_name("Bahan (Merah) Sintetis", "BHN-A") == "Bahan (Merah) Sintetis"  # kurung lain tetap
    assert clean_product_name("Bahan (A.B)", "A.B") == "Bahan"
    assert clean_product_name("Bahan (AxB)", "A.B") == "Bahan (AxB)"  # kode di-escape, bukan regex
    assert clean_product_name("Bahan (X)", "") == "Bahan (X)"


def test_window_is_full_calendar_months_before_today() -> None:
    assert window_months(TODAY, 3) == WINDOW
    assert window_months(date(2026, 11, 25), 13)[0] == date(2025, 10, 1)
    assert window_months(date(2026, 11, 25), 13)[-1] == date(2026, 10, 1)  # bulan berjalan tidak ikut
    assert window_months(date(2026, 1, 1), 1) == [date(2025, 12, 1)]
    assert add_months(date(2026, 1, 1), -13) == date(2024, 12, 1)
    with pytest.raises(ValueError):
        window_months(TODAY, 0)


def test_dominant_uom_by_value_then_qty_then_name() -> None:
    assert dominant_uom([po(JAN, "X", "1", "500", uom="KG"), po(JAN, "X", "100", "400", uom="GR")]) == "KG"
    assert dominant_uom([po(JAN, "X", "1", "500", uom="KG"), po(JAN, "X", "9", "500", uom="PCS")]) == "PCS"
    assert dominant_uom([po(JAN, "X", "1", "500", uom="PCS"), po(JAN, "X", "1", "500", uom="BOX")]) == "BOX"


def test_weighted_monthly_price_rolling_4m_and_base_index() -> None:
    ings = build_ingredients(PURCHASES, WINDOW)
    assert set(ings) == {"BHN-A", "BHN-C"}  # BHN-B: harga hanya di luar jendela bergulir
    a = ings["BHN-A"]
    assert a.uom == "KG" and a.product_name == "Bahan BHN-A" and a.group == "Sub A"
    # Bergulir 4 bulan berbobot receipt_qty: Feb = (1000 + 1200 + 3900) / (10 + 10 + 30) = 122 (bukan rata-rata
    # sederhana harga 116,67); Jan = 2200 / 20 = 110; Mar = Jan + Feb saja (Nov keluar) = 5100 / 40 = 127,5.
    # Baris GR (satuan minor) tidak ikut.
    assert a.price_4m == {JAN: D(110), FEB: D(122), MAR: D("127.5")}
    assert a.idx[JAN] == 1.0
    assert a.idx[FEB] == pytest.approx(122 / 110)
    assert a.idx[MAR] == pytest.approx(127.5 / 110)
    # BHN-C pertama dibeli Feb -> bulan dasar = Feb (idx 1); Jan tidak punya harga/baris.
    c = ings["BHN-C"]
    assert JAN not in c.idx and c.idx[FEB] == 1.0 and c.idx[MAR] == pytest.approx(1.2)


def test_group_falls_back_to_category_then_default() -> None:
    rows = [
        PurchaseMonth(JAN, "BHN-E", "KG", D(1), D(10), date(2026, 1, 5), "Bahan E", "", "Kategori Sintetis"),
        PurchaseMonth(JAN, "BHN-F", "KG", D(1), D(10), date(2026, 1, 5), "Bahan F", "", ""),
        PurchaseMonth(JAN, "BHN-G", "KG", D(1), D(10), date(2026, 1, 5), "Bahan G", "Sub Lama", ""),
        PurchaseMonth(FEB, "BHN-G", "KG", D(1), D(10), date(2026, 2, 5), "Bahan G", "Sub Baru", ""),
    ]
    ings = build_ingredients(rows, WINDOW)
    assert ings["BHN-E"].group == "Kategori Sintetis"
    assert ings["BHN-F"].group == "Lainnya"
    assert ings["BHN-G"].group == "Sub Baru"  # sub-kategori PO terbaru


def test_bom_expansion_proportional_to_line_value() -> None:
    menus = expand_menus(HEADERS, LINES)
    assert set(menus) == {"ROTI BAKAR SINTETIS"}
    # Adonan (600) diurai proporsional nilai baris sub-BOM: BHN-A 50/200, BHN-D 150/200; BOM nonaktif diabaikan.
    leaves = menus["ROTI BAKAR SINTETIS"]
    assert leaves == pytest.approx({"BHN-A": 150.0, "BHN-D": 450.0, "BHN-C": 100.0, "KMS-1": 300.0})


def test_bom_cycle_and_depth_limit_become_leaves() -> None:
    headers = [
        BomHeader(10, "Kue Siklus", "MENU", "", "", True),
        BomHeader(11, "Siklus A", "Production", "WIP-A", "", True),
        BomHeader(12, "Siklus B", "Production", "WIP-B", "", True),
    ]
    lines = [
        BomLine(10, "WIP-A", 1.0, 100.0),
        BomLine(11, "WIP-B", 1.0, 10.0),
        BomLine(11, "BHN-A", 1.0, 10.0),
        BomLine(12, "WIP-A", 1.0, 5.0),  # A -> B -> A: siklus berhenti, WIP-A jadi daun
    ]
    assert expand_menus(headers, lines)["KUE SIKLUS"] == pytest.approx({"BHN-A": 50.0, "WIP-A": 50.0})
    chain = [BomHeader(20, "Menu Rantai", "Menu", "", "", True)]
    chain += [BomHeader(20 + i, f"Rantai {i}", "Production", f"P{i}", "", True) for i in range(1, 11)]
    chain_lines = [BomLine(20 + i, f"P{i + 1}", 1.0, 1.0) for i in range(11)]
    # Rantai maksimum 8 BOM (menu + 7 sub-BOM) -> P8 menjadi daun walau masih punya sub-BOM.
    assert expand_menus(chain, chain_lines)["MENU RANTAI"] == {"P8": 1.0}


def test_menu_bom_matching_rules() -> None:
    headers = [
        BomHeader(1, "Menu Sintetis", "Menu", "", "", True),
        BomHeader(5, "MENU  sintetis T", "menu", "", "", True),  # nama kanonik sama -> bom_id terbesar
        BomHeader(6, "Menu Nonaktif", "Menu", "", "", False),
        BomHeader(7, "Menu Nol", "Menu", "", "", True),
    ]
    lines = [
        BomLine(1, "BHN-A", 1.0, 1.0),
        BomLine(5, "BHN-C", 2.0, 3.0),
        BomLine(6, "BHN-A", 1.0, 1.0),
        BomLine(7, "BHN-A", 0.0, 10.0),  # nilai 0 -> menu tanpa BOM bernilai (bom_matched 0)
    ]
    assert expand_menus(headers, lines) == {"MENU SINTETIS": {"BHN-C": 6.0}}


def test_compute_end_to_end_menu_index_and_laspeyres() -> None:
    result = compute("pb", "PB", WINDOW, PURCHASES, HEADERS, LINES, SALES, V)
    assert all(len(row) == len(PRICE_COLUMNS) and row[-1] == V for row in result.price)
    assert all(len(row) == len(MENU_COLUMNS) and row[:2] == ["pb", "PB"] for row in result.menu)
    assert all(len(row) == len(GROUP_COLUMNS) for row in result.group)
    price = {(row[3], row[2]): row for row in result.price}
    assert set(price) == {("BHN-A", JAN), ("BHN-A", FEB), ("BHN-A", MAR), ("BHN-C", FEB), ("BHN-C", MAR)}
    assert price[("BHN-A", FEB)][7:11] == [D(30), D(3900), D(130), D(122)]
    # Mar tanpa penerimaan: receipt_qty/value/price 0, price_4m tetap dari 4 bulan bergulir.
    assert price[("BHN-A", MAR)][7:11] == [D(0), D(0), D(0), D("127.5")]
    assert price[("BHN-A", MAR)][11] == pytest.approx(127.5 / 110)

    menu = {(row[3], row[2]): row for row in result.menu}
    assert set(menu) == {("ROTI BAKAR SINTETIS", m) for m in WINDOW} | {("MINUMAN SINTETIS", JAN)}
    roti = {m: menu[("ROTI BAKAR SINTETIS", m)] for m in WINDOW}
    assert roti[FEB][4:9] == ["Roti", D(20), D(300000), D(40000), 1]  # nama ' T' / 'KIOSK - ' digabung
    # cost_idx = Σ w·idx / Σ w; daun tanpa harga PO (BHN-D, KMS-1) idx 1; BHN-C sebelum bulan dasarnya idx 1.
    assert roti[JAN][9] == pytest.approx(1.0)
    assert roti[FEB][9] == pytest.approx((150 * 122 / 110 + 450 + 100 + 300) / 1000)
    assert roti[MAR][9] == pytest.approx((150 * 127.5 / 110 + 450 + 100 * 1.2 + 300) / 1000)
    assert roti[FEB][10] == pytest.approx(0.25)  # (150 + 100) / 1000 nilai daun ber-harga PO
    assert menu[("MINUMAN SINTETIS", JAN)][8:11] == [0, None, 0.0]  # tanpa BOM: tidak ikut indeks

    groups = {(row[3], row[2]): row[4:6] for row in result.group}
    # Keranjang tetap: qty/bulan = 60 / 3 = 20; nilai dasar = 20 × 1000 = 20000; COGS rata-rata menu cocok =
    # (20000 + 40000 + 60000) / 3 = 40000 -> skala 2 (menu tanpa BOM tidak ikut).
    assert groups[("Sub A", JAN)] == pytest.approx([6000.0, 6000.0])
    assert groups[("Sub A", FEB)] == pytest.approx([2 * 20 * 150 * 122 / 110, 6000.0])
    assert groups[("Sub B", MAR)] == pytest.approx([2 * 20 * 100 * 1.2, 4000.0])
    assert groups[(UNPRICED_GROUP, MAR)] == pytest.approx([30000.0, 30000.0])
    assert sum(groups[(g, JAN)][0] for g in ("Sub A", "Sub B", UNPRICED_GROUP)) == pytest.approx(40000.0)
    assert len(result.group) == 3 * len(WINDOW)


def test_scale_is_one_without_cogs() -> None:
    sales = [SalesMonth(JAN, "Roti Bakar Sintetis", "Roti", D(30), D(0), D(0))]
    result = compute("pb", "PB", WINDOW, PURCHASES, HEADERS, LINES, sales, V)
    groups = {(row[3], row[2]): row[4] for row in result.group}
    assert groups[(UNPRICED_GROUP, JAN)] == pytest.approx(10 * 750)  # qty/bulan 30/3 = 10, skala 1


def test_pick_company_single_pt_only() -> None:
    assert pick_company([("PB", 3)]) == "PB"
    assert pick_company([("", 9)]) == ""
    assert pick_company([]) == ""
    # SEC-MB-01: brand multi-PT ditolak (fail closed), bukan dilabel PT mayoritas.
    with pytest.raises(margin.MarginError, match="multi_company"):
        pick_company([("BCF", 5), ("BCR", 2)])


def test_weekly_schedule_wib() -> None:
    assert is_due(MONDAY, 0, 2, None, None)
    assert not is_due(MONDAY - timedelta(minutes=31), 0, 2, None, None)  # 01:59 WIB
    assert not is_due(MONDAY + timedelta(days=1), 0, 2, None, None)  # Selasa
    assert not is_due(MONDAY, 0, 2, MONDAY - timedelta(days=5), None)  # ok terakhir < 6 hari
    assert is_due(MONDAY, 0, 2, MONDAY - timedelta(days=7), None)
    assert not is_due(MONDAY, 0, 2, None, date(2026, 11, 23))  # sudah dicoba hari WIB ini


# ------------------------------------------------------------------------------------------------ I/O (palsu)
ROUTES = (
    ("lock", "aggregate_refresh_lock"),
    ("refresh", "aggregate_refresh\n"),
    ("branch", "dim_esb_branch"),
    ("lines", "dim_esb_bom_line"),
    ("bom", "dim_esb_bom FINAL"),
    ("purchase", "fact_esb_purchase_line"),
    ("sales", "agg_esb_sales_daily"),
)


class FakeStore:
    """ClickHouse palsu: query_rows dijawab menurut objek yang dirujuk SQL; mencatat SQL + parameter + tulisan."""

    def __init__(self, holder: uuid.UUID = RUN, fail_on: str = "", lose_on: str = "", **data: Any) -> None:
        self.holder = holder
        self.fail_on = fail_on
        self.lose_on = lose_on
        self.data: dict[str, list[Sequence[Any]]] = {"refresh": [(0, datetime(1970, 1, 1, tzinfo=UTC))], **data}
        self.queries: list[tuple[str, dict[str, Any] | None]] = []
        self.inserts: dict[str, tuple[tuple[str, ...], list[list[Any]]]] = {}
        self.records: list[list[Any]] = []
        self.lock_rows: list[list[Any]] = []

    @property
    def mart_db(self) -> str:
        return "efn_dwh"

    @property
    def raw_db(self) -> str:
        return "efn_esb"

    def command(
        self, sql: str, parameters: dict[str, Any] | None = None, settings: dict[str, Any] | None = None
    ) -> None:
        raise AssertionError("refresh margin tidak boleh menjalankan DDL/ALTER/TRUNCATE")

    def query_rows(self, sql: str, parameters: dict[str, Any] | None = None) -> list[Sequence[Any]]:
        self.queries.append((sql, parameters))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("Code: 241. DB::Exception: Memory limit exceeded nilai-rahasia")
        if self.lose_on and self.lose_on in sql:
            self.holder = OTHER
        for key, needle in ROUTES:
            if needle in sql:
                return [[self.holder]] if key == "lock" else list(self.data.get(key, []))
        raise AssertionError(f"query tak terduga: {sql}")

    def insert(self, table: str, columns: Sequence[str], rows: list[list[Any]]) -> None:
        self.inserts[table] = (tuple(columns), rows)

    def insert_control(self, table: str, columns: Sequence[str], rows: list[list[Any]]) -> None:
        if table == REFRESH_TABLE:
            assert tuple(columns) == REFRESH_COLUMNS
            self.records.extend(rows)
        else:
            self.lock_rows.extend(rows)


def store_rows() -> dict[str, list[Any]]:
    """Baris dalam bentuk keluaran SQL (urutan field dataclass = urutan kolom SELECT)."""
    return {
        "branch": [("PB", 4), ("", 1)],
        "bom": [astuple(h) for h in HEADERS],
        "lines": [astuple(line) for line in LINES],
        "purchase": [astuple(p) for p in PURCHASES],
        "sales": [astuple(s) for s in SALES],
    }


def _lock(store: FakeStore) -> RefreshLock:
    return RefreshLock(store, run_id=RUN, sleep=lambda _s: None, settle=0.0)


def test_refresh_writes_new_version_with_parameters_only() -> None:
    store = FakeStore(**store_rows())
    result = refresh_margin(store, "pb", 3, TODAY, lock=_lock(store), clock=lambda: V)
    assert (result.status, result.months, result.rows) == ("ok", 3, 18)
    assert (result.first_day, result.last_day) == (JAN, date(2026, 3, 31))
    assert set(store.inserts) == set(MART_TABLES)
    for table, (columns, rows) in store.inserts.items():
        assert columns == MART_TABLES[table] and rows, table
        assert all(row[:2] == ["pb", "PB"] and row[-1] == V for row in rows), table  # _version = mulai run
    assert store.records[-1][:2] == ["margin_pb", "ok"]
    assert store.lock_rows[-1] == [RUN, 1]  # kunci dilepas
    purchase = next(params for sql, params in store.queries if "fact_esb_purchase_line" in sql)
    assert purchase is not None
    assert purchase["start"] == date(2025, 10, 1) and purchase["end"] == date(2026, 3, 31)
    assert purchase["brand"] == "pb" and "currencies" not in purchase and "batal" in purchase["cancel"]
    # Nilai hanya lewat parameter ClickHouse: brand tidak pernah muncul di teks SQL.
    assert not any(re.search(r"\bpb\b", sql) for sql, _params in store.queries)


def test_refresh_busy_when_other_holds_lock() -> None:
    store = FakeStore(holder=OTHER, **store_rows())
    result = refresh_margin(store, "pb", 3, TODAY, lock=_lock(store))
    assert result.status == "busy" and not store.inserts
    assert store.records[-1][1] == "busy"
    assert store.lock_rows[-1] == [RUN, 1]


@pytest.mark.parametrize(
    ("drop", "label"),
    [("branch", "no_company"), ("bom", "no_bom"), ("purchase", "no_purchase"), ("sales", "no_sales")],
)
def test_refresh_missing_source_is_error_without_writes(drop: str, label: str) -> None:
    rows = store_rows()
    rows[drop] = []
    store = FakeStore(**rows)
    result = refresh_margin(store, "pb", 3, TODAY, lock=_lock(store))
    assert (result.status, result.error) == ("error", label)
    assert not store.inserts and store.records[-1][-1] == label


def test_refresh_clickhouse_error_and_lock_lost_do_not_write() -> None:
    store = FakeStore(fail_on="fact_esb_purchase_line", **store_rows())
    result = refresh_margin(store, "pb", 3, TODAY, lock=_lock(store))
    assert (result.status, result.error) == ("error", "RuntimeError code=241")
    assert "rahasia" not in repr(store.records) and not store.inserts
    lost = FakeStore(lose_on="agg_esb_sales_daily", **store_rows())
    result = refresh_margin(lost, "pb", 3, TODAY, lock=_lock(lost))
    assert (result.status, result.error) == ("error", "lock_lost") and not lost.inserts


def test_templates_use_parameters_and_validated_db_names() -> None:
    for template in TEMPLATES:
        sql = render(template, FakeStore())
        assert "$" not in sql and re.search(r"\{(brand|name):String\}", sql), template
        assert "'pb'" not in sql

    class BadStore(FakeStore):
        @property
        def mart_db(self) -> str:
            return "efn_dwh; DROP TABLE x"

    with pytest.raises(ValueError):
        render(TEMPLATES[0], BadStore())
    with pytest.raises(ValueError):
        margin.refresh_name("pb'")


def test_templates_only_read_objects_granted_to_esb_writer() -> None:
    granted = set(re.findall(r"^GRANT SELECT[^;]* ON (efn_\w+\.\w+) TO esb_writer;$", ESB_ACCESS, re.MULTILINE))
    used: set[str] = set()
    for template in TEMPLATES:
        used |= set(re.findall(r"\b(efn_\w+\.\w+)", render(template, FakeStore())))
    assert used <= granted, used - granted
    assert {"efn_dwh.fact_esb_purchase_line", "efn_dwh.agg_esb_sales_daily", "efn_dwh.dim_esb_bom_line"} <= used


# ------------------------------------------------------------------------------------------------ jadwal + CLI
def run_once(
    schedule: MarginSchedule,
    store: Any,
    now: datetime = MONDAY,
    breaker: AuthBreaker | None = None,
) -> list[Any]:
    runtimes: Any = {"pb": ("rt-pb", "client-pb")}
    return schedule.run_due(SETTINGS, runtimes, store, breaker or AuthBreaker(), now, threading.Event())


def test_schedule_snapshot_then_refresh_once_per_day(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, ...]] = []

    def fake_snapshot(*args: Any, **kwargs: Any) -> RunRecord:
        calls.append(("snapshot", args[4], args[5]))
        return RunRecord("pb", "snapshot", V, RUN, status="ok")

    def fake_refresh(store: Any, brand: str, months: int, today: date) -> aggregates.RefreshResult:
        calls.append(("refresh", brand, months, today))
        return aggregates.RefreshResult(f"margin_{brand}", "ok", V, V)

    monkeypatch.setattr(runner, "run_snapshot", fake_snapshot)
    monkeypatch.setattr(runner, "refresh_margin", fake_refresh)
    schedule = MarginSchedule(MarginSettings(brands=("pb",)))
    results = run_once(schedule, FakeStore())
    assert calls == [("snapshot", ["bom_detail"], date(2026, 11, 23)), ("refresh", "pb", 13, date(2026, 11, 23))]
    assert [r.status for r in results] == ["ok"]
    # Siklus berikutnya pada hari WIB yang sama: tidak dicoba lagi.
    assert run_once(schedule, FakeStore(), now=MONDAY + timedelta(hours=1)) == []
    assert len(calls) == 2


def test_schedule_snapshot_failure_records_error_and_skips_compute(monkeypatch: pytest.MonkeyPatch) -> None:
    refreshed: list[Any] = []

    def failed_snapshot(*args: Any, **kwargs: Any) -> RunRecord:
        return RunRecord("pb", "snapshot", V, RUN, status="partial")

    monkeypatch.setattr(runner, "run_snapshot", failed_snapshot)
    monkeypatch.setattr(runner, "refresh_margin", lambda *args: refreshed.append(args))
    store = FakeStore()
    results = run_once(MarginSchedule(MarginSettings(brands=("pb",))), store)
    assert [(r.status, r.error) for r in results] == [("error", "snapshot_partial")]
    assert store.records[-1][:2] == ["margin_pb", "error"] and refreshed == []


def test_schedule_skips_not_due_suspended_unknown_and_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(runner, "run_snapshot", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(runner, "refresh_margin", lambda *args: calls.append(args))
    schedule = MarginSchedule(MarginSettings(brands=("pb", "wtz")))  # wtz tanpa runtime -> dilewati
    assert run_once(schedule, FakeStore(), now=MONDAY + timedelta(days=1)) == []  # Selasa
    recent = FakeStore(refresh=[(1, MONDAY - timedelta(days=2))])
    assert run_once(schedule, recent) == []  # ok terakhir 2 hari lalu
    breaker = AuthBreaker(threshold=1)
    breaker.record("pb", "auth_failed")
    assert run_once(schedule, FakeStore(), breaker=breaker) == []  # SEC-186
    assert run_once(schedule, FakeStore(fail_on="aggregate_refresh")) == []  # ClickHouse gagal: tidak melempar
    assert calls == []


def test_cli_refresh_margin(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("ESB_ENABLED", "false")  # hanya ClickHouse: jalan walau ESB mati
    monkeypatch.delenv("ESB_MARGIN_BRANDS", raising=False)
    monkeypatch.delenv("ESB_MARGIN_MONTHS", raising=False)

    def no_store(_env: Any) -> Any:
        raise AssertionError("fitur mati: ClickHouse tidak boleh dibuka")

    monkeypatch.setattr(cli, "_store", no_store)
    assert cli.main(["refresh-margin"]) == 0
    assert "fitur mati" in capsys.readouterr().out
    seen: list[tuple[str, int]] = []

    def fake_refresh(store: Any, brand: str, months: int, today: date) -> aggregates.RefreshResult:
        seen.append((brand, months))
        return aggregates.RefreshResult(f"margin_{brand}", "ok" if brand == "pb" else "error", V, V)

    monkeypatch.setattr(cli, "_store", lambda _env: FakeStore())
    monkeypatch.setattr(margin, "refresh_margin", fake_refresh)
    monkeypatch.setenv("ESB_MARGIN_MONTHS", "6")
    assert cli.main(["refresh-margin", "--brand", "pb"]) == 0
    monkeypatch.setenv("ESB_MARGIN_BRANDS", "pb,wtz")
    assert cli.main(["refresh-margin"]) == 1
    assert seen == [("pb", 6), ("pb", 6), ("wtz", 6)]
    monkeypatch.setenv("ESB_MARGIN_MONTHS", "99")
    assert cli.main(["refresh-margin"]) == 2
    with pytest.raises(SystemExit):
        cli.main(["refresh-margin", "--brand", "xyz"])


def test_margin_settings_defaults_and_parsing() -> None:
    assert MarginSettings.from_env({}) == MarginSettings()
    assert MarginSettings() == MarginSettings(brands=(), months=13, weekday=0, hour_wib=2)
    settings = MarginSettings.from_env({"ESB_MARGIN_BRANDS": " PB ; wtz,pb", "ESB_MARGIN_MONTHS": "24"})
    assert settings.brands == ("pb", "wtz") and settings.months == 24


@pytest.mark.parametrize(
    "env",
    [
        {"ESB_MARGIN_BRANDS": "pb,xyz"},
        {"ESB_MARGIN_MONTHS": "25"},
        {"ESB_MARGIN_MONTHS": "1"},
        {"ESB_MARGIN_MONTHS": "tiga"},
        {"ESB_MARGIN_WEEKDAY": "7"},
        {"ESB_MARGIN_HOUR_WIB": "24"},
    ],
)
def test_margin_settings_rejects_invalid(env: dict[str, str]) -> None:
    with pytest.raises(RuntimeError):
        MarginSettings.from_env(env)


def test_validate_margin_requires_active_brand_and_purchase_line() -> None:
    validate_margin(SETTINGS, MarginSettings())  # fitur mati
    validate_margin(SETTINGS, MarginSettings(brands=("pb",)))
    with pytest.raises(RuntimeError, match="ESB_BRANDS"):
        validate_margin(SETTINGS, MarginSettings(brands=("pb", "wtz")))
    with pytest.raises(RuntimeError, match="purchase_line"):
        validate_margin(EsbSettings(enabled=True, brands=(BRAND,)), MarginSettings(brands=("pb",)))


# ------------------------------------------------------------------------------------------------ DDL / grant / policy
TYPES = {
    "month": "Date",
    "receipt_qty": "Decimal(18, 3)",
    "value": "Decimal(18, 2)",
    "price": "Decimal(18, 4)",
    "price_4m": "Decimal(18, 4)",
    "idx": "Nullable(Float64)",
    "usage_value": "Float64",
    "qty": "Decimal(18, 3)",
    "net": "Decimal(18, 2)",
    "cogs": "Decimal(18, 2)",
    "bom_matched": "UInt8",
    "cost_idx": "Nullable(Float64)",
    "leaf_coverage": "Float64",
    "basket_value": "Float64",
    "base_value": "Float64",
    "_version": "DateTime64(3, 'UTC')",
}


def table_block(name: str) -> str:
    return DDL.split(f"CREATE TABLE IF NOT EXISTS efn_dwh.{name}\n", 1)[1].split(";", 1)[0]


def view_body(name: str) -> str:
    return DDL.split(f"CREATE OR REPLACE VIEW efn_dwh.{name} AS\n", 1)[1].split(";", 1)[0]


def column_type(block: str, column: str) -> str:
    match = re.search(rf"^\s{{4}}{column}\s+(.+?),?(?:\s+--.*)?$", block, re.MULTILINE)
    assert match, column
    return match.group(1)


def test_ddl_tables_columns_engine_policy_ttl() -> None:
    code = re.sub(r"--[^\n]*", "", DDL)
    assert not re.search(r"\b(DROP|TRUNCATE|ALTER|INSERT|EXCHANGE|RENAME|OPTIMIZE)\b", code)
    assert set(re.findall(r"CREATE TABLE IF NOT EXISTS efn_dwh\.(\w+)", code)) == set(MART_TABLES)
    assert len(re.findall(r"CREATE TABLE ", code)) == len(MART_TABLES)  # semua IF NOT EXISTS
    for table, columns in MART_TABLES.items():
        block = table_block(table)
        body = block.split("\nENGINE", 1)[0]
        assert re.findall(r"^\s{4}(\w+)\s", body, re.MULTILINE) == [*columns, "_loaded_at"], table
        assert "\nENGINE = MergeTree\n" in block, table  # bukan Replacing: versi-insert (L-263)
        assert "\nSETTINGS storage_policy = 'encrypted'" in block, table
        assert "\nTTL toDateTime(_loaded_at) + INTERVAL 60 DAY\n" in block, table
        assert re.search(r"\nORDER BY \(brand, company_code, _version, month, \w+\)\n", block), table
        for column in columns:
            if column in TYPES:
                assert column_type(block, column) == TYPES[column], (table, column)
        assert column_type(block, "_loaded_at") == "DateTime64(3, 'UTC') DEFAULT now64(3)"
    assert "supplier" not in code  # tanpa nama supplier (PII perorangan, §27.13)


def test_views_show_latest_version_per_brand_without_version_column() -> None:
    for table, columns in MART_TABLES.items():
        view = table.replace("mart_esb_", "v_esb_")
        body = view_body(view)
        head = body.split("\nFROM ", 1)[0]
        assert re.findall(r"\bAS (\w+)", head) == [c for c in columns if c != "_version"], view
        assert f"\nFROM efn_dwh.{table} AS t\n" in body, view
        subquery = f"(SELECT brand, max(_version) FROM efn_dwh.{table} GROUP BY brand)"
        assert f"WHERE (t.brand, t._version) IN\n      {subquery}" in body, view


def test_esb_writer_select_insert_only_on_margin_tables() -> None:
    code = re.sub(r"--[^\n]*", "", ESB_ACCESS)
    lines = [line for line in code.splitlines() if "mart_esb_" in line]
    assert lines == [f"GRANT SELECT, INSERT ON efn_dwh.{table} TO esb_writer;" for table in MART_TABLES]


PRICE_ABSOLUTE = ("receipt_qty", "value", "price", "price_4m")


def test_insight_wrappers_definer_grants_and_pt_policy() -> None:
    code = re.sub(r"--[^\n]*", "", INSIGHT_ACCESS)
    for table, columns in MART_TABLES.items():
        view = table.replace("mart_esb_", "v_esb_")
        # SEC-MB-03: wrapper harga tanpa harga beli absolut (receipt_qty/value/price/price_4m).
        cols = ", ".join(c for c in columns if c not in ("_version", *PRICE_ABSOLUTE))
        header = f"CREATE OR REPLACE VIEW efn_insight.{view} DEFINER = efn_insight_definer SQL SECURITY DEFINER AS\n"
        assert f"{header}SELECT {cols}\nFROM efn_dwh.{view};" in code, view
        assert f"GRANT SELECT ON efn_dwh.{view} TO efn_insight_definer;" in code, view
        assert f"GRANT SELECT ON efn_dwh.{table} TO efn_insight_definer;" in code, table
    block = POLICY_SH.split('ESB_POLICY_VIEWS="\n', 1)[1].split('\n"\n', 1)[0]
    policy = dict(line.split() for line in block.splitlines() if line.strip())
    assert all(policy[table.replace("mart_esb_", "v_esb_")] == "PT" for table in MART_TABLES)
    assert "\nESB_POLICY_EXPECTED=25\n" in INIT_SH  # 20 setelah #165, + 5 view G68 (docs/04 §32.7)


def test_margin_objects_not_granted_to_other_roles() -> None:
    names = [*MART_TABLES, *(table.replace("mart_esb_", "v_esb_") for table in MART_TABLES)]
    for path in sorted((CH / "access").glob("*.sql")):
        text = re.sub(r"--[^\n]*", "", path.read_text(encoding="utf-8"))
        if path.name not in ("esb-access.sql", "insight-access.sql"):
            assert not any(name in text for name in names), path.name  # metabase/text2sql/forecast/mig/segment
        for grant in re.findall(r"GRANT [^;]*;", text):
            if any(name in grant for name in names):
                assert re.search(r" TO (esb_writer|efn_insight_definer);$", grant), (path.name, grant)


def test_init_fail_closed_guard_covers_margin_tables() -> None:
    start = INIT_SH.index("MARGIN_TABLES=")
    assert all(f"'{table}'" in INIT_SH[start:] for table in MART_TABLES)
    assert "arrayJoin([${AGG_ALL},${MARGIN_TABLES}])" in INIT_SH
    assert "agg_esb_*|mart_esb_*) ;;" in INIT_SH
    assert INIT_SH.index("MARGIN_TABLES=") > INIT_SH.index("system.row_policies WHERE database = 'efn_insight'")
