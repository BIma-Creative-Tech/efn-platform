"""SEC-07 (sisa, review r3): kunci nomor HP partner yang sudah dipakai sebagai member.

Fit-gap (docs/08 E-6, standar Odoo 19.0 terverifikasi di image `efn-odoo:ci`):
- `base/security/ir.model.access.csv`: `res.partner` create/write hanya untuk
  `base.group_partner_manager` ("Contact Creation"); `base.group_user` baca saja.
- POS membuat/mengubah pelanggan lewat form standar (`point_of_sale.res_partner_action_edit_pos`
  -> `base.view_partner_form`), jadi tunduk pada ACL itu.
- Standar hanya biner: kasir boleh membuat DAN mengubah semua partner (termasuk HP), atau tidak
  sama sekali. Onboarding pelanggan baru di kasir butuh hak create, sementara SEC-07 butuh HP
  member tidak bisa diganti kasir -> gap kecil, ditutup di sini.

Aturan:
- Partner "member terpakai" = punya riwayat OTP (`efn.pos.otp.challenge.partner_id`, retensi
  30 hari) atau order dengan `efn_otp_gate_state` (consumed/review, permanen). OTP hanya bisa
  diminta untuk HP yang valid sebagai `member_ref` (dasar `efn_member_hash`), jadi riwayat ini
  sekaligus berarti "punya member_hash"; dicek tanpa pepper agar kunci tidak gagal-terbuka saat
  pepper belum terpasang.
- Mengubah/mengosongkan `phone` (dan `mobile` bila ada modul yang menambahkannya) partner tsb
  hanya untuk `point_of_sale.group_pos_manager` atau `base.group_system`; selain itu
  `AccessError`. Keputusan SELALU memakai uid asli (`env.user`), juga di dalam `sudo()`
  (SEC-44, review r4): kode server yang menulis HP dengan `sudo()` atas nama kasir ikut ditolak.
  Proses internal sah berjalan sebagai OdooBot (`SUPERUSER_ID`, mis. cron erasure UU PDP) dan
  lolos lewat `_is_superuser()`; kode kustom yang perlu mengubah HP member atas nama sistem
  harus memakai `with_user(SUPERUSER_ID)` secara eksplisit (tercatat sebagai OdooBot di
  efn_audit), bukan sekadar `sudo()`.
- Perubahan oleh manajer dicatat di chatter partner (nomor ter-mask, UU PDP) + log aplikasi
  tanpa nomor. Jejak audit formal: `efn_audit` (aturan res.partner di `efn_setup_audit`,
  HP ter-mask) — hook `base` berada di dasar MRO sehingga melihat `vals` final setelah
  override ini (ADR-031; tidak ada lagi monkeypatch registry ala OCA auditlog).

Dua lapis (keduanya perlu):
1. `write()` — cek lebih awal (hanya bila nilai benar-benar berubah) + catatan chatter.
2. `@api.constrains("phone")` — jaring pengaman yang tidak bisa dilewati, termasuk jalur yang
   tidak melewati override (1) (mis. pemanggilan `write` kelas dasar langsung atau modul lain
   yang menulis lewat `sudo()` dengan uid kasir). Constraint dijalankan oleh `BaseModel.write`
   (selalu dengan sudo, jadi keputusan memakai uid asli; `sudo()` dengan uid kasir ikut
   ditolak = fail-closed). Penanda "sudah dicek lapis 1" disimpan di `cr.cache` (bukan
   context, yang bisa diisi klien lewat RPC) dan HANYA diisi setelah pemeriksaan lapis 1
   benar-benar dijalankan dan lolos (SEC-44: dulu diisi juga saat lapis 1 dilewati karena sudo).
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

from odoo.addons.efn_core.models.efn_bff_client import mask_phone, normalize_member_ref

_logger = logging.getLogger(__name__)

PHONE_FIELDS = ("phone", "mobile")
# Review r11 SEC-115 (L-25c): HP member hanya boleh diubah lewat alur resmi (OTP pemilik nomor lama + baru,
# belum ada di Odoo) atau ADMIN teraudit (Settings; chatter + efn_audit). Manajer POS tidak lagi cukup.
MANAGER_GROUPS = ("base.group_system",)
MEMBER_FLAG = "efn_member_locked_at"
# Kunci di `cr.cache` (per cursor/transaksi, tidak bisa diisi klien lewat context RPC): id partner
# yang sudah diperiksa lapis 1 sehingga constraint (lapis 2) tidak memeriksa ulang.
CHECKED_KEY = "efn_member_phone_checked"


def _mask(value):
    if not value:
        return "-"
    return mask_phone(normalize_member_ref(value) or value)


class ResPartner(models.Model):
    _inherit = "res.partner"

    # SEC-115 (L-25c): penanda tahan lama "partner ini member" (tanpa PII). Diisi server (sudo) saat OTP POS apa
    # pun (termasuk `pos_enroll`), reservasi redeem, dan earn; tidak pernah dihapus otomatis, tidak bisa ditulis
    # klien. Kunci HP SEC-07/44 memakai penanda ini (bukan umur challenge OTP yang dihapus setelah 30 hari).
    efn_member_locked_at = fields.Datetime(
        string="Member EFN sejak (kunci HP)",
        readonly=True,
        copy=False,
        groups="point_of_sale.group_pos_user,base.group_system",
        help="Diisi otomatis saat pelanggan memakai OTP/Poin EFN. HP member hanya bisa diubah admin (teraudit).",
    )

    def _efn_mark_member(self):
        """Tandai partner sebagai member (idempoten). Dipanggil kode server setelah OTP/redeem/earn."""
        todo = self.sudo().filtered(lambda p: p.id and not p.efn_member_locked_at)
        if todo:
            todo.with_context(efn_member_marking=True).write({MEMBER_FLAG: fields.Datetime.now()})

    # ----------------------------------------------------------------- write
    def write(self, vals):
        if MEMBER_FLAG in vals and not (self.env.su or self.env.user._is_superuser()):
            # Penanda hanya ditulis server; klien (RPC/kasir/manajer) tidak bisa menghapus/mengisi.
            raise AccessError(_("Penanda member EFN hanya diatur sistem."))
        fnames = [f for f in PHONE_FIELDS if f in vals and f in self._fields]
        if not fnames:
            return super().write(vals)
        # SEC-44: tanpa pengecualian `env.su` — keputusan dari uid asli (`env.user`), sudo atau
        # tidak. Bila sampai baris setelah pemeriksaan, pemeriksaan sudah dijalankan dan lolos.
        # OdooBot (proses sistem: cron erasure, data modul) selalu boleh, tanpa catatan chatter.
        changed = self.browse()
        if not self.env.user._is_superuser():
            members = self._efn_otp_member_partners().sudo()
            changed = members.filtered(lambda p: any((p[f] or False) != (vals[f] or False) for f in fnames))
            if changed and not self._efn_can_change_member_phone():
                self._efn_raise_member_phone_locked(len(changed))
        before = {p.id: {f: p[f] for f in fnames} for p in changed}
        checked = self.env.cr.cache.setdefault(CHECKED_KEY, set())
        added = set(self.ids) - checked
        checked |= added
        try:
            result = super().write(vals)
        finally:
            checked -= added
        for partner in changed:
            changes = ", ".join(f"{f}: {_mask(before[partner.id][f])} → {_mask(partner[f])}" for f in fnames)
            partner.message_post(
                body=_(
                    "Nomor HP member diubah oleh %(user)s (SEC-07). %(changes)s",
                    user=self.env.user.name,
                    changes=changes,
                ),
                message_type="comment",
                subtype_xmlid="mail.mt_note",
                author_id=self.env.user.partner_id.id,
            )
            _logger.info("EFN POS: HP partner member %s diubah oleh user %s (SEC-07)", partner.id, self.env.uid)
        return result

    # ------------------------------------------------------------ constraint
    @api.constrains("phone")
    def _efn_check_member_phone_change(self):
        """Jaring pengaman SEC-07 (lapis 2). Constraint selalu dijalankan Odoo dengan sudo, jadi
        keputusan memakai uid asli (`env.user`); ketat: tanpa pembanding nilai lama."""
        checked = self.env.cr.cache.get(CHECKED_KEY) or ()
        pending = self.filtered(lambda p: p.id not in checked)
        if not pending or self._efn_can_change_member_phone():
            return
        members = pending._efn_otp_member_partners()
        if members:
            self._efn_raise_member_phone_locked(len(members))

    # --------------------------------------------------------------- helpers
    def _efn_can_change_member_phone(self):
        user = self.env.user
        return user._is_superuser() or any(user.has_group(group) for group in MANAGER_GROUPS)

    def _efn_raise_member_phone_locked(self, count):
        _logger.warning("EFN POS: user %s ditolak mengubah HP %d partner member (SEC-07/115)", self.env.uid, count)
        raise AccessError(
            _(
                "Nomor HP pelanggan ini terdaftar sebagai member EFN. "
                "Perubahan hanya boleh dilakukan admin (teraudit) atau lewat verifikasi OTP (SEC-07/SEC-115)."
            )
        )

    def _efn_otp_member_partners(self):
        """Partner di `self` yang member: penanda tahan lama (SEC-115), riwayat OTP/consume (lintas company, sudo)."""
        ids = [i for i in self.ids if i]
        if not ids:
            return self.browse()
        used = set(self.sudo().browse(ids).filtered(MEMBER_FLAG).ids)
        used |= set(
            self.env["efn.pos.otp.challenge"].sudo().search([("partner_id", "in", ids)]).mapped("partner_id").ids
        )
        remaining = [i for i in ids if i not in used]
        if remaining:
            used |= set(
                self.env["pos.order"]
                .sudo()
                .search([("partner_id", "in", remaining), ("efn_otp_gate_state", "!=", False)])
                .mapped("partner_id")
                .ids
            )
        return self.browse([i for i in ids if i in used])
