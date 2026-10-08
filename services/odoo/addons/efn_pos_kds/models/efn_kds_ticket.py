"""Tiket KDS per (order, stasiun, pengiriman) — docs/08 §7.3 E-26 (F-52, F-54).

Status hidup di tiket per stasiun, BUKAN di ``pos.order`` (pola Cybrosys tidak ditiru). Tiket
tidak menyimpan data pelanggan: tanpa partner, nama/HP/alamat pelanggan, ``floating_order_name``,
``general_customer_note``, ``customer_note`` baris, harga, atau pembayaran (UU PDP: minimisasi).
Cap waktu per status menjadi dasar KPI waktu tunggu/SLA lewat ETL (bukan laporan Odoo).
"""

import json
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .efn_kds_station import ACTIVE_STATES

_logger = logging.getLogger(__name__)

STATES = [
    ("new", "Baru"),
    ("in_progress", "Dimasak"),
    ("ready", "Siap"),
    ("served", "Disajikan"),
    ("cancelled", "Batal"),
]
FORWARD = ("new", "in_progress", "ready", "served")
DEFAULT_RECALL_MINUTES = 10  # PLACEHOLDER (docs/08 §7.3)
DEFAULT_RETENTION_DAYS = 90  # PLACEHOLDER (docs/08 §7.3)
NOTE_MAX = 500


def note_text(raw):
    """``internal_note``/``note`` POS 19 bisa JSON list ``[{"text": ...}]`` atau teks biasa."""
    if not raw:
        return ""
    text = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = None
        if isinstance(parsed, list):
            text = ", ".join(str(n.get("text", "")) if isinstance(n, dict) else str(n) for n in parsed)
    return str(text).strip()[:NOTE_MAX]


