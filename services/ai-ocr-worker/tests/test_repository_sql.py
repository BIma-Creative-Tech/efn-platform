"""Mengunci least privilege user `efn_ocr` (docs/04 §7.3) pada level teks SQL repository."""

import re

import pytest

from efn_ocr import repository

ALLOWED_UPDATE_COLUMNS = {"status", "reason", "extracted", "confidence", "image_phash", "updated_at"}
ALL_SQL = {name: value for name, value in vars(repository).items() if name.startswith("SQL_")}
UPDATE_SQL = {name: sql for name, sql in ALL_SQL.items() if sql.lstrip().upper().startswith("UPDATE")}


def _set_columns(sql: str) -> set[str]:
    match = re.search(r"\bSET\b(.*?)\bWHERE\b", sql, flags=re.IGNORECASE | re.DOTALL)
    assert match, sql
    return {m.group(1).lower() for m in re.finditer(r"(\w+)\s*=", match.group(1))}


def test_sql_constants_found() -> None:
    assert {"SQL_CLAIM", "SQL_FINALIZE", "SQL_RELEASE", "SQL_COUNT_VALID_TODAY", "SQL_FIND_DUPLICATE"} <= set(ALL_SQL)
    assert set(UPDATE_SQL) == {"SQL_CLAIM", "SQL_FINALIZE", "SQL_RELEASE"}


@pytest.mark.parametrize("name", sorted(UPDATE_SQL))
def test_updates_only_touch_allowed_columns(name: str) -> None:
    assert _set_columns(UPDATE_SQL[name]) <= ALLOWED_UPDATE_COLUMNS


@pytest.mark.parametrize("name", sorted(ALL_SQL))
def test_no_encrypted_or_raw_identity_columns(name: str) -> None:
    sql = ALL_SQL[name].lower()
    assert "_enc" not in sql
    assert "user_id" not in sql
    assert "*" not in sql.replace("count(*)", "")
    assert not re.search(r"\b(insert|delete|truncate)\b", sql)


def test_daily_cap_and_claim_use_user_hash() -> None:
    assert "user_hash = %(user_hash)s" in repository.SQL_COUNT_VALID_TODAY
    assert "user_hash" in repository.SQL_CLAIM
