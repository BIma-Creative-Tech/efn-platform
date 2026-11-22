"""Port tests/test_asset.py (odoo-platform custom_accounting_asset, ADR-049): jadwal, posting, disposal, revaluasi,
pembalikan, sinkron jurnal. Fixture akun diganti akun buatan test (AccountTestInvoicingCommon)."""

from datetime import date

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import EfnAssetCommon


@tagged("post_install", "-at_install", "efn")
class TestEfnAsset(EfnAssetCommon):
    def test_01_create_and_build_schedule(self):
        asset = self._make_asset()
        self.assertEqual(asset.state, "draft")
        self.assertTrue(asset.code.startswith("FA/"))
        asset.action_confirm()
        self.assertEqual(asset.state, "running")
        self.assertEqual(len(asset.depreciation_line_ids), 12)
        self.assertAlmostEqual(sum(asset.depreciation_line_ids.mapped("amount")), 12000.0, places=2)
        self.assertEqual(asset.depreciation_line_ids.sorted("sequence")[0].date, date(2025, 2, 1))

    def test_02_post_three_months(self):
        asset = self._make_asset()
        asset.action_confirm()
        self.assertEqual(asset._post_due_depreciation(as_of=date(2025, 4, 5)), 3)
        posted = asset.depreciation_line_ids.filtered("posted")
        self.assertEqual(len(posted), 3)
        for line in posted:
            self.assertEqual(line.move_id.state, "posted")
        self.assertAlmostEqual(asset.accumulated_depreciation, 3000.0, places=2)
        self.assertAlmostEqual(asset.net_book_value, 9000.0, places=2)

    def test_03_dispose_with_gain(self):
        asset = self._make_asset()
        asset.action_confirm()
        asset._post_due_depreciation(as_of=date(2025, 4, 5))
        wiz = self.env["efn.account.asset.disposal.wizard"].create(
            {
                "asset_id": asset.id,
                "disposal_date": date(2025, 4, 30),
                "disposal_value": 10000.0,  # nilai buku 9000 -> laba 1000
                "gain_account_id": self.gain_account.id,
                "loss_account_id": self.loss_account.id,
                "receivable_account_id": self.proceeds_account.id,
            }
        )
        self.assertAlmostEqual(wiz.gain_loss, 1000.0, places=2)
        wiz.action_dispose()
        self.assertEqual(asset.state, "disposed")
        self.assertAlmostEqual(asset.disposal_gain_loss, 1000.0, places=2)
        move = asset.disposal_move_id
        self.assertEqual(move.state, "posted")
        self.assertAlmostEqual(sum(move.line_ids.mapped("debit")), sum(move.line_ids.mapped("credit")), places=2)
        by_account = {line.account_id: line.balance for line in move.line_ids}
        self.assertAlmostEqual(by_account[self.accum_account], 3000.0, places=2)
        self.assertAlmostEqual(by_account[self.asset_account], -12000.0, places=2)
        self.assertAlmostEqual(by_account[self.gain_account], -1000.0, places=2)
        # Baris jadwal sesudah disposal tidak tersisa (cron tidak akan memposting).
        self.assertFalse(asset.depreciation_line_ids.filtered(lambda line: not line.posted))

    def test_04_constraints_and_cancel(self):
        with self.assertRaises(ValidationError):
            self._make_asset(salvage_value=20000.0)
        with self.assertRaises(ValidationError):
            self._make_asset(useful_life_months=0)
        asset = self._make_asset()
        asset.action_cancel()
        self.assertEqual(asset.state, "cancelled")
        asset.action_reset_draft()
        self.assertEqual(asset.state, "draft")
        with self.assertRaises(UserError):
            asset.action_open_dispose_wizard()

    def test_05_cron_posts_due_only(self):
        a1 = self._make_asset(name="Aset A")
        a2 = self._make_asset(name="Aset B")
        (a1 | a2).action_confirm()
        with self._mock_today(date(2025, 3, 15)):
            self.env["efn.account.asset"]._cron_post_due_depreciation()
        self.assertEqual(len(a1.depreciation_line_ids.filtered("posted")), 2)
        self.assertEqual(len(a2.depreciation_line_ids.filtered("posted")), 2)
        # Dikelompokkan per tanggal: dua aset satu jurnal per tanggal.
        self.assertEqual(len((a1 | a2).depreciation_line_ids.filtered("posted").move_id), 2)

    def test_07_posting_date_modes(self):
        a_next = self._make_asset(depreciation_date_mode="next_month")
        self.assertEqual(a_next.posting_date, date(2025, 1, 1))
        a_next.action_confirm()
        self.assertEqual(a_next.depreciation_line_ids.sorted("sequence")[0].date, date(2025, 2, 1))
        a_spec = self._make_asset(depreciation_date_mode="specific", posting_date=date(2025, 1, 10))
        a_spec.action_confirm()
        spec_lines = a_spec.depreciation_line_ids.sorted("sequence")
        self.assertEqual(spec_lines[0].date, date(2025, 1, 10))
        self.assertEqual(spec_lines[1].date, date(2025, 2, 10))
        a_eom = self._make_asset(depreciation_date_mode="end_following_month", posting_date=date(2025, 1, 15))
        a_eom.action_confirm()
        eom_lines = a_eom.depreciation_line_ids.sorted("sequence")
        self.assertEqual(eom_lines[0].date, date(2025, 2, 28))
        self.assertEqual(eom_lines[1].date, date(2025, 3, 31))

    def test_08_bulk_post_wizard_and_action(self):
        a1 = self._make_asset(name="Massal A")
        a2 = self._make_asset(name="Massal B")
        (a1 | a2).action_confirm()
        self.env["efn.account.asset.post.wizard"].create({"cutoff_date": date(2025, 3, 5)}).action_post()
        self.assertEqual(len(a1.depreciation_line_ids.filtered("posted")), 2)
        self.assertEqual(len(a2.depreciation_line_ids.filtered("posted")), 2)
        with self._mock_today(date(2025, 6, 5)):
            (a1 | a2).action_post_selected()
        self.assertEqual(len(a1.depreciation_line_ids.filtered("posted")), 5)
        self.assertEqual(len(a2.depreciation_line_ids.filtered("posted")), 5)

    def _revalue(self, asset, new_value, on, life, **accounts):
        vals = {
            "asset_id": asset.id,
            "revaluation_date": on,
            "new_value": new_value,
            "new_remaining_life": life,
            "journal_id": self.journal.id,
        }
        vals.update({key: account.id for key, account in accounts.items()})
        self.env["efn.account.asset.revaluation.wizard"].create(vals).action_revalue()

    def test_09_revaluation_upward_prospective(self):
        asset = self._make_asset()
        asset.action_confirm()
        asset._post_due_depreciation(as_of=date(2025, 4, 5))
        posted_before = asset.depreciation_line_ids.filtered("posted")
        posted_moves = posted_before.move_id
        self._revalue(asset, 13500.0, date(2025, 4, 30), 6, surplus_account_id=self.surplus_account)
        for line in posted_before:
            self.assertTrue(line.posted)
            self.assertAlmostEqual(line.amount, 1000.0, places=2)
        self.assertEqual(posted_before.move_id, posted_moves)
        self.assertAlmostEqual(asset.revaluation_value, 4500.0, places=2)
        self.assertAlmostEqual(asset.net_book_value, 13500.0, places=2)
        self.assertEqual(asset.useful_life_months, 9)
        unposted = asset.depreciation_line_ids.filtered(lambda line: not line.posted)
        self.assertEqual(len(unposted), 6)
        self.assertAlmostEqual(sum(unposted.mapped("amount")), 13500.0, places=2)
        self.assertAlmostEqual(unposted.sorted("sequence")[0].amount, 2250.0, places=2)
        move = asset.revaluation_ids.move_id
        self.assertAlmostEqual(
            move.line_ids.filtered(lambda line: line.account_id == self.asset_account).debit, 4500.0, places=2
        )
        self.assertAlmostEqual(
            move.line_ids.filtered(lambda line: line.account_id == self.surplus_account).credit, 4500.0, places=2
        )

    def test_10_revaluation_downward(self):
        asset = self._make_asset()
        asset.action_confirm()
        asset._post_due_depreciation(as_of=date(2025, 4, 5))
        self._revalue(asset, 6000.0, date(2025, 4, 30), 9, loss_account_id=self.reval_loss_account)
        self.assertAlmostEqual(asset.revaluation_value, -3000.0, places=2)
        self.assertAlmostEqual(asset.net_book_value, 6000.0, places=2)
        move = asset.revaluation_ids.move_id
        self.assertAlmostEqual(
            move.line_ids.filtered(lambda line: line.account_id == self.reval_loss_account).debit, 3000.0, places=2
        )

    def test_11_disposal_after_revaluation_releases_full_carrying(self):
        asset = self._make_asset()
        asset.action_confirm()
        asset._post_due_depreciation(as_of=date(2025, 4, 5))
        self._revalue(asset, 13500.0, date(2025, 4, 30), 9, surplus_account_id=self.surplus_account)
        self.env["efn.account.asset.disposal.wizard"].create(
            {
                "asset_id": asset.id,
                "disposal_date": date(2025, 5, 31),
                "disposal_value": 13500.0,
                "receivable_account_id": self.proceeds_account.id,
                "surplus_account_id": self.surplus_account.id,
                "retained_earnings_account_id": self.retained_earnings_account.id,
            }
        ).action_dispose()
        move = asset.disposal_move_id
        by_account = {line.account_id: line.balance for line in move.line_ids}
        self.assertAlmostEqual(by_account[self.asset_account], -16500.0, places=2)
        self.assertAlmostEqual(by_account[self.surplus_account], 4500.0, places=2)
        self.assertAlmostEqual(by_account[self.retained_earnings_account], -4500.0, places=2)
        self.assertAlmostEqual(asset.revaluation_surplus_balance, 0.0, places=2)

    def test_12_downward_offsets_existing_surplus(self):
        asset = self._make_asset()
        asset.action_confirm()
        asset._post_due_depreciation(as_of=date(2025, 4, 5))
        self._revalue(asset, 11000.0, date(2025, 4, 30), 9, surplus_account_id=self.surplus_account)
        self.assertAlmostEqual(asset.revaluation_surplus_balance, 2000.0, places=2)
        self._revalue(
            asset,
            8000.0,
            date(2025, 5, 31),
            9,
            surplus_account_id=self.surplus_account,
            loss_account_id=self.reval_loss_account,
        )
        self.assertAlmostEqual(asset.revaluation_surplus_balance, 0.0, places=2)
        self.assertAlmostEqual(asset.revaluation_loss_recognized, 1000.0, places=2)
        self.assertAlmostEqual(asset.net_book_value, 8000.0, places=2)

    def test_13_upward_reverses_prior_loss(self):
        asset = self._make_asset()
        asset.action_confirm()
        asset._post_due_depreciation(as_of=date(2025, 4, 5))
        self._revalue(asset, 6000.0, date(2025, 4, 30), 9, loss_account_id=self.reval_loss_account)
        self._revalue(
            asset,
            10000.0,
            date(2025, 5, 31),
            9,
            surplus_account_id=self.surplus_account,
            income_account_id=self.reval_income_account,
        )
        self.assertAlmostEqual(asset.revaluation_loss_recognized, 0.0, places=2)
        self.assertAlmostEqual(asset.revaluation_surplus_balance, 1000.0, places=2)
        move = asset.revaluation_ids.sorted("id")[-1].move_id
        self.assertAlmostEqual(
            move.line_ids.filtered(lambda line: line.account_id == self.reval_income_account).credit, 3000.0, places=2
        )

    def test_14_reverse_depreciation_line(self):
        asset = self._make_asset()
        asset.action_confirm()
        asset._post_due_depreciation(as_of=date(2025, 4, 5))
        line = asset.depreciation_line_ids.filtered("posted").sorted("sequence")[-1]
        line.action_reverse()
        self.assertFalse(line.posted)
        self.assertTrue(line.reversed)
        self.assertAlmostEqual(asset.accumulated_depreciation, 2000.0, places=2)
        self.assertAlmostEqual(asset.net_book_value, 10000.0, places=2)
        asset._post_due_depreciation(as_of=date(2025, 4, 5))
        self.assertFalse(line.posted)
        with self.assertRaises(UserError):
            line.action_reverse()

    def test_14b_reverse_line_of_shared_move(self):
        a1 = self._make_asset(name="Bersama A")
        a2 = self._make_asset(name="Bersama B")
        (a1 | a2).action_confirm()
        (a1 | a2)._post_due_depreciation(as_of=date(2025, 2, 5))
        line = a1.depreciation_line_ids.filtered("posted")
        shared = line.move_id
        self.assertEqual(shared, a2.depreciation_line_ids.filtered("posted").move_id)
        line.action_reverse()
        self.assertEqual(shared.state, "posted")  # jurnal bersama tetap, hanya baris a1 dibalik
        self.assertAlmostEqual(a1.accumulated_depreciation, 0.0, places=2)
        self.assertAlmostEqual(a2.accumulated_depreciation, 1000.0, places=2)

    def test_15_external_move_delete_and_draft_self_heal(self):
        asset = self._make_asset()
        asset.action_confirm()
        asset._post_due_depreciation(as_of=date(2025, 4, 5))
        line = asset.depreciation_line_ids.filtered("posted").sorted("sequence")[-1]
        move = line.move_id
        move.button_draft()
        self.assertFalse(line.posted)
        self.assertAlmostEqual(asset.accumulated_depreciation, 2000.0, places=2)
        move.unlink()
        self.assertFalse(line.posted)
        self.assertFalse(line.move_id)
        self.assertFalse(line.reversed)
        asset._post_due_depreciation(as_of=date(2025, 4, 5))
        self.assertTrue(line.posted)
        self.assertAlmostEqual(asset.accumulated_depreciation, 3000.0, places=2)

    def test_16_group_defaults_applied_server_side(self):
        asset = self.env["efn.account.asset"].create(
            {
                "name": "Tanpa akun",
                "company_id": self.company.id,
                "group_id": self.group.id,
                "acquisition_date": date(2025, 1, 1),
                "acquisition_value": 1200.0,
            }
        )
        self.assertEqual(asset.asset_account_id, self.asset_account)
        self.assertEqual(asset.depreciation_account_id, self.accum_account)
        self.assertEqual(asset.expense_account_id, self.expense_account)
        self.assertEqual(asset.journal_id, self.journal)
        self.assertEqual(asset.useful_life_months, 12)

    def test_17_running_asset_locked_fields(self):
        asset = self._make_asset()
        asset.action_confirm()
        asset.write({"name": "Laptop ganti nama"})
        with self.assertRaises(UserError):
            asset.write({"acquisition_value": 1.0})
        with self.assertRaises(UserError):
            asset.unlink()
