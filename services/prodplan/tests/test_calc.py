"""Vektor wajib docs/04 §30.5 + kasus negatif (siklus, kedalaman, satuan, stok)."""

from decimal import Decimal as D

import pytest

from efn_prodplan.calc import (
    Bom,
    BomLine,
    CalcError,
    Demand,
    Transfer,
    TransferLine,
    allocate,
    compute_plan,
    explode,
    order_sources,
    q4,
    short_materials,
    to_decimal,
)

SUSU, TEPUNG = 21, 31
SOURCES = [(4, 8), (4, 9), (13, 21)]


def ln(pid, code, uom, qty, yld=None):
    return BomLine(pid, code, f"Nama {code}", uom, D(qty), D(yld) if yld is not None else None)


def bom(bom_id, pid, code, uom, lines):
    return Bom(bom_id, pid, code, f"Nama {code}", uom, tuple(lines))


def plan(demands, boms, mapping, **kw):
    args = {
        "master_uom": {},
        "pack_rules": {},
        "stock": {},
        "prod_branch": 4,
        "prod_loc": 7,
        "sources": SOURCES,
    }
    args.update(kw)
    return compute_plan(demands, {b.bom_id: b for b in boms}, mapping, **args)


# ---------------------------------------------------------------- vektor 1+2: susu kemasan utuh
MILK_BOMS = [
    bom(101, 1001, "FIN-A", "PCS", [ln(SUSU, "RMT000021", "ML", "45")]),
    bom(102, 1002, "FIN-B", "PCS", [ln(SUSU, "RMT000021", "ML", "30")]),
]
MILK_DEMAND = [Demand(1001, 101, D(30)), Demand(1002, 102, D(20))]
MILK_STOCK = {(4, 7, SUSU): D(700), (4, 8, SUSU): D(1500), (4, 9, SUSU): D(400), (13, 21, SUSU): D(5000)}


def milk_plan():
    return plan(
        MILK_DEMAND,
        MILK_BOMS,
        {1001: 101, 1002: 102},
        master_uom={"RMT000021": "ML"},
        pack_rules={"RMT000021": D(1000)},
        stock=MILK_STOCK,
    )


def test_vector_milk_pack_and_suggestions():
    result = milk_plan()
    (req,) = result.requirements
    assert req.product_code == "RMT000021"
    assert req.need_qty == D("1950.0000")
    assert req.packs == 2
    assert req.leftover_qty == D("50.0000")
    assert req.need_eff_qty == D("2000.0000")
    assert req.stock_prod_qty == D("700.0000")
    assert req.flags == ()
    suggestions = [
        (s.material_detail_id, s.bom_id, s.product_detail_id, s.extra_units, s.uses_qty, s.leftover_qty)
        for s in result.suggestions
    ]
    assert suggestions == [
        (SUSU, 101, 1001, 1, D("45.0000"), D("50.0000")),
        (SUSU, 102, 1002, 1, D("30.0000"), D("50.0000")),
    ]


def test_vector_milk_transfers():
    result = milk_plan()
    (req,) = result.requirements
    assert req.transfer_qty == D("2000.0000")
    assert req.shortage_qty == D(0)
    assert result.transfers == (
        Transfer("simple_transfer", 4, 8, 4, 7, (TransferLine(SUSU, "RMT000021", "ML", D("1000.0000")),)),
        Transfer("goods_transfer_request", 13, 21, 4, 7, (TransferLine(SUSU, "RMT000021", "ML", D("1000.0000")),)),
    )
    assert short_materials(result) == []


def test_vector_milk_allocation_steps():
    candidates = order_sources(SOURCES, MILK_STOCK, SUSU, 4, 7)
    assert candidates == [(4, 8, D(1500)), (4, 9, D(400)), (13, 21, D(5000))]
    takes, shortage = allocate(D(1300), candidates, D(1000))
    assert takes == [(4, 8, D(1000)), (13, 21, D(1000))]  # loc 9: min(1000, 0) = 0 dilewati; sisa -700 -> 0
    assert shortage == D(0)


