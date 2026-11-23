"""G60-A: persetujuan 4-eyes pembayaran keluar + helper cetakan Payment Voucher/Receipt.

Gerbang (docs/fit-gap/G59_G60_ACCOUNTING_PORT.md §G60-A, D-7): pembayaran keluar non-POS, bukan transfer internal,
dengan nilai (mata uang company) >= ``company.efn_payment_approval_threshold`` (> 0) tidak bisa dikonfirmasi sebelum
disetujui Accounting Manager yang BUKAN peminta dan BUKAN pembuat (pola ``efn_loyalty_accounting``). Ditolak = tetap
draft (Q-G60-2). Reset ke draft setelah disetujui = persetujuan gugur.

Helper voucher (``_efn_voucher_*``) diport dari odoo-platform `custom_levis_localization/models/account_payment.py`
(``_edo_*``) dengan rename + masking nomor rekening tujuan (Q-G60-3). Field OU/override akun outstanding Levi's di-drop.
"""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .terbilang import terbilang_id

APPROVER_GROUP = "account.group_account_manager"
INVOICE_TYPES = ("in_invoice", "in_refund", "out_invoice", "out_refund")
APPROVAL_STATES = [
    ("not_required", "Belum diminta / tidak perlu"),
    ("to_approve", "Menunggu persetujuan"),
    ("approved", "Disetujui"),
    ("rejected", "Ditolak"),
]
# SEC-G60-01: field persetujuan hanya ditulis kode modul ini (sudo + konteks privat, setelah cek akses).
APPROVAL_FIELDS = frozenset(
    {"efn_approval_state", "efn_approval_requested_by_id", "efn_approved_by_id", "efn_approved_at"}
)
SYSTEM_CONTEXT = "efn_payment_approval_system"
# SEC-G60-02: perubahan isi pada payment to_approve/approved menggugurkan persetujuan.
CONTENT_FIELDS = frozenset(
    {
        "amount",
        "currency_id",
        "date",
        "partner_id",
        "partner_bank_id",
        "journal_id",
        "payment_method_line_id",
        "payment_type",
        "destination_account_id",
        "company_id",
        "pos_session_id",
    }
)
APPROVAL_RESET = {
    "efn_approval_state": "not_required",
    "efn_approval_requested_by_id": False,
    "efn_approved_by_id": False,
    "efn_approved_at": False,
}


def mask_account_number(value):
    """``1234567890`` -> ``****7890`` (4 karakter terakhir; <= 4 karakter -> ``****``)."""
    compact = "".join((value or "").split())
    if len(compact) <= 4:
        return "****" if compact else ""
    return f"****{compact[-4:]}"


