from . import models


def post_init_hook(env):
    """Buat aturan SoD bawaan (SOD-01..03 block, SOD-05/06 report_only) yang grupnya sudah ada.

    Grup modul lain yang belum terpasang dilewati; cron harian `_cron_efn_role_daily` mengulang (idempoten,
    kunci = `code`), sehingga aturan muncul setelah modul pemilik grup dipasang.
    """
    env["efn.sod.rule"]._efn_ensure_default_rules()
