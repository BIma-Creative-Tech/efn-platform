"""Konfigurasi QR ponsel per POS + RPC kasir/pelayan untuk sesi meja (E-27, docs/04 §14.A.4 + §14.7 L-70/L-71).

RPC POS (terdaftar di ``efn_core/tests/test_rpc_surface.py`` ``RPC_ALLOWLIST``, SEC-58):

- ``efn_qr_session_open(table_id, order_uuid, reissue=False)``, dipanggil
  ``[[config_id], table_id, order_uuid, reissue]``.
  Cek ``_efn_qr_check_pos_caller`` (grup POS user + ``check_access('read')`` config + company config ∈ company user +
  sesi POS ``opened`` + ``efn_qr_enabled``), meja ∈ denah config, order draft milik config & meja. Odoo meminta BFF
  ``POST /v1/pos/fnb/table-sessions`` (klien ``efn.bff.client``: HTTPS + CA internal + service key), menyimpan
  **hanya id sesi** BFF, lalu merender QR di server (reportlab, tanpa cache) -> ``{qr_png_b64, expires_at,
  table_label}``. URL bertoken TIDAK disimpan, TIDAK di-log, TIDAK dikembalikan sebagai teks.
- ``efn_qr_session_close(table_id)``: tutup sesi meja ``closed_by_staff`` + event ``session_closed`` ke BFF (outbox).

POS offline: tombol dinonaktifkan di frontend (butuh server, CLAUDE.md). BFF mati -> ``{"error_code":
"bff_unavailable"}`` (POS tetap jalan, pelayan menerima pesanan manual).
"""

import base64
import io
import logging
import re
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.efn_core.models.efn_bff_client import EfnBffError, plaintext_allowed
from odoo.addons.efn_core.scope import touch_row

from .qr_common import ORDER_UUID_RE, UUID_RE

_logger = logging.getLogger(__name__)

POS_USER_GROUP = "point_of_sale.group_pos_user"
TABLE_SESSION_PATH = "/v1/pos/fnb/table-sessions"
QR_COMPANY_CODES = ("PB", "BCF")  # §14.A.4: company_code ∈ {PB, BCF} (business_type fnb)
MAX_SESSION_HOURS = 4  # FNB_TABLE_SESSION_TTL_SECONDS maks 14 400
MAX_PRINT_NO = 99_999_999  # SEC-167: batas n di kunci fnb_open:<config>:<table>:<n> (BFF r15)
# qr_url dari BFF: https://<domain tenant>/meja#t=<token>. Dibangun BFF dari env (API7); Odoo hanya memeriksa bentuk.
QR_URL_RE = re.compile(r"^https://[A-Za-z0-9.-]+(:\d{1,5})?/[A-Za-z0-9/_-]*#t=[A-Za-z0-9_.-]{16,512}$")
QR_URL_DEV_RE = re.compile(r"^https?://[A-Za-z0-9.-]+(:\d{1,5})?/[A-Za-z0-9/_-]*#t=[A-Za-z0-9_.-]{16,512}$")


def render_qr_png(value, size=300):
    """PNG QR (reportlab, sama dengan mesin ``ir.actions.report.barcode``) TANPA cache LRU.

    ``ir.actions.report.barcode`` 19.0 menyimpan hasil di cache LRU berkunci nilai -> URL bertoken akan tinggal di
    memori proses. Di sini drawing dibuat langsung lalu dibuang."""
    from reportlab.graphics.barcode import createBarcodeDrawing  # noqa: PLC0415 - dependency Odoo

    drawing = createBarcodeDrawing("QR", value=value, width=size, height=size, barLevel="M", barBorder=4)
    buffer = io.BytesIO()
    buffer.write(drawing.asString("png"))
    return buffer.getvalue()


