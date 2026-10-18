"""R-T18 (ADR-036): `base_vat` mendefinisikan ulang `vat = fields.Char(inverse=..., store=True)` dan dimuat
setelah `efn_pii` (efn_pii tidak bergantung pada `account`) -> kelas field kembali `fields.Char` biasa dan NPWP
tersimpan plaintext. Modul ini dimuat SETELAH keduanya dan mengembalikan kelas `EfnPiiChar`; atribut definisi
sebelumnya (`inverse` base_vat, `tracking` mail, index base) tetap digabung oleh ORM.
"""

from odoo import models

from odoo.addons.efn_pii.models.fields_pii import EfnPiiChar


class ResPartner(models.Model):
    _inherit = "res.partner"

    vat = EfnPiiChar()
