"""G61: tabel peran kategori (category_roles.csv) -> pohon kategori + flag produk di bundel."""

import pytest
from conftest import UOM_MAP, product

from efn_mig import category_roles as cr
from efn_mig import mapping as m
from efn_mig.xmlid import xmlid

# 52 kategori ESB PB (docs/fit-gap/G54_G55_G61_ODOO_SETUP.md §G61.3) -> path target.
ESB_PB = {
    "Inventory - Food & Beverage": "Persediaan / Bahan Baku / Food & Beverage",
    "Inventory - WIP": "Persediaan / WIP",
    "FA LVA": "Aset Tetap / LVA",
    "Packaging Material": "Persediaan / Kemasan",
    "FA Office & Outlet Equipment": "Aset Tetap / Peralatan Kantor & Outlet",
    "FA Furniture & Fixture": "Aset Tetap / Furnitur & Perlengkapan Tetap",
    "Local - Dry": "Persediaan / Bahan Baku / Lokal - Dry",
    "Supplies": "Perlengkapan / Operasional Toko",
    "Import Ingredient": "Persediaan / Bahan Baku / Impor",
    "WIP": "Persediaan / WIP",
    "Indirect Material": "Persediaan / Bahan Baku / Bahan Penolong",
    "Expense - Store Supplies": "Perlengkapan / Operasional Toko",
    "Local - Fresh": "Persediaan / Bahan Baku / Lokal - Fresh",
    "Uniform": "Perlengkapan / Seragam",
    "Tableware & Kitchenware": "Perlengkapan / Peralatan Makan & Dapur",
    "FIN": "Persediaan / Barang Jadi",
    "Sparepart Machine": "Perlengkapan / Suku Cadang",
    "Packaging": "Persediaan / Kemasan",
    "Local - Frozen": "Persediaan / Bahan Baku / Lokal - Frozen",
    "Marketing": "Perlengkapan / Pemasaran",
    "Local - Chill": "Persediaan / Bahan Baku / Lokal - Chill",
    "Supplies - Cleaning": "Perlengkapan / Kebersihan & Keselamatan",
    "Supplies - Production": "Perlengkapan / Produksi",
    "FA Building & Improvements": "Aset Tetap / Bangunan & Renovasi",
    "FA Machinery": "Aset Tetap / Mesin",
    "Supplies - Selling": "Perlengkapan / Operasional Toko",
    "Expense - Equipment Maintenance": "Beban / Jasa / Pemeliharaan & Instalasi",
    "Supplies - Packaging": "Perlengkapan / Kemasan Habis Pakai",
    "Freight - Import": "Beban / Jasa / Biaya Impor",
    "FA Office & Outlet Equipment - Metal": "Aset Tetap / Peralatan Kantor & Outlet",
    "Supplies - Safety": "Perlengkapan / Kebersihan & Keselamatan",
    "Supplies - Marketing": "Perlengkapan / Pemasaran",
    "Expense - Water & Gas": "Beban / Jasa / Utilitas",
    "Expense - Other": "Beban / Jasa / Lain-lain",
    "Expense - Building Maintenance": "Beban / Jasa / Pemeliharaan & Instalasi",
    "Inventory - Merchandise": "Persediaan / Merchandise",
    "Licensing / Sertification": "Beban / Jasa / Perizinan & Sertifikasi",
    "Expense - Maintenance Fee": "Beban / Jasa / Pemeliharaan & Instalasi",
    "Expense - Stationaries": "Perlengkapan / Kantor & ATK",
    "Expense - Office Supplies": "Perlengkapan / Kantor & ATK",
    "EXP - DOMESTIC DISTRIBUTION": "Beban / Jasa / Pengiriman",
    "Expense - Household": "Perlengkapan / Operasional Toko",
    "Expense - Furniture Maintenance": "Beban / Jasa / Pemeliharaan & Instalasi",
    "Expense - Computer supplies": "Perlengkapan / Kantor & ATK",
    "Expense - Printing": "Beban / Jasa / Pemasaran & Cetak",
    "FA - Software": "Aset Tetap / Perangkat Lunak",
    "ONGKOS KIRIM": "Beban / Jasa / Pengiriman",
    "Expense - Reklame": "Beban / Jasa / Pemasaran & Cetak",
    "Expense - Production Supplies": "Perlengkapan / Produksi",
    "Sewa Peralatan": "Beban / Jasa / Sewa",
    "Expense - Printing Marketing": "Beban / Jasa / Pemasaran & Cetak",
    "JASA INSTALASI EQUIPMENT": "Beban / Jasa / Pemeliharaan & Instalasi",
}
HEADER = ",".join(cr.COLUMNS) + "\n"


def test_all_52_pb_categories_mapped_without_duplicates() -> None:
    roles = cr.load_roles()
    assert len(ESB_PB) == 52 and len(roles) == 52
    assert {r.esb_category: r.target_path for r in roles.values()} == ESB_PB
    for name in ESB_PB:
        assert cr.role_for(roles, f"  {name.upper()} ").esb_category == name  # spasi & huruf besar/kecil diabaikan


