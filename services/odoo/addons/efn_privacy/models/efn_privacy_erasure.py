"""Cron erasure UU PDP sisi Odoo (docs/04 §8.4 + §8.8, docs/08 §4.2).

Alur tiap 15 menit (user OdooBot):
1. Pepper member_hash wajib terbaca -> kalau tidak: ERROR + cron gagal keras (tanpa memanggil
   BFF, agar permintaan tidak pernah "selesai" tanpa benar-benar dihapus).
2. `GET /v1/pos/privacy/erasures?store=odoo` -> `[{request_id, member_hash}]`.
3. Per permintaan (savepoint sendiri; gagal satu tidak menghentikan yang lain):
   - `res.partner` dengan `efn_member_hash` cocok, **termasuk arsip & lintas company**
     (sudo + active_test=False) -> dianonimkan (tidak di-unlink: FK pos.order/account.move).
   - Salinan kontak di `pos.order` (`email`, `mobile`, stored) dikosongkan.
   - Chatter (`mail.message` + `mail.tracking.value`) pada partner dihapus; `email_from`
     pesan yang ditulis partner di dokumen lain dikosongkan.
   - Log `efn.audit.log` (+ baris) untuk `res.partner` tsb dihapus lewat
     `_efn_audit_purge_records` (efn_audit; aturan efn_setup_audit mencatat HP lama ter-mask).
   - Jejak `privacy.log` standar (privacy_lookup) dengan nama/email placeholder valid; tanpa
     hash/HP dan **tanpa request_id** (SEC-31).
   - Penanda sementara `efn.privacy.erasure.pending` (request_id + jumlah, TANPA id partner)
     dibuat/ditambah di transaksi yang sama.
   - Commit (di luar test), lalu `POST /v1/pos/privacy/erasures/:id/complete
     {store:"odoo", records:n}`. `n` = jumlah di penanda (termasuk run sebelumnya) ->
     idempoten: rerun / complete yang sempat gagal mengirim angka yang sama.
   - Complete diterima BFF -> penanda dihapus. Sejak itu tidak ada lagi nilai di Odoo yang
     menautkan partner teranonim ke permintaan/member_hash (SEC-31, docs/04 §8.13).
4. Dokumen akuntansi & `pos.order` dipertahankan dengan partner teranonim (retensi 10 th,
   menunggu DPO).

Log hanya memuat `request_id` dan jumlah; tidak pernah nomor HP, email, hash, atau id partner.
"""

import logging
import re
import threading
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.efn_core.models.efn_bff_client import EfnBffError

from .member_hash import HASH_RE, PEPPER_FILE_ENV, PepperUnavailable, read_pepper

_logger = logging.getLogger(__name__)

ERASURES_PATH = "/v1/pos/privacy/erasures"
COMPLETE_PATH = "/v1/pos/privacy/erasures/%s/complete"
STORE = "odoo"
DEFAULT_BATCH = 100
REQUEST_ID_RE = re.compile(r"^[0-9A-Za-z\-]{8,64}$")
PLACEHOLDER_NAME = "Member"
# Nama partner teranonim: TANPA potongan request_id (SEC-31 rekomendasi 3).
ERASED_PARTNER_NAME = "Pelanggan dihapus"
PENDING_MAX_AGE_DAYS = 30
# Wajib berisi '@': privacy.log._anonymize_email mengembalikan UserError untuk email tanpa '@'
# (docs/08 §2 temuan 9). Domain .invalid (RFC 2606) tidak pernah bisa menerima email.
PLACEHOLDER_EMAIL = "erased@efn.invalid"

# Field kontak res.partner yang dikosongkan (yang tidak ada di registry dilewati).
PARTNER_CLEAR_FIELDS = (
    "phone",
    "mobile",
    "email",
    "street",
    "street2",
    "city",
    "zip",
    "state_id",
    "vat",
    "website",
    "function",
    "comment",
    "image_1920",
    "barcode",
)
POS_ORDER_CLEAR_FIELDS = ("email", "mobile")


