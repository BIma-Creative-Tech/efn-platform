import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from efn_etl.jobs import (
    COMPETITOR_PRICES,
    COMPETITOR_PRICES_COLUMNS,
    LOYALTY_EVENTS,
    LOYALTY_EVENTS_COLUMNS,
    SALES_FACT,
    SALES_FACT_COLUMNS,
    AgeOut,
    enabled_jobs,
    pseudonym,
    transform_competitor_price,
    transform_loyalty_event,
    transform_sales_line,
    years_before,
)

# Hash sudah dihitung BFF (docs/04 §1); ETL hanya meneruskan, tidak memegang pepper (SEC-16).
USER_HASH = "8930b421545b77ea6ff5093510c261afe67f1b1e3e18c925cb76d59516926653"
PHONE = "6281234567890"
# Waktu "sekarang" tetap: test tidak berubah hasil seiring waktu (umur kolom W-19 dihitung relatif terhadap now).
NOW = datetime(2026, 10, 10, 3, 0, tzinfo=UTC)


@pytest.mark.parametrize("job", [COMPETITOR_PRICES, LOYALTY_EVENTS, SALES_FACT])
def test_job_sql_contract(job) -> None:
    sql = job.sql
    assert "SELECT *" not in sql.upper()
    for param in ("%(cursor_ts)s", "%(cursor_key)s", "%(limit)s"):
        assert param in sql
    assert "_cursor_ts" in sql and "_cursor_key" in sql
    assert re.search(r"ORDER BY\s+\S+,\s+\S+", sql)
    # Kolom terenkripsi & nomor HP mentah tidak pernah dibaca ETL.
    assert "_enc" not in sql
    assert "user_id" not in sql


def test_pseudonym_accepts_only_sha256_hex() -> None:
    assert pseudonym(USER_HASH, "c") == USER_HASH
    for bad in (PHONE, "", None, USER_HASH.upper(), USER_HASH[:-1], "z" * 64):
        with pytest.raises(ValueError):
            pseudonym(bad, "c")


def _report_row(**overrides: object) -> dict[str, object]:
    ts = datetime(2026, 10, 5, 3, 0, tzinfo=UTC)
    row: dict[str, object] = {
        "id": uuid.uuid4(),
        "created_at": ts,
        "product_name": "Indomie Goreng",
        "competitor_name": None,
        "reported_price": Decimal("3500.00"),
        "extracted_price": "3500",
        "status": "valid",
        "reason": "price_and_name_match",
        "confidence": Decimal("0.912"),
        "geo_lat": -8.670512345,
        "geo_lng": 115.212634567,
        "user_hash": USER_HASH,
        "updated_at": ts,
    }
    row.update(overrides)
    return row


def test_transform_competitor_price_passes_hash_and_rounds_geo() -> None:
    row = _report_row()
    out = dict(zip(COMPETITOR_PRICES_COLUMNS, transform_competitor_price(row, now=NOW), strict=True))
    assert out["report_id"] == row["id"]
    assert out["competitor_name"] == "unknown"
    assert out["price"] == Decimal("3500.00")
    assert out["extracted_price"] == Decimal("3500")
    assert out["geo_lat"] == -8.671
    assert out["geo_lng"] == 115.213
    assert out["user_hash"] == USER_HASH


def test_transform_competitor_price_fails_closed_on_raw_phone() -> None:
    with pytest.raises(ValueError):
        transform_competitor_price(_report_row(user_hash=PHONE))


def _ledger_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "event_id": "17",
        "member_ref_hash": USER_HASH,
        "company_code": "PB",
        "delta_points": 10,
        "reason": "price_hunter_valid",
        "source": "price_hunter",
        "source_ref": "r-1",
        "created_at": datetime(2026, 10, 5, 3, 0),
    }
    row.update(overrides)
    return row


def test_transform_loyalty_event_with_company_code() -> None:
    out = dict(zip(LOYALTY_EVENTS_COLUMNS, transform_loyalty_event(_ledger_row(), now=NOW), strict=True))
    assert out["member_hash"] == USER_HASH
    assert out["company_code"] == "PB"
    assert out["created_at"].tzinfo is UTC
    assert "company_code" in LOYALTY_EVENTS.sql


@pytest.mark.parametrize("code", ["EFN", "GL", "PB", "BCR", "BCF", "FARM", "bcf"])
def test_transform_loyalty_event_accepts_six_entities(code: str) -> None:
    row = transform_loyalty_event(_ledger_row(company_code=code), now=NOW)
    out = dict(zip(LOYALTY_EVENTS_COLUMNS, row, strict=True))
    assert out["company_code"] == code.upper()


