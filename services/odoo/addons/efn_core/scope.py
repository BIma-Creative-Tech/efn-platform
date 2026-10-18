"""Penanda "jalur internal resmi" per cursor (bukan context RPC yang dikendalikan klien), dan kunci baris.

Pola SEC-44/SEC-104 (`cr.cache`): guard server dilewati hanya bila kode server di transaksi yang sama menandai
dirinya berjalan di jalur resmi (mis. sync order POS). Disimpan sebagai atribut objek cursor (bukan `cr.cache`,
yang dikosongkan saat rollback savepoint di tengah sync) sehingga tidak bisa diisi lewat
``/web/dataset/call_kw`` / ``/json/2``; dibersihkan di ``finally``. Bukan model (tidak ada metode RPC).

Re-review G18 SEC-126: setiap penanda membawa **token** (mis. ``("id", 12)``, ``("uuid", "...")``) yang membatasi
record mana yang boleh disentuh jalur itu. Guard wajib memeriksa token (`scope_tokens`), bukan hanya
`in_scope`, agar perintah x2many/relasi di payload tidak bisa menyentuh record milik order lain.
"""

from contextlib import contextmanager

from odoo.tools import SQL

_ATTR = "_efn_internal_scopes"

# Sync order POS resmi (`pos.order._process_order`): pembayaran order YANG SEDANG DISINKRON boleh dibuat/diubah
# (review r11 SEC-113, re-review SEC-126). Token: ("id", pos.order.id) dan ("uuid", pos.order.uuid).
POS_PAYMENT_SYNC_SCOPE = "efn_pos_payment_sync"
# Simpan order POS setelah validasi E-30/E-31/SEC-116/gerbang OTP (`pos.order._process_saved_order`): hanya order
# di sini yang boleh berpindah ke paid/done/invoiced (re-review SEC-125). Token: ("id", pos.order.id).
POS_ORDER_SAVE_SCOPE = "efn_pos_order_save"


def _scopes(cr):
    scopes = getattr(cr, _ATTR, None)
    if scopes is None:
        scopes = {}
        setattr(cr, _ATTR, scopes)
    return scopes


@contextmanager
def internal_scope(env, key, tokens=()):
    """Tandai jalur internal untuk `tokens`; bisa bersarang (tumpukan, token digabung)."""
    scopes = _scopes(env.cr)
    stack = scopes.setdefault(key, [])
    entry = set(tokens)
    stack.append(entry)
    try:
        yield entry
    finally:
        for index in range(len(stack) - 1, -1, -1):
            if stack[index] is entry:
                del stack[index]
                break
        if not stack:
            scopes.pop(key, None)


def in_scope(env, key):
    return bool(_scopes(env.cr).get(key))


def scope_tokens(env, key):
    """Gabungan token semua penanda `key` yang aktif di cursor ini (kosong bila tidak di jalur itu)."""
    tokens = set()
    for entry in _scopes(env.cr).get(key, ()):
        tokens |= entry
    return tokens


def touch_row(cr, table, row_id):
    """Re-review SEC-127: serialkan transaksi yang membaca turunan baris `table.id = row_id`.

    ``SELECT ... FOR UPDATE`` saja tidak cukup di isolasi REPEATABLE READ Odoo: transaksi kedua yang menunggu
    memperoleh kunci tanpa error setelah transaksi pertama commit (barisnya tidak berubah), lalu tetap membaca
    snapshot lama. UPDATE no-op membuat versi baris baru, sehingga transaksi kedua (snapshot sebelum commit
    pertama) gagal ``SerializationFailure`` dan Odoo mengulang RPC dengan snapshot baru
    (``odoo.service.model.retrying``). `table` hanya nama tabel dari kode (bukan input klien).
    """
    cr.execute(SQL("UPDATE %s SET write_date = write_date WHERE id = %s", SQL.identifier(table), row_id))
