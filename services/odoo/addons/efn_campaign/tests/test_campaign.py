"""G50 (docs/04 §28.7, ADR-047 §3.4, L-155/L-156/L-164): kampanye, voucher per mode, outbox klaim/ack, SoD,
isolasi multi-company, galat berkode 403/422.

Semua data = data uji (tanpa data member nyata). Metode bot dipanggil langsung sebagai user teknis (jalur /json/2
memakai get_public_method yang sama; gateway di luar cakupan test ini)."""

import uuid
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.fields import Command
from odoo.tests import TransactionCase, new_test_user, tagged

PAYLOAD_KEYS = {
    "schema_version",
    "campaign_id",
    "company_code",
    "kind",
    "approval_rev",
    "segment_run_id",
    "segment_code",
    "markdown",
    "channels",
    "title",
    "body",
    "voucher_mode",
    "voucher_program_ref",
    "voucher",
    "budget_idr",
    "cap_per_member_week",
    "valid_from",
    "valid_to",
}


def _rid():
    return str(uuid.uuid4())


@tagged("post_install", "-at_install", "efn")
class TestEfnCampaign(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Company = cls.env["res.company"]
        # Kode SMD/FARM mungkin dipakai data setup; bebaskan di transaksi uji (rollback setelah kelas).
        Company.search([("efn_brand_code", "in", ("SMD", "FARM"))]).write({"efn_brand_code": False})
        Company.flush_model(["efn_brand_code"])
        cls.company = Company.create({"name": "PT Uji Kampanye SMD", "efn_brand_code": "SMD"})
        cls.other = Company.create({"name": "PT Uji Kampanye Lain", "efn_brand_code": "FARM"})

        def user(login, groups, company):
            return new_test_user(cls.env, login=login, groups=groups, company_id=company.id, tz="Asia/Jakarta")

        cls.bot = user("efn_campaign_smd_uji", "base.group_user,efn_campaign.group_campaign_bot", cls.company)
        cls.bot_other = user("efn_campaign_farm_uji", "base.group_user,efn_campaign.group_campaign_bot", cls.other)
        cls.marketer = user("efn_cmp_mkt_uji", "base.group_user,efn_campaign.group_campaign_user", cls.company)
        cls.approver = user("efn_cmp_apr1_uji", "base.group_user,efn_campaign.group_campaign_approver", cls.company)
        cls.approver2 = user("efn_cmp_apr2_uji", "base.group_user,efn_campaign.group_campaign_approver", cls.company)
        cls.approver_other = user(
            "efn_cmp_apr_farm_uji", "base.group_user,efn_campaign.group_campaign_approver", cls.other
        )
        cls.plain = user("efn_cmp_plain_uji", "base.group_user", cls.company)

        reward = {
            "reward_type": "discount",
            "discount": 10,
            "discount_mode": "percent",
            "discount_applicability": "order",
            "required_points": 1,
        }
        Program = cls.env["loyalty.program"]
        cls.coupon_program = Program.with_context(default_program_type="coupons").create(
            {
                "name": "Kupon kampanye (uji)",
                "program_type": "coupons",
                "company_id": cls.company.id,
                "reward_ids": [Command.create(reward)],
            }
        )
        cls.public_code = f"EFNG50{uuid.uuid4().hex[:8].upper()}"
        cls.promo_program = Program.with_context(default_program_type="promo_code").create(
            {
                "name": "Kode promo kampanye (uji)",
                "program_type": "promo_code",
                "trigger": "with_code",
                "company_id": cls.company.id,
                "rule_ids": [Command.create({"code": cls.public_code, "reward_point_mode": "order"})],
                "reward_ids": [Command.create(reward)],
            }
        )
        cls.partner1 = cls.env["res.partner"].create({"name": "Member uji 1", "company_id": cls.company.id})
        cls.partner2 = cls.env["res.partner"].create({"name": "Member uji 2"})
        cls.partner_other = cls.env["res.partner"].create({"name": "Member PT lain", "company_id": cls.other.id})
        cls.warehouse = cls.env["stock.warehouse"].create(
            {"name": "Outlet uji kampanye", "code": "SMG5", "company_id": cls.company.id}
        )
        cls.product = cls.env["product.product"].create({"name": "Yoghurt (uji)", "is_storable": True})

    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_param("efn_campaign.enabled", "true")

    # ------------------------------------------------------------ helper
    def upsert(self, user=None, **over):
        payload = {
            "schema_version": 1,
            "company_code": "SMD",
            "run_id": over.pop("run_id", None) or _rid(),
            "segment_code": "viewed_not_bought",
            "member_count": 120,
            "metrics": {"avg_views": 3.5, "oos_share": 0.2},
            "summary": "Melihat produk susu minimal 3 kali tanpa membeli",
        }
        payload.update(over)
        return self.env["efn.campaign"].with_user(user or self.bot).efn_campaign_proposal_upsert(**payload)

    def proposed(self, editor=None, **vals):
        res = self.upsert(segment_code=vals.pop("segment_code", "viewed_not_bought"))
        camp = self.env["efn.campaign"].browse(res["campaign_id"])
        content = {
            "message_title": "Promo susu",
            "message_body": "Diskon 10% untuk Anda",
            "voucher_mode": "anonymous_code",
            "program_id": self.coupon_program.id,
            "code_qty": 3,
        }
        content.update(vals)
        camp.with_user(editor or self.marketer).write(content)
        return camp

    def approve(self, camp, user=None):
        camp.with_user(user or self.approver).action_efn_approve()
        return camp

    def claim(self, user=None, limit=20):
        return self.env["efn.campaign"].with_user(user or self.bot).efn_campaign_claim(limit=limit)

    def ack(self, ids, user=None):
        return self.env["efn.campaign"].with_user(user or self.bot).efn_campaign_ack(ids)

    def row(self, camp):
        return camp.sudo().outbox_ids.filtered(lambda r: r.approval_rev == camp.approval_rev)

    # ------------------------------------------------------------ pemanggil & sakelar
    def test_caller_must_be_single_company_bot(self):
        for user in (self.plain, self.marketer, self.approver):
            with self.assertRaisesRegex(AccessError, "access_denied"):
                self.claim(user=user)
        self.bot.sudo().write({"company_ids": [Command.link(self.other.id)]})
        with self.assertRaisesRegex(AccessError, "access_denied"):
            self.claim()
        self.bot.sudo().write({"company_ids": [Command.unlink(self.other.id)]})
        self.bot.sudo().write({"group_ids": [Command.link(self.env.ref("efn_campaign.group_campaign_user").id)]})
        with self.assertRaisesRegex(AccessError, "access_denied"):
            self.claim()

    def test_disabled_returns_coded_error(self):
        camp = self.proposed()
        self.env["ir.config_parameter"].sudo().set_param("efn_campaign.enabled", "false")
        with self.assertRaisesRegex(UserError, "efn_campaign:disabled"):
            self.claim()
        with self.assertRaisesRegex(UserError, "efn_campaign:disabled"):
            self.upsert()
        with self.assertRaisesRegex(UserError, "efn_campaign:disabled"):
            self.ack([1])
        with self.assertRaises(UserError):
            self.approve(camp)
        self.assertEqual(camp.state, "proposed")
        self.assertFalse(camp.card_ids)
        # Override per PT menang atas global.
        self.env["ir.config_parameter"].sudo().set_param("efn_campaign.enabled.SMD", "true")
        self.assertEqual(self.claim(), {"items": []})

    # ------------------------------------------------------------ usulan segmen
    def test_proposal_upsert_idempotent(self):
        run_id = _rid()
        first = self.upsert(run_id=run_id)
        self.assertTrue(first["created"])
        camp = self.env["efn.campaign"].browse(first["campaign_id"])
        self.assertEqual((camp.state, camp.origin, camp.kind), ("proposed", "worker", "segment"))
        self.assertEqual(camp.company_id, self.company)
        self.assertEqual(camp.segment_metrics, {"avg_views": 3.5, "oos_share": 0.2})
        again = self.upsert(run_id=run_id)
        self.assertEqual((again["campaign_id"], again["created"], again["updated"]), (camp.id, False, False))
        changed = self.upsert(run_id=run_id, member_count=150)
        self.assertEqual((changed["campaign_id"], changed["updated"]), (camp.id, True))
        self.assertEqual(camp.segment_member_count, 150)
        other_code = self.upsert(run_id=run_id, segment_code="wishlist_idle")
        self.assertNotEqual(other_code["campaign_id"], camp.id)
        count = self.env["efn.campaign"].search_count([("segment_run_id", "=", run_id)])
        self.assertEqual(count, 2)

    def test_proposal_upsert_validation(self):
        cases = [
            ({"run_id": "bukan-uuid"}, "invalid_input"),
            ({"segment_code": "Bad Code"}, "invalid_input"),
            ({"member_count": -1}, "invalid_input"),
            ({"member_count": True}, "invalid_input"),
            ({"metrics": {"x": "teks"}}, "invalid_input"),
            ({"phone": "0812"}, "invalid_input"),
            ({"schema_version": 2}, "unsupported_schema"),
            ({"summary": "contoh 081234567890"}, "pii_suspected"),
            ({"summary": "hubungi a.b@contoh.id"}, "pii_suspected"),
        ]
        for over, code in cases:
            with self.subTest(over=over), self.assertRaisesRegex(UserError, f"efn_campaign:{code}"):
                self.upsert(**over)
        with self.assertRaisesRegex(AccessError, "company_mismatch"):
            self.upsert(company_code="FARM")
        with self.assertRaisesRegex(AccessError, "company_mismatch"):
            self.upsert(user=self.bot_other)

    def test_upsert_after_approval_is_locked(self):
        camp = self.approve(self.proposed())
        res = self.upsert(run_id=camp.segment_run_id, member_count=999)
        self.assertTrue(res["locked"])
        self.assertFalse(res["updated"])
        self.assertEqual(camp.segment_member_count, 120)

    # ------------------------------------------------------------ SoD & guard
    def test_sod_maker_editor_cannot_approve(self):
        camp = self.proposed(editor=self.approver)
        with self.assertRaisesRegex(AccessError, "Pemisahan tugas"):
            self.approve(camp, self.approver)
        with self.assertRaises(AccessError):
            self.approve(camp, self.marketer)
        self.approve(camp, self.approver2)
        self.assertEqual((camp.state, camp.approval_rev, camp.approved_by_id), ("approved", 1, self.approver2))

        manual = (
            self.env["efn.campaign"]
            .with_user(self.approver)
            .create({"name": "Manual", "company_id": self.company.id, "voucher_mode": "public_code"})
        )
        self.assertEqual((manual.state, manual.maker_id), ("draft", self.approver))
        manual.with_user(self.approver).action_efn_propose()
        with self.assertRaisesRegex(AccessError, "Pemisahan tugas"):
            self.approve(manual, self.approver)

    def test_system_fields_guarded(self):
        Campaign = self.env["efn.campaign"]
        with self.assertRaises(AccessError):
            Campaign.with_user(self.plain).create({"name": "X", "company_id": self.company.id})
        with self.assertRaises(AccessError):
            Campaign.with_user(self.marketer).create({"name": "X", "state": "approved"})
        camp = self.proposed()
        with self.assertRaises(AccessError):
            camp.with_user(self.marketer).write({"state": "approved"})
        with self.assertRaises(AccessError):
            camp.with_user(self.marketer).write({"segment_member_count": 1})
        self.assertIn(self.marketer, camp.editor_ids)
        self.approve(camp)
        with self.assertRaises(UserError):
            camp.with_user(self.marketer).write({"message_title": "Ubah"})
        with self.assertRaises(AccessError):
            camp.sudo().outbox_ids.with_user(self.marketer).read(["payload"])

    # ------------------------------------------------------------ voucher per mode
    def test_voucher_anonymous_code(self):
        camp = self.approve(self.proposed(code_qty=3))
        cards = camp.card_ids
        self.assertEqual(len(cards), 3)
        self.assertFalse(cards.filtered("partner_id"))
        self.assertEqual(set(cards.mapped("expiration_date")), {camp.date_to})
        row = self.row(camp)
        self.assertEqual(
            (row.state, row.kind, row.ref_model, row.ref_id), ("queued", "segment", "efn.campaign", camp.id)
        )
        self.assertEqual(row.idempotency_key, f"campaign:SMD:{camp.id}:1")
        payload = row.payload
        self.assertEqual(set(payload), PAYLOAD_KEYS)
        self.assertEqual(payload["voucher_mode"], "anonymous_code")
        self.assertEqual(sorted(payload["voucher"]["codes"]), sorted(cards.mapped("code")))
        self.assertEqual(payload["segment_run_id"], camp.segment_run_id)
        self.assertEqual(payload["channels"], ["inbox", "push"])
        self.assertIsNone(payload["budget_idr"])
        self.assertNotIn("partner", str(payload))

    def test_voucher_public_code(self):
        camp = self.approve(self.proposed(voucher_mode="public_code", program_id=self.promo_program.id, code_qty=100))
        self.assertFalse(camp.card_ids)
        self.assertTrue(self.promo_program.limit_usage)
        self.assertEqual(self.promo_program.max_usage, 100)
        voucher = self.row(camp).payload["voucher"]
        self.assertEqual(voucher, {"mode": "public_code", "code": self.public_code, "quota": 100})
        again = self.proposed(
            segment_code="search_no_result", voucher_mode="public_code", program_id=self.promo_program.id, code_qty=5
        )
        with self.assertRaisesRegex(UserError, "sudah dipakai"):
            self.approve(again)

    def test_voucher_member_bound_and_mode_lock(self):
        with self.assertRaises(ValidationError):
            self.proposed(voucher_mode="anonymous_code", partner_ids=[Command.set([self.partner1.id])])
        camp = self.proposed(
            voucher_mode="member_bound", partner_ids=[Command.set([self.partner1.id, self.partner2.id])]
        )
        with self.assertRaises(UserError):  # ValidationError subkelas UserError
            camp.with_user(self.marketer).write({"partner_ids": [Command.link(self.partner_other.id)]})
        self.approve(camp)
        cards = camp.card_ids
        self.assertEqual(set(cards.mapped("partner_id").ids), {self.partner1.id, self.partner2.id})
        voucher = self.row(camp).payload["voucher"]
        self.assertEqual(voucher["mode"], "member_bound")
        self.assertEqual({c["partner_id"] for c in voucher["cards"]}, {self.partner1.id, self.partner2.id})
        self.assertTrue(all(set(c) == {"partner_id", "code"} for c in voucher["cards"]))
        self.assertNotIn("Member uji", str(self.row(camp).payload))
        # Batal -> kartu nonaktif, outbox gagal (cancelled); reset ke draft -> mode tetap terkunci.
        with self.assertRaises(AccessError):
            camp.with_user(self.marketer).action_efn_cancel()
        camp.with_user(self.approver2).action_efn_cancel()
        self.assertEqual(camp.state, "cancelled")
        self.assertFalse(cards.exists().filtered("active"))
        row = camp.sudo().outbox_ids
        self.assertEqual((row.state, row.last_error), ("failed", "cancelled"))
        camp.with_user(self.marketer).action_efn_reset_draft()
        with self.assertRaisesRegex(UserError, "dikunci"):
            camp.with_user(self.marketer).write({"voucher_mode": "anonymous_code"})
        camp.with_user(self.marketer).write({"message_title": "Promo baru"})

    def test_reapproval_increments_rev(self):
        camp = self.approve(self.proposed())
        camp.with_user(self.approver2).action_efn_cancel()
        camp.with_user(self.marketer).action_efn_reset_draft()
        camp.with_user(self.marketer).action_efn_propose()
        self.approve(camp, self.approver2)
        self.assertEqual(camp.approval_rev, 2)
        self.assertEqual(self.row(camp).idempotency_key, f"campaign:SMD:{camp.id}:2")
        self.assertEqual(len(camp.sudo().outbox_ids), 2)

    # ------------------------------------------------------------ klaim / ack
    def test_claim_ack_flow(self):
        c1 = self.approve(self.proposed(segment_code="viewed_not_bought"))
        c2 = self.approve(self.proposed(segment_code="wishlist_idle"))
        first = self.claim(limit=1)["items"]
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["attempts"], 1)
        self.assertIn("payload", first[0])
        second = self.claim()["items"]
        self.assertEqual(len(second), 1)
        self.assertNotEqual(first[0]["id"], second[0]["id"])
        self.assertEqual(self.claim(), {"items": []})
        ids = [first[0]["id"], second[0]["id"]]
        res = self.ack(ids)
        self.assertEqual((sorted(res["acked"]), res["already_done"], res["rejected"]), (sorted(ids), [], []))
        self.assertEqual((c1.state, c2.state), ("sent", "sent"))
        self.assertTrue(c1.sent_at)
        again = self.ack(ids + [999999999])
        self.assertEqual(sorted(again["already_done"]), sorted(ids))
        self.assertEqual(again["rejected"], [{"id": 999999999, "code": "not_found"}])
        c3 = self.approve(self.proposed(segment_code="oos_viewed"))
        self.assertEqual(self.ack([self.row(c3).id])["rejected"], [{"id": self.row(c3).id, "code": "not_claimed"}])

    def test_claim_ack_input_errors(self):
        for limit in (0, 21, True, "5"):
            with self.subTest(limit=limit), self.assertRaisesRegex(UserError, "invalid_input"):
                self.claim(limit=limit)
        with self.assertRaisesRegex(UserError, "invalid_input"):
            self.ack([])
        with self.assertRaisesRegex(UserError, "invalid_input"):
            self.ack(["1"])
        with self.assertRaisesRegex(UserError, "too_many_items"):
            self.ack(list(range(1, 22)))

    def test_reclaim_cron_and_max_attempts(self):
        camp = self.approve(self.proposed())
        row = self.row(camp)
        Outbox = self.env["efn.campaign.outbox"]
        for attempt in range(1, 6):
            items = self.claim()["items"]
            self.assertEqual([i["id"] for i in items], [row.id])
            self.assertEqual(row.attempts, attempt)
            Outbox._cron_efn_reclaim()
            self.assertEqual(row.state, "claimed", "klaim < 10 menit tidak diambil ulang")
            row.sudo().write({"claimed_at": fields.Datetime.now() - timedelta(minutes=11)})
            Outbox._cron_efn_reclaim()
            self.assertEqual(row.state, "queued" if attempt < 5 else "failed")
        self.assertEqual(row.last_error, "max_attempts")
        activity = camp.activity_ids.filtered(lambda a: a.user_id == self.approver)
        self.assertTrue(activity)
        self.assertEqual(self.claim(), {"items": []})
        # BFF yang terlambat ack tetap diterima (sudah terbit; hilir idempoten).
        self.assertEqual(self.ack([row.id])["acked"], [row.id])

    def test_claim_skips_expired_campaign(self):
        camp = self.approve(self.proposed())
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE efn_campaign SET date_to = %s WHERE id = %s",
            (fields.Date.today() - timedelta(days=2), camp.id),
        )
        camp.invalidate_recordset(["date_to"])
        self.assertEqual(self.claim(), {"items": []})
        self.assertEqual((self.row(camp).state, self.row(camp).last_error), ("failed", "expired"))

    # ------------------------------------------------------------ multi-company
    def test_multi_company_isolation(self):
        camp = self.approve(self.proposed())
        row = self.row(camp)
        self.assertEqual(self.claim(user=self.bot_other), {"items": []})
        self.assertEqual(self.ack([row.id], user=self.bot_other)["rejected"], [{"id": row.id, "code": "not_found"}])
        self.assertEqual(row.state, "queued")
        Campaign = self.env["efn.campaign"].with_user(self.approver_other)
        self.assertFalse(Campaign.search([("id", "=", camp.id)]))
        other = self.proposed(segment_code="price_sensitive")
        with self.assertRaises(AccessError):
            self.approve(other, self.approver_other)
        with self.assertRaises(UserError):  # ValidationError subkelas UserError
            self.proposed(segment_code="cart_abandon", program_id=self._other_program().id)

    def _other_program(self):
        return (
            self.env["loyalty.program"]
            .with_context(default_program_type="coupons")
            .create(
                {
                    "name": "Kupon PT lain (uji)",
                    "program_type": "coupons",
                    "company_id": self.other.id,
                    "reward_ids": [Command.create({"reward_type": "discount", "discount": 5, "required_points": 1})],
                }
            )
        )

    # ------------------------------------------------------------ markdown (G45, generik)
    def test_markdown_proposal_generic_ref(self):
        Campaign = self.env["efn.campaign"]
        date_to = fields.Date.today() + timedelta(days=3)
        camp = Campaign._efn_propose_markdown(
            self.company, "efn.lot.markdown", 7, [self.product.id], [self.warehouse.id], date_to
        )
        self.assertEqual((camp.kind, camp.state, camp.origin), ("markdown", "proposed", "markdown"))
        same = Campaign._efn_propose_markdown(
            self.company, "efn.lot.markdown", 7, [self.product.id], [self.warehouse.id], date_to
        )
        self.assertEqual(same, camp)
        with self.assertRaises(ValidationError):
            Campaign._efn_propose_markdown(self.company, "bukan model!", 7, [], [], date_to)
        camp.with_user(self.marketer).write({"message_title": "Markdown yoghurt", "message_body": "Harga turun"})
        self.approve(camp)
        row = self.row(camp)
        self.assertEqual((row.kind, row.ref_model, row.ref_id), ("markdown", "efn.lot.markdown", 7))
        payload = row.payload
        self.assertIsNone(payload["voucher"])
        self.assertEqual(
            payload["markdown"],
            {"product_ids": [self.product.id], "outlet_codes": ["SMG5"], "valid_to": fields.Date.to_string(date_to)},
        )
