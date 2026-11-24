"""G67 "Margin bahan": indeks harga bahan + atribusi margin (docs/04 §31, L-259..L-264; ADR-040/041 Proposed).

Sumber (sudah ada, tanpa endpoint ESB baru): resep `efn_dwh.dim_esb_bom` + `dim_esb_bom_line` (versi
snapshot terakhir per brand), harga terima PO `efn_dwh.fact_esb_purchase_line` (FINAL + hanya versi slice
ber-checkpoint), penjualan & COGS ESB `efn_dwh.agg_esb_sales_daily`. Tujuan (dibuat admin di
infra/clickhouse/init/24-margin-bahan.sql): `efn_dwh.mart_esb_ing_price_monthly`, `mart_esb_menu_cost_monthly`,
`mart_esb_ing_group_monthly`. Setiap refresh meng-INSERT VERSI BARU (`_version` = mulai run, UTC; L-263); view
`efn_dwh.v_esb_*` = versi terakhir per brand. esb_writer hanya SELECT + INSERT (tanpa CREATE/DROP/ALTER/TRUNCATE).

Algoritme §31.2 (sama dengan laporan analisis v2). Jendela = `ESB_MARGIN_MONTHS` bulan kalender PENUH terakhir.
1. Harga bahan bulanan per (product_code, uom): baris PO bukan batal, receipt_qty > 0, IDR; harga =
   Σ price × receipt_qty / Σ receipt_qty (rata-rata berbobot, satuan beli). price_4m = sama atas bulan ini +
   3 sebelumnya. Satuan beli dominan per bahan = nilai terbesar; satuan lain diabaikan.
2. idx(m) = price_4m(m) / price_4m(dasar); dasar = bulan pertama jendela yang punya harga (tanpa konversi
   satuan).
3. Kelompok = sub_category_name PO terbaru -> category_name -> 'Lainnya' (L-262: tanpa nama produk/supplier
   di kode).
4. BOM `Menu` aktif dicocokkan ke menu penjualan lewat nama kanonik (huruf besar, spasi tunggal, tanpa awalan
   `KIOSK - ` dan akhiran ` T`). Komponen = product_code BOM non-Menu aktif -> diurai proporsional nilai
   baris (qty × last_hpp, tanpa rasio batch); siklus / kedalaman > 8 -> daun. Menu tanpa BOM -> bom_matched 0.
5. cost_idx(m) = Σ w·idx(m) / Σ w (daun tanpa harga PO: idx = 1); leaf_coverage = porsi nilai daun ber-harga.
6. Laspeyres (keranjang tetap): qty menu = total jendela / jumlah bulan; basket_value(g, m) =
   Σ_menu qty/bulan × Σ_daun∈g w·idx(m), diskalakan ke rata-rata COGS ESB bulanan menu yang cocok.
   Kontribusi = basket_value - base_value (dihitung pembaca).

Logika murni (`compute` + fungsi kecil) diuji tanpa ClickHouse; I/O lewat `MarginStore`. SQL = templat
konstanta; yang disisipkan hanya nama database (identifier tervalidasi, SEC-AG-04); semua nilai lewat parameter
ClickHouse. Kunci = kunci heartbeat agregat (`efn_esb.aggregate_refresh_lock`, aggregates.RefreshLock): refresh
margin tidak pernah berjalan bersamaan dengan refresh margin lain maupun `refresh-aggregates`. Catatan
kesegaran: `efn_esb.aggregate_refresh` name `margin_<brand>`.
"""

import logging
import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol

from efn_esb.aggregates import (
    BUSY_ERROR,
    REFRESH_COLUMNS,
    REFRESH_TABLE,
    LockLost,
    RefreshLock,
    RefreshResult,
    RefreshStore,
    error_label,
)
from efn_esb.planning import wib_now
from efn_esb.records import version_now

log = logging.getLogger("efn_esb.margin")

