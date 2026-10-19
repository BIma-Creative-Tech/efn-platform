"""SEC-153 / AC-10 (ADR-036 §0.2): `account.move.invoice_partner_display_name` terenkripsi untuk faktur partner
terlindungi.

Odoo 19.0 (`account/models/account_move.py`, diverifikasi WebFetch 19 Okt 2026):
`invoice_partner_display_name = fields.Char(compute='_compute_invoice_partner_display_info', store=True)`,
`@api.depends('partner_id', 'invoice_source_email', 'partner_id.display_name')` = salinan `display_name` partner
(atau "@From: <email>" / "#Created by: <user>"). Tidak dicari di view standar (filter faktur memakai `partner_id`
`ilike`/`child_of`, `_rec_names_search = ['name', 'partner_id.name', 'ref']`); kolom tampil di daftar faktur.

- Penanda turunan `efn_pii_protected` (stored, precompute, satu arah) = partner atau commercial partner terlindungi.
- Domain `ilike`/`=` atas field ini untuk move terlindungi -> `partner_id IN (partner yang complete_name cocok,
  token nama)` (`_efn_pii_partner_search`); move tidak terlindungi memakai SQL standar.
- Partner yang BARU menjadi terlindungi: faktur lamanya ikut dienkripsi (`res.partner._efn_pii_after_protect`).
- Urutan daftar faktur menurut kolom ini untuk faktur terlindungi = urutan ciphertext (O1, ADR-036 §0.6).
Residu: `invoice_source_email` (vendor bill dari email masuk) plaintext; PDF faktur di filestore (tahap 2).
"""

from odoo import api, fields, models
from odoo.tools import SQL, split_every

from odoo.addons.efn_pii.models import pii_model as pm
from odoo.addons.efn_pii.models.fields_pii import PROTECTED_FIELD, EfnPiiChar

PII_FIELDS = ("invoice_partner_display_name",)


class AccountMove(models.Model):
    _inherit = "account.move"

    _efn_pii_fields = PII_FIELDS
    _efn_pii_partner_search = {"invoice_partner_display_name": "partner_id"}

    efn_pii_protected = fields.Boolean(
        string="EFN: Partner Terlindungi",
        compute="_compute_efn_pii_protected",
        store=True,
        precompute=True,
        index=True,
        copy=False,
        readonly=True,
        groups="base.group_system",
        help="Satu arah (ADR-036 §0.2): nama tampilan partner di faktur disimpan terenkripsi.",
    )
    invoice_partner_display_name = EfnPiiChar()

    # ------------------------------------------------------------------ registry / baca
    def _register_hook(self):
        res = super()._register_hook()
        pm.check_registry(self)
        return res

    def _fetch_query(self, query, fields):
        return pm.scan_fetch(self, lambda: super(AccountMove, self)._fetch_query(query, fields))

    # ------------------------------------------------------------------ penanda
    @api.depends("partner_id")
    def _compute_efn_pii_protected(self):
        existing = [move._origin.id or (move.id if isinstance(move.id, int) else None) for move in self]
        existing = [rid for rid in existing if rid]
        current = set()
        for chunk in split_every(500, existing):
            self.env.cr.execute(SQL("SELECT id FROM account_move WHERE id IN %s AND efn_pii_protected", tuple(chunk)))
            current.update(row[0] for row in self.env.cr.fetchall())
        for move in self:
            rid = move._origin.id or (move.id if isinstance(move.id, int) else None)
            partner = move.partner_id.sudo()
            move.efn_pii_protected = bool(
                (rid and rid in current)
                or (partner and (partner[PROTECTED_FIELD] or partner.commercial_partner_id[PROTECTED_FIELD]))
            )

    # ------------------------------------------------------------------ create / write
    @api.model
    def _create(self, data_list):
        return pm.created(super()._create(data_list))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            pm.check_markers(self, vals)
        records = super().create(vals_list)
        if pm.broken_fields(self) and any(records.sudo().mapped(PROTECTED_FIELD)):
            pm.refuse_broken(self, pm.broken_fields(self))
        pm.bind_new_records(records)
        return records

    def write(self, vals):
        pm.check_markers(self, vals)
        broken = pm.broken_fields(self)
        if broken and pm.vals_touch_broken(self, broken, vals) and any(self.sudo().mapped(PROTECTED_FIELD)):
            pm.refuse_broken(self, broken)
        return super().write(vals)

    # ------------------------------------------------------------------ partner baru terlindungi
    @api.model
    def _efn_pii_protect_for_partners(self, partners):
        if not partners:
            return
        moves = (
            self.sudo()
            .with_context(active_test=False)
            .search(
                [
                    (PROTECTED_FIELD, "=", False),
                    "|",
                    ("partner_id", "in", partners.ids),
                    ("commercial_partner_id", "in", partners.ids),
                ]
            )
        )
        if not moves:
            return
        pm.encrypt_existing(moves)
        for chunk in split_every(1000, moves.ids):
            self.env.cr.execute(SQL("UPDATE account_move SET efn_pii_protected = TRUE WHERE id IN %s", tuple(chunk)))
        moves.invalidate_recordset([PROTECTED_FIELD])
