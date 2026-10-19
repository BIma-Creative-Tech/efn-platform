"""R-T18 (ADR-036 §0): `account/models/partner.py` mendefinisikan ulang `name = fields.Char(tracking=True)` dan dimuat
setelah `efn_pii` (efn_pii tidak bergantung pada `account`) -> kelas field kembali `fields.Char` biasa dan
`efn_pii` menolak semua penulisan nama partner terlindungi (fail-closed, SEC-137). Modul ini dimuat SETELAH keduanya
dan mengembalikan kelas `EfnPiiChar`; atribut definisi sebelumnya (`tracking`, `index`, `required`) tetap digabung ORM.
Pola sama dengan `efn_pii_base_vat`.
"""

from odoo import models

from odoo.addons.efn_pii.models.fields_pii import EfnPiiChar


class ResPartner(models.Model):
    _inherit = "res.partner"

    name = EfnPiiChar()
