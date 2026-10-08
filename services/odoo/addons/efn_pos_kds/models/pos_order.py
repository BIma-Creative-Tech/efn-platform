"""Hook pembuatan tiket KDS di server — satu tempat (docs/08 §7.3).

Pemicu = frontend standar menandai "dikirim ke dapur": isi ``last_order_preparation_change``
(tanpa ``metadata``, yang diperbarui server tiap sinkron) berubah dibanding sebelum sinkron —
sama dengan pemicu cetak dapur standar — ATAU course (``restaurant.order.course``) baru di-*fire*.
``last_order_preparation_change``/``updateLastOrderChange`` TIDAK diubah.

Kuantitas dihitung server dari baris DB: ``delta = qty − terkirim``; "terkirim" = jumlah
``qty_delta`` tiket per ``order_line_uuid`` (otoritatif, juga untuk baris yang sudah dihapus),
dicerminkan ke ``pos.order.line.efn_kds_sent_qty``. Atribut diambil dari ``attribute_value_ids``
server, bukan teks klien. Hook dipasang di ``_process_order`` (pemanggil tunggal
``_process_saved_order`` saat sinkron, baik order baru maupun lama) karena nilai sebelum write
diperlukan; ``sync_from_ui`` (POS & kiosk self-order) melewatinya.
"""

import json
import logging

import psycopg2.errors

from odoo import api, fields, models
from odoo.exceptions import ConcurrencyError
from odoo.service.model import PG_CONCURRENCY_EXCEPTIONS_TO_RETRY
from odoo.tools import float_compare, float_is_zero

from .efn_kds_ticket import note_text

_logger = logging.getLogger(__name__)
# SEC-76: logger khusus alert (rule Loki: `|= "EFN_KDS_TICKET_FAILED"`). Satu baris JSON tanpa PII.
_alert_logger = logging.getLogger("odoo.addons.efn_pos_kds.alert")

TICKET_MODES = ("kds", "kds_printer")


def prep_signature(raw):
    """Isi preparation change tanpa ``metadata`` (stempel waktu server) sebagai string kanonik."""
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    data.pop("metadata", None)
    return json.dumps(data, sort_keys=True, default=str)


def prep_line_uuids(raw):
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return set()
    lines = data.get("lines") if isinstance(data, dict) else None
    if not isinstance(lines, dict):
        return set()
    return {value.get("uuid") for value in lines.values() if isinstance(value, dict) and value.get("uuid")}


