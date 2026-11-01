"""Logika `POST /v1/ask` (docs/04 §23.2). Tanpa I/O langsung: LLM & ClickHouse di-inject (mudah diuji).

Urutan: validasi request -> prompt (skema + contoh + pertanyaan) -> LLM -> extract_sql -> guard_sql -> eksekusi
ClickHouse dengan setting cakupan PT bertanda tangan. Hasil query tidak pernah dikirim ke LLM. Pertanyaan, SQL, dan
hasil tidak di-log (hanya request_id, kode, durasi, engine, jumlah baris).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

from efn_text2sql.agent import AnalysisAgent
from efn_text2sql.clickhouse import ClickHouseError, QueryResult
from efn_text2sql.guard import SqlGuardError, guard_sql
from efn_text2sql.llm import LlmAnswer, LlmUnavailableError
from efn_text2sql.prompt import build_prompt, extract_sql
from efn_text2sql.scope import Scope, ScopeError
from efn_text2sql.semantic import SemanticLayer

log = logging.getLogger(__name__)

ALLOWED_KEYS = frozenset({"question", "company_ids", "company_codes", "request_id"})
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MIN_QUESTION, MAX_QUESTION = 3, 500


class Llm(Protocol):
    def answer(self, prompt: str) -> LlmAnswer: ...


class Executor(Protocol):
    def query(self, sql: str, settings: dict[str, str]) -> QueryResult: ...


@dataclass(frozen=True)
class Reply:
    status: int
    body: dict[str, Any]


class InvalidRequest(ValueError):
    pass


def parse_request(body: Any) -> tuple[str, Scope, str]:
    if not isinstance(body, dict) or set(body) - ALLOWED_KEYS:
        raise InvalidRequest("bentuk body")
    question = body.get("question")
    if not isinstance(question, str):
        raise InvalidRequest("question")
    question = " ".join(question.split())
    if not MIN_QUESTION <= len(question) <= MAX_QUESTION:
        raise InvalidRequest("panjang question")
    request_id = body.get("request_id", "-")
    if not isinstance(request_id, str) or not (request_id == "-" or _REQUEST_ID_RE.match(request_id)):
        raise InvalidRequest("request_id")
    try:
        scope = Scope.build(body.get("company_ids"), body.get("company_codes", []))
    except ScopeError as exc:
        raise InvalidRequest(str(exc)) from exc
    return question, scope, request_id


class AskService:
    def __init__(
        self,
        layer: SemanticLayer,
        llm: Llm,
        executor: Executor,
        scope_key: str,
        enabled: bool,
        agent: AnalysisAgent | None = None,
    ) -> None:
        self._layer = layer
        self._config = layer.guard_config()
        self._llm = llm
        self._executor = executor
        self._scope_key = scope_key
        self._enabled = enabled
        # G39 (docs/04 §24): None = TEXT2SQL_AGENT_ENABLED=false -> /v1/analyze 503 agent_disabled.
        self._agent = agent

    def analyze(self, body: Any) -> Reply:
        """`POST /v1/analyze` (G39): rencana 1..3 query -> eksekusi per PT -> ringkasan (lihat agent.py)."""
        started = time.monotonic()
        request_id = "-"
        try:
            if not self._enabled or self._agent is None:
                code = "text2sql_disabled" if not self._enabled else "agent_disabled"
                return Reply(503, {"status": "unavailable", "code": code})
            try:
                question, scope, request_id = parse_request(body)
            except InvalidRequest:
                return Reply(400, {"status": "invalid", "code": "invalid_request"})
            status, payload, engine = self._agent.run(question, scope)
            reply = Reply(status, payload)
            log.info(
                "EFN_TEXT2SQL_AGENT request=%s status=%s code=%s engine=%s steps=%s summary=%s rows=%s ms=%d",
                request_id,
                payload.get("status"),
                payload.get("code", "-"),
                engine,
                len(payload.get("steps") or []),
                payload.get("summary_engine", "-") if payload.get("summary") else payload.get("summary_code", "-"),
                payload.get("row_count", 0),
                int((time.monotonic() - started) * 1000),
            )
            return reply
        except Exception:  # noqa: BLE001 - jangan bocorkan detail; log tanpa isi pertanyaan
            log.exception("text2sql analyze request=%s gagal tak terduga", request_id)
            return Reply(500, {"status": "error", "code": "internal_error"})

    def ask(self, body: Any) -> Reply:
        started = time.monotonic()
        request_id = "-"
        engine = "-"
        try:
            if not self._enabled:
                return Reply(503, {"status": "unavailable", "code": "text2sql_disabled"})
            try:
                question, scope, request_id = parse_request(body)
            except InvalidRequest:
                return Reply(400, {"status": "invalid", "code": "invalid_request"})
            try:
                answer = self._llm.answer(build_prompt(self._layer, question))
            except LlmUnavailableError:
                return self._done(
                    request_id, started, engine, Reply(503, {"status": "unavailable", "code": "llm_unavailable"})
                )
            engine = answer.engine
            sql = extract_sql(answer.text)
            if not sql:
                return self._done(request_id, started, engine, Reply(200, {"status": "rejected", "code": "llm_no_sql"}))
            try:
                guarded = guard_sql(sql, self._config)
            except SqlGuardError as err:
                return self._done(request_id, started, engine, Reply(200, {"status": "rejected", "code": err.code}))
            try:
                result = self._executor.query(guarded.sql, scope.settings(self._scope_key))
            except ClickHouseError as err:
                code = err.code
                status = 503 if code == "clickhouse_unavailable" else 200
                payload = {"status": "unavailable" if status == 503 else "rejected", "code": code}
                return self._done(request_id, started, engine, Reply(status, payload))
            rows = [list(r) for r in result.rows[: guarded.limit]]
            return self._done(
                request_id,
                started,
                engine,
                Reply(
                    200,
                    {
                        "status": "ok",
                        "sql": guarded.sql,
                        "columns": list(result.columns),
                        "rows": rows,
                        "row_count": len(rows),
                        "truncated": len(result.rows) >= guarded.limit,
                        "engine": engine,
                        "tables": list(guarded.tables),
                    },
                ),
            )
        except Exception:  # noqa: BLE001 - jangan bocorkan detail; log tanpa isi pertanyaan
            log.exception("text2sql request=%s gagal tak terduga", request_id)
            return Reply(500, {"status": "error", "code": "internal_error"})

    @staticmethod
    def _done(request_id: str, started: float, engine: str, reply: Reply) -> Reply:
        log.info(
            "EFN_TEXT2SQL request=%s status=%s code=%s engine=%s rows=%s ms=%d",
            request_id,
            reply.body.get("status"),
            reply.body.get("code", "-"),
            engine,
            reply.body.get("row_count", 0),
            int((time.monotonic() - started) * 1000),
        )
        return reply
