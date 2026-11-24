"""Perhitungan rencana produksi deterministik (docs/04 §30.5). MURNI: tanpa I/O, aritmetika `Decimal` prec 28.

Pembulatan 4 desimal ROUND_HALF_UP hanya pada keluaran. Masukan = data yang sudah dibaca job dari ESB/PG:
permintaan, BOM Assembly (per `bom_id`), pemetaan produk -> BOM Assembly, satuan master per kode produk, aturan kemasan
utuh, snapshot stok `(branch, location, productDetailID) -> qty`, lokasi produksi, lokasi sumber transfer.

Keterbatasan kontrak: tanpa netting stok WIP (L-253), `pendingQty` tidak dikurangi (L-254), urutan sumber = stok
terbesar bukan FEFO (L-255).
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Context, Decimal, localcontext

MAX_DEPTH = 10
Q4 = Decimal("0.0001")
ZERO = Decimal(0)
HUNDRED = Decimal(100)
_CTX = Context(prec=28)


def q4(value: Decimal) -> Decimal:
    return value.quantize(Q4, rounding=ROUND_HALF_UP)


def to_decimal(value: object) -> Decimal:
    """Angka ESB (int/float/str) -> Decimal lewat `str` (float tidak dikonversi biner langsung)."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or value is None:
        raise ValueError("bukan angka")
    if isinstance(value, int | float | str):
        result = Decimal(str(value).strip())
        if not result.is_finite():
            raise ValueError("bukan angka hingga")
        return result
    raise ValueError("bukan angka")


class CalcError(Exception):
    """`code` ∈ bom_cycle | bom_too_deep | bom_missing; `path` = jalur productCode (untuk `details`)."""

    def __init__(self, code: str, path: Sequence[str] = ()) -> None:
        super().__init__(code)
        self.code = code
        self.path = list(path)


@dataclass(frozen=True)
class BomLine:
    product_detail_id: int
    product_code: str
    product_name: str
    uom_name: str
    qty: Decimal  # per 1 satuan hasil
    yield_percent: Decimal | None = None


@dataclass(frozen=True)
class Bom:
    bom_id: int
    product_detail_id: int
    product_code: str
    product_name: str
    uom_name: str
    lines: tuple[BomLine, ...]


@dataclass(frozen=True)
class Demand:
    product_detail_id: int
    bom_id: int
    qty: Decimal


@dataclass(frozen=True)
class MaterialUse:
    product_detail_id: int
    product_code: str
    qty_per_unit: Decimal
    total_qty: Decimal


@dataclass(frozen=True)
class ProductionLine:
    level: int
    bom_id: int
    product_detail_id: int
    product_code: str
    product_name: str
    uom_name: str
    qty: Decimal
    is_demand: bool
    materials: tuple[MaterialUse, ...]


@dataclass(frozen=True)
class Requirement:
    product_detail_id: int
    product_code: str
    product_name: str
    uom_name: str
    need_qty: Decimal
    pack_qty: Decimal | None
    packs: int | None
    leftover_qty: Decimal | None
    need_eff_qty: Decimal
    stock_prod_qty: Decimal
    transfer_qty: Decimal
    shortage_qty: Decimal
    flags: tuple[str, ...]


@dataclass(frozen=True)
class Suggestion:
    material_detail_id: int
    bom_id: int
    product_detail_id: int
    extra_units: int
    uses_qty: Decimal
    leftover_qty: Decimal


@dataclass(frozen=True)
class TransferLine:
    product_detail_id: int
    product_code: str
    uom_name: str
    qty: Decimal


@dataclass(frozen=True)
class Transfer:
    doc_type: str  # simple_transfer | goods_transfer_request
    origin_branch_id: int
    origin_location_id: int
    dest_branch_id: int
    dest_location_id: int
    lines: tuple[TransferLine, ...]


@dataclass(frozen=True)
class PlanResult:
    production_lines: tuple[ProductionLine, ...]
    requirements: tuple[Requirement, ...]
    suggestions: tuple[Suggestion, ...]
    transfers: tuple[Transfer, ...]
    warnings: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class Explosion:
    """Hasil ledakan BOM (internal, presisi penuh)."""

    order: tuple[int, ...]  # bom_id urut posting (level menurun, bom_id naik)
    qty: Mapping[int, Decimal]
    level: Mapping[int, int]
    demanded: frozenset[int]
    need: Mapping[int, Decimal]  # bahan (bukan baris produksi) -> kebutuhan


def effective_qty(line: BomLine, apply_yield: bool) -> Decimal:
    """`q` (÷ yield/100 bila PRODPLAN_APPLY_YIELD dan 0 < yield < 100, Q-PP5)."""
    if apply_yield and line.yield_percent is not None and ZERO < line.yield_percent < HUNDRED:
        return line.qty / (line.yield_percent / HUNDRED)
    return line.qty


