"""ADR-036 §0.8: XML Coretax tidak lagi dilampirkan ke chatter.

`_generate_xml` (19.0) memanggil `self.message_post(body=..., attachments=[(attachment.name, attachment.raw)])`, yang
membuat salinan lampiran kedua. Selama `_generate_xml` berjalan (penanda per cursor), `_process_attachments_for_post`
(mail.thread 19.0: `(attachments, attachment_ids, message_values)`) menerima daftar `attachments` kosong, sehingga
pesan "e-Faktur report has been generated" tetap tercatat tanpa lampiran. Unduhan hanya lewat controller Coretax.
"""

from contextlib import contextmanager

from odoo import models

NO_CHATTER_KEY = "efn_pii_coretax_no_chatter_attachment"


@contextmanager
def _no_chatter_attachments(cr):
    previous = cr.cache.get(NO_CHATTER_KEY)
    cr.cache[NO_CHATTER_KEY] = True
    try:
        yield
    finally:
        if previous:
            cr.cache[NO_CHATTER_KEY] = previous
        else:
            cr.cache.pop(NO_CHATTER_KEY, None)


class EfakturDocument(models.Model):
    _inherit = "l10n_id_efaktur_coretax.document"

    def _generate_xml(self, regenerate=False):
        with _no_chatter_attachments(self.env.cr):
            return super()._generate_xml(regenerate=regenerate)

    def _process_attachments_for_post(self, attachments, attachment_ids, message_values):
        if self.env.cr.cache.get(NO_CHATTER_KEY):
            attachments = []
        return super()._process_attachments_for_post(attachments, attachment_ids, message_values)
