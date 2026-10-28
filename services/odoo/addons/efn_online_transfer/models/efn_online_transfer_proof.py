"""G26 §17.5/§17.15: bukti transfer bank pesanan online + keputusan Verifikator Pembayaran.

Data antrean (nomor SO, gerai, nominal, batas waktu, rekening tujuan) disalin saat bukti diterima agar Verifikator
cukup punya hak atas model ini (tanpa hak baca ``efn.online.order``/``sale.order``). Foto disimpan sebagai
``ir.attachment`` milik record ini (tidak publik, tidak tampil di chatter); hanya grup Verifikator + Auditor yang
membaca. Field record tidak memuat nama/nomor HP pelanggan, tetapi FOTO bukti adalah data pribadi (nama & rekening
pengirim): BFF tidak menyimpannya, Odoo satu-satunya penyimpan (docs/04 §17.4 no. 4).
"""

from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError

REJECT_REASONS = [
    ("amount_mismatch", "Nominal tidak sesuai"),
    ("unreadable", "Foto tidak terbaca"),
    ("wrong_account", "Rekening tujuan salah"),
    ("not_received", "Dana belum diterima di mutasi"),
    ("duplicate_proof", "Bukti dipakai ulang / bukan milik pesanan ini"),
]
PROOF_STATES = [
    ("awaiting_verification", "Menunggu verifikasi"),
    ("approved", "Disetujui"),
    ("rejected", "Ditolak"),
    ("superseded", "Diganti bukti baru"),
    ("expired", "Kedaluwarsa"),
]
VERIFIER_GROUP = "efn_online_transfer.group_payment_verifier"
# `expired` tetap bisa DISETUJUI (dana ternyata masuk setelah pesanan batal/kedaluwarsa -> review + refund Finance,
# docs/04 §17.5 no. 3); tidak bisa ditolak (tidak ada yang perlu diunggah ulang).
DECIDABLE_STATES = ("awaiting_verification", "expired")


class EfnOnlineTransferProof(models.Model):
    _name = "efn.online.transfer.proof"
    _description = "EFN Online - Bukti transfer bank"
    _order = "uploaded_at desc, id desc"
    _check_company_auto = True
    _rec_name = "sale_order_name"

    company_id = fields.Many2one("res.company", required=True, readonly=True, index=True)
    order_id = fields.Many2one(
        "efn.online.order",
        required=True,
        readonly=True,
        index=True,
        ondelete="restrict",
        check_company=True,
    )
    proof_id = fields.Char(required=True, readonly=True, index=True, help="UUID bukti dari BFF (opak).")
    idempotency_key = fields.Char(required=True, readonly=True)
    payload_sha256 = fields.Char(readonly=True)
    sha256 = fields.Char(string="SHA-256 foto", required=True, readonly=True, index=True)
    duplicate_suspect = fields.Boolean(
        string="Diduga duplikat",
        readonly=True,
        help="BFF menemukan foto yang sama/mirip pada bukti pesanan lain (90 hari). Periksa mutasi dengan teliti.",
    )
    uploaded_at = fields.Datetime(required=True, readonly=True)
    state = fields.Selection(
        PROOF_STATES,
        required=True,
        default="awaiting_verification",
        readonly=True,
        index=True,
    )
    attachment_id = fields.Many2one("ir.attachment", readonly=True, ondelete="restrict", copy=False)
    image = fields.Binary(string="Foto bukti", compute="_compute_image")
    # Salinan untuk antrean (tanpa PII).
    sale_order_name = fields.Char(string="Nomor pesanan", readonly=True)
    outlet_name = fields.Char(string="Gerai", readonly=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    amount_due = fields.Monetary(string="Nominal yang harus ditransfer", readonly=True)
    transfer_ref = fields.Char(string="Kode berita transfer", readonly=True)
    expires_at = fields.Datetime(string="Batas waktu", readonly=True)
    account_label = fields.Char(string="Rekening tujuan", readonly=True)
    # Keputusan.
    decided_by = fields.Many2one("res.users", readonly=True)
    decided_at = fields.Datetime(readonly=True)
    reject_reason = fields.Selection(REJECT_REASONS, readonly=True)
    bank_date = fields.Date(string="Tanggal mutasi", readonly=True)
    bank_amount = fields.Monetary(string="Nominal mutasi", readonly=True)
    bank_ref = fields.Char(string="Referensi mutasi", readonly=True)
    note = fields.Text(
        string="Catatan internal",
        readonly=True,
        help="Tidak pernah dikirim ke pelanggan/BFF.",
    )
    order_state = fields.Selection(related="order_id.state", string="Status pesanan")

    _proof_uniq = models.Constraint("unique(proof_id)", "Bukti transfer sudah terdaftar.")
    _key_uniq = models.Constraint("unique(idempotency_key)", "Kunci idempotensi bukti sudah dipakai.")

    def _compute_image(self):
        for proof in self:
            proof.image = proof.sudo().attachment_id.datas if proof.attachment_id else False

    # ------------------------------------------------------------------ akses staf
    def _efn_check_verifier(self):
        if not (self.env.su or self.env.user.has_group(VERIFIER_GROUP)):
            raise AccessError(_("Hanya Verifikator Pembayaran yang boleh memutuskan bukti transfer."))
        self.check_access("write")

    def action_efn_open_decide(self):
        self.ensure_one()
        self._efn_check_verifier()
        if self.state not in DECIDABLE_STATES:
            raise UserError(_("Bukti ini sudah diputuskan."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Putuskan bukti transfer"),
            "res_model": "efn.online.transfer.decide",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_proof_id": self.id,
                "default_bank_amount": self.amount_due,
            },
        }

    # --------------------------------------------------------------- keputusan
    def _efn_decide(
        self,
        decision,
        reason=None,
        bank_date=None,
        bank_amount=None,
        bank_ref=None,
        note=None,
    ):
        """Keputusan Verifikator. ``decision`` = ``approve`` | ``reject``. Dipanggil dari wizard setelah cek akses."""
        self.ensure_one()
        self._efn_check_verifier()
        user = self.env.user
        proof = self.sudo()
        order = proof.order_id.with_company(proof.company_id)
        order._efn_lock()
        proof.invalidate_recordset()
        if proof.state not in DECIDABLE_STATES or (decision == "reject" and proof.state != "awaiting_verification"):
            raise UserError(_("Bukti ini sudah diputuskan atau tidak berlaku lagi."))
        now = fields.Datetime.now()
        base = {
            "decided_by": user.id,
            "decided_at": now,
            "note": (note or "").strip()[:500] or False,
        }
        if decision == "reject":
            if reason not in dict(REJECT_REASONS):
                raise UserError(_("Pilih alasan penolakan."))
            proof.write({**base, "state": "rejected", "reject_reason": reason})
            if order.state == "placed":
                order._efn_emit("transfer_rejected", detail_code=reason)
            return {"proof_state": "rejected", "order_state": order.state}
        if decision != "approve":
            raise UserError(_("Keputusan tidak dikenal."))
        if not (bank_date and bank_ref and bank_amount is not None):
            raise UserError(_("Tanggal, nominal, dan referensi mutasi wajib diisi."))
        if order.currency_id.compare_amounts(bank_amount, order.amount_due) != 0:
            raise UserError(_("Nominal mutasi harus sama persis dengan nominal pesanan. Tolak bila berbeda."))
        proof.write(
            {
                **base,
                "state": "approved",
                "bank_date": bank_date,
                "bank_amount": bank_amount,
                "bank_ref": bank_ref.strip()[:64],
            }
        )
        return order._efn_transfer_mark_paid(proof)
