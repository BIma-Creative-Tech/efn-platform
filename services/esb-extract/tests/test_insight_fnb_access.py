"""G52 (docs/04 §28.15.6-§28.15.8, L-243/L-244): view Toko F&B + row policy ESB kelas PT/BR (sig3).

Statis (tanpa ClickHouse): membaca infra/clickhouse/{init/21-g52-insight-fnb.sql, access/insight-access.sql,
access/insight-esb-policy.sh}. Cermin Python predikat policy + negatif E1-E9 di bawah; uji perilaku sesungguhnya di
ClickHouse 24.8 = tests/clickhouse/g52_row_policy_harness.sh (dijalankan lead/devsecops di container).
"""

import hashlib
import re

import pytest
from test_insight_access import (
    ACCESS,
    DDL21,
    ESB_POLICY_SH,
    INIT_SH,
    ROOT,
    dwh_view_body,
    esb_policy_manifest,
    insight_views,
    output_columns,
    strip_comments,
)

HARNESS = ROOT / "services" / "esb-extract" / "tests" / "clickhouse" / "g52_row_policy_harness.sh"
PT_VIEWS = {
    "v_esb_sales_daily",
    "v_esb_sales_branch_daily",
    "v_esb_payment_daily",
    "v_esb_flow_daily",
    "v_esb_menu_mix",
    "v_esb_data_quality",
    "v_esb_product_list",
    "v_esb_movement_daily",
    "v_esb_coverage",
    "v_esb_forecast_daily",
    "v_esb_sales_done_day",  # Insight YoY 24 Nov 2026 (23-insight-yoy.sql)
    "v_esb_menu_econ_daily",
    "v_esb_ing_price_monthly",  # G67 margin bahan (24-margin-bahan.sql)
    "v_esb_menu_cost_monthly",
    "v_esb_ing_group_monthly",
    # G68 §32.7 (25-g68-esb-pl-waste-fillrate.sql)
    "v_esb_pl_monthly",
    "v_esb_item_usage_daily",
    "v_esb_item_usage_product_monthly",
    "v_esb_fill_rate_daily",
    "v_esb_fill_rate_product_monthly",
    "v_esb_oms_recon_daily",  # G69 (memuat Σ pembayaran ESB Core)
}
# G69 ESB OMS (26-g69-esb-oms.sql): view ber-cabang kelas BR (persona Toko F&B, §33.7).
OMS_BR_VIEWS = {
    "v_esb_oms_bill",
    "v_esb_oms_hourly",
    "v_esb_oms_menu_hourly",
    "v_esb_oms_fin_hourly",
    "v_esb_oms_fin_mapping_quality",
    "v_esb_oms_freshness",
}
BR_VIEWS = {
    "v_esb_branch_list",
    "v_esb_store_sales_daily",
    "v_esb_store_menu_daily",
    "v_esb_store_payment_daily",
    "v_esb_store_receipt_daily",
} | OMS_BR_VIEWS
STORE_VIEWS = BR_VIEWS - {"v_esb_branch_list"} - OMS_BR_VIEWS
# Kontrak kolom §28.15.8 (urutan tetap; frontend menulis query fstore_* terhadap daftar ini).
STORE_COLUMNS = {
    "v_esb_store_sales_daily": (
        "sales_date brand company_code branch_id branch_name trx_count qty gross discount net"
    ).split(),
    "v_esb_store_menu_daily": (
        "sales_date brand company_code branch_id menu_code menu_name menu_category qty gross discount net"
    ).split(),
    "v_esb_store_payment_daily": (
        "sales_date brand company_code branch_id branch_name payment_type payment_method payment_count amount"
    ).split(),
    "v_esb_store_receipt_daily": (
        "day brand company_code branch_id branch_name origin_branch_id origin_branch_name origin_kind item_group "
        "category product_code product_name uom qty lines"
    ).split(),
}
# Pasangan sumber (§28.15.8): view toko membaca sumber yang SAMA dengan view PT ini (dialihkan bersama).
SOURCE_PAIR = {
    "v_esb_store_sales_daily": "v_esb_sales_branch_daily",
    "v_esb_store_menu_daily": "v_esb_sales_daily",
    "v_esb_store_payment_daily": "v_esb_payment_daily",
}


