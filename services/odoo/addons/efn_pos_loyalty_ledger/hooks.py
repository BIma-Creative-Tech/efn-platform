"""E-24: filter audit "Baris negatif non-promo" milik efn_setup_pos akan menampilkan setiap
baris Poin EFN (harga negatif, bukan reward pos_loyalty). Modul ini menyediakan filter
pengganti yang mengecualikan produk poin (data/pos_audit_filters.xml) dan menonaktifkan filter
lama; uninstall mengaktifkannya kembali. Tidak ada `depends` ke efn_setup_pos (modul konfigurasi
boleh tidak terpasang): xmlid dicari dengan `raise_if_not_found=False`.
"""

OLD_FILTER_XMLID = "efn_setup_pos.filter_pos_line_negative_non_reward"


def _set_old_filter_active(env, active):
    old_filter = env.ref(OLD_FILTER_XMLID, raise_if_not_found=False)
    if old_filter:
        old_filter.sudo().write({"active": active})


def post_init_hook(env):
    _set_old_filter_active(env, False)


def uninstall_hook(env):
    _set_old_filter_active(env, True)
