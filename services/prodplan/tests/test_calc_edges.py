"""Kasus tepi perhitungan (docs/04 §30.5) di luar vektor wajib: kelipatan kemasan tepat, qty desimal, diamond BOM,
kedalaman lewat memo, bahan di beberapa level, stok sumber nol/negatif, pembulatan kemasan melebihi stok sumber.

Semua angka dihitung tangan di komentar; tes murni (tanpa I/O).
"""

from decimal import Decimal as D

import pytest

from efn_prodplan.calc import Bom, BomLine, CalcError, Demand, Transfer, TransferLine, compute_plan, explode

SUSU, TEPUNG, GULA = 21, 31, 41
SOURCES = [(4, 8), (4, 9), (13, 21)]


def ln(pid, code, uom, qty, yld=None):
    return BomLine(pid, code, f"Nama {code}", uom, D(qty), D(yld) if yld is not None else None)


def bom(bom_id, pid, code, uom, lines):
    return Bom(bom_id, pid, code, f"Nama {code}", uom, tuple(lines))


def plan(demands, boms, mapping, **kw):
    args = {"master_uom": {}, "pack_rules": {}, "stock": {}, "prod_branch": 4, "prod_loc": 7, "sources": SOURCES}
    args.update(kw)
    return compute_plan(demands, {b.bom_id: b for b in boms}, mapping, **args)


MILK_BOM = bom(101, 1001, "FIN-A", "PCS", [ln(SUSU, "RMT000021", "ML", "45")])


# ---------------------------------------------------------------- kemasan utuh
def test_need_exact_multiple_of_pack_has_no_leftover_and_no_suggestion():
    # 100 pcs x 20 ml = 2000 ml = tepat 2 kemasan 1000 ml
    boms = [bom(101, 1001, "FIN-A", "PCS", [ln(SUSU, "RMT000021", "ML", "20")])]
    result = plan(
        [Demand(1001, 101, D(100))],
        boms,
        {1001: 101},
        master_uom={"RMT000021": "ML"},
        pack_rules={"RMT000021": D(1000)},
        stock={(4, 7, SUSU): D(0), (4, 8, SUSU): D(5000)},
    )
    (req,) = result.requirements
    assert (req.need_qty, req.packs, req.leftover_qty, req.need_eff_qty) == (D(2000), 2, D(0), D(2000))
    assert result.suggestions == ()
    assert req.transfer_qty == D(2000) and req.shortage_qty == D(0)


def test_decimal_demand_rounds_pack_up_and_floors_suggestion():
    # 0.5 pcs x 45 ml = 22.5 ml -> 1 kemasan, sisa 977.5; floor(977.5 / 45) = 21 (21 x 45 = 945)
    result = plan(
        [Demand(1001, 101, D("0.5"))],
        [MILK_BOM],
        {1001: 101},
        master_uom={"RMT000021": "ML"},
        pack_rules={"RMT000021": D(1000)},
    )
    (req,) = result.requirements
    assert req.need_qty == D("22.5000") and req.packs == 1 and req.leftover_qty == D("977.5000")
    (sug,) = result.suggestions
    assert (sug.extra_units, sug.uses_qty, sug.leftover_qty) == (21, D("945.0000"), D("977.5000"))


def test_pack_take_never_exceeds_source_stock_and_leaves_shortage():
    # butuh 2000 (2 kemasan), stok produksi 0, sumber hanya 1500 -> ambil 1 kemasan (1000), sisa 1000 = kekurangan
    boms = [bom(101, 1001, "FIN-A", "PCS", [ln(SUSU, "RMT000021", "ML", "20")])]
    result = plan(
        [Demand(1001, 101, D(100))],
        boms,
        {1001: 101},
        master_uom={"RMT000021": "ML"},
        pack_rules={"RMT000021": D(1000)},
        stock={(4, 7, SUSU): D(0), (4, 8, SUSU): D(1500)},
    )
    (req,) = result.requirements
    assert req.transfer_qty == D(1000) and req.shortage_qty == D(1000)
    assert result.transfers == (
        Transfer("simple_transfer", 4, 8, 4, 7, (TransferLine(SUSU, "RMT000021", "ML", D("1000.0000")),)),
    )


