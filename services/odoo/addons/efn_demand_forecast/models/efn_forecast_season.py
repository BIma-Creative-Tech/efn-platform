"""Kalender musim (dikelola bisnis, bukan hard-code).

Dua jenis:
* ``period``  : rentang tanggal (Ramadan, Lebaran, Natal-Tahun Baru, Imlek). Tanggal Hijriah bergeser tiap tahun
  sehingga diisi per tahun oleh planner.
* ``monthly`` : hari-hari dalam bulan (gajian tgl 25 s.d. 5).
* ``weekday`` : hari tertentu dalam minggu (akhir pekan).
Faktor > 1 = permintaan naik. Beberapa musim pada hari yang sama dikalikan.
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


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
        [("period", "Rentang tanggal"), ("monthly", "Tanggal tiap bulan"), ("weekday", "Hari dalam minggu")],
        default="period",
        required=True,
    )
    date_from = fields.Date(tracking=True)
    date_to = fields.Date(tracking=True)
    day_from = fields.Integer(string="Dari tgl", help="Untuk 'Tanggal tiap bulan' (1-31). Boleh melewati akhir bulan.")
    day_to = fields.Integer(string="Sampai tgl")
    weekdays = fields.Char(string="Hari (0=Sen..6=Min)", help="Mis. '5,6' untuk Sabtu & Minggu.")
    factor = fields.Float(default=1.0, digits=(4, 2), required=True, tracking=True)
    product_categ_ids = fields.Many2many("product.category", string="Kategori", help="Kosong = semua produk.")

    @api.constrains("kind", "date_from", "date_to", "day_from", "day_to", "weekdays", "factor")
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

    def _efn_weekdays(self):
        try:
            days = {int(x) for x in (self.weekdays or "").split(",") if x.strip()}
        except ValueError:
            return set()
        return days if days and days <= set(range(7)) else set()

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
        return day.weekday() in self._efn_weekdays()

    @api.model
    def _efn_factor(self, seasons, day, product):
        factor = 1.0
        for season in seasons:
            if season._efn_applies(day, product):
                factor *= season.factor
        return factor
