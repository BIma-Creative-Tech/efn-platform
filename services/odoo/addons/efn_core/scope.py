"""Penanda "jalur internal resmi" per cursor (bukan context RPC yang dikendalikan klien).

Pola SEC-44/SEC-104 (`cr.cache`): guard server dilewati hanya bila kode server di transaksi yang sama menandai
dirinya berjalan di jalur resmi (mis. sync order POS). Disimpan sebagai atribut objek cursor (bukan `cr.cache`,
yang dikosongkan saat rollback savepoint di tengah sync) sehingga tidak bisa diisi lewat
``/web/dataset/call_kw`` / ``/json/2``; dibersihkan di ``finally``. Bukan model (tidak ada metode RPC).
"""

from contextlib import contextmanager

_ATTR = "_efn_internal_scopes"


def _scopes(cr):
    scopes = getattr(cr, _ATTR, None)
    if scopes is None:
        scopes = {}
        setattr(cr, _ATTR, scopes)
    return scopes


# Sync order POS resmi (`pos.order._process_order`): pembayaran boleh dibuat/diubah (review r11 SEC-113).
POS_PAYMENT_SYNC_SCOPE = "efn_pos_payment_sync"


@contextmanager
def internal_scope(env, key):
    """Tandai jalur internal; bisa bersarang (pencacah)."""
    scopes = _scopes(env.cr)
    scopes[key] = scopes.get(key, 0) + 1
    try:
        yield
    finally:
        remaining = scopes.get(key, 1) - 1
        if remaining > 0:
            scopes[key] = remaining
        else:
            scopes.pop(key, None)


def in_scope(env, key):
    return bool(_scopes(env.cr).get(key))