def _choose_bom(demands: Sequence[Demand], bom_for_product: Mapping[int, int]) -> dict[int, int]:
    chosen = dict(bom_for_product)
    by_product: dict[int, int] = {}
    for d in demands:
        by_product[d.product_detail_id] = min(d.bom_id, by_product.get(d.product_detail_id, d.bom_id))
    chosen.update(by_product)
    return chosen


def clean_boms(boms: Mapping[int, Bom]) -> tuple[dict[int, Bom], tuple[str, ...]]:
    """SEC-231: baris BOM ber-qty ≤ 0 atau bukan angka hingga dibuang (tidak pernah menghasilkan totalQty ≤ 0);
    kembalikan (BOM bersih, kode "BOMCODE:BAHAN" yang dibuang, urut) untuk peringatan hasil hitung."""
    out: dict[int, Bom] = {}
    dropped: list[str] = []

    def ok(ln: BomLine) -> bool:
        return ln.qty.is_finite() and ln.qty > ZERO

    for bom_id, bom in boms.items():
        good = tuple(ln for ln in bom.lines if ok(ln))
        if len(good) != len(bom.lines):
            dropped.extend(f"{bom.product_code}:{ln.product_code}" for ln in bom.lines if not ok(ln))
            bom = Bom(bom.bom_id, bom.product_detail_id, bom.product_code, bom.product_name, bom.uom_name, good)
        out[bom_id] = bom
    return out, tuple(sorted(set(dropped)))


def _children(bom: Bom, chosen: Mapping[int, int]) -> list[int]:
    return sorted({chosen[ln.product_detail_id] for ln in bom.lines if ln.product_detail_id in chosen})


def explode(
    demands: Sequence[Demand],
    boms: Mapping[int, Bom],
    bom_for_product: Mapping[int, int],
    *,
    apply_yield: bool = False,
) -> Explosion:
    """Langkah 1–2 §30.5: ledakan multi-level + deteksi siklus (stack DFS) + kedalaman maks 10."""
    with localcontext(_CTX):
        boms, _ = clean_boms(boms)
        valid = [d for d in demands if d.qty > ZERO]
        chosen = _choose_bom(valid, bom_for_product)
        roots = sorted({d.bom_id for d in valid})
        for bom_id in roots:
            if bom_id not in boms:
                raise CalcError("bom_missing", [str(bom_id)])

        def code(bom_id: int) -> str:
            bom = boms.get(bom_id)
            return bom.product_code if bom else str(bom_id)

        height: dict[int, int] = {}
        on_stack: set[int] = set()
        stack: list[int] = []

        def visit(bom_id: int) -> None:
            if bom_id in on_stack:
                start = stack.index(bom_id)
                raise CalcError("bom_cycle", [code(b) for b in stack[start:]] + [code(bom_id)])
            if bom_id in height:
                if len(stack) + height[bom_id] > MAX_DEPTH:
                    raise CalcError("bom_too_deep", [code(b) for b in stack] + [code(bom_id)])
                return
            if len(stack) >= MAX_DEPTH:
                raise CalcError("bom_too_deep", [code(b) for b in stack] + [code(bom_id)])
            bom = boms.get(bom_id)
            if bom is None:
                raise CalcError("bom_missing", [code(b) for b in stack] + [str(bom_id)])
            on_stack.add(bom_id)
            stack.append(bom_id)
            h = 1
            for child in _children(bom, chosen):
                visit(child)
                h = max(h, 1 + height[child])
            stack.pop()
            on_stack.discard(bom_id)
            height[bom_id] = h

        for root in roots:
            visit(root)

        # Kahn: induk diproses sebelum anak -> qty anak = total kebutuhan semua induk (tanpa netting WIP, L-253).
        nodes = sorted(height)
        indegree = dict.fromkeys(nodes, 0)
        for b in nodes:
            for child in _children(boms[b], chosen):
                indegree[child] += 1
        demand_qty: dict[int, Decimal] = {}
        for d in valid:
            demand_qty[d.bom_id] = demand_qty.get(d.bom_id, ZERO) + d.qty
        qty = {b: demand_qty.get(b, ZERO) for b in nodes}
        level = {b: 1 for b in nodes if b in demand_qty}
        need: dict[int, Decimal] = {}
        ready = sorted(b for b in nodes if indegree[b] == 0)
        processed = 0
        while ready:
            b = ready.pop(0)
            processed += 1
            bom = boms[b]
            for ln in bom.lines:
                amount = qty[b] * effective_qty(ln, apply_yield)
                if ln.product_detail_id in chosen:
                    child = chosen[ln.product_detail_id]
                    qty[child] += amount
                    level[child] = max(level.get(child, 0), level.get(b, 1) + 1)
                else:
                    need[ln.product_detail_id] = need.get(ln.product_detail_id, ZERO) + amount
            for child in _children(bom, chosen):
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
                    ready.sort()
        if processed != len(nodes):  # pragma: no cover - siklus sudah ditangkap DFS
            raise CalcError("bom_cycle")
        if any(lv > MAX_DEPTH for lv in level.values()):
            deepest = max(level, key=lambda b: (level[b], b))
            raise CalcError("bom_too_deep", [code(deepest)])
        order = tuple(sorted(nodes, key=lambda b: (-level[b], b)))
        return Explosion(order=order, qty=qty, level=level, demanded=frozenset(demand_qty), need=need)


