"""E-28: staf outlet (siap, serah terima kode ambil) + outbox event bertanda tangan HMAC (docs/04 §10.5)."""

import hashlib
import hmac
import json
import os
import tempfile
import uuid
from unittest.mock import MagicMock, patch

import requests

from odoo.exceptions import AccessError, UserError
from odoo.fields import Command
from odoo.tests import new_test_user, tagged
from odoo.tools import mute_logger

from odoo.addons.efn_online_order.models import efn_online_order as order_module

from .common import PICKUP_CODE, EfnOnlineCommon

POST = "odoo.addons.efn_online_order.models.efn_online_event_outbox.requests.post"
SECRET = "s" * 48
PAYLOAD_KEYS = {
    "schema_version",
    "idempotency_key",
    "event_id",
    "seq",
    "platform_order_ref",
    "checkout_ref",
    "channel_code",
    "company_code",
    "event",
    "state",
    "occurred_at",
    "refund",
}


def _response(status, body=None):
    response = MagicMock()
    response.status_code = status
    response.json.return_value = body or {}
    return response


@tagged("post_install", "-at_install", "efn")
class TestEfnOnlineHandover(EfnOnlineCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.stock_user = new_test_user(
            cls.env,
            login="efn_online_stock",
            groups="base.group_user,stock.group_stock_user",
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )
        cls.manager_user = new_test_user(
            cls.env,
            login="efn_online_mgr",
            groups="base.group_user,sales_team.group_sale_manager",
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )

    def _paid_order(self):
        part, _payload, _result = self.place()
        self.pay(part)
        return self.record(part)

    def _handover(self, order, code, user=None):
        wizard = (
            self.env["efn.online.handover.wizard"]
            .with_user(user or self.stock_user)
            .create({"order_id": order.id, "code": code})
        )
        wizard.action_confirm()
        return wizard

    def test_ready_requires_stock_group(self):
        order = self._paid_order()
        with self.assertRaises(AccessError):
            order.with_user(self.manager_user).action_efn_ready()
        plain = new_test_user(
            self.env,
            login="efn_online_plain3",
            groups="base.group_user",
            company_id=self.company.id,
            company_ids=[Command.set(self.company.ids)],
        )
        with self.assertRaises(AccessError):
            order.with_user(plain).action_efn_ready()
        order.with_user(self.stock_user).action_efn_ready()
        self.assertEqual(order.state, "ready")
        self.assertTrue(order.ready_at)
        self.assertEqual(order.event_ids.sorted("seq")[-1].event, "ready")

    def test_handover_wrong_code_five_times_review(self):
        order = self._paid_order()
        order.with_user(self.stock_user).action_efn_ready()
        for attempt in range(1, 5):
            wizard = self._handover(order, "ZZZZ-9999")
            self.assertFalse(wizard.done)
            self.assertFalse(wizard.code)
            self.assertEqual(order.pickup_attempts, attempt)
            self.assertEqual(order.state, "ready")
        self._handover(order, "WRONG")
        self.assertEqual(order.state, "review")
        self.assertEqual(order.review_reason, "pickup_code_attempts")
        self.assertFalse(order.pickup_code_hash)  # SEC-99
        # Setelah review, kode benar pun tidak menyerahkan pesanan.
        wizard = self._handover(order, PICKUP_CODE)
        self.assertFalse(wizard.done)
        self.assertEqual(order.state, "review")

    def test_handover_success_validates_picking(self):
        order = self._paid_order()
        order.with_user(self.stock_user).action_efn_ready()
        wizard = self._handover(order, "abcd-2345")
        self.assertTrue(wizard.done)
        self.assertEqual(order.state, "picked_up")
        self.assertTrue(order.picked_up_at)
        self.assertFalse(order.pickup_code_hash)
        self.assertTrue(all(p.state == "done" for p in order.sale_order_id.picking_ids))
        self.assertEqual(order.event_ids.sorted("seq")[-1].event, "picked_up")

    def test_handover_other_company_denied(self):
        order = self._paid_order()
        stock_2 = new_test_user(
            self.env,
            login="efn_online_stock2",
            groups="base.group_user,stock.group_stock_user",
            company_id=self.company_2.id,
            company_ids=[Command.set(self.company_2.ids)],
        )
        # SEC-103: akses ditolak dilaporkan di wizard (tanpa exception) agar kode tidak tertinggal.
        wizard = self._handover(order, PICKUP_CODE, user=stock_2)
        self.assertFalse(wizard.sudo().done)
        self.assertFalse(wizard.sudo().code)
        self.assertEqual(order.state, "confirmed")

    def test_sec103_code_not_kept_when_validation_fails(self):
        order = self._paid_order()
        Order = type(self.env["efn.online.order"])
        with (
            patch.object(Order, "_efn_handover", side_effect=UserError("Pengiriman tidak dapat divalidasi")),
            mute_logger("odoo.addons.efn_online_order"),
        ):
            wizard = self._handover(order, PICKUP_CODE)
        self.assertFalse(wizard.done)
        self.assertFalse(wizard.code)
        self.assertIn("tidak dapat divalidasi", wizard.message)
        self.env.flush_all()
        self.env.cr.execute("SELECT count(*) FROM efn_online_handover_wizard WHERE code IS NOT NULL")
        self.assertEqual(self.env.cr.fetchone()[0], 0)
        # Sukses juga tidak menyimpan kode.
        ok = self._handover(order, PICKUP_CODE)
        self.assertTrue(ok.done)
        self.assertFalse(ok.code)


@tagged("post_install", "-at_install", "efn")
class TestEfnOnlineOutbox(EfnOnlineCommon):
    def setUp(self):
        super().setUp()
        tmp = tempfile.mkdtemp()
        self.paths = {}
        for name, content in (("hmac", SECRET), ("key", "svc-key-test"), ("ca", "dummy-ca")):
            path = os.path.join(tmp, name)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(content)
            self.paths[name] = path
        env = {
            "EFN_ONLINE_EVENT_HMAC_FILE": self.paths["hmac"],
            "EFN_BFF_API_KEY_FILE": self.paths["key"],
            "EFN_CA_FILE": self.paths["ca"],
            "EFN_BFF_URL": "https://api-gateway:3000",
        }
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _outbox(self):
        return self.env["efn.online.event.outbox"].sudo()

    def test_signed_payload_without_pii(self):
        part, _payload, _result = self.place()
        order = self.record(part)
        with patch(POST, return_value=_response(200)) as post:
            processed = self._outbox()._efn_process_due()
        self.assertEqual(len(processed), 1)
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://api-gateway:3000/v1/pos/online/order-events")
        self.assertEqual(kwargs["verify"], self.paths["ca"])
        self.assertFalse(kwargs["allow_redirects"])
        raw = kwargs["data"]
        body = json.loads(raw)
        self.assertEqual(set(body), PAYLOAD_KEYS)
        self.assertEqual(body["event"], "placed")
        self.assertEqual(body["seq"], order.event_seq)
        self.assertGreater(body["seq"], 24 * 3600 * 200, "G18-QA-01: seq berbasis detik sejak 2026-01-01")
        event = order.event_ids
        self.assertEqual(str(uuid.UUID(body["event_id"], version=4)), body["event_id"])
        self.assertEqual(body["event_id"], event.event_id)
        # G18-QA-01: kunci tanpa id lokal Odoo.
        self.assertEqual(body["idempotency_key"], f"online:{part}:placed:{body['seq']}")
        self.assertNotIn(f"online:{order.id}:", body["idempotency_key"])
        self.assertEqual(body["platform_order_ref"], part)
        self.assertNotIn(PICKUP_CODE, raw.decode())
        self.assertNotIn(order.pickup_code_hash, raw.decode())
        headers = kwargs["headers"]
        self.assertEqual(headers["X-EFN-Service-Key"], "svc-key-test")
        signature = headers["X-EFN-Signature"]
        timestamp = signature.split(",")[0].split("=")[1]
        expected = hmac.new(SECRET.encode(), f"{timestamp}.".encode() + raw, hashlib.sha256).hexdigest()
        self.assertEqual(signature, f"t={timestamp},v1={expected}")
        self.assertEqual(order.event_ids.state, "done")

    def test_events_sent_in_seq_order(self):
        part, _payload, _result = self.place()
        self.pay(part)
        events = self.record(part).event_ids.sorted("seq")
        self.assertEqual(events.mapped("event"), ["placed", "paid", "confirmed"])
        first = events[0]
        first.write({"next_attempt_at": "2999-01-01 00:00:00"})
        with patch(POST, return_value=_response(200)) as post:
            self._outbox()._efn_process_due()
        post.assert_not_called()  # seq 2/3 menunggu seq 1
        first.write({"next_attempt_at": False})
        with patch(POST, return_value=_response(200)) as post:
            self._outbox()._efn_process_due()
        sent = [json.loads(c.kwargs["data"])["seq"] for c in post.call_args_list]
        self.assertEqual(sent, events.mapped("seq"))

    def test_final_and_retry_statuses(self):
        part, _payload, _result = self.place()
        event = self.record(part).event_ids
        with patch(POST, return_value=_response(503)) as post, mute_logger("odoo.addons.efn_online_order"):
            self._outbox()._efn_process_due()
        first_body = json.loads(post.call_args.kwargs["data"])
        self.assertEqual(event.state, "pending")
        self.assertEqual(event.last_error, "http_503")
        self.assertEqual(event.attempt_count, 1)
        event.next_attempt_at = False
        with patch(POST, side_effect=requests.exceptions.Timeout()), mute_logger("odoo.addons.efn_online_order"):
            self._outbox()._efn_process_due()
        self.assertEqual(event.last_error, "bff_timeout")
        event.next_attempt_at = False
        with patch(POST, return_value=_response(409)) as post, mute_logger("odoo.addons.efn_online_order"):
            self._outbox()._efn_process_due()
        self.assertEqual(event.state, "failed")
        self.assertEqual(event.last_error, "http_409")
        # G18-QA-01: retry event yang sama membawa event_id & kunci yang sama.
        retry_body = json.loads(post.call_args.kwargs["data"])
        self.assertEqual(
            (retry_body["event_id"], retry_body["idempotency_key"]),
            (first_body["event_id"], first_body["idempotency_key"]),
        )

    def test_missing_hmac_secret_never_sends_unsigned(self):
        part, _payload, _result = self.place()
        os.remove(self.paths["hmac"])
        with patch(POST) as post, mute_logger("odoo.addons.efn_online_order"):
            self._outbox()._efn_process_due()
        post.assert_not_called()
        event = self.record(part).event_ids
        self.assertEqual(event.state, "pending")
        self.assertEqual(event.last_error, "hmac_missing")

    def test_plain_http_url_rejected(self):
        part, _payload, _result = self.place()
        with (
            patch.dict(os.environ, {"EFN_BFF_URL": "http://api-gateway:3000"}),
            patch(POST) as post,
            mute_logger("odoo.addons.efn_online_order", "odoo.addons.efn_core"),
        ):
            self._outbox()._efn_process_due()
        post.assert_not_called()
        self.assertEqual(self.record(part).event_ids.last_error, "bff_insecure_url")

    def test_retry_events_staff_action(self):
        part, _payload, _result = self.place()
        order = self.record(part)
        order.event_ids.write({"state": "failed", "last_error": "http_409"})
        manager = new_test_user(
            self.env,
            login="efn_online_mgr_retry",
            groups="base.group_user,sales_team.group_sale_manager",
            company_id=self.company.id,
            company_ids=[Command.set(self.company.ids)],
        )
        stock = new_test_user(
            self.env,
            login="efn_online_stock_retry",
            groups="base.group_user,stock.group_stock_user",
            company_id=self.company.id,
            company_ids=[Command.set(self.company.ids)],
        )
        with self.assertRaises(AccessError):
            order.with_user(stock).action_efn_retry_events()
        order.with_user(manager).action_efn_retry_events()
        self.assertEqual(order.event_ids.state, "pending")

    def test_qa01_event_ids_unique_and_not_local_id_based(self):
        """G18-QA-01: event_id UUID per event; kunci = platform_order_ref + event + seq (tanpa id lokal)."""
        part, _payload, _result = self.place()
        self.pay(part)
        order = self.record(part)
        events = order.event_ids.sorted("seq")
        self.assertGreaterEqual(len(events), 2)
        self.assertEqual(len(set(events.mapped("event_id"))), len(events))
        self.assertEqual(events.mapped("seq"), sorted(set(events.mapped("seq"))), "naik ketat")
        for event in events:
            self.assertEqual(event.idempotency_key, f"online:{part}:{event.event}:{event.seq}")
            self.assertEqual(str(uuid.UUID(event.event_id, version=4)), event.event_id)

    def test_qa01_seq_continues_after_restore(self):
        """Restore DB: `event_seq` & event terakhir hilang, tetapi seq baru tetap > seq yang sudah diterima BFF."""
        part, _payload, _result = self.place()
        self.pay(part)
        order = self.record(part)
        sent_max = max(order.event_ids.mapped("seq"))
        lost = order.event_ids.filtered(lambda e: e.seq > order.event_ids.sorted("seq")[0].seq)
        lost.unlink()
        order.sudo().write({"event_seq": order.event_ids.seq})  # keadaan backup lama
        later = order_module._efn_epoch_seconds() + 60  # restore memakan waktu
        with patch.object(order_module, "_efn_epoch_seconds", return_value=later):
            event = order._efn_emit("review")
        self.assertGreater(event.seq, sent_max)
        self.assertEqual(event.idempotency_key, f"online:{part}:review:{event.seq}")
        # Event beruntun dalam detik yang sama tetap naik (+1).
        with patch.object(order_module, "_efn_epoch_seconds", return_value=later):
            self.assertEqual(order._efn_emit("review").seq, event.seq + 1)

    def test_qa01_event_conflict_final_review(self):
        part, _payload, _result = self.place()
        order = self.record(part)
        event = order.event_ids
        body = {"error": {"code": "event_conflict", "message": "x"}}
        with patch(POST, return_value=_response(409, body)), mute_logger("odoo.addons.efn_online_order"):
            self._outbox()._efn_process_due()
        self.assertEqual((event.state, event.last_error), ("failed", "event_conflict"))
        self.assertEqual((order.state, order.review_reason), ("review", "event_conflict"))
        # 409 lain tetap final biasa (tanpa mengubah state pesanan).
        part2, _payload, _result = self.place()
        order2 = self.record(part2)
        with (
            patch(POST, return_value=_response(409, {"error": {"code": "company_mismatch"}})),
            mute_logger("odoo.addons.efn_online_order"),
        ):
            self._outbox()._efn_process_due()
        self.assertEqual(order2.event_ids.last_error, "http_409")
        self.assertEqual(order2.state, "placed")
