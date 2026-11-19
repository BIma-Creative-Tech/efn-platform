"""Transform & whitelist entitas migrasi G43 (fixture sintetis, bentuk respons dari spec publik ESB)."""

from datetime import UTC, date, datetime
from decimal import Decimal

from efn_esb import transform_mig as t
from efn_esb.normalize import AP_FIELDS, AR_FIELDS, COA_FIELDS, redact, redact_customer, whitelist

V = datetime(2026, 11, 18, 3, 0, 0, 123000, tzinfo=UTC)
DAY = date(2026, 11, 17)


def company_for(branch_id: int) -> str:
    return "BCR" if branch_id == 6 else "BCF"


def test_bom_header_and_lines() -> None:
    detail = redact(
        {
            "bomID": 2429, "bomCode": "BM-1", "bomName": "ROTI", "bomTypeID": 1, "bomTypeName": "Production",
            "productCode": "FIN001", "productName": "FIN ROTI", "uomName": "BATCH", "flagActive": 1,
            "createdBy": "user.a", "editedBy": "user.b", "notes": "catatan",
            "bomDetails": [
                {"ID": 5, "productCode": "RM1", "productName": "Tepung", "qty": "2.5", "uomName": "KG", "uomQty": 1,
                 "convertionQty": 1000, "lastHpp": 12000.12345, "yieldPercent": 95, "tolerancePercent": 2},
                {"ID": 0, "productCode": "RM2", "qty": 1, "uomName": "GR"},
                {"ID": 7, "productCode": "", "qty": 9},
                "bukan-dict",
            ],
        }
    )  # fmt: skip
    header, lines = t.transform_bom(detail, "pb", "PB", V)
    assert header is not None and header[2] == 2429 and header[6] == "Production" and header[-2] == 1
    assert len(header) == len(t.DIM_BOM_COLUMNS) and "createdBy" not in detail and "notes" not in detail
    assert [ln[3] for ln in lines] == [5, 1_000_000_002]
    assert lines[0][6] == Decimal("2.500000") and lines[0][10] == Decimal("12000.1235")
    assert all(len(ln) == len(t.DIM_BOM_LINE_COLUMNS) for ln in lines)
    assert t.transform_bom({"bomID": 0}, "pb", "PB", V) == (None, [])


def test_menu_prices_dedup_last_wins() -> None:
    rows = [
        {"menuID": 1, "menuCode": "M1", "menuName": "Kopi", "price": 18000, "bomName": "BOM KOPI", "flagActive": 1},
        {"menuID": 1, "menuCode": "M1", "menuName": "Kopi", "price": 19000, "bomName": "BOM KOPI", "flagActive": 0},
        {"menuCode": "", "price": 1},
    ]
    out = t.transform_menu_prices(rows, "pb", "PB", V)
    assert out == [["pb", "PB", "M1", 1, "Kopi", Decimal("19000.00"), "BOM KOPI", 0, V]]


def test_customer_whitelist_drops_contact_and_npwp() -> None:
    raw = {
        "customerID": 3,
        "customerCode": "C1",
        "customerName": "PT Mitra",
        "customerCategoryName": "B2B",
        "paymentDueDays": 45,
        "flagActive": 1,
        "lockVat": 1,
        "address": "Jl. Contoh",
        "picName": "Budi",
        "picPhone": "080000000000",
        "email": "x@example.invalid",
        "npwp": "01.234",
        "customerPic": [{"a": 1}],
    }
    clean = redact_customer(raw)
    assert set(clean) == {"customerID", "customerCode", "customerName", "customerCategoryName", "paymentDueDays",
                          "flagActive", "lockVat"}  # fmt: skip
    rows = t.transform_customers([clean, {"customerCode": "C2", "paymentDueDays": 99999}], "pb", "PB", V)
    assert rows[0] == ["pb", "PB", "C1", "PT Mitra", "B2B", 45, 1, 1, V]
    assert rows[1][5] == 3650 and rows[1][6] == 0
    assert len(rows[0]) == len(t.DIM_CUSTOMER_COLUMNS)


