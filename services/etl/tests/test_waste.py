"""G32 (docs/04 §21, K25-08): mart waste & yield CK (efn_etl/waste.py) — transform murni, SQL minim-PII, grant, DDL."""

import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from efn_etl import waste
from efn_etl.jobs import enabled_jobs

WD = datetime(2026, 10, 27, 13, 23, 12)  # naive = UTC (Odoo)
DONE = datetime(2026, 10, 27, 13, 23, 18)
SQL_DIR = Path(__file__).resolve().parents[1] / "sql"
CH_INIT = Path(__file__).resolve().parents[3] / "infra" / "clickhouse" / "init" / "11-g32-waste-yield.sql"
UTC_WD = WD.replace(tzinfo=UTC)


def test_scrap_transform_done_and_reverted() -> None:
    row: dict[str, Any] = {
        "scrap_id": 1,
        "company_id": 6,
        "brand": "GL",
        "warehouse_id": 10,
        "product_id": 1109,
        "categ_id": 4,
        "reason_id": 2,
        "production_id": None,
        "qty": Decimal("4.0004"),
        "waste_value": Decimal("112000.004"),
        "state": "done",
        "date_done": DONE,
        "write_date": WD,
    }
    assert waste.transform_scrap(row) == [
        1,
        6,
        "GL",
        10,
        1109,
        4,
        2,
        0,
        Decimal("4.000"),
        Decimal("112000.00"),
        1,
        DONE.replace(tzinfo=UTC),
        UTC_WD,
    ]
    # Draft/batal -> is_done 0 (versi baru menimpa di ReplacingMergeTree); tanpa alasan -> 0;
    # tanpa date_done -> write_date.
    out = waste.transform_scrap({**row, "state": "draft", "reason_id": None, "date_done": None})
    assert (out[6], out[10], out[11]) == (0, 0, UTC_WD)
    assert len(out) == len(waste.SCRAP_COLUMNS)


def test_reason_name_from_translated_jsonb() -> None:
    assert waste.reason_name({"en_US": "Expired", "id_ID": "Kedaluwarsa"}) == "Kedaluwarsa"
    assert waste.reason_name({"en_US": "Susut"}) == "Susut"
    assert waste.reason_name({"fr_FR": "Perte"}) == "Perte"
    assert waste.reason_name(None) == ""
    assert waste.reason_name("x" * 300) == "x" * 128
    assert waste.transform_reason({"reason_id": 3, "name": {"en_US": "Reject QC"}, "write_date": WD}) == [
        3,
        "Reject QC",
        UTC_WD,
    ]


def test_mo_transform_zeroes_measures_when_not_done() -> None:
    row: dict[str, Any] = {
        "production_id": 9,
        "company_id": 6,
        "brand": "GL",
        "warehouse_id": 10,
        "product_id": 1108,
        "bom_id": 3,
        "planned_qty": Decimal("80"),
        "produced_qty": Decimal("76"),
        "yield_pct": Decimal("95"),
        "byproduct_qty": Decimal("1.5"),
        "variance_value": Decimal("132200"),
        "alert": True,
        "state": "done",
        "date_finished": DONE,
        "write_date": WD,
    }
    done = waste.transform_mo(row)
    assert done[6:13] == [
        Decimal("80.000"),
        Decimal("76.000"),
        Decimal("95.00"),
        Decimal("1.500"),
        Decimal("132200.00"),
        1,
        1,
    ]
    assert len(done) == len(waste.MO_COLUMNS)
    # MO di-unbuild/batal setelah selesai -> baris versi baru is_done 0, ukuran 0 (rencana tetap), alert 0.
    reverted = waste.transform_mo({**row, "state": "cancel", "date_finished": None})
    assert reverted[6:14] == [
        Decimal("80.000"),
        Decimal("0.000"),
        Decimal("0.00"),
        Decimal("0.000"),
        Decimal("0.00"),
        0,
        0,
        UTC_WD,
    ]


@pytest.mark.parametrize(
    ("expected", "diff", "pct"),
    [
        (Decimal("19"), Decimal("3.4"), Decimal("17.9")),
        (Decimal("5"), Decimal("-5"), Decimal("-100.0")),
        (Decimal("0"), Decimal("2"), Decimal("100.0")),  # tidak ada di BoM tapi dikonsumsi (sama dengan Odoo)
        (Decimal("0"), Decimal("0"), Decimal("0.0")),
    ],
)
def test_variance_pct(expected: Decimal, diff: Decimal, pct: Decimal) -> None:
    assert waste.variance_pct(expected, diff) == pct


def test_component_transform_matches_odoo_variance() -> None:
    # Data dev MO #9 (yield 95%): tepung BoM 20 -> expected 19, aktual 22.4, harga pokok 38.000.
    row = {
        "production_id": 9,
        "company_id": 6,
        "warehouse_id": 10,
        "product_id": 1110,
        "state": "done",
        "expected_qty": Decimal("19.0000"),
        "actual_qty": Decimal("22.4"),
        "unit_cost": Decimal("38000"),
        "date_finished": DONE,
        "write_date": WD,
    }
    out = waste.transform_component(row)
    assert out[4:10] == [
        Decimal("19.000"),
        Decimal("22.400"),
        Decimal("3.400"),
        Decimal("17.9"),
        Decimal("129200.00"),
        1,
    ]
    # Komponen dibatalkan (tidak dikonsumsi), tanpa harga pokok -> nilai 0 tapi selisih qty tetap terlihat.
    cancel = waste.transform_component({**row, "actual_qty": 0, "unit_cost": None})
    assert cancel[5:9] == [Decimal("0.000"), Decimal("-19.000"), Decimal("-100.0"), Decimal("0.00")]
    assert len(out) == len(waste.COMPONENT_COLUMNS)


