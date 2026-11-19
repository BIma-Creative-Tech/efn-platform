from . import models, wizard

DC_XMLIDS = ("efn_setup_supermarket.warehouse_smd_dc",)


def post_init_hook(env):
    """QA-1 (G44): tandai gudang DC supermarket (bila efn_setup_supermarket sudah terpasang) sebagai DC assortment.

    Pasangannya: post_init_hook efn_setup_supermarket men-set field yang sama bila efn_assortment
    terpasang lebih dulu."""
    for xmlid in DC_XMLIDS:
        warehouse = env.ref(xmlid, raise_if_not_found=False)
        if warehouse and not warehouse.efn_is_dc:
            warehouse.sudo().efn_is_dc = True
