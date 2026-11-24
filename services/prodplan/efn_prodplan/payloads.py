"""Payload ESB + marker outbox (docs/04 §30.9). Logika murni.

Body yang dibangun di sini disimpan PERSIS di `outbox.request_body` (tanpa kredensial) dan dikirim apa adanya.
"""

import re
import secrets
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from efn_prodplan.calc import q4

MARKER_RE = re.compile(r"^pp:[0-9a-f]{12}$")


def new_marker() -> str:
    return "pp:" + secrets.token_hex(6)


class InvalidQuantity(ValueError):
    """Qty dokumen ESB ≤ 0 / bukan angka hingga (SEC-231): ditolak SEBELUM body disimpan/dikirim."""


def num(value: Decimal | int | str) -> int | float:
    """Decimal -> angka JSON (4 desimal ROUND_HALF_UP; bilangan bulat dikirim sebagai int). Hanya qty > 0."""
    try:
        dec = Decimal(str(value))
    except ArithmeticError:
        raise InvalidQuantity("qty bukan angka") from None
    if not dec.is_finite():
        raise InvalidQuantity("qty bukan angka hingga")
    rounded = q4(dec)
    if rounded <= 0:
        raise InvalidQuantity("qty harus > 0")
    if rounded == rounded.to_integral_value():
        return int(rounded)
    return float(rounded)


def simple_transfer_body(
    origin_location_id: int,
    dest_location_id: int,
    doc_date: date,
    marker: str,
    lines: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "originLocationID": int(origin_location_id),
        "destinationLocationID": int(dest_location_id),
        "simpleTransferDate": doc_date.isoformat(),
        "additionalInfo": marker,
        "simpleTransferDetails": [
            {"productDetailID": int(ln["product_detail_id"]), "qty": num(ln["qty"])} for ln in lines
        ],
    }


def goods_transfer_request_body(
    origin_branch_id: int,
    dest_branch_id: int,
    origin_location_id: int,
    doc_date: date,
    marker: str,
    lines: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Tanpa `transferDetailAssets` (array kosong ditolak ESB) dan tanpa `purchaseRequestNum` (§30.9)."""
    return {
        "originBranchID": int(origin_branch_id),
        "destinationBranchID": int(dest_branch_id),
        "originLocationID": int(origin_location_id),
        "categoryTypeID": 1,
        "transferDate": doc_date.isoformat(),
        "additionalInfo": marker,
        "transferDetails": [
            {"productDetailID": int(ln["product_detail_id"]), "qty": num(ln["qty"]), "requestQty": num(ln["qty"])}
            for ln in lines
        ],
    }


def manufacturing_body(
    mode: str,
    branch_id: int,
    location_id: int,
    doc_date: date,
    marker: str,
    line: Mapping[str, Any],
) -> dict[str, Any]:
    """Satu dokumen per baris produksi (L-257). `line` = baris `plan_production_line` (materials snapshot)."""
    base: dict[str, Any] = {
        "branchID": int(branch_id),
        "originLocationID": int(location_id),
        "destinationLocationID": int(location_id),
        "simpleManufacturingDate": doc_date.isoformat(),
    }
    if mode == "actual":
        base["simpleManufacturingDetails"] = [
            {
                "bomID": int(line["bom_id"]),
                "productDetailID": int(line["product_detail_id"]),
                "manufacturingQty": num(line["qty"]),
                "resultQty": num(line["qty"]),
                "notes": marker,
                "simpleManufacturingMaterials": [
                    {"productDetailID": int(m["product_detail_id"]), "totalQty": num(m["total_qty"])}
                    for m in line["materials"]
                ],
            }
        ]
    elif mode == "standard":
        base["simpleManufacturingDetails"] = [
            {
                "bomID": int(line["bom_id"]),
                "productDetailID": int(line["product_detail_id"]),
                "qty": num(line["qty"]),
                "additionalInfo": marker,
            }
        ]
    else:
        raise ValueError("mode produksi tidak dikenal")
    return base


def doc_marker(doc: Mapping[str, Any] | None) -> str | None:
    """Marker dari dokumen ESB (`additionalInfo` header, atau detail produksi `additionalInfo`/`notes`)."""
    if not isinstance(doc, Mapping):
        return None
    candidates: list[Any] = [doc.get("additionalInfo")]
    for key in ("simpleManufacturingDetails", "details", "simpleManufacturingDetail"):
        rows = doc.get(key)
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, Mapping):
                    candidates.extend([row.get("additionalInfo"), row.get("notes")])
    for value in candidates:
        if isinstance(value, str):
            found = re.search(r"pp:[0-9a-f]{12}", value)
            if found:
                return found.group(0)
    return None


def doc_status_id(doc: Mapping[str, Any] | None) -> int | None:
    if not isinstance(doc, Mapping):
        return None
    value = doc.get("statusID")
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def list_doc_numbers(kind: str, rows: Sequence[Mapping[str, Any]]) -> list[str]:
    field = {
        "simple_transfer": "simpleTransferNum",
        "goods_transfer_request": "transferNum",
        "simple_manufacturing": "simpleManufacturingNum",
    }[kind]
    out: list[str] = []
    for row in rows:
        value = row.get(field)
        if isinstance(value, str) and value.strip() and value.strip() not in out:
            out.append(value.strip())
    return out
