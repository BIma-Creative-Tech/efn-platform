"""G57 §1.6/§1.8: akses manager di server, isolasi multi-company, seed kelompok tanpa menyilangkan akun antar-PT."""

from datetime import date

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged

from .common import EfnAssetCommon

SEED_ACCOUNTS = (
    ("12210010", "asset_fixed"),
    ("12210020", "asset_fixed"),
    ("12210030", "asset_fixed"),
    ("12281010", "asset_fixed"),
    ("12281020", "asset_fixed"),
    ("12281030", "asset_fixed"),
    ("67100010", "expense"),
    ("67100020", "expense"),
    ("67100030", "expense"),
    ("31510010", "equity"),
)


@tagged("post_install", "-at_install", "efn")
class TestEfnAssetSecurity(EfnAssetCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_b = cls.setup_other_company(name="PT B uji aset")["company"]
        cls.env.user.sudo().company_ids |= cls.company_b
        cls.asset_user = new_test_user(
            cls.env,
            login="efn_asset_user_a",
            groups="base.group_user,efn_account_asset.group_asset_user",
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )
        cls.manager_a = new_test_user(
            cls.env,
            login="efn_asset_manager_a",
            groups="base.group_user,account.group_account_manager,efn_account_asset.group_asset_manager",
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )

    def _asset_b(self):
        return (
            self.env["efn.account.asset"]
            .sudo()
            .create(
                {
                    "name": "Aset PT B",
                    "company_id": self.company_b.id,
                    "acquisition_date": date(2025, 1, 1),
                    "acquisition_value": 1000.0,
                }
            )
        )

    def test_non_manager_cannot_confirm_via_rpc(self):
        asset = self._make_asset()
        with self.assertRaises(AccessError):
            asset.with_user(self.asset_user).action_confirm()
        with self.assertRaises(AccessError):
            asset.with_user(self.asset_user).action_open_dispose_wizard()
        asset.action_confirm()
        line = asset.depreciation_line_ids[:1]
        with self.assertRaises(AccessError):
            line.with_user(self.asset_user).action_post_now()
        with self.assertRaises(AccessError):
            self.env["efn.account.asset.group"].with_user(self.asset_user).action_efn_reseed()

    def test_sec_g57_02_user_cannot_write_system_fields(self):
        asset = self._make_asset()
        other = self._make_asset(name="Lain")
        as_user = asset.with_user(self.asset_user)
        for vals in (
            {"state": "running"},
            {"opening_accumulated_depreciation": 5000.0},
            {"revaluation_value": 1000.0},
            {"merged_into_id": other.id},
            {"source_move_line_id": 1},  # ditolak sebelum FK diperiksa
        ):
            with self.assertRaises(AccessError):
                as_user.write(vals)
        self.assertEqual(asset.state, "draft")
        # sudo() saja (tanpa jalur _efn_system modul) juga ditolak.
        with self.assertRaises(AccessError):
            asset.sudo().write({"state": "running"})

    def test_sec_g57_02_user_cannot_create_with_system_fields(self):
        Asset = self.env["efn.account.asset"].with_user(self.asset_user)
        base = {
            "name": "RPC",
            "company_id": self.company.id,
            "acquisition_date": date(2025, 1, 1),
            "acquisition_value": 1000.0,
        }
        for extra in ({"state": "running"}, {"disposal_value": 1.0}, {"retired_cost": 1.0}):
            with self.assertRaises(AccessError):
                Asset.create(dict(base, **extra))
        # Nilai default (draft / nol) tetap diterima, sebagaimana dikirim klien web.
        asset = Asset.create(dict(base, state="draft", revaluation_value=0.0))
        self.assertEqual(asset.state, "draft")

    def test_user_can_edit_draft_but_not_running_values(self):
        asset = self._make_asset()
        asset.with_user(self.asset_user).write({"acquisition_value": 24000.0})
        self.assertAlmostEqual(asset.acquisition_value, 24000.0)
        asset.action_confirm()
        asset.with_user(self.asset_user).write({"name": "Ganti nama"})
        with self.assertRaises(UserError):
            asset.with_user(self.asset_user).write({"acquisition_value": 1.0})
        # Riwayat revaluasi hanya dibuat wizard (bukan user).
        with self.assertRaises(AccessError):
            self.env["efn.account.asset.revaluation"].with_user(self.asset_user).create(
                {"asset_id": asset.id, "revaluation_date": date(2025, 2, 1)}
            )

    def test_company_isolation(self):
        asset_a = self._make_asset()
        asset_b = self._asset_b()
        visible = self.env["efn.account.asset"].with_user(self.asset_user).search([])
        self.assertIn(asset_a, visible)
        self.assertNotIn(asset_b, visible)
        with self.assertRaises(AccessError):
            asset_b.with_user(self.manager_a).action_confirm()
        with self.assertRaises(AccessError):
            asset_b.with_user(self.asset_user).read(["name"])

    def test_bill_in_other_company_uses_own_groups(self):
        # Kelompok PT A tidak dipakai untuk tagihan PT B.
        categ = self.env["product.category"].create({"name": "Mesin isolasi uji"})
        self.group.categ_ids = [Command.set(categ.ids)]
        product = self.env["product.product"].create(
            {"name": "Mesin PT B", "type": "consu", "categ_id": categ.id, "supplier_taxes_id": [Command.clear()]}
        )
        bill = (
            self.env["account.move"]
            .with_company(self.company_b)
            .create(
                {
                    "move_type": "in_invoice",
                    "company_id": self.company_b.id,
                    "partner_id": self.partner_a.id,
                    "invoice_date": date(2025, 1, 15),
                    "invoice_line_ids": [
                        Command.create(
                            {"product_id": product.id, "quantity": 1, "price_unit": 100, "tax_ids": [Command.clear()]}
                        )
                    ],
                }
            )
        )
        bill.action_post()
        self.assertFalse(self.env["efn.account.asset"].sudo().search([("source_move_id", "=", bill.id)]))

    def _seed_accounts(self, company):
        return {code: self._efn_account(code, f"Seed {code}", kind, company=company) for code, kind in SEED_ACCOUNTS}

    def test_seed_groups_per_company_no_cross_accounts(self):
        Group = self.env["efn.account.asset.group"]
        categ_root = self.env["product.category"].create({"name": "Aset Tetap"})
        categ_machine = self.env["product.category"].create({"name": "Mesin", "parent_id": categ_root.id})
        company_c = self.setup_other_company(name="PT C uji seed")["company"]
        self.env.user.sudo().company_ids |= company_c
        acc_b = self._seed_accounts(self.company_b)
        acc_c = self._seed_accounts(company_c)
        Group._efn_seed_company(self.company_b)
        Group._efn_seed_company(company_c)
        for company, accounts in ((self.company_b, acc_b), (company_c, acc_c)):
            groups = Group.search([("company_id", "=", company.id)])
            by_code = {group.code: group for group in groups}
            self.assertLessEqual({"BLD-IMP", "KIT-MCH", "FURN", "OFC-OUT", "SOFT", "VEH"}, set(by_code))
            kit = by_code["KIT-MCH"]
            self.assertEqual(kit.default_asset_account_id, accounts["12210030"])
            self.assertEqual(kit.default_depreciation_account_id, accounts["12281030"])
            self.assertEqual(kit.default_expense_account_id, accounts["67100030"])
            self.assertEqual(kit.default_retained_earnings_account_id, accounts["31510010"])
            self.assertEqual(kit.default_useful_life_months, 96)
            self.assertEqual(kit.default_journal_id.code, "DEPR")
            self.assertEqual(kit.default_journal_id.company_id, company)
            self.assertEqual(kit.categ_ids.mapped("complete_name"), [categ_machine.complete_name])
            self.assertEqual(by_code["VEH"].default_asset_account_id, accounts["12210020"])
            self.assertFalse(by_code["SOFT"].default_asset_account_id)
            self.assertEqual(by_code["BLD-IMP"].default_useful_life_months, 60)
        # Idempoten + tidak menimpa koreksi Finance.
        kit_b = Group.search([("company_id", "=", self.company_b.id), ("code", "=", "KIT-MCH")])
        kit_b.default_useful_life_months = 120
        self.assertEqual(Group._efn_seed_company(self.company_b), 0)
        self.assertEqual(kit_b.default_useful_life_months, 120)
        # Perangkat Lunak tanpa akun -> konfirmasi diblokir (A10).
        soft = Group.search([("company_id", "=", self.company_b.id), ("code", "=", "SOFT")])
        asset = (
            self.env["efn.account.asset"]
            .with_company(self.company_b)
            .create(
                {
                    "name": "Lisensi POS",
                    "company_id": self.company_b.id,
                    "group_id": soft.id,
                    "acquisition_date": date(2025, 1, 1),
                    "acquisition_value": 4800.0,
                }
            )
        )
        with self.assertRaises(UserError):
            asset.action_confirm()
