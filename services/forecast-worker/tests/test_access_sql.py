"""Akses ClickHouse forecast-worker (statis): view efn_forecast eksplisit = kolom yang dibaca worker, grant minimum,
reader hanya efn_forecast, worker tidak pernah menulis ClickHouse (L-145)."""

import re
from pathlib import Path

from efn_forecast import source
from efn_forecast.source import VIEW_COLUMNS

ROOT = Path(__file__).resolve().parents[3]
CH = ROOT / "infra" / "clickhouse"
ACCESS = (CH / "access" / "forecast-access.sql").read_text(encoding="utf-8")
DDL = (CH / "init" / "15-g44-forecast.sql").read_text(encoding="utf-8")
VIEW_RE = re.compile(
    r"CREATE OR REPLACE VIEW efn_forecast\.(\w+) DEFINER = efn_forecast_definer SQL SECURITY DEFINER AS\n"
    r"SELECT (.+?)\nFROM (efn_dwh\.\w+);",
    re.DOTALL,
)


def _strip(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def _views() -> dict[str, tuple[list[str], str]]:
    out: dict[str, tuple[list[str], str]] = {}
    for name, cols, src in VIEW_RE.findall(ACCESS):
        out[name] = ([c.strip() for c in cols.replace("\n", " ").split(",")], src)
    return out


def test_views_match_worker_columns() -> None:
    views = _views()
    assert set(views) == set(VIEW_COLUMNS)
    for name, (cols, src) in views.items():
        assert tuple(cols) == VIEW_COLUMNS[name], name
        assert src == f"efn_dwh.v_fc_{name[2:]}", name
        assert f"CREATE OR REPLACE VIEW {src} AS" in DDL, src
    for pii in ("customer_id", "member_hash", "user_hash", "partner", "lot_name", "product_name"):
        assert pii not in _strip(ACCESS), pii


def test_worker_queries_only_efn_forecast_views_parameterized() -> None:
    sqls = [v for k, v in vars(source).items() if k.startswith("SQL_")]
    assert len(sqls) == 6
    for sql in sqls:
        refs = set(re.findall(r"FROM (\S+)", sql))
        assert refs and all(r.startswith("efn_forecast.v_") for r in refs), refs
        assert "SELECT *" not in sql and "{" in sql  # selalu difilter parameter PT/tanggal
        assert not re.search(r"\b(INSERT|ALTER|DELETE|CREATE|DROP|TRUNCATE)\b", sql)
    code = Path(source.__file__).read_text(encoding="utf-8")
    assert ".insert(" not in code and ".command(" not in code


def test_reader_role_only_reads_efn_forecast() -> None:
    lines = [ln for ln in ACCESS.splitlines() if re.search(r"\bTO forecast_reader\b", ln)]
    assert lines == ["GRANT SELECT ON efn_forecast.* TO forecast_reader;", "    TO forecast_reader;"]
    assert "REVOKE ALL ON efn_dwh.* FROM forecast_reader;" in ACCESS
    assert not re.search(r"GRANT (ALL|INSERT|ALTER|DROP|TRUNCATE|CREATE|OPTIMIZE)", ACCESS)
    assert "CREATE USER IF NOT EXISTS efn_forecast_definer NOT IDENTIFIED HOST NONE;" in ACCESS
    assert "ON efn_dwh.* TO efn_forecast_definer" not in ACCESS


def test_definer_grants_cover_exactly_referenced_objects() -> None:
    granted = set(re.findall(r"GRANT SELECT ON (efn_dwh\.\w+) TO efn_forecast_definer;", ACCESS))
    needed: set[str] = set()
    for _, src in _views().values():
        needed.add(src)
        body = _strip(DDL.split(f"CREATE OR REPLACE VIEW {src} AS", 1)[1].split(";", 1)[0])
        needed |= set(re.findall(r"\b(efn_dwh\.\w+)", body))
    assert granted == needed, (needed - granted, granted - needed)


def test_profile_readonly_and_create_alter_identical() -> None:
    def block(stmt: str) -> str:
        return re.sub(r"\s+", " ", _strip(ACCESS).split(stmt, 1)[1].split(";", 1)[0]).strip()

    create = block("CREATE SETTINGS PROFILE IF NOT EXISTS forecast_readonly SETTINGS")
    assert create == block("ALTER SETTINGS PROFILE forecast_readonly SETTINGS")
    for setting in ("readonly = 2 CONST", "allow_ddl = 0 CONST", "allow_introspection_functions = 0 CONST"):
        assert setting in create
    init_sh = (CH / "clickhouse-init.sh").read_text(encoding="utf-8")
    assert "create_user efn_forecast_reader /run/secrets/clickhouse_password_forecast" in init_sh
    assert "GRANT forecast_reader TO efn_forecast_reader;" in init_sh
    assert "ALTER USER efn_forecast_reader SETTINGS PROFILE 'forecast_readonly';" in init_sh
