"""G67 (docs/04 §31.5-§31.7) statis: DDL `24-g67-esb-pl-waste-fillrate.sql` cocok dengan kolom transform, kunci dedup
& partisi kontrak, view §31.6 (nama & kolom = antarmuka insight-web), rumus fill rate / kelas purpose / filter versi
slice (cek string DDL), grant & row policy (positif + negatif). Perilaku view di ClickHouse 24.8 = harness
tests/clickhouse/g67_views_harness.sh (dijalankan lead)."""

import re
from pathlib import Path

from test_insight_access import (
    ACCESS,
    DDL24,
    ESB_POLICY_SH,
    INIT_SH,
    dwh_view_body,
    esb_policy_manifest,
    insight_views,
    output_columns,
    strip_comments,
)

from efn_esb.transform_g67 import G67_TABLES

ROOT = Path(__file__).resolve().parents[3]
CH = ROOT / "infra" / "clickhouse"
ESB_ACCESS = (CH / "access" / "esb-access.sql").read_text(encoding="utf-8")
CODE = strip_comments(DDL24)

# Kontrak kolom §31.6 (urutan tetap; W-B menulis query terhadap daftar ini).
VIEW_COLUMNS = {
    "v_esb_pl_monthly": (
        "period_month brand company_code scope branch_id branch_name line_type section account_no account_key "
        "account_name amount is_partial"
    ).split(),
    "v_esb_item_usage_daily": (
        "journal_date brand company_code branch_id branch_name usage_class purpose_name docs lines value_out value_in "
        "value_net"
    ).split(),
    "v_esb_item_usage_product_monthly": (
        "month brand company_code branch_id usage_class purpose_name product_code product_name uom qty_out value_net"
    ).split(),
    "v_esb_fill_rate_line": (
        "transfer_date brand company_code requester_branch_id supplier_branch_id transfer_num product_code "
        "product_name uom request_qty qty_sent qty_received uom_mismatch first_delivery_date is_mature is_counted"
    ).split(),
    "v_esb_fill_rate_daily": (
        "transfer_date brand company_code branch_id branch_name supplier_branch_id supplier_name requests "
        "requests_full lines lines_full lines_zero line_ratio_sum lines_uom_mismatch lines_open lead_days_sum "
        "lead_days_n"
    ).split(),
    "v_esb_fill_rate_product_monthly": (
        "month brand company_code branch_id product_code product_name uom lines request_qty sent_capped qty_sent "
        "qty_received"
    ).split(),
    "v_esb_g67_quality": "check_name brand company_code item value last_day".split(),
}
WRAPPED = (
    "v_esb_pl_monthly", "v_esb_item_usage_daily", "v_esb_item_usage_product_monthly", "v_esb_fill_rate_daily",
    "v_esb_fill_rate_product_monthly",
)  # fmt: skip
INTERNAL = ("v_esb_item_journal_line", "v_esb_fill_rate_line", "v_esb_g67_quality")
ORDER_BY = {
    "fact_esb_profit_loss": "(company_code, brand, scope, branch_id, period_month, line_type, section, account_no)",
    "fact_esb_item_journal_line": "(company_code, brand, journal_date, journal_num, detail_id, line_no)",
    "fact_esb_transfer_request_line": "(company_code, brand, transfer_date, transfer_num, product_code, uom)",
    "fact_esb_delivery_ref_line": "(company_code, brand, delivery_date, doc_num, reference_num, product_code, uom)",
    "dim_esb_product_id": "(brand, product_id)",
}
PARTITION = {
    "fact_esb_profit_loss": "toYear(period_month)",
    "fact_esb_item_journal_line": "toYYYYMM(journal_date)",
    "fact_esb_transfer_request_line": "toYYYYMM(transfer_date)",
    "fact_esb_delivery_ref_line": "toYYYYMM(delivery_date)",
}
G67_OBJECTS = (*G67_TABLES, *VIEW_COLUMNS, "v_esb_item_journal_line")


def table_block(table: str) -> str:
    return CODE.split(f"CREATE TABLE IF NOT EXISTS efn_dwh.{table}\n", 1)[1].split(";", 1)[0]


def flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def top_level_items(name: str) -> int:
    head = dwh_view_body(name)
    head = head[head.index("SELECT") + 6 :].split("\nFROM", 1)[0]
    depth, items = 0, 1
    for ch in head:
        depth += ch == "("
        depth -= ch == ")"
        items += ch == "," and depth == 0
    return items


