import logging

from odoo.tools import SQL

from odoo.addons.efn_pii.models import keyring as kr

from . import models

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    """Order lama (sebelum modul dipasang) dengan email/HP plaintext -> efnpii2 bila kunci tersedia.

    Tanpa kunci: dibiarkan + WARNING (jumlah saja). Jalankan ulang dari shell setelah kunci dipasang:
    `env['pos.order'].sudo()._efn_pii_encrypt_legacy(); env.cr.commit()`.
    """
    marker = kr.PREFIX_V2 + "%"
    env.cr.execute(
        SQL(
            "SELECT count(*) FROM pos_order "
            "WHERE (email <> '' AND email NOT LIKE %s) OR (mobile <> '' AND mobile NOT LIKE %s)",
            marker,
            marker,
        )
    )
    pending = env.cr.fetchone()[0]
    if not pending:
        return
    if not kr.get_keyring(env.cr.dbname).can_encrypt:
        _logger.warning("%s pos.order plaintext lama=%d (kunci tidak tersedia, belum dienkripsi)", kr.LOG_TAG, pending)
        return
    env["pos.order"].sudo()._efn_pii_encrypt_legacy()
