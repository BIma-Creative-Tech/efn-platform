"""Klien /json/2: retry + backoff hanya untuk transport/429/5xx, chunk duplikat = sukses, log tanpa secret/body."""

import json
import logging
from typing import Any

import pytest

from efn_forecast.odoo import METHODS, OdooError, OdooForecastClient

KEY = "k3y-forecast-smd-super-secret"
RUN = "6f1c2b8e-0d3a-4c8e-9d61-1b5a2f4e7c90"


class FakeTransport:
    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[tuple[str, bytes, dict[str, str], float]] = []

    def post(self, path: str, body: bytes, headers: Any, timeout: float) -> tuple[int, bytes]:
        self.calls.append((path, body, dict(headers), timeout))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        status, payload = step
        return status, payload if isinstance(payload, bytes) else json.dumps(payload).encode()


def _client(script: list[Any], retries: int = 6) -> tuple[OdooForecastClient, FakeTransport, list[float]]:
    transport = FakeTransport(script)
    sleeps: list[float] = []
    client = OdooForecastClient(transport, KEY, database="efn_odoo", max_retries=retries, sleep=sleeps.append)
    return client, transport, sleeps


def _payload(chunk_no: int = 0) -> dict[str, Any]:
    return {"schema_version": 1, "run_id": RUN, "chunk_no": chunk_no, "lines": [{"product_id": 1}]}


def test_paths_match_contract_allowlist() -> None:
    assert {f"{m}/{f}" for m, f in METHODS.values()} == {
        "efn.forecast.calendar/efn_forecast_calendar_upsert",
        "efn.forecast.run/efn_forecast_run_open",
        "efn.forecast.daily/efn_forecast_ingest",
        "efn.demand.forecast.line/efn_minmax_ingest",
        "efn.forecast.run/efn_forecast_run_close",
    }


def test_retry_on_gateway_503_and_network_then_success(caplog: pytest.LogCaptureFixture) -> None:
    ok = {"ok": True, "run_id": RUN, "chunk_no": 0, "accepted": 1, "rejected": [], "duplicate_chunk": False}
    client, transport, sleeps = _client([(503, b"busy"), ConnectionError("reset"), (429, {}), (200, ok)])
    with caplog.at_level(logging.DEBUG):
        result = client.ingest(_payload())
    assert result["accepted"] == 1
    assert len(transport.calls) == 4 and len(sleeps) == 3
    assert sleeps[0] < sleeps[1] < sleeps[2]  # backoff eksponensial (+ jitter < 0,5 s)
    path, body, headers, timeout = transport.calls[0]
    assert path == "efn.forecast.daily/efn_forecast_ingest"
    assert headers["Authorization"] == f"bearer {KEY}" and headers["X-Odoo-Database"] == "efn_odoo"
    assert json.loads(body) == _payload()  # body = argumen bernama payload §28.2 apa adanya (idempoten diulang)
    assert all(c[1] == body for c in transport.calls)
    assert KEY not in caplog.text and RUN not in caplog.text and "product_id" not in caplog.text
    assert "OdooForecastClient()" == repr(client)


def test_retry_exhausted_raises_transport() -> None:
    client, transport, sleeps = _client([(502, b"")] * 3, retries=2)
    with pytest.raises(OdooError) as err:
        client.run_open({"run_id": RUN})
    assert err.value.kind == "transport" and len(transport.calls) == 3 and len(sleeps) == 2


@pytest.mark.parametrize(
    ("status", "body", "kind", "code"),
    [
        (400, {"error": "invalid_lines"}, "rejected", "invalid_lines"),
        # Bentuk nyata json2 Odoo 19 (efn_demand_forecast/models/forecast_api.py): UserError -> 422, AccessError -> 403
        (
            422,
            {"name": "odoo.exceptions.UserError", "message": "efn_forecast:incomplete_run"},
            "rejected",
            "incomplete_run",
        ),
        (
            403,
            {"name": "odoo.exceptions.AccessError", "message": "efn_forecast:company_mismatch"},
            "company_mismatch",
            "company_mismatch",
        ),
        (
            403,
            {"name": "odoo.exceptions.AccessError", "message": "efn_forecast:access_denied"},
            "auth",
            "access_denied",
        ),
        (403, {"error": "company_mismatch"}, "company_mismatch", "company_mismatch"),
        (401, b"", "auth", ""),
        (404, b"<html>", "not_found", ""),
        (400, {"error": "Traceback (most recent call last): ..."}, "rejected", ""),  # bukan identifier -> dibuang
    ],
)
def test_client_errors_not_retried(status: int, body: Any, kind: str, code: str) -> None:
    client, transport, sleeps = _client([(status, body)])
    with pytest.raises(OdooError) as err:
        client.run_close({"run_id": RUN})
    assert (err.value.kind, err.value.code) == (kind, code)
    assert len(transport.calls) == 1 and sleeps == []


def test_duplicate_chunk_is_success_and_response_checked() -> None:
    dup = {"ok": True, "run_id": RUN, "chunk_no": 3, "accepted": 0, "rejected": [], "duplicate_chunk": True}
    client, _, _ = _client([(200, dup)])
    assert client.ingest(_payload(3))["duplicate_chunk"] is True
    for bad in ({"ok": False}, {"ok": True, "chunk_no": 4}, {"ok": True, "rejected": "x"}, [1]):
        client, _, _ = _client([(200, bad)])
        with pytest.raises(OdooError) as err:
            client.minmax_ingest(_payload(3))
        assert err.value.kind == "invalid_response"
    client, _, _ = _client([(200, b"not json")])
    with pytest.raises(OdooError):
        client.calendar_upsert({"schema_version": 1})