# ---------------------------------------------------------------- tabel
def test_tables_columns_engine_key_partition() -> None:
    assert set(re.findall(r"CREATE TABLE IF NOT EXISTS efn_dwh\.(\w+)", CODE)) == set(G67_TABLES)
    for table, columns in G67_TABLES.items():
        block = table_block(table)
        cols = block.split("ENGINE", 1)[0]
        for column in columns:
            assert re.search(rf"^\s+{column}\s", cols, re.MULTILINE), (table, column)
        assert re.search(r"^\s+_loaded_at\s+DateTime64\(3, 'UTC'\) DEFAULT now64\(3\)", cols, re.MULTILINE), table
        assert re.search(r"^\s+_version\s+DateTime64\(3, 'UTC'\)", cols, re.MULTILINE), table
        assert "ENGINE = ReplacingMergeTree(_version)" in block and "TTL" not in block, table
        assert f"ORDER BY {ORDER_BY[table]}" in block, table
        if table in PARTITION:
            assert f"PARTITION BY {PARTITION[table]}" in block, table
        else:
            assert "PARTITION BY" not in block, table


def test_column_types_match_contract() -> None:
    for table, decls in {
        "fact_esb_profit_loss": ("period_month Date", "amount Decimal(20, 2)", "slice_year UInt16",
                                 "scope LowCardinality(String)", "line_type LowCardinality(String)"),
        "fact_esb_item_journal_line": ("qty Decimal(18, 3)", "hpp Decimal(18, 4)", "value Decimal(18, 2)",
                                       "detail_id UInt64", "line_no UInt32", "status_id Int32"),
        "fact_esb_transfer_request_line": ("request_qty Decimal(18, 3)", "qty Decimal(18, 3)", "bypass_gd UInt8",
                                           "finished UInt8", "requester_branch_id UInt32"),
        "fact_esb_delivery_ref_line": ("ref_date Nullable(Date)", "qty_sent Decimal(18, 3)",
                                       "qty_received Decimal(18, 3)", "trans_type LowCardinality(String)"),
        "dim_esb_product_id": ("product_id UInt64", "source LowCardinality(String)"),
    }.items():  # fmt: skip
        body = flat(table_block(table))
        for decl in decls:
            assert f" {decl}," in body, (table, decl)


def test_ddl_idempotent_additive_only() -> None:
    assert not re.search(r"\b(DROP|TRUNCATE|DELETE|INSERT|RENAME|MODIFY|ALTER)\b", CODE)
    assert "SELECT *" not in CODE
    assert not re.search(r"\b(SQL SECURITY|DEFINER)\b", CODE)  # view efn_dwh = INVOKER
    views = re.findall(r"CREATE OR REPLACE VIEW efn_dwh\.(\w+) AS", CODE)
    assert set(views) == set(VIEW_COLUMNS) | {"v_esb_item_journal_line"}
    # Tidak menyentuh objek G41/G65 (L-262: fact_esb_delivery TIDAK di-ALTER; v_esb_flow_daily tidak disentuh).
    assert "fact_esb_delivery " not in CODE and "fact_esb_delivery\n" not in CODE
    assert "v_esb_flow_daily" not in CODE and "efn_esb.raw_doc" not in CODE


# ---------------------------------------------------------------- view: kontrak kolom
def test_views_contract_columns_and_named_items() -> None:
    for name, cols in VIEW_COLUMNS.items():
        assert output_columns(name) == cols, name
        assert top_level_items(name) == len(cols), name
    journal = output_columns("v_esb_item_journal_line")
    assert {"is_counted", "usage_class", "purpose_name", "value", "qty", "company_code"} <= set(journal)
    assert top_level_items("v_esb_item_journal_line") == len(journal)


