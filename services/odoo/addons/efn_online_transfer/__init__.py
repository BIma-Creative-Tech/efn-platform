from . import models, wizard


def post_init_hook(env):
    """G26 L-99: aturan SoD bawaan SOD-PAY-01/02 (efn_role membuat yang belum ada; `report_only` bila sudah ada
    pelanggaran saat install, Superadmin menaikkan ke `block`)."""
    env["efn.sod.rule"]._efn_ensure_default_rules()
