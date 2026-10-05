{
    "name": "EFN Kitting (Central Kitchen)",
    "summary": "Paket Central Kitchen via BoM Manufacture + MO; Phantom hanya untuk menu combo POS (ADR-018).",
    "version": "19.0.1.1.0",
    "category": "EFN/Manufacturing",
    "author": "Era Food and Nourishment",
    "license": "LGPL-3",
    "website": "https://github.com/efn/efn-platform",
    # ADR-018 (Proposed): kitting = BoM Manufacture di CK; Phantom/Kit hanya menu combo POS.
    # Penjualan kit di POS memakai modul CE `pos_mrp` (auto-install bila point_of_sale + mrp).
    "depends": [
        "mrp",
        "stock",
        "efn_core",
    ],
    "data": [
        "data/uom_data.xml",
        "views/product_template_views.xml",
        # views/mrp_bom_views.xml dihapus (19.0.1.1.0): field efn_kitting_role ditiadakan.
    ],
    "demo": [
        "demo/efn_kitting_demo.xml",
    ],
    "installable": True,
    "application": False,
}
