"""Batch pembayaran satu jurnal bank -> satu file transfer (port ADR-049 dari
`custom_account_batch_payment/models/account_batch_payment.py`).

Perubahan EFN (G58 §5.1–§5.3):
- `company_id` stored + record rule; jejak `validated_by_id`, `export_sha256`, `exported_by_id`, `exported_at`;
- **empat mata**: validasi oleh Accounting/Bookkeeper; ekspor oleh Administrator yang BUKAN validator;
  reset setelah `sent` hanya Administrator (jejak ekspor lama tetap di chatter/efn_audit);
- persetujuan payment G60 (`efn_approval_state`, efn_account_localization) diperiksa bila field ada;
- **file tidak disimpan permanen**: attachment sementara privat, dihapus cron setelah 24 jam (sumber menyimpan
  `export_file` selamanya; file berisi nomor rekening orang, filestore belum terenkripsi per file);
- hanya format `generic` yang boleh diekspor sampai format bank `verified` (Q-G58-2).
Log & chatter tanpa nomor rekening atau nama.
"""

import hashlib
import logging
import re
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .efn_account_batch_payment_format import GENERIC_CODE

_logger = logging.getLogger(__name__)

#: Konteks privat penulisan field sistem; hanya dihormati bersama env.su (lihat `_efn_is_system`).
SYSTEM_CTX = "efn_batch_payment_system"
SYSTEM_FIELDS = frozenset(
    {"name", "state", "validated_by_id", "efn_validated_digest", "export_sha256", "exported_by_id", "exported_at"}
)
#: Isi batch yang terkunci setelah validasi.
LOCKED_FIELDS = frozenset({"payment_ids", "journal_id", "batch_type", "date", "export_format_id"})
USER_GROUP = "account.group_account_user"
MANAGER_GROUP = "account.group_account_manager"
EXPORT_MARKER = "efn_batch_payment_export"
EXPORT_TTL_HOURS = 24
APPROVED_STATES = ("not_required", "approved")


def _efn_unapproved(payments):
    """Payment yang belum lolos persetujuan G60 (field `efn_approval_state`), kosong bila modul G60 tidak terpasang."""
    if "efn_approval_state" not in payments._fields:
        return payments.browse()
    return payments.filtered(lambda p: p.efn_approval_state not in APPROVED_STATES)


