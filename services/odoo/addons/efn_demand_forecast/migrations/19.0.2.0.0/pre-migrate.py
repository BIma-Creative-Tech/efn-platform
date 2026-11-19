"""G44 (19.0.2.0.0): nama ``efn.forecast.run`` berpindah dari wizard v1 (TransientModel) ke model run v2 (persisten).

Tabel ``efn_forecast_run`` lama hanya berisi baris wizard sementara -> dikosongkan; kolom NOT NULL wizard (history_days,
lead_days, review_days, service_level) dilepas agar insert run v2 tidak gagal; flag transient di ``ir_model``
diturunkan (ORM juga merefleksikannya). Idempoten.
"""

import logging

_logger = logging.getLogger(__name__)

OLD_WIZARD_COLUMNS = ("history_days", "lead_days", "review_days", "service_level")


def migrate(cr, version):
    if not version:
        return
    cr.execute("SELECT 1 FROM ir_model WHERE model = 'efn.forecast.run' AND transient")
    if not cr.fetchone():
        return
    cr.execute("SELECT to_regclass('efn_forecast_run')")
    if cr.fetchone()[0]:
        cr.execute("DELETE FROM efn_forecast_run")
        for column in OLD_WIZARD_COLUMNS:
            cr.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_name = 'efn_forecast_run' AND column_name = %s",
                (column,),
            )
            if cr.fetchone():
                cr.execute(f'ALTER TABLE efn_forecast_run ALTER COLUMN "{column}" DROP NOT NULL')  # noqa: S608
    cr.execute("UPDATE ir_model SET transient = false WHERE model = 'efn.forecast.run'")
    _logger.info("EFN forecast G44: tabel wizard efn_forecast_run dikosongkan untuk model run v2")
