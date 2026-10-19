"""E-47c review akses bulanan (docs/08 §12.5b; L-65; test wajib 16 + cron bulanan)."""

from datetime import date

from odoo.exceptions import AccessError, UserError
from odoo.fields import Command
from odoo.tests import tagged

from .common import REASON, EfnRoleCase


@tagged("post_install", "-at_install", "efn")
class TestEfnAccessReview(EfnRoleCase):
    def _snapshot_user(self):
        """User GL: g_role1 dari peran, g_direct langsung (menyiratkan g_implied), g_a + g_b dengan pengecualian."""
        self.g_implied = self.Groups.create({"name": "EFN Uji Tersirat"})
        self.g_direct = self.Groups.create(
            {"name": "EFN Uji Langsung", "implied_ids": [Command.link(self.g_implied.id)]}
        )
        user = self._make_user("efn_role_snap", companies=self.gl, extra=self.g_direct | self.g_a)
        self.role = self._role("Peran Snapshot GL", self.g_role1)
        self._assign(self.role, user)
        self.exc = self.Exc.with_user(self.superadmin).create(
            {"rule_id": self.rule_ab.id, "user_id": user.id, "reason": REASON}
        )
        user.write({"group_ids": [Command.link(self.g_b.id)]})
        return user

    def test_16a_snapshot_lines_sources_sod_exception(self):
        user = self._snapshot_user()
        review = self.Review.create({"company_id": self.gl.id, "period": "2001-02"})
        self.assertEqual(review.state, "draft")
        self.assertTrue(review.snapshot_date)
        lines = review.line_ids.filtered(lambda line: line.user_id == user)
        by_group = {line.group_id: line for line in lines}
        # Satu baris per grup efektif (all_group_ids).
        self.assertEqual(set(by_group), set(user.all_group_ids))
        self.assertEqual(len(lines), len(user.all_group_ids))
        self.assertEqual(by_group[self.g_role1].source, "role")
        self.assertEqual(by_group[self.g_role1].via_role_id, self.role)
        self.assertEqual(by_group[self.g_direct].source, "direct")
        self.assertEqual(by_group[self.g_implied].source, "implied")
        self.assertEqual(by_group[self.group_user].source, "direct")
        self.assertIn("Peran Snapshot GL", by_group[self.g_role1].role_names)
        self.assertTrue(by_group[self.g_role1].has_company_role)
        self.assertEqual(by_group[self.g_role1].user_login, user.login)
        sod_line = by_group[self.g_a]
        self.assertEqual(sod_line.sod_rule_ids, self.rule_ab)
        self.assertEqual(sod_line.sod_codes, "UJI-AB")
        self.assertEqual(sod_line.exception_id, self.exc)
        self.assertEqual(sod_line.exception_state, "effective")
        self.assertFalse(sod_line.sod_unexcepted)
        self.assertFalse(by_group[self.g_role1].sod_rule_ids)
        self.assertGreaterEqual(review.exception_count, 1)
        self.assertGreaterEqual(review.user_count, 1)
        self.assertGreaterEqual(review.direct_grant_count, 1)
        # Snapshot tidak berubah walau grup berubah kemudian.
        count = len(review.line_ids)
        user.write({"group_ids": [Command.unlink(self.g_direct.id)]})
        self.assertEqual(len(review.line_ids), count)
        self.assertTrue(review.line_ids.filtered(lambda line: line.group_id == self.g_direct and line.user_id == user))

    def test_16b_expired_exception_marked_in_review(self):
        user = self._snapshot_user()
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE efn_sod_exception SET date_from = '2001-01-01', date_to = '2001-01-31' WHERE id = %s",
            [self.exc.id],
        )
        self.exc.invalidate_recordset()
        review = self.Review.create({"company_id": self.gl.id, "period": "2001-03"})
        sod_line = review.line_ids.filtered(lambda line: line.user_id == user and line.group_id == self.g_a)
        self.assertEqual(sod_line.exception_state, "expired")
        self.assertTrue(sod_line.sod_unexcepted)
        self.assertGreaterEqual(review.expired_exception_count, 1)
        self.assertGreaterEqual(review.sod_violation_count, 1)

    def test_16c_workflow_done_is_read_only(self):
        self._snapshot_user()
        review = self.Review.create({"company_id": self.gl.id, "period": "2001-04"})
        auditor_review = review.with_user(self.auditor)
        auditor_review.write({"notes": "Temuan awal auditor."})
        with self.assertRaises(AccessError):
            review.with_user(self.access_admin).action_efn_start_review()
        auditor_review.action_efn_start_review()
        self.assertEqual(review.state, "in_review")
        with self.assertRaises(UserError):
            auditor_review.write({"state": "done"})
        auditor_review.action_efn_done()
        self.assertEqual(review.state, "done")
        self.assertEqual(review.reviewer_id, self.auditor)
        self.assertTrue(review.reviewed_on)
        with self.assertRaises(UserError):
            auditor_review.write({"notes": "ubah setelah selesai"})
        with self.assertRaises(UserError):
            review.write({"notes": "sudo pun ditolak"})
        with self.assertRaises(UserError):
            review.action_efn_snapshot()
        with self.assertRaises(UserError):
            review.line_ids[:1].write({"source": "role"})
        with self.assertRaises(UserError):
            review.line_ids[:1].unlink()
        with self.assertRaises(UserError):
            review.unlink()
        with self.assertRaises(UserError):
            self.env["efn.access.review.line"].create({"review_id": review.id, "source": "direct"})

    def test_16d_auditor_without_access_rights_reads_but_no_generic_export(self):
        self._snapshot_user()
        review = self.Review.create({"company_id": self.gl.id, "period": "2001-05"})
        self.assertFalse(self.auditor.has_group("base.group_erp_manager"))
        self.assertFalse(self.auditor.has_group("base.group_allow_export"), "SEC-155: tanpa hak ekspor generik")
        Line = self.env["efn.access.review.line"].with_user(self.auditor)
        lines = Line.search([("review_id", "=", review.id)])
        self.assertEqual(len(lines), len(review.line_ids))
        # Ekspor generik ORM (sumber /web/export/*) ditolak untuk Auditor; ekspor lewat action_efn_export_csv.
        with self.assertRaises(UserError):
            lines.export_data(["user_login", "group_full_name"])
        # Header review terbaca auditor juga.
        self.assertEqual(self.Review.with_user(self.auditor).search([("id", "=", review.id)]), review)

    def test_16e_monthly_cron_creates_previous_period_and_notifies(self):
        created = self.Review._cron_efn_access_review_monthly(today=date(2001, 7, 1))
        gl_review = created.filtered(lambda r: r.company_id == self.gl)
        self.assertEqual(len(gl_review), 1)
        self.assertEqual(gl_review.period, "2001-06")
        activities = (
            self.env["mail.activity"]
            .sudo()
            .search(
                [
                    ("res_model", "=", "efn.access.review"),
                    ("res_id", "=", gl_review.id),
                    ("user_id", "=", self.auditor.id),
                ]
            )
        )
        self.assertTrue(activities)
        # Idempoten + di luar jendela tanggal 1-3 tidak membuat apa pun.
        self.assertFalse(self.Review._cron_efn_access_review_monthly(today=date(2001, 7, 2)))
        self.assertFalse(self.Review._cron_efn_access_review_monthly(today=date(2001, 8, 15)))
