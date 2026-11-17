"""Paging ESB (docs/04 §25.3). Logika murni + helper yang menerima fungsi GET (diinjeksi).

Dua bentuk total di ESB:
- laporan & master: `result.count` = total baris semua halaman -> lanjut selama baris terkumpul < count (aman walau
  server membatasi ukuran halaman di bawah `limit` yang diminta);
- `/branch/list/user`: `dataCount` = baris di halaman INI (bukan total) -> lanjut selama halaman penuh (= limit).
Laporan `sales-menu-cogs` limit tetap 20 dan `limit` tidak dikirim (`send_limit=False`).
"""

from collections.abc import Callable, Mapping, Sequence
from typing import Any

Getter = Callable[[str, dict[str, Any]], Any]


class PagingError(RuntimeError):
    """Jumlah halaman melewati batas pengaman (`ESB_MAX_PAGES`)."""


def page_rows(result: Any) -> list[dict[str, Any]]:
    """Baris data dari `result` (dict ber-`data` atau list). Elemen bukan dict dibuang."""
    if result is None:
        return []
    if isinstance(result, list):
        data: Any = result
    elif isinstance(result, Mapping):
        data = result.get("data") or []
    else:
        return []
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def _total(result: Any) -> int | None:
    if not isinstance(result, Mapping):
        return None
    count = result.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        return None
    return count


def has_next_page(result: Any, seen: int, got: int, limit: int) -> bool:
    if got <= 0:
        return False
    total = _total(result)
    if total is not None:
        return seen < total
    return got >= limit


def fetch_all(
    get: Getter,
    path: str,
    params: Mapping[str, Any],
    *,
    limit: int,
    send_limit: bool = True,
    limit_params: Sequence[str] = ("limit",),
    max_pages: int = 500,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
        query = dict(params)
        query["page"] = page
        if send_limit:
            for name in limit_params:
                query[name] = limit
        result = get(path, query)
        if isinstance(result, list):  # endpoint tanpa paging (mis. ringkasan pembayaran)
            rows.extend(page_rows(result))
            return rows
        data = page_rows(result)
        rows.extend(data)
        if not has_next_page(result, len(rows), len(data), limit):
            return rows
        page += 1
        if page > max_pages:
            raise PagingError(f"{path}: melebihi {max_pages} halaman")
