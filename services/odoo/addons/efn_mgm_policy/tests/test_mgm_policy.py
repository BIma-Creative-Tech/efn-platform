"""Kebijakan MGM per PT (K25-10, ADR-009, docs/04 §19)."""

from psycopg2 import IntegrityError

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

from odoo.addons.efn_loyalty_accounting.tests.common import (
    ACCOUNT_MANAGER,
    LOYALTY_MANAGER,
    assign_brand_code,
    make_user,
)


@tagged("post_install", "-at_install", "efn")
class TestEfnMgmPolicy(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "PT MGM uji"})
        cls.other = cls.env["res.company"].create({"name": "PT MGM lain"})
        assign_brand_code(cls.env, cls.company, "GL")
        assign_brand_code(cls.env, cls.other, "BCR")
        cls.maker = make_user(cls.env, "efn_mgm_maker", [LOYALTY_MANAGER], [cls.company])
        cls.approver = make_user(cls.env, "efn_mgm_approver", [ACCOUNT_MANAGER], [cls.company])
        cls.approver_other = make_user(cls.env, "efn_mgm_approver_x", [ACCOUNT_MANAGER], [cls.other])
        cls.Policy = cls.env["efn.mgm.policy"]

    def _draft(self, **vals):
        return (
            self.Policy.with_user(self.maker).with_company(self.company).create({"company_id": self.company.id, **vals})
        )

    def test_versions_and_four_eyes(self):
        first = self._draft()
        self.assertEqual((first.version, first.state, first.max_depth), (1, "draft", 3))
        with self.assertRaises(AccessError):
            first.with_user(self.maker).action_efn_approve()
        with self.assertRaises(AccessError):
            first.with_user(self.approver_other).action_efn_approve()
        first.with_user(self.approver).action_efn_approve()
        self.assertEqual(first.state, "approved")
        self.assertEqual(self.Policy.efn_active_policy(self.company), first)
        with self.assertRaisesRegex(UserError, "versi baru"):
            first.with_user(self.maker).write({"level1_pct_bp": 300})
        with self.assertRaises(UserError):
            first.with_user(self.maker).unlink()
        second = self._draft(level1_pct_bp=300)
        self.assertEqual(second.version, 2)

    def test_approver_who_edited_cannot_approve(self):
        both = make_user(self.env, "efn_mgm_both", [LOYALTY_MANAGER, ACCOUNT_MANAGER], [self.company])
        policy = self._draft()
        policy.with_user(both).write({"note": "disunting"})
        with self.assertRaisesRegex(AccessError, "Empat mata"):
            policy.with_user(both).action_efn_approve()

    def test_system_fields_and_maker_only(self):
        with self.assertRaises(AccessError):
            self._draft(state="approved")
        with self.assertRaises(AccessError):
            self.Policy.with_user(self.approver).create({"company_id": self.company.id})
        policy = self._draft()
        with self.assertRaises(AccessError):
            policy.with_user(self.maker).write({"state": "approved"})

    def test_bounds(self):
        # Kedalaman di luar 2..5 ditolak CHECK DB lebih dulu (jaring pengaman selain validasi Python).
        for depth in (0, 1, 6):
            with self.subTest(max_depth=depth), mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError):
                self._draft(max_depth=depth)
        for vals in (
            {"level1_pct_bp": 2_001},
            {"level1_pct_bp": 100, "level2_pct_bp": 200},
            {"cap_points_per_referrer_month": 0},
            {"settle_hold_days": 0},
            {"relation_months": 37},
        ):
            with self.subTest(vals=vals), self.assertRaises(ValidationError), self.env.cr.savepoint():
                self._draft(**vals)

    def test_reward_formula(self):
        policy = self._draft(
            level1_pct_bp=200, level2_pct_bp=50, flat_points_level3=5, max_depth=4, min_order_idr=50_000
        )
        # Rp1.000.000 x 2 % = Rp20.000 = 200 poin @Rp100; level 2: 0,5 % = 50 poin; level 3-4 flat; level 5 di luar.
        self.assertEqual(
            [policy._efn_reward_points(level, 1_000_000, 100) for level in (1, 2, 3, 4, 5)], [200, 50, 5, 5, 0]
        )
        self.assertEqual(policy._efn_reward_points(1, 49_999, 100), 0)
        self.assertEqual(policy._efn_reward_points(1, 50_050, 100), 10)  # floor, tanpa pembulatan ke atas

    def test_multi_company_rule(self):
        policy = self._draft()
        self.assertFalse(self.Policy.with_user(self.approver_other).search([("id", "=", policy.id)]))