def test_flags_per_role() -> None:
    roles = cr.load_roles()
    expected = {
        "persediaan": ("consu", True),
        "perlengkapan": ("consu", False),
        "jasa": ("service", False),
        "aset_tetap": ("consu", False),
    }
    for role in roles.values():
        assert (role.type, role.is_storable) == expected[role.role], role.esb_category
        assert role.target_path.startswith(cr.ROLES[role.role][0] + cr.SEP)
        if role.esb_category != "Inventory - Merchandise":
            assert not role.sale_ok and role.available_in_pos == "false", role.esb_category
    merch = cr.role_for(roles, "Inventory - Merchandise")
    assert merch.sale_ok and merch.available_in_pos == "keep" and merch.purchase(False)
    wip, fin, dry = (cr.role_for(roles, n) for n in ("WIP", "FIN", "Local - Dry"))
    assert wip.purchase_ok == "supplierinfo" and wip.purchase(True) and not wip.purchase(False)
    assert not fin.purchase(True, is_fin=True)
    assert dry.purchase(False) and not dry.purchase(False, is_fin=True)


def test_ambiguities_a1_a11_kept_as_notes() -> None:
    notes = " ".join(r.note for r in cr.load_roles().values())
    for code in range(1, 12):
        assert f"A{code} " in notes, f"A{code}"


def test_unmapped_category_is_hard_error() -> None:
    roles = cr.load_roles()
    for bad in ("Kategori Baru", "", None):
        with pytest.raises(cr.CategoryRoleError, match="tidak terpetakan"):
            cr.role_for(roles, bad)
    umap = m.parse_uom_map(UOM_MAP)
    rows = [product("X1", "Barang", "Kategori Baru", "EA", 0, None)]
    with pytest.raises(m.MapError, match="tidak terpetakan"):
        m.build_categories("pb", "PB", rows)
    with pytest.raises(m.MapError, match="tidak terpetakan"):
        m.build_products("pb", "PB", rows, {"X1": "EA"}, umap)


@pytest.mark.parametrize(
    "line, reason",
    [
        ("A,Persediaan / X,persediaan,consu,false,false,true,false,\n", "is_storable"),
        ("A,Beban / Jasa / X,jasa,consu,false,false,true,false,\n", "type"),
        ("A,Perlengkapan / X,persediaan,consu,true,false,true,false,\n", "target_path"),
        ("A,Persediaan / X,gudang,consu,true,false,true,false,\n", "role"),
        ("A,Persediaan / X,persediaan,consu,true,false,maybe,false,\n", "purchase_ok"),
        ("A,Persediaan / X,persediaan,consu,true,false,true,false,\na,Persediaan / Y,persediaan,consu,true,false,true,"
         "false,\n", "ganda"),
    ],
)  # fmt: skip
def test_parse_rejects_inconsistent_rows(line: str, reason: str) -> None:
    with pytest.raises(cr.CategoryRoleError, match=reason):
        cr.parse_roles(HEADER + line)
    with pytest.raises(cr.CategoryRoleError, match="kolom"):
        cr.parse_roles("esb_category,target_path\nA,Persediaan / X\n")


def test_build_categories_tree_parent_first() -> None:
    rows = [
        product("A", "a", "Import Ingredient", "EA", 0, None),
        product("B", "b", "Local - Dry", "EA", 0, None),
        product("C", "c", "Expense - Water & Gas", "EA", 0, None),
    ]
    out = m.build_categories("pb", "PB", rows)
    keys = [r["esb_key"] for r in out]
    assert keys == [
        "Beban",
        "Persediaan",
        "Beban / Jasa",
        "Persediaan / Bahan Baku",
        "Beban / Jasa / Utilitas",
        "Persediaan / Bahan Baku / Impor",
        "Persediaan / Bahan Baku / Lokal - Dry",
    ]
    by = {r["esb_key"]: r for r in out}
    assert by["Persediaan"]["data"] == {"name": "Persediaan", "parent_key": None}  # tanpa induk brand
    leaf = by["Persediaan / Bahan Baku / Impor"]
    assert leaf["xmlid"] == xmlid("pb", "PB", "category", "Persediaan / Bahan Baku / Impor")
    assert leaf["data"] == {"name": "Impor", "parent_key": xmlid("pb", "PB", "category", "Persediaan / Bahan Baku")}


def test_build_products_flags_from_role() -> None:
    umap = m.parse_uom_map(UOM_MAP)
    rows = [
        product("SVC", "Ongkir", "ONGKOS KIRIM", "EA", 0, None),
        product("LVA", "Kursi", "FA LVA", "EA", 0, None),
        product("WIP1", "Adonan", "WIP", "EA", 0, None),
        product("FIN1", "Roti", "FIN", "EA", 1, None),
        product("MER", "Tumbler", "Inventory - Merchandise", "EA", 0, None),
    ]
    uoms = {p["product_code"]: "EA" for p in rows}
    data = {r["esb_key"]: r["data"] for r in m.build_products("pb", "PB", rows, uoms, umap)}
    flags = {k: (d["type"], d["is_storable"], d["sale_ok"], d["purchase_ok"]) for k, d in data.items()}
    assert flags == {
        "SVC": ("service", False, False, True),
        "LVA": ("consu", False, False, True),
        "WIP1": ("consu", True, False, False),  # A2: belum ada sumber supplierinfo di export
        "FIN1": ("consu", True, False, False),
        "MER": ("consu", True, True, True),
    }
    assert data["SVC"]["categ_key"] == xmlid("pb", "PB", "category", "Beban / Jasa / Pengiriman")
    assert set(data["SVC"]) <= m.DATA_KEYS["product"]