PRICE_TABLE = "mart_esb_ing_price_monthly"
MENU_TABLE = "mart_esb_menu_cost_monthly"
GROUP_TABLE = "mart_esb_ing_group_monthly"
# Urutan = kolom tabel/kontrak view §31.3 (diuji sama dengan 24-margin-bahan.sql). `_loaded_at` = DEFAULT server.
PRICE_COLUMNS = (
    "brand",
    "company_code",
    "month",
    "product_code",
    "product_name",
    "ing_group",
    "uom",
    "receipt_qty",
    "value",
    "price",
    "price_4m",
    "idx",
    "usage_value",
    "_version",
)
MENU_COLUMNS = (
    "brand",
    "company_code",
    "month",
    "menu_name",
    "menu_category",
    "qty",
    "net",
    "cogs",
    "bom_matched",
    "cost_idx",
    "leaf_coverage",
    "_version",
)
GROUP_COLUMNS = (
    "brand",
    "company_code",
    "month",
    "ing_group",
    "basket_value",
    "base_value",
    "_version",
)
MART_TABLES: dict[str, tuple[str, ...]] = {
    PRICE_TABLE: PRICE_COLUMNS,
    MENU_TABLE: MENU_COLUMNS,
    GROUP_TABLE: GROUP_COLUMNS,
}

ROLLING_MONTHS = 4
MAX_BOM_DEPTH = 8  # jumlah BOM maksimum dalam satu rantai (menu + 7 tingkat sub-BOM)
MENU_BOM_TYPE = "MENU"  # bom_type_name setelah normalize_name
KIOSK_PREFIX = "KIOSK - "
TAKEAWAY_SUFFIX = " T"
DEFAULT_GROUP = "Lainnya"
UNPRICED_GROUP = "Tanpa harga PO"
CANCEL_MARKERS = ("cancel", "batal", "void")  # status PO batal (substring, tanpa beda huruf besar/kecil)
REFRESH_PREFIX = "margin_"
REFRESH_MIN_AGE = timedelta(days=6)
ZERO = Decimal(0)
Q2 = Decimal("0.01")
Q3 = Decimal("0.001")
Q4 = Decimal("0.0001")
_DB_RE = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")
_BRAND_RE = re.compile(r"^[a-z0-9]{2,16}$")

# ------------------------------------------------------------------------------------------------ SQL (templat)
# `$mart` / `$raw` = nama database (render()); nilai SELALU parameter ClickHouse.
PURCHASE_SQL = """
SELECT toStartOfMonth(purchase_date) AS m, product_code,
       if(upper(toString(currency)) IN ('', 'IDR', 'RP'), toString(uom),
          concat(toString(uom), ' (', upper(toString(currency)), ')')) AS uom_key, sum(receipt_qty) AS rq,
       sum(toDecimal128(price, 4) * toDecimal128(receipt_qty, 3)) AS val, max(purchase_date) AS last_d,
       argMax(product_name, purchase_date) AS p_name,
       argMaxIf(sub_category_name, purchase_date, sub_category_name != '') AS p_sub,
       argMaxIf(category_name, purchase_date, category_name != '') AS p_cat
FROM $mart.fact_esb_purchase_line FINAL
WHERE brand = {brand:String} AND purchase_date >= {start:Date} AND purchase_date <= {end:Date}
  AND receipt_qty > 0 AND product_code != ''
  AND NOT multiSearchAnyCaseInsensitive(toString(status_name), {cancel:Array(String)})
  AND (brand, purchase_date, _version) IN
      (SELECT brand, day, finished_at FROM $raw.extract_checkpoint FINAL
       WHERE entity = 'purchase_line' AND scope_key = '*' AND brand = {brand:String})
GROUP BY m, product_code, uom_key
"""
SALES_SQL = """
SELECT toStartOfMonth(sales_date) AS m, menu_name, menu_category, sum(qty) AS q, sum(net) AS n, sum(cogs) AS c
FROM $mart.agg_esb_sales_daily
WHERE brand = {brand:String} AND sales_date >= {start:Date} AND sales_date <= {end:Date}
GROUP BY m, menu_name, menu_category
"""
BOM_SQL = """
SELECT bom_id, bom_name, bom_type_name, product_code, product_name, active
FROM $mart.dim_esb_bom FINAL
WHERE brand = {brand:String}
  AND _version = (SELECT max(_version) FROM $mart.dim_esb_bom WHERE brand = {brand:String})
"""
BOM_LINE_SQL = """
SELECT bom_id, product_code, qty, last_hpp
FROM $mart.dim_esb_bom_line FINAL
WHERE brand = {brand:String}
  AND _version = (SELECT max(_version) FROM $mart.dim_esb_bom_line WHERE brand = {brand:String})
"""
COMPANY_SQL = """
SELECT company_code, count() AS n
FROM $mart.dim_esb_branch FINAL
WHERE brand = {brand:String} AND active = 1 AND company_code != ''
GROUP BY company_code
"""
LAST_OK_SQL = """
SELECT count(), max(finished_at)
FROM $raw.aggregate_refresh
WHERE name = {name:String} AND status = 'ok'
"""
TEMPLATES = (PURCHASE_SQL, SALES_SQL, BOM_SQL, BOM_LINE_SQL, COMPANY_SQL, LAST_OK_SQL)


