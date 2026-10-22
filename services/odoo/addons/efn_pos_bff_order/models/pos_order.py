"""Penanda order QR, siklus sesi meja, dan klaim kirim-ke-dapur mode ``auto`` (E-27, docs/04 §14.7 L-70/L-71).

- ``pos.order.efn_qr_order_ref`` (= submission_ref kiriman) / ``efn_qr_table_session_id`` / ``efn_qr_send_claimed_at``
  dan ``pos.order.line.efn_qr_submission_ref`` = milik SERVER. Nilai dari klien dibuang di ``_process_order`` dan di
  ``create``/``write`` non-sudo; pengecualian: baris baru boleh membawa ``efn_qr_submission_ref`` yang SAH (kiriman ada
  dan POS-nya sama dengan order) agar penanda ikut saat pelayan menggabung order meja (``mergeTableOrders`` membuat
  ulang baris dari data baris sumber).
- ``source`` tetap ``pos`` (fallback docs/08 §13.1b no. 4: efek ``selection_add`` ke filter frontend belum
  terverifikasi); order QR dikenali dari ``efn_qr_order_ref``.
- Order final (dibayar/batal/dihapus) -> status kiriman dihitung ulang + sesi meja ditutup bila meja tanpa draft.
- SEC-166 (r15): order dibayar yang BELUM tertaut sesi hanya dilekatkan bila sesi ``open`` (bukan ``expired``),
  ``date_order >= opened_at``, bukan refund, dan order membawa baris kiriman QR sesi itu (hasil gabung). Order
  pelayan/carried sudah tertaut saat sesi dibuka; order kiriman QR tertaut saat dibuat. Order tamu berikutnya di meja
  yang sama tidak pernah diserap.
- K3 (r15): ``_efn_qr_refund_mode`` memberi tahu ``efn_pos_loyalty_ledger`` (tanpa depends) apakah refund order asal
  dikirim sebagai reverse QR (sesi sudah close), ditunda ke neto close (sesi masih open/expired), atau sudah
  dinetokan.
- ``efn_qr_claim_send()`` (RPC POS, ``RPC_ALLOWLIST``): mode ``auto`` — ``UPDATE ... WHERE efn_qr_send_claimed_at IS
  NULL`` sehingga hanya satu terminal yang memanggil ``sendOrderInPreparationUpdateLastChange`` (cegah tiket/cetak
  ganda multi-perangkat). Server sendiri tidak pernah membuat tiket (L-71).
"""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

from odoo.addons.efn_core.models.efn_bff_client import partner_member_ref
from odoo.addons.efn_core.scope import in_scope

from .efn_pos_qr_order import QR_INTERNAL_SCOPE

POS_USER_GROUP = "point_of_sale.group_pos_user"
FINAL_STATES = ("paid", "done", "invoiced", "cancel")
PAID_STATES = ("paid", "done", "invoiced")
LIVE_SESSION_STATES = ("open", "expired")
ORDER_SERVER_FIELDS = (
    "efn_qr_order_ref",
    "efn_qr_table_session_id",
    "efn_qr_send_claimed_at",
    "efn_qr_points_eligible",
    "efn_qr_eligible_amount",
    "efn_qr_refund_netted",
    "efn_qr_refund_awaiting",
)
LINE_SERVER_FIELDS = ("efn_qr_submission_ref",)


