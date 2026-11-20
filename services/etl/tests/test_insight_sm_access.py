"""G47 (docs/04 §28.13.6-§28.13.9): akses ClickHouse insight-web untuk view supermarket. Statis (tanpa ClickHouse).

- view efn_insight.v_sm_* = tabel §28.13.9 (nama + kolom persis), kolom ⊆ keluaran view efn_dwh;
- grant efn_insight_definer = penutupan rekursif objek efn_dwh yang dirujuk (tanpa ESB/legacy, L-196);
- row policy (access/insight-sm-policy.sh): kelas W/C/F + allowlist persona per view + predikat persis kontrak;
- 8 test negatif §28.13.7 dijalankan pada cermin Python predikat (`policy_allows`) yang klausanya diuji sama dengan
  fragmen shell; uji perilaku di ClickHouse 24.8 = tests/clickhouse/g47_row_policy_harness.sh (dijalankan lead).
"""

import hashlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CH = ROOT / "infra" / "clickhouse"
INIT = "\n".join(p.read_text(encoding="utf-8") for p in sorted((CH / "init").glob("*.sql")))
ACCESS = (CH / "access" / "insight-sm-access.sql").read_text(encoding="utf-8")
ESB_ACCESS = (CH / "access" / "insight-access.sql").read_text(encoding="utf-8")
POLICY_SH = (CH / "access" / "insight-sm-policy.sh").read_text(encoding="utf-8")
PG22 = (ROOT / "infra" / "postgres" / "init" / "22-platform-g47.sql").read_text(encoding="utf-8")

