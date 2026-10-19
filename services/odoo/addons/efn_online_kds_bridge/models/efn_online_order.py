"""Jembatan pesanan online F&B -> KDS (E-29a; docs/04 §14.B B-6/B-7, docs/08 §10.6 + §13.2 #1-#5).

- **Pemicu** = ``efn.online.order._efn_after_confirmed`` (bagian ``confirmed`` = sudah dibayar; termasuk pelunasan di
  kasir E-45 dan QRIS). Satu kali per pesanan (idempoten: tiket yang sudah ada -> tidak dibuat lagi).
- **Stasiun** = ``efn.kds.station`` mode ``kds``/``kds_printer`` milik company pesanan yang ``config_ids`` memuat POS
  outlet: ``efn.online.outlet.kds_config_id`` (override opsional) atau semua ``pos.config`` dengan
  ``picking_type_id.warehouse_id`` = gudang outlet (pola E-45). Rute per baris = ``pos.order._efn_kds_route`` (kategori
  POS produk + induk ∩ kategori stasiun; stasiun "semua" selalu) — dipakai ulang, tidak ditulis ulang.
- **Baris dapur** = baris SO produk non-jasa dengan qty > 0 (reward diskon/jasa/uang muka tidak; reward produk gratis
  ya). Modifier = nama PTAV ``no_variant`` (sama dengan tiket POS). Catatan pelanggan TIDAK ada (minimisasi PDP).
- **Label** = nomor SO pendek di ``tracking_number``; ``preset_name`` "Online - ambil". DILARANG kode ambil/bayar
  (L-50/L-56) dan data pelanggan (diuji).
- **Siap**: semua tiket aktif (bukan batal) pesanan ``ready``/``served`` -> ``_efn_set_ready("kds")`` -> event ``ready``
  (``ready_source: "kds"``). Tiket pertama mulai dikerjakan -> ``kitchen_started_at`` + event opsional ``preparing``.
- **Batal / refund penuh** -> tiket aktif ``cancelled``; **refund per baris** sebelum siap -> tiket pengurangan (qty
  negatif) di stasiun yang pernah menerima produk itu (pola baris dihapus E-26).
- Kegagalan membuat tiket TIDAK membatalkan pembayaran: savepoint + log alert ``EFN_KDS_TICKET_FAILED`` (tanpa PII,
  rule Loki yang sama dengan E-26 SEC-76) + aktivitas staf outlet ("siap" manual tetap tersedia).
"""

import json
import logging

import psycopg2.errors

from odoo import _, models
from odoo.exceptions import ConcurrencyError
from odoo.fields import Command
from odoo.service.model import PG_CONCURRENCY_EXCEPTIONS_TO_RETRY

from odoo.addons.efn_pos_kds.models.pos_order import TICKET_MODES

_logger = logging.getLogger(__name__)
# Logger alert yang sama dengan efn_pos_kds (rule Loki `|= "EFN_KDS_TICKET_FAILED"`).
_alert_logger = logging.getLogger("odoo.addons.efn_pos_kds.alert")

LABEL_MAX = 12
ACTIVE_TICKET_STATES = ("new", "in_progress", "ready")