class PosOrder(models.Model):
    _inherit = "pos.order"

    efn_qr_order_ref = fields.Char(
        string="Ref kiriman QR", readonly=True, copy=False, index="btree_not_null", help="Order dibuat dari QR ponsel."
    )
    efn_qr_table_session_id = fields.Many2one(
        "efn.pos.table.session",
        string="Sesi meja QR",
        readonly=True,
        copy=False,
        index="btree_not_null",
        ondelete="set null",
    )
    efn_qr_send_claimed_at = fields.Datetime(readonly=True, copy=False)
    # L-80: order sesi QR yang dibayar dan dilaporkan ke BFF di event close (BFF dapat meng-earn untuk member yang
    # mengklaim sesi). Dibaca efn_pos_loyalty_ledger (tanpa depends) untuk reverse `earn_channel:"qr"` saat refund.
    efn_qr_points_eligible = fields.Boolean(readonly=True, copy=False)
    # K2/K3 r15: dasar poin neto yang dilaporkan di event close (penyebut rasio reverse QR setelah close).
    efn_qr_eligible_amount = fields.Integer(readonly=True, copy=False)
    # K3 r15: order REFUND ini sudah dinetokan ke eligible_amount event close -> tidak boleh memicu reverse QR lagi.
    efn_qr_refund_netted = fields.Boolean(readonly=True, copy=False)
    # K3 r15 (L-83): refund dibayar SETELAH snapshot close dibekukan dan sebelum 2xx -> reverse QR di-enqueue saat 2xx;
    # tetap True (review + alert) bila event close gagal permanen.
    efn_qr_refund_awaiting = fields.Boolean(readonly=True, copy=False, index="btree_not_null")

    @api.model
    def _load_pos_data_fields(self, config):
        fields_list = super()._load_pos_data_fields(config)
        if fields_list and "efn_qr_order_ref" not in fields_list:
            fields_list = [*fields_list, "efn_qr_order_ref"]
        return fields_list

    # ------------------------------------------------------------------ guard field server
    def _efn_qr_internal(self):
        return self.env.su or in_scope(self.env, QR_INTERNAL_SCOPE)

    @api.model_create_multi
    def create(self, vals_list):
        if not self._efn_qr_internal():
            for vals in vals_list:
                for key in ORDER_SERVER_FIELDS:
                    vals.pop(key, None)
        return super().create(vals_list)

    def write(self, vals):
        if not self._efn_qr_internal():
            vals = {k: v for k, v in vals.items() if k not in ORDER_SERVER_FIELDS}
        final = vals.get("state") in FINAL_STATES
        result = super().write(vals)
        if final:
            self._efn_qr_after_final()
        return result

    @api.model
    def _get_open_order(self, order):
        """L-70: banyak draft per meja (order pelayan + satu order per kiriman QR).

        ``pos_restaurant`` mencari ``uuid = X OR (meja + draft)`` dan mengambil id terbesar: kiriman QR akan menimpa
        order pelayan, dan sinkron order pelayan akan menimpa order QR terbaru. Di sini uuid yang cocok selalu menang;
        kiriman QR tanpa uuid cocok selalu order baru; fallback standar meja tidak pernah memilih order QR."""
        uuid_value = order.get("uuid")
        exact = self.env["pos.order"]
        if uuid_value:
            exact = exact.search([("uuid", "=", uuid_value)], limit=1, order="id desc")
        if exact or in_scope(self.env, QR_INTERNAL_SCOPE):
            return exact
        found = super()._get_open_order(order)
        if found.efn_qr_order_ref:
            found = self.env["pos.order"].search(
                [
                    ("table_id", "=", found.table_id.id),
                    ("config_id", "=", found.config_id.id),
                    ("state", "=", "draft"),
                    ("efn_qr_order_ref", "=", False),
                ],
                limit=1,
                order="id desc",
            )
        return found

    @api.model
    def _process_order(self, order, existing_order):
        internal = in_scope(self.env, QR_INTERNAL_SCOPE)
        if isinstance(order, dict) and not internal:
            for key in ORDER_SERVER_FIELDS:
                order.pop(key, None)
        order_id = super()._process_order(order, existing_order)
        if not internal:
            self.env["efn.pos.qr.order"].sudo()._efn_refresh_for_orders(self.browse(order_id))
        return order_id

    def unlink(self):
        orders = self.sudo()
        refs = set(orders.mapped("efn_qr_order_ref")) | set(orders.lines.mapped("efn_qr_submission_ref"))
        refs.discard(False)
        sessions = orders.efn_qr_table_session_id | orders._efn_qr_table_sessions()
        result = super().unlink()
        if refs:
            self.env["efn.pos.qr.order"].sudo().search([("submission_ref", "in", sorted(refs))])._efn_refresh_status()
        sessions.exists()._efn_check_close_after_orders()
        return result

    # ------------------------------------------------------------------ siklus sesi meja
    def _efn_qr_table_sessions(self):
        """Sesi meja aktif (open/expired) di meja order-order ini."""
        orders = self.sudo().filtered("table_id")
        if not orders:
            return self.env["efn.pos.table.session"]
        return (
            self.env["efn.pos.table.session"]
            .sudo()
            .search(
                [
                    ("config_id", "in", orders.config_id.ids),
                    ("table_id", "in", orders.table_id.ids),
                    ("state", "in", LIVE_SESSION_STATES),
                ]
            )
        )

    def _efn_qr_is_refund(self):
        """Order refund/retur (baris menunjuk order asal, atau ``is_refund``)."""
        self.ensure_one()
        order = self.sudo()
        return bool(order.is_refund or order.lines.filtered("refunded_orderline_id"))

    def _efn_qr_after_final(self):
        orders = self.sudo()
        sessions = orders._efn_qr_table_sessions()
        for order in orders.filtered(
            lambda o: o.state in PAID_STATES and not o.efn_qr_table_session_id and not o._efn_qr_is_refund()
        ):
            # SEC-166: hanya order hasil gabung yang membawa baris kiriman QR sesi itu (lihat docstring modul).
            match = sessions.filtered(
                lambda s, o=order: s.config_id == o.config_id and s.table_id == o.table_id and s._efn_may_attach(o)
            )
            if match:
                order.efn_qr_table_session_id = match[:1]
        # Sesi expired tetap ditutup (event close) bila meja tanpa draft, tanpa menyerap order baru.
        (orders.efn_qr_table_session_id | sessions)._efn_check_close_after_orders()
        self.env["efn.pos.qr.order"].sudo()._efn_refresh_for_orders(orders)

    # ------------------------------------------------------------------ poin QR (K2/K3 r15)
    def _efn_qr_pos_member_attached(self):
        """K2 r15 (SEC-165): True bila order sudah punya member POS, sehingga earn POS (OTP kasir) yang berlaku dan BFF
        TIDAK meng-earn klaim QR.

        Ditentukan dari data yang sama dengan ``efn_pos_loyalty_ledger`` (tanpa depends):
        1. ledger mencatat earn/redeem_commit yang tidak ``skipped`` untuk order ini
           (``_efn_loyalty_original_involved``), atau
        2. partner order punya ``member_ref`` sah (HP Indonesia, ``partner_member_ref``) — kondisi yang membuat ledger
           mengantre earn ``pos_earn:<uuid>`` dengan member. Poin 2 diperlukan karena event close bisa dibangun di
           transaksi pembayaran yang sama SEBELUM outbox earn POS dibuat (hook ledger berjalan setelah state paid).
        Konservatif: partner member terlampir dianggap attached walau earn POS di config dimatikan."""
        self.ensure_one()
        order = self.sudo()
        if hasattr(order, "_efn_loyalty_original_involved") and order._efn_loyalty_original_involved():
            return True
        if not order.partner_id:
            return False
        member, _error = partner_member_ref(order.partner_id)
        return bool(member)

    def _efn_qr_refund_mode(self, refund):
        """K3 r15: perlakuan refund ``refund`` atas order asal ``self`` untuk poin QR (dibaca efn_pos_loyalty_ledger).

        - ``"netted"``: refund sudah dikurangkan dari ``eligible_amount`` snapshot close yang diterima BFF.
        - ``"reverse"``: event close yang melaporkan order asal sebagai dapat di-earn QR sudah diterima BFF (2xx; flag
          ``efn_qr_points_eligible`` hanya dipasang saat itu) -> kirim reverse ``earn_channel:"qr"``.
        - ``"deferred"``: sesi meja order asal masih ``open``/``expired``, ATAU sudah ditutup dan event close
          ``pending`` tetapi snapshot BELUM dibekukan -> refund akan dinetokan saat pembekuan; tanpa reverse.
        - ``"awaiting"``: event close ``pending`` dengan snapshot SUDAH beku (POST pertama sudah/akan dicoba dengan
          body itu) -> refund ditandai ``efn_qr_refund_awaiting``; saat 2xx outbox memprosesnya ulang menjadi reverse
          (rasio terhadap ``eligible_amount`` snapshot); close gagal permanen -> tetap bertanda + alert.
        - ``None``: bukan urusan poin QR (bukan order sesi, sesi ditutup staf/dibatalkan, event close gagal final,
          atau order ``pos_member_attached``/neto 0).
        Untuk ``deferred``/``awaiting`` baris sesi dikunci (``touch_row``); pembekuan dan pemrosesan 2xx di outbox
        mengunci baris yang sama, sehingga transaksi yang kalah gagal serialisasi dan diulang dengan snapshot baru —
        setiap refund tercermin tepat sekali (neto ATAU reverse)."""
        self.ensure_one()
        original = self.sudo()
        refund = refund.sudo()
        if refund.efn_qr_refund_netted:
            return "netted"
        if original.efn_qr_points_eligible:
            return "reverse"
        session = original.efn_qr_table_session_id
        if not session:
            return None
        if session.state in LIVE_SESSION_STATES:
            session._efn_lock()
            return "deferred"
        close = session._efn_pending_close()
        if not close:
            return None
        session._efn_lock()
        if not close.snapshot_frozen:
            return "deferred"
        refund.write({"efn_qr_refund_awaiting": True})
        return "awaiting"

    # ------------------------------------------------------------------ RPC POS (mode auto)
    def efn_qr_claim_send(self):
        """Klaim satu terminal untuk mengirim order QR ke dapur (mode ``auto``). True = pemanggil menang."""
        self.ensure_one()
        user = self.env.user
        if not user.has_group(POS_USER_GROUP):
            raise AccessError(_("Anda tidak berhak memakai fitur ini."))
        try:
            self.check_access("read")
        except AccessError:
            raise AccessError(_("Anda tidak berhak memakai fitur ini.")) from None
        order = self.sudo()
        config = order.config_id
        if order.company_id not in user.company_ids or config.company_id not in user.company_ids:
            raise AccessError(_("Anda tidak berhak memakai fitur ini."))
        if (
            not order.efn_qr_order_ref
            or order.state != "draft"
            or config.efn_qr_confirm_mode != "auto"
            or not config._efn_qr_open_pos_session()
        ):
            return False
        self.env.flush_all()
        self.env.cr.execute(
            """
            UPDATE pos_order SET efn_qr_send_claimed_at = (now() AT TIME ZONE 'UTC')
             WHERE id = %s AND efn_qr_send_claimed_at IS NULL
         RETURNING id
            """,
            [order.id],
        )
        won = bool(self.env.cr.fetchone())
        order.invalidate_recordset(["efn_qr_send_claimed_at"])
        return won


