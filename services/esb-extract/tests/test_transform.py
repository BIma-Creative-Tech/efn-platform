"""Transform murni (fixture sintetis, bukan data klien): penjualan, line_no L-113, FIN terjual, pembayaran, produksi,
delivery, transfer, stok, dimensi."""

from datetime import UTC, date, datetime
from decimal import Decimal

from efn_esb import transform as t

V = datetime(2026, 11, 17, 3, 15, 0, 123000, tzinfo=UTC)
DAY = date(2026, 11, 16)
CK = frozenset({4, 38})


def company_for(branch_id: int) -> str:
    return "BCR" if branch_id == 12 else "PB"


def sale(num: str, code: str, qty: object, total: object, **kw: object) -> dict[str, object]:
    row: dict[str, object] = {
        "salesNum": num,
        "salesDate": "2026-11-16",
        "salesType": "Sales",
        "menuCode": code,
        "menuName": kw.pop("name", f"Menu {code}"),
        "qty": qty,
        "price": kw.pop("price", 10000),
        "total": total,
        "discountTotal": kw.pop("discount", 0),
        "cogsTotal": kw.pop("cogs", 4000),
        "rowType": kw.pop("rowType", "ALACARTE"),
    }
    row.update(kw)
    return row


def test_sales_lines_line_no_stable_under_reordering() -> None:
    rows = [
        sale("S-1", "M02", 1, 10000),
        sale("S-1", "M01", 2, 20000, name="Kopi Susu T"),
        sale("S-1", "M01", 2, 20000, name="Kopi Susu T"),  # baris kembar identik
        sale("S-2", "M03", 1, 15000, discount=1500),
        sale("S-3", "M04", 1, 5000, salesType="Void"),
        sale("", "M05", 1, 5000),
    ]
    out = t.transform_sales_lines(rows, "pb", "PB", 7, DAY, V)
    for permuted in (list(reversed(rows)), rows[2:] + rows[:2], rows[1::2] + rows[::2]):
        assert t.transform_sales_lines(permuted, "pb", "PB", 7, DAY, V) == out
    keys = [(r[4], r[5], r[6]) for r in out]
    assert keys == [("S-1", 1, "M01"), ("S-1", 2, "M01"), ("S-1", 3, "M02"), ("S-2", 1, "M03")]
    first = dict(zip(t.FACT_SALES_COLUMNS, out[0], strict=True))
    assert first["qty"] == Decimal("2.000") and first["total"] == Decimal("20000.00")
    assert first["is_takeaway"] == 1 and first["company_code"] == "PB" and first["_version"] == V
    assert dict(zip(t.FACT_SALES_COLUMNS, out[3], strict=True))["discount"] == Decimal("1500.00")


def test_fin_sold_aggregates_and_marks_unmapped() -> None:
    rows = [
        {"productName": "FIN Roti Keju", "qtyTotal": 3},
        {"productName": "fin roti  keju", "qtyTotal": 2},
        {"productName": "FIN Yoghurt", "qtyTotal": 1.5},
        {"productName": "Saus", "qtyTotal": 9},
        {"productName": "FIN Roti Keju", "qtyTotal": 4, "salesTypeHead": "Void"},
    ]
    out = t.transform_fin_sold(rows, "pb", "PB", 7, DAY, {"FIN ROTI KEJU": "FIN001"}, V)
    assert [(r[4], r[5]) for r in out] == [("FIN001", Decimal("5.000")), ("NAME:FIN YOGURT", Decimal("1.500"))]
    assert all(len(r) == len(t.FACT_FIN_SOLD_COLUMNS) for r in out)


def test_payments_per_branch_with_company_override() -> None:
    rows = [
        {
            "branchCode": "BC12",
            "branchName": "Butik",
            "payments": [
                {"paymentMethodTypeName": "QRIS", "paymentMethodName": "QRIS BCA", "paymentCount": 2,
                 "paymentAmount": 50000, "mdr": 350, "netAfterMDR": 49650},
                {"paymentMethodTypeName": "QRIS", "paymentMethodName": "QRIS BCA", "paymentCount": 1,
                 "paymentAmount": 10000, "mdr": 70, "netAfterMDR": 9930},
            ],
        },
        {"branchCode": "X99", "payments": [{"paymentMethodTypeName": "Cash", "paymentMethodName": "Tunai",
                                            "paymentCount": 1, "paymentAmount": 1000}]},
        {"branchCode": "", "payments": [{"paymentMethodTypeName": "Cash"}]},
    ]  # fmt: skip
    out = t.transform_payments(rows, "bacha", company_for, {"BC12": 12}, DAY, V)
    assert len(out) == 2
    qris = dict(zip(t.FACT_PAYMENT_COLUMNS, out[0], strict=True))
    assert qris["company_code"] == "BCR" and qris["branch_id"] == 12 and qris["payment_count"] == 3
    assert qris["amount"] == Decimal("60000.00") and qris["net"] == Decimal("59580.00")
    unknown = dict(zip(t.FACT_PAYMENT_COLUMNS, out[1], strict=True))
    assert unknown["branch_id"] == 0 and unknown["company_code"] == "PB"


