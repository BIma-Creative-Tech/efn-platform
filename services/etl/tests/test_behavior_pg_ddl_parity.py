"""Paritas ETL `behavior.py` <-> DDL staging PG `infra/postgres/init/20-platform-g50a.sql` (QA G50, baca source).

Staging `behavior.event_raw` dimiliki BFF; ETL membacanya dengan grant kolom. Test ini menjaga tiga hal yang bila
menyimpang menghentikan job (transform ETL gagal-tutup) atau membuat segmen salah:
1. nilai `type` / `channel` / `subject_kind` yang boleh ditulis BFF harus diterima transform ETL;
2. kolom SELECT ETL tercakup grant kolom di init SQL (bukan hanya di `services/etl/sql/platform-behavior-grants.sql`);
3. tipe event yang diandalkan segmen/ClickHouse harus bisa dihasilkan BFF.
"""

import re
from pathlib import Path

from efn_etl.behavior import CHANNELS, EVENT_TYPES, SERVER_EVENT_TYPES, SQL_BEHAVIOR_EVENTS, SUBJECT_KINDS

ROOT = Path(__file__).resolve().parents[3]
PG_INIT = (ROOT / "infra" / "postgres" / "init" / "20-platform-g50a.sql").read_text(encoding="utf-8")
BFF_DTO = (ROOT / "services" / "api-gateway" / "src" / "behavior" / "dto" / "behavior.dto.ts").read_text(
    encoding="utf-8"
)


def _event_raw_ddl() -> str:
    return PG_INIT.split("CREATE TABLE IF NOT EXISTS behavior.event_raw (", 1)[1].split(") PARTITION BY", 1)[0]


def _check_values(column: str) -> set[str]:
    match = re.search(rf"{column}\s+text\s+NOT NULL CHECK \({column} IN \((.*?)\)\)", _event_raw_ddl(), re.S)
    assert match, f"CHECK {column} tidak ditemukan di DDL event_raw"
    return set(re.findall(r"'([a-z_]+)'", match.group(1)))


def test_pg_staging_values_are_accepted_by_etl_transform() -> None:
    assert _check_values("type") <= set(EVENT_TYPES), "BFF boleh menulis tipe yang membuat halaman ETL batal"
    assert _check_values("channel") - {"server"} <= set(CHANNELS)
    assert _check_values("subject_kind") <= set(SUBJECT_KINDS)


def test_pg_channel_server_is_never_written_or_etl_accepts_it() -> None:
    # PG mengizinkan channel 'server' tetapi transform ETL menolaknya (ValueError -> halaman batal).
    # Aman HANYA selama BFF
    # tidak menulisnya (cart_abandon dan purchase mewarisi kanal web|app). Bila BFF menulis 'server', ubah ETL.
    if "server" in _check_values("channel"):
        writer = (ROOT / "services" / "api-gateway" / "src" / "behavior" / "behavior-jobs.service.ts").read_text(
            encoding="utf-8"
        )
        assert "'server'" not in writer


def test_etl_select_columns_covered_by_init_sql_grants() -> None:
    grant = PG_INIT.split("GRANT SELECT (event_id", 1)[1].split(")", 1)[0]
    granted = {c.strip() for c in ("event_id" + grant).replace("\n", " ").split(",")}
    for column in re.findall(r"e\.(\w+)", SQL_BEHAVIOR_EVENTS):
        assert column in granted, f"{column} dibaca ETL tetapi tidak di-grant di 20-platform-g50a.sql"
    assert "GRANT SELECT (id, subject_key, request_id, created_at) ON behavior.subject_erasure TO efn_etl;" in PG_INIT


def test_dto_client_types_match_pg_check() -> None:
    dto = set(re.findall(r"'([a-z_]+)',", BFF_DTO.split("CLIENT_EVENT_TYPES = [", 1)[1].split("] as const", 1)[0]))
    # Tipe server (bukan dari klien): cart_abandon (job BFF) dan purchase (L-185, order online lunas).
    assert dto | {"cart_abandon", "purchase"} == _check_values("type")


def test_purchase_event_can_reach_staging() -> None:
    assert "purchase" in SERVER_EVENT_TYPES
    assert "purchase" in _check_values("type")
