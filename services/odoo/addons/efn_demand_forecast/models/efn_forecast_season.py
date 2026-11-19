"""Kalender musim (dikelola bisnis, bukan hard-code).

Jenis:
* ``period``  : rentang tanggal (Ramadan, Lebaran, Natal-Tahun Baru, Imlek). Tanggal Hijriah bergeser tiap tahun
  sehingga diisi per tahun oleh planner.
* ``monthly`` : hari-hari dalam bulan (mis. tgl 25 s.d. 5). Tetap didukung untuk musim lama.
* ``weekday`` : hari tertentu dalam minggu (akhir pekan).
* ``payday``  : (G44, docs/04 §28.3, L-146) minggu gajian = N hari TERAKHIR bulan + M hari PERTAMA bulan, N/M dari
  parameter per company ``efn_forecast.payday_tail_days`` / ``efn_forecast.payday_head_days`` (default 7/7),
  mengikuti panjang bulan. Menggantikan default lama "tgl 25 s.d. 5".
* ``holiday`` : (G44) jendela H-x..H+y relatif terhadap entri ``efn.forecast.calendar`` jenis tertentu (libur
  nasional, cuti bersama, Lebaran, ...), sehingga v1 memakai kalender yang sama dengan v2 (worker).
Faktor > 1 = permintaan naik. Beberapa musim pada hari yang sama dikalikan.
"""

import calendar
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .efn_forecast_calendar import CALENDAR_KINDS

PAYDAY_TAIL_PARAM = "efn_forecast.payday_tail_days"
PAYDAY_HEAD_PARAM = "efn_forecast.payday_head_days"
PAYDAY_DEFAULT = 7
PAYDAY_MAX = 15


def _param_int(icp, key, default, low, high):
    try:
        value = int(str(icp.get_param(key) or "").strip())
    except (TypeError, ValueError):
        return default
    return value if low <= value <= high else default


