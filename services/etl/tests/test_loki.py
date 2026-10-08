"""Pembaca Loki query_range (G14): validasi respons, paginasi tanpa kehilangan entri di batas timestamp."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from efn_etl.loki import HttpsLokiTransport, LokiError, parse_query_range, query_range, to_ns

T0 = datetime(2026, 10, 8, 0, 0, tzinfo=UTC)


def _resp(values: list[tuple[int, str]], labels: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "status": "success",
        "data": {
            "resultType": "streams",
            "result": [{"stream": labels or {"stream": "x"}, "values": [[str(t), line] for t, line in values]}],
        },
    }


class FakeLoki:
    """Meniru Loki: entri [start, end) urut maju, maks `limit`."""

    def __init__(self, entries: list[tuple[int, str]]) -> None:
        self.entries = sorted(entries)
        self.calls: list[dict[str, str]] = []

    def get_json(self, path: str, params: dict[str, str]) -> Any:
        self.calls.append(params)
        start, end, limit = int(params["start"]), int(params["end"]), int(params["limit"])
        hit = [e for e in self.entries if start <= e[0] < end][:limit]
        return _resp(hit)


def test_to_ns_microsecond_precision() -> None:
    assert to_ns(T0 + timedelta(microseconds=5)) == to_ns(T0) + 5000


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "error"},
        {"status": "success", "data": {"resultType": "matrix", "result": []}},
        {"status": "success", "data": {"resultType": "streams", "result": [{"values": [["x", "l"]]}]}},
        {"status": "success", "data": {"resultType": "streams", "result": [{"values": [["1", 5]]}]}},
        [],
    ],
)
def test_invalid_payload_fails_closed(payload: Any) -> None:
    with pytest.raises(LokiError):
        parse_query_range(payload)


def test_parse_sorts_across_streams() -> None:
    payload = _resp([(5, "b")])
    payload["data"]["result"].append({"stream": {"stream": "y"}, "values": [["1", "a"]]})
    assert [e.line for e in parse_query_range(payload)] == ["a", "b"]


def test_pagination_keeps_entries_sharing_boundary_timestamp() -> None:
    base = to_ns(T0)
    entries = [(base + i // 3, f"l{i}") for i in range(10)]  # 3 entri per nanodetik
    loki = FakeLoki(entries)
    result = query_range(loki, "{q}", T0, T0 + timedelta(seconds=1), page_limit=4)
    assert sorted(e.line for e in result.entries) == sorted(line for _, line in entries)
    assert not result.truncated and result.pages > 1


def test_pagination_truncated_when_page_cannot_advance() -> None:
    base = to_ns(T0)
    loki = FakeLoki([(base, f"l{i}") for i in range(6)])
    result = query_range(loki, "{q}", T0, T0 + timedelta(seconds=1), page_limit=3)
    assert result.truncated


def test_pagination_truncated_at_max_pages() -> None:
    base = to_ns(T0)
    loki = FakeLoki([(base + i, f"l{i}") for i in range(20)])
    result = query_range(loki, "{q}", T0, T0 + timedelta(seconds=1), page_limit=2, max_pages=3)
    assert result.truncated and len(result.entries) < 20


def test_transport_requires_ca_for_https() -> None:
    with pytest.raises(ValueError):
        HttpsLokiTransport("https://loki:3100", None, None, None)
    with pytest.raises(ValueError):
        HttpsLokiTransport("ftp://loki", None, None, None)
