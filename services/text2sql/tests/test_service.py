from pathlib import Path

import pytest

from efn_text2sql.clickhouse import ClickHouseError, QueryResult
from efn_text2sql.llm import LlmAnswer, LlmUnavailableError
from efn_text2sql.semantic import load_semantic_layer
from efn_text2sql.service import AskService

LAYER = load_semantic_layer(Path(__file__).resolve().parents[1] / "semantic_layer.yaml")
KEY = "c" * 32


class FakeLlm:
    def __init__(self, text: str = "", fail: bool = False) -> None:
        self.text = text
        self.fail = fail
        self.prompts: list[str] = []

    def answer(self, prompt: str) -> LlmAnswer:
        self.prompts.append(prompt)
        if self.fail:
            raise LlmUnavailableError("semua penyedia gagal")
        return LlmAnswer(self.text, "ninerouter:cc/x")


class FakeCh:
    def __init__(self, result: QueryResult | None = None, error: str = "") -> None:
        self.result = result or QueryResult(("brand", "net"), (("PB", 100.0),))
        self.error = error
        self.calls: list[tuple[str, dict[str, str]]] = []

    def query(self, sql: str, settings: dict[str, str]) -> QueryResult:
        self.calls.append((sql, settings))
        if self.error:
            raise ClickHouseError(self.error)
        return self.result


def body(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "question": "Penjualan per brand bulan ini",
        "company_ids": [1],
        "company_codes": ["PB"],
        "request_id": "r1",
    }
    base.update(kw)
    return base


SQL = "```sql\nSELECT brand, sum(net_sales) AS net FROM v_sales_daily GROUP BY brand\n```"


def svc(llm: FakeLlm, ch: FakeCh, enabled: bool = True) -> AskService:
    return AskService(LAYER, llm, ch, KEY, enabled)


def test_ok_flow_signs_scope_and_wraps_sql() -> None:
    llm, ch = FakeLlm(SQL), FakeCh()
    reply = svc(llm, ch).ask(body())
    assert reply.status == 200
    assert reply.body["status"] == "ok"
    assert reply.body["rows"] == [["PB", 100.0]]
    assert reply.body["engine"] == "ninerouter:cc/x"
    assert reply.body["tables"] == ["efn_t2s.v_sales_daily"]
    sql, settings = ch.calls[0]
    assert sql.startswith("SELECT * FROM (") and "LIMIT 1000" in sql
    assert settings["SQL_efn_cids"] == "1" and settings["SQL_efn_codes"] == "PB"
    assert len(settings["SQL_efn_sig"]) == 64


def test_prompt_contains_schema_and_question_but_no_scope_or_key() -> None:
    llm = FakeLlm(SQL)
    svc(llm, FakeCh()).ask(body(company_ids=[77]))
    prompt = llm.prompts[0]
    assert "efn_t2s.v_sales_daily(" in prompt
    assert "Question: Penjualan per brand bulan ini" in prompt
    assert KEY not in prompt
    assert "77" not in prompt


def test_disabled_returns_503_without_llm_call() -> None:
    llm = FakeLlm(SQL)
    reply = svc(llm, FakeCh(), enabled=False).ask(body())
    assert (reply.status, reply.body["code"]) == (503, "text2sql_disabled")
    assert not llm.prompts


@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        {"question": "x" * 3},
        body(extra=1),
        body(question="ab"),
        body(question="x" * 501),
        body(question=5),
        body(company_ids=[]),
        body(company_ids=["1"]),
        body(company_codes=["ZZ"]),
        body(request_id="a b"),
    ],
)
def test_invalid_request_400(bad: object) -> None:
    llm = FakeLlm(SQL)
    reply = svc(llm, FakeCh()).ask(bad)
    assert (reply.status, reply.body) == (400, {"status": "invalid", "code": "invalid_request"})
    assert not llm.prompts


