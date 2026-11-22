"""G57 §1.3 (baru): tagihan vendor berkategori 'Aset Tetap / ...' -> draf aset pool; LVA tidak dikapitalisasi;
reset tagihan; user Billing; cron memposting bulan berjalan."""

from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import new_test_user, tagged

from .common import EfnAssetCommon


@tagged("post_install", "-at_install", "efn")
class TestEfnAssetFromBill(EfnAssetCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Category = cls.env["product.category"]
        cls.categ_root = Category.create({"name": "Aset Tetap uji"})
        cls.categ_machine = Category.create({"name": "Mesin", "parent_id": cls.categ_root.id})
        cls.categ_oven = Category.create({"name": "Oven", "parent_id": cls.categ_machine.id})
        cls.categ_lva = Category.create({"name": "LVA", "parent_id": cls.categ_root.id})
        for categ in (cls.categ_machine, cls.categ_oven):
            categ.with_company(cls.company).property_account_expense_categ_id = cls.asset_account
        cls.group.write({"categ_ids": [Command.set(cls.categ_machine.ids)], "auto_create": "draft"})
        # Kelompok untuk sub-kategori Oven (lebih spesifik dari Mesin). Akar "Aset Tetap uji" (induk LVA) tidak
        # dipetakan, sama dengan G61 (LVA dibebankan).
        cls.group_oven = cls.env["efn.account.asset.group"].create(
            {
                "name": "Oven uji",
                "code": "OVN-T",
                "company_id": cls.company.id,
                "categ_ids": [Command.set(cls.categ_oven.ids)],
                "auto_create": "draft",
            }
        )
        cls.product_oven = cls._efn_product("Oven combi uji", cls.categ_oven)
        cls.product_machine = cls._efn_product("Mixer uji", cls.categ_machine)
        cls.product_lva = cls._efn_product("Blender kecil uji", cls.categ_lva)

    @classmethod
    def _efn_product(cls, name, categ):
        return cls.env["product.product"].create(
            {
                "name": name,
                "type": "consu",
                "categ_id": categ.id,
                "purchase_ok": True,
                "sale_ok": False,
                "supplier_taxes_id": [Command.clear()],
                "taxes_id": [Command.clear()],
            }
        )

    def _bill(self, product, qty=2.0, price=5000.0, move_type="in_invoice"):
        return self.env["account.move"].create(
            {
                "move_type": move_type,
                "partner_id": self.partner_a.id,
                "invoice_date": date(2025, 1, 15),
                "invoice_line_ids": [
                    Command.create(
                        {"product_id": product.id, "quantity": qty, "price_unit": price, "tax_ids": [Command.clear()]}
                    )
                ],
            }
        )

    def _assets_of(self, bill):
        return self.env["efn.account.asset"].sudo().search([("source_move_id", "=", bill.id)])

    def test_bill_creates_one_draft_pool_asset(self):
        bill = self._bill(self.product_machine)
        bill.action_post()
        asset = self._assets_of(bill)
        self.assertEqual(len(asset), 1)
        self.assertEqual(asset.state, "draft")
        self.assertEqual(asset.group_id, self.group)
        self.assertAlmostEqual(asset.quantity, 2.0)
        self.assertAlmostEqual(asset.original_quantity, 2.0)
        self.assertAlmostEqual(asset.acquisition_value, 10000.0, places=2)
        self.assertEqual(asset.acquisition_date, date(2025, 1, 15))
        self.assertEqual(asset.company_id, self.company)
        self.assertEqual(asset.product_id, self.product_machine)
        # Default kelompok diterapkan di server (bukan onchange).
        self.assertEqual(asset.asset_account_id, self.asset_account)
        self.assertEqual(asset.journal_id, self.journal)
        self.assertEqual(asset.useful_life_months, 12)
        self.assertTrue(any(asset.code in str(message.body or "") for message in bill.message_ids))
        # Idempoten.
        bill._efn_asset_create_from_bill()
        self.assertEqual(len(self._assets_of(bill)), 1)

    def test_most_specific_category_wins(self):
        bill = self._bill(self.product_oven, qty=1.0)
        bill.action_post()
        self.assertEqual(self._assets_of(bill).group_id, self.group_oven)
        # Kelompok Oven dimatikan -> jatuh ke leluhur terdekat yang aktif (Mesin).
        self.group_oven.auto_create = "no"
        bill2 = self._bill(self.product_oven, qty=1.0)
        bill2.action_post()
        self.assertEqual(self._assets_of(bill2).group_id, self.group)

    def test_lva_bill_creates_no_asset(self):
        bill = self._bill(self.product_lva)
        bill.action_post()
        self.assertEqual(bill.state, "posted")
        self.assertFalse(self._assets_of(bill))

    def test_vendor_refund_creates_no_asset(self):
        refund = self._bill(self.product_machine, move_type="in_refund")
        refund.action_post()
        self.assertFalse(self._assets_of(refund))

    def test_reset_bill_removes_draft_asset(self):
        bill = self._bill(self.product_machine)
        bill.action_post()
        self.assertTrue(self._assets_of(bill))
        bill.button_draft()
        self.assertFalse(self._assets_of(bill))
        bill.action_post()
        self.assertEqual(len(self._assets_of(bill)), 1)

    def test_reset_bill_with_running_asset_blocked(self):
        bill = self._bill(self.product_machine)
        bill.action_post()
        self._assets_of(bill).action_confirm()
        with self.assertRaises(UserError):
            bill.button_draft()
        with self.assertRaises(UserError):
            bill.button_cancel()
        self.assertEqual(bill.state, "posted")

    def test_billing_user_posts_bill_and_asset_is_created(self):
        billing = new_test_user(
            self.env,
            login="efn_asset_billing",
            groups="base.group_user,account.group_account_invoice",
            company_id=self.company.id,
            company_ids=[Command.set(self.company.ids)],
        )
        bill = self._bill(self.product_machine).with_user(billing)
        bill.action_post()
        self.assertEqual(len(self._assets_of(bill)), 1)
        bill.button_draft()
        self.assertFalse(self._assets_of(bill))

    def test_cron_posts_current_month(self):
        today = fields.Date.context_today(self.env.user)
        bill = self._bill(self.product_machine, qty=1.0, price=1200.0)
        bill.invoice_date = today - relativedelta(months=2)
        bill.action_post()
        asset = self._assets_of(bill)
        asset.action_confirm()
        self.assertEqual(len(asset.depreciation_line_ids), 12)
        self.env["efn.account.asset"]._cron_post_due_depreciation()
        posted = asset.depreciation_line_ids.filtered("posted")
        self.assertEqual(len(posted), 2)
        self.assertTrue(all(line.date <= today for line in posted))
        self.assertTrue(all(line.move_id.state == "posted" for line in posted))
        self.assertAlmostEqual(asset.accumulated_depreciation, 200.0, places=2)
        # Jalan kedua di hari yang sama tidak memposting ulang.
        self.env["efn.account.asset"]._cron_post_due_depreciation()
        self.assertEqual(len(asset.depreciation_line_ids.filtered("posted")), 2)
