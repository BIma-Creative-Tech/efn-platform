"""Publikasi kebijakan MGM Odoo -> BFF (docs/04 §20.1, G31): body kontrak, tanda tangan, status published, 422 final,
tanpa kunci = tertahan. Mesin outbox diwarisi dari efn.loyalty.policy.outbox (tabel sendiri). BFF di-mock."""

import json
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from odoo.addons.efn_loyalty_accounting.models import efn_loyalty_policy_outbox as outbox_module
from odoo.addons.efn_loyalty_accounting.models import signing
from odoo.addons.efn_loyalty_accounting.tests.common import (
    ACCOUNT_MANAGER,
    LOYALTY_MANAGER,
    FakeBffMixin,
    SigningKeyMixin,
    assign_brand_code,
    make_user,
)
from odoo.addons.efn_mgm_policy.models.efn_mgm_policy import PARAM_FIELDS

T1 = 1_792_000_000
OUTBOX_LOGGER = "odoo.addons.efn_loyalty_accounting.models.efn_loyalty_policy_outbox"


@tagged("post_install", "-at_install", "efn")
class TestEfnMgmPolicyOutbox(SigningKeyMixin, FakeBffMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "PT MGM publikasi"})
        assign_brand_code(cls.env, cls.company, "GL")
        cls.maker = make_user(cls.env, "efn_mgmo_lm", [LOYALTY_MANAGER], [cls.company])
        cls.approver = make_user(cls.env, "efn_mgmo_am", [ACCOUNT_MANAGER], [cls.company])

    def setUp(self):
        super().setUp()
        self.install_fake_bff()
        self.Outbox = self.env["efn.mgm.policy.outbox"].sudo()

    def _approved(self, **vals):
        policy = (
            self.env["efn.mgm.policy"]
            .with_user(self.maker)
            .with_company(self.company)
            .create({"company_id": self.company.id, **vals})
        )
        policy.with_user(self.approver).action_efn_approve()
        return policy, policy.sudo().outbox_ids

    def test_publish_signed_contract_body_and_mark_published(self):
        key = self.install_signing_key()
        policy, outbox = self._approved(level1_pct_bp=150, level2_pct_bp=40, max_depth=4)
        self.assertEqual(outbox.idempotency_key, f"mgm_policy:GL:{policy.version}")
        self.assertEqual(len(self.env["efn.loyalty.policy.outbox"].sudo().search([("id", "in", outbox.ids)])), 0)
        self.bff_responses = [self.ok({"ok": True, "status": "stored"})]
        with patch.object(outbox_module, "_now_unix", return_value=T1):
            self.Outbox._efn_process_due()
        call = self.bff_calls[0]
        self.assertEqual((call["method"], call["path"]), ("PUT", f"/v1/pos/loyalty/mgm-policies/GL/{policy.version}"))
        body = json.loads(call["body"])
        self.assertEqual(
            set(body), {"schema_version", "company_code", "version", "effective_from", "approved_by_uid", "params"}
        )
        self.assertEqual(list(body["params"]), sorted(PARAM_FIELDS))  # kanonik: kunci terurut
        self.assertEqual(
            (body["params"]["level1_pct_bp"], body["params"]["level2_pct_bp"], body["params"]["max_depth"]),
            (150, 40, 4),
        )
        self.assertTrue(all(type(body["params"][name]) is int for name in PARAM_FIELDS))
        self.assertEqual(body["approved_by_uid"], self.approver.id)
        self.assertEqual(call["body"], signing.canonical_json(body))
        header = call["headers"][signing.SIGNATURE_HEADER]
        self.assertEqual(signing.verify_signature(key, header, call["body"], now=T1 + 5), (True, None))
        self.assertEqual((outbox.state, policy.state), ("done", "published"))
        self.assertTrue(policy.published_at)
        # versi baru draft mengambil nilai dari versi terakhir yang sudah dipublikasikan
        nxt = self.env["efn.mgm.policy"].with_user(self.maker).with_company(self.company).create({})
        self.assertEqual((nxt.version, nxt.level1_pct_bp, nxt.max_depth), (policy.version + 1, 150, 4))
        self.assertEqual(self.env["efn.mgm.policy"].efn_active_policy(self.company), policy)

    def test_out_of_bounds_is_final_and_without_key_is_held(self):
        self.remove_signing_key()
        policy, outbox = self._approved()
        # log ERROR "tertahan" = perilaku yang diharapkan; ditangkap agar gate CI (grep ERROR) tetap bersih
        with self.assertLogs(OUTBOX_LOGGER, "ERROR"):
            self.Outbox._efn_process_due()
        self.assertEqual(self.bff_calls, [])
        self.assertEqual((outbox.state, outbox.last_error), ("pending", "signing_key_missing"))
        self.install_signing_key()
        outbox.write({"next_attempt_at": False})
        self.bff_responses = [self.ok({"error": {"code": "policy_out_of_bounds"}}, status=422)]
        self.Outbox._efn_process_due()
        self.assertEqual(
            (outbox.state, outbox.last_error, policy.state), ("failed", "policy_out_of_bounds", "approved")
        )
        # outbox kebijakan poin tidak tersentuh (tabel terpisah)
        self.assertFalse(
            self.env["efn.loyalty.policy.outbox"].sudo().search([("idempotency_key", "like", "mgm_policy:")])
        )
