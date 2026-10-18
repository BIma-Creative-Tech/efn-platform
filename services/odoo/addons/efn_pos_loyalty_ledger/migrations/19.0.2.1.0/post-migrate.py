"""19.0.2.1.0 (review r11 SEC-115): penanda member tahan lama dari riwayat redeem/earn yang ada."""


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        """
        UPDATE res_partner p SET efn_member_locked_at = now() at time zone 'UTC'
         WHERE p.efn_member_locked_at IS NULL
           AND (EXISTS (SELECT 1 FROM efn_pos_loyalty_reservation r WHERE r.partner_id = p.id)
                OR EXISTS (SELECT 1 FROM pos_order o WHERE o.partner_id = p.id AND o.efn_loyalty_state IS NOT NULL))
        """
    )
