import http.client
import json
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from efn_text2sql.clickhouse import QueryResult
from efn_text2sql.llm import LlmAnswer
from efn_text2sql.semantic import load_semantic_layer
from efn_text2sql.server import build_server
from efn_text2sql.service import AskService

LAYER = load_semantic_layer(Path(__file__).resolve().parents[1] / "semantic_layer.yaml")
API_KEY = "s" * 40


class Llm:
    def answer(self, prompt: str) -> LlmAnswer:
        return LlmAnswer("```sql\nSELECT brand FROM v_sales_daily\n```", "ollama:q")


class Ch:
    def query(self, sql: str, settings: dict[str, str]) -> QueryResult:
        return QueryResult(("brand",), (("PB",),))


@pytest.fixture
def port() -> Iterator[int]:
    httpd = build_server(AskService(LAYER, Llm(), Ch(), "d" * 32, True), API_KEY, "127.0.0.1", 0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield httpd.server_address[1]
    httpd.shutdown()


def call(port: int, method: str, path: str, body: bytes | None = None, key: str | None = API_KEY) -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Content-Type": "application/json"}
    if key is not None:
        headers["X-EFN-Service-Key"] = key
    conn.request(method, path, body=body, headers=headers)
    resp = conn.getresponse()
    data = json.loads(resp.read() or b"{}")
    conn.close()
    return resp.status, data


def test_healthz(port: int) -> None:
    assert call(port, "GET", "/healthz", key=None) == (200, {"status": "ok"})


def test_ask_requires_key(port: int) -> None:
    body = json.dumps({"question": "berapa", "company_ids": [1]}).encode()
    assert call(port, "POST", "/v1/ask", body, key=None)[0] == 401
    assert call(port, "POST", "/v1/ask", body, key="salah")[0] == 401


def test_ask_ok(port: int) -> None:
    body = json.dumps({"question": "brand apa saja", "company_ids": [1], "company_codes": ["PB"]}).encode()
    status, data = call(port, "POST", "/v1/ask", body)
    assert status == 200 and data["rows"] == [["PB"]]


def test_body_limits(port: int) -> None:
    assert call(port, "POST", "/v1/ask", b"x" * 9000)[0] == 400
    assert call(port, "POST", "/v1/ask", b"{bukan json")[0] == 400
    assert call(port, "POST", "/v2/ask", b"{}")[0] == 404


def test_analyze_route_requires_key_and_reports_disabled_agent(port: int) -> None:
    body = json.dumps({"question": "tren penjualan", "company_ids": [1]}).encode()
    assert call(port, "POST", "/v1/analyze", body, key=None)[0] == 401
    assert call(port, "POST", "/v1/analyze", body) == (503, {"status": "unavailable", "code": "agent_disabled"})
