"""Relokasi xmlid grup persona efn_insight_sm -> efn_insight (G52, docs/04 §28.15.2, L-239).

Dipanggil pre-migrate `efn_insight` 19.0.3.0.0 DAN `efn_insight_sm` 19.0.2.0.0 (SQL sama, idempoten): record grup dan
privilege tidak dibuat ulang, hanya `ir_model_data.module` berpindah, sehingga id grup dan keanggotaan user tetap.
Tanpa langkah ini, upgrade `efn_insight_sm` akan menghapus grup yang tidak lagi dideklarasikannya (`_process_end`)
beserta keanggotaannya. `group_insight_kpi_admin` + privilege-nya tetap di efn_insight_sm.

Referensi konsep: OCA openupgradelib `rename_xmlids` (tidak dipakai/disalin, ADR-031).
"""

import logging

_logger = logging.getLogger(__name__)

PERSONAS = ("category", "dc", "exec", "marketing", "store")
GROUP_NAMES = tuple(f"group_insight_{p}" for p in PERSONAS)
PRIVILEGE_NAMES = tuple(f"res_groups_privilege_insight_sm_{p}" for p in PERSONAS)
MOVED_NAMES = GROUP_NAMES + PRIVILEGE_NAMES


def rename_persona_xmlids(cr):
    """`UPDATE ir_model_data SET module='efn_insight'` untuk 5 grup + 5 privilege. Mengembalikan jumlah baris.

    Idempoten: baris yang sudah pindah tidak cocok lagi; `NOT EXISTS` mencegah bentrok unik (module, name) bila xmlid
    efn_insight dengan nama sama sudah ada.
    """
    cr.execute(
        """
        UPDATE ir_model_data d
           SET module = 'efn_insight'
         WHERE d.module = 'efn_insight_sm'
           AND d.model IN ('res.groups', 'res.groups.privilege')
           AND d.name IN %s
           AND NOT EXISTS (
               SELECT 1 FROM ir_model_data o WHERE o.module = 'efn_insight' AND o.name = d.name
           )
        """,
        (MOVED_NAMES,),
    )
    moved = cr.rowcount
    _logger.info("efn_insight persona xmlid relocation: %s baris dipindah ke efn_insight", moved)
    return moved
