"""G59-A: jenis PPh + kode objek pajak e-Bupot pada `account.tax` (D-1: PPh = pajak negatif CE).

Bukan port kode `custom_tax_id` (engine aturan withholding di-drop, ADR-049 + spesifikasi §G59-A); hanya ide "kode
objek per jenis PPh" yang dipakai. Tarif tidak pernah ditulis di kode/data (ADR-027): produksi diisi Finance.
"""

import logging
import re

from odoo import Command, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

BUPOT_OBJECT_CODE_RE = re.compile(r"^\d{2}-\d{3}-\d{2}$")
DEMO_COUNTRY_GROUP = "Luar Indonesia (EFN PLACEHOLDER)"
DEMO_TAX_GROUP = "PPh (PLACEHOLDER)"


class AccountTax(models.Model):
    _inherit = "account.tax"

    efn_pph_kind = fields.Selection(
        [
            ("pph_23", "PPh 23"),
            ("pph_4_2", "PPh 4(2)"),
            ("pph_26", "PPh 26"),
            ("pph_22", "PPh 22"),
            ("pph_15", "PPh 15"),
        ],
        string="Jenis PPh",
        help="Isi bila pajak ini PPh yang dipotong (tarif negatif). Kosong = bukan PPh.",
    )
    efn_bupot_object_code = fields.Char(
        string="Kode Objek Pajak (e-Bupot)",
        help="Format NN-NNN-NN, mis. 24-104-01. Wajib untuk pajak PPh aktif; dikonfirmasi konsultan pajak.",
    )

    @api.constrains("efn_pph_kind", "efn_bupot_object_code", "amount", "amount_type", "active")
    def _check_efn_pph(self):
        for tax in self:
            code = tax.efn_bupot_object_code
            if code and not BUPOT_OBJECT_CODE_RE.match(code):
                raise ValidationError(
                    self.env._("Kode objek pajak '%(code)s' harus berformat NN-NNN-NN (mis. 24-104-01).", code=code)
                )
            if not tax.efn_pph_kind:
                continue
            if tax.amount_type != "percent" or tax.amount >= 0:
                raise ValidationError(
                    self.env._(
                        "Pajak PPh '%(tax)s' harus bertipe persentase dengan tarif negatif (dipotong dari tagihan).",
                        tax=tax.name,
                    )
                )
            if tax.active and not code:
                raise ValidationError(
                    self.env._("Pajak PPh '%(tax)s' wajib punya kode objek pajak e-Bupot.", tax=tax.name)
                )

    # ------------------------------------------------------------------ demo (demo/account_tax_pph_demo.xml)
    @api.model
    def _efn_pph_load_demo(self):
        """Pajak PPh + fiscal position PLACEHOLDER untuk setiap PT ber-CoA l10n_id. Idempoten per nama."""
        foreign = self.env["res.country.group"].search([("name", "=", DEMO_COUNTRY_GROUP)], limit=1)
        if not foreign:
            foreign = self.env["res.country.group"].create(
                {
                    "name": DEMO_COUNTRY_GROUP,
                    "country_ids": [Command.set(self.env["res.country"].search([("code", "!=", "ID")]).ids)],
                }
            )
        for company in self.env["res.company"].search([("chart_template", "=", "id")]):
            chart = self.env["account.chart.template"].with_company(company)
            accounts = {
                code: chart.ref(f"l10n_id_{code}", raise_if_not_found=False)
                for code in ("21210030", "21210050", "21210060")
            }
            if not all(accounts.values()):
                _logger.info("EFN_PPH_DEMO company=%s skipped: l10n_id payable accounts not found", company.id)
                continue
            self._efn_pph_demo_company(company, accounts, foreign)

    @api.model
    def _efn_pph_demo_company(self, company, accounts, foreign_group):
        group = self.env["account.tax.group"].search(
            [("company_id", "=", company.id), ("name", "=", DEMO_TAX_GROUP)], limit=1
        ) or self.env["account.tax.group"].create(
            {"name": DEMO_TAX_GROUP, "company_id": company.id, "country_id": self.env.ref("base.id").id}
        )

        def tax(name, amount, kind, code, account):
            existing = self.search([("company_id", "=", company.id), ("name", "=", name)], limit=1)
            if existing:
                return existing

            def repartition():
                return [
                    Command.create({"repartition_type": "base"}),
                    Command.create({"repartition_type": "tax", "account_id": account.id}),
                ]

            return self.create(
                {
                    "name": name,
                    "company_id": company.id,
                    "type_tax_use": "purchase",
                    "amount_type": "percent",
                    "amount": amount,
                    "tax_group_id": group.id,
                    "efn_pph_kind": kind,
                    "efn_bupot_object_code": code,
                    "invoice_repartition_line_ids": repartition(),
                    "refund_repartition_line_ids": repartition(),
                }
            )

        pph23 = tax("PPh 23 Jasa 2% (PLACEHOLDER)", -2.0, "pph_23", "24-104-01", accounts["21210030"])
        pph23_no_npwp = tax(
            "PPh 23 Jasa 4% tanpa NPWP (PLACEHOLDER)", -4.0, "pph_23", "24-104-01", accounts["21210030"]
        )
        tax("PPh 4(2) Sewa 10% (PLACEHOLDER)", -10.0, "pph_4_2", "28-403-01", accounts["21210050"])
        pph26 = tax("PPh 26 20% (PLACEHOLDER)", -20.0, "pph_26", "27-100-99", accounts["21210060"])

        FiscalPosition = self.env["account.fiscal.position"]
        for name, vals, dest in (
            ("Vendor Luar Negeri (PLACEHOLDER)", {"auto_apply": True, "country_group_id": foreign_group.id}, pph26),
            ("Vendor tanpa NPWP (PLACEHOLDER)", {"auto_apply": False}, pph23_no_npwp),
        ):
            position = FiscalPosition.search([("company_id", "=", company.id), ("name", "=", name)], limit=1)
            if not position:
                position = FiscalPosition.create({"name": name, "company_id": company.id, "sequence": 90, **vals})
            # CE 19: pemetaan pajak ada di pajak tujuan (fiscal_position_ids + original_tax_ids = "Replaces").
            dest.write(
                {"fiscal_position_ids": [Command.link(position.id)], "original_tax_ids": [Command.link(pph23.id)]}
            )
        _logger.info("EFN_PPH_DEMO company=%s loaded", company.id)
