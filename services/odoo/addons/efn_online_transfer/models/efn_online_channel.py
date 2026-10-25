"""G26 §17.6: jurnal bank tujuan transfer per kanal + rekening yang ditampilkan ke pelanggan."""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class EfnOnlineChannel(models.Model):
    _inherit = "efn.online.channel"

    efn_transfer_journal_id = fields.Many2one(
        "account.journal",
        string="Jurnal transfer bank",
        domain="[('type', '=', 'bank'), ('company_id', '=', company_id)]",
        check_company=True,
        help="G26 (docs/04 §17.6): rekening bank PT penjual untuk metode 'bank_transfer'. Nama bank, nomor rekening, "
        "dan pemilik rekening diambil dari Rekening Bank jurnal ini dan ditampilkan ke pelanggan. Pelunasan dicatat "
        "di jurnal ini setelah Verifikator menyetujui bukti.",
    )

    @api.constrains("payment_modes", "efn_transfer_journal_id")
    def _check_efn_transfer_journal(self):
        for channel in self:
            if "bank_transfer" not in channel._efn_online_payment_modes():
                continue
            if channel._efn_online_transfer_account() is None:
                raise ValidationError(
                    _(
                        "Metode 'bank_transfer' membutuhkan jurnal transfer bank dengan Rekening Bank lengkap "
                        "(bank, nomor rekening, pemilik rekening) milik PT kanal yang sudah diverifikasi Finance "
                        "(Rekening Bank: 'Send Money' / trusted)."
                    )
                )

    def _efn_online_transfer_account(self):
        self.ensure_one()
        journal = self.sudo().efn_transfer_journal_id
        if not journal or journal.type != "bank" or journal.company_id != self.company_id:
            return None
        account = journal.bank_account_id
        if not account:
            return None
        bank_name = (account.bank_id.name or "").strip()
        number = (account.acc_number or "").replace(" ", "").strip()
        holder = (account.acc_holder_name or account.partner_id.name or "").strip()
        # Rekening milik PT (partner perusahaan), bukan rekening pribadi (efn_pii melindungi rekening orang).
        if account.partner_id.commercial_partner_id != self.company_id.partner_id.commercial_partner_id:
            return None
        if not (bank_name and number.isdigit() and 6 <= len(number) <= 20 and holder):
            return None
        # Rekening yang belum diverifikasi (CE `allow_out_payment`) tidak boleh ditampilkan ke pelanggan: nomor rekening
        # palsu adalah pola phishing, dan faktur ber-rekening untrusted ditolak saat posting (persetujuan gagal).
        if not account.allow_out_payment:
            return None
        return {
            "bank_name": bank_name[:64],
            "account_number": number,
            "account_holder": holder[:128],
        }
