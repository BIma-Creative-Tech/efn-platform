"""Publikasi kebijakan Odoo -> BFF (docs/04 §13.1 no. 3, L-61): body kontrak, tanda tangan, replay, tanpa kunci,
409/422 final, retry/backoff, transport klien (https + CA + service key). BFF di-mock."""

import hashlib
import json
import os
import secrets
import tempfile
from datetime import timedelta
from unittest.mock import MagicMock, patch

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

from odoo.addons.efn_core.models.efn_bff_client import EfnBffError
from odoo.addons.efn_loyalty_accounting.models import efn_loyalty_policy_outbox as outbox_module
from odoo.addons.efn_loyalty_accounting.models import signing
from odoo.addons.efn_loyalty_accounting.models.efn_loyalty_policy import PARAM_FIELDS
from odoo.addons.efn_loyalty_accounting.tests.common import (
    ACCOUNT_MANAGER,
    LOYALTY_MANAGER,
    FakeBffMixin,
    SigningKeyMixin,
    assign_brand_code,
    make_user,
)

OUTBOX_LOGGER = "odoo.addons.efn_loyalty_accounting.models.efn_loyalty_policy_outbox"
T1 = 1_792_000_000


@tagged("post_install", "-at_install", "efn")
class TestEfnLoyaltyPolicyOutbox(SigningKeyMixin, FakeBffMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Company = cls.env["res.company"].sudo()
        cls.gl = Company.create({"name": "PT GL Uji Publikasi"})
        cls.pb = Company.create({"name": "PT PB Uji Publikasi"})
        cls.efn = Company.create({"name": "Holding EFN Uji Publikasi"})
        for company, code in ((cls.gl, "GL"), (cls.pb, "PB"), (cls.efn, "EFN")):
            assign_brand_code(cls.env, company, code)
        cls.lm = make_user(cls.env, "efn_lpo_lm", [LOYALTY_MANAGER], [cls.gl])
        cls.lm_efn = make_user(cls.env, "efn_lpo_lm_efn", [LOYALTY_MANAGER], [cls.efn])
        cls.am = make_user(cls.env, "efn_lpo_am", [ACCOUNT_MANAGER], [cls.gl])
        cls.am_efn = make_user(cls.env, "efn_lpo_am_efn", [ACCOUNT_MANAGER], [cls.efn])
        cls.am_pb = make_user(cls.env, "efn_lpo_am_pb", [ACCOUNT_MANAGER], [cls.pb])

    def setUp(self):
        super().setUp()
        self.install_fake_bff()
        self.Outbox = self.env["efn.loyalty.policy.outbox"].sudo()

    def _approved(self, user=None, approver=None, **vals):
        policy = self.env["efn.loyalty.policy"].with_user(user or self.lm).create(vals)
        policy.with_user(approver or self.am).action_efn_approve()
        return policy, policy.sudo().outbox_ids

    def _run(self):
        return self.Outbox._efn_process_due()

    def _make_due(self, outbox):
        outbox.write({"next_attempt_at": fields.Datetime.now() - timedelta(seconds=1)})

    # --------------------------------------------------------- happy path
    def test_publish_signed_contract_body_and_mark_published(self):
        key = self.install_signing_key()
        policy, outbox = self._approved(earn_idr_per_point=12_000, earn_on_money_only=False)
        self.bff_responses = [self.ok({"ok": True})]
        with patch.object(outbox_module, "_now_unix", return_value=T1):
            self._run()
        self.assertEqual(len(self.bff_calls), 1)
        call = self.bff_calls[0]
        self.assertEqual((call["method"], call["path"]), ("PUT", f"/v1/pos/loyalty/policies/GL/{policy.version}"))
        body = json.loads(call["body"])
        self.assertEqual(
            set(body), {"schema_version", "company_code", "version", "effective_from", "approved_by_uid", "params"}
        )
        self.assertEqual(
            (body["schema_version"], body["company_code"], body["version"], body["approved_by_uid"]),
            (1, "GL", policy.version, self.am.id),
        )
        self.assertEqual(set(body["params"]), set(PARAM_FIELDS))
        self.assertEqual(body["params"]["earn_idr_per_point"], 12_000)
        self.assertIs(body["params"]["earn_on_money_only"], False)
        for name in PARAM_FIELDS:
            if name != "earn_on_money_only":
                self.assertIs(type(body["params"][name]), int, name)
        self.assertRegex(body["effective_from"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        # Byte yang dikirim = byte kanonik yang ditandatangani; header terverifikasi dengan kunci yang sama.
        self.assertEqual(call["body"], signing.canonical_json(body))
        header = call["headers"][signing.SIGNATURE_HEADER]
        self.assertTrue(header.startswith(f"t={T1},v1="))
        self.assertEqual(signing.verify_signature(key, header, call["body"], now=T1 + 10), (True, None))
        self.assertEqual(call["log_context"], {"company": "GL", "version": policy.version})
        self.assertEqual((outbox.state, outbox.last_status), ("done", 200))
        self.assertEqual(outbox.body_sha256, hashlib.sha256(call["body"]).hexdigest())
        self.assertEqual(policy.state, "published")
        self.assertTrue(policy.published_at)
        # Kunci tidak pernah tersimpan di record.
        self.assertNotIn(key.decode(), json.dumps(outbox.read()[0], default=str))

    def test_global_policy_body_has_point_value(self):
        self.install_signing_key()
        policy, _outbox = self._approved(user=self.lm_efn, approver=self.am_efn, point_value_idr=150)
        self.bff_responses = [self.ok(status=201)]
        self._run()
        body = json.loads(self.bff_calls[0]["body"])
        self.assertEqual((body["company_code"], body["point_value_idr"]), ("EFN", 150))
        self.assertEqual(self.bff_calls[0]["path"], f"/v1/pos/loyalty/policies/EFN/{policy.version}")
        self.assertEqual(policy.state, "published")

    # ------------------------------------------------------ replay / retry
    def test_retry_resigns_with_new_timestamp_old_signature_not_replayable(self):
        key = self.install_signing_key()
        policy, outbox = self._approved()
        self.bff_responses = [EfnBffError("bff_timeout")]
        with patch.object(outbox_module, "_now_unix", return_value=T1):
            self._run()
        self.assertEqual((outbox.state, outbox.attempt_count, outbox.last_error), ("pending", 1, "bff_timeout"))
        self.assertGreater(outbox.next_attempt_at, fields.Datetime.now())
        self.assertFalse(self._run(), "backoff: belum jatuh tempo")
        self._make_due(outbox)
        self.bff_responses = [self.ok()]
        with patch.object(outbox_module, "_now_unix", return_value=T1 + 600):
            self._run()
        first, second = self.bff_calls
        self.assertEqual(first["body"], second["body"], "isi kebijakan read-only -> body identik")
        header_1 = first["headers"][signing.SIGNATURE_HEADER]
        header_2 = second["headers"][signing.SIGNATURE_HEADER]
        self.assertNotEqual(header_1, header_2)
        self.assertTrue(signing.verify_signature(key, header_2, second["body"], now=T1 + 600)[0])
        # Header lama diputar ulang 10 menit kemudian -> di luar jendela 300 s.
        self.assertEqual(signing.verify_signature(key, header_1, first["body"], now=T1 + 600)[1], "replay_window")
        self.assertEqual((outbox.state, policy.state), ("done", "published"))

    def test_401_and_404_retry_until_max_age(self):
        self.install_signing_key()
        _policy, outbox = self._approved()
        self.bff_responses = [self.ok({"error": {"code": "invalid_signature"}}, status=401)]
        self._run()
        self.assertEqual((outbox.state, outbox.last_error, outbox.last_status), ("pending", "invalid_signature", 401))
        self.env.cr.execute(
            "UPDATE efn_loyalty_policy_outbox SET create_date = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(hours=73), outbox.id),
        )
        outbox.invalidate_recordset()
        self._make_due(outbox)
        self.bff_responses = [self.ok(status=404)]
        self._run()
        self.assertEqual((outbox.state, outbox.last_error), ("failed", "max_age:http_404"))

    # --------------------------------------------------------- final
    def test_conflict_and_out_of_bounds_are_final_with_activity(self):
        self.install_signing_key()
        cases = (
            (409, "policy_conflict", {"error": {"code": "policy_conflict", "message": "x"}}),
            (422, "policy_out_of_bounds", {"error": {"code": "policy_out_of_bounds", "message": "x"}}),
            (400, "http_400", {"error": {"code": "Bukan Kode Aman!"}}),
        )
        for status, code, data in cases:
            with self.subTest(status=status):
                policy, outbox = self._approved()
                self.bff_responses = [self.ok(data, status=status)]
                self._run()
                self.assertEqual((outbox.state, outbox.last_error, outbox.last_status), ("failed", code, status))
                self.assertEqual(policy.state, "approved", "tidak dipublikasikan; perbaikan = versi baru")
                self.assertTrue(outbox.activity_ids)
                self.assertEqual(outbox.activity_ids.user_id, self.lm)

    # ------------------------------------------------------ tanpa kunci
    def test_without_signing_key_publication_is_held(self):
        self.remove_signing_key()
        policy, outbox = self._approved()
        with self.assertLogs(OUTBOX_LOGGER, "ERROR") as logs:
            self._run()
        self.assertFalse(self.bff_calls, "tidak pernah dikirim tanpa tanda tangan")
        self.assertEqual((outbox.state, outbox.last_error), ("pending", "signing_key_missing"))
        self.assertIn(signing.SIGNING_KEY_FILE_ENV, logs.output[0])
        self.assertTrue(outbox.activity_ids)
        # Umur tidak membuat publikasi tertahan jatuh ke failed (menunggu kunci dipasang).
        self.env.cr.execute(
            "UPDATE efn_loyalty_policy_outbox SET create_date = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(days=10), outbox.id),
        )
        outbox.invalidate_recordset()
        self._make_due(outbox)
        with self.assertLogs(OUTBOX_LOGGER, "ERROR"):
            self._run()
        self.assertEqual(outbox.state, "pending")
        self.install_signing_key()
        self._make_due(outbox)
        self.bff_responses = [self.ok()]
        self._run()
        self.assertEqual((outbox.state, policy.state), ("done", "published"))

    def test_short_key_is_treated_as_missing(self):
        handle = tempfile.NamedTemporaryFile("wb", delete=False)
        handle.write(b"pendek")
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        with patch.dict(os.environ, {signing.SIGNING_KEY_FILE_ENV: handle.name}):
            _policy, outbox = self._approved()
            with self.assertLogs(OUTBOX_LOGGER, "ERROR"):
                self._run()
        self.assertEqual(outbox.last_error, "signing_key_too_short")
        self.assertFalse(self.bff_calls)

    # ------------------------------------------------------ tombol retry
    def test_retry_button_access(self):
        self.install_signing_key()
        _policy, outbox = self._approved()
        self.bff_responses = [self.ok({"error": {"code": "policy_conflict"}}, status=409)]
        self._run()
        self.assertEqual(outbox.state, "failed")
        with self.assertRaises(AccessError):
            outbox.with_user(self.am_pb).action_efn_retry()
        outbox.with_user(self.lm).action_efn_retry()
        self.assertEqual((outbox.state, outbox.attempt_count), ("pending", 0))

    def test_enqueue_is_idempotent(self):
        policy, outbox = self._approved()
        self.assertEqual(self.Outbox._efn_enqueue_policy(policy.sudo()), outbox)
        self.assertEqual(self.Outbox.search_count([("policy_id", "=", policy.id)]), 1)


@tagged("post_install", "-at_install", "efn")
class TestEfnLoyaltyBffTransport(TransactionCase):
    """``efn.bff.client._efn_loyalty_request``: https + CA internal + service key file, tanpa redirect, header
    tanda tangan diteruskan, service key tidak bisa ditimpa, query akrual hanya allowlist."""

    def setUp(self):
        super().setUp()
        directory = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, directory)
        self.ca_path = os.path.join(directory, "ca.crt")
        self.key_path = os.path.join(directory, "api.key")
        self.fixture_header = secrets.token_hex(24)
        with open(self.ca_path, "w", encoding="ascii") as handle:
            handle.write("dummy-ca-for-test\n")
        # Nilai uji acak sekali pakai (bukan rahasia nyata); berkas 0600 seperti /run/secrets.
        fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(self.fixture_header)
        for path in (self.ca_path, self.key_path):
            self.addCleanup(os.unlink, path)
        env_patch = patch.dict(
            os.environ,
            {
                "EFN_BFF_URL": "https://bff.efn.test",
                "EFN_CA_FILE": self.ca_path,
                "EFN_BFF_API_KEY_FILE": self.key_path,
            },
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)
        self.client = self.env["efn.bff.client"]
        response = MagicMock(status_code=200)
        response.json.return_value = {"ok": True}
        self.response = response

    def test_put_sends_exact_bytes_with_signature_and_service_key(self):
        body = b'{"a":1}'
        header = "t=1,v1=" + "0" * 64
        with patch("odoo.addons.efn_loyalty_accounting.models.efn_bff_client.requests.put") as put:
            put.return_value = self.response
            result = self.client._efn_loyalty_request(
                "PUT", "/v1/pos/loyalty/policies/GL/1", body=body, extra_headers={"X-EFN-Signature": header}
            )
        self.assertEqual(result.status, 200)
        args, kwargs = put.call_args
        self.assertEqual(args[0], "https://bff.efn.test/v1/pos/loyalty/policies/GL/1")
        self.assertEqual(kwargs["data"], body)
        self.assertEqual(kwargs["headers"]["X-EFN-Service-Key"], self.fixture_header)
        self.assertEqual(kwargs["headers"]["X-EFN-Signature"], header)
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/json")
        self.assertIs(kwargs["allow_redirects"], False)
        self.assertEqual(kwargs["verify"], self.ca_path)

    def test_service_key_cannot_be_overridden_and_server_error_raises(self):
        with self.assertRaises(ValueError):
            self.client._efn_loyalty_request("PUT", "/v1/x", body=b"{}", extra_headers={"X-EFN-Service-Key": "palsu"})
        with self.assertRaises(ValueError):
            self.client._efn_loyalty_request("PUT", "/v1/x", body={"bukan": "bytes"})
        with patch("odoo.addons.efn_loyalty_accounting.models.efn_bff_client.requests.put") as put:
            put.return_value = MagicMock(status_code=503)
            with self.assertRaises(EfnBffError) as error:
                self.client._efn_loyalty_request("PUT", "/v1/x", body=b"{}")
        self.assertEqual(error.exception.code, "bff_server_error")

    def test_get_accruals_query_allowlist(self):
        with patch("odoo.addons.efn_loyalty_accounting.models.efn_bff_client.requests.get") as get:
            get.return_value = self.response
            self.client._efn_loyalty_request(
                "GET", "/v1/pos/loyalty/accruals", params={"company_code": "GL", "date": "2026-10-18"}
            )
        self.assertEqual(get.call_args.kwargs["params"], {"company_code": "GL", "date": "2026-10-18"})
        self.assertEqual(get.call_args.kwargs["headers"]["X-EFN-Service-Key"], self.fixture_header)
        for params in (
            {"member_ref": "6281234567890"},
            {"date": "2026-10-18' OR 1=1"},
            {"company_code": "gl"},
            {"company_code": "GL", "phone": "081234567890"},
        ):
            with self.subTest(params=params), self.assertRaises(ValueError):
                self.client._efn_loyalty_request("GET", "/v1/pos/loyalty/accruals", params=params)
