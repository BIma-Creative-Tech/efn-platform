import os
import tempfile
from unittest.mock import MagicMock, patch

import requests

from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

from odoo.addons.efn_core.models.efn_bff_client import (
    ALLOW_PLAINTEXT_ENV,
    API_KEY_FILE_ENV,
    APP_ENV_ENV,
    BFF_URL_ENV,
    CA_FILE_ENV,
    SERVICE_KEY_HEADER,
    EfnBffError,
    mask_phone,
    normalize_member_ref,
    parse_member_ref,
    plaintext_allowed,
)

REQUESTS_POST = "odoo.addons.efn_core.models.efn_bff_client.requests.post"
REQUESTS_GET = "odoo.addons.efn_core.models.efn_bff_client.requests.get"
# Log ERROR yang memang diharapkan (CA hilang, TLS gagal, URL http) dibisukan agar langkah
# CI "Periksa log (ERROR/CRITICAL)" tidak salah menganggapnya kegagalan.
CLIENT_LOGGER = "odoo.addons.efn_core.models.efn_bff_client"
# Isi dummy; test hanya memastikan path CA diteruskan ke requests (HTTP di-mock).
DUMMY_CA_PEM = "-----BEGIN CERTIFICATE-----\nTEST-ONLY\n-----END CERTIFICATE-----\n"


