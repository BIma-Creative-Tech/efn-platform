"""Port tests/test_quantity_asset.py (odoo-platform, ADR-049): aset pool 5 unit, pelepasan sebagian, penggabungan."""

from datetime import date

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import EfnAssetCommon


@tagged("post_install", "-at_install", "efn")
class TestEfnPooledAsset(EfnAssetCommon):
    def _make_pool(self, quantity=5.0, value=5000.0):
        """5 unit @1.000, disusutkan 10 bulan = 500/bulan."""
        return self._make_asset(name="Tempat sampah", quantity=quantity, acquisition_value=value, useful_life_months=10)

    def _retire(self, asset, quantity=1.0, proceeds=0.0, on=date(2025, 3, 31)):
        return self.env["efn.account.asset.partial.disposal.wizard"].create(
            {
                "asset_id": asset.id,
                "quantity": quantity,
                "disposal_date": on,
                "reason": "scrap",
                "proceeds": proceeds,
                "loss_account_id": self.loss_account.id,
                "gain_account_id": self.gain_account.id,
                "receivable_account_id": self.proceeds_account.id,
            }
        )

    def test_01_pool_created_with_quantity(self):
        asset = self._make_pool()
        self.assertEqual(asset.original_quantity, 5.0)
        self.assertTrue(asset.is_quantity_asset)
        self.assertAlmostEqual(asset.unit_acquisition_value, 1000.0, places=2)
        asset.action_confirm()
        self.assertEqual(len(asset.depreciation_line_ids), 10)
        self.assertAlmostEqual(asset.depreciation_line_ids[0].amount, 500.0, places=2)

    def test_02_retire_one_of_five_reduces_value_and_schedule(self):
        asset = self._make_pool()
        asset.action_confirm()
        asset._post_due_depreciation(as_of=date(2025, 3, 10))
        self.assertAlmostEqual(asset.accumulated_depreciation, 1000.0, places=2)
        wiz = self._retire(asset)
        self.assertAlmostEqual(wiz.cost_removed, 1000.0, places=2)
        self.assertAlmostEqual(wiz.accumulated_removed, 200.0, places=2)
        self.assertAlmostEqual(wiz.net_book_value_removed, 800.0, places=2)
        self.assertAlmostEqual(wiz.gain_loss, -800.0, places=2)
        wiz.action_retire()
        self.assertEqual(asset.state, "running")
        self.assertAlmostEqual(asset.quantity, 4.0, places=2)
        self.assertAlmostEqual(asset.original_quantity, 5.0, places=2)
        self.assertAlmostEqual(asset.retired_quantity, 1.0, places=2)
        self.assertAlmostEqual(asset.acquisition_value, 4000.0, places=2)
        self.assertAlmostEqual(asset.accumulated_depreciation, 800.0, places=2)
        self.assertAlmostEqual(asset.net_book_value, 3200.0, places=2)
        remaining = asset.depreciation_line_ids.filtered(lambda line: not line.posted and not line.reversed)
        self.assertEqual(len(remaining), 8)
        for line in remaining:
            self.assertAlmostEqual(line.amount, 400.0, places=2)
        self.assertEqual(len(asset.depreciation_line_ids.filtered("posted")), 2)

    def test_03_retirement_entry_is_balanced(self):
        asset = self._make_pool()
        asset.action_confirm()
        asset._post_due_depreciation(as_of=date(2025, 3, 10))
        self._retire(asset).action_retire()
        record = asset.partial_disposal_ids
        self.assertEqual(len(record), 1)
        self.assertAlmostEqual(record.quantity_before, 5.0, places=2)
        self.assertAlmostEqual(record.quantity_after, 4.0, places=2)
        move = record.move_id
        self.assertEqual(move.state, "posted")
        by_account = {line.account_id: line.balance for line in move.line_ids}
        self.assertAlmostEqual(by_account[self.accum_account], 200.0, places=2)
        self.assertAlmostEqual(by_account[self.asset_account], -1000.0, places=2)
        self.assertAlmostEqual(by_account[self.loss_account], 800.0, places=2)

    def test_04_retirement_with_proceeds_books_a_gain(self):
        asset = self._make_pool()
        asset.action_confirm()
        asset._post_due_depreciation(as_of=date(2025, 3, 10))
        wiz = self._retire(asset, proceeds=1000.0)
        self.assertAlmostEqual(wiz.gain_loss, 200.0, places=2)
        wiz.action_retire()
        by_account = {line.account_id: line.balance for line in asset.partial_disposal_ids.move_id.line_ids}
        self.assertAlmostEqual(by_account[self.gain_account], -200.0, places=2)
        self.assertAlmostEqual(by_account[self.proceeds_account], 1000.0, places=2)

    def test_05_retiring_every_unit_disposes_the_asset(self):
        asset = self._make_pool()
        asset.action_confirm()
        asset._post_due_depreciation(as_of=date(2025, 3, 10))
        self._retire(asset, quantity=5.0).action_retire()
        self.assertEqual(asset.state, "disposed")
        self.assertTrue(asset.disposal_move_id)
        self.assertFalse(asset.depreciation_line_ids.filtered(lambda line: not line.posted and not line.reversed))

    def test_06_cannot_retire_more_than_held(self):
        asset = self._make_pool()
        asset.action_confirm()
        with self.assertRaises(UserError):
            self._retire(asset, quantity=6.0).action_retire()

    def test_07_quantity_cannot_grow_past_the_original(self):
        asset = self._make_pool()
        asset.action_confirm()
        with self.assertRaises(ValidationError):
            asset.sudo().quantity = 6.0

    def test_08_single_unit_asset_has_no_retire(self):
        asset = self._make_pool(quantity=1.0, value=1000.0)
        asset.action_confirm()
        self.assertFalse(asset.is_quantity_asset)
        with self.assertRaises(UserError):
            asset.action_open_partial_disposal_wizard()

    def test_09_successive_retirements_keep_the_pool_consistent(self):
        asset = self._make_pool()
        asset.action_confirm()
        asset._post_due_depreciation(as_of=date(2025, 3, 10))
        self._retire(asset).action_retire()
        self._retire(asset, on=date(2025, 4, 30)).action_retire()
        self.assertAlmostEqual(asset.quantity, 3.0, places=2)
        self.assertAlmostEqual(asset.acquisition_value, 3000.0, places=2)
        self.assertAlmostEqual(asset.accumulated_depreciation, 600.0, places=2)
        self.assertAlmostEqual(asset.net_book_value, 2400.0, places=2)
        remaining = asset.depreciation_line_ids.filtered(lambda line: not line.posted and not line.reversed)
        self.assertAlmostEqual(sum(remaining.mapped("amount")), 2400.0, places=2)

    def test_10_merging_per_unit_assets_creates_a_pool(self):
        assets = self.env["efn.account.asset"]
        for _i in range(5):
            assets |= self._make_pool(quantity=1.0, value=1000.0)
        assets.action_confirm()
        assets._post_due_depreciation(as_of=date(2025, 3, 10))
        nbv_before = sum(assets.mapped("net_book_value"))
        survivor = assets[0]
        survivor._merge_assets_into_pool(assets[1:])
        self.assertAlmostEqual(survivor.quantity, 5.0, places=2)
        self.assertAlmostEqual(survivor.original_quantity, 5.0, places=2)
        self.assertAlmostEqual(survivor.acquisition_value, 5000.0, places=2)
        self.assertAlmostEqual(survivor.opening_accumulated_depreciation, 800.0, places=2)
        self.assertAlmostEqual(survivor.accumulated_depreciation, 1000.0, places=2)
        self.assertAlmostEqual(survivor.net_book_value, nbv_before, places=2)
        absorbed = assets[1:]
        self.assertEqual(set(absorbed.mapped("state")), {"cancelled"})
        self.assertEqual(absorbed.merged_into_id, survivor)
        self.assertFalse(absorbed.depreciation_line_ids.filtered(lambda line: not line.posted))
        remaining = survivor.depreciation_line_ids.filtered(lambda line: not line.posted and not line.reversed)
        self.assertEqual(len(remaining), 8)
        self.assertAlmostEqual(sum(remaining.mapped("amount")), 4000.0, places=2)

    def test_11_retiring_from_a_merged_pool_keeps_the_maths(self):
        assets = self.env["efn.account.asset"]
        for _i in range(5):
            assets |= self._make_pool(quantity=1.0, value=1000.0)
        assets.action_confirm()
        assets._post_due_depreciation(as_of=date(2025, 3, 10))
        survivor = assets[0]
        survivor._merge_assets_into_pool(assets[1:])
        wiz = self._retire(survivor)
        self.assertAlmostEqual(wiz.cost_removed, 1000.0, places=2)
        self.assertAlmostEqual(wiz.accumulated_removed, 200.0, places=2)
        wiz.action_retire()
        self.assertAlmostEqual(survivor.quantity, 4.0, places=2)
        self.assertAlmostEqual(survivor.accumulated_depreciation, 800.0, places=2)
        self.assertAlmostEqual(survivor.net_book_value, 3200.0, places=2)
