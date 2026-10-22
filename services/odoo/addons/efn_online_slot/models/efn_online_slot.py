"""Slot ambil pre-order (docs/04 §15.4, L-87; W-36).

- ``efn.online.outlet``: ``slot_enabled``, ``slot_minutes``, ``slot_capacity``, ``slot_lead_minutes``,
  ``slot_horizon_days``, ``slot_calendar_id`` (default = ``resource_calendar_id`` outlet).
- RPC #20 ``efn.online.channel/efn_online_slots(channel_code, outlet_code, date)`` (``_efn_online_check_caller``).
- ``efn_online_quote(..., pickup_slot=None)`` / ``efn_online_place(order{..., pickup_slot})``: tanpa ``pickup_slot``
  perilaku & ``quote_hash`` IDENTIK dengan sebelumnya. Dengan slot: quote -> issue ``slot_unavailable`` bila tidak sah
  / penuh; ``quote_hash`` = sha256({q: hash dasar, pickup_slot}) lewat penanda cursor ``efn_core.scope`` (bukan context
  klien, yang dibuang ``_efn_online_check_caller``). Place -> kunci baris outlet + hitung kapasitas, simpan
  ``efn.online.order.pickup_slot`` + ``sale.order.commitment_date`` (standar ``sale``).
"""

import re
from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.efn_core.scope import internal_scope, scope_tokens, touch_row
from odoo.addons.efn_online_order.models.efn_online_channel import iso_utc
from odoo.addons.efn_online_order.models.efn_online_common import canonical_sha256, online_error, require

SLOT_SCOPE = "efn_online_pickup_slot"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DEFAULT_TZ = "Asia/Jakarta"
MAX_SLOTS = 48
OPEN_EXCLUDED = ("cancelled",)


def parse_slot(value):
    """ISO UTC (``Z``/``+00:00``) detik nol -> datetime naive UTC; selain itu ``invalid_input``."""
    require(isinstance(value, str) and 0 < len(value) <= 40)
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise online_error("invalid_input") from None
    require(parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0))
    require(parsed.second == 0 and parsed.microsecond == 0)
    return parsed.replace(tzinfo=None)