def _stock(stock: Mapping[tuple[int, int, int], Decimal], key: tuple[int, int, int]) -> tuple[Decimal, str | None]:
    value = stock.get(key)
    if value is None:
        return ZERO, "no_stock_data"
    if value < ZERO:
        return ZERO, "negative_stock"
    return value, None


def ceil_div(a: Decimal, b: Decimal) -> int:
    return int((a / b).to_integral_value(rounding=ROUND_CEILING))


def floor_div(a: Decimal, b: Decimal) -> int:
    return int((a / b).to_integral_value(rounding=ROUND_FLOOR))


def order_sources(
    sources: Iterable[tuple[int, int]],
    stock: Mapping[tuple[int, int, int], Decimal],
    product_detail_id: int,
    prod_branch: int,
    prod_loc: int,
) -> list[tuple[int, int, Decimal]]:
    """Langkah 7: cabang produksi dulu, lalu branch_id naik; per cabang stok terbesar dulu, seri -> location_id naik."""
    out: list[tuple[int, int, Decimal]] = []
    for branch, loc in sources:
        if (branch, loc) == (prod_branch, prod_loc):
            continue
        qty, _ = _stock(stock, (branch, loc, product_detail_id))
        out.append((branch, loc, qty))
    out.sort(key=lambda s: (0 if s[0] == prod_branch else 1, s[0], -s[2], s[1]))
    return out


def allocate(
    short: Decimal, candidates: Sequence[tuple[int, int, Decimal]], pack_qty: Decimal | None
) -> tuple[list[tuple[int, int, Decimal]], Decimal]:
    """Ambil dari sumber berurutan sampai `sisa <= 0`. Kembalikan (takes, shortage)."""
    remaining = short
    takes: list[tuple[int, int, Decimal]] = []
    for branch, loc, available in candidates:
        if remaining <= ZERO:
            break
        if pack_qty is not None:
            take = min(ceil_div(remaining, pack_qty) * pack_qty, floor_div(available, pack_qty) * pack_qty)
        else:
            take = min(remaining, available)
        if take > ZERO:
            takes.append((branch, loc, take))
            remaining -= take
    return takes, max(ZERO, remaining)


def _same_uom(a: str, b: str) -> bool:
    return a.strip().upper() == b.strip().upper()


