"""G47 QA: konsistensi LINTAS SERVICE yang tidak tertangkap test per-service (statis, hanya membaca file repo).

Mencakup: (1) persona Odoo = insight-web = policy ClickHouse, (2) tiap view yang diquery halaman persona boleh dibaca
persona itu oleh row policy, (3) enum/rentang KPI insight-web = file normatif JSON, (4) pass-through flag
INSIGHT_SM_ENABLED di compose (Odoo + insight-web), (5) kolom efn_insight.v_sm_* yang diquery insight-web ada di view.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "services" / "insight-web"
POLICY = (ROOT / "infra" / "clickhouse" / "access" / "insight-sm-policy.sh").read_text(encoding="utf-8")
ACCESS = (ROOT / "infra" / "clickhouse" / "access" / "insight-sm-access.sql").read_text(encoding="utf-8")
QUERIES = (WEB / "lib" / "queries-sm.ts").read_text(encoding="utf-8")
SM_DATA = (WEB / "lib" / "sm-data.ts").read_text(encoding="utf-8")
DEFAULTS_TS = (WEB / "lib" / "sm-defaults.ts").read_text(encoding="utf-8")
CLAIMS = (WEB / "lib" / "claims.ts").read_text(encoding="utf-8")
ODOO_USERS = (ROOT / "services/odoo/addons/efn_insight_sm/models/res_users.py").read_text(encoding="utf-8")
ODOO_TOKEN = (ROOT / "services/odoo/addons/efn_insight/lib/insight_token.py").read_text(encoding="utf-8")
COMPOSE = (ROOT / "infra" / "compose" / "docker-compose.yml").read_text(encoding="utf-8")
KPI_JSON = json.loads((ROOT / "docs" / "contracts" / "insight_sm_kpi_defaults.json").read_text(encoding="utf-8"))

PERSONAS = {"category", "dc", "exec", "marketing", "store"}
LOADER_PERSONA = {
    "loadStore": "store",
    "loadExec": "exec",
    "loadCategory": "category",
    "loadDc": "dc",
    "loadMarketing": "marketing",
}


def policy_views() -> dict[str, tuple[str, set[str]]]:
    block = POLICY.split('SM_POLICY_VIEWS="', 1)[1].split('"', 1)[0]
    out: dict[str, tuple[str, set[str]]] = {}
    for line in block.strip().splitlines():
        view, cls, pers = line.split()
        out[view] = (cls, set(pers.split(",")))
    return out


def web_queries() -> dict[str, tuple[str, set[str]]]:
    """nama query -> (sql, view yang dirujuk)."""
    out: dict[str, tuple[str, set[str]]] = {}
    for name, sql in re.findall(r"^  (sm_\w+): q\(`(.*?)`\)", QUERIES, re.S | re.M):
        views = {f"v_sm_{v}" for v in re.findall(r"\$\{V\}(\w+)", sql)}
        views |= set(re.findall(r"efn_insight\.(v_sm_\w+)", sql))
        if "${CATF}" in sql:
            views.add("v_sm_product_list")
        out[name] = (sql, views)
    return out


def test_persona_sets_equal_across_odoo_web_policy() -> None:
    odoo = set(re.findall(r'^\s+"(\w+)": "efn_insight_sm\.group_insight_\w+"', ODOO_USERS, re.M))
    web = set(re.findall(r"'(\w+)'", CLAIMS.split("export const PERSONAS = [", 1)[1].split("]", 1)[0]))
    token = set(re.findall(r'"(\w+)"', ODOO_TOKEN.split("PERSONAS = frozenset({", 1)[1].split("}", 1)[0]))
    policy_case = set(re.findall(r"\b(\w+)(?=\||\))", POLICY.split('case "$_p" in', 1)[1].split("esac", 1)[0]))
    assert odoo == web == token == PERSONAS
    assert PERSONAS <= policy_case
    used = {p for _cls, pers in policy_views().values() for p in pers}
    assert used <= PERSONAS


def test_pt_level_personas_equal_odoo_web() -> None:
    web = set(re.findall(r"'(\w+)'", CLAIMS.split("PT_LEVEL_PERSONAS", 1)[1].split("]", 1)[0].split("[", 1)[1]))
    odoo = set(re.findall(r'"(\w+)"', ODOO_TOKEN.split("ORG_PERSONAS = frozenset({", 1)[1].split("}", 1)[0]))
    assert web == odoo == {"category", "dc", "exec", "marketing"}


def test_policy_views_equal_access_sql_views() -> None:
    declared = set(re.findall(r"CREATE OR REPLACE VIEW efn_insight\.(v_sm_\w+)", ACCESS))
    assert declared == set(policy_views())
    assert len(declared) == 29


def test_every_view_queried_by_a_persona_page_is_readable_by_that_persona() -> None:
    views = policy_views()
    queries = web_queries()
    bodies = re.split(r"export async function (load\w+)\(", SM_DATA)
    seen_pages: set[str] = set()
    for fn, body in zip(bodies[1::2], bodies[2::2], strict=True):
        persona = LOADER_PERSONA.get(fn)
        if persona is None:  # loadSmBase: dipakai semua persona -> view dasar wajib terbuka untuk semuanya
            for q in set(re.findall(r"q\('(sm_\w+)'", body)) | {
                "sm_warehouses",
                "sm_categories",
                "sm_kpi_config",
                "sm_freshness",
            }:
                for v in queries[q][1]:
                    assert PERSONAS <= views[v][1], (q, v)
            continue
        seen_pages.add(fn)
        for q in set(re.findall(r"q\('(sm_\w+)'", body)):
            for v in queries[q][1]:
                assert v in views, (q, v)
                assert persona in views[v][1], f"{persona}: query {q} membaca {v} yang tidak diizinkan policy"
    assert seen_pages == set(LOADER_PERSONA)


def test_all_queries_use_only_declared_views() -> None:
    views = set(policy_views())
    for name, (_sql, used) in web_queries().items():
        assert used, name
        assert used <= views, (name, used - views)


def test_web_selected_columns_exist_in_efn_insight_views() -> None:
    """Kolom literal (bukan alias) pada SELECT sederhana query katalog harus ada di daftar kolom view efn_insight."""
    cols: dict[str, set[str]] = {}
    for view, body in re.findall(
        r"CREATE OR REPLACE VIEW efn_insight\.(v_sm_\w+) [^\n]*\nSELECT (.*?)\nFROM", ACCESS, re.S
    ):
        cols[view] = {c.strip() for c in body.split(",")}
    checked = 0
    for name, (sql, used) in web_queries().items():
        if len(used - {"v_sm_product_list"}) != 1 or "GROUP BY" in sql or "(" in sql.split("FROM", 1)[0]:
            continue
        view = next(iter(used - {"v_sm_product_list"}))
        select = sql.split("FROM", 1)[0].replace("SELECT", "")
        for col in (c.strip() for c in select.split(",")):
            assert col in cols[view], f"{name}: kolom {col} tidak ada di efn_insight.{view}"
            checked += 1
    assert checked > 30


def test_web_kpi_enums_and_ranges_equal_contract_json() -> None:
    enums = {
        k: re.findall(r"'([a-z_0-9]+)'", v)
        for k, v in re.findall(r"'([a-z_.]+)': \[((?:'[a-z_0-9]+',? ?)+)\]", DEFAULTS_TS)
    }
    ranges = {k: (float(a), float(b)) for k, a, b in re.findall(r"'([a-z_.]+)': \[([0-9.]+), ([0-9.]+)\]", DEFAULTS_TS)}
    for spec in KPI_JSON["keys"]:
        if spec["type"] == "enum":
            assert enums[spec["key"]] == spec["choices"], spec["key"]
            assert spec["key"] not in ranges
        else:
            assert ranges[spec["key"]] == (float(spec["min"]), float(spec["max"])), spec["key"]
            assert spec["key"] not in enums
    assert len(enums) + len(ranges) == len(KPI_JSON["keys"]) == 39


@pytest.mark.parametrize("service_marker", ["EFN_INSIGHT_SSO_KEY_FILE", "INSIGHT_SESSION_KEY_FILE"])
def test_insight_sm_enabled_passed_through_compose(service_marker: str) -> None:
    """Flag harus diteruskan ke Odoo DAN insight-web (saklar tunggal §28.13.12); default false."""
    idx = COMPOSE.index(service_marker)
    window = COMPOSE[max(0, idx - 2500) : idx + 200]
    assert "INSIGHT_SM_ENABLED: ${INSIGHT_SM_ENABLED:-false}" in window