def test_views_read_only_finished_slice_versions() -> None:
    pl = dwh_view_body("v_esb_pl_monthly")
    assert (
        "WHERE (brand, if(scope = 'brand', '*', toString(branch_id)), makeDate(slice_year, 1, 1), _version) IN"
    ) in pl
    assert "WHERE entity = 'profit_loss')" in pl and "FROM efn_dwh.fact_esb_profit_loss FINAL" in pl
    journal = dwh_view_body("v_esb_item_journal_line")
    assert "(brand, journal_date, _version) IN" in journal
    assert "WHERE entity = 'item_journal' AND scope_key = '*')" in journal
    line = dwh_view_body("v_esb_fill_rate_line")
    assert "(brand, transfer_date, _version) IN" in line
    assert "WHERE entity = 'transfer_request' AND scope_key = '*')" in line
    assert line.count("(brand, delivery_date, _version) IN") == 2
    assert line.count("WHERE entity = 'delivery_ref' AND scope_key = '*')") == 2
    for name in VIEW_COLUMNS:
        body = dwh_view_body(name)
        for table in re.findall(r"FROM efn_dwh\.(fact_esb_\w+|dim_esb_\w+)", body):
            assert f"FROM efn_dwh.{table} FINAL" in body, (name, table)


def test_pl_view_rules() -> None:
    pl = dwh_view_body("v_esb_pl_monthly")
    assert "toUInt8(p.period_month = toStartOfMonth(toDate(now(), 'Asia/Jakarta'))) AS is_partial" in pl
    assert "if(p.scope = 'brand', '(Total brand)'," in pl
    assert "concat('Cabang #', toString(p.branch_id))" in pl
    # Laba dari summary ESB: view tidak menghitung rumus laba sendiri.
    assert "netProfitLoss" not in pl and "grossProfit" not in pl


def test_item_journal_status_and_purpose_class() -> None:
    body = dwh_view_body("v_esb_item_journal_line")
    assert (
        "toUInt8(lower(trimBoth(CAST(j.status_name, 'String'))) IN ('authorized', 'finished', 'closed')) AS is_counted"
    ) in body
    assert "upperUTF8(trimBoth(CAST(purpose_name, 'String'))) AS pn" in body
    group = flat(body.split("multiIf(", 1)[1].split("AS usage_class", 1)[0]).strip()
    order = [group.index(f"'{c}'") for c in ("waste", "internal_use", "rnd", "other", "unclassified")]
    assert order == sorted(order)
    assert group.startswith("j.pn = 'WASTE', 'waste',")
    for purpose in ("PRODUCTION SUPPLIES", "PACKAGING CONSUMPTION", "SELLING SUPPLIES", "CONSUMABLES SUPPLIES",
                    "CLEANING SUPPLIES", "SAFETY SUPPLIES", "STATIONARY SUPPLIES", "UNIFORM"):  # fmt: skip
        assert f"'{purpose}'" in group.split("'internal_use'", 1)[0], purpose
    assert "j.pn = 'RESEARCH & DEVELOPMENT', 'rnd'" in group and "j.pn = 'OTHER RECEIVABLE', 'other'" in group
    assert group.rstrip().endswith("'unclassified')")


def test_item_usage_formulas() -> None:
    daily = dwh_view_body("v_esb_item_usage_daily")
    for needle in (
        "uniqExact(l.journal_num) AS docs_s",
        "-sumIf(l.value, l.qty < 0) AS out_s",
        "sumIf(l.value, l.qty > 0) AS in_s",
        "-sum(l.value) AS net_s",
        "WHERE l.is_counted = 1\n",
        "GROUP BY journal_date, brand, company_code, branch_id, usage_class, purpose_name\n",
    ):
        assert needle in daily, needle
    product = dwh_view_body("v_esb_item_usage_product_monthly")
    assert "if(m.product_code = '', concat('ID:', toString(l.product_id)), m.product_code) AS p_code" in product
    assert "LEFT JOIN (SELECT brand, product_id, product_code FROM efn_dwh.dim_esb_product_id FINAL) AS m" in product
    assert "-sum(l.qty) AS qty_s" in product and "WHERE l.is_counted = 1\n" in product
    assert "GROUP BY month, brand, company_code, branch_id, usage_class, purpose_name, p_code, uom\n" in product