class EfnKdsTicket(models.Model):
    _name = "efn.kds.ticket"
    _description = "EFN KDS Ticket"
    _order = "sent_at desc, id desc"
    _check_company_auto = True
    _rec_name = "display_label"

    company_id = fields.Many2one("res.company", required=True, index=True, readonly=True)
    station_id = fields.Many2one(
        "efn.kds.station", required=True, index=True, ondelete="cascade", readonly=True, check_company=True
    )
    # Relasi ke order hanya untuk user POS/manager (perangkat dapur tidak bisa membaca order).
    order_id = fields.Many2one(
        "pos.order",
        required=True,
        index=True,
        ondelete="cascade",
        readonly=True,
        groups="point_of_sale.group_pos_user",
    )
    config_id = fields.Many2one("pos.config", readonly=True, groups="point_of_sale.group_pos_user")
    session_id = fields.Many2one("pos.session", readonly=True, groups="point_of_sale.group_pos_user")
    table_label = fields.Char(readonly=True)
    tracking_number = fields.Char(readonly=True)
    preset_name = fields.Char(readonly=True)
    course_index = fields.Integer(readonly=True)
    state = fields.Selection(STATES, required=True, default="new", index=True, readonly=True)
    is_rush = fields.Boolean()
    printed_offline = fields.Boolean(readonly=True, help="Sudah dicetak di printer cadangan (POS offline / KDS mati).")
    internal_note = fields.Char(readonly=True)
    sequence_no = fields.Integer(required=True, readonly=True)
    sent_at = fields.Datetime(required=True, readonly=True, default=fields.Datetime.now)
    started_at = fields.Datetime(readonly=True)
    ready_at = fields.Datetime(readonly=True)
    served_at = fields.Datetime(readonly=True)
    cancelled_at = fields.Datetime(readonly=True)
    wait_seconds = fields.Integer(readonly=True, help="Detik dari terkirim sampai siap (KPI F-54).")
    line_ids = fields.One2many("efn.kds.ticket.line", "ticket_id", readonly=True)
    display_label = fields.Char(compute="_compute_display_label")

    _order_station_seq_uniq = models.Constraint(
        "UNIQUE (order_id, station_id, sequence_no)",
        "Tiket KDS ganda untuk pengiriman yang sama.",
    )

    @api.depends("table_label", "tracking_number", "sequence_no")
    def _compute_display_label(self):
        for ticket in self:
            ticket.display_label = f"{ticket.table_label or ticket.tracking_number or '-'} #{ticket.sequence_no}"

    # ------------------------------------------------------------------ param
    @api.model
    def _efn_param_int(self, key, default):
        try:
            value = int(self.env["ir.config_parameter"].sudo().get_param(key, default))
        except (TypeError, ValueError):
            value = default
        return value if value > 0 else default

    @api.model
    def _efn_recall_minutes(self):
        return self._efn_param_int("efn.kds.recall_minutes", DEFAULT_RECALL_MINUTES)

    # ------------------------------------------------------------------ payload
    def _efn_kds_payload(self):
        """Isi tiket untuk layar KDS. Daftar kunci tetap (diuji: tanpa data pelanggan)."""
        self.ensure_one()
        return {
            "id": self.id,
            "state": self.state,
            "table_label": self.table_label or "",
            "tracking_number": self.tracking_number or "",
            "preset_name": self.preset_name or "",
            "course_index": self.course_index,
            "is_rush": self.is_rush,
            "printed_offline": self.printed_offline,
            "internal_note": self.internal_note or "",
            "sequence_no": self.sequence_no,
            "sent_at": fields.Datetime.to_string(self.sent_at),
            "ready_at": fields.Datetime.to_string(self.ready_at) if self.ready_at else False,
            "served_at": fields.Datetime.to_string(self.served_at) if self.served_at else False,
            "lines": [
                {
                    "id": line.id,
                    "product_name": line.product_name,
                    "qty_delta": line.qty_delta,
                    "attribute_text": line.attribute_text or "",
                    "note": line.note or "",
                    "combo_parent_name": line.combo_parent_name or "",
                    "state": line.state,
                }
                for line in self.line_ids
            ],
        }

    # ------------------------------------------------------------------ akses RPC
    def _efn_kds_check_ticket_access(self):
        for station in self.sudo().station_id:
            station.with_env(self.env)._efn_kds_check_device()
        if not self.env.su:
            try:
                self.check_access("read")
            except AccessError:
                raise AccessError(_("Anda tidak berhak memakai layar KDS ini.")) from None

    # ------------------------------------------------------------------ transisi
    def _efn_kds_set_state(self, to_state):
        """Tulis transisi + cap waktu (sudah dicek). Kembalikan tiket yang berubah."""
        now = fields.Datetime.now()
        changed = self.browse()
        for ticket in self.sudo():
            if ticket.state == to_state:
                continue
            vals = {"state": to_state}
            if to_state == "in_progress":
                vals["started_at"] = ticket.started_at or now
                vals["ready_at"] = False
                vals["wait_seconds"] = 0
            elif to_state == "ready":
                vals["started_at"] = ticket.started_at or now
                vals["ready_at"] = now
                vals["served_at"] = False
                vals["wait_seconds"] = int((now - ticket.sent_at).total_seconds())
            elif to_state == "served":
                vals["served_at"] = now
            elif to_state == "cancelled":
                vals["cancelled_at"] = now
            ticket.write(vals)
            changed |= ticket
        return changed

    def _efn_kds_after_change(self, kind="update"):
        """Notifikasi stasiun + POS (badge siap) setelah perubahan status."""
        tickets = self.sudo()
        for station in tickets.station_id:
            station._efn_kds_notify(kind, tickets.filtered(lambda t, s=station: t.station_id == s).ids)
        for ticket in tickets.filtered(lambda t: t.state in ("ready", "served")):
            order = ticket.order_id
            if order.config_id:
                # SEC-77: kanal partner kasir, bukan kanal token pos.config (semi-publik bila
                # self-order/kiosk aktif).
                order.config_id._efn_kds_cashier_send(
                    "EFN_KDS_READY",
                    {
                        "order_id": order.id,
                        "table_id": order.table_id.id or False,
                        "ticket_id": ticket.id,
                        "state": ticket.state,
                    },
                )

    def efn_kds_bump(self, to_state):
        """Bump/recall dari layar KDS. Maju: new→in_progress→ready→served (boleh melompat maju).

        Recall: ready→in_progress, served→ready (maks ``efn.kds.recall_minutes`` setelah served).
        """
        if to_state not in FORWARD:
            raise UserError(_("Status KDS tidak dikenal."))
        self._efn_kds_check_ticket_access()
        now = fields.Datetime.now()
        recall_limit = timedelta(minutes=self._efn_recall_minutes())
        for ticket in self.sudo():
            current = ticket.state
            if current == "cancelled":
                raise UserError(_("Tiket sudah dibatalkan."))
            if current == to_state:
                continue
            forward = FORWARD.index(to_state) > FORWARD.index(current)
            recall = (current, to_state) in (("ready", "in_progress"), ("served", "ready"))
            if not forward and not recall:
                raise UserError(_("Transisi status KDS tidak diizinkan."))
            if (current, to_state) == ("served", "ready") and (
                not ticket.served_at or now - ticket.served_at > recall_limit
            ):
                raise UserError(_("Batas waktu recall sudah lewat."))
        changed = self._efn_kds_set_state(to_state)
        if to_state in ("ready", "served"):
            changed.sudo().line_ids.filtered(lambda line: line.state != "done").write({"state": "done", "done_at": now})
        changed._efn_kds_after_change()
        return [ticket._efn_kds_payload() for ticket in self.sudo()]

    # ------------------------------------------------------------------ pembuatan tiket
    @api.model
    def _efn_kds_create_for_order(self, order, line_deltas, printed_offline=False):
        """Buat tiket dari ``line_deltas`` [(vals_line, station, course_index)] — dipanggil sudo.

        Satu tiket per (stasiun, course) per pengiriman; ``sequence_no`` urut per (order, stasiun).
        Baris pos.order terkunci oleh write sinkronisasi di transaksi ini, sehingga dua sinkron
        paralel untuk order yang sama berurutan (kunci unik tidak bentrok).
        """
        groups = {}
        for line_vals, station, course_index in line_deltas:
            groups.setdefault((station, course_index), []).append(line_vals)
        if not groups:
            return self.browse()
        self.env.cr.execute(
            "SELECT station_id, COALESCE(MAX(sequence_no), 0) FROM efn_kds_ticket "
            "WHERE order_id = %s GROUP BY station_id",
            [order.id],
        )
        last_seq = dict(self.env.cr.fetchall())
        table = order.table_id
        table_label = ""
        if table:
            table_label = f"{table.table_number} / {table.floor_id.name}" if table.floor_id else str(table.table_number)
        vals_list = []
        for (station, course_index), lines in sorted(groups.items(), key=lambda kv: (kv[0][0].id, kv[0][1])):
            last_seq[station.id] = last_seq.get(station.id, 0) + 1
            offline = printed_offline and station.mode == "kds"
            all_cancel = all(line["qty_delta"] < 0 for line in lines)
            vals = {
                "company_id": order.company_id.id,
                "station_id": station.id,
                "order_id": order.id,
                "config_id": order.config_id.id,
                "session_id": order.session_id.id,
                "table_label": table_label,
                "tracking_number": order.tracking_number or "",
                "preset_name": order.preset_id.name or "",
                "course_index": course_index,
                "internal_note": note_text(order.internal_note),
                "sequence_no": last_seq[station.id],
                "printed_offline": offline,
                "line_ids": [fields.Command.create(line) for line in lines],
            }
            if offline and not all_cancel:
                # Dapur sudah memegang cetakan: tiket langsung "dimasak" agar tidak dimasak ganda.
                vals.update({"state": "in_progress", "started_at": fields.Datetime.now()})
            vals_list.append(vals)
        tickets = self.create(vals_list)
        for ticket in tickets:
            station = ticket.station_id
            station._efn_kds_notify("new", ticket.ids)
        return tickets

    # ------------------------------------------------------------------ retensi
    @api.autovacuum
    def _gc_efn_kds_tickets(self):
        days = self._efn_param_int("efn.kds.retention_days", DEFAULT_RETENTION_DAYS)
        limit = fields.Datetime.now() - timedelta(days=days)
        old = self.sudo().with_context(active_test=False).search([("sent_at", "<", limit)], limit=10000)
        if old:
            _logger.info("EFN KDS: autovacuum menghapus %s tiket > %s hari", len(old), days)
            old.unlink()