class PosConfig(models.Model):
    _inherit = "pos.config"

    efn_qr_enabled = fields.Boolean(
        string="QR ponsel (Super-App)",
        default=False,
        copy=False,
        help="E-27 / ADR-032: pelayan mencetak QR sesi meja; tamu memesan dari ponsel lewat Super-App + BFF; pesanan "
        "masuk sebagai order draft di meja, bayar di kasir. Wajib POS restoran + preset dine-in. Review security wajib "
        "sebelum diaktifkan di produksi. BFF juga punya kill switch FNB_QR_ENABLED.",
    )
    efn_qr_preset_id = fields.Many2one(
        "pos.preset",
        string="Preset pesanan QR",
        help="Preset order QR (wajib layanan di meja/dine-in, termasuk preset POS ini). Fiscal position & pricelist "
        "order QR diambil dari preset (PB1 dine-in + SC). available_in_self preset tetap mati (SEC-75).",
    )
    efn_qr_confirm_mode = fields.Selection(
        [("waiter", "Dikonfirmasi pelayan"), ("auto", "Langsung ke dapur")],
        string="Mode konfirmasi QR",
        required=True,
        default="waiter",
        help="L-71 (Q-QR6): 'Dikonfirmasi pelayan' = pesanan QR menunggu pelayan menekan Order (atau menghapus "
        "order = tolak). 'Langsung ke dapur' = satu terminal POS yang memenangkan klaim server mengirim ke dapur "
        "otomatis. Server tidak pernah membuat tiket/mencetak sendiri.",
    )
    efn_qr_session_hours = fields.Integer(string="Umur sesi meja (jam)", default=4)
    # L-76: 10 kiriman per sesi meja di kedua sisi (BFF env FNB_MAX_SUBMISSIONS_PER_SESSION default 10).
    efn_qr_max_submissions = fields.Integer(string="Maks kiriman per sesi meja", default=10)
    efn_qr_max_items = fields.Integer(string="Maks item (Σ qty) per sesi meja", default=150)
    efn_qr_max_amount = fields.Monetary(
        string="Maks nominal per sesi meja",
        currency_field="currency_id",
        default=3000000.0,
        help="PLACEHOLDER Rp3.000.000 (Ops F&B). Total order QR sesi meja (termasuk pajak) di atas ini -> ditolak.",
    )
    efn_qr_stale_minutes = fields.Integer(
        string="Alert pesanan QR belum dikirim (menit)",
        default=5,
        help="Order QR yang belum dikirim ke dapur lebih lama dari ini -> log alert EFN_QR_ORDER_STALE + "
        "notifikasi kasir.",
    )

    # ------------------------------------------------------------------ constraints
    @api.constrains(
        "efn_qr_enabled",
        "efn_qr_preset_id",
        "module_pos_restaurant",
        "use_presets",
        "available_preset_ids",
        "default_preset_id",
        "company_id",
    )
    def _check_efn_qr_config(self):
        for config in self:
            if not config.efn_qr_enabled:
                continue
            problem = config._efn_qr_config_problem()
            if problem:
                raise ValidationError(problem)

    def _efn_qr_config_problem(self):
        """Pesan masalah konfigurasi QR (None bila sah)."""
        self.ensure_one()
        if not self.module_pos_restaurant:
            return _("QR ponsel hanya untuk POS restoran (aktifkan Restaurant).")
        if not self.use_presets:
            return _("QR ponsel membutuhkan preset (aktifkan Presets).")
        preset = self.efn_qr_preset_id
        if not preset:
            return _("Pilih preset pesanan QR (dine-in).")
        if "service_at" in preset._fields and preset.service_at != "table":
            return _("Preset pesanan QR harus berlayanan di meja (dine-in).")
        if preset not in (self.available_preset_ids | self.default_preset_id):
            return _("Preset pesanan QR harus termasuk preset POS ini.")
        fiscal_position = preset.fiscal_position_id
        if fiscal_position and fiscal_position.company_id and fiscal_position.company_id != self.company_id:
            return _("Fiscal position preset QR milik company lain.")
        if self.company_id.efn_brand_code not in QR_COMPANY_CODES:
            return _("QR ponsel hanya untuk PT F&B (kode company PB atau BCF).")
        return None

    @api.constrains(
        "efn_qr_session_hours",
        "efn_qr_max_submissions",
        "efn_qr_max_items",
        "efn_qr_max_amount",
        "efn_qr_stale_minutes",
    )
    def _check_efn_qr_limits(self):
        for config in self:
            if not 1 <= config.efn_qr_session_hours <= MAX_SESSION_HOURS:
                raise ValidationError(_("Umur sesi meja QR harus 1..4 jam."))
            if not 1 <= config.efn_qr_max_submissions <= 100:
                raise ValidationError(_("Maks kiriman QR per sesi meja harus 1..100."))
            if not 1 <= config.efn_qr_max_items <= 1000:
                raise ValidationError(_("Maks item QR per sesi meja harus 1..1000."))
            if (config.efn_qr_max_amount or 0.0) <= 0:
                raise ValidationError(_("Maks nominal QR per sesi meja harus > 0."))
            if not 1 <= config.efn_qr_stale_minutes <= 120:
                raise ValidationError(_("Alert pesanan QR belum dikirim harus 1..120 menit."))

    # ------------------------------------------------------------------ data POS
    @api.model
    def _load_pos_data_fields(self, config):
        fields_list = super()._load_pos_data_fields(config)
        if fields_list:
            extra = ["efn_qr_enabled", "efn_qr_confirm_mode"]
            fields_list = [*fields_list, *[f for f in extra if f not in fields_list]]
        return fields_list

    # ------------------------------------------------------------------ helper
    def _efn_qr_company_code(self):
        self.ensure_one()
        code = self.company_id.efn_brand_code
        return code if code in QR_COMPANY_CODES else None

    def _efn_qr_open_pos_session(self):
        """Sesi POS ``opened`` config ini (None bila tidak ada)."""
        self.ensure_one()
        session = self.sudo().current_session_id
        return session if session and session.state == "opened" else None

    def _efn_qr_pricing(self):
        """(preset, fiscal_position, pricelist) order QR — pola ``pos_self_order._check_pos_order`` dengan fallback
        config bila preset tidak mengisi pricelist/FP."""
        self.ensure_one()
        preset = self.efn_qr_preset_id
        fiscal_position = preset.fiscal_position_id or self.default_fiscal_position_id
        pricelist = preset.pricelist_id or self.pricelist_id
        return preset, fiscal_position, pricelist

    def _efn_qr_check_pos_caller(self):
        """SEC-58: pemanggil RPC POS = user POS dengan akses config, company config ∈ company user, sesi POS terbuka."""
        self.ensure_one()
        user = self.env.user
        if not user.has_group(POS_USER_GROUP):
            raise AccessError(_("Anda tidak berhak memakai QR meja."))
        try:
            self.check_access("read")
        except AccessError:
            raise AccessError(_("Anda tidak berhak memakai QR meja.")) from None
        if self.company_id not in user.company_ids:
            raise AccessError(_("Anda tidak berhak memakai QR meja."))
        session = self._efn_qr_open_pos_session()
        if not session:
            raise UserError(_("Sesi POS belum dibuka."))
        return session

    def _efn_qr_table(self, table_id):
        self.ensure_one()
        if not isinstance(table_id, int) or isinstance(table_id, bool) or table_id <= 0:
            raise UserError(_("Meja tidak valid."))
        table = self.env["restaurant.table"].sudo().browse(table_id).exists()
        if not table or not table.active or table.floor_id not in self.sudo().floor_ids:
            raise UserError(_("Meja tidak termasuk denah POS ini."))
        return table

    def _efn_qr_next_print_no(self, table):
        """SEC-167 (r15): nomor cetak = MAX(print_no) sesi (config, meja) + 1, dibaca SETELAH ``touch_row`` meja.

        - Monoton: retensi menghapus sesi TERTUA (nomor terkecil), sehingga MAX tidak pernah turun selama masih ada
          sesi. Bila semua sesi meja terhapus (meja tak dipakai > 400 hari), nomor mulai lagi dari 1 — aman karena
          kunci ``fnb_open`` di BFF disimpan 180 hari (< retensi 400 hari, L-76).
        - Stabil saat transaksi diulang (rollback/serialization retry): MAX dibaca dari data ter-commit, jadi kunci
          idempotensi sama dan BFF me-replay sesi yang sama (bukan membuat sesi kedua).
        - Batas kontrak BFF: 1..99 999 999 (``OPEN_KEY_RE`` r15). Di atas itu -> ``None`` (QR tidak dicetak)."""
        self.ensure_one()
        last = (
            self.env["efn.pos.table.session"]
            .sudo()
            .search(
                [("config_id", "=", self.id), ("table_id", "=", table.id)],
                order="print_no desc, id desc",
                limit=1,
            )
        )
        print_no = (last.print_no or 0) + 1
        if print_no > MAX_PRINT_NO:
            _logger.error("EFN QR: nomor cetak melewati batas kontrak (config %s table %s)", self.id, table.id)
            return None
        return print_no

    @api.model
    def _efn_qr_table_label(self, table):
        """Label meja <= 32 karakter tanpa PII (nomor + nama area)."""
        floor = table.floor_id.name or ""
        label = f"T{table.table_number} / {floor}" if floor else f"T{table.table_number}"
        return label[:32]

    @api.model
    def _efn_qr_error(self, code, message):
        return {"error_code": code, "message": message}

    # ------------------------------------------------------------------ RPC POS
    def efn_qr_session_open(self, table_id, order_uuid, reissue=False):
        """Buka (atau cetak ulang) sesi meja QR. Lihat docstring modul. Hasil tanpa token teks."""
        session = self._efn_qr_check_pos_caller()
        config = self.sudo()
        if not config.efn_qr_enabled or config._efn_qr_config_problem() or not config._efn_qr_company_code():
            return self._efn_qr_error("qr_disabled", _("QR ponsel tidak aktif di POS ini."))
        table = config._efn_qr_table(table_id)
        if not isinstance(order_uuid, str) or not ORDER_UUID_RE.match(order_uuid):
            raise UserError(_("Order meja tidak valid."))
        reissue = reissue is True
        order = (
            self.env["pos.order"]
            .sudo()
            .search(
                [("uuid", "=", order_uuid), ("config_id", "=", config.id), ("company_id", "=", config.company_id.id)],
                limit=1,
            )
        )
        if not order or order.state != "draft" or order.table_id != table:
            return self._efn_qr_error(
                "order_invalid", _("Simpan dulu order meja ini (order harus terbuka di meja yang sama).")
            )
        TableSession = self.env["efn.pos.table.session"].sudo()
        # Serialkan pembukaan sesi per meja (dua pelayan menekan "Cetak QR" bersamaan).
        touch_row(self.env.cr, "restaurant_table", table.id)
        current = TableSession.search(
            [("config_id", "=", config.id), ("table_id", "=", table.id), ("state", "=", "open")], limit=1
        )
        if current and not reissue:
            return self._efn_qr_error(
                "session_exists", _("Meja ini sudah punya QR aktif. Cetak ulang akan mematikan QR lama.")
            )
        # L-76: body TANPA order_uuid (L-70: sesi meja tidak terikat satu order). Kunci idempotensi stabil bila
        # transaksi ini di-rollback lalu diulang: nomor cetak per (config, meja).
        print_no = self._efn_qr_next_print_no(table)
        if print_no is None:
            return self._efn_qr_error("bff_unavailable", _("QR tidak tersedia saat ini. Terima pesanan secara manual."))
        payload = {
            "idempotency_key": f"fnb_open:{config.id}:{table.id}:{print_no}",
            "company_code": config._efn_qr_company_code(),
            "config_id": config.id,
            "table_id": table.id,
            "table_label": self._efn_qr_table_label(table),
            # Permintaan cetak ulang diteruskan walau Odoo tidak punya sesi `open` (mis. sudah `expired` di Odoo karena
            # efn_qr_session_hours < TTL BFF, atau event close gagal terkirim): tanpa ini BFF membalas 409
            # table_session_exists dan pelayan buntu sampai TTL BFF habis. BFF tanpa sesi open mengabaikan flag ini.
            "reissue": bool(current) or reissue,
        }
        try:
            response = self.env["efn.bff.client"]._efn_post(
                TABLE_SESSION_PATH, payload, log_context={"config": config.id, "table": table.id, "print": print_no}
            )
        except EfnBffError as exc:
            _logger.warning("EFN QR: buka sesi meja gagal (%s) config %s table %s", exc.code, config.id, table.id)
            return self._efn_qr_error("bff_unavailable", _("QR tidak tersedia saat ini. Terima pesanan secara manual."))
        data = response.data
        if response.status not in (200, 201):
            code = (data.get("error") or {}).get("code") if isinstance(data.get("error"), dict) else None
            _logger.warning(
                "EFN QR: BFF menolak buka sesi meja (%s %s) config %s table %s",
                response.status,
                code if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,64}", code) else "-",
                config.id,
                table.id,
            )
            if response.status == 409 and code == "table_session_exists":
                return self._efn_qr_error(
                    "session_exists", _("Meja ini sudah punya QR aktif. Cetak ulang akan mematikan QR lama.")
                )
            if response.status == 422:
                return self._efn_qr_error("qr_disabled", _("QR ponsel dimatikan di server Super-App."))
            return self._efn_qr_error("bff_unavailable", _("QR tidak tersedia saat ini. Terima pesanan secara manual."))
        session_ref = data.get("table_session_id")
        qr_url = data.get("qr_url")
        expires_raw = data.get("expires_at")
        url_re = QR_URL_DEV_RE if plaintext_allowed() else QR_URL_RE
        if (
            not isinstance(session_ref, str)
            or not UUID_RE.match(session_ref)
            or not isinstance(qr_url, str)
            or len(qr_url) > 1024
            or not url_re.match(qr_url)
        ):
            # API10: respons BFF tidak sesuai skema -> tidak dipakai, isi tidak di-log.
            _logger.error("EFN QR: respons BFF buka sesi meja tidak valid (config %s table %s)", config.id, table.id)
            return self._efn_qr_error("bff_unavailable", _("QR tidak tersedia saat ini. Terima pesanan secara manual."))
        existing = TableSession.search([("bff_session_id", "=", session_ref)], limit=1)
        if existing:
            # SEC-167: print_no monoton -> kunci baru tidak pernah sama dengan sesi yang sudah tercatat di Odoo. BFF
            # yang menunjuk sesi Odoo lama (tutup/kedaluwarsa/meja lain) = tabrakan kunci: QR tidak dicetak, sesi
            # lama tidak dipakai ulang, dan sesi yang sedang open tidak ditutup.
            _logger.error(
                "EFN QR: BFF menunjuk sesi meja Odoo yang sudah ada (session %s state %s) config %s table %s",
                existing.id,
                existing.state,
                config.id,
                table.id,
            )
            return self._efn_qr_error("bff_unavailable", _("QR tidak tersedia saat ini. Terima pesanan secara manual."))
        now = fields.Datetime.now()
        expires_at = now + timedelta(hours=config.efn_qr_session_hours)
        bff_expires = TableSession._efn_parse_bff_datetime(expires_raw)
        if bff_expires and bff_expires < expires_at:
            expires_at = bff_expires
        carried_orders = self.env["pos.order"]
        if current:
            # Sesi lama dicabut BFF (reissue=true -> close_reason reissued); di Odoo cukup ditandai tanpa event.
            # Order sesi lama pindah ke sesi baru (tagihan meja tetap satu siklus; L-70).
            carried_orders = current.order_ids
            current._efn_close("reissued", emit=False)
            # Index unik parsial "satu sesi open per meja": UPDATE state harus sampai DB sebelum INSERT sesi baru.
            current.flush_recordset()
        table_session = TableSession.create(
            {
                "company_id": config.company_id.id,
                "config_id": config.id,
                "table_id": table.id,
                "bff_session_id": session_ref,
                "order_uuid": order_uuid,
                "print_no": print_no,
                "opened_by_id": self.env.uid,
                "opened_at": now,
                "expires_at": expires_at,
                "pos_session_id": session.id,
            }
        )
        # sudo eksplisit: union mengambil env operand kiri (non-sudo) dan field server dibuang diam-diam di write.
        (carried_orders | order).sudo().write({"efn_qr_table_session_id": table_session.id})
        png = render_qr_png(qr_url)
        qr_url = None  # token tidak disimpan di variabel yang hidup lebih lama dari render
        _logger.info(
            "EFN_QR_SESSION_OPEN config=%s table=%s session=%s print=%s",
            config.id,
            table.id,
            table_session.id,
            print_no,
        )
        return {
            "qr_png_b64": base64.b64encode(png).decode("ascii"),
            "expires_at": fields.Datetime.to_string(table_session.expires_at),
            "table_label": self._efn_qr_table_label(table),
            "print_no": print_no,
        }

    def efn_qr_session_close(self, table_id):
        """Tutup sesi meja QR oleh staf (``closed_by_staff``) + event ``session_closed`` ke BFF."""
        self._efn_qr_check_pos_caller()
        config = self.sudo()
        table = config._efn_qr_table(table_id)
        sessions = (
            self.env["efn.pos.table.session"]
            .sudo()
            .search([("config_id", "=", config.id), ("table_id", "=", table.id), ("state", "in", ("open", "expired"))])
        )
        for table_session in sessions:
            table_session._efn_close("closed_by_staff")
        return {"closed": len(sessions)}
