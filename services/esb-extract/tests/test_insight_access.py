"""G42 bagian B: akses ClickHouse insight-web (efn_insight) -- view eksplisit, grant minimum, row policy per view.

Statis (tanpa ClickHouse): membaca infra/clickhouse/{init,access}/*.sql dan clickhouse-init.sh. Uji perilaku row
policy (scope PB tidak melihat WTZ, sig salah -> 0 baris, tanpa setting -> error) dijalankan di container ClickHouse
24.8 (laporan efn-data-ai-engineer, bagian G42).
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CH = ROOT / "infra" / "clickhouse"
DDL12 = (CH / "init" / "12-g41-esb.sql").read_text(encoding="utf-8")
DDL13 = (CH / "init" / "13-g42-insight.sql").read_text(encoding="utf-8")
DDL19 = (CH / "init" / "19-insight-review.sql").read_text(encoding="utf-8")
DDL21 = (CH / "init" / "21-g52-insight-fnb.sql").read_text(encoding="utf-8")
DDL22 = (CH / "init" / "22-g53-insight-fnb-forecast.sql").read_text(encoding="utf-8")
DDL23 = (CH / "init" / "23-insight-yoy.sql").read_text(encoding="utf-8")
DDL24 = (CH / "init" / "24-g67-esb-pl-waste-fillrate.sql").read_text(encoding="utf-8")
ACCESS = (CH / "access" / "insight-access.sql").read_text(encoding="utf-8")
INIT_SH = (CH / "clickhouse-init.sh").read_text(encoding="utf-8")
ESB_POLICY_SH = (CH / "access" / "insight-esb-policy.sh").read_text(encoding="utf-8")
# G52 §28.15.7 + G53 + YoY 24 Nov 2026 + G67 §31.7: jumlah policy ESB (17 PT + 5 BR); = ESB_POLICY_EXPECTED di init.
ESB_POLICY_COUNT = 22

# G52: WHERE opsional satu baris (hanya v_esb_store_payment_daily: `WHERE branch_id != 0`).
INSIGHT_VIEW_RE = re.compile(
    r"CREATE OR REPLACE VIEW efn_insight\.(\w+) DEFINER = efn_insight_definer SQL SECURITY DEFINER AS\n"
    r"SELECT (.+?)\nFROM (efn_dwh\.\w+)(?:\nWHERE [^;\n]+)?;",
    re.DOTALL,
)
NEW_DWH_VIEWS = {"v_esb_sales_branch_daily", "v_esb_branch_list", "v_esb_product_list", "v_esb_data_quality_pt"}
EXPECTED_INSIGHT_VIEWS = {
    "v_esb_sales_daily",
    "v_esb_sales_branch_daily",
    "v_esb_payment_daily",
    "v_esb_flow_daily",
    "v_esb_menu_mix",
    "v_esb_data_quality",
    "v_esb_branch_list",
    "v_esb_product_list",
    "v_esb_movement_daily",  # review Insight 21 Nov 2026 (19-insight-review.sql)
    "v_esb_coverage",
    "v_esb_forecast_daily",  # G53 forecast F&B (22-g53-insight-fnb-forecast.sql)
    # G52 §28.15.8 (persona Toko F&B, kelas BR)
    "v_esb_store_sales_daily",
    "v_esb_store_menu_daily",
    "v_esb_store_payment_daily",
    "v_esb_store_receipt_daily",
    "v_esb_sales_done_day",  # Insight YoY 24 Nov 2026 (23-insight-yoy.sql)
    "v_esb_menu_econ_daily",
    # G67 §31.6 (24-g67-esb-pl-waste-fillrate.sql), kelas PT
    "v_esb_pl_monthly",
    "v_esb_item_usage_daily",
    "v_esb_item_usage_product_monthly",
    "v_esb_fill_rate_daily",
    "v_esb_fill_rate_product_monthly",
}
REVIEW_DWH_VIEWS = {"v_esb_movement_daily", "v_esb_coverage"}
YOY_DWH_VIEWS = {"v_esb_sales_done_day", "v_esb_menu_econ_daily"}
DONE_DAY_COLUMNS = ["brand", "company_code", "branch_id", "day"]
MENU_ECON_COLUMNS = (
    "sales_date brand company_code branch_id menu_name menu_category qty_paid gross_paid disc_paid cogs_paid "
    "qty_free cogs_free qty_pkg cogs_pkg pkg_qty pkg_gross pkg_disc"
).split()
# Kontrak kolom (frontend menulis query paralel terhadap daftar ini; urutan = urutan SELECT).
MOVEMENT_COLUMNS = [
    "day",
    "brand",
    "company_code",
    "kind",
    "origin_branch_id",
    "origin_branch_name",
    "origin_kind",
    "dest_branch_id",
    "dest_branch_name",
    "dest_kind",
    "item_group",
    "category",
    "product_code",
    "product_name",
    "uom",
    "qty",
    "lines",
]
COVERAGE_COLUMNS = ["brand", "company_code", "entity", "first_day", "last_day", "days"]


def strip_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def dwh_view_body(name: str) -> str:
    """Badan view efn_dwh.<name> (tanpa komentar) dari 12/13/19/21/22/23/24."""
    header = f"CREATE OR REPLACE VIEW efn_dwh.{name} AS"
    for ddl in (DDL24, DDL23, DDL22, DDL21, DDL19, DDL13, DDL12):
        if header in ddl:
            return strip_comments(ddl.split(header, 1)[1].split(";", 1)[0])
    raise AssertionError(f"view efn_dwh.{name} tidak ditemukan")


def output_columns(name: str) -> list[str]:
    """Kolom keluaran SELECT teratas (setiap kolom wajib `AS <nama>`)."""
    body = dwh_view_body(name)
    head = body[body.index("SELECT") + len("SELECT") :].split("\nFROM", 1)[0]
    return re.findall(r"\bAS (\w+)", head)


def insight_views() -> dict[str, tuple[list[str], str]]:
    out: dict[str, tuple[list[str], str]] = {}
    for name, cols, source in INSIGHT_VIEW_RE.findall(ACCESS):
        out[name] = ([c.strip() for c in cols.replace("\n", " ").split(",")], source)
    return out


def referenced_objects(name: str, seen: set[str] | None = None) -> set[str]:
    """Semua objek efn_dwh/efn_esb yang dirujuk view efn_dwh.<name>, rekursif lewat view lain."""
    seen = set() if seen is None else seen
    for ref in re.findall(r"\b(efn_(?:dwh|esb)\.\w+)", dwh_view_body(name)):
        if ref in seen:
            continue
        seen.add(ref)
        db, obj = ref.split(".")
        if db == "efn_dwh" and obj.startswith("v_"):
            referenced_objects(obj, seen)
    return seen


def test_insight_views_set_and_explicit_columns() -> None:
    views = insight_views()
    assert set(views) == EXPECTED_INSIGHT_VIEWS
    assert ACCESS.count("CREATE OR REPLACE VIEW efn_insight.") == len(EXPECTED_INSIGHT_VIEWS)
    for name, (cols, source) in views.items():
        assert "*" not in cols, name
        assert "company_code" in cols, name  # target row policy
        assert len(cols) == len(set(cols)), name
        src_cols = output_columns(source.split(".", 1)[1])
        assert set(cols) <= set(src_cols), (name, set(cols) - set(src_cols))


def test_data_quality_uses_pt_mapped_source() -> None:
    assert insight_views()["v_esb_data_quality"][1] == "efn_dwh.v_esb_data_quality_pt"
    body = dwh_view_body("v_esb_data_quality_pt")
    # Baris ber-PT diteruskan; baris per brand (company_code '') dipetakan ke PT cabang brand itu (Bacha -> BCF+BCR).
    assert "WHERE company_code != '')" in body and "WHERE company_code = '')" in body
    assert body.count("FROM efn_dwh.dim_esb_branch FINAL WHERE company_code != ''") == 1
    assert "c.company_code AS company_code" in body


def test_product_list_scoped_by_branch_company() -> None:
    body = dwh_view_body("v_esb_product_list")
    # company_code dari dim_esb_branch (PT cabang), BUKAN dim_esb_product.company_code (PT default brand).
    assert "c.company_code AS company_code" in body and "p.company_code" not in body
    assert "INNER JOIN (SELECT DISTINCT brand, company_code FROM efn_dwh.dim_esb_branch FINAL" in body


def test_sales_branch_daily_counts_unique_receipts_on_latest_slice() -> None:
    body = dwh_view_body("v_esb_sales_branch_daily")
    assert "uniqExact(s.sales_num) AS trx_s" in body
    assert "GROUP BY sales_date, brand, company_code, branch_id\n" in body
    assert "menu_code" not in body
    assert "FROM efn_esb.extract_checkpoint FINAL WHERE entity = 'sales'" in body


def test_new_dwh_views_idempotent_and_named_columns() -> None:
    assert set(re.findall(r"CREATE OR REPLACE VIEW efn_dwh\.(\w+) AS", DDL13)) == NEW_DWH_VIEWS
    assert not re.search(r"\b(DROP|TRUNCATE|ALTER|INSERT|CREATE TABLE)\b", strip_comments(DDL13))
    assert "SELECT *" not in DDL13
    for name in NEW_DWH_VIEWS:
        cols = output_columns(name)
        assert "company_code" in cols, name
        head = dwh_view_body(name)
        head = head[head.index("SELECT") + 6 :].split("\nFROM", 1)[0]
        # Setiap item SELECT teratas memakai AS (jumlah koma tingkat atas + 1 == jumlah alias).
        depth, items = 0, 1
        for ch in head:
            depth += ch == "("
            depth -= ch == ")"
            items += ch == "," and depth == 0
        assert items == len(cols), name


def _top_level_items(name: str) -> int:
    head = dwh_view_body(name)
    head = head[head.index("SELECT") + 6 :].split("\nFROM", 1)[0]
    depth, items = 0, 1
    for ch in head:
        depth += ch == "("
        depth -= ch == ")"
        items += ch == "," and depth == 0
    return items


def test_review_dwh_views_idempotent_and_contract_columns() -> None:
    assert set(re.findall(r"CREATE OR REPLACE VIEW efn_dwh\.(\w+) AS", DDL19)) == REVIEW_DWH_VIEWS
    code = strip_comments(DDL19)
    assert not re.search(r"\b(DROP|TRUNCATE|ALTER|INSERT|CREATE TABLE)\b", code)
    assert "SELECT *" not in code
    assert output_columns("v_esb_movement_daily") == MOVEMENT_COLUMNS
    assert output_columns("v_esb_coverage") == COVERAGE_COLUMNS
    for name in REVIEW_DWH_VIEWS:
        assert _top_level_items(name) == len(output_columns(name)), name  # setiap item SELECT teratas ber-AS
    views = insight_views()
    assert views["v_esb_movement_daily"] == (MOVEMENT_COLUMNS, "efn_dwh.v_esb_movement_daily")
    assert views["v_esb_coverage"] == (COVERAGE_COLUMNS, "efn_dwh.v_esb_coverage")


def test_movement_reads_all_items_from_finished_slices_only() -> None:
    body = dwh_view_body("v_esb_movement_daily")
    # Semua produk (FIN dan non-FIN): tidak ada filter is_fin di fakta (beda dengan v_esb_flow_daily).
    assert "is_fin = 1 AND" not in body and "WHERE is_fin" not in body
    assert "FROM efn_dwh.fact_esb_production FINAL" in body and "FROM efn_dwh.fact_esb_delivery FINAL" in body
    assert "fact_esb_transfer" not in body and "fact_esb_fin_sold_daily" not in body
    # Hanya versi slice selesai terakhir (pola v_esb_flow_daily).
    assert "(brand, prod_date, _version) IN" in body and "(brand, delivery_date, _version) IN" in body
    assert body.count("FROM efn_esb.extract_checkpoint FINAL") == 2
    assert "WHERE entity = 'production' AND scope_key = '*'" in body
    assert "WHERE entity = 'delivery' AND scope_key = '*'" in body
    # qty tidak pernah dijumlah lintas satuan: uom ikut kunci agregasi.
    assert "GROUP BY day, brand, kind, origin_id, dest_id, p_code, uom_r\n" in body
    assert "'production' AS kind" in body and "'delivery' AS kind" in body
    assert "toUInt32(0) AS dest_id" in body


def test_movement_company_code_fail_closed() -> None:
    # SEC-IR-02: PT dari cabang asal saja; cabang tak dikenal -> '' (row policy `company_code != ''` menolak),
    # tidak jatuh ke PT default brand (Bacha: cabang BCR belum dipetakan tidak boleh terlihat user BCF).
    body = dwh_view_body("v_esb_movement_daily")
    assert "CAST(ob.company_code, 'String') AS company_code" in body
    assert "t.c_code, ob.company_code" not in body


def test_movement_kind_and_item_group_rules() -> None:
    body = dwh_view_body("v_esb_movement_daily")
    assert "multiIf(ob.is_ck = 1, 'ck', ob.is_store = 1, 'store', 'non_sales') AS origin_kind" in body
    dest_kind = "if(t.kind = 'production', '', multiIf(tb.is_ck = 1, 'ck', tb.is_store = 1, 'store', 'non_sales'))"
    assert dest_kind in body
    group = re.sub(r"\s+", " ", body.split("multiIf(\n", 1)[1].split("AS item_group", 1)[0]).strip()
    # Urutan multiIf = prioritas: FIN dulu, lalu WIP, bahan baku, kemasan, supplies, sisanya other.
    order = [group.index(f"'{g}'") for g in ("fin", "wip", "raw", "packaging", "supplies", "other")]
    assert order == sorted(order)
    assert group.startswith("t.fin_s = 1, 'fin',")
    for needle in (
        "startsWith(t.p_code, 'WIP') OR pr.category = 'Inventory - WIP', 'wip'",
        "startsWith(t.p_code, 'RMT')",
        "startsWith(t.p_code, 'IDF')",
        "pr.category IN ('Inventory - Food & Beverage', 'Import Ingredient'), 'raw'",
        "startsWith(t.p_code, 'PAM') OR pr.category = 'Packaging Material', 'packaging'",
        "startsWith(t.p_code, 'SU') OR startsWith(t.p_code, 'UNI') OR "
        "pr.category IN ('Supplies', 'Uniform'), 'supplies'",
    ):
        assert needle in group, needle
    # Negatif: 'N' (satuan kemasan) bukan prefix -> tidak boleh menangkap semua kode berawalan N.
    assert "startsWith(t.p_code, 'N')" not in group
    assert "if(pr.category = '', 'Tanpa kategori', pr.category) AS category" in body
    # PT = PT cabang asal (tanpa fallback, SEC-IR-02), tidak diduplikasi ke PT tujuan.
    assert "CAST(ob.company_code, 'String') AS company_code" in body
    assert "tb.company_code" not in body


def test_coverage_from_finished_daily_checkpoints_mapped_to_pt() -> None:
    body = dwh_view_body("v_esb_coverage")
    # G67 §31.4: + item_journal, transfer_request, delivery_ref; profit_loss TIDAK (slice tahun).
    entities = (
        "entity IN ('sales', 'payment', 'production', 'delivery', 'transfer', 'fin_sold', 'item_journal',\n"
        "                               'transfer_request', 'delivery_ref')"
    )
    assert body.count(entities) == 2
    assert "profit_loss" not in body
    assert body.count("status = 'done'") == 2
    # Per brand ('*') -> semua PT brand; per toko -> PT cabang. Tanpa cabang terdaftar -> tidak ada baris.
    assert "scope_key = '*'" in body and "scope_key != '*'" in body
    assert (
        "INNER JOIN (SELECT DISTINCT brand, company_code FROM efn_dwh.dim_esb_branch FINAL WHERE company_code != '')"
    ) in body
    assert "toString(branch_id) AS scope_key" in body and "s.scope_key = k.scope_key" in body
    assert "LEFT JOIN" not in body  # fail closed: baris tanpa PT tidak pernah muncul
    assert "uniqExact(u.day) AS days_d" in body
    assert "efn_esb.extract_run" not in body


def test_insight_reader_only_reads_efn_insight() -> None:
    reader = [line for line in ACCESS.splitlines() if re.search(r"\bTO insight_reader\b", line)]
    assert reader == ["GRANT SELECT ON efn_insight.* TO insight_reader;", "    TO insight_reader;"]
    assert "REVOKE ALL ON efn_dwh.* FROM insight_reader;" in ACCESS
    assert "REVOKE ALL ON efn_esb.* FROM insight_reader;" in ACCESS
    assert not re.search(r"GRANT [^;]*ON efn_(dwh|esb)\.[^;]*TO insight_reader", ACCESS)


def test_definer_grants_exactly_referenced_objects() -> None:
    granted = set(re.findall(r"GRANT SELECT ON (efn_\w+\.\w+) TO efn_insight_definer;", ACCESS))
    needed: set[str] = set()
    for _, source in insight_views().values():
        name = source.split(".", 1)[1]
        needed |= {source} | referenced_objects(name)
    assert granted == needed, (needed - granted, granted - needed)
    assert "efn_esb.raw_doc" not in granted and "efn_esb.bom_ratio" not in granted
    assert "ON efn_dwh.* TO efn_insight_definer" not in ACCESS
    assert "CREATE USER IF NOT EXISTS efn_insight_definer NOT IDENTIFIED HOST NONE;" in ACCESS
    assert not re.search(r"GRANT (ALL|INSERT|ALTER|DROP|TRUNCATE|CREATE|OPTIMIZE)", ACCESS)


def test_profile_and_quota() -> None:
    profile = ACCESS.split("CREATE SETTINGS PROFILE IF NOT EXISTS insight_readonly SETTINGS", 1)[1].split(";", 1)[0]
    for setting in (
        "readonly = 2 CONST",
        "allow_ddl = 0 CONST",
        "allow_introspection_functions = 0 CONST",
        "max_execution_time = 30 MAX 60",
        "max_result_rows = 200000 MAX 200000",
    ):
        assert setting in profile, setting
    assert "ALTER SETTINGS PROFILE insight_readonly SETTINGS" in ACCESS
    assert "CREATE QUOTA IF NOT EXISTS insight_quota KEYED BY user_name" in ACCESS


def test_init_script_user_binding_and_scope_key() -> None:
    assert "create_user efn_insight /run/secrets/clickhouse_password_insight" in INIT_SH
    assert "GRANT insight_reader TO efn_insight;" in INIT_SH
    assert "SET DEFAULT ROLE insight_reader TO efn_insight;" in INIT_SH
    assert "ALTER USER efn_insight SETTINGS PROFILE 'insight_readonly';" in INIT_SH
    assert 'INSIGHT_KEY="$(cat /run/secrets/insight_scope_key)"' in INIT_SH
    assert "''|*[!0-9a-fA-F]*) echo \"clickhouse-init: insight_scope_key harus hex\"" in INIT_SH
    assert '[ "${#INSIGHT_KEY}" -ge 64 ]' in INIT_SH  # docs/04 §26.7/§26.10
    assert '[ "$INSIGHT_KEY" != "$SCOPE_KEY" ]' in INIT_SH  # kunci beda dari text2sql


def esb_policy_manifest() -> dict[str, str]:
    """ESB_POLICY_VIEWS insight-esb-policy.sh -> {view: kelas PT|BR} (baris duplikat = gagal)."""
    block = ESB_POLICY_SH.split('ESB_POLICY_VIEWS="\n', 1)[1].split('\n"\n', 1)[0]
    out: dict[str, str] = {}
    for line in block.splitlines():
        view, cls = line.split()
        assert view not in out, view
        out[view] = cls
    return out


def test_every_insight_view_has_row_policy() -> None:
    # G52 (§28.15.7): daftar + predikat di access/insight-esb-policy.sh (disourcing clickhouse-init.sh); 14 policy.
    manifest = esb_policy_manifest()
    assert set(manifest) == EXPECTED_INSIGHT_VIEWS
    assert len(manifest) == ESB_POLICY_COUNT
    assert f"\nESB_POLICY_EXPECTED={ESB_POLICY_COUNT}\n" in INIT_SH
    assert '[ "$ESB_POLICY_N" = "$ESB_POLICY_EXPECTED" ]' in ESB_POLICY_SH
    assert "\n. /init/access/insight-esb-policy.sh\n" in INIT_SH
    assert (
        "CREATE ROW POLICY OR REPLACE efn_insight_scope ON efn_insight.%s USING %s TO insight_reader;" in ESB_POLICY_SH
    )
    # SEC-190: format kanonik codes/cids (menolak byte padding length-extension), juga untuk efn_t2s.
    fmt = re.search(r'^FMT="(.+)"$', INIT_SH, re.MULTILINE)
    assert fmt and "match(getSetting('SQL_efn_codes'), '^[A-Z]{2,8}(,[A-Z]{2,8})*" in fmt.group(1)
    assert "match(getSetting('SQL_efn_cids'), '^([0-9]{1,10}(,[0-9]{1,10})*)?" in fmt.group(1)
    assert INIT_SH.count("USING ${FMT} AND ${CIDS} AND ${SIG}") == 1
    assert INIT_SH.count("USING ${FMT} AND ${CODES} AND ${SIG}") == 1
    # SEC-191: DDL policy (memuat kunci) tanpa query_log: 2x efn_t2s di init + policy ESB di file yang disourcing.
    assert INIT_SH.count("$CH --log_queries=0 --multiquery") == 2
    assert ESB_POLICY_SH.count("$CH --log_queries=0 --multiquery") == 1
    # G52: insight tidak lagi memakai sig v1 (SQL_efn_sig) untuk view ESB; sig v1 tetap milik text2sql.
    assert "INSIGHT_SIG" not in INIT_SH
    assert "SQL_efn_sig'" not in ESB_POLICY_SH and "getSetting('SQL_efn_sig3')" in ESB_POLICY_SH
    assert "getSetting('SQL_efn_sig')" in re.search(r'^SIG="(.+)"$', INIT_SH, re.MULTILINE).group(1)
    # Verifikasi fail-closed ke system.row_policies + cabut grant bila ada view tanpa policy.
    assert "system.row_policies WHERE database = 'efn_insight' AND short_name = 'efn_insight_scope'" in INIT_SH
    assert 'REVOKE SELECT ON efn_insight.* FROM insight_reader" < /dev/null' in INIT_SH


# --------------------------------------------------------------------------------------------------------------------
# Kontrak lintas-sisi (QA G42): nama secret/env, batas konkurensi, header Referrer-Policy, dokumen. Statis (baca teks).
# --------------------------------------------------------------------------------------------------------------------
def _read(*parts: str) -> str:
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def _profile_block(statement: str) -> str:
    """Daftar setting satu statement profil insight_readonly, tanpa komentar/spasi berlebih."""
    # Komentar dibuang DULU: komentar profil memuat ";" yang akan memotong blok statement.
    body = strip_comments(ACCESS).split(statement, 1)[1].split(";", 1)[0]
    return re.sub(r"\s+", " ", body).strip()


def test_profile_create_and_alter_are_identical() -> None:
    # CREATE ... IF NOT EXISTS tidak mengubah profil lama; ALTER menyamakannya. Keduanya harus sama persis, kalau
    # tidak profil bergantung pada riwayat deploy (deploy baru vs lama).
    create = _profile_block("CREATE SETTINGS PROFILE IF NOT EXISTS insight_readonly SETTINGS")
    alter = _profile_block("ALTER SETTINGS PROFILE insight_readonly SETTINGS")
    assert create == alter


def test_concurrency_limit_matches_insight_web_semaphore() -> None:
    profile = _profile_block("CREATE SETTINGS PROFILE IF NOT EXISTS insight_readonly SETTINGS")
    match = re.search(r"max_concurrent_queries_for_user = (\d+) MAX (\d+)", profile)
    assert match, "max_concurrent_queries_for_user harus diset (satu user ClickHouse dipakai semua staf)"
    limit, limit_max = int(match.group(1)), int(match.group(2))
    assert limit == limit_max  # klien tidak bisa menaikkan
    semaphore = _read("services", "insight-web", "lib", "semaphore.ts")
    # `n >= 1 && n <= <maks> ? n : <default>` dan `raw === '' ? <default> : Number(raw)`
    bounds = re.search(r"n >= 1 && n <= (\d+) \? n : (\d+)", semaphore)
    default = re.search(r"raw === '' \? (\d+) : Number\(raw\)", semaphore)
    assert bounds and default, "bentuk concurrencyFromEnv berubah; sesuaikan test ini"
    env_max, fallback, env_default = int(bounds.group(1)), int(bounds.group(2)), int(default.group(1))
    assert env_max <= limit, "INSIGHT_CH_MAX_CONCURRENCY boleh melebihi batas ClickHouse -> galat 202"
    assert fallback == env_default < limit, "default semaphore harus < batas profil agar ekspor CSV tetap muat"


def test_insight_secret_names_consistent_across_sides() -> None:
    rows = [line.split() for line in _read("scripts", "secrets.list").splitlines() if not line.startswith("#")]
    secrets = {parts[0]: parts[1] for parts in rows if len(parts) >= 2}
    assert secrets["insight_sso_key"] == secrets["insight_session_key"] == secrets["insight_scope_key"] == "hex32"
    assert secrets["clickhouse_password_insight"] == "random"
    compose = _read("infra", "compose", "docker-compose.yml")
    config_ts = _read("services", "insight-web", "lib", "config.ts")
    for name in ("insight_sso_key", "insight_session_key", "insight_scope_key", "clickhouse_password_insight"):
        assert re.search(rf"^  {name}: \{{ file: \.\./\.\./secrets/{name}\.txt \}}", compose, re.MULTILINE), name
        assert f"/run/secrets/{name}" in config_ts, name  # default kode = nama secret compose
    web = re.search(r"\n  insight-web:\n(.*?)\n  insight-web-tls:", compose, re.DOTALL)
    assert web, "service insight-web tidak ditemukan di docker-compose.yml"
    for env, name in (
        ("INSIGHT_SSO_KEY_FILE", "insight_sso_key"),
        ("INSIGHT_SESSION_KEY_FILE", "insight_session_key"),
        ("INSIGHT_SCOPE_KEY_FILE", "insight_scope_key"),
        ("CH_PASSWORD_FILE", "clickhouse_password_insight"),
    ):
        assert f"{env}: /run/secrets/{name}" in web.group(1), env
        assert f"{env}" in config_ts, env
    # Odoo: nama env + default file identik dengan compose
    ctrl = _read("services", "odoo", "addons", "efn_insight", "controllers", "main.py")
    assert 'KEY_FILE_ENV = "EFN_INSIGHT_SSO_KEY_FILE"' in ctrl and 'URL_ENV = "EFN_INSIGHT_URL"' in ctrl
    assert 'DEFAULT_KEY_FILE = "/run/secrets/insight_sso_key"' in ctrl
    assert "EFN_INSIGHT_SSO_KEY_FILE: /run/secrets/insight_sso_key" in compose
    assert "EFN_INSIGHT_URL: https://insight." in compose
    # Kunci cakupan di clickhouse-init = secret yang sama dengan yang dibaca insight-web
    assert "/run/secrets/insight_scope_key" in INIT_SH and "/run/secrets/clickhouse_password_insight" in INIT_SH


def test_dev_origin_ports_match_between_odoo_url_and_insight_origin() -> None:
    # Cek Origin /sso (C1) = origin lengkap Odoo: skema + host + PORT harus sama dengan URL yang dibuka browser dev.
    dev = _read("infra", "compose", "docker-compose.dev.yml")
    odoo = re.search(r"EFN_INSIGHT_ODOO_URL: https://(erp\.[\w.-]+):(\d+)\b", dev)
    form = re.search(r"EFN_INSIGHT_URL: https://(insight\.[\w.-]+):(\d+)\b", dev)
    assert odoo and form
    assert odoo.group(2) == form.group(2)  # port wg-https dev sama
    assert odoo.group(1).split(".", 1)[1] == form.group(1).split(".", 1)[1]  # domain dasar sama


def test_referrer_policy_variants_and_routers() -> None:
    middlewares = _read("infra", "traefik", "dynamic", "middlewares.yml")
    sso = middlewares.split("    secure-headers-insight-sso:", 1)[1].split("\n\n", 1)[0]
    plain = middlewares.split("    secure-headers-insight:", 1)[1].split("    # G42: varian KHUSUS", 1)[0]
    # Halaman form Odoo: strict-origin (Origin harus sampai ke /sso); dashboard: no-referrer.
    assert "referrerPolicy: strict-origin" in sso and "referrerPolicy: no-referrer" not in sso
    assert "referrerPolicy: no-referrer" in plain and "strict-origin" not in plain
    compose = _read("infra", "compose", "docker-compose.yml")
    form_mw = re.search(r"traefik\.http\.routers\.odoo-insight-open\.middlewares: (\S+)", compose)
    dash_mw = re.search(r"traefik\.http\.routers\.insight\.middlewares: (\S+)", compose)
    assert form_mw and dash_mw
    assert "secure-headers-insight-sso@file" in form_mw.group(1).split(",")
    assert "secure-headers-insight@file" in dash_mw.group(1).split(",")
    assert "secure-headers-insight-sso@file" not in dash_mw.group(1)
    # Controller Odoo memasang strict-origin hanya pada halaman form (halaman info memakai default no-referrer).
    ctrl = _read("services", "odoo", "addons", "efn_insight", "controllers", "main.py")
    assert ctrl.count('referrer="strict-origin"') == 1
    assert 'def _secure_headers(response, csp, referrer="no-referrer")' in ctrl
    # Kill switch Odoo = insight-web: hanya string "true" persis.
    assert 'return os.environ.get(ENABLED_ENV) == "true"' in ctrl
    config_ts = _read("services", "insight-web", "lib", "config.ts")
    assert "env.INSIGHT_ENABLED === 'true'" in config_ts


def test_contract_doc_reflects_g42_hardening() -> None:
    doc = _read("docs", "04_SERVICE_CONTRACTS.md")
    section = doc.split("## 26. Gelombang 42", 1)[1].split("\n## 27.", 1)[0]
    for needle in (
        "__Host-eis",  # C2
        "company_code != ''",  # C4
        "max_concurrent_queries_for_user",
        "INSIGHT_CH_MAX_CONCURRENCY",
        "strict-origin",
        "secure-headers-insight-sso",
        "v_esb_sales_branch_daily",
    ):
        assert needle in section, f"docs/04 §26 belum memuat {needle}"
    # Origin wajib: tidak boleh lagi ada klaim "tanpa Origin diterima".
    assert "tanpa Origin ditolak" in section or "Origin wajib" in section


def test_policy_codes_predicate_uses_company_code() -> None:
    assert "CODES=\"has(splitByChar(',', getSetting('SQL_efn_codes')), company_code)\"" in INIT_SH
    # Policy insight dibuat SETELAH kunci divalidasi dan SEBELUM kunci dibuang dari environment skrip.
    esb_at = INIT_SH.index("\n. /init/access/insight-esb-policy.sh\n")
    sm_at = INIT_SH.index("\n. /init/access/insight-sm-policy.sh\n")
    assert INIT_SH.index('[ "$INSIGHT_KEY" != "$SCOPE_KEY" ]') < esb_at < sm_at
    assert INIT_SH.index("\nunset INSIGHT_KEY\n") > sm_at


def test_yoy_views_contract_and_rules() -> None:
    """Insight YoY 24 Nov 2026: view idempoten, kolom kontrak, wrapper + row policy PT, aturan pemisahan baris."""
    code = strip_comments(DDL23)
    assert set(re.findall(r"CREATE OR REPLACE VIEW efn_dwh\.(\w+) AS", DDL23)) == YOY_DWH_VIEWS
    assert not re.search(r"\b(DROP|TRUNCATE|ALTER|INSERT|CREATE TABLE)\b", code)
    assert "SELECT *" not in code
    assert output_columns("v_esb_sales_done_day") == DONE_DAY_COLUMNS
    assert output_columns("v_esb_menu_econ_daily") == MENU_ECON_COLUMNS
    for name in YOY_DWH_VIEWS:
        assert _top_level_items(name) == len(output_columns(name)), name
        assert f"GRANT SELECT ON efn_dwh.{name} TO efn_insight_definer;" in ACCESS
    views = insight_views()
    assert views["v_esb_sales_done_day"] == (DONE_DAY_COLUMNS, "efn_dwh.v_esb_sales_done_day")
    assert views["v_esb_menu_econ_daily"] == (MENU_ECON_COLUMNS, "efn_dwh.v_esb_menu_econ_daily")
    # Kelas PT: memuat cogs (menu) / dipakai bersama view PT; sesi persona toko (brs terisi) ditolak.
    manifest = esb_policy_manifest()
    assert all(manifest[name] == "PT" for name in YOY_DWH_VIEWS)
    done = dwh_view_body("v_esb_sales_done_day")
    # Hanya slice penjualan selesai; PT dari cabang, cabang tak dikenal -> '' (fail closed, tanpa PT default brand).
    assert "WHERE entity = 'sales' AND status = 'done'" in done
    assert "CAST(ifNull(b.company_code, ''), 'String') AS company_code" in done
    econ = dwh_view_body("v_esb_menu_econ_daily")
    # Versi slice terakhir saja; dikelompokkan per NAMA menu (kode SC000xx dan DB/TB lama bergabung).
    assert "FROM efn_esb.extract_checkpoint FINAL WHERE entity = 'sales'" in econ
    assert "GROUP BY sales_date, brand, company_code, branch_id, menu_name\n" in econ
    assert "s.row_type = 'ALACARTE' AND s.price > 0" in econ
    assert "s.row_type = 'ALACARTE' AND s.price = 0" in econ
    assert "s.row_type = 'PACKAGE_COMPONENT'" in econ and "s.row_type = 'PACKAGE_HEADER'" in econ
