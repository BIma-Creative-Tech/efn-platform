"""G47 (docs/04 §28.13.8c, L-200): default KPI sama di file normatif JSON, registri ETL, literal view ClickHouse, dan
(bila ada) insight-web lib/sm-defaults.ts. Registri Odoo diuji sisi Odoo terhadap JSON yang sama."""

import json
import math
import re
from pathlib import Path

import pytest

from efn_etl.sm_kpi import KIND_ENUM, KPI_BY_KEY, KPI_PAIRS, KPI_REGISTRY, defaults_document, parse_kpi_value

ROOT = Path(__file__).resolve().parents[3]
JSON_PATH = ROOT / "docs" / "contracts" / "insight_sm_kpi_defaults.json"
DDL = (ROOT / "infra" / "clickhouse" / "init" / "17-g47-insight-sm.sql").read_text(encoding="utf-8")
WEB = ROOT / "services" / "insight-web" / "lib" / "sm-defaults.ts"


def view_defaults() -> dict[str, float | str]:
    body = DDL.split("CREATE OR REPLACE VIEW efn_dwh.v_sm_kpi_config AS", 1)[1].split(";", 1)[0]
    out: dict[str, float | str] = {}
    for key, num, text in re.findall(r"\('([a-z_.]+)', (nan|[0-9.]+), '([a-z_]*)'\)", body):
        assert key not in out, key
        out[key] = text if num == "nan" else float(num)
    return out


def test_json_equals_registry() -> None:
    doc = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    assert doc == defaults_document()
    assert len(doc["keys"]) == 39 == len(KPI_REGISTRY) == len(KPI_BY_KEY)


def test_view_literal_equals_registry() -> None:
    assert view_defaults() == {s.key: s.default for s in KPI_REGISTRY}
    order = re.findall(r"\('([a-z_.]+)', (?:nan|[0-9.]+), '[a-z_]*'\)", DDL.split("v_sm_kpi_config AS", 1)[1])
    assert order[:39] == [s.key for s in KPI_REGISTRY]


def test_defaults_valid_and_pairs_consistent() -> None:
    for spec in KPI_REGISTRY:
        if spec.kind == KIND_ENUM:
            raw = str(spec.default)
        elif spec.kind == "bool":
            raw = str(int(float(spec.default)))
        else:
            raw = str(spec.default)
        assert parse_kpi_value(spec.key, raw) is not None, spec.key
    for lower, upper in KPI_PAIRS:
        assert float(KPI_BY_KEY[lower].default) < float(KPI_BY_KEY[upper].default), (lower, upper)


@pytest.mark.parametrize(
    ("key", "raw", "expected"),
    [
        ("gmroi.period", "quarter", (None, "quarter")),
        ("gmroi.period", "week", None),
        ("gmroi.include_dc", "true", (1.0, "")),
        ("gmroi.include_dc", "0", (0.0, "")),
        ("gmroi.include_dc", "2", None),
        ("critical.cover_days", "0,5", (0.5, "")),
        ("critical.cover_days", "0.4", None),
        ("critical.sales_window_days", "14", (14.0, "")),
        ("critical.sales_window_days", "14.5", None),
        ("thr.price.min_samples", "nan", None),
        ("thr.price.min_samples", "inf", None),
        ("fresh.daily.warn_min", "59", None),
        ("no.such.key", "1", None),
        ("oos.basis", None, None),
    ],
)
def test_parse_kpi_value(key: str, raw: str | None, expected: tuple[float | None, str] | None) -> None:
    assert parse_kpi_value(key, raw) == expected


def test_insight_web_defaults_match_when_present() -> None:
    if not WEB.is_file():
        pytest.skip("insight-web lib/sm-defaults.ts belum ada")
    text = WEB.read_text(encoding="utf-8")
    block = text.split("SM_KPI_DEFAULTS", 1)[1].split("};", 1)[0]
    found = dict(re.findall(r"'([a-z_.]+)':\s*('?[a-z_0-9.]+'?)", block))
    expected = {s.key: s.default for s in KPI_REGISTRY}
    assert set(found) == set(expected)
    for key, raw in found.items():
        value: float | str = raw.strip("'") if raw.startswith("'") else float(raw)
        if isinstance(value, float):
            assert math.isclose(value, float(expected[key])), key
        else:
            assert value == expected[key], key