ALL = "category,dc,exec,marketing,store"
# §28.13.9: view -> (kelas, persona, kolom).
CONTRACT: dict[str, tuple[str, str, str]] = {
    "v_sm_warehouse_list": (
        "W",
        ALL,
        "company_id company_code warehouse_id warehouse_code warehouse_name outlet_type active",
    ),
    "v_sm_product_list": (
        "C",
        ALL,
        "company_id product_id product_tmpl_id default_code product_name categ_id category categ_parent_path active",
    ),
    "v_sm_sales_daily": (
        "W",
        "category,dc,exec,store",
        "day company_id warehouse_id outlet_type categ_id category "
        "gross_sales discounts points_redeemed_idr refunds net_sales tax net_sales_incl_tax qty_sold "
        "lines",
    ),
    "v_sm_outlet_daily": (
        "W",
        "category,dc,exec,store",
        "day company_id warehouse_id outlet_type orders net_sales net_sales_incl_tax refunds avg_basket_incl_tax",
    ),
    "v_sm_margin_daily": (
        "W",
        "category,exec",
        "day company_id warehouse_id outlet_type categ_id category net_sales "
        "cogs gross_margin gross_margin_pct net_sales_with_cost",
    ),
    "v_sm_gmroi_monthly": (
        "W",
        "category,exec",
        "month company_id warehouse_id outlet_type categ_id category "
        "gross_margin avg_stock_value_cost_daily avg_stock_value_cost_month_end stock_days "
        "days_in_month",
    ),
    "v_sm_waste_daily": (
        "W",
        "category,dc,exec,store",
        "day company_id warehouse_id outlet_type categ_id category "
        "reason_id reason_name scraps waste_qty waste_value_cost",
    ),
    "v_sm_forecast_accuracy_weekly": (
        "W",
        "category,dc,exec,store",
        "week_start company_id warehouse_id outlet_type "
        "product_id categ_id days days_with_sales forecast_qty actual_qty abs_error",
    ),
    "v_sm_stock_daily": (
        "W",
        "category,dc,exec,store",
        "day company_id warehouse_id outlet_type categ_id sku_universe sku_oos sku_below_min on_hand_qty",
    ),
    "v_sm_oos_product_daily": (
        "W",
        "category,dc,exec,store",
        "day company_id warehouse_id outlet_type product_id "
        "product_tmpl_id categ_id in_universe oos below_min on_hand_qty",
    ),
    "v_sm_stock_value_daily": (
        "W",
        "category,dc,exec",
        "day company_id warehouse_id outlet_type categ_id "
        "stock_value_cost value_age_0_30 value_age_31_60 value_age_61_90 value_age_91p "
        "value_exp_expired value_exp_0_7 value_exp_8_30 value_exp_31_90 value_exp_91p "
        "value_no_lot",
    ),
    "v_sm_stock_critical": (
        "W",
        "category,dc,exec,store",
        "snapshot_ts company_id warehouse_id outlet_type product_id "
        "product_tmpl_id default_code product_name categ_id on_hand_qty free_qty min_qty max_qty "
        "below_min avg_daily_sales days_of_cover is_out is_critical open_proposal_id",
    ),
    "v_sm_replenishment": (
        "W",
        "category,dc,exec,store",
        "proposal_id company_id warehouse_id outlet_type "
        "supply_warehouse_id delivery_date cutoff_at state approved_at picking_id line_count "
        "lines_excluded lines_changed qty_proposed_total qty_approved_total",
    ),
    "v_sm_replenishment_line": (
        "W",
        "category,dc,exec,store",
        "line_id proposal_id company_id warehouse_id "
        "outlet_type delivery_date state product_id categ_id qty_proposed qty_approved "
        "excluded reason_codes change_reason",
    ),
    "v_sm_replenishment_bias_weekly": (
        "W",
        "category,dc,exec",
        "week_start company_id warehouse_id outlet_type kind entries qty_system qty_final bias_qty",
    ),
    "v_sm_dc_fill_rate": (
        "W",
        "category,dc,exec,store",
        "line_id proposal_id company_id warehouse_id outlet_type "
        "dc_warehouse_id delivery_date product_id categ_id qty_approved qty_shipped qty_received "
        "qty_basis on_time picking_shared",
    ),
    "v_sm_dc_purchase": (
        "W",
        "category,dc,exec",
        "line_id order_id company_id warehouse_id outlet_type supplier_id "
        "supplier_name product_id po_state date_order date_planned product_qty qty_received "
        "price_subtotal from_orderpoint is_late",
    ),
    "v_sm_lot_expiry": (
        "W",
        "category,dc,exec,store",
        "alert_id company_id warehouse_id outlet_type lot_id lot_name "
        "product_id product_tmpl_id product_name categ_id stage state detected_date last_run_date "
        "days_left expiration_date qty value_cost value_retail markdown_id markdown_state "
        "markdown_effective_pct markdown_price markdown_date_to display_status",
    ),
    "v_sm_expiry_daily": (
        "W",
        "category,dc,exec,store",
        "day company_id warehouse_id outlet_type categ_id stage lots qty value_cost",
    ),
    "v_sm_markdown": (
        "W",
        "category,exec,store",
        "markdown_id company_id warehouse_id outlet_type lot_id product_id "
        "alert_id state price discount_pct effective_discount_pct date_from date_to qty_cap qty_sold "
        "notify_members",
    ),
    "v_sm_price_index": (
        "W",
        "category,exec,marketing",
        "company_id warehouse_id outlet_type product_id "
        "product_tmpl_id default_code product_name categ_id our_price market_median_30d "
        "market_min_30d market_max_30d sample_n_30d competitors_n_30d last_sample_date diff_pct",
    ),
    "v_sm_price_coverage": (
        "W",
        "category,exec",
        "company_id warehouse_id outlet_type categ_id sku_active sku_with_sample sku_with_sample_3",
    ),
    "v_sm_price_signal": (
        "W",
        "category,exec,marketing",
        "company_id warehouse_id outlet_type product_id categ_id "
        "viewers_30d views_30d cart_adds_30d purchases_30d our_price market_median_30d diff_pct",
    ),
    "v_sm_visitor_funnel_daily": (
        "W",
        "category,exec,marketing",
        "day company_id warehouse_id outlet_type channel "
        "channel_code subjects viewers carters checkout_starters purchasers "
        "searchers_no_result",
    ),
    "v_sm_search_terms": (
        "W",
        "category,marketing",
        "day company_id warehouse_id outlet_type channel channel_code query searches no_result subjects",
    ),
    "v_sm_segment_daily": (
        "W",
        "exec,marketing",
        "day company_id warehouse_id outlet_type segment_code member_count run_id campaign_id campaign_state",
    ),
    "v_sm_campaign_result": (
        "W",
        "exec,marketing",
        "campaign_id company_id warehouse_id outlet_type name kind origin "
        "state voucher_mode channels date_from date_to approved_at sent_at approval_rev budget_idr "
        "segment_code segment_member_count delivered skipped push_sent email_queued wa_queued "
        "stop_reason vouchers_issued vouchers_used orders_with_voucher discount_idr "
        "revenue_with_voucher_idr computed_at",
    ),
    "v_sm_freshness": ("F", ALL, "mart freshness_class data_as_of source_as_of last_run_at last_status"),
    "v_sm_kpi_config": ("C", ALL, "company_id key value_num value_str is_default effective_since"),
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
    assert bodies, name
    return bodies[-1]


def top_level_items(body: str) -> list[str]:
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
    return [i.strip() for i in items if i.strip()]


def output_columns(name: str) -> list[str]:
    cols = []
    for item in top_level_items(dwh_view_body(name)):
        found = re.search(r"\bAS (\w+)$", item)
        assert found, (name, item[:60])  # setiap item teratas wajib ber-AS
        cols.append(found.group(1))
    return cols


def insight_views() -> dict[str, tuple[list[str], str]]:
    return {
        n: ([c.strip() for c in cols.replace("\n", " ").split(",")], src)
        for n, cols, src in INSIGHT_VIEW_RE.findall(ACCESS)
    }


def closure(name: str, seen: set[str]) -> None:
    for ref in re.findall(r"\b(efn_\w+\.\w+)", dwh_view_body(name)):
        if ref not in seen:
            seen.add(ref)
            db, obj = ref.split(".")
            if db == "efn_dwh" and obj.startswith("v_"):
                closure(obj, seen)


def manifest() -> dict[str, tuple[str, str]]:
    block = POLICY_SH.split('SM_POLICY_VIEWS="', 1)[1].split('"', 1)[0]
    out = {}
    for line in block.strip().splitlines():
        view, cls, pers = line.split()
        out[view] = (cls, pers)
    return out


# ---------------------------------------------------------------- view & kolom
def test_insight_sm_views_match_contract_exactly() -> None:
    views = insight_views()
    assert set(views) == set(CONTRACT)
    assert ACCESS.count("CREATE OR REPLACE VIEW efn_insight.") == len(CONTRACT) == 29
    for name, (cols, source) in views.items():
        assert source == f"efn_dwh.{name}", name
        assert cols == CONTRACT[name][2].split(), (name, set(cols) ^ set(CONTRACT[name][2].split()))
        assert "*" not in cols


@pytest.mark.parametrize("name", sorted(CONTRACT))
def test_insight_columns_exist_in_dwh_view(name: str) -> None:
    cols = insight_views()[name][0]
    src = output_columns(name)
    assert set(cols) <= set(src), (name, set(cols) - set(src))
    assert len(src) == len(set(src)), name
    cls = CONTRACT[name][0]
    if cls in ("W", "C"):
        assert "company_id" in cols, name
    if cls == "W":
        assert "warehouse_id" in cols and "outlet_type" in cols, name


def test_helper_views_have_named_columns() -> None:
    for helper in (
        "v_sm_kpi_settings",
        "v_sm_category",
        "v_sm_unit_cost",
        "v_sm_sales_product_daily",
        "v_sm_replenishment_current",
    ):
        assert output_columns(helper)


def test_pt_level_views_use_warehouse_zero() -> None:
    for name in (
        "v_sm_price_index",
        "v_sm_price_coverage",
        "v_sm_price_signal",
        "v_sm_search_terms",
        "v_sm_segment_daily",
        "v_sm_campaign_result",
    ):
        body = dwh_view_body(name)
        assert "toUInt32(0) AS warehouse_id" in body and "'none' AS outlet_type" in body, name
    funnel = dwh_view_body("v_sm_visitor_funnel_daily")
    assert re.search(r"toUInt32\(0\)\)\s+AS warehouse_id", funnel) and "m.company_id = co.company_id" in funnel  # L-190


def test_margin_excludes_tax_and_sales_include_tax() -> None:
    sales = dwh_view_body("v_sm_sales_product_daily")
    assert re.search(r"sum\(subtotal \+ tax_amount\)\s+AS net_sales_incl_tax", sales)
    assert re.search(r"sum\(subtotal\)\s+AS net_sales\b", sales)
    margin = dwh_view_body("v_sm_margin_daily")
    assert "m.ns - m.cg AS gross_margin" in margin and "tax" not in margin  # L-202


def test_kpi_driven_definitions() -> None:
    oos = dwh_view_body("v_sm_oos_product_daily")
    for needle in ("k.oos_universe", "k.oos_basis", "k.oos_threshold_qty", "k.oos_include_dc"):
        assert needle in oos
    crit = dwh_view_body("v_sm_stock_critical")
    for needle in (
        "k.critical_rule",
        "k.critical_cover_days",
        "kk.critical_sales_window_days",
        "'min_only'",
        "'cover_only'",
    ):
        assert needle in crit
    fill = dwh_view_body("v_sm_dc_fill_rate")
    assert "k.fill_basis = 'received'" in fill and "k.fill_grace_days" in fill
    kpi = dwh_view_body("v_sm_kpi_config")
    assert "c.active != 1" in kpi and "c.value_str = d.str" in kpi and "c.value_num = d.num" in kpi  # C-1


# ---------------------------------------------------------------- grant definer
def test_definer_grants_exact_closure_without_legacy() -> None:
    granted = set(re.findall(r"^GRANT SELECT ON (efn_\w+\.\w+) TO efn_insight_definer;$", ACCESS, re.MULTILINE))
    needed: set[str] = set()
    for name in CONTRACT:
        needed.add(f"efn_dwh.{name}")
        closure(name, needed)
    assert granted == needed, (needed - granted, granted - needed)
    for ref in needed:
        assert not re.search(r"esb|legacy|gl_|audit|loyalty", ref), ref  # L-196, tanpa GL/audit/loyalty
    assert not re.search(r"GRANT (ALL|INSERT|ALTER|DROP|TRUNCATE|CREATE|OPTIMIZE)", strip_comments(ACCESS))
    assert "TO insight_reader" not in strip_comments(ACCESS)  # reader tetap hanya efn_insight.* (insight-access.sql)


def test_insight_reader_never_reads_efn_dwh() -> None:  # test negatif (7) §28.13.7
    assert "REVOKE ALL ON efn_dwh.* FROM insight_reader;" in ESB_ACCESS
    for path in (CH / "access").glob("*.sql"):
        text = strip_comments(path.read_text(encoding="utf-8"))
        assert not re.search(r"GRANT [^;]*ON efn_dwh\.[^;]*TO insight_reader", text), path.name


# ---------------------------------------------------------------- row policy (fragmen shell)
def test_policy_manifest_matches_contract() -> None:
    m = manifest()
    assert set(m) == set(CONTRACT)
    for view, (cls, pers, _cols) in CONTRACT.items():
        assert m[view] == (cls, pers), view
        assert pers.split(",") == sorted(pers.split(","))
    # store tidak pernah melihat margin/GMROI/nilai stok total/PO (L-193/L-203); melihat waste & near-expiry tokonya.
    for view in ("v_sm_margin_daily", "v_sm_gmroi_monthly", "v_sm_stock_value_daily", "v_sm_dc_purchase"):
        assert "store" not in m[view][1], view
    for view in ("v_sm_waste_daily", "v_sm_lot_expiry", "v_sm_expiry_daily"):
        assert "store" in m[view][1], view


SHARED_CLAUSES = (
    r"match(getSetting('SQL_efn_codes'), '^[A-Z]{2,8}(,[A-Z]{2,8})*\$')",
    r"match(getSetting('SQL_efn_cids'), '^[0-9]{1,10}(,[0-9]{1,10})*\$')",
    r"match(getSetting('SQL_efn_wids'), '^[0-9]{1,10}(,[0-9]{1,10})*\$')",
    r"match(getSetting('SQL_efn_personas'), "
    r"'^(category|dc|exec|marketing|store)(,(category|dc|exec|marketing|store))*\$')",
    r"match(getSetting('SQL_efn_cats'), '^([0-9]{1,10}(,[0-9]{1,10})*)?\$')",
    "getSetting('SQL_efn_sig2') = lower(hex(SHA256(concat('$1', '|', getSetting('SQL_efn_cids'), '|', "
    "getSetting('SQL_efn_codes'), '|', getSetting('SQL_efn_wids'), '|', getSetting('SQL_efn_personas'), '|', "
    "getSetting('SQL_efn_cats')))))",
    "hasAny(splitByChar(',', getSetting('SQL_efn_personas')), ${_pers})",
)
CID_CLAUSE = (
    "company_id != 0 AND has(arrayMap(x -> toUInt32OrZero(x), splitByChar(',', getSetting('SQL_efn_cids'))), "
    "company_id)"
)
WID_CLAUSE = "has(arrayMap(x -> toUInt32OrZero(x), splitByChar(',', getSetting('SQL_efn_wids'))), warehouse_id)"


def test_policy_predicate_clauses_exact() -> None:
    for clause in SHARED_CLAUSES:
        assert clause in POLICY_SH, clause
    assert f'_cid="{CID_CLAUSE}"' in POLICY_SH and f'_wid="{WID_CLAUSE}"' in POLICY_SH
    assert 'W) printf \'%s AND %s AND %s\' "$_shared" "$_cid" "$_wid" ;;' in POLICY_SH
    assert 'C) printf \'%s AND %s\' "$_shared" "$_cid" ;;' in POLICY_SH
    assert "F) printf '%s' \"$_shared\" ;;" in POLICY_SH
    assert "CREATE ROW POLICY OR REPLACE efn_insight_scope ON efn_insight.%s USING %s TO insight_reader;" in POLICY_SH
    assert "$CH --log_queries=0 --multiquery" in POLICY_SH  # SEC-191
    assert '[ "$SM_POLICY_N" = "29" ]' in POLICY_SH
    assert "SM_POLICY_DEFINE_ONLY" in POLICY_SH  # harness memuat fungsi saja


# ---------------------------------------------------------------- cermin Python predikat + 8 test negatif
KEY = "ab" * 32
FMT = {
    "SQL_efn_codes": r"[A-Z]{2,8}(,[A-Z]{2,8})*",
    "SQL_efn_cids": r"[0-9]{1,10}(,[0-9]{1,10})*",
    "SQL_efn_wids": r"[0-9]{1,10}(,[0-9]{1,10})*",
    "SQL_efn_personas": r"(category|dc|exec|marketing|store)(,(category|dc|exec|marketing|store))*",
    "SQL_efn_cats": r"([0-9]{1,10}(,[0-9]{1,10})*)?",
}
SETTINGS = ("SQL_efn_cids", "SQL_efn_codes", "SQL_efn_wids", "SQL_efn_personas", "SQL_efn_cats", "SQL_efn_sig2")


def sig2(cids: str, codes: str, wids: str, personas: str, cats: str, key: str = KEY) -> str:
    raw = f"{key}|{cids}|{codes}|{wids}|{personas}|{cats}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def sig1(cids: str, codes: str, key: str = KEY) -> str:
    return hashlib.sha256(f"{key}|{cids}|{codes}".encode()).hexdigest()


def _u32_or_zero(text: str) -> int:
    return int(text) if text.isdigit() and int(text) < 2**32 else 0


def policy_allows(view: str, settings: dict[str, str], row: dict[str, int], key: str = KEY) -> bool:
    """Cermin predikat insight-sm-policy.sh. Setting tak ada -> KeyError (= getSetting galat ClickHouse).
    RE2 `$` hanya cocok di akhir teks -> fullmatch."""
    values = {name: settings[name] for name in SETTINGS}  # semua getSetting dievaluasi
    cls, pers = manifest()[view]
    if not all(re.fullmatch(FMT[n], values[n]) for n in FMT):
        return False
    expected = sig2(
        values["SQL_efn_cids"],
        values["SQL_efn_codes"],
        values["SQL_efn_wids"],
        values["SQL_efn_personas"],
        values["SQL_efn_cats"],
        key,
    )
    if values["SQL_efn_sig2"] != expected:
        return False
    if not set(values["SQL_efn_personas"].split(",")) & set(pers.split(",")):
        return False
    if cls in ("W", "C"):
        cids = [_u32_or_zero(x) for x in values["SQL_efn_cids"].split(",")]
        if row["company_id"] == 0 or row["company_id"] not in cids:
            return False
    if cls == "W":
        wids = [_u32_or_zero(x) for x in values["SQL_efn_wids"].split(",")]
        if row["warehouse_id"] not in wids:
            return False
    return True


def scope(cids: str, codes: str, wids: str, personas: str, cats: str = "") -> dict[str, str]:
    return {
        "SQL_efn_cids": cids,
        "SQL_efn_codes": codes,
        "SQL_efn_wids": wids,
        "SQL_efn_personas": personas,
        "SQL_efn_cats": cats,
        "SQL_efn_sig2": sig2(cids, codes, wids, personas, cats),
    }


STORE = scope("11", "SMD", "22", "store")
EXEC = scope("11", "SMD", "0,20,21,22,23,24,25", "exec")


def test_positive_controls() -> None:
    assert policy_allows("v_sm_sales_daily", STORE, {"company_id": 11, "warehouse_id": 22})
    assert policy_allows("v_sm_price_index", EXEC, {"company_id": 11, "warehouse_id": 0})
    assert policy_allows("v_sm_freshness", STORE, {})
    assert policy_allows("v_sm_kpi_config", STORE, {"company_id": 11, "warehouse_id": 999})  # kelas C


def test_neg1_store_sees_neither_other_outlet_nor_pt_rows() -> None:
    assert not policy_allows("v_sm_sales_daily", STORE, {"company_id": 11, "warehouse_id": 21})
    assert not policy_allows("v_sm_sales_daily", STORE, {"company_id": 11, "warehouse_id": 0})


@pytest.mark.parametrize("view", ["v_sm_margin_daily", "v_sm_gmroi_monthly", "v_sm_dc_purchase"])
def test_neg2_store_persona_blocked_from_cost_views(view: str) -> None:
    assert not policy_allows(view, STORE, {"company_id": 11, "warehouse_id": 22})


def test_neg3_sig2_from_other_wids() -> None:
    forged = {**STORE, "SQL_efn_wids": "21,22", "SQL_efn_sig2": STORE["SQL_efn_sig2"]}
    assert not policy_allows("v_sm_sales_daily", forged, {"company_id": 11, "warehouse_id": 21})


def test_neg4_sig_v1_as_sig2() -> None:
    forged = {**STORE, "SQL_efn_sig2": sig1("11", "SMD")}
    assert not policy_allows("v_sm_sales_daily", forged, {"company_id": 11, "warehouse_id": 22})


@pytest.mark.parametrize("wids", ["22\n", "22a", "22|21", "２２", "22,", ""])
def test_neg5_non_digit_wids_rejected_even_with_valid_signature(wids: str) -> None:
    forged = {**STORE, "SQL_efn_wids": wids, "SQL_efn_sig2": sig2("11", "SMD", wids, "store", "")}
    assert not policy_allows("v_sm_sales_daily", forged, {"company_id": 11, "warehouse_id": 22})


def test_neg6_missing_personas_setting_errors() -> None:
    missing = {k: v for k, v in STORE.items() if k != "SQL_efn_personas"}
    with pytest.raises(KeyError):
        policy_allows("v_sm_sales_daily", missing, {"company_id": 11, "warehouse_id": 22})


def test_neg8_company_zero_never_visible() -> None:
    zero = scope("0,11", "SMD", "0,22", "exec")
    assert not policy_allows("v_sm_sales_daily", zero, {"company_id": 0, "warehouse_id": 0})
    assert not policy_allows("v_sm_kpi_config", zero, {"company_id": 0, "warehouse_id": 0})


def test_scope_vectors_are_computable() -> None:
    """Vektor §28.13.6 (nilai dicatat lead sebelum merge; Python & Node wajib identik)."""
    a = sig2("3,5,11", "SMD", "0,20,21,22,23,24,25", "exec,store", "")
    c = sig2("11", "SMD", "0,20,21,22,23,24,25", "category", "4,9")
    assert re.fullmatch(r"[0-9a-f]{64}", a) and re.fullmatch(r"[0-9a-f]{64}", c) and a != c
    assert (KEY + "|3,5,11|SMD|0,20,21,22,23,24,25|exec,store|").count("|") == 5


# ---------------------------------------------------------------- PG platform view (§28.13.8b)
def test_platform_campaign_stats_view_is_aggregate_only() -> None:
    body = strip_comments(PG22)
    assert "CREATE OR REPLACE VIEW platform.v_campaign_stats AS" in body
    assert "GRANT SELECT ON platform.v_campaign_stats TO efn_etl;" in body
    view = body.split("CREATE OR REPLACE VIEW platform.v_campaign_stats AS", 1)[1].split(";", 1)[0]
    for forbidden in ("member_id", "payload", "code_enc", "code_hash", "SELECT *"):
        assert forbidden not in view, forbidden
    assert "SET LOCAL ROLE efn_platform_owner;" in body and "GRANT SELECT ON platform.campaign" not in body