def render(template: str, store: RefreshStore) -> str:
    """Sisipkan nama database (identifier sederhana, divalidasi ulang di sini); nilai tidak pernah disisipkan."""
    mart, raw = store.mart_db, store.raw_db
    if not (_DB_RE.fullmatch(mart) and _DB_RE.fullmatch(raw)):
        raise ValueError("nama database tidak valid")
    return template.replace("$mart", mart).replace("$raw", raw)


def refresh_name(brand: str) -> str:
    if not _BRAND_RE.fullmatch(brand):
        raise ValueError("brand tidak valid")
    return f"{REFRESH_PREFIX}{brand}"


# ------------------------------------------------------------------------------------------------ data masukan
@dataclass(frozen=True)
class PurchaseMonth:
    """Satu baris PURCHASE_SQL: agregat PO per (bulan, product_code, uom)."""

    month: date
    product_code: str
    uom: str
    receipt_qty: Decimal
    value: Decimal
    last_day: date
    product_name: str
    sub_category: str
    category: str


@dataclass(frozen=True)
class BomHeader:
    bom_id: int
    bom_name: str
    bom_type: str
    product_code: str
    product_name: str
    active: bool


@dataclass(frozen=True)
class BomLine:
    bom_id: int
    product_code: str
    qty: float
    last_hpp: float

    @property
    def value(self) -> float:
        return self.qty * self.last_hpp


@dataclass(frozen=True)
class SalesMonth:
    month: date
    menu_name: str
    menu_category: str
    qty: Decimal
    net: Decimal
    cogs: Decimal


@dataclass
class MenuMonth:
    qty: Decimal = ZERO
    net: Decimal = ZERO
    cogs: Decimal = ZERO


SalesIndex = dict[tuple[date, str], MenuMonth]  # (bulan, menu kanonik) -> jumlah bulan itu


@dataclass
class Ingredient:
    product_code: str
    uom: str
    product_name: str
    group: str
    monthly: dict[date, tuple[Decimal, Decimal]]  # bulan -> (receipt_qty, value), satuan dominan saja
    price_4m: dict[date, Decimal] = field(default_factory=dict)
    idx: dict[date, float | None] = field(default_factory=dict)


@dataclass
class MarginResult:
    price: list[list[Any]]
    menu: list[list[Any]]
    group: list[list[Any]]

    @property
    def rows(self) -> int:
        return len(self.price) + len(self.menu) + len(self.group)


def _dec(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value or 0))


def _q(value: Decimal, quantum: Decimal) -> Decimal:
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def parse_purchases(rows: Iterable[Sequence[Any]]) -> list[PurchaseMonth]:
    """Baris PURCHASE_SQL: m, product_code, uom, rq, val, last_d, p_name, p_sub, p_cat."""
    return [
        PurchaseMonth(r[0], str(r[1]), str(r[2]), _dec(r[3]), _dec(r[4]), r[5], str(r[6]), str(r[7]), str(r[8]))
        for r in rows
    ]


def parse_sales(rows: Iterable[Sequence[Any]]) -> list[SalesMonth]:
    """Baris SALES_SQL: m, menu_name, menu_category, q, n, c."""
    return [SalesMonth(r[0], str(r[1]), str(r[2]), _dec(r[3]), _dec(r[4]), _dec(r[5])) for r in rows]


