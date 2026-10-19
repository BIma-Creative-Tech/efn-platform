"""ADR-036 §0.8 (18-8, Q-PII12): isi XML Coretax tersimpan terenkripsi.

Odoo 19.0 `l10n_id_efaktur_coretax/models/efaktur_document.py` (WebFetch 19 Okt 2026): `_generate_xml` membuat
`ir.attachment.create({'raw': xml, 'name': 'efaktur_<ts>.xml', 'type': 'binary', 'res_model':
'l10n_id_efaktur_coretax.document', 'res_id': doc.id})` atau `attachment.write({'raw': xml, 'name': ...})` saat
regenerasi, lalu `message_post(attachments=[(name, raw)])`. Lampiran tidak dibaca ulang untuk regenerasi (XML selalu
dirender ulang dari faktur lewat ORM).

Di sini, setiap lampiran ber-`res_model` dokumen Coretax yang menerima `raw`/`datas`:
- `create`: isi dilepas dari vals, lampiran dibuat tanpa isi (id diketahui), lalu isi ditulis sebagai bytes
  `efnpii2:` + base64(blob) dengan AAD `efn:odoo:ir_attachment.raw:<id>:v2` (filestore/db hanya berisi ciphertext;
  plaintext tidak pernah sampai ke penyimpanan). `index_content`/`checksum` dihitung Odoo dari ciphertext.
- `write`: langsung dienkripsi dengan id.
- Isi yang sudah berawalan penanda `efnpii<n>:` dari luar ditolak (tidak bisa memalsukan/menyalin ciphertext).
- Baca terdekripsi hanya lewat `_efn_coretax_plain_raw()` (controller unduhan, grup Accounting). `/web/content`
  menyajikan ciphertext.
Mode terkunci: membuat/regenerasi XML ditolak (UserError), tanpa fallback plaintext.
"""

import base64
from contextlib import contextmanager

from odoo import _, api, models
from odoo.exceptions import ValidationError

from odoo.addons.efn_pii.models import keyring as kr
from odoo.addons.efn_pii.models import pii_model as pm

CORETAX_MODEL = "l10n_id_efaktur_coretax.document"
XML_MIMETYPE = "application/xml"
INTERNAL_KEY = "efn_pii_coretax_internal"


@contextmanager
def _internal(cr):
    """Tulis ciphertext dari dalam modul ini (penanda per cursor, bukan context RPC)."""
    previous = cr.cache.get(INTERNAL_KEY)
    cr.cache[INTERNAL_KEY] = True
    try:
        yield
    finally:
        if previous:
            cr.cache[INTERNAL_KEY] = previous
        else:
            cr.cache.pop(INTERNAL_KEY, None)


def _content_from_vals(vals):
    """(ada_isi, bytes) dari vals `raw`/`datas` (dipop)."""
    has = "raw" in vals or "datas" in vals
    raw = vals.pop("raw", None)
    datas = vals.pop("datas", None)
    if raw is None and datas:
        raw = base64.b64decode(datas)
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    return has, bytes(raw or b"")


def _aad(attachment_id):
    return kr.aad_for("ir_attachment", "raw", attachment_id)


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    @api.model
    def _efn_coretax_check_content(self, content):
        if kr.is_bytes_ciphertext(content) or content[:6] == b"efnpii":
            raise ValidationError(_("Isi lampiran tidak boleh diawali penanda format enkripsi internal."))

    def _efn_coretax_store(self, content):
        """Tulis ``content`` (plaintext) sebagai ciphertext terikat id lampiran ini."""
        self.ensure_one()
        if not content:
            return
        ring = pm.require_keys(self.env)
        blob = ring.encrypt_bytes(content, _aad(self.id))
        with _internal(self.env.cr):
            super(IrAttachment, self.sudo()).write({"raw": blob, "mimetype": self.sudo().mimetype or XML_MIMETYPE})

    def _efn_coretax_plain_raw(self):
        """Isi plaintext lampiran Coretax (hanya untuk pemanggil yang sudah memeriksa hak akses)."""
        self.ensure_one()
        raw = self.sudo().raw or b""
        if kr.is_bytes_ciphertext(raw):
            ring = pm.keyring_for(self.env)
            try:
                return ring.decrypt_bytes(raw, _aad(self.id))
            except kr.PiiError as exc:
                kr.log_alarm("coretax_decrypt", self.env.cr.dbname, exc.code, model="ir.attachment")
                raise ValidationError(_("XML e-Faktur tidak dapat dibuka (kode %s).", exc.code)) from None
        if raw:
            # lampiran lama sebelum modul dipasang: tetap disajikan (tanpa kehilangan data) + alarm sampai dimigrasi
            kr.log_alarm("coretax_read", self.env.cr.dbname, "plaintext_in_protected", model="ir.attachment", count=1)
        return raw

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.cr.cache.get(INTERNAL_KEY):
            return super().create(vals_list)
        vals_list = [dict(vals) for vals in vals_list]
        pending = {}
        for index, vals in enumerate(vals_list):
            if vals.get("res_model") != CORETAX_MODEL:
                continue
            has, content = _content_from_vals(vals)
            if not has:
                continue
            self._efn_coretax_check_content(content)
            vals.setdefault("mimetype", XML_MIMETYPE)
            pending[index] = content
        if pending:
            pm.require_keys(self.env)
        records = super().create(vals_list)
        for index, content in pending.items():
            records[index]._efn_coretax_store(content)
        return records

    def write(self, vals):
        if self.env.cr.cache.get(INTERNAL_KEY) or not ("raw" in vals or "datas" in vals):
            return super().write(vals)
        target = vals.get("res_model")
        coretax = self.sudo().filtered(lambda att: CORETAX_MODEL in (att.res_model, target))
        if not coretax:
            return super().write(vals)
        coretax = self.browse(coretax.ids)
        others = self - coretax
        if others:
            super(IrAttachment, others).write(vals)
        rest = dict(vals)
        _has, content = _content_from_vals(rest)
        self._efn_coretax_check_content(content)
        pm.require_keys(self.env)
        if rest:
            super(IrAttachment, coretax).write(rest)
        for attachment in coretax:
            attachment._efn_coretax_store(content)
        return True

    @api.model
    def _efn_coretax_encrypt_legacy(self):
        """Lampiran Coretax lama berisi plaintext -> efnpii2. :returns: jumlah."""
        attachments = self.sudo().with_context(active_test=False).search([("res_model", "=", CORETAX_MODEL)])
        count = 0
        for attachment in attachments:
            raw = attachment.raw or b""
            if raw and not kr.is_bytes_ciphertext(raw):
                attachment._efn_coretax_store(raw)
                count += 1
        return count