def test_locations_sorted_and_scoped_to_branch() -> None:
    rows = [{"locationID": 9, "locationName": "Dapur"}, {"locationID": 2, "locationName": "Gudang"}, {"locationID": 0}]
    out = t.transform_locations(rows, "bacha", "BCR", 6, V)
    assert [r[3] for r in out] == [2, 9] and out[0][:3] == ["bacha", "BCR", 6]


def test_coa_whitelist_keeps_account_name() -> None:
    raw = {
        "coaNo": "1 1 05 01",
        "description": "Persediaan",
        "coaLevel": 4,
        "currency": "IDR",
        "flagActive": 1,
        "createdBy": "x",
        "mapCoa": [{"key": "AP"}],
        "releasePaymentCode": "BANK_X",
    }
    clean = whitelist(raw, COA_FIELDS)
    assert set(clean) == set(COA_FIELDS)
    assert t.transform_coa([clean], "pb", "PB", V) == [["pb", "PB", "1 1 05 01", "Persediaan", 4, "IDR", 1, V]]


def test_stock_opname_aggregates_per_doc_product_uom() -> None:
    doc = redact(
        {"stockOpnameNum": "SP1", "branchID": 6, "locationID": 3, "stockOpnameDate": "2026-11-16", "statusID": 3,
         "createdBy": "x", "authorizedBy": "y", "imageAttachments": [{"imageUrl": "u"}],
         "printData": {"createdByFullName": "Nama Orang", "companyName": "PT"},
         "stockOpnameDetails": [
             {"productCode": "RM1", "uomName": "KG", "qty": 2, "stockQty": 3, "hpp": 100},
             {"productCode": "RM1", "uomName": "KG", "qty": "1.5", "stockQty": 0, "hpp": 120},
             {"productCode": "", "qty": 1},
         ]}
    )  # fmt: skip
    assert "createdByFullName" not in doc["printData"] and "imageAttachments" not in doc
    out = t.transform_stock_opname([doc], "bacha", company_for, DAY, V)
    assert out == [
        ["bacha", "BCR", DAY, 6, 3, date(2026, 11, 16), "SP1", 3, "RM1", "KG", Decimal("3.500"), Decimal("3.000"),
         Decimal("120.0000"), V]
    ]  # fmt: skip


def test_ap_ar_whitelist_codes_only() -> None:
    ap_raw = {"purchaseInvoiceNum": "PI1", "supplierCode": "S1", "supplierName": "PT S", "supplierInvoiceNum": "INV9",
              "invoiceDate": "2026-10-01", "dueDate": "", "currencyName": "IDR", "payableTotal": "1000.5",
              "paymentTotal": 0, "additionalInfo": "catatan"}  # fmt: skip
    ap = whitelist(ap_raw, AP_FIELDS)
    assert "supplierName" not in ap and "additionalInfo" not in ap
    rows = t.transform_ap_open([ap], "pb", "PB", 1, DAY, V)
    assert rows[0][4:10] == ["PI1", "S1", "INV9", date(2026, 10, 1), None, "IDR"]
    assert rows[0][10] == Decimal("1000.50")
    ar_raw = {
        "productSalesInvoiceNum": "SI1",
        "customerCode": "C1",
        "customerName": "Orang",
        "address": "Jl",
        "receivableTotal": 500,
        "paymentTotal": 100,
        "advanceTotal": 0,
        "invoiceDate": "2026-10-02",
    }
    ar = whitelist(ar_raw, AR_FIELDS)
    assert "customerName" not in ar and "address" not in ar
    rows = t.transform_ar_open([ar, {"productSalesInvoiceNum": ""}], "pb", "PB", 1, DAY, V)
    assert len(rows) == 1 and rows[0][5] == "C1" and rows[0][9] == Decimal("500.00")
    assert all(len(r) == len(t.FACT_AR_COLUMNS) for r in rows)
