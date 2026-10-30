"""G31b (docs/04 §20.10): dasar MGM per baris di jalur POS — `mgm_eligible_amount` (earn) dan
`mgm_refunded_eligible_amount` (reverse) tanpa kategori `excluded_categ_ids` kebijakan MGM PT; tanpa
kebijakan/pengecualian payload tidak berubah. Memakai fixture & mock BFF test Poin EFN POS (TestPoSCommon)."""

from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from odoo.addons.efn_core.models.efn_bff_client import BffResponse

# Modul (bukan kelas) diimpor: loader Odoo memuat SEMUA TestCase di namespace modul test ini; kelas induk yang diimpor
# langsung akan ikut dijalankan ulang di bawah efn_mgm_policy.
from odoo.addons.efn_pos_loyalty_ledger.tests import test_loyalty_ledger as base

EARN, MEMBER_A, REVERSE = base.EARN, base.MEMBER_A, base.REVERSE


@tagged("post_install", "-at_install", "efn")
class TestEfnMgmPosBasis(base.TestEfnPosLoyaltyLedger):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Category = cls.env["product.category"]
        cls.categ_mgm_excluded = Category.create({"name": "Voucher (uji MGM)"})
        cls.categ_mgm_child = Category.create(
            {"name": "Voucher digital (uji MGM)", "parent_id": cls.categ_mgm_excluded.id}
        )
        cls.product_mgm_excluded = cls.create_product("Voucher Uji", cls.categ_mgm_child, 30000.0)

    def _mgm_policy(self, excluded=True, effective_from=None):
        """Kebijakan MGM disetujui untuk PT POS (sudo: fokus dasar, bukan empat mata — lihat test_mgm_policy)."""
        company = self.config.company_id
        policy = (
            self.env["efn.mgm.policy"]
            .sudo()
            .with_company(company)
            .create(
                {
                    "company_id": company.id,
                    "effective_from": effective_from or fields.Datetime.now() - timedelta(days=1),
                    "excluded_categ_ids": [(6, 0, [self.categ_mgm_excluded.id] if excluded else [])],
                }
            )
        )
        policy.write({"state": "approved", "approved_at": fields.Datetime.now() - timedelta(hours=1)})
        return policy

    def test_mgm_basis_excludes_policy_categories_and_children(self):
        self._mgm_policy()
        lines = [(self.product, 2), (self.product_excluded, 1), (self.product_mgm_excluded, 1)]
        order = self._sync(self._order_data(lines))
        earn = self._outbox(order, "earn")
        # dasar earn: 2x50.000 + voucher 30.000 (rokok dikecualikan poin); dasar MGM: tanpa voucher (subkategori)
        self.assertEqual(
            (earn.eligible_amount, earn.efn_mgm_eligible_amount, earn.efn_mgm_basis_set), (130000, 100000, True)
        )
        self.responses[EARN] = self._ok_earn(13)
        self._run_outbox()
        payload = self._calls_to(EARN)[0]
        self.assertEqual((payload["eligible_amount"], payload["mgm_eligible_amount"]), (130000, 100000))
        self.assertEqual(payload["member_ref"], MEMBER_A)

    def test_no_policy_or_no_exclusion_keeps_payload(self):
        order = self._sync(self._order_data([(self.product, 1), (self.product_mgm_excluded, 1)]))
        self.assertFalse(self._outbox(order, "earn").efn_mgm_basis_set)
        self._mgm_policy(excluded=False)
        order2 = self._sync(self._order_data([(self.product, 1)]))
        self.responses[EARN] = self._ok_earn(5)
        self._run_outbox()
        for payload in self._calls_to(EARN):
            self.assertNotIn("mgm_eligible_amount", payload)
        self.assertFalse(self._outbox(order2, "earn").efn_mgm_basis_set)

    def test_future_policy_not_applied(self):
        self._mgm_policy(effective_from=fields.Datetime.now() + timedelta(days=3))
        order = self._sync(self._order_data([(self.product, 1), (self.product_mgm_excluded, 1)]))
        self.assertFalse(self._outbox(order, "earn").efn_mgm_basis_set)

    def test_reverse_sends_refunded_mgm_basis(self):
        self._mgm_policy()
        uuid = self.create_random_uid()
        original = self._sync(self._order_data([(self.product, 2), (self.product_mgm_excluded, 1)], uuid=uuid))
        self.responses[EARN] = self._ok_earn(13)
        self._run_outbox()
        voucher_line = original.lines.filtered(lambda line: line.product_id == self.product_mgm_excluded)
        product_line = original.lines.filtered(lambda line: line.product_id == self.product)
        # retur voucher saja -> dasar MGM direfund 0 (BFF: tanpa reversal MGM), earn tetap di-clawback
        refund_voucher = self._sync(
            self._order_data(
                [{"product": self.product_mgm_excluded, "quantity": -1, "refunded_orderline_id": voucher_line.id}],
                extra={"is_refund": True},
            )
        )
        rv = self._outbox(refund_voucher, "reverse")
        self.assertEqual((rv.eligible_amount, rv.efn_mgm_eligible_amount, rv.efn_mgm_basis_set), (30000, 0, True))
        # retur 1 produk -> dasar MGM direfund 50.000 (dari 100.000)
        refund_product = self._sync(
            self._order_data(
                [{"product": self.product, "quantity": -1, "refunded_orderline_id": product_line.id}],
                extra={"is_refund": True},
            )
        )
        rp = self._outbox(refund_product, "reverse")
        self.assertEqual((rp.eligible_amount, rp.efn_mgm_eligible_amount), (50000, 50000))
        self.responses[REVERSE] = BffResponse(
            200, {"points_clawed_back": 3, "points_restored": 0, "points_balance": 10}
        )
        self._run_outbox()
        sent = {p["refund_order_ref"]: p.get("mgm_refunded_eligible_amount") for p in self._calls_to(REVERSE)}
        self.assertEqual(sent, {refund_voucher.uuid: 0, refund_product.uuid: 50000})


# Fixture & helper induk dipakai, test induk TIDAK diulang di sini (sudah dijalankan di efn_pos_loyalty_ledger).
for _name in dir(base.TestEfnPosLoyaltyLedger):
    if _name.startswith("test") and _name not in TestEfnMgmPosBasis.__dict__:
        setattr(TestEfnMgmPosBasis, _name, None)
