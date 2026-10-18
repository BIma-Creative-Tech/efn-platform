"""E-41 (docs/08 §11.6, docs/04 §12.4, L-51): retur tanpa struk dengan persetujuan Store Manager (PIN server)."""

from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged

from odoo.addons.efn_pos_refund_origin.models.return_pin import hash_pin, verify_pin
from odoo.addons.point_of_sale.tests.common import TestPoSCommon

PIN = "135790"
WRONG_PIN = "246801"
MODE_PARAM = "efn.pos.refund.unlinked_return_mode"
WIZARD = "efn.pos.return.pin.wizard"


@tagged("post_install", "-at_install", "efn")
class TestEfnPosReturnApproval(TestPoSCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.basic_config
        cls.product = cls.create_product("Produk Retur Tanpa Struk Uji", cls.categ_basic, 50000.0)
        cls.env["ir.config_parameter"].sudo().set_param("efn.pos.return_pin_kdf_rounds", "1000")
        company = cls.config.company_id
        # Company lain buatan test (tanpa kode entitas string; pola efn_pos_loyalty_ledger).
        cls.other_company = cls.env["res.company"].create({"name": "PT Lain Uji E41"})

        def user(login, groups, company_ids=None, name=None):
            vals = {"company_id": (company_ids or company)[:1].id}
            if company_ids:
                vals["company_ids"] = [Command.set(company_ids.ids)]
            return new_test_user(cls.env, login=login, groups=groups, name=name or login, **vals)

        cls.cashier = user("efn_e41_kasir", "base.group_user,point_of_sale.group_pos_user")
        cls.cashier2 = user("efn_e41_kasir2", "base.group_user,point_of_sale.group_pos_user")
        cls.manager = user("efn_e41_sm", "base.group_user,point_of_sale.group_pos_manager", name="SM Satu")
        cls.manager2 = user("efn_e41_sm2", "base.group_user,point_of_sale.group_pos_manager", name="SM Dua")
        cls.manager_nopin = user("efn_e41_sm_nopin", "base.group_user,point_of_sale.group_pos_manager")
        cls.pos_user_pin = user("efn_e41_user_pin", "base.group_user,point_of_sale.group_pos_user")
        cls.outsider = user(
            "efn_e41_sm_lain", "base.group_user,point_of_sale.group_pos_manager", company_ids=cls.other_company
        )
        cls.admin_user = user("efn_e41_admin", "base.group_user,base.group_system,point_of_sale.group_pos_manager")
        cls.plain = user("efn_e41_plain", "base.group_user")
        pin_hash = hash_pin(cls.env, PIN)
        for approver in (cls.manager, cls.manager2, cls.pos_user_pin, cls.outsider):
            approver.sudo().write({"efn_return_pin_hash": pin_hash})

    def setUp(self):
        super().setUp()
        self.open_new_session()
        self.env["ir.config_parameter"].sudo().set_param(MODE_PARAM, "manager_approval")
        self.Approval = self.env["efn.pos.return.approval"].sudo()

    # ----------------------------------------------------------------- helpers
    def _return_data(self, amount=50000, method=None, qty=1):
        return self.create_ui_order_data([(self.product, -qty)], payments=[(method or self.cash_pm1, -amount)])

    def _request(self, data, approver=None, pin=PIN, amount=50000, user=None, reason="Barang rusak"):
        return self.config.with_user(user or self.cashier).efn_return_approval_request(
            data["uuid"], (approver or self.manager).id, pin, amount, reason
        )

    def _sync(self, data, user=None):
        self.env["pos.order"].with_user(user or self.cashier).sync_from_ui([data])
        return self.env["pos.order"].search([("uuid", "=", data["uuid"])])

    def _approvals(self, data):
        return self.Approval.search([("order_ref", "=", data["uuid"])])

    def _assert_sync_rejected(self, data, user=None):
        with self.assertRaises(UserError):
            self._sync(data, user=user)
        self.assertFalse(self.env["pos.order"].search([("uuid", "=", data["uuid"])]))

    # ------------------------------------------------------------- default/mode
    def test_default_mode_is_manager_approval(self):
        self.assertEqual(self.env["pos.order"]._efn_unlinked_return_mode(), "manager_approval")
        self.assertEqual(self.config.efn_unlinked_return_mode, "manager_approval")
        param = self.env.ref("efn_pos_refund_origin.param_unlinked_return_mode")
        self.assertEqual(param.value, "manager_approval")
        self.env["ir.config_parameter"].sudo().set_param(MODE_PARAM, "tidak-dikenal")
        self.assertEqual(self.env["pos.order"]._efn_unlinked_return_mode(), "manager_approval", "fail closed")

    def test_sync_without_approval_rejected(self):
        self._assert_sync_rejected(self._return_data())

    def test_cash_mode_still_works_without_approval(self):
        self.env["ir.config_parameter"].sudo().set_param(MODE_PARAM, "cash")
        self.assertEqual(self._sync(self._return_data()).state, "paid")
        result = self.config.with_user(self.cashier).efn_return_approval_approvers()
        self.assertEqual(result["error_code"], "mode_disabled")
        self.assertEqual(self._request(self._return_data())["error_code"], "mode_disabled")

    # --------------------------------------------------------------- happy path
    def test_correct_pin_approves_and_sync_consumes_once(self):
        data = self._return_data()
        result = self._request(data)
        self.assertEqual(result, {"ok": True, "expires_in": 300, "max_amount": 50000.0})
        approval = self._approvals(data)
        self.assertEqual(approval.state, "approved")
        self.assertEqual(
            (approval.cashier_user_id, approval.approver_user_id, approval.session_id, approval.company_id),
            (self.cashier, self.manager, self.pos_session, self.config.company_id),
        )
        self.assertAlmostEqual(
            (approval.expires_at - fields.Datetime.now()).total_seconds(), 300, delta=30, msg="TTL 5 menit"
        )
        order = self._sync(data)
        self.assertEqual(order.state, "paid")
        self.assertEqual((approval.state, approval.consumed_order_id, approval.amount_used), ("consumed", order, 50000))
        # Dipakai ulang untuk order lain -> ditolak.
        self.assertEqual(
            approval._efn_usable_error(self._sync(self.create_ui_order_data([(self.product, 1)])), 0), "used"
        )
        self.assertIsNone(approval._efn_usable_error(order, 50000), "sync ulang order yang sama idempoten")
        # Order retur lain (uuid lain) tidak bisa menumpang persetujuan ini.
        self._assert_sync_rejected(self._return_data())

    def test_new_request_supersedes_previous(self):
        data = self._return_data()
        self.assertTrue(self._request(data, amount=30000)["ok"])
        self.assertTrue(self._request(data, approver=self.manager2)["ok"])
        approvals = self._approvals(data)
        self.assertEqual(sorted(approvals.mapped("state")), ["approved", "superseded"])
        self.assertEqual(self._sync(data).state, "paid")

    def test_pin_never_logged(self):
        data = self._return_data()
        with self.assertLogs("odoo.addons.efn_pos_refund_origin", level="INFO") as logs:
            self._request(data, pin=WRONG_PIN)
            self._request(data)
        joined = "\n".join(logs.output)
        self.assertNotIn(WRONG_PIN, joined)
        self.assertNotIn(PIN, joined)

    # -------------------------------------------------------------- token rules
    def test_expired_approval_rejected(self):
        data = self._return_data()
        self.assertTrue(self._request(data)["ok"])
        self._approvals(data).write({"expires_at": fields.Datetime.now() - timedelta(seconds=1)})
        self._assert_sync_rejected(data)

    def test_amount_over_approval_rejected(self):
        data = self._return_data(amount=50000)
        self.assertTrue(self._request(data, amount=30000)["ok"])
        self._assert_sync_rejected(data)
        self.assertEqual(self._approvals(data).state, "approved", "rollback: persetujuan tidak terpakai")

    def test_other_cashier_cannot_use_approval(self):
        data = self._return_data()
        self.assertTrue(self._request(data)["ok"])
        self._assert_sync_rejected(data, user=self.cashier2)
        self.assertEqual(self._sync(data).state, "paid")

    def test_approval_still_cash_only(self):
        data = self._return_data(method=self.bank_pm1)
        self.assertTrue(self._request(data)["ok"])
        self._assert_sync_rejected(data)

    def test_deny_mode_rejects_even_with_approval(self):
        data = self._return_data()
        self.assertTrue(self._request(data)["ok"])
        self.env["ir.config_parameter"].sudo().set_param(MODE_PARAM, "deny")
        self._assert_sync_rejected(data)

    # ------------------------------------------------------------ PIN & lockout
    def test_wrong_pin_lock_after_five_then_unlock_after_15_minutes(self):
        data = self._return_data()
        for _attempt in range(4):
            self.assertEqual(self._request(data, pin=WRONG_PIN)["error_code"], "invalid_pin")
        self.assertEqual(self._request(data, pin=WRONG_PIN)["error_code"], "locked")
        self.assertEqual(self._request(data, pin=PIN)["error_code"], "locked", "PIN benar pun ditolak saat terkunci")
        failed = self._approvals(data).filtered(lambda a: a.state == "failed")
        self.assertEqual(sorted(failed.mapped("failure_reason")), ["locked"] + ["wrong_pin"] * 5)
        # Kunci per approver: approver lain tetap bisa.
        self.assertTrue(self._request(data, approver=self.manager2)["ok"])
        self.env.cr.execute(
            "UPDATE efn_pos_return_approval SET create_date = create_date - interval '16 minutes' "
            "WHERE approver_user_id = %s",
            (self.manager.id,),
        )
        self.Approval.invalidate_model()
        self.assertTrue(self._request(data, pin=PIN)["ok"])

    def test_success_resets_failure_counter(self):
        data = self._return_data()
        for _attempt in range(4):
            self._request(data, pin=WRONG_PIN)
        self.assertTrue(self._request(data)["ok"])
        self.assertEqual(self._request(data, pin=WRONG_PIN)["error_code"], "invalid_pin")

    def test_approver_must_not_be_cashier(self):
        data = self._return_data()
        result = self._request(data, approver=self.manager, user=self.manager)
        self.assertEqual(result["error_code"], "approver_invalid")
        approvers = self.config.with_user(self.manager).efn_return_approval_approvers()["approvers"]
        self.assertNotIn(self.manager.id, [a["id"] for a in approvers])

    def test_approver_needs_group_company_and_pin(self):
        data = self._return_data()
        for approver in (self.pos_user_pin, self.outsider, self.manager_nopin, self.cashier):
            with self.subTest(approver=approver.login):
                self.assertEqual(self._request(data, approver=approver)["error_code"], "approver_invalid")
        result = self.config.with_user(self.cashier).efn_return_approval_approvers()
        self.assertTrue(result["ok"])
        self.assertEqual({a["id"] for a in result["approvers"]}, {self.manager.id, self.manager2.id})
        self.assertEqual(set(result["approvers"][0]), {"id", "name"}, "tanpa hash/PII lain")
        self.assertFalse(self._approvals(data), "approver tidak sah tidak menghasilkan percobaan PIN")

    def test_invalid_input(self):
        data = self._return_data()
        cases = {
            "order_ref": dict(data={"uuid": "x"}),
            "amount_negative": dict(amount=-1),
            "amount_str": dict(amount="50000"),
            "amount_nan": dict(amount=float("nan")),
            "reason_empty": dict(reason="  "),
            "pin_int": dict(pin=135790),
            "pin_long": dict(pin="1" * 40),
        }
        for name, kwargs in cases.items():
            with self.subTest(case=name):
                payload = kwargs.pop("data", data)
                self.assertEqual(self._request(payload, **kwargs)["error_code"], "invalid_input")
        approver_bool = self.config.with_user(self.cashier).efn_return_approval_request(
            data["uuid"], True, PIN, 1, "Barang rusak"
        )
        self.assertEqual(approver_bool["error_code"], "invalid_input")

    def test_rpc_access(self):
        data = self._return_data()
        with self.assertRaises(AccessError):
            self.config.with_user(self.plain).efn_return_approval_approvers()
        with self.assertRaises(AccessError):
            self._request(data, user=self.plain)
        with self.assertRaises(AccessError):
            self._request(data, user=self.outsider)

    # ------------------------------------------------------------------- audit
    def test_approval_record_append_only_and_company_scoped(self):
        data = self._return_data()
        self._request(data)
        approval = self._approvals(data)
        for user in (self.manager, self.admin_user):
            with self.subTest(user=user.login):
                as_user = approval.with_user(user)
                self.assertEqual(as_user.state, "approved")
                with self.assertRaises(UserError):
                    as_user.write({"amount_max": 1e9})
                with self.assertRaises(UserError):
                    as_user.unlink()
                with self.assertRaises(UserError):
                    self.env["efn.pos.return.approval"].with_user(user).create(
                        {"config_id": self.config.id, "state": "approved"}
                    )
        with self.assertRaises(AccessError):
            approval.with_user(self.cashier).read(["state"])
        outsider_view = self.env["efn.pos.return.approval"].with_user(self.outsider).search([])
        self.assertNotIn(approval, outsider_view, "record rule multi-company")

    def test_autovacuum_keeps_minimum_retention(self):
        data = self._return_data()
        self._request(data)
        approval = self._approvals(data)
        self.env["ir.config_parameter"].sudo().set_param("efn.pos.return_approval_retention_days", "1")
        self.env.cr.execute(
            "UPDATE efn_pos_return_approval SET create_date = create_date - interval '100 days' WHERE id = %s",
            (approval.id,),
        )
        self.Approval.invalidate_model()
        self.Approval._gc_efn_pos_return_approvals()
        self.assertTrue(approval.exists(), "minimum 365 hari walau parameter lebih kecil")

    # ----------------------------------------------------------- PIN wizard
    def _apply_pin(self, target, actor, pin, confirm=None, identity=True):
        wizard = (
            self.env[WIZARD]
            .with_user(actor)
            .with_context(default_user_id=target.id)
            .create({"pin": pin, "pin_confirm": pin if confirm is None else confirm})
        )
        if not identity:
            return wizard.action_apply()
        with patch.object(type(self.env[WIZARD]), "_efn_require_recent_identity", lambda self: None):
            return wizard.action_apply()

    def test_manager_sets_own_pin_hashed(self):
        self._apply_pin(self.manager_nopin, self.manager_nopin, "802413")
        stored = self.manager_nopin.sudo().efn_return_pin_hash
        self.assertTrue(stored.startswith("$pbkdf2-sha512$"))
        self.assertNotIn("802413", stored)
        self.assertTrue(verify_pin("802413", stored))
        self.assertTrue(self.manager_nopin.efn_return_pin_is_set)
        wizards = self.env[WIZARD].sudo().search([("user_id", "=", self.manager_nopin.id)])
        self.assertFalse(any(wizards.mapped("pin")), "teks PIN dikosongkan setelah disimpan")

    def test_pin_only_self_or_system_admin(self):
        with self.assertRaises(AccessError):
            self._apply_pin(self.manager, self.cashier, "802413")
        with self.assertRaises(AccessError):
            self._apply_pin(self.manager, self.manager2, "802413")
        self._apply_pin(self.manager, self.admin_user, "802413")
        self.assertTrue(verify_pin("802413", self.manager.sudo().efn_return_pin_hash))
        self.assertFalse(self.manager.with_user(self.manager2)._efn_can_manage_return_pin())
        self.assertTrue(self.manager.with_user(self.admin_user)._efn_can_manage_return_pin())

    def test_pin_rules(self):
        for pin, confirm in (("111111", None), ("123456", None), ("654321", None), ("12ab56", None), ("80241", None)):
            with self.subTest(pin=pin), self.assertRaises(UserError):
                self._apply_pin(self.manager, self.manager, pin, confirm)
        with self.assertRaises(UserError):
            self._apply_pin(self.manager, self.manager, "802413", "802414")
        with self.assertRaises(UserError):
            self._apply_pin(self.cashier, self.cashier, "802413")  # bukan Store Manager

    def test_pin_requires_identity_check_and_hash_not_readable(self):
        with self.assertRaises(UserError):
            self._apply_pin(self.manager, self.manager, "802413", identity=False)
        with self.assertRaises(UserError):
            self.manager.with_user(self.manager).efn_action_return_pin_wizard()  # @check_identity: hanya HTTP
        with self.assertRaises(AccessError):
            self.manager.with_user(self.manager2).read(["efn_return_pin_hash"])
        with self.assertRaises(AccessError):
            self.manager.with_user(self.manager).read(["efn_return_pin_hash"])
