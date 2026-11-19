"""Kalender forecast (G44, docs/04 §28.3, ADR-046 §3.2, L-146): sumber kebenaran libur/cuti bersama/Ramadan/Lebaran.

- Per company (wajib), opsional per warehouse. ``source``: ``lib`` (seed library ``holidays`` oleh forecast-worker
  lewat ``efn_forecast_calendar_upsert``) atau ``manual`` (planner). Entri ``manual`` TIDAK PERNAH ditimpa worker;
  entri ``lib`` yang dinonaktifkan user tetap nonaktif (upsert tidak mengaktifkan ulang).
- Kunci unik ``(company_id, date, kind, name)`` — persis kontrak §28.2 (warehouse tidak ikut kunci).
- Minggu gajian & akhir pekan TIDAK disimpan per tanggal (parameter, lihat ``efn.forecast.season``).
- Hanya Inventory Administrator (planner) yang mengubah; perubahan teraudit ``efn_audit``
  (data/efn_audit_rule_data.xml).
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from . import forecast_api as fa

CALENDAR_KINDS = [
    ("holiday", "Libur nasional"),
    ("cuti_bersama", "Cuti bersama"),
    ("ramadan", "Ramadan"),
    ("lebaran_window", "Jendela Lebaran"),
    ("custom", "Lainnya"),
]
CALENDAR_KIND_CODES = frozenset(code for code, _label in CALENDAR_KINDS)
MAX_NAME = 64


class EfnForecastCalendar(models.Model):
    _name = "efn.forecast.calendar"
    _description = "EFN kalender forecast (libur, cuti bersama, Ramadan, Lebaran)"
    _order = "date desc, kind, id"
    _check_company_auto = True

    company_id = fields.Many2one(
        "res.company", required=True, index=True, default=lambda self: self.env.company, ondelete="cascade"
    )
    warehouse_id = fields.Many2one(
        "stock.warehouse", check_company=True, index="btree_not_null", help="Kosong = semua gudang PT."
    )
    date = fields.Date(required=True, index=True)
    kind = fields.Selection(CALENDAR_KINDS, required=True, default="holiday")
    name = fields.Char(required=True, size=MAX_NAME)
    source = fields.Selection(
        [("lib", "Library holidays (worker)"), ("manual", "Manual (planner)")],
        required=True,
        default="manual",
        readonly=True,
        help="Entri manual tidak pernah ditimpa seed worker.",
    )
    active = fields.Boolean(default=True)

    _efn_calendar_key_uniq = models.Constraint(
        "UNIQUE(company_id, date, kind, name)",
        "Entri kalender (company, tanggal, jenis, nama) sudah ada.",
    )

    @api.constrains("name")
    def _check_efn_name(self):
        for rec in self:
            if not (rec.name or "").strip() or len(rec.name) > MAX_NAME:
                raise ValidationError(_("efn_demand_forecast:calendar_name — nama 1-64 karakter."))

    # =================================================================== RPC
    @api.model
    def efn_forecast_calendar_upsert(self, **payload):
        """docs/04 §28.2: seed libur (source ``lib``). Idempoten per ``(company, date, kind, name)``.

        Respons ``{ok, company_code, accepted, unchanged, skipped_manual, rejected[{index, code}]}``."""
        env, company = fa.check_caller(self.env)
        fa.check_payload(payload, ("schema_version", "company_code", "entries"))
        fa.check_company_code(company, payload["company_code"])
        entries = payload["entries"]
        fa.check_items(entries)
        Calendar = env["efn.forecast.calendar"].with_context(active_test=False)
        rejected, to_create, seen = [], [], set()
        unchanged = skipped_manual = 0
        parsed = []
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict) or set(entry) != {"date", "kind", "name", "source"}:
                rejected.append({"index": index, "code": "invalid_entry"})
                continue
            day = fa.parse_date(entry["date"])
            name = entry["name"].strip() if isinstance(entry["name"], str) else ""
            if day is None:
                rejected.append({"index": index, "code": "invalid_date"})
                continue
            if entry["kind"] not in CALENDAR_KIND_CODES:
                rejected.append({"index": index, "code": "invalid_kind"})
                continue
            if not name or len(name) > MAX_NAME:
                rejected.append({"index": index, "code": "invalid_name"})
                continue
            if entry["source"] != "lib":
                rejected.append({"index": index, "code": "invalid_source"})
                continue
            key = (day, entry["kind"], name)
            if key in seen:
                rejected.append({"index": index, "code": "duplicate_entry"})
                continue
            seen.add(key)
            parsed.append(key)
        existing = {}
        if parsed:
            dates = sorted({key[0] for key in parsed})
            for rec in Calendar.search([("company_id", "=", company.id), ("date", "in", dates)]):
                existing[(rec.date, rec.kind, rec.name)] = rec
        for day, kind, name in parsed:
            rec = existing.get((day, kind, name))
            if rec is None:
                to_create.append({"company_id": company.id, "date": day, "kind": kind, "name": name, "source": "lib"})
            elif rec.source == "manual":
                skipped_manual += 1
            else:
                # Entri lib yang sudah ada (aktif ATAU dinonaktifkan user) tidak diubah.
                unchanged += 1
        if to_create:
            Calendar.create(to_create)
        return {
            "ok": True,
            "company_code": company.efn_brand_code,
            "accepted": len(to_create),
            "unchanged": unchanged,
            "skipped_manual": skipped_manual,
            "rejected": rejected,
        }