# ---------------------------------------------------------------- view: rumus fill rate (L-263, §31.6.1)
def test_fill_rate_line_join_and_flags() -> None:
    line = dwh_view_body("v_esb_fill_rate_line")
    # Join: brand + reference_num = transfer_num + product_code + uom; hanya trans_type Transfer Request; semua tanggal.
    joined = flat(line)
    assert (
        "ON s.brand = r.brand AND s.reference_num = r.transfer_num AND s.product_code = r.product_code "
        "AND s.uom_s = r.uom_s"
    ) in joined
    assert joined.rstrip().endswith(
        "ON a.brand = r.brand AND a.reference_num = r.transfer_num AND a.product_code = r.product_code"
    )
    assert line.count("WHERE trans_type = 'Transfer Request'") == 2
    assert "sum(qty_sent) AS sent_s" in line and "sum(qty_received) AS recv_s" in line
    # QA P3: kiriman pertama = GD ber-qty > 0 saja; sebelum tanggal permintaan -> NULL.
    assert "minIf(delivery_date, qty_sent > 0) AS first_d" in line
    assert "toUInt8(countIf(qty_sent > 0) > 0) AS pos_hit" in line
    assert "GROUP BY brand, reference_num, product_code, uom_s\n" in line
    assert "delivery_date >=" not in line and "delivery_date <=" not in line  # tanpa batas tanggal kirim
    # Satuan beda = mismatch (tanpa konversi diam-diam).
    assert "toUInt8(a.hit_any = 1 AND s.hit = 0) AS uom_mismatch" in line
    assert "if(s.pos_hit = 1 AND s.first_d >= r.transfer_date, s.first_d, NULL) AS first_delivery_date" in line
    mature = "r.finished = 1 OR r.transfer_date <= toDate(now(), 'Asia/Jakarta') - 7"  # FILL_RATE_MATURE_DAYS = 7
    assert f"toUInt8({mature}) AS is_mature" in line
    counted = flat(line.split("AS is_mature,", 1)[1].split("AS is_counted", 1)[0])
    assert f"({mature})" in counted
    # GTR_COUNTED_STATUS [B]; QA P2#4: product_code '' tidak dihitung.
    for cond in ("r.status_id IN (3, 6)", "r.bypass_gd = 0", "r.request_qty > 0", "r.product_code != ''"):
        assert cond in counted, cond
    assert "r.request_qty AS request_qty" in line  # diminta = requestQty (Q-ESB31), bukan qty


def test_fill_rate_daily_additive_formulas() -> None:
    daily = dwh_view_body("v_esb_fill_rate_daily")
    counted = "l.is_counted = 1 AND l.uom_mismatch = 0"
    for needle in (
        f"countIf({counted}) AS lines_q",
        f"countIf({counted} AND l.qty_sent >= l.request_qty) AS full_q",
        f"countIf({counted} AND l.qty_sent = 0) AS zero_q",
        "least(toFloat64(l.qty_sent) / toFloat64(l.request_qty), 1.0)",  # kirim lebih tidak menaikkan skor
        "countIf(l.is_counted = 1 AND l.uom_mismatch = 1) AS mismatch_q",
        "countIf(l.is_mature = 0 AND l.request_qty > 0) AS open_q",
        "dateDiff('day', l.transfer_date, assumeNotNull(l.first_delivery_date))",
        f"countIf({counted} AND l.first_delivery_date IS NOT NULL) AS lead_n_q",
        "countIf(q.lines_q > 0) AS requests_s",
        "countIf(q.lines_q > 0 AND q.full_q = q.lines_q) AS requests_full_s",
        "GROUP BY transfer_date, brand, company_code, requester_branch_id, supplier_branch_id, transfer_num\n",
        "GROUP BY transfer_date, brand, company_code, requester_branch_id, supplier_branch_id\n",
        "t.requester_branch_id AS branch_id",
    ):
        assert needle in daily, needle
    ratio = flat(daily.split("sumIf(least(", 1)[1].split("AS ratio_q", 1)[0])
    assert ratio.endswith(f"{counted})") or ratio.endswith(f"{counted}) ")
    # Tidak ada rasio jadi di view (tidak aditif): rasio dihitung di query insight-web dari kolom aditif.
    assert "/ sum(" not in daily and "avg(" not in daily


def test_fill_rate_product_monthly_capped_qty() -> None:
    product = dwh_view_body("v_esb_fill_rate_product_monthly")
    assert "sum(if(l.qty_sent < l.request_qty, l.qty_sent, l.request_qty)) AS capped_s" in product
    assert "WHERE l.is_counted = 1 AND l.uom_mismatch = 0\n" in product
    assert "GROUP BY month, brand, company_code, branch_id, product_code, uom\n" in product  # per produk x satuan
    assert "l.requester_branch_id AS branch_id" in product


