"""W-36 slot ambil pre-order (docs/04 §15.4, L-87). Tag efn. Angka = data uji."""

import uuid
from datetime import datetime, timedelta

import pytz

from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.efn_online_order.tests.common import PICKUP_CODE, EfnOnlineCommon

TZ = pytz.timezone("Asia/Jakarta")


def utc_iso(day, hour):
    local = TZ.localize(datetime.combine(day, datetime.min.time()).replace(hour=hour))
    return local.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@tagged("post_install", "-at_install", "efn")
class TestEfnOnlineSlot(EfnOnlineCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.calendar = cls.env["resource.calendar"].create(
            {
                "name": "Jam ambil uji 9-17",
                "tz": "Asia/Jakarta",
                "company_id": cls.company.id,
                "attendance_ids": [Command.clear()]
                + [
                    Command.create(
                        {"name": f"H{d}", "dayofweek": str(d), "hour_from": 9.0, "hour_to": 17.0, "day_period": "morning"}
                    )
                    for d in range(7)
                ],
            }
        )
        cls.outlet.write(
            {
                "slot_enabled": True,
                "slot_minutes": "60",
                "slot_capacity": 2,
                "slot_lead_minutes": 240,
                "slot_horizon_days": 7,
                "slot_calendar_id": cls.calendar.id,
            }
        )
        cls.day = (datetime.now(TZ) + timedelta(days=2)).date()
        cls.lines = [{"product_id": cls.p_bag.id, "qty": 1}]

    def assert_code(self, code, func, *args, **kwargs):
        with self.assertRaises(UserError) as ctx, self.env.cr.savepoint():
            func(*args, **kwargs)
        self.assertEqual(str(ctx.exception.args[0]), f"efn_online:{code}")

    def quote_slot(self, slot, lines=None):
        return self.channel_api().efn_online_quote("TST", "TOKO-T1", "pickup", lines or self.lines, pickup_slot=slot)

    def place_slot(self, slot, part=None):
        quote = self.quote_slot(slot)
        part = part or str(uuid.uuid4())
        payload = {
            "idempotency_key": f"online:place:{part}",
            "platform_order_ref": part,
            "checkout_ref": str(uuid.uuid4()),
            "outlet_code": "TOKO-T1",
            "fulfilment": "pickup",
            "lines": self.lines,
            "quote_hash": quote["quote_hash"],
            "pickup_code": PICKUP_CODE,
            "pickup_slot": slot,
        }
        return part, payload, self.order_api().efn_online_place("TST", payload)

    # ------------------------------------------------------------------ slots
    def test_slots_list_hours_and_capacity(self):
        res = self.channel_api().efn_online_slots("TST", "TOKO-T1", self.day.isoformat())
        self.assertEqual(res["timezone"], "Asia/Jakarta")
        starts = [s["start_at"] for s in res["slots"]]
        self.assertEqual(starts[0], utc_iso(self.day, 9))
        self.assertEqual(starts[-1], utc_iso(self.day, 16))
        self.assertEqual(len(starts), 8)
        self.assertTrue(all(s["remaining"] == 2 for s in res["slots"]))

    def test_slots_rejected_outside_window_or_disabled(self):
        far = (self.day + timedelta(days=30)).isoformat()
        self.assert_code("invalid_input", self.channel_api().efn_online_slots, "TST", "TOKO-T1", far)
        self.outlet.slot_enabled = False
        self.assert_code("not_found", self.channel_api().efn_online_slots, "TST", "TOKO-T1", self.day.isoformat())

    # ------------------------------------------------------------------ quote
    def test_quote_without_slot_is_unchanged(self):
        base = self.channel_api().efn_online_quote("TST", "TOKO-T1", "pickup", self.lines)
        self.assertNotIn("pickup_slot", base)
        again = self.quote_slot(utc_iso(self.day, 10))
        self.assertNotEqual(base["quote_hash"], again["quote_hash"])
        self.assertEqual(again["pickup_slot"], utc_iso(self.day, 10))
        self.assertEqual(again["issues"], [])
        self.assertEqual(base["amount_payable"], again["amount_payable"])

    def test_quote_issue_for_invalid_slot(self):
        res = self.quote_slot(utc_iso(self.day, 18))  # di luar jam
        self.assertIn({"product_id": None, "code": "slot_unavailable"}, res["issues"])
        self.assert_code("invalid_input", self.quote_slot, "2026-10-21T10:30:15Z")

    # ------------------------------------------------------------------ place
    def test_place_sets_commitment_date_and_capacity(self):
        slot = utc_iso(self.day, 11)
        part, _payload, result = self.place_slot(slot)
        self.assertEqual(result["pickup_slot"], slot)
        record = self.record(part)
        self.assertEqual(record.pickup_slot.strftime("%Y-%m-%dT%H:%M:%SZ"), slot)
        self.assertEqual(record.sale_order_id.commitment_date, record.pickup_slot)
        self.place_slot(slot)
        # Kapasitas 2 habis -> quote memberi issue, place ditolak.
        self.assertIn({"product_id": None, "code": "slot_unavailable"}, self.quote_slot(slot)["issues"])
        self.assert_code("slot_unavailable", self.place_slot, slot)
        remaining = {
            s["start_at"]: s["remaining"]
            for s in self.channel_api().efn_online_slots("TST", "TOKO-T1", self.day.isoformat())["slots"]
        }
        self.assertEqual(remaining[slot], 0)

    def test_place_idempotent_and_slot_conflict(self):
        slot = utc_iso(self.day, 12)
        part, payload, first = self.place_slot(slot)
        again = self.order_api().efn_online_place("TST", payload)
        self.assertEqual(first, again)
        other = dict(payload, pickup_slot=utc_iso(self.day, 13))
        self.assert_code("idempotency_conflict", self.order_api().efn_online_place, "TST", other)

    def test_place_hash_mismatch_when_slot_swapped(self):
        quote = self.quote_slot(utc_iso(self.day, 14))
        part = str(uuid.uuid4())
        payload = {
            "idempotency_key": f"online:place:{part}",
            "platform_order_ref": part,
            "checkout_ref": str(uuid.uuid4()),
            "outlet_code": "TOKO-T1",
            "fulfilment": "pickup",
            "lines": self.lines,
            "quote_hash": quote["quote_hash"],
            "pickup_code": PICKUP_CODE,
            "pickup_slot": utc_iso(self.day, 15),
        }
        self.assert_code("price_changed", self.order_api().efn_online_place, "TST", payload)

    def test_place_without_slot_still_works(self):
        part, _payload, result = self.place(lines=self.lines)
        self.assertNotIn("pickup_slot", result)
        self.assertFalse(self.record(part).pickup_slot)

    def test_channel_block_exposes_slots(self):
        block = self.channel_api().efn_online_catalog("TST")["channel"]
        self.assertTrue(block["fulfilment"]["scheduled"])
        self.assertEqual(block["outlets"][0]["slot_enabled"], True)
        self.assertEqual(block["outlets"][0]["slot_minutes"], 60)
        self.outlet.slot_enabled = False
        block = self.channel_api().efn_online_catalog("TST")["channel"]
        self.assertNotIn("fulfilment", block)
        self.assertNotIn("slot_enabled", block["outlets"][0])

    def test_outlet_constraints(self):
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.outlet.slot_capacity = 0
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.outlet.slot_horizon_days = 60