def compute_plan(
    demands: Sequence[Demand],
    boms: Mapping[int, Bom],
    bom_for_product: Mapping[int, int],
    *,
    master_uom: Mapping[str, str],
    pack_rules: Mapping[str, Decimal],
    stock: Mapping[tuple[int, int, int], Decimal],
    prod_branch: int,
    prod_loc: int,
    sources: Sequence[tuple[int, int]],
    apply_yield: bool = False,
) -> PlanResult:
    """§30.5 langkah 1–7. Deterministik untuk masukan yang sama."""
    with localcontext(_CTX):
        boms, invalid_qty = clean_boms(boms)
        exp = explode(demands, boms, bom_for_product, apply_yield=apply_yield)
        chosen = _choose_bom([d for d in demands if d.qty > ZERO], bom_for_product)

        # Baris produksi + snapshot material.
        lines: list[ProductionLine] = []
        material_info: dict[int, BomLine] = {}
        material_uoms: dict[int, set[str]] = {}
        direct_users: dict[int, list[tuple[int, int, Decimal]]] = {}  # bahan -> [(bom_id, product, q_eff)]
        for bom_id in exp.order:
            bom = boms[bom_id]
            qty = exp.qty[bom_id]
            uses: dict[int, Decimal] = {}
            codes: dict[int, str] = {}
            for ln in bom.lines:
                uses[ln.product_detail_id] = uses.get(ln.product_detail_id, ZERO) + effective_qty(ln, apply_yield)
                codes.setdefault(ln.product_detail_id, ln.product_code)
                if ln.product_detail_id not in chosen:
                    material_info.setdefault(ln.product_detail_id, ln)
                    material_uoms.setdefault(ln.product_detail_id, set()).add(ln.uom_name.strip().upper())
            materials: list[MaterialUse] = []
            for detail_id in sorted(uses):
                q_sum = uses[detail_id]
                materials.append(MaterialUse(detail_id, codes[detail_id], q4(q_sum), q4(qty * q_sum)))
                if detail_id not in chosen and q_sum > ZERO:
                    direct_users.setdefault(detail_id, []).append((bom_id, bom.product_detail_id, q_sum))
            lines.append(
                ProductionLine(
                    level=exp.level[bom_id],
                    bom_id=bom_id,
                    product_detail_id=bom.product_detail_id,
                    product_code=bom.product_code,
                    product_name=bom.product_name,
                    uom_name=bom.uom_name,
                    qty=q4(qty),
                    is_demand=bom_id in exp.demanded,
                    materials=tuple(materials),
                )
            )

        requirements: list[Requirement] = []
        suggestions: list[Suggestion] = []
        grouped: dict[tuple[int, int], list[TransferLine]] = {}
        for detail_id in sorted(exp.need):
            info = material_info[detail_id]
            need = exp.need[detail_id]
            flags: list[str] = []
            master = master_uom.get(info.product_code)
            mismatch = len(material_uoms.get(detail_id, set())) > 1 or (
                master is not None and not _same_uom(master, info.uom_name)
            )
            if mismatch:
                flags.append("uom_mismatch")
            pack = pack_rules.get(info.product_code)
            pack_qty = pack if (pack is not None and pack > ZERO and not mismatch) else None
            packs: int | None = None
            leftover: Decimal | None = None
            need_eff = need
            if pack_qty is not None:
                packs = ceil_div(need, pack_qty)
                need_eff = packs * pack_qty
                leftover = need_eff - need
            stock_prod, stock_flag = _stock(stock, (prod_branch, prod_loc, detail_id))
            if stock_flag:
                flags.append(stock_flag)
            short = max(ZERO, need_eff - stock_prod)
            transfer_total = ZERO
            shortage = short
            if short > ZERO and not mismatch and sources:
                candidates = order_sources(sources, stock, detail_id, prod_branch, prod_loc)
                takes, shortage = allocate(short, candidates, pack_qty)
                for branch, loc, take in takes:
                    transfer_total += take
                    grouped.setdefault((branch, loc), []).append(
                        TransferLine(detail_id, info.product_code, info.uom_name, q4(take))
                    )
            requirements.append(
                Requirement(
                    product_detail_id=detail_id,
                    product_code=info.product_code,
                    product_name=info.product_name,
                    uom_name=info.uom_name,
                    need_qty=q4(need),
                    pack_qty=q4(pack_qty) if pack_qty is not None else None,
                    packs=packs,
                    leftover_qty=q4(leftover) if leftover is not None else None,
                    need_eff_qty=q4(need_eff),
                    stock_prod_qty=q4(stock_prod),
                    transfer_qty=q4(transfer_total),
                    shortage_qty=q4(shortage),
                    flags=tuple(flags),
                )
            )
            if leftover is not None and leftover > ZERO:
                for bom_id, product_id, q_eff in sorted(direct_users.get(detail_id, []), key=lambda u: u[0]):
                    extra = floor_div(leftover, q_eff)
                    if extra >= 1:
                        suggestions.append(
                            Suggestion(detail_id, bom_id, product_id, extra, q4(extra * q_eff), q4(leftover))
                        )

        transfers = tuple(
            Transfer(
                doc_type="simple_transfer" if branch == prod_branch else "goods_transfer_request",
                origin_branch_id=branch,
                origin_location_id=loc,
                dest_branch_id=prod_branch,
                dest_location_id=prod_loc,
                lines=tuple(sorted(grouped[(branch, loc)], key=lambda t: t.product_detail_id)),
            )
            for branch, loc in sorted(grouped, key=lambda k: (0 if k[0] == prod_branch else 1, k[0], k[1]))
        )
        wip_codes = {boms[b].product_code for b in exp.order}
        warnings: dict[str, tuple[str, ...]] = {}
        packed_wip = tuple(sorted(c for c in pack_rules if c in wip_codes))
        if packed_wip:
            warnings["pack_rule_on_wip"] = packed_wip
        if invalid_qty:
            warnings["invalid_bom_qty"] = invalid_qty
        return PlanResult(
            production_lines=tuple(lines),
            requirements=tuple(requirements),
            suggestions=tuple(suggestions),
            transfers=transfers,
            warnings=warnings,
        )


def short_materials(result: PlanResult) -> list[int]:
    """Bahan yang masih kurang di lokasi produksi (untuk menentukan stok sumber yang perlu dibaca)."""
    return [r.product_detail_id for r in result.requirements if r.shortage_qty > ZERO and "uom_mismatch" not in r.flags]
