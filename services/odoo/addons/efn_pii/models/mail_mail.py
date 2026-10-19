"""SEC-153 (residu `mail.mail`): penerima email antrean untuk subjek terlindungi tersimpan terenkripsi.

Odoo 19.0 (`mail/models/mail_mail.py`): `_inherits = {'mail.message': 'mail_message_id'}`,
`email_to = fields.Text`, `email_cc = fields.Char`, `recipient_ids` Many2many res.partner. Alamat dibaca lewat ORM saat
kirim (`_prepare_outgoing_list`: `email_normalize_all(self.email_to)`, `_send`), tidak ada domain/SQL atas `email_to`.
Struk POS (`pos.order.action_send_receipt` -> `mail.template.send_mail(..., email_values={'email_to': email})`) membuat
`mail.mail` dengan `model='pos.order'`.

`efn_pii_protected` (satu arah) dihitung di `create` dari: record dokumen (`model`/`res_id`) terlindungi atau model
`_efn_pii_always` (pos.order), penerima (`recipient_ids`) / penulis (`author_id`) partner terlindungi, atau alamat
`email_to`/`email_cc` yang cocok blind index email partner terlindungi. Bila terlindungi: `email_to` (EfnPiiText) dan
`email_cc` (EfnPiiChar) = `efnpii2:`. Mode terkunci: membuat email ke subjek terlindungi ditolak (fail-closed, tanpa
plaintext). `mail.mail` terkirim dengan `auto_delete` tetap dihapus Odoo (`_postprocess_sent_message`).

Residu tetap (didokumentasikan README): `body_html`/`mail.message.body` (isi pesan), `mail.message.email_from`,
`mail.message.incoming_email_to/outgoing_email_to`, `mail.notification.mail_email_address`.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.mail import email_normalize_all

from . import pii_model as pm
from .fields_pii import PROTECTED_FIELD, EfnPiiChar, EfnPiiText

PII_FIELDS = ("email_to", "email_cc")


def _command_ids(value):
    """Id partner dari perintah x2many (6, 4, Command.set/link) atau daftar id."""
    ids = []
    for command in value or ():
        if isinstance(command, int):
            ids.append(command)
        elif isinstance(command, (list, tuple)) and command:
            if command[0] == 6 and len(command) > 2:
                ids.extend(i for i in command[2] or () if isinstance(i, int))
            elif command[0] == 4 and len(command) > 1 and isinstance(command[1], int):
                ids.append(command[1])
    return ids


class MailMail(models.Model):
    _inherit = "mail.mail"

    _efn_pii_fields = PII_FIELDS

    efn_pii_protected = fields.Boolean(
        string="EFN: Penerima Terlindungi",
        readonly=True,
        copy=False,
        help="Satu arah (ADR-036, SEC-153): email ke subjek data pribadi terlindungi; penerima disimpan terenkripsi.",
    )
    email_to = EfnPiiText()
    email_cc = EfnPiiChar()

    # ------------------------------------------------------------------ registry / baca
    def _register_hook(self):
        res = super()._register_hook()
        pm.check_registry(self)
        return res

    def _fetch_query(self, query, fields):
        return pm.scan_fetch(self, lambda: super(MailMail, self)._fetch_query(query, fields))

    # ------------------------------------------------------------------ aturan
    @api.model
    def _efn_pii_partner_protected(self, partner_ids):
        partner_ids = [pid for pid in partner_ids if isinstance(pid, int) and pid]
        if not partner_ids:
            return False
        Partner = self.env["res.partner"].sudo().with_context(active_test=False)
        return bool(Partner.search_count([("id", "in", partner_ids), (PROTECTED_FIELD, "=", True)], limit=1))

    @api.model
    def _efn_pii_vals_protected(self, vals):
        if vals.get(PROTECTED_FIELD):
            return True
        model, res_id = vals.get("model"), vals.get("res_id")
        if not model and vals.get("mail_message_id"):
            message = self.env["mail.message"].sudo().browse(vals["mail_message_id"]).exists()
            model, res_id = message.model, message.res_id
        if model and model in self.env:
            Model = self.env[model]
            if getattr(Model, "_efn_pii_always", False):
                return True
            if res_id and PROTECTED_FIELD in Model._fields:
                record = Model.sudo().browse(res_id).exists()
                if record and record[PROTECTED_FIELD]:
                    return True
        partner_ids = _command_ids(vals.get("recipient_ids"))
        if isinstance(vals.get("author_id"), int):
            partner_ids.append(vals["author_id"])
        if self._efn_pii_partner_protected(partner_ids):
            return True
        emails = set()
        for key in PII_FIELDS:
            if isinstance(vals.get(key), str) and vals[key].strip():
                emails.update(email for email in email_normalize_all(vals[key]) if email)
        if emails:
            Partner = self.env["res.partner"].sudo().with_context(active_test=False)
            domain = [("email_normalized", "in", sorted(emails)), (PROTECTED_FIELD, "=", True)]
            return bool(Partner.search_count(domain, limit=1))
        return False

    # ------------------------------------------------------------------ create / write
    @api.model
    def _create(self, data_list):
        return pm.created(super()._create(data_list))

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [dict(vals) for vals in vals_list]
        for vals in vals_list:
            pm.check_markers(self, vals)
            if self._efn_pii_vals_protected(vals):
                vals[PROTECTED_FIELD] = True
            else:
                vals.pop(PROTECTED_FIELD, None)
        if any(vals.get(PROTECTED_FIELD) for vals in vals_list):
            broken = pm.broken_fields(self)
            if broken:
                pm.refuse_broken(self, broken)
        records = super().create(vals_list)
        pm.bind_new_records(records)
        return records

    def write(self, vals):
        pm.check_markers(self, vals)
        if PROTECTED_FIELD in vals and not vals[PROTECTED_FIELD] and any(self.sudo().mapped(PROTECTED_FIELD)):
            raise UserError(_("Penanda penerima terlindungi bersifat satu arah."))
        broken = pm.broken_fields(self)
        if broken and any(self.sudo().mapped(PROTECTED_FIELD)) and pm.vals_touch_broken(self, broken, vals):
            pm.refuse_broken(self, broken)
        return super().write(vals)

    # ------------------------------------------------------------------ partner baru terlindungi
    @api.model
    def _efn_pii_protect_for_partners(self, partners):
        """Email antrean (belum terkirim/gagal) ke ``partners`` yang baru menjadi terlindungi -> enkripsi penerima."""
        if not partners:
            return
        Mail = self.sudo().with_context(active_test=False)
        mails = Mail.search(
            [
                (PROTECTED_FIELD, "=", False),
                "|",
                ("recipient_ids", "in", partners.ids),
                "&",
                ("model", "=", "res.partner"),
                ("res_id", "in", partners.ids),
            ]
        )
        if not mails:
            return
        pm.encrypt_existing(mails)
        mails.write({PROTECTED_FIELD: True})
