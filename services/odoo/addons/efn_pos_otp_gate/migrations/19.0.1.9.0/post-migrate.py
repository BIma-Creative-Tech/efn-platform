"""19.0.1.9.0 (review r11 SEC-115): isi penanda member tahan lama dari riwayat yang masih ada.

Partner dengan challenge OTP (belum terhapus autovacuum) atau order promo ber-OTP -> `efn_member_locked_at`.
Riwayat redeem/earn diisi migrasi `efn_pos_loyalty_ledger` 19.0.2.1.0.
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        """
        UPDATE res_partner p SET efn_member_locked_at = now() at time zone 'UTC'
         WHERE p.efn_member_locked_at IS NULL
           AND (EXISTS (SELECT 1 FROM efn_pos_otp_challenge c WHERE c.partner_id = p.id)
                OR EXISTS (SELECT 1 FROM pos_order o WHERE o.partner_id = p.id AND o.efn_otp_gate_state IS NOT NULL))
        """
    )