def test_pack_source_below_one_pack_is_unusable():
    # sumber 999 < 1 kemasan -> tidak ada transfer, kekurangan penuh
    boms = [bom(101, 1001, "FIN-A", "PCS", [ln(SUSU, "RMT000021", "ML", "20")])]
    result = plan(
        [Demand(1001, 101, D(50))],
        boms,
        {1001: 101},
        master_uom={"RMT000021": "ML"},
        pack_rules={"RMT000021": D(1000)},
        stock={(4, 8, SUSU): D(999)},
        sources=[(4, 8)],
    )
    (req,) = result.requirements
    assert req.transfer_qty == D(0) and req.shortage_qty == D(1000)
    assert result.transfers == ()


# ---------------------------------------------------------------- stok sumber nol / negatif
def test_zero_and_negative_source_stock_are_skipped():
    boms = [bom(103, 1003, "FIN-T", "PCS", [ln(TEPUNG, "RMT000031", "KG", "1")])]
    stock = {(4, 7, TEPUNG): D(0), (4, 8, TEPUNG): D(-5), (4, 9, TEPUNG): D(0), (13, 21, TEPUNG): D(3)}
    result = plan([Demand(1003, 103, D(5))], boms, {1003: 103}, stock=stock)
    (req,) = result.requirements
    assert req.transfer_qty == D(3) and req.shortage_qty == D(2)
    assert [(t.doc_type, t.origin_branch_id, t.origin_location_id) for t in result.transfers] == [
        ("goods_transfer_request", 13, 21)
    ]


def test_two_materials_from_same_source_merge_into_one_transfer_sorted_by_product():
    boms = [bom(103, 1003, "FIN-T", "PCS", [ln(GULA, "RMT000041", "KG", "2"), ln(TEPUNG, "RMT000031", "KG", "1")])]
    stock = {(4, 8, TEPUNG): D(10), (4, 8, GULA): D(10)}
    result = plan([Demand(1003, 103, D(3))], boms, {1003: 103}, stock=stock)
    (transfer,) = result.transfers
    assert [(t.product_detail_id, t.qty) for t in transfer.lines] == [(TEPUNG, D("3.0000")), (GULA, D("6.0000"))]


# ---------------------------------------------------------------- struktur BOM
def test_diamond_bom_explodes_shared_child_once_with_summed_quantity():
    # A(1) -> B 1, C 1 ; B -> D 2 ; C -> D 3 ; D -> tepung 1. qty D = 5, tepung = 5, D level 3.
    boms = [
        bom(1, 10, "A", "PCS", [ln(11, "B", "PCS", "1"), ln(12, "C", "PCS", "1")]),
        bom(2, 11, "B", "PCS", [ln(13, "D", "KG", "2")]),
        bom(3, 12, "C", "PCS", [ln(13, "D", "KG", "3")]),
        bom(4, 13, "D", "KG", [ln(TEPUNG, "RMT000031", "KG", "1")]),
    ]
    result = plan([Demand(10, 1, D(1))], boms, {10: 1, 11: 2, 12: 3, 13: 4}, sources=[])
    by_bom = {pl.bom_id: pl for pl in result.production_lines}
    assert by_bom[4].qty == D("5.0000") and by_bom[4].level == 3 and not by_bom[4].is_demand
    assert [pl.bom_id for pl in result.production_lines] == [4, 2, 3, 1]  # level menurun, lalu bom_id naik
    (req,) = result.requirements
    assert req.need_qty == D("5.0000")


