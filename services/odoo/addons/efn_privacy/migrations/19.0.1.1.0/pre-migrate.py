"""Migrasi efn_privacy 19.0.1.1.0 — SEC-31 (docs/04 §8.13, review r3).

Versi lama menyimpan `res.partner.efn_erasure_request_id` (request_id penuh) dan nama
`Pelanggan dihapus <8 karakter request_id>` pada partner teranonim, serta request_id di
`privacy.log.execution_details`. Itu menautkan partner teranonim ke `platform.dsar_request`
(dan member_hash-nya). Pre-migrate (sebelum ORM memuat model baru):

1. Kolom baru `efn_erased` diisi TRUE untuk partner yang pernah dianonimkan.
2. request_id dihapus dari partner (kolom lama di-NULL-kan; ORM kemudian men-drop kolomnya
   karena field sudah tidak ada) dan potongan id dihapus dari `name`/`complete_name`, serta
   dari `account_move.invoice_partner_display_name` (salinan nama stored).
3. request_id dihapus dari `privacy_log.execution_details`.

Tidak ada penanda `efn.privacy.erasure.pending` yang dibuat untuk request lama: request yang
sudah anonim tetapi complete-nya belum diterima BFF akan di-complete ulang dengan
`records: 0` (angka informatif saja; tidak memengaruhi status di BFF).
"""

import logging

_logger = logging.getLogger(__name__)

OLD_NAME_RE = r"Pelanggan dihapus [0-9A-Za-z-]{1,8}"
NEW_NAME = "Pelanggan dihapus"


def _column_exists(cr, table, column):
    cr.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
        (table, column),
    )
    return bool(cr.fetchone())


def migrate(cr, version):
    if not version or not _column_exists(cr, "res_partner", "efn_erasure_request_id"):
        return
    cr.execute("ALTER TABLE res_partner ADD COLUMN IF NOT EXISTS efn_erased boolean")
    cr.execute("SELECT id FROM res_partner WHERE efn_erasure_request_id IS NOT NULL")
    ids = [row[0] for row in cr.fetchall()]
    if ids:
        cr.execute(
            """
            UPDATE res_partner
               SET efn_erased = TRUE,
                   efn_erasure_request_id = NULL,
                   name = regexp_replace(name, %(old)s, %(new)s, 'g'),
                   complete_name = regexp_replace(complete_name, %(old)s, %(new)s, 'g')
             WHERE id = ANY(%(ids)s)
            """,
            {"old": OLD_NAME_RE, "new": NEW_NAME, "ids": ids},
        )
        if _column_exists(cr, "account_move", "invoice_partner_display_name"):
            cr.execute(
                """
                UPDATE account_move
                   SET invoice_partner_display_name =
                       regexp_replace(invoice_partner_display_name, %(old)s, %(new)s, 'g')
                 WHERE partner_id = ANY(%(ids)s)
                   AND invoice_partner_display_name ~ %(old)s
                """,
                {"old": OLD_NAME_RE, "new": NEW_NAME, "ids": ids},
            )
    if _column_exists(cr, "privacy_log", "execution_details"):
        cr.execute(
            """
            UPDATE privacy_log
               SET execution_details =
                   regexp_replace(execution_details, 'EFN erasure request [0-9A-Za-z-]+:', 'EFN erasure (UU PDP):')
             WHERE execution_details LIKE 'EFN erasure request %%'
            """
        )
    _logger.info("EFN privacy: SEC-31 migrasi selesai (%d partner teranonim dilepas dari request_id)", len(ids))
