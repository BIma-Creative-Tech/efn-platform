"""Validasi server saat sync order POS (docs/04 §28.5a; fit-gap G45 §3 opsi D "Server").

Order yang tersinkron sudah dibayar -> **tidak ditolak** (termasuk yang dibuat offline). Setelah
``_process_saved_order`` (non-draft, lunas) dan flag ``efn_expiry_monitor.enabled`` menyala, setiap baris ber-lot
diperiksa sekali (``efn_expiry_checked``):

- Waktu penilaian (SEC-217): ``date_order`` bila di jendela ``[session.start_at − 5 mnt, now + 5 mnt]``; di luar itu
  waktu server + event ``clock_skew`` (jam perangkat tidak dipercaya). Tanggal periode markdown memakai waktu yang sama.
- Lot kedaluwarsa / ``removal_date`` lewat pada waktu penilaian: sah bila baris membawa ``efn_expiry_override`` DAN
  (user yang menyinkronkan = ``env.uid`` punya grup ``group_expiry_override`` ATAU ada ``efn.expiry.pos.approval`` sah
  untuk config + uuid order + produk + lot + kasir = ``env.uid``) -> event ``pos_expired_override`` (persetujuan
  ``consumed``); selain itu ``pos_expired_no_override``. ``order.user_id`` payload tidak dipakai (SEC-217).
- Markdown (``efn_markdown_ref`` dari klien): sah bila markdown milik PT & outlet config, produk & lot cocok, status
  bukan draft/batal, ``date_order`` (tanggal lokal) dalam periode, dan sumber lot ``gs1``/``manual``; selain itu event
  ``markdown_invalid``. Kuota: baris markdown dikunci (``touch_row``), ``qty_sold`` += min(qty, sisa kuota); kelebihan
  -> event ``markdown_cap_exceeded`` (klien sudah membatasi qty; sisa dijual harga normal di baris lain). Harga baris
  di bawah markdown (``price``: harga efektif < harga markdown; persen: diskon > persen ATAU harga efektif < harga
  jual normal × (1 − persen)) -> event ``markdown_price_mismatch``. Baris retur (qty < 0) mengurangi ``qty_sold``
  hanya dari baris asal ``refunded_orderline_id.efn_markdown_id`` (dibatasi qty asal); klaim retur diabaikan (SEC-222).
Field klien (``efn_lot_source``, ``efn_markdown_ref``, ``efn_expiry_override``) hanya klaim; server memutuskan.
"""

from datetime import timedelta

from odoo import api, fields, models

from odoo.addons.efn_core.scope import touch_row

from .common import EXPLICIT_SOURCES, GROUP_OVERRIDE, LOT_SOURCES, is_enabled, lot_blocked

PAID_STATES = ("paid", "done", "invoiced")
# SEC-217: toleransi jam perangkat di depan waktu server.
CLOCK_SKEW_MINUTES = 5
POS_LINE_FIELDS = ("efn_lot_source", "efn_markdown_ref", "efn_expiry_override")


class PosOrderLine(models.Model):
    _inherit = "pos.order.line"

    efn_lot_source = fields.Selection(LOT_SOURCES, string="Sumber lot", copy=False)
    efn_markdown_ref = fields.Integer(string="Markdown (klaim POS)", copy=False)
    efn_expiry_override = fields.Boolean(string="Override kedaluwarsa (klaim POS)", copy=False)
    efn_markdown_id = fields.Many2one("efn.lot.markdown", string="Markdown sah", readonly=True, copy=False)
    efn_expiry_checked = fields.Boolean(readonly=True, copy=False)

    @api.model
    def _load_pos_data_fields(self, config):
        fields_list = super()._load_pos_data_fields(config)
        # Daftar kosong = semua field dimuat (pos.load.mixin); jangan dipersempit.
        if fields_list:
            fields_list = [*fields_list, *(name for name in POS_LINE_FIELDS if name not in fields_list)]
        return fields_list