def test_bom_ratio_prefers_fin_output() -> None:
    detail = {
        "manufacturingQty": 2,
        "simpleManufacturingResults": [
            {"productCode": "WIP1", "productName": "Adonan", "qty": 500, "uomName": "GR"},
            {"productCode": "FIN010", "productName": "FIN Donat", "qty": 48, "uomName": "EA"},
        ],
    }
    ratio = t.compute_bom_ratio(detail, "SM-SINT-1")
    assert ratio == t.BomRatio("FIN010", "FIN Donat", 24.0, "EA", "SM-SINT-1")
    assert (
        t.compute_bom_ratio(
            {"manufacturingQty": 0, "simpleManufacturingResults": detail["simpleManufacturingResults"]}, "x"
        )
        is None
    )  # noqa: E501
    assert t.compute_bom_ratio({"manufacturingQty": 1}, "x") is None


def test_simple_mfg_uses_ratio_or_marks_no_ratio() -> None:
    rows = [
        {"simpleManufacturingNum": "SM1", "branchID": 4, "bomID": 10, "bomCode": "B10", "bomName": "FIN Donat",
         "manufacturingQty": 2, "statusID": 3},
        {"simpleManufacturingNum": "SM1", "branchID": 4, "bomID": 10, "bomCode": "B10", "bomName": "FIN Donat",
         "manufacturingQty": 1, "statusID": 3},
        {"simpleManufacturingNum": "SM2", "branchID": 7, "bomID": 11, "bomCode": "FIN011", "bomName": "FIN Roti",
         "manufacturingQty": 3, "statusID": 3},
        {"simpleManufacturingNum": "SM3", "branchID": 7, "bomID": 10, "bomCode": "B10", "bomName": "FIN Donat",
         "manufacturingQty": 5, "statusID": 1},
    ]  # fmt: skip
    ratios = {10: t.BomRatio("FIN010", "FIN Donat", 24.0, "EA", "SM0")}
    out = [
        dict(zip(t.FACT_PRODUCTION_COLUMNS, r, strict=True))
        for r in t.transform_simple_mfg(rows, ratios, "pb", company_for, CK, DAY, V)
    ]  # noqa: E501
    assert [(r["branch_id"], r["doc_num"], r["product_code"], r["qty_ea"], r["uom"], r["is_ck"]) for r in out] == [
        (4, "SM1", "FIN010", Decimal("72.000"), "EA", 1),
        (7, "SM2", "FIN011", Decimal("3.000"), "NO_RATIO", 0),
    ]
    assert all(r["source"] == "simple_mfg" and r["is_fin"] == 1 and r["prod_date"] == DAY for r in out)


def test_production_result_conversion() -> None:
    docs = [
        {
            "productionResultNum": "PR1",
            "branchID": 38,
            "productionResultDetails": [
                {"productCode": "FIN020", "productName": "FIN Bolu", "qty": 2, "convertionQty": 12, "uomName": "BOX"},
                {"productCode": "RM1", "productName": "Gula", "qty": 1, "uomName": "KG"},
            ],
        }
    ]
    out = [
        dict(zip(t.FACT_PRODUCTION_COLUMNS, r, strict=True))
        for r in t.transform_production_result(docs, "pb", company_for, CK, DAY, V)
    ]  # noqa: E501
    assert [(r["product_code"], r["qty_ea"], r["uom"], r["is_fin"], r["is_ck"]) for r in out] == [
        ("FIN020", Decimal("24.000"), "EA", 1, 1),
        ("RM1", Decimal("1.000"), "KG", 0, 1),
    ]


def test_goods_delivery_receipt_qty_and_ignored_status() -> None:
    docs = [
        {"goodsDeliveryNum": "GD1", "statusID": 3, "originBranchID": 4, "destinationBranchID": 7,
         "goodsDeliveryDetails": [
             {"productCode": "FIN001", "productName": "FIN Roti", "uomName": "EA", "deliveryQty": 10, "receiptQty": 9},
             {"productCode": "FIN002", "productName": "FIN Kue", "uomName": "PCS", "deliveryQty": 5, "receiptQty": 0},
         ]},
        {"goodsDeliveryNum": "GD2", "statusID": 33, "originBranchID": 4, "destinationBranchID": 7,
         "goodsDeliveryDetails": [{"productCode": "FIN001", "deliveryQty": 99}]},
        {"goodsDeliveryNum": "", "statusID": 3, "goodsDeliveryDetails": [{"productCode": "FIN001", "deliveryQty": 1}]},
    ]  # fmt: skip
    out = [
        dict(zip(t.FACT_DELIVERY_COLUMNS, r, strict=True))
        for r in t.transform_goods_delivery(docs, "pb", company_for, DAY, V)
    ]  # noqa: E501
    assert [(r["doc_num"], r["product_code"], r["qty_ea"], r["uom"]) for r in out] == [
        ("GD1", "FIN001", Decimal("9.000"), "EA"),
        ("GD1", "FIN002", Decimal("5.000"), "PCS"),
    ]
    assert out[0]["origin_branch_id"] == 4 and out[0]["dest_branch_id"] == 7 and out[0]["company_code"] == "PB"


