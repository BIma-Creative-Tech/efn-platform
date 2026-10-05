"""Kompatibilitas OCA auditlog 19.0.2.0.0 dengan ORM Odoo 19.0 (build 2026-09).

Akar masalah: Odoo 19 menyimpan referensi `Environment._field_dirty` sebagai
`functools.cached_property` yang menunjuk ke `transaction.field_dirty`. `ThrowAwayCache`
milik auditlog (dipakai mode `full` saat create/write) menukar `transaction.field_dirty`
dengan dict kosong, tetapi `env._field_dirty` tetap menunjuk dict LAMA. Flush di dalam
blok itu lalu melihat id kotor lama tanpa nilai di `field_data` pengganti ->
"AssertionError: Could not find all values of <model>(<id>,) to flush them"
(terjadi mis. saat membuat produk ketika akun/record lain belum di-flush).

Perbaikan: setiap kali ThrowAwayCache masuk/keluar, buang cache `_field_dirty` di semua
env transaksi agar dibaca ulang dari `transaction.field_dirty` yang aktif.
Hapus patch ini bila upstream OCA/server-tools sudah memperbaikinya.
"""

from odoo.addons.auditlog.models import auditlog_rule

_ThrowAwayCache = auditlog_rule.ThrowAwayCache


def _reset_field_dirty_memo(transaction):
    for env in transaction.envs:
        env.__dict__.pop("_field_dirty", None)


if not getattr(_ThrowAwayCache, "_efn_field_dirty_patched", False):
    _orig_enter = _ThrowAwayCache.__enter__
    _orig_exit = _ThrowAwayCache.__exit__

    def __enter__(self):
        result = _orig_enter(self)
        _reset_field_dirty_memo(self._transaction)
        return result

    def __exit__(self, exc_type, exc_val, exc_tb):
        result = _orig_exit(self, exc_type, exc_val, exc_tb)
        _reset_field_dirty_memo(self._transaction)
        return result

    _ThrowAwayCache.__enter__ = __enter__
    _ThrowAwayCache.__exit__ = __exit__
    _ThrowAwayCache._efn_field_dirty_patched = True
