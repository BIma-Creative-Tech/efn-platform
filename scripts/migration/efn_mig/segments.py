"""Rentang histori & segmentasi bundel (docs/04 §27.3a, §27.5 butir 5, §27.8). Murni.

- flow.daily: `[cutover - legacy_days, cutover - 1]` (legacy_days 1..730).
- sales.daily: `[awal bulan(cutover) - 24 bulan, cutover - 1]`.
- sales.line: `[awal bulan(cutover) - N bulan, cutover - 1]`, N = sales_line_months (0..24; 0 = tidak ada).
Segmen = company x bulan kalender; bila baris > max_rows -> dipecah per cabang (gudang), satu cabang tidak terbelah.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta

SALES_DAILY_MONTHS = 24
MAX_LEGACY_DAYS = 730
MAX_SALES_LINE_MONTHS = 24


def month_start(day: date) -> date:
    return day.replace(day=1)


def add_months(day: date, months: int) -> date:
    """Hari pertama bulan `day` digeser `months` (boleh negatif)."""
    index = day.year * 12 + (day.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def month_label(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def parse_month(label: str) -> date:
    try:
        year, month = label.split("-")
        return date(int(year), int(month), 1)
    except ValueError:
        raise ValueError(f"bulan tidak valid (YYYY-MM): {label!r}") from None


@dataclass(frozen=True)
class HistoryRanges:
    """Rentang [mulai, akhir) per berkas histori; None = tidak diekspor."""

    flow: tuple[date, date]
    sales_daily: tuple[date, date]
    sales_line: tuple[date, date] | None

    def for_month(self, month: date) -> dict[str, tuple[date, date] | None]:
        """Irisan setiap rentang dengan bulan kalender `month`."""
        lo, hi = month, add_months(month, 1)

        def clip(rng: tuple[date, date] | None) -> tuple[date, date] | None:
            if rng is None:
                return None
            start, end = max(rng[0], lo), min(rng[1], hi)
            return (start, end) if start < end else None

        return {
            "legacy_flow_daily": clip(self.flow),
            "legacy_sales_daily": clip(self.sales_daily),
            "legacy_sales_line": clip(self.sales_line),
        }


def history_ranges(cutover: date, legacy_days: int, sales_line_months: int) -> HistoryRanges:
    if not 1 <= legacy_days <= MAX_LEGACY_DAYS:
        raise ValueError(f"legacy_days di luar 1..{MAX_LEGACY_DAYS}")
    if not 0 <= sales_line_months <= MAX_SALES_LINE_MONTHS:
        raise ValueError(f"sales_line_months di luar 0..{MAX_SALES_LINE_MONTHS}")
    base = month_start(cutover)
    line = (add_months(base, -sales_line_months), cutover) if sales_line_months else None
    return HistoryRanges(
        flow=(cutover - timedelta(days=legacy_days), cutover),
        sales_daily=(add_months(base, -SALES_DAILY_MONTHS), cutover),
        sales_line=line,
    )


def history_months(ranges: HistoryRanges, selected: Iterable[str] | None = None) -> list[date]:
    """Bulan yang punya minimal satu rentang aktif (lama -> baru), opsional dibatasi `--months`."""
    starts = [ranges.flow[0], ranges.sales_daily[0]] + ([ranges.sales_line[0]] if ranges.sales_line else [])
    end = ranges.sales_daily[1]
    months: list[date] = []
    current = month_start(min(starts))
    while current < end:
        if any(v is not None for v in ranges.for_month(current).values()):
            months.append(current)
        current = add_months(current, 1)
    if selected is not None:
        wanted = {parse_month(label) for label in selected}
        unknown = wanted - set(months)
        if unknown:
            raise ValueError(f"bulan di luar rentang histori: {', '.join(sorted(map(month_label, unknown)))}")
        months = [m for m in months if m in wanted]
    return months


def plan_parts[K: (int, str)](rows_by_branch: Mapping[K, int], max_rows: int) -> list[list[K]]:
    """Unit (gudang tujuan; dulu cabang) -> part, greedy urut kunci. Total part <= max_rows kecuali satu unit sendiri
    sudah > max_rows; unit tidak pernah terbelah. Export memakai kunci gudang tujuan (SEC-197)."""
    if max_rows < 1:
        raise ValueError("max_rows harus >= 1")
    parts: list[list[K]] = []
    current: list[K] = []
    total = 0
    for branch_id in sorted(b for b, n in rows_by_branch.items() if n > 0):
        rows = rows_by_branch[branch_id]
        if current and total + rows > max_rows:
            parts.append(current)
            current, total = [], 0
        current.append(branch_id)
        total += rows
    if current:
        parts.append(current)
    return parts


def segment_filename(parent_run_id: str, company_code: str, month: date, part: int, parts: int) -> str:
    suffix = f"_p{part}" if parts > 1 else ""
    return f"{parent_run_id}_{company_code}_{month.year:04d}{month.month:02d}{suffix}.efnmig"