def parse_headers(rows: Iterable[Sequence[Any]]) -> list[BomHeader]:
    return [BomHeader(int(r[0]), str(r[1]), str(r[2]), str(r[3]), str(r[4]), bool(r[5])) for r in rows]


def parse_lines(rows: Iterable[Sequence[Any]]) -> list[BomLine]:
    return [BomLine(int(r[0]), str(r[1]), float(r[2]), float(r[3])) for r in rows]


# ------------------------------------------------------------------------------------------------ logika murni
def normalize_name(text: Any) -> str:
    """Huruf besar + spasi tunggal."""
    return _text(text).upper()


def canonical_menu(text: Any) -> str:
    """Nama menu kanonik (§31.2 langkah 4): normalisasi, buang awalan `KIOSK - ` lalu akhiran ` T` (takeaway)."""
    name = normalize_name(text).removeprefix(KIOSK_PREFIX).strip()
    return name.removesuffix(TAKEAWAY_SUFFIX).strip()


def clean_product_name(name: Any, code: str) -> str:
    """Nama PO memuat kode dalam kurung (mis. `Nama (KODE)`): kurung berisi kode dibuang, spasi dirapikan."""
    text = str(name or "")
    if code:
        text = re.sub(r"\(\s*" + re.escape(code) + r"\s*\)", " ", text)
    return _text(text)


