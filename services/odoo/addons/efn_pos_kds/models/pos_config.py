"""Status stasiun KDS untuk POS (cadangan printer F-53) + pemuatan stasiun ke data POS."""

from odoo import _, api, models
from odoo.exceptions import AccessError


class PosConfig(models.Model):
    _inherit = "pos.config"

    def efn_kds_station_status(self):
        """[{id, mode, is_online}] stasiun config ini — dipanggil POS sebelum mencetak dapur.

        Cek SEC-58: grup user POS + record rule config + company user. Tanpa token/data perangkat.
        """
        self.ensure_one()
        if not self.env.su:
            if not self.env.user.has_group("point_of_sale.group_pos_user"):
                raise AccessError(_("Anda tidak berhak membaca status KDS."))
            try:
                self.check_access("read")
            except AccessError:
                raise AccessError(_("Anda tidak berhak membaca status KDS.")) from None
            if self.company_id not in self.env.user.company_ids:
                raise AccessError(_("Anda tidak berhak membaca status KDS."))
        stations = (
            self.env["efn.kds.station"]
            .sudo()
            .search([("config_ids", "in", self.id), ("company_id", "=", self.company_id.id)])
        )
        return stations._efn_kds_status()


class PosSession(models.Model):
    _inherit = "pos.session"

    @api.model
    def _load_pos_data_models(self, config):
        return [*super()._load_pos_data_models(config), "efn.kds.station"]
