"""Settlement bulanan Poin EFN antar-PT (W-15). Akrual dibuat langsung (state final) -- BFF tidak dipanggil."""

import json
from datetime import date, timedelta
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.fields import Command
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.efn_loyalty_accounting.tests.common import ACCOUNT_MANAGER, assign_brand_code, make_user

PERIOD = "2026-09"
START = date(2026, 9, 1)


@tagged("post_install", "-at_install", "efn")
class TestEfnLoyaltySettlement(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.gl = cls.company_data["company"]
        cls.bcr = cls.setup_other_company(name="PT BCR Uji Settlement Poin")["company"]
        assign_brand_code(cls.env, cls.gl, "GL")
        assign_brand_code(cls.env, cls.bcr, "BCR")
        Account = cls.env["account.account"].sudo()
        cls.gl_payable = Account.with_company(cls.gl).create(
            {
                "name": "Utang Poin Antar-PT uji GL",
                "code": "LOYSETGL1",
                "account_type": "liability_current",
                "company_ids": [Command.set(cls.gl.ids)],
            }
        )
        cls.bcr_clearing = Account.with_company(cls.bcr).create(
            {
                "name": "Kliring Poin Antar-PT uji BCR",
                "code": "LOYSETBCR1",
                "account_type": "asset_current",
                "company_ids": [Command.set(cls.bcr.ids)],
            }
        )
        cls.gl.sudo().efn_loyalty_interco_payable_account_id = cls.gl_payable
        cls.bcr.sudo().efn_loyalty_clearing_account_id = cls.bcr_clearing
        both = [cls.gl, cls.bcr]
        cls.fin_a = make_user(cls.env, "efn_set_fin_a", [ACCOUNT_MANAGER], both)
        cls.fin_b = make_user(cls.env, "efn_set_fin_b", [ACCOUNT_MANAGER], both)
        cls.fin_gl_only = make_user(cls.env, "efn_set_fin_gl", [ACCOUNT_MANAGER], [cls.gl])
        cls.Settlement = cls.env["efn.loyalty.settlement"]

    def _accruals(self, company, per_day=None, days=30, state="done"):
        """Akrual final untuk setiap tanggal September; ``per_day`` = {tanggal (int): (redeemed, restored)} ke BCR."""
        Accrual = self.env["efn.loyalty.accrual"].sudo()
        per_day = per_day or {}
        for offset in range(days):
            day = START + timedelta(days=offset)
            red, res = per_day.get(day.day, (0, 0))
            Accrual.create(
                {
                    "company_id": company.id,
                    "company_code": company.efn_brand_code,
                    "date": day,
                    "state": state,
                    "issuer_by_redeemer": json.dumps({"redeemed": {"BCR": red}, "restored": {"BCR": res}}),
                }
            )

    def _today(self):
        return patch.object(
            type(self.env["efn.loyalty.accrual"]), "_efn_business_today", return_value=date(2026, 10, 5)
        )

    def _prepare(self, user):
        with self._today():
            return self.Settlement.with_user(user).efn_prepare(PERIOD, self.gl.id, self.bcr.id)

    def test_full_flow_two_moves_four_eyes(self):
        self._accruals(self.gl, {3: (3_000, 0), 10: (2_000, 500), 28: (1_000, 0)})
        settlement = self._prepare(self.fin_a)
        self.assertEqual(
            (
                settlement.redeemed_value_idr,
                settlement.restored_value_idr,
                settlement.amount_idr,
                settlement.accrual_days,
            ),
            (6_000, 500, 5_500, 30),
        )
        self.assertEqual(settlement.ref, "EFN-LOYSET-GL-BCR-2026-09")
        self.assertEqual(json.loads(settlement.breakdown)["2026-09-10"], [2_000, 500])
        settlement.with_user(self.fin_a).write({"payment_ref": "TRF-0001", "payment_date": date(2026, 10, 5)})
        with self.assertRaisesRegex(AccessError, "efn_loyalty_settlement:sod"), self._today():
            settlement.with_user(self.fin_a).action_approve()
        with self._today():
            settlement.with_user(self.fin_b).action_approve()
        self.assertEqual(settlement.state, "approved")
        issuer_move, redeemer_move = settlement.issuer_move_id, settlement.redeemer_move_id
        self.assertEqual((issuer_move.company_id, redeemer_move.company_id), (self.gl, self.bcr))
        self.assertEqual(issuer_move.state, "posted")
        self.assertEqual(
            sum(issuer_move.line_ids.filtered(lambda line: line.account_id == self.gl_payable).mapped("debit")), 5_500
        )
        self.assertEqual(
            sum(redeemer_move.line_ids.filtered(lambda line: line.account_id == self.bcr_clearing).mapped("credit")),
            5_500,
        )
        self.assertIn("TRF-0001", issuer_move.ref)
        # Idempoten: menyiapkan lagi mengembalikan dokumen yang sama, bukan duplikat
        self.assertEqual(self._prepare(self.fin_a), settlement)

    def test_incomplete_accruals_rejected(self):
        self._accruals(self.gl, days=29)  # 30 September tidak ada
        with self.assertRaisesRegex(UserError, "accrual_incomplete"):
            self._prepare(self.fin_a)

    def test_non_final_accrual_rejected(self):
        self._accruals(self.gl, days=29)
        self.env["efn.loyalty.accrual"].sudo().create(
            {"company_id": self.gl.id, "company_code": "GL", "date": date(2026, 9, 30), "state": "missing_config"}
        )
        with self.assertRaisesRegex(UserError, "missing_config"):
            self._prepare(self.fin_a)

    def test_open_period_rejected(self):
        self._accruals(self.gl)
        with (
            patch.object(type(self.env["efn.loyalty.accrual"]), "_efn_business_today", return_value=date(2026, 9, 30)),
            self.assertRaisesRegex(UserError, "period_open"),
        ):
            self.Settlement.with_user(self.fin_a).efn_prepare(PERIOD, self.gl.id, self.bcr.id)

    def test_changed_accrual_blocks_approval_until_recompute(self):
        self._accruals(self.gl, {5: (1_000, 0)})
        settlement = self._prepare(self.fin_a)
        settlement.with_user(self.fin_a).write({"payment_ref": "TRF-2", "payment_date": date(2026, 10, 5)})
        late = (
            self.env["efn.loyalty.accrual"]
            .sudo()
            .search([("company_id", "=", self.gl.id), ("date", "=", date(2026, 9, 6))])
        )
        late.issuer_by_redeemer = json.dumps({"redeemed": {"BCR": 400}, "restored": {}})
        with self.assertRaisesRegex(UserError, "changed"), self._today():
            settlement.with_user(self.fin_b).action_approve()
        with self._today():
            settlement.with_user(self.fin_a).action_recompute()
            self.assertEqual(settlement.amount_idr, 1_400)
            settlement.with_user(self.fin_b).action_approve()
        self.assertEqual(settlement.state, "approved")

    def test_access_requires_both_companies_and_payment_ref(self):
        self._accruals(self.gl, {5: (1_000, 0)})
        with self.assertRaisesRegex(AccessError, "efn_loyalty_settlement:company"), self._today():
            self.Settlement.with_user(self.fin_gl_only).efn_prepare(PERIOD, self.gl.id, self.bcr.id)
        settlement = self._prepare(self.fin_a)
        with self.assertRaisesRegex(UserError, "efn_loyalty_settlement:payment"), self._today():
            settlement.with_user(self.fin_b).action_approve()
        settlement.with_user(self.fin_a).action_cancel()
        self.assertEqual(settlement.state, "cancelled")
        # Batal -> boleh disiapkan ulang (UNIQUE parsial)
        again = self._prepare(self.fin_a)
        self.assertNotEqual(again, settlement)

    def test_prepare_all_pairs_skips_zero(self):
        self._accruals(self.gl, {7: (2_500, 0)})
        self._accruals(self.bcr)
        with self._today():
            records = self.Settlement.with_user(self.fin_a).efn_prepare(PERIOD)
        self.assertEqual(
            records.mapped(lambda r: (r.issuer_company_id.efn_brand_code, r.redeemer_company_id.efn_brand_code)),
            [("GL", "BCR")],
        )
