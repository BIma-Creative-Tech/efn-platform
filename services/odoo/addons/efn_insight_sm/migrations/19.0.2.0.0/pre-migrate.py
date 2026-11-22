"""efn_insight_sm 19.0.2.0.0 (G52, docs/04 §28.15.2): SQL relokasi xmlid yang sama dengan efn_insight 19.0.3.0.0.

Dijalankan juga di sini agar upgrade efn_insight_sm (mis. operator lupa efn_insight) tidak menghapus grup persona
yang tidak lagi dideklarasikan modul ini (`_process_end`) beserta keanggotaan user. Idempoten (no-op bila efn_insight
sudah memindahkannya). Upgrade yang benar tetap `-u efn_insight,efn_insight_sm` satu perintah.
"""

from odoo.addons.efn_insight.lib.persona_xmlids import rename_persona_xmlids


def migrate(cr, version):
    if not version:
        return
    rename_persona_xmlids(cr)
