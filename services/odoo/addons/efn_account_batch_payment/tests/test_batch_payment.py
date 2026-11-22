"""Batch pembayaran (port test_batch_payment.py sumber) + kasus keamanan G58 §5.6.
Nomor rekening di fixture sintetis (bukan rekening nyata)."""

import csv
import hashlib
import io
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.service.model import get_public_method
from odoo.tests import new_test_user, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.efn_account_batch_payment.models.efn_account_batch_payment import EXPORT_MARKER

ACC_NUMBER = "9900000001"


@tagged("post_install", "-at_install", "efn")
class TestEfnBatchPayment(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.journal = cls.company_data["default_journal_bank"]
        # Vendor badan usaha: rekening tidak dienkripsi efn_pii (bila terpasang) -> fixture tidak butuh kunci PII.
        cls.vendor = cls.env["res.partner"].create({"name": "PT Vendor Uji", "is_company": True})
        cls.vendor_bank = cls.env["res.partner.bank"].create({"acc_number": ACC_NUMBER, "partner_id": cls.vendor.id})
        memos = ("-2+3 , bayar\ninv", "INV/2026/0002", "=cmd|1,2\r\nX")
        cls.payments = cls.env["account.payment"].create(
            [
                {
                    "payment_type": "outbound",
                    "partner_type": "supplier",
                    "partner_id": cls.vendor.id,
                    "partner_bank_id": cls.vendor_bank.id,
                    "amount": amount,
                    "memo": memo,
                    "journal_id": cls.journal.id,
                }
                for amount, memo in zip((100.0, 250.0, 650.0), memos, strict=True)
            ]
        )
        cls.payments.action_post()
        cls.exporter = new_test_user(
            cls.env,
            login="efn_batch_exporter",
            groups="base.group_user,account.group_account_manager,account.group_account_user",
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )
        cls.billing = new_test_user(
            cls.env,
            login="efn_batch_billing",
            groups="base.group_user,account.group_account_invoice",
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )
        cls.fmt_generic = cls.env.ref("efn_account_batch_payment.format_generic")
        cls.fmt_mandiri = cls.env.ref("efn_account_batch_payment.format_mandiri_mcm")

    def _batch(self, fmt=None):
        batch = self.env["efn.account.batch.payment"].create(
            {
                "journal_id": self.journal.id,
                "batch_type": "outbound",
                "payment_ids": [Command.set(self.payments.ids)],
                "export_format_id": (fmt or self.fmt_generic).id,
            }
        )
        batch.action_validate()
        return batch

    def _export_attachments(self, batch):
        return (
            self.env["ir.attachment"]
            .sudo()
            .search([("res_model", "=", False), ("description", "=", f"{EXPORT_MARKER}:{batch.id}")])
        )

    def _export(self, batch):
        batch.with_user(self.exporter).action_generate_export_file()
        attachment = self._export_attachments(batch)
        self.assertEqual(len(attachment), 1)
        return attachment.raw.decode("utf-8")

    # ------------------------------------------------------------------ port sumber
    def test_validate_and_export_generic(self):
        batch = self._batch()
        self.assertEqual(batch.state, "validated")
        self.assertTrue(batch.name.startswith("BATCH/"))
        self.assertEqual(batch.validated_by_id, self.env.user)
        self.assertEqual(batch.amount_total, 1000.0)
        action = batch.with_user(self.exporter).action_generate_export_file()
        self.assertEqual(action["type"], "ir.actions.act_url")
        attachment = self._export_attachments(batch)
        self.assertFalse(attachment.public)
        content = attachment.raw
        self.assertEqual(batch.state, "sent")
        self.assertEqual(batch.export_sha256, hashlib.sha256(content).hexdigest())
        self.assertEqual(batch.exported_by_id, self.exporter)
        text = content.decode("utf-8")
        self.assertEqual(len([line for line in text.split("\r\n") if line]), 4, "judul + 3 baris")
        self.assertIn(ACC_NUMBER, text)
        self.assertTrue(all(self.payments.mapped("is_sent")))
        bodies = " ".join(str(body or "") for body in batch.message_ids.mapped("body"))
        self.assertNotIn(ACC_NUMBER, bodies, "chatter tanpa nomor rekening")
        self.assertEqual(self.payments[0].efn_partner_bank_masked, "****001")

    # ------------------------------------------------------------------ SEC-G58-01 / SEC-G58-02
    def test_manager_cannot_forge_validation_via_write(self):
        batch = self.env["efn.account.batch.payment"].create(
            {"journal_id": self.journal.id, "payment_ids": [Command.set(self.payments.ids)]}
        )
        Batch = batch.with_user(self.exporter)
        for vals in (
            {"state": "validated", "validated_by_id": self.env.uid},
            {"validated_by_id": self.env.uid},
            {"export_sha256": "x"},
            {"efn_validated_digest": "x"},
        ):
            with self.assertRaises(AccessError):
                Batch.write(vals)
        with self.assertRaises(AccessError):
            self.env["efn.account.batch.payment"].with_user(self.exporter).create(
                {"journal_id": self.journal.id, "state": "validated", "validated_by_id": self.env.uid}
            )
        with self.assertRaises(AccessError):
            Batch.with_context(efn_batch_payment_system=True).write({"state": "validated"})
        self.assertEqual(batch.state, "draft")

    def test_content_locked_after_validation(self):
        batch = self._batch()
        extra = self.env["account.payment"].create(
            {
                "payment_type": "outbound",
                "partner_type": "supplier",
                "partner_id": self.vendor.id,
                "partner_bank_id": self.vendor_bank.id,
                "amount": 777.0,
                "journal_id": self.journal.id,
            }
        )
        extra.action_post()
        Batch = batch.with_user(self.exporter)
        with self.assertRaises(UserError):
            Batch.write({"payment_ids": [Command.link(extra.id)]})
        with self.assertRaises(UserError):
            extra.with_user(self.exporter).write({"efn_batch_payment_id": batch.id})
        with self.assertRaises(UserError):
            self.payments[0].with_user(self.exporter).write({"efn_batch_payment_id": False})
        with self.assertRaises(UserError):
            Batch.write({"export_format_id": self.fmt_mandiri.id})
        self.assertEqual(batch.payment_ids, self.payments)

    def test_digest_mismatch_refuses_export(self):
        batch = self._batch()
        other_bank = self.env["res.partner.bank"].create({"acc_number": "9900000002", "partner_id": self.vendor.id})
        self.payments[1].partner_bank_id = other_bank
        with self.assertRaisesRegex(UserError, "berubah sejak divalidasi"):
            batch.with_user(self.exporter).action_generate_export_file()
        self.assertFalse(self._export_attachments(batch))

    def test_other_user_cannot_read_export_attachment(self):
        batch = self._batch()
        self._export(batch)
        attachment = self._export_attachments(batch)
        self.assertFalse(attachment.res_model, "tidak tertaut ke batch -> tidak tampil di chatter")
        self.assertEqual(attachment.create_uid, self.exporter)
        reader = new_test_user(
            self.env,
            login="efn_batch_reader",
            groups="base.group_user,account.group_account_user,account.group_account_readonly",
            company_id=self.company.id,
            company_ids=[Command.set(self.company.ids)],
        )
        self.assertFalse(self.env["ir.attachment"].with_user(reader).search([("id", "=", attachment.id)]))
        with self.assertRaises(AccessError):
            attachment.with_user(reader).read(["raw"])
        self.assertTrue(attachment.with_user(self.exporter).read(["raw"]))

    def test_empty_batch_blocks_validate(self):
        batch = self.env["efn.account.batch.payment"].create({"journal_id": self.journal.id})
        with self.assertRaises(UserError):
            batch.action_validate()

    def test_missing_bank_account_blocks_export(self):
        self.payments[0].partner_bank_id = False
        batch = self._batch()
        with self.assertRaises(UserError):
            batch.with_user(self.exporter).action_generate_export_file()
        self.assertFalse(self._export_attachments(batch))
        self.assertEqual(batch.state, "validated")

    def test_double_batching_blocked(self):
        action = self.payments.action_efn_create_batch()
        batch = self.env["efn.account.batch.payment"].browse(action["res_id"])
        self.assertEqual(batch.payment_ids, self.payments)
        with self.assertRaises(UserError):
            self.payments.action_efn_create_batch()

    # ------------------------------------------------------------------ CSV aman
    def test_memo_with_delimiter_newline_and_formula_stays_one_valid_row(self):
        text = self._export(self._batch())
        self.assertEqual(text.count("\r\n"), 4, "tidak ada baris tambahan akibat CR/LF di memo")
        rows = list(csv.reader(io.StringIO(text, newline="")))
        self.assertEqual([len(row) for row in rows], [6, 6, 6, 6])
        memos = [row[4] for row in rows[1:]]
        self.assertEqual(memos[0], " -23 bayar inv", "awalan '-' diberi spasi (injeksi formula)")
        self.assertEqual(memos[2], "cmd1 2 X")
        for row in rows[1:]:
            for cell in row:
                self.assertFalse(cell.startswith(("=", "+", "-", "@")), cell)

    # ------------------------------------------------------------------ PII & fail-closed
    def test_keyring_locked_refuses_export_without_file(self):
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE res_partner_bank SET acc_number = %s WHERE id = %s",
            ("•••• (kunci PII tidak tersedia)", self.vendor_bank.id),
        )
        self.env.invalidate_all()
        batch = self._batch()
        with self.assertRaisesRegex(UserError, "Kunci PII"):
            batch.with_user(self.exporter).action_generate_export_file()
        self.assertFalse(self._export_attachments(batch))
        self.assertFalse(batch.export_sha256)

    def test_non_digit_account_refused(self):
        self.vendor_bank.acc_number = "ABC-123"
        batch = self._batch()
        with self.assertRaises(UserError):
            batch.with_user(self.exporter).action_generate_export_file()

    # ------------------------------------------------------------------ empat mata & format
    def test_validator_cannot_export(self):
        batch = self._batch()
        with self.assertRaisesRegex(UserError, "Empat mata"):
            batch.action_generate_export_file()

    def test_user_without_manager_group_cannot_export(self):
        batch = self._batch()
        accountant = new_test_user(
            self.env,
            login="efn_batch_accountant",
            groups="base.group_user,account.group_account_user",
            company_id=self.company.id,
            company_ids=[Command.set(self.company.ids)],
        )
        with self.assertRaises(AccessError):
            batch.with_user(accountant).action_generate_export_file()

    def test_unverified_format_refused(self):
        batch = self._batch(self.fmt_mandiri)
        with self.assertRaisesRegex(UserError, "belum diverifikasi"):
            batch.with_user(self.exporter).action_generate_export_file()
        self.fmt_mandiri.verified = True
        text = self._export(batch)
        self.assertEqual(len([line for line in text.split("\r\n") if line]), 4)

    def test_bca_mcm_needs_company_config(self):
        fmt = self.env.ref("efn_account_batch_payment.format_bca_mcm")
        fmt.verified = True
        batch = self._batch(fmt)
        with self.assertRaises(UserError):
            batch.with_user(self.exporter).action_generate_export_file()
        self.env["efn.account.batch.payment.format.company"].create(
            {"format_id": fmt.id, "company_id": self.company.id, "corporate_id": "CORPUJI", "debit_account": "0000-111"}
        )
        rows = list(csv.reader(io.StringIO(self._export(batch), newline="")))
        self.assertEqual(len(rows), 3, "tanpa judul")
        self.assertEqual(rows[0][:2], ["CORPUJI", "0000111"])

    def test_draft_by_manager_clears_export_trail(self):
        batch = self._batch()
        self._export(batch)
        batch.with_user(self.exporter).action_draft()
        self.assertEqual(batch.state, "draft")
        self.assertFalse(batch.export_sha256)
        self.assertFalse(batch.validated_by_id)
        self.assertFalse(any(self.payments.mapped("is_sent")))
        self.assertFalse(self._export_attachments(batch))

    def test_gc_cron_removes_old_files_keeps_hash(self):
        batch = self._batch()
        self._export(batch)
        attachment = self._export_attachments(batch)
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE ir_attachment SET create_date = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(hours=25), attachment.id),
        )
        self.env.invalidate_all()
        self.assertEqual(self.env["efn.account.batch.payment"]._efn_gc_export_files(), 1)
        self.assertFalse(self._export_attachments(batch))
        self.assertTrue(batch.export_sha256, "hash, pengekspor, dan waktu tetap tersimpan")

    # ------------------------------------------------------------------ akses & multi-company
    def test_other_company_cannot_see_batch(self):
        batch = self._batch()
        other = self.setup_other_company(name="PT B uji batch")
        user_b = new_test_user(
            self.env,
            login="efn_batch_user_b",
            groups="base.group_user,account.group_account_user",
            company_id=other["company"].id,
            company_ids=[Command.set(other["company"].ids)],
        )
        self.assertFalse(self.env["efn.account.batch.payment"].with_user(user_b).search([("id", "=", batch.id)]))

    def test_billing_user_cannot_create_batch(self):
        with self.assertRaises(AccessError):
            self.payments.with_user(self.billing).action_efn_create_batch()

    def test_renderer_is_not_rpc_callable(self):
        for name in ("_efn_render", "_efn_rows", "_efn_render_generic"):
            with self.assertRaises(AccessError):
                get_public_method(self.fmt_generic, name)
        self.assertFalse(hasattr(self.fmt_generic, "render"))

    def test_unapproved_payment_refused_when_g60_installed(self):
        if "efn_approval_state" not in self.env["account.payment"]._fields:
            self.skipTest("efn_account_localization (G60) tidak terpasang")
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE account_payment SET efn_approval_state = 'to_approve' WHERE id = %s", (self.payments[0].id,)
        )
        self.env.invalidate_all()
        with self.assertRaises(UserError):
            self.payments.action_efn_create_batch()
