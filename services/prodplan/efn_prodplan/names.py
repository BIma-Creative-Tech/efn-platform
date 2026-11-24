"""Nama cabang/lokasi ESB untuk tampilan (bukan ID). Cache memori TTL ±1 jam per brand, GET lewat klien baca brand
(token bucket tetap berlaku). Gagal apa pun -> `None` (request tidak pernah gagal karena nama), cache negatif 60 dtk.

Sumber: `GET /branch/list/user` (`branchID`, `branchName`) dan `GET /location/branch/{branchID}`
(`locationID`, `locationName`).
"""

import logging
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

from efn_prodplan.esb import page_rows
from efn_prodplan.logs import log_event

NAME_TTL_SECONDS = 3600.0
NEGATIVE_TTL_SECONDS = 60.0


def _int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return 0


def parse_names(rows: Any, id_key: str, name_key: str) -> dict[int, str]:
    out: dict[int, str] = {}
    for row in page_rows(rows):
        key = _int(row.get(id_key))
        name = row.get(name_key)
        if key > 0 and isinstance(name, str) and name.strip():
            out[key] = name.strip()[:120]
    return out


class NameCache:
    def __init__(self, ttl: float = NAME_TTL_SECONDS, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl
        self._clock = clock
        self._data: dict[tuple[str, ...], tuple[float, dict[int, str]]] = {}
        self._lock = threading.Lock()

    def _get(self, key: tuple[str, ...], load: Callable[[], dict[int, str]]) -> dict[int, str]:
        now = self._clock()
        with self._lock:
            cached = self._data.get(key)
            if cached and now < cached[0]:
                return cached[1]
        try:
            value = load()
            expires = now + self._ttl
        except Exception as exc:  # noqa: BLE001 - nama hanya kosmetik; jangan gagalkan request
            log_event("esb_names_failed", logging.WARNING, error_class=type(exc).__name__)
            value, expires = {}, now + NEGATIVE_TTL_SECONDS
        with self._lock:
            self._data[key] = (expires, value)
        return value

    def branches(self, brand: str, client: Any) -> dict[int, str]:
        if client is None:
            return {}
        return self._get(
            ("branch", brand),
            lambda: parse_names(
                client.get_all("/branch/list/user", {"pageSize": 200}, limit=200, max_pages=20),
                "branchID",
                "branchName",
            ),
        )

    def locations(self, brand: str, client: Any, branch_id: int) -> dict[int, str]:
        if client is None or int(branch_id) <= 0:
            return {}
        return self._get(
            ("location", brand, str(int(branch_id))),
            lambda: parse_names(client.get(f"/location/branch/{int(branch_id)}"), "locationID", "locationName"),
        )

    def branch_name(self, brand: str, client: Any, branch_id: Any) -> str | None:
        return self.branches(brand, client).get(_int(branch_id))

    def location_name(self, brand: str, client: Any, branch_id: Any, location_id: Any) -> str | None:
        return self.locations(brand, client, _int(branch_id)).get(_int(location_id))

    def decorate(
        self,
        brand: str,
        client: Any,
        target: dict[str, Any],
        pairs: Mapping[str, tuple[str, str | None]],
    ) -> None:
        """`pairs` = {kolom_nama_tujuan: (kolom_branch, kolom_location|None)}; mengisi nama atau None."""
        for out_key, (branch_key, loc_key) in pairs.items():
            branch_id = target.get(branch_key)
            if loc_key is None:
                target[out_key] = self.branch_name(brand, client, branch_id)
            else:
                target[out_key] = self.location_name(brand, client, branch_id, target.get(loc_key))
