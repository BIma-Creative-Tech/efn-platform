"""Paging ESB: result.count (total) vs dataCount (per halaman), limit tetap, batas halaman."""

from typing import Any

import pytest

from efn_esb.paging import PagingError, fetch_all, has_next_page, page_rows


class Pages:
    def __init__(self, pages: list[Any]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, path: str, params: dict[str, Any]) -> Any:
        self.calls.append((path, dict(params)))
        index = params["page"] - 1
        return self.pages[index] if index < len(self.pages) else {"count": 0, "data": []}


def rows(n: int, start: int = 0) -> list[dict[str, int]]:
    return [{"i": start + i} for i in range(n)]


def test_count_based_paging_fixed_limit_without_limit_param() -> None:
    get = Pages(
        [
            {"count": 45, "data": rows(20)},
            {"count": 45, "data": rows(20, 20)},
            {"count": 45, "data": rows(5, 40)},
        ]
    )
    out = fetch_all(get, "/report/sales-menu-cogs", {"salesDate": "2026-11-16"}, limit=20, send_limit=False)
    assert [r["i"] for r in out] == list(range(45))
    assert len(get.calls) == 3
    assert all("limit" not in params for _, params in get.calls)


def test_count_based_paging_survives_server_capped_page_size() -> None:
    # Diminta 100 per halaman, server hanya memberi 50: lanjut berdasarkan total, bukan page*limit.
    get = Pages(
        [
            {"count": 120, "data": rows(50)},
            {"count": 120, "data": rows(50, 50)},
            {"count": 120, "data": rows(20, 100)},
        ]
    )
    out = fetch_all(get, "/product", {}, limit=100)
    assert len(out) == 120
    assert get.calls[0][1]["limit"] == 100


def test_datacount_is_per_page_so_full_page_continues() -> None:
    # /branch/list/user: dataCount = baris halaman ini (bukan total) -> tidak dipakai sebagai total.
    get = Pages([{"dataCount": 20, "data": rows(20)}, {"dataCount": 3, "data": rows(3, 20)}])
    out = fetch_all(get, "/branch/list/user", {}, limit=20, limit_params=("limit", "pageSize"))
    assert len(out) == 23
    assert get.calls[0][1] == {"page": 1, "limit": 20, "pageSize": 20}


def test_empty_first_page() -> None:
    get = Pages([{"count": 0, "data": []}])
    assert fetch_all(get, "/supplier", {}, limit=100) == []
    assert len(get.calls) == 1


def test_list_result_is_not_paged() -> None:
    get = Pages([[{"branchCode": "A"}, "bukan-dict"]])
    assert fetch_all(get, "/report/sales-payment-summary", {}, limit=20, send_limit=False) == [{"branchCode": "A"}]


def test_max_pages_guard() -> None:
    def endless(path: str, params: dict[str, Any]) -> Any:
        return {"data": rows(20)}

    with pytest.raises(PagingError):
        fetch_all(endless, "/product", {}, limit=20, max_pages=3)


@pytest.mark.parametrize(
    ("result", "seen", "got", "limit", "expected"),
    [
        ({"count": 40}, 40, 20, 20, False),
        ({"count": 41}, 40, 20, 20, True),
        ({"count": True}, 20, 20, 20, True),  # bool bukan total -> aturan halaman penuh
        ({"count": -1}, 5, 5, 20, False),
        ({}, 20, 20, 20, True),
        ({}, 19, 19, 20, False),
        ({"count": 100}, 0, 0, 20, False),
    ],
)
def test_has_next_page(result: dict[str, Any], seen: int, got: int, limit: int, expected: bool) -> None:
    assert has_next_page(result, seen, got, limit) is expected


def test_page_rows_shapes() -> None:
    assert page_rows(None) == []
    assert page_rows("x") == []
    assert page_rows({"data": None}) == []
    assert page_rows({"data": [{"a": 1}, 2]}) == [{"a": 1}]
