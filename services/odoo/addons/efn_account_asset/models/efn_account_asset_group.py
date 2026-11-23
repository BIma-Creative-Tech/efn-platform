"""Kelompok aset per PT + seed kelompok F&B/supermarket ke akun l10n_id (G57 §1.4).

Port `custom.fixed.asset.group` (ADR-049). Perubahan: company_id wajib, `categ_ids` (tautan kategori G61) +
`auto_create` (tagihan vendor -> draf aset), seed `_efn_seed_groups` menggantikan seed tenant
`custom_levis_asset_accounts` (akun Erajaya dibuang; akun l10n_id per company, umur PLACEHOLDER Q-G57-2).
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

_logger = logging.getLogger(__name__)

MANAGER_GROUP = "efn_account_asset.group_asset_manager"
USER_GROUP = "efn_account_asset.group_asset_user"
DEPRECIATION_JOURNAL_CODE = "DEPR"
RETAINED_EARNINGS_CODE = "31510010"

# (code, nama, kategori G61 [complete_name] atau None, akun aset, akumulasi, beban, umur bulan PLACEHOLDER)
# Akun l10n_id terverifikasi (docs/fit-gap G57 §1.4). Q-G57-1: satu akun 12210030 untuk mesin/furnitur/peralatan.
# SOFT: belum ada akun aset tak berwujud (G61 A10) -> akun kosong -> action_confirm menolak sampai Finance mengisi.
ASSET_GROUP_SEED = (
    (
        "BLD-IMP",
        "Bangunan & Renovasi Outlet",
        "Aset Tetap / Bangunan & Renovasi",
        "12210010",
        "12281010",
        "67100010",
        60,
    ),
    ("KIT-MCH", "Mesin & Peralatan Dapur", "Aset Tetap / Mesin", "12210030", "12281030", "67100030", 96),
    (
        "FURN",
        "Furnitur & Perlengkapan Tetap",
        "Aset Tetap / Furnitur & Perlengkapan Tetap",
        "12210030",
        "12281030",
        "67100030",
        48,
    ),
    (
        "OFC-OUT",
        "Peralatan Kantor & Outlet",
        "Aset Tetap / Peralatan Kantor & Outlet",
        "12210030",
        "12281030",
        "67100030",
        48,
    ),
    ("SOFT", "Perangkat Lunak", "Aset Tetap / Perangkat Lunak", None, None, None, 48),
    ("VEH", "Kendaraan", None, "12210020", "12281020", "67100020", 96),
)


def efn_check_asset_manager(env):
    """Grup Aset / Manager (superuser/cron lolos). Dipanggil sebelum sudo() apa pun."""
    if not env.su and not env.user.has_group(MANAGER_GROUP):
        raise AccessError(_("Hanya Aset / Manager yang dapat melakukan tindakan ini."))


class EfnAccountAssetGroup(models.Model):
    _name = "efn.account.asset.group"
    _description = "EFN Asset Group"
    _order = "name"
    _check_company_auto = True

    name = fields.Char(required=True)
    code = fields.Char()
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    default_useful_life_months = fields.Integer(string="Umur Default (bulan)", default=60)
    default_asset_account_id = fields.Many2one(
        "account.account", string="Akun Aset Default", check_company=True, help="Akun neraca aset kelompok ini."
    )
    default_depreciation_account_id = fields.Many2one(
        "account.account", string="Akun Akumulasi Penyusutan Default", check_company=True
    )
    default_expense_account_id = fields.Many2one(
        "account.account", string="Akun Beban Penyusutan Default", check_company=True
    )
    default_journal_id = fields.Many2one(
        "account.journal",
        string="Jurnal Penyusutan Default",
        check_company=True,
        domain="[('type', '=', 'general')]",
    )
    default_revaluation_surplus_account_id = fields.Many2one(
        "account.account",
        string="Akun Surplus Revaluasi Default",
        check_company=True,
        help="Akun ekuitas/OCI yang dikredit saat revaluasi naik.",
    )
    default_revaluation_loss_account_id = fields.Many2one(
        "account.account",
        string="Akun Rugi Revaluasi Default",
        check_company=True,
        help="Akun beban L/R untuk revaluasi turun yang tidak tertutup surplus.",
    )
    default_revaluation_income_account_id = fields.Many2one(
        "account.account",
        string="Akun Pendapatan Revaluasi Default",
        check_company=True,
        help="Akun pendapatan L/R saat revaluasi naik membalik rugi revaluasi sebelumnya.",
    )
    default_retained_earnings_account_id = fields.Many2one(
        "account.account",
        string="Akun Laba Ditahan Default",
        check_company=True,
        help="Akun ekuitas tujuan pemindahan surplus revaluasi saat disposal (PSAK 16 / IAS 16.41).",
    )
    categ_ids = fields.Many2many(
        "product.category",
        "efn_asset_group_categ_rel",
        "group_id",
        "categ_id",
        string="Kategori Produk (G61)",
        help="Baris tagihan vendor dengan produk di kategori ini (atau turunannya) membuat draf aset. "
        "Satu kategori hanya boleh di satu kelompok per PT.",
    )
    auto_create = fields.Selection(
        [("no", "Tidak"), ("draft", "Buat draf aset")],
        string="Dari Tagihan Vendor",
        default="draft",
        required=True,
    )

    _code_company_unique = models.Constraint(
        "UNIQUE(code, company_id)",
        "Kode kelompok aset harus unik per PT.",
    )

    @api.constrains("categ_ids", "company_id")
    def _check_categ_unique_per_company(self):
        for group in self.filtered("categ_ids"):
            clash = self.sudo().search(
                [
                    ("id", "!=", group.id),
                    ("company_id", "=", group.company_id.id),
                    ("categ_ids", "in", group.categ_ids.ids),
                ],
                limit=1,
            )
            if clash:
                raise ValidationError(
                    _(
                        "Kategori produk sudah dipakai kelompok aset %(other)s di PT yang sama.",
                        other=clash.display_name,
                    )
                )

    # ------------------------------------------------------------------ seed (G57 §1.4)
    def action_efn_reseed(self):
        """Tombol Asset Manager: pasang ulang kelompok + kategori G61 untuk PT aktif user (idempoten)."""
        efn_check_asset_manager(self.env)
        companies = self.env.companies.filtered(lambda c: c.chart_template == "id")
        count = self.sudo()._efn_seed_groups(companies)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "title": _("Kelompok Aset"),
                "message": _("%(count)s kelompok aset dibuat/dilengkapi.", count=count),
                "sticky": False,
            },
        }

    @api.model
    def _efn_seed_groups(self, companies=None):
        """Upsert kelompok seed untuk setiap PT ber-CoA l10n_id (dipanggil `<function>` tiap install/update)."""
        if companies is None:
            companies = self.env["res.company"].sudo().search([("chart_template", "=", "id")])
        count = 0
        for company in companies:
            count += self._efn_seed_company(company)
        return count

    @api.model
    def _efn_seed_account(self, company, code):
        if not code:
            return self.env["account.account"]
        # Gotcha sumber: `code` company-dependent di 19 -> selalu with_company + company_ids.
        return (
            self.env["account.account"]
            .with_company(company)
            .search([("code", "=", code), ("company_ids", "in", company.ids)], limit=1)
        )

    @api.model
    def _efn_seed_journal(self, company):
        Journal = self.env["account.journal"]
        journal = Journal.search(
            [("code", "=", DEPRECIATION_JOURNAL_CODE), ("type", "=", "general"), ("company_id", "=", company.id)],
            limit=1,
        )
        if not journal:
            journal = Journal.create(
                {
                    "name": _("Penyusutan Aset"),
                    "code": DEPRECIATION_JOURNAL_CODE,
                    "type": "general",
                    "company_id": company.id,
                }
            )
        return journal

    @api.model
    def _efn_seed_company(self, company):
        """Satu PT: hanya mengisi field kosong (koreksi Finance tidak tertimpa). Mengembalikan jumlah kelompok
        yang dibuat/diubah. PT supermarket (retail) tanpa categ_ids (tidak memakai pohon kategori G61)."""
        Category = self.env["product.category"]
        journal = self._efn_seed_journal(company)
        retained = self._efn_seed_account(company, RETAINED_EARNINGS_CODE)
        use_categories = getattr(company, "efn_business_type", False) != "retail"
        missing = []
        changed = 0
        for code, name, categ_name, asset_code, accum_code, expense_code, life in ASSET_GROUP_SEED:
            vals = {
                "default_asset_account_id": self._efn_seed_account(company, asset_code).id,
                "default_depreciation_account_id": self._efn_seed_account(company, accum_code).id,
                "default_expense_account_id": self._efn_seed_account(company, expense_code).id,
                "default_retained_earnings_account_id": retained.id,
                "default_journal_id": journal.id,
            }
            group = self.search([("code", "=", code), ("company_id", "=", company.id)], limit=1)
            if group:
                vals = {key: value for key, value in vals.items() if value and not group[key]}
            else:
                vals = {key: value for key, value in vals.items() if value}
                vals.update({"name": name, "code": code, "company_id": company.id, "default_useful_life_months": life})
            if use_categories and categ_name and not group.categ_ids:
                categ = Category.search([("complete_name", "=", categ_name)], limit=1)
                if not categ:
                    missing.append(categ_name)
                elif not self.search_count([("company_id", "=", company.id), ("categ_ids", "in", categ.ids)]):
                    vals["categ_ids"] = [fields.Command.link(categ.id)]
            if not vals:
                continue
            if group:
                group.write(vals)
            else:
                self.create(vals)
            changed += 1
        if missing:
            _logger.warning(
                "efn_account_asset: company %s - kategori G61 belum ada (seed dilewati): %s",
                company.id,
                ", ".join(missing),
            )
        return changed
