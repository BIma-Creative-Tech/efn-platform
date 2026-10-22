"""W-35 reservasi meja (docs/04 §15.2-§15.3, L-85/L-86). Tag efn. Semua angka = data uji."""

import json
import uuid
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytz

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import new_test_user, tagged

from odoo.addons.efn_online_order.tests.common import EfnOnlineCommon

POST = "odoo.addons.efn_table_booking.models.efn_booking_event_outbox.requests.post"
SECRET = "b" * 48  # HMAC uji (bukan rahasia)
TZ = pytz.timezone("Asia/Jakarta")


def utc_iso(day, hour, minute=0):
    local = TZ.localize(datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute))
    return local.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@tagged("post_install", "-at_install", "efn")
class TestEfnTableBooking(EfnOnlineCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        env["res.company"].search([("efn_brand_code", "=", "PB"), ("id", "!=", cls.company.id)]).write(
            {"efn_brand_code": False}
        )
        cls.company.efn_brand_code = "PB"  # -> efn_business_type fnb
        cls.calendar = env["resource.calendar"].create(
            {
                "name": "Jam buka uji 10-22",
                "tz": "Asia/Jakarta",
                "company_id": cls.company.id,
                "attendance_ids": [Command.clear()]
                + [
                    Command.create(
                        {"name": f"Hari {d}", "dayofweek": str(d), "hour_from": 10.0, "hour_to": 22.0, "day_period": "morning"}
                    )
                    for d in range(7)
                ],
            }
        )
        cls.pos_config = env["pos.config"].with_company(cls.company).create(
            {"name": "Resto uji reservasi", "module_pos_restaurant": True, "company_id": cls.company.id}
        )
        cls.floor = env["restaurant.floor"].create({"name": "Lantai", "pos_config_ids": [Command.set(cls.pos_config.ids)]})
        Table = env["restaurant.table"]
        cls.t2 = Table.create({"floor_id": cls.floor.id, "table_number": 1, "seats": 2})
        cls.t4 = Table.create({"floor_id": cls.floor.id, "table_number": 2, "seats": 4})
        cls.settings = env["efn.booking.settings"].create(
            {
                "outlet_id": cls.outlet.id,
                "pos_config_id": cls.pos_config.id,
                "calendar_id": cls.calendar.id,
                "slot_minutes": "30",
                "duration_minutes": 90,
                "lead_minutes": 60,
                "horizon_days": 14,
                "max_party": 6,
                "confirm_mode": "staff",
            }
        )
        cls.bk_user = new_test_user(
            env,
            login="efn_booking_tst",
            groups="base.group_user,efn_table_booking.group_booking_bff",
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )
        cls.manager = new_test_user(
            env,
            login="efn_booking_mgr",
            groups="base.group_user,point_of_sale.group_pos_manager",
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )
        cls.day = (datetime.now(TZ) + timedelta(days=3)).date()

    def setUp(self):
        super().setUp()
        patcher = patch.dict("os.environ", {"EFN_BOOKING_EVENT_HMAC_FILE": "/nonexistent-efn-test"})
        patcher.start()
        self.addCleanup(patcher.stop)

    # ------------------------------------------------------------------ helpers
    def api(self, user=None):
        return self.env["efn.table.booking"].with_user(user or self.bk_user)

    def create(self, hour=12, party=2, ref=None, key=None, **extra):
        ref = ref or str(uuid.uuid4())
        booking = {
            "idempotency_key": key or f"booking:create:{ref}",
            "platform_booking_ref": ref,
            "start_at": utc_iso(self.day, hour),
            "party_size": party,
            **extra,
        }
        return self.api().efn_booking_create("TOKO-T1", booking), booking

    def assert_code(self, code, func, *args, **kwargs):
        with self.assertRaises(UserError) as ctx, self.env.cr.savepoint():
            func(*args, **kwargs)
        self.assertEqual(str(ctx.exception.args[0]), f"efn_booking:{code}")

    # ------------------------------------------------------------ availability
    def test_availability_respects_hours_and_duration(self):
        res = self.api().efn_booking_availability("TOKO-T1", self.day.isoformat(), 2)
        self.assertEqual(res["timezone"], "Asia/Jakarta")
        starts = [s["start_at"] for s in res["slots"]]
        self.assertEqual(starts[0], utc_iso(self.day, 10))
        # 90 menit harus muat sebelum 22:00 -> slot terakhir 20:30.
        self.assertEqual(starts[-1], utc_iso(self.day, 20, 30))
        self.assertEqual(len(starts), 22)
        self.assertTrue(all(s["available"] for s in res["slots"]))
        self.assertEqual(res["max_party"], 6)

    def test_availability_party_limits_and_window(self):
        self.assert_code("party_size", self.api().efn_booking_availability, "TOKO-T1", self.day.isoformat(), 7)
        self.assert_code("party_size", self.api().efn_booking_availability, "TOKO-T1", self.day.isoformat(), 0)
        far = (self.day + timedelta(days=30)).isoformat()
        self.assert_code("too_far", self.api().efn_booking_availability, "TOKO-T1", far, 2)
        past = (self.day - timedelta(days=10)).isoformat()
        self.assert_code("too_late", self.api().efn_booking_availability, "TOKO-T1", past, 2)
        self.assert_code("invalid_input", self.api().efn_booking_availability, "TOKO-T1", "21-10-2026", 2)

    def test_availability_no_table_big_enough(self):
        res = self.api().efn_booking_availability("TOKO-T1", self.day.isoformat(), 5)
        self.assertFalse(any(s["available"] for s in res["slots"]))

    def test_availability_marks_overlap_busy(self):
        self.create(hour=12, party=4)  # meja 4 kursi terpakai 12:00-13:30
        res = self.api().efn_booking_availability("TOKO-T1", self.day.isoformat(), 3)
        by_start = {s["start_at"]: s["available"] for s in res["slots"]}
        self.assertFalse(by_start[utc_iso(self.day, 12)])
        self.assertFalse(by_start[utc_iso(self.day, 11)])  # 11:00-12:30 tumpang tindih
        self.assertTrue(by_start[utc_iso(self.day, 10, 30)])  # 10:30-12:00 tidak tumpang tindih
        self.assertTrue(by_start[utc_iso(self.day, 13, 30)])

    # ------------------------------------------------------------------ create
    def test_create_staff_mode_picks_smallest_table(self):
        res, _b = self.create(hour=12, party=2, guest_label="Rina", note="dekat jendela")
        self.assertEqual(res["state"], "requested")
        self.assertEqual(res["table_label"], "Lantai 1")
        self.assertEqual(res["end_at"], utc_iso(self.day, 13, 30))
        rec = self.env["efn.table.booking"].search([("platform_booking_ref", "=", res["platform_booking_ref"])])
        self.assertEqual(rec.table_id, self.t2)
        self.assertTrue(rec.name.startswith("BKG/"))
        self.assertFalse(self.env["efn.booking.event.outbox"].search([("booking_id", "=", rec.id)]))

    def test_create_auto_mode_confirms_and_emits(self):
        self.settings.confirm_mode = "auto"
        res, _b = self.create(hour=14, party=2)
        self.assertEqual(res["state"], "confirmed")
        rec = self.env["efn.table.booking"].search([("platform_booking_ref", "=", res["platform_booking_ref"])])
        outbox = self.env["efn.booking.event.outbox"].search([("booking_id", "=", rec.id)])
        self.assertEqual(outbox.event, "confirmed")
        payload = outbox._efn_payload()
        self.assertEqual(
            set(payload),
            {"schema_version", "idempotency_key", "seq", "platform_booking_ref", "company_code", "outlet_code", "event", "state", "occurred_at"},
        )
        self.assertEqual(payload["company_code"], "PB")

    def test_second_party_gets_next_table_then_full(self):
        self.create(hour=12, party=2)
        res2, _ = self.create(hour=12, party=2)
        self.assertEqual(res2["table_label"], "Lantai 2")
        self.assert_code("slot_unavailable", self.create, hour=12, party=2)

    def test_idempotent_replay_and_conflict(self):
        ref = str(uuid.uuid4())
        first, _ = self.create(hour=15, party=2, ref=ref)
        again, _ = self.create(hour=15, party=2, ref=ref)
        self.assertEqual(first, again)
        self.assertEqual(self.env["efn.table.booking"].search_count([("platform_booking_ref", "=", ref)]), 1)
        self.assert_code("idempotency_conflict", self.create, hour=16, party=2, ref=ref)

    def test_create_rejects_bad_times(self):
        self.assert_code("outside_hours", self.create, hour=21, party=2)  # 21:00 + 90 menit > 22:00
        ref = str(uuid.uuid4())
        bad = {
            "idempotency_key": f"booking:create:{ref}",
            "platform_booking_ref": ref,
            "start_at": utc_iso(self.day, 12, 15),  # bukan batas slot 30 menit
            "party_size": 2,
        }
        self.assert_code("outside_hours", self.api().efn_booking_create, "TOKO-T1", bad)
        soon = (datetime.now(pytz.utc) + timedelta(minutes=10)).replace(second=0, microsecond=0)
        bad2 = dict(bad, platform_booking_ref=str(uuid.uuid4()), idempotency_key="booking:create:x-soon-1", start_at=soon.strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.assert_code("too_late", self.api().efn_booking_create, "TOKO-T1", bad2)
        bad3 = dict(bad, platform_booking_ref=str(uuid.uuid4()), idempotency_key="booking:create:x-extra-1", phone="0812")
        self.assert_code("invalid_input", self.api().efn_booking_create, "TOKO-T1", bad3)

    def test_guest_text_pii_filter(self):
        self.assert_code("invalid_input", self.create, hour=12, guest_label="rina@mail.test")
        self.assert_code("invalid_input", self.create, hour=12, note="hubungi 0812 3456 7890")
        self.assert_code("invalid_input", self.create, hour=12, guest_label="x" * 41)
        res, _ = self.create(hour=12, guest_label="  Budi\u200b  ")
        rec = self.env["efn.table.booking"].search([("platform_booking_ref", "=", res["platform_booking_ref"])])
        self.assertEqual(rec.guest_label, "Budi")

    # ------------------------------------------------------------------ cancel
    def test_cancel_by_customer_idempotent_and_frees_table(self):
        res, _ = self.create(hour=12, party=4)
        ref = res["platform_booking_ref"]
        out = self.api().efn_booking_cancel("TOKO-T1", ref, f"booking:cancel:{ref}")
        self.assertEqual(out["state"], "cancelled")
        again = self.api().efn_booking_cancel("TOKO-T1", ref, f"booking:cancel:{ref}")
        self.assertEqual(again, out)
        avail = self.api().efn_booking_availability("TOKO-T1", self.day.isoformat(), 4)
        self.assertTrue({s["start_at"]: s["available"] for s in avail["slots"]}[utc_iso(self.day, 12)])

    def test_cancel_rejected_after_seated(self):
        res, _ = self.create(hour=12)
        rec = self.env["efn.table.booking"].search([("platform_booking_ref", "=", res["platform_booking_ref"])])
        rec.with_user(self.manager).action_efn_seat()
        ref = res["platform_booking_ref"]
        self.assert_code("invalid_state", self.api().efn_booking_cancel, "TOKO-T1", ref, f"booking:cancel:{ref}")
        self.assert_code("not_found", self.api().efn_booking_cancel, "TOKO-T1", str(uuid.uuid4()), "booking:cancel:none-1")

    # ------------------------------------------------------------------ status
    def test_status_only_own_outlet_refs(self):
        res, _ = self.create(hour=12)
        rows = self.api().efn_booking_status("TOKO-T1", [res["platform_booking_ref"], str(uuid.uuid4())])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["state"], "requested")
        self.assertEqual(self.api().efn_booking_status("TOKO-T1", []), [])
        self.assert_code("invalid_input", self.api().efn_booking_status, "TOKO-T1", ["bukan-uuid"])

    # ------------------------------------------------------------------ caller
    def test_caller_checks_fail_closed(self):
        plain = new_test_user(self.env, login="efn_booking_plain", groups="base.group_user", company_id=self.company.id)
        with self.assertRaises(AccessError):
            self.api(plain).efn_booking_availability("TOKO-T1", self.day.isoformat(), 2)
        extra = new_test_user(
            self.env,
            login="efn_booking_extra",
            groups="base.group_user,efn_table_booking.group_booking_bff,point_of_sale.group_pos_user",
            company_id=self.company.id,
            company_ids=[Command.set(self.company.ids)],
        )
        with self.assertRaises(AccessError):
            self.api(extra).efn_booking_availability("TOKO-T1", self.day.isoformat(), 2)
        other = new_test_user(
            self.env,
            login="efn_booking_other",
            groups="base.group_user,efn_table_booking.group_booking_bff",
            company_id=self.company_2.id,
            company_ids=[Command.set(self.company_2.ids)],
        )
        # PT lain punya outlet berkode sama (fixture TSB) tetapi tanpa pengaturan reservasi -> not_found; reservasi
        # outlet PT pertama tidak pernah tersentuh.
        self.assert_code("not_found", self.api(other).efn_booking_availability, "TOKO-T1", self.day.isoformat(), 2)
        with self.assertRaises(AccessError):
            self.api().efn_booking_availability("toko-t1", self.day.isoformat(), 2)

    def test_disabled_settings(self):
        self.settings.active = False
        self.assert_code("booking_disabled", self.api().efn_booking_availability, "TOKO-T1", self.day.isoformat(), 2)

    # ------------------------------------------------------------ staff/events
    def test_staff_transitions_and_permissions(self):
        res, _ = self.create(hour=12)
        rec = self.env["efn.table.booking"].search([("platform_booking_ref", "=", res["platform_booking_ref"])])
        cashier = new_test_user(
            self.env, login="efn_booking_cashier", groups="base.group_user,point_of_sale.group_pos_user", company_id=self.company.id
        )
        with self.assertRaises(AccessError):
            rec.with_user(cashier).action_efn_confirm()
        rec.with_user(self.manager).action_efn_confirm()
        self.assertEqual(rec.state, "confirmed")
        with self.assertRaises(ValidationError):
            rec.with_user(self.manager).action_efn_no_show()  # belum lewat jam mulai + tahan
        rec.with_user(self.manager).action_efn_seat()
        rec.with_user(self.manager).action_efn_done()
        events = self.env["efn.booking.event.outbox"].search([("booking_id", "=", rec.id)], order="seq")
        self.assertEqual(events.mapped("event"), ["confirmed", "seated", "done"])
        self.assertEqual(events.mapped("seq"), [1, 2, 3])

    def test_outbox_signed_post_without_pii(self):
        self.settings.confirm_mode = "auto"
        res, _ = self.create(hour=12, guest_label="Rina", note="kursi bayi")
        outbox = self.env["efn.booking.event.outbox"].search([("booking_id.platform_booking_ref", "=", res["platform_booking_ref"])])
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write(SECRET)
        mock = MagicMock(status_code=200)
        client = type(self.env["efn.bff.client"])
        with patch.dict("os.environ", {"EFN_BOOKING_EVENT_HMAC_FILE": handle.name}), patch(POST, return_value=mock) as post, patch.object(
            client, "_get_base_url", return_value="https://bff.test"
        ), patch.object(client, "_get_verify", return_value=True), patch.object(client, "_read_api_key", return_value="k" * 40):
            outbox._efn_send_one()
        self.assertEqual(outbox.state, "done")
        body = post.call_args.kwargs["data"].decode()
        self.assertNotIn("Rina", body)
        self.assertNotIn("kursi", body)
        self.assertEqual(json.loads(body)["event"], "confirmed")
        self.assertTrue(post.call_args.kwargs["headers"]["X-EFN-Signature"].startswith("t="))
        self.assertTrue(post.call_args.args[0].endswith("/v1/pos/booking/events"))

    def test_outbox_without_secret_retries(self):
        self.settings.confirm_mode = "auto"
        res, _ = self.create(hour=12)
        outbox = self.env["efn.booking.event.outbox"].search([("booking_id.platform_booking_ref", "=", res["platform_booking_ref"])])
        client = type(self.env["efn.bff.client"])
        with patch(POST) as post, patch.object(client, "_get_base_url", return_value="https://bff.test"), patch.object(
            client, "_get_verify", return_value=True
        ), patch.object(client, "_read_api_key", return_value="k" * 40):
            outbox._efn_send_one()
        post.assert_not_called()
        self.assertEqual(outbox.state, "pending")
        self.assertEqual(outbox.last_error, "hmac_missing")

    def test_cron_expires_unconfirmed(self):
        res, _ = self.create(hour=12)
        rec = self.env["efn.table.booking"].search([("platform_booking_ref", "=", res["platform_booking_ref"])])
        rec.sudo().write({"start_at": fields.Datetime.now() - timedelta(minutes=5), "end_at": fields.Datetime.now() + timedelta(minutes=85)})
        self.env["efn.table.booking"]._cron_efn_booking_expire()
        self.assertEqual(rec.state, "cancelled")
        self.assertEqual(rec.cancel_reason, "expired")

    def test_multi_company_record_rule(self):
        res, _ = self.create(hour=12)
        other = new_test_user(
            self.env,
            login="efn_booking_mgr2",
            groups="base.group_user,point_of_sale.group_pos_manager",
            company_id=self.company_2.id,
            company_ids=[Command.set(self.company_2.ids)],
        )
        visible = self.env["efn.table.booking"].with_user(other).search([("platform_booking_ref", "=", res["platform_booking_ref"])])
        self.assertFalse(visible)

    def test_settings_constraints(self):
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.settings.max_party = 0
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.settings.duration_minutes = 10

    def test_date_helper(self):
        self.assertIsInstance(self.day, date)