def test_raw_material_used_at_two_levels_is_aggregated_and_suggests_for_both_boms():
    # FIN-C 10 pcs: WIP-D 0.2 + tepung 1; WIP-D 2.0: tepung 0.5 -> need 10 + 1 = 11; kemasan 5 -> 3 kemasan, sisa 4
    boms = [
        bom(104, 1004, "FIN-C", "PCS", [ln(2001, "WIP-D", "KG", "0.2"), ln(TEPUNG, "RMT000031", "KG", "1")]),
        bom(201, 2001, "WIP-D", "KG", [ln(TEPUNG, "RMT000031", "KG", "0.5")]),
    ]
    result = plan(
        [Demand(1004, 104, D(10))],
        boms,
        {1004: 104, 2001: 201},
        master_uom={"RMT000031": "KG"},
        pack_rules={"RMT000031": D(5)},
        sources=[],
    )
    (req,) = result.requirements
    assert (req.need_qty, req.packs, req.leftover_qty, req.need_eff_qty) == (D(11), 3, D(4), D(15))
    # usulan untuk BOM langsung pemakai bahan di level berapa pun: FIN-C floor(4/1)=4, WIP-D floor(4/0.5)=8
    assert [(s.bom_id, s.extra_units) for s in result.suggestions] == [(104, 4), (201, 8)]


def test_two_boms_for_same_product_demand_bom_overrides_default_mapping():
    boms = [
        bom(10, 1001, "FIN-A", "PCS", [ln(TEPUNG, "RMT000031", "KG", "1")]),
        bom(20, 1001, "FIN-A", "PCS", [ln(TEPUNG, "RMT000031", "KG", "2")]),
    ]
    result = plan([Demand(1001, 20, D(3))], boms, {1001: 10}, sources=[])
    assert [pl.bom_id for pl in result.production_lines] == [20]
    assert result.requirements[0].need_qty == D(6)


def test_zero_or_negative_demand_is_ignored():
    result = plan([Demand(1001, 101, D(0)), Demand(1001, 101, D(-2))], [MILK_BOM], {1001: 101})
    assert result.production_lines == () and result.requirements == () and result.transfers == ()


def test_duplicate_demand_lines_for_same_bom_are_summed():
    result = plan([Demand(1001, 101, D(10)), Demand(1001, 101, D(5))], [MILK_BOM], {1001: 101}, sources=[])
    assert result.production_lines[0].qty == D(15) and result.requirements[0].need_qty == D(675)


def test_three_node_cycle_reports_full_path():
    boms = {
        1: bom(1, 10, "A", "PCS", [ln(11, "B", "PCS", "1")]),
        2: bom(2, 11, "B", "PCS", [ln(12, "C", "PCS", "1")]),
        3: bom(3, 12, "C", "PCS", [ln(10, "A", "PCS", "1")]),
    }
    with pytest.raises(CalcError) as exc:
        explode([Demand(10, 1, D(1))], boms, {10: 1, 11: 2, 12: 3})
    assert exc.value.code == "bom_cycle" and exc.value.path == ["A", "B", "C", "A"]


def _chain(levels):
    boms, mapping = {}, {}
    for i in range(levels):
        pid, bid = 4000 + i, 500 + i
        lines = [ln(4000 + i + 1, f"P{i + 1}", "KG", "1")] if i < levels - 1 else [ln(TEPUNG, "RMT", "KG", "1")]
        boms[bid] = bom(bid, pid, f"P{i}", "KG", lines)
        mapping[pid] = bid
    return boms, mapping


def test_depth_limit_applies_through_memoized_shared_subtree():
    # P0..P9 = rantai 10 level. Root kedua Q -> Z -> P1 membuat P9 berada di level 11 walau P1 sudah di-memo.
    boms, mapping = _chain(10)
    boms[600] = bom(600, 6000, "Q", "PCS", [ln(6100, "Z", "PCS", "1")])
    boms[610] = bom(610, 6100, "Z", "PCS", [ln(4001, "P1", "KG", "1")])
    mapping.update({6000: 600, 6100: 610})
    demands = [Demand(4000, 500, D(1)), Demand(6000, 600, D(1))]
    with pytest.raises(CalcError) as exc:
        explode(demands, boms, mapping)
    assert exc.value.code == "bom_too_deep"
    # tanpa Z: Q -> P1 langsung = tepat 10 level -> sah, dan P9 tetap level 10
    boms[600] = bom(600, 6000, "Q", "PCS", [ln(4001, "P1", "KG", "1")])
    exp = explode(demands, boms, mapping)
    assert exp.level[509] == 10 and exp.qty[509] == D(2)