def test_quality_checks_present() -> None:
    quality = dwh_view_body("v_esb_g67_quality")
    for check in ("pl_branch_vs_brand", "pl_sign_check", "dup_account", "journal_product_unmapped", "unknown_status",
                  "fill_rate_uom_mismatch", "delivery_ref_unmatched", "gtr_without_delivery_mature",
                  "gtr_bypass_gd", "gtr_product_unmapped"):  # fmt: skip
        assert f"'{check}'" in quality, check
    assert "source_lines > 1" in quality and "startsWith(reference_num, 'TF')" in quality
    assert "status_id NOT IN (3, 6)" in quality and "is_counted = 0" in quality


def test_coverage_extended_with_g67_daily_entities() -> None:
    coverage = dwh_view_body("v_esb_coverage")
    assert coverage.count("'item_journal'") == 2 and coverage.count("'transfer_request'") == 2
    assert coverage.count("'delivery_ref'") == 2 and "profit_loss" not in coverage


# ---------------------------------------------------------------- akses (§31.7)
def _grants(text: str, role: str, privileges: str) -> set[str]:
    return set(re.findall(rf"^GRANT {privileges} ON (efn_\w+\.\w+) TO {role};$", text, re.MULTILINE))


def test_esb_writer_select_insert_on_g67_tables_only() -> None:
    tables = {f"efn_dwh.{t}" for t in G67_TABLES}
    assert tables <= _grants(ESB_ACCESS, "esb_writer", "SELECT, INSERT")
    views = {f"efn_dwh.{v}" for v in (*VIEW_COLUMNS, "v_esb_item_journal_line")}
    assert not views & set(re.findall(r"ON (efn_\w+\.\w+) TO esb_writer", ESB_ACCESS))
    assert "GRANT SELECT, INSERT ON efn_esb.raw_doc TO esb_writer;" in ESB_ACCESS  # retransform membaca raw_doc


def test_insight_wrappers_exact_columns_and_pt_policy() -> None:
    views = insight_views()
    manifest = esb_policy_manifest()
    for name in WRAPPED:
        assert views[name] == (VIEW_COLUMNS[name], f"efn_dwh.{name}"), name
        assert manifest[name] == "PT", name  # sesi ber-cabang -> 0 baris (§28.15.7)
    for name in INTERNAL:
        assert name not in views and name not in manifest, name  # tidak dibungkus efn_insight
    assert "\nESB_POLICY_EXPECTED=22\n" in INIT_SH
    assert sum(1 for c in manifest.values() if c == "PT") == 17


def test_definer_reads_g67_objects_but_quality_view_is_admin_only() -> None:
    definer = _grants(ACCESS, "efn_insight_definer", "SELECT")
    expected = {f"efn_dwh.{n}" for n in (*G67_TABLES, *WRAPPED, "v_esb_item_journal_line", "v_esb_fill_rate_line")}
    assert expected <= definer
    for path in sorted((CH / "access").iterdir()):  # cek kualitas = admin/lead saja (tidak di-grant ke role mana pun)
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"GRANT [^;]*v_esb_g67_quality", text), path.name
        assert "efn_insight.v_esb_g67_quality" not in text and "efn_insight.v_esb_fill_rate_line" not in text


def test_g67_objects_not_granted_to_other_roles() -> None:
    # L-267: tanpa Metabase, text2sql, forecast, mig, segment, ETL, Insight SM.
    for path in sorted((CH / "access").iterdir()):
        if path.name in ("esb-access.sql", "insight-access.sql", "insight-esb-policy.sh"):
            continue
        text = path.read_text(encoding="utf-8")
        for name in G67_OBJECTS:
            assert name not in text, (path.name, name)
    metabase = _grants(ESB_ACCESS, "metabase_reader", "SELECT")
    assert not metabase & {f"efn_dwh.{n}" for n in G67_OBJECTS}
    objects = "|".join(G67_OBJECTS)
    assert not re.search(rf"ON efn_dwh\.({objects}) TO (?!esb_writer;|efn_insight_definer;)", ESB_ACCESS + ACCESS)


def test_insight_reader_cannot_read_g67_base_tables_directly() -> None:
    reader = [line for line in ACCESS.splitlines() if re.search(r"\bTO insight_reader\b", line)]
    assert not any("efn_dwh" in line for line in reader)
    assert "REVOKE ALL ON efn_dwh.* FROM insight_reader;" in ACCESS
    assert "v_esb_pl_monthly PT" in ESB_POLICY_SH and "v_esb_fill_rate_daily PT" in ESB_POLICY_SH