def test_threshold_and_warehouse_transform() -> None:
    assert waste.transform_threshold(
        {
            "company_id": 6,
            "brand": "GL",
            "yield_warn_pct": 95,
            "variance_warn_pct": Decimal("5"),
            "waste_warn_value": None,
            "write_date": WD,
        }
    ) == [6, "GL", Decimal("95.00"), Decimal("5.00"), Decimal("0.00"), UTC_WD]
    assert waste.transform_warehouse(
        {"warehouse_id": 10, "name": " CK Cikarang ", "company_id": 6, "is_ck": True, "active": False, "write_date": WD}
    ) == [10, "CK Cikarang", 6, 1, 0, UTC_WD]


# Kolom yang TIDAK boleh dibaca ETL (PDP: partner konsinyasi, pelaku, dokumen, lot).
FORBIDDEN = ("owner_id", "user_id", "create_uid", "origin", "lot_id", "lot_producing_id", "partner_id", "package_id")


@pytest.mark.parametrize("job", waste.waste_jobs(), ids=lambda j: j.name)
def test_sql_minimised_and_keyset(job: Any) -> None:
    words = set(re.findall(r"[a-z_]+", job.sql))
    assert not words & set(FORBIDDEN)
    assert "select *" not in job.sql.lower()
    for token in ("ORDER BY", "%(cursor_ts)s", "%(cursor_key)s", "LIMIT %(limit)s", "_cursor_ts", "_cursor_key"):
        assert token in job.sql, token
    # Nama/nomor dokumen tidak dibaca: `name` hanya dari tag alasan & gudang (label master, bukan dokumen).
    if job.name not in ("waste_reasons", "ck_warehouses"):
        assert not re.search(r"\b\w+\.name\b", job.sql), job.name


def test_component_cursor_key_unique_per_mo_and_product() -> None:
    sql = waste.SQL_MO_COMPONENTS
    # Kunci kursor menyertakan produk -> halaman yang terpotong di tengah MO tidak melompati komponen.
    assert "lpad(m.product_id::text, 12, '0') AS _cursor_key" in sql
    assert sql.count("lpad(mo.id::text, 12, '0') || ':' || lpad(m.product_id::text, 12, '0')") == 2


def test_enabled_jobs_waste_flag() -> None:
    assert not {j.name for j in enabled_jobs(True)} & {j.name for j in waste.waste_jobs()}
    jobs = {j.name: j for j in enabled_jobs(True, waste=True)}
    assert {name: jobs[name].target_table for name in jobs if name in {j.name for j in waste.waste_jobs()}} == {
        "waste_reasons": "dim_scrap_reason",
        "ck_thresholds": "dim_ck_threshold",
        "ck_warehouses": "dim_warehouse",
        "waste_scraps": "fact_scrap",
        "mo_yield": "fact_mo_yield",
        "mo_components": "fact_mo_component",
    }
    assert all(jobs[j.name].source == "odoo" for j in waste.waste_jobs())
    assert "waste_scraps" not in [j.name for j in enabled_jobs(False, waste=True)]  # tanpa Odoo -> tidak ada


def _granted(block: str, table: str) -> set[str]:
    found = re.search(rf"GRANT SELECT \(([^)]*)\)\s+ON public\.{table} TO efn_etl", block)
    assert found, table
    return {c.strip() for c in found.group(1).split(",")}


def test_odoo_grants_cover_sql_and_exclude_pii() -> None:
    grants = (SQL_DIR / "odoo-readonly-grants.sql").read_text(encoding="utf-8")
    block = grants.split("Gelombang 32 (efn-data-ai-engineer)", 1)[1]
    assert "\\if :efn_g32_waste" in block
    alias_table = {
        "s": "stock_scrap",
        "mo": "mrp_production",
        "m": "stock_move",
        "r": "stock_scrap_reason_tag",
        "spt": "stock_picking_type",
    }
    for job in waste.waste_jobs():
        for alias, table in alias_table.items():
            read = set(re.findall(rf"\b{alias}\.([a-z_]+)", job.sql))
            if not read:
                continue
            granted = _granted(block, table)
            if table == "stock_scrap_reason_tag":  # G32 hanya menambah reason tag; kolomnya lengkap di blok G32
                assert read <= granted, (job.name, table, read - granted)
            else:
                assert read <= granted | {"id"}, (job.name, table, read - granted)
            assert not granted & set(FORBIDDEN), table
    assert _granted(block, "stock_scrap_stock_scrap_reason_tag_rel") == {"stock_scrap_id", "stock_scrap_reason_tag_id"}
    # Kolom ambang & gudang tambahan G32 (kolom lain sudah di-grant G13/G21).
    assert {"efn_ck_yield_warn_pct", "efn_ck_variance_warn_pct", "efn_ck_waste_warn_value"} <= _granted(
        block, "res_company"
    )
    assert {"efn_is_central_kitchen", "company_id", "active", "write_date"} <= _granted(block, "stock_warehouse")


def test_clickhouse_columns_match_transform() -> None:
    ddl = CH_INIT.read_text(encoding="utf-8")
    for job in waste.waste_jobs():
        table = ddl.split(f"CREATE TABLE IF NOT EXISTS efn_dwh.{job.target_table}", 1)[1].split("ENGINE", 1)[0]
        for column in job.columns:
            assert re.search(rf"^\s+{column}\s", table, re.MULTILINE), (job.target_table, column)
    for view in (
        "v_waste_daily",
        "v_waste_warehouse_daily",
        "v_mo_yield",
        "v_mo_yield_daily",
        "v_mo_component_variance",
    ):
        assert f"CREATE OR REPLACE VIEW efn_dwh.{view} AS" in ddl
    assert ddl.count("WHERE is_done = 1") >= 4  # view hanya membaca versi done