class EfnOnlineOrder(models.Model):
    _inherit = "efn.online.order"

    # ------------------------------------------------------------------ hook
    def _efn_after_confirmed(self):
        result = super()._efn_after_confirmed()
        for order in self:
            order._efn_kds_create_tickets_safe()
        return result

    def _efn_after_cancelled(self):
        result = super()._efn_after_cancelled()
        self._efn_kds_cancel_tickets()
        return result

    def _efn_after_refund(self, refund):
        result = super()._efn_after_refund(refund)
        for order in self:
            if refund.kind == "full":
                order._efn_kds_cancel_tickets()
            elif order.state == "confirmed":
                order._efn_kds_refund_deltas(refund)
        return result

    # ------------------------------------------------------------- stasiun
    def _efn_kds_configs(self):
        """POS outlet: override ``kds_config_id`` atau config POS company yang gudangnya = gudang outlet."""
        self.ensure_one()
        outlet = self.outlet_id.sudo()
        if outlet.kds_config_id:
            return outlet.kds_config_id
        return (
            self.env["pos.config"]
            .sudo()
            .search(
                [
                    ("company_id", "=", self.company_id.id),
                    ("picking_type_id.warehouse_id", "=", outlet.warehouse_id.id),
                ]
            )
        )

    def _efn_kds_stations(self):
        self.ensure_one()
        configs = self._efn_kds_configs()
        if not configs:
            return self.env["efn.kds.station"]
        return (
            self.env["efn.kds.station"]
            .sudo()
            .search(
                [
                    ("config_ids", "in", configs.ids),
                    ("company_id", "=", self.company_id.id),
                    ("mode", "in", TICKET_MODES),
                ]
            )
        )

    # -------------------------------------------------------------- tiket
    def _efn_kds_label(self):
        """Nomor SO pendek (tanpa kode ambil/bayar, tanpa data pelanggan)."""
        self.ensure_one()
        name = self.sudo().sale_order_id.name or self.name or ""
        return name[-LABEL_MAX:]

    def _efn_kds_kitchen_lines(self):
        self.ensure_one()

        def is_kitchen(line):
            product = line.product_id
            if line.display_type or not product or line.product_uom_qty <= 0:
                return False
            if product.type != "consu" or getattr(line, "is_downpayment", False):
                return False
            reward = line.reward_id if "reward_id" in line._fields else False
            return not reward or reward.reward_type == "product"

        return self.sudo().sale_order_id.order_line.filtered(is_kitchen)

    def _efn_kds_line_vals(self, line, qty):
        return {
            "order_line_uuid": f"sol:{line.id}",
            "product_id": line.product_id.id,
            "product_name": (line.product_id.display_name or "")[:200],
            "qty_delta": qty,
            "attribute_text": ", ".join(line.product_no_variant_attribute_value_ids.mapped("name"))[:500],
            "note": "",
            "combo_parent_name": "",
        }

    def _efn_kds_create_tickets_safe(self):
        """Buat tiket di savepoint; galat (selain konkurensi) -> alert + aktivitas, pembayaran tetap jalan."""
        self.ensure_one()
        try:
            with self.env.cr.savepoint():
                self._efn_kds_create_tickets()
        except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
            raise
        except psycopg2.errors.UniqueViolation as exc:
            raise ConcurrencyError("EFN KDS online: konflik nomor tiket, diulang") from exc
        except Exception as exc:  # noqa: BLE001 - KDS di luar jalur transaksi kas (SEC-76)
            self._efn_kds_alert(exc)

    def _efn_kds_alert(self, exc):
        event = {
            "event": "EFN_KDS_TICKET_FAILED",
            "source": "online",
            "online_order_id": self.id,
            "company_id": self.company_id.id or None,
            "error": type(exc).__name__,
        }
        _alert_logger.error("EFN_KDS_TICKET_FAILED %s", json.dumps(event, sort_keys=True))
        _logger.debug("EFN KDS online: detail kegagalan tiket pesanan #%s", self.id, exc_info=exc)
        self._efn_schedule_activity(
            _("Tiket dapur pesanan online gagal dibuat"), "kds_ticket_failed", "stock.group_stock_user"
        )

    def _efn_kds_create_tickets(self):
        """Tiket per stasiun untuk pesanan ``confirmed`` (sekali). Kembalikan tiket yang dibuat."""
        self.ensure_one()
        Ticket = self.env["efn.kds.ticket"].sudo()
        if Ticket.search_count([("efn_online_order_id", "=", self.id)], limit=1):
            return Ticket
        stations = self._efn_kds_stations()
        if not stations:
            _logger.info("EFN KDS online: pesanan #%s tanpa stasiun KDS (siap manual)", self.id)
            return Ticket
        PosOrder = self.env["pos.order"]
        deltas = []
        for line in self._efn_kds_kitchen_lines():
            targets = PosOrder._efn_kds_route(line.product_id, stations)
            if not targets:
                _logger.info("EFN KDS online: produk #%s pesanan #%s tanpa stasiun", line.product_id.id, self.id)
                continue
            for station in targets:
                deltas.append((self._efn_kds_line_vals(line, line.product_uom_qty), station))
        return self._efn_kds_create(deltas)

    def _efn_kds_create(self, deltas):
        """``deltas`` = [(vals baris, stasiun)] -> satu tiket per stasiun; ``sequence_no`` urut per (pesanan, stasiun).
        Pesanan dikunci ``_efn_lock`` oleh pemanggil saga/refund -> pembuatan berurutan."""
        self.ensure_one()
        Ticket = self.env["efn.kds.ticket"].sudo()
        groups = {}
        for vals, station in deltas:
            groups.setdefault(station, []).append(vals)
        if not groups:
            return Ticket
        Ticket.flush_model()
        self.env.cr.execute(
            "SELECT station_id, COALESCE(MAX(sequence_no), 0) FROM efn_kds_ticket "
            "WHERE efn_online_order_id = %s GROUP BY station_id",
            [self.id],
        )
        last_seq = dict(self.env.cr.fetchall())
        label = self._efn_kds_label()
        vals_list = []
        for station in sorted(groups, key=lambda s: s.id):
            last_seq[station.id] = last_seq.get(station.id, 0) + 1
            vals_list.append(
                {
                    "company_id": self.company_id.id,
                    "station_id": station.id,
                    "efn_online_order_id": self.id,
                    "table_label": "",
                    "tracking_number": label,
                    "preset_name": _("Online - ambil"),
                    "course_index": 0,
                    "internal_note": "",
                    "sequence_no": last_seq[station.id],
                    "line_ids": [Command.create(vals) for vals in groups[station]],
                }
            )
        tickets = Ticket.create(vals_list)
        for ticket in tickets:
            ticket.station_id._efn_kds_notify("new", ticket.ids)
        _logger.info("EFN KDS online: %d tiket untuk pesanan #%s", len(tickets), self.id)
        return tickets

    def _efn_kds_tickets(self, states=None):
        domain = [("efn_online_order_id", "in", self.ids)]
        if states:
            domain.append(("state", "in", states))
        return self.env["efn.kds.ticket"].sudo().search(domain)

    def _efn_kds_cancel_tickets(self):
        tickets = self._efn_kds_tickets(ACTIVE_TICKET_STATES)
        if tickets:
            tickets._efn_kds_set_state("cancelled")
            tickets._efn_kds_after_change(kind="cancel")
        return tickets

    def _efn_kds_refund_deltas(self, refund):
        """Refund per baris sebelum siap: tiket pengurangan (qty negatif) di setiap stasiun yang pernah menerima produk
        itu, sebesar min(qty refund, qty terkirim ke stasiun itu)."""
        self.ensure_one()
        requested = {int(pid): qty for pid, qty in json.loads(refund.line_summary or "{}").items() if qty > 0}
        if not requested:
            return self.env["efn.kds.ticket"]
        self.env["efn.kds.ticket.line"].flush_model()
        self.env.cr.execute(
            """
            SELECT l.product_id, t.station_id, SUM(l.qty_delta), MAX(l.product_name)
              FROM efn_kds_ticket_line l
              JOIN efn_kds_ticket t ON t.id = l.ticket_id
             WHERE t.efn_online_order_id = %s AND t.state != 'cancelled' AND l.product_id = ANY(%s)
          GROUP BY l.product_id, t.station_id
            """,
            [self.id, list(requested)],
        )
        Station = self.env["efn.kds.station"].sudo()
        deltas = []
        for product_id, station_id, sent, product_name in self.env.cr.fetchall():
            qty = min(requested[product_id], sent or 0.0)
            if qty <= 0:
                continue
            vals = {
                "order_line_uuid": f"refund:{refund.refund_ref}:{product_id}",
                "product_id": product_id,
                "product_name": product_name or "",
                "qty_delta": -qty,
                "attribute_text": "",
                "note": "",
                "combo_parent_name": "",
            }
            deltas.append((vals, Station.browse(station_id)))
        return self._efn_kds_create(deltas)

    # ------------------------------------------------------- tiket -> pesanan
    def _efn_kds_sync_from_tickets(self):
        """Dipanggil setelah perubahan status tiket (sudo). Hanya pesanan ``confirmed`` yang berubah:
        tiket pertama dikerjakan -> ``preparing``; semua tiket aktif siap/disajikan -> ``ready`` (sumber ``kds``)."""
        for order in self.sudo():
            order._efn_lock()
            if order.state != "confirmed":
                continue
            tickets = order._efn_kds_tickets(("new", "in_progress", "ready", "served"))
            if not tickets:
                continue
            started = [ticket.started_at for ticket in tickets if ticket.started_at]
            if started:
                order._efn_set_kitchen_started(min(started))
            if all(ticket.state in ("ready", "served") for ticket in tickets):
                order._efn_set_ready("kds")
                _logger.info("EFN KDS online: pesanan #%s siap (semua tiket dapur)", order.id)
