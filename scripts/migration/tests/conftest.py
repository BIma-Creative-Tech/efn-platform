"""Fixture sintetis (bukan data klien): kunci uji, sumber ClickHouse palsu, tabel pemetaan."""

from collections.abc import Iterator, Sequence
from datetime import date, timedelta
from decimal import Decimal as D
from typing import Any

import pytest

from efn_mig.bundle import Keys, derive_keys
from efn_mig.export import Maps

TEST_KEY = "ab" * 32  # 64 hex, hanya untuk test

BRANCH_MAP = (
    "brand,branch_id,type,warehouse_code,display_name,parent_branch_id\n"
    "pb,4,ck,PBCK1,CK Satu,\n"
    "pb,11,store,PB011,Toko Sebelas,\n"
    "pb,12,store,PB012,Toko Dua Belas,\n"
    "pb,30,location,,Gudang Kering,4\n"
    "pb,99,skip,,,\n"
    "wtz,11,store,WZ011,Lain Brand,\n"
)
UOM_MAP = (
    "esb_uom,uom_xmlid,ea_factor\nEA,uom.product_uom_unit,1\nPACK,uom.product_uom_unit,6\nKG,uom.product_uom_kgm,\n"
)


@pytest.fixture
def keys() -> Keys:
    return derive_keys(TEST_KEY)


@pytest.fixture
def maps() -> Maps:
    return Maps.from_bytes({"branch_map.csv": BRANCH_MAP.encode(), "uom_map.csv": UOM_MAP.encode()}, "pb")


def branch(branch_id: int, name: str, is_ck: int = 0, is_store: int = 1, active: int = 1) -> dict[str, Any]:
    return {
        "branch_id": branch_id,
        "company_code": "PB",
        "branch_code": f"B{branch_id}",
        "branch_name": name,
        "is_ck": is_ck,
        "is_store": is_store,
        "active": active,
    }


def product(code: str, name: str, category: str, uom: str, is_fin: int, ratio: float | None) -> dict[str, Any]:
    return {
        "product_code": code,
        "company_code": "PB",
        "product_name": name,
        "category": category,
        "uom": uom,
        "is_fin": is_fin,
        "active": 1,
        "batch_to_ea_ratio": ratio,
    }


def menu(code: str, name: str, takeaway: int = 0, detail: str = "Roti") -> dict[str, Any]:
    return {
        "menu_code": code,
        "menu_name": name,
        "menu_category": "Bakery",
        "menu_category_detail": detail,
        "is_takeaway": takeaway,
    }


def bom(bom_id: int, name: str, type_name: str, product_code: str) -> dict[str, Any]:
    return {
        "bom_id": bom_id,
        "bom_code": f"B{bom_id}",
        "bom_name": name,
        "bom_type_id": 1,
        "bom_type_name": type_name,
        "product_code": product_code,
        "product_name": "",
        "uom": "EA",
        "active": 1,
    }


def bom_line(bom_id: int, code: str, qty: str, uom: str, hpp: str) -> dict[str, Any]:
    return {
        "bom_id": bom_id,
        "line_id": 1,
        "product_code": code,
        "product_name": code,
        "qty": D(qty),
        "uom": uom,
        "last_hpp": D(hpp),
        "yield_percent": D("100"),
    }


def flow(day: date, branch_id: int, code: str, sold: int, in_ck: int, st_out: int) -> dict[str, Any]:
    row = {c: D(0) for c in ("ck_prod", "store_prod", "ck_out", "in_other", "st_in")}
    row.update(day=day, company_code="PB", branch_id=branch_id, product_code=code)
    row.update(sold=D(sold), in_ck=D(in_ck), st_out=D(st_out))
    return row


def sales_daily(day: date, branch_id: int, code: str) -> dict[str, Any]:
    return {
        "sales_date": day,
        "company_code": "PB",
        "branch_id": branch_id,
        "menu_code": code,
        "is_takeaway": 1,
        "qty": D("3.000"),
        "gross": D("75000.00"),
        "discount": D("5000.00"),
        "cogs": D("10500.00"),
        "trx_count": 2,
    }


def sales_line(day: date, branch_id: int, line_no: int, code: str) -> dict[str, Any]:
    return {
        "sales_date": day,
        "company_code": "PB",
        "branch_id": branch_id,
        "sales_num": f"S{branch_id}{day:%m%d}",
        "line_no": line_no,
        "menu_code": code,
        "qty": D("1.000"),
        "price": D("25000.00"),
        "total": D("25000.00"),
        "discount": D("0.00"),
        "cogs": D("3500.00"),
        "row_type": "ALACARTE",
        "is_takeaway": 1,
    }


