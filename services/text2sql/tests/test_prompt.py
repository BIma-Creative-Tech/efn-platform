import pytest

from efn_text2sql.prompt import build_prompt, extract_sql
from efn_text2sql.semantic import parse_semantic_layer

LAYER = parse_semantic_layer(
    {
        "database": "efn_dwh",
        "tables": [{"name": "v_x", "description": "Contoh", "columns": [{"name": "a", "type": "UInt8"}]}],
        "examples": [{"question": "Berapa a?", "sql": "SELECT a FROM v_x"}],
    },
)


def test_build_prompt_contains_schema_and_question() -> None:
    prompt = build_prompt(LAYER, "  Total a?  ")
    assert "efn_dwh.v_x(a UInt8)" in prompt
    assert "Question: Total a?" in prompt
    assert "SELECT a FROM v_x" in prompt


def test_extract_sql_from_fenced_block() -> None:
    response = "Berikut query:\n```sql\nSELECT a FROM v_x;\n```\nSemoga membantu"
    assert extract_sql(response) == "SELECT a FROM v_x"


def test_extract_sql_without_fence() -> None:
    assert extract_sql("SELECT a FROM v_x;") == "SELECT a FROM v_x"
    assert extract_sql("") == ""


def test_semantic_layer_rejects_qualified_names_and_missing_tables() -> None:
    with pytest.raises(ValueError):
        parse_semantic_layer({"tables": [{"name": "system.tables"}]})
    with pytest.raises(ValueError):
        parse_semantic_layer({"database": "efn_dwh"})