class EfnAccountBatchPayment(models.Model):
    _name = "efn.account.batch.payment"
    _description = "EFN Batch Pembayaran"
    _inherit = ["mail.thread"]
    _order = "date desc, id desc"
    _check_company_auto = True

    name = fields.Char(readonly=True, copy=False, default="/")
    journal_id = fields.Many2one(
        "account.journal",
        required=True,
        domain="[('type', '=', 'bank')]",
        check_company=True,
        tracking=True,
    )
    company_id = fields.Many2one(related="journal_id.company_id", store=True, index=True)
    currency_id = fields.Many2one("res.currency", compute="_compute_currency_id")
    date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    batch_type = fields.Selection(
        [("outbound", "Keluar (pembayaran vendor)"), ("inbound", "Masuk (penerimaan pelanggan)")],
        required=True,
        default="outbound",
        tracking=True,
    )
    payment_ids = fields.One2many("account.payment", "efn_batch_payment_id", string="Pembayaran")
    payment_count = fields.Integer(compute="_compute_totals")
    amount_total = fields.Monetary(compute="_compute_totals", currency_field="currency_id")
    export_format_id = fields.Many2one("efn.account.batch.payment.format", string="Format File Transfer")
    state = fields.Selection(
        [("draft", "Draft"), ("validated", "Divalidasi"), ("sent", "Terkirim"), ("reconciled", "Terekonsiliasi")],
        default="draft",
        tracking=True,
        compute="_compute_state",
        store=True,
        readonly=False,
        copy=False,
    )
    validated_by_id = fields.Many2one("res.users", readonly=True, copy=False, tracking=True)
    efn_validated_digest = fields.Char(
        string="Digest Validasi",
        readonly=True,
        copy=False,
        help="sha256 isi batch saat validasi (jurnal, arah, id/nominal/mata uang/rekening penerima tiap pembayaran). "
        "Ekspor ditolak bila isi berubah.",
    )
    export_sha256 = fields.Char(string="SHA-256 File", readonly=True, copy=False, tracking=True)
    exported_by_id = fields.Many2one("res.users", readonly=True, copy=False, tracking=True)
    exported_at = fields.Datetime(readonly=True, copy=False)

    # ------------------------------------------------------------------ penguncian (SEC-G58-01)
    @api.model
    def _efn_is_system(self):
        """Penulisan field sistem hanya oleh aksi server (sudo + konteks privat; konteks RPC tidak bisa membuat su)."""
        return self.env.su and self.env.context.get(SYSTEM_CTX)

    def _efn_system(self):
        return self.sudo().with_context(**{SYSTEM_CTX: True})

    @api.model_create_multi
    def create(self, vals_list):
        if not self._efn_is_system():
            for vals in vals_list:
                forbidden = SYSTEM_FIELDS & set(vals)
                if forbidden:
                    raise AccessError(
                        _("Field sistem batch tidak boleh diisi langsung: %s", ", ".join(sorted(forbidden)))
                    )
        return super().create(vals_list)

    def write(self, vals):
        if not self._efn_is_system():
            forbidden = SYSTEM_FIELDS & set(vals)
            if forbidden:
                raise AccessError(
                    _("Field sistem batch hanya diubah lewat tombol (empat mata): %s", ", ".join(sorted(forbidden)))
                )
            if LOCKED_FIELDS & set(vals) and any(batch.state != "draft" for batch in self):
                raise UserError(
                    _("Isi batch terkunci setelah validasi. Reset ke draft (Administrator) untuk mengubah.")
                )
        return super().write(vals)

    def _efn_content_digest(self):
        """sha256 isi batch: jurnal, arah, dan (id, nominal, mata uang, rekening, nomor rekening) tiap pembayaran."""
        self.ensure_one()
        parts = [str(self.journal_id.id), self.batch_type or ""]
        for pay in self.payment_ids.sorted("id"):
            bank = pay.partner_bank_id
            parts.append(
                "|".join(
                    (
                        str(pay.id),
                        f"{pay.amount:.6f}",
                        str(pay.currency_id.id),
                        str(bank.id),
                        re.sub(r"[\s.\-]", "", bank.acc_number or ""),
                    )
                )
            )
        return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()

    @api.depends("journal_id")
    def _compute_currency_id(self):
        for batch in self:
            batch.currency_id = batch.journal_id.currency_id or batch.journal_id.company_id.currency_id

    @api.depends("payment_ids", "payment_ids.amount")
    def _compute_totals(self):
        for batch in self:
            batch.payment_count = len(batch.payment_ids)
            batch.amount_total = sum(batch.payment_ids.mapped("amount"))

    @api.depends("payment_ids.is_matched", "payment_ids.state")
    def _compute_state(self):
        # Hanya sent -> reconciled yang otomatis (semua payment paid + cocok dengan mutasi bank).
        for batch in self:
            if (
                batch.state == "sent"
                and batch.payment_ids
                and all(p.state == "paid" and p.is_matched for p in batch.payment_ids)
            ):
                batch.state = "reconciled"
            else:
                batch.state = batch.state or "draft"

    @api.ondelete(at_uninstall=False)
    def _unlink_except_not_draft(self):
        if any(batch.state != "draft" for batch in self):
            raise UserError(_("Hanya batch draft yang dapat dihapus. Reset ke draft dulu."))

    # ------------------------------------------------------------------ akses
    def _efn_check_group(self, group, operation="write"):
        if not self.env.user.has_group(group):
            raise AccessError(_("Anda tidak berhak menjalankan aksi ini pada batch pembayaran."))
        self.check_access(operation)
        if self.company_id - self.env.companies:
            raise AccessError(_("Batch pembayaran milik company yang tidak aktif di sesi Anda."))

    def _efn_validate_payments(self):
        for batch in self:
            payments = batch.payment_ids
            if not payments:
                raise UserError(_("Batch %s belum berisi pembayaran.", batch.name))
            bad_state = payments.filtered(lambda p: p.state not in ("in_process", "paid"))
            if bad_state:
                raise UserError(_("Pembayaran belum di-post: %s", ", ".join(bad_state.mapped("name"))))
            bad_journal = payments.filtered(lambda p, b=batch: p.journal_id != b.journal_id)
            if bad_journal:
                raise UserError(_("Pembayaran memakai jurnal lain: %s", ", ".join(bad_journal.mapped("name"))))
            bad_dir = payments.filtered(lambda p, b=batch: p.payment_type != b.batch_type)
            if bad_dir:
                raise UserError(_("Arah pembayaran tidak sesuai batch: %s", ", ".join(bad_dir.mapped("name"))))
            unapproved = _efn_unapproved(payments)
            if unapproved:
                raise UserError(_("Pembayaran belum disetujui: %s", ", ".join(unapproved.mapped("name"))))

    # ------------------------------------------------------------------ aksi
    def action_validate(self):
        self._efn_check_group(USER_GROUP)
        if any(batch.state != "draft" for batch in self):
            raise UserError(_("Hanya batch draft yang dapat divalidasi."))
        self._efn_validate_payments()
        for batch in self:
            vals = {
                "state": "validated",
                "validated_by_id": self.env.uid,
                "efn_validated_digest": batch._efn_content_digest(),
            }
            if not batch.name or batch.name == "/":
                vals["name"] = self.env["ir.sequence"].next_by_code("efn.account.batch.payment") or "/"
            batch._efn_system().write(vals)
        return True

    def action_generate_export_file(self):
        """Buat file transfer (empat mata, fail-closed) sebagai attachment privat sementara lalu unduh."""
        self.ensure_one()
        self._efn_check_group(MANAGER_GROUP)
        if self.state not in ("validated", "sent"):
            raise UserError(_("Validasi batch sebelum membuat file transfer."))
        if not self.validated_by_id or self.validated_by_id == self.env.user:
            raise UserError(_("Empat mata: file transfer harus dibuat oleh orang lain selain validator batch."))
        fmt = self.export_format_id
        if not fmt:
            raise UserError(_("Pilih format file transfer dulu."))
        if fmt.code != GENERIC_CODE and not fmt.verified:
            raise UserError(
                _("Format %s belum diverifikasi dengan portal bank; gunakan format generik.", fmt.display_name)
            )
        self._efn_validate_payments()
        if not self.efn_validated_digest or self.efn_validated_digest != self._efn_content_digest():
            raise UserError(
                _(
                    "Isi batch (pembayaran, nominal, atau rekening penerima) berubah sejak divalidasi. Reset ke draft "
                    "lalu validasi ulang oleh orang lain."
                )
            )
        content, filename = fmt._efn_render(self)
        digest = hashlib.sha256(content).hexdigest()
        self._efn_export_attachments().unlink()
        # SEC-G58-02: attachment TIDAK tertaut ke batch -> CE hanya mengizinkan pembuatnya (pengekspor) dan admin sistem
        # membaca; tidak tampil di chatter. Dibuat dengan hak user (create_uid = pengekspor), bukan sudo.
        attachment = self.env["ir.attachment"].create(
            {
                "name": filename,
                "raw": content,
                "public": False,
                "description": self._efn_export_marker(),
                "mimetype": "text/csv",
            }
        )
        self._efn_system().write(
            {
                "state": "sent",
                "export_sha256": digest,
                "exported_by_id": self.env.uid,
                "exported_at": fields.Datetime.now(),
            }
        )
        self.payment_ids.filtered(lambda p: not p.is_sent).mark_as_sent()
        self.message_post(
            body=_(
                "File transfer dibuat (%(count)s pembayaran, format %(fmt)s, SHA-256 %(digest)s). File tersedia "
                "%(hours)s jam lalu dihapus otomatis.",
                count=len(self.payment_ids),
                fmt=fmt.code,
                digest=digest,
                hours=EXPORT_TTL_HOURS,
            )
        )
        _logger.info("EFN batch payment %s: file transfer dibuat oleh user %s", self.id, self.env.uid)
        return {"type": "ir.actions.act_url", "url": f"/web/content/{attachment.id}?download=true", "target": "self"}

    def action_draft(self):
        self._efn_check_group(MANAGER_GROUP)
        for batch in self:
            batch.payment_ids.filtered("is_sent").unmark_as_sent()
            batch._efn_export_attachments().unlink()
            batch._efn_system().write(
                {
                    "state": "draft",
                    "validated_by_id": False,
                    "efn_validated_digest": False,
                    "export_sha256": False,
                    "exported_by_id": False,
                    "exported_at": False,
                }
            )
        return True

    def action_open_payments(self):
        self.ensure_one()
        self._efn_check_group(USER_GROUP, "read")
        return {
            "type": "ir.actions.act_window",
            "name": _("Pembayaran"),
            "res_model": "account.payment",
            "view_mode": "list,form",
            "views": [(False, "list"), (False, "form")],
            "domain": [("efn_batch_payment_id", "=", self.id)],
        }

    # ------------------------------------------------------------------ file sementara
    def _efn_export_marker(self):
        self.ensure_one()
        return f"{EXPORT_MARKER}:{self.id}"

    def _efn_export_attachments(self):
        markers = [batch._efn_export_marker() for batch in self]
        return (
            self.env["ir.attachment"]
            .sudo()
            .search([("res_model", "=", False), ("res_id", "=", False), ("description", "in", markers)])
        )

    @api.model
    def _efn_gc_export_files(self):
        """Cron: hapus file transfer yang lebih tua dari 24 jam (hanya hash + pengekspor + waktu yang tersisa)."""
        cutoff = fields.Datetime.now() - timedelta(hours=EXPORT_TTL_HOURS)
        old = (
            self.env["ir.attachment"]
            .sudo()
            .search(
                [
                    ("res_model", "=", False),
                    ("description", "=like", f"{EXPORT_MARKER}:%"),
                    ("create_date", "<", cutoff),
                ]
            )
        )
        count = len(old)
        old.unlink()
        if count:
            _logger.info("EFN batch payment: %s file transfer kedaluwarsa dihapus", count)
        return count
