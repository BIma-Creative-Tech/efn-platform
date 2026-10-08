"""efn_pos_kds — Gelombang 15 (review r8): SEC-74, SEC-76, SEC-77. Tag ``efn``.

- SEC-74: allowlist grup akun perangkat (dua sisi: res.users & res.groups), sandbox model perangkat
  (ir.rule._compute_domain) diuji runtime terhadap SEMUA model terpasang yang dapat dibaca.
- SEC-76: kegagalan pembuatan tiket -> log alert JSON tanpa PII + tanda order; konkurensi naik.
- SEC-77: rotasi token otomatis, kanal kasir, daftar akun tablet tertutup bagi kasir.
"""

import json
from datetime import timedelta
from unittest.mock import patch

import psycopg2.errors

from odoo import Command, fields
from odoo.exceptions import AccessError, ConcurrencyError, ValidationError
from odoo.tests import tagged

from odoo.addons.efn_pos_kds.models.ir_rule import is_device_model

from .test_kds import CUSTOMER_NAME, CUSTOMER_PHONE, TestEfnKdsCommon

ALERT_LOGGER = "odoo.addons.efn_pos_kds.alert"


@tagged("post_install", "-at_install", "efn")
class TestEfnKdsDeviceGroups(TestEfnKdsCommon):
    """SEC-74 butir 2-3: allowlist grup, ditegakkan dari res.users DAN res.groups."""

    def _other_group(self):
        # Grup internal non-admin yang pasti ada (stok, dari point_of_sale -> stock).
        return self.env.ref("stock.group_stock_user")

    def test_device_with_only_allowed_groups_ok(self):
        self.device_kitchen._efn_kds_check_device_allowlist()  # tidak raise

    def test_device_rejects_any_extra_group_via_user(self):
        for xmlid in ("stock.group_stock_user", "base.group_partner_manager", "point_of_sale.group_pos_manager"):
            group = self.env.ref(xmlid)
            with self.subTest(group=xmlid), self.assertRaises(ValidationError), self.env.cr.savepoint():
                self.device_kitchen.write({"group_ids": [Command.link(group.id)]})

    def test_device_rejects_extra_group_via_group_form(self):
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._other_group().write({"user_ids": [Command.link(self.device_kitchen.id)]})
        self.env.invalidate_all()
        self.assertNotIn(self.device_kitchen, self._other_group().all_user_ids)

    def test_device_rejects_implied_group(self):
        device_group = self.env.ref("efn_pos_kds.group_kds_device")
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            device_group.write({"implied_ids": [Command.link(self._other_group().id)]})
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            # Grup lain dibuat meng-imply grup perangkat lalu diberikan ke user perangkat.
            self.env["res.groups"].create(
                {
                    "name": "EFN test grup pembungkus",
                    "implied_ids": [Command.link(device_group.id), Command.link(self._other_group().id)],
                    "user_ids": [Command.link(self.device_bar.id)],
                }
            )

    def test_group_implied_by_internal_user_still_allowed(self):
        """Fitur teknis yang di-imply base.group_user (pengaturan) berlaku untuk semua -> boleh."""
        allowed = self.env["res.users"]._efn_kds_allowed_groups()
        self.assertTrue(self.env.ref("base.group_user").all_implied_ids <= allowed)


