"""xmlid §27.4: normalisasi + hash8 deterministik."""

import hashlib

import pytest

from efn_mig.xmlid import xmlid, xmlid_key, xmlid_name


def sha8(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()[:8]


def test_canonical_code_without_hash() -> None:
    assert xmlid_key("FIN0123") == "fin0123"
    assert xmlid_key("RM_001") == "rm_001"
    assert xmlid_key(42) == "42"


def test_non_canonical_code_gets_hash8() -> None:
    assert xmlid_key("FIN-0123") == f"fin_0123_{sha8('FIN-0123')}"  # meralat contoh fit-gap §3
    assert xmlid_key("Kopi Susu") == f"kopi_susu_{sha8('Kopi Susu')}"
    assert xmlid_key("ROTI É") == f"roti___{sha8('ROTI É')}"


def test_case_and_punctuation_variants_do_not_collide() -> None:
    keys = {xmlid_key(c) for c in ("ABC1", "abc1", "ABC-1", "ABC_1", "Abc1")}
    assert len(keys) == 5


def test_truncation_48_plus_hash_is_deterministic() -> None:
    long = "A" * 60
    key = xmlid_key(long)
    assert key == "a" * 48 + "_" + sha8(long)
    assert xmlid_key(long) == key
    assert xmlid_key("A" * 48) == "a" * 48


def test_full_xmlid_format() -> None:
    assert xmlid("pb", "PB", "product", "FIN0123") == "efn_mig_esb.pb_pb_product_fin0123"
    assert xmlid_name("bacha", "BCR", "menu", "M01") == "bacha_bcr_menu_m01"


@pytest.mark.parametrize(
    ("brand", "company", "entity", "code"),
    [("PB", "PB", "product", "X"), ("pb", "pb", "product", "X"), ("pb", "PB", "order", "X"), ("pb", "PB", "menu", "")],
)
def test_invalid_inputs_rejected(brand: str, company: str, entity: str, code: str) -> None:
    with pytest.raises(ValueError):
        xmlid(brand, company, entity, code)