def test_llm_unavailable_503() -> None:
    reply = svc(FakeLlm(fail=True), FakeCh()).ask(body())
    assert (reply.status, reply.body["code"]) == (503, "llm_unavailable")


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("", "llm_no_sql"),
        ("```sql\nDROP TABLE efn_dwh.fact_sales_line\n```", "not_select"),
        ("```sql\nSELECT * FROM efn_dwh.fact_sales_line\n```", "table_not_allowed"),
        ("```sql\nSELECT * FROM efn_dwh.v_gl_consolidated\n```", "table_not_allowed"),
        ("```sql\nSELECT * FROM v_sales_daily SETTINGS SQL_efn_cids='1,2,3'\n```", "forbidden_clause"),
        ("```sql\nSELECT getSetting('SQL_efn_sig') FROM v_sales_daily\n```", "function_not_allowed"),
        ("Maaf, saya tidak bisa membantu.", "parse_error"),
    ],
)
def test_guard_rejections_never_reach_clickhouse(text: str, code: str) -> None:
    ch = FakeCh()
    reply = svc(FakeLlm(text), ch).ask(body())
    assert reply.status == 200
    assert reply.body["status"] == "rejected"
    assert reply.body["code"] in {code, "parse_error", "not_select", "no_table"}
    assert not ch.calls


def test_clickhouse_errors() -> None:
    reply = svc(FakeLlm(SQL), FakeCh(error="clickhouse_unavailable")).ask(body())
    assert (reply.status, reply.body["code"]) == (503, "clickhouse_unavailable")
    reply = svc(FakeLlm(SQL), FakeCh(error="query_failed")).ask(body())
    assert (reply.status, reply.body) == (200, {"status": "rejected", "code": "query_failed"})


def test_truncated_flag() -> None:
    rows = tuple((str(i), float(i)) for i in range(1000))
    reply = svc(FakeLlm(SQL), FakeCh(QueryResult(("a", "b"), rows))).ask(body())
    assert reply.body["truncated"] is True
    assert reply.body["row_count"] == 1000


def test_question_and_sql_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    svc(FakeLlm(SQL), FakeCh()).ask(body(question="Penjualan Toko Rahasia XYZ"))
    text = caplog.text
    assert "EFN_TEXT2SQL request=r1 status=ok" in text
    assert "Rahasia" not in text
    assert "net_sales" not in text


class FakeAgent:
    def __init__(self, reply=(200, {"status": "ok", "steps": [], "row_count": 0}, "e"), boom: bool = False) -> None:  # noqa: ANN001
        self.reply, self.boom = reply, boom
        self.calls: list[tuple[str, object]] = []

    def run(self, question, scope):  # noqa: ANN001, ANN201
        self.calls.append((question, scope))
        if self.boom:
            raise RuntimeError("rahasia")
        return self.reply


def test_analyze_disabled_and_agent_missing() -> None:
    assert svc(FakeLlm(SQL), FakeCh(), enabled=False).analyze(body()).body["code"] == "text2sql_disabled"
    reply = svc(FakeLlm(SQL), FakeCh()).analyze(body())
    assert (reply.status, reply.body["code"]) == (503, "agent_disabled")


def test_analyze_validates_and_delegates() -> None:
    ag = FakeAgent()
    s = AskService(LAYER, FakeLlm(SQL), FakeCh(), KEY, True, agent=ag)  # type: ignore[arg-type]
    assert s.analyze(body(extra=1)).status == 400
    assert s.analyze(body(company_ids=[])).status == 400
    reply = s.analyze(body(question="  Tren   penjualan  "))
    assert reply.status == 200 and ag.calls[0][0] == "Tren penjualan"
    boom = AskService(LAYER, FakeLlm(SQL), FakeCh(), KEY, True, agent=FakeAgent(boom=True))  # type: ignore[arg-type]
    r = boom.analyze(body())
    assert r.status == 500 and "rahasia" not in str(r.body)