@tagged("post_install", "-at_install", "efn")
class TestEfnKdsDeviceSandbox(TestEfnKdsCommon):
    """SEC-74 butir 1: perangkat tidak membaca produk/HPP, karyawan, kontak lewat RPC/relasi."""

    def _device_env(self):
        return self.env(user=self.device_kitchen)

    def test_device_cannot_read_products_or_cost(self):
        env = self._device_env()
        self.assertFalse(env["product.product"].search([]))
        self.assertFalse(env["product.template"].search([]))
        with self.assertRaises(AccessError):
            self.p_rice.with_user(self.device_kitchen).read(["standard_price"])
        # Layar KDS tetap jalan (data dari tiket, sudo setelah cek akses).
        self._sync([self._line(self.p_rice, 1)])
        result = self.station_kitchen.with_user(self.device_kitchen).efn_kds_load()
        self.assertEqual(result["tickets"][0]["lines"][0]["product_name"], self.p_rice.display_name)

    def test_device_cannot_read_employees_or_discuss(self):
        env = self._device_env()
        for model in ("hr.employee.public", "hr.employee", "resource.resource", "discuss.channel", "mail.message"):
            if model not in env:
                continue
            with self.subTest(model=model):
                try:
                    found = env[model].search([], limit=5)
                except AccessError:
                    continue
                self.assertFalse(found, f"perangkat membaca {model}")

    def test_device_users_and_partners_limited_to_self(self):
        env = self._device_env()
        self.assertEqual(env["res.users"].search([]), self.device_kitchen)
        partners = env["res.partner"].search([])
        self.assertIn(self.device_kitchen.partner_id, partners)
        self.assertNotIn(self.customer, partners)
        self.assertNotIn(self.cashier.partner_id, partners)

    def test_device_runtime_surface_no_customer_name(self):
        """Uji runtime: SEMUA model terpasang yang dapat dibaca perangkat (ACL) -> model di luar
        allowlist 0 record; model allowlist tidak menampilkan nama pelanggan lewat many2one."""
        order = self._sync([self._line(self.p_rice, 1)], extra={"_customer": self.customer})
        self.assertEqual(order.partner_id, self.customer)
        env = self._device_env()
        access = env["ir.model.access"]
        leaks, readable_outside = [], []
        for model_name in sorted(env.registry):
            model = env[model_name]
            if model._abstract or model._transient:
                continue
            if not access.check(model_name, "read", raise_exception=False):
                continue
            partner_fields = [
                name
                for name, field in model._fields.items()
                if field.type == "many2one" and field.comodel_name == "res.partner" and field.store
            ]
            try:
                with self.env.cr.savepoint():
                    if not is_device_model(model_name):
                        if model.search_count([], limit=1):
                            readable_outside.append(model_name)
                        continue
                    if not partner_fields:
                        continue
                    for row in model.search_read([], partner_fields, limit=200):
                        for name in partner_fields:
                            value = row.get(name)
                            if value and CUSTOMER_NAME in str(value):
                                leaks.append(f"{model_name}.{name}")
            except AccessError:
                continue
        self.assertFalse(readable_outside, "model di luar allowlist perangkat terbaca")
        self.assertFalse(leaks, "nama pelanggan terbaca perangkat lewat relasi")

    def test_cashier_not_sandboxed(self):
        self.assertTrue(self.env["product.product"].with_user(self.cashier).search([("id", "=", self.p_rice.id)]))


@tagged("post_install", "-at_install", "efn")
class TestEfnKdsTicketFailure(TestEfnKdsCommon):
    """SEC-76: kegagalan tiket tidak ditelan diam-diam."""

    def _patch_create(self, exc):
        ticket_cls = type(self.env["efn.kds.ticket"])
        return patch.object(ticket_cls, "_efn_kds_create_for_order", side_effect=exc)

    def test_failure_logs_alert_without_pii_and_flags_order(self):
        order_uuid = self.create_random_uid()
        lines = [self._line(self.p_rice, 1, note=f"untuk {CUSTOMER_NAME} {CUSTOMER_PHONE}")]
        with self._patch_create(ValueError(f"gagal {CUSTOMER_NAME}")), self.assertLogs(ALERT_LOGGER, "ERROR") as logs:
            order = self._sync(lines, order_uuid=order_uuid, extra={"_customer": self.customer})
        self.assertEqual(order.state, "draft", "sinkron order tetap berhasil")
        self.assertTrue(order.efn_kds_ticket_failed)
        self.assertFalse(self._tickets(order))
        self.assertEqual(len(logs.records), 1)
        message = logs.records[0].getMessage()
        self.assertTrue(message.startswith("EFN_KDS_TICKET_FAILED {"))
        event = json.loads(message.split(" ", 1)[1])
        self.assertEqual(event["order_id"], order.id)
        self.assertEqual(event["error"], "ValueError")
        self.assertEqual({s["id"] for s in event["stations"]}, {self.station_kitchen.id, self.station_bar.id})
        for secret in (CUSTOMER_NAME, CUSTOMER_PHONE, "untuk", "gagal"):
            self.assertNotIn(secret, message)
        # Sinkron berikutnya (perubahan baru) berhasil -> tanda dikosongkan.
        lines.append(self._line(self.p_coffee, 1))
        order = self._sync(lines, order_uuid=order_uuid)
        self.assertFalse(order.efn_kds_ticket_failed)
        self.assertTrue(self._tickets(order))

    def test_client_cannot_set_failed_flag(self):
        order = self._sync([self._line(self.p_rice, 1)], extra={"efn_kds_ticket_failed": True})
        self.assertFalse(order.efn_kds_ticket_failed)
        self.assertTrue(self._tickets(order))

    def test_serialization_failure_propagates_for_retry(self):
        with (
            self._patch_create(psycopg2.errors.SerializationFailure("konflik")),
            self.assertRaises(psycopg2.errors.SerializationFailure),
        ):
            self._sync([self._line(self.p_rice, 1)])

    def test_unique_violation_becomes_concurrency_error(self):
        with self._patch_create(psycopg2.errors.UniqueViolation("duplikat")), self.assertRaises(ConcurrencyError):
            self._sync([self._line(self.p_rice, 1)])


