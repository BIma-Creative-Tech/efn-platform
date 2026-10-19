import logging

from odoo.tools import SQL

from odoo.addons.efn_pii.models import keyring as kr

from . import models

_logger = logging.getLogger(__name__)

TABLE_COLUMNS = {
    "hr_employee": models.hr_employee.EMPLOYEE_PII_FIELDS,
    "hr_version": models.hr_version.VERSION_PII_FIELDS,
}


def _pending(env):
    total = 0
    for table, columns in TABLE_COLUMNS.items():
        env.cr.execute(
            SQL(
                "SELECT count(*) FROM %s WHERE %s",
                SQL.identifier(table),
                SQL(" OR ").join(
                    SQL("(%s <> '' AND %s NOT LIKE %s)", SQL.identifier(c), SQL.identifier(c), kr.PREFIX_V2 + "%")
                    for c in columns
                ),
            )
        )
        total += env.cr.fetchone()[0]
    return total


def post_init_hook(env):
    """Karyawan lama (sebelum modul dipasang) dengan data pribadi plaintext -> efnpii2 + redaksi tracking lama.

    Tanpa kunci: dibiarkan + WARNING (jumlah saja). Jalankan ulang dari shell setelah kunci dipasang:
    `env['hr.employee'].sudo()._efn_pii_encrypt_legacy(); env['hr.version'].sudo()._efn_pii_encrypt_legacy();
    env.cr.commit()`.
    """
    pending = _pending(env)
    if not pending:
        return
    if not kr.get_keyring(env.cr.dbname).can_encrypt:
        _logger.warning("%s hr plaintext lama=%d (kunci tidak tersedia, belum dienkripsi)", kr.LOG_TAG, pending)
        return
    env["hr.employee"].sudo()._efn_pii_encrypt_legacy()
    env["hr.version"].sudo()._efn_pii_encrypt_legacy()
