"""SEC-49 (Gelombang 13): pembuatan API key tercatat di log audit + sink.

Odoo 19 membuat API key lewat SQL langsung (`res.users.apikeys._generate`: `INSERT ... RETURNING id`),
bukan `create()`, sehingga hook `base.create` tidak terpanggil. Override ini mencatat key baru
SETELAH insert: nama, pemilik (`res.users,id`), scope, kedaluwarsa — tanpa nilai key/hash/index
(bukan field ORM + denylist `efn_audit_rule.DENY_FIELDS`). Penghapusan (`_remove` -> `unlink`)
sudah lewat hook `base.unlink`. Penghapusan key kedaluwarsa oleh autovacuum Odoo (SQL DELETE) tidak
tercatat (batasan, README).
"""

from odoo import models
from odoo.tools import SQL


class ResUsersApikeys(models.Model):
    _inherit = "res.users.apikeys"

    def _generate(self, scope, name, expiration_date):
        key = super()._generate(scope, name, expiration_date)
        rule = self._efn_audit_rule()
        if rule and rule.log_create:
            # Baris terbaru milik user ini di transaksi ini (insert di atas memakai env.user).
            self.env.cr.execute(
                SQL(
                    "SELECT id FROM %s WHERE user_id = %s ORDER BY id DESC LIMIT 1",
                    SQL.identifier(self._table),
                    self.env.user.id,
                )
            )
            row = self.env.cr.fetchone()
            if row:
                records = self.browse(row[0])._efn_audit_filter(rule)
                if records:
                    fnames = sorted(rule.tracked)
                    new = records._efn_audit_read(fnames)
                    records._efn_audit_log("create", rule, {rid: fnames for rid in records.ids}, {}, new)
        return key
