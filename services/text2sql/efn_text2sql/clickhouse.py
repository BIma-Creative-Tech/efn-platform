"""Eksekusi SQL hasil guard di ClickHouse HTTPS 8443 sebagai `efn_text2sql` (readonly=2, docs/04 §23.3)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import requests

MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class ClickHouseError(RuntimeError):
    """ClickHouse menolak/tidak terjangkau. `code` aman untuk ditampilkan."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]


def _cell(value: Any) -> Any:
    if isinstance(value, Decimal):
        f = float(value)
        return f if abs(f) < 2**53 else str(value)
    return value


class ClickHouseClient:
    def __init__(
        self,
        url: str,
        user: str,
        password: str,
        ca_file: str | None,
        timeout_seconds: float = 30,
        session: requests.Session | None = None,
        database: str = "efn_t2s",
    ) -> None:
        self._url = url.rstrip("/") + "/"
        # Database default sesi = database semantic layer: nama view tanpa prefix (FROM v_sales_daily) diresolusi ke
        # efn_t2s, sama dengan cara guard memvalidasinya (default_database).
        self._database = database
        self._user = user
        self._password = password
        self._timeout = timeout_seconds
        self._session = session or requests.Session()
        self._session.trust_env = False
        self._session.verify = ca_file if ca_file else True

    def __repr__(self) -> str:
        return f"ClickHouseClient(url={self._url!r}, user={self._user!r})"

    def query(self, sql: str, settings: dict[str, str]) -> QueryResult:
        params = {
            "database": self._database,
            "default_format": "JSONCompact",
            "output_format_json_quote_64bit_integers": "0",
            "output_format_json_quote_decimals": "0",
            **settings,
        }
        headers = {"X-ClickHouse-User": self._user, "X-ClickHouse-Key": self._password}
        try:
            resp = self._session.post(
                self._url,
                params=params,
                data=sql.encode("utf-8"),
                headers=headers,
                timeout=self._timeout,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise ClickHouseError("clickhouse_unavailable") from exc
        if resp.status_code != 200:
            # Body ClickHouse bisa memuat potongan SQL -> tidak diteruskan; hanya kode generik. ClickHouse menjawab
            # error query (hak akses, tabel tidak ada, timeout query) dengan HTTP 4xx/500 + body "Code: N. ...";
            # selain itu (proxy/overload tanpa body ClickHouse) = tidak tersedia.
            is_query_error = resp.status_code < 500 or resp.content.lstrip().startswith(b"Code:")
            raise ClickHouseError("query_failed" if is_query_error else "clickhouse_unavailable")
        if len(resp.content) > MAX_RESPONSE_BYTES:
            raise ClickHouseError("result_too_large")
        try:
            body = json.loads(resp.content, parse_float=Decimal)
        except ValueError as exc:
            raise ClickHouseError("query_failed") from exc
        meta = body.get("meta") if isinstance(body, dict) else None
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(meta, list) or not isinstance(data, list):
            raise ClickHouseError("query_failed")
        columns = tuple(str(m.get("name", "")) for m in meta if isinstance(m, dict))
        rows = tuple(tuple(_cell(v) for v in row) for row in data if isinstance(row, list))
        return QueryResult(columns=columns, rows=rows)