@pytest.mark.parametrize(
    "override",
    [
        {"member_ref_hash": PHONE},
        {"company_code": "XYZ"},
        {"company_code": None},
        # Kode lama G19 dihapus G20 (docs/04 §12.1): ledger dev lama harus dibuang, bukan dimuat.
        {"company_code": "RTL"},
        {"company_code": "FNB"},
        {"company_code": "BACHA"},  # brand_code kanal, bukan kode PT
    ],
)
def test_transform_loyalty_event_rejects_bad_rows(override: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        transform_loyalty_event(_ledger_row(**override))


def test_transform_sales_line() -> None:
    row = {
        "line_id": 5,
        "company_id": 2,
        "company_name": "PT Paris Baguette",
        "brand": "PB",
        "store_id": 3,
        "order_ref": "Order 00012-003-0004",
        "order_date": datetime(2026, 10, 5, 3, 0),
        "customer_id": None,
        "product_id": 9,
        "product_name": "Kopi Susu",
        "category": "Minuman",
        "qty": Decimal("2"),
        "price_unit": Decimal("18000"),
        "discount_pct": None,
        "subtotal": Decimal("36000"),
        "tax_amount": Decimal("3600"),
        "cost_total": None,
        "write_date": datetime(2026, 10, 5, 3, 1),
    }
    out = dict(zip(SALES_FACT_COLUMNS, transform_sales_line(row, now=NOW), strict=True))
    assert out["source"] == "pos"
    assert out["discount_pct"] == Decimal("0")
    assert out["customer_id"] is None
    assert out["cost_total"] is None


def test_odoo_job_disabled_by_default() -> None:
    assert [j.name for j in enabled_jobs(False)] == ["competitor_prices", "loyalty_events"]
    assert "sales_fact_pos" in [j.name for j in enabled_jobs(True)]


# ---------------------------------------------------------------- G18/W-19: umur kolom pribadi (Q-P7, Q-P8)


def test_years_before_handles_leap_day() -> None:
    assert years_before(datetime(2028, 2, 29, tzinfo=UTC), 5) == datetime(2023, 2, 28, tzinfo=UTC)
    assert years_before(NOW, 5) == datetime(2021, 10, 10, 3, 0, tzinfo=UTC)


def test_age_out_boundaries() -> None:
    policy = AgeOut(geo_days=90, pseudonym_years=5)
    assert not policy.geo_expired(NOW - timedelta(days=90), NOW)
    assert policy.geo_expired(NOW - timedelta(days=90, seconds=1), NOW)
    assert not policy.pseudonym_expired(datetime(2021, 10, 10, 3, 0, tzinfo=UTC), NOW)
    assert policy.pseudonym_expired(datetime(2021, 10, 10, 2, 59, tzinfo=UTC), NOW)
    # Naive = UTC (Odoo write_date/date_order).
    assert policy.pseudonym_expired(datetime(2020, 1, 1), NOW)


def test_competitor_price_geo_nulled_after_90_days_hash_kept() -> None:
    row = _report_row(created_at=NOW - timedelta(days=91), updated_at=NOW)
    out = dict(zip(COMPETITOR_PRICES_COLUMNS, transform_competitor_price(row, now=NOW), strict=True))
    assert out["geo_lat"] is None and out["geo_lng"] is None
    assert out["user_hash"] == USER_HASH
    assert out["price"] == Decimal("3500.00")  # baris & atribut non-pribadi tetap


def test_competitor_price_pseudonym_blanked_after_5_years() -> None:
    row = _report_row(created_at=datetime(2021, 1, 1, tzinfo=UTC))
    out = dict(zip(COMPETITOR_PRICES_COLUMNS, transform_competitor_price(row, now=NOW), strict=True))
    assert out["user_hash"] == ""
    assert out["geo_lat"] is None


def test_competitor_price_old_row_still_fails_closed_on_phone() -> None:
    # Nilai yang bukan hash tetap membatalkan halaman walau akan dikosongkan (indikasi skema/sumber salah).
    with pytest.raises(ValueError):
        transform_competitor_price(_report_row(user_hash=PHONE, created_at=datetime(2020, 1, 1, tzinfo=UTC)), now=NOW)


def test_custom_age_out_policy() -> None:
    row = _report_row(created_at=NOW - timedelta(days=31))
    out = dict(zip(COMPETITOR_PRICES_COLUMNS, transform_competitor_price(row, AgeOut(geo_days=30), NOW), strict=True))
    assert out["geo_lat"] is None


def test_loyalty_member_hash_blanked_after_5_years() -> None:
    fresh = dict(zip(LOYALTY_EVENTS_COLUMNS, transform_loyalty_event(_ledger_row(), now=NOW), strict=True))
    old = dict(
        zip(
            LOYALTY_EVENTS_COLUMNS,
            transform_loyalty_event(_ledger_row(created_at=datetime(2021, 6, 1)), now=NOW),
            strict=True,
        )
    )
    assert fresh["member_hash"] == USER_HASH
    assert old["member_hash"] == ""
    assert old["delta_points"] == 10 and old["company_code"] == "PB"


def test_sales_customer_id_nulled_after_5_years() -> None:
    row = {
        "line_id": 6,
        "company_id": 2,
        "company_name": "PT Paris Baguette",
        "brand": "PB",
        "store_id": 3,
        "order_ref": "Order 1",
        "order_date": datetime(2021, 1, 1, 3, 0),
        "customer_id": 77,
        "product_id": 9,
        "product_name": "Kopi Susu",
        "category": "Minuman",
        "qty": Decimal("1"),
        "price_unit": Decimal("18000"),
        "discount_pct": None,
        "subtotal": Decimal("18000"),
        "tax_amount": Decimal("0"),
        "cost_total": None,
        "write_date": datetime(2026, 10, 5, 3, 1),
    }
    old = dict(zip(SALES_FACT_COLUMNS, transform_sales_line(row, now=NOW), strict=True))
    assert old["customer_id"] is None
    recent = dict(zip(SALES_FACT_COLUMNS, transform_sales_line({**row, "order_date": NOW}, now=NOW), strict=True))
    assert recent["customer_id"] == 77


def test_enabled_jobs_apply_age_out_policy() -> None:
    policy = AgeOut(geo_days=1, pseudonym_years=1)
    job = enabled_jobs(False, age_out=policy)[0]
    assert job.name == "competitor_prices"
    out = dict(zip(job.columns, job.transform(_report_row(created_at=datetime(2020, 1, 1, tzinfo=UTC))), strict=True))
    assert out["geo_lat"] is None and out["user_hash"] == ""
