"""G22 L-79 (docs/04 §14.7): akun pembayaran jurnal "Poin EFN" per PT.

Jurnal bank "Poin EFN" (``data/pos_loyalty_points_payment.xml``) dipakai juga oleh pesanan online (W-12): porsi Poin EFN
dicatat sebagai ``account.payment`` di jurnal ini. Odoo 19 hanya membuat jurnal akuntansi pembayaran bila baris metode
pembayaran jurnal punya ``payment_account_id`` (``account_payment._generate_journal_entry``, [V] source 19.0). Karena
itu setiap install/upgrade (``<function>`` noupdate=0) baris metode masuk & keluar jurnal Poin EFN yang MASIH KOSONG
diisi akun liabilitas/kliring Poin EFN PT itu (= ``outstanding_account_id`` metode POS, sumber kebenaran yang sama
dengan penutupan sesi POS). Baris yang sudah diisi Finance tidak diubah. Modul ``efn_online_order`` TIDAK menulis
konfigurasi ini saat runtime (L-79): akun kosong -> penukaran poin online ditolak.
"""

import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class PosPaymentMethod(models.Model):
    _inherit = "pos.payment.method"

    @api.model
    def _efn_setup_points_journal_accounts(self):
        """Isi ``payment_account_id`` baris metode jurnal Poin EFN yang kosong. Kembalikan baris yang diisi."""
        Line = self.env["account.payment.method.line"]
        filled = Line.browse()
        methods = self.sudo().with_context(active_test=False).search([("efn_is_loyalty_points", "=", True)])
        for method in methods:
            journal = method.journal_id
            account = method.outstanding_account_id
            if not journal or not account:
                _logger.warning("EFN setup POS: metode Poin EFN %s tanpa jurnal/akun liabilitas", method.id)
                continue
            lines = journal.inbound_payment_method_line_ids | journal.outbound_payment_method_line_ids
            empty = lines.filtered(lambda line: not line.payment_account_id)
            if empty:
                empty.write({"payment_account_id": account.id})
                filled |= empty
        if filled:
            _logger.info("EFN setup POS: akun pembayaran jurnal Poin EFN diisi untuk baris %s", filled.ids)
        return filled
