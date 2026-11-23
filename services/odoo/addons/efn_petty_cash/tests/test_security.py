"""Isolasi multi-company + rule "milik sendiri" + ACL."""

from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import EfnPettyCashCommon


@tagged("post_install", "-at_install", "efn")
class TestEfnPettyCashSecurity(EfnPettyCashCommon):
    def test_company_isolation(self):
        Fund = self.env["efn.petty.cash.fund"].with_user(self.custodian_b)
        self.assertFalse(Fund.search([]))
        with self.assertRaises(AccessError):
            Fund.browse(self.fund.id).read(["name"])
        self.assertFalse(self.env["efn.petty.cash.type"].with_user(self.custodian_b).search([]))
        request = self._new_request(300_000)
        Request = self.env["efn.petty.cash.request"].with_user(self.custodian_b)
        with self.assertRaises(AccessError):
            Request.browse(request.id).read(["name"])

    def test_custodian_sees_only_own_fund_and_documents(self):
        Fund = self.env["efn.petty.cash.fund"]
        self.assertEqual(Fund.with_user(self.custodian).search([]), self.fund)
        self.assertFalse(Fund.with_user(self.requester).search([]))
        request = self._new_request(300_000)
        Request = self.env["efn.petty.cash.request"]
        self.assertIn(request, Request.with_user(self.requester).search([]))
        self.assertNotIn(request, Request.with_user(self.custodian).search([]))
        self.assertIn(request, Request.with_user(self.approver).search([]))
        self.assertIn(request, Request.with_user(self.finance).search([]))

    def test_plain_user_cannot_create_type_or_edit_fund(self):
        Type = self.env["efn.petty.cash.type"].with_user(self.requester)
        with self.assertRaises(AccessError):
            Type.create({"name": "X", "code": "X", "company_id": self.company_a.id})
        with self.assertRaises(AccessError):
            self.fund.with_user(self.custodian).write({"float_amount": 50_000_000})

    def test_replenishment_only_by_holder_or_finance(self):
        with self.assertRaises(AccessError):
            self.fund.with_user(self.approver).action_request_replenishment()
