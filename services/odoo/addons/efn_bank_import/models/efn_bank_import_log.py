"""Log impor mutasi bank (port ADR-049 dari `custom_bank_import/models/bank_import_log.py`).

Perubahan EFN: `company_id` = company jurnal (stored) + record rule; immutable setelah dibuat (hanya `active` yang
boleh diubah); hitungan duplikat/baris nol; tanpa `raw_payload` (H2H dibuang); pesan error berupa kode per baris.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

#: Field yang boleh ditulis setelah log dibuat (arsip). Field mail.thread (`message_*`) dikelola chatter.
_WRITABLE = frozenset({"active"})
#: Penulisan internal wizard impor (sudo + context) untuk menautkan rekening koran yang baru dibuat.
_INTERNAL_CTX = "efn_bank_import_internal"


class EfnBankImportLog(models.Model):
    _name = "efn.bank.import.log"
    _description = "EFN Log Impor Mutasi Bank"
    _inherit = ["mail.thread"]
    _order = "create_date desc, id desc"
    _check_company_auto = True

    name = fields.Char(compute="_compute_name", store=True)
    active = fields.Boolean(default=True)
    journal_id = fields.Many2one(
        "account.journal", required=True, ondelete="restrict", check_company=True, domain=[("type", "=", "bank")]
    )
    company_id = fields.Many2one(related="journal_id.company_id", store=True, index=True)
    template_id = fields.Many2one("efn.bank.import.template", required=True, ondelete="restrict", check_company=True)
    statement_id = fields.Many2one("account.bank.statement", ondelete="set null", check_company=True)
    filename = fields.Char()
    file_hash = fields.Char(index=True, help="sha256 isi file (dedup). File aslinya tidak disimpan.")
    imported_by_id = fields.Many2one("res.users", default=lambda self: self.env.user, readonly=True)
    state = fields.Selection(
        [("imported", "Berhasil"), ("partial", "Sebagian"), ("failed", "Gagal")],
        default="imported",
        required=True,
        index=True,
    )
    line_count = fields.Integer(string="Baris Diimpor")
    duplicate_count = fields.Integer(string="Baris Duplikat Dilewati")
    skipped_zero = fields.Integer(string="Baris Nol Dilewati")
    error_count = fields.Integer(string="Baris Error")
    error_message = fields.Text(help="Format `baris N: <kode>` (tanpa isi sel). Maksimal 200 baris.")

    @api.depends("journal_id", "filename")
    def _compute_name(self):
        for log in self:
            log.name = f"{log.journal_id.code or ''} {log.filename or _('Impor')}".strip()

    def write(self, vals):
        blocked = {key for key in vals if key not in _WRITABLE and not key.startswith("message_")}
        if blocked and not (self.env.su and self.env.context.get(_INTERNAL_CTX)):
            raise UserError(_("Log impor mutasi tidak dapat diubah (jejak audit). Hanya arsip yang diizinkan."))
        return super().write(vals)
