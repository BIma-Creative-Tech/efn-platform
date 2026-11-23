"""V-01 (23 Nov 2026): akses ClickHouse insight-web untuk halaman "Pengunjung" lintas PT. Statis (tanpa ClickHouse).

- view efn_insight.v_vis_* = kolom eksplisit ⊆ keluaran view efn_dwh.v_vis_* (init/23-v01-visitor-insight.sql);
- sumber hanya agregat anonim (tanpa fact_visitor_event / subject_key);
- grant efn_insight_definer = penutupan rekursif objek efn_dwh yang dirujuk;
- row policy (access/insight-visitor-policy.sh) + cermin Python predikat dengan test negatif.
Uji perilaku di ClickHouse 24.8 belum ada (dijalankan lead; lihat laporan efn-data-ai-engineer V-01).
"""

import hashlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CH = ROOT / "infra" / "clickhouse"
INIT = (CH / "init" / "23-v01-visitor-insight.sql").read_text(encoding="utf-8")
ACCESS = (CH / "access" / "insight-visitor-access.sql").read_text(encoding="utf-8")
POLICY_SH = (CH / "access" / "insight-visitor-policy.sh").read_text(encoding="utf-8")
INIT_SH = (CH / "clickhouse-init.sh").read_text(encoding="utf-8")

CONTRACT: dict[str, str] = {
    "v_vis_page_daily": "day company_code channel page visitors views",
    "v_vis_funnel_daily": "day company_code channel subjects viewers carters checkout_starters purchasers "
    "searchers_no_result",
    "v_vis_product_daily": "day company_code channel product_id product_name views viewers cart_adds purchases",
    "v_vis_search_daily": "day company_code channel query searches no_result subjects",
}

INSIGHT_VIEW_RE = re.compile(
    r"CREATE OR REPLACE VIEW efn_insight\.(\w+) DEFINER = efn_insight_definer SQL SECURITY DEFINER AS\n"
    r"SELECT (.+?)\nFROM (efn_dwh\.\w+);",
    re.DOTALL,
)


def strip_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def dwh_view_body(name: str) -> str:
    bodies = re.findall(rf"CREATE OR REPLACE VIEW efn_dwh\.{name} AS(.*?);", strip_comments(INIT), re.DOTALL)
    assert len(bodies) == 1, name
    return bodies[0]


def output_columns(name: str) -> list[str]:
    body = dwh_view_body(name)
    head = body[body.index("SELECT") + len("SELECT") :].split("\nFROM", 1)[0]
    items, depth, cur = [], 0, ""
    for ch in head:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            items.append(cur)
            cur = ""
        else:
            cur += ch
    items.append(cur)
    cols = []
    for item in (i.strip() for i in items if i.strip()):
        found = re.search(r"\bAS (\w+)$", item)
        assert found, (name, item[:60])
        cols.append(found.group(1))
    return cols


def insight_views() -> dict[str, tuple[list[str], str]]:
    return {
        n: ([c.strip() for c in cols.replace("\n", " ").split(",")], src)
        for n, cols, src in INSIGHT_VIEW_RE.findall(ACCESS)
    }


def manifest() -> list[str]:
    block = POLICY_SH.split('VIS_POLICY_VIEWS="', 1)[1].split('"', 1)[0]
    return [line.strip() for line in block.strip().splitlines()]


# ---------------------------------------------------------------- view & kolom
def test_views_match_contract_and_dwh_outputs() -> None:
    views = insight_views()
    assert set(views) == set(CONTRACT)
    assert ACCESS.count("CREATE OR REPLACE VIEW efn_insight.") == len(CONTRACT)
    for name, (cols, source) in views.items():
        assert source == f"efn_dwh.{name}", name
        assert cols == CONTRACT[name].split(), name
        assert set(cols) <= set(output_columns(name)), name
        assert "company_code" in cols, name  # kolom predikat policy


def test_sources_are_anonymous_aggregates_only() -> None:
    body = strip_comments(INIT)
    assert "fact_visitor_event" not in body
    assert "subject_key" not in body
    refs = set(re.findall(r"\b(efn_\w+\.\w+)", body))
    allowed = {
        "efn_dwh.agg_visitor_page_daily",
        "efn_dwh.agg_visitor_funnel_daily",
        "efn_dwh.agg_visitor_product_daily",
        "efn_dwh.agg_visitor_search_daily",
        "efn_dwh.dim_product",
    } | {f"efn_dwh.{v}" for v in CONTRACT}
    assert refs <= allowed, refs - allowed


def test_definer_grants_exact_closure() -> None:
    granted = set(re.findall(r"^GRANT SELECT ON (efn_\w+\.\w+) TO efn_insight_definer;$", ACCESS, re.MULTILINE))
    needed: set[str] = set()
    for name in CONTRACT:
        needed.add(f"efn_dwh.{name}")
        needed |= set(re.findall(r"\b(efn_\w+\.\w+)", dwh_view_body(name)))
    assert granted == needed, (needed - granted, granted - needed)
    text = strip_comments(ACCESS)
    assert not re.search(r"GRANT (ALL|INSERT|ALTER|DROP|TRUNCATE|CREATE|OPTIMIZE)", text)
    assert "TO insight_reader" not in text  # reader hanya efn_insight.* (insight-access.sql)


