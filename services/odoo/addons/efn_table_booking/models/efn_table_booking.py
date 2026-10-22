"""Reservasi meja (docs/04 §15.2-§15.3, L-85, L-86; ADR-013 dibuka G23, ADR-029, ADR-031).

RPC untuk BFF (``/json/2/efn.table.booking/<metode>``, path gateway 16-19, user teknis per PT F&B
``efn_booking_pb``/``efn_booking_bcf`` dengan grup ``group_booking_bff``; terdaftar di ``RPC_ALLOWLIST``):

- ``efn_booking_availability(outlet_code, date, party_size)`` (#16, baca)
- ``efn_booking_create(outlet_code, booking)`` (#17, idempoten ``booking:create:<platform_booking_ref>``)
- ``efn_booking_cancel(outlet_code, platform_booking_ref, idempotency_key)`` (#18)
- ``efn_booking_status(outlet_code, platform_booking_refs)`` (#19, baca)

Setiap RPC diawali ``_efn_booking_check_caller(outlet_code)`` (pola E-27 ``_efn_qr_check_caller``): grup teknis, user
internal aktif dengan TEPAT satu company = company outlet, tanpa grup lain (fail closed), context klien dibuang kecuali
``lang`` whitelist, lalu ``sudo`` + ``with_company``. Galat akses = ``AccessError`` seragam; galat bisnis =
``UserError("efn_booking:<kode>")``. Data tamu minimal (L-86): ``guest_label`` (panggilan) + ``note``, difilter aturan
SEC-168 (tanpa '@', < 8 digit); TIDAK ada HP/email/partner. Teks tamu tidak pernah di-log.
"""

import logging
import re
import uuid
from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import frozendict

from odoo.addons.efn_pos_bff_order.models.qr_common import (
    ALLOWED_LANGS,
    IDEMPOTENCY_KEY_RE,
    NOTE_RAW_MAX,
    UUID_RE,
    canonical_sha256,
    iso_utc,
    normalize_note,
    note_has_pii,
)

_logger = logging.getLogger(__name__)

GROUP_XMLID = "efn_table_booking.group_booking_bff"
ERROR_CODES = frozenset(
    {
        "invalid_input",
        "not_found",
        "slot_unavailable",
        "outside_hours",
        "party_size",
        "too_late",
        "too_far",
        "idempotency_conflict",
        "invalid_state",
        "booking_disabled",
    }
)
STATES = [
    ("requested", "Menunggu konfirmasi"),
    ("confirmed", "Terkonfirmasi"),
    ("seated", "Datang"),
    ("done", "Selesai"),
    ("cancelled", "Batal"),
    ("no_show", "Tidak datang"),
]
ACTIVE_STATES = ("requested", "confirmed", "seated")
CANCEL_REASONS = [("customer", "Pelanggan"), ("staff", "Staf"), ("no_show", "Tidak datang"), ("expired", "Kedaluwarsa")]
OUTLET_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{1,23}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
GUEST_LABEL_MAX = 40
NOTE_MAX = 140
MAX_STATUS_REFS = 100
MAX_SLOTS = 96
DEFAULT_TZ = "Asia/Jakarta"


def booking_error(code):
    if code not in ERROR_CODES:
        code = "invalid_input"
    return UserError(f"efn_booking:{code}")


def require(condition, code="invalid_input"):
    if not condition:
        raise booking_error(code)


def clean_text(value, max_len):
    """Filter teks tamu = aturan catatan SEC-168 (identik BFF ``sanitizeNote``) dengan batas panjang sendiri."""
    if value in (None, False, ""):
        return ""
    require(isinstance(value, str) and len(value) <= NOTE_RAW_MAX)
    text = normalize_note(value)
    require(len(text) <= max_len)
    require(not note_has_pii(text))
    return text


def parse_start(value):
    """ISO-8601 UTC (``Z``/``+00:00``) -> datetime naive UTC detik bulat; selain itu ``invalid_input``."""
    require(isinstance(value, str) and 0 < len(value) <= 40)
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise booking_error("invalid_input") from None
    require(parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0))
    require(parsed.microsecond == 0 and parsed.second == 0)
    return parsed.replace(tzinfo=None)


def parse_date(value):
    require(isinstance(value, str) and bool(DATE_RE.match(value)))
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise booking_error("invalid_input") from None


