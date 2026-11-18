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
ACCESS = (CH / "access" / "insight-access.sql").read_text(encoding="utf-8")
INIT_SH = (CH / "clickhouse-init.sh").read_text(encoding="utf-8")

INSIGHT_VIEW_RE = re.compile(
    r"CREATE OR REPLACE VIEW efn_insight\.(\w+) DEFINER = efn_insight_definer SQL SECURITY DEFINER AS\n"
    r"SELECT (.+?)\nFROM (efn_dwh\.\w+);",
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
}


def strip_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def dwh_view_body(name: str) -> str:
    """Badan view efn_dwh.<name> (tanpa komentar) dari 12/13."""
    header = f"CREATE OR REPLACE VIEW efn_dwh.{name} AS"
    for ddl in (DDL13, DDL12):
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


def test_every_insight_view_has_row_policy() -> None:
    # Tanpa quantifier bersarang (CodeQL py/redos): daftar view = karakter kata/spasi/backslash sampai "; do".
    loop = re.search(r"for v in (v_esb_[\w \t\n\\]*?); do\n(.+?)\ndone", INIT_SH, re.DOTALL)
    assert loop, "loop row policy efn_insight tidak ditemukan"
    assert set(re.findall(r"v_esb_\w+", loop.group(1))) == EXPECTED_INSIGHT_VIEWS
    stmt = loop.group(2)
    # C4 (§26.13): baris tanpa PT tidak pernah terlihat, termasuk bila codes kosong ber-sig sah.
    assert (
        "CREATE ROW POLICY OR REPLACE efn_insight_scope ON efn_insight.${v} "
        "USING company_code != '' AND ${FMT} AND ${CODES} AND ${INSIGHT_SIG}"
    ) in stmt
    assert stmt.rstrip().endswith('TO insight_reader;"')
    # SEC-190: format kanonik codes/cids (menolak byte padding length-extension), juga untuk efn_t2s.
    fmt = re.search(r'^FMT="(.+)"$', INIT_SH, re.MULTILINE)
    assert fmt and "match(getSetting('SQL_efn_codes'), '^[A-Z]{2,8}(,[A-Z]{2,8})*" in fmt.group(1)
    assert "match(getSetting('SQL_efn_cids'), '^([0-9]{1,10}(,[0-9]{1,10})*)?" in fmt.group(1)
    assert INIT_SH.count("USING ${FMT} AND ${CIDS} AND ${SIG}") == 1
    assert INIT_SH.count("USING ${FMT} AND ${CODES} AND ${SIG}") == 1
    # SEC-191: DDL policy (memuat kunci) tanpa query_log.
    assert INIT_SH.count("$CH --log_queries=0 --multiquery") == 3
    # Format tanda tangan = text2sql (kunci|cids|codes), hanya kuncinya berbeda.
    t2s = re.search(r'^SIG="(.+)"$', INIT_SH, re.MULTILINE)
    ins = re.search(r'^INSIGHT_SIG="(.+)"$', INIT_SH, re.MULTILINE)
    assert t2s and ins
    assert ins.group(1) == t2s.group(1).replace("${SCOPE_KEY}", "${INSIGHT_KEY}")
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
    # Policy insight dibuat sebelum kunci dibuang dari environment skrip.
    policy_at = INIT_SH.index("efn_insight_scope ON efn_insight.${v}")
    assert INIT_SH.index("unset INSIGHT_KEY INSIGHT_SIG") > policy_at