class AccountPayment(models.Model):
    _inherit = "account.payment"

    efn_approval_state = fields.Selection(
        APPROVAL_STATES,
        string="Persetujuan",
        default="not_required",
        required=True,
        copy=False,
        tracking=True,
        readonly=True,
    )
    efn_needs_approval = fields.Boolean(string="Perlu persetujuan", compute="_compute_efn_needs_approval")
    efn_approval_requested_by_id = fields.Many2one("res.users", string="Diminta oleh", copy=False, readonly=True)
    efn_approved_by_id = fields.Many2one("res.users", string="Disetujui/ditolak oleh", copy=False, readonly=True)
    efn_approved_at = fields.Datetime(string="Waktu keputusan", copy=False, readonly=True)
    efn_approval_note = fields.Char(string="Catatan persetujuan", copy=False)

    # ------------------------------------------------------------------------------------------------- gerbang
    @api.depends("payment_type", "amount", "currency_id", "date", "company_id", "partner_id", "destination_account_id")
    def _compute_efn_needs_approval(self):
        for payment in self:
            payment.efn_needs_approval = payment._efn_needs_approval()

    # ------------------------------------------------------------------------------------- ORM guard (SEC-G60)
    def _efn_is_system(self):
        return self.env.su and bool(self.env.context.get(SYSTEM_CONTEXT))

    def _efn_system_write(self, vals):
        """Satu-satunya jalur tulis field persetujuan; dipanggil SETELAH cek akses/state."""
        return self.sudo().with_context(**{SYSTEM_CONTEXT: True}).write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        if not self._efn_is_system():
            # Buang nilai persetujuan dari vals dan timpa default_* konteks RPC: payment baru selalu belum disetujui.
            vals_list = [
                {**{k: v for k, v in vals.items() if k not in APPROVAL_FIELDS}, **APPROVAL_RESET} for vals in vals_list
            ]
        return super().create(vals_list)

    def write(self, vals):
        system = self._efn_is_system()
        if not system and APPROVAL_FIELDS.intersection(vals):
            raise AccessError(_("Field persetujuan pembayaran hanya bisa diubah lewat tombol persetujuan."))
        content = CONTENT_FIELDS.intersection(vals)
        watched = self.browse()
        before = {}
        if content and not system:
            watched = self.filtered(lambda p: p.efn_approval_state in ("to_approve", "approved"))
            before = {payment.id: {fname: payment[fname] for fname in content} for payment in watched}
        res = super().write(vals)
        changed = watched.filtered(lambda p: any(p[fname] != value for fname, value in before[p.id].items()))
        if changed:
            changed._efn_system_write(APPROVAL_RESET)
            for payment in changed:
                payment.message_post(body=_("Isi pembayaran diubah: persetujuan gugur, minta persetujuan ulang."))
        return res

    # ------------------------------------------------------------------------------------------------- gerbang
    def _efn_is_internal_transfer(self):
        # CE 19 tidak punya `is_internal_transfer` (dicek di account_payment.py 19.0). SEC-G60-01: hanya bila partner =
        # partner PT sendiri DAN akun tujuan = akun transfer antar-bank (keduanya; satu field saja bisa dipalsukan).
        self.ensure_one()
        company = self.company_id
        transfer = company.transfer_account_id
        return bool(transfer and self.partner_id == company.partner_id and self.destination_account_id == transfer)

    def _efn_needs_approval(self):
        self.ensure_one()
        company = self.company_id
        threshold = company.efn_payment_approval_threshold
        if self.payment_type != "outbound" or not company or threshold <= 0:
            return False
        # SEC-G60-01: pengecualian POS hanya di alur sistem (tutup sesi berjalan sudo, pos_session.py
        # `_validate_session`); `pos_session_id` yang ditulis user lewat RPC tidak membebaskan.
        if self.pos_session_id and self.env.su:
            return False
        if self._efn_is_internal_transfer():
            return False
        return company.currency_id.compare_amounts(abs(self.amount_company_currency_signed), threshold) >= 0

    def _efn_validly_approved(self):
        self.ensure_one()
        approver = self.efn_approved_by_id
        return (
            self.efn_approval_state == "approved"
            and bool(approver)
            and approver not in (self.create_uid | self.efn_approval_requested_by_id)
        )

    def action_post(self):
        blocked = self.filtered(lambda p: p._efn_needs_approval() and not p._efn_validly_approved())
        if blocked:
            raise UserError(
                _(
                    "Pembayaran %s bernilai di atas ambang persetujuan PT: minta persetujuan Accounting Manager "
                    "(tombol 'Minta Persetujuan') sebelum dikonfirmasi.",
                    ", ".join(blocked.mapped(lambda p: p.name or p.display_name)),
                )
            )
        return super().action_post()

    def action_draft(self):
        res = super().action_draft()
        # Persetujuan berlaku untuk nilai yang disetujui; setelah reset (bisa diedit) persetujuan gugur.
        self.filtered(lambda p: p.efn_approval_state == "approved")._efn_system_write(APPROVAL_RESET)
        return res

    # --------------------------------------------------------------------------------------------- persetujuan
    def _efn_check_company_access(self):
        foreign = self.company_id - self.env.user.company_ids
        if foreign:
            raise AccessError(_("Anda tidak punya akses ke PT %s.", ", ".join(foreign.mapped("name"))))

    def _efn_check_approver(self):
        user = self.env.user
        if not user.has_group(APPROVER_GROUP):
            raise AccessError(_("Hanya Accounting Manager yang dapat menyetujui/menolak pembayaran."))
        self.check_access("write")
        self._efn_check_company_access()
        for payment in self:
            if payment.efn_approval_state != "to_approve" or payment.state != "draft":
                raise UserError(_("Pembayaran %s tidak sedang menunggu persetujuan.", payment.display_name))
            if user in (payment.efn_approval_requested_by_id | payment.create_uid):
                raise AccessError(
                    _("Prinsip 4-eyes: peminta/pembuat pembayaran %s tidak boleh memutuskan.", payment.display_name)
                )

    def _efn_set_to_approve(self):
        self._efn_system_write(
            {
                "efn_approval_state": "to_approve",
                "efn_approval_requested_by_id": self.env.uid,
                "efn_approved_by_id": False,
                "efn_approved_at": False,
            }
        )
        for payment in self:
            payment.message_post(body=_("Persetujuan pembayaran keluar diminta (di atas ambang PT)."))

    def action_efn_request_approval(self):
        self.check_access("write")
        self._efn_check_company_access()
        for payment in self:
            if payment.state != "draft" or payment.efn_approval_state not in ("not_required", "rejected"):
                raise UserError(_("Persetujuan hanya bisa diminta untuk pembayaran draft: %s", payment.display_name))
            if not payment._efn_needs_approval():
                raise UserError(_("Pembayaran %s tidak memerlukan persetujuan.", payment.display_name))
        self._efn_set_to_approve()
        return True

    def action_efn_approve(self):
        self._efn_check_approver()
        self._efn_system_write(
            {
                "efn_approval_state": "approved",
                "efn_approved_by_id": self.env.uid,
                "efn_approved_at": fields.Datetime.now(),
            }
        )
        for payment in self:
            payment.message_post(body=_("Pembayaran disetujui."))
        self.action_post()
        self._efn_reconcile_matched_moves()
        return True

    def action_efn_reject(self):
        self._efn_check_approver()
        self._efn_system_write(
            {
                "efn_approval_state": "rejected",
                "efn_approved_by_id": self.env.uid,
                "efn_approved_at": fields.Datetime.now(),
            }
        )
        for payment in self:
            payment.message_post(body=_("Pembayaran ditolak; tetap draft agar bisa diperbaiki."))
        return True

    def _efn_reconcile_matched_moves(self):
        """Payment dari wizard Register Payment yang ditahan (to_approve) tidak direkonsiliasi CE karena belum posted;
        CE hanya menautkan ``matched_payment_ids``. Setelah disetujui & posted, rekonsiliasi dengan dokumen itu
        (logika sama dengan ``account.payment.register._reconcile_payments``)."""
        domain = [
            ("parent_state", "=", "posted"),
            ("account_type", "in", self._get_valid_payment_account_types()),
            ("reconciled", "=", False),
        ]
        for payment in self:
            if not payment.move_id or payment.move_id.state != "posted":
                continue
            documents = self.env["account.move"].search([("matched_payment_ids", "in", payment.ids)])
            if not documents:
                continue
            payment_lines = payment.move_id.line_ids.filtered_domain(domain)
            document_lines = documents.line_ids.filtered_domain(domain)
            for account in payment_lines.account_id:
                (payment_lines + document_lines).filtered_domain(
                    [("account_id", "=", account.id), ("reconciled", "=", False)]
                ).reconcile()

    # ------------------------------------------------------------------------------------- helper voucher (port)
    def _efn_voucher_allocations(self, line):
        """Yang dilunasi baris ini per dokumen: ``[(dokumen, amount, amount_currency)]`` (dari partial)."""
        allocations = []
        for partial in line.matched_debit_ids:
            allocations.append((partial.debit_move_id.move_id, partial.amount, abs(partial.credit_amount_currency)))
        for partial in line.matched_credit_ids:
            allocations.append((partial.credit_move_id.move_id, partial.amount, abs(partial.debit_amount_currency)))
        return [item for item in allocations if item[0].move_type in INVOICE_TYPES]

    def _efn_voucher_row(self, line, doc_ap, ref_vendor, debit, credit, orig_amount):
        rate = 1.0
        if line.amount_currency and line.balance:
            rate = abs(line.balance) / abs(line.amount_currency)
        return {
            "coa": line.account_id.display_name or line.name or "",
            "desc": line.name or "",
            "doc_ap": doc_ap or "",
            "ref_vendor": ref_vendor or "",
            "vendor": (line.partner_id or self.partner_id).name or "",
            "currency": self.currency_id.name or "",
            "orig_amount": orig_amount,
            "rate": rate,
            "debit": debit,
            "credit": credit,
        }

    def _efn_voucher_rows(self):
        """Tabel voucher: satu baris per item jurnal payment, dipecah per dokumen yang dilunasi; sisa (payment >
        dokumen / pembulatan) tetap satu baris atas nomor payment sehingga total DEBIT = KREDIT = jurnal."""
        self.ensure_one()
        rows = []
        if not self.move_id:
            return rows
        currency = self.company_id.currency_id
        fallback_ref = self.payment_reference or self.memo or ""
        lines = self.move_id.line_ids.filtered(lambda line: line.display_type not in ("line_section", "line_note"))
        for line in lines:
            allocations = self._efn_voucher_allocations(line)
            if not allocations:
                orig = abs(line.amount_currency) if line.amount_currency else abs(line.balance)
                rows.append(self._efn_voucher_row(line, self.name, fallback_ref, line.debit, line.credit, orig))
                continue
            is_debit = bool(line.debit)
            allocated = 0.0
            for document, amount, amount_currency in allocations:
                allocated += amount
                rows.append(
                    self._efn_voucher_row(
                        line,
                        document.name,
                        document.ref,
                        amount if is_debit else 0.0,
                        0.0 if is_debit else amount,
                        amount_currency or amount,
                    )
                )
            remainder = (line.debit or line.credit) - allocated
            if currency.compare_amounts(remainder, 0.0) > 0:
                rows.append(
                    self._efn_voucher_row(
                        line,
                        self.name,
                        fallback_ref,
                        remainder if is_debit else 0.0,
                        0.0 if is_debit else remainder,
                        remainder,
                    )
                )
        return rows

    def _efn_voucher_recipient_bank(self):
        """Rekening tujuan; nomor dimasking kecuali Accounting Manager (Q-G60-3, UU PDP)."""
        self.ensure_one()
        bank = self.partner_bank_id
        if not bank:
            return {"bank_name": "", "acc_number": "", "acc_holder": "", "branch": "", "swift": ""}
        acc_number = bank.acc_number or ""
        if not self.env.user.has_group(APPROVER_GROUP):
            acc_number = mask_account_number(acc_number)
        return {
            "bank_name": bank.bank_id.name or "",
            "acc_number": acc_number,
            "acc_holder": bank.acc_holder_name or bank.partner_id.name or "",
            "branch": bank.bank_id.street or "",
            "swift": bank.bank_id.bic or "",
        }

    def _efn_voucher_source_bank_account(self):
        """Rekening bank PT sumber dana (bukan data pribadi; tidak dimasking)."""
        self.ensure_one()
        account = self.journal_id.bank_account_id
        if not account:
            return ""
        return " - ".join(part for part in (account.acc_number, account.bank_id.name) if part)

    def _efn_voucher_amount_in_words(self):
        self.ensure_one()
        suffix = "Rupiah" if self.currency_id.name == "IDR" else (self.currency_id.currency_unit_label or "")
        return terbilang_id(self.amount, suffix=suffix)