class EfnForecastSeason(models.Model):
    _name = "efn.forecast.season"
    _description = "EFN kalender musim forecast"
    _inherit = ["mail.thread"]
    _order = "kind, date_from, sequence, id"

    name = fields.Char(required=True, tracking=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", help="Kosong = berlaku untuk semua PT.")
    kind = fields.Selection(
        [
            ("period", "Rentang tanggal"),
            ("monthly", "Tanggal tiap bulan"),
            ("weekday", "Hari dalam minggu"),
            ("payday", "Minggu gajian (parameter company)"),
            ("holiday", "Jendela hari libur (kalender)"),
        ],
        default="period",
        required=True,
    )
    date_from = fields.Date(tracking=True)
    date_to = fields.Date(tracking=True)
    day_from = fields.Integer(string="Dari tgl", help="Untuk 'Tanggal tiap bulan' (1-31). Boleh melewati akhir bulan.")
    day_to = fields.Integer(string="Sampai tgl")
    weekdays = fields.Char(string="Hari (0=Sen..6=Min)", help="Mis. '5,6' untuk Sabtu & Minggu.")
    calendar_kind = fields.Selection(
        CALENDAR_KINDS,
        string="Jenis kalender",
        help="Untuk 'Jendela hari libur': entri efn.forecast.calendar aktif jenis ini (company yang sama).",
    )
    days_before = fields.Integer(string="H- (hari sebelum)", default=0)
    days_after = fields.Integer(string="H+ (hari sesudah)", default=0)
    factor = fields.Float(default=1.0, digits=(4, 2), required=True, tracking=True)
    product_categ_ids = fields.Many2many("product.category", string="Kategori", help="Kosong = semua produk.")

    @api.constrains(
        "kind",
        "date_from",
        "date_to",
        "day_from",
        "day_to",
        "weekdays",
        "factor",
        "calendar_kind",
        "days_before",
        "days_after",
    )
    def _check_season(self):
        for season in self:
            if not 0.1 <= season.factor <= 10:
                raise ValidationError(_("efn_demand_forecast:factor_range — faktor 0,1 s.d. 10."))
            if season.kind == "period" and (
                not season.date_from or not season.date_to or season.date_to < season.date_from
            ):
                raise ValidationError(_("efn_demand_forecast:period — isi rentang tanggal yang valid."))
            if season.kind == "monthly" and not (1 <= season.day_from <= 31 and 1 <= season.day_to <= 31):
                raise ValidationError(_("efn_demand_forecast:monthly — tanggal 1-31."))
            if season.kind == "weekday" and not season._efn_weekdays():
                raise ValidationError(_("efn_demand_forecast:weekday — isi hari 0-6, pisahkan koma."))
            if season.kind == "holiday" and (
                not season.calendar_kind or not 0 <= season.days_before <= 60 or not 0 <= season.days_after <= 60
            ):
                raise ValidationError(
                    _("efn_demand_forecast:holiday — pilih jenis kalender; H-/H+ masing-masing 0-60 hari.")
                )

    def _efn_weekdays(self):
        try:
            days = {int(x) for x in (self.weekdays or "").split(",") if x.strip()}
        except ValueError:
            return set()
        return days if days and days <= set(range(7)) else set()

    @api.model
    def _efn_payday_window(self, company=None):
        """(tail, head) hari minggu gajian untuk company (L-146; default 7/7).

        Kunci per company ``<param>.<KODE>`` (mis. ``efn_forecast.payday_tail_days.SMD``) menang atas kunci global;
        nilai di luar 0..15 atau bukan bilangan bulat -> default 7."""
        icp = self.env["ir.config_parameter"].sudo()
        company = company or self.env.company
        code = company.efn_brand_code if company else False
        result = []
        for key in (PAYDAY_TAIL_PARAM, PAYDAY_HEAD_PARAM):
            value = _param_int(icp, key, PAYDAY_DEFAULT, 0, PAYDAY_MAX)
            if code and icp.get_param(f"{key}.{code}"):
                value = _param_int(icp, f"{key}.{code}", value, 0, PAYDAY_MAX)
            result.append(value)
        return tuple(result)

    @api.model
    def _efn_is_payday(self, day, company=None):
        tail, head = self._efn_payday_window(company)
        month_len = calendar.monthrange(day.year, day.month)[1]
        return (tail > 0 and day.day > month_len - tail) or (head > 0 and day.day <= head)

    def _efn_holiday_hit(self, day, company):
        self.ensure_one()
        if not company:
            return False
        return bool(
            self.env["efn.forecast.calendar"]
            .sudo()
            .search_count(
                [
                    ("company_id", "=", company.id),
                    ("warehouse_id", "=", False),
                    ("kind", "=", self.calendar_kind),
                    ("date", ">=", day - timedelta(days=self.days_after)),
                    ("date", "<=", day + timedelta(days=self.days_before)),
                ],
                limit=1,
            )
        )

    def _efn_applies(self, day, product):
        self.ensure_one()
        if self.product_categ_ids:
            categ, hit = product.categ_id, False
            while categ:
                if categ in self.product_categ_ids:
                    hit = True
                    break
                categ = categ.parent_id
            if not hit:
                return False
        if self.kind == "period":
            return self.date_from <= day <= self.date_to
        if self.kind == "monthly":
            if self.day_from <= self.day_to:
                return self.day_from <= day.day <= self.day_to
            return day.day >= self.day_from or day.day <= self.day_to
        if self.kind == "payday":
            # Musim global (tanpa company) memakai company env (forecast memanggil dengan with_company PT gudang).
            return self._efn_is_payday(day, self.company_id or self.env.company)
        if self.kind == "holiday":
            return self._efn_holiday_hit(day, self.company_id or self.env.company)
        return day.weekday() in self._efn_weekdays()

    @api.model
    def _efn_factor(self, seasons, day, product):
        factor = 1.0
        for season in seasons:
            if season._efn_applies(day, product):
                factor *= season.factor
        return factor
