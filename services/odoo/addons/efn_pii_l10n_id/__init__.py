import logging

from odoo.tools import SQL

from odoo.addons.efn_pii.models import keyring as kr

from . import controllers, models
from .models.ir_attachment import CORETAX_MODEL

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    """DB lama: NIK/nomor dokumen partner terlindungi & XML Coretax yang sudah ada -> efnpii2.

    Tanpa kunci: dibiarkan + WARNING (jumlah saja). Jalankan ulang dari shell setelah kunci dipasang:
    `env['res.partner'].sudo()._efn_pii_migrate(); env['ir.attachment'].sudo()._efn_coretax_encrypt_legacy();
    env.cr.commit()`.
    """
    env.cr.execute(
        SQL(
            """SELECT
                 (SELECT count(*) FROM res_partner WHERE efn_pii_protected
                     AND ((l10n_id_nik <> '' AND l10n_id_nik NOT LIKE %s)
                          OR (l10n_id_buyer_document_number <> '' AND l10n_id_buyer_document_number NOT LIKE %s))),
                 (SELECT count(*) FROM ir_attachment WHERE res_model = %s)""",
            kr.PREFIX_V2 + "%",
            kr.PREFIX_V2 + "%",
            CORETAX_MODEL,
        )
    )
    partners, attachments = env.cr.fetchone()
    if not (partners or attachments):
        return
    if not kr.get_keyring(env.cr.dbname).can_encrypt:
        _logger.warning(
            "%s l10n_id plaintext lama partner=%d lampiran_coretax=%d (kunci tidak tersedia)",
            kr.LOG_TAG,
            partners,
            attachments,
        )
        return
    if partners:
        env["res.partner"].sudo()._efn_pii_migrate()
    if attachments:
        env["ir.attachment"].sudo()._efn_coretax_encrypt_legacy()
