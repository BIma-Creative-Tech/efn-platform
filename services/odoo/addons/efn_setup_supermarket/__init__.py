# Data-only (ADR-031, ADR-045): tidak ada model. Satu post_init_hook (QA-1 G44) tanpa dependency baru.


def post_init_hook(env):
    """Tandai DC SMDDC sebagai DC assortment bila efn_assortment sudah terpasang (field ``efn_is_dc`` ada).

    Bila efn_assortment dipasang belakangan, post_init_hook efn_assortment yang men-set field ini."""
    warehouse = env.ref("efn_setup_supermarket.warehouse_smd_dc", raise_if_not_found=False)
    if warehouse and "efn_is_dc" in warehouse._fields and not warehouse.efn_is_dc:
        warehouse.sudo().efn_is_dc = True