class PosOrder(models.Model):
    _inherit = "pos.order"

    def _process_saved_order(self, draft):
        result = super()._process_saved_order(draft)
        if not draft and self.state in PAID_STATES and is_enabled(self.env):
            # SEC-217: identitas = user yang menyinkronkan (env.uid, pola E-41), bukan `user_id` payload.
            sync_user = self.env.user
            self.sudo()._efn_expiry_after_sync(sync_user)
        return result

    def _efn_expiry_eval_time(self, now=None):
        """SEC-217: (waktu penilaian, skew). ``date_order`` dipakai bila di dalam jendela
        ``[session.start_at, now + toleransi]`` (``start_at`` ditulis server); di luar itu jam perangkat dianggap tidak
        tepercaya -> nilai dengan waktu server dan catat ``clock_skew``."""
        self.ensure_one()
        now = now or fields.Datetime.now()
        start = self.session_id.start_at
        order_at = self.date_order
        tolerance = timedelta(minutes=CLOCK_SKEW_MINUTES)
        if not order_at or (start and order_at < start - tolerance) or order_at > now + tolerance:
            return now, True
        return order_at, False

    def _efn_expiry_after_sync(self, sync_user=None):
        self.ensure_one()
        sync_user = sync_user or self.env.user
        Event = self.env["efn.expiry.event"]
        warehouse = self.config_id.picking_type_id.warehouse_id
        eval_at, skew = self._efn_expiry_eval_time()
        order_day = fields.Datetime.context_timestamp(self, eval_at).date()
        if skew and self.lines.filtered(lambda line: not line.efn_expiry_checked):
            Event._efn_log(
                company_id=self.company_id.id,
                kind="clock_skew",
                warehouse_id=warehouse.id,
                pos_order_id=self.id,
                config_id=self.config_id.id,
                user_id=sync_user.id,
                note=f"date_order={self.date_order} session_start={self.session_id.start_at}",
            )
        for line in self.lines.filtered(lambda line: not line.efn_expiry_checked):
            product = line.product_id
            if line.qty < 0:
                # QA G45 + SEC-222: kuota markdown dipulihkan HANYA dari baris asal (`refunded_orderline_id`) yang
                # tercatat sah server; klaim `efn_markdown_ref` di baris retur diabaikan.
                self._efn_markdown_refund(line)
            names = [name for name in line.pack_lot_ids.mapped("lot_name") if name]
            if product.tracking != "lot" or not names:
                line.efn_expiry_checked = True
                continue
            lots = self.env["stock.lot"].search(
                [
                    ("name", "in", names),
                    ("product_id", "=", product.id),
                    "|",
                    ("company_id", "=", False),
                    ("company_id", "=", self.company_id.id),
                ]
            )
            base = {
                "company_id": self.company_id.id,
                "warehouse_id": warehouse.id,
                "product_id": product.id,
                "pos_order_id": self.id,
                "config_id": self.config_id.id,
                "user_id": sync_user.id,
            }
            if line.qty > 0:
                for lot in lots.filtered(lambda lot: lot_blocked(lot, eval_at)):
                    approver = (
                        self._efn_expiry_override_approver(line, lot, sync_user) if line.efn_expiry_override else None
                    )
                    Event._efn_log(
                        **base,
                        kind="pos_expired_override" if approver else "pos_expired_no_override",
                        lot_id=lot.id,
                        qty=line.qty,
                        approver_id=approver.id if approver else False,
                    )
            if line.efn_markdown_ref and line.qty > 0:
                self._efn_markdown_settle(line, lots, warehouse, order_day, base)
            line.efn_expiry_checked = True

    def _efn_markdown_refund(self, line):
        """Retur baris markdown sah: kurangi ``qty_sold`` (tidak di bawah 0), tanpa cek periode (retur boleh sesudah
        markdown berakhir)."""
        origin_line = line.refunded_orderline_id
        origin = origin_line.efn_markdown_id
        if not origin or origin.company_id != self.company_id:
            return
        # SEC-222: dibatasi qty baris asal (retur tidak bisa membuka kuota lebih dari yang terjual di baris itu).
        returned = min(abs(line.qty), max(origin_line.qty, 0.0))
        touch_row(self.env.cr, "efn_lot_markdown", origin.id)
        origin.invalidate_recordset(["qty_sold"])
        origin.sudo().write({"qty_sold": max(origin.qty_sold - returned, 0.0)})
        line.efn_markdown_id = origin

    def _efn_expiry_override_approver(self, line, lot, sync_user):
        """Penyetuju sah override lot kedaluwarsa untuk baris ini, atau None. SEC-217: kasir = ``sync_user``
        (env.uid saat sync) atau persetujuan PIN tersimpan server untuk kasir itu; ``order.user_id`` payload diabaikan."""
        if sync_user and sync_user.has_group(GROUP_OVERRIDE):
            return sync_user
        approval = self.env["efn.expiry.pos.approval"]._efn_find_valid(self, line.product_id, lot.name, sync_user)
        if not approval:
            return None
        if approval.state == "approved":
            approval.sudo().write({"state": "consumed", "consumed_order_id": self.id})
        return approval.approver_user_id

    def _efn_markdown_settle(self, line, lots, warehouse, order_day, base):
        Event = self.env["efn.expiry.event"]
        markdown = self.env["efn.lot.markdown"].sudo().browse(line.efn_markdown_ref).exists()
        valid = bool(
            markdown
            and markdown.company_id == self.company_id
            and markdown.warehouse_id == warehouse
            and markdown.product_id == line.product_id
            and markdown.lot_id in lots
            and markdown.state in ("active", "expired")
            and markdown.date_from <= order_day <= markdown.date_to
            and line.efn_lot_source in EXPLICIT_SOURCES
        )
        if not valid:
            Event._efn_log(
                **base,
                kind="markdown_invalid",
                lot_id=lots[:1].id,
                qty=line.qty,
                markdown_id=markdown.id if markdown else False,
                note=f"ref={line.efn_markdown_ref} source={line.efn_lot_source or '-'}",
            )
            return
        touch_row(self.env.cr, "efn_lot_markdown", markdown.id)
        markdown.invalidate_recordset(["qty_sold"])
        qty = line.qty
        if markdown.qty_cap > 0:
            counted = max(min(qty, markdown.qty_cap - markdown.qty_sold), 0.0)
        else:
            counted = qty
        markdown.write({"qty_sold": markdown.qty_sold + counted})
        line.efn_markdown_id = markdown
        excess = qty - counted
        if excess > 1e-9:
            Event._efn_log(**base, kind="markdown_cap_exceeded", lot_id=markdown.lot_id.id, qty=excess,
                           markdown_id=markdown.id)
        currency = self.currency_id
        effective_unit = line.price_unit * (1.0 - (line.discount or 0.0) / 100.0)
        if markdown.price:
            too_low = currency.compare_amounts(effective_unit, markdown.price) < 0
        else:
            # SEC-222: persen dinilai terhadap harga jual normal (basis sama dengan cabang `price`), sehingga
            # `price_unit` yang diturunkan manual + diskon sah juga terdeteksi. Pricelist di bawah harga normal ikut
            # tercatat (kontrol detektif; order tidak ditolak).
            expected = line.product_id.lst_price * (1.0 - markdown.discount_pct / 100.0)
            too_low = (line.discount or 0.0) > markdown.discount_pct + 1e-6 or currency.compare_amounts(
                effective_unit, expected
            ) < 0
        if too_low:
            Event._efn_log(
                **base,
                kind="markdown_price_mismatch",
                lot_id=markdown.lot_id.id,
                qty=qty,
                markdown_id=markdown.id,
                note=f"unit={effective_unit:.2f}",
            )