# ---------------------------------------------------------------- vektor 3: tepung tanpa kemasan
def test_vector_flour_without_pack():
    boms = [bom(103, 1003, "FIN-T", "PCS", [ln(TEPUNG, "RMT000031", "KG", "0.5")])]
    stock = {(4, 7, TEPUNG): D(4), (4, 8, TEPUNG): D(6), (13, 21, TEPUNG): D(10)}
    result = plan([Demand(1003, 103, D(25))], boms, {1003: 103}, stock=stock)
    (req,) = result.requirements
    assert req.need_qty == D("12.5000") and req.need_eff_qty == D("12.5000")
    assert req.pack_qty is None and req.packs is None and req.leftover_qty is None
    assert req.transfer_qty == D("8.5000") and req.shortage_qty == D(0)
    assert [(t.doc_type, t.origin_location_id, t.lines[0].qty) for t in result.transfers] == [
        ("simple_transfer", 8, D("6.0000")),
        ("goods_transfer_request", 21, D("2.5000")),
    ]
    assert result.suggestions == ()


# ---------------------------------------------------------------- vektor 4: multi-level
def test_vector_multilevel():
    boms = [
        bom(104, 1004, "FIN-C", "PCS", [ln(2001, "WIP-D", "KG", "0.2")]),
        bom(201, 2001, "WIP-D", "KG", [ln(TEPUNG, "RMT000031", "KG", "0.5")]),
    ]
    result = plan([Demand(1004, 104, D(10))], boms, {1004: 104, 2001: 201}, sources=[])
    lines = [(pl.product_code, pl.level, pl.qty, pl.is_demand) for pl in result.production_lines]
    assert lines == [("WIP-D", 2, D("2.0000"), False), ("FIN-C", 1, D("10.0000"), True)]
    (req,) = result.requirements
    assert req.product_code == "RMT000031" and req.need_qty == D("1.0000")
    fin = result.production_lines[1]
    materials = [(m.product_code, m.qty_per_unit, m.total_qty) for m in fin.materials]
    assert materials == [("WIP-D", D("0.2000"), D("2.0000"))]
    # WIP bukan kebutuhan bahan (tanpa netting stok WIP, L-253)
    assert all(r.product_detail_id != 2001 for r in result.requirements)


def test_wip_shared_by_two_parents_sums_and_takes_max_level():
    boms = [
        bom(104, 1004, "FIN-C", "PCS", [ln(2001, "WIP-D", "KG", "0.2")]),
        bom(105, 1005, "FIN-E", "PCS", [ln(2001, "WIP-D", "KG", "0.1"), ln(TEPUNG, "RMT000031", "KG", "1")]),
        bom(201, 2001, "WIP-D", "KG", [ln(TEPUNG, "RMT000031", "KG", "0.5")]),
    ]
    result = plan(
        [Demand(1004, 104, D(10)), Demand(1005, 105, D(10))], boms, {1004: 104, 1005: 105, 2001: 201}, sources=[]
    )
    wip = next(pl for pl in result.production_lines if pl.bom_id == 201)
    assert wip.qty == D("3.0000") and wip.level == 2
    assert result.production_lines[0].bom_id == 201  # level menurun: WIP diposting dulu
    (req,) = result.requirements
    assert req.need_qty == D("11.5000")  # 3 × 0.5 + 10 × 1


# ---------------------------------------------------------------- negatif
def test_bom_cycle_reports_path():
    boms = {
        301: bom(301, 3001, "A", "PCS", [ln(3002, "B", "PCS", "1")]),
        302: bom(302, 3002, "B", "PCS", [ln(3001, "A", "PCS", "1")]),
    }
    with pytest.raises(CalcError) as exc:
        explode([Demand(3001, 301, D(1))], boms, {3001: 301, 3002: 302})
    assert exc.value.code == "bom_cycle"
    assert exc.value.path == ["A", "B", "A"]


def test_self_cycle():
    boms = {301: bom(301, 3001, "A", "PCS", [ln(3001, "A", "PCS", "1")])}
    with pytest.raises(CalcError) as exc:
        explode([Demand(3001, 301, D(1))], boms, {3001: 301})
    assert exc.value.code == "bom_cycle"


def _chain(levels):
    boms = {}
    mapping = {}
    for i in range(levels):
        pid, bid = 4000 + i, 500 + i
        lines = [ln(4000 + i + 1, f"P{i + 1}", "KG", "1")] if i < levels - 1 else [ln(TEPUNG, "RMT", "KG", "1")]
        boms[bid] = bom(bid, pid, f"P{i}", "KG", lines)
        mapping[pid] = bid
    return boms, mapping


def test_depth_limit():
    boms, mapping = _chain(10)
    exp = explode([Demand(4000, 500, D(1))], boms, mapping)
    assert max(exp.level.values()) == 10
    boms, mapping = _chain(11)
    with pytest.raises(CalcError) as exc:
        explode([Demand(4000, 500, D(1))], boms, mapping)
    assert exc.value.code == "bom_too_deep"