class FakeSource:
    """Meniru `efn_mig.source.ClickHouseSource` dengan baris dict sintetis."""

    def __init__(self) -> None:
        self.dims: dict[str, list[dict[str, Any]]] = {
            "dim_esb_branch": [
                branch(4, "CK", is_ck=1, is_store=0),
                branch(11, "S11"),
                branch(12, "S12"),
                branch(30, "WH", is_store=0),
                branch(99, "Tutup", active=0),
            ],
            "dim_esb_product": [
                product("FIN-001", "FIN Roti Tawar", "Roti", "", 1, 12.0),
                product("RM001", "Tepung", "Bahan", "KG", 0, None),
            ],
            "dim_esb_menu": [menu("M01", "Roti Tawar"), menu("M01T", "Roti Tawar T", takeaway=1)],
            "dim_esb_supplier": [{"supplier_code": "SUP1", "supplier_name": "PT Pemasok", "active": 1}],
            "dim_esb_menu_price": [
                {
                    "menu_code": "M01",
                    "menu_name": "Roti Tawar",
                    "price": D("25000.00"),
                    "bom_name": "BOM MENU ROTI",
                    "active": 1,
                },
            ],  # fmt: skip
            "dim_esb_customer": [
                {
                    "customer_code": "C001",
                    "customer_name": "PT Mitra",
                    "customer_category": "B2B",
                    "payment_due_days": 30,
                    "active": 1,
                },
            ],  # fmt: skip
            "dim_esb_bom": [bom(7, "BOM ROTI", "Production", "FIN-001"), bom(8, "BOM MENU ROTI", "Menu", "")],
            "dim_esb_bom_line": [bom_line(7, "RM001", "2.5", "KG", "12000"), bom_line(8, "FIN-001", "1", "EA", "3500")],
        }
        self.history: dict[str, list[dict[str, Any]]] = {
            "legacy_flow_daily": [],
            "legacy_sales_daily": [],
            "legacy_sales_line": [],
        }
        start = date(2026, 9, 28)
        for i in range(10):  # 28 Sep .. 7 Okt 2026
            day = start + timedelta(days=i)
            for branch_id in (11, 12):
                self.history["legacy_flow_daily"].append(flow(day, branch_id, "FIN-001", 10, 12, 1))
                self.history["legacy_flow_daily"].append(flow(day, branch_id, "NAME:ROTI X", 1, 0, 0))
                self.history["legacy_sales_daily"].append(sales_daily(day, branch_id, "M01T"))
                self.history["legacy_sales_line"].append(sales_line(day, branch_id, 1, "M01T"))
                self.history["legacy_sales_line"].append(sales_line(day, branch_id, 2, "OLD9"))

    def companies(self, brand: str) -> list[str]:
        return ["PB"]

    def dim(self, table: str, brand: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.dims[table]]

    def bom_ratios(self, brand: str) -> dict[str, float]:
        return {"FIN-001": 12.0}

    def archive_menus(self, brand: str, start: date, end: date) -> list[dict[str, Any]]:
        return [menu("OLD9", "Menu Lama", detail="")]

    def snapshot(self, brand: str) -> dict[str, str]:
        return {"dim_esb_product": "2026-11-18T00:00:00+00:00"}

    @staticmethod
    def _day(file: str, row: dict[str, Any]) -> date:
        return row["day"] if file == "legacy_flow_daily" else row["sales_date"]

    def history_counts(self, brand: str, company: str, file: str, start: date, end: date) -> dict[int, int]:
        out: dict[int, int] = {}
        for r in self.history[file]:
            if r["company_code"] == company and start <= self._day(file, r) < end:
                out[r["branch_id"]] = out.get(r["branch_id"], 0) + 1
        return out

    def history_rows(
        self, brand: str, company: str, file: str, start: date, end: date, branch_ids: Sequence[int]
    ) -> Iterator[dict[str, Any]]:
        for r in self.history[file]:
            if r["company_code"] == company and start <= self._day(file, r) < end and r["branch_id"] in branch_ids:
                yield dict(r)


@pytest.fixture
def source() -> FakeSource:
    return FakeSource()
