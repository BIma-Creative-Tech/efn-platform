import re
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from efn_etl.jobs import (
    SALES_FACT,
    SALES_FACT_COLUMNS,
    enabled_jobs,
    line_discount_amount,
    line_kind,
    transform_sales_line,
)
from efn_etl.pos import (
    OUTLET_COLUMNS,
    POS_JOBS,
    POS_PAYMENT_COLUMNS,
    POS_PAYMENTS,
    PRODUCT_COLUMNS,
    PRODUCTS,
    transform_outlet,
    transform_pos_payment,
    transform_product,
)

TS = datetime(2026, 10, 7, 3, 0)


def test_enabled_jobs_with_odoo() -> None:
    names = [j.name for j in enabled_jobs(True)]
    assert names == ["competitor_prices", "loyalty_events", "sales_fact_pos", "pos_outlets", "products", "pos_payments"]


def _pos_line(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "line_id": 7,
        "company_id": 4,
        "company_name": "PT F&B",
        "brand": "FNB",
        "store_id": 1,
        "order_ref": "Order 00001-001-0001",
        "order_date": datetime(2026, 10, 5, 3, 0),
        "customer_id": 12,
        "product_id": 6,
        "product_name": "Nasi Goreng",
        "category": "Makanan",
        "qty": Decimal("2"),
        "price_unit": Decimal("50000"),
        "discount_pct": Decimal("10"),
        "subtotal": Decimal("90000"),
        "tax_amount": Decimal("9900"),
        "cost_total": Decimal("40000"),
        "write_date": datetime(2026, 10, 5, 3, 1),
        "order_id": 99,
        "is_refund": False,
        "is_reward_line": False,
        "default_code": "",
        "is_discount_product": False,
        "is_tip_product": False,
    }
    row.update(overrides)
    return row


def test_transform_sales_line_fase1_columns() -> None:
    out = dict(zip(SALES_FACT_COLUMNS, transform_sales_line(_pos_line()), strict=True))
    assert out["order_id"] == 99
    assert out["is_refund"] == 0
    assert out["line_kind"] == "product"
    # 90.000 setelah diskon 10 % -> dasar 100.000 -> diskon 10.000 (basis tanpa pajak)
    assert out["discount_amount"] == Decimal("10000.00")


def test_refund_line_flagged() -> None:
    row = _pos_line(is_refund=True, qty=Decimal("-1"), subtotal=Decimal("-45000"), discount_pct=Decimal("0"))
    out = dict(zip(SALES_FACT_COLUMNS, transform_sales_line(row), strict=True))
    assert out["is_refund"] == 1
    assert out["discount_amount"] == Decimal("0")


@pytest.mark.parametrize(
    ("overrides", "kind"),
    [
        ({"default_code": "EFN-POINTS", "is_reward_line": True}, "points"),
        ({"is_reward_line": True}, "reward"),
        ({"is_discount_product": True}, "discount"),
        ({"is_tip_product": True}, "tip"),
        ({"default_code": "SKU-1"}, "product"),
    ],
)
def test_line_kind_priority(overrides: dict[str, object], kind: str) -> None:
    assert line_kind(_pos_line(**overrides)) == kind


def test_points_codes_configurable() -> None:
    row = _pos_line(default_code="POIN-X")
    assert line_kind(row) == "product"
    assert line_kind(row, frozenset({"POIN-X"})) == "points"
    job = enabled_jobs(True, frozenset({"POIN-X"}))[2]
    out = dict(zip(SALES_FACT_COLUMNS, job.transform(row), strict=True))
    assert out["line_kind"] == "points"


def test_line_discount_amount_edges() -> None:
    assert line_discount_amount(Decimal("0"), Decimal("100"), Decimal("2"), Decimal("5000")) == Decimal("10000.00")
    assert line_discount_amount(Decimal("50"), Decimal("-5"), Decimal("1"), Decimal("50")) == Decimal("0")
    assert line_discount_amount(Decimal("75"), Decimal("25"), Decimal("1"), Decimal("100")) == Decimal("25.00")


def test_sales_sql_reads_refund_and_kind_sources() -> None:
    for column in ("is_refund", "refunded_orderline_id", "is_reward_line", "discount_product_id", "default_code"):
        assert column in SALES_FACT.sql
    for column in ("o.email", "o.mobile", "efn_otp_member_masked", "general_customer_note"):
        assert column not in SALES_FACT.sql


