"""Hook cakupan supermarket untuk token eit2 (G47, docs/04 §28.13.2-§28.13.3, ADR-041 §3.4 Proposed).

`efn_insight` terpasang juga di DB/instalasi F&B tanpa modul supermarket, jadi di sini cakupan SM selalu kosong.
`efn_insight_sm` (modul jembatan, auto_install) menimpa metode ini. Metode privat (awalan `_`): tidak bisa dipanggil
lewat RPC (SEC-58). Kill switch `INSIGHT_SM_ENABLED` dicek di controller (bukan di sini) agar modul penimpa tidak bisa
melewatinya.
"""

from odoo import models


def empty_sm_scope():
    """Bentuk nilai kembali hook. `no_outlet` = user ber-persona store tanpa outlet yang sah (halaman info)."""
    return {"personas": [], "codes": [], "cids": [], "wids": [], "cats": [], "no_outlet": False}


class ResUsers(models.Model):
    _inherit = "res.users"

    def _efn_insight_sm_scope(self):
        """Cakupan supermarket user (satu record): personas, codes, cids, wids, cats (list int/str terurut)."""
        self.ensure_one()
        return empty_sm_scope()