def add_months(month: date, delta: int) -> date:
    index = month.year * 12 + month.month - 1 + delta
    return date(index // 12, index % 12 + 1, 1)


def month_end(month: date) -> date:
    return add_months(month, 1) - timedelta(days=1)


def window_months(today: date, months: int) -> list[date]:
    """`months` bulan kalender PENUH terakhir sebelum bulan `today` (bulan berjalan tidak ikut: margin bulan setengah
    jalan menyesatkan), urut naik; elemen pertama = bulan dasar."""
    if months < 1:
        raise ValueError("months harus >= 1")
    last = add_months(today.replace(day=1), -1)
    return [add_months(last, offset) for offset in range(1 - months, 1)]


def dominant_uom(rows: Iterable[PurchaseMonth]) -> str:
    """Satuan beli dengan nilai terbesar (seri: receipt_qty terbesar, lalu nama satuan terkecil)."""
    totals: dict[str, list[Decimal]] = {}
    for row in rows:
        acc = totals.setdefault(row.uom, [ZERO, ZERO])
        acc[0] += row.value
        acc[1] += row.receipt_qty
    return max(sorted(totals), key=lambda uom: (totals[uom][0], totals[uom][1]))


def rolling_prices(monthly: Mapping[date, tuple[Decimal, Decimal]], window: Sequence[date]) -> dict[date, Decimal]:
    """price_4m(m) = Σ value / Σ receipt_qty atas bulan m-3..m (= rata-rata price berbobot receipt_qty)."""
    out: dict[date, Decimal] = {}
    for month in window:
        qty = value = ZERO
        for back in range(ROLLING_MONTHS):
            month_qty, month_value = monthly.get(add_months(month, -back), (ZERO, ZERO))
            qty += month_qty
            value += month_value
        if qty > 0:
            out[month] = value / qty
    return out


def price_index(price_4m: Mapping[date, Decimal], window: Sequence[date]) -> dict[date, float | None]:
    """idx(m) = price_4m(m) / price_4m(dasar); dasar = bulan pertama jendela yang punya harga. Dasar 0 -> NULL."""
    months = [month for month in window if month in price_4m]
    if not months:
        return {}
    base = price_4m[months[0]]
    if base <= 0:
        return {month: None for month in months}
    return {month: float(price_4m[month] / base) for month in months}


def _latest(rows: Iterable[PurchaseMonth], attr: str) -> str:
    """Nilai non-kosong dari baris dengan hari PO terbaru (seri: nilai terbesar); '' bila tidak ada."""
    best: tuple[date, str] | None = None
    for row in rows:
        value = _text(getattr(row, attr))
        if value and (best is None or (row.last_day, value) > best):
            best = (row.last_day, value)
    return best[1] if best else ""


def build_ingredients(rows: Iterable[PurchaseMonth], window: Sequence[date]) -> dict[str, Ingredient]:
    """Langkah 1-3. Hanya bahan yang punya price_4m di dalam jendela (sisanya = daun tanpa harga PO)."""
    by_code: dict[str, list[PurchaseMonth]] = defaultdict(list)
    for row in rows:
        if row.product_code:
            by_code[row.product_code].append(row)
    out: dict[str, Ingredient] = {}
    for code in sorted(by_code):
        items = by_code[code]
        uom = dominant_uom(items)
        monthly: dict[date, tuple[Decimal, Decimal]] = {}
        for row in items:
            if row.uom == uom:
                qty, value = monthly.get(row.month, (ZERO, ZERO))
                monthly[row.month] = (qty + row.receipt_qty, value + row.value)
        price_4m = rolling_prices(monthly, window)
        if not price_4m:
            continue
        out[code] = Ingredient(
            product_code=code,
            uom=uom,
            product_name=clean_product_name(_latest(items, "product_name"), code),
            group=_latest(items, "sub_category") or _latest(items, "category") or DEFAULT_GROUP,
            monthly=monthly,
            price_4m=price_4m,
            idx=price_index(price_4m, window),
        )
    return out


def _is_menu(header: BomHeader) -> bool:
    return normalize_name(header.bom_type) == MENU_BOM_TYPE


def menu_bom_index(headers: Iterable[BomHeader]) -> dict[str, int]:
    """Nama menu kanonik -> bom_id BOM Menu aktif (nama BOM dan nama produk hasil). Nama ganda -> bom_id terbesar."""
    out: dict[str, int] = {}
    for header in headers:
        if not header.active or not _is_menu(header):
            continue
        for name in (canonical_menu(header.bom_name), canonical_menu(header.product_name)):
            if name and header.bom_id > out.get(name, -1):
                out[name] = header.bom_id
    return out


def sub_bom_index(headers: Iterable[BomHeader]) -> dict[str, int]:
    """product_code hasil -> bom_id BOM non-Menu aktif (kode ganda -> bom_id terbesar)."""
    out: dict[str, int] = {}
    for header in headers:
        code = header.product_code
        if header.active and code and not _is_menu(header) and header.bom_id > out.get(code, -1):
            out[code] = header.bom_id
    return out


def _place(
    code: str,
    weight: float,
    path: frozenset[int],
    by_bom: Mapping[int, list[BomLine]],
    subs: Mapping[str, int],
    leaves: dict[str, float],
) -> None:
    """Komponen ber-sub-BOM diurai proporsional nilai baris sub-BOM; siklus (sub-BOM sudah di jalur), rantai sudah
    MAX_BOM_DEPTH BOM, atau sub-BOM bernilai 0 -> komponen menjadi daun."""
    sub = subs.get(code)
    if sub is not None and sub not in path and len(path) < MAX_BOM_DEPTH:
        children = by_bom.get(sub, [])
        total = sum(child.value for child in children)
        if total > 0:
            for child in children:
                _place(child.product_code, weight * child.value / total, path | {sub}, by_bom, subs, leaves)
            return
    leaves[code] = leaves.get(code, 0.0) + weight


def expand_menus(headers: Sequence[BomHeader], lines: Iterable[BomLine]) -> dict[str, dict[str, float]]:
    """Langkah 4: menu kanonik -> {product_code daun: bobot Rp (qty × last_hpp)}. Baris bernilai <= 0 diabaikan; menu
    dengan total bobot 0 tidak dikembalikan (= tanpa BOM, bom_matched 0)."""
    by_bom: dict[int, list[BomLine]] = defaultdict(list)
    for line in lines:
        if line.value > 0:
            by_bom[line.bom_id].append(line)
    subs = sub_bom_index(headers)
    out: dict[str, dict[str, float]] = {}
    for name, bom_id in sorted(menu_bom_index(headers).items()):
        leaves: dict[str, float] = {}
        for line in by_bom.get(bom_id, []):
            _place(line.product_code, line.value, frozenset({bom_id}), by_bom, subs, leaves)
        if sum(leaves.values()) > 0:
            out[name] = leaves
    return out


def leaf_idx(ingredients: Mapping[str, Ingredient], code: str, month: date) -> float:
    """Indeks daun bulan itu; tanpa harga PO / sebelum bulan dasarnya / dasar 0 -> 1."""
    ing = ingredients.get(code)
    value = ing.idx.get(month) if ing is not None else None
    return 1.0 if value is None else value


def cost_index(leaves: Mapping[str, float], ingredients: Mapping[str, Ingredient], month: date) -> float:
    """Langkah 5: Σ w·idx(m) / Σ w (leaves dari expand_menus, total > 0)."""
    total = sum(leaves.values())
    return sum(weight * leaf_idx(ingredients, code, month) for code, weight in leaves.items()) / total


def leaf_coverage(leaves: Mapping[str, float], ingredients: Mapping[str, Ingredient]) -> float:
    total = sum(leaves.values())
    return sum(weight for code, weight in leaves.items() if code in ingredients) / total


def aggregate_sales(rows: Iterable[SalesMonth], window: Sequence[date]) -> tuple[SalesIndex, dict[str, str]]:
    """(bulan, menu kanonik) -> qty/net/cogs semua cabang & PT brand; kategori menu = kategori dengan qty jendela
    terbesar (seri: nama terkecil). Bulan di luar jendela diabaikan."""
    months = set(window)
    sums: SalesIndex = {}
    category_qty: dict[tuple[str, str], Decimal] = {}
    for row in rows:
        name = canonical_menu(row.menu_name)
        if row.month not in months or not name:
            continue
        acc = sums.setdefault((row.month, name), MenuMonth())
        acc.qty += row.qty
        acc.net += row.net
        acc.cogs += row.cogs
        key = (name, _text(row.menu_category))
        category_qty[key] = category_qty.get(key, ZERO) + row.qty
    best: dict[str, tuple[Decimal, str]] = {}
    for (name, category), qty in sorted(category_qty.items()):
        if name not in best or qty > best[name][0]:
            best[name] = (qty, category)
    return sums, {name: category for name, (_qty, category) in best.items()}


def group_baskets(
    menus: Mapping[str, Mapping[str, float]],
    sales: SalesIndex,
    ingredients: Mapping[str, Ingredient],
    window: Sequence[date],
) -> tuple[dict[str, list[float]], dict[str, float]]:
    """Langkah 6 (Laspeyres, keranjang tetap): kelompok -> basket_value per bulan jendela (Rp). Qty menu = total
    jendela / jumlah bulan; skala = rata-rata COGS ESB bulanan menu yang cocok / nilai keranjang bulan dasar (skala 1
    bila salah satunya <= 0). Daun tanpa harga PO = kelompok UNPRICED_GROUP (idx 1). Kedua: nilai pakai per bahan
    (Rp/bulan pada harga dasar, skala sama) = bobot dampak per bahan di Insight (PO impor USD tetap dinilai Rupiah)."""
    n_months = len(window)
    qty: dict[str, float] = defaultdict(float)
    cogs: dict[str, float] = defaultdict(float)
    for (_month, name), acc in sales.items():
        if name in menus:
            qty[name] += float(acc.qty)
            cogs[name] += float(acc.cogs)
    raw: dict[str, list[float]] = {}
    usage: dict[str, float] = defaultdict(float)
    cogs_total = 0.0
    for name in sorted(qty):
        if qty[name] <= 0:
            continue
        cogs_total += cogs[name]
        per_month = qty[name] / n_months
        for code, weight in menus[name].items():
            usage[code] += per_month * weight
            ing = ingredients.get(code)
            series = raw.setdefault(ing.group if ing is not None else UNPRICED_GROUP, [0.0] * n_months)
            for i, month in enumerate(window):
                series[i] += per_month * weight * leaf_idx(ingredients, code, month)
    base_total = sum(series[0] for series in raw.values())
    avg_cogs = cogs_total / n_months
    scale = avg_cogs / base_total if base_total > 0 and avg_cogs > 0 else 1.0
    groups = {group: [value * scale for value in series] for group, series in sorted(raw.items())}
    return groups, {code: value * scale for code, value in usage.items()}


def compute(
    brand: str,
    company: str,
    window: Sequence[date],
    purchases: Iterable[PurchaseMonth],
    headers: Sequence[BomHeader],
    lines: Iterable[BomLine],
    sales: Iterable[SalesMonth],
    version: datetime,
) -> MarginResult:
    """Langkah 1-6 -> baris ketiga tabel (urutan kolom = *_COLUMNS)."""
    ingredients = build_ingredients(purchases, window)
    menus = expand_menus(headers, lines)
    by_month, categories = aggregate_sales(sales, window)
    # Tabel harga hanya bahan daun resep (PO lain: biaya marketing, aset, perlengkapan -> bukan bahan).
    used = {code for leaves in menus.values() for code in leaves}
    baskets, usage = group_baskets(menus, by_month, ingredients, window)
    price: list[list[Any]] = []
    for code, ing in ingredients.items():
        if code not in used:
            continue
        for month in window:
            if month not in ing.price_4m:
                continue
            qty, value = ing.monthly.get(month, (ZERO, ZERO))
            row = [
                brand,
                company,
                month,
                code,
                ing.product_name,
                ing.group,
                ing.uom,
                _q(qty, Q3),
                _q(value, Q2),
                _q(value / qty if qty > 0 else ZERO, Q4),
                _q(ing.price_4m[month], Q4),
                ing.idx.get(month),
                usage.get(code, 0.0),
                version,
            ]
            price.append(row)
    menu: list[list[Any]] = []
    for month, name in sorted(by_month):
        acc = by_month[(month, name)]
        leaves = menus.get(name)
        row = [
            brand,
            company,
            month,
            name,
            categories.get(name, ""),
            _q(acc.qty, Q3),
            _q(acc.net, Q2),
            _q(acc.cogs, Q2),
            0 if leaves is None else 1,
            None if leaves is None else cost_index(leaves, ingredients, month),
            0.0 if leaves is None else leaf_coverage(leaves, ingredients),
            version,
        ]
        menu.append(row)
    group: list[list[Any]] = []
    for name, series in baskets.items():
        for i, month in enumerate(window):
            group.append([brand, company, month, name, series[i], series[0], version])
    return MarginResult(price=price, menu=menu, group=group)


def pick_company(rows: Iterable[Sequence[Any]]) -> str:
    """PT brand dari cabang aktif dim_esb_branch; '' bila tidak ada. Brand multi-PT (mis. BCF + BCR) DITOLAK
    (SEC-MB-01): sumber PO/penjualan hanya difilter brand, jadi label satu PT akan membuka data PT lain."""
    codes = sorted({str(code) for code, _count in rows if str(code)})
    if len(codes) > 1:
        raise MarginError("multi_company")
    return codes[0] if codes else ""


def is_due(now: datetime, weekday: int, hour_wib: int, last_ok: datetime | None, attempted: date | None) -> bool:
    """Jadwal §31.2: hari WIB = weekday, jam WIB >= hour_wib, refresh ok terakhir > 6 hari (atau belum pernah), dan
    belum dicoba pada hari WIB ini (gagal tidak diulang setiap siklus; berikutnya minggu depan atau CLI manual)."""
    local = wib_now(now)
    if local.weekday() != weekday or local.hour < hour_wib or attempted == local.date():
        return False
    return last_ok is None or now - last_ok > REFRESH_MIN_AGE


# ------------------------------------------------------------------------------------------------ eksekusi (I/O)
class MarginStore(RefreshStore, Protocol):
    def insert(self, table: str, columns: Sequence[str], rows: list[list[Any]]) -> None: ...


class MarginError(RuntimeError):
    """Prasyarat data tidak terpenuhi; pesan = label tetap (aman dicatat): no_company | no_bom | no_purchase |
    no_sales."""


def last_ok(store: RefreshStore, brand: str) -> datetime | None:
    """Waktu selesai refresh `ok` terakhir brand ini (aggregate_refresh), None bila belum pernah."""
    count, finished = store.query_rows(render(LAST_OK_SQL, store), {"name": refresh_name(brand)})[0]
    if not int(count):
        return None
    return finished if finished.tzinfo else finished.replace(tzinfo=UTC)


def refresh_brand(
    store: MarginStore,
    brand: str,
    months: int,
    today: date,
    lock: RefreshLock,
    clock: Callable[[], datetime] = version_now,
) -> RefreshResult:
    """Baca sumber, hitung, INSERT versi baru (pemanggil memegang kunci). Melempar MarginError / LockLost / galat
    ClickHouse; tidak ada tulisan sebelum semua data terbaca dan dihitung."""
    started = clock()
    window = window_months(today, months)
    end = month_end(window[-1])
    params: dict[str, Any] = {"brand": brand}
    company = pick_company(store.query_rows(render(COMPANY_SQL, store), params))
    if not company:
        raise MarginError("no_company")
    headers = parse_headers(store.query_rows(render(BOM_SQL, store), params))
    if not headers:
        raise MarginError("no_bom")
    lines = parse_lines(store.query_rows(render(BOM_LINE_SQL, store), params))
    purchase_params = {
        "brand": brand,
        "start": add_months(window[0], 1 - ROLLING_MONTHS),
        "end": end,
        "cancel": list(CANCEL_MARKERS),
    }
    purchases = parse_purchases(store.query_rows(render(PURCHASE_SQL, store), purchase_params))
    if not purchases:
        raise MarginError("no_purchase")
    sales_params = {"brand": brand, "start": window[0], "end": end}
    sales = parse_sales(store.query_rows(render(SALES_SQL, store), sales_params))
    result = compute(brand, company, window, purchases, headers, lines, sales, started)
    if not result.menu:
        raise MarginError("no_sales")
    # Tiga tabel = satu versi; hasil kosong di salah satunya tidak ditulis (view campur versi, SEC-MB-02).
    if not result.price or not result.group:
        raise MarginError("no_price")
    lock.ensure()
    # Kelompok -> menu -> harga; satu INSERT per tabel (view = versi terakhir per tabel).
    for table, rows in ((GROUP_TABLE, result.group), (MENU_TABLE, result.menu), (PRICE_TABLE, result.price)):
        store.insert(table, MART_TABLES[table], rows)
    name = refresh_name(brand)
    return RefreshResult(name, "ok", started, clock(), result.rows, len(window), window[0], end)


def record(store: RefreshStore, result: RefreshResult) -> None:
    """Log tanpa isi data + baris aggregate_refresh (best effort)."""
    level = logging.INFO if result.status == "ok" else logging.ERROR
    log.log(
        level,
        "margin=%s status=%s rows=%d months=%d seconds=%.1f%s",
        result.name,
        result.status,
        result.rows,
        result.months,
        (result.finished_at - result.started_at).total_seconds(),
        f" error={result.error}" if result.error else "",
    )
    try:
        store.insert_control(REFRESH_TABLE, REFRESH_COLUMNS, [result.row()])
    except Exception as exc:  # catatan kesegaran best effort; mart sudah tertulis
        log.error("margin=%s aggregate_refresh tidak tersimpan error=%s", result.name, error_label(exc))


def refresh_margin(
    store: MarginStore,
    brand: str,
    months: int,
    today: date,
    lock: RefreshLock | None = None,
    clock: Callable[[], datetime] = version_now,
) -> RefreshResult:
    """Satu brand di bawah kunci agregat. Tidak pernah melempar: busy / error dicatat (aggregate_refresh + log)."""
    name = refresh_name(brand)
    lock = lock or RefreshLock(store)
    started = clock()
    try:
        if lock.acquire():
            result = refresh_brand(store, brand, months, today, lock, clock)
        else:
            result = RefreshResult(name, "busy", started, clock(), error=BUSY_ERROR)
    except MarginError as exc:
        result = RefreshResult(name, "error", started, clock(), error=str(exc))
    except LockLost:
        result = RefreshResult(name, "error", started, clock(), error="lock_lost")
    except Exception as exc:  # galat ClickHouse: kelas + kode saja (pesan bisa memuat potongan query/nilai)
        result = RefreshResult(name, "error", started, clock(), error=error_label(exc))
    finally:
        lock.release()
    record(store, result)
    return result
