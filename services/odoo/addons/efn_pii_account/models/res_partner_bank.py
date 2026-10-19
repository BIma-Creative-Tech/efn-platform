"""R-T18 + Q-PII9: `account` (19.0) mendefinisikan ulang `acc_number = fields.Char(tracking=True)`
dan `acc_holder_name = fields.Char(tracking=True)` -> kelas `EfnPiiChar` (efn_pii) dipulihkan di sini (atribut lain,
termasuk `tracking`, `required`, `search='_search_acc_number'`, compute holder, tetap digabung ORM).

`_compute_duplicate_bank_partner_ids` (account 19.0) memakai SQL `this.acc_number = other.acc_number`, yang tidak
pernah cocok untuk ciphertext. Untuk rekening terlindungi, duplikat dicari lewat `sanitized_acc_number` (blind index
deterministik) dengan aturan company yang sama dengan account. Rekening terlindungi vs tidak terlindungi dengan nomor
sama tidak terdeteksi sebagai duplikat (kelas berbeda; dicatat README).
"""

from odoo import api, models
from odoo.tools import SQL

from odoo.addons.efn_pii.models.fields_pii import PROTECTED_FIELD, EfnPiiChar


class ResPartnerBank(models.Model):
    _inherit = "res.partner.bank"

    acc_number = EfnPiiChar()
    acc_holder_name = EfnPiiChar()

    @api.depends("acc_number")
    def _compute_duplicate_bank_partner_ids(self):
        super()._compute_duplicate_bank_partner_ids()
        protected = self.filtered(lambda bank: bank._origin.id and bank.sudo()[PROTECTED_FIELD])
        if not protected:
            return
        self.flush_model(["sanitized_acc_number", "partner_id", "company_id", "active", PROTECTED_FIELD])
        rows = self.env.execute_query(
            SQL(
                """SELECT this.id, ARRAY_AGG(other.partner_id)
                     FROM res_partner_bank this
                     JOIN res_partner_bank other ON this.sanitized_acc_number = other.sanitized_acc_number
                      AND this.id != other.id AND other.active = TRUE AND other.efn_pii_protected
                    WHERE this.id = ANY(%(ids)s) AND other.partner_id IS NOT NULL AND this.active = TRUE
                      AND ((this.company_id = other.company_id)
                           OR (this.company_id IS NULL AND other.company_id IS NULL)
                           OR other.company_id IS NULL)
                    GROUP BY this.id""",
                ids=[bank._origin.id for bank in protected],
            )
        )
        id2duplicates = dict(rows)
        for bank in protected:
            duplicates = [pid for pid in id2duplicates.get(bank._origin.id) or [] if pid]
            bank.duplicate_bank_partner_ids = self.env["res.partner"].browse(duplicates) if duplicates else False
