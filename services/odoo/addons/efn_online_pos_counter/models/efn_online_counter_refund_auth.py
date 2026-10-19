"""SEC-145 (L-59): otorisasi BFF untuk refund POS atas pelunasan counter.

Refund online ``requested`` di Odoo **belum** berarti disetujui: BFF memanggil ``efn_online_refund`` (nota kredit)
SEBELUM approver CS menyetujui (``approval_required``, SEC-95/SEC-107), dan pembatalan Sales Manager dari UI Odoo
membuat refund ``initiator=odoo`` yang di BFF wajib disetujui approver. Satu-satunya sinyal "boleh keluar uang" =
BFF memindahkan refund counter ke ``manual_required`` (``counter_refund_via_pos``), yaitu setelah approver (atau di
bawah ambang approval / kompensasi sistem ``stock_short``). Pada titik itu BFF memanggil RPC
``efn.online.order.efn_online_counter_refund_authorize`` -> satu record di sini. Refund POS (``pos.order``) hanya
diterima bila otorisasi ini ada dan nominalnya persis.

Ditulis sudo oleh RPC setelah ``_efn_online_check_caller``; append-only (tanpa ACL tulis). Tanpa data pelanggan.
"""

from odoo import fields, models


class EfnOnlineCounterRefundAuth(models.Model):
    _name = "efn.online.counter.refund.auth"
    _description = "EFN Online - Otorisasi BFF refund pelunasan di kasir"
    _order = "id desc"
    _check_company_auto = True
    _rec_name = "bff_refund_id"

    company_id = fields.Many2one("res.company", required=True, index=True, readonly=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    settlement_id = fields.Many2one(
        "efn.online.counter.settlement",
        string="Pelunasan",
        required=True,
        readonly=True,
        index=True,
        ondelete="restrict",
        check_company=True,
    )
    online_order_id = fields.Many2one(
        "efn.online.order", string="Pesanan online", required=True, readonly=True, index=True, check_company=True
    )
    refund_id = fields.Many2one(
        "efn.online.refund",
        string="Refund online (nota kredit)",
        readonly=True,
        index="btree_not_null",
        check_company=True,
        help="Kosong = kompensasi tanpa faktur (pesanan batal sebelum difaktur).",
    )
    bff_refund_id = fields.Char(string="Id refund BFF", required=True, readonly=True, copy=False)
    idempotency_key = fields.Char(required=True, readonly=True, copy=False, groups="base.group_system")
    payload_sha256 = fields.Char(readonly=True, copy=False, groups="base.group_system")
    amount = fields.Monetary(readonly=True)
    consumed_pos_order_id = fields.Many2one(
        "pos.order", string="Order POS refund", readonly=True, index="btree_not_null", check_company=True
    )

    _idempotency_key_unique = models.Constraint("unique (idempotency_key)", "Kunci idempotensi otorisasi harus unik.")
    _bff_refund_unique = models.Constraint("unique (bff_refund_id)", "Satu refund BFF hanya satu otorisasi.")
    _refund_unique = models.Constraint("unique (refund_id)", "Satu refund online hanya satu otorisasi.")

    def _efn_result(self):
        self.ensure_one()
        return {"state": "authorized", "amount": self.online_order_id._efn_amount_out(self.amount)}