class EfnKdsTicketLine(models.Model):
    _name = "efn.kds.ticket.line"
    _description = "EFN KDS Ticket Line"
    _order = "ticket_id, id"
    _check_company_auto = True

    ticket_id = fields.Many2one("efn.kds.ticket", required=True, index=True, ondelete="cascade", readonly=True)
    company_id = fields.Many2one(related="ticket_id.company_id", store=True, index=True)
    order_line_uuid = fields.Char(index=True, readonly=True)
    product_id = fields.Many2one("product.product", readonly=True)
    product_name = fields.Char(readonly=True)
    qty_delta = fields.Float(digits="Product Unit", readonly=True, help="Positif = tambah, negatif = batal/kurang.")
    attribute_text = fields.Char(readonly=True)
    note = fields.Char(readonly=True)
    combo_parent_name = fields.Char(readonly=True)
    state = fields.Selection([("todo", "Belum"), ("done", "Selesai")], default="todo", required=True, readonly=True)
    done_at = fields.Datetime(readonly=True)

    def efn_kds_line_done(self):
        """Tandai baris selesai dari layar KDS; semua baris selesai -> tiket ``ready``."""
        tickets = self.sudo().ticket_id
        tickets.with_env(self.env)._efn_kds_check_ticket_access()
        if not self.env.su:
            try:
                self.check_access("read")
            except AccessError:
                raise AccessError(_("Anda tidak berhak memakai layar KDS ini.")) from None
        if any(t.state not in ACTIVE_STATES for t in tickets):
            raise UserError(_("Tiket tidak aktif."))
        now = fields.Datetime.now()
        self.sudo().filtered(lambda line: line.state != "done").write({"state": "done", "done_at": now})
        changed = self.env["efn.kds.ticket"]
        for ticket in tickets:
            if all(line.state == "done" for line in ticket.line_ids):
                changed |= ticket._efn_kds_set_state("ready")
            elif ticket.state == "new":
                changed |= ticket._efn_kds_set_state("in_progress")
        (tickets | changed).sudo()._efn_kds_after_change()
        return [ticket._efn_kds_payload() for ticket in tickets]
