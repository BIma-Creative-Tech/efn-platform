"""Review r11 (L-25): integritas pembayaran POS di luar sync (SEC-113), `is_change` dihitung ulang (SEC-116),
kunci order asal saat refund (SEC-117), dan modul terpasang otomatis (SEC-122).
Re-review G18: transisi ke paid hanya lewat `_process_saved_order` (SEC-125), penanda sync terbatas pada order
yang disinkron (SEC-126), kunci order asal = UPDATE no-op (SEC-127; uji dua koneksi nyata di
``efn_core/tests/test_scope.py``), dependency/instal di DB lama (SEC-129).

Percobaan lewat RPC nyata kasir (``/web/dataset/call_kw``) ada di ``test_payment_integrity_rpc.py`` (tag
``efn_tour``); di sini ORM dengan ``with_user(kasir)`` = hak yang sama dengan RPC (ACL + override, tanpa sudo).
"""

from contextlib import contextmanager
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged

from odoo.addons.efn_core.scope import POS_PAYMENT_SYNC_SCOPE, in_scope, internal_scope
from odoo.addons.point_of_sale.tests.common import TestPoSCommon


@tagged("post_install", "-at_install", "efn")
class TestEfnPosPaymentIntegrity(TestPoSCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.basic_config
        cls.product = cls.create_product("Produk Integritas Uji", cls.categ_basic, 50000.0)
        company = cls.config.company_id
        cls.cashier = new_test_user(
            cls.env, login="efn_r11_kasir", groups="base.group_user,point_of_sale.group_pos_user", company_id=company.id
        )
        cls.pos_manager = new_test_user(
            cls.env,
            login="efn_r11_manajer",
            groups="base.group_user,point_of_sale.group_pos_manager",
            company_id=company.id,
        )

    def setUp(self):
        super().setUp()
        self.open_new_session()
        self.env["ir.config_parameter"].sudo().set_param("efn.pos.refund.unlinked_return_mode", "cash")

    @contextmanager
    def assertGuard(self):
        """Ditolak guard efn (`UserError` bisnis), bukan kebetulan oleh ACL (`AccessError` turunan UserError)."""
        with self.assertRaises(UserError) as caught:
            yield
        self.assertNotIsInstance(caught.exception, AccessError, str(caught.exception))

    def _sync(self, data):
        self.env["pos.order"].sync_from_ui([data])
        return self.env["pos.order"].search([("uuid", "=", data["uuid"])])

    def _sale(self, payments, qty=1):
        return self._sync(self.create_ui_order_data([(self.product, qty)], payments=payments))

    def _refund_data(self, original, qty, payments):
        line = original.lines[:1]
        lines = [{"product": self.product, "quantity": -qty, "refunded_orderline_id": line.id}]
        return self.create_ui_order_data(lines, payments=payments, pos_order_ui_args={"is_refund": True})

    @staticmethod
    def _flag_change(data, index):
        """Tandai pembayaran ke-`index` payload sebagai kembalian (seperti dipalsukan lewat devtools)."""
        data["payment_ids"][index][2]["is_change"] = True
        return data

    # ------------------------------------------------------------- SEC-113
    def test_sec113_paid_order_payments_locked_for_cashier_and_manager(self):
        order = self._sale([(self.cash_pm1, 50000)])
        self.assertEqual(order.state, "paid")
        payment = order.payment_ids
        for user in (self.cashier, self.pos_manager):
            as_user = payment.with_user(user)
            with self.assertGuard():
                as_user.write({"payment_method_id": self.bank_pm1.id})
            with self.assertGuard():
                as_user.write({"amount": 1.0})
            with self.assertGuard():
                as_user.write({"is_change": True})
            with self.assertGuard():
                self.env["pos.payment"].with_user(user).create(
                    {"pos_order_id": order.id, "payment_method_id": self.cash_pm1.id, "amount": 1000.0}
                )
            with self.assertGuard():
                as_user.unlink()
        # Context RPC tidak bisa membuka jalur sync (penanda per cursor, bukan context).
        with self.assertGuard():
            payment.with_user(self.cashier).with_context(**{POS_PAYMENT_SYNC_SCOPE: True}).write({"amount": 1.0})
        self.assertFalse(in_scope(self.env, POS_PAYMENT_SYNC_SCOPE), "penanda tidak tertinggal setelah sync")
        payment.invalidate_recordset()
        self.assertEqual((payment.payment_method_id, payment.amount, len(order.payment_ids)), (self.cash_pm1, 50000, 1))

    def test_sec113_moving_payment_to_paid_order_rejected(self):
        paid = self._sale([(self.cash_pm1, 50000)])
        draft_data = self.create_ui_order_data([(self.product, 1)], payments=[(self.cash_pm1, 50000)])
        draft_data["state"] = "draft"
        draft = self._sync(draft_data)
        self.assertEqual(draft.state, "draft")
        with self.assertGuard():
            draft.payment_ids.with_user(self.cashier).write({"pos_order_id": paid.id})

    def test_sec113_draft_order_and_server_paths_unaffected(self):
        draft_data = self.create_ui_order_data([(self.product, 1)], payments=[])
        draft_data["state"] = "draft"
        draft = self._sync(draft_data)
        payment = (
            self.env["pos.payment"]
            .with_user(self.cashier)
            .create({"pos_order_id": draft.id, "payment_method_id": self.cash_pm1.id, "amount": 50000.0})
        )
        payment.write({"amount": 40000.0})
        payment.unlink()
        # Kode server ber-sudo (penutupan sesi, pembayaran online core) tidak terhalang.
        paid = self._sale([(self.cash_pm1, 50000)])
        paid.payment_ids.with_user(self.cashier).sudo().write({"amount": 50000.0})
        # Field non-finansial tetap bisa ditulis (mis. referensi transaksi terminal).
        paid.payment_ids.with_user(self.cashier).write({"transaction_id": "TRX-UJI"})

    # ------------------------------------------------------------- SEC-116
    def test_sec116_fake_change_on_refund_of_card_order_rejected(self):
        """Skenario review: order asal dibayar bank, refund dikembalikan tunai bertanda `is_change` palsu."""
        original = self._sale([(self.bank_pm1, 100000)], qty=2)
        data = self._flag_change(self._refund_data(original, 1, [(self.cash_pm1, -50000)]), 0)
        with self.assertRaises(UserError):
            self._sync(data)
        self.assertFalse(self.env["pos.order"].search([("uuid", "=", data["uuid"])]))

    def test_sec116_fake_amount_return_on_refund_rejected(self):
        """Varian: `amount_return` (dipakai core untuk membuat baris kembalian tunai) dipalsukan."""
        original = self._sale([(self.bank_pm1, 100000)], qty=2)
        data = self._refund_data(original, 1, [])
        data.update({"amount_return": -50000, "amount_paid": 0})
        with self.assertRaises(UserError):
            self._sync(data)

    def test_sec116_fake_change_on_sale_is_cleared(self):
        # Tender hanya bank 150.000 untuk total 50.000 + "kembalian" tunai -100.000 -> is_change dihapus server.
        data = self._flag_change(
            self.create_ui_order_data(
                [(self.product, 1)], payments=[(self.bank_pm1, 150000), (self.cash_pm1, -100000)]
            ),
            1,
        )
        order = self._sync(data)
        self.assertFalse(order.payment_ids.filtered(lambda p: p.amount < 0).is_change)

    def test_sec116_change_beyond_cash_tender_is_cleared(self):
        # Tunai 60.000 + bank 100.000 untuk total 50.000: kembalian sah maksimal 60.000 (tunai yang masuk).
        data = self._flag_change(
            self.create_ui_order_data(
                [(self.product, 1)],
                payments=[(self.cash_pm1, 60000), (self.bank_pm1, 100000), (self.cash_pm1, -110000)],
            ),
            2,
        )
        order = self._sync(data)
        self.assertFalse(order.payment_ids.filtered(lambda p: p.amount < 0).is_change)

    def test_sec116_legit_cash_change_kept(self):
        data = self.create_ui_order_data([(self.product, 1)], payments=[(self.cash_pm1, 70000)])
        data.update({"amount_return": -20000})
        order = self._sync(data)
        change = order.payment_ids.filtered("is_change")
        self.assertEqual((change.amount, change.payment_method_id), (-20000, self.cash_pm1))
        # Payload is_change sah (tunai, tertutup tender tunai) juga dipertahankan.
        data = self._flag_change(
            self.create_ui_order_data([(self.product, 1)], payments=[(self.cash_pm1, 100000), (self.cash_pm1, -50000)]),
            1,
        )
        order = self._sync(data)
        self.assertTrue(order.payment_ids.filtered(lambda p: p.amount < 0).is_change)
        # Refund sah ke metode asal tetap lolos setelah order asal punya kembalian.
        refund = self._sync(self._refund_data(order, 1, [(self.cash_pm1, -50000)]))
        self.assertEqual(refund.state, "paid")

    # ------------------------------------------------------------- SEC-117
    def test_sec117_refund_locks_origin_before_remaining(self):
        original = self._sale([(self.bank_pm1, 100000)], qty=2)
        Order = type(self.env["pos.order"])
        calls = []
        real_lock = Order._efn_lock_origin
        real_refunded = Order._efn_refunded_by_method

        def lock_spy(order, origin):
            calls.append(("lock", origin.id))
            return real_lock(order, origin)

        def refunded_spy(order, exclude=None):
            calls.append(("remaining", order.id))
            return real_refunded(order, exclude=exclude)

        with (
            patch.object(Order, "_efn_lock_origin", lock_spy),
            patch.object(Order, "_efn_refunded_by_method", refunded_spy),
        ):
            refund = self._sync(self._refund_data(original, 1, [(self.bank_pm1, -50000)]))
        self.assertEqual(refund.state, "paid")
        self.assertEqual(calls[:2], [("lock", original.id), ("remaining", original.id)])

    def test_sec127_lock_touches_origin_row(self):
        """SEC-127: kunci = UPDATE no-op baris order asal (`efn_core.scope.touch_row`), bukan SELECT FOR UPDATE."""
        original = self._sale([(self.cash_pm1, 50000)])
        target = "odoo.addons.efn_pos_refund_origin.models.pos_order.touch_row"
        with patch(target) as touch:
            self.env["pos.order"]._efn_lock_origin(original)
        touch.assert_called_once_with(self.env.cr, "pos_order", original.id)
        before = original.write_date
        self.env["pos.order"]._efn_lock_origin(original)  # SQL nyata: tidak mengubah nilai apa pun
        original.invalidate_recordset()
        self.assertEqual(original.write_date, before)

    # ------------------------------------------------------------- SEC-122/129
    def test_sec122_auto_install(self):
        module = self.env["ir.module.module"].search([("name", "=", "efn_pos_refund_origin")])
        self.assertTrue(module.auto_install)
        self.assertEqual(module.state, "installed")
        self.assertEqual(set(module.dependencies_id.mapped("name")), {"point_of_sale", "efn_core"})
        # SEC-129: modul POS efn yang memakai guard menjadikannya dependency (ikut terpasang saat `-u` DB lama).
        dependents = self.env["ir.module.module.dependency"].search([("name", "=", "efn_pos_refund_origin")])
        self.assertLessEqual({"efn_pos_loyalty_ledger", "efn_pos_otp_gate"}, set(dependents.module_id.mapped("name")))

    # ------------------------------------------------------------- SEC-125
    def _draft(self, payments):
        data = self.create_ui_order_data([(self.product, 1)], payments=payments)
        data["state"] = "draft"
        return self._sync(data)

    def _wizard(self, order, user, method, amount):
        return (
            self.env["pos.make.payment"]
            .with_user(user)
            .with_context(active_id=order.id)
            .create({"payment_method_id": method.id, "amount": amount})
        )

    def test_sec125_paid_only_through_validated_save(self):
        draft = self._draft([(self.cash_pm1, 50000)])
        for user in (self.cashier, self.pos_manager):
            with self.assertGuard():
                draft.with_user(user).action_pos_order_paid()
            for state in ("paid", "done", "invoiced"):
                with self.assertGuard():
                    draft.with_user(user).write({"state": state})
        self.assertEqual(draft.state, "draft")
        data = self.create_ui_order_data([(self.product, 1)])
        vals = {key: data[key] for key in ("session_id", "uuid", "amount_total", "amount_tax", "amount_paid", "lines")}
        with self.assertGuard():
            self.env["pos.order"].with_user(self.cashier).create(dict(vals, amount_return=0, state="paid"))
        # Jalur resmi: wizard Payment backend -> `_process_saved_order` (validasi lengkap) -> lunas.
        self._wizard(draft, self.pos_manager, self.cash_pm1, 0).check()
        self.assertEqual(draft.state, "paid")

    def test_sec125_backend_refund_payment_validated(self):
        """Skenario B review: refund dari backend dibayar lewat wizard dengan tunai padahal asal dibayar bank."""
        original = self._sale([(self.bank_pm1, 100000)], qty=2)
        refund = self.env["pos.order"].browse(original.refund()["res_id"])
        self.assertEqual(refund.state, "draft")
        with self.assertGuard():
            self._wizard(refund, self.pos_manager, self.cash_pm1, refund.amount_total).check()
        self.assertEqual(refund.state, "draft")
        self._wizard(refund, self.pos_manager, self.bank_pm1, refund.amount_total).check()
        self.assertEqual(refund.state, "paid")

    def test_sec125_forged_amount_paid_does_not_pay(self):
        draft = self._draft([])
        draft.with_user(self.cashier).write({"amount_paid": 50000})
        with self.assertGuard():
            self._wizard(draft, self.pos_manager, self.cash_pm1, 0).check()
        self.assertEqual(draft.state, "draft")

    # ------------------------------------------------------------- SEC-126
    def test_sec126_foreign_commands_in_sync_rejected(self):
        paid = self._sale([(self.cash_pm1, 50000)])
        foreign, foreign_line = paid.payment_ids, paid.lines
        commands = {
            "update": ("payment_ids", [1, foreign.id, {"payment_method_id": self.bank_pm1.id}]),
            "delete": ("payment_ids", [2, foreign.id]),
            "unlink": ("payment_ids", [3, foreign.id]),
            "link": ("payment_ids", [4, foreign.id]),
            "set": ("payment_ids", [6, 0, [foreign.id]]),
            "line_update": ("lines", [1, foreign_line.id, {"price_unit": 0.0}]),
        }
        for name, (fname, command) in commands.items():
            data = self.create_ui_order_data([(self.product, 1)], payments=[(self.cash_pm1, 50000)])
            data[fname].append(command)
            with self.subTest(command=name), self.assertGuard():
                self.env["pos.order"].with_user(self.cashier).sync_from_ui([data])
        # Gadget `relations_uuid_mapping` core: memindahkan pembayaran order lain ke order baru.
        data = self.create_ui_order_data([(self.product, 1)], payments=[(self.cash_pm1, 50000)])
        data["relations_uuid_mapping"] = {"pos.payment": {foreign.uuid: {"pos_order_id": data["uuid"]}}}
        with self.assertGuard():
            self.env["pos.order"].with_user(self.cashier).sync_from_ui([data])
        foreign.invalidate_recordset()
        foreign_line.invalidate_recordset()
        self.assertEqual((foreign.pos_order_id, foreign.payment_method_id), (paid, self.cash_pm1))
        self.assertEqual(foreign_line.price_unit, 50000)

    def test_sec126_own_commands_on_draft_resync_allowed(self):
        draft = self._draft([(self.cash_pm1, 40000)])
        own = draft.payment_ids
        data = self.create_ui_order_data([(self.product, 1)], payments=[], uuid=draft.uuid)
        data.update({"state": "draft", "payment_ids": [[1, own.id, {"amount": 50000}]]})
        self.env["pos.order"].with_user(self.cashier).sync_from_ui([data])
        self.assertEqual(own.amount, 50000)

    def test_sec126_guard_checks_ownership_inside_sync(self):
        """Lapis kedua: di dalam penanda sync order A, pembayaran order B tetap terkunci."""
        order_a = self._draft([(self.cash_pm1, 50000)])
        order_b = self._sale([(self.cash_pm1, 50000)])
        Payment = self.env["pos.payment"].with_user(self.cashier)
        with internal_scope(self.env, POS_PAYMENT_SYNC_SCOPE, {("id", order_a.id)}):
            order_a.payment_ids.with_user(self.cashier).write({"amount": 45000})
            for action in (
                lambda: order_b.payment_ids.with_user(self.cashier).write({"amount": 1.0}),
                lambda: order_b.payment_ids.with_user(self.cashier).unlink(),
                lambda: order_a.payment_ids.with_user(self.cashier).write({"pos_order_id": order_b.id}),
                lambda: Payment.create(
                    {"pos_order_id": order_b.id, "payment_method_id": self.cash_pm1.id, "amount": 1}
                ),
            ):
                with self.assertGuard():
                    action()
