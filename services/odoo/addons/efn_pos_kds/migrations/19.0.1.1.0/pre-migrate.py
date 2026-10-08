"""SEC-74 (r8): rule perangkat KDS dipindah dari blok ``noupdate="1"`` ke blok yang ter-update.

Record yang xmlid-nya sudah ``noupdate=True`` di DB tidak akan ditimpa loader walau XML-nya kini
di luar blok noupdate, jadi flag dibalik sebelum data dimuat ulang. Rule company (multi-company)
tetap noupdate (pola CE: boleh disesuaikan admin).
"""

DEVICE_RULE_XMLIDS = (
    "efn_kds_station_rule_device",
    "efn_kds_ticket_rule_device",
    "efn_kds_ticket_line_rule_device",
    "res_partner_rule_kds_device",
)


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        """
        UPDATE ir_model_data
           SET noupdate = FALSE
         WHERE module = 'efn_pos_kds'
           AND model = 'ir.rule'
           AND name IN %s
        """,
        [DEVICE_RULE_XMLIDS],
    )
