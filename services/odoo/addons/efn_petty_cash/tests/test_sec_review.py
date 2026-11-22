"""Regresi review keamanan 23 Nov 2026: SEC-G57-01 (penerima/penyunting di empat mata, status via RPC write) dan
SEC-G57-03 (injeksi formula XLSX)."""

import base64
import io
import zipfile

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import EfnPettyCashCommon


@tagged("post_install", "-at_install", "efn")
class TestEfnPettyCashSecReview(EfnPettyCashCommon):
    def _for_recipient(self, user):
        """Pengajuan pemohon dengan penerima dana = partner ``user`` dan tanpa user pemegang (Q-PC5)."""
        request = self._new_request(300_000, holder_partner_id=user.partner_id.id, holder_user_id=False)
        request.action_submit()
        return request

    def test_recipient_cannot_approve(self):
        request = self._for_recipient(self.approver)
        with self.assertRaises(UserError):
            request.with_user(self.approver).action_approve()
        self.assertEqual(request.state, "to_approve")

    def test_recipient_cannot_disburse(self):
        request = self._for_recipient(self.finance)
        request.with_user(self.approver).action_approve()
        with self.assertRaises(UserError):
            request.with_user(self.finance).action_disburse()
        self.assertFalse(request.sudo().disburse_move_id)

    def test_editor_finance_cannot_disburse(self):
        request = self._new_request(300_000)
        request.with_user(self.finance).write({"purpose": "Diubah Finance"})
        request.action_submit()
        request.with_user(self.approver).action_approve()
        with self.assertRaises(UserError):
            request.with_user(self.finance).action_disburse()

    def test_recipient_cannot_post_realization(self):
        request = self._for_recipient(self.finance_approver)
        request.with_user(self.approver).action_approve()
        request.with_user(self.finance).action_disburse()
        realization = self._realization({"request_id": request.id}, [self._expense_line(100_000)])
        realization.action_submit()
        with self.assertRaises(UserError):
            realization.with_user(self.finance_approver).action_post()

    def test_rpc_write_of_workflow_fields_rejected(self):
        request = self._new_request(300_000)
        system_ctx = {"efn_petty_cash_system": True}  # konteks tanpa su tidak cukup
        for user in (self.requester, self.approver, self.finance):
            for vals in ({"state": "settled"}, {"approved_by_id": user.id}, {"maker_id": user.id}):
                with self.assertRaises(AccessError):
                    request.with_user(user).with_context(**system_ctx).write(vals)
        self.assertEqual(request.state, "draft")
        realization = self._realization({"request_id": self._disbursed_request().id}, [self._expense_line(50_000)])
        with self.assertRaises(AccessError):
            realization.with_user(self.finance).write({"state": "posted"})
        with self.assertRaises(AccessError):
            self.fund.with_user(self.finance).write({"state": "closed"})

    def test_xlsx_formula_stored_as_string(self):
        request = self._disbursed_request(400_000)
        realization = self._realization({"request_id": request.id}, [self._expense_line(100_000, name="=1+2")])
        realization.action_submit()
        realization.with_user(self.finance).action_post()
        # Laporan "statement" menampilkan nama pemegang (bukan nama baris biaya) -> suntikkan formula di sana.
        request.holder_partner_id.sudo().name = "=1+2"
        wizard = self.env["efn.petty.cash.report.wizard"].with_user(self.finance).create({"report_kind": "statement"})
        wizard.action_xlsx()
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(wizard.xlsx_file))) as book:
            sheet = book.read("xl/worksheets/sheet1.xml").decode()
            strings = book.read("xl/sharedStrings.xml").decode()
        self.assertNotIn("<f>", sheet)
        self.assertIn("=1+2", strings)
