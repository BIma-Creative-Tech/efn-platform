"""Review r11 (SEC-113/116/121): percobaan RPC nyata kasir lewat ``/web/dataset/call_kw`` (HttpCase tanpa browser).

Tag ``efn_tour`` (job CI ``odoo-tour``; fixture ``TestPointOfSaleHttpCommon`` tidak cocok dengan 4 PT
``efn_setup_company`` di job unit, pola ``efn_pos_loyalty_ledger/tests/test_rpc_access.py``).

- SEC-113: kasir tidak bisa ``pos.payment.write/create/unlink`` pada order yang sudah tersinkron (paid).
- SEC-116: ``is_change`` dari payload sync dihitung ulang server (bank + is_change palsu -> False).
- SEC-121 (milik functional ``efn_setup_pos``): status ACL inti ``res.partner.bank`` untuk pemegang
  ``base.group_partner_manager`` dicatat (bukan diperbaiki di modul ini).
"""

import json
import logging
import uuid

from odoo import Command, fields
from odoo.tests import tagged
from odoo.tools import mute_logger

from odoo.addons.point_of_sale.tests.test_frontend import TestPointOfSaleHttpCommon

_logger = logging.getLogger(__name__)


@tagged("post_install", "-at_install", "efn_tour")
class TestEfnPosPaymentIntegrityRpc(TestPointOfSaleHttpCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env["product.product"].create(
            {
                "name": "Produk Integritas Bayar",
                "list_price": 100.0,
                "available_in_pos": True,
                "taxes_id": [Command.clear()],
            }
        )
        cls.cash = cls.main_pos_config.payment_method_ids.filtered("is_cash_count")[:1]
        cls.bank = cls.main_pos_config.payment_method_ids.filtered(
            lambda m: not m.is_cash_count and m.journal_id.type == "bank" and not m.split_transactions
        )[:1]

    def setUp(self):
        super().setUp()
        self.main_pos_config.with_user(self.pos_user).open_ui()
        self.pos_session = self.main_pos_config.current_session_id

    def _order_payload(self, payments, qty=1.0, amount_return=0.0):
        total = 100.0 * qty
        return {
            "uuid": str(uuid.uuid4()),
            "name": "Order integritas",
            "session_id": self.pos_session.id,
            "company_id": self.main_pos_config.company_id.id,
            "pricelist_id": self.main_pos_config.pricelist_id.id,
            "partner_id": False,
            "user_id": self.pos_user.id,
            "date_order": fields.Datetime.to_string(fields.Datetime.now()),
            "amount_total": total,
            "amount_tax": 0.0,
            "amount_paid": sum(p["amount"] for p in payments),
            "amount_return": amount_return,
            "state": "paid",
            "lines": [
                (
                    0,
                    0,
                    {
                        "product_id": self.product.id,
                        "qty": qty,
                        "price_unit": 100.0,
                        "price_subtotal": total,
                        "price_subtotal_incl": total,
                        "tax_ids": [(6, 0, [])],
                        "discount": 0.0,
                    },
                )
            ],
            "payment_ids": [(0, 0, {"name": fields.Datetime.now(), **p}) for p in payments],
        }

    def _sync_as_cashier(self, payload):
        self.env["pos.order"].with_user(self.pos_user).sync_from_ui([payload])
        return self.env["pos.order"].search([("uuid", "=", payload["uuid"])])

    def _call_kw(self, model, method, args, kwargs=None):
        response = self.url_open(
            "/web/dataset/call_kw",
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "call",
                    "id": 1,
                    "params": {"model": model, "method": method, "args": args, "kwargs": kwargs or {}},
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_sec113_cashier_cannot_tamper_paid_payments(self):
        order = self._sync_as_cashier(self._order_payload([{"amount": 100.0, "payment_method_id": self.cash.id}]))
        self.assertEqual(order.state, "paid")
        payment = order.payment_ids
        self.authenticate("pos_user", "pos_user")
        attempts = {
            "write_method": ("write", [payment.ids, {"payment_method_id": self.bank.id}]),
            "write_amount": ("write", [payment.ids, {"amount": 1.0}]),
            "create": ("create", [{"pos_order_id": order.id, "payment_method_id": self.cash.id, "amount": 50.0}]),
            "unlink": ("unlink", [payment.ids]),
        }
        results = {}
        for name, (method, args) in attempts.items():
            with mute_logger("odoo.http"):
                results[name] = "error" in self._call_kw("pos.payment", method, args)
        _logger.info("EFN r11 probe SEC-113 (kasir call_kw pos.payment, True=ditolak): %s", results)
        self.assertEqual(results, dict.fromkeys(attempts, True))
        payment.invalidate_recordset()
        self.assertEqual((payment.payment_method_id, payment.amount), (self.cash, 100.0))
        self.assertEqual(len(order.payment_ids), 1)

    def test_sec116_is_change_from_payload_recomputed(self):
        # Tender hanya bank (200) untuk total 100, lalu "kembalian" tunai -100 bertanda is_change dari browser:
        # tunai keluar laci padahal tidak ada tunai masuk.
        payload = self._order_payload(
            [
                {"amount": 200.0, "payment_method_id": self.bank.id},
                {"amount": -100.0, "payment_method_id": self.cash.id, "is_change": True},
            ],
        )
        order = self._sync_as_cashier(payload)
        fake = order.payment_ids.filtered(lambda p: p.amount < 0)
        _logger.info("EFN r11 probe SEC-116 (is_change payload tersimpan setelah sync): %s", fake.is_change)
        self.assertFalse(fake.is_change, "is_change palsu dari payload tidak dipercaya (dihitung ulang server)")

    def test_sec121_partner_bank_acl_probe(self):
        """Catatan untuk functional (SEC-121): pemegang Contact Creation bisa membuat res.partner.bank (ACL inti)."""
        self.pos_user.write({"group_ids": [Command.link(self.env.ref("base.group_partner_manager").id)]})
        self.authenticate("pos_user", "pos_user")
        partner = self.env["res.partner"].create({"name": "Vendor Uji Bank"})
        with mute_logger("odoo.http"):
            result = self._call_kw(
                "res.partner.bank", "create", [{"partner_id": partner.id, "acc_number": "1234567890"}]
            )
        _logger.info(
            "EFN r11 probe SEC-121 (Contact Creation membuat res.partner.bank): %s",
            "ditolak" if "error" in result else "DIIZINKAN",
        )

    # --------------------------------------------------------- re-review G18
    @staticmethod
    def _error_name(payload):
        """Nama exception dari respons JSON-RPC (None = diterima)."""
        error = payload.get("error")
        return (error.get("data") or {}).get("name", "error") if error else None

    @staticmethod
    def _jsonable(payload):
        return json.loads(json.dumps(payload, default=str))

    def test_sec125_cashier_rpc_cannot_mark_paid_outside_save(self):
        draft_payload = self._order_payload([{"amount": 100.0, "payment_method_id": self.cash.id}])
        draft_payload["state"] = "draft"
        draft = self._sync_as_cashier(draft_payload)
        self.assertEqual(draft.state, "draft")
        new_paid = self._jsonable(self._order_payload([{"amount": 100.0, "payment_method_id": self.cash.id}]))
        new_paid.pop("payment_ids")
        original = self._sync_as_cashier(self._order_payload([{"amount": 100.0, "payment_method_id": self.bank.id}]))
        refund = self.env["pos.order"].browse(original.refund()["res_id"])
        self.authenticate("pos_user", "pos_user")
        results = {}
        with mute_logger("odoo.http"):
            results["action_pos_order_paid"] = self._call_kw("pos.order", "action_pos_order_paid", [draft.ids])
            results["write_state"] = self._call_kw("pos.order", "write", [draft.ids, {"state": "paid"}])
            results["create_paid"] = self._call_kw("pos.order", "create", [new_paid])
        # Wizard Payment backend = POS manager saja (ACL CE); jalur itu menjalankan validasi E-31 (tunai ditolak).
        self.authenticate("pos_admin", "pos_admin")
        with mute_logger("odoo.http"):
            wizard = self._call_kw(
                "pos.make.payment",
                "create",
                [{"payment_method_id": self.cash.id, "amount": refund.amount_total}],
                {"context": {"active_id": refund.id}},
            )
            self.assertIn("result", wizard, wizard.get("error"))
            results["wizard_refund_cash_manager"] = self._call_kw(
                "pos.make.payment", "check", [[wizard["result"]]], {"context": {"active_id": refund.id}}
            )
        denied = {name: self._error_name(payload) for name, payload in results.items()}
        _logger.info("EFN re-review probe SEC-125 (call_kw, jenis penolakan): %s", denied)
        # Ditolak guard/validasi bisnis (UserError), bukan sekadar ACL.
        self.assertEqual(denied, dict.fromkeys(results, "odoo.exceptions.UserError"))
        self.assertEqual((draft.state, refund.state), ("draft", "draft"))
        self.assertFalse(self.env["pos.order"].search([("uuid", "=", new_paid["uuid"])]))

    def test_sec126_cashier_rpc_sync_foreign_commands_rejected(self):
        paid = self._sync_as_cashier(self._order_payload([{"amount": 100.0, "payment_method_id": self.cash.id}]))
        foreign = paid.payment_ids
        self.authenticate("pos_user", "pos_user")
        commands = {
            "update": [1, foreign.id, {"payment_method_id": self.bank.id}],
            "delete": [2, foreign.id],
            "link": [4, foreign.id],
        }
        results = {}
        for name, command in commands.items():
            payload = self._jsonable(self._order_payload([{"amount": 100.0, "payment_method_id": self.cash.id}]))
            payload["payment_ids"].append(command)
            with mute_logger("odoo.http"):
                results[name] = self._error_name(self._call_kw("pos.order", "sync_from_ui", [[payload]]))
        _logger.info("EFN re-review probe SEC-126 (kasir sync_from_ui perintah asing): %s", results)
        self.assertEqual(results, dict.fromkeys(commands, "odoo.exceptions.UserError"))
        foreign.invalidate_recordset()
        self.assertEqual(
            (foreign.exists(), foreign.pos_order_id, foreign.payment_method_id), (foreign, paid, self.cash)
        )