def _view_stmt(name: str) -> str:
    head = f"CREATE OR REPLACE VIEW efn_insight.{name} DEFINER"
    return strip_comments(ACCESS).split(head, 1)[1].split(";", 1)[0]


# ---------------------------------------------------------------- view (§28.15.8)
def test_store_views_exact_columns() -> None:
    views = insight_views()
    for name, cols in STORE_COLUMNS.items():
        assert views[name][0] == cols, name


def test_store_views_never_expose_cost_or_mdr() -> None:
    # Q-INS-F3 default: kepala toko tanpa HPP/margin; pembayaran tanpa mdr/net.
    views = insight_views()
    for name in STORE_VIEWS:
        cols = views[name][0]
        assert "cogs" not in cols and "mdr" not in cols, name
        assert "cogs" not in _view_stmt(name) and "mdr" not in _view_stmt(name), name
    assert "net" not in views["v_esb_store_payment_daily"][0]
    assert "trx_count" not in views["v_esb_store_menu_daily"][0]  # tidak aditif di grain menu
    # Kolom biaya memang ada di sumber: filter kolom view toko yang menahannya.
    assert "cogs" in output_columns("v_esb_sales_branch_daily")
    assert "mdr" in output_columns("v_esb_payment_daily")


def test_store_views_share_source_with_pt_pair() -> None:
    views = insight_views()
    for store, pair in SOURCE_PAIR.items():
        assert views[store][1] == views[pair][1], (store, pair)
    assert views["v_esb_store_receipt_daily"][1] == "efn_dwh.v_esb_store_receipt_daily"


def test_store_payment_drops_branch_zero() -> None:
    stmt = _view_stmt("v_esb_store_payment_daily")
    assert stmt.rstrip().endswith("FROM efn_dwh.v_esb_payment_daily_fb\nWHERE branch_id != 0")
    # Hanya view ini yang ber-WHERE (wrapper lain = kolom polos dari satu sumber).
    assert strip_comments(ACCESS).count("\nWHERE ") == 1


def test_store_views_definer_security() -> None:
    for name in STORE_VIEWS:
        header = f"CREATE OR REPLACE VIEW efn_insight.{name} DEFINER = efn_insight_definer SQL SECURITY DEFINER AS"
        assert header in ACCESS, name
    assert "GRANT SELECT ON efn_dwh.v_esb_store_receipt_daily TO efn_insight_definer;" in ACCESS


def test_receipt_dwh_view_idempotent_and_contract_columns() -> None:
    code = strip_comments(DDL21)
    assert re.findall(r"CREATE OR REPLACE VIEW (efn_\w+\.\w+) AS", code) == ["efn_dwh.v_esb_store_receipt_daily"]
    assert not re.search(r"\b(DROP|TRUNCATE|ALTER|INSERT|CREATE TABLE|SQL SECURITY|DEFINER)\b", code)
    assert "SELECT *" not in code
    assert output_columns("v_esb_store_receipt_daily") == STORE_COLUMNS["v_esb_store_receipt_daily"]
    assert "agg_esb" not in code  # tidak bergantung pada 20-esb-aggregates.sql


def test_receipt_uses_destination_pt_and_known_store_only() -> None:
    body = dwh_view_body("v_esb_store_receipt_daily")
    assert "FROM efn_dwh.v_esb_movement_daily\n    WHERE kind = 'delivery'" in body
    # E8 (statis): INNER JOIN cabang tujuan yang dikenal, ber-PT, dan toko; tanpa LEFT JOIN (fail closed).
    join = (
        "INNER JOIN (SELECT brand, branch_id, branch_name, company_code FROM efn_dwh.dim_esb_branch FINAL\n"
        "            WHERE company_code != '' AND is_store = 1) AS b\n"
        "    ON b.brand = m.brand AND b.branch_id = m.dest_branch_id"
    )
    assert join in body
    assert "LEFT JOIN" not in body
    # company_code = PT TUJUAN (dim cabang tujuan), bukan PT asal dari v_esb_movement_daily.
    assert "CAST(b.company_code, 'String') AS company_code" in body
    assert "m.company_code" not in body
    inner = body.split("FROM efn_dwh.v_esb_movement_daily", 1)[0].rsplit("SELECT", 1)[1]
    assert "company_code" not in inner
    assert "m.dest_branch_id AS branch_id" in body


