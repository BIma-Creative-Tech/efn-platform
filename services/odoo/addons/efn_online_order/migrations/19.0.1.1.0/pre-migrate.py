"""19.0.1.1.0 (G18-QA-01): `event_id` UUID per event outbox + kunci `online:<platform_order_ref>:<event>:<seq>`.

Kolom diisi SEBELUM ORM membuat constraint NOT NULL/UNIQUE (default callable ORM akan mengisi satu nilai sama
untuk semua baris). Event yang masih `pending` (belum diterima BFF) memakai format kunci baru agar diterima
validasi BFF baru; event `done`/`failed` mempertahankan kunci lama (riwayat).
"""


def migrate(cr, version):
    if not version:
        return
    cr.execute("ALTER TABLE efn_online_event_outbox ADD COLUMN IF NOT EXISTS event_id varchar")
    cr.execute("UPDATE efn_online_event_outbox SET event_id = gen_random_uuid()::text WHERE event_id IS NULL")
    cr.execute(
        """
        UPDATE efn_online_event_outbox x
           SET idempotency_key = concat_ws(':', 'online', o.platform_order_ref, x.event, x.seq)
          FROM efn_online_order o
         WHERE o.id = x.order_id AND x.state = 'pending'
        """
    )