class EfnPrivacyErasure(models.AbstractModel):
    _name = "efn.privacy.erasure"
    _description = "EFN Privacy Erasure (UU PDP)"

    # ------------------------------------------------------------------- cron
    @api.model
    def _cron_pull_erasures(self):
        try:
            read_pepper()
        except PepperUnavailable as exc:
            _logger.error(
                "EFN privacy: erasure DIHENTIKAN, pepper member_hash tidak tersedia (%s, cek env %s)",
                exc.code,
                PEPPER_FILE_ENV,
            )
            raise UserError(_("Pepper member_hash tidak tersedia; erasure tidak dijalankan.")) from None

        client = self.env["efn.bff.client"]
        try:
            response = client.get(ERASURES_PATH, {"store": STORE}, log_context={"store": STORE})
        except EfnBffError as exc:
            _logger.error("EFN privacy: gagal menarik daftar erasure (%s)", exc.code)
            raise UserError(_("BFF tidak dapat dihubungi untuk daftar erasure.")) from None
        if response.status != 200:
            _logger.error("EFN privacy: daftar erasure ditolak BFF (HTTP %s)", response.status)
            raise UserError(_("BFF menolak permintaan daftar erasure."))

        items = response.body
        if isinstance(items, dict):
            items = items.get("items")
        if not isinstance(items, list):
            _logger.error("EFN privacy: format daftar erasure tidak dikenal")
            raise UserError(_("Format daftar erasure dari BFF tidak dikenal."))

        batch = self._efn_batch_size()
        summary = {"done": 0, "failed": 0, "skipped": 0}
        for item in items[:batch]:
            request_id = item.get("request_id") if isinstance(item, dict) else None
            hashed = item.get("member_hash") if isinstance(item, dict) else None
            if not (isinstance(request_id, str) and REQUEST_ID_RE.match(request_id)) or not (
                isinstance(hashed, str) and HASH_RE.match(hashed)
            ):
                summary["skipped"] += 1
                _logger.warning("EFN privacy: item erasure tidak valid dilewati")
                continue
            if self._efn_process_request(request_id, hashed):
                summary["done"] += 1
            else:
                summary["failed"] += 1
        if len(items) > batch:
            _logger.info("EFN privacy: %d permintaan tersisa untuk run berikutnya", len(items) - batch)
        _logger.info("EFN privacy: erasure selesai=%(done)d gagal=%(failed)d dilewati=%(skipped)d", summary)
        return summary

    # ---------------------------------------------------------------- helpers
    @api.model
    def _efn_batch_size(self):
        raw = self.env["ir.config_parameter"].sudo().get_param("efn.privacy.erasure_batch", DEFAULT_BATCH)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = DEFAULT_BATCH
        return max(1, min(value, 1000))

    @api.model
    def _efn_commit(self):
        """Commit sebelum memberi tahu BFF `complete`, agar BFF tidak pernah menganggap
        selesai sementara anonimisasi masih bisa di-rollback. Dilewati saat test."""
        if self.env.context.get("efn_privacy_no_commit"):
            return
        if getattr(threading.current_thread(), "testing", False):
            return
        in_test_mode = getattr(self.env.registry, "in_test_mode", None)
        if callable(in_test_mode) and in_test_mode():
            return
        self.env.cr.commit()  # pylint: disable=invalid-commit

    @api.model
    def _efn_process_request(self, request_id, hashed):
        """Anonimkan + complete satu permintaan. True bila complete diterima BFF."""
        try:
            with self.env.cr.savepoint():
                records = self._efn_erase_member(request_id, hashed)
        except Exception as exc:  # noqa: BLE001 - satu permintaan gagal tidak menghentikan lainnya
            # Tanpa traceback/pesan: bisa memuat nilai field (PII).
            _logger.error("EFN privacy: anonimisasi request %s gagal (%s)", request_id, type(exc).__name__)
            return False
        self._efn_commit()
        try:
            response = self.env["efn.bff.client"].post(
                COMPLETE_PATH % request_id,
                {"store": STORE, "records": records},
                log_context={"request_id": request_id},
            )
        except EfnBffError as exc:
            _logger.warning("EFN privacy: complete request %s gagal (%s); diulang run berikutnya", request_id, exc.code)
            return False
        if not 200 <= response.status < 300:
            _logger.warning("EFN privacy: complete request %s ditolak BFF (HTTP %s)", request_id, response.status)
            return False
        # SEC-31: tautan request -> jumlah hanya hidup sampai BFF menerima complete.
        self.env["efn.privacy.erasure.pending"]._efn_done(request_id)
        self._efn_commit()
        _logger.info("EFN privacy: request %s selesai (%d partner)", request_id, records)
        return True

    @api.model
    def _efn_erase_member(self, request_id, hashed):
        """Anonimkan semua partner dengan hash cocok.

        Kembalikan jumlah partner yang dianonimkan untuk request ini, termasuk run sebelumnya
        yang complete-nya gagal (dibaca dari penanda sementara, bukan dari partner).
        """
        Partner = (
            self.env["res.partner"]
            .sudo()
            .with_context(
                active_test=False,
                tracking_disable=True,
                mail_notrack=True,
                mail_create_nolog=True,
            )
        )
        partners = Partner.search([("efn_member_hash", "=", hashed)])
        if partners:
            self._efn_anonymize_partners(partners)
            self.env["privacy.log"].sudo().create(
                {
                    "anonymized_name": PLACEHOLDER_NAME,
                    "anonymized_email": PLACEHOLDER_EMAIL,
                    # SEC-31: tanpa request_id / hash / id partner.
                    "execution_details": f"EFN erasure (UU PDP): {len(partners)} partner dianonimkan "
                    "(res.partner, pos.order kontak, chatter, log audit)",
                    "records_description": f"res.partner: {len(partners)}",
                }
            )
        return self.env["efn.privacy.erasure.pending"]._efn_add(request_id, len(partners))

    @api.model
    def _efn_anonymize_partners(self, partners):
        ids = partners.ids
        vals = {fname: False for fname in PARTNER_CLEAR_FIELDS if fname in partners._fields}
        vals.update({"name": ERASED_PARTNER_NAME, "efn_erased": True})
        partners.write(vals)
        # HP kosong -> efn_member_hash (compute stored) ikut kosong. Dipastikan eksplisit: hash
        # yang tersisa = tautan ke member_hash platform (SEC-31). Gagal -> savepoint rollback.
        partners.flush_recordset()
        if partners.filtered("efn_member_hash"):
            raise UserError(_("member_hash partner teranonim tidak terhapus."))
        # Partner milik user aktif tidak bisa diarsipkan (constraint base); user diurus terpisah.
        with_users = partners.filtered("user_ids")
        if with_users:
            _logger.warning("EFN privacy: %d partner terkait user tidak diarsipkan", len(with_users))
        (partners - with_users).write({"active": False})

        # Salinan kontak stored di pos.order (compute dari partner, readonly=False).
        PosOrder = self.env["pos.order"].sudo()
        order_vals = {f: False for f in POS_ORDER_CLEAR_FIELDS if f in PosOrder._fields}
        if order_vals:
            PosOrder.search([("partner_id", "in", ids)]).write(order_vals)

        # Chatter & tracking pada partner (termasuk tracking dari write di atas, bila ada).
        Message = self.env["mail.message"].sudo()
        messages = Message.search([("model", "=", "res.partner"), ("res_id", "in", ids)])
        if messages:
            self.env["mail.tracking.value"].sudo().search([("mail_message_id", "in", messages.ids)]).unlink()
            messages.unlink()
        Message.search([("author_id", "in", ids), ("email_from", "!=", False)]).write({"email_from": False})

        # efn_audit (ADR-031, pengganti OCA auditlog): log res.partner menyimpan HP/email lama
        # (ter-mask) + nama — termasuk write anonimisasi di atas. Hapus log partner tsb dan
        # kosongkan ref/nilai di baris log model lain yang menunjuk partner ini; efn_audit
        # menulis satu log `purge` tanpa id/nama.
        self.env["efn.audit.log"].sudo()._efn_audit_purge_records("res.partner", ids)
        return len(partners)