# ---------------------------------------------------------------- row policy (§28.15.7)
SHARED_CLAUSES = (
    "_esb=\"company_code != ''\"",
    r"match(getSetting('SQL_efn_codes'), '^[A-Z]{2,8}(,[A-Z]{2,8})*\$')",
    r"match(getSetting('SQL_efn_cids'), '^([0-9]{1,10}(,[0-9]{1,10})*)?\$')",
    r"match(getSetting('SQL_efn_brs'), "
    r"'^((bacha|cup|imd|pb|wtz):[1-9][0-9]{0,9}(,(bacha|cup|imd|pb|wtz):[1-9][0-9]{0,9})*)?\$')",
    "has(splitByChar(',', getSetting('SQL_efn_codes')), company_code)",
    "getSetting('SQL_efn_sig3') = lower(hex(SHA256(concat('$1', '|', getSetting('SQL_efn_cids'), '|', "
    "getSetting('SQL_efn_codes'), '|', getSetting('SQL_efn_brs')))))",
)
PT_CLAUSE = "_pt=\"getSetting('SQL_efn_brs') = ''\""
BR_CLAUSE = (
    "_br=\"(getSetting('SQL_efn_brs') = '' OR has(splitByChar(',', getSetting('SQL_efn_brs')), "
    "concat(brand, ':', toString(branch_id))))\""
)


def test_policy_manifest_classes() -> None:
    manifest = esb_policy_manifest()
    assert {v for v, c in manifest.items() if c == "PT"} == PT_VIEWS
    assert {v for v, c in manifest.items() if c == "BR"} == BR_VIEWS
    assert len(PT_VIEWS) + len(BR_VIEWS) == 32
    views = insight_views()
    # Kelas PT wajib untuk setiap view yang memuat cogs/mdr (Q-INS-F3).
    for name, (cols, _src) in views.items():
        if "cogs" in cols or "mdr" in cols:
            assert manifest[name] == "PT", name
    # Kelas BR butuh kolom brand + branch_id (predikat cabang) + company_code.
    for name in BR_VIEWS:
        assert {"brand", "branch_id", "company_code"} <= set(views[name][0]), name


def test_policy_predicate_clauses_exact() -> None:
    for clause in SHARED_CLAUSES:
        assert clause in ESB_POLICY_SH, clause
    assert PT_CLAUSE in ESB_POLICY_SH and BR_CLAUSE in ESB_POLICY_SH
    assert 'PT) printf \'%s AND %s\' "$_esb" "$_pt" ;;' in ESB_POLICY_SH
    assert 'BR) printf \'%s AND %s\' "$_esb" "$_br" ;;' in ESB_POLICY_SH
    assert '*) echo "insight-esb-policy: kelas tidak dikenal" >&2; return 1 ;;' in ESB_POLICY_SH
    assert "ESB_POLICY_DEFINE_ONLY" in ESB_POLICY_SH  # harness memuat fungsi saja
    # Fail closed: nama view divalidasi sebelum disisipkan ke DDL; kunci hex >= 64.
    assert "v_esb_*) ;;" in ESB_POLICY_SH
    assert '*[!a-z0-9_]*) echo "insight-esb-policy: nama view tidak sah"' in ESB_POLICY_SH
    assert '[ "${#1}" -ge 64 ]' in ESB_POLICY_SH


def test_policy_sourced_once_by_init() -> None:
    assert INIT_SH.count(". /init/access/insight-esb-policy.sh") == 1
    assert "ESB_POLICY_DEFINE_ONLY" not in INIT_SH  # produksi selalu memasang policy


