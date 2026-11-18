"""Importer sisi Odoo (G43 W2b): pemetaan baris bundel -> nilai Odoo, MURNI (tanpa Odoo).

Paket `services/odoo/addons/efn_legacy_history/esb_import` dimuat langsung sebagai paket top-level `esb_import`
(tanpa `efn_legacy_history/__init__.py` yang butuh Odoo). Juga menjaga salinan terkendali `efn_mig` di modul identik
dengan sumbernya, dan kontrak baris export (efn_mig.export) diterima validasi importer.
"""

import importlib.util
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from efn_mig import mapping as export_mapping
from efn_mig.bundle import open_bundle
from efn_mig.export import MasterData, RunParams, expand_steps, export_history, export_master

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
PKG_DIR = REPO / "services" / "odoo" / "addons" / "efn_legacy_history" / "esb_import"
if not PKG_DIR.is_dir():  # container esb-migrate hanya me-mount scripts/migration; CI menjalankan dari checkout penuh
    pytest.skip("checkout repo penuh dibutuhkan (services/odoo/addons)", allow_module_level=True)


def _load_pkg():
    name = "esb_import"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, PKG_DIR / "__init__.py", submodule_search_locations=[str(PKG_DIR)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_load_pkg()
from esb_import import mapping as m  # noqa: E402
from esb_import.efn_mig import bundle as vendored_bundle  # noqa: E402

BRAND = "pb"


def _normalized(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n")


# ------------------------------------------------------------------------------------------------ salinan & kontrak
@pytest.mark.parametrize("name", ["bundle.py", "xmlid.py"])
def test_vendored_efn_mig_identical(name):
    source = REPO / "scripts" / "migration" / "efn_mig" / name
    copy = PKG_DIR / "efn_mig" / name
    assert _normalized(copy) == _normalized(source), f"salin ulang scripts/migration/efn_mig/{name} ke modul Odoo"


def test_history_keys_match_export_contract():
    assert m.HISTORY_KEYS == export_mapping.HISTORY_KEYS
    assert m.FLOW_COLUMNS == export_mapping.FLOW_COLUMNS


def test_vendored_bundle_uses_only_aesgcm_hkdf():
    text = (PKG_DIR / "efn_mig" / "bundle.py").read_text(encoding="utf-8")
    imports = {ln.strip() for ln in text.splitlines() if ln.startswith("from cryptography")}
    assert imports == {
        "from cryptography.exceptions import InvalidTag",
        "from cryptography.hazmat.primitives import hashes",
        "from cryptography.hazmat.primitives.ciphers.aead import AESGCM",
        "from cryptography.hazmat.primitives.kdf.hkdf import HKDF",
    }


# ------------------------------------------------------------------------------------------------ resolver tiruan
class FakeResolver:
    def __init__(self, ids=None, std=None, variants=None, uoms=None):
        self.ids = ids or {}
        self.std = std or {"uom.product_uom_unit": 1, "uom.product_uom_kgm": 3}
        self.variants = variants or {}
        self.uoms = uoms or {}
        self.tags = {}

    def res_id(self, xmlid):
        return self.ids.get(xmlid)

    def std_ref(self, xmlid):
        return self.std.get(xmlid) if xmlid else None

    def variant_id(self, xmlid):
        return self.variants.get(xmlid)

    def uom_of(self, xmlid):
        return self.uoms.get(xmlid)

    def tag_id(self, name):
        return self.tags.setdefault(name, 900 + len(self.tags))


def X(entity, code):  # noqa: N802
    return export_mapping.xmlid(BRAND, "PB", entity, code)


def _row(entity, code, data, xmlid_entity=None):
    target = xmlid_entity or entity
    return {"entity": entity, "company_code": "PB", "esb_key": str(code), "xmlid": X(target, code), "data": data}


# ------------------------------------------------------------------------------------------------ validasi baris
def test_check_master_row_accepts_and_recomputes_xmlid():
    row = _row("product", "FIN-001", {"default_code": "FIN-001", "name": "Roti", "categ_key": X("category", "Roti")})
    m.check_master_row(BRAND, "product", row, ["PB"])
    bad = dict(row, xmlid=X("product", "FIN-002"))
    with pytest.raises(vendored_bundle.IntegrityError, match="hitung ulang"):
        m.check_master_row(BRAND, "product", bad, ["PB"])


@pytest.mark.parametrize(
    "mutate, reason",
    [
        (lambda r: r.update(company_code="WTZ"), "di luar manifest"),
        (lambda r: r.update(extra=1), "kunci baris"),
        (lambda r: r["data"].update(categ_key="efn_mig_esb.pb_wtz_category_roti"), "referensi categ_key"),
        (lambda r: r["data"].update(categ_key=X("product", "X")), "referensi categ_key"),
        (lambda r: r.update(entity="menu"), "entitas baris"),
    ],
)
def test_check_master_row_rejects(mutate, reason):
    row = _row("product", "FIN-001", {"default_code": "FIN-001", "name": "Roti", "categ_key": X("category", "Roti")})
    mutate(row)
    with pytest.raises(vendored_bundle.IntegrityError, match=reason):
        m.check_master_row(BRAND, "product", row, ["PB"])


def test_branch_rows_xmlid_entities():
    store = _row(
        "branch", 11, {"branch_id": 11, "name": "Toko", "warehouse_code": "PB011", "type": "store",
                       "is_central_kitchen": False, "active": True, "pos_xmlid": X("branch_pos", 11)},
        "branch_wh",
    )  # fmt: skip
    m.check_master_row(BRAND, "branch", store, ["PB"])
    loc = _row(
        "branch", 30, {"branch_id": 30, "name": "Gudang", "type": "location", "parent_key": X("branch_wh", 4),
                       "active": True}, "branch_loc",
    )  # fmt: skip
    m.check_master_row(BRAND, "branch", loc, ["PB"])
    store["data"]["pos_xmlid"] = X("branch_pos", 12)
    with pytest.raises(vendored_bundle.IntegrityError, match="pos_xmlid"):
        m.check_master_row(BRAND, "branch", store, ["PB"])


def test_history_row_keys_exact():
    row = {k: 0 for k in m.HISTORY_KEYS["legacy_sales_line"]}
    m.check_history_row("legacy_sales_line", row)
    with pytest.raises(vendored_bundle.IntegrityError):
        m.check_history_row("legacy_sales_line", {**row, "customer_phone": "x"})


# ------------------------------------------------------------------------------------------------ plan master
def test_warehouse_pos_location_plans():
    wh = m.warehouse_plan({"name": "Toko", "warehouse_code": "pb011", "is_central_kitchen": False}, 7)
    assert wh.owned == {"name": "Toko", "code": "PB011", "efn_is_central_kitchen": False}
    assert wh.create_only == {"company_id": 7} and not wh.missing
    assert m.warehouse_plan({"name": "X", "warehouse_code": "TOOLONG"}, 7).missing
    pos = m.pos_config_plan({"name": "Toko", "active": False}, 7, 55)
    assert pos.create_only["payment_method_ids"] == () and pos.create_only["active"] is False
    assert m.pos_config_plan({"name": "Toko"}, 7, None).missing
    loc = m.location_plan({"name": "Gudang"}, 7, 99)
    assert loc.owned == {"name": "Gudang", "location_id": 99} and loc.create_only["usage"] == "internal"


def test_product_and_menu_plans():
    resolver = FakeResolver(ids={X("category", "Roti"): 41, X("pos_category", "Bakery"): 51})
    data = {"default_code": "FIN-001", "name": "Roti", "categ_key": X("category", "Roti"), "uom_xmlid":
            "uom.product_uom_unit", "type": "consu", "is_storable": True, "purchase_ok": False, "sale_ok": False,
            "active": True}  # fmt: skip
    plan = m.product_plan(data, 7, resolver, None)
    assert plan.owned["categ_id"] == 41
    assert plan.create_only == {"company_id": 7, "uom_id": 1, "responsible_id": False}
    assert not plan.missing
    assert "uom_xmlid" in m.product_plan({**data, "uom_xmlid": None}, 7, resolver, None).missing
    root = m.product_plan({**data, "categ_key": None}, 7, resolver, 5)
    assert root.owned["categ_id"] == 5
    menu = {"menu_code": "M01", "name": "Roti Tawar", "pos_categ_key": X("pos_category", "Bakery"), "list_price": 25000,
            "available_in_pos": True, "sale_ok": True, "type": "consu", "active": True, "alias_of": None}  # fmt: skip
    mp = m.menu_plan(menu, 7, resolver)
    assert mp.owned["pos_categ_ids"] == (51,) and mp.owned["list_price"] == 25000.0
    assert mp.create_only == {"company_id": 7, "is_storable": False, "default_code": "M01", "responsible_id": False}
    archive = m.menu_plan({**menu, "list_price": None, "active": False, "available_in_pos": False}, 7, resolver)
    assert "list_price" not in archive.owned and archive.owned["active"] is False
    assert m.menu_alias_target({**menu, "alias_of": X("menu", "M01")}) == X("menu", "M01")


def test_partner_plans():
    resolver = FakeResolver(ids={X("payment_term", "net_30"): 61})
    term = m.payment_term_plan({"days": 30, "name": "30 Hari"}, 7)
    assert term.owned == {"name": "30 Hari", "_nb_days": 30}
    assert m.payment_term_plan({"days": 0, "name": "x"}, 7).missing
    sup = m.supplier_plan({"ref": "SUP1", "name": "PT Pemasok", "is_company": True, "supplier_rank": 1}, 7)
    assert sup.owned["supplier_rank"] == 1 and sup.create_only == {"company_id": 7}
    cus = m.customer_plan(
        {"ref": "C001", "name": "PT Mitra", "is_company": True, "customer_rank": 1, "category_name": "B2B",
         "payment_term_key": X("payment_term", "net_30"), "active": True}, 7, resolver,
    )  # fmt: skip
    assert cus.owned["property_payment_term_id"] == 61 and cus.owned["category_id"] == (900,)
    assert not {"street", "phone", "email", "vat"} & set(cus.owned)


# ------------------------------------------------------------------------------------------------ satuan & BoM
UOMS = {"EA": {"uom_xmlid": "uom.product_uom_unit", "ea_factor": 1},
        "PACK": {"uom_xmlid": "uom.product_uom_unit", "ea_factor": 6},
        "KG": {"uom_xmlid": "uom.product_uom_kgm", "ea_factor": None},
        "G": {"uom_xmlid": "uom.product_uom_gram", "ea_factor": None}}  # fmt: skip


def test_convert_qty_and_price():
    pack = {"uom_esb": "PACK", "uom_xmlid": "uom.product_uom_unit", "ea_factor": 6}
    assert m.convert_qty(12, "EA", "uom.product_uom_unit", pack, UOMS) == (Decimal(2), None)
    assert m.convert_qty(3, "PACK", "uom.product_uom_unit", pack, UOMS) == (Decimal(3), None)
    kg = {"uom_esb": "KG", "uom_xmlid": "uom.product_uom_kgm", "ea_factor": None}
    assert m.convert_qty(250, "G", "uom.product_uom_gram", kg, UOMS) == (Decimal(250), "uom.product_uom_gram")
    assert m.convert_qty(2, "EA", "uom.product_uom_unit", None, UOMS) == (Decimal(2), "uom.product_uom_unit")
    assert m.convert_price(600, "PACK", {"uom_esb": "EA", "uom_xmlid": "uom.product_uom_unit"}, UOMS) == (
        Decimal(100),
        None,
    )
    assert m.convert_price(12000, "KG", kg, UOMS) == (Decimal(12000), None)
    assert m.convert_price(12, "G", kg, UOMS) == (Decimal(12), "uom.product_uom_gram")


def _bom_row(bom_id, data):
    return {"entity": "bom", "company_code": "PB", "esb_key": str(bom_id), "xmlid": X("bom", bom_id), "data": data}


def test_bom_targets_normal_and_phantom():
    fin, rm = X("product", "FIN-001"), X("product", "RM001")
    m1, m2 = X("menu", "M01"), X("menu", "M02")
    resolver = FakeResolver(
        ids={fin: 10, rm: 11, m1: 20, m2: 21},
        variants={fin: 110, rm: 111},
        uoms={fin: 1, rm: 3, m1: 1, m2: 1},
    )
    products = {fin: {"uom_esb": "EA", "uom_xmlid": "uom.product_uom_unit", "ea_factor": 1},
                rm: {"uom_esb": "KG", "uom_xmlid": "uom.product_uom_kgm", "ea_factor": None}}  # fmt: skip
    rm_line = {"product_key": rm, "product_code": "RM001", "qty": 2.5, "uom_esb": "KG",
               "uom_xmlid": "uom.product_uom_kgm", "yield_percent": 100}  # fmt: skip
    normal = _bom_row(7, {"code": "B7", "type": "normal", "product_key": fin, "menu_keys": [], "product_qty": 12,
                          "ratio_source": "bom_ratio", "lines": [rm_line]})  # fmt: skip
    (target,) = m.bom_targets(normal, 7, resolver, products, UOMS)
    assert target.xmlid == X("bom", 7) and not target.plan.missing
    assert target.plan.owned == {"code": "B7", "type": "normal", "product_tmpl_id": 10, "product_qty": 12.0,
                                 "product_uom_id": 1, "bom_line_ids": ((111, 2.5, 3),)}  # fmt: skip
    phantom = _bom_row(8, {"code": "B8", "type": "phantom", "product_key": None, "menu_keys": [m2, m1],
                           "product_qty": 1, "ratio_source": "default_1", "lines": [{"product_key": fin,
                           "product_code": "FIN-001", "qty": 1, "uom_esb": "EA", "uom_xmlid": "uom.product_uom_unit",
                           "yield_percent": 100}]})  # fmt: skip
    first, second = m.bom_targets(phantom, 7, resolver, products, UOMS)
    assert first.xmlid == X("bom", 8) and first.plan.owned["product_tmpl_id"] == 20
    assert second.xmlid == m.extra_bom_xmlid(X("bom", 8), m2) and second.esb_key == f"8|{m2}"
    assert all(t.plan.owned["type"] == "phantom" for t in (first, second))
    broken = _bom_row(9, {**normal["data"], "lines": [{**normal["data"]["lines"][0], "product_key": None}]})
    assert m.bom_targets(broken, 7, resolver, products, UOMS)[0].plan.missing


# ------------------------------------------------------------------------------------------------ histori
def test_history_vals_and_aggregate():
    flow = {k: 0 for k in m.HISTORY_KEYS["legacy_flow_daily"]}
    flow.update(day="2026-10-01", branch_key=X("branch_wh", 11), product_key=X("product", "FIN-001"),
                esb_branch_id=11, esb_product_code="FIN-001", sold=10, st_out=1, outflow=11)  # fmt: skip
    vals = m.flow_vals(flow, company_id=7, warehouse_id=3, product_id=110, brand=BRAND)
    assert "outflow" not in vals and vals["sold"] == 10 and vals["warehouse_id"] == 3
    other = dict(vals, sold=2, esb_branch_id=30)
    merged = m.aggregate([vals, other], ("day", "company_id", "warehouse_id", "product_id"), m.FLOW_COLUMNS)
    assert len(merged) == 1 and merged[0]["sold"] == Decimal(12) and merged[0]["esb_branch_id"] == 11
    sale = {k: 0 for k in m.HISTORY_KEYS["legacy_sales_daily"]}
    sale.update(day="2026-10-01", esb_menu_code="M01T", is_takeaway=True, trx_count=2, net=70000)
    sd = m.sales_daily_vals(sale, company_id=7, warehouse_id=3, pos_config_id=None, product_id=None, brand=BRAND)
    agg = m.aggregate([sd, sd], ("day", "company_id", "warehouse_id", "esb_menu_code"), m.SALES_DAILY_SUMS)
    assert agg[0]["trx_count"] == 4 and agg[0]["net"] == Decimal(140000)
    line = {k: None for k in m.HISTORY_KEYS["legacy_sales_line"]}
    line.update(date="2026-10-02", row_type="", is_takeaway=1)
    lv = m.sales_line_vals(line, product_id=None)
    assert lv["row_type"] is None and lv["is_takeaway"] is True and "company_id" not in lv


def test_ranges_and_segment_gate():
    window = (date(2024, 11, 1), date(2026, 11, 15))
    assert m.clip_range(date(2026, 10, 1), date(2026, 11, 1), window) == (date(2026, 10, 1), date(2026, 11, 1))
    assert m.clip_range(date(2024, 10, 1), date(2024, 11, 1), window) is None
    assert m.clip_range(date(2024, 10, 20), date(2024, 11, 10), window) == (date(2024, 11, 1), date(2024, 11, 10))
    assert m.clip_range(date(2026, 10, 1), date(2026, 11, 1), None) is None
    assert m.segment_month_ok("2026-10", date(2026, 11, 1))
    assert m.segment_month_ok("2026-11", date(2026, 11, 15))
    assert not m.segment_month_ok("2026-11", date(2026, 11, 1))
    assert not m.segment_month_ok("2026-12", date(2026, 11, 15))
    assert m.parse_range({"name": "x", "range": ["2026-10-01", "2026-11-01"]}) == (date(2026, 10, 1), date(2026, 11, 1))
    with pytest.raises(vendored_bundle.IntegrityError):
        m.parse_range({"name": "x"})


# ------------------------------------------------------------------------------------------------ kontrak end-to-end
def test_export_bundles_pass_importer_row_checks(tmp_path, source, maps, keys):
    """Baris yang benar-benar dihasilkan export W1 (FakeSource) lolos validasi importer + semua plan bisa dibangun."""
    cutover = date(2026, 10, 5)
    params = RunParams(brand=BRAND, cutover=cutover, legacy_days=30, sales_line_months=1)
    data = MasterData.load(source, BRAND, date(2026, 9, 1), cutover)
    steps = expand_steps(["master"])
    path = export_master(data, params, steps, maps, keys, tmp_path)
    bundle = open_bundle(path.read_bytes(), keys, expect_kind="bundle")
    codes = bundle.manifest["company_codes"]
    ids = {}
    for entity in m.MASTER_ORDER:
        name = bundle.entity_file(entity)
        if not name:
            continue
        for row in bundle.rows(name):
            m.check_master_row(BRAND, entity, row, codes)
            if row["xmlid"]:
                ids[row["xmlid"]] = len(ids) + 100
    resolver = FakeResolver(ids=ids, variants=dict(ids), uoms={k: 1 for k in ids})
    products = m.product_info(bundle.rows("product.jsonl"))
    uoms = m.uom_index(bundle.rows("uom.jsonl"))
    for row in bundle.rows("bom.jsonl"):
        targets = m.bom_targets(row, 7, resolver, products, uoms)
        assert targets and all(not t.plan.missing for t in targets), row["esb_key"]
    menus = list(bundle.rows("menu.jsonl"))
    assert any(m.menu_alias_target(r["data"]) for r in menus), "fixture W1 punya menu takeaway alias"
    assert any(not r["data"]["active"] for r in menus), "fixture W1 punya menu arsip"
    parent, segments = export_history(
        source, data, params, ["legacy_flow", "legacy_sales_daily", "legacy_sales_line"], maps, keys, tmp_path,
        tmp_path,
    )  # fmt: skip
    assert segments
    for seg in segments:
        hb = open_bundle(seg.path.read_bytes(), keys, expect_kind="bundle")
        assert m.segment_month_ok(hb.manifest["segment"]["month"], cutover)
        for entry in hb.manifest["files"]:
            start, end = m.parse_range(entry)
            assert end <= cutover
            for row in hb.rows(entry["name"]):
                m.check_history_row(entry["entity"], row)