class PosOrder(models.Model):
    _inherit = "pos.order"

    # Diset frontend POS saat dapur sudah menerima cetakan cadangan (POS offline / KDS mati);
    # dikosongkan server setelah tiket dibuat (tiket langsung "dimasak", label "sudah dicetak").
    efn_kds_printed_offline = fields.Boolean(copy=False)
    # SEC-76: pembuatan tiket KDS terakhir untuk order ini GAGAL. Hanya server yang mengisi (nilai
    # dari klien dibuang); dikirim balik ke POS lewat respons sinkron -> POS mencetak perubahan yang
    # tadinya ditahan (stasiun mode `kds`) ke printer cadangan + notifikasi kasir. Dikosongkan saat
    # pembuatan tiket berikutnya berhasil.
    efn_kds_ticket_failed = fields.Boolean(copy=False, readonly=True)

    @api.model
    def _process_order(self, order, existing_order):
        if isinstance(order, dict):
            order.pop("efn_kds_ticket_failed", None)  # milik server, bukan klien
        before_sig = prep_signature(existing_order.last_order_preparation_change) if existing_order else ""
        before_fired = set(existing_order.course_ids.filtered("fired").ids) if existing_order else set()
        order_id = super()._process_order(order, existing_order)
        pos_order = self.browse(order_id)
        try:
            attempted = pos_order._efn_kds_after_sync(before_sig, before_fired)
        except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
            # Konflik serialisasi/lock: biarkan naik -> Odoo me-retry seluruh request sinkron.
            raise
        except psycopg2.errors.UniqueViolation as exc:
            # Unik (order, station, sequence_no) = dua sinkron paralel order yang sama -> retry.
            raise ConcurrencyError("EFN KDS: konflik nomor tiket, sinkron diulang") from exc
        except Exception as exc:  # noqa: BLE001 - KDS di luar jalur transaksi kas (lihat bawah)
            # Kegagalan tiket tidak boleh menggagalkan sinkron order (printer dapur tetap jalan);
            # savepoint menjaga transaksi sehat. TIDAK ditelan diam-diam: alert + tanda order.
            pos_order._efn_kds_mark_ticket_failed(exc)
        else:
            if attempted and pos_order.efn_kds_ticket_failed:
                pos_order.sudo().efn_kds_ticket_failed = False
        return order_id

    def _efn_kds_mark_ticket_failed(self, exc):
        """Log alert terstruktur TANPA PII (id + nama tipe exception) + tandai order (SEC-76)."""
        self.ensure_one()
        try:
            stations = self._efn_kds_stations()
            station_info = [{"id": s.id, "mode": s.mode} for s in stations]
        except Exception:  # noqa: BLE001 - log alert tetap harus keluar
            station_info = []
        event = {
            "event": "EFN_KDS_TICKET_FAILED",
            "order_id": self.id,
            "config_id": self.config_id.id or None,
            "session_id": self.session_id.id or None,
            "company_id": self.company_id.id or None,
            "stations": station_info,
            "kds_only": any(s["mode"] == "kds" for s in station_info),
            "error": type(exc).__name__,
        }
        _alert_logger.error("EFN_KDS_TICKET_FAILED %s", json.dumps(event, sort_keys=True))
        # Traceback hanya level DEBUG (pesan exception bisa memuat teks catatan dapur).
        _logger.debug("EFN KDS: detail kegagalan tiket pos.order #%s", self.id, exc_info=exc)
        self.sudo().efn_kds_ticket_failed = True

    # ------------------------------------------------------------------ routing
    def _efn_kds_stations(self):
        self.ensure_one()
        if not self.config_id:
            return self.env["efn.kds.station"]
        return (
            self.env["efn.kds.station"]
            .sudo()
            .search(
                [
                    ("config_ids", "in", self.config_id.id),
                    ("company_id", "=", self.company_id.id),
                    ("mode", "in", TICKET_MODES),
                ]
            )
        )

    @api.model
    def _efn_kds_route(self, product, stations):
        """Stasiun tujuan: kategori POS produk (+induk) ∩ kategori stasiun; stasiun 'semua' selalu."""
        categ_ids = set()
        for categ in product.pos_categ_ids:
            while categ:
                categ_ids.add(categ.id)
                categ = categ.parent_id
        catch_all = stations.filtered(lambda s: not s.category_ids)
        specific = stations.filtered(lambda s: s.category_ids and set(s.category_ids.ids) & categ_ids)
        return specific | catch_all

    def _efn_kds_after_sync(self, before_sig, before_fired):
        """Buat tiket bila ada pengiriman dapur baru. True = pembuatan tiket dicoba (dan berhasil)."""
        self.ensure_one()
        stations = self._efn_kds_stations()
        if not stations:
            return False
        sig_changed = prep_signature(self.last_order_preparation_change) != before_sig
        newly_fired = self.course_ids.filtered(lambda c: c.fired and c.id not in before_fired)
        if not sig_changed and not newly_fired:
            return False
        with self.env.cr.savepoint():
            self.sudo()._efn_kds_make_tickets(stations, sig_changed, newly_fired)
        return True

    def _efn_kds_sent_by_uuid(self):
        self.env["efn.kds.ticket.line"].flush_model()
        self.env.cr.execute(
            """
            SELECT l.order_line_uuid, t.station_id, SUM(l.qty_delta)
              FROM efn_kds_ticket_line l
              JOIN efn_kds_ticket t ON t.id = l.ticket_id
             WHERE t.order_id = %s
          GROUP BY l.order_line_uuid, t.station_id
            """,
            [self.id],
        )
        sent = {}
        for line_uuid, station_id, qty in self.env.cr.fetchall():
            sent[(line_uuid, station_id)] = qty or 0.0
        return sent

    def _efn_kds_make_tickets(self, stations, sig_changed, newly_fired):
        precision = self.env["decimal.precision"].precision_get("Product Unit")
        sent = self._efn_kds_sent_by_uuid()
        prep_uuids = prep_line_uuids(self.last_order_preparation_change)
        deltas = []
        live_uuids = set()
        for line in self.lines:
            live_uuids.add(line.uuid)
            if line.combo_line_ids:
                continue  # induk combo tidak ditiket; anak dirutekan sesuai kategorinya
            course = line.course_id
            if course and not course.fired:
                continue  # course belum di-fire tidak dikirim ke dapur
            if not sig_changed and course not in newly_fired:
                continue
            if sig_changed and course not in newly_fired and line.uuid not in prep_uuids:
                continue  # baris belum dikirim kasir (belum ada di preparation change)
            targets = self._efn_kds_route(line.product_id, stations)
            if not targets:
                _logger.info("EFN KDS: produk #%s pada order #%s tanpa stasiun", line.product_id.id, self.id)
                continue
            for station in targets:
                delta = line.qty - sent.get((line.uuid, station.id), 0.0)
                if float_is_zero(delta, precision_digits=precision):
                    continue
                deltas.append((self._efn_kds_line_vals(line, delta), station, course.index if course else 0))
            line.efn_kds_sent_qty = line.qty
        if sig_changed:
            # Baris yang dihapus setelah dikirim -> tiket pembatalan (qty negatif).
            for (line_uuid, station_id), qty in sent.items():
                if line_uuid in live_uuids or float_compare(qty, 0.0, precision_digits=precision) <= 0:
                    continue
                station = stations.filtered(lambda s, sid=station_id: s.id == sid)
                if not station:
                    continue
                last = self.env["efn.kds.ticket.line"].search(
                    [("order_line_uuid", "=", line_uuid), ("ticket_id.order_id", "=", self.id)],
                    limit=1,
                    order="id desc",
                )
                deltas.append(
                    (
                        {
                            "order_line_uuid": line_uuid,
                            "product_id": last.product_id.id,
                            "product_name": last.product_name,
                            "qty_delta": -qty,
                            "attribute_text": last.attribute_text,
                            "note": last.note,
                            "combo_parent_name": last.combo_parent_name,
                        },
                        station,
                        last.ticket_id.course_index,
                    )
                )
        if not deltas:
            return
        self.env["efn.kds.ticket"]._efn_kds_create_for_order(self, deltas, printed_offline=self.efn_kds_printed_offline)
        if self.efn_kds_printed_offline:
            self.efn_kds_printed_offline = False

    @api.model
    def _efn_kds_line_vals(self, line, delta):
        attributes = ", ".join(line.attribute_value_ids.mapped("name"))
        return {
            "order_line_uuid": line.uuid,
            "product_id": line.product_id.id,
            "product_name": (line.product_id.display_name or "")[:200],
            "qty_delta": delta,
            "attribute_text": attributes[:500],
            "note": note_text(line.note),
            "combo_parent_name": line.combo_parent_id.product_id.display_name or "",
        }

    # ------------------------------------------------------------------ pembatalan
    def action_pos_order_cancel(self):
        result = super().action_pos_order_cancel()
        self._efn_kds_cancel_tickets()
        return result

    def _efn_kds_cancel_tickets(self):
        tickets = (
            self.env["efn.kds.ticket"]
            .sudo()
            .search([("order_id", "in", self.ids), ("state", "in", ("new", "in_progress", "ready"))])
        )
        if tickets:
            tickets._efn_kds_set_state("cancelled")
            tickets._efn_kds_after_change(kind="cancel")


class PosOrderLine(models.Model):
    _inherit = "pos.order.line"

    # Qty kumulatif yang sudah ditiket (cermin; otoritatif = jumlah qty_delta tiket). Tidak
    # dimuat ke POS (field baris dimuat eksplisit) -> klien tidak dapat mengubahnya.
    efn_kds_sent_qty = fields.Float(digits="Product Unit", copy=False, readonly=True)
