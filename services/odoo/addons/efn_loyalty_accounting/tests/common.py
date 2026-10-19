"""Fixture E-44: kode PT dipindah ke company uji (kode unik per DB -- company efn_setup_company dibebaskan di
transaksi test yang di-rollback), user per peran, kunci tanda tangan acak per test (tidak ada rahasia di repo)."""

import os
import secrets
import tempfile
from unittest.mock import patch

from odoo.fields import Command
from odoo.tests import new_test_user

from odoo.addons.efn_core.models.efn_bff_client import BffResponse
from odoo.addons.efn_loyalty_accounting.models import signing

LOYALTY_MANAGER = "efn_loyalty_accounting.group_loyalty_manager"
ACCOUNT_MANAGER = "account.group_account_manager"


def assign_brand_code(env, company, code):
    """Pasang kode PT ke company uji (melepas kode itu dari company lain lebih dulu; UNIQUE)."""
    env["res.company"].sudo().search([("efn_brand_code", "=", code), ("id", "!=", company.id)]).write(
        {"efn_brand_code": False}
    )
    env["res.company"].flush_model(["efn_brand_code"])
    company.sudo().write({"efn_brand_code": code})


def make_user(env, login, groups, companies):
    return new_test_user(
        env,
        login=login,
        groups=",".join(["base.group_user", *groups]),
        company_id=companies[0].id,
        company_ids=[Command.set([c.id for c in companies])],
    )


class SigningKeyMixin:
    """Kunci tanda tangan acak di file sementara + env ``EFN_LOYALTY_POLICY_SIGNING_KEY_FILE``."""

    def install_signing_key(self):
        self.signing_key = secrets.token_hex(32).encode("ascii")
        handle = tempfile.NamedTemporaryFile("wb", delete=False, suffix=".key")
        handle.write(self.signing_key + b"\n")
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        patcher = patch.dict(os.environ, {signing.SIGNING_KEY_FILE_ENV: handle.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        return self.signing_key

    def remove_signing_key(self):
        missing = os.path.join(tempfile.gettempdir(), f"efn-no-key-{secrets.token_hex(8)}")
        patcher = patch.dict(os.environ, {signing.SIGNING_KEY_FILE_ENV: missing})
        patcher.start()
        self.addCleanup(patcher.stop)


class FakeBffMixin:
    """Mock ``efn.bff.client._efn_loyalty_request``: catat panggilan, jawab dari ``self.bff_responses``."""

    def install_fake_bff(self):
        self.bff_calls = []
        self.bff_responses = []
        client_cls = self.env.registry["efn.bff.client"]
        patcher = patch.object(client_cls, "_efn_loyalty_request", autospec=True, side_effect=self._fake_request)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _fake_request(self, _client, method, path, body=None, params=None, extra_headers=None, log_context=None):
        self.bff_calls.append(
            {
                "method": method,
                "path": path,
                "body": body,
                "params": dict(params or {}),
                "headers": dict(extra_headers or {}),
                "log_context": dict(log_context or {}),
            }
        )
        if not self.bff_responses:
            raise AssertionError(f"panggilan BFF tak terduga: {method} {path}")
        result = self.bff_responses.pop(0) if len(self.bff_responses) > 1 else self.bff_responses[0]
        if callable(result):
            result = result(method, path, body, params)
        if isinstance(result, Exception):
            raise result
        return result

    @staticmethod
    def ok(data=None, status=200):
        return BffResponse(status, data if data is not None else {})