# ---------------------------------------------------------------- cermin Python predikat + negatif E1-E9
KEY = "ab" * 32
SETTINGS = ("SQL_efn_cids", "SQL_efn_codes", "SQL_efn_brs", "SQL_efn_sig3")


def _shell_regex(setting: str) -> str:
    """Regex format satu setting persis dari insight-esb-policy.sh (bentuk `'^...\\$'`)."""
    start = f"match(getSetting('{setting}'), '^"
    return ESB_POLICY_SH.split(start, 1)[1].split("\\$')", 1)[0]


FMT = {name: _shell_regex(name) for name in ("SQL_efn_codes", "SQL_efn_cids", "SQL_efn_brs")}


def sig3(cids: str, codes: str, brs: str, key: str = KEY) -> str:
    return hashlib.sha256(f"{key}|{cids}|{codes}|{brs}".encode()).hexdigest()


def sig1(cids: str, codes: str, key: str = KEY) -> str:
    return hashlib.sha256(f"{key}|{cids}|{codes}".encode()).hexdigest()


def policy_allows(view: str, settings: dict[str, str], row: dict[str, object], key: str = KEY) -> bool:
    """Cermin predikat insight-esb-policy.sh. Setting tak ada -> KeyError (= getSetting galat ClickHouse).

    RE2 `$` hanya cocok di akhir teks -> fullmatch.
    """
    values = {name: settings[name] for name in SETTINGS}  # semua getSetting dievaluasi
    cls = esb_policy_manifest()[view]
    if row["company_code"] == "":
        return False
    if not all(re.fullmatch(FMT[n], values[n]) for n in FMT):
        return False
    if row["company_code"] not in values["SQL_efn_codes"].split(","):
        return False
    if values["SQL_efn_sig3"] != sig3(values["SQL_efn_cids"], values["SQL_efn_codes"], values["SQL_efn_brs"], key):
        return False
    if cls == "PT":
        return values["SQL_efn_brs"] == ""
    branch = str(row["brand"]) + ":" + str(row["branch_id"])
    return values["SQL_efn_brs"] == "" or branch in values["SQL_efn_brs"].split(",")


def scope(cids: str, codes: str, brs: str) -> dict[str, str]:
    return {"SQL_efn_cids": cids, "SQL_efn_codes": codes, "SQL_efn_brs": brs, "SQL_efn_sig3": sig3(cids, codes, brs)}


def row(brand: str, code: str, branch: int) -> dict[str, object]:
    return {"brand": brand, "company_code": code, "branch_id": branch}


EXEC_E = scope("3,5,11", "BCF,PB", "")  # vektor E §28.15.6 (bagian ESB)
STORE_D = scope("5", "PB", "pb:12,pb:7")  # vektor D
PB7 = scope("5", "PB", "pb:7")
BAD_BRS = [
    "PB:7",
    "pb:07",
    "pb:0",
    "pb:7,",
    ",pb:7",
    "pb:7 ",
    "pb:7\n",
    "pb:7|x",
    "smd:7",
    "pb:12345678901",
    "pb:７",
]


def test_sig3_vector_strings() -> None:
    # String yang ditandatangani = vektor §28.15.6 (hex dicatat lead dari builder Python + Node sebelum merge).
    assert sig3("5", "PB", "pb:12,pb:7") == hashlib.sha256((KEY + "|5|PB|pb:12,pb:7").encode()).hexdigest()
    assert sig3("3,5,11", "BCF,PB", "") == hashlib.sha256((KEY + "|3,5,11|BCF,PB|").encode()).hexdigest()
    assert sig3("5", "PB", "") != sig1("5", "PB")  # pemisahan domain v1 vs sig3


def test_positive_controls() -> None:
    for view in PT_VIEWS | BR_VIEWS:
        assert policy_allows(view, EXEC_E, row("pb", "PB", 7)), view
    for view in BR_VIEWS:
        assert policy_allows(view, STORE_D, row("pb", "PB", 12)), view
        assert policy_allows(view, STORE_D, row("pb", "PB", 7)), view