def test_missing_bom():
    with pytest.raises(CalcError) as exc:
        explode([Demand(1, 999, D(1))], {}, {})
    assert exc.value.code == "bom_missing"


def test_uom_mismatch_excluded_from_pack_and_transfer():
    boms = [bom(101, 1001, "FIN-A", "PCS", [ln(SUSU, "RMT000021", "L", "0.045")])]
    result = plan(
        [Demand(1001, 101, D(30))],
        boms,
        {1001: 101},
        master_uom={"RMT000021": "ML"},
        pack_rules={"RMT000021": D(1000)},
        stock={(4, 7, SUSU): D(0), (4, 8, SUSU): D(5000)},
    )
    (req,) = result.requirements
    assert "uom_mismatch" in req.flags
    assert req.pack_qty is None and req.transfer_qty == D(0)
    assert req.shortage_qty == D("1.3500")
    assert result.transfers == () and result.suggestions == ()


def test_stock_flags_and_no_sources():
    boms = [bom(103, 1003, "FIN-T", "PCS", [ln(TEPUNG, "RMT000031", "KG", "1"), ln(41, "RMT000041", "KG", "1")])]
    result = plan([Demand(1003, 103, D(5))], boms, {1003: 103}, stock={(4, 7, 41): D(-3)}, sources=[])
    flags = {r.product_code: r.flags for r in result.requirements}
    assert flags == {"RMT000031": ("no_stock_data",), "RMT000041": ("negative_stock",)}
    assert all(r.shortage_qty == D(5) and r.stock_prod_qty == D(0) for r in result.requirements)
    assert result.transfers == ()
    assert short_materials(result) == [TEPUNG, 41]


def test_shortage_remains_when_sources_insufficient():
    boms = [bom(103, 1003, "FIN-T", "PCS", [ln(TEPUNG, "RMT000031", "KG", "1")])]
    result = plan([Demand(1003, 103, D(10))], boms, {1003: 103}, stock={(4, 7, TEPUNG): D(2), (4, 8, TEPUNG): D(3)})
    (req,) = result.requirements
    assert req.transfer_qty == D(3) and req.shortage_qty == D(5)


def test_source_order_ties_and_skips_production_location():
    stock = {(4, 9, 1): D(10), (4, 8, 1): D(10), (2, 3, 1): D(99), (4, 7, 1): D(50)}
    assert order_sources([(2, 3), (4, 9), (4, 8), (4, 7)], stock, 1, 4, 7) == [
        (4, 8, D(10)),
        (4, 9, D(10)),
        (2, 3, D(99)),
    ]


def test_apply_yield():
    boms = [bom(103, 1003, "FIN-T", "PCS", [ln(TEPUNG, "RMT000031", "KG", "1", "80")])]
    plain = plan([Demand(1003, 103, D(8))], boms, {1003: 103}, sources=[])
    assert plain.requirements[0].need_qty == D(8)
    with_yield = plan([Demand(1003, 103, D(8))], boms, {1003: 103}, sources=[], apply_yield=True)
    assert with_yield.requirements[0].need_qty == D(10)
    ignored = plan(
        [Demand(1003, 103, D(8))],
        [bom(103, 1003, "FIN-T", "PCS", [ln(TEPUNG, "RMT000031", "KG", "1", "100")])],
        {1003: 103},
        sources=[],
        apply_yield=True,
    )
    assert ignored.requirements[0].need_qty == D(8)


def test_pack_rule_on_wip_warning_and_determinism():
    boms = [
        bom(104, 1004, "FIN-C", "PCS", [ln(2001, "WIP-D", "KG", "0.2")]),
        bom(201, 2001, "WIP-D", "KG", [ln(TEPUNG, "RMT000031", "KG", "0.5")]),
    ]
    a = plan([Demand(1004, 104, D(10))], boms, {1004: 104, 2001: 201}, pack_rules={"WIP-D": D(5)})
    b = plan([Demand(1004, 104, D(10))], boms, {1004: 104, 2001: 201}, pack_rules={"WIP-D": D(5)})
    assert a == b
    assert a.warnings == {"pack_rule_on_wip": ("WIP-D",)}


def test_rounding_half_up_and_to_decimal():
    assert q4(D("1.00005")) == D("1.0001")
    assert q4(D("1.00004")) == D("1.0000")
    assert to_decimal(0.1) == D("0.1")
    assert to_decimal("12.5") == D("12.5")
    for bad in (None, True, "abc", float("nan"), [1]):
        with pytest.raises((ValueError, ArithmeticError)):
            to_decimal(bad)