@tagged("post_install", "-at_install", "efn")
class TestEfnKdsTokenLifecycle(TestEfnKdsCommon):
    """SEC-77: rotasi otomatis + kanal kasir + daftar akun tablet tertutup bagi kasir."""

    def _token(self, station):
        return station.sudo()._ensure_access_token()

    def test_rotate_on_device_list_change(self):
        token = self._token(self.station_kitchen)
        self.station_kitchen.device_user_ids = [Command.unlink(self.device_kitchen.id)]
        self.assertNotEqual(self.station_kitchen.sudo().access_token, token)
        self.assertTrue(self.station_kitchen.sudo().token_rotated_at)

    def test_rotate_on_device_user_deactivated_or_password(self):
        token = self._token(self.station_kitchen)
        bar_token = self._token(self.station_bar)
        self.device_kitchen.active = False
        self.assertNotEqual(self.station_kitchen.sudo().access_token, token)
        self.assertEqual(self.station_bar.sudo().access_token, bar_token, "stasiun lain tidak terdampak")
        self.device_bar.password = "efn-kds-password-baru-123"
        self.assertNotEqual(self.station_bar.sudo().access_token, bar_token)

    def test_rotate_on_device_group_revoked_from_group_form(self):
        token = self._token(self.station_kitchen)
        self.env.ref("efn_pos_kds.group_kds_device").write({"user_ids": [Command.unlink(self.device_kitchen.id)]})
        self.assertNotEqual(self.station_kitchen.sudo().access_token, token)

    def test_scheduled_rotation(self):
        old = fields.Datetime.now() - timedelta(days=8)
        self.station_kitchen.sudo().write({"access_token": "token-lama", "token_rotated_at": old})
        self.station_bar.sudo().write({"access_token": "token-baru", "token_rotated_at": fields.Datetime.now()})
        self.env["efn.kds.station"]._cron_efn_kds_rotate_tokens()
        self.assertNotEqual(self.station_kitchen.sudo().access_token, "token-lama")
        self.assertEqual(self.station_bar.sudo().access_token, "token-baru")
        self.assertTrue(self.env.ref("efn_pos_kds.ir_cron_efn_kds_rotate_tokens").active)

    def test_cashier_cannot_read_device_accounts(self):
        station = self.station_kitchen.with_user(self.cashier)
        station.read(["name", "mode"])  # tetap boleh
        for fname in ("device_user_ids", "last_seen", "token_rotated_at", "access_token"):
            with self.subTest(field=fname), self.assertRaises(AccessError):
                station.read([fname])

    def test_ready_notification_on_cashier_channel_only(self):
        session = self.config.current_session_id
        session.with_user(self.cashier).load_data(["pos.session"])
        self.assertIn(self.cashier, session.sudo().efn_kds_listener_user_ids)
        order = self._sync([self._line(self.p_rice, 1)])
        kitchen = self._tickets(order, self.station_kitchen)
        self.sent.clear()
        kitchen.with_user(self.device_kitchen).efn_kds_bump("ready")
        targets = {t for t, kind, _m in self.sent if kind == "EFN_KDS_READY"}
        self.assertEqual(targets, {session.user_id.partner_id, self.cashier.partner_id})
        token = self.config.sudo().access_token
        self.assertFalse(
            [k for _t, k, _m in self.sent if "READY" in k and token and k.startswith(token)],
            "EFN_KDS_READY tidak boleh di kanal token pos.config",
        )
        self.assertNotIn(self.device_kitchen.partner_id, targets)