class EfnPrivacyErasurePending(models.Model):
    """Penanda SEMENTARA untuk idempotensi `complete` (SEC-31, docs/04 §8.13).

    Masalah: setelah anonimisasi, HP partner kosong sehingga pencarian ulang via member_hash
    mengembalikan 0. Bila `complete` gagal (BFF down) dan diulang, `records` akan salah. Desain
    lama menyimpan `request_id` permanen di partner -> partner teranonim bisa ditautkan ke
    `dsar_request` (dan member_hash-nya). Sekarang:

    - Penanda hanya berisi `request_id` + jumlah (TIDAK ada id/nama partner, hash, atau HP),
      dibuat di transaksi/savepoint yang sama dengan anonimisasi (atomik).
    - Dihapus begitu BFF menerima `complete` (2xx). Sisa penanda (mis. request dibatalkan di
      BFF setelah anonimisasi tetapi sebelum complete) dibersihkan autovacuum setelah 30 hari.

    Tanpa `company_id` (pengecualian konvensi CLAUDE.md, dicatat di README): data teknis proses
    erasure lintas company, bukan data bisnis per PT; ditulis cron (sudo), ACL baca saja untuk
    `base.group_system`.
    """

    _name = "efn.privacy.erasure.pending"
    _description = "EFN Privacy Erasure Pending (penanda sementara)"

    request_id = fields.Char(required=True, readonly=True, index=True)
    records = fields.Integer(required=True, default=0, readonly=True)

    _request_id_unique = models.Constraint("UNIQUE(request_id)", "Penanda erasure per request harus unik.")

    @api.model
    def _efn_add(self, request_id, count):
        """Tambah `count` ke penanda request (buat bila belum ada). Kembalikan total."""
        marker = self.sudo().search([("request_id", "=", request_id)], limit=1)
        if not marker:
            marker = self.sudo().create({"request_id": request_id, "records": count})
        elif count:
            marker.records += count
        return marker.records

    @api.model
    def _efn_done(self, request_id):
        self.sudo().search([("request_id", "=", request_id)]).unlink()

    @api.autovacuum
    def _gc_efn_stale_markers(self):
        limit = fields.Datetime.now() - timedelta(days=PENDING_MAX_AGE_DAYS)
        self.sudo().search([("create_date", "<", limit)]).unlink()
