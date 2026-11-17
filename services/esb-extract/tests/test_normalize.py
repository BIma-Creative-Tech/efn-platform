"""Normalisasi murni: alias, FIN, takeaway, angka, redaksi PII (L-110). Fixture sintetis."""

from decimal import Decimal

import pytest

from efn_esb.normalize import (
    alias_norm,
    flag,
    is_fin,
    is_pii_key,
    is_takeaway,
    learn_aliases,
    parse_day,
    redact,
    redact_supplier,
    resolve_product,
    to_decimal,
    to_int,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  fin  roti   keju ", "FIN ROTI KEJU"),
        ("FIN Yoghurt Cup", "FIN YOGURT CUP"),
        ("fin donat", "FIN DONAT"),
        (None, ""),
    ],
)
def test_alias_norm(raw: object, expected: str) -> None:
    assert alias_norm(raw) == expected


def test_is_fin() -> None:
    assert is_fin("FIN0001", "Apa saja")
    assert is_fin("", "FIN Roti Sobek")
    assert is_fin("", "Roti FINB coklat")
    assert not is_fin("RM001", "Tepung")
    assert not is_fin("", "FINISHING oil")  # token, bukan awalan kata
    assert not is_fin(None, None)


def test_takeaway() -> None:
    assert is_takeaway("Kopi Susu T")
    assert is_takeaway("kopi susu t ")
    assert not is_takeaway("Kopi Susu")
    assert not is_takeaway("TEH TARIK")


def test_resolve_product() -> None:
    aliases = {"FIN ROTI KEJU": "FIN001"}
    assert resolve_product(" fin roti  keju", aliases) == "FIN001"
    assert resolve_product("FIN Yoghurt", aliases) == "NAME:FIN YOGURT"


def test_learn_aliases_conflict_is_deterministic() -> None:
    pairs = [("FIN002", "Fin Roti"), ("FIN001", "FIN  ROTI"), ("", "x"), ("FIN003", "")]
    assert learn_aliases(pairs) == {"FIN ROTI": "FIN001"}


@pytest.mark.parametrize(
    ("value", "places", "expected"),
    [
        (12500, 2, Decimal("12500.00")),
        ("12500.555", 2, Decimal("12500.56")),
        (1.1, 3, Decimal("1.100")),
        (None, 2, Decimal("0.00")),
        ("", 3, Decimal("0.000")),
        (-3, 3, Decimal("-3.000")),
    ],
)
def test_to_decimal(value: object, places: int, expected: Decimal) -> None:
    assert to_decimal(value, places) == expected


@pytest.mark.parametrize("value", ["Rp12.500", "abc", True, "NaN", "Infinity"])
def test_to_decimal_rejects_garbage(value: object) -> None:
    with pytest.raises(ValueError):
        to_decimal(value, 2)


def test_to_int_and_flag_and_day() -> None:
    assert to_int("12") == 12 and to_int("3.0") == 3 and to_int("x", 7) == 7 and to_int(None) == 0
    assert to_int(True, 5) == 5
    assert flag(True) == 1 and flag(False) == 0 and flag(0) == 0 and flag("1") == 1 and flag("no") == 0
    assert flag(None, 1) == 1
    assert str(parse_day("2026-11-16 10:00:00")) == "2026-11-16"
    assert parse_day("16/11/2026") is None


@pytest.mark.parametrize(
    "key",
    [
        "createdBy",
        "createdByName",
        "authorizedBy",
        "email",
        "userEmail",
        "picPhone",
        "cellPhone",
        "contactPerson",
        "address",
        "customerName",
        "memberCode",
        "employeeName",
        "cashierName",
        "npwp",
        "fullName",
        # SEC-181: nama kunci nyata ESB (uji GET 18 Nov 2026) + varian pelaku/teks bebas/lampiran
        "creatorFullName",
        "authorizerFullName",
        "editorFullName",
        "editedBy",
        "creator",
        "editor",
        "authorizer",
        "additionalInfo",
        "notes",
        "imageAttachments",
        "receivedBy",
        "postedBy",
        "checkedByName",
        "createdUserID",
        "driverName",
        "receiverName",
    ],
)
def test_pii_keys(key: str) -> None:
    assert is_pii_key(key)


@pytest.mark.parametrize(
    "key",
    [
        "branchName",
        "productName",
        "menuName",
        "supplierName",
        "bomName",
        "salesNum",
        "qty",
        "uomName",
        "paymentAmount",
        "bomID",
        "productionOrderNum",
        "simpleTransferDetails",
        "destinationBranchID",
        "statusName",
        "convertionQty",
        "editedDate",
        "authorizedDate",
        "referenceNumber",
        "categoryDetailDesc",
        "wasteQty",
    ],  # fmt: skip
)
def test_business_keys_kept(key: str) -> None:
    assert not is_pii_key(key)


def test_redact_recursive() -> None:
    payload = {
        "goodsDeliveryNum": "GD-SINT-1",
        "createdBy": "Budi Sintetis",
        "authorizedByName": "Ani Sintetis",
        "goodsDeliveryDetails": [{"productCode": "FIN1", "productName": "FIN Roti", "createdBy": "x"}],
        "meta": {"email": "a@contoh.invalid", "phone": "0800000000", "branchName": "Toko A"},
    }
    out = redact(payload)
    assert out == {
        "goodsDeliveryNum": "GD-SINT-1",
        "goodsDeliveryDetails": [{"productCode": "FIN1", "productName": "FIN Roti"}],
        "meta": {"branchName": "Toko A"},
    }
    assert payload["createdBy"] == "Budi Sintetis"  # input tidak diubah


def test_redact_supplier_whitelist() -> None:
    row = {
        "supplierID": 1,
        "supplierCode": "SUP-SINT",
        "supplierName": "PT Pemasok Sintetis",
        "flagActive": True,
        "address": "Jl. Contoh 1",
        "cellPhone": "080000000000",
        "contactPerson": "Pak Sintetis",
        "vatSubject": True,
    }
    assert redact_supplier(row) == {
        "supplierID": 1,
        "supplierCode": "SUP-SINT",
        "supplierName": "PT Pemasok Sintetis",
        "flagActive": True,
    }
