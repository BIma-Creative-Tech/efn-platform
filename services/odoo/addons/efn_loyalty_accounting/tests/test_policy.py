"""Kebijakan Poin EFN per PT (docs/04 §13.1 no. 1-2): versi monoton, empat mata, read-only, L-62, record rule, audit."""

from datetime import timedelta

from psycopg2 import IntegrityError

from odoo import fields
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
class TestEfnLoyaltyPolicy(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Company = cls.env["res.company"].sudo()
        cls.gl = Company.create({"name": "PT GL Uji Poin"})
        cls.pb = Company.create({"name": "PT PB Uji Poin"})
        cls.efn = Company.create({"name": "Holding EFN Uji Poin"})
        for company, code in ((cls.gl, "GL"), (cls.pb, "PB"), (cls.efn, "EFN")):
            assign_brand_code(cls.env, company, code)
        env = cls.env
        cls.lm = make_user(env, "efn_lp_lm", [LOYALTY_MANAGER], [cls.gl, cls.pb])
        cls.lm2 = make_user(env, "efn_lp_lm2", [LOYALTY_MANAGER], [cls.gl])
        cls.lm_pb = make_user(env, "efn_lp_lm_pb", [LOYALTY_MANAGER], [cls.pb])
        cls.lm_efn = make_user(env, "efn_lp_lm_efn", [LOYALTY_MANAGER], [cls.efn])
        cls.am = make_user(env, "efn_lp_am", [ACCOUNT_MANAGER], [cls.gl])
        cls.am_pb = make_user(env, "efn_lp_am_pb", [ACCOUNT_MANAGER], [cls.pb])
        cls.am_efn = make_user(env, "efn_lp_am_efn", [ACCOUNT_MANAGER], [cls.efn])
        cls.am2 = make_user(env, "efn_lp_am2", [ACCOUNT_MANAGER], [cls.gl])
        # Catatan: user yang memegang Loyalty Manager + Accounting Manager sekaligus dicegah SoD efn_role (§13.3);
        # empat mata per record di sini diuji dengan penulisan ber-sudo atas nama penyetuju (uid asli tercatat).

    def _policy(self, user=None, company=None, **vals):
        user = user or self.lm
        Policy = self.env["efn.loyalty.policy"].with_user(user)
        if company:
            Policy = Policy.with_company(company)
            vals.setdefault("company_id", company.id)
        return Policy.create(vals)

    # ------------------------------------------------------------- versi
    def test_version_monotonic_per_company_and_not_user_settable(self):
        first = self._policy()
        second = self._policy(earn_idr_per_point=12_000)
        other = self._policy(company=self.pb)
        self.assertEqual((first.version, second.version, other.version), (1, 2, 1))
        self.assertEqual((first.company_code, other.company_code), ("GL", "PB"))
        self.assertEqual(first.maker_id, self.lm)
        with self.assertRaises(AccessError):
            self._policy(version=99)
        with self.assertRaises(AccessError):
            second.write({"version": 7})
        # Ditolak tetap memakai nomornya; versi berikut tetap naik (tidak pernah dipakai ulang).
        second.with_user(self.am).action_efn_reject()
        self.assertEqual(self._policy().version, 3)

    # -------------------------------------------------------- empat mata
    def test_maker_cannot_approve_own_policy(self):
        """Pembuat = penyetuju ditolak (mis. kode server ber-sudo atas nama Accounting Manager)."""
        policy = self.env["efn.loyalty.policy"].with_user(self.am).sudo().create({"company_id": self.gl.id})
        self.assertEqual(policy.maker_id, self.am)
        with self.assertRaises(UserError):
            policy.with_user(self.am).action_efn_approve()
        with self.assertRaises(UserError):
            policy.with_user(self.am).action_efn_reject()
        self.assertEqual(policy.state, "draft")
        policy.with_user(self.am2).action_efn_approve()
        self.assertEqual(policy.approved_by_id, self.am2)

    def test_last_editor_cannot_approve(self):
        policy = self._policy()
        policy.with_user(self.am).sudo().write({"earn_idr_per_point": 15_000})
        self.assertEqual((policy.maker_id, policy.last_editor_id), (self.lm, self.am))
        with self.assertRaises(UserError):
            policy.with_user(self.am).action_efn_approve()
        policy.with_user(self.lm).write({"redeem_min_points": 60})
        self.assertEqual(policy.last_editor_id, self.lm)
        policy.with_user(self.am2).action_efn_approve()
        self.assertEqual((policy.state, policy.approved_by_id), ("approved", self.am2))

    def test_any_intermediate_editor_cannot_approve(self):
        """SEC-157: A (LM) membuat; C (Accounting Manager, lewat kode ber-sudo atas namanya) menaikkan nilai; A
        menyunting catatan (penyunting terakhir = A) -> C tetap ditolak karena pernah menyunting draft."""
        policy = self._policy()
        policy.with_user(self.am).sudo().write({"earn_idr_per_point": 20_000})
        policy.with_user(self.lm).write({"note": "catatan uji"})
        self.assertEqual(policy.last_editor_id, self.lm)
        self.assertEqual(policy.editor_ids, self.lm | self.am)
        for action in ("action_efn_approve", "action_efn_reject"):
            with self.subTest(action=action), self.assertRaises(UserError):
                getattr(policy.with_user(self.am), action)()
        self.assertEqual(policy.state, "draft")
        # Himpunan penyunting tidak bisa dihapus/diubah pengguna (field sistem).
        with self.assertRaises(AccessError):
            policy.with_user(self.lm).write({"editor_ids": [(5, 0, 0)]})
        with self.assertRaises(AccessError):
            self.env["efn.loyalty.policy"].with_user(self.lm).create({"editor_ids": [(6, 0, [self.lm2.id])]})
        policy.with_user(self.am2).action_efn_approve()
        self.assertEqual((policy.state, policy.approved_by_id), ("approved", self.am2))

    def test_only_accounting_manager_of_same_company_approves(self):
        policy = self._policy()
        with self.assertRaises(AccessError):
            policy.with_user(self.lm2).action_efn_approve()  # Loyalty Manager bukan penyetuju
        with self.assertRaises(AccessError):
            policy.with_user(self.am_pb).action_efn_approve()  # Accounting Manager PT lain
        self.assertEqual(policy.state, "draft")
        policy.with_user(self.am).action_efn_approve()
        self.assertEqual(policy.state, "approved")
        outbox = policy.sudo().outbox_ids
        self.assertEqual((outbox.idempotency_key, outbox.state), ("loyalty_policy:GL:1", "pending"))
        with self.assertRaises(UserError):
            policy.with_user(self.am).action_efn_approve()  # sudah diputuskan

    def test_accounting_manager_cannot_create_or_edit(self):
        with self.assertRaises(AccessError):
            self._policy(user=self.am)
        policy = self._policy()
        with self.assertRaises(AccessError):
            policy.with_user(self.am).write({"earn_idr_per_point": 1})

    # ------------------------------------------------------- read-only
    def test_approved_policy_is_read_only_and_not_deletable(self):
        draft = self._policy()
        draft.with_user(self.lm).unlink()  # draft boleh dihapus pembuat
        policy = self._policy()
        policy.with_user(self.am).action_efn_approve()
        for env_policy in (policy.with_user(self.lm), policy.sudo()):
            with self.assertRaises(UserError):
                env_policy.write({"earn_idr_per_point": 1})
            with self.assertRaises(UserError):
                env_policy.write({"note": "ubah"})
            with self.assertRaises(UserError):
                env_policy.unlink()
        with self.assertRaises(AccessError):
            policy.with_user(self.lm).write({"state": "draft"})
        with self.assertRaises(AccessError):
            policy.with_user(self.lm).write({"approved_by_id": self.lm.id})
        with self.assertRaises(UserError):
            policy.with_user(self.lm).copy()
        self.assertEqual((policy.state, policy.earn_idr_per_point), ("approved", 10_000))

    def test_effective_from_never_retroactive(self):
        past = fields.Datetime.now() - timedelta(days=3)
        future = fields.Datetime.now() + timedelta(days=3)
        late = self._policy(effective_from=past)
        later = self._policy(effective_from=future)
        (late | later).with_user(self.am).action_efn_approve()
        self.assertGreaterEqual(late.effective_from, late.approved_at)
        self.assertLess(abs(later.effective_from - future), timedelta(seconds=1))

    def test_new_draft_prefilled_from_latest_approved(self):
        policy = self._policy(earn_idr_per_point=12_345, redeem_max_pct=40)
        policy.with_user(self.am).action_efn_approve()
        Policy = self.env["efn.loyalty.policy"].with_user(self.lm)
        defaults = Policy.default_get(["earn_idr_per_point", "redeem_max_pct"])
        self.assertEqual(defaults, {"earn_idr_per_point": 12_345, "redeem_max_pct": 40})

    # ------------------------------------------------------- nilai / L-62
    def test_point_value_only_on_global_policy(self):
        with self.assertRaises(ValidationError):
            self._policy(point_value_idr=100)  # PT GL: L-62
        with self.assertRaises(ValidationError):
            self._policy(user=self.lm_efn, point_value_idr=0)  # global wajib nilai tukar
        global_policy = self._policy(user=self.lm_efn, point_value_idr=100)
        self.assertTrue(global_policy.is_global)
        global_policy.with_user(self.am_efn).action_efn_approve()
        body = global_policy._efn_publish_body()
        self.assertEqual((body["company_code"], body["point_value_idr"]), ("EFN", 100))
        self.assertNotIn("point_value_idr", self._policy()._efn_publish_body())

    # ------------------------------------------- W-17 parameter hangus (G22)
    def test_inactivity_params_only_on_global_with_defaults(self):
        """D-22-13: parameter hangus hanya di kebijakan global EFN; PT lain 0/kosong dan tidak dikirim."""
        global_policy = self._policy(user=self.lm_efn)
        self.assertEqual(
            (global_policy.inactivity_expiry_months, global_policy.expiry_notice_days, global_policy.activity_epoch),
            (12, 30, False),
        )
        gl_policy = self._policy()
        self.assertEqual(
            (gl_policy.inactivity_expiry_months, gl_policy.expiry_notice_days, gl_policy.activity_epoch), (0, 0, False)
        )
        for vals in (
            {"inactivity_expiry_months": 12},
            {"expiry_notice_days": 30},
            {"activity_epoch": fields.Date.to_date("2026-11-01")},
        ):
            with self.subTest(vals=vals), self.assertRaises(ValidationError):
                self._policy(**vals)
        gl_policy.with_user(self.am).action_efn_approve()
        params = gl_policy._efn_publish_body()["params"]
        for name in ("inactivity_expiry_months", "expiry_notice_days", "activity_epoch"):
            self.assertNotIn(name, params)

    def test_inactivity_params_default_follows_record_company(self):
        """Loyalty Manager multi-company dengan company aktif GL membuat kebijakan EFN -> default W-17 dari EFN."""
        lm_multi = make_user(self.env, "efn_lp_lm_multi", [LOYALTY_MANAGER], [self.gl, self.efn])
        policy = (
            self.env["efn.loyalty.policy"]
            .with_user(lm_multi)
            .create({"company_id": self.efn.id, "point_value_idr": 100})
        )
        self.assertTrue(policy.is_global)
        self.assertEqual((policy.inactivity_expiry_months, policy.expiry_notice_days), (12, 30))
        # Company aktif EFN (default lambda W-17 = 12/30), record GL -> tetap 0/0.
        gl_policy = (
            self.env["efn.loyalty.policy"]
            .with_user(lm_multi)
            .with_context(allowed_company_ids=[self.efn.id, self.gl.id])
            .create({"company_id": self.gl.id})
        )
        self.assertEqual((gl_policy.inactivity_expiry_months, gl_policy.expiry_notice_days), (0, 0))

    def test_inactivity_params_bounds(self):
        for vals in (
            {"inactivity_expiry_months": 0},
            {"inactivity_expiry_months": 61},
            {"expiry_notice_days": 6},
            {"expiry_notice_days": 91},
            {"inactivity_expiry_months": 1, "expiry_notice_days": 30},  # pemberitahuan >= masa tidak aktif
        ):
            with self.subTest(vals=vals), self.assertRaises(ValidationError):
                self._policy(user=self.lm_efn, **vals)
        # CHECK SQL _inactivity_non_negative jalan sebelum constraint Python (assertRaises Odoo: bukan tuple).
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError):
            self._policy(user=self.lm_efn, inactivity_expiry_months=-1)
            self.env.flush_all()
        edge = self._policy(user=self.lm_efn, inactivity_expiry_months=60, expiry_notice_days=90)
        self.assertEqual((edge.inactivity_expiry_months, edge.expiry_notice_days), (60, 90))
        low = self._policy(user=self.lm_efn, inactivity_expiry_months=1, expiry_notice_days=7)
        self.assertEqual(low.inactivity_expiry_months, 1)
        draft = self._policy(user=self.lm_efn)
        with self.assertRaises(ValidationError):
            draft.with_user(self.lm_efn).write({"expiry_notice_days": 120})

    def test_inactivity_params_four_eyes_read_only_and_published(self):
        """Ikut empat mata (penyunting tidak boleh menyetujui), read-only setelah approved (termasuk sudo), dikirim
        di ``params`` body EFN, dan menjadi prefill versi berikutnya."""
        epoch = fields.Date.to_date("2026-11-01")
        policy = self._policy(user=self.lm_efn, point_value_idr=100)
        policy.with_user(self.am_efn).sudo().write({"inactivity_expiry_months": 18})
        self.assertIn(self.am_efn, policy.editor_ids)
        with self.assertRaises(UserError):
            policy.with_user(self.am_efn).action_efn_approve()
        policy.with_user(self.lm_efn).write({"activity_epoch": epoch, "expiry_notice_days": 45})
        am_efn2 = make_user(self.env, "efn_lp_am_efn2", [ACCOUNT_MANAGER], [self.efn])
        policy.with_user(am_efn2).action_efn_approve()
        self.assertEqual(policy.state, "approved")
        for env_policy in (policy.with_user(self.lm_efn), policy.sudo()):
            for vals in ({"inactivity_expiry_months": 24}, {"expiry_notice_days": 30}, {"activity_epoch": False}):
                with self.subTest(vals=vals), self.assertRaises(UserError):
                    env_policy.write(vals)
        body = policy._efn_publish_body()
        self.assertEqual(body["company_code"], "EFN")
        self.assertEqual(body["schema_version"], 1)
        self.assertEqual(
            {k: body["params"][k] for k in ("inactivity_expiry_months", "expiry_notice_days", "activity_epoch")},
            {"inactivity_expiry_months": 18, "expiry_notice_days": 45, "activity_epoch": "2026-11-01"},
        )
        self.assertIs(type(body["params"]["inactivity_expiry_months"]), int)
        defaults = (
            self.env["efn.loyalty.policy"]
            .with_user(self.lm_efn)
            .default_get(["inactivity_expiry_months", "expiry_notice_days", "activity_epoch"])
        )
        self.assertEqual(
            (
                defaults["inactivity_expiry_months"],
                defaults["expiry_notice_days"],
                fields.Date.to_date(defaults["activity_epoch"]),
            ),
            (18, 45, epoch),
        )
        # Audit efn_audit mencatat perubahan parameter W-17 (nilai lama/baru).
        changed = (
            self.env["efn.audit.log"]
            .sudo()
            .search(
                [("model_name", "=", "efn.loyalty.policy"), ("res_id", "=", policy.id), ("operation", "=", "write")]
            )
            .line_ids.mapped("field_name")
        )
        self.assertIn("inactivity_expiry_months", changed)
        self.assertIn("activity_epoch", changed)

    def test_value_bounds(self):
        for vals in (
            {"redeem_max_pct": 101},
            {"redeem_max_pct": 0},
            {"earn_idr_per_point": 0},
            {"redeem_min_points": 0},
            {"max_points_per_order": 0},
        ):
            with self.subTest(vals=vals), self.assertRaises(ValidationError):
                self._policy(**vals)
        # Negatif ditolak CHECK SQL `_non_negative` (pertahanan berlapis) sebelum validasi Python.
        for vals in ({"daily_max_points_member": -1}, {"company_hourly_max": -5}):
            with self.subTest(vals=vals), mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError):
                self._policy(**vals)
                self.env.flush_all()
        policy = self._policy(daily_max_points_member=0, pos_config_hourly_max=0, company_hourly_max=0)
        self.assertEqual(policy.daily_max_points_member, 0)  # 0 = mati (plafon env BFF tetap berlaku)

    def test_company_without_code_rejected(self):
        bare = self.env["res.company"].sudo().create({"name": "PT Tanpa Kode Poin"})
        self.lm.sudo().write({"company_ids": [(4, bare.id)]})
        with self.assertRaises(ValidationError):
            self._policy(company=bare)

    def test_company_cannot_be_moved(self):
        policy = self._policy()
        with self.assertRaises(UserError):
            policy.with_user(self.lm).with_company(self.pb).write({"company_id": self.pb.id})

    # ------------------------------------------------------ record rule
    def test_record_rule_multi_company(self):
        gl_policy = self._policy()
        pb_policy = self._policy(company=self.pb)
        Policy = self.env["efn.loyalty.policy"].with_user(self.lm_pb)
        self.assertEqual(Policy.search([("company_code", "in", ("GL", "PB"))]), pb_policy.with_user(self.lm_pb))
        with self.assertRaises(AccessError):
            gl_policy.with_user(self.lm_pb).read(["earn_idr_per_point"])
        Outbox = self.env["efn.loyalty.policy.outbox"]
        gl_policy.with_user(self.am).action_efn_approve()
        self.assertFalse(Outbox.with_user(self.am_pb).search([("policy_id", "=", gl_policy.id)]))
        self.assertTrue(Outbox.with_user(self.am).search([("policy_id", "=", gl_policy.id)]))

    # ----------------------------------------------------------- audit
    def test_policy_changes_are_audited(self):
        policy = self._policy()
        policy.with_user(self.lm).write({"redeem_min_points": 75})
        policy.with_user(self.am).action_efn_approve()
        logs = (
            self.env["efn.audit.log"]
            .sudo()
            .search([("model_name", "=", "efn.loyalty.policy"), ("res_id", "=", policy.id)])
        )
        self.assertIn("create", logs.mapped("operation"))
        changed = logs.filtered(lambda log: log.operation == "write").line_ids.mapped("field_name")
        self.assertIn("redeem_min_points", changed)
        self.assertIn("state", changed)
        self.assertIn("approved_by_id", changed)
        write_users = logs.filtered(lambda log: log.operation == "write").mapped("user_id")
        self.assertIn(self.am, write_users)  # uid asli walau persetujuan ditulis via sudo