def test_simple_transfer_status_and_alias() -> None:
    docs = [
        {"simpleTransferNum": "ST1", "statusID": 6, "originBranchID": 7, "destinationBranchID": 8,
         "simpleTransferDetails": [{"productName": "FIN Roti Keju", "qty": 2, "uomName": "EA"},
                                   {"productName": "FIN Baru", "qty": 1, "uomName": "EA"}]},
        {"simpleTransferNum": "ST2", "statusID": 1, "originBranchID": 7, "destinationBranchID": 8,
         "simpleTransferDetails": [{"productName": "FIN Roti Keju", "qty": 50}]},
    ]  # fmt: skip
    rows = t.transform_simple_transfer(docs, "pb", company_for, {"FIN ROTI KEJU": "FIN001"}, DAY, V)
    out = [dict(zip(t.FACT_TRANSFER_COLUMNS, r, strict=True)) for r in rows]
    assert [(r["product_code"], r["qty_ea"], r["status"]) for r in out] == [
        ("FIN001", Decimal("2.000"), 6),
        ("NAME:FIN BARU", Decimal("1.000"), 6),
    ]


def test_stock_sums_locations() -> None:
    rows = [
        {"productCode": "FIN001", "stockQty": 3, "uomName": "EA", "locationID": 1},
        {"productCode": "FIN001", "stockQty": 2.5, "uomName": "EA", "locationID": 2},
        {"productCode": "", "stockQty": 1},
    ]
    assert t.transform_stock(rows, "pb", "PB", 7, DAY, V) == [["pb", "PB", 7, DAY, "FIN001", "EA", Decimal("5.500"), V]]


def test_branches_dims_and_store_ids() -> None:
    rows = [
        {"branchID": 4, "branchCode": "CK1", "branchName": "Central Kitchen Sintetis", "flagActive": 1},
        {"branchID": 7, "branchCode": "T07", "branchName": "Toko Tujuh", "flagActive": True},
        {"branchID": 8, "branchCode": "T08", "branchName": "Toko Delapan", "flagActive": 0},
        {"branchID": 9, "branchCode": "WH", "branchName": "WHProd Gudang"},
        {"branchID": 10, "branchCode": "T10", "branchName": "Toko Sepuluh"},
        {"branchID": 0, "branchName": "rusak"},
    ]
    dims = t.transform_branches(rows, "pb", company_for, CK, frozenset({10}), V)
    flags = {r[2]: (r[5], r[6], r[7]) for r in dims}
    assert flags == {4: (1, 0, 1), 7: (0, 1, 1), 8: (0, 1, 0), 9: (0, 0, 1), 10: (0, 0, 1)}
    assert t.store_ids(dims, include_inactive=True) == [7, 8]
    assert t.store_ids(dims, include_inactive=False) == [7]


def test_master_dims() -> None:
    products = t.transform_products(
        [{"productCode": "FIN001", "productName": "FIN Roti", "categoryName": "Roti", "flagActive": False},
         {"productCode": "RM1", "productName": "Gula"}, {"productCode": "", "productName": "x"}],
        "pb", "PB", {"FIN001": 24.0}, V,
    )  # fmt: skip
    first = dict(zip(t.DIM_PRODUCT_COLUMNS, products[0], strict=True))
    assert first["is_fin"] == 1 and first["batch_to_ea_ratio"] == 24.0 and first["active"] == 0
    assert dict(zip(t.DIM_PRODUCT_COLUMNS, products[1], strict=True))["batch_to_ea_ratio"] is None
    menus = t.transform_menus(
        [{"menuCode": "M1", "menuName": "Kopi T", "categoryDesc": "Minuman", "categoryDetailDesc": "Kopi"}],
        "pb", "PB", V,
    )  # fmt: skip
    assert menus == [["pb", "PB", "M1", "Kopi T", "Minuman", "Kopi", 1, V]]
    suppliers = t.transform_suppliers([{"supplierCode": "S1", "supplierName": "PT Sintetis", "flagActive": True}],
                                      "pb", "PB", V)  # fmt: skip
    assert suppliers == [["pb", "PB", "S1", "PT Sintetis", 1, V]]
    assert t.alias_rows({"FIN ROTI": "FIN001", "": "X"}, "pb", "PB", V) == [["pb", "PB", "FIN ROTI", "FIN001", V]]


def test_every_row_width_matches_columns() -> None:
    assert set(t.MART_TABLES) == {
        "dim_esb_branch", "dim_esb_product", "dim_esb_menu", "dim_esb_product_alias", "dim_esb_supplier",
        "fact_esb_sales_line", "fact_esb_payment_daily", "fact_esb_fin_sold_daily", "fact_esb_production",
        "fact_esb_delivery", "fact_esb_transfer", "fact_esb_stock_snapshot",
    }  # fmt: skip
    for columns in t.MART_TABLES.values():
        assert columns[0] == "brand" and columns[1] == "company_code" and columns[-1] == "_version"