@pytest.mark.parametrize("view", sorted(BR_VIEWS))
def test_e1_branch_session_sees_only_own_branch(view: str) -> None:
    assert policy_allows(view, PB7, row("pb", "PB", 7))
    assert not policy_allows(view, PB7, row("pb", "PB", 12))
    assert not policy_allows(view, PB7, row("wtz", "PB", 7))  # brand ikut kunci cabang


@pytest.mark.parametrize("view", sorted(PT_VIEWS))
def test_e2_branch_session_blocked_from_pt_views(view: str) -> None:
    assert not policy_allows(view, PB7, row("pb", "PB", 7))
    assert not policy_allows(view, STORE_D, row("pb", "PB", 12))


def test_e3_sig3_from_other_brs() -> None:
    forged = {**PB7, "SQL_efn_brs": "pb:12,pb:7"}
    assert not policy_allows("v_esb_store_sales_daily", forged, row("pb", "PB", 12))
    widened = {**PB7, "SQL_efn_brs": ""}  # buang brs tanpa tanda tangan ulang -> PT penuh ditolak
    assert not policy_allows("v_esb_sales_daily", widened, row("pb", "PB", 12))


@pytest.mark.parametrize("view", ["v_esb_sales_daily", "v_esb_store_sales_daily"])
def test_e4_sig_v1_as_sig3(view: str) -> None:
    forged = {**scope("5", "PB", ""), "SQL_efn_sig3": sig1("5", "PB")}
    assert not policy_allows(view, forged, row("pb", "PB", 7))


@pytest.mark.parametrize("brs", BAD_BRS)
def test_e5_bad_brs_format_rejected_even_with_valid_signature(brs: str) -> None:
    forged = scope("5", "PB", brs)
    for view in ("v_esb_store_sales_daily", "v_esb_branch_list"):
        assert not policy_allows(view, forged, row("pb", "PB", 7)), (brs, view)


def test_e6_missing_brs_or_sig3_setting_errors() -> None:
    missing = {k: v for k, v in PB7.items() if k != "SQL_efn_brs"}
    with pytest.raises(KeyError):
        policy_allows("v_esb_store_sales_daily", missing, row("pb", "PB", 7))
    v1_only = {k: v for k, v in PB7.items() if k != "SQL_efn_sig3"} | {"SQL_efn_sig": sig1("5", "PB")}
    with pytest.raises(KeyError):
        policy_allows("v_esb_store_sales_daily", v1_only, row("pb", "PB", 7))


def test_e7_branch_zero_invisible_to_branch_session() -> None:
    for view in BR_VIEWS:
        assert not policy_allows(view, PB7, row("pb", "PB", 0)), view
    assert not policy_allows("v_esb_payment_daily", PB7, row("pb", "PB", 0))
    assert policy_allows("v_esb_payment_daily", EXEC_E, row("pb", "PB", 0))  # exec tetap melihat (PT penuh)


def test_e9_branch_with_wrong_pt_code() -> None:
    wrong = scope("4", "BCF", "bacha:15")
    for view in BR_VIEWS:
        assert not policy_allows(view, wrong, row("bacha", "BCR", 15)), view
    right = scope("4", "BCR", "bacha:15")  # vektor G
    assert policy_allows("v_esb_store_receipt_daily", right, row("bacha", "BCR", 15))


def test_empty_company_code_never_visible() -> None:
    for view in PT_VIEWS | BR_VIEWS:
        assert not policy_allows(view, EXEC_E, row("pb", "", 7)), view


# ---------------------------------------------------------------- harness ClickHouse (E1-E9 perilaku)
def test_harness_covers_e1_to_e9_with_production_predicate() -> None:
    harness = HARNESS.read_text(encoding="utf-8")
    for n in range(1, 10):
        assert f'expect "(E{n})' in harness, n
    assert "ESB_POLICY_DEFINE_ONLY=1" in harness
    assert 'esb_policy_predicate "$KEY" PT' in harness and 'esb_policy_predicate "$KEY" BR' in harness
    assert "infra/clickhouse/access/insight-esb-policy.sh" in harness
    assert "infra/clickhouse/init/21-g52-insight-fnb.sql" in harness  # badan view penerimaan dari DDL produksi
    assert "--log_queries=0" in harness