class EfnOnlineOutlet(models.Model):
    _inherit = "efn.online.outlet"

    slot_enabled = fields.Boolean(string="Pre-order berjadwal", default=False)
    slot_minutes = fields.Selection([("30", "30 menit"), ("60", "60 menit")], default="60", required=True)
    slot_capacity = fields.Integer(string="Pesanan per slot", default=10, required=True)
    slot_lead_minutes = fields.Integer(string="Minimal menit sebelum ambil", default=240, required=True)
    slot_horizon_days = fields.Integer(string="Maks hari ke depan", default=7, required=True)
    slot_calendar_id = fields.Many2one(
        "resource.calendar", string="Jam ambil pre-order", check_company=True, help="Kosong = 'Jam ambil' outlet."
    )

    @api.constrains("slot_enabled", "slot_capacity", "slot_lead_minutes", "slot_horizon_days", "slot_calendar_id")
    def _check_efn_slot(self):
        for outlet in self:
            if outlet.slot_capacity < 1 or outlet.slot_lead_minutes < 0:
                raise ValidationError(_("Kapasitas slot >= 1 dan menit minimal tidak boleh negatif."))
            if not 1 <= outlet.slot_horizon_days <= 30:
                raise ValidationError(_("Maks hari ke depan slot harus 1-30."))
            if outlet.slot_enabled and not (outlet.slot_calendar_id or outlet.resource_calendar_id):
                raise ValidationError(_("Pre-order berjadwal butuh kalender jam ambil."))

    def _efn_slot_calendar(self):
        self.ensure_one()
        return self.slot_calendar_id or self.resource_calendar_id

    def _efn_slot_tz(self):
        return pytz.timezone(self._efn_slot_calendar().tz or DEFAULT_TZ)

    def _efn_slot_starts(self, day):
        """Awal slot (naive UTC) di hari lokal ``day`` yang seluruh panjang slotnya di dalam jam ambil."""
        self.ensure_one()
        calendar = self._efn_slot_calendar()
        if not calendar:
            return []
        tz = self._efn_slot_tz()
        start_local = tz.localize(datetime.combine(day, time.min))
        end_local = tz.localize(datetime.combine(day + timedelta(days=1), time.min))
        step = timedelta(minutes=int(self.slot_minutes))
        out = []
        for begin, end, _rec in calendar._attendance_intervals_batch(start_local, end_local)[False]:
            offset = (begin - start_local) % step
            cursor = begin if not offset else begin + (step - offset)
            while cursor + step <= end and len(out) < MAX_SLOTS:
                out.append(cursor.astimezone(pytz.utc).replace(tzinfo=None))
                cursor += step
        return sorted(set(out))

    def _efn_slot_used(self, starts):
        self.ensure_one()
        if not starts:
            return {}
        self.env["efn.online.order"].flush_model(["outlet_id", "pickup_slot", "state"])
        self.env.cr.execute(
            """SELECT pickup_slot, count(*) FROM efn_online_order
                WHERE outlet_id = %s AND pickup_slot IN %s AND state NOT IN %s GROUP BY pickup_slot""",
            (self.id, tuple(starts), OPEN_EXCLUDED),
        )
        return dict(self.env.cr.fetchall())

    def _efn_slot_in_window(self, slot):
        self.ensure_one()
        now = fields.Datetime.now()
        if slot < now + timedelta(minutes=self.slot_lead_minutes):
            return False
        tz = self._efn_slot_tz()
        today = pytz.utc.localize(now).astimezone(tz).date()
        day = pytz.utc.localize(slot).astimezone(tz).date()
        return day <= today + timedelta(days=self.slot_horizon_days)

    def _efn_slot_valid(self, slot):
        """True bila slot aktif di outlet, sejajar batas slot, dalam jam & jendela waktu (tanpa cek kapasitas)."""
        self.ensure_one()
        if not self.slot_enabled or not self._efn_slot_in_window(slot):
            return False
        day = pytz.utc.localize(slot).astimezone(self._efn_slot_tz()).date()
        return slot in self._efn_slot_starts(day)


class EfnOnlineChannel(models.Model):
    _inherit = "efn.online.channel"

    @api.model
    def efn_online_slots(self, channel_code, outlet_code, date):
        """#20 slot ambil pre-order satu hari lokal outlet + sisa kapasitas (baca)."""
        channel = self._efn_online_check_caller(channel_code)
        outlet = channel._efn_online_outlet(outlet_code, "pickup")
        require(outlet.slot_enabled, "not_found")
        require(isinstance(date, str) and bool(DATE_RE.match(date)))
        try:
            day = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            raise online_error("invalid_input") from None
        tz = outlet._efn_slot_tz()
        today = pytz.utc.localize(fields.Datetime.now()).astimezone(tz).date()
        require(today <= day <= today + timedelta(days=outlet.slot_horizon_days))
        starts = [s for s in outlet._efn_slot_starts(day) if outlet._efn_slot_in_window(s)]
        used = outlet._efn_slot_used(starts)
        step = timedelta(minutes=int(outlet.slot_minutes))
        return {
            "outlet_code": outlet.code,
            "date": day.isoformat(),
            "timezone": tz.zone,
            "slot_minutes": int(outlet.slot_minutes),
            "slots": [
                {
                    "start_at": iso_utc(s),
                    "end_at": iso_utc(s + step),
                    "remaining": max(outlet.slot_capacity - used.get(s, 0), 0),
                }
                for s in starts
            ],
        }

    def _efn_online_channel_block(self):
        """§15.4: outlet + ``slot_enabled``/``slot_minutes``; kanal + ``fulfilment.scheduled``. Kunci lama tetap.

        Kanal tanpa outlet ber-slot -> blok IDENTIK dengan sebelum modul ini (BFF: kunci absen = tanpa slot)."""
        block = super()._efn_online_channel_block()
        outlets = {o.code: o for o in self.with_context(active_test=False).outlet_ids}
        if not any(o.slot_enabled for o in outlets.values()):
            return block
        for entry in block.get("outlets", []):
            outlet = outlets.get(entry.get("code"))
            entry["slot_enabled"] = bool(outlet and outlet.slot_enabled)
            entry["slot_minutes"] = int(outlet.slot_minutes) if outlet and outlet.slot_enabled else None
        block["fulfilment"] = {
            "pickup": bool(self.fulfilment_pickup),
            "delivery": False,
            "scheduled": any(o.slot_enabled and o.active for o in outlets.values()),
        }
        return block

    def _efn_online_summarize(self, order, outlet, fulfilment, promo_applied=None):
        summary = super()._efn_online_summarize(order, outlet, fulfilment, promo_applied=promo_applied)
        slots = sorted(value for kind, value in scope_tokens(self.env, SLOT_SCOPE) if kind == "slot")
        if slots:
            summary["quote_hash"] = canonical_sha256({"q": summary["quote_hash"], "pickup_slot": slots[0]})
        return summary

    @api.model
    def efn_online_quote(self, channel_code, outlet_code, fulfilment, lines, promo_codes=None, pickup_slot=None):
        if pickup_slot is None:
            return super().efn_online_quote(channel_code, outlet_code, fulfilment, lines, promo_codes=promo_codes)
        slot = parse_slot(pickup_slot)
        require(fulfilment == "pickup")
        with internal_scope(self.env, SLOT_SCOPE, {("slot", iso_utc(slot))}):
            result = super().efn_online_quote(channel_code, outlet_code, fulfilment, lines, promo_codes=promo_codes)
        channel = self._efn_online_check_caller(channel_code)
        outlet = channel._efn_online_outlet(outlet_code)
        ok = outlet._efn_slot_valid(slot) and outlet._efn_slot_used([slot]).get(slot, 0) < outlet.slot_capacity
        if not ok:
            result["issues"] = [*result["issues"], {"product_id": None, "code": "slot_unavailable"}]
        result["pickup_slot"] = iso_utc(slot)
        return result


