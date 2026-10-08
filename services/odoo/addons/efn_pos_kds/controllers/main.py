"""Layar KDS ``/efn_kds/<station_id>`` (docs/08 §7.3 "Layar KDS").

``auth='user'`` (bukan ``public``): hanya user perangkat stasiun itu atau manager POS, stasiun
milik company user. Selain itu 404 (tidak membocorkan keberadaan stasiun). Token kanal bus hanya
keluar lewat halaman ini, setelah cek akses.
"""

from odoo import http
from odoo.exceptions import AccessError, MissingError
from odoo.http import request


class EfnKdsController(http.Controller):
    @http.route("/efn_kds/<int:station_id>", type="http", auth="user", methods=["GET"], sitemap=False)
    def efn_kds_screen(self, station_id, **kw):
        station = request.env["efn.kds.station"].browse(station_id)
        try:
            if not station.exists():
                raise MissingError("station")
            station._efn_kds_check_device()
        except (AccessError, MissingError):
            raise request.not_found() from None
        session_info = request.env["ir.http"].session_info()
        session_info["efn_kds"] = station._efn_kds_screen_data()
        response = request.render("efn_pos_kds.kds_index", {"session_info": session_info})
        # Halaman berisi token kanal: jangan disimpan cache perantara/peramban.
        response.headers["Cache-Control"] = "no-store"
        return response
