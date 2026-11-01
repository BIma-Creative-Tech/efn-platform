import json
from decimal import Decimal

import pytest

from efn_text2sql.clickhouse import ClickHouseClient, ClickHouseError


class Resp:
    def __init__(self, status: int, content: bytes) -> None:
        self.status_code = status
        self.content = content


class Sess:
    def __init__(self, resp: Resp) -> None:
        self.resp = resp
        self.calls: list[dict[str, object]] = []
        self.trust_env = True
        self.verify: object = None

    def post(self, url: str, **kw: object) -> Resp:
        self.calls.append({"url": url, **kw})
        return self.resp


def client(resp: Resp) -> tuple[ClickHouseClient, Sess]:
    s = Sess(resp)
    return ClickHouseClient("https://clickhouse:8443", "efn_text2sql", "pw", "/ca", session=s), s  # type: ignore[arg-type]


def test_query_params_auth_database_and_decimal() -> None:
    body = {"meta": [{"name": "brand"}, {"name": "net"}], "data": [["PB", 1500.25], ["GL", 12345678901234567890.5]]}
    c, s = client(Resp(200, json.dumps(body).encode()))
    r = c.query("SELECT 1", {"SQL_efn_cids": "1"})
    call = s.calls[0]
    assert call["params"]["database"] == "efn_t2s"  # type: ignore[index]
    assert call["params"]["SQL_efn_cids"] == "1"  # type: ignore[index]
    assert call["headers"] == {"X-ClickHouse-User": "efn_text2sql", "X-ClickHouse-Key": "pw"}
    assert call["allow_redirects"] is False
    assert s.verify == "/ca" and s.trust_env is False
    assert r.columns == ("brand", "net")
    assert r.rows[0] == ("PB", 1500.25)
    assert isinstance(r.rows[1][1], str)
    assert "pw" not in repr(c)
    assert Decimal(r.rows[1][1]) > 0


@pytest.mark.parametrize(
    ("status", "content", "code"),
    [
        (404, b"Code: 60. Unknown table SELECT rahasia", "query_failed"),
        (500, b"x", "clickhouse_unavailable"),
        (503, b"", "clickhouse_unavailable"),
        (500, b"Code: 497. DB::Exception: efn_text2sql: Not enough privileges rahasia", "query_failed"),
        (200, b"bukan json", "query_failed"),
        (200, b'{"meta": 1}', "query_failed"),
    ],
)
def test_errors_do_not_leak_body(status: int, content: bytes, code: str) -> None:
    c, _ = client(Resp(status, content))
    with pytest.raises(ClickHouseError) as info:
        c.query("SELECT 1", {})
    assert info.value.code == code
    assert "rahasia" not in str(info.value)
