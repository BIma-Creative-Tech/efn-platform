"""Entitas G20 (docs/04 §12.1): kode PT, jenis usaha, grup brand, relasi antar-entitas + keselarasan view SQL."""

import itertools
import re
from pathlib import Path

import pytest

from efn_etl import entities
from efn_etl.entities import (
    BUSINESS_TYPES,
    COMPANY_CODES,
    IC_NON_TP,
    IC_RELATIONS,
    IC_TP_BACHA,
    IC_TP_HOLDING,
    IC_UNKNOWN,
    brand_group,
    business_type,
    company_code,
    ic_relation,
)

ROOT = Path(__file__).resolve().parents[1]
G20_SQL = ROOT.parents[1] / "infra" / "clickhouse" / "init" / "08-g20-entities.sql"


def test_six_entities_and_legacy_codes_removed() -> None:
    assert COMPANY_CODES == {"EFN", "GL", "PB", "BCR", "BCF", "FARM"}
    assert set(BUSINESS_TYPES) == COMPANY_CODES
    assert set(BUSINESS_TYPES.values()) == {"holding", "retail", "fnb", "farm"}
    assert {"RTL", "FNB"}.isdisjoint(COMPANY_CODES)


@pytest.mark.parametrize(
    ("code", "expected"),
    [("GL", "retail"), ("BCR", "retail"), ("PB", "fnb"), ("BCF", "fnb"), ("EFN", "holding"), ("FARM", "farm")],
)
def test_business_type(code: str, expected: str) -> None:
    assert business_type(code) == expected
    assert business_type(code.lower()) == expected


def test_brand_group_bacha_combines_bcr_bcf() -> None:
    assert brand_group("BCR") == brand_group("BCF") == "BACHA"
    assert [brand_group(c) for c in ("EFN", "GL", "PB", "FARM")] == ["EFN", "GL", "PB", "FARM"]
    assert brand_group("") == brand_group(None) == ""


@pytest.mark.parametrize("bad", ["RTL", "FNB", "BACHA", "G L", "efn_gl", "0", None, ""])
def test_company_code_fail_closed(bad: object) -> None:
    with pytest.raises(ValueError):
        company_code(bad)


def test_company_code_normalizes_case_and_whitespace() -> None:
    assert company_code(" bcr ") == "BCR"
    assert company_code("farm") == "FARM"


def test_business_type_and_brand_group_reject_legacy_codes() -> None:
    for fn in (business_type, brand_group):
        for legacy in ("RTL", "FNB"):
            with pytest.raises(ValueError):
                fn(legacy)


def _expected_relation(a: str, b: str) -> str:
    if "EFN" in (a, b):
        return IC_TP_HOLDING
    if {a, b} == {"BCR", "BCF"}:
        return IC_TP_BACHA
    return IC_NON_TP


def test_ic_relation_all_30_directed_pairs() -> None:
    pairs = list(itertools.permutations(sorted(COMPANY_CODES), 2))
    assert len(pairs) == 30
    for a, b in pairs:
        assert ic_relation(a, b) == ic_relation(b, a) == _expected_relation(a, b), (a, b)
    tp = {frozenset(p) for p in pairs if ic_relation(*p) in (IC_TP_HOLDING, IC_TP_BACHA)}
    # TP cost-plus hanya 5 pasangan Holding<->PT + 1 pasangan Bacha (18-3).
    assert len(tp) == 6
    assert frozenset({"BCR", "BCF"}) in tp
    assert frozenset({"GL", "PB"}) not in tp and frozenset({"FARM", "PB"}) not in tp


def test_ic_relation_edge_cases() -> None:
    assert ic_relation("GL", None) == ""  # bukan inter-company
    assert ic_relation(None, None) == ""
    assert ic_relation("GL", "") == IC_UNKNOWN  # lawan company grup tanpa kode PT
    assert ic_relation("RTL", "FNB") == IC_UNKNOWN  # kode lama tidak dikenali
    assert ic_relation("GL", "GL") == IC_UNKNOWN  # kode ganda = data salah
    assert ic_relation("EFN", "EFN") == IC_UNKNOWN
    assert ic_relation("bcr", " bcf ") == IC_TP_BACHA


# ------------------------------------------------------------------ keselarasan dengan view ClickHouse


def _sql() -> str:
    """Isi 08-g20-entities.sql tanpa komentar `--` (komentar boleh menyebut kode lama)."""
    lines = G20_SQL.read_text(encoding="utf-8").splitlines()
    return "\n".join(line.split("--", 1)[0] for line in lines)


def test_view_company_code_lists_match_python() -> None:
    lists = re.findall(r"brand NOT IN \(([^)]*)\)", _sql())
    assert len(lists) == 2
    for raw in lists:
        assert {item.strip().strip("'") for item in raw.split(",")} == COMPANY_CODES


def test_view_ic_relation_labels_and_tp_pairs_match_python() -> None:
    sql = _sql()
    for label in IC_RELATIONS:
        if label:
            assert f"'{label}'" in sql, label
    assert f"c.brand = '{entities.HOLDING_CODE}' OR cp.brand = '{entities.HOLDING_CODE}', 'tp_holding'" in sql
    assert "(c.brand = 'BCR' AND cp.brand = 'BCF') OR (c.brand = 'BCF' AND cp.brand = 'BCR'), 'tp_bacha'" in sql
    assert "OR c.brand = cp.brand, 'unknown'" in sql
    # Urutan cabang multiIf sama dengan ic_relation(): '' -> unknown -> tp_holding -> tp_bacha -> non_tp.
    start = sql.index("multiIf(")
    order = [sql.index(f"'{label}'", start) for label in ("unknown", "tp_holding", "tp_bacha", "non_tp")]
    assert order == sorted(order)


def test_view_elimination_covers_all_intercompany_and_no_pii() -> None:
    sql = _sql()
    # Eliminasi tidak bergantung pada label: semua baris ber-ic_company_id dikeluarkan.
    assert "sumIf(l.balance, isNull(l.ic_company_id)) AS balance_after_elimination" in sql
    assert "CREATE OR REPLACE VIEW efn_dwh.v_gl_ic_recon" in sql
    assert "ADD COLUMN IF NOT EXISTS business_type" in sql and "ADD COLUMN IF NOT EXISTS brand_group" in sql
    assert "partner_id" not in sql
    assert "RTL" not in sql and "'FNB'" not in sql
    # View lama sudah dipindah dari 03 (satu definisi saja).
    old = (G20_SQL.parent / "03-gl-audit.sql").read_text(encoding="utf-8")
    assert "CREATE OR REPLACE VIEW efn_dwh.v_gl_consolidated" not in old


def test_metabase_grant_for_ic_recon() -> None:
    grants = (ROOT.parents[1] / "infra" / "clickhouse" / "access" / "metabase-access.sql").read_text(encoding="utf-8")
    assert "GRANT SELECT ON efn_dwh.v_gl_ic_recon TO metabase_reader;" in grants
