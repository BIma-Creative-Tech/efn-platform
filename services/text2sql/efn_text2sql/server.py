"""Listener HTTPS `text2sql:8443` (stdlib, docs/04 §23.2). `POST /v1/ask` (service key) dan `GET /healthz`."""

from __future__ import annotations

import hmac
import json
import logging
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from efn_text2sql.service import AskService, Reply

log = logging.getLogger(__name__)

MAX_BODY_BYTES = 8 * 1024
SERVICE_KEY_HEADER = "X-EFN-Service-Key"


def make_handler(service: AskService, api_key: str) -> type[BaseHTTPRequestHandler]:
    expected = api_key.encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        server_version = "efn-text2sql"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - tanda tangan stdlib
            # Akses log tanpa query string/body (service.py mencatat ringkasan per request).
            log.debug("%s %s", self.command, self.path.split("?", 1)[0])

        def _send(self, reply: Reply) -> None:
            data = json.dumps(reply.body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(reply.status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 - nama stdlib
            if self.path == "/healthz":
                self._send(Reply(200, {"status": "ok"}))
            else:
                self._send(Reply(404, {"status": "not_found"}))

        def do_POST(self) -> None:  # noqa: N802 - nama stdlib
            routes = {"/v1/ask": service.ask, "/v1/analyze": service.analyze}
            handler = routes.get(self.path)
            if handler is None:
                self._send(Reply(404, {"status": "not_found"}))
                return
            given = (self.headers.get(SERVICE_KEY_HEADER) or "").encode("utf-8")
            if not hmac.compare_digest(given, expected):
                self._send(Reply(401, {"status": "unauthorized"}))
                return
            try:
                length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                length = -1
            if not 0 < length <= MAX_BODY_BYTES:
                self.close_connection = True
                self._send(Reply(400, {"status": "invalid", "code": "invalid_request"}))
                return
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw)
            except ValueError:
                self._send(Reply(400, {"status": "invalid", "code": "invalid_request"}))
                return
            self._send(handler(body))

    return Handler


def build_server(
    service: AskService,
    api_key: str,
    bind: str,
    port: int,
    cert_file: str = "",
    key_file: str = "",
) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((bind, port), make_handler(service, api_key))
    httpd.daemon_threads = True
    if cert_file and key_file:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(cert_file, key_file)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    return httpd
