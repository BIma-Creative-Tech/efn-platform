from . import models, wizard

DC_XMLIDS = ("efn_setup_supermarket.warehouse_smd_dc",)


def post_init_hook(env):
    """QA-1 (G44): pengaman — tandai gudang DC supermarket (bila efn_setup_supermarket sudah terpasang) sebagai DC
    assortment. Sejak G45 data efn_setup_supermarket sudah mengisi ``efn_is_dc=True`` langsung (hook di sana dihapus);
    hook ini tetap untuk DB lama yang terpasang sebelum perubahan itu, dan no-op bila sudah benar."""
    for xmlid in DC_XMLIDS:
        warehouse = env.ref(xmlid, raise_if_not_found=False)
        if warehouse and not warehouse.efn_is_dc:
            warehouse.sudo().efn_is_dc = True
