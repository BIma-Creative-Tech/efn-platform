"""E-27 sesi meja, outbox event, keamanan (docs/08 §13.1b test 7, 13, 14 + docs/04 §14.A.2/§14.A.4, L-76). Tag efn."""

import base64
import hashlib
import hmac
import json
import logging
from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged

from .common import FAKE_TOKEN, POST, QR_URL, SECRET, EfnQrCommon, response

EVENT_KEYS = {
    "schema_version",
    "idempotency_key",
    "seq",
    "table_session_id",
    "company_code",
    "config_id",
    "event",
    "occurred_at",
}


@tagged("post_install", "-at_install", "efn")
class TestEfnQrSession(EfnQrCommon):
    def _line(self, product, qty=1):
        return {"product_id": product.id, "qty": qty, "attribute_value_ids": []}

    def test_open_session_stores_only_id_and_renders_qr(self):
        anchor = self._anchor()
        root = logging.getLogger()
        with self.assertLogs(root, level="DEBUG") as logs, patch(POST, side_effect=self._fake_post) as post:
            result = self.config.efn_qr_session_open(self.table.id, anchor.uuid, False)
        self.assertEqual(set(result), {"qr_png_b64", "expires_at", "table_label", "print_no"})
        self.assertTrue(base64.b64decode(result["qr_png_b64"]).startswith(b"\x89PNG"))
        self.assertEqual(result["table_label"], "T12 / Teras")
        # Body ke BFF (L-76: tanpa order_uuid) + kanal TLS terverifikasi.
        _args, kwargs = post.call_args
        self.assertEqual(
            set(kwargs["json"]), {"idempotency_key", "company_code", "config_id", "table_id", "table_label", "reissue"}
        )
        self.assertEqual(kwargs["json"]["company_code"], "PB")
        self.assertFalse(kwargs["json"]["reissue"])
        self.assertEqual(kwargs["verify"], self.paths["ca"])
        self.assertFalse(kwargs["allow_redirects"])
        session = self.env["efn.pos.table.session"].search([("table_id", "=", self.table.id)])
        self.assertEqual(session.state, "open")
        self.assertEqual(session.order_uuid, anchor.uuid)
        self.assertEqual(anchor.efn_qr_table_session_id, session)
        # Token/URL tidak disimpan di record mana pun dan tidak di-log.
        for value in session.read()[0].values():
            self.assertNotIn(FAKE_TOKEN, str(value))
        self.assertFalse(any(FAKE_TOKEN in line or QR_URL in line for line in logs.output))
        self.assertLessEqual(session.expires_at, fields.Datetime.now() + timedelta(hours=4, minutes=1))

    def test_open_requires_order_on_table_and_offline_bff(self):
        anchor = self._anchor(self.table2)
        with patch(POST, side_effect=self._fake_post) as post:
            result = self.config.efn_qr_session_open(self.table.id, anchor.uuid, False)
        self.assertEqual(result["error_code"], "order_invalid")
        post.assert_not_called()
        anchor = self._anchor()
        with patch(POST, return_value=response(503)):
            result = self.config.efn_qr_session_open(self.table.id, anchor.uuid, False)
        self.assertEqual(result["error_code"], "bff_unavailable", "BFF mati -> POS tetap jalan")
        self.assertFalse(self.env["efn.pos.table.session"].search([("table_id", "=", self.table.id)]))

    def test_reissue_closes_old_session_without_event(self):
        _r, first, anchor = self._open()
        with patch(POST, side_effect=self._fake_post):
            again = self.config.efn_qr_session_open(self.table.id, anchor.uuid, False)
        self.assertEqual(again["error_code"], "session_exists")
        _r, second, _a = self._open(anchor=anchor, reissue=True)
        self.assertNotEqual(first, second)
        self.assertEqual((first.state, first.close_reason), ("closed", "reissued"))
        self.assertEqual(self.bff_calls[-1][1]["reissue"], True)
        self.assertFalse(self._outbox(first), "BFF mencabut sendiri saat reissue")
        self.assertEqual(anchor.efn_qr_table_session_id, second)

    def test_reissue_flag_forwarded_when_odoo_session_already_expired(self):
        """Sesi Odoo `expired` (bukan `open`) + BFF masih menganggap open: reissue=True harus sampai ke BFF."""
        _r, first, anchor = self._open()
        first.sudo().write({"expires_at": fields.Datetime.now() - timedelta(minutes=1)})
        self.env["efn.pos.table.session"]._cron_efn_qr_expire()
        self.assertEqual(first.state, "expired")
        _r, second, _a = self._open(anchor=anchor, reissue=True)
        self.assertEqual(self.bff_calls[-1][1]["reissue"], True)
        self.assertEqual(second.state, "open")
        self.assertEqual(anchor.efn_qr_table_session_id, second)
        # Tanpa permintaan reissue, flag tetap False.
        _r, third, _a = self._open(table=self.table2)
        self.assertFalse(self.bff_calls[-1][1]["reissue"])
        self.assertEqual(third.state, "open")

    def test_access_denied_message_is_clean_of_client_context(self):
        """L-81 (c): pesan AccessError dibangun dari env bersih (lang konteks klien tidak dipakai/bocor)."""
        Qr = self.env["efn.pos.qr.order"]
        with self.assertRaises(AccessError) as ctx:
            Qr.with_user(self.cashier).with_context(lang="xx_XX").efn_qr_menu(config_id=self.config.id)
        self.assertEqual(ctx.exception.args[0], "Akses QR ditolak.")
        # config tidak ada vs milik PT lain -> pesan sama (tidak membocorkan keberadaan).
        with self.assertRaises(AccessError) as missing:
            Qr.with_user(self.tech).efn_qr_menu(config_id=999999999)
        self.assertEqual(missing.exception.args[0], "Akses QR ditolak.")

    # 13 ----------------------------------------------------------------------------------------------
    def test_13_pay_closes_session_and_signed_event(self):
        _r, session, anchor = self._open()
        result = self._submit(session, [self._line(self.croissant, 4)])
        order = self.env["efn.pos.qr.order"].search([("submission_ref", "=", result["submission_ref"])]).order_id
        self._pay(order)
        self.assertEqual(order.state, "paid")
        self.assertEqual(session.state, "open", "order pelayan masih draft di meja")
        anchor.action_pos_order_cancel()
        self.assertEqual((session.state, session.close_reason), ("closed", "paid"))
        close = self._outbox(session).filtered(lambda o: o.event == "session_closed")
        self.assertEqual(len(close), 1)
        # L-80 + K2 r15: order sesi yang dibayar (order pelayan dibatalkan -> tidak ikut); dasar poin tanpa pajak & SC;
        # pos_member_attached=false (tanpa member POS).
        expected_orders = [
            {"order_uuid": order.uuid, "eligible_amount": 100000, "company_code": "PB", "pos_member_attached": False}
        ]
        self.assertEqual(close.orders_snapshot, expected_orders)
        self.assertFalse(order.efn_qr_points_eligible, "K3 r15: flag baru dipasang setelah event close diterima BFF")
        post = self._send_outbox()
        self.assertTrue(close.snapshot_frozen)
        self.assertEqual(close.state, "done")
        self.assertTrue(order.efn_qr_points_eligible)
        self.assertEqual(order.efn_qr_eligible_amount, 100000)
        self.assertFalse(anchor.efn_qr_points_eligible)
        _args, kwargs = post.call_args
        self.assertTrue(_args[0].endswith("/v1/pos/fnb/events"))
        raw = kwargs["data"]
        body = json.loads(raw)
        self.assertEqual(set(body), EVENT_KEYS | {"close"})
        self.assertEqual(body["close"], {"reason": "paid", "orders": expected_orders})
        self.assertEqual(body["idempotency_key"], f"fnb:{session.bff_session_id}:session_closed:{body['seq']}")
        self.assertEqual(kwargs["headers"]["X-EFN-Service-Key"], "svc-key-uji")
        signature = kwargs["headers"]["X-EFN-Signature"]
        timestamp = signature.split(",")[0].split("=")[1]
        expected = hmac.new(SECRET.encode(), f"{timestamp}.".encode() + raw, hashlib.sha256).hexdigest()
        self.assertEqual(signature, f"t={timestamp},v1={expected}")
        self.assertNotIn("croissant", raw.decode().lower(), "tanpa isi pesanan")

    def test_refund_qr_order_sends_qr_reverse(self):
        """L-80: refund order sesi QR -> efn_pos_loyalty_ledger mengirim reverse earn_channel qr tanpa member_ref."""
        if "efn.pos.loyalty.outbox" not in self.env:
            self.skipTest("efn_pos_loyalty_ledger tidak terpasang")
        session, anchor, order = self._paid_qr_order()
        anchor.action_pos_order_cancel()
        self._send_outbox()
        self.assertTrue(order.efn_qr_points_eligible)
        refund = self._refund(order, 4)
        self.assertFalse(refund.efn_qr_refund_netted)
        reverse = self._qr_reverses(order)
        self.assertEqual(len(reverse), 1)
        self.assertEqual(reverse.earn_channel, "qr")
        self.assertEqual(reverse.state, "pending")
        self.assertAlmostEqual(reverse.refund_ratio, 1.0, places=4)
        path, payload, log_context = reverse._efn_build_request()
        self.assertEqual(path, "/v1/pos/loyalty/reverse")
        self.assertNotIn("member_ref", payload)
        self.assertEqual(payload["earn_channel"], "qr")
        self.assertEqual(payload["original_order_ref"], order.uuid)
        self.assertEqual(payload["points_restored_value"], 0)
        self.assertNotIn("member", log_context)

    # r15 K2/K3 (SEC-164) ----------------------------------------------------------------------------
    def _close_event(self, session):
        return self._outbox(session).filtered(lambda o: o.event == "session_closed")

    def _sent_close_body(self, post):
        bodies = [json.loads(kwargs["data"]) for _args, kwargs in post.call_args_list]
        return [body for body in bodies if body["event"] == "session_closed"][-1]

    def test_refund_before_close_is_netted_without_reverse(self):
        """Refund saat sesi masih open: tanpa reverse; close melaporkan neto; refund ditandai netted setelah 2xx."""
        session, anchor, order = self._paid_qr_order()
        self.assertEqual(session.state, "open")
        refund = self._refund(order, 2)
        self.assertIn(refund.state, ("paid", "done"))
        self.assertFalse(refund.efn_qr_table_session_id, "refund tidak pernah dilekatkan ke sesi (SEC-166)")
        reverses = self._qr_reverses(order)
        if reverses is not None:
            self.assertFalse(reverses, "K3: sesi belum close -> reverse tidak dikirim")
        anchor.action_pos_order_cancel()
        self.assertEqual((session.state, session.close_reason), ("closed", "paid"))
        expected = [
            {"order_uuid": order.uuid, "eligible_amount": 50000, "company_code": "PB", "pos_member_attached": False}
        ]
        self.assertEqual(self._close_event(session).orders_snapshot, expected)
        self.assertFalse(refund.efn_qr_refund_netted)
        post = self._send_outbox()
        self.assertEqual(self._sent_close_body(post)["close"]["orders"], expected)
        self.assertTrue(refund.efn_qr_refund_netted)
        self.assertTrue(order.efn_qr_points_eligible)
        self.assertEqual(order.efn_qr_eligible_amount, 50000)
        if reverses is not None:
            self.assertFalse(self._qr_reverses(order))

    def test_refund_after_close_before_first_attempt_is_netted(self):
        """L-83: sesi sudah close, snapshot belum dibekukan (belum ada POST) -> refund dinetokan, tanpa reverse."""
        session, anchor, order = self._paid_qr_order()
        anchor.action_pos_order_cancel()
        close = self._close_event(session)
        self.assertFalse(close.snapshot_frozen)
        refund = self._refund(order, 3)
        self.assertFalse(refund.efn_qr_refund_awaiting)
        reverses = self._qr_reverses(order)
        if reverses is not None:
            self.assertFalse(reverses, "snapshot belum beku -> refund masuk neto, tanpa reverse")
        post = self._send_outbox()
        self.assertEqual(close.state, "done")
        orders = self._sent_close_body(post)["close"]["orders"]
        self.assertEqual([(o["order_uuid"], o["eligible_amount"]) for o in orders], [(order.uuid, 25000)])
        self.assertEqual(close.netted_refund_ids, refund.ids)
        self.assertTrue(refund.efn_qr_refund_netted)
        self.assertEqual(order.efn_qr_eligible_amount, 25000)

    def test_frozen_snapshot_reused_and_awaiting_refund_reversed_on_2xx(self):
        """L-83: snapshot beku sebelum POST pertama; retry mengirim body yang sama persis; refund setelah percobaan
        pertama TIDAK dinetokan, ditandai menunggu, dan di-enqueue reverse QR saat 2xx (rasio vs snapshot)."""
        session, anchor, order = self._paid_qr_order()
        anchor.action_pos_order_cancel()
        close = self._close_event(session)
        first = self._send_outbox(503)
        self.assertTrue(close.snapshot_frozen)
        self.assertEqual(close.state, "pending")
        self.assertFalse(order.efn_qr_points_eligible, "503: BFF belum menerima event -> tanpa flag")
        first_raw = [kwargs["data"] for _a, kwargs in first.call_args_list][-1]
        refund = self._refund(order, 3)
        ledger = self._qr_reverses(order) is not None
        if ledger:
            self.assertTrue(refund.efn_qr_refund_awaiting)
            self.assertFalse(self._qr_reverses(order), "belum 2xx -> belum ada reverse")
        close.next_attempt_at = False
        second = self._send_outbox()
        self.assertEqual(close.state, "done")
        second_raw = [kwargs["data"] for _a, kwargs in second.call_args_list][-1]
        self.assertEqual(second_raw, first_raw, "retry = body beku yang sama persis (kunci idempotensi sama)")
        self.assertEqual(json.loads(second_raw)["close"]["orders"][0]["eligible_amount"], 100000)
        self.assertFalse(refund.efn_qr_refund_netted)
        self.assertEqual(order.efn_qr_eligible_amount, 100000)
        if ledger:
            self.assertFalse(refund.efn_qr_refund_awaiting)
            reverse = self._qr_reverses(order)
            self.assertEqual(len(reverse), 1)
            self.assertEqual((reverse.order_id, reverse.earn_channel), (refund, "qr"))
            self.assertEqual(reverse.eligible_amount, 75000)
            self.assertAlmostEqual(reverse.refund_ratio, 0.75, places=4, msg="rasio vs eligible_amount snapshot")

    def test_awaiting_refund_kept_and_alerted_when_close_fails(self):
        """L-83: event close gagal permanen -> refund menunggu tetap bertanda (review) + alert, tanpa reverse."""
        if "efn.pos.loyalty.outbox" not in self.env:
            self.skipTest("efn_pos_loyalty_ledger tidak terpasang")
        session, anchor, order = self._paid_qr_order()
        anchor.action_pos_order_cancel()
        close = self._close_event(session)
        self._send_outbox(503)
        refund = self._refund(order, 2)
        self.assertTrue(refund.efn_qr_refund_awaiting)
        close.next_attempt_at = False
        with self.assertLogs("odoo.addons.efn_pos_bff_order.alert", level="ERROR") as logs:
            self._send_outbox(409)
        self.assertEqual(close.state, "failed")
        self.assertTrue(any("EFN_QR_REFUND_UNRESOLVED" in line for line in logs.output))
        self.assertTrue(refund.efn_qr_refund_awaiting, "tetap bertanda untuk review")
        self.assertFalse(order.efn_qr_points_eligible)
        self.assertFalse(self._qr_reverses(order))

    def test_full_refund_before_close_excluded(self):
        session, anchor, order = self._paid_qr_order()
        self._refund(order, 4)
        anchor.action_pos_order_cancel()
        self._send_outbox()  # L-83: snapshot dibekukan pada putaran outbox pertama, bukan saat close
        close = self._close_event(session)
        # Json Odoo menyimpan [] sebagai NULL; status beku dibaca dari snapshot_frozen, body tetap orders: [].
        self.assertTrue(close.snapshot_frozen)
        self.assertEqual(close._efn_payload()["close"]["orders"], [], "neto 0 -> order tidak dikirim (K2)")
        self.assertFalse(order.efn_qr_points_eligible)

    def test_refund_after_close_ratio_against_net(self):
        if "efn.pos.loyalty.outbox" not in self.env:
            self.skipTest("efn_pos_loyalty_ledger tidak terpasang")
        session, anchor, order = self._paid_qr_order()
        first = self._refund(order, 1)
        anchor.action_pos_order_cancel()
        self._send_outbox()
        self.assertTrue(first.efn_qr_refund_netted)
        self.assertEqual(order.efn_qr_eligible_amount, 75000)
        second = self._refund(order, 3)
        reverse = self._qr_reverses(order)
        self.assertEqual(len(reverse), 1, "hanya refund setelah event close diterima yang dikirim sebagai reverse")
        self.assertEqual((reverse.order_id, reverse.earn_channel), (second, "qr"))
        self.assertEqual(reverse.eligible_amount, 75000)
        self.assertAlmostEqual(reverse.refund_ratio, 1.0, places=4, msg="rasio terhadap neto close, bukan bruto")

    # r15 K2 (SEC-165) --------------------------------------------------------------------------------
    def test_pos_member_attached_not_points_eligible(self):
        partner = self.env["res.partner"].create({"name": "Member uji QR r15", "phone": "0812-3456-7890"})
        session, anchor, order = self._paid_qr_order(partner=partner)
        self.assertTrue(order._efn_qr_pos_member_attached())
        anchor.action_pos_order_cancel()
        entry = self._close_event(session).orders_snapshot[0]
        self.assertEqual((entry["order_uuid"], entry["pos_member_attached"]), (order.uuid, True))
        self._send_outbox()
        self.assertFalse(order.efn_qr_points_eligible, "earn POS yang berlaku; refund tidak lewat reverse QR")
        # Partner tanpa HP Indonesia sah = bukan member POS (ledger melewati earn) -> tidak attached.
        stranger = self.env["res.partner"].create({"name": "Tamu tanpa HP uji QR"})
        draft = self._anchor(self.table2)
        draft.sudo().write({"partner_id": stranger.id})
        self.assertFalse(draft._efn_qr_pos_member_attached())
        self.assertFalse(anchor._efn_qr_pos_member_attached())

    # r15 SEC-166 -------------------------------------------------------------------------------------
    def test_sec166_pending_submission_next_guest_not_absorbed(self):
        """Kiriman QR pending tidak dihapus pelayan -> sesi tetap open/expired; tamu berikutnya bayar -> tidak
        terserap; sesi expired tetap menutup (event close) tanpa order baru."""
        croissant = {"product": self.croissant, "quantity": 1, "price_unit": 25000.0}
        anchor = self._anchor(lines=[croissant])
        _r, session, _a = self._open(anchor=anchor)
        pending = self._qr_order(self._submit(session, [self._line(self.croissant)])).order_id
        self._pay(anchor)
        self.assertEqual(session.state, "open", "kiriman QR pending masih draft di meja")
        guest_b = self._anchor(lines=[{**croissant, "quantity": 2}])
        self.assertNotIn(guest_b, (anchor, pending))
        self._pay(guest_b)
        self.assertFalse(guest_b.efn_qr_table_session_id, "tamu berikutnya tidak diserap sesi open")
        session.sudo().write({"expires_at": fields.Datetime.now() - timedelta(minutes=1)})
        self.env["efn.pos.table.session"]._cron_efn_qr_expire()
        self.assertEqual(session.state, "expired")
        guest_c = self._anchor(lines=[croissant])
        self._pay(guest_c)
        self.assertFalse(guest_c.efn_qr_table_session_id, "sesi expired tidak menyerap order baru")
        pending.unlink()  # pelayan akhirnya menolak kiriman -> meja tanpa draft -> sesi expired ditutup
        self.assertEqual((session.state, session.close_reason), ("closed", "paid"))
        uuids = [entry["order_uuid"] for entry in self._close_event(session).orders_snapshot]
        self.assertEqual(uuids, [anchor.uuid])
        self.assertEqual(session.order_ids.filtered(lambda o: o.state == "paid"), anchor)

    # r15 re-review SEC-174 --------------------------------------------------------------------------
    def _session_close_bodies(self, post, session):
        """Body ``session_closed`` yang benar-benar di-POST untuk ``session`` (urut kirim)."""
        bodies = [json.loads(kwargs["data"]) for _args, kwargs in post.call_args_list]
        return [
            body
            for body in bodies
            if body["event"] == "session_closed" and body["table_session_id"] == session.bff_session_id
        ]

    def test_sec174_reissue_keeps_paid_orders_on_old_session(self):
        """Rombongan 1 bayar, kiriman QR pending tertinggal (sesi tetap open), pelayan cetak ulang:
        order dibayar TETAP di sesi lama dan dilaporkan di event close ``paid`` sesi lama; hanya draft yang pindah;
        close sesi baru tidak memuat order rombongan 1."""
        first, anchor, paid = self._paid_qr_order()
        pending = self._qr_order(self._submit(first, [self._line(self.croissant)])).order_id
        self.assertEqual((first.state, paid.state, pending.state), ("open", "paid", "draft"))
        _r, second, _a = self._open(anchor=anchor, reissue=True)
        self.assertNotEqual(first, second)
        self.assertEqual(self.bff_calls[-1][1]["reissue"], True)
        # Order dibayar tetap di sesi lama; draft (jangkar + kiriman pending) pindah ke sesi baru.
        self.assertEqual(paid.efn_qr_table_session_id, first)
        self.assertEqual(anchor.efn_qr_table_session_id, second)
        self.assertEqual(pending.efn_qr_table_session_id, second)
        self.assertEqual(first.order_ids, paid)
        # Sesi lama ditutup `paid` lewat jalur outbox biasa (snapshot beku L-83), bukan `reissued` tanpa event.
        self.assertEqual((first.state, first.close_reason), ("closed", "paid"))
        close_first = self._close_event(first)
        self.assertEqual(len(close_first), 1)
        self.assertEqual(close_first.close_reason, "paid")
        expected_first = [
            {"order_uuid": paid.uuid, "eligible_amount": 100000, "company_code": "PB", "pos_member_attached": False}
        ]
        self.assertEqual(close_first.orders_snapshot, expected_first)
        self.assertFalse(close_first.snapshot_frozen, "pembekuan di putaran outbox pertama, bukan saat cetak ulang")
        post = self._send_outbox()
        self.assertTrue(close_first.snapshot_frozen)
        self.assertEqual(close_first.state, "done")
        bodies = self._session_close_bodies(post, first)
        self.assertEqual(len(bodies), 1)
        self.assertEqual(bodies[0]["close"], {"reason": "paid", "orders": expected_first})
        self.assertTrue(paid.efn_qr_points_eligible, "flag poin dipasang setelah 2xx close sesi LAMA")
        self.assertEqual(paid.efn_qr_eligible_amount, 100000)
        # Rombongan 2 di sesi baru: kiriman baru dibayar, kiriman lama ditolak, jangkar dibatalkan -> close sesi baru.
        fresh = self._qr_order(self._submit(second, [self._line(self.croissant, 2)])).order_id
        self._pay(fresh)
        pending.unlink()
        self.assertEqual(second.state, "open", "jangkar masih draft di meja")
        anchor.action_pos_order_cancel()
        self.assertEqual((second.state, second.close_reason), ("closed", "paid"))
        uuids = [entry["order_uuid"] for entry in self._close_event(second).orders_snapshot]
        self.assertEqual(uuids, [fresh.uuid])
        self.assertNotIn(paid.uuid, uuids, "order rombongan 1 tidak pernah dilaporkan untuk pengklaim sesi baru")
        post = self._send_outbox()
        bodies = self._session_close_bodies(post, second)
        self.assertEqual(len(bodies), 1)
        self.assertEqual([o["order_uuid"] for o in bodies[0]["close"]["orders"]], [fresh.uuid])
        self.assertFalse(self._session_close_bodies(post, first), "close sesi lama tidak dikirim ulang")

    def test_sec174_reissue_without_paid_orders_sends_no_close(self):
        """Tanpa order dibayar di sesi lama: perilaku lama (``reissued``, tanpa event close);
        order batal tetap di sesi lama, draft pindah."""
        _r, first, anchor = self._open()
        pending = self._qr_order(self._submit(first, [self._line(self.croissant)])).order_id
        cancelled = self._qr_order(self._submit(first, [self._line(self.croissant, 3)])).order_id
        cancelled.action_pos_order_cancel()
        self.assertEqual((first.state, cancelled.state), ("open", "cancel"))
        _r, second, _a = self._open(anchor=anchor, reissue=True)
        self.assertEqual((first.state, first.close_reason), ("closed", "reissued"))
        self.assertFalse(self._close_event(first), "BFF mencabut sendiri saat reissue; tidak ada close paid")
        self.assertEqual((anchor | pending).efn_qr_table_session_id, second)
        self.assertEqual(cancelled.efn_qr_table_session_id, first, "order batal tidak dibawa")
        post = self._send_outbox()
        self.assertFalse(self._session_close_bodies(post, first))

    # r15 SEC-167 -------------------------------------------------------------------------------------
    def test_sec167_print_no_monotonic_after_retention(self):
        _r, first, anchor = self._open()
        self.assertEqual(first.print_no, 1)
        first._efn_close("closed_by_staff")
        result, second, _a = self._open(anchor=anchor)
        self.assertEqual((second.print_no, result["print_no"]), (2, 2))
        second._efn_close("closed_by_staff")
        first.sudo().unlink()  # retensi menghapus sesi tertua (hitungan sesi turun)
        _r, third, _a = self._open(anchor=anchor)
        self.assertEqual(third.print_no, 3, "MAX+1, bukan hitungan sesi (yang akan bertabrakan dengan #2)")
        self.assertEqual(self.bff_calls[-1][1]["idempotency_key"], f"fnb_open:{self.config.id}:{self.table.id}:3")

    def test_sec167_bff_pointing_to_existing_session_rejected(self):
        _r, first, anchor = self._open()
        first._efn_close("closed_by_staff")
        _r, second, _a = self._open(anchor=anchor)

        def stale(_url, **_kwargs):
            return response(201, {"table_session_id": first.bff_session_id, "qr_url": QR_URL})

        logger = "odoo.addons.efn_pos_bff_order.models.pos_config"
        with self.assertLogs(logger, level="ERROR"), patch(POST, side_effect=stale):
            result = self.config.efn_qr_session_open(self.table.id, anchor.uuid, True)
        self.assertEqual(result["error_code"], "bff_unavailable")
        self.assertEqual(second.state, "open", "sesi open tidak ditutup oleh respons BFF yang bertabrakan")
        self.assertEqual(first.state, "closed")
        self.assertEqual(anchor.efn_qr_table_session_id, second)

    def test_outbox_retry_final_and_order(self):
        _r, session, _a = self._open()
        session._efn_close("closed_by_staff")
        event = self._outbox(session)
        self.assertEqual(json.loads(json.dumps(event._efn_payload()))["close"], {"reason": "closed_by_staff"})
        with patch(POST, return_value=response(503)):
            self.env["efn.pos.qr.event.outbox"]._efn_process_due()
        self.assertEqual((event.state, event.attempt_count, event.last_error), ("pending", 1, "http_503"))
        event.next_attempt_at = False
        with (
            self.assertLogs("odoo.addons.efn_pos_bff_order.alert", level="ERROR") as logs,
            patch(POST, return_value=response(409, {"error": {"code": "company_mismatch"}})),
        ):
            self.env["efn.pos.qr.event.outbox"]._efn_process_due()
        self.assertEqual((event.state, event.last_error), ("failed", "http_409:company_mismatch"))
        self.assertIn("EFN_QR_EVENT_FAILED", logs.output[0])

    def test_hmac_missing_not_sent_unsigned(self):
        _r, session, _a = self._open()
        session._efn_close("closed_by_staff")
        with (
            self.assertLogs("odoo.addons.efn_pos_bff_order.models.efn_pos_qr_event_outbox", level="ERROR"),
            patch.dict("os.environ", {"EFN_FNB_EVENT_HMAC_FILE": "/nonexistent/efn"}),
            patch(POST) as post,
        ):
            self.env["efn.pos.qr.event.outbox"]._efn_process_due()
        post.assert_not_called()
        self.assertEqual(self._outbox(session).last_error, "hmac_missing")

    def test_close_by_staff_pos_session_and_expiry(self):
        _r, session, _a = self._open()
        self.config.efn_qr_session_close(self.table.id)
        self.assertEqual((session.state, session.close_reason), ("closed", "closed_by_staff"))
        _r, session2, _a = self._open(table=self.table2)
        session2.sudo().expires_at = fields.Datetime.now() - timedelta(minutes=1)
        self.env["efn.pos.table.session"]._cron_efn_qr_expire()
        self.assertEqual(session2.state, "expired")

    def test_retention_crons(self):
        _r, session, _a = self._open()
        result = self._submit(session, [{**self._line(self.croissant), "note": "tanpa gula"}])
        line = self.env["efn.pos.qr.order"].search([("submission_ref", "=", result["submission_ref"])]).order_id.lines
        total = line.order_id.amount_total
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE pos_order_line SET create_date = %s WHERE id = %s",
            [fields.Datetime.now() - timedelta(days=91), line.id],
        )
        self.assertEqual(self.env["efn.pos.qr.order"]._cron_efn_qr_note_retention(), 1)
        self.assertFalse(line.note)
        self.assertEqual(line.order_id.amount_total, total)
        session._efn_close("closed_by_staff")
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE efn_pos_table_session SET opened_at = %s WHERE id = %s",
            [fields.Datetime.now() - timedelta(days=401), session.id],
        )
        self.env.invalidate_all()
        self.env["efn.pos.table.session"]._cron_efn_qr_retention()
        self.assertFalse(session.exists())
        self.assertTrue(line.order_id.exists(), "order POS tetap")

    # 14 ----------------------------------------------------------------------------------------------
    def test_14_access_control(self):
        _r, session, _a = self._open()
        Qr = self.env["efn.pos.qr.order"]
        # User POS biasa tidak bisa memanggil RPC BFF.
        with self.assertRaises(AccessError):
            Qr.with_user(self.cashier).efn_qr_submit(
                config_id=self.config.id, table_session_ref=session.bff_session_id, submission=self._submission([])
            )
        # User teknis tidak bisa membaca dokumen lewat ORM umum.
        for model in ("pos.order", "pos.order.line", "efn.pos.table.session", "efn.pos.qr.order"):
            with self.subTest(model=model), self.assertRaises(AccessError):
                self.env[model].with_user(self.tech).search_read([], ["id"], limit=1)
        # User teknis tidak bisa membuka sesi meja (RPC POS).
        with self.assertRaises(AccessError):
            self.config.with_user(self.tech).efn_qr_session_open(self.table.id, "x" * 36, False)
        # User teknis dengan grup tambahan -> ditolak (fail closed).
        extra = new_test_user(
            self.env,
            login="efn_qr_tech_extra",
            groups="base.group_user,efn_pos_bff_order.group_qr_bff,point_of_sale.group_pos_user",
            company_id=self.company.id,
            company_ids=[Command.set(self.company.ids)],
        )
        with (
            self.assertLogs("odoo.addons.efn_pos_bff_order.models.efn_pos_qr_order", level="ERROR"),
            self.assertRaises(AccessError),
        ):
            Qr.with_user(extra).efn_qr_menu(config_id=self.config.id)
        # Context klien (allowed_company_ids, active_test) diabaikan.
        menu = (
            Qr.with_user(self.tech).with_context(active_test=False, lang="xx_XX").efn_qr_menu(config_id=self.config.id)
        )
        self.assertNotIn(self.not_pos.id, [i["product_id"] for i in menu["items"]])

    def test_status_probe_for_key_rotation(self):
        Qr = self.env["efn.pos.qr.order"].with_user(self.tech)
        self.assertEqual(Qr.efn_qr_status(config_id=0, submission_refs=[]), [])
        with self.assertRaises(UserError):
            Qr.efn_qr_status(config_id=0, submission_refs=["00000000-0000-4000-8000-000000000000"])
        with self.assertRaises(AccessError):
            self.env["efn.pos.qr.order"].with_user(self.cashier).efn_qr_status(config_id=0, submission_refs=[])

    def test_server_fields_not_writable_by_pos_user(self):
        _r, session, anchor = self._open()
        anchor.with_user(self.cashier).write({"efn_qr_order_ref": "palsu", "efn_qr_table_session_id": False})
        self.assertFalse(anchor.efn_qr_order_ref)
        self.assertEqual(anchor.efn_qr_table_session_id, session)

    def test_config_constraint(self):
        with self.assertRaises(UserError):
            self.config.write({"efn_qr_session_hours": 5})
        takeaway = self.env["pos.preset"].create({"name": "Bawa pulang (uji QR)", "service_at": "counter"})
        with self.assertRaises(UserError):
            self.config.write({"available_preset_ids": [Command.link(takeaway.id)], "efn_qr_preset_id": takeaway.id})
