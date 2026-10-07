"""Redaksi traceback di respons error JSON-RPC / ``/json/2`` (SEC-22, sisa Gelombang 12b).

Odoo 19 ``odoo.http.serialize_exception`` selalu menaruh traceback lengkap di ``data.debug``
(JSON-RPC) atau ``debug`` (``/json/2``) dan tidak ada opsi konfigurasi untuk mematikannya
(lihat ``docs/runbooks/odoo-access-changes.md`` §2). Override di sini adalah titik resmi
yang paling kecil: ``ir.http._handle_error`` (dipanggil ``Request._update_served_exception``
untuk setiap exception pada request ber-database) — respons yang sudah dibangun dispatcher
hanya di-post-process, tidak ada monkeypatch ``odoo.http``.

Aturan (hanya untuk dispatcher ``jsonrpc`` dan ``json2``; route ``http`` tidak disentuh):
- User ``base.group_system`` (dari sesi, atau API key untuk ``/json/2``) → respons asli
  (debug tetap untuk troubleshooting).
- Selain itu (user internal, POS, portal, publik, tanpa sesi):
  - ``debug`` dikosongkan (tetap string, karena web client & POS memakainya sebagai string).
  - Exception bisnis (``UserError`` dan turunannya: ``ValidationError``, ``AccessError``,
    ``MissingError``, ``AccessDenied``; ``RedirectWarning``; ``SessionExpiredException``;
    ``HTTPException`` werkzeug) → ``name``/``message``/``arguments``/``context`` tetap,
    supaya dialog web client & POS tidak berubah.
  - Exception tak terduga (``KeyError``, error psycopg2, dsb.) → ``message`` & ``arguments``
    diganti teks generik, ``name`` → ``Exception``, ``context`` → ``{}``; nama file, SQL, dan
    argumen internal tidak keluar.
- Gagal menentukan hak user → diperlakukan non-admin (fail closed).
- Log server tidak berubah: traceback penuh tetap dicatat ``odoo.http`` (Loki).

Batasan: request **tanpa database** (``_serve_nodb``, mis. tanpa ``dbfilter``) tidak melewati
``ir.http`` sehingga tidak tertangani di sini; produksi memakai satu DB dengan dbfilter.
"""

import json
import logging

from werkzeug.exceptions import HTTPException

from odoo import models
from odoo.exceptions import RedirectWarning, UserError
from odoo.http import Response, SessionExpiredException, request

_logger = logging.getLogger(__name__)

REDACTED_ROUTING_TYPES = ("jsonrpc", "json2")
GENERIC_ERROR_MESSAGE = "Internal server error. Please contact your administrator."
BUSINESS_EXCEPTIONS = (UserError, RedirectWarning, SessionExpiredException, HTTPException)


def redact_error_data(data, exception):
    """Kembalikan salinan dict hasil ``serialize_exception`` tanpa detail internal."""
    redacted = dict(data)
    redacted["debug"] = ""
    if not isinstance(exception, BUSINESS_EXCEPTIONS):
        redacted["name"] = "Exception"
        redacted["message"] = GENERIC_ERROR_MESSAGE
        redacted["arguments"] = [GENERIC_ERROR_MESSAGE]
        redacted["context"] = {}
    return redacted


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _handle_error(cls, exception):
        response = super()._handle_error(exception)
        dispatcher = getattr(request, "dispatcher", None)
        if getattr(dispatcher, "routing_type", None) not in REDACTED_ROUTING_TYPES:
            return response
        if not isinstance(response, Response) or response.mimetype != "application/json":
            return response
        if cls._efn_error_debug_allowed():
            return response
        try:
            payload = json.loads(response.get_data())
            if dispatcher.routing_type == "jsonrpc":
                error = payload.get("error")
                if isinstance(error, dict) and isinstance(error.get("data"), dict):
                    error["data"] = redact_error_data(error["data"], exception)
            elif isinstance(payload, dict) and "debug" in payload:
                payload = redact_error_data(payload, exception)
            response.set_data(json.dumps(payload, ensure_ascii=False, default=str))
        except Exception:  # noqa: BLE001 - fail closed: jangan pernah kirim traceback
            _logger.exception("EFN: gagal meredaksi respons error JSON; dikirim respons generik")
            response.set_data(json.dumps(cls._efn_generic_error_payload(dispatcher)))
        return response

    @classmethod
    def _efn_generic_error_payload(cls, dispatcher):
        data = {
            "name": "Exception",
            "message": GENERIC_ERROR_MESSAGE,
            "arguments": [GENERIC_ERROR_MESSAGE],
            "context": {},
            "debug": "",
        }
        if dispatcher.routing_type == "jsonrpc":
            return {
                "jsonrpc": "2.0",
                "id": getattr(dispatcher, "request_id", None),
                "error": {"code": 0, "message": "Odoo Server Error", "data": data},
            }
        return data

    @classmethod
    def _efn_error_debug_allowed(cls):
        """True hanya untuk admin sistem. Memakai uid sesi (bukan ``request.env.uid`` yang bisa
        dinaikkan controller ke superuser); untuk ``/json/2`` dengan API key, uid dari key."""
        try:
            uid = request.session.uid
            if not uid and request.dispatcher.routing_type == "json2":
                uid = request.env.uid
            if not uid:
                return False
            user = request.env["res.users"].sudo().browse(uid)
            return bool(user.exists() and user._has_group("base.group_system"))
        except Exception:  # noqa: BLE001 - fail closed
            _logger.debug("EFN: cek group_system gagal saat menangani error", exc_info=True)
            return False