class PosOrderLine(models.Model):
    _inherit = "pos.order.line"

    efn_qr_submission_ref = fields.Char(
        string="Ref kiriman QR", readonly=True, copy=False, index="btree_not_null", help="Baris berasal dari QR ponsel."
    )

    @api.model
    def _load_pos_data_fields(self, config):
        fields_list = super()._load_pos_data_fields(config)
        if fields_list and "efn_qr_submission_ref" not in fields_list:
            fields_list = [*fields_list, "efn_qr_submission_ref"]
        return fields_list

    @api.model
    def _efn_qr_valid_ref(self, ref, order_id):
        if not isinstance(ref, str) or not isinstance(order_id, int):
            return False
        order = self.env["pos.order"].sudo().browse(order_id).exists()
        if not order:
            return False
        submission = self.env["efn.pos.qr.order"].sudo().search([("submission_ref", "=", ref)], limit=1)
        return bool(submission) and submission.config_id == order.config_id

    @api.model_create_multi
    def create(self, vals_list):
        if not (self.env.su or in_scope(self.env, QR_INTERNAL_SCOPE)):
            for vals in vals_list:
                ref = vals.get("efn_qr_submission_ref")
                if ref and not self._efn_qr_valid_ref(ref, vals.get("order_id")):
                    vals.pop("efn_qr_submission_ref", None)
        return super().create(vals_list)

    def write(self, vals):
        if "efn_qr_submission_ref" in vals and not (self.env.su or in_scope(self.env, QR_INTERNAL_SCOPE)):
            vals = {k: v for k, v in vals.items() if k not in LINE_SERVER_FIELDS}
        return super().write(vals)
