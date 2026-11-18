"""G44 (19.0.2.0.0, L-146): musim gajian bawaan "tgl 25 s.d. 5" -> jenis ``payday`` (7 + 7 hari, parameter).

Hanya mengubah record ``efn_demand_forecast.season_payday`` bila masih bernilai bawaan (monthly 25..5); musim yang sudah
diubah planner dibiarkan. Idempoten.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        """
        UPDATE efn_forecast_season s
           SET kind = 'payday', day_from = NULL, day_to = NULL,
               name = 'Minggu gajian (7 hari terakhir + 7 hari pertama)'
          FROM ir_model_data d
         WHERE d.module = 'efn_demand_forecast' AND d.name = 'season_payday' AND d.model = 'efn.forecast.season'
           AND d.res_id = s.id AND s.kind = 'monthly' AND s.day_from = 25 AND s.day_to = 5
        """
    )
    if cr.rowcount:
        _logger.info("EFN forecast G44: musim gajian bawaan dikonversi ke jenis payday (7+7)")