@tagged("post_install", "-at_install", "efn")
class TestEfnBffClient(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        params = cls.env["ir.config_parameter"].sudo()
        params.set_param("efn.bff_url", "https://bff.test:3000/")
        params.set_param("efn.pos.otp_timeout_seconds", "3")
        cls.client = cls.env["efn.bff.client"]

    def _tmp_file(self, content):
        handle, path = tempfile.mkstemp()
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write(content)
        self.addCleanup(os.remove, path)
        return path

    def setUp(self):
        super().setUp()
        # Key dummy khusus test (bukan secret nyata), ditulis ke file sementara.
        self.key_path = self._tmp_file("test-only-dummy-key\n")
        self.ca_path = self._tmp_file(DUMMY_CA_PEM)
        env_patch = patch.dict(os.environ, {API_KEY_FILE_ENV: self.key_path, CA_FILE_ENV: self.ca_path})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        # Lingkungan CI bisa membawa env container; test harus deterministik.
        for name in (BFF_URL_ENV, ALLOW_PLAINTEXT_ENV, APP_ENV_ENV):
            os.environ.pop(name, None)

    def _response(self, status, json_body):
        resp = MagicMock()
        resp.status_code = status
        resp.json.return_value = json_body
        return resp

    # ------------------------------------------------------------------ HTTP
    def test_post_sends_service_key_timeout_and_verifies_internal_ca(self):
        with patch(REQUESTS_POST, return_value=self._response(202, {"challenge_id": "c1"})) as post:
            res = self.client.post("/v1/pos/otp/request", {"member_ref": "6281234567890"})
        self.assertEqual(res.status, 202)
        self.assertEqual(res.data["challenge_id"], "c1")
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://bff.test:3000/v1/pos/otp/request")
        self.assertEqual(kwargs["headers"][SERVICE_KEY_HEADER], "test-only-dummy-key")
        self.assertEqual(kwargs["timeout"], 3.0)
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["verify"], self.ca_path, "harus memverifikasi dengan CA internal")

    @mute_logger(CLIENT_LOGGER)
    def test_missing_ca_raises_without_http_call(self):
        os.environ[CA_FILE_ENV] = self.ca_path + ".missing"
        with patch(REQUESTS_POST) as post:
            with self.assertRaises(EfnBffError) as cm:
                self.client.post("/v1/pos/otp/request", {})
        self.assertEqual(cm.exception.code, "bff_ca_missing")
        post.assert_not_called()

    @mute_logger(CLIENT_LOGGER)
    def test_tls_verification_failure_is_not_retried_plaintext(self):
        with patch(REQUESTS_POST, side_effect=requests.exceptions.SSLError()) as post:
            with self.assertRaises(EfnBffError) as cm:
                self.client.post("/v1/pos/otp/request", {})
        self.assertEqual(cm.exception.code, "bff_tls_error")
        self.assertEqual(post.call_count, 1)

    @mute_logger(CLIENT_LOGGER)
    def test_http_url_rejected_in_production_even_with_flag(self):
        self.env["ir.config_parameter"].sudo().set_param("efn.bff_url", "http://bff.test:3000")
        os.environ[ALLOW_PLAINTEXT_ENV] = "true"
        os.environ[APP_ENV_ENV] = "production"
        with patch(REQUESTS_POST) as post:
            with self.assertRaises(EfnBffError) as cm:
                self.client.post("/v1/pos/otp/request", {})
        self.assertEqual(cm.exception.code, "bff_insecure_url")
        post.assert_not_called()

    @mute_logger(CLIENT_LOGGER)
    def test_http_url_rejected_without_flag(self):
        self.env["ir.config_parameter"].sudo().set_param("efn.bff_url", "http://bff.test:3000")
        os.environ[APP_ENV_ENV] = "development"
        with patch(REQUESTS_POST) as post:
            with self.assertRaises(EfnBffError) as cm:
                self.client.post("/v1/pos/otp/request", {})
        self.assertEqual(cm.exception.code, "bff_insecure_url")
        post.assert_not_called()

    @mute_logger(CLIENT_LOGGER)
    def test_http_url_rejected_when_app_env_unset(self):
        """Fail-closed: APP_ENV kosong diperlakukan sebagai produksi."""
        self.env["ir.config_parameter"].sudo().set_param("efn.bff_url", "http://bff.test:3000")
        os.environ[ALLOW_PLAINTEXT_ENV] = "true"
        self.assertFalse(plaintext_allowed())
        with self.assertRaises(EfnBffError) as cm:
            self.client._get_base_url()
        self.assertEqual(cm.exception.code, "bff_insecure_url")

    def test_http_url_allowed_only_with_flag_in_development(self):
        self.env["ir.config_parameter"].sudo().set_param("efn.bff_url", "http://bff.test:3000")
        os.environ[ALLOW_PLAINTEXT_ENV] = "true"
        os.environ[APP_ENV_ENV] = "development"
        with patch(REQUESTS_POST, return_value=self._response(202, {})) as post:
            self.client.post("/v1/pos/otp/request", {})
        args, _kwargs = post.call_args
        self.assertEqual(args[0], "http://bff.test:3000/v1/pos/otp/request")

    def test_env_bff_url_overrides_parameter(self):
        os.environ[BFF_URL_ENV] = "https://api-gateway:3000"
        with patch(REQUESTS_POST, return_value=self._response(202, {})) as post:
            self.client.post("/v1/pos/otp/request", {})
        self.assertEqual(post.call_args[0][0], "https://api-gateway:3000/v1/pos/otp/request")

    def test_default_parameter_is_https(self):
        record = self.env.ref("efn_core.param_bff_url")
        self.assertTrue(record.value.startswith("https://"))

    def test_4xx_is_returned_not_raised(self):
        with patch(REQUESTS_POST, return_value=self._response(400, {"ok": False, "reason": "invalid"})):
            res = self.client.post("/v1/pos/otp/verify", {})
        self.assertEqual(res.status, 400)
        self.assertEqual(res.data["reason"], "invalid")

    def test_timeout_raises_bff_error(self):
        with patch(REQUESTS_POST, side_effect=requests.exceptions.Timeout()):
            with self.assertRaises(EfnBffError) as cm:
                self.client.post("/v1/pos/otp/request", {})
        self.assertEqual(cm.exception.code, "bff_timeout")

    def test_connection_error_raises_bff_error(self):
        with patch(REQUESTS_POST, side_effect=requests.exceptions.ConnectionError()):
            with self.assertRaises(EfnBffError) as cm:
                self.client.post("/v1/pos/otp/request", {})
        self.assertEqual(cm.exception.code, "bff_unreachable")

    def test_5xx_raises_bff_error(self):
        with patch(REQUESTS_POST, return_value=self._response(503, {})):
            with self.assertRaises(EfnBffError) as cm:
                self.client.post("/v1/pos/otp/request", {})
        self.assertEqual(cm.exception.code, "bff_server_error")

    @mute_logger(CLIENT_LOGGER)
    def test_missing_key_file_raises_without_http_call(self):
        os.environ[API_KEY_FILE_ENV] = self.key_path + ".missing"
        with patch(REQUESTS_POST) as post:
            with self.assertRaises(EfnBffError) as cm:
                self.client.post("/v1/pos/otp/request", {})
        self.assertEqual(cm.exception.code, "bff_key_missing")
        post.assert_not_called()

    @mute_logger(CLIENT_LOGGER)
    def test_invalid_base_url(self):
        self.env["ir.config_parameter"].sudo().set_param("efn.bff_url", "ftp://nope")
        with self.assertRaises(EfnBffError) as cm:
            self.client.post("/v1/pos/otp/request", {})
        self.assertEqual(cm.exception.code, "bff_not_configured")

    def test_timeout_param_fallback_and_cap(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("efn.pos.otp_timeout_seconds", "abc")
        self.assertEqual(self.client._get_timeout(), 5.0)
        params.set_param("efn.pos.otp_timeout_seconds", "999")
        self.assertEqual(self.client._get_timeout(), 30.0)

    # ------------------------------------------------------------------ GET
    def test_get_uses_same_tls_key_timeout_rules(self):
        with patch(REQUESTS_GET, return_value=self._response(200, {"status": "paid"})) as get:
            res = self.client.get("/v1/pos/payments/pay-1", {"store": "odoo"})
        self.assertEqual(res.status, 200)
        self.assertEqual(res.data["status"], "paid")
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://bff.test:3000/v1/pos/payments/pay-1")
        self.assertEqual(kwargs["params"], {"store": "odoo"})
        self.assertEqual(kwargs["headers"][SERVICE_KEY_HEADER], "test-only-dummy-key")
        self.assertEqual(kwargs["timeout"], 3.0)
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["verify"], self.ca_path)
        self.assertNotIn("json", kwargs)

    def test_get_list_body_is_exposed(self):
        body = [{"request_id": "r1", "member_hash": "a" * 64}]
        with patch(REQUESTS_GET, return_value=self._response(200, body)):
            res = self.client.get("/v1/pos/privacy/erasures", {"store": "odoo"})
        self.assertEqual(res.body, body)
        self.assertEqual(res.data, {}, "data tetap dict untuk endpoint objek")
        self.assertNotIn("r1", repr(res))

    @mute_logger(CLIENT_LOGGER)
    def test_get_missing_ca_raises_without_http_call(self):
        os.environ[CA_FILE_ENV] = self.ca_path + ".missing"
        with patch(REQUESTS_GET) as get:
            with self.assertRaises(EfnBffError) as cm:
                self.client.get("/v1/pos/payments/pay-1")
        self.assertEqual(cm.exception.code, "bff_ca_missing")
        get.assert_not_called()

    @mute_logger(CLIENT_LOGGER)
    def test_get_tls_error_and_5xx(self):
        with patch(REQUESTS_GET, side_effect=requests.exceptions.SSLError()):
            with self.assertRaises(EfnBffError) as cm:
                self.client.get("/v1/pos/payments/pay-1")
        self.assertEqual(cm.exception.code, "bff_tls_error")
        with patch(REQUESTS_GET, return_value=self._response(502, {})):
            with self.assertRaises(EfnBffError) as cm:
                self.client.get("/v1/pos/payments/pay-1")
        self.assertEqual(cm.exception.code, "bff_server_error")
        with patch(REQUESTS_GET, return_value=self._response(302, {})):
            with self.assertRaises(EfnBffError) as cm:
                self.client.get("/v1/pos/payments/pay-1")
        self.assertEqual(cm.exception.code, "bff_server_error", "redirect tidak diikuti")

    def test_get_rejects_unsafe_path_and_params(self):
        with patch(REQUESTS_GET) as get:
            for path in ("/v1/pos/payments/../admin", "/v1/x?y=1", "v1/x", "/v1//x", "/v1/a b"):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    self.client.get(path)
            with self.assertRaises(ValueError):
                self.client.get("/v1/x", {"a": {"nested": 1}})
        get.assert_not_called()

    def test_get_rejects_pii_in_query_and_path(self):
        """R2 (docs/04 §8.13): HP/token/identitas tidak pernah lewat URL (access log)."""
        cases = [
            {"member_ref": "x"},
            {"MEMBER_REF": "x"},
            {"phone": "x"},
            {"discount_token": "x"},
            {"q": "6281234567890"},
            {"q": "+62 812-3456-7890"},
            {"q": 6281234567890},
        ]
        with patch(REQUESTS_GET) as get, patch(REQUESTS_POST) as post:
            for params in cases:
                with self.subTest(params=params), self.assertRaises(ValueError):
                    self.client.get("/v1/pos/loyalty/balance", params)
            for path in ("/v1/pos/loyalty/balance/6281234567890", "/v1/x/081234567890"):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    self.client.get(path)
                with self.subTest(path=path), self.assertRaises(ValueError):
                    self.client.post(path, {})
        get.assert_not_called()
        post.assert_not_called()

    def test_get_allows_non_pii_params(self):
        with patch(REQUESTS_GET, return_value=self._response(200, [])) as get:
            self.client.get("/v1/pos/privacy/erasures", {"store": "odoo", "limit": 100})
            self.client.get("/v1/pos/payments/7f0c2a9e-1b2c-4d5e-8f90-123456789abc")
        self.assertEqual(get.call_count, 2)

    # ---------------------------------------------------------------- phone
    def test_mask_and_normalize_phone(self):
        self.assertEqual(mask_phone("6281234567890"), "62812****890")
        self.assertEqual(mask_phone("123"), "****")
        self.assertEqual(normalize_member_ref("0812-3456-7890"), "6281234567890")
        self.assertEqual(normalize_member_ref("+62 812 3456 7890"), "6281234567890")
        self.assertEqual(normalize_member_ref("0062 812 3456 7890"), "6281234567890")
        self.assertEqual(normalize_member_ref("81234567890"), "6281234567890")
        self.assertIsNone(normalize_member_ref("12345"))
        self.assertIsNone(normalize_member_ref("0812abc"))
        self.assertIsNone(normalize_member_ref(None))

    def test_parse_member_ref_error_codes(self):
        self.assertEqual(parse_member_ref(""), (None, "no_phone"))
        self.assertEqual(parse_member_ref(None), (None, "no_phone"))
        self.assertEqual(parse_member_ref("+65 9123 4567"), (None, "foreign_phone"))
        self.assertEqual(parse_member_ref("0065 9123 4567"), (None, "foreign_phone"))
        self.assertEqual(parse_member_ref("0812abc"), (None, "invalid_phone"))
        self.assertEqual(parse_member_ref("+62 812"), (None, "invalid_phone"))
        self.assertEqual(parse_member_ref("6281234567890"), ("6281234567890", None))
