"""Pengaturan per PT E-40 (docs/08 §11.2 butir 1): akun & jurnal selisih pembulatan PPN Coretax.

Nilai = PLACEHOLDER (diisi Finance per PT; tidak ada akun/jurnal yang dibuat otomatis oleh modul ini). Akun khusus
*Selisih Pembulatan Pajak* (tipe beban), BUKAN akun selisih kas ``l10n_id_99900001/2``.
Audit: setiap perubahan dicatat satu baris log ``EFN_CORETAX_ROUNDING_CONFIG`` (uid + nama field, tanpa nilai);
aturan ``efn.audit.rule`` untuk ``res.company`` disediakan efn_setup_audit (belum, lihat README).
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

CONFIG_FIELDS = (
    "efn_tax_rounding_account_id",
    "efn_tax_rounding_gain_account_id",
    "efn_tax_rounding_journal_id",
)
EXPENSE_TYPES = ("expense", "expense_direct_cost")
GAIN_TYPES = ("income", "income_other", "expense", "expense_direct_cost")


class ResCompany(models.Model):
    _inherit = "res.company"

    efn_tax_rounding_account_id = fields.Many2one(
        "account.account",
        string="Akun Selisih Pembulatan Pajak",
        ondelete="restrict",
        copy=False,
        help="E-40: beban selisih PPN Coretax - Odoo (Coretax lebih besar). Akun beban khusus per PT (PLACEHOLDER).",
    )
    efn_tax_rounding_gain_account_id = fields.Many2one(
        "account.account",
        string="Akun Selisih Pembulatan Pajak (lebih)",
        ondelete="restrict",
        copy=False,
        help="E-40: dipakai bila PPN Coretax lebih kecil dari jurnal Odoo. Kosong = akun selisih yang sama.",
    )
    efn_tax_rounding_journal_id = fields.Many2one(
        "account.journal",
        string="Jurnal Selisih Pembulatan Pajak",
        ondelete="restrict",
        copy=False,
        help="E-40: jurnal Miscellaneous tempat jurnal penyesuaian per faktur dibuat.",
    )

    @api.constrains(*CONFIG_FIELDS)
    def _check_efn_tax_rounding_config(self):
        for company in self:
            allowed = company | company.parent_ids
            for fname, types in (
                ("efn_tax_rounding_account_id", EXPENSE_TYPES),
                ("efn_tax_rounding_gain_account_id", GAIN_TYPES),
            ):
                account = company[fname]
                if not account:
                    continue
                if not (account.company_ids & allowed):
                    raise ValidationError(_("Akun selisih pembulatan pajak harus milik company %s.", company.name))
                if account.account_type not in types:
                    raise ValidationError(
                        _(
                            "Akun %(account)s bukan tipe yang diizinkan untuk selisih pembulatan pajak.",
                            account=account.display_name,
                        )
                    )
            journal = company.efn_tax_rounding_journal_id
            if journal and (journal.company_id != company or journal.type != "general"):
                raise ValidationError(
                    _("Jurnal selisih pembulatan pajak harus jurnal Miscellaneous milik company %s.", company.name)
                )

    def write(self, vals):
        touched = sorted(set(vals) & set(CONFIG_FIELDS))
        result = super().write(vals)
        if touched:
            _logger.info(
                "EFN_CORETAX_ROUNDING_CONFIG companies=%s uid=%s fields=%s", self.ids, self.env.uid, ",".join(touched)
            )
        return result

    def _efn_tax_rounding_accounts(self):
        """(akun beban, akun lebih, jurnal) atau ``None`` bila belum diatur lengkap."""
        self.ensure_one()
        if not (self.efn_tax_rounding_account_id and self.efn_tax_rounding_journal_id):
            return None
        return (
            self.efn_tax_rounding_account_id,
            self.efn_tax_rounding_gain_account_id or self.efn_tax_rounding_account_id,
            self.efn_tax_rounding_journal_id,
        )
