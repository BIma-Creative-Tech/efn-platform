"""SEC-206: definer view efn_t2s hanya punya SELECT per objek yang dirujuk view efn_t2s (penutupan rekursif), tanpa
wildcard efn_dwh.* dan tanpa tabel ESB/migrasi. Statis (membaca infra/clickhouse)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CH = ROOT / "infra" / "clickhouse"
ACCESS = (CH / "access" / "text2sql-access.sql").read_text(encoding="utf-8")
INIT = "\n".join(p.read_text(encoding="utf-8") for p in sorted((CH / "init").glob("*.sql")))


def strip_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def dwh_view_body(name: str) -> str:
    pattern = rf"CREATE (?:OR REPLACE )?VIEW (?:IF NOT EXISTS )?efn_dwh\.{name} AS(.*?);"
    bodies = re.findall(pattern, strip_comments(INIT), re.DOTALL)
    assert bodies, name
    return bodies[-1]  # definisi terakhir (urutan clickhouse-init) yang berlaku


def closure(view: str, seen: set[str]) -> None:
    for ref in re.findall(r"\b(efn_dwh\.\w+)", dwh_view_body(view)):
        if ref not in seen:
            seen.add(ref)
            name = ref.split(".")[1]
            if name.startswith("v_"):
                closure(name, seen)


def test_definer_grants_are_exact_closure() -> None:
    body = strip_comments(ACCESS)
    sources = set(re.findall(r"SQL SECURITY DEFINER AS\s+SELECT .*?FROM (efn_dwh\.\w+);", body, re.DOTALL))
    assert len(sources) == 7
    needed: set[str] = set()
    for ref in sources:
        needed.add(ref)
        closure(ref.split(".")[1], needed)
    granted = set(re.findall(r"^GRANT SELECT ON (efn_dwh\.\w+) TO efn_t2s_definer;$", body, re.MULTILINE))
    assert granted == needed
    assert "GRANT SELECT ON efn_dwh.* TO efn_t2s_definer" not in body
    assert "REVOKE ALL ON efn_dwh.* FROM efn_t2s_definer;" in body
    assert body.index("REVOKE ALL ON efn_dwh.* FROM efn_t2s_definer;") < body.index("TO efn_t2s_definer;")


def test_definer_cannot_read_esb_or_migration_tables() -> None:
    granted = re.findall(r"GRANT SELECT ON (efn_\w+\.\w+) TO efn_t2s_definer", ACCESS)
    assert not [g for g in granted if "esb" in g or "audit" in g or "gl" in g.split(".")[1].split("_")]