@pytest.mark.parametrize("job", POS_JOBS)
def test_pos_job_sql_contract(job) -> None:
    sql = job.sql
    assert "SELECT *" not in sql.upper()
    for param in ("%(cursor_ts)s", "%(cursor_key)s", "%(limit)s"):
        assert param in sql
    assert "_cursor_ts" in sql and "_cursor_key" in sql
    assert re.search(r"ORDER BY\s+.+,\s+\S+", sql)
    assert job.source == "odoo"
    assert job.identity_field is None
    assert "{" not in sql  # template sudah di-format


def test_payment_sql_never_reads_card_or_reference_data() -> None:
    for column in (
        "card_no",
        "cardholder_name",
        "card_brand",
        "card_type",
        "transaction_id",
        "payment_ref_no",
        "authcode",
        "issuer_bank",
        "ticket",
        "partner_id",
        "email",
        "mobile",
    ):
        assert column not in POS_PAYMENTS.sql
    assert "o.state IN ('paid', 'done')" in POS_PAYMENTS.sql


def test_transform_pos_payment() -> None:
    row = {
        "payment_id": 3,
        "order_id": 9,
        "company_id": 4,
        "company_name": "PT F&B",
        "store_id": 1,
        "payment_date": TS,
        "amount": Decimal("-5000"),
        "payment_method_id": 2,
        "method_name": "Cash",
        "method_type": "none",
        "is_cash": True,
        "is_change": True,
        "is_refund": False,
        "write_date": TS,
    }
    out = dict(zip(POS_PAYMENT_COLUMNS, transform_pos_payment(row), strict=True))
    assert out["amount"] == Decimal("-5000")
    assert out["is_cash"] == 1 and out["is_change"] == 1 and out["is_refund"] == 0
    assert out["payment_date"].tzinfo is UTC


def test_transform_outlet_nullable_warehouse() -> None:
    row = {"store_id": 1, "name": "Outlet Kuta", "company_id": 4, "warehouse_id": None, "active": True}
    row["write_date"] = TS
    out = dict(zip(OUTLET_COLUMNS, transform_outlet(row), strict=True))
    assert out["warehouse_id"] is None and out["active"] == 1


def test_transform_product() -> None:
    row = {
        "product_id": 9,
        "template_id": 8,
        "default_code": None,
        "name": "Susu UHT",
        "category": "All / Susu",
        "company_id": None,
        "list_price": Decimal("21000"),
        "product_type": "consu",
        "is_storable": True,
        "tracking": None,
        "use_expiration_date": True,
        "sale_ok": True,
        "available_in_pos": False,
        "active": True,
        "version_ts": TS,
    }
    out = dict(zip(PRODUCT_COLUMNS, transform_product(row), strict=True))
    assert out["default_code"] == "" and out["company_id"] is None
    assert out["tracking"] == "none" and out["use_expiration_date"] == 1
    assert out["list_price"] == Decimal("21000")


def test_product_sql_versions_on_variant_and_template() -> None:
    assert PRODUCTS.sql.count("GREATEST(pp.write_date, pt.write_date)") >= 4


def test_points_payment_flag_only_in_payment_mode() -> None:
    # SEC-114: default mode payment membaca penanda E-30; mode produk (historis) tidak menyentuhnya.
    from efn_etl.pos import pos_jobs, pos_payments_job

    product = pos_payments_job(points_payment=False)
    assert "efn_is_loyalty_points" not in product.sql
    assert "FALSE AS is_loyalty_points" in product.sql
    assert "efn_is_loyalty_points" in POS_PAYMENTS.sql
    payment = pos_payments_job(points_payment=True)
    assert "COALESCE(pm.efn_is_loyalty_points, FALSE) AS is_loyalty_points" in payment.sql
    assert "{" not in payment.sql and "%(cursor_ts)s" in payment.sql
    assert [j.name for j in pos_jobs(True)] == [j.name for j in POS_JOBS]
    row = {
        "payment_id": 4,
        "order_id": 9,
        "company_id": 4,
        "company_name": "PT Retail",
        "store_id": 1,
        "payment_date": TS,
        "amount": Decimal("6000"),
        "payment_method_id": 7,
        "method_name": "Poin EFN",
        "method_type": "none",
        "is_cash": False,
        "is_change": False,
        "is_refund": False,
        "is_loyalty_points": True,
        "write_date": TS,
    }
    out = dict(zip(POS_PAYMENT_COLUMNS, transform_pos_payment(row), strict=True))
    assert out["is_loyalty_points"] == 1
