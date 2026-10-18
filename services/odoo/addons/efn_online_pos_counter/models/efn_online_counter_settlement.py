"""Tautan pelunasan POS <-> pesanan online (satu pesanan = maksimal satu order POS pelunasan; satu refund POS).

Dibuat sudo oleh sync order POS (``pos.order._process_saved_order``) setelah validasi server. Unik per pesanan online
dan per ``pos.order.uuid`` (idempotensi + cegah bayar dobel). Tanpa data pelanggan. ACL baca saja untuk user.
"""

from odoo import fields, models


class EfnOnlineCounterSettlement(models.Model):
    _name = "efn.online.counter.settlement"
    _description = "EFN Online - Pelunasan pesanan online di kasir"
    _order = "id desc"
    _check_company_auto = True
    _rec_name = "pos_order_uuid"

    company_id = fields.Many2one("res.company", required=True, index=True, readonly=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    online_order_id = fields.Many2one(
        "efn.online.order", string="Pesanan online", required=True, readonly=True, index=True, check_company=True
    )
    pos_order_id = fields.Many2one(
        "pos.order", string="Order POS pelunasan", required=True, readonly=True, index=True, check_company=True
    )
    pos_order_uuid = fields.Char(required=True, readonly=True, index=True)
    config_id = fields.Many2one("pos.config", readonly=True, check_company=True)
    session_id = fields.Many2one("pos.session", readonly=True, check_company=True)
    amount = fields.Monetary(readonly=True)
    online_state = fields.Char(
        string="Status pesanan setelah bayar", readonly=True, help="confirmed | stock_short | review (kode saja)."
    )
    refund_pos_order_id = fields.Many2one(
        "pos.order", string="Order POS refund", readonly=True, index="btree_not_null", check_company=True
    )
    refunded_at = fields.Datetime(readonly=True)

    _online_order_unique = models.Constraint(
        "unique (online_order_id)", "Pesanan online hanya boleh dilunasi satu kali di kasir."
    )
    _pos_order_unique = models.Constraint("unique (pos_order_id)", "Satu order POS hanya melunasi satu pesanan.")
    _uuid_unique = models.Constraint("unique (pos_order_uuid)", "uuid order POS pelunasan harus unik.")
    _refund_unique = models.Constraint(
        "unique (refund_pos_order_id)", "Satu order POS refund hanya untuk satu pelunasan."
    )
