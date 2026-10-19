"""ADR-036 §0.8: unduhan XML Coretax = satu-satunya jalur dekripsi lampiran.

Odoo 19.0 `l10n_id_efaktur_coretax/controllers/download_efaktur.py`: route
`/l10n_id_efaktur_coretax/download_attachments/<models("ir.attachment"):attachments>` (`type='http'`, `auth='user'`),
`attachments.check_access('read')`, `assert` res_model dokumen Coretax, satu lampiran -> `raw`, lebih -> zip
(`_build_zip_from_attachments`). Route di-override (route sama, `@http.route()` mewarisi parameter):
- hak: `check_access('read')` + grup `account.group_account_invoice` (Accounting / Billing); semua lampiran wajib
  milik dokumen Coretax (bukan `assert`, yang hilang dengan `python -O`);
- isi didekripsi di server (`_efn_coretax_plain_raw`), zip dibangun dari plaintext di memori;
- jejak: pesan log (tanpa isi) di chatter dokumen + log terstruktur `EFN_PII_KEYRING` event `coretax_download`
  (uid & jumlah saja). `efn_audit` tidak punya operasi "baca"; chatter + log = jejak unduhan.
"""

import io
import json
import logging
import zipfile

from odoo import _, http
from odoo.exceptions import AccessError
from odoo.http import request

from odoo.addons.efn_pii.models import keyring as kr
from odoo.addons.l10n_id_efaktur_coretax.controllers.download_efaktur import EfakturDownloadController, _get_headers

from ..models.ir_attachment import CORETAX_MODEL, XML_MIMETYPE

_logger = logging.getLogger(__name__)
ACCOUNTING_GROUP = "account.group_account_invoice"


class EfnEfakturDownloadController(EfakturDownloadController):
    @http.route()
    def download_invoice_attachments(self, attachments):
        attachments.check_access("read")
        if not request.env.user.has_group(ACCOUNTING_GROUP):
            raise AccessError(_("Hanya user Accounting yang dapat mengunduh XML e-Faktur."))
        if not attachments or any(not att.res_id or att.res_model != CORETAX_MODEL for att in attachments.sudo()):
            raise AccessError(_("Lampiran bukan dokumen e-Faktur Coretax."))
        contents = [(attachment, attachment._efn_coretax_plain_raw()) for attachment in attachments]
        self._efn_log_download(attachments)
        if len(contents) == 1:
            attachment, content = contents[0]
            headers = _get_headers(attachment.name, XML_MIMETYPE, content)
            return request.make_response(content, headers)
        buffer = io.BytesIO()
        used = set()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for attachment, content in contents:
                name = attachment.name or f"efaktur_{attachment.id}.xml"
                if name in used:
                    name = f"{attachment.id}_{name}"
                used.add(name)
                archive.writestr(name, content)
        content = buffer.getvalue()
        headers = _get_headers(_("efaktur") + ".zip", "zip", content)
        return request.make_response(content, headers)

    def _efn_log_download(self, attachments):
        documents = request.env[CORETAX_MODEL].sudo().browse(set(attachments.sudo().mapped("res_id"))).exists()
        for document in documents:
            document._message_log(body=_("XML e-Faktur diunduh (didekripsi untuk user berhak)."))
        payload = {
            "schema_version": 1,
            "event": "coretax_download",
            "db": (request.env.cr.dbname or "")[:64],
            "uid": request.env.uid,
            "count": len(attachments),
            "status": "ok",
        }
        _logger.info("%s %s", kr.LOG_TAG, json.dumps(payload, sort_keys=True))