# ---------------------------------------------------------------- row policy (fragmen shell)
def test_policy_manifest_and_init_wiring() -> None:
    assert manifest() == list(CONTRACT)
    assert "VIS_POLICY_EXPECTED=4" in POLICY_SH
    assert '[ "$VIS_POLICY_N" = "$VIS_POLICY_EXPECTED" ]' in POLICY_SH
    assert "CREATE ROW POLICY OR REPLACE efn_insight_scope ON efn_insight.%s USING %s TO insight_reader;" in POLICY_SH
    assert POLICY_SH.count("$CH --log_queries=0 --multiquery") == 1  # SEC-191
    sm_at = INIT_SH.index("\n. /init/access/insight-sm-policy.sh\n")
    vis_at = INIT_SH.index("\n. /init/access/insight-visitor-policy.sh\n")
    assert sm_at < vis_at < INIT_SH.index("\nunset INSIGHT_KEY\n")
    # Fail closed tetap berlaku: view efn_insight tanpa policy -> grant dicabut + init gagal.
    assert "short_name = 'efn_insight_scope'" in INIT_SH


CLAUSES = (
    "company_code != ''",
    r"match(getSetting('SQL_efn_vcodes'), '^[A-Z]{2,8}(,[A-Z]{2,8})*\$')",
    "has(splitByChar(',', getSetting('SQL_efn_vcodes')), company_code)",
    "getSetting('SQL_efn_vsig') = lower(hex(SHA256(concat('$1', '|visitor|', getSetting('SQL_efn_vcodes')))))",
)


def test_policy_predicate_clauses_exact() -> None:
    for clause in CLAUSES:
        assert clause in POLICY_SH, clause
    for other in ("SQL_efn_sig3", "SQL_efn_sig2", "SQL_efn_sig'"):
        assert other not in POLICY_SH  # domain tanda tangan sendiri


# ---------------------------------------------------------------- cermin Python predikat + negatif
KEY = "ab" * 32


def vsig(codes: str, key: str = KEY) -> str:
    return hashlib.sha256(f"{key}|visitor|{codes}".encode()).hexdigest()


def policy_allows(settings: dict[str, str], company_code: str, key: str = KEY) -> bool:
    """Cermin predikat insight-visitor-policy.sh. Setting tak ada -> KeyError (= getSetting galat ClickHouse)."""
    codes, sig = settings["SQL_efn_vcodes"], settings["SQL_efn_vsig"]
    if company_code == "":
        return False
    if not re.fullmatch(r"[A-Z]{2,8}(,[A-Z]{2,8})*", codes):
        return False
    if company_code not in codes.split(","):
        return False
    return sig == vsig(codes, key)


def scope(codes: str) -> dict[str, str]:
    return {"SQL_efn_vcodes": codes, "SQL_efn_vsig": vsig(codes)}


def test_positive_pb_and_smd() -> None:
    assert policy_allows(scope("PB"), "PB")
    assert policy_allows(scope("PB,SMD"), "SMD")
    assert policy_allows(scope("SMD"), "SMD")


def test_neg_other_pt_not_visible() -> None:
    assert not policy_allows(scope("PB"), "SMD")
    assert not policy_allows(scope("SMD"), "PB")
    assert not policy_allows(scope("PB"), "")


def test_neg_forged_codes_with_old_signature() -> None:
    forged = {"SQL_efn_vcodes": "PB,WTZ", "SQL_efn_vsig": vsig("PB")}
    assert not policy_allows(forged, "WTZ")


def test_neg_sig3_or_wrong_key_not_accepted() -> None:
    sig3 = hashlib.sha256(f"{KEY}|3|PB|".encode()).hexdigest()  # bentuk sig3 (cids|codes|brs)
    assert not policy_allows({"SQL_efn_vcodes": "PB", "SQL_efn_vsig": sig3}, "PB")
    assert not policy_allows({"SQL_efn_vcodes": "PB", "SQL_efn_vsig": vsig("PB", "cd" * 32)}, "PB")


@pytest.mark.parametrize("codes", ["PB|visitor|PB", "pb", "PB,", "PB\n", "ＰＢ", ""])
def test_neg_non_canonical_codes_rejected_even_with_valid_signature(codes: str) -> None:
    assert not policy_allows({"SQL_efn_vcodes": codes, "SQL_efn_vsig": vsig(codes)}, "PB")


def test_neg_missing_setting_errors() -> None:
    with pytest.raises(KeyError):
        policy_allows({"SQL_efn_vcodes": "PB"}, "PB")