class EfnTableBooking(models.Model):
    _name = "efn.table.booking"
    _description = "EFN - Reservasi meja"
    _order = "start_at desc, id desc"
    _check_company_auto = True

    name = fields.Char(required=True, readonly=True, copy=False, default="/", index=True)
    company_id = fields.Many2one("res.company", required=True, index=True, readonly=True)
    settings_id = fields.Many2one("efn.booking.settings", required=True, readonly=True, ondelete="restrict")
    outlet_id = fields.Many2one(related="settings_id.outlet_id", store=True, index=True)
    platform_booking_ref = fields.Char(required=True, readonly=True, copy=False, index=True)
    idempotency_key = fields.Char(required=True, readonly=True, copy=False, groups="base.group_system")
    request_sha256 = fields.Char(readonly=True, copy=False, groups="base.group_system")
    start_at = fields.Datetime(required=True, readonly=True, index=True)
    end_at = fields.Datetime(required=True, readonly=True)
    party_size = fields.Integer(required=True, readonly=True)
    table_id = fields.Many2one("restaurant.table", readonly=True, index=True)
    table_label = fields.Char(compute="_compute_table_label", store=True)
    state = fields.Selection(STATES, required=True, default="requested", readonly=True, index=True)
    cancel_reason = fields.Selection(CANCEL_REASONS, readonly=True)
    guest_label = fields.Char(readonly=True, help="Nama panggilan dari pelanggan (bukan nama lengkap / HP).")
    note = fields.Char(readonly=True)
    seq = fields.Integer(readonly=True, default=0, copy=False)
    confirmed_at = fields.Datetime(readonly=True)
    seated_at = fields.Datetime(readonly=True)
    cancelled_at = fields.Datetime(readonly=True)

    _ref_unique = models.Constraint(
        "unique (company_id, platform_booking_ref)", "Referensi reservasi platform harus unik per PT."
    )
    _key_unique = models.Constraint("unique (idempotency_key)", "Kunci idempotensi reservasi harus unik.")
    _party_positive = models.Constraint("CHECK (party_size > 0)", "Jumlah tamu harus > 0.")
    _end_after_start = models.Constraint("CHECK (end_at > start_at)", "Akhir reservasi harus setelah awal.")

    @api.depends("table_id", "table_id.floor_id.name", "table_id.table_number")
    def _compute_table_label(self):
        for rec in self:
            table = rec.table_id
            rec.table_label = f"{table.floor_id.name or ''} {table.table_number}".strip() if table else False

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "/") == "/":
                vals["name"] = self.env["ir.sequence"].sudo().next_by_code("efn.table.booking") or "/"
        return super().create(vals_list)

    # ================================================================== caller
    @api.model
    def _efn_booking_allowed_groups(self):
        base = self.env.ref("base.group_user") | self.env.ref(GROUP_XMLID)
        return base | base.all_implied_ids

    @api.model
    def _efn_booking_check_caller(self, outlet_code):
        """Cek user teknis BFF; kembalikan settings outlet (sudo, company outlet, context bersih)."""
        lang = self.env.context.get("lang")
        bare = self.env(context=frozendict())
        denied = AccessError(bare._("Akses reservasi ditolak."))
        clean = {}
        if lang in ALLOWED_LANGS and bare["res.lang"].sudo().search_count([("code", "=", lang)], limit=1):
            clean["lang"] = lang
        user = bare.user
        if not user.has_group(GROUP_XMLID) or user.share or not user.active:
            raise denied
        user_sudo = user.sudo()
        companies = user_sudo.company_ids
        if len(companies) != 1:
            raise denied
        if user_sudo.all_group_ids - bare[self._name].sudo()._efn_booking_allowed_groups():
            _logger.error("EFN booking: user teknis #%s punya grup tambahan; RPC ditolak", user.id)
            raise denied
        if not isinstance(outlet_code, str) or not OUTLET_CODE_RE.match(outlet_code):
            raise denied
        outlets = (
            bare["efn.online.outlet"]
            .sudo()
            .with_context(active_test=False)
            .search([("code", "=", outlet_code), ("company_id", "=", companies.id)])
        )
        if not outlets:
            raise denied
        env = self.env(context=frozendict(clean), su=True)
        settings = (
            bare["efn.booking.settings"]
            .sudo()
            .with_context(active_test=False)
            .search([("outlet_id", "in", outlets.ids)], limit=1)
        )
        if not settings:
            raise booking_error("not_found")
        settings = settings.with_env(env).with_company(settings.company_id)
        if not settings.active or not settings.outlet_id.active:
            raise booking_error("booking_disabled")
        return settings

    # ============================================================ availability
    @api.model
    def _efn_tz(self, settings):
        return pytz.timezone(settings.calendar_id.tz or DEFAULT_TZ)

    @api.model
    def _efn_now(self):
        return fields.Datetime.now().replace(microsecond=0)

    @api.model
    def _efn_day_slots(self, settings, day):
        """Slot mulai (naive UTC) di hari lokal ``day`` yang seluruh durasinya di dalam jam buka."""
        tz = self._efn_tz(settings)
        start_local = tz.localize(datetime.combine(day, time.min))
        end_local = tz.localize(datetime.combine(day + timedelta(days=1), time.min))
        intervals = settings.calendar_id._attendance_intervals_batch(start_local, end_local)[False]
        step = timedelta(minutes=int(settings.slot_minutes))
        duration = timedelta(minutes=settings.duration_minutes)
        slots = []
        for begin, end, _rec in intervals:
            # Slot sejajar kelipatan langkah sejak tengah malam lokal.
            offset = (begin - start_local) % step
            cursor = begin if not offset else begin + (step - offset)
            while cursor + duration <= end and len(slots) < MAX_SLOTS:
                slots.append(cursor.astimezone(pytz.utc).replace(tzinfo=None))
                cursor += step
        return sorted(set(slots))

    @api.model
    def _efn_busy_by_table(self, settings, tables, window_start, window_end, exclude=None):
        domain = [
            ("settings_id", "=", settings.id),
            ("table_id", "in", tables.ids),
            ("state", "in", ACTIVE_STATES),
            ("start_at", "<", window_end),
            ("end_at", ">", window_start),
        ]
        if exclude:
            domain.append(("id", "not in", exclude.ids))
        busy = {}
        for rec in self.sudo().search(domain):
            busy.setdefault(rec.table_id.id, []).append((rec.start_at, rec.end_at))
        return busy

    @api.model
    def _efn_free_table(self, settings, tables, busy, start, end, party):
        for table in tables:
            if table.seats < party:
                continue
            if any(b_start < end and b_end > start for b_start, b_end in busy.get(table.id, ())):
                continue
            return table
        return self.env["restaurant.table"]

    @api.model
    def _efn_check_window(self, settings, day, start=None):
        tz = self._efn_tz(settings)
        now = self._efn_now()
        today_local = pytz.utc.localize(now).astimezone(tz).date()
        require(day >= today_local, "too_late")
        require(day <= today_local + timedelta(days=settings.horizon_days), "too_far")
        if start is not None:
            require(start >= now + timedelta(minutes=settings.lead_minutes), "too_late")

    @api.model
    def efn_booking_availability(self, outlet_code, date, party_size):
        """#16 slot jam reservasi untuk satu hari lokal outlet (baca)."""
        settings = self._efn_booking_check_caller(outlet_code)
        self = self.with_env(settings.env)  # noqa: PLW0642 - sudo + context bersih setelah cek pemanggil
        day = parse_date(date)
        require(isinstance(party_size, int) and not isinstance(party_size, bool), "invalid_input")
        require(settings.min_party <= party_size <= settings.max_party, "party_size")
        self._efn_check_window(settings, day)
        slots = self._efn_day_slots(settings, day)
        tables = settings._efn_tables()
        duration = timedelta(minutes=settings.duration_minutes)
        now = self._efn_now()
        earliest = now + timedelta(minutes=settings.lead_minutes)
        busy = (
            self._efn_busy_by_table(settings, tables, slots[0], slots[-1] + duration) if slots and tables else {}
        )
        out = []
        for start in slots:
            ok = start >= earliest and bool(self._efn_free_table(settings, tables, busy, start, start + duration, party_size))
            out.append({"start_at": iso_utc(start), "available": ok})
        return {
            "outlet_code": settings.outlet_id.code,
            "date": day.isoformat(),
            "timezone": self._efn_tz(settings).zone,
            "slot_minutes": int(settings.slot_minutes),
            "duration_minutes": settings.duration_minutes,
            "min_party": settings.min_party,
            "max_party": settings.max_party,
            "horizon_days": settings.horizon_days,
            "slots": out,
        }

    # ================================================================== create
    def _efn_result(self):
        self.ensure_one()
        return {
            "booking_ref": self.name,
            "platform_booking_ref": self.platform_booking_ref,
            "state": self.state,
            "start_at": iso_utc(self.start_at),
            "end_at": iso_utc(self.end_at),
            "party_size": self.party_size,
            "table_label": self.table_label or None,
            "seq": self.seq,
        }

    @api.model
    def efn_booking_create(self, outlet_code, booking):
        """#17 buat reservasi (idempoten). Ketersediaan dihitung ulang di bawah kunci settings outlet."""
        settings = self._efn_booking_check_caller(outlet_code)
        self = self.with_env(settings.env)  # noqa: PLW0642 - sudo + context bersih setelah cek pemanggil
        allowed = {"idempotency_key", "platform_booking_ref", "start_at", "party_size", "guest_label", "note"}
        require(isinstance(booking, dict) and {"idempotency_key", "platform_booking_ref", "start_at", "party_size"} <= set(booking) <= allowed)
        key = booking["idempotency_key"]
        require(isinstance(key, str) and bool(IDEMPOTENCY_KEY_RE.match(key)))
        ref = booking["platform_booking_ref"]
        require(isinstance(ref, str) and bool(UUID_RE.match(ref)))
        ref = ref.lower()
        start = parse_start(booking["start_at"])
        party = booking["party_size"]
        require(isinstance(party, int) and not isinstance(party, bool), "invalid_input")
        guest_label = clean_text(booking.get("guest_label"), GUEST_LABEL_MAX)
        note = clean_text(booking.get("note"), NOTE_MAX)
        digest = canonical_sha256(
            {
                "outlet": settings.outlet_id.code,
                "ref": ref,
                "start_at": iso_utc(start),
                "party": party,
                "guest_label": guest_label,
                "note": note,
            }
        )
        settings._efn_lock()
        existing = self.search([("idempotency_key", "=", key)], limit=1) or self.search(
            [("company_id", "=", settings.company_id.id), ("platform_booking_ref", "=", ref)], limit=1
        )
        if existing:
            require(existing.request_sha256 == digest and existing.idempotency_key == key, "idempotency_conflict")
            return existing._efn_result()
        require(settings.min_party <= party <= settings.max_party, "party_size")
        tz = self._efn_tz(settings)
        day = pytz.utc.localize(start).astimezone(tz).date()
        self._efn_check_window(settings, day, start)
        require(start in self._efn_day_slots(settings, day), "outside_hours")
        end = start + timedelta(minutes=settings.duration_minutes)
        tables = settings._efn_tables()
        busy = self._efn_busy_by_table(settings, tables, start, end)
        table = self._efn_free_table(settings, tables, busy, start, end, party)
        require(bool(table), "slot_unavailable")
        auto = settings.confirm_mode == "auto"
        now = self._efn_now()
        rec = self.create(
            {
                "company_id": settings.company_id.id,
                "settings_id": settings.id,
                "platform_booking_ref": ref,
                "idempotency_key": key,
                "request_sha256": digest,
                "start_at": start,
                "end_at": end,
                "party_size": party,
                "table_id": table.id,
                "state": "confirmed" if auto else "requested",
                "confirmed_at": now if auto else False,
                "guest_label": guest_label or False,
                "note": note or False,
            }
        )
        _logger.info("EFN booking %s dibuat (outlet %s, %s)", rec.id, settings.outlet_id.code, rec.state)
        if auto:
            rec._efn_emit("confirmed")
        return rec._efn_result()

    # ================================================================== cancel
    @api.model
    def efn_booking_cancel(self, outlet_code, platform_booking_ref, idempotency_key):
        """#18 batal oleh pelanggan: hanya requested/confirmed yang belum mulai. Idempoten (sudah batal -> hasil sama)."""
        settings = self._efn_booking_check_caller(outlet_code)
        self = self.with_env(settings.env)  # noqa: PLW0642
        require(isinstance(idempotency_key, str) and bool(IDEMPOTENCY_KEY_RE.match(idempotency_key)))
        require(isinstance(platform_booking_ref, str) and bool(UUID_RE.match(platform_booking_ref)))
        settings._efn_lock()
        rec = self.search(
            [("settings_id", "=", settings.id), ("platform_booking_ref", "=", platform_booking_ref.lower())], limit=1
        )
        require(bool(rec), "not_found")
        if rec.state == "cancelled" and rec.cancel_reason == "customer":
            return {"state": rec.state, "seq": rec.seq}
        require(rec.state in ("requested", "confirmed") and rec.start_at > self._efn_now(), "invalid_state")
        rec._efn_transition("cancelled", cancel_reason="customer", emit=False)
        return {"state": rec.state, "seq": rec.seq}

    # ================================================================== status
    @api.model
    def efn_booking_status(self, outlet_code, platform_booking_refs):
        """#19 status reservasi milik outlet (baca, rekonsiliasi BFF). Ref asing/tidak dikenal = tidak muncul."""
        settings = self._efn_booking_check_caller(outlet_code)
        self = self.with_env(settings.env)  # noqa: PLW0642
        require(isinstance(platform_booking_refs, list) and len(platform_booking_refs) <= MAX_STATUS_REFS)
        refs = []
        for ref in platform_booking_refs:
            require(isinstance(ref, str) and bool(UUID_RE.match(ref)))
            refs.append(ref.lower())
        recs = self.search([("settings_id", "=", settings.id), ("platform_booking_ref", "in", refs)]) if refs else self
        return [
            {
                "platform_booking_ref": r.platform_booking_ref,
                "state": r.state,
                "seq": r.seq,
                "start_at": iso_utc(r.start_at),
                "end_at": iso_utc(r.end_at),
                "table_label": r.table_label or None,
            }
            for r in recs
        ]

    # ======================================================= transitions/events
    def _efn_transition(self, state, cancel_reason=None, emit=True):
        """Ubah state + seq; event ke BFF. ``emit=False`` hanya untuk batal oleh BFF sendiri (BFF sudah tahu)."""
        now = self._efn_now()
        for rec in self:
            vals = {"state": state, "seq": rec.seq + 1}
            if state == "confirmed":
                vals["confirmed_at"] = now
            elif state == "seated":
                vals["seated_at"] = now
            elif state in ("cancelled", "no_show"):
                vals["cancelled_at"] = now
                vals["cancel_reason"] = cancel_reason or ("no_show" if state == "no_show" else "staff")
            rec.sudo().write(vals)
            if emit:
                rec._efn_emit(state)

    def _efn_emit(self, event):
        outbox = self.env["efn.booking.event.outbox"].sudo()
        for rec in self:
            outbox._efn_enqueue(rec, event)

    def _efn_check_staff(self):
        if not self.env.user.has_group("point_of_sale.group_pos_manager"):
            raise AccessError(_("Hanya manajer POS yang boleh mengubah reservasi."))
        self.check_access("write")
        for rec in self:
            if rec.company_id not in self.env.user.company_ids:
                raise AccessError(_("Reservasi PT lain."))

    def _efn_staff_move(self, allowed_from, state, cancel_reason=None):
        self._efn_check_staff()
        for rec in self:
            if rec.state not in allowed_from:
                raise ValidationError(_("Status reservasi %(name)s tidak dapat diubah dari '%(state)s'.", name=rec.name, state=rec.state))
        self._efn_transition(state, cancel_reason=cancel_reason)
        return True

    def action_efn_confirm(self):
        return self._efn_staff_move(("requested",), "confirmed")

    def action_efn_seat(self):
        return self._efn_staff_move(("requested", "confirmed"), "seated")

    def action_efn_done(self):
        return self._efn_staff_move(("seated",), "done")

    def action_efn_no_show(self):
        self._efn_check_staff()
        now = self._efn_now()
        for rec in self:
            hold = timedelta(minutes=rec.settings_id.hold_minutes)
            if rec.start_at + hold > now:
                raise ValidationError(_("'Tidak datang' baru boleh setelah jam mulai + waktu tahan meja."))
        return self._efn_staff_move(("requested", "confirmed"), "no_show")

    def action_efn_cancel(self):
        return self._efn_staff_move(("requested", "confirmed"), "cancelled", cancel_reason="staff")

    # ==================================================================== cron
    @api.model
    def _cron_efn_booking_expire(self):
        """``requested`` yang tidak dikonfirmasi sampai jam mulai -> batal (expired) + event. ``no_show`` = staf."""
        due = self.sudo().search([("state", "=", "requested"), ("start_at", "<=", self._efn_now())], limit=500)
        due._efn_transition("cancelled", cancel_reason="expired")
        return len(due)

    @api.model
    def _efn_new_ref(self):
        return str(uuid.uuid4())