class EfnOnlineOrder(models.Model):
    _inherit = "efn.online.order"

    pickup_slot = fields.Datetime(string="Jadwal ambil", readonly=True, index=True, copy=False)

    @api.model
    def efn_online_place(self, channel_code, order):
        if not isinstance(order, dict) or "pickup_slot" not in order:
            return super().efn_online_place(channel_code, order)
        order = dict(order)
        slot = parse_slot(order.pop("pickup_slot"))
        require(order.get("fulfilment") == "pickup")
        channel = self.env["efn.online.channel"]._efn_online_check_caller(channel_code)
        Orders = self.with_env(channel.env)
        key = order.get("idempotency_key")
        existing = Orders.search([("idempotency_key", "=", key)], limit=1) if isinstance(key, str) else Orders
        if existing:
            # Kunci sama + slot beda = isi beda.
            require(existing.pickup_slot == slot, "idempotency_conflict")
            with internal_scope(self.env, SLOT_SCOPE, {("slot", iso_utc(slot))}):
                return super().efn_online_place(channel_code, order)
        outlet = channel._efn_online_outlet(order.get("outlet_code"), "pickup")
        # Kapasitas dihitung di bawah kunci baris outlet (SEC-127: touch_row agar transaksi paralel diulang).
        self.env.cr.execute("SELECT id FROM efn_online_outlet WHERE id = %s FOR UPDATE", (outlet.id,))
        touch_row(self.env.cr, "efn_online_outlet", outlet.id)
        if not outlet._efn_slot_valid(slot) or outlet._efn_slot_used([slot]).get(slot, 0) >= outlet.slot_capacity:
            raise online_error("slot_unavailable")
        with internal_scope(self.env, SLOT_SCOPE, {("slot", iso_utc(slot))}):
            result = super().efn_online_place(channel_code, order)
        record = Orders.search([("idempotency_key", "=", key)], limit=1)
        record.write({"pickup_slot": slot})
        if record.sale_order_id:
            record.sale_order_id.write({"commitment_date": slot})
        result["pickup_slot"] = iso_utc(slot)
        return result

    def _efn_place_result(self):
        result = super()._efn_place_result()
        if self.pickup_slot:
            result["pickup_slot"] = iso_utc(self.pickup_slot)
        return result
