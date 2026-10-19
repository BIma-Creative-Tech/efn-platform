"""K-a (ADR-036 §0.2, Q-PII7, 18-40) + kelas A (akun portal vendor, Q-PII8, K-36-10).

- Partner milik user internal (`share=False`) = identitas kerja, plaintext. Partner user dibuat lewat `_inherits` di
  dalam `res.users.create` dengan context server `efn_pii_skip_default` (nilai `pii_model.SKIP_TOKEN`, tidak bisa
  dibuat klien RPC). Setelah create, partner milik user portal (`share=True`, kelas A) langsung dijadikan terlindungi
  (enkripsi nilai yang baru ditulis, transaksi yang sama).
- **Login portal opak (Q-PII8 default):** `res.users.login` tidak dienkripsi (unik, dipakai autentikasi), sehingga login
  akun portal tidak boleh berupa email. Akun `share=True` yang login-nya memuat `@` (mis. `portal.wizard` 19.0 memakai
  email sebagai login) diganti `vnd-XXXXXX` (alfabet tanpa huruf/angka mirip). Alur portal standar tidak diubah
  selain itu: undangan/reset kata sandi tetap dikirim ke email partner (terenkripsi + blind index), dan email undangan
  memuat login opak hasil penggantian.
Residu: tuple mati berisi plaintext (nama partner portal, login email sebelum diganti) di heap/WAL sampai VACUUM, dan
log audit `efn_audit` perubahan login (nilai email di-mask oleh efn_audit).
"""

import secrets

from odoo import api, models
from odoo.tools import SQL

from . import pii_model as pm
from .fields_pii import PROTECTED_FIELD

GROUP_KEYS = ("group_ids", "groups_id", "share")
OPAQUE_PREFIX = "vnd-"
OPAQUE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
OPAQUE_LENGTH = 6
OPAQUE_ATTEMPTS = 20


def new_opaque_login():
    return OPAQUE_PREFIX + "".join(secrets.choice(OPAQUE_ALPHABET) for _i in range(OPAQUE_LENGTH))


class ResUsers(models.Model):
    _inherit = "res.users"

    @api.model_create_multi
    def create(self, vals_list):
        users = super(ResUsers, self.with_context(**{pm.SKIP_CONTEXT_KEY: pm.SKIP_TOKEN})).create(vals_list)
        users = users.with_env(self.env)
        users._efn_pii_protect_share_partners()
        return users

    def write(self, vals):
        res = super().write(vals)
        if "login" in vals or any(key in GROUP_KEYS or key.startswith(("sel_groups_", "in_group_")) for key in vals):
            self._efn_pii_protect_share_partners()
        return res

    def _efn_pii_protect_share_partners(self):
        """Partner user portal/share (kelas A) wajib terlindungi; login portal berupa email diganti ID opak."""
        share_users = self.sudo().filtered("share")
        partners = share_users.partner_id.filtered(lambda p: not p[PROTECTED_FIELD])
        if partners:
            partners._efn_pii_protect()
        for user in share_users.filtered(lambda u: u.login and "@" in u.login):
            super(ResUsers, user).write({"login": user._efn_pii_unique_opaque_login()})

    def _efn_pii_unique_opaque_login(self):
        self.env["res.users"].flush_model(["login"])
        for _attempt in range(OPAQUE_ATTEMPTS):
            login = new_opaque_login()
            self.env.cr.execute(SQL("SELECT 1 FROM res_users WHERE login = %s", login))
            if not self.env.cr.fetchone():
                return login
        raise RuntimeError("login opak tidak unik")  # 32^6 ~ 1e9 kombinasi: praktis tidak terjadi
